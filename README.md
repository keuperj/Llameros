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
future models). Open **Add key** below the existing-key table and choose **All models**
or one downloaded local model tag. Expiry choices are **1 week**, **1 month**,
**1 year**, and **Never** (default), implemented as 7, 30, and 365 days or no expiry.
New keys have no per-key output-token quota/cap. The gateway's server-wide 1–4096
output-token safety range and default of 256 tokens still apply; this is not
unbounded generation. Existing keys retain their original expiry and restrictions.

The table stays within the page width and shows at most ten rows before scrolling;
each row has a delete icon on the right. Generated keys remain masked: use **Copy
key**, then **Clear key** after storing the credential securely. Only a SHA-256
digest is persisted; a secret cannot be retrieved again after clearing, logout,
or leaving the page. Never-expiring keys remain valid until deleted or revoked;
prefer shorter expiry when practical and treat credentials like passwords.
Deletion disables new requests without interrupting in-flight inference. Non-secret
key metadata and usage remain in the **Usage by key** history tile, including
request/failure counts, known input/output tokens, unknown-usage requests, and time.

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

## RAG inference and benchmark

The gateway supports authenticated `POST /inference/api/embed` and
`POST /inference/v1/embeddings` with the same model/target permissions as chat.
Provide `input` as text or 1–64 strings (up to 32,000 characters each; total JSON
body limit 128 KiB). Truncation defaults to false. OpenAI compatibility accepts
float vectors, not base64 or token-ID input. Input tokens are accounted without
storing texts or vectors; embedding output tokens are zero. Missing upstream
input counts remain unknown. Output-token caps apply to generation, not vectors.

The default resident-model capacity is two per device (`max_loaded_models`), with
memory-fit checks and individual Stop controls. Requests on one device execute
sequentially; CPU and separate GPUs can execute independently. Existing containers
must be explicitly recreated to apply the changed Ollama environment—restarting
a container alone does not change it. No automatic container migration is performed.
Residency validation detects eviction/fallback but cannot restore an evicted model.

Run the standalone standard-library benchmark against an **already-running fixed
model set**. It neither loads models nor changes containers. Set a key permitted
for both models and their targets, using the exact loaded `model:tag` names:

```bash
read -rsp 'API key: ' LLAMEROS_API_KEY; echo
export LLAMEROS_API_KEY
python3 /home/keuper/Projekte/Llameros/scripts/benchmark_rag.py \
  --base-url https://localhost:8443/inference \
  --ca-cert /absolute/path/to/trusted-certificate.pem \
  --embedding-model embedding-model:tag --embedding-target cpu \
  --llm-model generation-model:tag --llm-target gpu-YOUR-TARGET-ID \
  --repeats 5 --concurrency 1 > cpu-embedding-rag.json
unset LLAMEROS_API_KEY
```

Model names and target IDs above are placeholders: use the dashboard access
examples or authenticated `/inference/v1/models` listing. Repeat with an embedding
model already resident on the same GPU and change `--embedding-target` to compare
placements. Test concurrency separately with `--concurrency 2` or `4`. Each run
includes document indexing once, excluded warmup, then measured question embedding,
cosine retrieval and streaming generation. JSON reports indexing time, stage
mean/p50/p95, time to first streamed content/thinking, output tokens, throughput,
and per-query latency (including queue waits). Missing token counts are explicit.
The preflight and final checks reject missing or ambiguous model placement.

Use `--query-prefix` / `--document-prefix` if your embedding model requires them.
For a custom fixed dataset, `--corpus /absolute/path/to/corpus.json` accepts
`{"documents": ["chunk text", "another chunk"], "queries": ["question"]}`; set
`--top-k` no larger than the document count. The default corpus is deliberately
small: this is an execution/latency benchmark, not a retrieval-quality evaluation
or large-index performance test. Retrieval uses an in-memory exact cosine scan;
production document ingestion and persistent vector storage remain outside Llameros.
