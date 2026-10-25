from html.parser import HTMLParser
from pathlib import Path


def test_playground_closed_and_scroll_regions_accessible():
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.nodes = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.nodes[attrs["id"]] = (tag, attrs)

    static = Path(__file__).resolve().parents[1] / "src/llameros/static"
    parser = Parser()
    parser.feed((static / "index.html").read_text())
    tag, attrs = parser.nodes["try-model"]
    assert tag == "details" and "open" not in attrs
    for name in ("events", "installed", "model-usage-scroll", "key-usage-scroll"):
        attrs = parser.nodes[name][1]
        assert attrs["tabindex"] == "0" and attrs["role"] == "region"
    assert parser.nodes["runtime"][1]["aria-live"] == "polite"
    css = (static / "style.css").read_text()
    assert "max-height:calc(10 * var(--event-height))" in css
    assert "height:var(--event-height)" in css
    assert "overflow-y:auto" in css
    assert "10 * var(--usage-row-height)" in css
    assert "height:var(--usage-row-height)" in css
    html = (static / "index.html").read_text()
    assert html.index("/static/usage.js") < html.index("/static/app.js")
