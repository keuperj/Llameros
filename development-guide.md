# Development guide

[Overview](README.md) · [Development reference](development.md) · [Installation](install.md) · [API compatibility](api-compatibility.md)

This guide explains how to make and validate changes to Llameros. The
[development reference](development.md) contains the detailed security model,
runtime constraints, and manual acceptance contracts. Read it before changing
authentication, inference admission, container operations, or storage.

## 1. Prepare a local workspace

Use Linux with Python 3.11 or newer, `uv`, and Node.js with support for the
built-in test runner. Python dependencies and development tools are declared in
`pyproject.toml`; `uv.lock` records their resolved versions. The dashboard uses
plain JavaScript and has no npm dependencies or frontend build step.

```bash
cd /home/keuper/Projekte/Llameros
uv sync --extra dev --locked
uv run llameros --help
```

Adjust the absolute checkout path if your workspace is elsewhere. Docker, PAM,
host GPU drivers, and an installed text-extracting Poppler `pdftotext` are needed
for the corresponding live features, not for mocked service tests. Tests that
exercise real PDF extraction skip when `pdftotext` is unavailable; inspect skips
when evaluating coverage.

Start the service only when a live check is needed:

```bash
uv run llameros
```

The normal endpoint is `https://127.0.0.1:8443`. Startup prepares application state
and TLS material. Running it is not a read-only test: later dashboard operations
can change owned containers and stored models. Use a separate local configuration
and state directory for experiments, following [installation](install.md).
Never run Llameros with sudo or weaken PAM/TLS to make a development test pass.

## 2. Find the right component

Paths below are relative to the checkout root.

| Responsibility | Main files |
|---|---|
| CLI, configuration, HTTPS certificates | `src/llameros/cli.py`, `config.py`, `tls.py` |
| Dashboard routes, application lifetime, lifecycle jobs | `src/llameros/app.py` |
| PAM, cookie sessions, persisted roles and blocking | `src/llameros/auth.py`, `users.py` |
| Bearer keys, ownership, expiry, usage | `src/llameros/api_keys.py` |
| Protocol validation, discovery, forwarding, streaming cleanup | `src/llameros/gateway.py` |
| Device queues and lifecycle reservations | `src/llameros/scheduling.py` |
| CPU/GPU slots and managed containers | `src/llameros/runtimes.py`, `docker.py` |
| Hardware estimates and official model catalogue | `src/llameros/hardware.py`, `catalog.py` |
| Model-directory safety and offline inventory | `src/llameros/model_storage.py` |
| SQLite resource, event, and generation history | `src/llameros/storage.py` |
| Inline image/PDF admission and preprocessing | `src/llameros/multimodal.py` |
| Shared page structure, controller, and styles | `src/llameros/static/index.html`, `app.js`, `style.css` |
| Board/user controls and reusable frontend helpers | `src/llameros/static/users.js`, `actions.js`, `access.js`, `catalogue.js`, `progress.js`, `usage.js` |
| Offline regressions | `tests/test_*.py`, `tests/*.test.cjs` |
| Explicitly opt-in live checks and benchmarking | `scripts/test_api_access.py`, `check_api_compatibility.py`, `benchmark_rag.py` |

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
4. Forwarding uses a managed loopback Ollama endpoint; no client credential is
   forwarded upstream.
5. Completion, upstream errors, cancellation, and early disconnect all need
   cleanup: close resources, record content-free usage, and release the ticket.

Changes must preserve these boundaries. Consult `api-compatibility.md` and
`tests/ollama-compatibility.json` before altering protocol fields or terminal
stream events. Adding an SDK to a client example does not make that SDK a runtime
dependency of Llameros.

## 3. Make a focused change

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

## 4. Document the change

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

Update user-facing guides when behavior changes and update both this guide and
the development reference when changing the architecture or contributor workflow.

## 5. Validate locally

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

## 6. Perform the relevant manual acceptance checks

Automated mocks do not establish working host PAM, drivers, real protocol behavior,
or browser visuals. Select the checks appropriate to the change:

- **Accounts:** real second-account PAM login, role updates on subsequent requests,
  owner isolation, blocking/revocation, and persisted policy after an intentional restart.
- **Browser:** both boards, narrow/wide layouts, keyboard focus, errors, more than ten
  keys/accounts, sticky headers/scrolling, and clipboard success/denial.
- **Runtimes:** CPU and supported GPU slots, concurrent independent targets, residency,
  targeted stop, shared inventory, and explicit stopped-container storage migration.
- **Gateway:** trusted HTTPS, real loaded models, streaming terminal events, disconnect
  cleanup, expiry/revocation while queued, and optional embedding/vision/PDF checks.

Record which checks were actually run, their results, and any skips or host-specific
limitations. Do not claim a live acceptance check based on a mocked regression.
Restart/deploy services, alter containers, or commit changes only as part of an
explicitly requested workflow.