"""Shared dashboard branding, local assets, and accessible statistics page shells."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import llameros
from statistics_server.app import create_app


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize("route", ["/", "/admin"])
def test_branded_pages_have_local_images_navigation_and_project_footer(tmp_path, route):
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        response = client.get(route)
        assert response.status_code == 200
        page = Page()
        page.feed(response.text)
        ids = [attrs["id"] for _, attrs in page.elements if "id" in attrs]
        assert len(ids) == len(set(ids))
        assert {"main-content", "entries", "status"} <= set(ids)
        for tag in ("header", "main", "footer", "nav"):
            assert any(element == tag for element, _ in page.elements)
        assert any(attrs.get("class") == "skip-link" for _, attrs in page.elements)
        assert sum(attrs.get("aria-current") == "page" for _, attrs in page.elements) == 1
        styles = [
            attrs["href"]
            for tag, attrs in page.elements
            if tag == "link" and attrs.get("rel") == "stylesheet"
        ]
        assert styles == ["brand/style.css", "table.css"]
        for tag, attrs in page.elements:
            if tag == "img":
                assert attrs["src"].startswith("brand/") and attrs["alt"]
                image = client.get("/" + attrs["src"])
                assert image.status_code == 200
                assert image.headers["content-type"].startswith("image/")
            if tag == "a" and attrs.get("target") == "_blank":
                assert attrs["rel"] == "noopener noreferrer"
        footer = response.text.split("<footer>", 1)[1].split("</footer>", 1)[0]
        assert 'href="https://github.com/keuperj/Llameros"' in footer
        assert 'href="https://www.llm-praxis.de/"' in footer
        assert "forschung-an-haw.png" in footer and "bmftr.png" in footer
        assert "img-src 'self'" in response.headers["content-security-policy"]
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["referrer-policy"] == "no-referrer"
        assert not response.cookies


def test_brand_assets_are_shared_packaged_files_and_allowlisted(tmp_path):
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        for name in ("logo.svg", "style.css", "bmftr.png", "forschung-an-haw.png"):
            response = client.get("/brand/" + name)
            assert response.status_code == 200
            assert (
                response.content == (Path(llameros.__file__).parent / "static" / name).read_bytes()
            )
        for name in ("app.js", "index.html", "missing.png"):
            assert client.get("/brand/" + name).status_code == 404
        css = client.get("/table.css").text
        assert "@media" in css and "focus-visible" in css
        assert "overflow:auto" in css and "[aria-sort=ascending]" in css


def test_admin_page_keeps_all_javascript_control_ids(tmp_path):
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        page = Page()
        page.feed(client.get("/admin").text)
        ids = {attrs["id"]: attrs for _, attrs in page.elements if "id" in attrs}
        expected = {
            "login-form",
            "username",
            "password",
            "feedback",
            "dashboard",
            "logout",
            "refresh",
            "overview",
            "status",
            "page-info",
            "entries",
            "previous",
            "next",
        }
        assert expected <= ids.keys()
        assert "hidden" in ids["dashboard"]
        assert ids["password"]["type"] == "password"
