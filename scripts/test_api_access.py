#!/usr/bin/env python3
"""Standalone HTTPS access smoke test for Llameros bearer authentication and chat inference.

Standalone HTTPS bearer-access smoke test; uses only Python's standard library.
"""

import argparse
import json
import os
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    """Keep smoke-test bearer credentials from being forwarded to redirect destinations."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Decline urllib redirects instead of forwarding an authenticated request."""
        return None


def check_access(base_url, key, ca_cert=None, model=None, target=None, max_tokens=16, stream=False):
    """Check unauthenticated rejection, permitted model discovery and chat generation."""
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Base URL must be HTTPS with no credentials, query, or fragment")
    if not key:
        raise ValueError("Set LLAMEROS_API_KEY before running this test")
    if not 1 <= max_tokens <= 4096:
        raise ValueError("max-tokens must be 1–4096 and within the key's limit")
    base_url = base_url.rstrip("/")
    context = ssl.create_default_context(cafile=ca_cert or None)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    def request(path, payload=None, authenticated=True):
        """Issue one verified-HTTPS request with bounded timeout and optional bearer authentication."""
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + key
        if target:
            headers["X-Llameros-Target"] = target
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        return opener.open(Request(base_url + path, data=data, headers=headers), timeout=120)

    try:
        with request("/v1/models", authenticated=False):
            raise ValueError("FAIL: endpoint accepted an unauthenticated request")
    except HTTPError as exc:
        try:
            if exc.code != 401:
                raise ValueError(
                    f"Expected unauthenticated HTTP 401, received {exc.code}"
                ) from None
        finally:
            exc.close()
    print("PASS: unauthenticated access rejected")
    with request("/v1/models") as response:
        models = json.load(response)["data"]
    matches = [
        item
        for item in models
        if (not model or item["id"] == model) and (not target or item.get("target") == target)
    ]
    if not matches:
        raise ValueError("No matching permitted loaded model. Load one in the dashboard first.")
    if not model:
        model = matches[0]["id"]
    if not target:
        target = next(item.get("target") for item in matches if item["id"] == model)
    print(f"PASS: bearer model discovery; testing {model} on {target or 'default target'}")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hello briefly."}],
        "max_tokens": max_tokens,
        "stream": stream,
    }
    with request("/v1/chat/completions", payload) as response:
        if stream:
            done = False
            chunks = 0
            for raw in response:
                line = raw.decode().strip()
                if not line.startswith("data: "):
                    continue
                if line == "data: [DONE]":
                    done = True
                    break
                event = json.loads(line[6:])
                if event.get("error"):
                    raise ValueError("Streaming API returned an error")
                chunks += bool(event.get("choices"))
            if not done or not chunks:
                raise ValueError("Stream ended without chat chunks and a completion marker")
        else:
            result = json.load(response)
            if result.get("error") or not result.get("choices"):
                raise ValueError("API did not return chat completion choices")
    print("PASS: authenticated streaming chat" if stream else "PASS: authenticated chat")


def main(argv=None):
    """Parse smoke-test options, read the environment key, and return a success/failure exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://127.0.0.1:8443/inference")
    parser.add_argument("--ca-cert", default=os.environ.get("LLAMEROS_CA_CERT"))
    parser.add_argument(
        "--model", help="Exact loaded model name including tag; default first permitted model"
    )
    parser.add_argument("--target", help="cpu or gpu-… slot ID; default discovered model target")
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args(argv)
    try:
        check_access(
            args.base_url,
            os.environ.get("LLAMEROS_API_KEY", ""),
            args.ca_cert,
            args.model,
            args.target,
            args.max_tokens,
            args.stream,
        )
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError) as exc:
        # Never print credentials, request headers, or server response bodies.
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
