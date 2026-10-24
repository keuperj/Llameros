import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeCatalog, FakeDocker, sign_in, wait_job

from llameros.app import create_app
from llameros.config import Settings
from llameros.docker import Docker, DockerError
from llameros.model_storage import ModelStorage, scan_models, validate_directory


def make_model(directory: Path):
    manifest = directory / "manifests/registry.ollama.ai/library/gemma3/270m"
    manifest.parent.mkdir(parents=True)
    directory.chmod(0o700)
    blobs = directory / "blobs"
    blobs.mkdir()
    digest = "sha256:" + "a" * 64
    (blobs / digest.replace(":", "-")).write_bytes(b"model")
    descriptor = {"digest": digest, "size": 5}
    manifest.write_text(json.dumps({"config": descriptor, "layers": [descriptor]}))
    return manifest


def test_offline_inventory_and_incomplete_download(tmp_path):
    models = tmp_path / "models"
    manifest = make_model(models)
    result = scan_models(models)
    assert result["models"][0]["name"] == "gemma3:270m"
    assert result["models"][0]["size"] == 10
    (models / "blobs" / ("sha256-" + "a" * 64)).unlink()
    result = scan_models(models)
    assert not result["models"] and "incomplete" in result["warnings"][0]
    manifest.write_text("not json")
    assert scan_models(models)["warnings"]


def test_storage_persistence_and_cache_identity(tmp_path):
    cfg = Settings(state_dir=tmp_path / "state")
    cfg.prepare()
    storage = ModelStorage(cfg)
    assert storage.inventory()["source"] == "unknown"
    storage.remember([{"name": "gemma3:270m"}])
    assert storage.inventory()["source"] == "cache"
    models = tmp_path / "models"
    make_model(models)
    storage.save(str(models))
    restored = ModelStorage(cfg)
    assert restored.directory == str(models)
    assert restored.inventory()["source"] == "disk"
    assert restored.inventory()["models"][0]["name"] == "gemma3:270m"
    assert storage.preference_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "value", ["relative/path", "/", "~/../models", "/tmp/models,readonly", "bad\npath"]
)
def test_invalid_directory(value, tmp_path):
    with pytest.raises(ValueError):
        validate_directory(value, tmp_path / "state", create=True)


def test_reject_home_state_symlinks_and_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state = tmp_path / "state"
    for target in [tmp_path, state, state / "models"]:
        with pytest.raises(ValueError):
            validate_directory(str(target), state, create=True)
    target = tmp_path / "models"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic"):
        validate_directory(str(link), state)
    (target / "private.txt").write_text("do not mount")
    with pytest.raises(ValueError, match="empty directory"):
        validate_directory(str(target), state)
    (target / "private.txt").unlink()
    target.chmod(0o777)
    with pytest.raises(ValueError, match="owned"):
        validate_directory(str(target), state)


def test_storage_change_requires_stop_and_preserves_old_files(tmp_path):
    cfg = Settings(state_dir=tmp_path / "state")
    cfg.prepare()
    docker = Docker(cfg)
    calls = []
    docker.check_daemon = lambda: None
    docker.command = lambda *args, **kwargs: calls.append(args) or ""
    docker.inspect = lambda: {"State": {"Running": True}}
    target = tmp_path / "models"
    with pytest.raises(ValueError, match="Confirm"):
        docker.configure_storage(str(target), False)
    with pytest.raises(DockerError, match="Stop"):
        docker.configure_storage(str(target), True)
    docker.inspect = lambda: {"State": {"Running": False}}
    make_model(target)
    docker.configure_storage(str(target), True)
    assert calls == [("rm", cfg.container_name)]
    assert scan_models(target)["models"]
    args = docker.run_args()
    assert f"type=bind,src={target},dst=/models" in args
    assert "OLLAMA_MODELS=/models" in args
    assert "-v" not in args
    docker.configure_storage("", True)
    assert "-v" in docker.run_args()
    assert scan_models(target)["models"]  # Switching back never deletes weights.


def test_failed_container_removal_restores_preference(tmp_path):
    cfg = Settings(state_dir=tmp_path / "state")
    cfg.prepare()
    docker = Docker(cfg)
    docker.check_daemon = lambda: None
    docker.inspect = lambda: {"State": {"Running": False}}

    def fail(*args, **kwargs):
        raise DockerError("removal failed")

    docker.command = fail
    with pytest.raises(DockerError):
        docker.configure_storage(str(tmp_path / "models"), True)
    assert docker.storage.directory == ""
    assert ModelStorage(cfg).directory == ""


def test_storage_api_auth_csrf_and_downloaded_models(tmp_path):
    app = create_app(
        Settings(state_dir=tmp_path),
        docker=FakeDocker(),
        catalog=FakeCatalog(),
        authenticator=lambda *args: True,
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        assert client.get("/api/storage").status_code == 401
        assert client.get("/api/models").status_code == 401
        sign_in(client)
        assert client.get("/api/storage").json()["mode"] == "volume"
        inventory = client.get("/api/models").json()
        assert inventory["source"] == "cache" and inventory["models"]
        assert (
            client.post(
                "/api/storage",
                json={"directory": "", "confirmed": True},
                headers={"X-CSRF-Token": "bad"},
            ).status_code
            == 403
        )
        assert (
            client.post("/api/storage", json={"directory": "", "confirmed": False}).status_code
            == 202
        )
        assert wait_job(client)["state"] == "failed"
        assert (
            client.post("/api/storage", json={"directory": "", "confirmed": True}).status_code
            == 202
        )
        assert wait_job(client)["state"] == "completed"
        client.post("/api/container/start", json={})
        wait_job(client)
        assert client.get("/api/models").json()["source"] == "live"


def test_storage_changes_share_lifecycle_job_lock(tmp_path):
    docker = FakeDocker()
    entered, release = threading.Event(), threading.Event()

    def block(directory, confirmed):
        entered.set()
        release.wait(5)

    docker.configure_storage = block
    app = create_app(
        Settings(state_dir=tmp_path),
        docker=docker,
        authenticator=lambda *args: True,
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        sign_in(client)
        try:
            client.post("/api/storage", json={"confirmed": True})
            assert entered.wait(2)
            assert client.post("/api/container/start", json={}).status_code == 409
        finally:
            release.set()
        assert wait_job(client)["state"] == "completed"


def test_directory_inventory_available_without_docker(tmp_path):
    cfg = Settings(state_dir=tmp_path / "state")
    cfg.prepare()
    models = tmp_path / "models"
    make_model(models)
    docker = Docker(cfg)
    docker.storage.save(str(models))

    def unavailable():
        raise DockerError("Docker unavailable")

    docker.status = unavailable
    app = create_app(
        cfg,
        docker=docker,
        authenticator=lambda *args: True,
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        sign_in(client)
        result = client.get("/api/models")
        assert result.status_code == 200
        inventory = result.json()
        assert inventory["source"] == "disk"
        assert inventory["models"][0]["name"] == "gemma3:270m"
        assert "Docker unavailable" in inventory["warnings"]
        assert inventory["storage"]["directory"] == str(models)


def test_volume_cache_not_reused_for_different_location(tmp_path):
    cfg = Settings(state_dir=tmp_path / "state")
    cfg.prepare()
    storage = ModelStorage(cfg)
    directory = tmp_path / "models"
    directory.mkdir(mode=0o700)
    storage.save(str(directory))
    storage.remember([{"name": "only-in-directory:latest"}])
    storage.save("")
    assert storage.inventory()["source"] == "unknown"
    assert not storage.inventory()["models"]
