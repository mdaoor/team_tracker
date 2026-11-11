"""Phase 13 R4.1 (review T7): layers point one way, imports have no cycles, lazy imports stay inside a budget.

* nothing outside `tt_server.api` / `tt_server.main` imports the HTTP layer (the domain raises `domain.errors`);
* the module-level import graph of `tt_server` is acyclic;
* function-level imports of other `tt_server` modules (a cycle workaround) stay within `LAZY_BUDGET`; `admin.py` (CLI
  start-up time) is exempt.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "tt_server"
LAZY_BUDGET = 30
EXEMPT_LAZY = {"tt_server.admin"}


def _modules() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in ROOT.rglob("*.py"):
        name = ".".join(("tt_server", *p.relative_to(ROOT).with_suffix("").parts))
        out[name.removesuffix(".__init__")] = p
    return out


def _targets(node: ast.AST, mods: dict[str, Path], pkg: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:
            parts = pkg.split(".")
            base = ".".join([*parts[: len(parts) - node.level + 1], *([node.module] if node.module else [])])
        for a in node.names:
            cand = f"{base}.{a.name}"
            found.append(cand if cand in mods else base)
    elif isinstance(node, ast.Import):
        found += [a.name for a in node.names]
    return [t for t in found if t in mods]


def _graph() -> tuple[dict[str, set[str]], list[tuple[str, str]]]:
    mods = _modules()
    graph: dict[str, set[str]] = defaultdict(set)
    lazy: list[tuple[str, str]] = []
    for name, path in mods.items():
        pkg = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]

        def walk(node: ast.AST, in_func: bool, name: str = name, pkg: str = pkg) -> None:
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, ast.FunctionDef | ast.AsyncFunctionDef):
                    walk(ch, True)
                elif isinstance(ch, ast.Import | ast.ImportFrom):
                    for t in _targets(ch, mods, pkg):
                        if in_func:
                            lazy.append((name, t))
                        else:
                            graph[name].add(t)
                else:
                    walk(ch, in_func)

        walk(ast.parse(path.read_text()), False)
    return graph, lazy


def test_the_domain_does_not_import_the_http_layer() -> None:
    graph, lazy = _graph()
    inverted = sorted(
        f"{src} -> {dst}"
        for src, dsts in [*graph.items(), *((s, {d}) for s, d in lazy)]
        for dst in dsts
        if dst.startswith("tt_server.api")
        and not (src.startswith("tt_server.api") or src == "tt_server.main")
    )
    assert inverted == []


def test_module_level_imports_have_no_cycles() -> None:
    graph, _ = _graph()
    seen: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    cycles: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        seen[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph.get(v, ()):
            if w not in seen:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], seen[w])
        if low[v] == seen[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                cycles.append(sorted(comp))

    for v in list(graph):
        if v not in seen:
            visit(v)
    assert cycles == []


def test_lazy_imports_of_our_own_modules_stay_within_budget() -> None:
    _, lazy = _graph()
    used = [(s, d) for s, d in lazy if s not in EXEMPT_LAZY]
    assert len(used) <= LAZY_BUDGET, f"{len(used)} function-level imports of tt_server modules: {used}"


def test_only_the_unit_of_work_commits() -> None:
    """Phase 13 R4.3 (review T4): a bare `.commit()` skips the missing-Event guard. Everything goes through
    `uow.commit()`, `uow.commit_bookkeeping()` or `db.uow.commit_derived()`."""
    import re

    allowed_receivers = {"uow", "self"}
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.relative_to(ROOT).as_posix() == "db/uow.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for m in re.finditer(r"(\w+(?:\.\w+)*)\.commit\(\)", line):
                if m.group(1) not in allowed_receivers:
                    offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert offenders == []


def test_no_request_handler_seeds_reference_data() -> None:
    """Built-ins are loaded by `bootstrap.seed_reference_data` (migrate + start), not inside GET handlers."""
    hits = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "api").rglob("*.py")
        if "ensure_builtins" in p.read_text()
    ]
    assert hits == []
