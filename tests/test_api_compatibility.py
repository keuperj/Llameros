"""Offline contracts pinned to Ollama v0.40.11; not real-model inference tests."""

import base64
import json
import runpy
import shutil
import struct

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker
from test_gateway import gateway as shared_gateway
from test_gateway import issue

from llameros.api_keys import normalize
from llameros.app import create_app
from llameros.config import Settings
from llameros.multimodal import extract_pdf, image_dimensions, validate_images

PIN = "0.40.11"
gateway = shared_gateway
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
)


def headers(client, **kwargs):
    """Create a test key and return its bearer Authorization header."""
    created, _ = issue(client, **kwargs)
    return {"Authorization": "Bearer " + created["key"]}


@pytest.mark.parametrize(
    "path,fields",
    [
        ("/v1/completions", {"prompt": "hello", "suffix": "end", "frequency_penalty": 0.1}),
        (
            "/v1/responses",
            {"input": "hello", "reasoning": {"effort": "low"}, "max_output_tokens": 12},
        ),
        (
            "/v1/messages",
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 12,
                "thinking": {"type": "disabled"},
            },
        ),
        (
            "/v1/chat/completions",
            {
                "messages": [{"role": "user", "content": "hello"}],
                "reasoning_effort": "low",
                "tool_choice": "auto",
                "logprobs": True,
            },
        ),
    ],
)
def test_inference_protocols(gateway, path, fields):
    """Verify inference protocols."""
    app, client, _, calls, response = gateway
    auth = headers(client)
    response.clear()
    response.update(
        usage={"input_tokens": 2, "output_tokens": 3, "prompt_tokens": 2, "completion_tokens": 3}
    )
    result = client.post("/inference" + path, headers=auth, json={"model": "gemma3:270m", **fields})
    assert result.status_code == 200, result.text
    body = json.loads(calls[-1].content)
    assert calls[-1].url.path == path
    for name, value in fields.items():
        assert body[name] == value
    assert "authorization" not in calls[-1].headers
    assert app.state.api_keys.list()[0]["output_tokens"] == 3
    assert not app.state.jobs.scheduler.tickets


@pytest.mark.parametrize(
    "path,fields",
    [
        ("/v1/responses", {"input": "hello", "max_output_tokens": 9}),
        ("/v1/completions", {"prompt": "hello", "max_tokens": 9}),
        ("/v1/messages", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 9}),
    ],
)
def test_new_protocols_preserve_key_limits(gateway, path, fields):
    """Verify new protocols preserve key limits."""
    _, client, _, calls, _ = gateway
    auth = headers(client, max_tokens=8)
    result = client.post("/inference" + path, headers=auth, json={"model": "gemma3:270m", **fields})
    assert result.status_code == 403
    assert result.json()["error"]["type"] == "permission_error"
    assert not calls


@pytest.mark.parametrize(
    "path,fields",
    [
        ("/v1/responses", {"input": "hi", "previous_response_id": "x"}),
        ("/v1/responses", {"input": "hi", "store": True}),
        ("/v1/responses", {"input": "hi", "tools": [{"type": "web_search"}]}),
        (
            "/v1/messages",
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "web_search_20250305"}],
            },
        ),
    ],
)
def test_no_stateful_or_server_tools(gateway, path, fields):
    """Verify no stateful or server tools."""
    _, client, _, calls, _ = gateway
    result = client.post(
        "/inference" + path, headers=headers(client), json={"model": "gemma3:270m", **fields}
    )
    assert result.status_code == 400
    assert not calls


def test_legacy_and_base64_embeddings(gateway):
    """Verify legacy and base64 embeddings."""
    _, client, _, calls, response = gateway
    auth = headers(client)
    response.clear()
    response.update(embeddings=[[1.0, -0.5]], prompt_eval_count=2)
    result = client.post(
        "/inference/api/embeddings", headers=auth, json={"model": "gemma3:270m", "prompt": "hello"}
    )
    assert result.json() == {"embedding": [1.0, -0.5]}
    assert calls[-1].url.path == "/api/embed"
    result = client.post(
        "/inference/v1/embeddings",
        headers=auth,
        json={"model": "gemma3:270m", "input": "hello", "encoding_format": "base64"},
    )
    assert struct.unpack("<ff", base64.b64decode(result.json()["data"][0]["embedding"])) == (
        1.0,
        -0.5,
    )


def test_namespaced_inference_not_urls():
    """Verify namespaced inference not urls."""
    assert normalize("organization/model") == "organization/model:latest"
    for value in ("../model", "https://evil/model", "model?x=1", "org//model", None):
        with pytest.raises(ValueError):
            normalize(value)


def test_filtered_metadata(gateway):
    """Verify filtered metadata."""
    _, client, docker, calls, response = gateway
    auth = headers(client)
    docker.loaded.append({"name": "private:latest", "target": "cpu", "base_url": "http://secret"})
    listed = client.get("/inference/api/ps", headers=auth)
    assert len(listed.json()["models"]) == 1
    response.clear()
    response.update(capabilities=["completion", "vision"])
    result = client.post("/inference/api/show", headers=auth, json={"model": "gemma3:270m"})
    assert result.json()["capabilities"] == ["completion", "vision"]
    count = len(calls)
    assert client.get("/inference/v1/models/private:latest", headers=auth).status_code == 404
    assert len(calls) == count
    assert client.get("/inference/v1/models/gemma3:270m", headers=auth).status_code == 200
    response.clear()
    response.update(version=PIN)
    assert client.get("/inference/api/version", headers=auth).json() == {"version": PIN}


@pytest.mark.parametrize(
    "body",
    [
        {"images": ["aGVsbG8="]},
        {"images": ["https://example.org/image.png"]},
        {"images": "invalid"},
        {
            "messages": [
                {"content": [{"type": "image_url", "image_url": {"url": "https://example.org/a"}}]}
            ]
        },
        {"input": [{"content": [{"type": "input_image", "file_id": "abc"}]}]},
        {
            "messages": [
                {
                    "content": [
                        {"type": "image", "source": {"type": "url", "url": "https://example.org"}}
                    ]
                }
            ]
        },
    ],
)
def test_media_rejects_malformed_and_remote(body):
    """Verify media rejects malformed and remote."""
    with pytest.raises(HTTPException):
        validate_images(body, Settings())


def test_raster_dimensions_and_limits():
    """Verify raster dimensions and limits."""
    assert image_dimensions(PNG) == ("image/png", 1, 1)
    data = base64.b64encode(PNG).decode()
    assert validate_images({"images": [data]}, Settings()) == 1
    for cfg, body in [
        (Settings(image_max_count=1), {"images": [data, data]}),
        (Settings(image_max_bytes=8), {"images": [data]}),
        (
            Settings(image_max_pixels=1),
            {"images": [base64.b64encode(PNG[:16] + struct.pack(">II", 2, 2) + PNG[24:]).decode()]},
        ),
    ]:
        with pytest.raises(HTTPException) as error:
            validate_images(body, cfg)
        assert error.value.status_code == 413


@pytest.mark.parametrize("vision", [True, False])
def test_large_inline_image_and_model_capability(gateway, vision):
    """Verify large inline image and model capability."""
    app, client, _, calls, response = gateway
    auth = headers(client)
    response["capabilities"] = ["vision"] if vision else ["completion"]
    result = client.post(
        "/inference/api/generate",
        headers=auth,
        json={
            "model": "gemma3:270m",
            "prompt": "describe",
            "stream": False,
            "images": [base64.b64encode(PNG + b"\0" * 140000).decode()],
        },
    )
    assert result.status_code == (200 if vision else 400), result.text
    assert calls[0].url.path == "/api/show"
    assert len(calls) == (2 if vision else 1)
    assert not app.state.jobs.scheduler.tickets


@pytest.mark.parametrize(
    "protocol,broken",
    [(p, b) for p in ("responses", "messages", "completions") for b in (False, True)],
)
def test_protocol_streams(tmp_path, protocol, broken):
    """Verify protocol streams."""
    docker = FakeDocker()
    docker.running, docker.loaded = True, [{"name": "gemma3:270m"}]
    if protocol == "responses":
        events = [
            {"type": "response.created"},
            {"type": "response.output_text.delta", "delta": "Hello"},
        ]
        if not broken:
            events.append(
                {
                    "type": "response.completed",
                    "response": {"usage": {"input_tokens": 2, "output_tokens": 3}},
                }
            )
    elif protocol == "messages":
        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 2, "output_tokens": 0}},
            },
            {"type": "content_block_delta", "delta": {"text": "Hello"}},
        ]
        if not broken:
            events += [
                {"type": "message_delta", "usage": {"output_tokens": 3}},
                {"type": "message_stop"},
            ]
    else:
        events = [{"choices": [{"text": "Hello"}]}]
        if not broken:
            events += [{"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3}}]
    wire = "".join(
        ("event: " + e["type"] + "\n" if "type" in e else "") + "data: " + json.dumps(e) + "\n\n"
        for e in events
    )
    if protocol == "completions" and not broken:
        wire += "data: [DONE]\n\n"
    app = create_app(
        Settings(state_dir=tmp_path),
        docker=docker,
        sampling=False,
        hardware_provider=lambda: HARDWARE,
        authenticator=lambda *args: True,
        gateway_transport=httpx.MockTransport(lambda _: httpx.Response(200, text=wire)),
    )
    with TestClient(app, base_url="https://testserver") as client:
        auth = headers(client)
        if protocol == "messages":
            auth = {"x-api-key": auth["Authorization"].removeprefix("Bearer ")}
        fields = (
            {"input": "hi"}
            if protocol == "responses"
            else {"prompt": "hi"}
            if protocol == "completions"
            else {"messages": [{"role": "user", "content": "hi"}]}
        )
        result = client.post(
            "/inference/v1/" + protocol,
            headers=auth,
            json={"model": "gemma3:270m", "stream": True, **fields},
        )
        assert result.status_code == 200
        assert "Hello" in result.text
        assert ("error" in result.text) == broken
        if protocol != "completions" and not broken:
            assert "event: " + events[-1]["type"] in result.text
        assert app.state.api_keys.list()[0]["failed"] == int(broken)
        assert not app.state.jobs.scheduler.tickets


def pdf_bytes(text="Hello PDF"):
    """Build a minimal valid single-page text PDF for offline extraction checks."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data, offsets = b"%PDF-1.4\n", [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return data + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler not installed")
def test_real_pdf_extraction_and_inline_translation(gateway):
    """Verify real pdf extraction and inline translation."""
    _, client, _, calls, response = gateway
    response.clear()
    response.update(usage={"input_tokens": 2, "output_tokens": 3})
    auth = headers(client)
    data = base64.b64encode(pdf_bytes()).decode()
    result = client.post("/inference/api/documents/extract", headers=auth, json={"data": data})
    assert result.status_code == 200, result.text
    assert "Hello PDF" in result.json()["text"]
    assert result.json()["chunks"]
    for path, fields in [
        (
            "responses",
            {
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_file",
                                "file_data": "data:application/pdf;base64," + data,
                            }
                        ],
                    }
                ]
            },
        ),
        (
            "messages",
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": data,
                                },
                            }
                        ],
                    }
                ]
            },
        ),
    ]:
        result = client.post(
            "/inference/v1/" + path, headers=auth, json={"model": "gemma3:270m", **fields}
        )
        assert result.status_code == 200, result.text
        body = json.loads(calls[-1].content)
        assert "Hello PDF" in json.dumps(body)
        assert data not in json.dumps(body)
    with pytest.raises(HTTPException) as error:
        extract_pdf(base64.b64encode(pdf_bytes("")).decode(), Settings())
    assert "OCR" in error.value.detail


def test_pdf_unavailable_and_invalid(gateway, monkeypatch):
    """Verify pdf unavailable and invalid."""
    _, client, _, _, _ = gateway
    auth = headers(client)
    monkeypatch.setattr("llameros.multimodal.shutil.which", lambda _: None)
    result = client.post(
        "/inference/api/documents/extract",
        headers=auth,
        json={"data": base64.b64encode(pdf_bytes()).decode()},
    )
    assert result.status_code == 503
    assert client.post("/inference/api/documents/extract", json={}).status_code == 401
    result = client.post(
        "/inference/api/documents/extract", headers=auth, json={"data": "aGVsbG8="}
    )
    assert result.status_code == 400
    result = client.post(
        "/inference/api/documents/extract",
        headers=auth,
        json={"data": "aGVsbG8=", "chunk_size": 256, "overlap": 255},
    )
    assert result.status_code == 400


@pytest.mark.parametrize(
    "name",
    [
        "inference_body_bytes",
        "image_max_bytes",
        "image_max_count",
        "image_max_pixels",
        "pdf_max_bytes",
        "pdf_max_pages",
        "pdf_max_text_bytes",
    ],
)
def test_media_config_bounds(name):
    """Verify media config bounds."""
    for value in (0, -1, True, 10000000000):
        with pytest.raises(ValueError):
            Settings(**{name: value})


def test_live_checker_contract_and_pin(tmp_path, monkeypatch):
    """Verify live checker contract and pin."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "tests/ollama-compatibility.json").read_text())
    assert manifest["version"] == PIN
    script = runpy.run_path(str(root / "scripts/check_api_compatibility.py"))
    monkeypatch.setenv("LLAMEROS_API_KEY", "llm_test_not_a_real_key")
    monkeypatch.setattr(script["ssl"], "create_default_context", lambda **_: True)
    original = httpx.Client
    seen = []

    def handle(request):
        seen.append(request.url.path)
        if request.url.path.endswith("api/version"):
            return httpx.Response(200, json={"version": PIN})
        body = json.loads(request.content) if request.content else {}
        if body.get("stream"):
            path = request.url.path
            event = (
                {"type": "response.completed"}
                if path.endswith("responses")
                else {"type": "message_stop"}
                if path.endswith("messages")
                else None
            )
            wire = (
                "data: " + json.dumps(event) + "\n\n"
                if event
                else "data: [DONE]\n\n"
                if "/v1/" in path
                else '{"done": true}\n'
            )
            return httpx.Response(200, text=wire)
        return httpx.Response(
            200,
            json={
                "response": "Hello",
                "message": {"content": "Hello"},
                "choices": [{"text": "Hello"}],
                "output": [{"text": "Hello"}],
                "content": [{"text": "Hello"}],
            },
        )

    monkeypatch.setattr(
        script["httpx"],
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    assert script["main"](["--ca", str(tmp_path / "cert.pem"), "--model", "gemma3:270m"]) == 0
    assert len(seen) == 15
