from unittest.mock import Mock

import pytest

from llameros.config import Settings
from llameros.docker import Docker, DockerError


@pytest.mark.parametrize(
    ("endpoint", "options", "allowed"),
    [
        ("unix:///run/user/1002/docker.sock", '["name=rootless"]', True),
        ("unix:///run/user/1002/docker.sock", '["name=seccomp,profile=builtin"]', False),
        ("unix:///var/run/docker.sock", '["name=rootless"]', False),
        ("ssh://remote", '["name=rootless"]', False),
    ],
)
def test_restart_only_targets_standard_rootless_user_service(
    tmp_path, monkeypatch, endpoint, options, allowed
):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1002")
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    docker = Docker(Settings(state_dir=tmp_path, allow_rootful_docker=True))
    docker.command = Mock(return_value=options)
    restart = Mock()
    monkeypatch.setattr("llameros.docker.subprocess.run", restart)

    if not allowed:
        with pytest.raises(DockerError, match="standard rootless user socket"):
            docker.restart_daemon()
        restart.assert_not_called()
        return

    docker.restart_daemon()
    restart.assert_called_once_with(
        ["systemctl", "--user", "--no-ask-password", "restart", "docker.service"],
        capture_output=True,
        timeout=180,
        check=True,
    )
    assert docker.command.call_count == 2  # Verify Docker answers again after restart.


def test_restart_respects_docker_context_over_docker_host(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1002")
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1002/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    docker = Docker(Settings(state_dir=tmp_path))
    docker.command = Mock(return_value="ssh://remote")
    restart = Mock()
    monkeypatch.setattr("llameros.docker.subprocess.run", restart)

    with pytest.raises(DockerError, match="standard rootless user socket"):
        docker.restart_daemon()
    restart.assert_not_called()
