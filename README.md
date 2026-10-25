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

### Independent API keys and access smoke test

Each loaded model/device has its own dashboard box with status, a Stop button, and
an **Access model** dropdown containing authenticated client examples. Create or
delete credentials in **Key Management**, before Configuration—even when no model
is running. A name is required; model permissions default to all models (including
future models). Select one or more downloaded local model tags to restrict access.
An optional **maximum output tokens per request** restricts generation to 1–4096
output tokens; it is not a cumulative quota or a context/input-token limit. Omitted
request limits default to the smaller of 256 and the key cap; explicit higher
limits return HTTP 403. Existing keys retain their permissions and server limits.
Deletion disables new requests without interrupting in-flight inference. Non-secret
key metadata and usage remain in the **Usage by key** history tile, including
request/failure counts, known input/output tokens, unknown-usage requests, and time.
The existing 30-day key expiry default remains configurable at creation.

The standalone script at `/home/keuper/Projekte/Llameros/scripts/test_api_access.py`
needs only Python 3.11+ and does not start or stop any containers. Load a permitted
model first. Run (adjust the base URL and certificate path for your installation):

```sh
export LLAMEROS_API_KEY='your-dashboard-issued-key'
export LLAMEROS_CA_CERT='/path/to/llameros/state/cert.pem'
python3 /home/keuper/Projekte/Llameros/scripts/test_api_access.py \
  --base-url https://127.0.0.1:8443/inference --stream
```

It verifies unauthenticated requests are rejected, discovers models with the key,
and submits one short OpenAI-compatible chat request. Omit `--stream` to test JSON
responses; use `--model MODEL:TAG`, `--target cpu` (or a GPU slot ID), and
`--max-tokens N` as needed. The default is 16 output tokens; lower this for keys
with smaller caps. TLS verification stays enabled and redirects are refused.
Exit status is zero on success, nonzero on failure. It consumes inference usage
and may return 409 if a lifecycle or inference job is already running.
