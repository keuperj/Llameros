"""Unprivileged hardware sampling and model-fit estimates for Llameros runtime placement.

Best-effort, non-privileged Linux hardware inspection.
"""

import csv
import math
import platform
import re
import subprocess
from pathlib import Path

import psutil

GIB = 1024**3


def gpu_info(memory=None) -> list[dict]:
    """Discover GPU identity, memory bytes, and utilization using NVIDIA tools and Linux sysfs.

    Unavailable probes are tolerated; missing metrics remain unknown rather than implying safe
    capacity.
    """
    devices = []
    try:
        query = [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free,utilization.gpu,uuid",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = subprocess.run(query, capture_output=True, text=True, timeout=4, check=True)
            rows = list(csv.reader(result.stdout.splitlines()))
        except subprocess.SubprocessError:
            # Some driver versions reject a query containing unsupported metrics.
            # Identity alone still permits safe UUID targeting and GB10 UMA detection.
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,uuid",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=4,
                check=True,
            )
            rows = [
                [row[0], "N/A", "N/A", "N/A", row[1]]
                for row in csv.reader(result.stdout.splitlines())
                if len(row) == 2
            ]
        for row in rows:
            if len(row) != 5:
                continue

            def metric(value, scale=1):
                """Unsupported metrics must not discard device identity or other GPUs."""
                try:
                    number = float(value)
                    return number * scale if math.isfinite(number) and number >= 0 else None
                except ValueError:
                    return None

            unified = bool(re.search(r"\bGB10\b", row[0], re.IGNORECASE))
            if unified and memory is None:
                memory = psutil.virtual_memory()
            devices.append(
                {
                    "name": row[0].strip(),
                    "vendor": "nvidia",
                    "total": memory.total if unified else metric(row[1], 1024**2),
                    "free": memory.available if unified else metric(row[2], 1024**2),
                    "unified_memory": unified,
                    "utilization": metric(row[3]),
                    "id": row[4].strip(),
                }
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    nvidia_detected = bool(devices)
    for device in Path("/sys/class/drm").glob("card[0-9]*/device"):
        try:
            vendor = (device / "vendor").read_text().strip()
            if vendor not in {"0x1002", "0x8086", "0x10de"}:
                continue
            if vendor == "0x10de" and nvidia_detected:
                continue
            total_file = device / "mem_info_vram_total"
            used_file = device / "mem_info_vram_used"
            total = int(total_file.read_text()) if total_file.exists() else None
            used = int(used_file.read_text()) if used_file.exists() else None
            devices.append(
                {
                    "id": str(device.resolve()),
                    "render_device": next(
                        (f"/dev/dri/{node.name}" for node in (device / "drm").glob("renderD*")), ""
                    ),
                    "name": f"{device.parent.name} ({vendor})",
                    "vendor": {"0x1002": "amd", "0x8086": "intel", "0x10de": "nvidia"}[vendor],
                    "total": total,
                    "free": max(0, total - used)
                    if total is not None and used is not None
                    else None,
                    "utilization": float((device / "gpu_busy_percent").read_text())
                    if (device / "gpu_busy_percent").exists()
                    else None,
                }
            )
        except (OSError, ValueError):
            continue
    return devices


def snapshot() -> dict:
    """Return CPU identity/load, RAM byte counts/percentage, and best-effort GPU measurements."""
    memory = psutil.virtual_memory()
    cpu = platform.processor() or platform.machine()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "cpu": cpu,
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(),
        "cpu_percent": psutil.cpu_percent(),
        "memory_total": memory.total,
        "memory_available": memory.available,
        "memory_percent": memory.percent,
        "gpus": gpu_info(memory),
    }


def assess_fit(size: int, hardware: dict, gpu_mode: str = "cpu") -> dict:
    # Fixed 2 GiB runtime + 1 GiB reserve excluded even tiny CPU models. Scale
    # runtime headroom with weights while retaining a floor and OS reserve.
    """Estimate model memory headroom and placement from weight bytes and a hardware snapshot.

    RAM and VRAM are not added together; a positive fit is advisory, not a guarantee of
    residency or architecture support.
    """
    runtime = max(256 * 1024**2, min(2 * GIB, int(size * 0.25)))
    required = int(size * 1.25 + runtime)
    available = max(0, hardware["memory_available"] - 512 * 1024**2)
    gpu_free = (
        max(
            (
                available if gpu.get("unified_memory") else gpu.get("free") or 0
                for gpu in hardware["gpus"]
                if gpu["vendor"] == gpu_mode
            ),
            default=0,
        )
        if gpu_mode != "cpu"
        else 0
    )
    fits = required <= available
    return {
        "fits": fits,
        "estimated_memory": required,
        "placement": "gpu"
        if fits and required <= gpu_free
        else "cpu"
        if gpu_mode == "cpu" and fits
        else "cpu/partial-gpu"
        if fits
        else "insufficient-memory",
        "note": "Estimate only: weights × 1.25 + runtime allowance (25% of weights, minimum 256 MiB, maximum 2 GiB); 512 MiB reserve from currently available RAM. Context, architecture, and other workloads change actual requirements. GPU memory is not added to RAM. A memory fit does not guarantee CPU architecture support or acceptable speed.",
    }
