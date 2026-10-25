# Llameros — independent current-state security audit by GPT-6 Astra

## Scope and evidence

This report supersedes the earlier audit and its implementation follow-ups. Findings
were derived again from current source and fresh probes, not carried forward as
established facts. This is an independent review pass, not third-party certification.

Review context date: **October 24, 2026**. The execution host reported
**October 25, 2026, 10:04:57 +01:00**; evidence timestamps follow that host clock.
This discrepancy is recorded rather than silently normalizing the evidence date.

Baseline commit: `7fdc5bf8d5b7593fbb37832462456e45aac9da62`, **plus the uncommitted
working tree**. The commit alone does not reproduce the audited implementation.
Paths below are repository-relative to keep deployment documentation portable.

Reviewed: CLI/TLS, ASGI middleware, sessions/PAM/account policy, key ownership and
entitlements, inference admission/queue/streaming, Docker invocation and private
transport, model/state filesystem validation, media/protocol validators, browser
lifecycle, telemetry, dependency tooling, CI and deployment instructions.

Threat model and limits:

- VPN members and authenticated ordinary users are not automatically trusted.
- Root, the service UID, administrators and the selected local Docker daemon are
  trusted; a rootful-daemon override expands the host privilege boundary.
- VPN/firewall restrictions and trusted HTTPS certificates are deployment
  prerequisites, not controls proven by this review.
- Aggregate budgets/fairness/quotas remain deferred by operator request.
- Additional Host/header/HSTS hardening (previous point 13) remains deliberately
  excluded. Malformed-input handling in existing code is still assessed.
- No live containers, GPU jobs, destructive load tests or production login attempts
  were run. No scanner was installed and no deployment was changed.

## Executive assessment

**Passing tests are not security clearance.** The current source has meaningful
isolation and credential controls, but fresh checks found two medium-severity
weaknesses and two low-severity input-validation gaps. Aggregate resource exhaustion
remains a medium-severity accepted/deferred risk. Image/native-component
vulnerability assurance is incomplete.

No critical vulnerability, remote code execution, or unauthenticated inference
bypass was demonstrated. This is a statement about evidence, not a guarantee.

| ID | Severity | Finding | Evidence / disposition |
| --- | --- | --- | --- |
| RA-01 | Medium | Browser presents logout despite failed server invalidation | UI suppression and surviving server session reproduced separately; open |
| RA-02 | Medium, conditional | Model-store descendants need not have trusted ownership/permissions | Writable descendant accepted; cross-account exploit not executed; open |
| RA-03 | Medium | Aggregate admission/execution limits remain absent | Source-confirmed; operator-deferred budgets |
| RA-04 | Low | Malformed Origin can raise an unhandled middleware exception | HTTP 500 reproduced without authentication; open |
| RA-05 | Low | Protocol admission forwards malformed format envelopes | Mock upstream receipt reproduced; incomplete defense in depth |
| RA-06 | Assurance gap | Runtime images and host/native components lack current scan clearance | Scout unavailable; Python-only lookup is not full clearance |

## Findings

### RA-01 — Failed logout is silently presented as successful logout

**Evidence:** `src/llameros/static/app.js:439` initiates logout, calls `signedOut()`
immediately, and suppresses every failure. Local view reset does not clear the
HttpOnly cookie. Server invalidation and cookie deletion occur in the `/api/logout`
handler in `src/llameros/app.py`.

**Fresh reproduction:** a Node VM executed the actual logout handler with a rejected
network promise: `signedOut=true`, `failureReported=false`. Separately, an isolated
TestClient login followed by a rejected logout returned 403; a subsequent session
request with the retained cookie returned 200. These establish the two components
without claiming a real-browser network-interruption test.

**Impact:** on a shared browser, a user may believe access was revoked when reloading
can restore the previous account. A VPN does not prevent this. Immediate local
secret clearing is good and should remain.

**Recommendation:** separate local clearing from confirmed server logout. Display an
invalidation-failed warning, offer retry, and do not silently auto-restore after
failed logout. Test offline rejection, CSRF rejection, delayed logout responses,
reload and subsequent login. UI suppression alone is not revocation.

### RA-02 — Trusted ancestry does not establish a trusted model-store tree

**Evidence:** `src/llameros/filesystem.py` validates ancestors and the selected
directory using no-follow descriptors. `src/llameros/model_storage.py:76–79` checks
descendants only for symbolic links, not ownership or write modes. Existing model
directories can be 0755, with group/other-writable descendants. `scan_models()`
subsequently opens pathnames, while Docker bind-mounts the selected pathname.

**Fresh reproduction:** validation accepted an owner-controlled model directory
with a `blobs` child explicitly chmodded 0777. This used temporary state, not
production models. No second account or racing mount was used.

**Preconditions and impact:** another local user must be able to traverse ancestors
and mutate a descendant. That user could replace blobs/manifests or introduce links
after the walk, changing subsequent reads/loading. A private 0700 ancestor prevents
this cross-account path. No arbitrary host read or code execution was demonstrated;
a compromised service UID is already trusted.

**Recommendation:** at storage admission/startup reject untrusted owners and shared-
writable descendants, or require a genuinely private tree with an explicit import
workflow. Prefer no-follow relative manifest/blob opens. Test writable descendants,
foreign ownership and substitution between validation and use. Do not hash multi-GB
weights on inference requests.

### RA-03 — Per-request limits do not provide aggregate availability protection

**Evidence:** `src/llameros/body_limits.py` buffers and JSON-parses bodies before
route authentication (default inference limit 16 MiB, 30-second read deadline).
The gateway parses/serializes again and validates media before scheduler admission.
`src/llameros/scheduling.py` bounds waiting requests per target to 16, but tickets
have no owner/fairness budget. Docker run arguments limit PIDs, not memory or CPU.
Gateway response handling uses `aread()` or line buffering without an explicit
aggregate response-byte cap.

`src/llameros/gateway.py` uses a 600-second HTTPX timeout, not an absolute execution
deadline. HTTPX documents read timeout as time waiting for a chunk, so a continuing
stream can exceed that duration [R1]. Valid-key discovery also performs account and
runtime checks without a separate request budget.

**Impact:** VPN peers can overlap unauthenticated body processing; credentialed users
can monopolize queues or consume host/native-runtime resources. No stress test or
exhaustion threshold was measured; severity is architecture-based.

**Disposition:** explicitly accepted/deferred, not fixed. Future work should add cheap
pre-body/global admission, owner fairness, byte/concurrency budgets, absolute execution
deadlines and measured container limits. VPN restrictions reduce exposure, not insider risk.

### RA-04 — Malformed Origin causes HTTP 500

**Evidence:** `src/llameros/app.py:233–238` calls `urlsplit(origin)` without catching
parsing exceptions in middleware. A synthetic HTTPS login request with
`Origin: https://[` returned **500 Internal Server Error**, without authentication.

**Impact:** avoidable error/log pressure and brittle middleware behavior. No CSRF/auth
bypass or service-wide crash was demonstrated. This is an input robustness defect,
not a DNS-rebinding claim, and is distinct from skipped Host/header hardening.

**Recommendation:** parse once, catch malformed-origin errors, return 400/403 and add
malformed bracket/IPv6/port fixtures without exposing exception text.

### RA-05 — Cheap nested protocol checks are still incomplete

**Evidence:** `src/llameros/protocol.py` checks message/tool envelopes and sampling
ranges, but generally checks only dictionary shape for format/reasoning structures.
Native `format` contents lack comparable envelope validation.

**Fresh reproduction:** valid-key requests containing native `format: {type: []}`
and OpenAI `response_format: {type: 123}` reached a mock upstream and received its
200 response. A legitimate JSON Schema union type was also accepted; a blanket ban
on arrays named `type` would be the wrong fix. Mock acceptance does not prove that
the real runtime accepts or exploits these inputs.

**Impact:** malformed requests unnecessarily reach native parsing, and error behavior
depends on upstream handling. No parser exploit was demonstrated.

**Recommendation:** endpoint-aware checks for known envelopes and content block
discriminators. Preserve bounded JSON Schema semantics; do not resolve external
references, compile complex schemas or fully decode images on the hot path.
Header-only raster inspection and client-side tool-call validation remain documented
trade-offs, not full media/schema validation.

### RA-06 — Dependency lookup is not runtime/host vulnerability clearance

Fresh execution of `scripts/check_security.py` queried **29 locked Python registry
packages**, returning **no matching advisory IDs**. It exited **2**, correctly marking
both runtime images `not-scanned` and `complete=false`. OSV queries package versions
for known advisory matches [R2]; an empty match is not proof of absence of flaws.
Installed deployment versions were not reconciled against the lockfile, and the
documented unlocked pip fallback can differ.

`docker scout version` reported an unavailable command. No scanner was installed,
images pulled or host advisories reconciled. Digest pinning establishes identity,
not patch status. CI explicitly accepts the Python-only exit code; it does not scan
images or constitute a kernel/Docker/PAM/TLS/GPU release gate. Hosted CI execution
was not observed.

**Recommendation:** retain an operator release gate for both exact image digests,
installed Python versions and host inventory, with dated evidence. Missing scans
must not count as passes.

## Controls verified in source and offline tests

- HTTPS enforcement and CLI TLS setup; CLI proxy headers disabled; secure HttpOnly
  SameSite=Strict host cookie; server-side roles and CSRF checks.
- Source/account login throttling, bounded limiter state and four password/PAM slots.
  Live PAM/NSS behavior and latency were not comprehensively verified.
- High-entropy hashed API keys, owner-scoped ordinary-user management, explicit
  model/target grants, expiry and blocked-account checks. Queued work reauthenticates;
  already executing work may finish. Password-only Linux locks are not universal
  bearer revocation.
- Actual-byte body limits, lexical JSON-depth checks and read deadlines. These are
  per-request protections and do not resolve RA-03.
- New runtime arguments publish no ports, bind Ollama to container loopback, drop
  capabilities, use no-new-privileges and pin images. Rootless Docker is the default
  requirement. Legacy network/image configurations are rejected before runtime use.
- Private Unix sockets live in temporary private directories. Docker exec uses a
  fixed relay rather than user-interpolated shell code. Live CPU/GPU compatibility,
  cancellation and cross-account isolation remain unverified.
- State/model ancestry uses no-follow descriptors; RA-02 concerns descendants.
- Browser epoch guards discard delayed API responses after local account/board
  changes; reset routines clear prompts/results/secrets. RA-01 is a separate
  server-invalidation problem. Cross-tab changes are polling-based, not immediate
  erasure of already displayed information.
- Operational error responses and job-failure persistence use content-independent
  diagnostics. This is not a universal claim about every driver/progress/log source.
- Telemetry row retention is bounded. Shared resident-model metadata, model totals
  and CPU/RAM history are explicit policy, not private per-user telemetry. Admin
  playground job results are global admin-visible state, not personal conversations.
- PDF parsing is absent. Inline images use bounded header inspection, not a full
  decoder. Remote image fetching is not implemented. Tool calls are not executed.

## Fresh validation

| Check | Observed result |
| --- | --- |
| Python tests | 223 passed; one Starlette/httpx deprecation warning |
| Node tests | 43 passed |
| Ruff lint / format | Passed; 44 files formatted |
| Git whitespace check | Passed |
| Focused HTTP probes | RA-04 500; RA-05 forwarded; rejected logout leaves session valid |
| Filesystem probe | 0777 blobs descendant accepted |
| Node logout probe | Local signed-out state with no reported network failure |
| Locked Python OSV lookup | 29 packages; no advisory IDs; exit 2, overall assurance incomplete |
| Docker Scout | Unavailable; no image scan |

Fresh evidence is retained locally under `/tmp/llameros-audit-*`: test logs,
security JSON, probe scripts and JSON outputs. These are ephemeral, not durable
release evidence; material observations are recorded here. Focused probes use
fake authentication, temporary state and mock upstreams, not real credentials/models.

Not verified: second-account filesystem attack, automated real browser behavior,
long-duration load/fuzzing, live images/GPU, VPN/firewall policy, existing container
stopped state, host patch inventory or deployed-package attestation. Existing tests
passing does not cover these missing checks.

## Next decisions

1. Fix RA-01 and RA-02 with regressions for the actual failure cases.
2. Fix RA-04 and inexpensive endpoint-aware checks in RA-05.
3. Keep RA-03 explicitly accepted until the deferred budgets are authorized.
4. Complete RA-06 and deployment isolation/PAM/VPN verification before clearance.
5. Preserve skipped Host/header/HSTS policy; reconsider if exposure changes. Never
   recommend disabling TLS verification.

This task changes documentation/evidence only, not application behavior or services.
No commit or deployment was made. Historical intermediate implementation evidence
is superseded, not interpreted as fresh proof.

## External primary references

- [R1] HTTPX timeout semantics: `https://www.python-httpx.org/advanced/timeouts/`
- [R2] OSV API semantics: `https://google.github.io/osv.dev/api/`

References clarify library/advisory semantics. Findings are grounded in local
source and explicit fresh observations, not generic security advice.
