#!/usr/bin/env python3
"""Verify rootless NVIDIA startup and optionally install a user CDI mirror."""

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path("/run/cdi/nvidia.yaml")
SERVICE = "llameros-nvidia-cdi.service"
WATCHER = "llameros-nvidia-cdi.path"
HELPER = "llameros-nvidia-cdi.py"
DROP_IN = "40-llameros-nvidia-cdi.conf"
GPU_UUID = re.compile(r"(?<!\w)GPU-[0-9a-fA-F-]+\b")


def run(*args, capture=False, env=None):
    return subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        env=env,
        timeout=90,
    ).stdout


def docker(runtime, *args, capture=False):
    # Test the standard user-service socket regardless of shell context overrides.
    env = dict(os.environ)
    env.pop("DOCKER_CONTEXT", None)
    env.pop("DOCKER_HOST", None)
    return run(
        "docker", "--host", f"unix://{runtime / 'docker.sock'}", *args, capture=capture, env=env
    )


def rootless_info(runtime):
    info = json.loads(docker(runtime, "info", "--format", "{{json .}}", capture=True))
    if "name=rootless" not in (info.get("SecurityOptions") or []):
        raise ValueError("The user-service Docker socket does not point to a rootless daemon.")
    return info


def check(runtime, image, gpu):
    rootless_info(runtime)
    host = run("nvidia-smi", "-L", capture=True)
    devices = list(dict.fromkeys(GPU_UUID.findall(host)))
    if not devices:
        raise ValueError("No NVIDIA GPU UUIDs found on the host.")
    if gpu:
        if gpu not in devices:
            raise ValueError("The requested GPU UUID was not found on the host.")
        devices = [gpu]
    for device in devices:
        print(f"Testing --gpus device={device} with {image}", flush=True)
        visible = docker(
            runtime, "run", "--rm", "--pull=never", "--network=none", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--gpus", f"device={device}",
            "--entrypoint=nvidia-smi", "--", image, "-L", capture=True,
        )
        if set(GPU_UUID.findall(visible)) != {device}:
            raise ValueError(f"Container did not expose exactly the requested GPU {device}: {visible}")
        print(visible.strip())


def read_host_spec():
    # Do not import a spec from a location writable by an unprivileged host user.
    # CDI specs can add mounts and hooks to containers.
    try:
        parent = SOURCE.parent.lstat()
        if parent.st_uid != 0 or parent.st_mode & 0o022 or not stat.S_ISDIR(parent.st_mode):
            raise ValueError(f"Untrusted CDI directory: {SOURCE.parent}")
        with os.fdopen(os.open(SOURCE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"Untrusted CDI spec: {SOURCE}")
            content = stream.read()
    except FileNotFoundError:
        return None
    if not content:
        raise ValueError(f"Empty CDI spec: {SOURCE}")
    return content


def mirror(runtime):
    content = read_host_spec()
    if content is None:
        # The Toolkit may not have published yet; the watcher retries on changes.
        return
    directory = runtime / "cdi"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise ValueError(f"Refusing to use a symlinked CDI directory: {directory}")
    dest = directory / SOURCE.name
    if not dest.is_symlink() and dest.is_file() and dest.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".llameros-nvidia-", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.close()
            temporary.replace(dest)
        finally:
            temporary.unlink(missing_ok=True)


def install(runtime, config):
    if str(runtime / "cdi") not in (rootless_info(runtime).get("CDISpecDirs") or []):
        raise ValueError("Docker does not list the runtime user CDI directory; this workaround does not apply.")
    if run("systemctl", "--user", "is-active", "docker.service", capture=True).strip() != "active":
        raise ValueError("The standard rootless docker.service must be active.")
    if read_host_spec() is None:
        raise ValueError(f"{SOURCE} is missing. Check the host nvidia-cdi-refresh.service first.")

    units = config / "systemd/user"
    helper = config / "llameros" / HELPER
    # Use %E/%t so the service follows the user manager's XDG directories.
    files = {
        helper: Path(__file__).read_bytes(),
        units / SERVICE: (
            "[Unit]\nDescription=Mirror NVIDIA CDI for rootless Docker (Llameros)\n"
            "[Service]\nType=oneshot\n"
            f'ExecStart=/usr/bin/python3 "%E/llameros/{HELPER}" _sync "%t"\n'
        ).encode(),
        units / WATCHER: (
            "[Unit]\nDescription=Watch the host NVIDIA CDI spec (Llameros)\n"
            f"[Path]\nPathChanged={SOURCE.parent}\nPathChanged={SOURCE}\nUnit={SERVICE}\n"
            "[Install]\nWantedBy=default.target\n"
        ).encode(),
        units / "docker.service.d" / DROP_IN: (
            f"[Unit]\nWants={SERVICE}\nAfter={SERVICE}\n"
        ).encode(),
    }
    for path, content in files.items():
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != content)):
            raise ValueError(f"Refusing to overwrite {path}.")
    dest = runtime / "cdi" / SOURCE.name
    if dest.is_symlink() or (dest.exists() and not (units / SERVICE).is_file()):
        raise ValueError(f"Refusing to take over the existing CDI spec at {dest}.")

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("xb") as file:
                file.write(content)
    run("systemctl", "--user", "daemon-reload")
    run("systemctl", "--user", "enable", "--now", WATCHER)
    run("systemctl", "--user", "start", SERVICE)
    print("CDI sync enabled. Docker was not restarted. Run the check again to verify GPU access.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    probe = sub.add_parser("check", help="Run a disposable GPU container (no image pull)")
    probe.add_argument("--image", default="ubuntu:24.04", help="Trusted image already present locally")
    probe.add_argument("--gpu", help="Check only this GPU UUID instead of each host GPU")
    sub.add_parser("install", help="Enable the opt-in CDI mirror without restarting Docker")
    if (sys.argv[1:] if argv is None else argv)[:1] == ["_sync"]:
        worker = argparse.ArgumentParser()
        worker.add_argument("runtime", type=Path)
        args = worker.parse_args((sys.argv[2:] if argv is None else argv[1:]))
        mirror(args.runtime)
        return
    args = parser.parse_args(argv)
    if os.getuid() == 0 or not os.environ.get("XDG_RUNTIME_DIR"):
        raise ValueError("Run as the Llameros user in a user systemd session, not with sudo.")
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    if args.action == "check":
        check(runtime, args.image, args.gpu)
    else:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        install(runtime, config)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(f"NVIDIA rootless setup: {exc}")