"""Offline runtimes regression tests for Llameros behavior and integration boundaries."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_core import HARDWARE, sign_in, wait_job

from llameros.app import create_app
from llameros.config import Settings
from llameros.docker import Docker, DockerError
from llameros.hardware import GIB
from llameros.model_storage import ModelStorage
from llameros.runtimes import Runtimes

GPUS = [
    {
        "id": "GPU-aaaa-1111",
        "name": "First NVIDIA",
        "vendor": "nvidia",
        "free": 8 * GIB,
        "total": 8 * GIB,
    },
    {
        "id": "GPU-bbbb-2222",
        "name": "Second NVIDIA",
        "vendor": "nvidia",
        "free": 8 * GIB,
        "total": 8 * GIB,
    },
    {"id": "intel", "name": "Intel integrated", "vendor": "intel", "free": None},
]


@pytest.fixture
def pool(tmp_path, monkeypatch):
    """Build isolated runtime slots with fake Docker state and test-controlled hardware."""
    states = {}

    class FakeSlot:
        def __init__(self, settings, *, target=None, name=None, storage=None):
            self.settings, self.target = settings, target
            self.name = name or settings.container_name
            self.storage = storage or ModelStorage(settings)
            self.state = states.setdefault(self.name, {"running": False, "loaded": [], "calls": []})

        def status(self):
            return {
                **self.state,
                "models": [
                    {"name": name, "size": 300_000_000}
                    for name in ("one:latest", "two:latest", "three:latest")
                ],
                "image_available": True,
                "container_exists": True,
            }

        def storage_info(self):
            return self.storage.info()

        def ensure_running(self):
            if not self.state["running"]:
                raise DockerError("Not running")

        def start(self, progress=lambda value: None):
            self.state["running"] = True

        def stop(self):
            self.state.update(running=False, loaded=[])

        def api(self, method, path, body=None, **kwargs):
            self.state["calls"].append((method, path, body))
            if path == "/api/ps":
                return {"models": self.state["loaded"]}
            if path == "/api/tags":
                return {"models": self.status()["models"]}
            if path == "/api/show":
                return {
                    "capabilities": ["embedding"]
                    if body["model"].startswith("embed")
                    else ["completion"]
                }
            if body.get("keep_alive") == 0:
                self.state["loaded"] = [
                    model for model in self.state["loaded"] if model["name"] != body["model"]
                ]
            elif not body.get("prompt"):
                self.state["loaded"] = [
                    model for model in self.state["loaded"] if model["name"] != body["model"]
                ] + [
                    {
                        "name": body["model"],
                        "size_vram": 300_000_000 if self.target["vendor"] != "cpu" else 0,
                    }
                ]
            return {
                "model": body["model"],
                "response": "Hello",
                "eval_count": 1,
                "prompt_eval_count": 1,
                "total_duration": 1,
                "eval_duration": 1,
            }

    monkeypatch.setattr("llameros.runtimes.Docker", FakeSlot)
    cfg = Settings(state_dir=tmp_path)
    hardware = {**HARDWARE, "gpus": GPUS}
    return Runtimes(cfg, lambda: hardware), states, cfg, hardware


def test_cpu_and_two_gpu_models_coexist_with_stable_ports_and_routing(pool):
    """Verify cpu and two gpu models coexist with stable ports and routing."""
    manager, states, cfg, hw = pool
    manager.client("cpu", register=True).start()
    ids = [slot["id"] for slot in manager.status()["slots"] if slot["supported"]]
    assert len(ids) == 3
    for name, target in zip(("one:latest", "two:latest", "three:latest"), ids, strict=True):
        manager.load(name, 300_000_000, target, 2048, lambda value: None)
    state = manager.status()
    assert len(state["loaded"]) == 3

    assert all(slot["available"] for slot in state["slots"] if slot["supported"])
    assert {model["base_url"] for model in state["loaded"]} == {"/inference"}
    assert manager.model_client("two:latest", ids[1]).target["id"] == ids[1]
    with pytest.raises(ValueError, match="already loaded"):
        manager.load("two:latest", 1, ids[1], 4096, lambda value: None)
    client = manager.model_client("two:latest", ids[1])
    client.api("POST", "/api/generate", {"model": "two:latest", "keep_alive": 0})
    assert next(slot for slot in manager.status()["slots"] if slot["id"] == ids[1])["available"]
    assert len(manager.status()["loaded"]) == 2
    again = Runtimes(cfg, lambda: hw)
    assert again.records == manager.records
    cpu = states[cfg.container_name]
    options = next(call[2] for call in cpu["calls"] if call[1] == "/api/generate")["options"]
    assert options["num_gpu"] == 0
    assert "num_thread" not in options
    manager.stop()
    assert not manager.status()["running"]


def test_unified_gpu_uses_current_host_memory(pool):
    manager, states, cfg, hw = pool
    hw["gpus"] = [
        {
            "id": "GPU-spark",
            "name": "NVIDIA GB10",
            "vendor": "nvidia",
            "unified_memory": True,
            "free": None,
            "total": 128 * GIB,
        }
    ]
    manager.refresh()
    target = next(slot for slot in manager.targets.values() if slot["vendor"] == "nvidia")
    assert target["supported"]
    manager.load("spark:latest", 300_000_000, target["id"], 2048, lambda value: None)
    hw["memory_available"] = 100_000_000
    with pytest.raises(ValueError, match="system RAM"):
        manager.load("other:latest", 300_000_000, target["id"], 2048, lambda value: None)


def test_gpu_fit_unknown_target_and_cpu_fallback_rejected(pool):
    """Verify gpu fit unknown target and cpu fallback rejected."""
    manager, _, _, _ = pool
    target = next(
        key for key, value in manager.targets.items() if value.get("device") == "GPU-aaaa-1111"
    )
    with pytest.raises(ValueError, match="free"):
        manager.load("one", 1, "invented", 4096, lambda value: None)
    with pytest.raises(ValueError, match="VRAM"):
        manager.load("one", 7 * GIB, target, 4096, lambda value: None)
    unsupported = next(key for key, value in manager.targets.items() if value["vendor"] == "intel")
    with pytest.raises(ValueError, match="free"):
        manager.load("one", 1, unsupported, 4096, lambda value: None)
    gpu = manager.client(target, register=True)
    original = gpu.api

    def fallback(method, path, body=None, **kwargs):
        result = original(method, path, body, **kwargs)
        if path == "/api/ps":
            return {"models": [{**model, "size_vram": 0} for model in result["models"]]}
        return result

    gpu.api = fallback
    manager.client = lambda identity="cpu", **kwargs: gpu
    manager.status = lambda: {"slots": [{"id": target, "available": True}]}
    with pytest.raises(DockerError, match="fell back"):
        manager.load("one", 300_000_000, target, 4096, lambda value: None)
    assert not gpu.state["loaded"]


def test_isolated_container_arguments_share_storage_but_not_devices(tmp_path, monkeypatch):
    """Verify isolated container arguments share storage but not devices."""
    cfg = Settings(state_dir=tmp_path, gpu="nvidia")
    target = {"id": "gpu-test", "device": "GPU-aaaa-1111"}
    args = Docker(cfg, target=target, name=cfg.container_name + "-gpu-test").run_args()
    assert args[args.index("--gpus") + 1] == "device=GPU-aaaa-1111"
    assert f"{cfg.container_name}-models:/root/.ollama" in args
    assert "OLLAMA_NOPRUNE=1" in args
    assert cfg.container_name + "-gpu-test" in args
    cpu = Docker(replace(cfg, gpu="cpu"), target={"id": "cpu"}).run_args()
    assert "--gpus" not in cpu and "--device" not in cpu
    assert "CUDA_VISIBLE_DEVICES=-1" in cpu
    amd_client = Docker(
        replace(cfg, gpu="amd"), target={"id": "gpu-amd", "device": "/dev/dri/renderD129"}
    )
    monkeypatch.setattr(
        "llameros.docker.Path.stat", lambda path: type("Stat", (), {"st_gid": 44})()
    )
    amd = amd_client.run_args()
    assert "/dev/dri/renderD129" in amd and "/dev/dri" not in amd
    assert "--group-add" in amd


@pytest.mark.parametrize("cpu_percent", [0, 75, 100])
def test_cpu_load_ignores_utilization_without_reserving_all_threads(pool, cpu_percent):
    """Verify cpu load ignores utilization without reserving all threads."""
    manager, states, cfg, hw = pool
    hw["cpu_percent"] = cpu_percent
    slot = next(slot for slot in manager.status()["slots"] if slot["id"] == "cpu")
    assert slot["available"]
    assert slot["label"] == "CPU"
    manager.load("one:latest", 300_000_000, "cpu", 4096, lambda value: None)
    options = next(
        body["options"]
        for method, path, body in states[cfg.container_name]["calls"]
        if method == "POST" and path == "/api/generate"
    )
    assert options == {"num_ctx": 4096, "num_gpu": 0}
    manager.load("two:latest", 300_000_000, "cpu", 4096, lambda value: None)
    with pytest.raises(ValueError, match="free"):
        manager.load("three:latest", 300_000_000, "cpu", 4096, lambda value: None)


def test_app_routes_inference_and_stop_to_selected_slot(pool):
    """Verify app routes inference and stop to selected slot."""
    _, states, cfg, hw = pool
    app = create_app(
        cfg, hardware_provider=lambda: hw, authenticator=lambda *args: True, sampling=False
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        sign_in(client)
        client.post("/api/container/start", json={})
        assert wait_job(client)["state"] == "completed"
        status = client.get("/api/status").json()
        target = next(
            slot["id"] for slot in status["ollama"]["slots"] if slot["vendor"] == "nvidia"
        )
        for slot in ("cpu", target):
            client.post("/api/models/load", json={"model": "one:latest", "target": slot})
            assert wait_job(client)["state"] == "completed"
        client.post(
            "/api/generate", json={"model": "one:latest", "target": target, "prompt": "Hello"}
        )
        assert wait_job(client)["state"] == "completed"
        assert any(
            call[2] and call[2].get("prompt") == "Hello"
            for call in states[cfg.container_name + "-" + target]["calls"]
        )
        client.post("/api/models/unload", json={"model": "one:latest", "target": target})
        assert wait_job(client)["state"] == "completed"
        loaded = client.get("/api/status").json()["ollama"]["loaded"]
        assert len(loaded) == 1 and loaded[0]["target"] == "cpu"
        hw["cpu_percent"] = 100
        client.post(
            "/api/generate", json={"model": "one:latest", "target": "cpu", "prompt": "Hello CPU"}
        )
        assert wait_job(client)["state"] == "completed"
        options = next(
            body["options"]
            for _, _, body in states[cfg.container_name]["calls"]
            if body and body.get("prompt") == "Hello CPU"
        )
        assert options["num_gpu"] == 0
        assert "num_thread" not in options


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("explicit_target", [False, True])
def test_gpu_load_starts_selected_container_with_cpu_stopped(
    pool, cached, explicit_target, monkeypatch
):
    """A stopped CPU metadata runtime must not prevent starting a GPU model."""
    manager, states, cfg, hw = pool
    slot_type = type(manager.client("cpu"))
    original_api = slot_type.api
    operations = []

    def checked_api(slot, method, path, body=None, **kwargs):
        assert slot.state["running"], "Container must start before receiving model requests"
        operations.append((slot.name, path))
        return original_api(slot, method, path, body, **kwargs)

    monkeypatch.setattr(slot_type, "api", checked_api)
    if cached:
        ModelStorage(cfg).remember([{"name": "one:latest", "size": 300_000_000}])
    app = create_app(
        cfg, hardware_provider=lambda: hw, authenticator=lambda *args: True, sampling=False
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        sign_in(client)
        target = next(
            slot["id"]
            for slot in client.get("/api/status").json()["ollama"]["slots"]
            if slot["vendor"] == "nvidia"
        )
        assert not states[cfg.container_name]["running"]
        response = client.post(
            "/api/models/load",
            json={"model": "one:latest", **({"target": target} if explicit_target else {})},
        )
        assert response.status_code == 202
        job = wait_job(client)
        assert job["state"] == "completed", job
        assert states[cfg.container_name + "-" + target]["running"]
        assert not states[cfg.container_name]["running"]
        assert operations[0] == (cfg.container_name + "-" + target, "/api/tags")
        assert all(name == cfg.container_name + "-" + target for name, _ in operations)
        assert job["targets"] == [target]
        loaded = client.get("/api/status").json()["ollama"]["loaded"]
        assert any(model["name"] == "one:latest" and model["target"] == target for model in loaded)


def test_unknown_model_does_not_warm_gpu_after_inventory_check(pool):
    manager, states, cfg, _ = pool
    target = next(key for key, value in manager.targets.items() if value["vendor"] == "nvidia")
    with pytest.raises(ValueError, match="Download this model"):
        manager.load("missing:latest", None, target, 4096, lambda value: None)
    calls = states[cfg.container_name + "-" + target]["calls"]
    assert any(path == "/api/tags" for _, path, _ in calls)
    assert not any(path == "/api/generate" for _, path, _ in calls)


def test_legacy_container_requires_explicit_recreation(tmp_path):
    """Verify legacy container requires explicit recreation."""
    import json
    import os

    cfg = Settings(state_dir=tmp_path)
    client = Docker(cfg, target={"id": "cpu"})
    data = {
        "State": {"Running": True},
        "Config": {"Labels": {"io.llameros.owner": str(os.getuid())}},
    }
    client.command = lambda *args, **kwargs: (
        cfg.container_name if args[0] == "container" else json.dumps([data])
    )
    with pytest.raises(DockerError, match="Legacy"):
        client.inspect()
    client.target = None
    assert client.inspect()["State"]["Running"]


def test_hardware_detection_keeps_uuid_and_lists_other_vendors(monkeypatch):
    """Verify hardware detection keeps uuid and lists other vendors."""
    import subprocess

    from llameros.hardware import gpu_info

    monkeypatch.setattr(
        "llameros.hardware.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, "NVIDIA test, 8192, 4096, 12, GPU-abcd-1234\n"
        ),
    )
    monkeypatch.setattr("llameros.hardware.Path.glob", lambda *args: [])
    detected = gpu_info()
    assert detected[0]["id"] == "GPU-abcd-1234"
    assert detected[0]["free"] == 4 * GIB


def test_two_models_share_gpu_embedding_warmup_and_individual_stop(pool):
    """Verify two models share gpu embedding warmup and individual stop."""
    manager, states, cfg, hw = pool
    target = next(key for key, value in manager.targets.items() if value.get("vendor") == "nvidia")
    manager.load("one:latest", 300_000_000, target, 4096, lambda value: None)
    manager.load("embed:latest", 300_000_000, target, 4096, lambda value: None)
    loaded = manager.status()["loaded"]
    assert {model["name"] for model in loaded} == {"one:latest", "embed:latest"}
    client = manager.model_client("embed:latest", target)
    assert any(path == "/api/embed" for _, path, _ in client.state["calls"])
    with pytest.raises(ValueError, match="free"):
        manager.load("three:latest", 1, target, 4096, lambda value: None)
    client.api("POST", "/api/generate", {"model": "embed:latest", "keep_alive": 0})
    assert [model["name"] for model in manager.status()["loaded"]] == ["one:latest"]
    hw["gpus"][0]["free"] = 1
    with pytest.raises(ValueError, match="VRAM"):
        manager.load("three:latest", 300_000_000, target, 4096, lambda value: None)
