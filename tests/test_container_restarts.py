"""Owned-container restart policy and durable, deduplicated error reporting."""

import json
import os

import pytest

from llameros.config import Settings
from llameros.docker import Docker, DockerError
from llameros.storage import Store


def observation(count=0, started="2026-10-31T10:00:00Z", identity="container-1"):
    return {"Id": identity, "RestartCount": count, "State": {"StartedAt": started, "Running": True}}


def test_restart_observations_are_persistent_and_classed_as_errors(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    store.observe_container("ollama", observation())
    store.observe_container("ollama", observation(2))
    store = Store(store.path)
    store.observe_container("ollama", observation(2))
    with store.connect() as db:
        rows = db.execute("SELECT * FROM events").fetchall()
        assert len(rows) == 1
        assert rows[0]["kind"] == "container-restart:failed"
        assert "2 restart(s)" in rows[0]["detail"]
    store.observe_container("ollama", observation(2, "2026-10-31T11:00:00Z"))
    # An explicitly requested start should establish the new timestamp baseline.
    store.observe_container("ollama", observation(2, "2026-10-31T12:00:00Z"), expected_start=True)
    store.observe_container("ollama", observation(0, identity="replacement"))
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_initial_existing_restarts_and_cpu_gpu_deduplication(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    for name in ("cpu", "gpu"):
        store.observe_container(name, observation(3))
        store.observe_container(name, observation(3))
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_run_policy_and_existing_policy_upgrade(tmp_path, monkeypatch):
    docker = Docker(Settings(state_dir=tmp_path))
    args = docker.run_args()
    assert args[args.index("--restart") + 1] == "unless-stopped"
    calls = []
    monkeypatch.setattr(docker, "command", lambda *args, **kwargs: calls.append(args))
    docker.ensure_restart_policy({"HostConfig": {"RestartPolicy": {"Name": "no"}}})
    assert calls == [("update", "--restart", "unless-stopped", docker.name)]
    docker.ensure_restart_policy({"HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}}})
    assert len(calls) == 1


def test_inspection_reports_restart_before_api_readiness_and_requires_ownership(
    tmp_path, monkeypatch
):
    docker = Docker(Settings(state_dir=tmp_path))
    observed = []
    docker.observer = lambda *args, **kwargs: observed.append((args, kwargs))
    data = {**observation(1), "Config": {"Labels": {"io.llameros.owner": str(os.getuid())}}}
    monkeypatch.setattr(
        docker,
        "command",
        lambda *args, **kwargs: docker.name if args[0] == "container" else json.dumps([data]),
    )
    assert docker.inspect() == data
    assert len(observed) == 1
    data["Config"]["Labels"] = {}
    with pytest.raises(DockerError, match="ownership"):
        docker.inspect()
    assert len(observed) == 1
