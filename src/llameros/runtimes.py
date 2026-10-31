"""Hardware-isolated Ollama runtime slots sharing Llameros model storage and lifecycle policy.

Hardware-isolated Ollama slots sharing a model store, never arbitrary devices.
"""

import hashlib
import json
import re
import threading
from dataclasses import replace

from .diagnostics import diagnostic
from .docker import Docker, DockerError
from .hardware import assess_fit
from .model_storage import write_json


class Runtimes:
    """Manage persistent CPU/GPU slot identities and resolve already-loaded model instances."""

    def __init__(self, settings, hardware):
        """Load the private slot registry, shared storage, and current hardware capabilities."""
        self.settings = settings
        self.observer = None
        self.hardware = hardware
        self.storage = Docker(settings).storage
        self.registry = settings.state_dir / "runtime-slots.json"
        self.lock = threading.RLock()
        self.records = json.loads(self.registry.read_text()) if self.registry.exists() else {}
        self.refresh()

    def refresh(self):
        """Rebuild supported hardware targets with stable hashed GPU IDs and validated device paths."""
        targets = [{"id": "cpu", "label": "CPU", "vendor": "cpu", "supported": True}]
        for index, gpu in enumerate(self.hardware()["gpus"]):
            identity = gpu.get("id", f"unidentified-{index}")
            vendor = gpu["vendor"]
            device = identity if vendor == "nvidia" else gpu.get("render_device", "")
            supported = (
                bool(re.fullmatch(r"GPU-[A-Za-z0-9-]+", device))
                if vendor == "nvidia"
                else bool(vendor == "amd" and re.fullmatch(r"/dev/dri/renderD\d+", device))
            )
            targets.append(
                {
                    **gpu,
                    "id": "gpu-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                    "label": f"GPU {index + 1} · {gpu['name']}",
                    "vendor": vendor,
                    "device": device,
                    "supported": supported,
                }
            )
        self.targets = {target["id"]: target for target in targets}

    def client(self, target_id="cpu", *, register=False):
        """Resolve a slot under the registry lock, optionally persisting its port assignment."""
        with self.lock:
            return self._client(target_id, register=register)

    def _client(self, target_id="cpu", *, register=False):
        """Build a device-bound Docker client; caller holds the registry lock for registration."""
        target = self.targets.get(target_id) or self.records.get(target_id, {}).get("target")
        if not target or not target["supported"]:
            raise ValueError("This hardware cannot be safely targeted by this Docker runtime")
        if target_id not in self.records:
            if not register and target_id != "cpu":
                return None
            used = {record["port"] for record in self.records.values()}
            port = (
                self.settings.ollama_port
                if target_id == "cpu"
                else next(
                    (
                        p
                        for p in range(self.settings.ollama_port + 1, 65536)
                        if p not in used and p != self.settings.port
                    ),
                    None,
                )
            )
            if port is None:
                raise ValueError("No available configured port range for a GPU runtime")
            self.records[target_id] = {"port": port, "target": target}
            write_json(self.registry, self.records)
        record = self.records[target_id]
        cfg = replace(self.settings, gpu=target["vendor"], ollama_port=record["port"])
        name = self.settings.container_name + ("" if target_id == "cpu" else "-" + target_id)
        client = Docker(cfg, target=target, name=name, storage=self.storage)
        client.observer = self.observer
        return client

    def status(self):
        """Merge registered runtime status with available hardware and annotate loaded models by slot."""
        self.refresh()
        loaded, slots = [], []
        main = None
        with self.lock:
            records = dict(self.records)
        for identity, target in {
            **{key: rec["target"] for key, rec in records.items()},
            **self.targets,
        }.items():
            slot = {**target, "available": False, "running": False, "loaded": []}
            if target["supported"]:
                try:
                    client = self.client(identity)
                    state = client.status() if client else {"running": False, "loaded": []}
                    slot.update(
                        running=state["running"],
                        loaded=state.get("loaded", []),
                        available=len(state.get("loaded", [])) < self.settings.max_loaded_models,
                    )
                    if client:
                        slot["base_url"] = "/inference"
                    for model in state.get("loaded", []):
                        loaded.append(
                            {
                                **model,
                                "target": identity,
                                "hardware": target["label"],
                                "base_url": "/inference",
                            }
                        )
                    if identity == "cpu":
                        main = state
                except Exception as exc:
                    slot["error"] = diagnostic(exc)
                    # Report legacy container state honestly so the Stop control
                    # remains usable, while never treating it as a free slot.
                    if identity == "cpu":
                        legacy = Docker(self.settings)
                        legacy.observer = self.observer
                        try:
                            legacy.check_daemon()
                            data = legacy.inspect()
                            slot["running"] = bool(data and data["State"]["Running"])
                            main = {"running": slot["running"], "legacy_error": diagnostic(exc)}
                        except Exception:
                            pass
            else:
                slot["error"] = (
                    "No supported per-device isolation (requires NVIDIA UUID or AMD render node)"
                )
            if identity not in self.targets:
                slot.update(
                    available=False,
                    error="Hardware no longer detected; stop this slot before reuse",
                )
            slots.append(slot)
        return {
            **(main or {"running": False, "error": "CPU runtime status unavailable"}),
            "running": any(slot["running"] for slot in slots),
            "loaded": loaded,
            "slots": slots,
        }

    def model_client(self, name, target=""):
        """Return the unique runtime containing a model, requiring a target when ambiguous."""
        normalized = name if ":" in name else name + ":latest"
        matches = [
            model
            for model in self.status()["loaded"]
            if model.get("name", model.get("model")) == normalized
            and (not target or model["target"] == target)
        ]
        if len(matches) != 1:
            raise ValueError("Choose a loaded model and its hardware slot")
        return self.client(matches[0]["target"])

    def load(self, name, size, target_id, context, progress):
        """Start and warm a model on selected hardware after fit/capacity checks.

        Verify residency and GPU use afterward; unload the new model if prior residency was lost
        or GPU execution fell back.
        """
        state = self.status()
        slot = next((slot for slot in state["slots"] if slot["id"] == target_id), None)
        if not slot or not slot["available"]:
            raise ValueError(
                slot.get("error")
                if slot and slot.get("error")
                else "Select a free, supported hardware slot"
            )
        client = self.client(target_id, register=True)
        started = False
        if size is None:
            progress({"status": "Starting selected runtime to verify downloaded model inventory"})
            client.start(progress=progress)
            started = True
            models = client.api("GET", "/api/tags").get("models", [])
            self.storage.remember(models)
            normalized = name if ":" in name else name + ":latest"
            info = next(
                (model for model in models if model.get("name", model.get("model")) == normalized),
                None,
            )
            if info is None:
                raise ValueError("Download this model before loading or running it")
            size = info["size"]
        hw = self.hardware()
        target = self.targets[target_id]
        fit = assess_fit(size, hw, "cpu", self.settings.ollama_num_parallel, context)
        if not fit["fits"]:
            raise ValueError("Insufficient available system RAM for this model")
        gpu_available = (
            max(0, hw["memory_available"] - 512 * 1024**2)
            if target.get("unified_memory")
            else target.get("free") or 0
        )
        if target_id != "cpu" and fit["estimated_memory"] > gpu_available:
            raise ValueError(
                "Model does not fit selected GPU VRAM; select CPU or a larger free GPU"
            )
        if not started:
            client.start(progress=progress)
        # Recheck capacity after startup; free-memory estimates include resident models.
        previous = client.api("GET", "/api/ps").get("models", [])
        normalized = name if ":" in name else name + ":latest"
        if any(model.get("name", model.get("model")) == normalized for model in previous):
            raise ValueError("Model is already loaded on this hardware")
        if len(previous) >= self.settings.max_loaded_models:
            raise ValueError("Select hardware with a free resident-model slot")
        info = client.api("POST", "/api/show", {"model": name})
        embedding = "embedding" in info.get("capabilities", [])
        options = {"num_ctx": context}
        if target_id == "cpu":
            options.update(num_gpu=0)
        result = client.api(
            "POST",
            "/api/embed" if embedding else "/api/generate",
            {
                "model": name,
                **({"input": "warmup", "truncate": False} if embedding else {"stream": False}),
                "keep_alive": -1,
                "options": options,
            },
        )
        loaded = client.api("GET", "/api/ps").get("models", [])
        resident = next(
            (model for model in loaded if model.get("name", model.get("model")) == normalized), None
        )
        missing = {model.get("name", model.get("model")) for model in previous} - {
            model.get("name", model.get("model")) for model in loaded
        }
        if (
            resident is None
            or missing
            or (target_id != "cpu" and resident.get("size_vram", 0) <= 0)
        ):
            client.api("POST", "/api/generate", {"model": name, "keep_alive": 0})
            raise DockerError(
                "Model residency could not be preserved or GPU fell back to CPU; new model unloaded"
            )
        return result

    def stop(self):
        """Attempt to stop all registered owned runtimes and report collected failures."""
        errors = []
        for identity in list(self.records):
            client = self.client(identity)
            client.target = None  # Owner label still checked; permits stopping legacy containers.
            try:
                client.stop()
            except Exception as exc:
                errors.append(f"{identity}: {exc}")
        if errors:
            raise DockerError("Some runtimes could not be stopped: " + "; ".join(errors))

    def configure_storage(self, directory, confirmed):
        """Reconfigure shared storage only after confirmation and all runtimes are stopped."""
        if not confirmed:
            raise ValueError("Explicit confirmation required")
        if any(slot["running"] for slot in self.status()["slots"]):
            raise ValueError("Stop all runtimes before changing storage")
        # Inspect without target checking allows explicit legacy-container migration.
        clients = [self.client(identity) for identity in self.records]
        for client in clients:
            client.target = None
            client.check_daemon()
            data = client.inspect()
            if data and data["State"]["Running"]:
                raise ValueError("Stop legacy container before reapplying storage")
        result = Docker(self.settings).configure_storage(directory, confirmed)
        for client in clients:
            if client.name != self.settings.container_name and client.inspect():
                client.command("rm", client.name)
        self.storage.directory = result["directory"]
        return result
