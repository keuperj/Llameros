"""Content-independent operational diagnostics safe for persistence and display."""


def diagnostic(exc: Exception) -> str:
    """Never include upstream text, URLs, paths, prompts, or credentials."""
    return "Operation failed [runtime-error]; check runtime availability and configuration"
