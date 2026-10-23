"""Wellbeing habits / violations, capture privacy and the event feed (Phase 4 §4.3, router: access)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.enums import (
    HabitStatus,
    Polarity,
    PolicyCategory,
    ProjectRole,
    ScopeType,
    TargetType,
    Trend,
)
from tt_server.acl.context import load_context
from tt_server.acl.visibility import visible
from tt_server.db import models as m

from tests.api.conftest import Api
from tests.api.test_acl_core import pid

ROUTERS_COVERED = ("access",)


async def _line(api: Api, person: str, manager: str, kind: str = "line") -> None:
    r = await api.call(
        "POST",
        "/reporting-lines",
        json={"person_id": await pid(api, person), "manager_id": await pid(api, manager), "kind": kind},
    )
    assert r.status_code == 201, r.text


async def _habit(s: AsyncSession, ws_id: str, person_id: str, key: str, category: PolicyCategory) -> str:
    h = m.Habit(
        workspace_id=ws_id,
        subject_type=TargetType.PERSON,
        subject_id=person_id,
        key=key,
        polarity=Polarity.NEGATIVE,
        category=category,
        trend=Trend.STABLE,
        status=HabitStatus.OBSERVED,
        occurrences=3,
        rate=Decimal("0.5"),
        score=Decimal("1"),
    )
    s.add(h)
    await s.flush()
    return h.id


async def _seen(s: AsyncSession, ws_id: str, who: str, model: type) -> set[str]:
    ctx = await load_context(s, ws_id, who)
    return {
        r.id
        for r in (
            await s.scalars(visible(select(model).where(model.workspace_id == ws_id), ctx, model))
        ).all()  # type: ignore[attr-defined]
    }


async def test_wellbeing_habits_follow_the_reporting_line_not_project_management(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    sara, ali, mahmoud = await pid(api, "@sara"), await pid(api, "@ali"), await pid(api, "@mahmoud")
    await _line(api, "@sara", "@mahmoud")  # mahmoud is sara's line manager
    # ali manages WEB (project role), sara works on WEB, but ali is not on her reporting line
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    await api.call(
        "POST", "/projects/WEB/members", json={"handle": "@ali", "role": ProjectRole.MANAGER.value}
    )
    await api.role("@ali", "manager", scope_type=ScopeType.PROJECT.value, scope_id="WEB")
    async with session_factory() as s:
        ws_id = await s.scalar(select(m.Workspace.id).where(m.Workspace.slug == "acme"))
        assert ws_id
        plain = await _habit(s, ws_id, sara, "no_estimates", PolicyCategory.PLANNING)
        well = await _habit(s, ws_id, sara, "late_nights", PolicyCategory.WELLBEING)
        await s.commit()
        assert await _seen(s, ws_id, sara, m.Habit) == {plain, well}  # subjects always see their own (§13.3)
        assert await _seen(s, ws_id, mahmoud, m.Habit) == {plain, well}  # line manager sees both
        assert await _seen(s, ws_id, ali, m.Habit) == {plain}  # project manager: never wellbeing


async def test_workspace_admin_without_a_line_still_cannot_see_wellbeing(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    sara, ali, mahmoud = await pid(api, "@sara"), await pid(api, "@ali"), await pid(api, "@mahmoud")
    await _line(api, "@sara", "@ali")  # ali (not the admin) is her line manager
    async with session_factory() as s:
        ws_id = await s.scalar(select(m.Workspace.id).where(m.Workspace.slug == "acme"))
        assert ws_id
        well = await _habit(s, ws_id, sara, "burnout_signals", PolicyCategory.WELLBEING)
        plain = await _habit(s, ws_id, sara, "late_logging", PolicyCategory.TIME)
        await s.commit()
        assert await _seen(s, ws_id, mahmoud, m.Habit) == {plain}  # admin: normal habits only
        assert await _seen(s, ws_id, ali, m.Habit) == {plain, well}


async def test_raw_input_is_redacted_for_non_line_managers(api: Api) -> None:
    await _line(api, "@sara", "@mahmoud")
    await api.role("@ali", "manager")  # a manager role, but not on sara's reporting line
    res = await api.send(
        as_="@sara", action="create", item={"title": "secret-ish"}, project="WEB", raw_input="ugh, WEB again"
    )
    key = res["item"]["key"]

    async def raw(as_: str) -> set[str | None]:
        rows = (await api.call("GET", "/events", as_=as_, params={"entity_id": res["item"]["id"]})).json()[
            "data"
        ]
        return {r["raw_input"] for r in rows if r["event_type"] == "item.created"}

    assert key
    assert await raw("@sara") == {"ugh, WEB again"}
    assert await raw("@mahmoud") == {"ugh, WEB again"}  # line manager
    assert await raw("@ali") == {None}  # visible event, redacted text
