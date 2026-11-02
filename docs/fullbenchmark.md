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
directory**, not the application's runtimes or model library. New runs benchmark
**only the first supported GPU**, in hardware discovery order. CPU benchmarking
requires explicit `--target cpu`; a machine without a supported GPU stops with an
explanation instead of falling back to CPU.

List GPU IDs, names and support status without starting Docker, creating runtime
storage, accessing the statistics server, or requiring upload consent:

```sh
uv run --no-sync python RunFullbenchmark.py --list-gpus
```

Use `--target gpu-<hash>` to select a particular GPU, or repeat `--target` for
multiple devices. These stable IDs are not `gpu0`/`gpu1`. Internal target IDs stay
in the local plan; only coarse hardware buckets are uploaded. Automatic selection
is saved in the checkpoint, so resume does not switch to a different GPU if device
order changes. Older checkpoints preserve their original job targets, including
CPU jobs. Keep the original command options when resuming; use another `--run-dir`
for a deliberately different plan, preserving the old checkpoint for recovery.

### Configure uploads

Uploads are open to anyone; no token or contributor account is required. Every
submitted result stays private until an administrator confirms it.

No manual certificate download is required, including for self-signed HTTPS
servers. Without `--ca-cert`, the runner automatically retrieves and saves the
server certificate on first upload in `server-trust.json` inside the private run
directory. It prints the destination and SHA-256 fingerprint. This is **trust on
first use (TOFU)**: the initial server identity is not independently authenticated
and an attacker on that first connection could impersonate it. `--confirm-share`
includes consent to this first-use trust behavior. No benchmark payload is sent
during certificate discovery.

Later uploads/resumes reuse the saved trust anchor and never silently replace it.
The TLS chain, expiry and destination IP SAN are still verified. Certificate
renewal/replacement may require deliberate trust updates; back up the trust file,
verify the replacement fingerprint independently, and use `--ca-cert` to override
with the verified public PEM. Do not delete trust state to bypass an unexplained
certificate change. A new run directory establishes new first-use trust.

For authenticated trust from the first connection, obtain a public PEM through a
trusted channel and pass `--ca-cert certificates/statistics-cert.pem`. This takes
precedence over TOFU. Never supply a private key. There is no fallback to unverified
benchmark uploads, and expired or wrong-IP certificates are rejected.

## Start in the background

The default destination is `92.205.18.100:9443`; override `--server` and `--port`
when using another statistics server:

```sh
uv run --no-sync python RunFullbenchmark.py start \
  --server 92.205.18.100 --port 9443 \
  --confirm-share
```

The command prints a background PID and returns; terminal logout does not send it
a hangup through the launcher. Host/session policies can still terminate user
processes. For unattended boot/restart supervision, use your user service manager;
pass `--foreground` when a supervisor should own the process. The runner does not
install or start a systemd service automatically.

Options:

- `--list-gpus`: show GPU IDs/names/support and exit, without starting a run.
- `--target gpu-<hash>`: choose a GPU; default is the first supported GPU only.
  Repeat for multiple targets, or use `--target cpu` for explicit CPU benchmarks.
- `--ca-cert certificates/statistics-cert.pem`: verified trust instead of automatic TOFU.
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
- `fullbenchmark/server-trust.json`: private per-destination first-use trust anchors.
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