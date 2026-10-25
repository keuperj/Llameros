#!/usr/bin/env python3
"""Opt-in protocol smoke checks against an existing Llameros HTTPS inference gateway.

Opt-in HTTPS smoke tests. Never load models or change runtime containers.
"""

import argparse
import base64
import json
import os
import ssl
import sys
from pathlib import Path

import httpx


def main(argv=None):
    """Run explicitly requested live protocol/media checks against existing loaded runtimes.

    Read the key from the environment, require a trusted CA, and return nonzero if a check
    fails; never change containers or load models.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://127.0.0.1:8443/inference")
    parser.add_argument("--ca", required=True, help="Trusted Llameros certificate PEM")
    parser.add_argument("--model", required=True, help="Already-loaded chat model")
    parser.add_argument("--embedding-model", help="Already-loaded embedding model")
    parser.add_argument("--vision-model", help="Already-loaded vision model")
    parser.add_argument("--image", type=Path, help="Valid PNG/JPEG/GIF/WebP for real inference")
    parser.add_argument("--target", default="cpu")
    parser.add_argument("--expected-version", default="0.40.11")
    args = parser.parse_args(argv)
    key = os.environ.get("LLAMEROS_API_KEY")
    if not key:
        parser.error("Set LLAMEROS_API_KEY; never pass credentials on the command line")
    if args.vision_model and not args.image:
        parser.error("--vision-model requires --image")
    failed = 0
    with httpx.Client(
        base_url=args.url.rstrip("/") + "/",
        headers={"Authorization": "Bearer " + key, "X-Llameros-Target": args.target},
        verify=ssl.create_default_context(cafile=args.ca),
        trust_env=False,
        follow_redirects=False,
        timeout=600,
    ) as client:

        def check(label, path, body=None, stream=False):
            """Validate one JSON/stream terminal response and emit only a pass/fail record."""
            nonlocal failed
            try:
                result = client.request("POST" if body is not None else "GET", path, json=body)
                result.raise_for_status()
                if stream:
                    if '"error"' in result.text:
                        raise ValueError("stream contains an error")
                    terminal = (
                        "[DONE]"
                        if path in {"v1/chat/completions", "v1/completions"}
                        else "response.completed"
                        if path == "v1/responses"
                        else "message_stop"
                        if path == "v1/messages"
                        else '"done": true'
                    )
                    if terminal not in result.text:
                        raise ValueError("missing successful terminal event")
                else:
                    data = result.json()
                    if path == "api/version" and data.get("version") != args.expected_version:
                        raise ValueError("Runtime version differs from compatibility pin")
                    if not isinstance(data, dict) or data.get("error"):
                        raise ValueError("invalid response")
                    if path in {
                        "api/generate",
                        "api/chat",
                        "v1/chat/completions",
                        "v1/completions",
                        "v1/responses",
                        "v1/messages",
                    }:
                        content = (
                            data.get("response")
                            if path == "api/generate"
                            else data.get("message", {}).get("content")
                            if path == "api/chat"
                            else data.get("choices")
                            if "completions" in path
                            else data.get("output")
                            if path == "v1/responses"
                            else data.get("content")
                        )
                        if not content:
                            raise ValueError("empty model output")
                print(json.dumps({"check": label, "status": "passed"}))
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                failed += 1
                # Do not print credentials, document text, or model output.
                print(json.dumps({"check": label, "status": "failed"}))

        check("version pin", "api/version")
        check("models", "v1/models")
        check("capabilities", "api/show", {"model": args.model})
        for stream in (False, True):
            for path, fields in [
                ("api/generate", {"prompt": "Say hello.", "options": {"num_predict": 32}}),
                (
                    "api/chat",
                    {
                        "messages": [{"role": "user", "content": "Say hello."}],
                        "options": {"num_predict": 32},
                    },
                ),
                (
                    "v1/chat/completions",
                    {"messages": [{"role": "user", "content": "Say hello."}], "max_tokens": 32},
                ),
                ("v1/completions", {"prompt": "Say hello.", "max_tokens": 32}),
                ("v1/responses", {"input": "Say hello.", "max_output_tokens": 32}),
                (
                    "v1/messages",
                    {"messages": [{"role": "user", "content": "Say hello."}], "max_tokens": 32},
                ),
            ]:
                check(
                    path + (" streaming" if stream else " JSON"),
                    path,
                    {"model": args.model, "stream": stream, **fields},
                    stream,
                )
        if args.embedding_model:
            for path, fields in [
                ("api/embed", {"input": ["hello", "world"]}),
                ("api/embeddings", {"prompt": "hello"}),
                ("v1/embeddings", {"input": "hello", "encoding_format": "base64"}),
            ]:
                check(path, path, {"model": args.embedding_model, **fields})
        if args.vision_model:
            image = base64.b64encode(args.image.read_bytes()).decode()
            check(
                "real vision inference",
                "api/generate",
                {
                    "model": args.vision_model,
                    "prompt": "Describe the image briefly.",
                    "images": [image],
                    "stream": False,
                    "options": {"num_predict": 64},
                },
            )
        else:
            print(json.dumps({"check": "real vision inference", "status": "skipped"}))
    return int(bool(failed))


if __name__ == "__main__":
    sys.exit(main())
