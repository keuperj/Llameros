# Development

[Overview](README.md) · [Installation & usage](install.md) · [Troubleshooting](trouble_shooting.md) · [Development](development.md)

Llameros helps users **manage and securely share their local Ollama models**.
Keep this local-first theme consistent across the dashboard, key management, and
client examples; bearer authentication does not imply supported public hosting.

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

The inference contract reference is recorded in `tests/ollama-compatibility.json`.
See [API compatibility](api-compatibility.md) for offline coverage, restrictions,
and opt-in HTTPS/model checks using `scripts/check_api_compatibility.py`.

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
- `src/llameros/gateway.py` and `api_keys.py`: bearer-authenticated inference,
  allowlisted runtime forwarding, streamed usage accounting, and digest-only keys.
- `src/llameros/runtimes.py`: CPU/GPU slots, persistent device/port registry,
  model placement, and coordinated stopping/storage changes.
- `src/llameros/docker.py`: constrained Docker CLI/API access and device isolation.
- `src/llameros/hardware.py`: host telemetry and conservative memory-fit estimates.
- `src/llameros/catalog.py`: official model discovery, tags, and descriptions.
- `src/llameros/model_storage.py` and `storage.py`: model inventory/preferences
  and SQLite history. `auth.py` handles PAM and in-memory sessions.
- `src/llameros/static/`: plain HTML/CSS/JavaScript; no frontend build step.
- `tests/`: Python integration/unit tests and Node.js frontend tests.

Dashboard jobs retain a single worker. Gateway requests use bounded FIFO queues
per hardware target (one executing request plus at most 16 queued, 120-second queue
timeout). CPU and distinct GPUs can execute independently. Explicitly targeted
load/unload jobs reserve that device; ambiguous and global lifecycle jobs reserve
all devices. Queued requests prevent conflicting lifecycle admission. Retrieval
happens outside the gateway and holds no execution permit. Direct Ollama clients
bypass scheduling, fit guards, and token accounting. Preserve ownership
and hardware-assignment checks when changing container lifecycle code.

## Security model and limitations

- The application does not sudo, read shadow files, switch identity, or create
  privileged host helpers. Linux PAM authenticates system login accounts and checks
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
  HttpOnly, SameSite=Strict, and host-only. Dashboard mutations require matching HTTPS Origin
  and CSRF token. Login attempts are globally limited to five per minute; a hostile
  client can therefore temporarily deny login. No CORS or proxy-header trust.
- `/inference/*` uses HTTPS bearer authentication instead of browser sessions or
  CSRF; cookies alone cannot grant inference access. Key management still requires
  PAM sessions and CSRF. High-entropy keys are digest-only at rest, scoped to model
  tags/devices, optionally expiring, and revocable. No client authorization/cookies or arbitrary
  destination URLs are forwarded upstream. Failed streams release the shared job
  lock and retain content-free accounting; incomplete token counts remain unknown.
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

## Key management contract and UI acceptance

- `POST /api/keys` requires a name. Omitted `models` means `["*"]`; the dashboard
  dropdown offers all models or one local tag. Advanced API callers may supply
  multiple model tags and hardware target restrictions.
- `expires_days` accepts `null` (default: never) or integers 1–365. UI presets are
  7, 30, 365, and never. SQLite stores no expiry as `expires=0`, preserving the
  existing NOT NULL schema and finite expiry values of old keys. Authentication
  checks revocation and `(expires=0 OR expires>now)`.
- New dashboard keys use `max_tokens=null`: no additional per-key cap. Existing
  capped keys remain capped and the advanced API still accepts caps. The global
  1–4096 output-token range and 256-token request default remain safety guardrails.
- `DELETE /api/keys/{id}` is session/CSRF protected, disables authentication, and
  retains a deleted-key tombstone for `/api/keys/usage`. No plaintext secret or
  digest is exposed by listing/history endpoints.
- The fixed-layout key table has sticky headings, right-aligned accessible delete
  icons, truncation with full-text tooltips, and a scroll region sized for ten
  entries. Test narrow and wide browser widths and more than ten keys manually.
- **Add key** is collapsed below the table. The one-time secret is a readonly
  password field, copied using Clipboard API with masked keyboard-copy fallback.
  Test copy success/denial, clear, logout, expiry presets, deletion, and usage.
  No reveal control or persistent browser secret storage is provided. Clipboard
  contents are controlled by the user/OS and are not cleared by logout.
- Run the standalone HTTPS smoke test described in the README against a trusted
  certificate and loaded model for live acceptance; automated tests use mocks.

### Dashboard user authorization

`users.py` persists admin/user roles and blocks in the history database. The process
account is seeded as admin once; later role changes survive restart. Login-shell
accounts discovered through the system password database default to user. PAM still
checks credentials and account policy; no password hashes are read by Llameros.
Admin dependencies protect lifecycle, catalogue, storage, playground, full history,
and user-management endpoints. Key endpoints use session authorization and SQL
owner scoping for users. `?board=user` additionally scopes admins' key operations
while viewing the user board; it is not a role elevation mechanism.

`GET /api/user/dashboard` returns allowlisted running-model fields, CPU/RAM samples,
overall model aggregates,
and the signed-in owner's API-key aggregates. It excludes host events, raw runtime
addresses, other users' key statistics, and stored credential digests. `POST /api/users/{username}`
is admin/CSRF protected and takes `role` (`admin` or `user`) and `blocked` (boolean).
Blocks and key revocation commit together; key creation checks blocks in a write
transaction. Blocking deletes existing sessions; unblocking restores neither keys
nor sessions. Role checks are refreshed on each request. Already-executing inference
cannot be undone; queued admission continues to honor the existing revocation checks.

The user board has no playground or runtime-management controls. Admin board
switching does not change the authenticated user's actual role. Offline tests use
injected authenticators; successful cross-account PAM login must be verified on the
deployment host without weakening PAM or running the service as root.
