"""Validated persistent Ollama environment policy shared by CPU and GPU runtimes."""

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OllamaSettings(BaseModel):
    """Use documented environment values; reject durations Go cannot represent."""

    model_config = ConfigDict(extra="forbid")
    ollama_keep_alive: str = Field(default="-1", min_length=1, max_length=128, strict=True)
    ollama_flash_attention: bool = Field(default=True, strict=True)
    ollama_kv_cache_type: Literal["f16", "q8_0", "q4_0"] = "q4_0"
    confirmed: bool = Field(default=False, strict=True, exclude=True)

    @field_validator("ollama_keep_alive")
    @classmethod
    def duration(cls, value):
        if re.fullmatch(r"[+-]?\d+", value):
            if abs(int(value)) > (2**63 - 1) // 1_000_000_000:
                raise ValueError("Keep alive seconds exceed the supported duration range")
            return value
        number = r"(?:\d+(?:\.\d*)?|\.\d+)"
        unit = r"(?:ns|us|µs|μs|ms|s|m|h)"
        if not re.fullmatch(rf"[+-]?(?:{number}{unit})+", value):
            raise ValueError("Use integer seconds or a duration such as -1, 0, 5m, 1h30m")
        factors = {
            "ns": 1,
            "us": 1000,
            "µs": 1000,
            "μs": 1000,
            "ms": 1_000_000,
            "s": 1_000_000_000,
            "m": 60_000_000_000,
            "h": 3_600_000_000_000,
        }
        total = sum(
            Decimal(amount) * factors[suffix]
            for amount, suffix in re.findall(rf"({number})({unit})", value.lstrip("+-"))
        )
        if total > 2**63 - 1:
            raise ValueError("Keep alive exceeds the supported duration range")
        return value


def keep_alive_value(settings):
    """Numeric seconds use the API's numeric form; durations retain their unit suffix."""
    value = settings.ollama_keep_alive
    return int(value) if re.fullmatch(r"[+-]?\d+", value) else value
