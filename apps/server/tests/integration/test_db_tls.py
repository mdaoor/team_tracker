"""ADR-0036: Postgres `sslmode=verify-full` with `TT_DB_SSLROOTCERT`, against a real TLS-enabled throw-away cluster.

Skipped when the machine cannot start one (needs `initdb` and root + a `postgres` OS user, as in CI containers)."""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from tt_server.db.session import make_engine
from tt_server.settings import Settings, get_settings

from tests.unit.certs import make_ca, make_leaf, write

PORT = 55433
BIN = (
    next(iter(sorted(Path("/usr/lib/postgresql").glob("*/bin"))), None)
    if Path("/usr/lib/postgresql").exists()
    else None
)


def _can_run() -> bool:
    if BIN is None or not (BIN / "initdb").exists() or os.geteuid() != 0:
        return False
    try:
        pwd.getpwnam("postgres")
    except KeyError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _can_run(), reason="needs initdb and root with a postgres user")


def sh(*args: str, user: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = ["runuser", "-u", user, "--", *args] if user else list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603


@pytest.fixture(scope="module")
def cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    root = Path("/tmp") / f"tt-pgtls-{os.getpid()}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(mode=0o755)
    shutil.chown(root, "postgres", "postgres")
    ca, other = make_ca("good ca"), make_ca("other ca")
    # the name clients must verify; `hostaddr` pins the socket to 127.0.0.1 so no DNS is needed
    server = make_leaf(ca, "db.example.test", dns=("db.example.test",), ips=())
    write(server, root / "server.crt", root / "server.key")
    write(ca, root / "ca.crt", root / "ca.key")
    write(other, root / "other.crt", root / "other.key")
    for f in ("server.crt", "server.key", "ca.crt", "other.crt"):
        shutil.chown(root / f, "postgres", "postgres")
        (root / f).chmod(0o600 if f.endswith(".key") else 0o644)
    data = root / "data"
    r = sh(str(BIN / "initdb"), "-D", str(data), "-A", "trust", "-U", "postgres", user="postgres")  # type: ignore[operator]
    assert r.returncode == 0, r.stderr
    with (data / "postgresql.conf").open("a") as f:
        f.write(
            f"\nport = {PORT}\nlisten_addresses = '127.0.0.1'\nunix_socket_directories = '{root}'\n"
            f"ssl = on\nssl_cert_file = '{root / 'server.crt'}'\nssl_key_file = '{root / 'server.key'}'\n"
        )
    (data / "pg_hba.conf").write_text(
        "hostssl all all 127.0.0.1/32 trust\nhost all all 127.0.0.1/32 reject\n"
    )
    r = sh(str(BIN / "pg_ctl"), "-D", str(data), "-l", str(root / "log"), "-w", "start", user="postgres")  # type: ignore[operator]
    assert r.returncode == 0, r.stderr + (root / "log").read_text()
    try:
        yield {"ca": root / "ca.crt", "other": root / "other.crt"}
    finally:
        sh(str(BIN / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop", user="postgres")  # type: ignore[operator]
        time.sleep(0.2)
        shutil.rmtree(root, ignore_errors=True)


async def ssl_in_use(monkeypatch: pytest.MonkeyPatch, host: str, **env: str) -> bool:
    for k, v in env.items():
        monkeypatch.setenv(f"TT_{k.upper()}", v)
    get_settings.cache_clear()
    eng = make_engine(f"postgresql+psycopg://postgres@{host}:{PORT}/postgres?hostaddr=127.0.0.1")
    try:
        async with eng.connect() as c:
            return bool(
                (await c.execute(text("select ssl from pg_stat_ssl where pid = pg_backend_pid()"))).scalar()
            )
    finally:
        await eng.dispose()
        get_settings.cache_clear()


async def test_verify_full_with_the_right_ca_connects_over_tls(
    cluster: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await ssl_in_use(
        monkeypatch, "db.example.test", db_sslmode="verify-full", db_sslrootcert=str(cluster["ca"])
    )


async def test_verify_full_rejects_a_server_signed_by_another_ca(
    cluster: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(Exception, match=r"certificate verify failed|root certificate"):
        await ssl_in_use(
            monkeypatch, "db.example.test", db_sslmode="verify-full", db_sslrootcert=str(cluster["other"])
        )


async def test_verify_full_checks_the_host_name_but_verify_ca_does_not(
    cluster: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(Exception, match=r"does not match|certificate|host name"):
        await ssl_in_use(
            monkeypatch, "other.example.test", db_sslmode="verify-full", db_sslrootcert=str(cluster["ca"])
        )
    assert await ssl_in_use(
        monkeypatch, "other.example.test", db_sslmode="verify-ca", db_sslrootcert=str(cluster["ca"])
    )


async def test_plain_connection_is_refused_by_the_server_policy(
    cluster: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(Exception, match=r"no pg_hba|reject|SSL"):
        await ssl_in_use(monkeypatch, "db.example.test", db_sslmode="disable")


def test_settings_fail_fast_and_the_procrastinate_dsn_carries_the_same_parameters(tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    with pytest.raises(RuntimeError, match="SSLROOTCERT"):
        Settings(db_sslmode="verify-full").validate_for_runtime()
    with pytest.raises(RuntimeError, match="no such file"):
        Settings(db_sslmode="verify-full", db_sslrootcert=str(tmp_path / "nope.pem")).validate_for_runtime()
    with pytest.raises(RuntimeError, match="not a libpq sslmode"):
        Settings(db_sslmode="sometimes").validate_for_runtime()
    with pytest.raises(RuntimeError, match="together"):
        Settings(db_sslmode="require", db_sslcert="c").validate_for_runtime()
    s = Settings(db_sslmode="verify-full", db_sslrootcert=str(ca))
    s.validate_for_runtime()
    assert (
        s.psycopg_dsn.startswith("postgresql://")
        and "sslmode=verify-full" in s.psycopg_dsn
        and "sslrootcert=" in s.psycopg_dsn
    )
    assert "?" not in Settings().psycopg_dsn
