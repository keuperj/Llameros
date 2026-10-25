#!/usr/bin/env python3
"""Read-only release checks: locked Python advisories and optional registry image scans.

No images are run or managed containers changed. Registry scanning requires the
operator-provided Docker Scout CLI plugin; it is never installed by this script.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import httpx

from llameros.config import Settings


def scan_lock(client, path):
    packages = [
        p for p in tomllib.loads(path.read_text())["package"] if "registry" in p.get("source", {})
    ]
    if not packages:
        raise ValueError("No locked registry packages")
    queries = [
        {"package": {"name": p["name"], "ecosystem": "PyPI"}, "version": p["version"]}
        for p in packages
    ]
    response = client.post("https://api.osv.dev/v1/querybatch", json={"queries": queries})
    response.raise_for_status()
    results = response.json()["results"]
    if len(results) != len(queries):
        raise ValueError("Incomplete OSV response")
    output = []
    for package, query, result in zip(packages, queries, results, strict=True):
        vulnerabilities = list(result.get("vulns", []))
        seen = set()
        while result.get("next_page_token"):
            token = result["next_page_token"]
            if token in seen:
                raise ValueError("Repeated OSV page token")
            seen.add(token)
            page = client.post("https://api.osv.dev/v1/query", json={**query, "page_token": token})
            page.raise_for_status()
            result = page.json()
            vulnerabilities.extend(result.get("vulns", []))
        output.append(
            {
                "name": package["name"],
                "version": package["version"],
                "advisories": sorted({v["id"] for v in vulnerabilities}),
            }
        )
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--scan-images",
        action="store_true",
        help="Opt in to registry image downloads/cache and Docker Scout vulnerability scanning",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    lock = root / "uv.lock"
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "images": [],
        "host_review": "required: kernel, Docker, PAM, TLS and GPU drivers via distribution/vendor advisories",
    }
    failed = False
    try:
        with httpx.Client(timeout=60, trust_env=False) as client:
            report["python"] = scan_lock(client, lock)
        failed = any(p["advisories"] for p in report["python"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        report["python_error"] = type(exc).__name__
        failed = True
    cfg = Settings()
    for image in (cfg.ollama_image, cfg.ollama_rocm_image):
        item = {"image": image, "status": "not-scanned"}
        if args.scan_images:
            try:
                result = subprocess.run(
                    [
                        "docker",
                        "scout",
                        "cves",
                        "--format",
                        "sarif",
                        "--exit-code",
                        "registry://" + image,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=1800,
                    check=False,
                )
                item.update(
                    status="complete" if result.returncode in (0, 2) else "error",
                    exit_code=result.returncode,
                )
                if result.returncode in (0, 2):
                    item["report"] = json.loads(result.stdout)
                    if (
                        not isinstance(item["report"].get("runs"), list)
                        or not item["report"]["runs"]
                    ):
                        raise ValueError("Missing SARIF runs")
                failed |= result.returncode != 0
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                item.update(status="error", error=type(exc).__name__)
                failed = True
        report["images"].append(item)
    report["complete"] = not failed and args.scan_images
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    # A Python-only check is useful but never reports full release assurance.
    return 1 if failed else (0 if args.scan_images else 2)


if __name__ == "__main__":
    sys.exit(main())
