"""Public Ollama catalogue discovery and conservative sizing for the Llameros admin dashboard.

Read public Ollama catalogue pages and registry manifests without credentials.
"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from urllib.parse import urlencode

import httpx

from .hardware import assess_fit

MODEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$")


def validate_model(name: str) -> str:
    """Validate an official-library lifecycle name; inference namespace rules are separate."""
    if not MODEL.fullmatch(name):
        raise ValueError("Use an official library model name, for example gemma3:270m")
    return name


class Links(HTMLParser):
    """Extract official model links and conservative rounded sizes from public catalogue HTML."""

    def __init__(self):
        """Initialize parser state or the catalogue response cache for a fresh instance."""
        super().__init__()
        self.names: list[str] = []
        self.sizes: dict[str, int] = {}
        self.active = ""
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        """Track relevant catalogue links or readable-description elements as HTML begins."""
        if tag != "a":
            return
        self.active = ""
        self.text = []
        href = dict(attrs).get("href", "")
        if href.startswith("/library/"):
            name = href.removeprefix("/library/")
            if MODEL.fullmatch(name) and name not in self.names:
                self.names.append(name)
            if MODEL.fullmatch(name):
                self.active = name

    def handle_data(self, data):
        """Collect only text belonging to the active link or visible README section."""
        if self.active:
            self.text.append(data)

    def handle_endtag(self, tag):
        """Finalize link size text or close readable-description nesting as HTML ends."""
        if tag == "a" and self.active:
            text = " ".join(self.text)
            match = re.search(r"\b(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)\b", text)
            if match and not re.search(r"\bcloud\b", self.active + " " + text, re.I):
                # Assume rounding to the displayed precision, then add 1% headroom.
                number = match[1]
                precision = len(number.partition(".")[2])
                upper = float(number) + 0.5 * 10**-precision
                unit = 1000 ** {"KB": 1, "MB": 2, "GB": 3, "TB": 4}[match[2]]
                self.sizes[self.active] = int(upper * unit * 1.01)
            self.active = ""
            self.text = []


class Catalog:
    """Cache public catalogue metadata for discovery, descriptions, and model-fit suggestions."""

    def __init__(self):
        """Initialize parser state or the catalogue response cache for a fresh instance."""
        self.cache: dict[str, tuple[float, object]] = {}

    def _get(self, url: str, json: bool = False):
        """Fetch public text/JSON without redirects or environment proxies and cache it for ten minutes."""
        cached = self.cache.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.get(url)
            response.raise_for_status()
        value = response.json() if json else response.text
        self.cache[url] = (time.monotonic() + 600, value)
        return value

    def list(self, query: str = "") -> list[str]:
        """Return model families in upstream popularity/search order, leaving tag selection separate."""
        query = query.strip()
        # Tags are selected from the family page; send only the family to search.
        family = query.partition(":")[0]
        url = "https://ollama.com/library?sort=popular"
        if family:
            url = "https://ollama.com/search?" + urlencode({"q": family, "sort": "popular"})
        parser = Links()
        parser.feed(self._get(url))
        # The search page can match descriptions, not just model names. Preserve
        # upstream order and keep the existing official-library-only boundary.
        return [name for name in parser.names if ":" not in name]

    def description(self, name: str) -> dict:
        """Return readable model information extracted from the public page, never raw HTML."""
        validate_model(name)
        url = f"https://ollama.com/library/{name}"
        parser = Description()
        parser.feed(self._get(url))
        return {
            "model": name,
            "url": url,
            "summary": parser.summary,
            "description": re.sub(r"\n[ \t\n]*\n", "\n\n", "".join(parser.content)).strip()[:100000]
            or parser.summary
            or "No model description is available.",
        }

    def tags(self, family: str) -> list[str]:
        """Return validated tags discovered on the selected official model family page."""
        validate_model(family)
        if ":" in family:
            raise ValueError("Choose a model family without a tag")
        parser = Links()
        parser.feed(self._get(f"https://ollama.com/library/{family}/tags"))
        return [name for name in parser.names if name.startswith(family + ":")]

    def size(self, name: str) -> int:
        """Sum downloadable manifest-layer bytes or reject cloud-only/unsupported manifests."""
        validate_model(name)
        family, _, tag = name.partition(":")
        manifest = self._get(
            f"https://registry.ollama.ai/v2/library/{family}/manifests/{tag or 'latest'}",
            json=True,
        )
        sizes = [int(layer["size"]) for layer in manifest.get("layers", [])]
        if not sizes or any(size < 0 for size in sizes):
            raise ValueError(
                "This model does not expose downloadable weights (cloud-only or unsupported manifest)"
            )
        return sum(sizes)

    def variants(
        self,
        family: str,
        hardware: dict,
        gpu_mode: str,
        parallel: int = 2,
        context_length: int = 4096,
    ) -> list[dict]:
        """Return family tags with conservative CPU/runtime fit estimates and explicit unknown sizes."""
        validate_model(family)
        if ":" in family:
            raise ValueError("Choose a model family without a tag")
        parser = Links()
        parser.feed(self._get(f"https://ollama.com/library/{family}/tags"))
        variants = []
        for name in parser.names:
            if not name.startswith(family + ":"):
                continue
            size = parser.sizes.get(name)
            cpu_fit = (
                None
                if size is None
                else assess_fit(size, hardware, "cpu", parallel, context_length)["fits"]
            )
            variants.append(
                {
                    "name": name,
                    "download_bytes": size,
                    "fits": None
                    if size is None
                    else assess_fit(size, hardware, gpu_mode, parallel, context_length)["fits"],
                    "cpu_fits": cpu_fit,
                    "size_source": "rounded catalogue size" if size is not None else "unknown",
                }
            )
        return sorted(variants, key=lambda item: {True: 0, False: 1, None: 2}[item["fits"]])


class Description(HTMLParser):
    """Extract readable README text only, never forward external HTML/scripts."""

    def __init__(self):
        """Initialize parser state or the catalogue response cache for a fresh instance."""
        super().__init__()
        self.summary = ""
        self.content: list[str] = []
        self.stack: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        """Track relevant catalogue links or readable-description elements as HTML begins."""
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name") == "description":
            self.summary = attrs.get("content", "")
        if attrs.get("id") == "readme" and not self.stack:
            self.stack.append(tag)
        elif self.stack and tag not in {
            "br",
            "hr",
            "img",
            "input",
            "meta",
            "link",
            "source",
            "wbr",
        }:
            self.stack.append(tag)
        if self.stack:
            if tag in {"script", "style", "form", "textarea"}:
                self.hidden += 1
            if not self.hidden and tag in {
                "p",
                "div",
                "br",
                "li",
                "h1",
                "h2",
                "h3",
                "h4",
                "pre",
                "tr",
            }:
                self.content.append("\n")

    def handle_endtag(self, tag):
        """Finalize link size text or close readable-description nesting as HTML ends."""
        if not self.stack or tag not in self.stack:
            return
        if tag in {"script", "style", "form", "textarea"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "pre", "tr"}:
            self.content.append("\n")
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        self.stack = self.stack[:index]

    def handle_data(self, data):
        """Collect only text belonging to the active link or visible README section."""
        if self.stack and not self.hidden:
            self.content.append(data)
