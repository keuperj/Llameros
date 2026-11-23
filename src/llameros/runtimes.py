"""Hardware-isolated Ollama runtime slots sharing Llameros model storage and lifecycle policy.

Hardware-isolated Ollama slots sharing a model store, never arbitrary devices.
"""

import hashlib
import json
import re
import threading
from dataclasses import replace

from .diagnostics import GPUPlacementError, RuntimeStartupError, RuntimeStartupSummary, diagnostic
from .docker import Docker, DockerError
from .hardware import assess_fit
from .model_storage import write_json
from .ollama_settings import keep_alive_value


class Runtimes:
    """Manage persistent CPU/GPU slot identities and resolve already-loaded model instances."""

    def __init__(self, settings, hardware):
        """Load the private slot registry, shared storage, and current hardware capabilities."""
        self.settings = settings
        self.observer = None
        self.hardware = hardware
        self.storage = Docker(settings).storage
        self.registry = settings.state_dir / "runtime-slots.json"
        self.deployment_registry = settings.state_dir / "gpu-deployments.json"
        self.lock = threading.RLock()
        self.records = json.loads(self.registry.read_text()) if self.registry.exists() else {}
        self.deployments = (
            json.loads(self.deployment_registry.read_text())
            if self.deployment_registry.exists()
            else {}
        )
        self.refresh()

    def refresh(self):
        """Rebuild supported hardware targets with stable hashed GPU IDs and validated device paths."""
        self.targets = self.hardware_targets(self.hardware())

    @staticmethod
    def hardware_targets(hardware):
        """Describe targets without opening storage, contacting Docker, or creating slots."""
        targets = [{"id": "cpu", "label": "CPU", "vendor": "cpu", "supported": True}]
        for index, gpu in enumerate(hardware["gpus"]):
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
        return {target["id"]: target for target in targets}

    def client(self, target_id="cpu", *, register=False, target=None):
        """Resolve a slot under the registry lock, optionally persisting its port assignment."""
        with self.lock:
            return self._client(target_id, register=register, target=target)

    def _client(self, target_id="cpu", *, register=False, target=None):
        """Build a device-bound Docker client; caller holds the registry lock for registration."""
        target = (
            target or self.targets.get(target_id) or self.records.get(target_id, {}).get("target")
        )
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
            deployments = dict(self.deployments)
        reserved = {gpu: key for key, group in deployments.items() for gpu in group["devices"]}
        workers = {worker: key for key, group in deployments.items() for worker in group["workers"]}
        grouped = {key: [] for key in deployments}
        multi_available = []
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
                        entry = {
                            **model,
                            "target": identity,
                            "hardware": target["label"],
                            "base_url": "/inference",
                        }
                        group = workers.get(identity)
                        if (
                            group
                            and model.get("name", model.get("model")) == deployments[group]["model"]
                        ):
                            if state["running"] and self._fully_on_gpu(model):
                                grouped[group].append(entry)
                        elif not target.get("placement"):
                            loaded.append(entry)
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
            if target.get("placement"):
                # Preserve visibility of a running or unhealthy orphan for operator cleanup.
                if identity in deployments or slot["running"] or slot.get("error"):
                    slot.update(available=False)
                    slots.append(slot)
                continue
            if identity not in self.targets:
                slot.update(
                    available=False,
                    error="Hardware no longer detected; stop this slot before reuse",
                )
            if identity in reserved:
                slot.update(available=False, deployment=reserved[identity])
            elif target["vendor"] != "cpu" and slot["available"] and not slot["loaded"]:
                multi_available.append(identity)
            slots.append(slot)
        summaries = []
        for identity, group in deployments.items():
            live = (
                [
                    entry
                    for entry in grouped[identity]
                    if entry["target"] in self.targets
                    or all(device in self.targets for device in group["devices"])
                ]
                if group["ready"]
                else []
            )
            summary = {
                "target": identity,
                "name": group["model"],
                "hardware": group["label"],
                "placement": group["placement"],
                "devices": group["devices"],
                "workers": [entry["target"] for entry in live],
                "worker_count": len(group["workers"]),
            }
            summaries.append(summary)
            if live:
                loaded.append({**live[0], **summary})
        return {
            **(main or {"running": False, "error": "CPU runtime status unavailable"}),
            "running": any(slot["running"] for slot in slots),
            "loaded": loaded,
            "slots": slots,
            "deployments": summaries,
            "multi_available": multi_available,
        }

    def model_client(self, name, target="", *, worker=None):
        """Return the unique runtime containing a model, requiring a target when ambiguous."""
        return self.model_runtime(name, target, worker=worker)[0]

    def model_runtime(self, name, target="", *, worker=None):
        """Resolve the worker and its logical deployment from the same status snapshot."""
        normalized = name if ":" in name else name + ":latest"
        matches = [
            model
            for model in self.status()["loaded"]
            if model.get("name", model.get("model")) == normalized
            and (not target or model["target"] == target)
        ]
        if len(matches) != 1:
            raise ValueError("Choose a loaded model and its hardware slot")
        match = matches[0]
        candidates = match.get("workers", [match["target"]])
        if worker is not None and worker not in candidates:
            raise ValueError("Selected model worker is no longer available")
        return self.client(worker or candidates[0]), match

    def start(self, progress=lambda value: None):
        """Start CPU and every detected supported GPU, retaining partial successes."""
        self.refresh()
        started, failures = [], []
        for identity, target in self.targets.items():
            if not target["supported"]:
                continue
            if any(identity in group["devices"] for group in self.deployments.values()):
                continue

            def report(value, identity=identity):
                progress({**value, "target": identity})

            report(
                {
                    "status": f"Starting {'CPU' if identity == 'cpu' else 'GPU'} Ollama container",
                    "phase": "container",
                }
            )
            try:
                self.client(identity, register=True).start(progress=report)
                started.append(identity)
            except Exception as exc:
                failures.append(diagnostic(RuntimeStartupError(identity, exc)))
        if failures:
            # These are already sanitized, fixed-vocabulary diagnostics.
            raise RuntimeStartupSummary(failures)
        return {"started": started}

    def _save_deployment(self, identity, group=None):
        with self.lock:
            records = dict(self.deployments)
            if group is None:
                records.pop(identity, None)
            else:
                records[identity] = group
            write_json(self.deployment_registry, records)
            self.deployments = records

    def reservation_targets(self, identity):
        """Reserve the public target as well as every underlying device and runtime."""
        with self.lock:
            group = self.deployments.get(identity, {})
            return {identity, *group.get("devices", []), *group.get("workers", [])}

    @staticmethod
    def deployment_id(devices):
        return "multi-" + hashlib.sha256(",".join(sorted(devices)).encode()).hexdigest()[:16]

    def load_many(self, name, devices, context, progress):
        """Deploy on exclusively reserved physical GPUs and publish only after warmup succeeds."""
        devices = sorted(devices)
        state = self.status()
        if (
            len(devices) < 2
            or len(set(devices)) != len(devices)
            or not set(devices) <= set(state["multi_available"])
        ):
            raise GPUPlacementError(
                "Select distinct, free GPUs with no resident models for multi-GPU loading"
            )
        name = name if ":" in name else name + ":latest"
        # The shared store can usually answer without starting another container.
        size = self._inventory_size(self.storage.inventory()["models"], name)
        if size is None:
            slot = next(slot for slot in state["slots"] if slot["id"] == devices[0])
            client = self.client(devices[0], register=True)
            try:
                self._start_client(devices[0], client, progress)
                size = self._downloaded_size(client, name)
            finally:
                if not slot["running"]:
                    client.stop()
        hw = self.hardware()
        fresh_targets = self.hardware_targets(hw)
        targets = [fresh_targets.get(device) for device in devices]
        if any(not target or not target["supported"] for target in targets):
            raise GPUPlacementError("Selected GPU is no longer available")
        fit = assess_fit(size, hw, "cpu", self.settings.ollama_num_parallel, context)
        memory = max(0, hw["memory_available"] - 512 * 1024**2)
        if not fit["fits"]:
            raise GPUPlacementError("Insufficient available system RAM for this model")
        required = fit["estimated_memory"]
        unified = sum(bool(target.get("unified_memory")) for target in targets)
        free = [
            memory if target.get("unified_memory") else target.get("free") for target in targets
        ]
        if any(value is None or value <= 0 for value in free):
            raise GPUPlacementError("Cannot verify free GPU memory on every selected device")
        identity = self.deployment_id(devices)
        placement = "replicated" if all(value >= required for value in free) else "sharded"
        workers = devices if placement == "replicated" else [identity]
        if placement == "replicated" and unified * required > memory:
            raise GPUPlacementError(
                "Insufficient shared system memory for one replica per selected GPU"
            )
        if placement == "sharded":
            if len({target["vendor"] for target in targets}) != 1 or unified:
                raise GPUPlacementError(
                    "Sharding requires dedicated-memory GPUs from the same vendor"
                )
            if sum(free) < required:
                raise GPUPlacementError(
                    "Model does not fit the combined free VRAM of the selected GPUs"
                )
        label = f"{'Replicated' if placement == 'replicated' else 'Sharded'} · " + " + ".join(
            t["label"] for t in targets
        )
        group = {
            "model": name,
            "label": label,
            "placement": placement,
            "devices": devices,
            "workers": workers,
            "ready": False,
        }
        self._save_deployment(identity, group)
        attempted = []
        try:
            for index, worker in enumerate(workers, 1):
                target = (
                    None
                    if worker != identity
                    else {
                        "id": identity,
                        "vendor": targets[0]["vendor"],
                        "supported": True,
                        "devices": [gpu["device"] for gpu in targets],
                        "label": label,
                        "placement": placement,
                    }
                )
                client = self.client(worker, register=True, target=target)
                attempted.append(client)
                progress({"status": f"Starting {placement} model ({index}/{len(workers)})"})
                self._start_client(worker, client, progress)
                if index == 1 and self._downloaded_size(client, name) != size:
                    raise GPUPlacementError(
                        "Model size changed since placement was estimated; retry the load"
                    )
                self._warm(client, name, worker, context, exclusive=True)
            self._save_deployment(identity, {**group, "ready": True})
        except Exception as exc:
            failed_cleanup = False
            for client in attempted:
                try:
                    client.stop()
                except Exception:
                    failed_cleanup = True
            if failed_cleanup:
                raise GPUPlacementError(
                    "Multi-GPU startup failed and cleanup was incomplete; stop the deployment before retrying"
                ) from exc
            self._save_deployment(identity)
            raise
        return {"target": identity, "placement": placement, "devices": devices}

    def unload(self, name, target, progress):
        """Stop a deployment as a unit, including failed or expired deployments."""
        group = self.deployments.get(target)
        if group:
            if group["model"] != (name if ":" in name else name + ":latest"):
                raise ValueError("Choose the model assigned to this deployment")
            progress({"status": "Stopping multi-GPU deployment"})
            self._save_deployment(target, {**group, "ready": False})
            errors = []
            for worker in group["workers"]:
                try:
                    client = self.client(worker) if worker in self.records else None
                    if client:
                        client.stop()
                except Exception as exc:
                    errors.append(diagnostic(exc))
            if errors:
                raise RuntimeStartupSummary(errors)
            self._save_deployment(target)
            return {"target": target, "stopped": True}
        progress({"status": "Unloading model from selected hardware"})
        return self.model_client(name, target).api(
            "POST", "/api/generate", {"model": name, "stream": False, "keep_alive": 0}
        )

    @staticmethod
    def _start_client(target, client, progress):
        try:
            client.start(progress=progress)
        except Exception as exc:
            raise RuntimeStartupError(target, exc) from exc

    def _downloaded_size(self, client, name):
        models = client.api("GET", "/api/tags").get("models", [])
        self.storage.remember(models)
        size = self._inventory_size(models, name)
        if size is None:
            raise ValueError("Download this model before loading or running it")
        return size

    @staticmethod
    def _inventory_size(models, name):
        normalized = name if ":" in name else name + ":latest"
        for model in models:
            if model.get("name", model.get("model")) == normalized:
                return model["size"]
        return None

    @staticmethod
    def _fully_on_gpu(model):
        # /api/ps reports runner memory, not the on-disk size from /api/tags.
        return 0 < model.get("size", 0) == model.get("size_vram", 0)

    def load(self, name, size, target_id, context, progress):
        """Start and warm a model on selected hardware after fit/capacity checks.

        Verify residency and GPU use afterward; unload the new model if prior residency was lost
        or GPU execution fell back.
        """
        state = self.status()
        slot = next((slot for slot in state["slots"] if slot["id"] == target_id), None)
        if slot and slot.get("supported", True) and slot.get("error"):
            raise RuntimeStartupSummary([slot["error"]])
        if not slot or not slot["available"]:
            raise ValueError("Select a free, supported hardware slot")
        client = self.client(target_id, register=True)
        started = False
        if size is None:
            progress({"status": "Starting selected runtime to verify downloaded model inventory"})
            self._start_client(target_id, client, progress)
            started = True
            size = self._downloaded_size(client, name)
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
            self._start_client(target_id, client, progress)
        return self._warm(client, name, target_id, context)

    def _warm(self, client, name, target_id, context, *, exclusive=False):
        # Recheck capacity after startup; free-memory estimates include resident models.
        previous = client.api("GET", "/api/ps").get("models", [])
        normalized = name if ":" in name else name + ":latest"
        if any(model.get("name", model.get("model")) == normalized for model in previous):
            raise ValueError("Model is already loaded on this hardware")
        if len(previous) >= self.settings.max_loaded_models or (exclusive and previous):
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
                "keep_alive": keep_alive_value(self.settings),
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
        if exclusive and not self._fully_on_gpu(resident):
            raise GPUPlacementError(
                "Ollama did not load the full model into GPU memory; try more VRAM or a shorter context"
            )
        return result

    def delete(self, name, progress):
        """Delete shared weights only after verifying every registered runtime is idle for this model."""
        normalized = name if ":" in name else name + ":latest"
        if any(group["model"] == normalized for group in self.deployments.values()):
            raise ValueError("Stop the model on every hardware target before deleting it")
        # Do not use the aggregate status: it intentionally catches per-slot errors.
        # Unknown residency must fail closed for a destructive storage operation.
        with self.lock:
            identities = list(self.records)
        for identity in identities:
            state = self.client(identity).status()
            if state.get("error"):
                raise DockerError("Cannot verify model residency before deletion")
            if any(
                model.get("name", model.get("model")) == normalized
                for model in state.get("loaded", [])
            ):
                raise ValueError("Stop the model on every hardware target before deleting it")
        client = self.client("cpu", register=True)
        client.start(progress=progress)
        progress({"status": "Deleting model from shared storage"})
        result = client.api("DELETE", "/api/delete", {"model": name})
        self.storage.remember(client.api("GET", "/api/tags").get("models", []))
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
        for identity in list(self.deployments):
            self._save_deployment(identity)

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
