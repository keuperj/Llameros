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

## Switch an existing installation to rootless Docker

This creates a **separate per-user daemon**, not an in-place conversion. Keep the
old containers/volumes until the replacement works. The steps below assume Linux
with systemd and Docker Engine; package names differ for distribution/Snap/Desktop
installations. Follow Docker's official rootless installation instructions for your
distribution rather than mixing package sources.

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
Each device executes one request at a time, with up to 16 queued requests and a
120-second queue timeout; different devices can execute independently.

Text-only requests are limited to 128 KiB; inline-image requests default to 16 MiB.
Output is limited to 1–4096 tokens (default 256). Documents/PDFs are unsupported;
provide plain text. These limits are **not aggregate resource quotas**. See
[API compatibility](api-compatibility.md) for endpoints, streaming, and media limits.

Delete a key to reject new requests; revocation does not stop an already executing
request. Usage history retains content-free totals, not prompts or responses;
interrupted streams may have unknown token counts. Model-generated tool calls are
untrusted: client applications must validate and authorize any side effects.

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
