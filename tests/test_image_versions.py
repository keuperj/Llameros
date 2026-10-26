"""Offline image-version coexistence and owned-container upgrade tests."""

import pytest

from llameros.config import Settings
from llameros.docker import Docker, DockerError


def test_version_alias_is_digest_specific_and_does_not_retag_upstream(tmp_path, monkeypatch):
    docker = Docker(Settings(state_dir=tmp_path))
    calls = []
    monkeypatch.setattr(
        docker, "command", lambda *args, **kwargs: calls.append(args) or "ollama/ollama:latest"
    )
    docker.name_local_image()
    assert calls[-1] == ("image", "tag", docker.image, docker.local_image_name)
    assert docker.local_image_name.endswith(docker.image.split("sha256:")[1])
    assert docker.run_args()[-1] == docker.image


def test_alias_collision_not_overwritten(tmp_path, monkeypatch):
    docker = Docker(Settings(state_dir=tmp_path))

    def command(*args, **kwargs):
        if args[:2] == ("image", "ls"):
            return docker.local_image_name
        if args[:2] == ("image", "inspect"):
            return "correct" if args[-1] == docker.image else "wrong"
        pytest.fail(f"Unexpected mutation: {args}")

    monkeypatch.setattr(docker, "command", command)
    with pytest.raises(DockerError, match="refusing to overwrite"):
        docker.name_local_image()


@pytest.mark.parametrize("running,failed_pull", [(False, False), (True, False), (False, True)])
def test_old_container_preserved_on_upgrade(tmp_path, monkeypatch, running, failed_pull):
    docker = Docker(Settings(state_dir=tmp_path))
    calls, progress = [], []
    monkeypatch.setattr(docker, "check_daemon", lambda: None)
    monkeypatch.setattr(
        docker,
        "inspect",
        lambda: {"Config": {"Image": "ollama/ollama:old"}, "State": {"Running": running}},
    )
    monkeypatch.setattr(docker, "image_available", lambda: "pull" in calls)

    def pull(callback):
        if failed_pull:
            raise DockerError("pull failed")
        calls.append("pull")

    monkeypatch.setattr(docker, "pull_image", pull)
    monkeypatch.setattr(docker, "name_local_image", lambda: calls.append("alias"))
    monkeypatch.setattr(docker, "command", lambda *args, **kwargs: calls.append(args) or "")
    monkeypatch.setattr(docker, "api", lambda *args, **kwargs: {})
    if running or failed_pull:
        with pytest.raises(DockerError):
            docker.start(progress.append)
        assert not any(isinstance(call, tuple) for call in calls)
    else:
        docker.start(progress.append)
        assert calls[:2] == ["pull", "alias"]
        assert calls[2][:2] == ("rename", docker.name)
        assert calls[2][2].startswith(docker.name + "-old-")
        assert calls[3][0] == "run"
        assert calls[3][-1] == docker.image
        assert not any(
            isinstance(call, tuple) and call[0] in {"rm", "rmi", "stop"} for call in calls
        )
