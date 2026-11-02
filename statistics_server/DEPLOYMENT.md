# Benchmark statistics server: systemd user service and backups

Run the benchmark statistics server as a **systemd user service under your normal
Linux login account**, not as root. That account is also the `/admin` PAM
administrator. This guide is not for an Ollama runtime or separate backup service.
See `README.md` in this directory for privacy, vouchers and moderation.

## 1. Requirements and installation

Use Linux with systemd, Python **3.11+**, system PAM and the dependencies declared
in `pyproject.toml`. The server needs no Docker, GPU or Ollama installation.
Your unprivileged login account needs a usable PAM password and login shell for
browser administration. Locked, passwordless or `nologin` accounts may fail the
host PAM policy. Never run the server as root to bypass it.

Run all application and service commands **as the account that should sign in to
the UI**, from its SSH/desktop login session, not a root or switch-user shell.
Only missing OS prerequisites and optional lingering authorization may need an
administrator. On Debian/Ubuntu, prerequisites can be installed with:

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv python3-pip libpam0g openssl
```

Check `python3 --version` is at least 3.11. Install **without sudo**, replacing the
example checkout `/home/keuper/Projekte/Llameros` with your actual absolute path:

```sh
install -d -m 0700 "$HOME/.local/share/llameros-statistics"
python3 -m venv "$HOME/.local/share/llameros-statistics/venv"
"$HOME/.local/share/llameros-statistics/venv/bin/python" -m pip install /home/keuper/Projekte/Llameros
install -d -m 0700 "$HOME/.local/share/llameros-statistics/state"
"$HOME/.local/share/llameros-statistics/venv/bin/python" -m statistics_server --help
```

This installs the whole project package and assets, but does not start Llameros or
Docker. It is not editable: reinstall after source updates. Keep the base Python
installation available after reboot. Use approved pinned artifacts or the project
lockfile workflow when reproducibility is required; pip resolves declared ranges.
Other users must not be able to modify the installation, unit or private state.
Never put Linux passwords in a unit, environment variable or command argument.

## 2. Install the user unit

Stop any manually running server first. Copy the supplied template:

```sh
install -d -m 0700 "$HOME/.config/systemd/user"
install -m 0600 /home/keuper/Projekte/Llameros/statistics_server/llameros-statistics.service "$HOME/.config/systemd/user/llameros-statistics.service"
```

The complete template is:

```ini
[Unit]
Description=Llameros benchmark statistics HTTPS server
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=%h/.local/share/llameros-statistics
ExecStart=%h/.local/share/llameros-statistics/venv/bin/python -m statistics_server --host 127.0.0.1 --port 9443 --state-dir %h/.local/share/llameros-statistics/state --pam-service login --max-entries 100000
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
LimitCORE=0

[Install]
WantedBy=default.target
```

`%h` expands to the user's home. There is deliberately **no `User=`/`Group=`**:
the process inherits the user manager's account. Do not install this unit under
`/etc/systemd/system`, manage it with `sudo systemctl`, or run it in root's user
manager. The UI uses the process's effective owner, not `USER` or `SUDO_USER`.

The template binds **127.0.0.1:9443** for local access. For remote access, change
`--host` to a stable reachable IP and permit the port only from intended networks
in host/provider firewalls. Prefer a protected network/VPN for administration.

```sh
systemd-analyze --user verify "$HOME/.config/systemd/user/llameros-statistics.service"
systemctl --user daemon-reload
systemctl --user enable --now llameros-statistics.service
systemctl --user status llameros-statistics.service --no-pager
journalctl --user -u llameros-statistics.service -n 50 --no-pager
```

`Type=simple` reports process startup, not HTTPS readiness. Perform the checks below.
The unit does not depend on the system manager's `network-online.target`: a user
manager does not gain system network ordering through that dependency. If binding
an IP unavailable at boot, arrange host-specific readiness; restart after it is
available if the restart limit has been reached.

### Boot startup and running after logout

Authorize lingering for **your login account**, if needed with administrator help:

```sh
sudo loginctl enable-linger "$(id -un)"
loginctl show-user "$(id -un)" --property=Linger
```

Run from the intended user's session and check `Linger=yes`. An administrator in a
root shell must substitute the actual account name instead of `$(id -un)`.
Lingering keeps the user manager running; it grants no root privileges to the
application. Test HTTPS after logout/reboot.

Routine operations, always without sudo:

```sh
systemctl --user restart llameros-statistics.service
systemctl --user stop llameros-statistics.service
systemctl --user disable --now llameros-statistics.service
```

After unit edits, run `systemctl --user daemon-reload` before restarting. Do not
run duplicate processes/multiple workers to bypass limits. Shell startup files
are not sourced by the service, so it uses an explicit virtualenv interpreter.

Avoid adding `NoNewPrivileges=`, `RestrictSUIDSGID=` or aggressive sandboxing
without testing host PAM helpers. Never expose shadow files or run as root to
fix authentication. `PAMName=` is not needed: the application invokes PAM itself.
`LimitCORE=0` reduces ordinary core dumps; review host crash collectors separately.

### Migrating from the previous system service

Have an administrator stop/disable the old system unit first; never run both.
Back up its state and preserve the TLS certificate/key together. With all writers
stopped, arrange an authorized copy into your user state directory with ownership
transferred to your login account, directory mode 0700 and database/key mode 0600.
Do not mix database snapshots with old WAL/SHM/journal sidecars. Install/enable
the user unit and repeat HTTPS/PAM checks before removing the old state. The UI
administrator is now your login user, not the previous dedicated service account.

## 3. TLS and owner login checks

Startup creates `cert.pem`, `key.pem` and `benchmarks.sqlite3` under
`$HOME/.local/share/llameros-statistics/state`. Keep the private key mode 0600;
never distribute it. The database includes private moderation/voucher data.

Binding to a specific IP includes it in the generated certificate. For wildcard
binding (`0.0.0.0`) or NAT, supply a certificate with the client-visible IP SAN
using `--cert-file` and `--key-file` with absolute paths. Llameros connects to
literal IPs, so a DNS-only certificate is insufficient for that connection.
Certificate maintenance happens at startup, not continuously: monitor expiration
and plan maintenance restarts. Renewal/name changes can regenerate the self-signed
certificate; update clients' trust whenever its public certificate changes.

Inspect the certificate through a trusted channel:

```sh
openssl x509 -in "$HOME/.local/share/llameros-statistics/state/cert.pem" -noout -dates -fingerprint -sha256
curl --cacert "$HOME/.local/share/llameros-statistics/state/cert.pem" https://127.0.0.1:9443/api/v1/benchmarks
```

Use the unit's actual IP/port, not necessarily loopback, for the HTTPS check; also
test from a remote client if applicable. Install curl separately if unavailable.
Never use `curl -k` to hide trust problems. An empty public table is normal until
valid evidence is approved.

Verify/trust the public certificate in your browser **before entering credentials**.
Open `/admin` and use **your Linux login username and PAM password**, matching
`id -un` in the session managing this user service. Root login is not required and
there is no separate admin password. The default PAM service is `login`; choose
`--pam-service SERVICE` only for an appropriate administrator-configured policy.

In Llameros Configuration → Community benchmark server, set the reachable IP/port,
paste the trusted **public** PEM certificate, enable and save. This enables
downloads, not automatic uploads. Never transfer `key.pem`.

## 4. Administration

Use `/admin` to monitor capacity/queues, issue bounded single-use upload vouchers,
and review six-case evidence/warning flags before approval or rejection. Approval
means reviewed, not proven genuine. Only approved results are public/recommended;
clients may cache a previously approved entry for ten minutes.

Deliver vouchers privately without keeping recipient-to-voucher lists or recording
screens/terminal sessions containing secrets. They cannot be retrieved later;
the browser clears displayed secrets after five minutes or logout. The CLI runs
as your login user, without sudo:

```sh
"$HOME/.local/share/llameros-statistics/venv/bin/python" -m statistics_server.manage --state-dir "$HOME/.local/share/llameros-statistics/state" review
"$HOME/.local/share/llameros-statistics/venv/bin/python" -m statistics_server.manage --state-dir "$HOME/.local/share/llameros-statistics/state" issue-voucher --max-entries 3 --hours 24
```

See `README.md` for decision commands. CLI authorization relies on OS access to
the state directory rather than a web session.

## 5. Backup and restore

### Full backup with a maintenance window

Preserve state, the user unit and deployed package version/artifact. Stop the
user service **and all CLI writes** before an ordinary file archive. Do not copy
only an actively written SQLite main file.

```sh
install -d -m 0700 "$HOME/.local/state/llameros-statistics-backups"
systemctl --user stop llameros-statistics.service
(umask 077; tar -C "$HOME/.local/share/llameros-statistics" -czf "$HOME/.local/state/llameros-statistics-backups/state-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" state)
systemctl --user start llameros-statistics.service
```

Check the archive succeeded and test restoration. Copy the user unit separately
after configuration changes. Backups contain private keys and moderation data:
encrypt off-host copies, restrict access and define retention. Same-disk copies
alone are not disaster recovery.

### Online database-only snapshot

Use SQLite's backup API instead of copying a live file:

```sh
install -d -m 0700 "$HOME/.local/state/llameros-statistics-backups"
"$HOME/.local/share/llameros-statistics/venv/bin/python" - <<'PY'
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

os.umask(0o077)
home = Path.home()
source_path = home / ".local/share/llameros-statistics/state/benchmarks.sqlite3"
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
destination = home / ".local/state/llameros-statistics-backups" / f"benchmarks-{stamp}.sqlite3"
with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source:
    with sqlite3.connect(destination) as backup:
        source.backup(backup)
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
print(destination)
PY
```

This does not back up TLS/configuration. Copy the certificate/key together during
stopped-service maintenance, including after renewal. Automate backups with a
tested scheduler; these instructions do not install a timer.

### Restore

1. Run `systemctl --user stop llameros-statistics.service` and stop CLI writes.
   Preserve current state for rollback.
2. Verify/decrypt a trusted archive in private staging. Restore its `state`
   directory into `$HOME/.local/share/llameros-statistics`. For a database-only
   restore, replace the database set while stopped; never mix an old WAL/SHM or
   journal with a snapshot from another time.
3. Ensure ownership belongs to your login user, directories are 0700 and
   database/key are 0600. Restore the matching certificate/key pair together.
4. Check integrity and **invalidate restored vouchers** before startup. Restoring
   old redemption flags otherwise makes previously consumed vouchers usable again:

```sh
"$HOME/.local/share/llameros-statistics/venv/bin/python" - <<'PY'
import sqlite3
from pathlib import Path

database = Path.home() / ".local/share/llameros-statistics/state/benchmarks.sqlite3"
with sqlite3.connect(database) as db:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("Restored database integrity check failed")
    db.execute("DELETE FROM vouchers")
PY
```

5. Run `systemctl --user start llameros-statistics.service`, then test HTTPS and
   owner PAM login. Review restored approvals: later-rejected entries might be
   approved in an older snapshot. Issue replacement vouchers. Sessions are
   memory-only and restart invalidates them. A changed host/address may regenerate
   the certificate; update client trust if needed.

Do not restore a newer schema into older code without a tested plan. Legacy v1
records migrate to pending and still need valid evidence for approval.

## 6. Updates, privacy and troubleshooting

Back up state and the package artifact, stop the user service, install tested code
into its virtualenv without sudo, then start and verify. Do not replace code while
serving requests. Unit edits also need `systemctl --user daemon-reload`.

The launcher suppresses access logs and request-context error logging. The user
journal still records lifecycle/startup failures. PAM, OS, firewalls, proxies and
hosting systems may retain security data; review them and crash collectors.
Never log passwords, sessions, vouchers or bodies. Connection IPs remain visible
at the network layer; do not promise complete anonymity.

| Symptom | Check |
| --- | --- |
| Cannot connect to user bus | Log in directly via SSH/desktop as the intended account; do not use `sudo systemctl --user`. |
| Stops after logout | Check lingering for the correct account and that its user unit is enabled. |
| Unit fails immediately | Interpreter/package paths, private user-owned state, `systemctl --user status` and `journalctl --user`. |
| Address already in use | Stop duplicate manual/old system-service processes or change port. |
| Cannot bind at boot | Confirm the IP is ready, fix network readiness and restart. |
| TLS rejected | Check IP SAN, expiration and fingerprint; distribute correct public PEM, never disable verification. |
| PAM fails | Use the user manager's account; check shell, password/account status and host PAM policy. Authorized CLI access remains available. |
| Login throttled | Wait at least one minute; the limit is global without IP/username tracking. |
| No public entries | Approve valid v2 pending evidence first. |
| Database full | Pending/rejected rows count too; back up before cleanup or adjust `--max-entries`. |
| Start limit reached | Fix cause, then `systemctl --user reset-failed llameros-statistics.service` and start again. |

Consult installed `systemd.service(5)`, `systemd.exec(5)` and `loginctl(1)` manuals.
Test actual PAM and boot/logout behavior on the host; syntax verification alone
cannot validate them.