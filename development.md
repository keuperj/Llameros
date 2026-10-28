# Development guide

[Overview](README.md) · [Installation & usage](install.md) · [Troubleshooting](trouble_shooting.md) · [API compatibility](api-compatibility.md) · [Security audit](Security_Audit.md)

- Keep Llameros local-first: manage and share local Ollama models through private
  HTTPS, not public-Internet hosting. Read the security sections before changing
  authentication, inference, containers, or storage; documented controls are not
  a security certification.

## Prepare a local workspace

- Use Linux, Python 3.11+, `uv`, and Node.js with its built-in test runner.
  Dependencies/tools are in `pyproject.toml`, resolved versions in `uv.lock`.
  The frontend is plain JavaScript: no npm dependencies or build step.
- Run commands from the checkout root:

```bash
uv sync --extra dev --locked
uv run llameros --help
```

- Live startup: `uv run llameros` at `https://127.0.0.1:8443`. It creates state/TLS
  files; dashboard actions can change containers/models. Use a separate test config
  and state directory, and avoid sharing live runtime ownership with experiments.
- PAM, Docker, and GPU drivers are needed for corresponding live checks, not mocked
  tests. Never run with sudo or weaken PAM/TLS to make a test pass.

## Architecture

- Backend modules under `src/llameros/`:
  - `cli.py`, `config.py`, `tls.py`: startup, validated settings, HTTPS.
  - `app.py`: dashboard routes, application lifetime, lifecycle jobs.
  - `auth.py`, `users.py`, `api_keys.py`: PAM, sessions, roles/blocks, key ownership/usage.
  - `gateway.py`, `protocol.py`, `body_limits.py`, `multimodal.py`: inference admission,
    protocol/media validation, forwarding, streaming cleanup; documents are unsupported.
  - `scheduling.py`, `limits.py`: device queues/reservations and token quotas.
  - `runtimes.py`, `docker.py`, `runtime_transport.py`: slots, containers, private relays.
  - `hardware.py`, `catalog.py`: memory estimates and official catalogue.
  - `model_storage.py`, `filesystem.py`, `storage.py`: safe storage, inventory, SQLite history.
  - `benchmark.py`, `diagnostics.py`: model benchmarking and sanitized diagnostics.
- Frontend under `src/llameros/static/`: `index.html`, `app.js`, `style.css`, plus
  focused helpers for users, actions, access, catalogue, progress, usage, limits,
  and benchmarks. Scripts share globals; preserve their load order.
- Tests: `tests/test_*.py` and `tests/*.test.cjs`. Opt-in live tools:
  `scripts/test_api_access.py`, `scripts/check_api_compatibility.py`,
  `scripts/benchmark_rag.py`.
- Lifecycle jobs use one worker. Inference has a FIFO queue per target with
  `ollama_num_parallel` active requests (default 2), 16 waiting, and a 120-second
  queue timeout. CPU/distinct GPUs execute independently; models on one target share capacity.
- Targeted lifecycle jobs reserve that device; global/ambiguous jobs reserve all.
  Queued work prevents conflicting lifecycle admission. Benchmarks require idle
  queues and reserve the gateway globally. External retrieval holds no execution permit.

### Trace a change

- **Dashboard:** page element → frontend handler → `/api/...` route in `app.py` or
  `gateway.py` → session/role/CSRF checks → backend and Node regressions.
  Hiding controls is never authorization; check real-browser layout/keyboard use.
- **Inference:** allowlisted `/inference` route → bearer/model authorization and
  bounded protocol/media validation → loaded-target selection → queue admission
  and credential recheck → quota checks → private relay → cleanup/accounting.
  Targets select routing, not key permissions.
- Every success, error, cancellation, and disconnect must close upstream resources,
  record content-free usage, and release its ticket. Never forward client credentials.
- Consult [API compatibility](api-compatibility.md) and `tests/ollama-compatibility.json`
  before changing fields or terminal stream events. Client-example SDKs are not
  automatically application dependencies.

## Make a focused change

- Inspect the working tree; preserve unrelated work and separate fixes from dependency
  upgrades, deployment changes, and broad formatting.
- Reuse `create_app` injection points for fake Docker/catalogue/authentication/hardware
  and gateway transport; use `sampling=False` for isolated tests.
- Use temporary state/SQLite databases. Migrations must preserve owners, revocations,
  role overrides, usage counters, and deleted-key tombstones.
- Cover success **and denial**: admin routes, owner isolation, queued revocation,
  blocking, quota enforcement, cancellation, and persistence as relevant.
- Preserve UID/target ownership checks, private runtime access, fixed Docker arguments,
  and transactional block/revoke behavior. Memory fit remains an estimate.
- Never persist passwords, plaintext keys, prompts/responses, or attachment bodies
  in telemetry. Clear one-time browser secrets on logout; no persistent browser storage.

## Document the change

- Explain contracts and non-obvious decisions, not each implementation line.
  Update user guides for behavior changes and this guide for contributor/architecture changes.
- Start code files with a short purpose/relationship description: Python module
  docstring, JavaScript/CommonJS `@file` JSDoc, or native HTML/CSS/SVG comments.
  Preserve shebangs/doctypes/XML declarations first; exclude binaries and comment-free JSON.
- Document Python modules, classes, production functions/methods, meaningful nested
  workers, and routes. Use an imperative summary; explain relevant units, side effects,
  exceptions, transactions, ownership, and cancellation without repeating typed signatures.
- Keep test scenario docstrings concise; explain fixture/fake contracts where needed.
- Document JavaScript helpers/controllers; use `@param`/`@returns` for useful DOM/data
  shapes and promises. Explain state changes, stale-response guards, and secret handling.
  Tiny callbacks need comments only for distinct behavior; no JSDoc renderer is required.

## Setup and checks

- Iterate with focused tests:

```bash
uv run pytest tests/test_users.py tests/test_gateway.py
node --test tests/users.test.cjs tests/usage.test.cjs
```

- Before handoff, run offline checks:

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

- Python tests use pytest/FastAPI/httpx; Node tests use built-in test/assert and DOM
  stubs. They require no real passwords, model downloads, or Docker changes.
- Documentation-only edits must not alter executable behavior, script ordering,
  styles, or markup attributes. Check examples and links; for Python docstring edits,
  an AST comparison excluding docstrings can supplement runtime tests.
- CI runs lint/format, Python/Node tests, and locked-dependency OSV checks through
  `scripts/check_security.py`. Network failure is not a pass; exit 2 denotes clean
  Python advisories with images unscanned, not full release clearance.
- Live scripts are opt-in, not substitutes for offline tests. Use trusted TLS and
  secure key input; never put real keys on the command line.

## Manual acceptance checks

- **Accounts:** real second-account PAM login, owner isolation, role changes on later
  requests, atomic blocking/revocation, and persisted policy after restart.
- **Browser:** both boards, narrow/wide layouts, focus/errors, more than ten
  keys/accounts, sticky headers/scrolling, clipboard success/denial, secret clearing.
- **Runtimes:** CPU and supported NVIDIA/AMD isolation, concurrent targets, residency,
  one model on two devices, independent stopping, target routing, shared inventory,
  and stopped-container migration.
- **Gateway:** trusted HTTPS and real models, terminal stream events, disconnect
  cleanup, queued expiry/revocation, parallel limits, quotas, and relevant vision/embedding cases.
- **Benchmarks:** idle admission, temporary gateway rejection, cleanup, persisted rankings.
- Record checks actually run, results, skips, and host limitations. Mocks do not prove
  PAM, driver compatibility, real protocols, isolation, or browser visuals. Deploy,
  restart services, alter containers, or commit only when explicitly requested.

## Security model and limitations

- **Authentication:** no sudo, identity switching, shadow access, or privileged helpers.
  PAM/NSS policy varies; locked, passwordless, MFA-only, or expired-password accounts
  may not support this flow. Password-only locks are not portable offboarding:
  block the Llameros account too.
- **Sessions:** mandatory HTTPS; CLI rejects root/setuid. Opaque in-memory sessions
  expire absolutely and vanish on restart. Cookies are host-only, Secure, HttpOnly,
  SameSite=Strict. Dashboard writes require matching HTTPS Origin and CSRF token.
  No CORS/proxy-header trust. Per minute: five login attempts per source/account,
  30 per source; at most four simultaneous PAM checks.
- **Inference:** bearer authentication, not browser sessions/CSRF. Keys are digest-only
  at rest, model-scoped, optionally expiring, and revocable. Never forward credentials,
  cookies, or arbitrary destination URLs. Interrupted usage may have unknown token counts.
- **Containers:** rootless by default; `allow_rootful_docker=true` explicitly accepts
  root-equivalent daemon access. Fixed arguments, validated dedicated mounts, official
  lifecycle model names, and UID/target labels prevent browser-controlled Docker commands.
  Drop capabilities, use no-new-privileges, bounded logs/PIDs, no socket mount or automatic
  restart. These are defense in depth, not a sandbox guarantee.
- **Runtime boundary:** Ollama binds container loopback without published ports;
  a private Unix-socket/Docker-exec relay serves the gateway. Service UID, root, and
  daemon administrators remain trusted. Direct runtime access bypasses gateway controls.
- **Memory/GPU:** fit uses `1.25 × weights + clamp(25% of weights, 256 MiB, 2 GiB)
  × parallelism × max(1, context_length / 4096)`, with 512 MiB reserved RAM.
  RAM/VRAM are not summed; unified memory is not double-counted. Recheck before
  downloads/loads, but architecture, other workloads, and drivers require live validation.
- **Catalogue/history:** HTTPS catalogue/manifest data is cached for ten minutes;
  upstream HTML changes can break discovery. Unsupported/cloud-only manifests are
  rejected; installed models can run offline. Samples exist only while Llameros runs,
  and usage covers gateway/app requests, not direct clients. Container metrics cannot
  reliably identify individual models; runtimes may survive dashboard exit.
- **Data:** private state/SQLite excludes passwords and prompt/response bodies;
  playground results temporarily exist in memory/browser. Back up state deliberately.
  Storage descendant-permission and other known limitations remain in the
  [security audit](Security_Audit.md).
- **Validation:** bounded admission checks and raster-header inspection, not full image
  decompression. No weight hashing, advisory calls, or external schema resolution in
  inference. Host/image scanning remains a release gate. Llameros never executes tools;
  clients must validate generated calls, permissions, and side effects against local schemas.

## Key management contract and UI acceptance

- `POST /api/keys` requires a name; omitted `models` defaults to `["*"]`. All active
  users can choose all models or specific tags without admin grants. Advanced callers
  may provide multiple tags. Legacy `targets` fields are accepted but do not restrict access.
- For all roles, `expires_days` is null or 1–365; UI presets: 7, 30, 365, never.
  No expiry is stored as `expires=0`; authentication checks revocation, owner status,
  and `(expires=0 OR expires>now)`. Legacy grants no longer restrict keys.
- New UI keys use `max_tokens=null`; existing/API caps still apply. Global output
  range: 1–4096; normal request default: 256, bounded by the key cap.
- Session/CSRF-protected deletion disables access but retains usage tombstones.
  Listings/history expose neither secrets nor digests. Model/user/key quotas remain
  independent of model permissions; see [usage limits](install.md#token-usage-limits).
- Preserve sticky headings, accessible delete controls, truncation/tooltips, and
  ten-row scrolling. **Add key** stays collapsed below the table; secrets use a
  readonly password field with Clipboard API and masked keyboard-copy fallback.
  No reveal/persistent storage; logout clears UI secrets, not the OS clipboard.
- Test expiry presets, copy success/denial, clear/logout, deletion/usage, and public
  certificate download beside generation (never private keys). Use
  `scripts/test_api_access.py` with trusted TLS and a loaded model for live acceptance.

### Dashboard user authorization

- `users.py` persists roles/blocks; the process account is seeded admin once, other
  eligible accounts default user. Role checks refresh each request; PAM still governs accounts.
- Admin dependencies protect lifecycle, catalogue, storage, playground, full history,
  and user management. Key routes enforce SQL owner scoping; `?board=user` also
  scopes admin personal-board operations without changing their role.
- `/api/user/dashboard` allowlists running-model fields, CPU/RAM samples, overall
  model totals, and own-key totals; exclude events, runtime addresses, others' key
  statistics, and digests. Users have no playground/lifecycle controls.
- Admin/CSRF-protected user updates accept role and blocked status. Block/revoke
  commits atomically; key creation checks blocks within a write transaction.
  Blocking removes sessions; unblocking restores neither sessions nor keys.
  Reject self-block/demotion; executing inference cannot be undone, queued work rechecks access.

## VPN-protected deployment advisory

- Restrict HTTPS to localhost/VPN or equivalent firewall rules; test denial outside
  the VPN, including IPv6 and development forwarding. VPN membership does not replace
  HTTPS, PAM, CSRF, scoped keys, or rootless isolation. Never disable TLS verification.
- Additional Host/header/HSTS hardening remains deferred (audit P2 point 13);
  VPN deployment does not eliminate browser-origin risks.
- Signed-in users see model names/targets, aggregate usage, and CPU/RAM history.
  Obtain agreement or separate deployments; keys remain owner-scoped and activity/
  security logs admin-only. Never include credentials or prompts in reports.