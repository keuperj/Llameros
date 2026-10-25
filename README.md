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
  containers, bounded per-device inference queues, shared downloads, and individual **■ Stop model** controls.
- **Hardware-aware discovery** — RAM/VRAM fit estimates, recommendations,
  search, model descriptions, and download progress.
- **Secure model sharing** — named, model-scoped API keys with expiry or no expiry,
  masked one-time copy, deletion, a scrollable key table, and per-key usage history.
- **Authenticated inference** — scoped, revocable API keys, streaming, usage accounting, and copyable
  Ollama, OpenAI-compatible, LangChain, and LlamaIndex examples.
- **Local control** — Linux PAM sign-in over HTTPS, configurable model storage,
  resource history, lifecycle events, and token/timing totals.

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
