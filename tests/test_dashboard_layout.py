"""Offline dashboard layout regression tests for Llameros behavior and integration boundaries."""

from html.parser import HTMLParser
from pathlib import Path


def test_playground_closed_and_scroll_regions_accessible():
    """Verify playground closed and scroll regions accessible."""

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
    for name in ("admin-activity-scroll", "installed", "model-usage-scroll", "key-usage-scroll"):
        attrs = parser.nodes[name][1]
        assert attrs["tabindex"] == "0" and attrs["role"] == "region"
    assert parser.nodes["runtime"][1]["aria-live"] == "polite"
    for name in (
        "key-management",
        "usage-history",
        "activity-log",
        "benchmark-history",
        "config-menu",
    ):
        tag, attrs = parser.nodes[name]
        assert tag == "details" and "open" not in attrs
    assert parser.nodes["hardware-tab"][1]["aria-selected"] == "true"
    assert "hidden" in parser.nodes["token-plot"][1]
    assert parser.nodes["activity-timeframe"][0] == "select"
    css = (static / "style.css").read_text()
    assert "max-height:calc(10 * var(--event-height))" in css
    assert "height:var(--event-height)" in css
    assert "overflow-y:auto" in css
    assert ".usage-tiles{display:grid;grid-template-columns:minmax(0,1fr)" in css
    assert "height:auto;padding:10px 8px;line-height:1.45;vertical-align:top" in css
    assert "height:var(--usage-row-height)" not in css
    html = (static / "index.html").read_text()
    assert html.index("/static/usage.js") < html.index("/static/app.js")
    assert html.index("/static/actions.js") < html.index("/static/app.js")
    assert 'id="events-heading">Admin activity log' in html
    assert parser.nodes["events"][0] == "tbody"
    assert html.index('id="activity-log"') > html.index('id="usage-history"')
    assert ".action-error{" in css
    assert ".action-error[hidden]{display:none}" in css


def test_usage_tables_have_compact_matching_headers():
    """Verify usage tables have compact matching headers."""

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
    parser.feed(html[html.index('class="usage-tiles"') : html.index('id="activity-log"')])
    assert parser.labels == [
        "Model",
        "Requests",
        "Input",
        "Output",
        "Tokens/s",
        "Seconds",
        "Model limits",
        "Key",
        "User",
        "Status",
        "Requests",
        "Failed",
        "Input",
        "Output",
        "Seconds",
        "Key limits",
        "User limits",
    ]
    css = (static / "style.css").read_text()
    assert "min-width:640px" not in css
    assert "min-width:840px" not in css
    assert "table-layout:fixed;width:100%;min-width:0" in css
    assert "min-width:850px" not in css
    assert "min-width:1100px" not in css
    assert "content:attr(data-label)" in css
    assert "overflow-wrap:anywhere" in css
