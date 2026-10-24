"""Configuration is local TOML, never browser-supplied container arguments."""

import os
import pwd
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


def current_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8443
    state_dir: Path = Path.home() / ".local/share/llameros"
    cert_file: str = ""
    key_file: str = ""
    pam_service: str = "login"
    session_seconds: int = 3600
    sample_seconds: int = 15
    retention_days: int = 30
    ollama_port: int = 11435
    ollama_image: str = "ollama/ollama:latest"
    gpu: str = "cpu"
    allow_rootful_docker: bool = False
    context_length: int = 4096

    def __post_init__(self):
        object.__setattr__(self, "state_dir", Path(self.state_dir).expanduser().absolute())
        for name in ("port", "ollama_port"):
            if not 1024 <= getattr(self, name) <= 65535:
                raise ValueError(f"{name} must be between 1024 and 65535")
        if self.port == self.ollama_port:
            raise ValueError("HTTPS and Ollama ports must differ")
        if self.gpu not in {"cpu", "nvidia", "amd"}:
            raise ValueError("gpu must be cpu, nvidia, or amd")
        for name in ("session_seconds", "sample_seconds", "retention_days", "context_length"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if bool(self.cert_file) != bool(self.key_file):
            raise ValueError("cert_file and key_file must be supplied together")

    @property
    def container_name(self) -> str:
        return f"llameros-{os.getuid()}"

    @property
    def ollama_url(self) -> str:
        return f"http://127.0.0.1:{self.ollama_port}"

    def prepare(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.state_dir.stat().st_uid != os.getuid():
            raise ValueError("State directory must be owned by the current user")
        self.state_dir.chmod(0o700)


def load_settings(path: Path | None = None) -> Settings:
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
