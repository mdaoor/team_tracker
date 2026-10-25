"""Identity-provider provisioning and group -> role / team mapping (Phase 4 §4.6).

Provider-neutral: LDAP passes `memberOf` DNs, OIDC passes the `groups` claim. Everything derived from groups
carries `source="idp"`; sync only ever removes rows with that marker, so manual roles and memberships survive.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import MembershipRole, MemberStatus, ScopeType, TargetType

from tt_server.acl import roles as acl_roles
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.tenancy import service as tenancy
from tt_server.integrations.ldap import DirectoryUser

IDP = "idp"  # legacy prefix; rows are stamped "idp:<provider>" so providers never remove each other's grants


@dataclass
class GroupDelta:
    roles_added: list[str] = field(default_factory=list)
    roles_removed: list[str] = field(default_factory=list)
    teams_added: list[str] = field(default_factory=list)
    teams_removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.roles_added or self.roles_removed or self.teams_added or self.teams_removed)


async def _workspace_scope_ids(uow: UnitOfWork, ws_id: str) -> set[str]:
    s = uow.session
    ids = {ws_id}
    for model in (m.Team, m.Project, m.Product):
        ids |= set((await s.scalars(select(model.id).where(model.workspace_id == ws_id))).all())
    return ids


async def apply_groups(
    uow: UnitOfWork, ws: m.Workspace, person_id: str, provider: str, groups: set[str] | frozenset[str]
) -> GroupDelta:
    """Make the person's idp-sourced roles and team memberships exactly what the mapping table says."""
    s = uow.session
    src = f"{IDP}:{provider}"
    lowered = {g.lower() for g in groups}
    mappings = [
        mp
        for mp in (
            await s.scalars(
                select(m.IdpGroupMapping).where(
                    m.IdpGroupMapping.workspace_id == ws.id,
                    m.IdpGroupMapping.provider == provider,
                    m.IdpGroupMapping.deleted_at.is_(None),
                )
            )
        ).all()
        if mp.group.lower() in lowered
    ]
    want_roles: dict[tuple[str, str, str | None], m.IdpGroupMapping] = {}
    want_teams: set[str] = set()
    for mp in mappings:
        if mp.role_key:
            scope = ScopeType(mp.scope_type)
            sid = ws.id if scope == ScopeType.WORKSPACE else mp.scope_id
            want_roles[(mp.role_key, scope.value, sid)] = mp
        if mp.team_id:
            want_teams.add(mp.team_id)

    delta = GroupDelta()
    role_by_id = {
        r.id: r
        for r in (
            await s.scalars(
                select(m.Role).where((m.Role.workspace_id == ws.id) | m.Role.workspace_id.is_(None))
            )
        ).all()
    }
    existing = (
        await s.scalars(
            select(m.RoleAssignment).where(
                m.RoleAssignment.person_id == person_id,
                m.RoleAssignment.workspace_id == ws.id,
                m.RoleAssignment.deleted_at.is_(None),
            )
        )
    ).all()
    ws_scopes = await _workspace_scope_ids(uow, ws.id)
    have = {
        (role_by_id[a.role_id].key, a.scope_type.value, a.scope_id)
        for a in existing
        if a.role_id in role_by_id
    }
    for key, scope_value, sid in want_roles:
        if (key, scope_value, sid) not in have:
            await acl_roles.assign(s, ws.id, person_id, key, ScopeType(scope_value), sid, source=src)
            delta.roles_added.append(f"{key}@{scope_value}")
    for a in existing:
        role = role_by_id.get(a.role_id)
        if a.source != src or role is None or a.scope_id not in ws_scopes:
            continue
        if (role.key, a.scope_type.value, a.scope_id) not in want_roles:
            a.deleted_at = utcnow()
            delta.roles_removed.append(f"{role.key}@{a.scope_type.value}")

    team_ids = set((await s.scalars(select(m.Team.id).where(m.Team.workspace_id == ws.id))).all())
    memberships = (
        await s.scalars(
            select(m.Membership).where(
                m.Membership.person_id == person_id,
                m.Membership.team_id.in_(team_ids),
                m.Membership.left_at.is_(None),
            )
        )
    ).all()
    in_team = {mb.team_id for mb in memberships}
    for tid in want_teams - in_team:
        s.add(
            m.Membership(
                id=new_id(),
                team_id=tid,
                person_id=person_id,
                role=MembershipRole.MEMBER,
                source=src,
                joined_at=utcnow(),
            )
        )
        delta.teams_added.append(tid)
    for mb in memberships:
        if mb.source == src and mb.team_id not in want_teams:
            mb.left_at = utcnow()
            delta.teams_removed.append(mb.team_id)

    if delta.changed:
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=person_id,
            entity_type=TargetType.PERSON,
            entity_id=person_id,
            event_type="idp.groups_applied",
            payload={"after": {"provider": provider, **delta.__dict__}},
        )
    return delta


async def _unique_handle(uow: UnitOfWork, ws: m.Workspace, base: str, person_id: str) -> tuple[str, bool]:
    taken = set(
        (
            await uow.session.scalars(
                select(m.WorkspaceMember.handle).where(m.WorkspaceMember.workspace_id == ws.id)
            )
        ).all()
    )
    if base not in taken:
        return base, False
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    uow.session.add(
        m.ReviewItem(
            id=new_id(),
            workspace_id=ws.id,
            kind="handle_collision",
            target_type=TargetType.PERSON.value,
            target_id=person_id,
            suggestion={"wanted": base, "assigned": f"{base}{n}"},
        )
    )
    return f"{base}{n}", True


def domain_allowed(ws: m.Workspace, email: str) -> bool:
    domains = {d.lower() for d in ((ws.settings or {}).get("auth") or {}).get("allowed_domains", [])}
    return "@" in email and email.rsplit("@", 1)[1].lower() in domains


async def _request_access(uow: UnitOfWork, ws: m.Workspace, person: m.Person, provider: str) -> None:
    """Unknown, unmapped identity: leave one open review row for the admins instead of creating a member."""
    s = uow.session
    open_row = await s.scalar(
        select(m.ReviewItem.id).where(
            m.ReviewItem.workspace_id == ws.id,
            m.ReviewItem.kind == "access_request",
            m.ReviewItem.target_id == person.id,
            m.ReviewItem.status == "open",
        )
    )
    if open_row is None:
        s.add(
            m.ReviewItem(
                id=new_id(),
                workspace_id=ws.id,
                kind="access_request",
                target_type=TargetType.PERSON.value,
                target_id=person.id,
                suggestion={"email": person.email, "provider": provider},
            )
        )
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=person.id,
            entity_type=TargetType.PERSON,
            entity_id=person.id,
            event_type="auth.access_requested",
            payload={"after": {"provider": provider}},
        )
    await uow.commit()


async def provision_login(
    uow: UnitOfWork,
    ws: m.Workspace,
    *,
    provider: str,
    email: str,
    display_name: str,
    handle_hint: str,
    groups: set[str] | frozenset[str],
) -> m.WorkspaceMember:
    """Return the active member for a verified identity, creating or activating it when policy allows.

    Allowed when the person is already a member (an `invited` one is activated), is in at least one mapped
    group, or their e-mail domain is on the workspace allow-list. Otherwise an `access_request` review row is
    left and login is refused with 403.
    """
    s = uow.session
    person = await tenancy.get_or_create_person(uow, display_name=display_name, email=email.lower())
    member = await s.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.person_id == person.id
        )
    )
    mapped = bool(
        {g.lower() for g in groups}
        & {
            mp.group.lower()
            for mp in (
                await s.scalars(
                    select(m.IdpGroupMapping).where(
                        m.IdpGroupMapping.workspace_id == ws.id,
                        m.IdpGroupMapping.provider == provider,
                        m.IdpGroupMapping.deleted_at.is_(None),
                    )
                )
            ).all()
        }
    )
    if member is None:
        if not (mapped or domain_allowed(ws, email)):
            await _request_access(uow, ws, person, provider)
            raise ApiError(403, "pending_approval", "your account is awaiting approval by a workspace admin")
        handle, _ = await _unique_handle(uow, ws, "@" + handle_hint.lower().lstrip("@"), person.id)
        member = await tenancy.add_member(uow, ws, person, handle=handle, actor_id=person.id)
    elif member.status == MemberStatus.INVITED:
        member.status = MemberStatus.ACTIVE  # an admin-created invitation is itself the allow-list entry
    if member.status != MemberStatus.ACTIVE:
        raise ApiError(403, "suspended", "this account is suspended")
    await apply_groups(uow, ws, person.id, provider, groups)
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=person.id,
        entity_type=TargetType.PERSON,
        entity_id=person.id,
        event_type="auth.login",
        payload={"after": {"provider": provider}},
    )
    await uow.commit()
    return member


Lookup = Callable[[str], Awaitable[DirectoryUser | None]]


@dataclass
class SyncResult:
    checked: int = 0
    changed: int = 0
    missing: int = 0
    log: list[str] = field(default_factory=list)


async def sync_workspace(uow: UnitOfWork, ws: m.Workspace, lookup: Lookup) -> SyncResult:
    """Re-read each member's directory groups (found by e-mail, as login matched them)."""
    s = uow.session
    result = SyncResult()
    rows = (
        await s.execute(
            select(m.WorkspaceMember, m.Person.email)
            .join(m.Person, m.Person.id == m.WorkspaceMember.person_id)
            .where(m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.deleted_at.is_(None))
        )
    ).all()
    for member, email in rows:
        user = await lookup(email)
        result.checked += 1
        if user is None:
            result.missing += 1
        delta = await apply_groups(uow, ws, member.person_id, "ldap", user.groups if user else frozenset())
        if delta.changed:
            result.changed += 1
            added, removed = delta.roles_added + delta.teams_added, delta.roles_removed + delta.teams_removed
            result.log.append(f"{member.handle}: +{added} -{removed}")
    if rows:
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=rows[0][0].person_id,
            entity_type=TargetType.PERSON,
            entity_id=rows[0][0].person_id,
            event_type="idp.synced",
            payload={
                "after": {"checked": result.checked, "changed": result.changed, "missing": result.missing}
            },
        )
    await uow.commit()
    return result
