# Benchmark statistics server: deployment, systemd and backups

This guide is for the **benchmark statistics server**, not an Ollama runtime or a
separate backup service. It covers installation, unattended startup, owner-only
administration, and backup/restore of its SQLite database and TLS identity.
See `README.md` in this directory for the shared-data allowlist, privacy limits,
voucher protocol and moderation rules.

## 1. Requirements and deployment layout

- Linux with systemd, Python **3.11 or newer**, a working system PAM library and
  the project dependencies listed in the repository's `pyproject.toml`.
- A persistent, unprivileged Linux account. The examples use
  `llameros-statistics`; it must not have sudo or Docker-group access.
- A login-capable shell and usable PAM password if you want browser administration.
  A locked/passwordless/`nologin` account can run the public service but may not
  be able to sign in to `/admin`. Do not run the server as root to bypass PAM.
- A reachable IP address, an unused TCP port (default **9443**), and a private
  persistent state directory. No Docker daemon, GPU or Ollama installation is
  needed on this host.

The system-service example uses:

| Purpose | Absolute path |
| --- | --- |
| Installed virtual environment | `/opt/llameros-statistics/.venv` |
| Persistent state | `/var/lib/llameros-statistics` |
| systemd unit | `/etc/systemd/system/llameros-statistics.service` |
| Example source checkout | `/home/keuper/Projekte/Llameros` |

Replace the source checkout path with your actual checkout. Installation is a
wheel/package installation, **not editable**: future source edits do not change
the running installation until you reinstall it. Keep the Python installation
used to create the virtual environment available after reboot.

## 2. Install the service account and application

Account/package commands vary by distribution. On Debian/Ubuntu, an administrator
can install the prerequisites and create a dedicated normal account as follows:

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv python3-pip libpam0g openssl
sudo useradd --create-home --shell /bin/bash llameros-statistics
sudo passwd llameros-statistics
```

Use an existing account instead if appropriate; do not recreate it. Check that
`python3 --version` is at least 3.11. Set a strong password interactively: never
put a Linux password in a unit file, environment variable or command argument.
Restrict SSH access to this account according to your host policy; the web PAM
password does not require granting remote shell access.

Install from a trusted source checkout:

```sh
sudo install -d -o root -g root -m 0755 /opt/llameros-statistics
sudo python3 -m venv /opt/llameros-statistics/.venv
sudo /opt/llameros-statistics/.venv/bin/python -m pip install /home/keuper/Projekte/Llameros
sudo install -d -o llameros-statistics -g llameros-statistics -m 0700 /var/lib/llameros-statistics

/opt/llameros-statistics/.venv/bin/python -m statistics_server --help
```

This installs the complete project package, including `statistics_server`, its
website assets, and the PAM dependency. It does not start Llameros or Docker.
Only administrators should be able to modify `/opt/llameros-statistics` and the
service unit. The service account must own and be able to write its state, but
must not be able to replace its installed program. Use the repository lockfile
workflow or approved pinned deployment artifacts if your environment requires
reproducible dependency versions; a plain pip install resolves the declared ranges.

## 3. Choose the bind address and TLS identity

The supplied unit defaults to **127.0.0.1:9443**, deliberately limiting access to
the host. Before enabling remote contributions, change `--host` to the actual
reachable static IPv4/IPv6 address. That IP is included in the generated
certificate's subject alternative names. Do not use an example/documentation IP
as if it were your host address.

- Binding to a specific reachable IP allows direct HTTPS without a reverse proxy.
  Permit TCP 9443 only from intended networks in the host/provider firewall.
- `--host 0.0.0.0` is a bind wildcard, not an address clients can validate. For
  wildcard binding or a NAT/public address different from the bind address, supply
  a certificate with the actual client-visible address in its SAN using
  `--cert-file` and `--key-file`.
- The Llameros integration accepts a literal server IP, not a hostname. Make sure
  the certificate covers that IP, even if you also use a DNS name for the website.
- Prefer a protected network/VPN for administration. The built-in global limits
  are not a substitute for network-level abuse protection.

Generated files in `/var/lib/llameros-statistics`:

| File | Purpose |
| --- | --- |
| `benchmarks.sqlite3` | Benchmark payloads, moderation status, review flags and voucher hashes |
| `cert.pem` | Public self-signed certificate |
| `key.pem` | TLS private key; keep mode **0600**, never distribute |

Certificate maintenance runs **at process startup**, not continuously. A generated
certificate is renewed if near expiry or missing required names. Plan maintenance
restarts and expiration monitoring. Changing the address, hostname or restoring
on another host can cause regeneration; redistribute the public certificate to
clients whenever the self-signed trust anchor changes.

## 4. Install and enable the systemd system service

The ready-to-copy template is `llameros-statistics.service` in this directory.
From the source checkout:

```sh
sudo install -o root -g root -m 0644 /home/keuper/Projekte/Llameros/statistics_server/llameros-statistics.service /etc/systemd/system/llameros-statistics.service
sudoedit /etc/systemd/system/llameros-statistics.service
```

Review `User=`, `Group=`, the Python path and `--host` before starting. The template
contains:

```ini
[Unit]
Description=Llameros benchmark statistics HTTPS server
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=llameros-statistics
Group=llameros-statistics
WorkingDirectory=/opt/llameros-statistics
ExecStart=/opt/llameros-statistics/.venv/bin/python -m statistics_server --host 127.0.0.1 --port 9443 --state-dir /var/lib/llameros-statistics --pam-service login --max-entries 100000
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
LimitCORE=0

[Install]
WantedBy=multi-user.target
```

Validate and enable it:

```sh
sudo systemd-analyze verify /etc/systemd/system/llameros-statistics.service
sudo systemctl daemon-reload
sudo systemctl enable --now llameros-statistics.service
sudo systemctl status llameros-statistics.service --no-pager
sudo journalctl -u llameros-statistics.service -n 50 --no-pager
```

This is a **system service running as an unprivileged user**, not a root process.
It starts at boot without a user login or lingering. `Type=simple` reports process
startup, not application readiness: also perform the HTTPS checks below. If the
chosen address is not available at boot, the service may need a network-specific
wait-online configuration or a later restart; `network-online.target` alone does
not configure your network.

The template intentionally does not add `NoNewPrivileges=`, `RestrictSUIDSGID=`,
capability restrictions or aggressive filesystem namespaces. Depending on the
host PAM stack, authentication may need a privileged password-check helper or
specific filesystem access. Blindly applying such restrictions can break PAM.
Add hardening only after testing the actual host policy; never loosen shadow-file
permissions or run the web process as root to make login work. `LimitCORE=0`
reduces accidental credential exposure through ordinary process core dumps;
review host crash collectors and diagnostics separately.

Do not add `PAMName=` merely to enable website login: the application calls PAM
itself with `--pam-service`. Under `User=llameros-statistics`, that account—and
only that account—is the web administrator. It is **not** the administrator who
ran `sudo systemctl start`.

Useful lifecycle commands:

```sh
sudo systemctl restart llameros-statistics.service
sudo systemctl stop llameros-statistics.service
sudo systemctl disable --now llameros-statistics.service
```

After unit edits, run `daemon-reload` before restarting. Do not launch a second
manual process on the same port or run multiple workers to bypass rate limits.

### Alternative: systemd user service

If running under an existing login account, install a unit in that account's
`~/.config/systemd/user/llameros-statistics.service` instead. Use the same
`[Service]` command with absolute paths writable/readable by that account, remove
`User=` and `Group=`, and use `WantedBy=default.target`. Do not enable both the
system and user variants. Manage it with `systemctl --user`; boot startup after
logout requires an administrator to enable lingering for that account. The
account running the user manager becomes the PAM administrator. The main
instructions above use the system-service variant throughout.

## 5. Verify HTTPS and connect Llameros

Inspect the certificate through a trusted local/admin channel:

```sh
sudo openssl x509 -in /var/lib/llameros-statistics/cert.pem -noout -dates -fingerprint -sha256
sudo -u llameros-statistics /opt/llameros-statistics/.venv/bin/python - <<'PY'
import json
from pathlib import Path
from llameros.benchmark_exchange import SharingSettings, exchange

policy = SharingSettings(
    enabled=True,
    host="127.0.0.1",
    port=9443,
    ca_certificate=Path("/var/lib/llameros-statistics/cert.pem").read_text(),
)
print(json.dumps(exchange(policy, "GET", "/api/v1/benchmarks"), indent=2))
PY
```

Change the check's IP/port to match the unit. This uses verified TLS rather than
`curl -k` or disabling verification. An empty `entries` array is normal until a
benchmark has been approved. Test connectivity from a remote client too if using
a reachable network address.

Open the server's HTTPS address in a browser, verify/trust its public certificate,
then visit `/admin`. Sign in with the service account's Linux username/password.
The default PAM service is `login`; change it only to a host policy configured by
your system administrator. Do not create permissive PAM rules to bypass failures.

On each Llameros instance, open **Configuration → Community benchmark server**:
enter the reachable IP and port, paste the **public** PEM certificate received
through a trusted channel, enable the integration and save. Never paste or
transfer `key.pem`. Enabling allows downloads, not automatic uploads.

## 6. Daily administration

At `/admin` you can:

1. Monitor queue counts, capacity and aggregate voucher usage.
2. Authorize an upload request by issuing a bounded, expiring voucher; deliver it
   privately. No public contact/request form or contributor account is created.
3. Review pending payloads, six-case measurements and warning flags.
4. Confirm **Approve / publish** or **Reject / unpublish**. Only approved entries
   are public and eligible for recommendations. Approval is review, not proof.

Voucher secrets cannot be retrieved later. The browser clears displayed secrets
after five minutes or on logout. Do not retain recipient-to-voucher lists or
record screens/terminal sessions containing secrets. Client recommendation caches
may retain a previously approved result for up to ten minutes after rejection.

The local CLI uses the **same installed interpreter and state directory**:

```sh
sudo -u llameros-statistics /opt/llameros-statistics/.venv/bin/python -m statistics_server.manage --state-dir /var/lib/llameros-statistics review
sudo -u llameros-statistics /opt/llameros-statistics/.venv/bin/python -m statistics_server.manage --state-dir /var/lib/llameros-statistics issue-voucher --max-entries 3 --hours 24
```

See `README.md` for approval/rejection CLI commands. CLI access does not require
the web password, but does require authorized OS access to the state directory.

## 7. Backup and restore

### Consistent full backup (short maintenance window)

Back up the SQLite database **and** the certificate/key pair, plus a copy of the
unit and the deployed package/version information. The example below stops the
service to make an ordinary file archive consistent. Also stop any CLI writes
and do not run moderation/voucher commands during the backup. Never copy only a
live SQLite main file while transactions may be in flight.

```sh
sudo install -d -o root -g root -m 0700 /var/backups/llameros-statistics
sudo systemctl stop llameros-statistics.service
sudo sh -c 'umask 077; tar -C /var/lib -czf "/var/backups/llameros-statistics/state-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" llameros-statistics'
sudo systemctl start llameros-statistics.service
```

Check that the archive command succeeded; inspect/list and test an archive before
relying on it. Copy the service unit separately after any configuration change.
Encrypt backups before transferring them off-host, restrict access and establish
a retention schedule. Backups include the TLS private key and private moderation
queue; do not publish them alongside the benchmark table. A backup only on the
same disk is not disaster recovery.

### Database-only online snapshot

For an online database snapshot, use SQLite's backup API through Python rather
than copying the live file. Example: create a unique private snapshot in an
operator-created directory owned by `llameros-statistics`:

```sh
sudo install -d -o llameros-statistics -g llameros-statistics -m 0700 /var/backups/llameros-statistics-online
sudo -u llameros-statistics /opt/llameros-statistics/.venv/bin/python - <<'PY'
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

os.umask(0o077)
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
destination = Path("/var/backups/llameros-statistics-online") / f"benchmarks-{stamp}.sqlite3"
with sqlite3.connect("file:/var/lib/llameros-statistics/benchmarks.sqlite3?mode=ro", uri=True) as source:
    with sqlite3.connect(destination) as backup:
        source.backup(backup)
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
print(destination)
PY
```

This snapshot is **not** a full TLS/config backup. Preserve the certificate/key
pair separately during a stopped-service maintenance window, and after renewal.
Automate the chosen procedure with your existing backup scheduler after testing
it; this guide does not install a backup timer or select your retention policy.

### Restore

1. Stop the service and all CLI writes. Preserve the current state separately for
   rollback before replacing anything.
2. Verify/decrypt the archive in a private staging directory. Restore only a
   trusted archive you control; do not extract arbitrary archives as root.
3. Restore the full state directory into `/var/lib/llameros-statistics`. If using
   a database-only snapshot, remove stale journal/WAL/SHM sidecars from the old
   database as part of replacing the **old database set**, while all writers are
   stopped. Do not combine sidecars from different snapshots.
4. Set directory ownership to `llameros-statistics:llameros-statistics`, directory
   mode 0700 and database/private-key mode 0600. Restore the matching public
   certificate and private key together. Check the configured paths against the
   restored unit before restarting.
5. With the installed interpreter, open the restored database and run
   `PRAGMA integrity_check`; require `ok` before returning it to service.
6. **Invalidate restored upload vouchers** before starting. Restoring an older
   database also restores old redemption flags; otherwise a voucher consumed
   after the snapshot could be used again:

```sh
sudo -u llameros-statistics /opt/llameros-statistics/.venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect("/var/lib/llameros-statistics/benchmarks.sqlite3") as db:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("Restored database integrity check failed")
    db.execute("DELETE FROM vouchers")
PY
```

7. Start the service and repeat the HTTPS/admin checks. Review restored approval
   decisions: restoring an older snapshot can make a later-rejected record
   approved again. Issue new vouchers as needed. Sessions are in memory only and
   are invalidated by restart. On a new host/address, check for certificate
   regeneration and update client trust if necessary.

Do not restore a newer schema into older application code without a tested
compatibility plan. Existing v1 databases migrate to pending; migration does not
make legacy evidence approvable.

## 8. Updates, privacy and troubleshooting

Before updating: back up state and the current deployment artifact, stop the
service, install the tested new package into the virtual environment, then start
and verify it. Installing packages while the process is serving traffic can mix
code versions. Unit changes additionally require `systemctl daemon-reload`.

The launcher disables request access logs and suppresses Uvicorn error logging
that could contain request context. systemd still reports process lifecycle and
may capture startup failures; PAM, the OS, firewall, proxy or hosting provider may
have separate logs. Review those facilities and crash collectors for identifying
data. No password, session, voucher or request body should enter diagnostics.
The server sees connection IPs at the network layer even though it does not store
them in the benchmark database. Do not promise complete anonymity.

| Symptom | Check |
| --- | --- |
| Unit fails immediately | Interpreter path, installed package, account/group names, readable program and writable private state directory; inspect systemctl/journal output locally. |
| `Address already in use` | Stop the duplicate/manual process or choose another port; do not launch additional workers. |
| Cannot bind an address | Confirm the address exists on this host and is ready at service startup. |
| Browser/client rejects TLS | Verify IP SAN, expiration and fingerprint; distribute the correct public certificate. Never disable verification to hide a mismatch. |
| PAM sign-in fails | Sign in as the unit's `User=`, check login shell/account status and host PAM policy. The CLI remains available for authorized OS users. |
| Login temporarily throttled | Wait at least one minute; limit is global and does not keep IP/user tracking records. |
| No public benchmarks | Pending/rejected results are deliberately private; approve valid v2 evidence first. |
| Database full | Capacity includes rejected/pending rows; review and plan a backed-up administrative cleanup or change `--max-entries`. |
| Unit hit start limit | Fix the cause, then `sudo systemctl reset-failed llameros-statistics.service` and start again. |

### Reference manuals

Consult the host's installed `systemd.service(5)` and `systemd.exec(5)` for unit
semantics and PAM-related sandbox restrictions, and SQLite's **Online Backup API**
documentation for consistent live snapshots. These are the upstream references
used for this guide; host-specific PAM and systemd behavior still needs testing.