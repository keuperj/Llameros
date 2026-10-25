# Installation and usage

[Overview](README.md) · [Installation & usage](install.md) · [Troubleshooting](trouble_shooting.md) · [Development](development.md)

Manage and securely share your local Ollama models with the HTTPS inference gateway
and dashboard-managed bearer keys. Sharing remains within the documented local-host
security boundary; it does not make public or multi-user hosting supported.

## Requirements and installation

Requires Linux, Python 3.11+, Docker CLI, an accessible **local** Docker daemon,
and the system PAM library. GPU support additionally requires compatible drivers
and Docker GPU configuration. Start Llameros as your normal Linux user, never sudo.

Run the following commands from your local project checkout:

```bash
# Run from the repository root.
uv sync --locked
uv run llameros
```

Open `https://127.0.0.1:8443`. A self-signed certificate is generated automatically;
your browser will warn because it is not signed by a trusted CA. Verify/trust your
own certificate before entering your Linux password. Sign in with the **same Linux
username and password as the user running Llameros**.
There is no default password, separate user database, or authentication bypass.

The dashboard works without Docker; Docker errors appear in the runtime panel.
Prefer configuring rootless Docker. The current application default allows
rootful Docker (`allow_rootful_docker = true`), which carries root-equivalent
access risk. The example configuration explicitly sets this to `false` to require
rootless Docker; change it to `true` only if you deliberately accept that risk.
Llameros does not change your Docker setup.

```bash
mkdir -p "$HOME/.config/llameros"
cp config.example.toml "$HOME/.config/llameros/config.toml"
uv run llameros --config "$HOME/.config/llameros/config.toml"
# Command-line overrides:
uv run llameros --host 127.0.0.1 --port 9443
```

Without uv, use a Python virtual environment and `pip install -e .`, then
run `llameros`. The committed `uv.lock` pins the tested dependency set.

## First model and daily workflow

The indicator beside **Start Ollama** distinguishes an existing local container,
a cached Docker image, an image that needs downloading, and unknown availability
(for example, an inaccessible daemon). Existing containers can start even if the
configured image tag is no longer cached. No image is pulled just to check status.

Every lifecycle/inference job shows a progress bar, stage, elapsed time, and
completion/failure status. Docker image pulls show real per-layer download and
extraction counters via the local daemon API; Ollama model pulls show blob byte
progress. Percentages apply to the current layer/stage, **not** the entire startup
operation. Loading, stopping, storage changes, readiness checks, and inference
use indeterminate bars because those operations do not expose a reliable percent.
Job progress refreshes independently every second; runtime availability refreshes
with the main dashboard. Conflicting lifecycle buttons are disabled while busy.
Docker pulls use anonymous public-image access; private authenticated registries
are not supported by this streaming download path.

1. Inspect CPU cores/utilization, available RAM, and detected GPU VRAM. NVIDIA
   uses `nvidia-smi`; AMD/Intel inspection falls back to Linux DRM sysfs where
   available. Missing telemetry is shown as unknown rather than invented.
2. Start Ollama using **Start Ollama**. First launch downloads the configured
   Docker image and may take several minutes. Operations run as background jobs
   and the panel reports progress/errors.
3. Browse the live official Ollama library, choose a family and tag, and review
   its download size and estimated memory requirement. Download is enabled for
   variants estimated to fit available system RAM. The catalogue lists the
   families exposed by the library page; it is not an exhaustive registry API.
   Compatible families and variants have a green bullet and sort first. A family
   qualifies when at least one variant fits. Unknown entries are marked separately.
   Checks run progressively with three concurrent requests, starting with the
   most popular families. The **Recommended for this host** list shows up to six
   fitting families in Ollama's live popularity order, preferring a fitting `latest`
   variant when available. It includes specialized models such as embeddings;
   popularity is not a quality score or a guarantee of playground compatibility.
   **Search / recheck** searches Ollama, rather than filtering only the families
   already loaded. Enter a family, description, or `family:tag`; tag queries
   search for the family upstream and filter its variants locally. Check
   **CPU-only** to show only variants estimated to fit available system RAM
   without relying on GPU memory. The same filter applies to recommendations.
   Unknown/oversized variants are hidden while checked; clear it to inspect them.
   This is a discovery filter, not a change to the configured execution mode:
   select **CPU** when loading to run without GPU devices.
   Small local models are not a separate CPU-only model category. Changing the
   checkbox or submitting search refreshes memory estimates.
   Catalogue badges use rounded download sizes with rounding headroom and the
   existing memory heuristic. Selecting a tag checks its exact registry size
   before enabling download; execution checks memory again. No check guarantees
   performance, GPU driver support, or sufficient memory at a different context.
4. Download a model, select a device with capacity under **Start model on available hardware**,
   and load/start it. Another model can stay loaded on each other supported slot;
   the same model can run on multiple devices. GPU containers start on demand.
   **■ Stop model** unloads only the selected model; **Stop Ollama** stops all
   registered runtime containers. Downloaded
   weights persist in the `llameros-<uid>-models` Docker volume by default, or in
   the host directory selected in the **Model storage** panel. The **Downloaded
   models** panel shows the models available in the selected location.
5. Expand **Try a model**, select a loaded model and its hardware, and generate
   to record token counts and timing. Generation runs asynchronously, returning
   up to 256 tokens from the UI (API limit 4096). Expand **Access model** for the
   selected runtime's API URLs and copyable Python/framework examples.
6. Inspect sampled host CPU/RAM history, container CPU/memory/network usage,
   loaded-model snapshots, lifecycle events, and per-model inference totals.

## Dashboard and runtime details

### Dashboard controls

The runtime banner distinguishes **Ollama off**, **Ollama running** (no model
loaded), and **Model running — model name**. Unknown/error states are reported
explicitly rather than displayed as off. “Running model” means loaded in memory,
not necessarily generating tokens right now.

**Try a model** is collapsed by default. The downloaded-models card has a bounded
height and scrollable inventory. Dashboard controls and backend load jobs permit
one loaded model per CPU/GPU slot; unload a slot's model before reusing it. This does not intercept
independent requests sent directly to the local Ollama API. Lifecycle events show
up to ten single-line entries at once, with vertical scrolling for older events
and horizontal scrolling for long details. Refresh preserves the event scroll
position.

### Per-device runtimes

Select a free device above the downloaded-models list before pressing Load/start.
There is one CPU slot (Ollama chooses its thread count), plus one slot per safely
identifiable NVIDIA GPU UUID or AMD render device. Intel/unidentified GPUs are shown but cannot
be selected. Hardware cards show assigned models, free VRAM and utilization when
available. “Free” means unassigned by Llameros, not exclusive of other programs.
CPU utilization does not restrict loading: one CPU model can run alongside other
CPU workloads, provided it fits available RAM. CPU cores are not reserved.
Each running-model row includes a **■ Stop model** button on its right; it unloads
that model without stopping other models or the Ollama services.

Each GPU gets its own managed container, device restriction, and private relay.
CPU uses `ollama_port`; GPU ports are allocated above it, skipping the dashboard
port, and persisted in `state_dir/runtime-slots.json`. Port collisions with other
programs fail visibly rather than changing endpoints. Model files are shared;
startup pruning is disabled. Access examples and playground selections include
hardware and route to the selected runtime, even for the same model on two GPUs.
The legacy `gpu` configuration controls catalogue estimates, not runtime placement.
AMD uses the ROCm image when the configured image is the default Ollama image;
custom images must support the selected device. Compatible host drivers and
container GPU support remain prerequisites.

Loads require available system RAM and, for GPU slots, estimated VRAM fit. A GPU
load reporting no VRAM use is unloaded and reported as an error, not labelled GPU
execution. GPU models may still use CPU resources; this is device assignment,
not exclusive CPU affinity. Dashboard lifecycle/inference jobs remain globally
serialized, but models on different slots can stay loaded simultaneously and
their direct API endpoints can be used independently.

**Existing-container migration:** older containers lack the new hardware-assignment
label and will be refused for model operations. Follow the [container migration steps](trouble_shooting.md#cpu-shows-unavailable-despite-low-utilization). This recreates managed containers only; model files
are never moved or deleted.
Stop Ollama stops all registered runtimes. Stopping a model alone leaves its slot
service ready for reuse. Do not delete the runtime registry while containers exist.

The **Ollama runtime → Access model** dropdown shows the authenticated Llameros
HTTPS gateway, not the direct Ollama port. Select a loaded model/device and choose
Ollama Python, OpenAI-compatible Python, LangChain, or LlamaIndex for copyable
examples. Install the chosen client in your own environment. See [Secure inference API](#secure-inference-api).

Container start/stop and model load/unload controls are enabled only for the
opposite known state, and remain disabled during background jobs. The backend
also rejects redundant transitions. The inference playground lists only active
(loaded) models; load a downloaded model before generating a response.
Loading requests indefinite residency; playground generation sets a five-minute
keep-alive, so a model can subsequently expire from memory without a manual stop.

The button-shaped container availability indicator refreshes status when clicked.
Model info opens a closable panel over the downloaded-models card, showing the
selected family or tag's public Ollama README as plain text. External scripts,
HTML, images, and editor forms are not embedded. An **Open on Ollama** link is
available for the original formatting. Description fetching requires internet
access; fetching failures are shown inside the panel.

## Secure inference API

1. Sign in with PAM and open **Key Management** before Configuration. Existing keys
   appear in a width-constrained table with a scrollbar after ten entries. Delete
   any key using its right-hand trash icon; content-free usage history is retained.
2. Expand **Add key** below the table. Enter a name and select **All models**
   (default) or a downloaded local model from the dropdown. Choose **1 week**,
   **1 month**, **1 year**, or **Never** (default) for expiry. Finite choices mean
   7, 30, and 365 days for admins. Ordinary users require explicit grants and
   1–30-day keys within the grant lifetime; never-expiring user keys are refused.
   Exact model tags are used; untagged API permissions mean `:latest`, not all tags.
   The dashboard creates keys for all hardware targets. Advanced management API
   clients can still restrict targets to `cpu` or registered `gpu-…` slot IDs.
3. Generate the key and use **Copy key** next to the masked field; the credential
   is never displayed in plain text. Store it securely and use **Clear key**.
   Only its SHA-256 digest is stored; clearing, logout, or navigation loses the
   one-time copy opportunity. If clipboard permission is denied, the masked field
   can be selected for keyboard copying. No per-key token cap is added by the form;
   server-wide request limits (1–4096 output tokens, default 256) remain enforced.
4. Set `LLAMEROS_API_KEY` and `LLAMEROS_CA_CERT` in the client environment.
   The latter points to the trusted PEM certificate/CA for your Llameros server
   (the default generated certificate is `state_dir/cert.pem`). Verify its
   fingerprint before trusting it. Never disable certificate verification.
5. Expand **Access model** in a running model's own box and use its client example. Keys grant inference only, not dashboard,
   container, storage, download, load, or unload access. Never send PAM passwords
   or dashboard cookies to inference clients.

Default base URLs are `https://127.0.0.1:8443/inference` (Ollama) and
`https://127.0.0.1:8443/inference/v1` (OpenAI-compatible).
The gateway requires `Authorization: Bearer <key>` on every endpoint:

| Method | Path under `/inference` | Purpose |
|---|---|---|
| POST | `/api/generate` | Ollama text generation, JSON or NDJSON stream |
| POST | `/api/chat` | Ollama chat, JSON or NDJSON stream |
| POST | `/v1/chat/completions` | OpenAI-compatible chat, JSON or SSE stream |
| GET | `/api/tags` | Permitted **loaded** models and device targets |
| GET | `/v1/models` | OpenAI-style list of permitted loaded models |

Set `X-Llameros-Target: cpu` or a GPU slot ID to choose a device; the JSON
extension `target` also works and takes precedence. Without a target, exactly
one permitted instance of the model must be loaded, otherwise the request gets
409. Models must be loaded through the dashboard first; the gateway never
intentionally loads a different model. All gateway generation and dashboard
jobs share per-device reservations. Each device executes one request at a time,
with up to 16 queued requests and a 120-second wait timeout. Independent devices
can execute concurrently; conflicting lifecycle operations return 409.

Ollama requests default to streaming; OpenAI requests default to non-streaming.
Use `stream: true` for streaming and OpenAI `stream_options.include_usage: true`
for a usage chunk. OpenAI compatibility covers chat completions, completions,
stateless Responses, and text embeddings; Anthropic Messages is also exposed.
This is not the full OpenAI API: audio and stateful Responses are excluded.
Unsupported fields return 400. See [API compatibility](api-compatibility.md).
Limits: 128 KiB text-only JSON request; bounded inline image requests default
to 16 MiB. Output is 1–4096 tokens (default 256), configured
context length only, and allowlisted sampling options. Client residency hints
are ignored; Ollama-style gateway generation keeps the loaded model resident.
These are guardrails, not hard CPU/RAM quotas or a public-service rate limiter.

The key list shows requests reaching inference routing, failures, known input/output
tokens, elapsed seconds, and requests with unknown usage. Rejected authentication,
validation, permission, or busy-lock requests are not counted. Interrupted streams
can lack final token counts; missing counts are **unknown**, not measured zero.
Refresh the key list to update usage. Accounting stores no prompts/responses;
usage is retained for at most 10,000 rows and `retention_days`; inactive key
tombstones without retained usage are eventually removed. Revoke keys in the dashboard;
revocation/expiration rejects new requests but does not terminate an active one.

**Security boundary:** HTTPS remains loopback by default. Ollama binds only to
container loopback, with no published host ports. Llameros uses private Unix
sockets (0700 parent directories, 0600 sockets) and fixed Docker exec relays.
Runtime images must provide Bash with TCP redirections and `cat`; no extra image
or host dependency is installed. The service UID, root, and Docker-daemon
administrators remain trusted. Public-Internet deployment remains unsupported.

### P0 migration for existing installations

1. Stop all existing managed runtimes. Old published ports remain exposed until
   those containers are stopped; upgrading code alone cannot close them.
2. Reapply the current model-storage selection while stopped, then start runtimes
   to recreate containers without published ports. Model files/volumes are retained.
   Unsafe legacy containers are refused for inference/start, not silently reused.
3. Use a dedicated rootless Docker context. Existing rootful containers/volumes do
   not automatically migrate between daemons. Stop old containers on their original
   daemon and arrange model storage before switching. If deliberately retaining a
   rootful daemon, explicitly set `allow_rootful_docker = true` in local TOML.
4. Remove obsolete `pdf_max_*` settings. PDF extraction and inline document blocks
   are no longer supported; supply plain text. Poppler is no longer required.
5. Use the HTTPS `/inference` gateway and bearer keys for every client. Validate
   access denial from a separate unprivileged account/container before shared use.

Dashboard bodies are limited to 128 KiB of actual received bytes, including
chunked input, with a 30-second body-read deadline and a 64-level JSON-depth limit.
Inference retains its configurable image-body limit. These per-request controls
are not aggregate admission/resource budgets; the P1 resource findings remain open.

## Configuration

### Model storage in the GUI

Expand the collapsed **Configuration** drop-down to find **Model storage**.
The panel lets you select a dedicated host directory, including
paths beginning with `~`. Leave the path empty to use the original Docker-managed
volume. New directories are created with private permissions; existing directories
must be owned by the current user, not group/other-writable, and contain only
Ollama's `blobs` and `manifests` directories (or be empty). Symlinks are rejected.
Choose the **models directory itself**, not its parent `.ollama` directory.

Stop Ollama, enter the location, check the confirmation, and select **Save storage
location**. The operation shares the lifecycle job lock with downloads/inference.
It removes the stopped managed CPU and registered GPU containers; the CPU runtime
is recreated on start and GPU runtimes on their next load, sharing the selected
storage. Model data is **never automatically copied or deleted**.
Switch back to the previous location to see its models again. To relocate models,
copy `blobs` and `manifests` yourself while Ollama is stopped, preserving access
permissions. Back up important data before doing so.

The preference is saved privately in `model-storage.json` inside the configured
application state directory and survives restarts; it does not rewrite TOML.
The selected directory is mounted read/write at a fixed container model directory,
with `OLLAMA_MODELS` pointing there. Only select model data you intend the container to access. Docker
and Ollama must have suitable filesystem permissions; rootless setups may require
additional permission configuration. Llameros does not sudo or change ownership.

The **Downloaded models** panel displays names, sizes, modification dates (when
available), and loaded/downloaded status. While Ollama runs, its API is authoritative.
While stopped, host directories are scanned for manifests and complete referenced
blob sizes (checksums are not recomputed). Docker volumes show a timestamped,
last-known inventory, not a claim of current contents. Start Ollama to populate or
refresh that inventory. Only models in the selected store are listed; Llameros
does not search unrelated directories or other Docker volumes. Existing custom
models can be listed, but lifecycle controls still accept official library names only.

### Application settings

The default config is `~/.config/llameros/config.toml`, beneath the current user's
home directory. See `config.example.toml` in your checkout for
all settings. Home-directory paths beginning with `~` in the configuration are
expanded for the user running Llameros.

| Setting | Default | Purpose |
|---|---|---|
| `host`, `port` | `127.0.0.1`, `8443` | HTTPS binding; ports must be unprivileged |
| `state_dir` | Current user's `.local/share/llameros` | TLS files, SQLite history, storage preference, inventory cache, and runtime registry |
| `cert_file`, `key_file` | Empty | Optional existing PEM certificate and private key |
| `pam_service` | `login` | Existing Linux PAM service |
| `session_seconds` | `3600` | Absolute session lifetime |
| `sample_seconds` | `15` | Resource collection interval |
| `retention_days` | `30` | History retention, pruned during sampling |
| `ollama_port` | `11435` | Legacy slot identifier retained for registry compatibility; no host port is opened |
| `ollama_image` | SHA-256 pin in example config | Standard runtime; digest required |
| `ollama_rocm_image` | SHA-256 pin in example config | AMD runtime; digest required |
| `gpu` | `cpu` | Catalogue estimate preference: `cpu`, `nvidia`, or `amd`; execution is selected per model |
| `context_length` | `4096` | Runtime context length |
| `allow_rootful_docker` | `false` | Rootless required unless explicitly set true to accept root-equivalent daemon access |

Configuration changes to existing containers do not recreate them. Stop all
runtimes and confirm/reapply model storage to recreate them for image changes.
Keep the model volume to preserve downloads. Runtime ports are persisted in
`runtime-slots.json`; changing `ollama_port` after creating slots requires a
deliberate stopped-container/registry migration, not merely editing TOML.
Only one Llameros instance per Linux UID and Docker daemon is supported.

## Security before use

Keep HTTPS private and runtime APIs unpublished. Never send Linux credentials to
Ollama. Read the [security model and limitations](development.md#security-model-and-limitations)
before changing network bindings or Docker permissions. For errors and legacy
container migration, see [Troubleshooting](trouble_shooting.md).

## Multi-user dashboard access

Sign in with a system login account and its PAM password. The Linux user starting
Llameros is initially an **admin**; other login-shell accounts are **users** by default.
Service accounts with `nologin` or `false` shells cannot sign in. Account enumeration
uses the system password database (including NSS where configured), not the system shadow password file.

Users can view running models and host CPU/RAM history, see overall model usage and usage for their own keys,
and create/delete their own keys. They cannot download/start/stop models, change
configuration, use the admin playground, or see other users' keys or activity logs.
Existing keys migrate automatically to the process account. Admins can optionally
set a Linux key owner when generating a key; users cannot assign keys to others.

Admins manage **Configuration → User access** to promote, demote, block, or unblock
accounts. Blocking revokes all owned keys and invalidates existing sessions. These
credentials stay revoked after unblocking. Self-blocking/self-demotion is refused.
The header's board switch lets admins preview/use their own user dashboard and return
to the admin board. Roles and blocks survive application restarts.

**PAM deployment limitation:** cross-account password verification from an ordinary
Linux process may be disallowed by your distribution's PAM/shadow policy, even with
the correct password. This feature does not install a privileged authentication
helper, read shadow hashes, or modify PAM. Test with a second real account on the
host. If policy refuses it, multi-user password login requires a separately reviewed
host authentication solution; do not run Llameros as root or weaken PAM as a workaround.


## P1 credential and provenance migration

See `security-operations.md` for new login limits, offboarding integration,
scoped user grants, key expiry rules, bounded retention, security events and
release scanning. Existing ordinary-user wildcard/non-expiring keys must be
replaced. Replace mutable image tags in local TOML with the example digest pins.
Point 5 resource budgets and user quotas remain deferred.

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
image/host scanning remains an operator release gate (see security-operations.md).
Model tool calls are untrusted output: clients must validate function names and
arguments against local schemas, enforce permissions and require confirmation for
side effects. Model text can contain prompt injection; Llameros never executes tools.
