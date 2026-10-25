"""ADR-0036: the served certificate is swapped without a restart (SIGHUP / file change); a bad replacement never takes the site down."""

from __future__ import annotations

import asyncio
import os
import ssl
from pathlib import Path

from cryptography import x509
from tt_server.tls import CertReloader, days_until_expiry

from tests.unit.certs import make_ca, make_leaf, write


async def served_serial(port: int, ca_pem: bytes) -> int:
    ctx = ssl.create_default_context(cadata=ca_pem.decode())
    reader, writer = await asyncio.open_connection("localhost", port, ssl=ctx, server_hostname="localhost")
    der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    writer.close()
    await writer.wait_closed()
    return x509.load_der_x509_certificate(der).serial_number


async def with_server(ctx: ssl.SSLContext):  # type: ignore[no-untyped-def]
    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        w.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
    return srv, srv.sockets[0].getsockname()[1]


async def test_new_handshakes_get_the_new_certificate_after_the_files_change(tmp_path: Path) -> None:
    ca = make_ca()
    a, b = make_leaf(ca), make_leaf(ca)
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    write(a, cert, key)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    rl = CertReloader(ctx, cert, key)
    srv, port = await with_server(ctx)
    async with srv:
        assert await served_serial(port, ca.cert_pem) == a.cert.serial_number
        assert rl.check() is False  # nothing changed: no reload
        write(b, cert, key)
        os.utime(cert, ns=(1, 1))  # defeat coarse mtime granularity
        assert rl.check() is True and rl.reloads == 1
        assert await served_serial(port, ca.cert_pem) == b.cert.serial_number


async def test_a_mismatched_or_broken_replacement_keeps_the_old_certificate(tmp_path: Path) -> None:
    ca = make_ca()
    a, b = make_leaf(ca), make_leaf(ca)
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    write(a, cert, key)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    rl = CertReloader(ctx, cert, key)
    srv, port = await with_server(ctx)
    async with srv:
        cert.write_bytes(
            b.cert_pem
        )  # the certificate was swapped but the key is still the old one (a rotation caught half-way)
        assert rl.check() is False and rl.last_error
        assert await served_serial(port, ca.cert_pem) == a.cert.serial_number
        assert rl.check() is False  # same broken bytes: not retried every tick
        key.write_bytes(b.key_pem)  # the second file lands
        os.utime(key, ns=(2, 2))
        assert rl.check() is True and rl.last_error is None
        assert await served_serial(port, ca.cert_pem) == b.cert.serial_number
        cert.write_bytes(b"not a pem")
        os.utime(cert, ns=(3, 3))
        assert rl.check() is False
        assert await served_serial(port, ca.cert_pem) == b.cert.serial_number


async def test_sighup_reloads_and_the_loop_stops_cleanly(tmp_path: Path) -> None:
    import signal

    ca = make_ca()
    a, b = make_leaf(ca), make_leaf(ca)
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    write(a, cert, key)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    reasons: list[str] = []
    rl = CertReloader(ctx, cert, key, interval=3600, on_reload=reasons.append)
    stop = asyncio.Event()
    task = asyncio.create_task(rl.run(stop))
    await asyncio.sleep(0.05)
    write(b, cert, key)
    os.kill(os.getpid(), signal.SIGHUP)
    for _ in range(50):
        if reasons:
            break
        await asyncio.sleep(0.02)
    assert reasons == ["SIGHUP"]
    stop.set()
    await asyncio.wait_for(task, 2)
    asyncio.get_running_loop().remove_signal_handler(signal.SIGHUP)


def test_expiry_days(tmp_path: Path) -> None:
    ca = make_ca()
    soon, later = make_leaf(ca, days=3), make_leaf(ca, days=40)
    write(soon, tmp_path / "s.pem", tmp_path / "sk.pem")
    write(later, tmp_path / "l.pem", tmp_path / "lk.pem")
    assert 2.9 < days_until_expiry(tmp_path / "s.pem") < 3.1
    assert 39.9 < days_until_expiry(tmp_path / "l.pem") < 40.1
    expired = make_leaf(ca, days=-1)
    write(expired, tmp_path / "e.pem", tmp_path / "ek.pem")
    assert days_until_expiry(tmp_path / "e.pem") < 0


def test_cert_info_exit_code_flags_expiry(tmp_path: Path) -> None:
    from tt_server.admin import app
    from typer.testing import CliRunner

    ca = make_ca()
    write(make_leaf(ca, days=3), tmp_path / "s.pem", tmp_path / "k.pem")
    write(make_leaf(ca, days=60), tmp_path / "l.pem", tmp_path / "lk.pem")
    r = CliRunner()
    assert r.invoke(app, ["cert-info", str(tmp_path / "s.pem")]).exit_code == 1
    ok = r.invoke(app, ["cert-info", str(tmp_path / "l.pem")])
    assert ok.exit_code == 0 and "days left" in ok.output
