"""Per-request access context: the actor's roles, memberships, reporting lines and grants, and `can()`."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import utcnow
from tt_schema.enums import GrantLevel, ProductLinkRole, ProjectRole, ScopeType, SubjectType, TargetType

from tt_server.acl.permissions import GRANT_PERMISSIONS, OWN_PERMISSIONS, PROJECT_OWNER, ROLES
from tt_server.acl.reporting import LINE_KINDS, reports_of
from tt_server.db import models as m

MANAGER_ROLES = ("manager", "project_owner", "workspace_admin")


@dataclass(frozen=True)
class ObjRef:
    """What a permission check needs to know about the object (build with `item_ref` / `project_ref`)."""

    project_id: str | None = None
    team_id: str | None = None
    product_ids: tuple[str, ...] = ()
    item_ids: tuple[str, ...] = ()  # the item and its ancestors (grants inherit down)
    owner_ids: tuple[str, ...] = ()  # reporter / assignee: own-row permissions
    grant_keys: tuple[
        tuple[str, str], ...
    ] = ()  # (object_type, object_id) ancestry used by AccessGrant checks


@dataclass
class Assignment:
    role_key: str
    permissions: frozenset[str]
    scope_type: ScopeType
    scope_id: str | None


@dataclass
class AclContext:
    ws_id: str
    person_id: str
    assignments: list[Assignment] = field(default_factory=list)
    is_admin: bool = False
    member_project_ids: set[str] = field(default_factory=set)  # owner or ProjectMember of any role
    manager_project_ids: set[str] = field(default_factory=set)  # owner or ProjectMember owner/manager
    team_ids: set[str] = field(default_factory=set)
    team_ancestor_ids: set[str] = field(default_factory=set)  # my teams and all their ancestors
    team_children: dict[str, set[str]] = field(default_factory=dict)
    managed_ids: set[str] = field(default_factory=set)  # line/senior reports
    project_people_ids: set[str] = field(default_factory=set)  # people on projects I manage
    grants: dict[tuple[str, str], GrantLevel] = field(default_factory=dict)
    # derived "manager coverage" for visibility
    manager_all: bool = False
    manager_scope_projects: set[str] = field(default_factory=set)
    manager_scope_teams: set[str] = field(default_factory=set)
    reader_all: bool = False  # a workspace-scope role with item.read
    reader_projects: set[str] = field(default_factory=set)
    token_scopes: tuple[str, ...] | None = None  # None = interactive session (no narrowing)

    # ---- team tree helpers
    def team_descendants(self, team_id: str) -> set[str]:
        out, stack = {team_id}, [team_id]
        while stack:
            for child in self.team_children.get(stack.pop(), ()):
                if child not in out:
                    out.add(child)
                    stack.append(child)
        return out

    # ---- permission check
    def can(self, permission: str, obj: ObjRef | None = None) -> bool:
        if self.is_admin:
            return True
        if obj is not None and obj.project_id:
            if obj.project_id in self.manager_project_ids and permission in ROLES[PROJECT_OWNER].permissions:
                return True
        for a in self.assignments:
            if permission not in a.permissions and "*" not in a.permissions:
                continue
            if a.scope_type == ScopeType.WORKSPACE:
                return True
            if obj is None:
                continue
            if a.scope_type == ScopeType.PROJECT and a.scope_id == obj.project_id:
                return True
            if (
                a.scope_type == ScopeType.TEAM
                and obj.team_id
                and obj.team_id in self.team_descendants(str(a.scope_id))
            ):
                return True
            if a.scope_type == ScopeType.PRODUCT and a.scope_id in obj.product_ids:
                return True
        if obj is not None:
            if permission in OWN_PERMISSIONS and self.person_id in obj.owner_ids:
                return True
            for key in obj.grant_keys:
                level = self.grants.get(key)
                if level is not None and permission in GRANT_PERMISSIONS[level]:
                    return True
        elif permission in OWN_PERMISSIONS:
            return self.has_workspace_permission(permission)
        return False

    def has_workspace_permission(self, permission: str) -> bool:
        return self.is_admin or any(
            a.scope_type == ScopeType.WORKSPACE and (permission in a.permissions or "*" in a.permissions)
            for a in self.assignments
        )

    def is_line_manager_of(self, person_id: str) -> bool:
        return person_id in self.managed_ids


async def load_context(
    session: AsyncSession, ws_id: str, person_id: str, token_scopes: tuple[str, ...] | None = None
) -> AclContext:
    ctx = AclContext(ws_id=ws_id, person_id=person_id, token_scopes=token_scopes)
    now = utcnow()

    # roles
    rows = (
        await session.execute(
            select(m.Role, m.RoleAssignment)
            .join(m.RoleAssignment, m.RoleAssignment.role_id == m.Role.id)
            .where(m.RoleAssignment.person_id == person_id, m.RoleAssignment.deleted_at.is_(None))
        )
    ).all()
    for role, ra in rows:
        if ra.scope_type == ScopeType.WORKSPACE and ra.scope_id != ws_id:
            continue  # another workspace's assignment
        ctx.assignments.append(Assignment(role.key, frozenset(role.permissions), ra.scope_type, ra.scope_id))
    ctx.is_admin = any(
        a.role_key == "workspace_admin" and a.scope_type == ScopeType.WORKSPACE for a in ctx.assignments
    )

    # projects
    for pid, owner in (
        await session.execute(
            select(m.Project.id, m.Project.owner_id).where(
                m.Project.workspace_id == ws_id, m.Project.deleted_at.is_(None)
            )
        )
    ).all():
        if owner == person_id:
            ctx.member_project_ids.add(pid)
            ctx.manager_project_ids.add(pid)
    for pid, prole in (
        await session.execute(
            select(m.ProjectMember.project_id, m.ProjectMember.role)
            .join(m.Project, m.Project.id == m.ProjectMember.project_id)
            .where(
                m.ProjectMember.person_id == person_id,
                m.Project.workspace_id == ws_id,
                m.ProjectMember.deleted_at.is_(None),
            )
        )
    ).all():
        ctx.member_project_ids.add(pid)
        if prole in (ProjectRole.OWNER, ProjectRole.MANAGER):
            ctx.manager_project_ids.add(pid)

    # teams (direct, ancestors, children map)
    teams = (
        await session.execute(
            select(m.Team.id, m.Team.parent_team_id).where(
                m.Team.workspace_id == ws_id, m.Team.deleted_at.is_(None)
            )
        )
    ).all()
    parent = {t: p for t, p in teams}
    for t, p in teams:
        if p:
            ctx.team_children.setdefault(p, set()).add(t)
    ctx.team_ids = set(
        (
            await session.scalars(
                select(m.Membership.team_id).where(
                    m.Membership.person_id == person_id,
                    m.Membership.left_at.is_(None),
                    m.Membership.deleted_at.is_(None),
                )
            )
        ).all()
    ) & set(parent)
    for t in ctx.team_ids:
        while t and t not in ctx.team_ancestor_ids:
            ctx.team_ancestor_ids.add(t)
            t = parent.get(t)  # type: ignore[assignment]

    # reporting lines
    ctx.managed_ids = set(await reports_of(session, ws_id, person_id, LINE_KINDS))

    # grants (person, my teams, my roles)
    role_ids = {str(r.id) for r, _ in rows}
    subject_filter = or_(
        (m.AccessGrant.subject_type == SubjectType.PERSON) & (m.AccessGrant.subject_id == person_id),
        (m.AccessGrant.subject_type == SubjectType.TEAM) & m.AccessGrant.subject_id.in_(ctx.team_ids or {""}),
        (m.AccessGrant.subject_type == SubjectType.ROLE) & m.AccessGrant.subject_id.in_(role_ids or {""}),
    )
    order = [GrantLevel.READ, GrantLevel.COMMENT, GrantLevel.WRITE, GrantLevel.ADMIN]
    for g in (
        await session.scalars(
            select(m.AccessGrant).where(
                m.AccessGrant.workspace_id == ws_id,
                m.AccessGrant.deleted_at.is_(None),
                subject_filter,
                or_(m.AccessGrant.expires_at.is_(None), m.AccessGrant.expires_at > now),
            )
        )
    ).all():
        key = (g.object_type.value, g.object_id)
        if key not in ctx.grants or order.index(g.level) > order.index(ctx.grants[key]):
            ctx.grants[key] = g.level

    # coverage for visibility
    for a in ctx.assignments:
        if a.role_key in MANAGER_ROLES:
            if a.scope_type == ScopeType.WORKSPACE:
                ctx.manager_all = True
            elif a.scope_type == ScopeType.PROJECT and a.scope_id:
                ctx.manager_scope_projects.add(a.scope_id)
            elif a.scope_type == ScopeType.TEAM and a.scope_id:
                ctx.manager_scope_teams |= ctx.team_descendants(a.scope_id)
            elif a.scope_type == ScopeType.PRODUCT and a.scope_id:
                ctx.manager_scope_projects |= await _projects_of_product(session, a.scope_id)
        if "item.read" in a.permissions or "*" in a.permissions:
            if a.scope_type == ScopeType.WORKSPACE:
                ctx.reader_all = True
            elif a.scope_type == ScopeType.PROJECT and a.scope_id:
                ctx.reader_projects.add(a.scope_id)
    ctx.manager_scope_projects |= ctx.manager_project_ids

    # people on the projects I manage (for habit / violation visibility)
    if ctx.manager_scope_projects or ctx.manager_all:
        proj_filter = ctx.manager_scope_projects
        people: set[str | None] = set(
            (
                await session.scalars(
                    select(m.ProjectMember.person_id).where(
                        m.ProjectMember.project_id.in_(proj_filter or {""})
                    )
                )
            ).all()
        )
        people |= set(
            (
                await session.scalars(
                    select(m.WorkItem.assignee_id).where(
                        m.WorkItem.project_id.in_(proj_filter or {""}), m.WorkItem.assignee_id.is_not(None)
                    )
                )
            ).all()
        )
        ctx.project_people_ids = {p for p in people if p}
    return ctx


async def _projects_of_product(session: AsyncSession, product_id: str) -> set[str]:
    out: set[str] = set()
    frontier = {product_id}
    seen: set[str] = set()
    while frontier:
        seen |= frontier
        out |= set(
            (
                await session.scalars(
                    select(m.ProductLink.target_id).where(
                        m.ProductLink.product_id.in_(frontier),
                        m.ProductLink.target_type == TargetType.PROJECT,
                        m.ProductLink.role == ProductLinkRole.DELIVERS,
                        m.ProductLink.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        frontier = (
            set(
                (
                    await session.scalars(
                        select(m.Product.id).where(m.Product.parent_product_id.in_(frontier))
                    )
                ).all()
            )
            - seen
        )
    return out


async def product_chain(session: AsyncSession, project_id: str) -> tuple[str, ...]:
    """The product delivering the project and all its ancestors."""
    pid = await session.scalar(
        select(m.ProductLink.product_id).where(
            m.ProductLink.target_type == TargetType.PROJECT,
            m.ProductLink.target_id == project_id,
            m.ProductLink.role == ProductLinkRole.DELIVERS,
            m.ProductLink.deleted_at.is_(None),
        )
    )
    chain: list[str] = []
    while pid and pid not in chain:
        chain.append(pid)
        pid = await session.scalar(select(m.Product.parent_product_id).where(m.Product.id == pid))
    return tuple(chain)


async def project_ref(session: AsyncSession, project: m.Project) -> ObjRef:
    products = await product_chain(session, project.id)
    keys = (("project", project.id), *(("product", p) for p in products))
    return ObjRef(
        project_id=project.id,
        team_id=project.team_id,
        product_ids=products,
        owner_ids=(project.owner_id,),
        grant_keys=keys,
    )


async def item_ref(session: AsyncSession, item: m.WorkItem) -> ObjRef:
    project = await session.get(m.Project, item.project_id)
    assert project is not None  # noqa: S101
    products = await product_chain(session, project.id)
    ancestors = list(
        (
            await session.scalars(
                select(m.WorkItem.id).where(
                    m.WorkItem.path.op("@>")(item.path), m.WorkItem.deleted_at.is_(None)
                )
            )
        ).all()
    ) or [item.id]
    keys = (
        *(("work_item", i) for i in ancestors),
        ("project", project.id),
        *(("product", p) for p in products),
    )
    owners = tuple(x for x in (item.reporter_id, item.assignee_id) if x)
    return ObjRef(
        project_id=project.id,
        team_id=item.team_id or project.team_id,
        product_ids=products,
        item_ids=tuple(ancestors),
        owner_ids=owners,
        grant_keys=keys,
    )
