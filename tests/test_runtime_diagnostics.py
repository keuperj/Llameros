"""Fixed-vocabulary startup diagnostics never expose upstream exception content."""

import pytest

from llameros.diagnostics import RuntimeStartupError, diagnostic
from llameros.docker import DockerError


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("Runtime parallelism differs from configuration", "container-parallelism"),
        ("could not select device driver with capabilities: [[gpu]]", "gpu-runtime"),
        ("nvidia-container-cli: initialization error", "gpu-runtime"),
        ("Legacy or differently assigned container found", "container-isolation"),
        ("Runtime image differs from configured digest", "container-image"),
        ("Rootful Docker refused", "docker-policy"),
        ("permission denied", "docker-permission"),
        ("Ollama did not become ready", "runtime-readiness"),
    ],
)
def test_docker_startup_diagnostics_are_specific_and_redacted(message, code):
    error = RuntimeStartupError(
        "gpu-1234", DockerError(message + " /private/secret Bearer llm_secret prompt=password")
    )
    text = diagnostic(error)
    assert "GPU container startup failed" in text
    assert f"runtime-error:{code}" in text
    for secret in ("/private/secret", "llm_secret", "password"):
        assert secret not in text


def test_non_docker_text_does_not_trigger_classification():
    assert "gpu-runtime" not in diagnostic(ValueError("nvidia-container password"))
