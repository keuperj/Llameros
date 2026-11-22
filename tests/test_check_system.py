"""Exercise the Ubuntu preflight without talking to a real daemon."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "CheckSystem.sh"


@pytest.fixture
def preflight(tmp_path):
    def stub(name, body):
        script = tmp_path / name
        script.write_text(f"#!/bin/bash\n{body}\n")
        script.chmod(0o755)

    stub("id", "if [[ $1 == -u ]]; then echo 1000; else echo llameros; fi")
    stub("uv", 'if [[ $1 == --version ]]; then echo "uv test"; else echo "$TEST_PYTHON"; fi')
    stub("systemctl", "exit 0")
    stub("nvidia-smi", "exit 1")
    stub("nvidia-ctk", "exit 1")
    stub(
        "docker",
        """case "$1" in
    context) echo "$TEST_ENDPOINT" ;;
    info)
        [[ $TEST_SECURITY != offline ]] || exit 1
        echo "29.0|$TEST_SECURITY|no" ;;
    *) echo "Unexpected Docker command: $1" >&2; exit 98 ;;
esac""",
    )

    def run(*args, **settings):
        env = dict(os.environ)
        env.pop("DOCKER_HOST", None)
        env.pop("DOCKER_CONTEXT", None)
        env.update(
            PATH=f"{tmp_path}:/usr/bin:/bin",
            TEST_PYTHON=sys.executable,
            TEST_SECURITY="name=rootless,name=cgroupns",
            TEST_ENDPOINT="unix:///run/user/1000/docker.sock",
        )
        env.update(settings)
        return subprocess.run([SCRIPT, *args], env=env, capture_output=True, text=True, timeout=15)

    return run


def test_reports_active_rootless_daemon_and_skips_gpu_in_cpu_mode(preflight):
    result = preflight("--gpu", "cpu")
    assert "Docker Engine 29.0: rootless" in result.stdout
    assert "GPU checks skipped" in result.stdout
    assert "[OK]   Python " in result.stdout


@pytest.mark.parametrize("allow", [False, True])
def test_rootful_requires_explicit_acknowledgement(preflight, allow):
    flags = ["--allow-rootful"] if allow else []
    result = preflight("--gpu", "cpu", *flags, TEST_SECURITY="name=cgroupns")
    assert f"[{'WARN' if allow else 'FAIL'}] Docker Engine 29.0: rootful" in result.stdout
    if not allow:
        assert result.returncode == 1


def test_unreachable_docker_is_not_reported_as_rootful(preflight):
    result = preflight("--gpu", "cpu", TEST_SECURITY="offline")
    assert result.returncode == 1
    assert "Cannot reach Docker" in result.stdout
    assert "Docker Engine 29.0: rootful" not in result.stdout


def test_remote_context_is_not_supported(preflight):
    result = preflight(
        "--gpu",
        "cpu",
        DOCKER_CONTEXT="remote",
        DOCKER_HOST="unix:///run/docker.sock",
        TEST_ENDPOINT="ssh://example.invalid",
    )
    assert result.returncode == 1
    assert "does not support remote Docker" in result.stdout


@pytest.mark.parametrize("mode, severity", [("nvidia", "FAIL"), ("auto", "WARN")])
def test_missing_nvidia_components_produce_mode_specific_hints(preflight, mode, severity):
    result = preflight("--gpu", mode)
    assert f"[{severity}] NVIDIA Container Toolkit CLI" in result.stdout
    assert "install nvidia-container-toolkit" in result.stdout
    if mode == "nvidia":
        assert result.returncode == 1


def test_invalid_gpu_mode(preflight):
    assert preflight("--gpu", "intel").returncode == 2
