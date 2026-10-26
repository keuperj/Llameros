"""GPU discovery must preserve identity when UMA memory metrics are unsupported."""

import subprocess
from types import SimpleNamespace

from llameros.hardware import GIB, assess_fit, gpu_info


def test_gb10_shared_memory_and_mixed_gpu_metrics(monkeypatch):
    monkeypatch.setattr("llameros.hardware.Path.glob", lambda *args: [])
    monkeypatch.setattr(
        "llameros.hardware.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=(
                "NVIDIA GB10, [N/A], [Not Supported], [N/A], GPU-spark\n"
                "Discrete GPU, 24576, 16384, 12, GPU-discrete\n"
                "Unknown GPU, N/A, N/A, 5, GPU-unknown\n"
                "Malformed row\n"
            )
        ),
    )
    memory = SimpleNamespace(total=128 * GIB, available=80 * GIB)
    devices = gpu_info(memory)
    assert len(devices) == 3
    assert devices[0] == {
        "name": "NVIDIA GB10",
        "vendor": "nvidia",
        "id": "GPU-spark",
        "total": 128 * GIB,
        "free": 80 * GIB,
        "unified_memory": True,
        "utilization": None,
    }
    assert devices[1]["free"] == 16 * GIB
    assert devices[2]["free"] is None and not devices[2]["unified_memory"]
    hw = {"memory_available": memory.available, "gpus": devices}
    assert assess_fit(40 * GIB, hw, "nvidia")["placement"] == "gpu"
    assert not assess_fit(70 * GIB, hw, "nvidia")["fits"]  # Never add CPU + GPU memory.


def test_identity_probe_survives_unsupported_metrics(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "memory.total" in command[1]:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout="NVIDIA GB10, GPU-spark\nDiscrete GPU, GPU-other\n")

    monkeypatch.setattr("llameros.hardware.subprocess.run", run)
    monkeypatch.setattr("llameros.hardware.Path.glob", lambda *args: [])
    monkeypatch.setattr(
        "llameros.hardware.psutil.virtual_memory",
        lambda: SimpleNamespace(total=128 * GIB, available=40 * GIB),
    )
    devices = gpu_info()
    assert len(calls) == 2
    assert devices[0]["id"] == "GPU-spark"
    assert devices[0]["free"] == 40 * GIB
    assert devices[1]["free"] is None
    assert (
        assess_fit(
            GIB, {"memory_available": 4 * GIB, "gpus": [{**devices[0], "free": None}]}, "nvidia"
        )["placement"]
        == "gpu"
    )


def test_failed_probes_remain_unknown(monkeypatch):
    def run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llameros.hardware.subprocess.run", run)
    monkeypatch.setattr("llameros.hardware.Path.glob", lambda *args: [])
    assert gpu_info() == []
