# Development guide

[Overview](README.md) · [Installation & usage](install.md) · [Troubleshooting](trouble_shooting.md) · [API compatibility](api-compatibility.md) · [Security audit](Security_Audit.md)

Llameros helps users **manage and securely share their local Ollama models**.
Keep this local-first theme consistent across the dashboard, key management, and
client examples; bearer authentication does not imply supported public hosting.

This guide combines the contributor workflow, architecture, documentation conventions,
security model, and acceptance contracts. Read the security sections before changing
authentication, inference admission, container operations, or storage. Consult the
[security audit](Security_Audit.md) for outstanding findings and verification limits;
the controls described here are not a claim of complete security clearance.

## Contents

- [Prepare a local workspace](#prepare-a-local-workspace)
- [Architecture](#architecture)
- [Make a focused change](#make-a-focused-change)
- [Document the change](#document-the-change)
- [Setup and checks](#setup-and-checks)
- [Manual acceptance checks](#manual-acceptance-checks)
- [Security model and limitations](#security-model-and-limitations)
- [Key management contract and UI acceptance](#key-management-contract-and-ui-acceptance)
- [VPN-protected deployment advisory](#vpn-protected-deployment-advisory)


## Prepare a local workspace

Use Linux with Python 3.11 or newer, `uv`, and Node.js with support for the
built-in test runner. Python dependencies and development tools are declared in
`pyproject.toml`; `uv.lock` records their resolved versions. The dashboard uses
plain JavaScript and has no npm dependencies or frontend build step.

```bash
# Run from the repository root.
uv sync --extra dev --locked
uv run llameros --help
```

All repository commands assume your checkout root as the working directory. Docker, PAM,
and host GPU drivers are needed for corresponding live features, not for mocked
service tests. Document parsing is not supported.

Start the service only when a live check is needed:

```bash
uv run llameros
```

The normal endpoint is `https://127.0.0.1:8443`. Startup prepares application state
and TLS material. Running it is not a read-only test: later dashboard operations
can change owned containers and stored models. Use a separate local configuration
and state directory for experiments, following [installation](install.md).
Never run Llameros with sudo or weaken PAM/TLS to make a development test pass.

## Architecture

Paths below are relative to the checkout root.

| Responsibility | Main files |
|---|---|
| CLI, configuration, HTTPS certificates | `src/llameros/cli.py`, `config.py`, `tls.py` |
| Dashboard routes, application lifetime, lifecycle jobs | `src/llameros/app.py` |
| PAM, cookie sessions, persisted roles and blocking | `src/llameros/auth.py`, `users.py` |
| Bearer keys, ownership, expiry, usage | `src/llameros/api_keys.py` |
| Protocol validation, discovery, forwarding, streaming cleanup | `src/llameros/gateway.py`, `protocol.py`, `body_limits.py` |
| Device queues and lifecycle reservations | `src/llameros/scheduling.py` |
| CPU/GPU slots and managed containers | `src/llameros/runtimes.py`, `docker.py`, `runtime_transport.py` |
| Hardware estimates and official model catalogue | `src/llameros/hardware.py`, `catalog.py` |
| Model-directory safety and offline inventory | `src/llameros/model_storage.py`, `filesystem.py` |
| SQLite resource, event, and generation history | `src/llameros/storage.py` |
| Inline image admission and document rejection | `src/llameros/multimodal.py` |
| Shared page structure, controller, and styles | `src/llameros/static/index.html`, `app.js`, `style.css` |
| Board/user controls and reusable frontend helpers | `src/llameros/static/users.js`, `actions.js`, `access.js`, `catalogue.js`, `progress.js`, `usage.js` |
| Offline regressions | `tests/test_*.py`, `tests/*.test.cjs` |
| Explicitly opt-in live checks and benchmarking | `scripts/test_api_access.py`, `check_api_compatibility.py`, `benchmark_rag.py` |

Dashboard jobs retain a single worker. Gateway requests use bounded FIFO queues
per hardware target (one executing request plus at most 16 queued, 120-second queue
timeout). CPU and distinct GPUs can execute independently. Explicitly targeted
load/unload jobs reserve that device; ambiguous and global lifecycle jobs reserve
all devices. Queued requests prevent conflicting lifecycle admission. Retrieval
happens outside the gateway and holds no execution permit. Direct Ollama clients
bypass scheduling, fit guards, and token accounting. Preserve ownership
and hardware-assignment checks when changing container lifecycle code.

### Trace a dashboard change

1. Find the page element in `index.html`, then its handler in `app.js` or
   `users.js`. Keep the existing helper-script load order: these scripts share
   page globals rather than using a bundler.
2. Follow the same-origin `/api/...` call to `app.py` or the key routes installed
   by `gateway.py`.
3. Keep server-side session/role dependencies and write-request CSRF checks.
   Hiding a control is presentation, never authorization.
4. Add a Python endpoint/authorization regression and an appropriate Node helper
   or DOM-stub regression. Validate layout and keyboard use in a real browser too.

### Trace an inference change

1. A client calls an allowlisted route under `/inference` with a scoped bearer key.
2. `gateway.py` bounds and validates the request, checks model/device permission,
   and validates inline media before choosing a loaded runtime.
3. `scheduling.py` admits one executing request per device and prevents lifecycle
   conflicts. Key authentication is checked again after waiting in the queue.
4. Forwarding uses the private Unix-socket/Docker-exec relay to container loopback; no client credential is
   forwarded upstream.
5. Completion, upstream errors, cancellation, and early disconnect all need
   cleanup: close resources, record content-free usage, and release the ticket.

Changes must preserve these boundaries. Consult `api-compatibility.md` and
`tests/ollama-compatibility.json` before altering protocol fields or terminal
stream events. Adding an SDK to a client example does not make that SDK a runtime
dependency of Llameros.

## Make a focused change

- Inspect the working tree first and preserve unrelated work. Keep fixes separate
  from dependency upgrades, deployment changes, and broad formatting rewrites.
- Reuse existing collaborators and injection points. `create_app` accepts fake
  Docker/catalogue/authentication/hardware services and a gateway transport;
  `sampling=False` avoids background telemetry in isolated tests.
- Test persistent changes with temporary directories and SQLite databases, not
  the live application state. Migrate existing tables without losing owners,
  revocations, role overrides, or usage tombstones.
- Cover denial cases as well as success: ordinary users cannot reach admin APIs
  or another owner's key details. Admin personal-board mode scopes keys but does
  not alter the authenticated role. Model usage is overall; key usage is personal.
- Keep blocking and key revocation atomic. Unblocking must not restore sessions
  or revoked keys; self-blocking and self-demotion remain prohibited.
- Treat memory fit as an estimate. Preserve target identity, UID ownership labels,
  loopback bindings, and fixed Docker argument construction.
- Never store passwords, plaintext bearer keys, prompts, response text, or uploaded
  document bodies in telemetry. Browser one-time key secrets must be cleared on
  logout and must not enter persistent browser storage.

## Document the change

Use medium-detail documentation: explain the contract and non-obvious decisions,
not a line-by-line translation of the implementation.

### File headers

Every code file starts with a short description of its content and relationship
to Llameros. Use a module docstring for Python, a JSDoc `@file` block for
JavaScript/CommonJS, and native comments for HTML/CSS/SVG. Preserve shebangs,
HTML doctypes, and XML declarations before comments where applicable. Binary
assets and comment-free JSON data are not code-documentation targets.

### Python docstrings

Document modules, classes, and production functions/methods, including meaningful
nested workers and route handlers. Start with an imperative summary. Add a short
paragraph for non-obvious inputs/return values, units, side effects, exceptions,
ownership, transactions, or cancellation behavior. Typed signatures already
describe basic types; avoid repeating every obvious parameter. Keep test scenario
docstrings concise and explain reusable fixtures/fakes where their contract matters.

```python
def inventory(self) -> dict:
    """Read validated host manifests or a matching volume cache.

    Mark the source explicitly so callers can distinguish disk observations
    from cached metadata; this does not start a runtime or download weights.
    """
```

This excerpt illustrates documentation style; it is not a replacement
implementation. Keep inline comments for reasoning, security boundaries, units,
or race conditions that are not clear from the code.

### JavaScript JSDoc

Document named helpers and controller methods with a summary. Use `@param` and
`@returns` where they clarify DOM types, data shapes, optional/default values,
or promises. Describe state changes, stale-response guards, and secret handling.
Tiny event callbacks need comments only when they introduce a distinct behavior.
The repository does not require a JSDoc renderer or TypeScript conversion.

Update user-facing guides when behavior changes and update this guide when
changing the architecture or contributor workflow.

## Setup and checks

Run focused tests while iterating:

```bash
uv run pytest tests/test_users.py tests/test_gateway.py
node --test tests/users.test.cjs tests/usage.test.cjs
```

Before handing off a change, run the full offline checks from the checkout root:

```bash
uv run pytest
node --test tests/*.test.cjs
uv run ruff check .
uv run ruff format --check .
uv run python -m compileall -q src/llameros scripts tests
for script in src/llameros/static/*.js tests/*.test.cjs; do
  node --check "$script" || exit 1
done
git diff --check
```

Python tests use pytest and FastAPI/httpx test clients; frontend tests use Node's
built-in test/assert modules and small DOM stubs. No npm install or external test
service is required. The scripts in `scripts/` perform live operations only when
explicitly invoked with the required arguments; they are not a substitute for the
offline suite. Never put a real key on the command line or disable TLS verification.

For documentation-only changes, also review that executable statements, script
ordering, CSS rules, and markup attributes are unchanged. A Python AST comparison
with docstrings removed can verify Python behavior was not accidentally rewritten;
the test suite remains necessary for import/runtime regressions.

The inference contract reference is recorded in `tests/ollama-compatibility.json`.
See [API compatibility](api-compatibility.md) for offline coverage, restrictions,
and opt-in HTTPS/model checks using `scripts/check_api_compatibility.py`.

## Manual acceptance checks

Automated mocks do not establish working host PAM, drivers, real protocol behavior,
or browser visuals. Select the checks appropriate to the change:

- **Accounts:** real second-account PAM login, role updates on subsequent requests,
  owner isolation, blocking/revocation, and persisted policy after an intentional restart.
- **Browser:** both boards, narrow/wide layouts, keyboard focus, errors, more than ten
  keys/accounts, sticky headers/scrolling, and clipboard success/denial.
- **Runtimes:** CPU and supported GPU slots, concurrent independent targets, residency,
  targeted stop, shared inventory, and explicit stopped-container storage migration.
- **Gateway:** trusted HTTPS, real loaded models, streaming terminal events, disconnect
  cleanup, expiry/revocation while queued, and optional embedding/vision checks.

Record which checks were actually run, their results, and any skips or host-specific
limitations. Do not claim a live acceptance check based on a mocked regression.
Restart/deploy services, alter containers, or commit changes only as part of an
explicitly requested workflow.

Tests use fake PAM/Docker/catalogue services; they do not use your password,
download large models, or modify your Docker daemon. A successful real PAM login
and GPU/container inference need local acceptance testing on the target host.
For multi-device acceptance, verify NVIDIA/AMD isolation, simultaneous CPU/GPU
loads, the same model on two devices, independent stopping, slot-specific target
selection, and migration of an existing container. Automated fake-runtime tests do
not establish real driver compatibility or visual browser correctness.

## Security model and limitations

- The application does not sudo, read shadow files, switch identity, or create
  privileged host helpers. Linux PAM authenticates system login accounts and checks
  account policy. PAM configuration differs by distribution; test it locally.
  Passwordless, locked, MFA-only, and expired-password accounts may not support
  this simple username/password flow. Do not weaken PAM or run as root to fix it.
- Rootless Docker is required by default. Explicitly setting
  `allow_rootful_docker = true` accepts rootful daemon risk. Docker-group membership/rootful
  socket access already carries root-equivalent authority, even if this web
  process has an ordinary UID. Rootless mode does not eliminate kernel risks.
- HTTPS is mandatory; the CLI refuses root/setuid execution. Sessions are opaque,
  in memory, expire absolutely, and disappear on restart. Cookies are Secure,
  HttpOnly, SameSite=Strict, and host-only. Dashboard mutations require matching HTTPS Origin
  and CSRF token. Login throttling allows five attempts per source/account and
  30 per source per minute, with at most four concurrent PAM verifications.
  No CORS or proxy-header trust.
- `/inference/*` uses HTTPS bearer authentication instead of browser sessions or
  CSRF; cookies alone cannot grant inference access. Key management still requires
  PAM sessions and CSRF. High-entropy keys are digest-only at rest, scoped to model
  tags/devices, optionally expiring, and revocable. No client authorization/cookies or arbitrary
  destination URLs are forwarded upstream. Failed streams release the shared job
  lock and retain content-free accounting; incomplete token counts remain unknown.
- No user-interpolated shell commands or browser-supplied Docker arguments, image names, devices,
  or hosts. The authenticated storage setting permits only a validated, dedicated
  directory mount to a fixed container destination. Only official library model
  names are accepted for lifecycle operations. Container
  ownership labels are checked. Containers have capabilities dropped,
  no-new-privileges, bounded logs/PIDs, no Docker socket mount, and no automatic
  restart. These measures are defense in depth, not a sandbox guarantee.
- Ollama listens only on container loopback, with no published ports. A private
  Unix socket relays fixed Docker exec streams; clients use the HTTPS gateway.
  The service UID, Docker-daemon administrators, and root remain trusted.
  Keep HTTPS private; public-Internet deployment remains unsupported.
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
  usage history. See the [security audit](Security_Audit.md) for known findings
  and unverified deployment controls; this is not a security certification.

## Key management contract and UI acceptance

- `POST /api/keys` requires a name. Omitted `models` means `["*"]`; the dashboard
  dropdown offers all models or one local tag. Advanced API callers may supply
  multiple model tags and hardware target restrictions.
- Admin-owned keys accept `expires_days=null` or integers 1–365. Ordinary-user
  keys require 1–30 days and explicit administrator grants. UI presets are
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
- Run the standalone HTTPS smoke test in `scripts/test_api_access.py` against a trusted
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

## VPN-protected deployment advisory

Run Llameros on a VPN-protected network: restrict the dashboard/inference listener
to the VPN interface or enforce equivalent host-firewall rules. Do not publish
it directly to the Internet. Verify access is denied outside the VPN, including
IPv6 and development port forwarding. VPN membership is not application
authorization: retain trusted HTTPS certificates, PAM, CSRF, scoped keys, explicit
grants, and rootless runtime isolation. Never disable certificate verification.
P2 point 13 (additional Host/header/HSTS hardening) is intentionally deferred
for this deployment; VPN usage does not claim to solve every browser-origin risk.

The shared-network privacy policy exposes resident model names/targets, aggregate
per-model totals and host CPU/RAM history to signed-in users. These reveal workload
activity; obtain participant agreement and use separate deployments where that
sharing is unacceptable. Keys remain owner-scoped; activity/security logs are
admin-only. Never put credentials or prompts in diagnostic reports.

P2 validation is performance-first: cheap message/tool envelope and numeric checks
once at admission; bounded raster header inspection, not full image decompression.
No weight hashing, advisory network calls, or external schema resolution occurs in
inference. CI runs malformed-input regressions and locked-dependency OSV checks;
image/host scanning remains an operator release gate (see the [security audit](Security_Audit.md)).
Model tool calls are untrusted output: clients must validate function names and
arguments against local schemas, enforce permissions and require confirmation for
side effects. Model text can contain prompt injection; Llameros never executes tools.
