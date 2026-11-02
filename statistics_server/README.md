# Public benchmark statistics server

This is a separate Python HTTPS service using the same installed dependencies as
Llameros (FastAPI, Uvicorn, cryptography) and Python's built-in SQLite database.
No Docker, Redis, external database, third-party JavaScript, analytics, cookies,
contributor accounts or telemetry are required. The optional operator UI uses the
host account running the service and its PAM password.

## Setup

For unattended six-case benchmarking across discoverable compatible Ollama models,
see `docs/fullbenchmark.md` from the repository root and `RunFullbenchmark.py`.
The runner skips stress tests and requires explicit consent for uploads.

For the complete installation, **systemd user service configuration**, TLS verification,
operations and **backup/restore** procedures, see `DEPLOYMENT.md` in this directory.
A ready-to-copy systemd **user service** template is provided as
`llameros-statistics.service`; install it under your login user's user manager
and review its paths and bind IP before use. That login user is the PAM UI owner.

From the repository root, use uv to install the locked environment and run.
All file paths below are relative to that root:

```sh
uv sync --locked --no-editable
uv run --no-sync python -m statistics_server --host 192.0.2.10 --port 9443 --state-dir statistics-state
```

Replace the example IP with the server's actual reachable IP. The process must
own its state directory. Run as your unprivileged login account, not
root. The bind IP and port are configurable (`--help` lists all options).
Binding directly to the reachable IP ensures the generated certificate contains
that IP. When binding to `0.0.0.0`, supply a certificate with the actual reachable
IP in its SAN via `--cert-file` and `--key-file` instead. Key files must be mode 600.

At first startup a self-signed certificate and private key are generated in the
state directory, alongside `benchmarks.sqlite3`. Existing certificates are reused
and renewed near expiry by Llameros' TLS helper. Back up the database and protect
the key. Renewal of a self-signed certificate requires updating client trust.

Obtain the **public** `cert.pem` via a trusted channel. Compare its fingerprint:

```sh
openssl x509 -in statistics-state/cert.pem -noout -fingerprint -sha256
```

Never distribute `key.pem`. On each Llameros instance, in admin Configuration →
Community benchmark server, enter the IP/port, paste the public certificate PEM,
enable community benchmarks and save. Verification remains mandatory; an empty
certificate field uses the system trust store. Open the HTTPS server address in
a browser to see its sortable, paginated-fetch public table. Trust the public
certificate in the browser after verifying its fingerprint.

### Sharing with a self-signed benchmark server

In the Llameros admin dashboard, open **Configuration → Community benchmark
server**, enter the destination IP/port and select **Retrieve self-signed server
certificate**. Compare the displayed SHA-256 fingerprint with the operator's
`openssl x509` output above through a separate trusted channel before accepting.
Retrieval only inspects TLS; it sends no HTTP request or benchmarks and does not
automatically save trust. Select **Save benchmark server settings**, with community
benchmarks enabled, then use **Share benchmarks** and confirm the payload separately.

Alternatively, paste the verified public PEM directly into the certificate field.
The `RunFullbenchmark.py` runner automatically retrieves and saves a first-use
trust anchor in its run directory when `--ca-cert` is omitted, so no certificate
download is required. This TOFU mode cannot independently authenticate the first
connection; `--confirm-share` includes consent to that limitation. For verified
trust from the first connection, save the public PEM locally and pass
`--ca-cert certificates/statistics-cert.pem`. Saved trust is reused on resume, not
silently refreshed. Both upload paths support trusted self-signed certificates,
including older generated certificates on newer Python versions. TLS signature, expiry and destination IP checks remain enabled;
there is no insecure-upload fallback. Expired or wrong-IP certificates must be
fixed by the server operator. Reconfirm trust when the server certificate changes.

## Updating an existing server

Updating the checkout is not enough: deployment uses a **non-editable** installed
`llameros` package. New `statistics_server` source can otherwise import stale code
from `.venv/.../site-packages/llameros`, causing errors such as
`ImportError: cannot import name 'PublicUpload'`.

As the service's login user, without sudo:

1. Stop the service and CLI writes, record the current source revision and lockfile,
   and back up private state using `DEPLOYMENT.md`.
2. Update the checkout to the intended tested revision, preserving local changes.
3. From the repository root, explicitly reinstall and verify imports:

   ```sh
   uv sync --locked --no-editable --reinstall-package llameros
   .venv/bin/python -c 'from llameros.benchmark_exchange import PublicUpload; from statistics_server.app import create_app; print("Statistics server imports: OK")'
   ```

   Run the import check only if installation succeeded. If either command fails,
   leave the service stopped and resolve the error before continuing.
4. Reload systemd only if you changed the installed unit, then start and inspect:

   ```sh
   systemctl --user reset-failed llameros-statistics.service
   systemctl --user start llameros-statistics.service
   systemctl --user status llameros-statistics.service --no-pager -l
   journalctl --user -u llameros-statistics.service -n 100 --no-pager -o cat
   ```

For step 1, stop with `systemctl --user stop llameros-statistics.service`.
For unit changes in step 4, run `systemctl --user daemon-reload` before starting.
Verify HTTPS, `/admin` login, the moderation queue and public averages afterward.
Keep the existing database and TLS files; an import mismatch does not require
deleting state or changing the listening address. Do not update source or sync the
environment while the service is running. See section 6 of `DEPLOYMENT.md` for the
complete procedure, rollback precautions and troubleshooting.

## Sharing and recommendations

The dashboard and full-benchmark runner default to `92.205.18.100:9443`.
Existing saved destinations are preserved; sharing remains disabled by default.
This client destination does not change the service's local bind address.

The integration defaults **off**; no statistics requests occur while disabled.
Enabling allows public benchmark downloads during model search. Matching is
performed locally against coarse hardware, context, parallelism, Flash Attention
and cache precision; the hardware profile and search text are **not** sent as
recommendation queries. Downloads are cached for ten minutes. Local popularity
and fit suggestions remain available if the community server fails.

Uploads are never automatic. In admin Benchmarks select **Share benchmarks**,
review the exact JSON and destination, and explicitly confirm. Only completed
six-case benchmarks with hardware snapshots and resolvable official-library
names are eligible. Re-run older benchmarks to create snapshots. Private aliases,
failed/partial results and stress-test details are excluded. Each action handles
at most 10 entries without contributor credentials. The displayed payload is checked against current results
again at submission. Model-name verification contacts the existing official
catalogue, just like normal model discovery.

Stored public fields:

- public model family/tag, optional public model artifact digest, workload kind and benchmark protocol;
- architecture class, rounded-up core/RAM/accelerator-memory buckets;
- recognized accelerator product class (unknown products fall back to vendor),
  and unified-memory flag;
- context length, parallel setting, Flash Attention, KV-cache precision;
- six standard case names, token counts and durations (rounded to microseconds), timing methods, and throughput recomputed and rounded to one decimal.

No username, instance ID, hostname, CPU name, IP address, GPU UUID, device path,
serial number, precise memory reading, benchmark timestamp, prompt, response,
API key, runtime address, local file path or raw error is stored in the database.
Content hashes deduplicate identical public payloads; they are not client IDs.
There is no per-client tracking. Entry records contain only the public payload, its content hash, moderation status and review flags.
Invalid request responses do not echo submitted values. The application writes
no access logs; the launcher also suppresses Uvicorn request/error logs that may
contain connection context. Monitor service health without storing request data.

### Privacy limitations

**This is data minimization, not a guarantee of anonymity.** Connection IPs are
necessarily visible to the receiving network/server; rare hardware/settings
combinations and public model preferences can be distinctive. Any reverse proxy,
firewall, load balancer, hosting provider or OS audit facility must be configured
separately not to retain identifying access data. Do not place analytics on the
public site. Do not share results you are unwilling to publish. There is no
per-user deletion mechanism because no owner identifier is collected; an operator
can remove rows administratively or clear the database. Disabling the integration
does not retract already published records.

### Reliability and abuse limits

Results are client-reported measurements, not quality scores or independent-user
counts. **Approval means reviewed, not proven genuine.** The server cannot prove
that reported hardware, model digest or measurements are truthful. An approved
attacker can still submit plausible false data; review open submissions carefully
and review suspicious reports. There is no automatic approval or identity-based
reputation. Coarse CPU classes cannot distinguish processor generations. Check
local fit before downloading.

Only approved records are returned by the public API and website, so pending or
rejected records cannot affect model search. Arithmetic averages and min/max ranges are shown
for distinct approved reports, not unique installations. Grouping separates
hardware, artifact digest (including an explicit unknown group), workload,
settings and timing method. Local recommendation caches expire after ten minutes;
rejecting a previously approved entry may take that long to disappear on clients.

Requests have bounded bodies, a 10-entry upload limit, strict field validation,
bounded result pages and a database cap (`--max-entries`, default 100,000, including
pending and rejected rows). Independent global budgets allow 20 upload attempts
and 120 read requests per minute. Upload exhaustion does not consume the browsing
budget. There are no IP-keyed counters. Run one worker so these budgets apply to
all traffic. The launcher limits concurrent connections. These are lightweight
safeguards, not comprehensive Internet DDoS protection: connections and network
bandwidth are still shared. A concurrent approval can change pagination order;
refresh to obtain a newer view.

## Owner-only administration website

Open `/admin` over HTTPS and sign in with the **Linux username and PAM password
of the effective user running the statistics server**. The identity is captured
from the process effective UID at startup; `USER`, `SUDO_USER`, submitted usernames,
and other valid PAM accounts cannot grant access. If you use `sudo` to run the
service as another user, that effective user is the administrator, not the
original shell user. Run as an unprivileged account. There is no initial web
password, separate admin database, password reset or additional web administrator.

The default PAM service is `login`; configure a host-specific policy with
`--pam-service SERVICE`. The account needs a valid login shell, a usable password,
and a PAM configuration that permits the unprivileged service to authenticate it.
Locked, disabled, passwordless or `nologin` service accounts may be unable to use
the UI. Do not make the process root or weaken PAM merely to enable web login;
choose an appropriate host policy/account or continue using the local CLI.
PAM/system authentication facilities may retain their own security logs; these are
outside the benchmark application and should be reviewed against your privacy
policy. No contributor identity is introduced by the operator login.

The dashboard provides:

- Pending/approved/rejected counts, database capacity.
- Status-filtered, paginated benchmark review with full six-case evidence, model
  digest, hardware/settings and warning flags; refreshes every 30 seconds.

- Explicit **Approve / publish** and **Reject / unpublish** confirmations. Stale
  decisions are rejected if another reviewer/CLI changed the entry's status.
  Approval validates v2 evidence again; legacy evidence remains blocked.
- Sign out and automatic 30-minute session expiry. Sessions, CSRF tokens and
  credentials are never written to SQLite. Session identity is checked against
  current host account policy on every protected request.

Admin APIs live under `/admin/api/` and require the owner session. Writes require
same-origin HTTPS, a CSRF token, and explicit action confirmation. Cookies are
Secure, HttpOnly, SameSite=Strict and host-only with a `__Host-` prefix. Login has
one global five-attempt-per-minute limit and at most four concurrent PAM calls,
without source-IP or submitted-username tracking. The admin request budget is
separate from public browsing/uploads, although an attacker can still target the
admin endpoint's global budget. No security mechanism here guarantees DDoS
resistance. The application logs no passwords, sessions or identities.
There is no cross-origin API access or trusted forwarded-identity header.

## Operator: open uploads and moderation

Use the owner-only web UI at `/admin`, or run these commands locally on the
statistics host, as the service account, using the same absolute state directory
as the running service. The CLI remains available for offline operations.

Anyone can submit up to ten valid reports per request, without credentials.
Reports remain private and pending until an administrator approves them. Exact
payload duplicates are ignored, including reports already rejected. Global upload
rate limits, request size limits, and database capacity still apply. Open admission
can be abused to fill the queue; monitor capacity and review submissions regularly.

Review the private moderation queue (default first 100 records; up to 1,000 with
`--limit`). The JSON includes the public-payload digest needed for moderation:

```sh
python -m statistics_server.manage --state-dir statistics-state review
python -m statistics_server.manage --state-dir statistics-state review --status approved --limit 1000
python -m statistics_server.manage --state-dir statistics-state approve PAYLOAD_DIGEST
python -m statistics_server.manage --state-dir statistics-state reject PAYLOAD_DIGEST
```

Replace `PAYLOAD_DIGEST` with a digest from review output. Rejection also removes a
previously approved entry from the public API. No free-text moderator names or
notes are recorded. Rejected hashes remain for deduplication; repeated submissions
cannot silently reopen them. Back up the database before any administrative SQL
cleanup. The cap includes rejected entries; review and clean up capacity offline
when appropriate.

Every new record starts **pending**, private and excluded from recommendations.
The server validates six ordered standard cases, token limits, finite positive
durations, wall/evaluation timing consistency and recomputes aggregate throughput.
It rejects mismatching totals rather than accepting a claimed rate. All unknown
fields are rejected. High throughput, missing artifact digest, or a difference greater than 50%
from the comparable approved arithmetic average produces review flags, never automatic
approval. There is intentionally no hardware-dependent hard performance ceiling:
new hardware may legitimately be faster.

Legacy databases migrate in place: existing reports become pending, never
implicitly approved. Legacy v1 reports without case evidence cannot be approved;
rerun/resubmit them with v2 evidence. Missing artifact digests in otherwise valid
v2 submissions are reviewable but explicitly flagged. Model digest collection is
best-effort from local runtime inventory and is still a client claim.

## API

- `POST /api/v1/benchmarks`: `{ "entries": [ ... ] }`; returns counts of new and
  duplicate records and `status: "pending"`. Unknown fields are rejected.
- `GET /api/v1/benchmarks?offset=0&limit=200`: approved public entries and total count;
  maximum page size 1,000.
- `GET /api/v1/averages?offset=0&limit=200`: arithmetic averages, sample counts and
  min/max of approved reports, grouped by matching hardware, artifact, workload,
  settings and timing. Recomputed from originals after confirmation or rejection.
  The raw benchmark endpoint remains available for six-case evidence and clients.
- `/`: sortable public averages table, with all rows fetched in bounded pages. No remote
  scripts or tracking requests.

For development, run `pytest -q` and
`node --test tests/*.test.cjs` from the repository root.

Review warnings are refreshed against current approvals, including for reports
submitted before a baseline existed. A warning never auto-rejects a report.
Original six-case evidence remains unchanged. Samples are not independent users.
Legacy voucher hashes are discarded on initialization; entries are preserved.
