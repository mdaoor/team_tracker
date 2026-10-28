"""Public docstring coverage of the server's domain and governance packages (Phase 13 R4).

A "public item" is a module-level function, async function or class whose name does not start with an underscore, and
each public method of such a class. Pydantic models, dataclasses and enums count like any class. A leading `#` comment
on the line above a class (the old convention for models whose JSON-schema description must stay empty) does not count:
the point of the measure is that the docstring is visible to people and to the generated schemas.

    uv run python tools/docstring_coverage.py            # print the summary and the first offenders
    uv run python tools/docstring_coverage.py --list     # print all of them
    uv run python tools/docstring_coverage.py --min 95   # exit 1 below 95 %
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGES = ("apps/server/tt_server/domain", "apps/server/tt_server/governance")


@dataclass
class Report:
    """Counts and the undocumented items found by `measure`."""

    total: int
    documented: int
    missing: list[str]

    @property
    def percent(self) -> float:
        """Documented share in percent (100 when nothing is public)."""
        return 100.0 * self.documented / self.total if self.total else 100.0


def _public(name: str) -> bool:
    return not name.startswith("_")


def _items(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    out: list[tuple[str, ast.AST]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _public(node.name):
            out.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and _public(node.name):
            out.append((node.name, node))
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef) and _public(sub.name):
                    out.append((f"{node.name}.{sub.name}", sub))
    return out


def measure(packages: tuple[str, ...] = DEFAULT_PACKAGES, root: Path = ROOT) -> Report:
    """Walk the packages and count public items with and without a docstring."""
    total, documented, missing = 0, 0, []
    for pkg in packages:
        for path in sorted((root / pkg).rglob("*.py")):
            tree = ast.parse(path.read_text())
            for name, node in _items(tree):
                total += 1
                if ast.get_docstring(node):  # type: ignore[arg-type]
                    documented += 1
                else:
                    missing.append(f"{path.relative_to(root)}:{node.lineno} {name}")  # type: ignore[attr-defined]
    return Report(total, documented, missing)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min", type=float, default=0.0, help="fail when coverage is below this percentage")
    ap.add_argument("--list", action="store_true", help="print every undocumented item")
    ap.add_argument("packages", nargs="*", default=list(DEFAULT_PACKAGES))
    args = ap.parse_args(argv)
    rep = measure(tuple(args.packages))
    print(f"public docstring coverage: {rep.documented}/{rep.total} = {rep.percent:.1f} %")
    for line in rep.missing if args.list else rep.missing[:15]:
        print("  missing:", line)
    return 0 if rep.percent >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
