"""Narrow Docker CLI wrapper. Never executes a shell or accepts arbitrary flags."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

from .config import Settings
from .model_storage import ModelStorage, validate_directory


class DockerError(RuntimeError):
    pass


class Docker:
    def __init__(self, settings: Settings, *, target=None, name=None, storage=None):
        self.settings = settings
        self.target = target
        self.name = name or settings.container_name
        self.storage = storage or ModelStorage(settings)

    def storage_info(self) -> dict:
        return self.storage.info()

    def inventory(self) -> dict:
        return self.storage.inventory()

    @property
    def image(self) -> str:
        cfg = self.settings
        return (
            "ollama/ollama:rocm"
            if cfg.gpu == "amd" and cfg.ollama_image == "ollama/ollama:latest"
            else cfg.ollama_image
        )

    def image_available(self) -> bool:
        images = self.command("image", "ls", "--quiet", "--filter", f"reference={self.image}")
        if images:
            return True
        # Digest references aren't matched consistently by the reference filter.
        if "@" in self.image:
            return (
                self.image
                in self.command(
                    "image", "ls", "--digests", "--format", "{{.Repository}}@{{.Digest}}"
                ).splitlines()
            )
        return False

    def pull_image(self, progress):
        """Read daemon JSON progress over the same local socket as Docker CLI."""
        self.check_daemon()
        endpoint = os.environ.get("DOCKER_HOST") or self.command(
            "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"
        )
        transport = httpx.HTTPTransport(uds=endpoint.removeprefix("unix://"))
        with httpx.Client(
            transport=transport,
            base_url="http://docker",
            timeout=httpx.Timeout(30, read=900),
            trust_env=False,
        ) as client:
            version = client.get("/version")
            version.raise_for_status()
            api_version = version.json()["ApiVersion"]
            if not re.fullmatch(r"\d+\.\d+", api_version):
                raise DockerError("Invalid Docker daemon API version")
            with client.stream(
                "POST", f"/v{api_version}/images/create", params={"fromImage": self.image}
            ) as response:
                response.raise_for_status()
                seen = False
                started = time.monotonic()
                for line in response.iter_lines():
                    if time.monotonic() - started > 1800:
                        raise DockerError("Docker image download exceeded 30 minutes")
                    if not line:
                        continue
                    seen = True
                    event = json.loads(line)
                    if event.get("error") or event.get("errorDetail"):
                        raise DockerError(
                            event.get("error")
                            or event["errorDetail"].get("message", "Docker pull failed")
                        )
                    detail = event.get("progressDetail") or {}
                    progress(
                        {
                            "status": event.get("status", "Pulling Docker image"),
                            "phase": "docker-image",
                            "id": event.get("id", ""),
                            "completed": detail.get("current"),
                            "total": detail.get("total"),
                        }
                    )
                if not seen or not self.image_available():
                    raise DockerError(
                        "Docker image download ended without a locally available image"
                    )

    def configure_storage(self, directory: str, confirmed: bool):
        if not confirmed:
            raise ValueError(
                "Confirm that changing storage does not move or delete existing models"
            )
        self.check_daemon()
        data = self.inspect()
        if data and data["State"]["Running"]:
            raise DockerError("Stop Ollama before changing model storage")
        target = (
            str(validate_directory(directory, self.settings.state_dir, create=True))
            if directory
            else ""
        )
        # Save before removal; restore the preference if Docker refuses removal.
        previous = self.storage.directory
        self.storage.save(target)
        try:
            if data:
                self.command("rm", self.name)
        except Exception:
            self.storage.save(previous)
            raise
        return self.storage.info()

    def command(self, *args: str, timeout: int = 30) -> str:
        try:
            result = subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DockerError(f"Docker unavailable: {exc}") from exc
        if result.returncode:
            raise DockerError(result.stderr.strip()[:2000] or "Docker command failed")
        return result.stdout.strip()

    def check_daemon(self):
        endpoint = os.environ.get("DOCKER_HOST")
        if not endpoint:
            endpoint = self.command("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
        if not endpoint.startswith("unix://"):
            raise DockerError("Only a local Unix-socket Docker daemon is supported")
        options = json.loads(self.command("info", "--format", "{{json .SecurityOptions}}"))
        rootless = any("rootless" in option for option in options or [])
        if not rootless and not self.settings.allow_rootful_docker:
            raise DockerError(
                "Rootful Docker refused. Configure rootless Docker, or explicitly set allow_rootful_docker = true after accepting the root-equivalent daemon access risk."
            )

    def inspect(self) -> dict | None:
        # Listing first distinguishes a missing container from daemon/permission failures.
        names = self.command("container", "ls", "-a", "--format", "{{.Names}}").splitlines()
        if self.name not in names:
            return None
        data = json.loads(self.command("inspect", self.name))[0]
        labels = data.get("Config", {}).get("Labels") or {}
        if labels.get("io.llameros.owner") != str(os.getuid()):
            raise DockerError("Refusing to manage a container without the expected ownership label")
        if self.target and labels.get("io.llameros.target") != self.target["id"]:
            raise DockerError(
                "Legacy or differently assigned container found. Stop Ollama and reapply model storage to recreate it safely; model files are retained."
            )
        return data

    def run_args(self) -> list[str]:
        cfg = self.settings
        if self.storage.directory:
            directory = validate_directory(self.storage.directory, cfg.state_dir)
            mount = [
                "--mount",
                f"type=bind,src={directory},dst=/models",
                "-e",
                "OLLAMA_MODELS=/models",
            ]
        else:
            mount = ["-v", f"{cfg.container_name}-models:/root/.ollama"]
        args = [
            "run",
            "-d",
            "--name",
            self.name,
            "--label",
            f"io.llameros.owner={os.getuid()}",
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            "512",
            "--restart",
            "no",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            "-p",
            f"127.0.0.1:{cfg.ollama_port}:11434",
            *mount,
            "-e",
            "OLLAMA_HOST=0.0.0.0:11434",
            "-e",
            "OLLAMA_NO_CLOUD=1",
            "-e",
            "OLLAMA_NOPRUNE=1",
            "-e",
            "OLLAMA_MAX_LOADED_MODELS=1",
            "-e",
            "OLLAMA_NUM_PARALLEL=1",
            "-e",
            f"OLLAMA_CONTEXT_LENGTH={cfg.context_length}",
        ]
        if self.target:
            args += ["--label", f"io.llameros.target={self.target['id']}"]
        if cfg.gpu == "nvidia" and self.target:
            args += ["--gpus", f"device={self.target['device']}"]
        elif cfg.gpu == "amd" and self.target:
            device = self.target["device"]
            if not re.fullmatch(r"/dev/dri/renderD\d+", device):
                raise DockerError("Invalid AMD render device")
            args += ["--device", "/dev/kfd", "--device", device]
            for path in ("/dev/kfd", device):
                args += ["--group-add", str(Path(path).stat().st_gid)]
        elif cfg.gpu == "nvidia":
            args += ["--gpus", "all"]
        elif cfg.gpu == "amd":
            args += ["--device", "/dev/kfd", "--device", "/dev/dri"]
        if self.target and cfg.gpu == "cpu":
            args += [
                "-e",
                "CUDA_VISIBLE_DEVICES=-1",
                "-e",
                "ROCR_VISIBLE_DEVICES=-1",
                "-e",
                "NVIDIA_VISIBLE_DEVICES=void",
            ]
        return [*args, self.image]

    def start(self, progress=lambda value: None):
        progress({"status": "Checking local Docker daemon and container"})
        self.check_daemon()
        data = self.inspect()
        if data and self.storage.directory:
            mounts = data.get("Mounts", [])
            if not any(
                mount.get("Source") == self.storage.directory
                and mount.get("Destination") == "/models"
                for mount in mounts
            ):
                raise DockerError(
                    "Container storage differs from the selected directory; reapply storage settings while stopped"
                )
        if data is None:
            if not self.image_available():
                progress({"status": "Downloading Ollama Docker image", "phase": "docker-image"})
                self.pull_image(progress)
            progress({"status": "Creating and starting Ollama container", "phase": "container"})
            self.command(*self.run_args(), timeout=60)
        elif not data["State"]["Running"]:
            progress({"status": "Starting existing Ollama container", "phase": "container"})
            self.command("start", self.name)
        progress({"status": "Waiting for Ollama API readiness", "phase": "readiness"})
        for _ in range(60):
            try:
                self.api("GET", "/api/version", timeout=2)
                return
            except (httpx.HTTPError, DockerError):
                time.sleep(1)
        raise DockerError("Ollama did not become ready within 60 seconds; inspect container logs")

    def stop(self):
        self.check_daemon()
        data = self.inspect()
        if data and data["State"]["Running"]:
            self.command("stop", "--time", "20", self.name, timeout=30)

    def ensure_running(self):
        self.check_daemon()
        data = self.inspect()
        if not data or not data["State"]["Running"]:
            raise DockerError("The managed Ollama container is not running")

    def api(self, method: str, path: str, body: dict | None = None, timeout: int = 600) -> dict:
        with httpx.Client(
            base_url=self.settings.ollama_url, timeout=timeout, trust_env=False
        ) as client:
            response = client.request(method, path, json=body)
            response.raise_for_status()
            data = response.json()
        if data.get("error"):
            raise DockerError(str(data["error"]))
        return data

    def pull(self, name: str, progress):
        self.ensure_running()
        with httpx.Client(
            base_url=self.settings.ollama_url, timeout=httpx.Timeout(120, read=900), trust_env=False
        ) as client:
            with client.stream(
                "POST", "/api/pull", json={"model": name, "stream": True}
            ) as response:
                response.raise_for_status()
                final = None
                for line in response.iter_lines():
                    if not line:
                        continue
                    final = json.loads(line)
                    if final.get("error"):
                        raise DockerError(str(final["error"]))
                    progress(final)
                if not final or final.get("status") != "success":
                    raise DockerError("Model download ended without a success response")
        self.storage.remember(self.api("GET", "/api/tags", timeout=5).get("models", []))

    def status(self) -> dict:
        self.check_daemon()
        data = self.inspect()
        availability = {
            "image": self.image,
            "image_available": self.image_available(),
            "container_exists": data is not None,
        }
        if not data:
            return {"running": False, "models": [], "loaded": [], **availability}
        running = data["State"]["Running"]
        result = {
            **availability,
            "running": running,
            "models": [],
            "loaded": [],
            "container": self.name,
        }
        if running:
            result["models"] = self.api("GET", "/api/tags", timeout=5).get("models", [])
            self.storage.remember(result["models"])
            result["loaded"] = self.api("GET", "/api/ps", timeout=5).get("models", [])
            raw = self.command(
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                self.name,
                timeout=10,
            )
            result["resources"] = json.loads(raw) if raw else {}
        return result
