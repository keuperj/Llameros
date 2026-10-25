"""Offline core regression tests for Llameros behavior and integration boundaries."""

import json
import time
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest
from cryptography import x509
from fastapi.testclient import TestClient

from llameros.app import create_app
from llameros.auth import COOKIE, Sessions, verify_password
from llameros.catalog import Catalog, Links, validate_model
from llameros.config import Settings, current_user, load_settings
from llameros.docker import Docker, DockerError
from llameros.hardware import GIB, assess_fit
from llameros.storage import Store
from llameros.tls import certificates

HARDWARE = {
    "memory_available": 16 * GIB,
    "memory_total": 32 * GIB,
    "gpus": [],
    "cpu_percent": 10,
    "memory_percent": 50,
    "cpu": "Test CPU",
    "logical_cores": 8,
    "physical_cores": 4,
}


class FakeDocker:
    """In-memory runtime fake recording calls without invoking the host Docker daemon."""

    def __init__(self):
        self.running = False
        self.calls = []
        self.loaded = []

    def status(self):
        return {
            "running": self.running,
            "models": [{"name": "gemma3:270m", "size": 300_000_000}],
            "loaded": self.loaded if self.running else [],
        }

    def storage_info(self):
        return {
            "mode": "volume",
            "directory": "",
            "location": "test-models",
            "suggested_directory": "/example/models",
        }

    def inventory(self):
        return {
            "models": self.status()["models"],
            "source": "cache",
            "cached_at": 1,
            "warnings": [],
        }

    def configure_storage(self, directory, confirmed):
        if not confirmed:
            raise ValueError("Confirmation required")
        if self.running:
            raise DockerError("Stop Ollama first")
        return {"directory": directory}

    def start(self, progress=lambda value: None):
        progress({"status": "Starting test container"})
        self.running = True

    def stop(self):
        self.running = False
        self.loaded = []

    def ensure_running(self):
        if not self.running:
            raise DockerError("Not running")

    def pull(self, name, progress):
        self.ensure_running()
        progress({"status": "success"})

    def api(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body))
        if path == "/api/tags":
            return {"models": self.status()["models"]}
        if path == "/api/ps":
            return {"models": self.loaded}
        if path == "/api/generate" and not body.get("prompt"):
            self.loaded = [] if body.get("keep_alive") == 0 else [{"name": body["model"]}]
        return {
            "model": body["model"],
            "response": "Hello",
            "eval_count": 4,
            "prompt_eval_count": 2,
            "total_duration": 2_000_000_000,
            "eval_duration": 1_000_000_000,
        }


class FakeCatalog:
    """Deterministic official-model catalogue fake that makes no network requests."""

    def list(self, query):
        return ["gemma3"]

    def tags(self, family):
        return ["gemma3:270m"]

    def size(self, name):
        return 300_000_000


@pytest.fixture
def client(tmp_path):
    """Yield an HTTPS test client with temporary state and injected PAM/runtime/catalogue fakes."""
    app = create_app(
        Settings(state_dir=tmp_path),
        docker=FakeDocker(),
        catalog=FakeCatalog(),
        authenticator=lambda user, password, service: (
            user == current_user() and password == "test-secret"
        ),
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        yield client


def sign_in(client):
    """Authenticate the process-account fixture and attach its CSRF token to later requests."""
    response = client.post(
        "/api/login", json={"username": current_user(), "password": "test-secret"}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return response


def wait_job(client):
    """Poll the fake-backed lifecycle worker until terminal state, failing on a bounded timeout."""
    for _ in range(100):
        result = client.get("/api/job").json()
        if result["state"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("Job did not finish")


def test_landing_page_branding_and_repository_link(client):
    """Verify landing page branding and repository link."""
    response = client.get("/")
    assert response.status_code == 200
    assert '<img class="brand-logo" src="/static/logo.svg"' in response.text
    assert '<link rel="icon" href="/static/logo.svg" type="image/svg+xml">' in response.text
    footer = response.text.split("<footer>", 1)[1].split("</footer>", 1)[0]
    assert 'href="https://github.com/keuperj/Llameros"' in footer
    assert 'rel="noopener noreferrer"' in footer
    assert "View on GitHub" in footer
    assert 'href="https://www.llm-praxis.de/"' in footer
    assert "maintained by LLM-Praxis" in footer
    assert "keuper-labs.org" not in footer


def test_footer_logos_are_small_public_local_images(client):
    """Verify footer logos are small public local images."""
    from html.parser import HTMLParser

    class Images(HTMLParser):
        def __init__(self):
            super().__init__()
            self.images = []

        def handle_starttag(self, tag, attrs):
            if tag == "img":
                self.images.append(dict(attrs))

    footer = client.get("/").text.split("<footer>", 1)[1].split("</footer>", 1)[0]
    parser = Images()
    parser.feed(footer)
    assert [image["src"] for image in parser.images] == [
        "/static/bmftr.png",
        "/static/forschung-an-haw.png",
    ]
    for image in parser.images:
        assert image["alt"]
        assert int(image["height"]) <= 40
        assert int(image["width"]) <= 80
        response = client.get(image["src"])
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_logo_is_public_svg_and_shared_with_readme(client):
    """Verify logo is public svg and shared with readme."""
    response = client.get("/static/logo.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    logo = ElementTree.fromstring(response.content)
    assert logo.tag == "{http://www.w3.org/2000/svg}svg"
    assert logo.attrib["viewBox"] == "0 0 160 160"
    assert logo.find("{http://www.w3.org/2000/svg}title").text == "Llameros logo"
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert 'src="src/llameros/static/logo.svg"' in readme.split("# Llameros", 1)[0]


def test_authentication_and_cookie_flags(client):
    """Verify authentication and cookie flags."""
    assert client.get("/api/status").status_code == 401
    assert (
        client.post(
            "/api/login", json={"username": current_user(), "password": "wrong"}
        ).status_code
        == 401
    )
    response = sign_in(client)
    cookie = response.headers["set-cookie"]
    assert (
        COOKIE in cookie
        and "HttpOnly" in cookie
        and "Secure" in cookie
        and "SameSite=strict" in cookie
    )
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/logout", json={}).status_code == 200
    assert client.get("/api/session").status_code == 401


def test_csrf_and_origin(client):
    """Verify csrf and origin."""
    sign_in(client)
    assert (
        client.post("/api/container/start", json={}, headers={"X-CSRF-Token": "bad"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/container/start", json={}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/login", json={}, headers={"Origin": "http://testserver"}).status_code
        == 403
    )


def test_http_rejected_and_security_headers(client):
    """Verify http rejected and security headers."""
    assert client.get("http://testserver/").status_code == 400
    response = client.get("/")
    assert response.status_code == 200 and "Llameros" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200


def test_login_rate_limit(client):
    """Verify login rate limit."""
    for _ in range(5):
        assert (
            client.post(
                "/api/login", json={"username": current_user(), "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/login", json={"username": current_user(), "password": "wrong"}
        ).status_code
        == 429
    )


def test_lifecycle_generation_and_history(client):
    """Verify lifecycle generation and history."""
    sign_in(client)
    assert client.post("/api/container/start", json={}).status_code == 202
    assert wait_job(client)["state"] == "completed"
    assert client.post("/api/models/pull", json={"model": "gemma3:270m"}).status_code == 202
    assert wait_job(client)["state"] == "completed"
    for action in ("load", "unload", "load"):
        assert (
            client.post(f"/api/models/{action}", json={"model": "gemma3:270m"}).status_code == 202
        )
        assert wait_job(client)["state"] == "completed"
    assert (
        client.post(
            "/api/generate", json={"model": "gemma3:270m", "prompt": "private prompt"}
        ).status_code
        == 202
    )
    assert wait_job(client)["result"]["response"] == "Hello"
    history = client.get("/api/history").json()
    assert history["models"][0]["output_tokens"] == 4
    assert history["models"][0]["tokens_per_second"] == 4
    assert "private prompt" not in json.dumps(history)
    assert "test-secret" not in json.dumps(history)
    assert client.post("/api/container/stop", json={}).status_code == 202
    assert wait_job(client)["state"] == "completed"


def test_catalog_and_validation(client):
    """Verify catalog and validation."""
    sign_in(client)
    assert client.get("/api/catalog").json()["models"] == ["gemma3"]
    assert client.get("/api/catalog/gemma3/tags").json()["tags"] == ["gemma3:270m"]
    assert client.get("/api/fit", params={"model": "gemma3:270m"}).json()["fits"]
    assert client.get("/api/fit", params={"model": "../../bad"}).status_code == 400
    assert client.post("/api/models/load", json={"model": "$(id)"}).status_code == 400


@pytest.mark.parametrize(
    "name",
    ["../escape", "x/y", "x;id", "--privileged", "https://host/model", "gemma3:\n", "x:tag/path"],
)
def test_invalid_model_names(name):
    """Verify invalid model names."""
    with pytest.raises(ValueError):
        validate_model(name)


def test_catalog_html_and_manifest():
    """Verify catalog html and manifest."""
    parser = Links()
    parser.feed(
        '<a href="/library/gemma3">a</a><a href="/library/gemma3">b</a><a href="/library/../bad">bad</a>'
    )
    assert parser.names == ["gemma3"]
    catalog = Catalog()
    catalog._get = lambda *args, **kwargs: {"layers": [{"size": 10}, {"size": 20}]}
    assert catalog.size("gemma3:270m") == 30
    catalog._get = lambda *args, **kwargs: {"layers": []}
    with pytest.raises(ValueError):
        catalog.size("gemma3:cloud")


def test_memory_fit():
    """Verify memory fit."""
    assert assess_fit(GIB, HARDWARE)["fits"]
    assert not assess_fit(30 * GIB, HARDWARE)["fits"]
    gpu_hw = {**HARDWARE, "gpus": [{"vendor": "nvidia", "free": 8 * GIB}]}
    assert assess_fit(GIB, gpu_hw, "nvidia")["placement"] == "gpu"
    assert assess_fit(GIB, gpu_hw, "cpu")["placement"] != "gpu"


def test_sessions_expire_and_rate_limit():
    """Verify sessions expire and rate limit."""
    sessions = Sessions(60)
    token, session = sessions.create("tester")
    assert sessions.get(token)["username"] == "tester"
    session["expires"] = 0
    assert sessions.get(token) is None
    assert all(sessions.allow_attempt() for _ in range(5))
    assert not sessions.allow_attempt()


def test_pam_rejects_other_users_and_empty_passwords():
    """Verify pam rejects other users and empty passwords."""
    assert not verify_password("not-the-current-user", "password", "login")
    assert not verify_password(current_user(), "", "login")
    assert not verify_password(current_user(), "bad\x00password", "login")


def test_docker_arguments_and_gpu_modes(tmp_path):
    """Verify docker arguments and gpu modes."""
    cfg = Settings(state_dir=tmp_path)
    args = Docker(cfg).run_args()
    assert "-p" not in args
    assert "OLLAMA_HOST=127.0.0.1:11434" in args
    assert "no-new-privileges:true" in args and "ALL" in args
    assert "--privileged" not in args and "--gpus" not in args
    assert "/var/run/docker.sock" not in " ".join(args)
    assert "--gpus" in Docker(replace(cfg, gpu="nvidia")).run_args()
    amd = Docker(replace(cfg, gpu="amd")).run_args()
    assert "/dev/kfd" in amd and amd[-1] == cfg.ollama_rocm_image


def test_rootful_guard_and_remote_daemon(tmp_path, monkeypatch):
    """Verify rootful guard and remote daemon."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    docker = Docker(Settings(state_dir=tmp_path, allow_rootful_docker=False))
    docker.command = lambda *args, **kwargs: (
        "unix:///var/run/docker.sock" if args[0] == "context" else '["name=seccomp"]'
    )
    with pytest.raises(DockerError, match="Rootful"):
        docker.check_daemon()
    docker.settings = replace(docker.settings, allow_rootful_docker=True)
    docker.check_daemon()
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2375")
    with pytest.raises(DockerError, match="local Unix"):
        docker.check_daemon()


def test_container_ownership(tmp_path):
    """Verify container ownership."""
    docker = Docker(Settings(state_dir=tmp_path))

    def command(*args, **kwargs):
        if args[0] == "container":
            return docker.settings.container_name
        return json.dumps([{"Config": {"Labels": {"io.llameros.owner": "someone-else"}}}])

    docker.command = command
    with pytest.raises(DockerError, match="ownership"):
        docker.inspect()


def test_store_retention(tmp_path):
    """Verify store retention."""
    store = Store(tmp_path / "history.sqlite3")
    store.event("start", "gemma3:270m")
    store.sample({"hardware": HARDWARE}, 30)
    assert len(store.history()["samples"]) == 1
    with store.connect() as db:
        db.execute("UPDATE events SET timestamp=0")
    store.sample({"hardware": HARDWARE}, 30)
    assert not store.history()["events"]
    assert (tmp_path / "history.sqlite3").stat().st_mode & 0o777 == 0o600


def test_tls_generation_and_reuse(tmp_path):
    """Verify tls generation and reuse."""
    settings = Settings(state_dir=tmp_path)
    cert, key = certificates(settings)
    initial = cert.read_bytes()
    parsed = x509.load_pem_x509_certificate(initial)
    assert "localhost" in parsed.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.DNSName)
    assert key.stat().st_mode & 0o777 == 0o600
    assert certificates(settings) == (cert, key)
    assert cert.read_bytes() == initial


def test_config(tmp_path):
    """Verify config."""
    path = tmp_path / "config.toml"
    path.write_text('host = "0.0.0.0"\nport = 9443\n')
    assert load_settings(path).port == 9443
    path.write_text('allow_rootful_docker = "false"\n')
    with pytest.raises(ValueError):
        load_settings(path)
    path.write_text("unknown = true\n")
    with pytest.raises(ValueError):
        load_settings(path)
    with pytest.raises(ValueError):
        Settings(port=80)


def test_example_config_uses_current_home(tmp_path, monkeypatch):
    """Verify example config uses current home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    settings = load_settings(example)
    assert settings.state_dir == tmp_path / ".local/share/llameros"
    assert not settings.allow_rootful_docker


def test_default_config_discovery_uses_current_home(tmp_path, monkeypatch):
    """Verify default config discovery uses current home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".config/llameros/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('port = 9443\nstate_dir = "~/.local/share/llameros"\n')
    settings = load_settings()
    assert settings.port == 9443
    assert settings.state_dir == tmp_path / ".local/share/llameros"


def test_docker_errors_visible(tmp_path):
    """Verify docker errors visible."""

    class BrokenDocker(FakeDocker):
        def status(self):
            raise DockerError("Rootful Docker refused")

    app = create_app(
        Settings(state_dir=tmp_path),
        docker=BrokenDocker(),
        authenticator=lambda *args: True,
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        sign_in(client)
        assert "runtime-error" in client.get("/api/status").json()["ollama"]["error"]
