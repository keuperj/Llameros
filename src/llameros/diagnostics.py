"""Content-independent operational diagnostics safe for persistence and display."""


class RuntimeStartupSummary(RuntimeError):
    """Carry only diagnostics already sanitized by diagnostic()."""

    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__("One or more runtimes are unavailable")


class RuntimeStartupError(RuntimeError):
    """Identify a failed runtime without exposing Docker output or upstream content."""

    def __init__(self, target_id, cause):
        self.target_id = target_id
        self.cause = cause
        super().__init__("Runtime container startup failed")


def diagnostic(exc: Exception) -> str:
    """Never include upstream text, URLs, paths, prompts, or credentials."""
    if isinstance(exc, RuntimeStartupSummary):
        return " ".join(exc.diagnostics)
    if isinstance(exc, RuntimeStartupError):
        # IDs originate from the fixed CPU identity or a SHA-256 GPU identity.
        target = "CPU" if exc.target_id == "cpu" else "GPU"
        return f"{target} container startup failed. {diagnostic(exc.cause)}"
    # Classify only Docker errors, never arbitrary model/HTTP exception content.
    # Output is entirely fixed text; do not echo the matched error or its arguments.
    from .docker import DockerError

    if isinstance(exc, DockerError):
        text = str(exc).lower()
        reasons = (
            (
                ("runtime parallelism differs",),
                "container-parallelism",
                "Container parallelism differs from scheduler settings. Stop all runtimes and reapply model storage to recreate containers; model files are retained.",
            ),
            (
                (
                    "could not select device driver",
                    "nvidia-container",
                    "unknown or invalid runtime name",
                    "nvml",
                    "cuda driver",
                ),
                "gpu-runtime",
                "Docker could not initialize GPU access. Check the NVIDIA Container Toolkit, Docker GPU runtime and host driver.",
            ),
            (
                ("legacy or differently assigned container", "unsafe legacy runtime"),
                "container-isolation",
                "The existing container has incompatible isolation or hardware labels. Stop all runtimes and reapply model storage; model files are retained.",
            ),
            (
                ("runtime image differs", "older ollama container is running"),
                "container-image",
                "The existing container uses a different configured image. Stop it before retrying startup; the old container and model files are preserved.",
            ),
            (
                ("rootful docker refused",),
                "docker-policy",
                "Rootful Docker is disabled in configuration. Check the service Docker endpoint and allow_rootful_docker setting.",
            ),
            (
                ("permission denied",),
                "docker-permission",
                "Docker access was denied. Check Docker socket permissions for the Llameros service account.",
            ),
            (
                ("did not become ready",),
                "runtime-readiness",
                "The container started but Ollama did not become ready. Inspect that container's Docker logs.",
            ),
        )
        for patterns, code, message in reasons:
            if any(pattern in text for pattern in patterns):
                return f"Operation failed [runtime-error:{code}]; {message}"
    return "Operation failed [runtime-error]; check runtime availability and configuration"
