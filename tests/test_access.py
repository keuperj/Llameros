from html.parser import HTMLParser

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


def test_access_panel_is_collapsed_inside_runtime_with_readonly_copy_fields(tmp_path):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.runtime = False
            self.inside = False
            self.fields = set()
            self.copies = set()

        def handle_data(self, data):
            if data == "Ollama runtime":
                self.runtime = True

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get("id") == "access-model":
                assert self.runtime and tag == "details" and "open" not in attrs
                self.inside = True
            if self.inside and attrs.get("id") in {
                "access-url",
                "access-openai-url",
                "access-install",
                "access-code",
            }:
                assert "readonly" in attrs
                self.fields.add(attrs["id"])
            if self.inside and "data-copy" in attrs:
                assert "disabled" in attrs
                self.copies.add(attrs["data-copy"])

        def handle_endtag(self, tag):
            if tag == "section":
                self.runtime = False
            if tag == "details":
                self.inside = False

    app = create_app(Settings(state_dir=tmp_path), docker=FakeDocker(), sampling=False)
    with TestClient(app, base_url="https://testserver") as client:
        parser = Parser()
        parser.feed(client.get("/").text)
        assert (
            parser.fields
            == parser.copies
            == {"access-url", "access-openai-url", "access-install", "access-code"}
        )
