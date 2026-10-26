"""Parallel configuration, runtime contract and memory safety regressions."""

from dataclasses import replace

import pytest

from llameros.config import Settings, load_settings
from llameros.docker import Docker, DockerError
from llameros.hardware import GIB, assess_fit


@pytest.mark.parametrize("value", [0, -1, 17, True, 2.5, "2"])
def test_parallel_configuration_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="ollama_num_parallel"):
        Settings(ollama_num_parallel=value)


def test_parallel_configuration_and_container_contract(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("ollama_num_parallel = 4\n")
    assert Settings().ollama_num_parallel == 2
    cfg = replace(load_settings(path), state_dir=tmp_path)
    docker = Docker(cfg)
    assert "OLLAMA_NUM_PARALLEL=4" in docker.run_args()
    data = {
        "HostConfig": {"NetworkMode": "bridge"},
        "Config": {
            "Image": docker.image,
            "Env": ["OLLAMA_HOST=127.0.0.1:11434", "OLLAMA_NUM_PARALLEL=4"],
        },
    }
    docker.require_isolation(data)
    data["Config"]["Env"][-1] = "OLLAMA_NUM_PARALLEL=1"
    with pytest.raises(DockerError, match="parallelism"):
        docker.require_isolation(data)


def test_parallel_and_context_memory_margin():
    hw = {"memory_available": 4 * GIB, "gpus": []}
    one = assess_fit(GIB, hw, parallel=1)
    two = assess_fit(GIB, hw, parallel=2)
    four = assess_fit(GIB, hw, parallel=4)
    assert two["estimated_memory"] - one["estimated_memory"] == 256 * 1024**2
    assert four["runtime_allowance"] == 4 * one["runtime_allowance"]
    assert (
        assess_fit(GIB, hw, parallel=2, context_length=8192)["estimated_memory"]
        == four["estimated_memory"]
    )
    tight = {**hw, "memory_available": one["estimated_memory"] + 512 * 1024**2}
    assert assess_fit(GIB, tight, parallel=1)["fits"]
    assert not assess_fit(GIB, tight, parallel=2)["fits"]
