"""Offline rag benchmark regression tests for Llameros behavior and integration boundaries."""

import importlib.util
import io
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "benchmark_rag", Path(__file__).resolve().parents[1] / "scripts/benchmark_rag.py"
)
rag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rag)


class FakeClient:
    """Deterministic in-memory RAG benchmark client returning fixed vectors/timing metrics."""

    def __init__(self):
        self.calls = []

    def models(self):
        return [{"id": "embed:latest", "target": "cpu"}, {"id": "llm:latest", "target": "gpu"}]

    def embed(self, model, texts, target):
        self.calls.append(("embed", len(texts), target))
        return [[1.0, 0.0] for text in texts]

    def generate(self, model, question, documents, target, max_tokens):
        self.calls.append(("generate", len(documents), target))
        return {"generation_seconds": 0.01, "ttft_seconds": 0.005, "output_tokens": 3}


def test_benchmark_batches_once_excludes_warmup_and_reports_timings():
    """Verify benchmark batches once excludes warmup and reports timings."""
    client = FakeClient()
    result = rag.benchmark(client, "embed:latest", "llm:latest", repeats=2, concurrency=2)
    assert result["queries"] == 8
    assert result["known_output_tokens"] == 24
    assert len(result["requests"]) == 8
    assert client.calls[0] == ("embed", 8, "cpu")
    assert sum(call[0] == "generate" for call in client.calls) == 9
    assert result["timings"]["ttft_seconds"]["p95"] == 0.005
    assert result["queries_per_second"] > 0


def test_preflight_rejects_unloaded_model_before_work():
    """Verify preflight rejects unloaded model before work."""
    client = FakeClient()
    with pytest.raises(ValueError, match="loaded"):
        rag.benchmark(client, "absent:latest", "llm:latest")
    assert not client.calls


@pytest.mark.parametrize("vector", [[], [0.0, 0.0], [float("nan")], [True]])
def test_invalid_vectors(vector):
    """Verify invalid vectors."""
    with pytest.raises(ValueError):
        rag.unit_vector(vector)


def test_https_and_cli(monkeypatch, capsys):
    """Verify https and cli."""
    with pytest.raises(ValueError, match="HTTPS"):
        rag.Client("http://localhost/inference", "key")
    monkeypatch.delenv("LLAMEROS_API_KEY", raising=False)
    assert rag.main(["--embedding-model", "embed:latest", "--llm-model", "llm:latest"]) == 1
    assert "LLAMEROS_API_KEY" in capsys.readouterr().err


def test_streaming_client_measures_terminal_usage_without_retaining_content():
    """Verify streaming client measures terminal usage without retaining content."""
    client = rag.Client("https://localhost/inference", "test-key")
    calls = []

    def open_stream(path, body, target):
        calls.append((path, body, target))
        return io.BytesIO(
            b'{"message":{"content":"answer"},"done":false}\n{"done":true,"eval_count":4}\n'
        )

    client.open = open_stream
    result = client.generate("llm:latest", "question", ["context"], "gpu", 32)
    assert result["output_tokens"] == 4
    assert result["ttft_seconds"] is not None
    assert "answer" not in str(result)
    assert calls[0][0] == "/api/chat"
    assert calls[0][1]["options"]["num_predict"] == 32


def test_streaming_client_rejects_incomplete_response():
    """Verify streaming client rejects incomplete response."""
    client = rag.Client("https://localhost/inference", "test-key")
    client.open = lambda *args: io.BytesIO(b'{"message":{"content":"partial"}}\n')
    with pytest.raises(ValueError, match="terminal"):
        client.generate("llm:latest", "question", ["context"], "gpu", 32)
