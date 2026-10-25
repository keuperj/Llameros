"""Private model-storage preferences and offline Ollama inventory for Llameros runtimes.

Private storage preferences and offline inventory for Ollama model stores.
"""

import json
import os
import re
import tempfile
import time
from pathlib import Path

from .config import Settings
from .filesystem import trusted_directory

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def write_json(path: Path, data: dict):
    """Atomically replace private JSON state via a flushed temporary file in the same directory."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_directory(value: str, state_dir: Path, *, create: bool = False) -> Path:
    """Return a safe dedicated model directory, optionally creating it.

    Reject symlinks, unsafe ownership/permissions, unrelated contents, and overlap with
    application state.
    """
    if not value or any(char in value for char in (",", "\n", "\r", "\x00")):
        raise ValueError("Choose a non-empty directory path without commas or control characters")
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Use an absolute path or a path beginning with ~, without .. components")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Model storage must not use symbolic links")
    # Do not resolve a link substituted after the preliminary check: the
    # descriptor traversal below must see and reject the original pathname.
    home, state = Path.home().resolve(), state_dir.resolve()
    if (
        path == home
        or path == Path(path.anchor)
        or path == state
        or path in state.parents
        or state in path.parents
    ):
        raise ValueError(
            "Choose a dedicated model directory, not your home, filesystem root, or application state directory"
        )
    existing = next(part for part in (path, *path.parents) if part.exists())
    if (
        not existing.is_dir()
        or existing.stat().st_uid != os.getuid()
        or existing.stat().st_mode & 0o022
    ):
        raise ValueError(
            "Storage must be owned by the current user and not writable by group/others"
        )
    trusted_directory(path, create=create)
    if not path.is_dir():
        raise ValueError("Model directory does not exist")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError("Model directory must be readable and writable by the current user")
    if any(entry.name not in {"blobs", "manifests"} for entry in path.iterdir()):
        raise ValueError(
            "Use an empty directory or an Ollama models directory containing only blobs and manifests"
        )
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError("Model storage must not contain symbolic links")
    return path


def scan_models(path: Path) -> dict:
    """List manifests with all referenced blobs present; do not hash multi-GB weights."""
    models, warnings = [], []
    manifests = path / "manifests"
    if not manifests.exists():
        return {"models": [], "warnings": []}
    for manifest in sorted(manifests.glob("*/*/*/*")):
        try:
            if (
                not manifest.is_file()
                or manifest.is_symlink()
                or path not in manifest.resolve().parents
            ):
                continue
            if manifest.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("manifest too large")
            data = json.loads(manifest.read_text())
            layers = data.get("layers", [])
            if not layers:
                raise ValueError("no downloadable layers")
            descriptors = [data["config"], *layers]
            for layer in descriptors:
                digest, size = layer["digest"], int(layer["size"])
                if not DIGEST.fullmatch(digest) or size < 0:
                    raise ValueError("invalid blob descriptor")
                blob = path / "blobs" / digest.replace(":", "-")
                if (
                    blob.is_symlink()
                    or path not in blob.resolve().parents
                    or not blob.is_file()
                    or blob.stat().st_size != size
                ):
                    raise ValueError("missing or incomplete blob")
            registry, namespace, family, tag = manifest.relative_to(manifests).parts
            prefix = (
                ""
                if registry == "registry.ollama.ai" and namespace == "library"
                else f"{registry}/{namespace}/"
            )
            models.append(
                {
                    "name": f"{prefix}{family}:{tag}",
                    "size": sum(int(layer["size"]) for layer in descriptors),
                    "modified_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(manifest.stat().st_mtime)
                    ),
                }
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            warnings.append(f"Skipped {manifest.relative_to(manifests)}: {exc}")
    return {"models": models, "warnings": warnings}


class ModelStorage:
    """Track the selected host directory or Docker volume and its last known inventory."""

    def __init__(self, settings: Settings):
        """Load private storage preferences and locate the inventory cache in the state directory."""
        self.settings = settings
        self.preference_file = settings.state_dir / "model-storage.json"
        self.cache_file = settings.state_dir / "model-inventory.json"
        self.directory = ""
        if self.preference_file.exists():
            self.directory = json.loads(self.preference_file.read_text())["directory"]

    @property
    def identity(self) -> str:
        """Return the selected host path or UID-scoped volume name used to match cached data."""
        return self.directory or f"{self.settings.container_name}-models"

    def info(self) -> dict:
        """Describe storage mode, location, and a suggested dedicated host directory."""
        return {
            "mode": "directory" if self.directory else "volume",
            "directory": self.directory,
            "location": self.identity,
            "suggested_directory": str(Path.home() / ".local/share/llameros-models"),
        }

    def save(self, directory: str):
        """Atomically persist a storage selection and update the in-memory directory."""
        write_json(self.preference_file, {"directory": directory})
        self.directory = directory

    def remember(self, models: list):
        """Cache a live inventory with its storage identity and observation timestamp."""
        write_json(
            self.cache_file, {"location": self.identity, "timestamp": time.time(), "models": models}
        )

    def inventory(self) -> dict:
        """Read validated host manifests or a matching volume cache, marking the source explicitly."""
        if self.directory:
            path = validate_directory(self.directory, self.settings.state_dir)
            return {**scan_models(path), "source": "disk", "cached_at": None}
        if self.cache_file.exists():
            cache = json.loads(self.cache_file.read_text())
            if cache["location"] == self.identity:
                return {
                    "models": cache["models"],
                    "source": "cache",
                    "cached_at": cache["timestamp"],
                    "warnings": [],
                }
        return {
            "models": [],
            "source": "unknown",
            "cached_at": None,
            "warnings": ["Start Ollama once to read the downloaded models in this Docker volume."],
        }
