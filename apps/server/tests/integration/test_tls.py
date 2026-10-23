"""HTTPS from a PEM pair, internal-CA trust and the HTTP -> HTTPS redirect (Phase 4 §4.6).

A throw-away CA signs the server certificate; the server runs under real uvicorn TLS on a free port.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from tt_client.login import LoginError, check_server
from tt_server.main import create_app
from tt_server.settings import Settings
from tt_server.tls import redirect_app


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@dataclass
class Pki:
    ca_pem: Path
    cert_pem: Path
    key_pem: Path
    other_ca_pem: Path


def make_pki(root: Path) -> Pki:
    now = dt.datetime.now(dt.UTC)

    def ca(cn: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(_name(cn))
            .issuer_name(_name(cn))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_cert_sign=True,
                    crl_sign=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        return key, cert

    ca_key, ca_cert = ca("Acme Internal CA")
    _, other_cert = ca("Some Other CA")
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    paths = Pki(root / "ca.pem", root / "cert.pem", root / "key.pem", root / "other-ca.pem")
    paths.ca_pem.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    paths.other_ca_pem.write_bytes(other_cert.public_bytes(serialization.Encoding.PEM))
    paths.cert_pem.write_bytes(
        leaf.public_bytes(serialization.Encoding.PEM)
    )  # full chain: leaf (+ intermediates)
    paths.key_pem.write_bytes(_pem_key(leaf_key))
    return paths


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _Running:
    def __init__(self, server: uvicorn.Server, thread: threading.Thread) -> None:
        self.server, self.thread = server, thread

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _run(config: uvicorn.Config) -> _Running:
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return _Running(server, thread)
        time.sleep(0.05)
    raise RuntimeError("test server did not start")


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    return make_pki(tmp_path_factory.mktemp("pki"))


@pytest.fixture
def https_server(pki: Pki) -> Iterator[tuple[str, int]]:
    port, plain = _free_port(), _free_port()
    settings = Settings(
        database_url="postgresql+psycopg://x:x@127.0.0.1:1/none",
        log_json=False,
        log_level="WARNING",
        force_https=True,
        https_port=port,
    )
    app = create_app(settings)
    tls = _run(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            ssl_certfile=str(pki.cert_pem),
            ssl_keyfile=str(pki.key_pem),
            log_level="warning",
        )
    )
    redirect = _run(uvicorn.Config(redirect_app(port), host="127.0.0.1", port=plain, log_level="warning"))
    try:
        yield f"https://localhost:{port}", plain
    finally:
        redirect.stop()
        tls.stop()


def test_untrusted_chain_is_refused_and_the_internal_ca_is_accepted(
    https_server: tuple[str, int], pki: Pki
) -> None:
    url, _ = https_server
    with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
        httpx.get(f"{url}/healthz")
    with pytest.raises(httpx.ConnectError):
        httpx.get(
            f"{url}/healthz", verify=ssl.create_default_context(cafile=str(pki.other_ca_pem))
        )  # the wrong CA is no better
    ok = httpx.get(f"{url}/healthz", verify=ssl.create_default_context(cafile=str(pki.ca_pem)))
    assert ok.status_code == 200 and ok.json() == {"status": "ok"}
    assert ok.headers["strict-transport-security"].startswith("max-age=31536000")


def test_tt_login_check_needs_the_ca_bundle_and_has_no_insecure_escape(
    https_server: tuple[str, int], pki: Pki
) -> None:
    url, _ = https_server
    with pytest.raises(LoginError, match="TT_CA_BUNDLE"):
        check_server(url)
    with pytest.raises(LoginError, match="TLS verification failed"):
        check_server(url, str(pki.other_ca_pem))
    check_server(url, str(pki.ca_pem))  # no exception
    with pytest.raises(LoginError, match="does not exist"):
        check_server(url, "/nonexistent/ca.pem")
    with pytest.raises(LoginError, match="plain HTTP is only allowed for localhost"):
        check_server("http://tracker.corp.example")


def test_plain_http_is_redirected_to_https(https_server: tuple[str, int]) -> None:
    url, plain = https_server
    https_port = url.rsplit(":", 1)[1]
    r = httpx.get(f"http://localhost:{plain}/api/v1/w/acme/items?x=1", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == f"https://localhost:{https_port}/api/v1/w/acme/items?x=1"


def test_tt_login_command_stores_config_only_after_the_chain_verifies(
    https_server: tuple[str, int], pki: Pki, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tt_cli.main import app
    from typer.testing import CliRunner

    monkeypatch.setenv("TT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("TT_CA_BUNDLE", raising=False)
    url, _ = https_server
    args = ["login", "--url", url, "--workspace", "acme", "--handle", "@mahmoud", "--token", "tt_x"]
    refused = CliRunner().invoke(app, args)
    assert refused.exit_code == 1 and "TT_CA_BUNDLE" in refused.output
    assert not (tmp_path / "config.toml").exists()
    ok = CliRunner().invoke(app, [*args, "--ca-bundle", str(pki.ca_pem)])
    assert ok.exit_code == 0, ok.output
    saved = (tmp_path / "config.toml").read_text()
    assert f'ca_bundle = "{pki.ca_pem}"' in saved and url in saved


async def test_force_https_middleware_hsts_and_forwarded_proto() -> None:
    from httpx import ASGITransport, AsyncClient

    def client(**kw: object) -> AsyncClient:
        s = Settings(database_url="postgresql+psycopg://x:x@127.0.0.1:1/none", log_level="WARNING", **kw)  # type: ignore[arg-type]
        return AsyncClient(transport=ASGITransport(app=create_app(s)), base_url="http://tracker.corp.test")

    async with client(force_https=True) as c:
        r = await c.get("/api/v1/w/acme/items?a=1", follow_redirects=False)
        assert (
            r.status_code == 308
            and r.headers["location"] == "https://tracker.corp.test/api/v1/w/acme/items?a=1"
        )
        assert (await c.get("/healthz")).status_code == 200  # probes stay reachable over plain HTTP
        assert "strict-transport-security" not in (await c.get("/healthz")).headers
        spoofed = await c.get("/healthz", headers={"X-Forwarded-Proto": "https"})
        assert "strict-transport-security" not in spoofed.headers  # ignored unless the proxy is trusted
    async with client(force_https=True, trust_forwarded_proto=True, hsts_max_age=60) as c:
        r = await c.get("/auth/providers", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
        assert (
            r.status_code != 308 and r.headers["strict-transport-security"] == "max-age=60; includeSubDomains"
        )
    async with client() as c:  # dev default: no redirect, no HSTS
        r = await c.get("/healthz")
        assert r.status_code == 200 and "strict-transport-security" not in r.headers


def test_tt_admin_serve_validates_the_pem_pair(tmp_path: Path) -> None:
    from tt_server.admin import app
    from typer.testing import CliRunner

    cert = tmp_path / "c.pem"
    cert.write_text("x")
    both = CliRunner().invoke(app, ["serve", "--certfile", str(cert)])
    assert both.exit_code != 0 and "together" in both.output
    missing = CliRunner().invoke(
        app, ["serve", "--certfile", str(cert), "--keyfile", str(tmp_path / "nope.pem")]
    )
    assert missing.exit_code != 0 and "nope.pem" in missing.output
