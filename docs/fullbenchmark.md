# Full hardware benchmark runner

`RunFullbenchmark.py` runs all six standard speed/embedding cases, **without stress
tests**, for official Ollama model tags that fit the selected hardware estimate.
It runs in the background by default, contains each evaluation in a subprocess,
and continues after failed downloads, unsupported capabilities, OOMs and evaluation
deadlines. It does not guarantee that every model judged to fit can actually run.

## Prepare

Run from the repository root as your normal login user, never root:

```sh
uv sync --locked --no-editable
uv run --no-sync python RunFullbenchmark.py --help
```

The runner needs working rootless Docker and NVIDIA/AMD GPU support as applicable,
just like Llameros. It downloads the configured pinned Ollama image if missing.
Rootful Docker is refused unless you explicitly accept its root-equivalent access
with `--allow-rootful-docker`. Stop other inference jobs before benchmarking;
competing workloads invalidate measurements and consume memory needed by models.

The runner creates **separate containers and a separate model volume per run
directory**, not the application's runtimes or model library. It benchmarks CPU
and each supported isolated GPU sequentially by default. Internal target IDs are
recorded locally in the progress/plan; only coarse hardware buckets leave the host.
Use `--target cpu` for CPU only, or repeat `--target` with existing GPU target IDs.

### Configure uploads

Uploads are open to anyone; no token or contributor account is required. Every
submitted result stays private until an administrator confirms it.

Obtain the server's public certificate through a trusted channel and save it as
`certificates/statistics-cert.pem`. The file must contain no private key. TLS
verification is mandatory; omit `--ca-cert` only for system-trusted certificates.
Self-signed certificates are supported through this explicit trust file, including
older generated certificates on newer Python versions. Compare the public PEM's
SHA-256 fingerprint with the operator through a separate trusted channel before
use. Expired certificates and certificates lacking the destination IP SAN are
still rejected; the runner never falls back to unverified uploads.

## Start in the background

The default destination is `92.205.18.100:9443`; override `--server` and `--port`
when using another statistics server:

```sh
uv run --no-sync python RunFullbenchmark.py start \
  --server 92.205.18.100 --port 9443 \
  --ca-cert certificates/statistics-cert.pem \
  --confirm-share
```

The command prints a background PID and returns; terminal logout does not send it
a hangup through the launcher. Host/session policies can still terminate user
processes. For unattended boot/restart supervision, use your user service manager;
pass `--foreground` when a supervisor should own the process. The runner does not
install or start a systemd service automatically.

Options:

- `--family gemma3 --family embeddinggemma`: limit discovery to those families.
- `--context 4096`: context size (default 4096); parallelism is fixed at one.
- `--model-timeout 3600`: maximum seconds per download + load + evaluation.
- `--cleanup-timeout 300`: separate maximum seconds for cleanup/recovery.
- `--run-dir fullbenchmark`: private checkpoint directory; use the same directory
  and arguments to resume. Never run two processes against one run directory.

Discovery uses Llameros's existing official library/tag reader and registry
manifests. This is **not a guarantee of exhaustive registry enumeration**: tags
absent from catalogue pages, private/namespaced models, cloud-only entries and
unreadable manifests are not evaluated. Different aliases are measured separately.
Fit estimates use currently available RAM/VRAM, not total installed memory, and
are conservative heuristics. Unsupported model kinds are recorded as failures.

## Progress, results and recovery

```sh
uv run --no-sync python RunFullbenchmark.py status
tail -f fullbenchmark/run.log
```

Status reports whether the run lock is held, phase, completed/failed/total jobs,
submission count, and the latest download/case progress. During discovery the
total is still being built. No reliable completion ETA is promised. Files:

- `fullbenchmark/status.json`: durable model/target plan, results, failures,
  cleanup outcomes, and upload states.
- `fullbenchmark/progress.json`: latest worker/catalogue progress, with timestamp;
  it may describe the previous job when no new event has arrived.
- `fullbenchmark/run.log`: per-model completion reports and safe failure messages.

Send SIGTERM to the reported supervisor PID for a graceful stop. The active child
is terminated; rerun the original start command to clean up the interrupted model
and resume unfinished jobs. Finished model failures are not automatically retried;
use a new run directory for a deliberate rerun. Reboot/crash recovery also requires
rerunning the command. Do not delete the checkpoint directory during recovery.

After **every** model/target evaluation, including failures, the runner stops its
isolated container, removes the evaluated model through Ollama, and stops the
container again. It never deletes the main application's installed models.
If cleanup cannot be verified, the run stops instead of accumulating more weights;
inspect the isolated runtime and resume after fixing Docker/storage access.
Interrupted partial pulls may leave temporary/shared blobs that Ollama does not
remove through model deletion; reclaim the dedicated run volume manually only
after stopping its containers and checking that it belongs to this run. Empty
containers, volumes and the shared Docker image are not automatically removed.

Uploads happen after evaluation. `--confirm-share` explicitly consents to sending
the allowlisted model identity, coarse hardware class and six-case measurements
to the selected destination. No Linux password, prompt, hostname, GPU UUID or
contributor identity is sent. Successful uploads enter the **private
moderation queue**, not immediate publication.

Upload intent is checkpointed before sending. An error/timeout is marked
`uncertain` because the server may already have accepted the batch. Such batches
are not automatically retried. Check with the server operator before manually
resubmitting uncertain entries. Exact duplicate payloads are ignored by the server.
Keep the complete checkpoint history; restoring older state can lose submission
knowledge. Confirmed compatible reports contribute equally to arithmetic averages.

No live full-library download/evaluation is part of the automated tests. Tests
use fake runtimes and network exchanges to exercise recovery and privacy behavior.