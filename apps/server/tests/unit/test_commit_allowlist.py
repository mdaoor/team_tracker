"""The commit allow-list is pinned (Phase 13 R3 review finding): every place that may commit without an Event is listed here.

Adding a call site means editing this test, i.e. a reviewer sees it. `commit_bookkeeping` additionally refuses to run once a
domain row has been touched in the unit of work, so it can never carry a domain change past the Event guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.db.uow import MissingEventError, UnitOfWork

SRC = Path(__file__).resolve().parents[2] / "tt_server"

# (file relative to tt_server, enclosing function) -> exactly how many calls
COMMIT_BOOKKEEPING = {
    ("api/deps.py", "_authenticate"): 1,  # ApiToken.last_used_at
    ("api/deps.py", "current_actor"): 1,  # ends the read transaction; nothing may be pending
    ("api/routers/privacy.py", "purge"): 1,  # rewrites events in place
    ("bootstrap.py", "seed_reference_data"): 1,  # shipped reference data
    ("domain/signin.py", "record_failure"): 1,  # login-attempt backoff rows
    ("domain/signin.py", "record_success"): 1,
    ("governance/service.py", "ensure_system"): 1,  # the governance bot person
    ("integrations/jira/sync.py", "_push_worklogs"): 1,  # sync links and logs
    ("integrations/jira/sync.py", "_record_failure"): 1,
    ("integrations/jira/sync.py", "finish"): 1,
}
COMMIT_DERIVED = {
    ("admin.py", "run"): 1,
    ("domain/deckdata.py", "get_snapshot"): 1,  # snapshot cache rows
    ("domain/deckdata.py", "refresh_cache"): 1,
    ("domain/insights.py", "nightly"): 1,
    ("domain/privacy.py", "purge_all"): 1,
}
ALLOW_UNLOGGED = {
    ("admin.py", "_backfill"): 1,  # the seed/backfill path
    (
        "domain/recurrence.py",
        "run_due",
    ): 1,  # the spawned item emits its own events; next_run_at is bookkeeping
}


def _sites() -> dict[str, dict[tuple[str, str], int]]:
    found: dict[str, dict[tuple[str, str], int]] = {"bookkeeping": {}, "derived": {}, "unlogged": {}}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == "db/uow.py":
            continue
        tree = ast.parse(path.read_text())
        parents: dict[ast.AST, ast.AST] = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

        def owner(node: ast.AST, parents: dict[ast.AST, ast.AST] = parents) -> str:
            while node in parents:
                node = parents[node]
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    return node.name
            return "<module>"

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
            if name == "commit_bookkeeping":
                bucket = "bookkeeping"
            elif name == "commit_derived":
                bucket = "derived"
            elif any(
                k.arg == "allow_unlogged"
                and not (isinstance(k.value, ast.Constant) and k.value.value is False)
                for k in node.keywords
            ):
                bucket = "unlogged"
            else:
                continue
            key = (rel, owner(node))
            found[bucket][key] = found[bucket].get(key, 0) + 1
    return found


def test_commit_bookkeeping_call_sites_are_frozen() -> None:
    assert _sites()["bookkeeping"] == COMMIT_BOOKKEEPING


def test_commit_derived_call_sites_are_frozen() -> None:
    assert _sites()["derived"] == COMMIT_DERIVED


def test_allow_unlogged_call_sites_are_frozen() -> None:
    assert _sites()["unlogged"] == ALLOW_UNLOGGED


async def test_commit_bookkeeping_refuses_to_carry_a_domain_change(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with UnitOfWork(session_factory) as uow:
        uow.session.add(m.Workspace(id="w-leak", name="Leak", slug="leak"))
        await uow.session.flush()  # a domain row is now in the transaction, with no Event
        with pytest.raises(MissingEventError):
            await uow.commit_bookkeeping()
    async with session_factory() as s:
        assert await s.get(m.Workspace, "w-leak") is None


async def test_commit_bookkeeping_also_catches_a_pending_domain_row_and_still_commits_bookkeeping(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with UnitOfWork(session_factory) as uow:
        uow.session.add(m.Workspace(id="w-pending", name="Pending", slug="pending"))  # not flushed yet
        with pytest.raises(MissingEventError):
            await uow.commit_bookkeeping()
    async with UnitOfWork(session_factory) as uow:
        uow.session.add(m.LoginAttempt(id="la-1", key="k"))
        await uow.commit_bookkeeping()
    async with session_factory() as s:
        assert await s.get(m.LoginAttempt, "la-1") is not None
        assert await s.get(m.Workspace, "w-pending") is None
