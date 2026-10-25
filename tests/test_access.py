"""Offline access regression tests for Llameros behavior and integration boundaries."""

from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker, sign_in

from llameros.app import create_app
from llameros.config import Settings


def test_access_endpoints_use_configured_port_and_require_login(tmp_path):
    """Verify access endpoints use configured port and require login."""
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
    """Verify model boxes and independent keys precede configuration."""
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "src/llameros/static"
    html = (static / "index.html").read_text()
    assert (
        html.index('id="runtime-models"')
        < html.index('id="key-management"')
        < html.index('id="config-menu"')
    )
    assert 'id="key-usage"' in html
    assert 'id="api-key-models"' in html
    assert 'id="api-key-max-tokens"' not in html
    assert 'id="access-active-model"' not in html


def test_key_table_collapsed_form_and_masked_secret():
    """Verify key table collapsed form and masked secret."""
    from html.parser import HTMLParser
    from pathlib import Path

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.nodes = {}
            self.expiry_options = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.nodes[attrs["id"]] = (tag, attrs)
            if tag == "option" and attrs.get("value") in {"7", "30", "365", "never"}:
                self.expiry_options.append(attrs)

    static = Path(__file__).resolve().parents[1] / "src/llameros/static"
    html = (static / "index.html").read_text()
    parser = Parser()
    parser.feed(html)
    assert parser.nodes["api-keys"][0] == "tbody"
    assert parser.nodes["api-key-add"][0] == "details"
    assert "open" not in parser.nodes["api-key-add"][1]
    assert html.index('id="api-keys"') < html.index('id="api-key-add"')
    assert parser.nodes["api-key-secret"][1]["type"] == "password"
    assert "readonly" in parser.nodes["api-key-secret"][1]
    assert "disabled" in parser.nodes["api-key-copy"][1]
    assert {option["value"] for option in parser.expiry_options} == {"7", "30", "365", "never"}
    assert "selected" in next(
        option for option in parser.expiry_options if option["value"] == "never"
    )
    css = (static / "style.css").read_text()
    assert "10 * var(--key-row-height)" in css
    assert "table-layout:fixed" in css
    assert "overflow-y:auto" in css
