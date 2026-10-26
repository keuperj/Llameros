<p align="center">
  <img src="src/llameros/static/logo.svg" alt="Llameros — a llama carrying a circuit-patterned saddle against Andean peaks" width="160" height="160">
</p>

# Llameros

**Manage and securely share your local Ollama models.** Your local model caravan
for Linux: discover models, run them on CPU or GPU, create scoped bearer keys for
your clients, and monitor usage—without a cloud account, frontend build, or external
UI assets. Secure sharing is through the local HTTPS gateway, not public hosting.

## Features

- **Per-device runtimes** — multiple resident models per CPU/GPU, isolated NVIDIA/AMD
  DOCKER containers, bounded per-device inference queues, shared downloads, and individual model controls.
- **Hardware-aware discovery** — RAM/VRAM fit estimates, recommendations,
  search, model descriptions, and download progress.
- **Secure model sharing** — named, model-scoped API keys with expiry or no expiry,
  masked one-time copy, deletion, a scrollable key table, and per-key usage history.
- **Token allowances** — admin-set user-wide and per-user model limits, plus owner-set
  key limits, with independent input/output allowances and daily, weekly, monthly, or no reset.
- **Authenticated inference** — scoped, revocable API keys, streaming, usage accounting, and copyable
  Ollama, OpenAI-compatible, LangChain, and LlamaIndex examples.
- **Local control** — Linux PAM sign-in over HTTPS, configurable model storage,
  resource history, lifecycle events, and token/timing totals.

## Dashboard previews

Screenshots of the actual dashboard rendered with synthetic demo data, not a live
deployment. No real accounts or credentials are shown.

### Admin board

Host telemetry, runtime controls, loaded models, and key management.

![Admin board showing host metrics, a loaded model, and API key management](docs/screenshots/admin-board.png)

### User board

Shared running models and resource history, with owner-scoped API keys and usage.

![User board showing a running model, personal API keys, and usage history](docs/screenshots/user-board.png)

## Quick start

Requires **Linux, Python 3.11+, uv, Docker, and PAM**. GPU execution needs compatible
host drivers and container support. Run as your normal user, never sudo.

```bash
# Run from the repository root.
uv sync --locked
uv run llameros
```

Open **https://127.0.0.1:8443**, verify/trust your generated certificate, and sign in
with your Linux account. The account running Llameros defaults to admin; other login accounts default to users. Start Ollama, download a model, select a
free CPU/GPU slot, and load it.

> Rootless Docker is required by default. Runtime APIs are private to the service;
> clients use the authenticated HTTPS gateway. See the installation guide for
> migration of existing containers and explicit rootful risk acceptance.

## Access a model from Python

Load a model in the dashboard, then create a key in **Key Management → Add key**
with permission for that model and device. Copy the one-time secret; use this
bearer key—not your Linux password—in clients. Ordinary users need an admin grant.

Install `httpx` in your client environment (`python -m pip install httpx`; it is
already included in the Llameros environment). Set `LLAMEROS_API_KEY` securely,
for example with a hidden Bash prompt so the key is not saved in shell history:

```bash
read -rsp 'Llameros API key: ' LLAMEROS_API_KEY; echo
export LLAMEROS_API_KEY
export LLAMEROS_CA_CERT="/absolute/path/to/verified-llameros-certificate.pem"
```

Point `LLAMEROS_CA_CERT` to your verified server certificate or issuing CA bundle,
then run this Python example. Change the model tag and target to a loaded instance
allowed by your key; use the HTTPS address shown under **Access model** if remote.

All active users can create generic **All models** keys by default, without an
administrator-issued model grant. These keys cover current and future models;
users can also choose a model-specific key. Expiry is optional for all roles.
Legacy account model grants and grant-expiry dates no longer restrict keys.
Account blocking, key revocation, selected key expiry and usage quotas still apply.

All API keys work on all hardware targets, including existing keys previously
restricted to CPU or a GPU. Key model permissions, expiry,
revocation and quotas still apply. Hardware selection is routing, not a key
permission: specify `X-Llameros-Target` when a model is loaded on multiple targets.
Legacy `targets` fields are accepted for compatibility but do not restrict access.

```python
import os
import ssl

import httpx

tls = ssl.create_default_context(cafile=os.environ["LLAMEROS_CA_CERT"])
with httpx.Client(
    base_url="https://127.0.0.1:8443/inference/",
    verify=tls,
    timeout=180.0,
    headers={
        "Authorization": f"Bearer {os.environ['LLAMEROS_API_KEY']}",
        "X-Llameros-Target": "cpu",
    },
) as client:
    response = client.post(
        "api/chat",
        json={
            "model": "llama3.2:3b",
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "stream": False,
            "options": {"num_predict": 256},
        },
    )
    response.raise_for_status()
    print(response.json()["message"]["content"])
```

Never disable TLS verification, commit keys, or paste them into support reports.
Unset `LLAMEROS_API_KEY` after use and revoke unwanted keys in the dashboard.
See [API compatibility](api-compatibility.md) for other endpoints and streaming.

## Documentation

| Guide | Contents |
|---|---|
| [Installation & usage](install.md) | Setup, configuration, storage, model workflow, GPU runtimes, and client access |
| [Troubleshooting](trouble_shooting.md) | Unavailable CPU/GPU slots, container migration, login, downloads, and API issues |
| [Development guide](development.md) | Contributor setup, architecture, documentation conventions, tests, acceptance checks, and security model |
| [Security audit](Security_Audit.md) | Current security findings, validation evidence, limitations, and outstanding deployment checks |

CPU utilization does not block loading; available memory and slot assignment do.
For **CPU unavailable**, follow the [migration and diagnostic guide](trouble_shooting.md#cpu-shows-unavailable-despite-low-utilization).

---

MIT licensed. Llameros: the people who manage llama caravans through the Andes.

<p>
  <a href="https://github.com/keuperj/Llameros" target="_blank" rel="noopener noreferrer">View on GitHub ↗</a>
  · <a href="https://www.llm-praxis.de/" target="_blank" rel="noopener noreferrer">maintained by LLM-Praxis</a>
</p>
<p align="right">
Supported by
  <img src="src/llameros/static/bmftr.png" alt="Federal Ministry of Research, Technology and Space"  height="160">
  <img src="src/llameros/static/forschung-an-haw.png" alt="Forschung an HAW"  height="160">
</p>
