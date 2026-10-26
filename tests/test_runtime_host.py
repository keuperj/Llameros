"""Private runtime HTTP must retain a loopback Host accepted by Ollama."""

import httpx
import pytest

from llameros.config import Settings
from llameros.docker import Docker


@pytest.mark.parametrize("operation", ["version", "tags", "pull"])
def test_runtime_requests_use_localhost(tmp_path, monkeypatch, operation):
    docker = Docker(Settings(state_dir=tmp_path))
    monkeypatch.setattr(docker, "ensure_running", lambda: None)
    monkeypatch.setattr(docker, "socket_path", lambda: "/unused/private.sock")
    monkeypatch.setattr(docker.storage, "remember", lambda models: None)
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.headers["host"] != "localhost:11434":
            return httpx.Response(403)
        if request.url.path == "/api/pull":
            return httpx.Response(200, content=b'{"status":"success"}\n')
        return httpx.Response(200, json={"version": "test", "models": []})

    def transport(**kwargs):
        assert kwargs == {"uds": "/unused/private.sock"}
        return httpx.MockTransport(handle)

    monkeypatch.setattr(httpx, "HTTPTransport", transport)
    if operation == "pull":
        docker.pull("test:latest", lambda value: None)
        assert calls == ["/api/pull", "/api/tags"]
    else:
        docker.api("GET", f"/api/{operation}")
        assert calls == [f"/api/{operation}"]
