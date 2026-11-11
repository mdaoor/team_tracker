"""CI guard (Phase 4 acceptance): every router module has at least one `test_acl_*` file that declares it."""

from __future__ import annotations

import ast
from pathlib import Path

ROUTERS = Path(__file__).parents[2] / "tt_server" / "api" / "routers"
TESTS = Path(__file__).parents[1]


def _declared() -> set[str]:
    out: set[str] = set()
    for path in TESTS.rglob("test_acl_*.py"):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(getattr(t, "id", "") == "ROUTERS_COVERED" for t in targets) and node.value:
                    out |= {str(c) for c in ast.literal_eval(node.value)}
    return out


def test_every_router_module_has_an_acl_test() -> None:
    modules = {p.stem for p in ROUTERS.glob("*.py") if p.stem != "__init__"}
    missing = modules - _declared()
    assert not missing, f"routers without a test_acl_* file declaring ROUTERS_COVERED: {sorted(missing)}"


def test_declared_routers_exist() -> None:
    modules = {p.stem for p in ROUTERS.glob("*.py")}
    assert _declared() <= modules
