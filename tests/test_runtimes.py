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
            if body.get("keep_alive") == 0:
                self.state["loaded"] = []
            elif not body.get("prompt"):
                self.state["loaded"] = [
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
    manager, states, cfg, hw = pool
    manager.client("cpu", register=True).start()
    ids = [slot["id"] for slot in manager.status()["slots"] if slot["supported"]]
    assert len(ids) == 3
    for name, target in zip(("one:latest", "two:latest", "three:latest"), ids, strict=True):
        manager.load(name, 300_000_000, target, 2048, lambda value: None)
    state = manager.status()
    assert len(state["loaded"]) == 3
    assert not any(slot["available"] for slot in state["slots"])
    assert len({model["base_url"] for model in state["loaded"]}) == 3
    assert manager.model_client("two:latest", ids[1]).target["id"] == ids[1]
    with pytest.raises(ValueError, match="free"):
        manager.load("other:latest", 1, ids[1], 4096, lambda value: None)
    client = manager.model_client("two:latest", ids[1])
    client.api("POST", "/api/generate", {"model": "two:latest", "keep_alive": 0})
    assert next(slot for slot in manager.status()["slots"] if slot["id"] == ids[1])["available"]
    assert len(manager.status()["loaded"]) == 2
    again = Runtimes(cfg, lambda: hw)
    assert again.records == manager.records
    cpu = states[cfg.container_name]
    assert (
        next(call[2] for call in cpu["calls"] if call[0] == "POST")["options"]["num_thread"]
        == HARDWARE["logical_cores"]
    )
    manager.stop()
    assert not manager.status()["running"]


def test_gpu_fit_unknown_target_and_cpu_fallback_rejected(pool):
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


def test_app_routes_inference_and_stop_to_selected_slot(pool):
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


def test_legacy_container_requires_explicit_recreation(tmp_path):
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
