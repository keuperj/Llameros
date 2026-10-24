"""Best-effort, non-privileged Linux hardware inspection."""

import csv
import platform
import subprocess
from pathlib import Path

import psutil

GIB = 1024**3


def gpu_info() -> list[dict]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            check=True,
        )
        return [
            {
                "name": row[0].strip(),
                "vendor": "nvidia",
                "total": int(row[1]) * 1024**2,
                "free": int(row[2]) * 1024**2,
                "utilization": float(row[3]),
            }
            for row in csv.reader(result.stdout.splitlines())
            if len(row) == 4
        ]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    devices = []
    for device in Path("/sys/class/drm").glob("card[0-9]*/device"):
        try:
            vendor = (device / "vendor").read_text().strip()
            if vendor not in {"0x1002", "0x8086", "0x10de"}:
                continue
            total_file = device / "mem_info_vram_total"
            used_file = device / "mem_info_vram_used"
            total = int(total_file.read_text()) if total_file.exists() else None
            used = int(used_file.read_text()) if used_file.exists() else None
            devices.append(
                {
                    "name": f"{device.parent.name} ({vendor})",
                    "vendor": {"0x1002": "amd", "0x8086": "intel", "0x10de": "nvidia"}[vendor],
                    "total": total,
                    "free": max(0, total - used)
                    if total is not None and used is not None
                    else None,
                    "utilization": None,
                }
            )
        except (OSError, ValueError):
            continue
    return devices


def snapshot() -> dict:
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
        "gpus": gpu_info(),
    }


def assess_fit(size: int, hardware: dict, gpu_mode: str = "cpu") -> dict:
    # Reserve OS headroom; weights + a conservative fixed KV/runtime allowance.
    required = int(size * 1.25 + 2 * GIB)
    available = max(0, hardware["memory_available"] - GIB)
    gpu_free = (
        max(
            (gpu.get("free") or 0 for gpu in hardware["gpus"] if gpu["vendor"] == gpu_mode),
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
        else "cpu/partial-gpu"
        if fits
        else "insufficient-memory",
        "note": "Estimate only: weights × 1.25 + 2 GiB; 1 GiB OS reserve. Context, architecture, and other workloads change actual requirements. GPU memory is not added to RAM.",
    }
