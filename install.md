# Installation and usage

[Overview](README.md) · [Troubleshooting](trouble_shooting.md) · [Development](development.md) · [Security audit](Security_Audit.md)

## Requirements and installation

- Linux, Python 3.11+, `uv`, the system PAM library, and a local Docker daemon/CLI.
- Rootless Docker is required by default. GPU execution also needs compatible
  NVIDIA/AMD drivers and Docker GPU support; CPU execution does not.
- Run as a normal Linux login account, **never with sudo**. Keep Llameros on
  localhost or a VPN-protected network; public-Internet hosting is unsupported.
- From your checkout, install the locked dependencies and start:

```bash
uv sync --locked
uv run llameros
```

- Open `https://127.0.0.1:8443`. Verify and trust the certificate **before** entering
  your Linux credentials. There is no default password or separate password database.
- The service account starts as admin; other eligible Linux accounts use the user
  board, subject to PAM policy. The dashboard opens without Docker, but model
  operations require a working daemon.
- To customize settings, copy the example without overwriting an existing config:

```bash
mkdir -p "$HOME/.config/llameros"
cp -n config.example.toml "$HOME/.config/llameros/config.toml"
uv run llameros --config "$HOME/.config/llameros/config.toml"
```

- CLI overrides: `--config`, `--host`, `--port`. Without `uv`, use
  `pip install -e .` in a virtual environment, then `llameros`; `uv.lock` records
  the tested dependency set.

## Run automatically with systemd (`systemctl`)

- Use a **user service** under the same account as Llameros and rootless Docker.
  Complete installation first and stop any manually started Llameros process.
- This unit assumes `$HOME/Llameros`; replace both `%h/Llameros` paths if needed.
  `%h` is the home directory; `%t` is the user runtime directory.

```bash
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/llameros.service" <<'EOF'
[Unit]
Description=Llameros HTTPS dashboard
Wants=docker.service
After=docker.service

[Service]
Type=simple
WorkingDirectory=%h/Llameros
ExecStart=%h/Llameros/.venv/bin/llameros
Environment="PATH=%h/.local/bin:%h/bin:/usr/local/bin:/usr/bin:/bin"
Environment=DOCKER_HOST=unix://%t/docker.sock
UnsetEnvironment=DOCKER_CONTEXT
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now docker.service
systemctl --user enable --now llameros.service
systemctl --user status llameros.service --no-pager
journalctl --user -u llameros.service -n 50 --no-pager
```

- The unit uses the installed virtual environment and normal per-user config.
  Append `--config /absolute/path/to/config.toml` to `ExecStart` for another file.
- Services do not read shell startup files: adjust `PATH`, `DOCKER_HOST`, and, if
  needed, `DOCKER_CONFIG` to match your installation.
- **For boot startup and operation after logout**, enable lingering from the
  Llameros account's session and confirm `Linger=yes`:

```bash
sudo loginctl enable-linger "$(id -un)"
loginctl show-user "$(id -un)" --property=Linger
```

- From a root shell, substitute the actual Llameros account name. Lingering does
  not run Llameros as root. Check dashboard access after logout.
- After app/config changes: `systemctl --user restart llameros.service`.
  After unit edits, run `systemctl --user daemon-reload` first.
- Stop and disable startup: `systemctl --user disable --now llameros.service`
  (Docker remains enabled). For “Failed to connect to bus”, log in directly through
  SSH/desktop rather than switching accounts with sudo.

### Using an existing rootful Docker daemon

- Keep Llameros as a **user service**, but set `allow_rootful_docker = true` in its
  loaded TOML. This explicitly accepts **root-equivalent Docker access**.
- Remove the unit's `Wants=docker.service` and `After=docker.service` (user daemon
  dependencies). Set `Environment=DOCKER_HOST=unix:///var/run/docker.sock` and keep
  `UnsetEnvironment=DOCKER_CONTEXT`.
- Start the system daemon, verify socket access without sudo, then reload/restart:

```bash
sudo systemctl enable --now docker.service
env -u DOCKER_CONTEXT DOCKER_HOST=unix:///var/run/docker.sock docker info
systemctl --user daemon-reload
systemctl --user restart llameros.service
```

- Ask an administrator about access failures; never make the socket world-writable.
  Docker-group membership is root-equivalent. Restart the user manager/session
  after group changes (or reboot). Using systemd does not require model migration.

### Docker works in a terminal but not in the service

- Compare `docker context show`, `docker context inspect`, `DOCKER_HOST`, and
  `DOCKER_CONTEXT` with `systemctl --user cat llameros.service`.
- Test the standard rootless socket explicitly and inspect logs:

```bash
env -u DOCKER_CONTEXT DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock" docker info
systemctl --user status docker.service llameros.service --no-pager
journalctl --user -u llameros.service -n 80 --no-pager
```

- Start the existing user Docker service if stopped. For custom sockets, update
  the unit to the verified local `unix://` endpoint; ensure `PATH` contains Docker.
  Reload/restart after edits.
- Do not switch daemons, loosen permissions, or run as root to bypass errors:
  another daemon may contain different containers and models.

## Switch an existing installation to rootless Docker

- **This creates a separate daemon:** containers and volumes do not migrate.
  Check `docker info --format '{{json .SecurityOptions}}'` for `rootless`;
  a context name alone proves nothing, and an error does not prove rootful mode.
- Record the original context/environment, stop its managed runtimes and Llameros,
  and back up configuration and `state_dir`. Preserve `runtime-slots.json` and old
  storage; do not stop unrelated workloads.
- Preserve models before switching:
  - Host directory: back up `blobs`/`manifests`; use a private copy owned by the
    service account and verify rootless access.
  - Docker volume: re-download or export first. The default volume is
    `llameros-<uid>-models`, mounted at `/root/.ollama`; restore its `models/blobs`
    and `models/manifests` into a private host models directory. Do not copy Docker's
    internal data directory or ownership mappings.
- Follow Docker's rootless installation instructions for your distribution.
  Prerequisites include `newuidmap`/`newgidmap` and at least 65,536 non-overlapping
  subordinate UIDs/GIDs in `/etc/subuid` and `/etc/subgid`. With Docker's official
  Debian/Ubuntu repository, packages are typically `uidmap docker-ce-rootless-extras`.
- Log in directly as the Llameros account, resolve installer-reported namespace/
  AppArmor prerequisites, and run the setup tool **without sudo**:

```bash
unset DOCKER_HOST DOCKER_CONTEXT
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker.service
docker context use rootless
docker info --format '{{json .SecurityOptions}}'
```

- If intentionally retaining a running rootful daemon, use `install --force`.
  Disable the system daemon/socket only with its owner's approval and after all
  affected workloads are stopped or migrated.
- Confirm `rootless` in the output; set `allow_rootful_docker = false`, update the
  launcher/service environment, and enable lingering. Restart Llameros, select the
  preserved model directory or new volume, then start Ollama.
- Validate inventory, CPU inference, keys, GPU execution, and resource-limit support
  before removing backups. Never fix permissions with `chmod 777` or sudo.
- **Rollback:** stop replacement runtimes/server, restore the original daemon,
  environment, and storage, and explicitly accept rootful access if needed.
  New-store changes are not synchronized back.
- Docker references: [installation](https://docs.docker.com/engine/security/rootless/),
  [user services](https://docs.docker.com/engine/security/rootless/tips/),
  [troubleshooting](https://docs.docker.com/engine/security/rootless/troubleshoot/),
  [volume backups](https://docs.docker.com/engine/storage/volumes/#back-up-restore-or-migrate-data-volumes).

## First model and daily workflow

- **Start Ollama:** first startup downloads the pinned runtime image; only public
  images are supported. Progress/errors appear in the dashboard.
- Search the catalogue, select a tag, and download. **CPU-only** filters estimated
  RAM fits; it does not select the execution device.
- Under **Start model on available hardware**, choose CPU or a supported GPU and
  load the model. Residency depends on memory and `max_loaded_models` (default 2).
  CPU utilization does not block loading; Intel/unidentified GPUs are not selectable.
- Use **Try a model** for text generation or **Access model** for API examples.
  GPU slots have separate containers but share downloaded weights.
- **Stop model** unloads one model; **Stop Ollama** stops all managed runtimes.
  Neither deletes downloaded weights.

## Secure inference API

- In **Key Management → Add key**, create an **All models** or model-specific key
  and save the one-time secret. All active users can do this without admin grants.
  Expiry is optional; keys work on every CPU/GPU target. Legacy grants/hardware scopes
  no longer restrict keys; blocking, revocation, expiry, and quotas still apply.
- Download the public server certificate beside key generation and verify it
  independently. Keep TLS verification enabled; never use Linux passwords as API
  credentials or expose keys in command lines/support reports.
- Every inference endpoint requires `Authorization: Bearer <key>`:
  - Ollama base: `https://127.0.0.1:8443/inference`
  - OpenAI-compatible base: `https://127.0.0.1:8443/inference/v1`
  - For remote access, use the HTTPS address under **Access model**.
- Models must already be loaded. For multiple placements, select one with
  `X-Llameros-Target` or JSON `target` (takes precedence); ambiguity returns HTTP 409.
- Text-only bodies: 128 KiB maximum; inline-image bodies: 16 MiB default.
  Output: 1–4096 tokens (normally 256, subject to a key's output cap).
  Documents/PDFs are unsupported: extract plain text yourself.
- Revoking/deleting keys blocks new work, not executing requests. Usage stores
  content-free totals, not prompts/responses. Validate generated tool calls before
  allowing side effects.
- See the [Python example](README.md#access-a-model-from-python), dashboard client
  snippets, and [API compatibility](api-compatibility.md) for streaming/media details.

### Simultaneous users and request queues

- Each target shares a FIFO queue across all users/keys/models:
  `ollama_num_parallel` active requests (default 2), 16 waiting, 120-second maximum
  queue wait. Different targets execute independently.
- Full queue: HTTP 429 with `Retry-After: 1`; wait timeout: HTTP 504. Allow timeouts
  for queueing **plus** generation and respect retry delays. Streams hold their
  slot until completion/cleanup and may produce no tokens while waiting.
- There is no least-busy routing or per-user fairness. Credentials are rechecked
  before execution. Direct Ollama access bypasses queues, authorization, and quotas.

### Configure parallel inference and memory headroom

- Set `ollama_num_parallel` in TOML (1–16; 1 for serial execution). It controls
  `OLLAMA_NUM_PARALLEL` and the gateway's device-wide request ceiling;
  `max_loaded_models` controls residency instead.
- Higher parallelism needs more memory and may reduce individual request speed.
  Fit estimates account for weights, context, and parallelism, reserve 512 MiB RAM,
  and evaluate VRAM separately (system RAM for unified memory). They are not
  guarantees against out-of-memory failures.
- To change runtime settings: let requests finish, stop runtimes, edit TOML,
  restart Llameros, and reapply the same **Model storage** selection while stopped.
  Start/load models again. Weights remain; restarting Llameros alone cannot update
  container environments, and mismatched parallelism is refused.

### Benchmark a running model

- Admins select **Benchmark** beside **Stop model**. Queues must be idle; confirm
  the interruption. During a run, the entire gateway returns HTTP 503 with
  `Retry-After: 5`, and model-management operations are reserved.
- Six sequential chat/embedding cases measure single-request speed. Afterwards,
  the same six workloads rotate in an adaptive stress test, starting at 1 request
  per minute. Initial success selects scale-up: double clean rates and stop at
  the first failure. Initial failure selects scale-down: halve failed rates and
  stop at the first success. Each
  stage offers at least six requests over at least one minute, then drains pending
  requests before changing rate. Queue overflows, response/queue timeouts, and
  runtime errors fail the stage without creating activity errors or usage entries.
  The configured queue timeout also bounds each complete stress request, including
  queueing. The test uses an isolated instance of the production FIFO scheduler
  with the current queue settings and parallelism, and one synthetic account.
- There is no overall benchmark time limit. Select **Stop benchmark** in Ollama
  status to cancel in-flight benchmark HTTP work and release the reservation.
  Service shutdown also requests cancellation. Speed results remain ranked when
  all six speed cases completed; stopping earlier leaves an unranked partial run.
  The gateway remains unavailable until automatic completion or manual stop.
- Results show the highest clean tested rate and the latest 200 completed load
  stages, not a guarantee of permanent capacity. Warm-up, context, parallelism,
  workload mix, and direct runtime traffic affect results. The load rate is offered
  requests per minute, not token throughput or a production fairness test.
- **Benchmarks → Model performance rankings** shows persisted model/device results,
  separate chat/embedding rankings, and per-case metrics on both boards. Compare
  matching settings/timing methods; failed runs retain metrics but are unranked.
- Each run replaces the previous result, even on failure. Results are separate
  from usage/quotas and telemetry retention; prompts/responses/vectors are not saved.

### Admin activity and error monitoring

The independent **Activity and errors** panel at the bottom of the admin board
has three keyboard-accessible tabs with scrollable tables:

- **Overview:** successful/failed API requests and admin lifecycle activity totals
  over retained history. Admin totals also include started events. A warning stays
  visible until an administrator selects **Acknowledge failures**. Acknowledgement
  is shared across admins and persists across restarts; new failures trigger it again.
- **User activity log:** timestamp, user, key name/ID (never its secret), model,
  target, status, input/output tokens and output tokens/s over full request time,
  including queueing. Failed requests are red and can be isolated with
  **Show only errors**. Early authentication/validation failures may have unknown
  fields; incomplete usage is shown as unknown, not zero. Direct Ollama traffic
  is not logged by Llameros.
- **Admin activity log:** the existing lifecycle/action log, moved out of Usage
  history, with red failure rows and an independent **Show only errors** toggle.

Tables show the newest 1,000 retained entries; overview counts cover all retained
entries. Normal history age/row limits apply. The unacknowledged-failure counter
survives history cleanup, so old failures still require explicit acknowledgement.
Prompts, generated responses and bearer secrets are not included in these logs.
Only administrators can read this panel or acknowledge failures.

## Configuration

### Application settings

- Edit `$HOME/.config/llameros/config.toml`; see [all settings](config.example.toml).
  Restart after changes. Leading `~` expands to the service user's home.
- Main defaults:
  - Listener: `host = "127.0.0.1"`, `port = 8443`.
  - Private state/TLS/history: `state_dir = "~/.local/share/llameros"`.
  - PAM/session: `pam_service = "login"`, `session_seconds = 3600` (absolute lifetime).
  - Telemetry: `sample_seconds = 15`, `retention_days = 30`.
  - Models: `context_length = 4096`, `max_loaded_models = 2`, `ollama_num_parallel = 2`.
  - `gpu = "cpu"` affects catalogue estimates, not execution placement.
  - `allow_rootful_docker = false`; runtime images require SHA-256 digest pins.
- Runtime APIs use private relays, not published ports; `ollama_port` is a legacy
  registry identifier. Use one instance per Linux UID/Docker daemon; never delete
  the runtime registry while containers exist.

### TLS certificates and client trust

- Leave `cert_file`/`key_file` empty to generate `cert.pem`/`key.pem` in `state_dir`,
  or supply **both** PEM paths. Keep the private key account-only (`chmod 600`);
  certificate names/IPs must match client addresses.
- `certificate_download_enabled = true` offers **Download server certificate (PEM)**
  beside key generation on both boards. Authenticated `GET /api/certificate` exports
  public certificates only; disabled downloads return 404, invalid/missing files 503.
- **Downloading is not verification:** establish browser trust before login and
  compare the certificate/fingerprint through an independently authenticated channel.
  Use the verified file as `LLAMEROS_CA_CERT`; never distribute private keys.
- For shared deployments, prefer an organization/public CA with trusted roots on
  clients. A downloaded server chain is not automatically a trusted CA bundle.
- Restart after replacement/renewal so downloads match the served certificate.
  Renewed self-signed certificates must be verified and distributed again.

### Model storage in the GUI

- In **Configuration → Model storage**, select a dedicated models directory with
  `blobs`/`manifests`, **not** its parent `.ollama`; leave empty for the managed
  Docker volume. The preference persists separately from TOML.
- Existing roots must belong to the service account and not be group/other-writable;
  symlinks are rejected. Keep the entire tree private; descendant checks have a
  known limitation in the [security audit](Security_Audit.md).
- Stop runtimes, enter the path, confirm, and **Save storage location**. Stopped
  managed containers are removed and recreated on restart. **Data is not copied or
  deleted automatically**: back up and copy `blobs`/`manifests` while stopped,
  preserving permissions. Llameros does not change ownership.
- Running inventory comes from Ollama; stopped directory scans check sizes, not
  hashes. Stopped volume inventory is cached; start Ollama to refresh.

## Multi-user dashboard access

- Admins manage roles, blocking, and allowances in **Configuration → User Access**.
  Users manage their own keys and see shared models, usage totals, and CPU/RAM
  history, but cannot manage runtimes or other owners' keys.
- Admins can use their personal user board without changing roles. Roles/blocks
  persist; self-blocking/demotion is refused. Blocking invalidates sessions and
  revokes keys; unblocking restores neither.
- **Test a real second-account login before sharing.** PAM/NSS may prevent
  unprivileged cross-account authentication; login-disabled accounts cannot sign in.
  Do not weaken PAM, install privileged workarounds, or run as root.

## Token usage limits

- **Configuration → User Access → Default user limits** gives each inheriting user
  a separate allowance, including admins/future accounts. Input/output default to
  **No limit**. Explicit overrides, including unlimited, take priority; **Use defaults**
  removes an override without resetting consumption.
- Admins set user-wide limits in a user's **Config** and per-user/model limits beside
  downloaded models. Both boards offer **Config** for keys; users can edit only
  their own key limits. Model allowances are per user, not a shared pool.
- Input/output limits are independent, in thousands of tokens (1 = 1,000).
  Resets: day (00:00 UTC), week (Monday), month (first day), or **Never**.
- All applicable limits are checked before dispatch, including user/model limits
  for the admin playground. Actual usage is charged afterward; in-flight requests
  can overshoot. Subsequent requests get HTTP 429, with `Retry-After` for calendar
  quotas. Requests crossing a reset belong to their admission window.
- Counters survive restarts, history cleanup, key deletion, and policy edits.
  Older history is not imported. Migrating global model quotas preserves policies
  and user/key counters but starts fresh per-user/model counters.
- Missing upstream counts mark that dimension unknown and block finite allowances
  for the window. Calendar resets clear this; lifetime policies require an admin
  policy change. Unlimited dimensions remain unlimited. Abrupt crashes can lose
  accounting for unfinished requests.
- Use `/inference` to enforce accounting; direct runtime access bypasses it.

## Upgrading existing installations

- Back up configuration/state/models and stop old managed runtimes on their original
  daemon. Code updates alone do not close legacy ports.
- Reapply storage while stopped to recreate private runtime access, retaining weights.
  Daemon changes require [rootless migration](#switch-an-existing-installation-to-rootless-docker).
- Use digest pins from `config.example.toml`, remove obsolete `pdf_max_*` settings,
  and recreate containers after image/runtime-setting changes. All active users can
  use wildcard/non-expiring keys; no grant migration is needed.
- Move clients to the HTTPS bearer gateway and test unauthorized access from another
  unprivileged account/container before sharing. For refused containers/slots, see
  [Troubleshooting](trouble_shooting.md#cpu-shows-unavailable-despite-low-utilization).

## Ollama image versions

- Images run by digest; local `llameros/ollama:sha256-<full-digest>` aliases do not
  overwrite upstream tags. Conflicting aliases are rejected.
- Eligible older stopped containers are archived as `<container-name>-old-<unique-suffix>`
  after the new image downloads; failed downloads leave them untouched. Stop running
  old containers first. Ownership checks protect unrelated containers.
- Images, volumes, and weights remain. Replacements use current storage; archives
  retain old mounts and require manual management. **Never run old and replacement
  containers against shared model storage simultaneously.**

## Security before sharing

- Bind to localhost or a VPN interface and restrict the HTTPS port in your firewall,
  including IPv6. With UFW, inspect `sudo ufw status`; do not open access broadly.
- VPN membership is not authorization: retain trusted HTTPS, PAM, scoped keys, and
  rootless isolation. The service UID, root, and daemon administrators remain trusted.
- Users see resident model names, aggregate usage, and host history: obtain agreement
  or use separate deployments. Never put credentials/prompts in support reports.
- Review the [security audit](Security_Audit.md) and
  [security model](development.md#security-model-and-limitations), including outstanding
  host/image scanning and live isolation checks, before changing exposure/permissions.