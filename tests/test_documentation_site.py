"""Offline checks for the static Pages shell and shared admin-board visual palette."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_documentation_theme_matches_dashboard_colors_and_accessibility():
    css = (ROOT / "assets/css/documentation.css").read_text()
    dashboard = (ROOT / "src/llameros/static/style.css").read_text()
    for name in ("bg", "card", "line", "text", "muted", "green", "orange"):
        color = re.search(rf"--{name}:\s*(#[a-f0-9]+)", dashboard)[1]
        assert f"--{name}: {color}" in css
    assert "@media (max-width: 800px)" in css
    assert "@media print" in css
    assert ":focus-visible" in css
    assert "overflow-x: auto" in css
    layout = (ROOT / "_layouts/default.html").read_text()
    assert 'href="#main-content"' in layout
    assert 'id="main-content"' in layout
    assert 'aria-label="Documentation"' in layout
    assert "{{ content }}" in layout
    assert "| relative_url" in layout
    assert "<script" not in layout
    assert "@import" not in css


def test_readme_assets_and_navigation_targets_exist():
    readme = (ROOT / "README.md").read_text()
    for source in re.findall(r'<img[^>]+src="([^"]+)"', readme):
        assert (ROOT / source).is_file(), source
        assert not source.startswith("src/")
    layout = (ROOT / "_layouts/default.html").read_text()
    for target in re.findall(r"'([^']+\.html)' \| relative_url", layout):
        assert (ROOT / (target.lstrip("/")[:-5] + ".md")).is_file(), target
    for asset in ("logo.svg", "bmftr.png", "forschung-an-haw.png"):
        assert (ROOT / "assets/images" / asset).read_bytes() == (
            ROOT / "src/llameros/static" / asset
        ).read_bytes()


def test_pages_configuration_renders_existing_markdown_and_excludes_runtime_data():
    config = (ROOT / "_config.yml").read_text()
    for plugin in ("jekyll-optional-front-matter", "jekyll-readme-index", "jekyll-relative-links"):
        assert plugin in config
    assert "layout: default" in config
    assert 'url: "https://www.llameros.org"' in config
    assert 'baseurl: ""' in config
    assert (ROOT / "CNAME").read_text().strip() == "www.llameros.org"
    for private_path in ("state", "statistics-state", "backups", "config.toml", ".venv"):
        assert f"  - {private_path}\n" in config
    assert "  - statistics_server\n" not in config
