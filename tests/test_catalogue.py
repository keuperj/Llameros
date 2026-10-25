"""Offline catalogue regression tests for Llameros behavior and integration boundaries."""

from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker, sign_in

from llameros.app import create_app
from llameros.catalog import Catalog, Links
from llameros.config import Settings

TAGS = """
<a href="/library/example:huge"><span>example:huge</span> 99GB • 32K context</a>
<a href="/library/example:small"><span>example:small</span> 292MB • Text</a>
<a href="/library/example:small">example:small</a>
<a href="/library/example:cloud">example:cloud • 1GB • Cloud</a>
<a href="/library/example:unknown">example:unknown • 32K context</a>
<a href="/library/example:latest"><span>example:latest</span> 1.0 GB</a>
<a href="/library/unrelated:small">unrelated:small 1GB</a>
"""


def test_parser_sizes_duplicates_and_cloud():
    """Verify parser sizes duplicates and cloud."""
    parser = Links()
    parser.feed(TAGS)
    assert parser.names.count("example:small") == 1
    assert parser.sizes["example:small"] == int(292.5 * 1000**2 * 1.01)
    assert parser.sizes["example:latest"] == int(1.05 * 1000**3 * 1.01)
    assert "example:cloud" not in parser.sizes
    assert "example:unknown" not in parser.sizes


def test_variants_compatible_first_unknown_last_and_memory_reassessed():
    """Verify variants compatible first unknown last and memory reassessed."""
    catalog = Catalog()
    catalog._get = lambda *args, **kwargs: TAGS
    variants = catalog.variants("example", HARDWARE, "cpu")
    assert [item["name"] for item in variants] == [
        "example:small",
        "example:latest",
        "example:huge",
        "example:cloud",
        "example:unknown",
    ]
    assert [item["fits"] for item in variants] == [True, True, False, None, None]
    assert not any(
        item["fits"]
        for item in catalog.variants("example", {**HARDWARE, "memory_available": 0}, "cpu")
    )


def test_popularity_order_and_search():
    """Verify popularity order and search."""
    catalog = Catalog()
    seen = []

    def get(url):
        seen.append(url)
        return '<a href="/library/popular">Popular</a><a href="/library/second">Second</a>'

    catalog._get = get
    assert catalog.list() == ["popular", "second"]
    assert seen == ["https://ollama.com/library?sort=popular"]
    assert catalog.list("SECOND") == ["popular", "second"]
    assert seen[-1] == "https://ollama.com/search?q=SECOND&sort=popular"


def test_search_queries_upstream_and_handles_explicit_tags():
    """Verify search queries upstream and handles explicit tags."""
    catalog = Catalog()
    urls = []

    def get(url):
        urls.append(url)
        # Matches can be in descriptions, or absent from the default library page.
        return '<a href="/library/smollm2">Small CPU language model</a><a href="/library/gemma3">Compact</a>'

    catalog._get = get
    assert catalog.list("small cpu") == ["smollm2", "gemma3"]
    assert urls[-1] == "https://ollama.com/search?q=small+cpu&sort=popular"
    assert catalog.list("smollm2:135m") == ["smollm2", "gemma3"]
    assert urls[-1] == "https://ollama.com/search?q=smollm2&sort=popular"


def test_small_cpu_models_fit_without_gpu_or_multi_gib_fixed_overhead():
    """Verify small cpu models fit without gpu or multi gib fixed overhead."""
    from llameros.hardware import GIB, assess_fit

    hardware = {**HARDWARE, "memory_available": int(1.3 * GIB), "gpus": []}
    assert assess_fit(89_000_000, hardware, "cpu")["fits"]
    assert assess_fit(295_425_000, hardware, "cpu")["fits"]
    assert assess_fit(295_425_000, hardware, "cpu")["placement"] == "cpu"
    assert not assess_fit(4 * GIB, hardware, "cpu")["fits"]
    assert not assess_fit(89_000_000, {**hardware, "memory_available": 256 * 1024**2}, "cpu")[
        "fits"
    ]


def test_cpu_only_api_reports_ram_fit_without_gpu_and_uses_same_exact_estimate(tmp_path):
    """Verify cpu only api reports ram fit without gpu and uses same exact estimate."""
    from llameros.hardware import GIB

    hardware = {
        **HARDWARE,
        "memory_available": int(1.3 * GIB),
        "gpus": [{"vendor": "nvidia", "free": 32 * GIB}],
    }
    catalog = Catalog()
    catalog._get = lambda *args, **kwargs: TAGS
    catalog.size = lambda name: 292_000_000
    app = create_app(
        Settings(state_dir=tmp_path, gpu="nvidia"),
        catalog=catalog,
        docker=FakeDocker(),
        authenticator=lambda *args: True,
        hardware_provider=lambda: hardware,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        assert client.get("/api/catalog/example/compatibility?cpu_only=true").status_code == 401
        sign_in(client)
        data = client.get("/api/catalog/example/compatibility?cpu_only=true").json()
        assert data["cpu_only"] is True
        assert data["fits"] is True
        assert next(v for v in data["variants"] if v["name"] == "example:small")["cpu_fits"] is True
        assert next(v for v in data["variants"] if v["name"] == "example:huge")["cpu_fits"] is False
        exact = client.get("/api/fit?model=example:small&cpu_only=true").json()
        assert exact["fits"] is True and exact["placement"] == "cpu"
        assert client.get("/api/fit?model=example:small").json()["placement"] == "gpu"
        hardware["memory_available"] = 256 * 1024**2
        assert client.get("/api/fit?model=example:small&cpu_only=true").json()["fits"] is False


def test_compatibility_api_authentication_family_and_unknown(tmp_path):
    """Verify compatibility api authentication family and unknown."""
    catalog = Catalog()
    catalog._get = lambda *args, **kwargs: TAGS
    app = create_app(
        Settings(state_dir=tmp_path),
        catalog=catalog,
        docker=FakeDocker(),
        authenticator=lambda *args: True,
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        assert client.get("/api/catalog/example/compatibility").status_code == 401
        sign_in(client)
        response = client.get("/api/catalog/example/compatibility")
        assert response.status_code == 200
        assert response.json()["fits"] is True
        assert response.json()["variants"][0]["name"] == "example:small"
        assert client.get("/api/catalog/example:small/compatibility").status_code == 400
        assert client.get("/api/catalog/missing/compatibility").json()["fits"] is None
        catalog._get = lambda *args, **kwargs: '<a href="/library/example:huge">99GB</a>'
        assert client.get("/api/catalog/example/compatibility").json()["fits"] is False


def test_config_menu_contains_storage_and_is_collapsed(tmp_path):
    """Verify config menu contains storage and is collapsed."""
    from html.parser import HTMLParser

    class MenuParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inside = False
            self.storage_inside = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "details" and attrs.get("id") == "config-menu":
                assert "open" not in attrs
                self.inside = True
            if attrs.get("id") == "storage-form":
                self.storage_inside = self.inside

        def handle_endtag(self, tag):
            if tag == "details":
                self.inside = False

    parser = MenuParser()
    app = create_app(Settings(state_dir=tmp_path), docker=FakeDocker(), sampling=False)
    with TestClient(app, base_url="https://testserver") as client:
        parser.feed(client.get("/").text)
        assert parser.storage_inside
        assert 'id="cpu-only" type="checkbox"' in client.get("/").text
        assert client.get("/static/catalogue.js").status_code == 200


def test_model_info_and_maintainer_links(tmp_path):
    """Verify model info and maintainer links."""
    from html.parser import HTMLParser

    class LinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
            self.active = None

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.active = {**dict(attrs), "text": ""}
                self.links.append(self.active)

        def handle_data(self, data):
            if self.active is not None:
                self.active["text"] += data

        def handle_endtag(self, tag):
            if tag == "a":
                self.active = None

    app = create_app(Settings(state_dir=tmp_path), docker=FakeDocker(), sampling=False)
    with TestClient(app, base_url="https://testserver") as client:
        parser = LinkParser()
        parser.feed(client.get("/").text)
    info = next(link for link in parser.links if link.get("id") == "model-info-source")
    assert info["text"] == "Open on Ollama"
    maintainer = next(
        link for link in parser.links if link.get("href") == "https://www.llm-praxis.de/"
    )
    assert maintainer["text"] == "maintained by LLM-Praxis"
    for link in (info, maintainer):
        assert link["target"] == "_blank"
        assert {"noopener", "noreferrer"} <= set(link["rel"].split())
    github = next(link for link in parser.links if "github.com" in link.get("href", ""))
    assert parser.links.index(maintainer) == parser.links.index(github) + 1


def test_model_description_text_extraction_and_api(tmp_path):
    """Verify model description text extraction and api."""
    from llameros.catalog import Description

    html = '<meta name="description" content="Summary"><div id="readme"><h2>About</h2><p>Useful model</p><script>evil()</script><form>Private editor<textarea>draft</textarea></form><p>Usage</p></div><footer>Unrelated</footer>'
    parser = Description()
    parser.feed(html)
    text = "".join(parser.content)
    assert "Useful model" in text and "Usage" in text
    assert not any(word in text for word in ["evil", "Private", "draft", "Unrelated"])
    catalog = Catalog()
    catalog._get = lambda url: html
    app = create_app(
        Settings(state_dir=tmp_path),
        docker=FakeDocker(),
        catalog=catalog,
        authenticator=lambda *args: True,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        assert client.get("/api/model-info?model=gemma3").status_code == 401
        sign_in(client)
        data = client.get("/api/model-info?model=gemma3:270m").json()
        assert "Useful model" in data["description"]
        assert "<script>" not in data["description"]
        assert data["url"] == "https://ollama.com/library/gemma3:270m"
        assert client.get("/api/model-info?model=../escape").status_code == 400
