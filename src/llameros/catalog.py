"""Read public Ollama catalogue pages and registry manifests without credentials."""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser

import httpx

from .hardware import assess_fit

MODEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$")


def validate_model(name: str) -> str:
    if not MODEL.fullmatch(name):
        raise ValueError("Use an official library model name, for example gemma3:270m")
    return name


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names: list[str] = []
        self.sizes: dict[str, int] = {}
        self.active = ""
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
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
        if self.active:
            self.text.append(data)

    def handle_endtag(self, tag):
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
    def __init__(self):
        self.cache: dict[str, tuple[float, object]] = {}

    def _get(self, url: str, json: bool = False):
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
        parser = Links()
        parser.feed(self._get("https://ollama.com/library?sort=popular"))
        return [name for name in parser.names if ":" not in name and query.lower() in name.lower()]

    def tags(self, family: str) -> list[str]:
        validate_model(family)
        if ":" in family:
            raise ValueError("Choose a model family without a tag")
        parser = Links()
        parser.feed(self._get(f"https://ollama.com/library/{family}/tags"))
        return [name for name in parser.names if name.startswith(family + ":")]

    def size(self, name: str) -> int:
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

    def variants(self, family: str, hardware: dict, gpu_mode: str) -> list[dict]:
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
            variants.append(
                {
                    "name": name,
                    "download_bytes": size,
                    "fits": None if size is None else assess_fit(size, hardware, gpu_mode)["fits"],
                    "size_source": "rounded catalogue size" if size is not None else "unknown",
                }
            )
        return sorted(variants, key=lambda item: {True: 0, False: 1, None: 2}[item["fits"]])
