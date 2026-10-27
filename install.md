# Installation and usage

[Overview](README.md) · [Troubleshooting](trouble_shooting.md) · [Development](development.md) · [Security audit](Security_Audit.md)

Run local Ollama models and share them through authenticated HTTPS. Keep Llameros
on localhost or a VPN-protected network; public-Internet hosting is unsupported.

## Requirements and installation

- Linux, Python 3.11+, `uv`, the system PAM library, and a local Docker daemon/CLI.
- Rootless Docker (required by default). GPU execution also needs compatible host
  drivers and Docker GPU support; CPU execution does not.
- A normal Linux login account. **Never start Llameros with sudo.**

From your project checkout:

```bash
uv sync --locked
uv run llameros
```

Open `https://127.0.0.1:8443`. Verify and trust the generated self-signed certificate
before entering your Linux username/password. There is no default password or
separate password database. The service account starts as admin; other eligible
system accounts use the user board, subject to host PAM policy.

To customize settings, copy the example once (do not overwrite an existing config):

```bash
mkdir -p "$HOME/.config/llameros"
cp -n config.example.toml "$HOME/.config/llameros/config.toml"
uv run llameros --config "$HOME/.config/llameros/config.toml"
```

Command-line overrides include `--host` and `--port`. Without `uv`, install with
`pip install -e .` inside a virtual environment and run `llameros`; the committed
`uv.lock` records the tested dependency set. The dashboard can open without Docker,
but model operations require a working daemon.

## Run automatically with systemd (`systemctl`)

Use a **user service**, not a root service. Log in as the same Linux account used
for installation and rootless Docker. First complete `uv sync --locked`, configure
Llameros as above, and stop any manually started Llameros process to free its port.
This example assumes the checkout is at `$HOME/Llameros`; replace both
`%h/Llameros` paths below with your actual absolute checkout path if different.

Create the unit:

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
```

The service uses the installed virtual environment directly and the normal
per-user Llameros configuration. For a different configuration file, append
`--config /absolute/path/to/config.toml` to `ExecStart`. `%h` is your home
directory and `%t` is your user runtime directory. The Docker socket assumes the
standard rootless installation; adjust it if yours differs. Services do not read
your shell startup files, so include any custom Docker CLI location in `PATH`.

Enable Docker and Llameros, then inspect startup and logs:

```bash
systemctl --user daemon-reload
systemctl --user enable --now docker.service
systemctl --user enable --now llameros.service
systemctl --user status llameros.service --no-pager
journalctl --user -u llameros.service -n 50 --no-pager
```

### Keep Llameros running after logout (enable lingering)

**Do not skip this step for an unattended server.** Enabling the user service
alone does not ensure that its user-service manager stays running after the last
login session ends. Enable lingering so the manager starts at boot and remains
available after logout. This applies whether Docker is rootless or rootful:
Llameros itself is still a user service.

While logged in as the **account running Llameros**, run:

```bash
sudo loginctl enable-linger "$(id -un)"
loginctl show-user "$(id -un)" --property=Linger
```

The verification command should print `Linger=yes`. If an administrator runs
this from a root shell, use the actual Llameros account name instead of
`$(id -un)`; otherwise lingering would be enabled for root. Lingering does
**not** run Llameros as root.

As the Llameros account, without sudo, ensure the service is enabled and running:

```bash
systemctl --user enable --now llameros.service
systemctl --user status llameros.service --no-pager
```

Log out and check the dashboard from another machine. If it still stops despite
`Linger=yes`, reconnect and inspect the unit and logs:

```bash
systemctl --user cat llameros.service
journalctl --user -u llameros.service -n 80 --no-pager
```

### Restarting or disabling the service

After configuration changes or application updates, use
`systemctl --user restart llameros.service`. After editing the unit itself, run
`systemctl --user daemon-reload` first. To stop Llameros and disable automatic
startup, use `systemctl --user disable --now llameros.service`; this does not
disable Docker. If you get “Failed to connect to bus”, log in directly as the
account through SSH or a desktop session rather than switching users with sudo.

### Using an existing rootful Docker daemon

The unit above assumes rootless Docker. If your installation deliberately uses
the system-wide (rootful) daemon, keep Llameros as a **user service**, but make
these changes:

1. Set `allow_rootful_docker = true` in the Llameros configuration actually loaded
   by the service (normally `$HOME/.config/llameros/config.toml`). This explicitly
   accepts root-equivalent access through Docker; it does not run Llameros as root.
2. In `$HOME/.config/systemd/user/llameros.service`, remove
   `Wants=docker.service` and `After=docker.service`: those refer to a **user**
   Docker unit, not the system daemon. Replace the Docker environment line with
   `Environment=DOCKER_HOST=unix:///var/run/docker.sock`. Keep
   `UnsetEnvironment=DOCKER_CONTEXT` to avoid a conflicting context override.
3. Start the system daemon and verify socket access as the ordinary Llameros
   account, then restart Llameros:

```bash
sudo systemctl enable --now docker.service
env -u DOCKER_CONTEXT DOCKER_HOST=unix:///var/run/docker.sock docker info
systemctl --user daemon-reload
systemctl --user restart llameros.service
journalctl --user -u llameros.service -n 50 --no-pager
```

The `docker info` command must succeed **without sudo**. If access is denied,
have an administrator review socket access for the service account. Do not make
the socket world-writable. Docker-group access is root-equivalent; if group
membership changes, the existing user manager may retain its old groups until
its session is restarted (a reboot is an unambiguous way to apply the change).
Do not start a second rootless daemon or migrate models merely to use systemd.

### Docker works in a terminal but not in the service

The example above explicitly selects the standard rootless socket. Before using
it, verify that this is the same daemon used by your working terminal. Run these
commands as the Llameros account on the affected host:

```bash
command -v docker
printenv DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG
docker context show
docker context inspect --format '{{.Endpoints.docker.Host}}'
docker info --format '{{json .SecurityOptions}}'
env -u DOCKER_CONTEXT DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock" docker info
systemctl --user status docker.service llameros.service --no-pager
systemctl --user cat llameros.service
journalctl --user -u llameros.service -n 80 --no-pager
```

If ordinary `docker info` works but the explicit socket check fails, the service
is targeting a different or stopped daemon. For a standard rootless installation,
start its existing user service with `systemctl --user start docker.service`.
If that unit does not exist, follow the rootless installation section below;
do not assume the system-wide Docker service is the same daemon.

For a custom rootless socket, change `Environment=DOCKER_HOST=...` in the
Llameros unit to the verified local `unix://` endpoint. Account for any terminal
`DOCKER_CONTEXT` override when determining that endpoint. Also check that the
service `PATH` contains the directory reported by `command -v docker`, and set
`DOCKER_CONFIG` explicitly if you use a non-default Docker configuration directory.
Then run `systemctl --user daemon-reload` and
`systemctl --user restart llameros.service`.

Do not switch to `/var/run/docker.sock`, loosen socket permissions, or run Llameros
as root merely to bypass an error. The system daemon may be rootful (refused by
default) and may contain different models and containers. Preserve the existing
daemon's workloads and storage when diagnosing the connection.

## Check your firewall configuration
```bash
ufw status 
```
and make sure that the port you are using for Llameros is not blocked.

## Switch an existing installation to rootless Docker
This creates a **separate per-user daemon**, not an in-place conversion. Keep the
old containers/volumes until the replacement works. The steps below assume Linux
with systemd and Docker Engine; package names differ for distribution/Snap/Desktop
installations. Follow Docker's official rootless installation instructions for your
distribution rather than mixing package sources.

### Check if you have rootless Docker running allready

```bash
docker info -f "{{println .SecurityOptions}}" | grep rootless
```
If this command outputs rootless, your Docker daemon is running in rootless mode. If it returns blank or an error, it is running as root.


### 1. Stop workloads and preserve data

- Record the current Docker context (`docker context show`) and any `DOCKER_HOST`
  or `DOCKER_CONTEXT` overrides. Confirm which daemon owns your existing workloads.
- Stop Llameros model runtimes on that original daemon, then stop the Llameros
  server. If the dashboard refuses rootful Docker, use the original Docker context
  to inspect and stop the specific managed containers manually; do not stop all
  containers indiscriminately.
- Back up Llameros configuration and `state_dir` while the server is stopped.
  Keep `runtime-slots.json`; do not delete it to work around migration errors.
- **Host-directory models:** back up `blobs` and `manifests`. Use a private copy
  owned by the Llameros account and verify rootless container access before reuse.
- **Docker-volume models:** volumes are not transferred by switching contexts.
  Either re-download models on the new daemon or export them while the old daemon
  is available. The default Llameros volume is `llameros-<uid>-models`, mounted at
  `/root/.ollama`; its `models` subdirectory contains `blobs` and `manifests`.
  Restore those two directories into a private host models directory, then select
  it under **Configuration → Model storage**. Use Docker's volume backup/restore
  procedure; do not copy Docker's internal data directory or its ownership mappings.

### 2. Prepare the account and install the user daemon

Log in directly as the account that runs Llameros (desktop or SSH), not through
`sudo su`. Rootless Docker needs `newuidmap`/`newgidmap` and at least 65,536 subordinate
UIDs and GIDs assigned to this account in `/etc/subuid` and `/etc/subgid`. Ask an
administrator to allocate non-overlapping ranges if missing; do not copy another
account's ranges. On Debian/Ubuntu using Docker's official apt repository, the
relevant packages are typically installed with:

```bash
sudo apt-get install uidmap docker-ce-rootless-extras
```

Administrator privileges here are for host setup only, **never for running
Llameros or the rootless setup tool**. Resolve any distribution-specific user
namespace/AppArmor prerequisites reported by Docker's installer.

If all workloads on the system-wide daemon have been stopped or migrated and its
owner approves the outage, disable that daemon and its socket activation:

```bash
sudo systemctl disable --now docker.service docker.socket
```

Otherwise keep it available for unrelated workloads and use the installer's
`--force` option below. This leaves a rootful daemon running; selecting rootless
Docker does not remove that daemon or its access privileges.

```bash
# Run as the normal Llameros account, without sudo.
unset DOCKER_HOST DOCKER_CONTEXT
dockerd-rootless-setuptool.sh install
# If intentionally retaining the rootful daemon, use instead:
# dockerd-rootless-setuptool.sh install --force
systemctl --user enable --now docker.service
docker context use rootless
docker info --format '{{json .SecurityOptions}}'
```

The final output must include `rootless`. A context name alone is not proof.
For startup before login and persistence after logout, an administrator can enable
lingering with `sudo loginctl enable-linger "$USER"` from this account's session.
If the user service cannot connect to the bus, log in normally and check Docker's
rootless troubleshooting guide instead of creating a system-wide rootless service.

### 3. Reconnect Llameros and verify

Set `allow_rootful_docker = false` in the Llameros startup TOML. Remove stale Docker
environment overrides from the shell or service that launches Llameros: changing
your interactive shell does not change an already running service's environment.
Use the `rootless` context for that same account. If the launcher needs an explicit
socket, rootless Docker normally uses `unix:///run/user/<uid>/docker.sock`; do not
point it back to `/var/run/docker.sock`.

Restart Llameros, select the preserved host model directory or new Docker volume,
and start Ollama. Containers and digest-pinned images are recreated/downloaded on
the new daemon. Confirm model inventory, CPU inference, API-key access, and any
GPU execution before removing backups. GPU access and resource-limit support need
separate host validation; do not fix permission failures with `chmod 777` or sudo.

**Rollback:** stop the new Llameros server and rootless runtimes first. Restore the
previous launcher context/environment and storage selection; re-enable the original
daemon only if you disabled it and its owner approves. Using rootful Docker again
also requires explicit `allow_rootful_docker = true` risk acceptance. Changes made
to the new model store are not automatically synchronized back to the old one.

References: Docker's [rootless installation](https://docs.docker.com/engine/security/rootless/),
[user-service and context setup](https://docs.docker.com/engine/security/rootless/tips/),
[troubleshooting](https://docs.docker.com/engine/security/rootless/troubleshoot/), and
[volume backup/restore](https://docs.docker.com/engine/storage/volumes/#back-up-restore-or-migrate-data-volumes).

## First model and daily workflow

1. **Start Ollama.** The first start may download the runtime image; progress and
   errors appear in the dashboard. Only public-image downloads are supported.
2. Search the official model catalogue, choose a tag, and download it. The
   **CPU-only** filter limits discovery to estimated RAM fits; it does not choose
   the execution device. Memory estimates are heuristics, not guarantees.
3. Select CPU or a supported GPU under **Start model on available hardware**, then
   load the downloaded model. Multiple models can remain resident per device,
   subject to memory and `max_loaded_models` (default 2).
4. Use **Try a model** for text generation or **Access model** for authenticated
   API URLs and client examples. Create a bearer key as described below.
5. **Stop model** unloads only that model; **Stop Ollama** stops all managed runtimes.
   Downloaded weights remain in the selected model store.

CPU utilization does not prevent loading if memory permits. NVIDIA/AMD execution
requires compatible hardware and drivers; detected Intel/unidentified GPUs are not
selectable. GPU slots use separate containers but share downloaded weights. Host
CPU/RAM history and usage totals are available on both boards; detailed lifecycle
controls and events are admin-only.

## Secure inference API

In **Key Management → Add key**, choose a name, model scope, and expiry, then copy
the one-time masked secret into a secure store. It cannot be retrieved later.
Admins may create wildcard or non-expiring keys; ordinary users need explicit
administrator model/target grants and keys valid for 1–30 days within the grant
lifetime. Never share Linux passwords with inference clients.

- Ollama base URL: `https://127.0.0.1:8443/inference`
- OpenAI-compatible base URL: `https://127.0.0.1:8443/inference/v1`
- Every inference endpoint requires `Authorization: Bearer <key>`.
- Use the dashboard's copyable client examples and a trusted CA certificate.
  **Never disable TLS verification or put real keys on a command line.**

Models must already be loaded. If a model is loaded on multiple permitted devices,
select one with `X-Llameros-Target` or the JSON `target` field (which takes precedence).
Each device executes up to `ollama_num_parallel` requests at a time (default 2), with up to 16 queued requests and a
120-second queue timeout; different devices can execute independently.

Text-only requests are limited to 128 KiB; inline-image requests default to 16 MiB.
Output is limited to 1–4096 tokens (default 256). Documents/PDFs are unsupported;
provide plain text. These limits are **not aggregate resource quotas**. See
[API compatibility](api-compatibility.md) for endpoints, streaming, and media limits.

Delete a key to reject new requests; revocation does not stop an already executing
request. Usage history retains content-free totals, not prompts or responses;
interrupted streams may have unknown token counts. Model-generated tool calls are
untrusted: client applications must validate and authorize any side effects.

### Simultaneous users and request queues

Requests through the Llameros inference gateway share a **first-in, first-out
(FIFO) queue per hardware target**, regardless of user or API key. Each GPU admits up to
`ollama_num_parallel` active API requests (default **2**); the CPU target has its own queue. Different
targets can execute requests in parallel.

For example, Alice and Bob can generate concurrently on GPU 1. With the default
limit of 2, a third request waits until either finishes. GPU 2 has its own two
permits. Different models loaded on the **same** GPU share the device limit and queue. Streaming requests retain
their reservation until completion or cleanup after interruption.

| Current default, per target | Behavior |
| --- | --- |
| Active inference requests | `ollama_num_parallel` (default 2) |
| Waiting requests | Up to 16 |
| Maximum queue wait | 120 seconds (not an execution-time limit) |
| Queue full | HTTP 429 with `Retry-After: 1` |
| Queue wait exceeded | HTTP 504 |

Client timeouts should allow for both queueing and generation. On queue-full
responses, respect `Retry-After` and avoid immediately flooding the queue with
retries. A streaming client may receive no generated tokens while waiting.

**Routing and fairness:** Llameros does not automatically choose the least-busy
GPU. If a model is loaded on multiple targets, select one explicitly, for example
with `X-Llameros-Target`; an ambiguous selection returns HTTP 409. FIFO is not
per-user fair scheduling: one user submitting many requests can occupy the queue
ahead of others. Waiting requests start when an active permit becomes free; Llameros forwards each
request separately and leaves model execution to Ollama.

Credentials are checked again after queue admission, before upstream execution,
so a key revoked while waiting cannot start new work. Direct requests to Ollama
bypass these Llameros queues, authorization checks, and usage accounting.

### Configure parallel inference and memory headroom

In your Llameros startup TOML, set:

```toml
ollama_num_parallel = 2 # Integer 1–16; default 2. Use 1 for serial execution.
```

Llameros passes this value as `OLLAMA_NUM_PARALLEL` to every managed Ollama
container and enforces the same active-request ceiling **per hardware target**,
shared across all users and loaded models on that target. Ollama's setting is
per model; the Llameros device-wide ceiling is deliberately more conservative.
`max_loaded_models` controls model residency, not concurrent requests. Existing
quota checks still apply; concurrent in-flight requests can exceed a remaining
allowance before their actual token usage is recorded.

Higher parallelism can improve aggregate throughput but increases memory demand
and can reduce each request's individual speed. The catalogue, exact model-fit
check, and load admission use the same safety estimate:

```text
base allowance = clamp(25% of model weight bytes, 256 MiB, 2 GiB)
estimated memory = 1.25 × weight bytes
                 + base allowance × ollama_num_parallel × max(1, context_length / 4096)
```

A further 512 MiB is reserved from available system RAM. GPU VRAM is evaluated
separately, never added to RAM; unified-memory GPUs use available system RAM.
Weights are not multiplied by parallelism. This is a heuristic allowance, not an
exact architecture-specific KV-cache calculation or an out-of-memory guarantee.
A model that fitted with one request may no longer fit with two or more.

**Applying a change / upgrading from serial runtimes:** wait for active requests
to finish, stop all Ollama runtimes, edit the configuration, and restart Llameros.
While runtimes are stopped, reapply the existing **Model storage** selection to
recreate the containers, then start/load models again. Keep the same storage
location; downloaded weights are retained. Restarting the Llameros service alone
cannot change an existing container's environment. Containers with mismatched
parallelism are refused with instructions to recreate them. Set the TOML option,
not just an environment variable on the Llameros systemd service.

## Configuration

### Application settings

Edit `~/.config/llameros/config.toml`; see `config.example.toml` for all settings.
Paths beginning with `~` expand to the service user's home directory.

| Setting | Default / purpose |
|---|---|
| `host`, `port` | `127.0.0.1`, `8443`; HTTPS listener |
| `state_dir` | `~/.local/share/llameros`; private TLS, history, and runtime state |
| `cert_file`, `key_file` | Empty: generate TLS material; otherwise use existing PEM files |
| `certificate_download_enabled` | `true`; offer the public certificate download to signed-in users on both boards |
| `pam_service`, `session_seconds` | `login`, `3600`; authentication and absolute session lifetime |
| `sample_seconds`, `retention_days` | `15`, `30`; telemetry interval and history retention |
| `ollama_image`, `ollama_rocm_image` | Digest-pinned runtime images; use the example configuration |
| `gpu` | `cpu`; catalogue estimate preference, not execution placement |
| `ollama_num_parallel` | `2`; integer 1–16, Ollama parallelism and Llameros active requests per device |
| `context_length`, `max_loaded_models` | `4096`, `2`; context and resident models per device |
| `allow_rootful_docker` | `false`; setting `true` explicitly accepts root-equivalent daemon access |

Runtime APIs use private relays, not published host ports. `ollama_port` is a legacy
registry identifier. Keep one Llameros instance per Linux UID/Docker daemon; do not
delete the runtime registry while containers exist. Changing an image/configuration
does not recreate existing containers: stop runtimes and reapply storage first.

### TLS certificates and client trust

Set these in your startup TOML and restart Llameros after certificate/config changes:

```toml
cert_file = "/home/keuper/.config/llameros/tls/fullchain.pem"
key_file = "/home/keuper/.config/llameros/tls/key.pem"
certificate_download_enabled = true
```

Replace the example paths with your service account's paths. Supply both PEM files,
keep the private key readable only by that account (`chmod 600`), and use a certificate
whose subject alternative names match the DNS name/IP clients actually use. With
both paths empty, Llameros generates `cert.pem` and `key.pem` inside `state_dir`.
Never distribute `key.pem` or a CA private key.

With downloads enabled (the default), **Key Management → Add key** on both admin
and user boards offers **Download server certificate (PEM)**. The session-protected
`GET /api/certificate` exports public certificate blocks only, including a configured
chain, as `llameros-cert.pem`. Setting the option to `false` hides the link and makes
the endpoint return 404. Missing/invalid certificate files return 503; downloading
does not generate certificates. Keep the on-disk certificate aligned with the running
TLS configuration by restarting after renewal/replacement.

**Downloading is not trust verification.** Establish browser trust before signing
in; do not bypass a certificate warning to obtain a supposedly trustworthy copy
from that same server. For a small self-signed deployment, distribute the public
certificate or its SHA-256 fingerprint through an independently authenticated
channel, and compare before trusting the downloaded file. Use the verified file
as `LLAMEROS_CA_CERT` in Python examples. The generated certificate is a server
certificate, not an organizational CA; renewed self-signed certificates must be
verified/distributed again.

For shared deployments, prefer an organization-issued certificate with its root CA
distributed through managed device configuration. Alternatively, use a publicly
trusted certificate for a domain you control; DNS-01 issuance allows the service
to remain VPN-only. CA-issued deployments should use the trusted issuing CA bundle
or normal system trust, not assume the downloaded server chain contains a trusted
root. All client examples must retain TLS verification. The model cards on both
boards offer the same Ollama, OpenAI-compatible, LangChain, and LlamaIndex examples;
user cards do not expose lifecycle controls.

### Model storage in the GUI

In **Configuration → Model storage**, choose a dedicated directory containing only
Ollama's `blobs` and `manifests`, or leave the path empty for the managed Docker volume.
Select the **models directory itself**, not its parent `.ollama` directory. Existing
roots must belong to the service user and not be group/other-writable; symlinks are
rejected. Keep the entire tree private: descendant permission checks have a known
limitation documented in the [security audit](Security_Audit.md).

Stop all runtimes, enter the location, confirm, and **Save storage location**. This
removes stopped managed containers; restarting recreates them against the selected
store. **Model data is never automatically copied or deleted.** To relocate it,
back up first and copy `blobs`/`manifests` while stopped, preserving permissions.
The preference persists separately from TOML. Llameros does not change ownership.

While Ollama runs, inventory comes from its API. While stopped, directory inventory
checks manifests/blob sizes (not hashes); Docker-volume inventory is a timestamped
cache. Start Ollama to refresh it.

## Multi-user dashboard access

Admins manage roles, blocking, and time-limited model/target grants in
**Configuration → User access**. Users can view shared running models/host history
and manage only their own keys; they cannot manage runtimes or see other owners'
keys. Admins can switch to their personal user board without changing their role.
Blocking invalidates sessions and revokes keys; unblocking restores neither.
Roles and blocks survive restart. Self-blocking/self-demotion is refused.

**Test a real second-account login before sharing.** PAM/NSS policy may prevent
cross-account authentication by an unprivileged process. Llameros does not install
privileged helpers, read shadow hashes, or modify PAM. Do not weaken PAM or run as
root to work around a failure. Login-disabled service accounts cannot sign in.

## Upgrading existing installations

1. Stop old managed containers on their original Docker daemon. Upgrading code
   alone does not close their previously published ports.
2. Reapply the model-storage selection while stopped, then restart to recreate
   containers with private runtime access. Preserve model volumes and backups.
3. When switching to rootless Docker, arrange model storage separately: containers
   and volumes do not migrate automatically between daemons. Follow the
   [rootless migration steps](#switch-an-existing-installation-to-rootless-docker).
4. Replace mutable image tags with digest pins from the example configuration;
   remove obsolete `pdf_max_*` options and replace unsupported user wildcard or
   non-expiring keys with granted, expiring keys.
5. Move every client to the HTTPS bearer gateway and verify access denial from
   another unprivileged account/container before shared use.

For refused legacy containers or unavailable slots, follow
[Troubleshooting](trouble_shooting.md#cpu-shows-unavailable-despite-low-utilization).

## Security before sharing

Bind to localhost or restrict access to a VPN interface/firewall, including IPv6.
VPN membership is not authorization: retain trusted HTTPS, PAM, scoped grants/keys,
and rootless runtime isolation. The service UID, root, and daemon administrators
remain trusted. Signed-in users can see resident model names, aggregate model usage,
and host CPU/RAM history; obtain agreement or use separate deployments.

Review the [security audit](Security_Audit.md) for known findings and unverified
release checks, including image/host scanning and live isolation tests. Consult the
[security model](development.md#security-model-and-limitations) before changing
network bindings or Docker permissions. Never include credentials or prompts in
support reports.

## Ollama image versions

Llameros downloads the configured immutable image digest and adds a separate local
alias, `llameros/ollama:sha256-<full-digest>`. Existing upstream tags such as
`ollama/ollama:latest` are not overwritten. Containers still run by digest, not by
the mutable alias. An alias pointing to a different image is rejected rather than overwritten.

On startup, an older stopped container owned by this Llameros user and hardware
target is preserved as `<container-name>-old-<unique-suffix>` before its replacement
is created. The configured image is downloaded first; a failed download leaves the
old container untouched. Running old containers must be stopped explicitly first.
Unowned or differently assigned containers remain protected by ownership checks.
Images, volumes and model files are not deleted. Archived containers retain their
old mounts, while replacements use the currently selected storage. Do not run old
and replacement containers simultaneously against shared model storage. Archives
are retained for manual recovery and are not managed by the dashboard.

## Token usage limits

**Configuration → User Access → Default user limits** sets the allowance for every
user without an explicit user-limit override, including accounts the admin has not
seen yet. Each user gets a separate allowance; this is not a shared pool. The initial
default is **No limit** for both input and output. Changing defaults immediately affects
all inheriting users (including admins) without clearing their consumption. Explicit
per-user settings, including explicit unlimited settings, take precedence. User statistics
label inherited policies **Default user limits**; **Use defaults** in a user's Config
dialog removes their override without resetting counters. Defaults persist across restarts.
These are quota defaults only: users still require the existing model/target grants to
create usable keys; changing defaults does not grant access.

Administrators can configure a user's allowance across all keys/models in **Configuration →
User Access → Config**, and a model allowance per user using **Config** beside a downloaded
model. Both boards provide **Config** in **Add key** and beside existing keys. Users can
change only their own key limits; they cannot override admin user/model allowances.

Input and output allowances are independent, entered in whole thousands of tokens
(1 = 1,000). Each defaults to **No limit**. A policy shares one reset period:
**1 day** (00:00 UTC), **1 week** (Monday 00:00 UTC), **1 month** (first day 00:00 UTC),
or **Never**. Key/model statistics show consumption, allowance, remaining tokens and reset;
key statistics also show the owner's shared allowance.

All applicable allowances are checked just before inference dispatch, including admin
playground requests (user/model limits). Actual upstream token counts are charged after
execution. Requests already in flight may exceed the allowance, including concurrent
requests; subsequent requests receive HTTP 429. Calendar quotas include a `Retry-After`
header. Requests crossing a reset belong to their admission window.

Counters persist independently of history cleanup and key deletion. Editing or disabling
limits does not erase consumption. Accounting starts when this feature is installed;
older retained history is not imported. Lifetime counters have no automatic reset.
If an interrupted/failed upstream request lacks token counts, that dimension is marked
unknown and finite allowances fail closed for its window; unlimited dimensions remain
unlimited. The UI identifies this state. A calendar reset clears it; for a lifetime policy,
an administrator can change the applicable policy (for example to a calendar window or
unlimited). Restarting the server does not clear recorded usage, but an abrupt process or
host crash before request finalization can lose that in-flight request's accounting.

Direct connections to Ollama bypass Llameros authorization and accounting. Restrict direct
runtime access when enforcing quotas; use the authenticated `/inference` gateway for clients.

Model allowances apply separately to each user across all of their keys, not to a shared
pool across users. Model usage totals remain overall statistics; the model-limit column
shows the signed-in user's allowance and consumption. Existing model policies are preserved
when upgrading from global model quotas, but per-user/model counters start fresh because
legacy global counters cannot be reliably attributed to individual users. Existing key
and user-wide counters are unchanged.
