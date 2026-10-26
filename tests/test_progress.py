"""Offline progress regression tests for Llameros behavior and integration boundaries."""

import json
import threading

import httpx
import pytest

from llameros.app import Jobs
from llameros.config import Settings
from llameros.docker import Docker, DockerError
from llameros.storage import Store


def test_start_cached_image_skips_download_and_reports_stages(tmp_path):
    """Verify start cached image skips download and reports stages."""
    docker = Docker(Settings(state_dir=tmp_path))
    docker.check_daemon = lambda: None
    docker.inspect = lambda: None
    docker.image_available = lambda: True
    docker.api = lambda *args, **kwargs: {}
    calls, stages = [], []
    docker.command = lambda *args, **kwargs: calls.append(args) or ""
    docker.pull_image = lambda progress: pytest.fail("Cached image should not be downloaded")
    docker.start(stages.append)
    assert calls[0][:2] == ("image", "ls")
    assert calls[1] == ("image", "tag", docker.image, docker.local_image_name)
    assert calls[2][0] == "run"
    assert any("Creating" in stage["status"] for stage in stages)
    assert stages[-1]["phase"] == "readiness"


def test_start_downloads_missing_image_before_creating_container(tmp_path):
    """Verify start downloads missing image before creating container."""
    docker = Docker(Settings(state_dir=tmp_path))
    docker.check_daemon = lambda: None
    docker.inspect = lambda: None
    docker.image_available = lambda: False
    docker.api = lambda *args, **kwargs: {}
    calls = []
    docker.pull_image = lambda progress: calls.append("pull")
    docker.command = lambda *args, **kwargs: calls.append(args[0]) or ""
    docker.start()
    assert calls == ["pull", "image", "image", "run"]


@pytest.mark.parametrize("local", [True, False])
def test_stopped_status_reports_image_and_container_availability(tmp_path, local):
    """Verify stopped status reports image and container availability."""
    docker = Docker(Settings(state_dir=tmp_path))
    docker.check_daemon = lambda: None
    docker.image_available = lambda: local
    docker.inspect = lambda: None
    status = docker.status()
    assert status["image_available"] is local
    assert status["container_exists"] is False
    docker.inspect = lambda: {"State": {"Running": False}}
    assert docker.status()["container_exists"] is True


def mock_pull_client(monkeypatch, events, status=200):
    """Replace daemon HTTP with supplied JSON progress events and return captured requests."""
    original = httpx.Client
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"ApiVersion": "1.45"})
        return httpx.Response(
            status, content="\n".join(json.dumps(event) for event in events).encode()
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), base_url="http://docker"),
    )
    return requests


@pytest.mark.parametrize("present", [True, False])
def test_digest_availability_includes_untagged_images(tmp_path, monkeypatch, present):
    docker = Docker(Settings(state_dir=tmp_path))

    def command(*args, **kwargs):
        assert args == (
            "image",
            "ls",
            "--all",
            "--digests",
            "--format",
            "{{.Repository}}@{{.Digest}}",
        )
        return docker.image if present else "ollama/ollama@sha256:" + "0" * 64

    monkeypatch.setattr(docker, "command", command)
    assert docker.image_available() is present


def test_pull_accepts_untagged_digest_before_alias_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///example/docker.sock")
    docker = Docker(Settings(state_dir=tmp_path))
    monkeypatch.setattr(docker, "check_daemon", lambda: None)

    def command(*args, **kwargs):
        assert "--all" in args and "--digests" in args
        return docker.image

    monkeypatch.setattr(docker, "command", command)
    mock_pull_client(monkeypatch, [{"status": "Pull complete"}])
    docker.pull_image(lambda value: None)


def test_docker_pull_stream_reports_real_layer_download_and_extraction(tmp_path, monkeypatch):
    """Verify docker pull stream reports real layer download and extraction."""
    monkeypatch.setenv("DOCKER_HOST", "unix:///example/docker.sock")
    docker = Docker(Settings(state_dir=tmp_path))
    docker.check_daemon = lambda: None
    docker.image_available = lambda: True
    requests = mock_pull_client(
        monkeypatch,
        [
            {
                "status": "Downloading",
                "id": "layer-one",
                "progressDetail": {"current": 25, "total": 100},
            },
            {
                "status": "Extracting",
                "id": "layer-one",
                "progressDetail": {"current": 50, "total": 200},
            },
            {"status": "Pull complete", "id": "layer-one"},
        ],
    )
    stages = []
    docker.pull_image(stages.append)
    assert stages[0]["completed"] == 25 and stages[0]["total"] == 100
    assert stages[1]["status"] == "Extracting" and stages[1]["total"] == 200
    assert requests[-1].url.path == "/v1.45/images/create"
    assert requests[-1].url.params["fromImage"] == docker.image


@pytest.mark.parametrize("events", [[{"error": "registry denied"}], []])
def test_docker_pull_rejects_stream_errors_or_empty_stream(tmp_path, monkeypatch, events):
    """Verify docker pull rejects stream errors or empty stream."""
    monkeypatch.setenv("DOCKER_HOST", "unix:///example/docker.sock")
    docker = Docker(Settings(state_dir=tmp_path))
    docker.check_daemon = lambda: None
    docker.image_available = lambda: True
    mock_pull_client(monkeypatch, events)
    with pytest.raises(DockerError):
        docker.pull_image(lambda value: None)


def test_job_tracks_layers_finishes_at_100_and_status_is_a_snapshot(tmp_path):
    """Verify job tracks layers finishes at 100 and status is a snapshot."""
    store = Store(tmp_path / "history.sqlite3")
    jobs = Jobs(store)
    entered, release = threading.Event(), threading.Event()

    def work(progress):
        progress(
            {
                "id": "a",
                "phase": "docker-image",
                "status": "Downloading",
                "completed": 25,
                "total": 100,
            }
        )
        progress(
            {
                "id": "b",
                "phase": "docker-image",
                "status": "Downloading",
                "completed": 50,
                "total": 200,
            }
        )
        entered.set()
        release.wait(3)

    try:
        jobs.submit("container-start", "", work)
        assert entered.wait(2)
        snapshot = jobs.status()
        assert len(snapshot["progress"]["layers"]) == 2
        snapshot["progress"]["layers"][0]["completed"] = -1
        assert jobs.status()["progress"]["layers"][0]["completed"] == 25
    finally:
        release.set()
        jobs.pool.shutdown(wait=True)
    assert jobs.status()["state"] == "completed"
    assert jobs.status()["progress"]["total"] == 1
    assert jobs.status()["finished"] >= jobs.status()["started"]
