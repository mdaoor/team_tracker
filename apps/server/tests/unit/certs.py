"""Throw-away X.509 material for TLS tests (never used outside tests)."""

from __future__ import annotations

import datetime as dt
import ipaddress
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass
class Pair:
    cert_pem: bytes
    key_pem: bytes
    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def make_ca(cn: str = "tt test ca") -> Pair:
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(_name(cn)).issuer_name(_name(cn)).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False).sign(key, hashes.SHA256())
    )  # fmt: skip
    return _pair(cert, key)


def make_leaf(
    ca: Pair | None,
    cn: str = "localhost",
    *,
    days: float = 30,
    dns: tuple[str, ...] = ("localhost",),
    ips: tuple[str, ...] = ("127.0.0.1",),
) -> Pair:
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    issuer = ca.cert.subject if ca else _name(cn)
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (
        x509.CertificateBuilder().subject_name(_name(cn)).issuer_name(issuer).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(min(now - dt.timedelta(minutes=5), now + dt.timedelta(days=days) - dt.timedelta(days=2)))
        .not_valid_after(now + dt.timedelta(days=days)).add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key((ca.key if ca else key).public_key()), critical=False)
        .sign(ca.key if ca else key, hashes.SHA256())
    )  # fmt: skip
    return _pair(cert, key)


def _pair(cert: x509.Certificate, key: ec.EllipticCurvePrivateKey) -> Pair:
    return Pair(
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        cert,
        key,
    )


def write(pair: Pair, cert_path: Path, key_path: Path) -> None:
    cert_path.write_bytes(pair.cert_pem)
    key_path.write_bytes(pair.key_pem)
