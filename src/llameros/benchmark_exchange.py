"""Privacy-reduced benchmark protocol; no raw runtime or hardware dictionaries cross it."""

import ipaddress
import json
import platform
import re
import ssl
import statistics
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import validate_model

MEMORY_BUCKETS = (1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 512, 1024)
CORE_BUCKETS = (1, 2, 4, 8, 16, 32, 64, 128, 256)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SharingSettings(StrictModel):
    enabled: bool = False
    host: str = Field(default="127.0.0.1", max_length=45)
    port: int = Field(default=9443, ge=1, le=65535)
    ca_certificate: str = Field(default="", max_length=16384)

    @field_validator("host")
    @classmethod
    def address(cls, value):
        address = ipaddress.ip_address(value)
        if address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError(
                "Use a destination IPv4/IPv6 address, not a wildcard or link-local address"
            )
        return str(address)

    @field_validator("ca_certificate")
    @classmethod
    def certificate(cls, value):
        if value:
            if "PRIVATE KEY" in value:
                raise ValueError("Paste public certificates only, never a private key")
            try:
                ssl.create_default_context(cadata=value)
            except (ssl.SSLError, ValueError):
                raise ValueError("Invalid public PEM certificate") from None
        return value

    @property
    def url(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"https://{host}:{self.port}"


class HardwareProfile(StrictModel):
    architecture: Literal["x86_64", "aarch64", "other"]
    logical_cores: int
    memory_gib: int
    accelerator: str = Field(max_length=48)
    accelerator_memory_gib: int
    unified_memory: bool

    @field_validator("logical_cores")
    @classmethod
    def cores(cls, value):
        if value not in CORE_BUCKETS:
            raise ValueError("Invalid core bucket")
        return value

    @field_validator("memory_gib", "accelerator_memory_gib")
    @classmethod
    def memory(cls, value):
        if value not in (0, *MEMORY_BUCKETS):
            raise ValueError("Invalid memory bucket")
        return value

    @field_validator("accelerator")
    @classmethod
    def product(cls, value):
        if value not in {
            "cpu",
            "amd-other",
            "intel-other",
            "nvidia-other",
            "other",
        } and not re.fullmatch(
            r"nvidia-(?:RTX (?:[2345]0[56789]0|[456]000)|[AH](?:100|200)|L40|GB10)", value
        ):
            raise ValueError("Only public accelerator product classes are supported")
        return value


class SharedCase(StrictModel):
    name: str = Field(max_length=24)
    tokens: int = Field(ge=1, le=10000000)
    seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)
    wall_seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)
    timing: Literal["generation", "request"]


class SharedBenchmark(StrictModel):
    protocol: Literal["llameros-six-cases-v2"] = "llameros-six-cases-v2"
    model: str = Field(max_length=210)
    model_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    kind: Literal["chat", "embedding"]
    hardware: HardwareProfile
    context_length: int = Field(ge=1, le=1048576)
    parallel: int = Field(ge=1, le=256)
    flash_attention: bool
    kv_cache_type: Literal["f16", "q8_0", "q4_0"]
    tokens_per_second: float = Field(gt=0, le=10000000, allow_inf_nan=False)
    timing: Literal["generation", "request", "mixed"]
    cases: list[SharedCase] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def measurements(self):
        from .benchmark import CHAT_CASES, EMBED_CASES

        workloads = CHAT_CASES if self.kind == "chat" else EMBED_CASES
        if [case.name for case in self.cases] != [case[0] for case in workloads]:
            raise ValueError("Require all six standard workloads in order")
        for case, workload in zip(self.cases, workloads, strict=True):
            if self.kind == "chat" and case.tokens > workload[2]:
                raise ValueError("Output token count exceeds workload limit")
            if self.kind == "embedding" and case.timing != "request":
                raise ValueError("Embeddings use request timing")
            if case.timing == "request" and abs(case.seconds - case.wall_seconds) > 0.000002:
                raise ValueError("Request duration must match wall duration")
            if case.seconds > case.wall_seconds * 1.1 + 0.001:
                raise ValueError("Evaluation duration exceeds wall duration")
            if case.tokens / case.seconds > 10000000:
                raise ValueError("Case throughput exceeds supported range")
        timings = {case.timing for case in self.cases}
        expected_timing = next(iter(timings)) if len(timings) == 1 else "mixed"
        rate = round(
            sum(case.tokens for case in self.cases) / sum(case.seconds for case in self.cases), 1
        )
        if (
            rate <= 0
            or self.timing != expected_timing
            or abs(self.tokens_per_second - rate) > 0.000001
        ):
            raise ValueError("Reported totals do not match the six cases")
        # Recompute rather than trusting the client's aggregate (also canonicalizes hashes).
        self.tokens_per_second = rate
        return self

    @field_validator("model")
    @classmethod
    def public_name(cls, value):
        return validate_model(value)


class BenchmarkBatch(StrictModel):
    entries: list[SharedBenchmark] = Field(min_length=1, max_length=50)


class ShareConfirmation(BenchmarkBatch):
    entries: list[SharedBenchmark] = Field(min_length=1, max_length=10)
    confirmed: bool = False
    destination: str = Field(max_length=256)


class PublicUpload(BenchmarkBatch):
    entries: list[SharedBenchmark] = Field(min_length=1, max_length=10)


def bucket(value, choices):
    """Use coarse, bounded categories rather than exact machine measurements."""
    return next((item for item in choices if (value or 0) <= item), choices[-1])


def hardware_profile(hardware, target=None):
    """Never copy names, UUIDs, devices, free memory, hostnames, or serial numbers."""
    architecture = platform.machine().lower()
    architecture = {"amd64": "x86_64", "arm64": "aarch64"}.get(architecture, architecture)
    accelerator = "cpu"
    if target and target.get("vendor") != "cpu":
        vendor = target.get("vendor")
        accelerator = f"{vendor}-other" if vendor in {"amd", "intel", "nvidia"} else "other"
        if vendor == "nvidia":
            match = re.search(
                r"\b(RTX (?:[2345]0[56789]0|[456]000)|[AH](?:100|200)|L40|GB10)\b",
                target.get("name", ""),
                re.IGNORECASE,
            )
            if match:
                accelerator = "nvidia-" + match[1].upper()
    return HardwareProfile(
        architecture=architecture if architecture in {"x86_64", "aarch64"} else "other",
        logical_cores=bucket(hardware.get("logical_cores"), CORE_BUCKETS),
        memory_gib=bucket((hardware.get("memory_total") or 0) / 1024**3, MEMORY_BUCKETS),
        accelerator=accelerator,
        accelerator_memory_gib=bucket((target.get("total") or 0) / 1024**3, MEMORY_BUCKETS)
        if target and target.get("total")
        else 0,
        unified_memory=bool(target and target.get("unified_memory")),
    )


def shareable(row):
    """Construct an explicit allowlist, not a copy with a blacklist of private fields."""
    if row.get("status") != "completed" or len(row.get("cases", [])) != 6:
        raise ValueError("Only completed six-case benchmarks can be shared")
    cases = [
        SharedCase(
            name=case["name"],
            tokens=case["tokens"],
            seconds=round(case["seconds"], 6),
            wall_seconds=round(case["wall_seconds"], 6),
            timing=case["timing"],
        )
        for case in row["cases"]
    ]
    timing = {case.timing for case in cases}
    return SharedBenchmark(
        model=row["model"],
        model_digest=row.get("model_digest"),
        kind=row["kind"],
        hardware=HardwareProfile.model_validate(row["share_hardware"]),
        context_length=row["context_length"],
        parallel=row["parallel"],
        flash_attention=row["share_environment"]["flash_attention"],
        kv_cache_type=row["share_environment"]["kv_cache_type"],
        tokens_per_second=round(
            sum(case.tokens for case in cases) / sum(case.seconds for case in cases), 1
        ),
        timing=next(iter(timing)) if len(timing) == 1 else "mixed",
        cases=cases,
    )


def exchange(settings, method, path, payload=None):
    """Verified, bounded HTTPS without cookies, redirects, proxy env, or local auth headers."""
    if not settings.enabled:
        raise ValueError("Benchmark sharing is disabled")
    context = ssl.create_default_context()
    if settings.ca_certificate:
        context.load_verify_locations(cadata=settings.ca_certificate)
    # A self-signed leaf can be an explicit trust anchor without disabling name validation.
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    with httpx.Client(
        verify=context, trust_env=False, follow_redirects=False, timeout=15
    ) as client:
        client.headers.clear()
        client.headers["User-Agent"] = "Llameros-benchmark-exchange"
        with client.stream(method, settings.url + path, json=payload) as response:
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("Statistics response too large")
    return json.loads(raw)


def rank_matches(entries, profiles, context, parallel, flash_attention, kv_cache_type):
    """Local matching: no hardware query or installed-model list leaves the instance."""
    groups = {}
    for entry in entries:
        if (
            entry.hardware not in profiles
            or entry.context_length != context
            or entry.parallel != parallel
            or entry.flash_attention != flash_attention
            or entry.kv_cache_type != kv_cache_type
        ):
            continue
        key = (
            entry.kind,
            entry.model,
            entry.model_digest,
            entry.timing,
            entry.hardware.model_dump_json(),
        )
        groups.setdefault(key, {})[entry.model_dump_json()] = entry.tokens_per_second
    results = [
        {
            "kind": kind,
            "model": model,
            "model_digest": digest,
            "hardware": json.loads(hardware),
            "timing": timing,
            "tokens_per_second": statistics.mean(values.values()),
            "samples": len(values),
            "minimum": min(values.values()),
            "maximum": max(values.values()),
        }
        for (kind, model, digest, timing, hardware), values in groups.items()
    ]
    return sorted(results, key=lambda item: (item["kind"], -item["tokens_per_second"]))
