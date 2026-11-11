"""Table-driven permission matrix generated from the catalogue (no hand-written expectations)."""

from __future__ import annotations

import itertools

import pytest
from tt_schema.enums import GrantLevel, ScopeType
from tt_server.acl.context import AclContext, Assignment, ObjRef
from tt_server.acl.permissions import ALL_PERMISSIONS, GRANT_PERMISSIONS, OWN_PERMISSIONS, ROLES

WS = "ws-1"
PROJECT, OTHER_PROJECT, TEAM, OTHER_TEAM, PRODUCT, OTHER_PRODUCT = "p1", "p2", "t1", "t2", "pr1", "pr2"
OBJ = ObjRef(project_id=PROJECT, team_id=TEAM, product_ids=(PRODUCT,), owner_ids=("someone-else",))
# (scope_type, scope_id) relative to OBJ: does the scope contain the object?
SCOPES = [
    (ScopeType.WORKSPACE, WS, True),
    (ScopeType.PROJECT, PROJECT, True),
    (ScopeType.PROJECT, OTHER_PROJECT, False),
    (ScopeType.TEAM, TEAM, True),
    (ScopeType.TEAM, OTHER_TEAM, False),
    (ScopeType.PRODUCT, PRODUCT, True),
    (ScopeType.PRODUCT, OTHER_PRODUCT, False),
]


def expected(role_key: str, permission: str, scope: tuple[ScopeType, str, bool]) -> bool:
    """Independent oracle: the role grants the permission and the scope contains the object."""
    st, _, contains = scope
    perms = ROLES[role_key].permissions
    if "*" not in perms and permission not in perms:
        return False
    if not ROLES[role_key].scoped and st != ScopeType.WORKSPACE:
        return False
    return st == ScopeType.WORKSPACE or contains


def context_for(role_key: str, scope: tuple[ScopeType, str, bool]) -> AclContext:
    ctx = AclContext(ws_id=WS, person_id="me")
    st, sid, _ = scope
    ctx.assignments.append(Assignment(role_key, ROLES[role_key].permissions, st, sid))
    ctx.is_admin = role_key == "workspace_admin" and st == ScopeType.WORKSPACE
    ctx.team_children = {}
    return ctx


CASES = [
    pytest.param(role, perm, scope, id=f"{role}|{perm}|{scope[0].value}:{scope[1]}")
    for role, perm, scope in itertools.product(ROLES, sorted(ALL_PERMISSIONS), SCOPES)
    if ROLES[role].scoped
    or scope[0] == ScopeType.WORKSPACE  # unscoped roles are only assigned workspace-wide
]


def test_matrix_is_large_enough() -> None:
    assert len(CASES) >= 150


@pytest.mark.parametrize(("role", "permission", "scope"), CASES)
def test_role_permission_scope(role: str, permission: str, scope: tuple[ScopeType, str, bool]) -> None:
    ctx = context_for(role, scope)
    assert ctx.can(permission, OBJ) is expected(role, permission, scope), (role, permission, scope)


@pytest.mark.parametrize(("role", "permission"), list(itertools.product(ROLES, sorted(ALL_PERMISSIONS))))
def test_workspace_level_check_without_an_object(role: str, permission: str) -> None:
    ctx = context_for(role, (ScopeType.WORKSPACE, WS, True))
    perms = ROLES[role].permissions
    assert ctx.can(permission) is ("*" in perms or permission in perms)


def test_scoped_role_never_grants_a_workspace_level_check() -> None:
    ctx = context_for("manager", (ScopeType.PROJECT, PROJECT, True))
    assert ctx.can("item.write_overlay", OBJ) and not ctx.can("item.write_overlay")


@pytest.mark.parametrize("level", list(GrantLevel))
def test_grants_confer_exactly_their_level(level: GrantLevel) -> None:
    ctx = AclContext(ws_id=WS, person_id="me")
    ref = ObjRef(project_id=PROJECT, grant_keys=(("project", PROJECT),))
    ctx.grants[("project", PROJECT)] = level
    for perm in ALL_PERMISSIONS:
        assert ctx.can(perm, ref) is (perm in GRANT_PERMISSIONS[level]), (level, perm)
    other = ObjRef(project_id=OTHER_PROJECT, grant_keys=(("project", OTHER_PROJECT),))
    assert not ctx.can("item.read", other)


def test_own_rows_allow_capture_writes_only() -> None:
    ctx = AclContext(ws_id=WS, person_id="me")
    mine = ObjRef(project_id=PROJECT, owner_ids=("me",))
    for perm in ALL_PERMISSIONS:
        assert ctx.can(perm, mine) is (perm in OWN_PERMISSIONS)


def test_project_owner_implicit_role() -> None:
    ctx = AclContext(ws_id=WS, person_id="me", manager_project_ids={PROJECT})
    assert ctx.can("project.write", OBJ) and ctx.can("milestone.write", OBJ)
    assert not ctx.can("project.write", ObjRef(project_id=OTHER_PROJECT))
    assert not ctx.can("dimension.admin", OBJ)
