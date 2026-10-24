# Development

[Overview](README.md) · [Installation & usage](install.md) · [Troubleshooting](trouble_shooting.md) · [Development](development.md)

## Setup and checks

```bash
cd /path/to/Llameros
uv sync --extra dev --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -m compileall -q src/llameros
for script in src/llameros/static/*.js; do node --check "$script"; done
node --test tests/*.test.cjs
```

Tests use fake PAM/Docker/catalogue services; they do not use your password,
download large models, or modify your Docker daemon. A successful real PAM login
and GPU/container inference need local acceptance testing on the target host.
For multi-device acceptance, verify NVIDIA/AMD isolation, simultaneous CPU/GPU
loads, the same model on two devices, independent stopping, slot-specific access
URLs, and migration of an existing container. Automated fake-runtime tests do
not establish real driver compatibility or visual browser correctness.

## Architecture

- `src/llameros/app.py`: FastAPI routes, session/CSRF protection, background jobs,
  inference routing, and static dashboard delivery.
- `src/llameros/runtimes.py`: CPU/GPU slots, persistent device/port registry,
  model placement, and coordinated stopping/storage changes.
- `src/llameros/docker.py`: constrained Docker CLI/API access and device isolation.
- `src/llameros/hardware.py`: host telemetry and conservative memory-fit estimates.
- `src/llameros/catalog.py`: official model discovery, tags, and descriptions.
- `src/llameros/model_storage.py` and `storage.py`: model inventory/preferences
  and SQLite history. `auth.py` handles PAM and in-memory sessions.
- `src/llameros/static/`: plain HTML/CSS/JavaScript; no frontend build step.
- `tests/`: Python integration/unit tests and Node.js frontend tests.

Dashboard lifecycle and inference jobs share a single-worker lock. Models can
remain loaded on several slots, but dashboard jobs are serialized. Direct Ollama
clients bypass that lock, fit guards, and token accounting. Preserve ownership
and hardware-assignment checks when changing container lifecycle code.

## Security model and limitations

- The application does not sudo, read shadow files, switch identity, or create
  privileged host helpers. Linux PAM authenticates the current user and checks
  account policy. PAM configuration differs by distribution; test it locally.
  Passwordless, locked, MFA-only, and expired-password accounts may not support
  this simple username/password flow. Do not weaken PAM or run as root to fix it.
- Rootless Docker is recommended and required by the example configuration.
  The current application default also accepts rootful Docker; set
  `allow_rootful_docker = false` to refuse it. Docker-group membership/rootful
  socket access already carries root-equivalent authority, even if this web
  process has an ordinary UID. Rootless mode does not eliminate kernel risks.
- HTTPS is mandatory; the CLI refuses root/setuid execution. Sessions are opaque,
  in memory, expire absolutely, and disappear on restart. Cookies are Secure,
  HttpOnly, SameSite=Strict, and host-only. Mutations require matching HTTPS Origin
  and CSRF token. Login attempts are globally limited to five per minute; a hostile
  client can therefore temporarily deny login. No CORS or proxy-header trust.
- No shell execution or browser-supplied Docker arguments, image names, devices,
  or hosts. The authenticated storage setting permits only a validated, dedicated
  directory mount to a fixed container destination. Only official library model
  names are accepted for lifecycle operations. Container
  ownership labels are checked. Containers have capabilities dropped,
  no-new-privileges, bounded logs/PIDs, no Docker socket mount, and no automatic
  restart. These measures are defense in depth, not a sandbox guarantee.
- Ollama's API is **not authenticated** on loopback. Other local processes can
  access it directly. Do not publish its port on a LAN. Keep Llameros bound to
  loopback unless you understand the additional exposure; no public-Internet
  deployment, reverse proxy, or multi-user hosting is supported in this MVP.
- CPU is supported by default. NVIDIA needs NVIDIA Container Toolkit; AMD needs
  ROCm-compatible hardware, devices, and permissions. Rootless GPU setups may
  require additional administrator configuration. Integrated-GPU memory may be
  shared with RAM and is not double-counted. Validate GPU execution on your target
  hardware before relying on it.
- Fit estimates are **heuristics**, not guarantees: weights × 1.25 plus runtime
  allowance of 25% of weights (minimum 256 MiB, maximum 2 GiB), with a 512 MiB
  reserve subtracted from currently available RAM. This replaces the old fixed
  3 GiB overhead that incorrectly excluded tiny CPU models. The same heuristic
  is used for catalogue, download, and load checks. It does not fully model KV cache, architecture,
  long contexts, MoE, disk capacity, or concurrent processes. RAM and VRAM are not
  simply summed. Large contexts may still exhaust memory. Runtime checks are
  repeated before downloads/loads; GPU availability can change afterward.
- Official catalogue HTML and registry manifests are fetched over HTTPS and
  cached for ten minutes. HTML parsing can break if Ollama changes its site.
  Network access is needed to discover/download models; installed models can run
  offline. Cloud-only/unsupported manifests without downloadable layers are rejected.
- Resource history is sampled only while Llameros is running. Per-model token and
  timing totals cover **only requests through Llameros**, not direct Ollama clients.
  Container metrics cannot reliably be attributed to individual models. Models
  can remain running when the dashboard exits; use **Stop Ollama** explicitly.
- Passwords, prompts, and responses are not saved in SQLite. The latest response
  exists temporarily in the in-memory job result and browser. Database/state files
  are private to the current user. Back up the database only if you want to retain
  usage history. This is an initial implementation, not a security-audited product.
