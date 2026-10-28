<p align="center">
  <img src="src/llameros/static/logo.svg" alt="Llameros — a llama carrying a circuit-patterned saddle against Andean peaks" width="160" height="160">
</p>

# Llameros

**Light weight Ollama manger: setup and securely share your local Ollama models.** 

**Usage scenario**: you want to share LLMs running on a **single** local sever/workstation with **multiple** users. *LLameros* is easy to setup and provides a simple web-interface which helps you to serve and mange acess to language models.  

## Key Features:
* easy setup - **no sudo** privileges needed!
* run models securely in rootless Docker
* CPU, multi-GPU and multi-model support
* Hardware / usage monitoring 
* model search, automatic model recommendation, download and management 
* model benchmarking
* multi-user support - authentication via host PEM (e.g. local users, LDAP, ...)
* HTTPS gateway
* web-ui API key-management (fine grain user and model token-limits)
* modern API support (streaming, responses  ...)


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

With a model loaded, create a key in **Key Management → Add key** and copy the
one-time secret. All active users can create **All models** or model-specific keys
without an admin grant. Keys work on all CPU/GPU targets; expiry is optional.
Download the server certificate next to the key-generation controls and verify it
before use.

Install `httpx` (`python -m pip install httpx`; already included in Llameros), then
set your key and verified server certificate or CA bundle in Bash:

```bash
read -rsp 'Llameros API key: ' LLAMEROS_API_KEY; echo
export LLAMEROS_API_KEY
export LLAMEROS_CA_CERT="/absolute/path/to/verified-llameros-certificate.pem"
```

Use the HTTPS address shown under **Access model** and adjust the model tag and
target below to a loaded instance allowed by your key. `X-Llameros-Target` selects
the CPU/GPU instance and is required when the model is loaded on multiple targets.

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

Use the API key, not your Linux password. Keep TLS verification enabled and keys
private; unset `LLAMEROS_API_KEY` after use and revoke unwanted keys in the dashboard.
See [API compatibility](api-compatibility.md) for other endpoints and streaming.

## Documentation

| Guide | Contents |
|---|---|
| [Installation & usage](install.md) | Setup, configuration, storage, model workflow, GPU runtimes, and client access |
| [API compatibility](api-compatibility.md) | Full API ducumentation
| [Troubleshooting](trouble_shooting.md) | Unavailable CPU/GPU slots, container migration, login, downloads, and API issues |
| [Development guide](development.md) | Contributor setup, architecture, documentation conventions, tests, acceptance checks, and security model |
| [Security audit](Security_Audit.md) | Current security findings, validation evidence, limitations, and outstanding deployment checks |


---

MIT licensed. Llameros: the people who manage llama caravans through the Andes.

<p>
  <a href="https://github.com/keuperj/Llameros" target="_blank" rel="noopener noreferrer">View on GitHub ↗</a>
  · <a href="https://www.llm-praxis.de/" target="_blank" rel="noopener noreferrer">maintained by LLM-Praxis</a>
  · <a href="https://www.keuper-labs.org/" target="_blank" rel="noopener noreferrer">developed by Keuper-Labs</a>
</p>
<p align="right">
Supported by
  <img src="src/llameros/static/bmftr.png" alt="Federal Ministry of Research, Technology and Space"  height="160">
  <img src="src/llameros/static/forschung-an-haw.png" alt="Forschung an HAW"  height="160">
</p>
