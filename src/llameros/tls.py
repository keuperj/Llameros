"""TLS certificate validation and private self-signed certificate creation for the Llameros HTTPS service.

Generate a private self-signed certificate with host/IP subject alternatives.
"""

import ipaddress
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .config import Settings


def certificates(settings: Settings) -> tuple[Path, Path]:
    """Return certificate/key paths, validating configured files or maintaining a private local pair.

    Generated certificates include host/IP alternatives and are renewed when near expiry or
    missing required names. No trust-store changes are made.
    """
    if settings.cert_file:
        cert, key = (
            Path(settings.cert_file).expanduser().absolute(),
            Path(settings.key_file).expanduser().absolute(),
        )
        if not cert.is_file() or not key.is_file():
            raise ValueError("Configured TLS certificate/key does not exist")
        if key.stat().st_mode & 0o077:
            raise ValueError(
                "TLS private key must not be accessible to group/other users (chmod 600)"
            )
        return cert, key
    cert, key = settings.state_dir / "cert.pem", settings.state_dir / "key.pem"
    names = {"localhost", socket.gethostname(), settings.host}
    alternatives = [
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    for name in sorted(names):
        try:
            alternatives.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alternatives.append(x509.DNSName(name))
    if cert.exists() and key.exists():
        key.chmod(0o600)
        existing = x509.load_pem_x509_certificate(cert.read_bytes())
        sans = existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if existing.not_valid_after_utc > datetime.now(UTC) + timedelta(days=7) and all(
            item in sans for item in alternatives
        ):
            return cert, key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Llameros")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    for path, data in (
        (
            key,
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        ),
        (cert, certificate.public_bytes(serialization.Encoding.PEM)),
    ):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        path.chmod(0o600)
    return cert, key
