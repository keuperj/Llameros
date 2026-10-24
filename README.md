<p align="center">
  <img src="src/llameros/static/logo.svg" alt="Llameros — a llama carrying a circuit-patterned saddle against Andean peaks" width="160" height="160">
</p>

# Llameros

**Your local model caravan.** A local-first Ollama dashboard for Linux.
Discover models, run them on CPU or GPU, and monitor usage—without a cloud
account, frontend build, or external UI assets.

## Features

- **Per-device runtimes** — one model per CPU/GPU slot, isolated NVIDIA/AMD
  containers, shared downloads, and individual **■ Stop model** controls.
- **Hardware-aware discovery** — RAM/VRAM fit estimates, recommendations,
  search, model descriptions, and download progress.
- **Authenticated inference** — scoped, revocable API keys, streaming, usage accounting, and copyable
  Ollama, OpenAI-compatible, LangChain, and LlamaIndex examples.
- **Local control** — Linux PAM sign-in over HTTPS, configurable model storage,
  resource history, lifecycle events, and token/timing totals.

## Quick start

Requires **Linux, Python 3.11+, uv, Docker, and PAM**. GPU execution needs compatible
host drivers and container support. Run as your normal user, never sudo.

```bash
cd /path/to/Llameros
uv sync --locked
uv run llameros
```

Open **https://127.0.0.1:8443**, verify/trust your generated certificate, and sign in
with the Linux account running Llameros. Start Ollama, download a model, select a
free CPU/GPU slot, and load it.

> Rootless Docker is recommended. The application currently allows rootful Docker
> by default; the example configuration disables it. Keep all APIs on loopback:
> Ollama's local API is unauthenticated. See the installation guide before use.

## Documentation

| Guide | Contents |
|---|---|
| [Installation & usage](install.md) | Setup, configuration, storage, model workflow, GPU runtimes, and client access |
| [Troubleshooting](trouble_shooting.md) | Unavailable CPU/GPU slots, container migration, login, downloads, and API issues |
| [Development](development.md) | Architecture, tests, acceptance checklist, security model, and limitations |

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
