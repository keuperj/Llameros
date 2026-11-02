"""Real local HTTPS exchange with explicitly trusted self-signed certificates."""

import hashlib
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from test_users import multiuser as multiuser

from llameros.benchmark_exchange import (
    SharingSettings,
    exchange,
    inspect_server_certificate,
    sharing_tls_context,
)
from llameros.config import Settings
from llameros.tls import certificates


@pytest.fixture
def https_server(tmp_path):
    cert, key = certificates(Settings(host="127.0.0.1", state_dir=tmp_path))
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"accepted":1,"duplicates":0}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            SharingSettings(enabled=True, host="127.0.0.1", port=server.server_port),
            cert,
            requests,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_self_signed_discovery_never_uploads_and_explicit_trust_allows_upload(https_server):
    settings, cert, requests = https_server
    with pytest.raises(httpx.ConnectError):
        exchange(settings, "POST", "/api/v1/benchmarks", {"entries": []})
    assert requests == []
    discovered = inspect_server_certificate(settings)
    assert discovered["destination"] == settings.url
    assert discovered["ca_certificate"] == cert.read_text()
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    assert discovered["sha256"] == ":".join(f"{byte:02X}" for byte in hashlib.sha256(der).digest())
    assert requests == []
    trusted = SharingSettings(
        **{**settings.model_dump(), "ca_certificate": discovered["ca_certificate"]}
    )
    assert exchange(trusted, "POST", "/api/v1/benchmarks", {"entries": []})["accepted"] == 1
    assert requests == [{"entries": []}]
    context = sharing_tls_context(trusted)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT
    with pytest.raises(ssl.SSLCertVerificationError):
        with socket.create_connection((settings.host, settings.port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname="192.0.2.1"):
                pass


def test_unrelated_certificate_does_not_authorize_upload(https_server, tmp_path):
    settings, cert, requests = https_server
    other_state = tmp_path / "different-server"
    other_state.mkdir()
    other_cert, _ = certificates(Settings(host="127.0.0.1", state_dir=other_state))
    trusted = SharingSettings(**{**settings.model_dump(), "ca_certificate": other_cert.read_text()})
    with pytest.raises(httpx.ConnectError):
        exchange(trusted, "POST", "/api/v1/benchmarks", {"entries": []})
    assert requests == []


def test_certificate_inspection_admin_only_and_does_not_save_trust(multiuser, monkeypatch):
    app, admin, user = multiuser
    calls = []
    monkeypatch.setattr(
        "llameros.app.inspect_server_certificate",
        lambda body: (
            calls.append(body)
            or {
                "destination": body.url,
                "ca_certificate": "public-pem",
                "sha256": "AA:BB",
            }
        ),
    )
    payload = {"host": "127.0.0.1", "port": 9443}
    assert user.post("/api/benchmark-sharing/certificate", json=payload).status_code == 403
    assert not calls
    response = admin.post("/api/benchmark-sharing/certificate", json=payload)
    assert response.status_code == 200
    assert response.json()["sha256"] == "AA:BB"
    assert admin.get("/api/benchmark-sharing").json()["ca_certificate"] == ""
    assert admin.get("/api/benchmark-sharing").json()["enabled"] is False

    def unavailable(body):
        raise ssl.SSLCertVerificationError("private error details")

    monkeypatch.setattr("llameros.app.inspect_server_certificate", unavailable)
    response = admin.post("/api/benchmark-sharing/certificate", json=payload)
    assert response.status_code == 502
    assert "private error details" not in response.text
