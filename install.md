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
   and volumes do not migrate automatically between daemons.
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
