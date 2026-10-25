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


def test_usage_tables_have_compact_matching_headers():
    class Headers(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_header = False
            self.labels = []

        def handle_starttag(self, tag, attrs):
            if tag == "th":
                self.in_header = True

        def handle_endtag(self, tag):
            if tag == "th":
                self.in_header = False

        def handle_data(self, data):
            if self.in_header:
                self.labels.append(data)

    static = Path(__file__).resolve().parents[1] / "src/llameros/static"
    html = (static / "index.html").read_text()
    parser = Headers()
    parser.feed(html[html.index('class="usage-tiles"') :])
    assert parser.labels == [
        "Model",
        "Requests",
        "Input",
        "Output",
        "Tokens/s",
        "Seconds",
        "Key",
        "User",
        "Status",
        "Requests",
        "Failed",
        "Input",
        "Output",
        "Seconds",
    ]
    css = (static / "style.css").read_text()
    assert "min-width:640px" not in css
    assert "min-width:840px" not in css
    assert "table-layout:fixed;width:100%;min-width:0" in css
