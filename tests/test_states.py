import pytest
from test_core import client as core_client
from test_core import sign_in, wait_job

client = pytest.fixture(core_client.__wrapped__)


def test_container_rejects_same_state_operations(client):
    sign_in(client)
    client.post("/api/container/stop", json={})
    assert "already stopped" in wait_job(client)["error"]
    client.post("/api/container/start", json={})
    assert wait_job(client)["state"] == "completed"
    client.post("/api/container/start", json={})
    assert "already running" in wait_job(client)["error"]


def test_only_loaded_models_can_generate_and_model_transitions_are_guarded(client):
    sign_in(client)
    client.post("/api/container/start", json={})
    wait_job(client)
    client.post("/api/generate", json={"model": "gemma3:270m", "prompt": "Hello"})
    assert "Load the model" in wait_job(client)["error"]
    client.post("/api/models/unload", json={"model": "gemma3:270m"})
    assert "already unloaded" in wait_job(client)["error"]
    client.post("/api/models/load", json={"model": "gemma3:270m"})
    assert wait_job(client)["state"] == "completed"
    client.post("/api/models/load", json={"model": "gemma3:270m"})
    assert "already loaded" in wait_job(client)["error"]
    client.post("/api/generate", json={"model": "gemma3:270m", "prompt": "Hello"})
    assert wait_job(client)["state"] == "completed"
    client.post("/api/models/unload", json={"model": "gemma3:270m"})
    assert wait_job(client)["state"] == "completed"
