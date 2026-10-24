"""Permission catalogue and builtin roles (object model §10, Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass

from tt_schema.enums import GrantLevel

WORKSPACE_ADMIN = "workspace_admin"
MANAGER = "manager"
PROJECT_OWNER = "project_owner"
CONTRIBUTOR = "contributor"
VIEWER = "viewer"

_MANAGER = frozenset(
    {
        "item.read",
        "item.write_overlay",
        "classification.write",
        "classification.confirm",
        "weight.set",
        "risk.write",
        "approval.decide",
        "review_queue.resolve",
        "violation.resolve",
        "violation.waive",
        "habit.view_team",
        "habit.coach",
        "view.share",
        "context_note.write",
        "policy.read",
        "policy.write",
        "objective.write",
        "translator_rule.write",
        "project.create",
        "dailylog.review",
        # what every contributor can do
        "capture.write",
        "comment.write",
        "evidence.write",
        "time.write",
        "view.save",
        "item.write_capture",
    }
)
_PROJECT_OWNER = _MANAGER | {
    "project.write",
    "project_member.write",
    "milestone.write",
    "iteration.write",
    "approval.request",
}
_CONTRIBUTOR = frozenset(
    {
        "item.read",
        "item.write_capture",
        "comment.write",
        "evidence.write",
        "time.write",
        "view.save",
        "capture.write",
    }
)


@dataclass(frozen=True)
class RoleSpec:
    key: str
    name: str
    permissions: frozenset[str]
    scoped: bool = False  # may be assigned at project/team/product scope


ROLES: dict[str, RoleSpec] = {
    r.key: r
    for r in (
        RoleSpec(WORKSPACE_ADMIN, "Workspace admin", frozenset({"*"})),
        RoleSpec(MANAGER, "Manager", _MANAGER, scoped=True),
        RoleSpec(PROJECT_OWNER, "Project owner", _PROJECT_OWNER, scoped=True),
        RoleSpec(CONTRIBUTOR, "Contributor", _CONTRIBUTOR, scoped=True),
        RoleSpec(VIEWER, "Viewer", frozenset({"item.read", "view.read"}), scoped=True),
        RoleSpec("feature_admin:dimensions", "Dimension admin", frozenset({"dimension.admin"})),
        RoleSpec("feature_admin:process", "Process admin", frozenset({"process.admin"})),
        RoleSpec("feature_admin:products", "Product admin", frozenset({"product.admin"})),
        RoleSpec("policy_admin", "Policy admin", frozenset({"policy.admin", "policy.read"})),
    )
}

ALL_PERMISSIONS: frozenset[str] = frozenset().union(*(r.permissions for r in ROLES.values())) - {"*"} | {
    "member.write",
    "team.write",
    "reporting_line.write",
    "role.admin",
    "grant.write",
    "workspace.admin",
}

# Permissions that apply to what the actor produced themselves (capture layer, own rows).
OWN_PERMISSIONS = frozenset(
    {"item.write_capture", "time.write", "evidence.write", "comment.write", "capture.write", "view.save"}
)

_READ = frozenset({"item.read", "view.read"})
_COMMENT = _READ | {"comment.write"}
_WRITE = _COMMENT | {
    "item.write_overlay",
    "item.write_capture",
    "classification.write",
    "evidence.write",
    "time.write",
    "risk.write",
    "weight.set",
}
_ADMIN = _WRITE | {
    "classification.confirm",
    "approval.decide",
    "approval.request",
    "project.write",
    "milestone.write",
    "iteration.write",
    "review_queue.resolve",
    "project_member.write",
}

GRANT_PERMISSIONS: dict[GrantLevel, frozenset[str]] = {
    GrantLevel.READ: _READ,
    GrantLevel.COMMENT: _COMMENT,
    GrantLevel.WRITE: _WRITE,
    GrantLevel.ADMIN: _ADMIN,
}

# Token scopes (capture < read < manage < admin). A token's rights are its owner's, narrowed by its scopes.
TOKEN_SCOPES = ("capture", "read", "manage", "admin")
