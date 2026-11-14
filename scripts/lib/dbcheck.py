"""Check the PostgreSQL SERVER that TT_DATABASE_URL points at (not the local psql client).

Prints one JSON object: reachable, error kind, server_version_num, the required extensions (available / installed) and
the alembic revision. Exit 0 always; the shell script decides what the result means. Run with the project's Python
(psycopg is a server dependency):  .venv/bin/python scripts/lib/dbcheck.py [URL]
"""

import json
import os
import sys

REQUIRED = os.environ.get("TT_PG_EXTENSIONS", "pg_trgm ltree btree_gist").split()


def classify(msg: str) -> str:
    m = msg.lower()
    if (
        "password authentication failed" in m
        or "role" in m
        and "does not exist" in m
        or "no password supplied" in m
    ):
        return "auth"
    if "database" in m and "does not exist" in m:
        return "no_database"
    if (
        "connection refused" in m
        or "could not connect" in m
        or "timeout" in m
        or "no route" in m
        or "name or service" in m
    ):
        return "unreachable"
    return "other"


def main() -> None:
    url = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TT_DATABASE_URL", "")).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    out: dict[str, object] = {"reachable": False, "error": None, "error_kind": None}
    try:
        import psycopg
    except ImportError as exc:  # the Python environment is not installed yet
        out.update(error=f"psycopg not importable: {exc}", error_kind="no_driver")
        print(json.dumps(out))
        return
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            out["reachable"] = True
            cur = conn.cursor()
            cur.execute("SHOW server_version_num")
            row = cur.fetchone()
            out["server_version_num"] = int(row[0]) if row else None
            cur.execute("SHOW server_version")
            row = cur.fetchone()
            out["server_version"] = row[0] if row else None
            cur.execute(
                "SELECT name, installed_version FROM pg_available_extensions WHERE name = ANY(%s)",
                (REQUIRED,),
            )
            found = {name: inst for name, inst in cur.fetchall()}
            out["extensions"] = {
                e: ("installed" if found.get(e) else "available" if e in found else "missing")
                for e in REQUIRED
            }
            cur.execute("SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname = current_user")
            row = cur.fetchone()
            out["can_create"] = bool(row[0]) if row else False
            cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            row = cur.fetchone()
            if row and row[0]:
                cur.execute("SELECT version_num FROM alembic_version")
                rev = cur.fetchone()
                out["revision"] = rev[0] if rev else None
            else:
                out["revision"] = None
    except Exception as exc:  # noqa: BLE001 - report every connection problem as data
        out["error"] = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        out["error_kind"] = classify(str(exc))
    print(json.dumps(out))


if __name__ == "__main__":
    main()
