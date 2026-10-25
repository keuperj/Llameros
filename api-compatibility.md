# Inference API compatibility

All routes below are prefixed with `/inference`. HTTPS and a dashboard-issued
bearer key are required. No dashboard/playground attachment UI is provided.

## Contract reference and routes

The offline compatibility suite targets **Ollama v0.40.11**, source commit
`675150d6ad2747df1e218ef59de9194bc4a7e538`. The pin is recorded in
`tests/ollama-compatibility.json`; it does not change existing containers or
guarantee identical behavior on other runtime versions. For a reproducible
runtime set `ollama_image = "ollama/ollama:0.40.11"` and explicitly recreate
containers. Merely restarting an existing container does not apply image or
environment changes.

| Route | Behavior |
|---|---|
| `POST /api/generate`, `/api/chat` | Native JSON or NDJSON streaming |
| `POST /api/embed` | Batched text embeddings |
| `POST /api/embeddings` | Legacy `prompt` input and single `embedding` output, translated through embed |
| `POST /v1/chat/completions`, `/v1/completions` | JSON or SSE with `[DONE]` |
| `POST /v1/responses` | Stateless JSON or named SSE events |
| `POST /v1/messages` | Anthropic JSON or named SSE events; bearer or `x-api-key` |
| `GET /api/tags`, `/api/ps`, `/v1/models` | Permission-filtered **loaded** model list |
| `POST /api/show`, `GET /v1/models/{model}` | Permission-filtered runtime metadata |
| `GET /api/version` | Version of a permitted loaded runtime |
| `POST /api/documents/extract` | Llameros extension: inline PDF text and chunks |

Native sampling includes `min_p`, `num_keep`, `repeat_last_n`, and frequency/
presence penalties, in addition to the previous sampling controls. Native
`context` and log probabilities are accepted. OpenAI chat accepts reasoning,
tool choice, penalties, logit bias, and log probabilities. Actual tool calls,
thinking, schemas and log probabilities depend on the runtime/model.
Only client-executed function tools are allowed; server search/computer tools
are rejected. Tool definitions and model tool calls do not execute application code.

Embeddings accept 1–64 non-empty text strings, at most 32,000 characters each.
OpenAI vectors support `float` and `base64` (little-endian float32). Token-ID
input is not supported by the pinned Ollama contract. Native embeddings accept
allowlisted sampling options; context remains server-controlled. Truncation
continues to default to **false**, deliberately differing from Ollama.

Namespaced local model names are accepted for inference/key permissions. The
catalogue and dashboard lifecycle controls retain their stricter library-name
rules. Models must already be loaded. Use `X-Llameros-Target: cpu` (or a permitted
GPU slot ID) when placement is ambiguous; POST inference requests can also use
`target`. Metadata requires exactly one permitted loaded placement.

List timestamps use `modified_at` where available, otherwise zero (unknown);
ownership uses the model namespace or `library`. Individual model metadata is
requested from Ollama. Lists intentionally do not expose downloaded-but-unloaded
models or internal runtime URLs.

## Inline images

Supported representations:

* Native `images` / `messages[].images`: base64 raster bytes.
* OpenAI chat: `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`.
* Responses: `{"type":"input_image","image_url":"data:image/png;base64,..."}`.
* Anthropic: `{"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}`.

No URL fetching, stored file IDs, multipart uploads, resizing or compression.
PNG, JPEG, GIF and WebP raster headers are checked; malformed base64, unsupported
headers, declared MIME mismatches, byte/count/pixel excesses are rejected.
Header validation is not full image decoding: a corrupt compressed image can
still be rejected by Ollama. Before image inference the gateway requires the
loaded model's `/api/show` capabilities to include `vision`.

Defaults: 16 MiB total JSON, 8 MiB decoded per image, 8 images, 40 million pixels
per image. Text-only requests retain the 128 KiB limit. Configure
`inference_body_bytes`, `image_max_bytes`, `image_max_count`, `image_max_pixels`
in local TOML. Larger limits increase memory/CPU exposure.

## PDF preprocessing (API only)

Install the distribution's **Poppler utilities** (`pdftotext`). No Python parser
dependency is added. Keep Poppler updated: it processes untrusted document bytes
as the Llameros user, not in a security sandbox. Extraction uses private temporary
files removed after completion, a 15-second timeout, two concurrent workers, and
output/page/byte bounds. Scanned or image-only PDFs return 422 (OCR required).
No OCR, page-image rendering, persistent documents, vector database, or retrieval
service is included.

`POST /api/documents/extract`:

```json
{"data":"data:application/pdf;base64,...","chunk_size":2000,"overlap":200}
```

Returns `text`, overlapping character-based `chunks`, and `page_limit`.
`chunk_size` is 256–32000; overlap cannot exceed half the chunk size. Chunking
is not token-aware. Embed the chunks via `/api/embed` and store vectors in your
own RAG application. An authenticated key can extract without loading a model.

Inline PDF blocks are also converted to text before inference:

* Responses: `{"type":"input_file","file_data":"data:application/pdf;base64,..."}`.
* Anthropic: `{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":"..."}}`.

Only PDFs are accepted. File IDs and remote URLs are rejected. Up to four inline
PDFs share a 1 MiB extracted-text allowance by default. Extraction reads the
**first 64 pages** by default, not an assertion that later pages were processed.
Configure `pdf_max_bytes`, `pdf_max_pages`, `pdf_max_text_bytes`. Text is not
automatically truncated to model context; oversized context remains an inference
error unless explicitly permitted by the embedding request's truncation setting.

## Deliberate restrictions and remaining exclusions

Bearer keys cannot pull/create/copy/push/delete models, upload blobs, select
runtime URLs, change hardware parameters, unload models, or change residency.
Output limits (including key-specific limits), context policy, bounded per-device
queues, revocation rechecks and content-free usage accounting apply across the
new protocols. Anthropic credentials and other caller headers are never forwarded.

Unsupported: stateful Responses, file storage APIs, remote attachments,
server-executed tools, audio APIs, System One and Responses compact. This remains
a restricted inference gateway, not a transparent/full Ollama or OpenAI proxy.
Upstream rejection statuses are preserved for common client errors, with a generic
message to avoid leaking runtime details. Local inference errors use native or
OpenAI/Anthropic error envelopes; dashboard errors retain their existing format.

## Validation

`tests/test_api_compatibility.py` covers offline admission/forwarding, streaming
terminal events, failed-stream cleanup, authorization, limits, embeddings and
real Poppler text extraction where installed. Mock vision tests are **not** proof
of vision-model execution. Use the opt-in checker against an already-running HTTPS
gateway (no model loads, container changes or insecure TLS bypass):

```bash
export LLAMEROS_API_KEY='your-issued-key'
uv run python scripts/check_api_compatibility.py \
  --ca "$HOME/.local/share/llameros/cert.pem" \
  --model your-loaded-chat-model --embedding-model your-loaded-embedding-model \
  --vision-model your-loaded-vision-model --image /absolute/path/image.png \
  --pdf /absolute/path/text-document.pdf --target cpu
```

Supply your actual certificate path. The checker emits status only, never prompts,
keys, PDF text or model output. Optional vision/PDF/embedding checks require their
arguments. A passing live smoke test is not an exhaustive semantic compatibility
certification; GPU behavior requires running it on the intended hardware slot.