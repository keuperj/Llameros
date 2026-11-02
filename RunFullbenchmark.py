#!/usr/bin/env python3
"""Background, resumable official-library six-case benchmarks (never stress tests)."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from llameros.benchmark import measure
from llameros.benchmark_exchange import (
    SharingSettings,
    exchange,
    hardware_profile,
    inspect_server_certificate,
    shareable,
)
from llameros.catalog import Catalog, validate_model
from llameros.config import Settings
from llameros.diagnostics import diagnostic
from llameros.filesystem import trusted_directory
from llameros.hardware import assess_fit, snapshot
from llameros.model_storage import write_json
from llameros.runtimes import Runtimes


class RunnerConfigurationError(ValueError):
    """Locally generated, safe-to-display configuration diagnostics."""


class RunnerRuntimeError(RuntimeError):
    """Safe local runtime/recovery failure, without raw Docker or HTTP output."""


@dataclass(frozen=True)
class BenchmarkSettings(Settings):
    """Keep benchmark containers and their volume separate from the application."""

    @property
    def container_name(self):
        identity = hashlib.sha256(str(self.state_dir).encode()).hexdigest()[:12]
        return f"llameros-fullbenchmark-{os.getuid()}-{identity}"


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def fits(size, hardware, target, settings):
    estimate = assess_fit(
        size, hardware, "cpu", settings.ollama_num_parallel, settings.context_length
    )
    if not estimate["fits"]:
        return False
    if target["id"] == "cpu":
        return True
    available = (
        hardware["memory_available"] - 512 * 1024**2
        if target.get("unified_memory")
        else target.get("free", 0)
    )
    return estimate["estimated_memory"] <= max(0, available or 0)


def discover(catalog, hardware, targets, settings, report, families=None):
    """Snapshot discoverable official tags; isolate metadata failures per family/model."""
    jobs, errors, skipped = [], [], 0
    for family in families or catalog.list():
        try:
            names = catalog.tags(family)
        except Exception:
            errors.append({"family": family, "error": "tag-discovery-failed"})
            continue
        for name in dict.fromkeys(names):
            report({"phase": "discovery", "model": name, "jobs": len(jobs)})
            try:
                size = catalog.size(name)
                matches = [target for target in targets if fits(size, hardware, target, settings)]
                jobs.extend(
                    {"model": name, "target": target["id"], "size": size} for target in matches
                )
                if not matches:
                    skipped += 1
            except Exception:
                errors.append({"model": name, "error": "manifest-unavailable"})
    return jobs, errors, skipped


def evaluate(settings, job, folder):
    """Child process: download, warm up, and run only the six sequential cases."""

    def progress(value):
        write_json(folder / "progress.json", {**value, "updated": time.time(), **job})

    try:
        pool = Runtimes(settings, snapshot)
        client = pool.client(job["target"], register=True)
        client.start(progress=progress)
        progress({"phase": "download", "status": "Downloading model"})
        client.pull(job["model"], progress)
        pool.load(job["model"], job["size"], job["target"], settings.context_length, progress)
        result = asyncio.run(
            measure(
                client,
                job["model"],
                job["target"],
                settings.context_length,
                settings.ollama_num_parallel,
                progress,
                policy=None,
            )
        )
        result["share_hardware"] = hardware_profile(snapshot(), client.target).model_dump()
        result["share_environment"] = {
            "flash_attention": settings.ollama_flash_attention,
            "kv_cache_type": settings.ollama_kv_cache_type,
        }
        # Validate the publication evidence before marking a job successful.
        payload = shareable(result).model_dump()
        write_json(folder / "worker-result.json", {"status": "completed", "entry": payload})
    except Exception as exc:
        write_json(
            folder / "worker-result.json",
            {"status": "failed", "error": "model-evaluation-failed", "diagnostic": diagnostic(exc)},
        )


def cleanup(settings, job, folder):
    """Remove only this runner's model; container stop also recovers stalled inference."""
    client = None
    stage = "runtime-init"
    try:
        pool = Runtimes(settings, snapshot)
        client = pool.client(job["target"], register=True)
        stage = "container-stop"
        client.stop()
        stage = "container-start"
        client.start()
        stage = "model-list"
        models = client.api("GET", "/api/tags", timeout=10).get("models", [])
        if any(item.get("name", item.get("model")) == job["model"] for item in models):
            stage = "model-delete"
            client.api("DELETE", "/api/delete", {"model": job["model"]}, timeout=30)
        stage = "final-stop"
        client.stop()
        write_json(folder / "cleanup-result.json", {"ok": True})
    except Exception as exc:
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass
        write_json(
            folder / "cleanup-result.json",
            {"ok": False, "stage": stage, "diagnostic": diagnostic(exc)},
        )


def cleanup_failure(folder, *, interrupted):
    result = read_json(folder / "cleanup-result.json", {})
    stages = {
        "runtime-init",
        "container-stop",
        "container-start",
        "model-list",
        "model-delete",
        "final-stop",
    }
    stage = result.get("stage")
    stage = stage if stage in stages else "worker-timeout-or-crash"
    # Do not echo arbitrary contents from existing checkpoint files.
    reason = "Interrupted-model cleanup failed" if interrupted else "Cleanup failed"
    return RunnerRuntimeError(
        f"{reason} at {stage}; stopping to avoid accumulating model weights. "
        "Inspect cleanup-result.json and progress.json in --run-dir, check Docker access "
        "and the isolated container logs, then resume with the same options. "
        "Preserve the checkpoint and model volume for recovery."
    )


def bounded(function, args, seconds):
    """Contain blocked downloads/runtime calls; do not let a single model stall the run."""
    process = multiprocessing.get_context("spawn").Process(target=function, args=args)
    process.start()
    try:
        process.join(seconds)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
    return process.exitcode == 0


def upload_policy(policy, folder):
    """Trust on first use per destination/run; never silently replace a saved anchor."""
    if policy.ca_certificate:
        return policy
    path = folder / "server-trust.json"
    anchors = read_json(path, {})
    if policy.url not in anchors:
        try:
            certificate = inspect_server_certificate(policy)
        except (OSError, ValueError):
            raise RunnerConfigurationError(
                "Cannot establish server certificate trust. Check server reachability, "
                "certificate expiry and destination IP SAN. No upload was attempted."
            ) from None
        anchors[policy.url] = certificate["ca_certificate"]
        write_json(path, anchors)
        print(
            f"First-use TLS trust saved for {policy.url}; SHA-256 {certificate['sha256']}. "
            "Initial trust was not independently authenticated. Use --ca-cert for verified trust.",
            flush=True,
        )
    return SharingSettings(**{**policy.model_dump(), "ca_certificate": anchors[policy.url]})


def select_targets(available, requested, state):
    """Pin automatic selection in checkpoints; never silently resume on another GPU."""
    supported = {key: target for key, target in available.items() if target["supported"]}
    selected = requested
    if not selected:
        selected = state.get("selected_targets")
        if not selected and state.get("jobs"):
            # Preserve existing pre-GPU-default plans, including any CPU jobs.
            selected = list(dict.fromkeys(job["target"] for job in state["jobs"]))
        if not selected:
            selected = [key for key in supported if key != "cpu"][:1]
    if not selected or set(selected) - supported.keys():
        raise RunnerConfigurationError(
            "Requested/default GPU target is unavailable. Supported target IDs: "
            f"{', '.join(supported) or 'none'}. Use --list-gpus to inspect hardware, "
            "an exact --target gpu-<hash>, or --target cpu for explicit CPU benchmarking. "
            "No CPU fallback is used by default."
        )
    return [supported[key] for key in dict.fromkeys(selected)]


def upload(state, folder, policy, send=exchange, pause=time.sleep):
    """Persist upload intent before sending; leave uncertain submissions for manual review."""
    pending = [
        row for row in state["results"] if row["status"] == "completed" and "upload" not in row
    ]
    if pending and policy is not None:
        policy = upload_policy(policy, folder)
    for offset in range(0, len(pending), 10):
        if offset:
            # Stay below the statistics server's default 20 uploads/minute.
            pause(3.2)
        batch = pending[offset : offset + 10]
        for row in batch:
            row["upload"] = "uncertain"
        write_json(folder / "status.json", state)
        try:
            send(
                policy,
                "POST",
                "/api/v1/benchmarks",
                {
                    "entries": [row["entry"] for row in batch],
                },
            )
        except Exception:
            # A timeout may occur after acceptance: never automatically replay it.
            continue
        for row in batch:
            row["upload"] = "submitted-for-moderation"
        write_json(folder / "status.json", state)


def run(args, folder):
    settings = BenchmarkSettings(
        state_dir=folder / "runtime",
        context_length=args.context,
        ollama_num_parallel=1,
        max_loaded_models=1,
        allow_rootful_docker=args.allow_rootful_docker,
    )
    settings.prepare()
    policy = SharingSettings(
        enabled=True,
        host=args.server,
        port=args.port,
        ca_certificate=args.ca_cert.read_text() if args.ca_cert else "",
    )
    fingerprint = {
        "context": args.context,
        "destination": policy.url,
        "families": args.family,
        "targets": args.target,
        "rootful": args.allow_rootful_docker,
    }
    state = read_json(folder / "status.json", {})
    if state and state["configuration"] != fingerprint:
        changed = ", ".join(
            key for key, value in fingerprint.items() if state["configuration"].get(key) != value
        )
        raise RunnerConfigurationError(
            f"Resume configuration differs ({changed}). Resume with the original options "
            "or choose a separate --run-dir. Keep the existing checkpoint for recovery."
        )

    def report(value):
        write_json(folder / "progress.json", {**value, "updated": time.time()})

    pool = Runtimes(settings, snapshot)
    targets = select_targets(pool.targets, args.target, state)
    state = state or {"configuration": fingerprint, "results": [], "created": time.time()}
    state.update(
        pid=os.getpid(),
        phase="discovery",
        updated=time.time(),
        selected_targets=[target["id"] for target in targets],
    )
    write_json(folder / "status.json", state)
    if "jobs" not in state:
        jobs, errors, skipped = discover(
            Catalog(), snapshot(), targets, settings, report, args.family
        )
        state.update(jobs=jobs, discovery_errors=errors, skipped_memory=skipped)
        write_json(folder / "status.json", state)
    # An interrupted job may have downloaded weights: clean it before proceeding.
    if state.get("active"):
        recover = state["active"]
        (folder / "cleanup-result.json").unlink(missing_ok=True)
        bounded(cleanup, (settings, recover, folder), args.cleanup_timeout)
        if not read_json(folder / "cleanup-result.json", {}).get("ok"):
            raise cleanup_failure(folder, interrupted=True)
        state.pop("active")
        for row in state["results"]:
            if row["model"] == recover["model"] and row["target"] == recover["target"]:
                row["cleaned"] = True
        write_json(folder / "status.json", state)
    done = {(row["model"], row["target"]) for row in state["results"]}
    for job in state["jobs"]:
        if (job["model"], job["target"]) in done:
            continue
        state.update(active=job, phase="benchmark", updated=time.time())
        write_json(folder / "status.json", state)
        for filename in ("worker-result.json", "cleanup-result.json"):
            (folder / filename).unlink(missing_ok=True)
        bounded(evaluate, (settings, job, folder), args.model_timeout)
        result = read_json(
            folder / "worker-result.json", {"status": "failed", "error": "worker-timeout-or-crash"}
        )
        bounded(cleanup, (settings, job, folder), args.cleanup_timeout)
        cleaned = read_json(folder / "cleanup-result.json", {}).get("ok", False)
        state["results"].append({**job, **result, "cleaned": cleaned})
        state.update(updated=time.time())
        if cleaned:
            state.pop("active", None)
        write_json(folder / "status.json", state)
        print(
            f"{len(state['results'])}/{len(state['jobs'])} {job['model']} {job['target']}: {result['status']}; cleaned={cleaned}",
            flush=True,
        )
        if not cleaned:
            raise cleanup_failure(folder, interrupted=False)
    state.update(phase="upload", updated=time.time())
    upload(state, folder, policy)
    state.update(phase="finished", updated=time.time())
    write_json(folder / "status.json", state)
    submitted = sum(row.get("upload") == "submitted-for-moderation" for row in state["results"])
    print(
        f"Finished {len(state['results'])} jobs; submitted {submitted} results for moderation. See status.json for failures and unsent/uncertain uploads.",
        flush=True,
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list-gpus",
        action="store_true",
        help="List local GPU target IDs and support status, without Docker or benchmarks",
    )
    parser.add_argument("command", choices=["start", "status"], nargs="?", default="start")
    parser.add_argument("--run-dir", type=Path, default=Path("fullbenchmark"))
    parser.add_argument("--server", default="92.205.18.100", help="Statistics server literal IP")
    parser.add_argument("--port", type=int, default=9443)
    parser.add_argument(
        "--ca-cert",
        type=Path,
        help="Verified public PEM; otherwise trust and save the server certificate on first use",
    )
    parser.add_argument(
        "--confirm-share",
        action="store_true",
        help="Consent to uploads and automatic first-use TLS trust unless --ca-cert is provided",
    )
    parser.add_argument(
        "--family", action="append", help="Optional official model family; repeatable"
    )
    parser.add_argument(
        "--target",
        action="append",
        help="CPU/GPU target ID; repeatable. Default: first supported GPU only, no CPU fallback",
    )
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--model-timeout", type=int, default=3600)
    parser.add_argument("--cleanup-timeout", type=int, default=300)
    parser.add_argument("--allow-rootful-docker", action="store_true")
    parser.add_argument(
        "--foreground", action="store_true", help="Stay attached instead of detaching"
    )
    args = parser.parse_args(argv)
    if args.list_gpus:
        gpus = [
            target for key, target in Runtimes.hardware_targets(snapshot()).items() if key != "cpu"
        ]
        if not gpus:
            print("No GPUs detected. CPU benchmarking requires --target cpu.")
        for target in gpus:
            print(
                f"{target['id']} · {target['label']} · "
                f"{'supported' if target['supported'] else 'unsupported'}"
            )
        return 0
    folder = args.run_dir
    if args.command == "status":
        state = read_json(folder / "status.json", {})
        counts = {
            key: sum(row.get("status") == key for row in state.get("results", []))
            for key in ("completed", "failed")
        }
        locked = False
        if (folder / "run.lock").exists():
            with (folder / "run.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    locked = True
        print(
            json.dumps(
                {
                    "running": locked,
                    "phase": state.get("phase", "not-started"),
                    "finished_jobs": len(state.get("results", [])),
                    "total_jobs": len(state.get("jobs", [])),
                    **counts,
                    "submitted": sum(
                        row.get("upload") == "submitted-for-moderation"
                        for row in state.get("results", [])
                    ),
                    "unsent": sum(
                        row.get("status") == "completed" and "upload" not in row
                        for row in state.get("results", [])
                    ),
                    "uncertain_uploads": sum(
                        row.get("upload") == "uncertain" for row in state.get("results", [])
                    ),
                    "discovery_errors": len(state.get("discovery_errors", [])),
                    "skipped_memory": state.get("skipped_memory", 0),
                    "progress": read_json(folder / "progress.json", {}),
                },
                indent=2,
            )
        )
        return 0
    if not args.confirm_share:
        parser.error("--confirm-share is required; uploads are not automatic without consent")
    if min(args.context, args.model_timeout, args.cleanup_timeout) <= 0:
        parser.error("Context and timeouts must be positive")
    if args.ca_cert and not args.ca_cert.is_file():
        parser.error("Optional CA certificate must exist")
    for family in args.family or []:
        if ":" in validate_model(family):
            parser.error("--family takes an official model family without a tag")
    if os.geteuid() == 0:
        parser.error("Run as an unprivileged login user, not root")
    os.umask(0o077)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    trusted_directory(folder.absolute())
    if not args.foreground:
        arguments = list(argv if argv is not None else sys.argv[1:])
        with (folder / "run.log").open("a") as log:
            child = subprocess.Popen(
                [sys.executable, __file__, *arguments, "--foreground"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        print(
            f"Started PID {child.pid}; use: uv run --no-sync python RunFullbenchmark.py status --run-dir {folder}"
        )
        return 0
    with (folder / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("This run directory is already active", file=sys.stderr)
            return 1

        # Do not leave evaluation children alive on a graceful stop.
        def stop(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        try:
            return run(args, folder)
        except (RunnerConfigurationError, RunnerRuntimeError) as exc:
            print(f"Run stopped: {exc}", file=sys.stderr)
            return 1
        except (Exception, KeyboardInterrupt) as exc:
            print(
                f"Run stopped ({type(exc).__name__}); inspect status and resume with the same arguments",
                file=sys.stderr,
            )
            return 1


if __name__ == "__main__":
    sys.exit(main())
