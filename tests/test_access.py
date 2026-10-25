from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker, sign_in

from llameros.app import create_app
from llameros.config import Settings


def test_access_endpoints_use_configured_port_and_require_login(tmp_path):
    app = create_app(
        Settings(state_dir=tmp_path, ollama_port=12567),
        docker=FakeDocker(),
        authenticator=lambda *args: True,
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        assert client.get("/api/status").status_code == 401
        sign_in(client)
        assert client.get("/api/status").json()["access"] == {
            "base_url": "http://127.0.0.1:12567",
            "openai_url": "http://127.0.0.1:12567/v1",
        }
        assert client.get("/static/access.js").status_code == 200


def test_model_boxes_and_independent_keys_precede_configuration():
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "src/llameros/static"
    html = (static / "index.html").read_text()
    assert (
        html.index('id="runtime-models"')
        < html.index('id="key-management"')
        < html.index('id="config-menu"')
    )
    assert 'id="key-usage"' in html
    assert 'id="api-key-models" multiple' in html
    assert 'id="api-key-max-tokens"' in html
    assert 'id="access-active-model"' not in html
