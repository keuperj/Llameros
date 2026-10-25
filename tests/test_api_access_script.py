import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/test_api_access.py"
spec = importlib.util.spec_from_file_location("api_smoke", PATH)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@pytest.mark.parametrize("stream", [False, True])
def test_smoke_script_discovery_auth_and_generation(monkeypatch, stream):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if not request.get_header("Authorization"):
                raise HTTPError(request.full_url, 401, "unauthorized", {}, io.BytesIO())
            if request.full_url.endswith("/v1/models"):
                return io.BytesIO(
                    json.dumps({"data": [{"id": "test:latest", "target": "cpu"}]}).encode()
                )
            payload = json.loads(request.data)
            assert payload["stream"] is stream and payload["max_tokens"] == 16
            assert request.get_header("X-llameros-target") == "cpu"
            if stream:
                return io.BytesIO(
                    b'data: {"choices": [{"delta": {"content": "hello"}}]}\n\ndata: [DONE]\n\n'
                )
            return io.BytesIO(b'{"choices": [{"message": {"content": "hello"}}]}')

    monkeypatch.setattr(smoke, "build_opener", lambda *args: Opener())
    smoke.check_access("https://localhost:8443/inference", "llm_secret", stream=stream)
    assert len(calls) == 3
    assert calls[1].get_header("Authorization") == "Bearer llm_secret"


@pytest.mark.parametrize(
    "url",
    ["http://localhost/inference", "https://user:secret@localhost", "https://localhost?key=secret"],
)
def test_smoke_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        smoke.check_access(url, "llm_secret")


def test_smoke_missing_key_returns_failure(monkeypatch, capsys):
    monkeypatch.delenv("LLAMEROS_API_KEY", raising=False)
    assert smoke.main([]) == 1
    assert "LLAMEROS_API_KEY" in capsys.readouterr().err
