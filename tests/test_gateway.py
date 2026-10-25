import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker, sign_in

from llameros.api_keys import ApiKeys, KeyRequest
from llameros.app import create_app
from llameros.config import Settings
from llameros.gateway import InferenceStream
from llameros.storage import Store


@pytest.fixture
def gateway(tmp_path):
    docker = FakeDocker()
    docker.running = True
    docker.loaded = [{"name": "gemma3:270m", "target": "cpu"}]
    calls = []
    response = {
        "model": "gemma3:270m",
        "response": "secret response",
        "done": True,
        "prompt_eval_count": 2,
        "eval_count": 3,
    }

    def handle(request):
        calls.append(request)
        if isinstance(response, dict):
            return httpx.Response(200, json=response)
        return httpx.Response(200, content=response)

    app = create_app(
        Settings(state_dir=tmp_path),
        docker=docker,
        sampling=False,
        hardware_provider=lambda: HARDWARE,
        authenticator=lambda *args: True,
        gateway_transport=httpx.MockTransport(handle),
    )
    with TestClient(app, base_url="https://testserver") as client:
        yield app, client, docker, calls, response


def issue(client, **kwargs):
    client.headers["Origin"] = "https://testserver"
    csrf = sign_in(client).json()["csrf"]
    result = client.post(
        "/api/keys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "test", "models": ["gemma3:270m"], "targets": ["cpu"], **kwargs},
    )
    assert result.status_code == 201, result.text
    client.headers.pop("Origin")
    return result.json(), csrf


def post(client, key, **body):
    return client.post(
        "/inference/api/generate",
        headers={"Authorization": "Bearer " + key},
        json={"model": "gemma3:270m", "prompt": "secret prompt", "stream": False, **body},
    )


def test_keys_default_to_all_models_without_runtime(gateway):
    app, client, docker, calls, response = gateway
    docker.running, docker.loaded = False, []
    created, _ = issue(client, models=["*"])
    assert app.state.api_keys.authenticate("Bearer " + created["key"])["models"] == ["*"]
    assert KeyRequest(name="all local models").models == ["*"]


def test_key_token_limits_apply_to_all_protocols(gateway):
    app, client, docker, calls, response = gateway
    created, _ = issue(client, max_tokens=8)
    token = created["key"]
    assert post(client, token).status_code == 200
    assert json.loads(calls[-1].content)["options"]["num_predict"] == 8
    assert post(client, token, options={"num_predict": 9}).status_code == 403
    for field in ("max_tokens", "max_completion_tokens"):
        result = client.post(
            "/inference/v1/chat/completions",
            headers={"Authorization": "Bearer " + token},
            json={
                "model": "gemma3:270m",
                "messages": [{"role": "user", "content": "hello"}],
                field: 9,
            },
        )
        assert result.status_code == 403
    for value in (0, 4097, True, 1.5):
        with pytest.raises(ValueError):
            KeyRequest(name="bad", max_tokens=value)


def test_delete_rejects_new_requests_and_retains_usage(gateway):
    app, client, docker, calls, response = gateway
    created, csrf = issue(client)
    assert post(client, created["key"]).status_code == 200
    path = "/api/keys/" + created["id"]
    assert client.delete(path, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    client.headers["Origin"] = "https://testserver"
    assert client.delete(path, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/keys").json()["keys"] == []
    history = client.get("/api/keys/usage").json()["keys"][0]
    assert history["deleted"] and history["requests"] == 1
    assert history["output_tokens"] == 3
    assert "digest" not in history and "key" not in history
    assert post(client, created["key"]).status_code == 401
    assert client.delete(path, headers={"X-CSRF-Token": csrf}).status_code == 404


def test_existing_key_database_migration(tmp_path):
    store = Store(tmp_path / "history.sqlite")
    with store.connect() as db:
        db.execute(
            "CREATE TABLE api_keys (id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL, models TEXT NOT NULL, targets TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)"
        )
    keys = ApiKeys(store)
    created = keys.create(KeyRequest(name="migrated", max_tokens=20))
    assert keys.authenticate("Bearer " + created["key"])["max_tokens"] == 20
    assert ApiKeys(store).list()[0]["deleted"] == 0


def test_authentication_management_and_no_credential_forwarding(gateway):
    app, client, docker, calls, response = gateway
    assert client.get("/api/keys").status_code == 401
    assert post(client, "invalid").status_code == 401
    created, csrf = issue(client)
    assert (
        post(client, "invalid").status_code == 401
    )  # Session alone cannot authenticate inference.
    assert client.post("/api/keys", json={"name": "bad", "models": ["*"]}).status_code == 403
    assert post(client, created["key"]).status_code == 200  # No browser Origin/CSRF needed.
    upstream = calls[-1]
    assert "authorization" not in upstream.headers
    assert "cookie" not in upstream.headers
    assert upstream.url.host == "127.0.0.1"
    payload = json.loads(upstream.content)
    assert payload["options"] == {"num_ctx": 4096, "num_predict": 256, "num_gpu": 0}
    assert payload["keep_alive"] == -1
    listed = client.get("/api/keys").json()["keys"][0]
    assert "digest" not in listed and "key" not in listed
    assert (listed["requests"], listed["prompt_tokens"], listed["output_tokens"]) == (1, 2, 3)
    client.headers["Origin"] = "https://testserver"
    assert (
        client.post(
            f"/api/keys/{created['id']}/revoke", headers={"X-CSRF-Token": csrf}, json={}
        ).status_code
        == 200
    )
    assert post(client, created["key"]).status_code == 401
    with app.state.store.connect() as db:
        saved = " ".join(
            str(tuple(row))
            for table in ("api_keys", "api_usage", "generations")
            for row in db.execute(f"SELECT * FROM {table}")
        )
    assert created["key"] not in saved
    assert "secret prompt" not in saved and "secret response" not in saved


def test_permissions_routing_and_job_lock(gateway):
    app, client, docker, calls, response = gateway
    created, _ = issue(client)
    key = created["key"]
    assert post(client, key, model="other:latest").status_code == 403
    assert post(client, key, target="gpu-0123456789abcdef").status_code == 403
    assert post(client, key, model="https://evil.example/model").status_code == 400
    headers = {"Authorization": "Bearer " + key}
    assert (
        client.get("/inference/v1/models", headers=headers).json()["data"][0]["id"] == "gemma3:270m"
    )
    docker.loaded.append({"name": "gemma3:270m", "target": "gpu-0123456789abcdef"})
    assert post(client, key).status_code == 200  # Restricted key only sees its CPU slot.
    wildcard, _ = issue(client, models=["*"], targets=["*"])
    assert post(client, wildcard["key"]).status_code == 409
    assert post(client, wildcard["key"], target="cpu").status_code == 200
    app.state.jobs.job = {"state": "running"}
    assert post(client, key).status_code == 409
    app.state.jobs.job = {"state": "idle"}
    docker.loaded = []
    assert post(client, key).status_code == 409
    assert app.state.jobs.status()["state"] == "failed"


@pytest.mark.parametrize(
    "fields",
    [
        {"options": {"num_gpu": 10}},
        {"options": {"num_ctx": 100000}},
        {"options": {"num_predict": -1}},
        {"options": {"num_predict": True}},
        {"stream": "yes"},
        {"host": "http://evil"},
        {"prompt": ""},
        {"target": []},
    ],
)
def test_request_guardrails(gateway, fields):
    _, client, _, calls, _ = gateway
    created, _ = issue(client)
    assert post(client, created["key"], **fields).status_code == 400
    assert not calls


def test_body_size_https_and_lifecycle_exclusion(gateway):
    _, client, _, _, _ = gateway
    created, _ = issue(client)
    key = created["key"]
    assert post(client, key, prompt="a" * 131072).status_code == 413
    assert client.post("http://testserver/inference/api/generate").status_code == 400
    assert (
        client.post(
            "/inference/api/pull",
            headers={"Authorization": "Bearer " + key},
            json={"model": "gemma3"},
        ).status_code
        == 404
    )


def test_openai_nonstream_and_usage(gateway):
    _, client, _, calls, response = gateway
    created, _ = issue(client)
    response.clear()
    response.update(
        id="chat-1",
        object="chat.completion",
        choices=[{"message": {"role": "assistant", "content": "Hello"}}],
        usage={"prompt_tokens": 4, "completion_tokens": 5},
    )
    result = client.post(
        "/inference/v1/chat/completions",
        headers={"Authorization": "Bearer " + created["key"]},
        json={
            "model": "gemma3:270m",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_completion_tokens": 42,
        },
    )
    assert result.status_code == 200
    assert result.json()["choices"][0]["message"]["content"] == "Hello"
    assert json.loads(calls[-1].content)["max_tokens"] == 42
    assert client.get("/api/keys").json()["keys"][0]["output_tokens"] == 5


@pytest.mark.parametrize(
    "openai,broken", [(False, False), (True, False), (False, True), (True, True)]
)
def test_streams_and_failed_stream_accounting(tmp_path, openai, broken):
    docker = FakeDocker()
    docker.running, docker.loaded = True, [{"name": "gemma3:270m"}]
    if openai:
        wire = 'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        if not broken:
            wire += 'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":3}}\n\ndata: [DONE]\n\n'
    else:
        wire = '{"response":"Hello","done":false}\n'
        if not broken:
            wire += '{"done":true,"prompt_eval_count":2,"eval_count":3}\n'
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, text=wire)

    app = create_app(
        Settings(state_dir=tmp_path),
        docker=docker,
        sampling=False,
        authenticator=lambda *args: True,
        gateway_transport=httpx.MockTransport(handle),
    )
    with TestClient(app, base_url="https://testserver") as client:
        created, _ = issue(client)
        path = "/v1/chat/completions" if openai else "/api/chat"
        result = client.post(
            "/inference" + path,
            headers={"Authorization": "Bearer " + created["key"]},
            json={
                "model": "gemma3:270m",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert result.status_code == 200
        assert "Hello" in result.text
        assert ("error" in result.text) == broken
        if openai and not broken:
            assert "[DONE]" in result.text
            assert '"usage"' not in result.text
            assert json.loads(requests[-1].content)["stream_options"] == {"include_usage": True}
        listed = client.get("/api/keys").json()["keys"][0]
        assert listed["requests"] == 1 and listed["failed"] == int(broken)
        assert listed["unknown_usage"] == int(broken)
        assert listed["output_tokens"] == (0 if broken else 3)
        assert app.state.jobs.status()["state"] != "running"


def test_keys_persist_expire_and_validate(tmp_path):
    store = Store(tmp_path / "keys.sqlite3")
    keys = ApiKeys(store)
    created = keys.create(KeyRequest(name="one", models=["gemma3"], targets=["cpu"]))
    keys = ApiKeys(store)
    assert keys.authenticate("Bearer " + created["key"])["models"] == ["gemma3:latest"]
    with store.connect() as db:
        db.execute("UPDATE api_keys SET expires=?", (time.time() - 1,))
    with pytest.raises(Exception) as error:
        keys.authenticate("Bearer " + created["key"])
    assert error.value.status_code == 401
    with pytest.raises(ValueError):
        KeyRequest(name="bad", models=["*"], targets=["arbitrary-port"])


@pytest.mark.parametrize("days", [None, 7, 30, 365])
def test_key_expiry_presets_and_unlimited_default(gateway, days):
    app, client, docker, calls, response = gateway
    before = time.time()
    created, _ = issue(client, expires_days=days)
    key = app.state.api_keys.authenticate("Bearer " + created["key"])
    assert key["max_tokens"] is None
    if days is None:
        assert key["expires"] == 0
        # Never-expiring keys remain valid even far into the future.
        with app.state.store.connect() as db:
            db.execute("UPDATE api_keys SET created=? WHERE id=?", (1, created["id"]))
        assert post(client, created["key"], options={"num_predict": 4096}).status_code == 200
    else:
        assert before + days * 86400 <= key["expires"] <= time.time() + days * 86400
    assert KeyRequest(name="default").expires_days is None
    assert KeyRequest(name="default").max_tokens is None


@pytest.mark.parametrize("days", [0, -1, 366, True, "never"])
def test_invalid_key_expiry(days):
    with pytest.raises(ValueError):
        KeyRequest(name="invalid", expires_days=days)


@pytest.mark.parametrize(
    "status,content",
    [
        (500, "private runtime error"),
        (302, ""),
        (200, "not JSON"),
        (200, '{"error":"private error"}'),
    ],
)
def test_upstream_failures_release_lock_without_leaking_errors(tmp_path, status, content):
    docker = FakeDocker()
    docker.running, docker.loaded = True, [{"name": "gemma3:270m"}]
    app = create_app(
        Settings(state_dir=tmp_path),
        docker=docker,
        sampling=False,
        authenticator=lambda *args: True,
        gateway_transport=httpx.MockTransport(lambda request: httpx.Response(status, text=content)),
    )
    with TestClient(app, base_url="https://testserver") as client:
        created, _ = issue(client)
        result = post(client, created["key"])
        assert result.status_code == 502
        assert "private" not in result.text
        assert app.state.jobs.status()["state"] == "failed"
        assert client.get("/api/keys").json()["keys"][0]["failed"] == 1


def test_disconnect_before_stream_iteration_runs_cleanup():
    async def scenario():
        cleaned = []

        async def cleanup():
            cleaned.append(True)

        async def chunks():
            await asyncio.sleep(10)
            yield "unused"

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        response = InferenceStream(chunks(), cleanup=cleanup)
        await asyncio.wait_for(
            response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send), 1
        )
        assert cleaned == [True]

    asyncio.run(scenario())


def test_device_routing_uses_registered_slot_url(tmp_path, monkeypatch):
    from types import SimpleNamespace

    gpu = "gpu-0123456789abcdef"
    calls = []

    class Pool:
        def __init__(self, cfg, hardware):
            pass

        def client(self, target, register=False):
            return FakeDocker()

        def status(self):
            return {
                "loaded": [
                    {"name": "gemma3:270m", "target": "cpu"},
                    {"name": "gemma3:270m", "target": gpu},
                ]
            }

        def model_client(self, model, target):
            calls.append((model, target))
            return SimpleNamespace(settings=Settings(state_dir=tmp_path, ollama_port=12567))

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"done": True, "eval_count": 1, "prompt_eval_count": 1})

    monkeypatch.setattr("llameros.app.Runtimes", Pool)
    app = create_app(
        Settings(state_dir=tmp_path),
        sampling=False,
        authenticator=lambda *args: True,
        gateway_transport=httpx.MockTransport(handle),
    )
    with TestClient(app, base_url="https://testserver") as client:
        created, _ = issue(client, targets=[gpu])
        assert post(client, created["key"], target=gpu).status_code == 200
        assert calls == [("gemma3:270m", gpu)]
        assert requests[0].url.port == 12567
        assert "num_gpu" not in json.loads(requests[0].content)["options"]


@pytest.mark.parametrize("path", ["/api/embed", "/v1/embeddings"])
def test_embeddings_permissions_batched_translation_and_accounting(gateway, path):
    app, client, docker, calls, response = gateway
    created, _ = issue(client)
    response.clear()
    response.update(embeddings=[[1.0, 0.0], [0.0, 1.0]], prompt_eval_count=7)
    headers = {"Authorization": "Bearer " + created["key"]}
    result = client.post(
        "/inference" + path, headers=headers, json={"model": "gemma3:270m", "input": ["one", "two"]}
    )
    assert result.status_code == 200, result.text
    assert calls[-1].url.path == "/api/embed"
    body = json.loads(calls[-1].content)
    assert body["keep_alive"] == -1 and body["truncate"] is False
    assert "stream" not in body and "num_predict" not in body["options"]
    if path.startswith("/v1"):
        assert result.json()["usage"] == {"prompt_tokens": 7, "total_tokens": 7}
        assert result.json()["data"][1]["embedding"] == [0.0, 1.0]
    else:
        assert result.json()["embeddings"] == response["embeddings"]
    usage = app.state.api_keys.list()[0]
    assert usage["prompt_tokens"] == 7 and usage["output_tokens"] == 0
    assert not app.state.jobs.scheduler.tickets
    for fields, status in [({"model": "other:latest"}, 403), ({"target": "gpu-forbidden"}, 403)]:
        result = client.post(
            "/inference" + path,
            headers=headers,
            json={"model": "gemma3:270m", "input": "one", **fields},
        )
        assert result.status_code == status


@pytest.mark.parametrize(
    "fields",
    [
        {"input": []},
        {"input": ["ok", 2]},
        {"input": ["ok"] * 65},
        {"input": "x" * 32001},
        {"input": ""},
        {"dimensions": True},
        {"dimensions": 0},
        {"truncate": "yes"},
        {"stream": True},
        {"options": {"num_gpu": 9}},
        {"encoding_format": "base64"},
    ],
)
def test_embedding_validation(gateway, fields):
    _, client, _, calls, _ = gateway
    created, _ = issue(client)
    result = client.post(
        "/inference/v1/embeddings",
        headers={"Authorization": "Bearer " + created["key"]},
        json={"model": "gemma3:270m", "input": "ok", **fields},
    )
    assert result.status_code == 400
    assert not calls


def test_malformed_embedding_response_releases_device(gateway):
    app, client, _, _, response = gateway
    created, _ = issue(client)
    response.clear()
    response.update(embeddings=[[True]])
    result = client.post(
        "/inference/api/embed",
        headers={"Authorization": "Bearer " + created["key"]},
        json={"model": "gemma3:270m", "input": "ok"},
    )
    assert result.status_code == 502
    assert not app.state.jobs.scheduler.tickets
