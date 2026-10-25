"""Offline states regression tests for Llameros behavior and integration boundaries."""

import pytest
from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker, sign_in, wait_job
from test_core import client as core_client

from llameros.app import create_app
from llameros.config import Settings

client = pytest.fixture(core_client.__wrapped__)


def test_container_rejects_same_state_operations(client):
    """Verify container rejects same state operations."""
    sign_in(client)
    client.post("/api/container/stop", json={})
    assert "runtime-error" in wait_job(client)["error"]
    client.post("/api/container/start", json={})
    assert wait_job(client)["state"] == "completed"
    client.post("/api/container/start", json={})
    assert "runtime-error" in wait_job(client)["error"]


def test_only_loaded_models_can_generate_and_model_transitions_are_guarded(client):
    """Verify only loaded models can generate and model transitions are guarded."""
    sign_in(client)
    client.post("/api/container/start", json={})
    wait_job(client)
    client.post("/api/generate", json={"model": "gemma3:270m", "prompt": "Hello"})
    assert "runtime-error" in wait_job(client)["error"]
    client.post("/api/models/unload", json={"model": "gemma3:270m"})
    assert "runtime-error" in wait_job(client)["error"]
    client.post("/api/models/load", json={"model": "gemma3:270m"})
    assert wait_job(client)["state"] == "completed"
    client.post("/api/models/load", json={"model": "gemma3:270m"})
    assert "runtime-error" in wait_job(client)["error"]
    client.post("/api/generate", json={"model": "gemma3:270m", "prompt": "Hello"})
    assert wait_job(client)["state"] == "completed"
    client.post("/api/models/unload", json={"model": "gemma3:270m"})
    assert wait_job(client)["state"] == "completed"


def test_second_model_requires_unloading_first_without_implicit_eviction(tmp_path):
    """Verify second model requires unloading first without implicit eviction."""

    class TwoModelsDocker(FakeDocker):
        def status(self):
            return {
                **super().status(),
                "models": [
                    {"name": "first:latest", "size": 300_000_000},
                    {"name": "second:latest", "size": 300_000_000},
                ],
            }

    docker = TwoModelsDocker()
    docker.running = True
    app = create_app(
        Settings(state_dir=tmp_path),
        docker=docker,
        authenticator=lambda *args: True,
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as browser:
        sign_in(browser)
        browser.post("/api/models/load", json={"model": "first:latest"})
        assert wait_job(browser)["state"] == "completed"
        before = len([call for call in docker.calls if call[0] == "POST"])
        browser.post("/api/models/load", json={"model": "second:latest"})
        assert "runtime-error" in wait_job(browser)["error"]
        assert docker.loaded == [{"name": "first:latest"}]
        assert len([call for call in docker.calls if call[0] == "POST"]) == before
        browser.post("/api/models/unload", json={"model": "first:latest"})
        assert wait_job(browser)["state"] == "completed"
        browser.post("/api/models/load", json={"model": "second:latest"})
        assert wait_job(browser)["state"] == "completed"
        assert docker.loaded == [{"name": "second:latest"}]
