"""Narrow Docker CLI wrapper. Never executes a shell or accepts arbitrary flags."""

import json
import os
import subprocess
import time

import httpx

from .config import Settings
from .model_storage import ModelStorage, validate_directory


class DockerError(RuntimeError):
    pass


class Docker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = ModelStorage(settings)

    def storage_info(self) -> dict:
        return self.storage.info()

    def inventory(self) -> dict:
        return self.storage.inventory()

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
                self.command("rm", self.settings.container_name)
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
        if self.settings.container_name not in names:
            return None
        data = json.loads(self.command("inspect", self.settings.container_name))[0]
        labels = data.get("Config", {}).get("Labels") or {}
        if labels.get("io.llameros.owner") != str(os.getuid()):
            raise DockerError("Refusing to manage a container without the expected ownership label")
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
            cfg.container_name,
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
            "OLLAMA_MAX_LOADED_MODELS=1",
            "-e",
            "OLLAMA_NUM_PARALLEL=1",
            "-e",
            f"OLLAMA_CONTEXT_LENGTH={cfg.context_length}",
        ]
        if cfg.gpu == "nvidia":
            args += ["--gpus", "all"]
        elif cfg.gpu == "amd":
            args += ["--device", "/dev/kfd", "--device", "/dev/dri"]
        image = (
            "ollama/ollama:rocm"
            if cfg.gpu == "amd" and cfg.ollama_image == "ollama/ollama:latest"
            else cfg.ollama_image
        )
        return [*args, image]

    def start(self):
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
            self.command(*self.run_args(), timeout=900)
        elif not data["State"]["Running"]:
            self.command("start", self.settings.container_name)
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
            self.command("stop", "--time", "20", self.settings.container_name, timeout=30)

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
        if not data:
            return {"running": False, "models": [], "loaded": []}
        running = data["State"]["Running"]
        result = {
            "running": running,
            "models": [],
            "loaded": [],
            "container": self.settings.container_name,
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
                self.settings.container_name,
                timeout=10,
            )
            result["resources"] = json.loads(raw) if raw else {}
        return result
