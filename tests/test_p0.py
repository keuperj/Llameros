"""P0 boundary regressions, with no live Docker or PAM dependencies."""

import asyncio
import os
import subprocess

import httpx
import pytest

from llameros.body_limits import BodyLimits
from llameros.config import Settings
from llameros.docker import Docker, DockerError
from llameros.runtime_transport import close_relays, private_socket


@pytest.mark.parametrize(
    "headers", [[], [(b"content-length", b"2")], [(b"content-length", b"140000")]]
)
def test_actual_body_limit_before_application(headers):
    async def check():
        called, sent = [], []

        async def app(*args):
            called.append(True)

        messages = iter(
            [
                {"type": "http.request", "body": b" " * 70000, "more_body": True},
                {"type": "http.request", "body": b" " * 70000},
            ]
        )

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        await BodyLimits(app, 16777216)(
            {"type": "http", "path": "/api/login", "headers": headers}, receive, send
        )
        assert sent[0]["status"] == 413
        assert not called

    asyncio.run(check())


def test_body_deadline_and_depth():
    async def check(slow):
        sent = []

        async def app(*args):
            pytest.fail("Application must not run")

        async def receive():
            if slow:
                await asyncio.sleep(1)
            return {"type": "http.request", "body": b"[" * 65 + b"0" + b"]" * 65}

        async def send(message):
            sent.append(message)

        await BodyLimits(app, 100000, timeout=0.01)(
            {"type": "http", "path": "/api/login", "headers": []}, receive, send
        )
        assert sent[0]["status"] == (408 if slow else 400)

    asyncio.run(check(True))
    asyncio.run(check(False))


def test_runtime_isolation_and_rootless_default(tmp_path):
    cfg = Settings(state_dir=tmp_path)
    assert cfg.allow_rootful_docker is False
    docker = Docker(cfg)
    safe = {
        "HostConfig": {"NetworkMode": "bridge"},
        "Config": {"Image": docker.image, "Env": ["OLLAMA_HOST=127.0.0.1:11434"]},
    }
    docker.require_isolation(safe)
    for host in (
        {"NetworkMode": "host"},
        {"NetworkMode": "bridge", "PortBindings": {"11434/tcp": [{}]}},
        {"NetworkMode": "bridge", "PublishAllPorts": True},
    ):
        with pytest.raises(DockerError, match="legacy"):
            docker.require_isolation({**safe, "HostConfig": host})
    with pytest.raises(DockerError, match="legacy"):
        docker.require_isolation({**safe, "Config": {"Image": docker.image, "Env": []}})


def test_private_relay_http_and_cleanup(monkeypatch):
    original = subprocess.Popen
    calls = []

    def fake_exec(args, **kwargs):
        calls.append(args)
        return original(
            [
                "python3",
                "-u",
                "-c",
                "import sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write(b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\nConnection: close\\r\\n\\r\\n{}'); sys.stdout.buffer.flush()",
            ],
            **kwargs,
        )

    monkeypatch.setattr("llameros.runtime_transport.subprocess.Popen", fake_exec)
    path = private_socket("test-container")
    try:
        assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700
        assert os.stat(path).st_mode & 0o777 == 0o600
        with httpx.Client(transport=httpx.HTTPTransport(uds=path)) as client:
            assert client.get("http://ollama/api/tags").json() == {}
        assert calls[0][:5] == ["docker", "exec", "-i", "test-container", "bash"]
    finally:
        close_relays()
    assert not os.path.exists(path)
