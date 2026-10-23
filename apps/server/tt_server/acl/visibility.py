"""Visibility policies: one SQL predicate per entity, built from the actor's `AclContext` (Phase 4 §4.1).

`visible(stmt, ctx, Model)` appends the predicate. Routers never filter by hand; repositories call this.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy import and_, case, exists, false, or_, select, true
from sqlalchemy.orm import aliased
from tt_schema.enums import ProjectKind, TargetType, Visibility

from tt_server.acl.context import AclContext
from tt_server.db import models as m

Predicate = Callable[[AclContext], Any]
_PRIVATE_TIERS = (Visibility.PRIVATE, Visibility.ASSIGNEES)


def _me_and_managed(ctx: AclContext) -> list[str]:
    return [ctx.person_id, *ctx.managed_ids]


def _project_pred(ctx: AclContext, pj: Any, eff: Any | None = None) -> Any:
    """Can the actor see project row `pj`? `eff` overrides the visibility tier (item-level)."""
    for_item = eff is not None
    eff = pj.visibility if eff is None else eff
    members = list(ctx.member_project_ids)
    granted_projects = [oid for (t, oid), _ in ctx.grants.items() if t == "project"]
    granted_products = [oid for (t, oid), _ in ctx.grants.items() if t == "product"]
    product_projects = (
        select(m.ProductLink.target_id).where(
            m.ProductLink.product_id.in_(granted_products),
            m.ProductLink.target_type == TargetType.PROJECT,
            m.ProductLink.deleted_at.is_(None),
        )
        if granted_products
        else None
    )
    coverage = or_(
        true() if ctx.manager_all else false(),
        pj.id.in_(list(ctx.manager_scope_projects)),
        pj.team_id.in_(list(ctx.manager_scope_teams)),
    )
    granted = or_(
        pj.id.in_(granted_projects), pj.id.in_(product_projects) if product_projects is not None else false()
    )
    tier = or_(
        and_(eff.in_([Visibility.MEMBERS, Visibility.INHERIT]), pj.id.in_(members)),
        and_(eff == Visibility.TEAM, pj.team_id.in_(list(ctx.team_ancestor_ids))),
        and_(
            eff == Visibility.PROJECT,
            or_(
                pj.id.in_(members),
                pj.id.in_(list(ctx.reader_projects)),
                true() if ctx.reader_all else false(),
            ),
        ),
        eff == Visibility.WORKSPACE,
        and_(eff == Visibility.PRIVATE, pj.id.in_(members)) if not for_item else false(),
    )
    if (
        for_item
    ):  # private / assignees items are for the people on them (`own`) and admins; grants never widen them
        granted = and_(granted, eff.notin_(_PRIVATE_TIERS))
    normal = or_(
        true() if ctx.is_admin else false(), and_(coverage, eff.notin_(_PRIVATE_TIERS)), granted, tier
    )
    inbox = pj.owner_id.in_(_me_and_managed(ctx))
    return case((pj.kind == ProjectKind.INBOX, inbox), else_=normal)


def project_visible(ctx: AclContext) -> Any:
    return _project_pred(ctx, m.Project)


def item_visible(ctx: AclContext) -> Any:
    """WorkItem: inbox rule, own/managed rows, admin, manager coverage, grants, visibility tier (§4.1)."""
    wi, pj, g = m.WorkItem, aliased(m.Project), aliased(m.WorkItem)
    me = _me_and_managed(ctx)
    own = or_(
        wi.reporter_id.in_(me),
        wi.assignee_id.in_(me),
        exists().where(
            m.Assignment.work_item_id == wi.id,
            m.Assignment.person_id == ctx.person_id,
            m.Assignment.unassigned_at.is_(None),
        ),
    )
    eff = case((wi.visibility != Visibility.INHERIT, wi.visibility), else_=pj.visibility)
    item_grants = [oid for (t, oid), _ in ctx.grants.items() if t == "work_item"]
    via_item_grant = (
        exists().where(g.id.in_(item_grants), wi.path.op("<@")(g.path)).correlate(wi)
        if item_grants
        else false()
    )
    project_ok = _project_pred(ctx, pj, eff)
    non_inbox = or_(own, via_item_grant, project_ok)
    inbox_pred = pj.owner_id.in_(me)
    return exists().where(
        pj.id == wi.project_id,
        case((pj.kind == ProjectKind.INBOX, inbox_pred), else_=non_inbox),
    )


def product_visible(ctx: AclContext) -> Any:
    pr = m.Product
    granted = [oid for (t, oid), _ in ctx.grants.items() if t == "product"]
    return or_(
        pr.visibility == Visibility.WORKSPACE,
        pr.owner_id == ctx.person_id,
        true() if (ctx.is_admin or ctx.manager_all) else false(),
        pr.id.in_(granted),
    )


def _person_scoped(ctx: AclContext, col: Any, project_col: Any | None = None) -> Any:
    me = _me_and_managed(ctx)
    parts = [col.in_(me), true() if ctx.is_admin else false()]
    if project_col is not None:
        parts += [true() if ctx.manager_all else false(), project_col.in_(list(ctx.manager_scope_projects))]
    return or_(*parts)


def activity_visible(ctx: AclContext) -> Any:
    return _person_scoped(ctx, m.Activity.person_id, m.Activity.project_id)


def time_entry_visible(ctx: AclContext) -> Any:
    wi = aliased(m.WorkItem)
    by_project = exists().where(
        wi.id == m.TimeEntry.work_item_id, wi.project_id.in_(list(ctx.manager_scope_projects))
    )
    return or_(_person_scoped(ctx, m.TimeEntry.person_id), true() if ctx.manager_all else false(), by_project)


def evidence_visible(ctx: AclContext) -> Any:
    wi = aliased(m.WorkItem)
    return or_(
        m.Evidence.person_id.in_(_me_and_managed(ctx)),
        exists().where(wi.id == m.Evidence.work_item_id, item_visible_for(ctx, wi)),
    )


def item_visible_for(ctx: AclContext, alias: Any) -> Any:
    """`item_visible` re-aimed at an aliased WorkItem (for sub-selects)."""
    pj, g = aliased(m.Project), aliased(m.WorkItem)
    me = _me_and_managed(ctx)
    own = or_(
        alias.reporter_id.in_(me),
        alias.assignee_id.in_(me),
        exists().where(
            m.Assignment.work_item_id == alias.id,
            m.Assignment.person_id == ctx.person_id,
            m.Assignment.unassigned_at.is_(None),
        ),
    )
    eff = case((alias.visibility != Visibility.INHERIT, alias.visibility), else_=pj.visibility)
    item_grants = [oid for (t, oid), _ in ctx.grants.items() if t == "work_item"]
    via = (
        exists().where(g.id.in_(item_grants), alias.path.op("<@")(g.path)).correlate(alias)
        if item_grants
        else false()
    )
    return exists().where(
        pj.id == alias.project_id,
        case(
            (pj.kind == ProjectKind.INBOX, pj.owner_id.in_(me)),
            else_=or_(own, via, _project_pred(ctx, pj, eff)),
        ),
    )


def daily_log_visible(ctx: AclContext) -> Any:
    return _person_scoped(ctx, m.DailyLog.person_id)


def event_visible(ctx: AclContext) -> Any:
    wi = aliased(m.WorkItem)
    item_events = and_(
        m.Event.entity_type == TargetType.WORK_ITEM,
        exists().where(wi.id == m.Event.entity_id, item_visible_for(ctx, wi)),
    )
    return or_(
        m.Event.actor_id.in_(_me_and_managed(ctx)),
        true() if (ctx.is_admin or ctx.manager_all) else false(),
        item_events,
    )


def context_note_visible(ctx: AclContext) -> Any:
    cn = m.ContextNote
    return or_(
        cn.author_id == ctx.person_id,
        cn.visibility.in_(
            [
                Visibility.INHERIT,
                Visibility.WORKSPACE,
                Visibility.MEMBERS,
                Visibility.TEAM,
                Visibility.PROJECT,
            ]
        ),
        and_(
            cn.visibility == Visibility.MANAGERS_ONLY,
            true() if (ctx.is_admin or ctx.manager_all) else false(),
        ),
    )


def risk_visible(ctx: AclContext) -> Any:
    pj = aliased(m.Project)
    return exists().where(pj.id == m.Risk.project_id, _project_pred(ctx, pj))


def _wellbeing_scoped(ctx: AclContext, category_col: Any, subject_col: Any, project_col: Any | None) -> Any:
    from tt_schema.enums import PolicyCategory

    line = or_(subject_col == ctx.person_id, subject_col.in_(list(ctx.managed_ids)))
    team_view = or_(
        true() if (ctx.is_admin or ctx.manager_all) else false(),
        subject_col.in_(list(ctx.project_people_ids)),
        project_col.in_(list(ctx.manager_scope_projects)) if project_col is not None else false(),
    )
    return case((category_col == PolicyCategory.WELLBEING, line), else_=or_(line, team_view))


def violation_visible(ctx: AclContext) -> Any:
    v = m.Violation
    subject = sa.func.coalesce(v.person_id, "")
    return _wellbeing_scoped(ctx, v.category, subject, v.project_id)


def habit_visible(ctx: AclContext) -> Any:
    h = m.Habit
    subject = case((h.subject_type == TargetType.PERSON, h.subject_id), else_="")
    return _wellbeing_scoped(ctx, h.category, subject, None)


def saved_view_visible(ctx: AclContext) -> Any:
    return or_(m.SavedView.owner_id == ctx.person_id, m.SavedView.is_shared.is_(True))


def managers_only(ctx: AclContext) -> Any:
    return true() if (ctx.is_admin or ctx.manager_all) else false()


POLICIES: dict[type, Predicate] = {
    m.WorkItem: item_visible,
    m.Project: project_visible,
    m.Product: product_visible,
    m.Activity: activity_visible,
    m.TimeEntry: time_entry_visible,
    m.Evidence: evidence_visible,
    m.DailyLog: daily_log_visible,
    m.Event: event_visible,
    m.ContextNote: context_note_visible,
    m.Risk: risk_visible,
    m.Violation: violation_visible,
    m.Habit: habit_visible,
    m.SavedView: saved_view_visible,
    m.Insight: managers_only,
    m.MetricSnapshot: managers_only,
}


def visible(stmt: Any, ctx: AclContext, model: type) -> Any:
    """Append the entity's visibility predicate to a SELECT."""
    return stmt.where(POLICIES[model](ctx))


def predicate(ctx: AclContext, model: type) -> Any:
    return POLICIES[model](ctx)


# ---- redaction (capture privacy, wellbeing) ----------------------------------------------------


def sees_private_capture(ctx: AclContext, person_id: str | None) -> bool:
    """`Event.raw_input`, `Activity.note` and `mood`: the actor and their line/senior managers only."""
    return person_id is not None and (ctx.person_id == person_id or person_id in ctx.managed_ids)


def redact_event(ctx: AclContext, row: dict[str, Any]) -> dict[str, Any]:
    if not sees_private_capture(ctx, row.get("actor_id")):
        row = {**row, "raw_input": None}
    return row


def redact_activity(ctx: AclContext, row: dict[str, Any]) -> dict[str, Any]:
    if not sees_private_capture(ctx, row.get("person_id")):
        row = {**row, "note": None, "mood": None, "client_meta": None}
    return row


def redact(ctx: AclContext, kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """Single entry point for snapshot / export assemblers (Phase 7)."""
    if kind == "event":
        return redact_event(ctx, row)
    if kind == "activity":
        return redact_activity(ctx, row)
    return row
