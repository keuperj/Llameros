# Benchmark statistics server: systemd user service and backups

Run the benchmark statistics server as a **systemd user service under your normal
Linux login account**, not as root. That account is also the `/admin` PAM
administrator. This guide is not for an Ollama runtime or separate backup service.
See `README.md` in this directory for privacy, open uploads and moderation.

## 1. Requirements and installation

Use Linux with systemd, Python **3.11+**, `uv`, system PAM and the dependencies
locked in `uv.lock`. The server needs no Docker, GPU or Ollama installation.
Your unprivileged login account needs a usable PAM password and login shell for
browser administration. Locked, passwordless or `nologin` accounts may fail the
host PAM policy. Never run the server as root to bypass it.

Run all application and service commands **as the account that should sign in to
the UI**, from its SSH/desktop login session, not a root or switch-user shell.
Only missing OS prerequisites and optional lingering authorization may need an
administrator. On Debian/Ubuntu, prerequisites can be installed with:

```sh
sudo apt-get update
sudo apt-get install python3 libpam0g openssl
```

Install `uv` using Astral's official installation instructions or your approved
package manager, and verify `uv --version`. uv manages the project environment.
Check `python3 --version` is at least 3.11. Place the checkout in `Llameros`
relative to your login home. All commands below run from that checkout:

```sh
cd
cd Llameros
```

Install **without sudo**:

```sh
uv sync --locked --no-editable
install -d -m 0700 statistics-state
.venv/bin/python -m statistics_server --help
```

This creates `.venv` and installs the whole project package and assets using
`uv.lock`, but does not start Llameros or Docker. `--locked` rejects stale lockfiles
instead of updating them; `--no-editable` installs a non-editable project package.
For source updates, follow section 6 and explicitly reinstall `llameros` while
the service is stopped; a plain sync is not the update procedure.
Keep the base Python installation available after reboot. The service invokes
`.venv/bin/python` directly, so startup does not require uv on the service's PATH
or synchronize dependencies while handling requests.
Other users must not be able to modify the installation, unit or private state.
Never put Linux passwords in a unit, environment variable or command argument.

## 2. Install the user unit

Stop any manually running server first. Copy the supplied template:

```sh
install -d -m 0700 ../.config/systemd/user
install -m 0600 statistics_server/llameros-statistics.service ../.config/systemd/user/llameros-statistics.service
```

The complete template is:

```ini
[Unit]
Description=Llameros benchmark statistics HTTPS server
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart=sh -c 'cd Llameros && exec .venv/bin/python -m statistics_server --host 127.0.0.1 --port 9443 --state-dir statistics-state --pam-service login --max-entries 100000'
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

The user manager starts in the login home; the shell changes to `Llameros`
before executing the relative interpreter path. If you choose another checkout
location, change that home-relative `cd` target. There is deliberately
**no `User=`/`Group=`**:
the process inherits the user manager's account. Do not install this unit under
the system manager's unit directory, manage it with `sudo systemctl`, or run it in root's user
manager. The UI uses the process's effective owner, not `USER` or `SUDO_USER`.

The template binds **127.0.0.1:9443** for local access. For remote access, change
`--host` to a stable reachable IP and permit the port only from intended networks
in host/provider firewalls. Prefer a protected network/VPN for administration.

```sh
systemd-analyze --user verify ../.config/systemd/user/llameros-statistics.service
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
are not sourced by the service, so it uses a checkout-relative virtualenv interpreter.

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
`statistics-state`. Keep the private key mode 0600;
never distribute it. The database includes private moderation data.

Binding to a specific IP includes it in the generated certificate. For wildcard
binding (`0.0.0.0`) or NAT, supply a certificate with the client-visible IP SAN
using `--cert-file` and `--key-file` with checkout-relative paths. Llameros connects to
literal IPs, so a DNS-only certificate is insufficient for that connection.
Certificate maintenance happens at startup, not continuously: monitor expiration
and plan maintenance restarts. Renewal/name changes can regenerate the self-signed
certificate; update clients' trust whenever its public certificate changes.

Inspect the certificate through a trusted channel:

```sh
openssl x509 -in statistics-state/cert.pem -noout -dates -fingerprint -sha256
curl --cacert statistics-state/cert.pem https://127.0.0.1:9443/api/v1/benchmarks
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

Use `/admin` to monitor capacity/queues and review six-case evidence and warning
flags before approval or rejection. Anyone can upload without credentials; only
approved reports affect public averages/recommendations. Approval means reviewed,
not proven genuine. Clients may cache approved reports for ten minutes. Monitor
queue capacity because open uploads can be abused. The CLI runs as your login user,
without sudo:

```sh
.venv/bin/python -m statistics_server.manage --state-dir statistics-state review
```

See `README.md` for decision commands. CLI authorization relies on OS access to
the state directory rather than a web session.

## 5. Backup and restore

### Full backup with a maintenance window

Preserve state, the user unit and deployed package version/artifact. Stop the
user service **and all CLI writes** before an ordinary file archive. Do not copy
only an actively written SQLite main file.

```sh
install -d -m 0700 backups/statistics
systemctl --user stop llameros-statistics.service
(umask 077; tar -czf "backups/statistics/state-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" statistics-state)
systemctl --user start llameros-statistics.service
```

Check the archive succeeded and test restoration. Copy the user unit separately
after configuration changes. Backups contain private keys and moderation data:
encrypt off-host copies, restrict access and define retention. Same-disk copies
alone are not disaster recovery.

### Online database-only snapshot

Use SQLite's backup API instead of copying a live file:

```sh
install -d -m 0700 backups/statistics
.venv/bin/python - <<'PY'
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

os.umask(0o077)
source_path = Path("statistics-state/benchmarks.sqlite3")
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
destination = Path("backups/statistics") / f"benchmarks-{stamp}.sqlite3"
with sqlite3.connect("file:" + source_path.as_posix() + "?mode=ro", uri=True) as source:
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
2. Verify/decrypt a trusted archive in private staging. Restore its `statistics-state`
   directory into the checkout. For a database-only
   restore, replace the database set while stopped; never mix an old WAL/SHM or
   journal with a snapshot from another time.
3. Ensure ownership belongs to your login user, directories are 0700 and
   database/key are 0600. Restore the matching certificate/key pair together.
4. Check database integrity before startup:

```sh
.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path

database = Path("statistics-state/benchmarks.sqlite3")
with sqlite3.connect(database) as db:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("Restored database integrity check failed")
PY
```

5. Run `systemctl --user start llameros-statistics.service`, then test HTTPS and
   owner PAM login. Review restored approvals: later-rejected entries might be
   approved in an older snapshot. Sessions are
   memory-only and restart invalidates them. A changed host/address may regenerate
   the certificate; update client trust if needed.

Do not restore a newer schema into older code without a tested plan. Legacy v1
records migrate to pending and still need valid evidence for approval.

## 6. Updates, privacy and troubleshooting

### Update an existing installation

Run as the service's login user, **without sudo**. Record the deployed source
revision and preserve its `uv.lock`. Stop the service and any CLI writes, then
back up the state using section 5 before replacing source files:

```sh
systemctl --user stop llameros-statistics.service
cd
cd Llameros
git status --short
git rev-parse HEAD
```

Update the checkout to the intended tested revision using your normal source
deployment procedure. Preserve local changes; do not blindly reset or overwrite
them. From that same checkout, rebuild and reinstall the project:

```sh
uv sync --locked --no-editable --reinstall-package llameros
```

**Continue only if installation succeeds.** `--no-editable` installs a separate
copy of the project in `.venv`. Updating the checkout alone can mix new
`statistics_server` source with an older installed `llameros` package. Explicit
`--reinstall-package llameros` prevents relying on automatic rebuild detection,
including when the project version has not changed. `--locked` preserves the
tested dependency resolution; do not regenerate the lockfile on the server to
work around an update failure.

Verify the imports with the same interpreter used by the service. This does not
start a listener or initialize the database:

```sh
.venv/bin/python - <<'PY'
import llameros.benchmark_exchange as exchange
from statistics_server.app import create_app

assert exchange.PublicUpload
assert callable(create_app)
print("Benchmark exchange loaded from:", exchange.__file__)
print("PublicUpload and statistics server imports: OK")
PY
```

If the import check fails, leave the service stopped and resolve the installation
error first. If you intentionally changed the installed user unit, run
`systemctl --user daemon-reload`; source-only updates do not require it. Do not
overwrite a locally customized unit with the template during routine updates.

Start the service, clearing any previous restart-limit failure, and inspect it:

```sh
systemctl --user reset-failed llameros-statistics.service
systemctl --user start llameros-statistics.service
systemctl --user status llameros-statistics.service --no-pager -l
journalctl --user -u llameros-statistics.service -n 100 --no-pager -o cat
```

Verify HTTPS using the configured certificate trust and test the owner PAM login
at `/admin`, the moderation queue, and the public averages table. Keep the existing
state directory and certificate/key pair; do not delete them to fix import errors.
If rollback is necessary, stop the service, restore the previous source and lockfile,
and reinstall that revision explicitly. Restore database state only under a tested
schema-compatible rollback plan (section 5).

Do not replace code or sync the environment while serving requests. Restarting
invalidates in-memory admin sessions, so administrators must sign in again.

### Privacy and troubleshooting

The launcher suppresses access logs and request-context error logging. The user
journal still records lifecycle/startup failures. PAM, OS, firewalls, proxies and
hosting systems may retain security data; review them and crash collectors.
Never log passwords, sessions or bodies. Connection IPs remain visible
at the network layer; do not promise complete anonymity.

| Symptom | Check |
| --- | --- |
| Cannot connect to user bus | Log in directly via SSH/desktop as the intended account; do not use `sudo systemctl --user`. |
| Stops after logout | Check lingering for the correct account and that its user unit is enabled. |
| Unit fails immediately | Interpreter/package paths, private user-owned state, `systemctl --user status` and `journalctl --user`. |
| `ImportError: cannot import name 'PublicUpload'` from `site-packages/llameros` | Checkout and installed package are out of sync. Stop the service, run `uv sync --locked --no-editable --reinstall-package llameros` from the updated checkout, verify imports, then reset the failed state and start as above. No database or bind-address change is needed for this error. |
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