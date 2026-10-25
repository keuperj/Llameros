"""Validated local TOML settings shared by Llameros web, storage, and runtime services.

Configuration is local TOML, never browser-supplied container arguments.
"""

import os
import pwd
import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


def current_user() -> str:
    """Resolve the process UID through the system account database, not environment variables."""
    return pwd.getpwuid(os.getuid()).pw_name


@dataclass(frozen=True)
class Settings:
    """Immutable service configuration with validated ports, runtime policy, and attachment ceilings."""

    host: str = "127.0.0.1"
    port: int = 8443
    state_dir: Path = Path.home() / ".local/share/llameros"
    cert_file: str = ""
    key_file: str = ""
    certificate_download_enabled: bool = True
    pam_service: str = "login"
    session_seconds: int = 3600
    sample_seconds: int = 15
    retention_days: int = 30
    ollama_port: int = 11435
    ollama_image: str = (
        "ollama/ollama@sha256:dea72d1e22270fe095b68fdf756d2b489d615643c5a2eaebbc5f569181e322d8"
    )
    ollama_rocm_image: str = (
        "ollama/ollama@sha256:c57155dfdd87acadac039c2679482db7f761960dfe0419b3072e1cfd52cb5167"
    )
    gpu: str = "cpu"
    allow_rootful_docker: bool = False
    context_length: int = 4096
    max_loaded_models: int = 2
    inference_body_bytes: int = 16 * 1024 * 1024
    image_max_bytes: int = 8 * 1024 * 1024
    image_max_count: int = 8
    image_max_pixels: int = 40_000_000

    def __post_init__(self):
        """Normalize the state path and reject inconsistent or out-of-range settings."""
        object.__setattr__(self, "state_dir", Path(self.state_dir).expanduser().absolute())
        if type(self.certificate_download_enabled) is not bool:
            raise ValueError("certificate_download_enabled must be boolean")
        for name, ceiling in {
            "inference_body_bytes": 64 * 1024 * 1024,
            "image_max_bytes": 32 * 1024 * 1024,
            "image_max_count": 64,
            "image_max_pixels": 100_000_000,
        }.items():
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= ceiling:
                raise ValueError(f"{name} must be 1–{ceiling}")
        for name in ("port", "ollama_port"):
            if not 1024 <= getattr(self, name) <= 65535:
                raise ValueError(f"{name} must be between 1024 and 65535")
        if self.port == self.ollama_port:
            raise ValueError("HTTPS and Ollama ports must differ")
        for image in (self.ollama_image, self.ollama_rocm_image):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[a-f0-9]{64}", image):
                raise ValueError("Runtime images must be pinned by sha256 digest")
        if self.gpu not in {"cpu", "nvidia", "amd"}:
            raise ValueError("gpu must be cpu, nvidia, or amd")
        for name in ("session_seconds", "sample_seconds", "retention_days", "context_length"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if bool(self.cert_file) != bool(self.key_file):
            raise ValueError("cert_file and key_file must be supplied together")
        if type(self.max_loaded_models) is not int or not 1 <= self.max_loaded_models <= 16:
            raise ValueError("max_loaded_models must be 1–16")

    @property
    def container_name(self) -> str:
        """Return the stable UID-scoped container name used for runtime ownership."""
        return f"llameros-{os.getuid()}"

    def prepare(self):
        """Create a private state directory and refuse a directory owned by another UID."""
        from .filesystem import trusted_directory

        trusted_directory(self.state_dir, create=True)
        if self.state_dir.stat().st_uid != os.getuid():
            raise ValueError("State directory must be owned by the current user")
        self.state_dir.chmod(0o700)


def load_settings(path: Path | None = None) -> Settings:
    """Load validated TOML or defaults when absent; reject unknown keys and wrong types."""
    path = path or Path.home() / ".config/llameros/config.toml"
    if not path.exists():
        return Settings()
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    known = {field.name: field for field in fields(Settings)}
    unknown = set(data) - known.keys()
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    defaults = Settings()
    for key, value in data.items():
        expected = str if key == "state_dir" else type(getattr(defaults, key))
        if type(value) is not expected:
            raise ValueError(f"{key} must be {expected.__name__}")
    return Settings(**data)
