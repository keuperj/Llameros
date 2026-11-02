# Public benchmark statistics server

This is a separate Python HTTPS service using the same installed dependencies as
Llameros (FastAPI, Uvicorn, cryptography) and Python's built-in SQLite database.
No Docker, Redis, external database, third-party JavaScript, analytics, cookies,
contributor accounts or telemetry are required. The optional operator UI uses the
host account running the service and its PAM password.

## Setup

For the complete installation, **systemd service configuration**, TLS verification,
operations and **backup/restore** procedures, see `DEPLOYMENT.md` in this directory.
A ready-to-copy system service template is provided as
`llameros-statistics.service`; review its paths/account and bind IP before use.

From the repository, install the project into a virtual environment and run:

```sh
python -m pip install .
python -m statistics_server --host 192.0.2.10 --port 9443 --state-dir /srv/llameros-statistics
```

Replace the example IP with the server's actual reachable IP. The process must
own its state directory. Run as an unprivileged dedicated service account, not
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
openssl x509 -in /srv/llameros-statistics/cert.pem -noout -fingerprint -sha256
```

Never distribute `key.pem`. On each Llameros instance, in admin Configuration →
Community benchmark server, enter the IP/port, paste the public certificate PEM,
enable community benchmarks and save. Verification remains mandatory; an empty
certificate field uses the system trust store. Open the HTTPS server address in
a browser to see its sortable, paginated-fetch public table. Trust the public
certificate in the browser after verifying its fingerprint.

## Sharing and recommendations

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
at most 10 entries and requires a single-use upload voucher from the operator. The displayed payload is checked against current results
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
There is no per-client tracking. Entry records contain only the public payload, its content hash, moderation status and fixed review flags. Voucher records store only a secret hash, expiry, entry allowance and a redeemed boolean; there is no voucher-to-entry relationship.
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
attacker can still submit plausible false data; keep voucher distribution limited
and review suspicious reports. There is no automatic approval or identity-based
reputation. Coarse CPU classes cannot distinguish processor generations. Check
local fit before downloading.

Only approved records are returned by the public API and website, so pending or
rejected records cannot affect model search. Medians and min/max ranges are shown
for distinct throughput values, not unique installations. Grouping separates
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

- Pending/approved/rejected counts, database capacity and aggregate voucher counts.
- Status-filtered, paginated benchmark review with full six-case evidence, model
  digest, hardware/settings and warning flags; refreshes every 30 seconds.
- **Allow upload requests:** issue a single-use voucher with a bounded entry
  allowance and expiry, then privately deliver it to a contributor. There is no
  public request/contact form that collects contributor identity. This authorizes
  a push; it does not approve any pushed result.
- Explicit **Approve / publish** and **Reject / unpublish** confirmations. Stale
  decisions are rejected if another reviewer/CLI changed the entry's status.
  Approval validates v2 evidence again; legacy evidence remains blocked.
- Sign out and automatic 30-minute session expiry. Sessions, CSRF tokens and
  credentials are never written to SQLite. Session identity is checked against
  current host account policy on every protected request.

The new voucher is returned once and shown in a password field; it can be revealed
and selected/copied manually. Clear it after delivery. It clears automatically
after five minutes, on sign-out, or when leaving the page. No clipboard or browser
storage API is used. Existing voucher secrets cannot be retrieved. A lost creation
response can leave an unused voucher until expiry; do not issue excess vouchers.

Admin APIs live under `/admin/api/` and require the owner session. Writes require
same-origin HTTPS, a CSRF token, and explicit action confirmation. Cookies are
Secure, HttpOnly, SameSite=Strict and host-only with a `__Host-` prefix. Login has
one global five-attempt-per-minute limit and at most four concurrent PAM calls,
without source-IP or submitted-username tracking. The admin request budget is
separate from public browsing/uploads, although an attacker can still target the
admin endpoint's global budget. No security mechanism here guarantees DDoS
resistance. The application logs no passwords, vouchers, sessions or identities.
There is no cross-origin API access or trusted forwarded-identity header.

## Operator: vouchers and moderation

Use the owner-only web UI at `/admin`, or run these commands locally on the
statistics host, as the service account, using the same absolute state directory
as the running service. The CLI remains available for offline operations.

Issue one voucher (default 10 entries, expires in 72 hours):

```sh
python -m statistics_server.manage --state-dir /srv/llameros-statistics issue-voucher
python -m statistics_server.manage --state-dir /srv/llameros-statistics issue-voucher --max-entries 3 --hours 24
```

The secret is printed once; deliver it through a private channel. Do not record
recipient identities alongside tokens, paste tokens into URLs, or keep terminal
recordings. Issuance allows 1–10 entries and 1–720 hours. Only a SHA-256 hash is
stored. Expired hashes are purged on subsequent issuance. No voucher redemption
time, client identity or benchmark reference is stored. Ordinary vouchers are
**not cryptographically unlinkable**: an operator retaining issuance records or
observing requests can correlate them. Do not claim Privacy Pass anonymity.

The Llameros admin pastes the voucher into the password field in Benchmarks, then
selects **Share benchmarks** and confirms the exact public payload and destination.
The confirmation explains that the secret voucher is also sent over verified
HTTPS. It is never included in the public preview, settings file or entry records.
The field clears on submission/session reset. A voucher permits one successful
batch only, including duplicate-only batches. Failed validation, over-limit or
capacity-rejected requests do not redeem it. Redemption and insertion are atomic,
so simultaneous reuse cannot admit two batches. If a successful response is lost,
check the moderation queue; do not retry the used voucher.

Review the private moderation queue (default first 100 records; up to 1,000 with
`--limit`). The JSON includes the public-payload digest needed for moderation:

```sh
python -m statistics_server.manage --state-dir /srv/llameros-statistics review
python -m statistics_server.manage --state-dir /srv/llameros-statistics review --status approved --limit 1000
python -m statistics_server.manage --state-dir /srv/llameros-statistics approve PAYLOAD_DIGEST
python -m statistics_server.manage --state-dir /srv/llameros-statistics reject PAYLOAD_DIGEST
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
fields are rejected. High throughput, missing artifact digest, or a >3× difference
from comparable approved medians produces fixed review flags, never automatic
approval. There is intentionally no hardware-dependent hard performance ceiling:
new hardware may legitimately be faster.

Legacy databases migrate in place: existing reports become pending, never
implicitly approved. Legacy v1 reports without case evidence cannot be approved;
rerun/resubmit them with v2 evidence. Missing artifact digests in otherwise valid
v2 submissions are reviewable but explicitly flagged. Model digest collection is
best-effort from local runtime inventory and is still a client claim.

## API

- `POST /api/v1/benchmarks`: `{ "entries": [ ... ], "voucher": "SECRET" }`; returns counts of new and
  duplicate records and `status: "pending"`. Unknown fields are rejected.
- `GET /api/v1/benchmarks?offset=0&limit=200`: approved public entries and total count;
  maximum page size 1,000.
- `/`: sortable public table, with all rows fetched in bounded pages. No remote
  scripts or tracking requests.

For development, run `pytest -q` and
`node --test tests/*.test.cjs` from the repository root.