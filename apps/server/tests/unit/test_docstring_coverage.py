"""Phase 13 R4: public items in `domain/` and `governance/` are documented (tools/docstring_coverage.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _tool():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "docstring_coverage", ROOT / "tools" / "docstring_coverage.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["docstring_coverage"] = mod  # dataclasses resolves annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def test_domain_and_governance_public_docstring_coverage_is_at_least_ninety_percent() -> None:
    rep = _tool().measure()
    assert rep.total > 300, "the measure should see the domain and governance packages"
    assert rep.percent >= 90.0, f"{rep.percent:.1f} % documented; missing: {rep.missing[:10]}"


def test_the_measure_counts_what_it_claims(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text(
        'def documented():\n    """ok"""\n\ndef bare(): ...\n\ndef _private(): ...\n\n'
        'class K:\n    """ok"""\n\n    def method(self): ...\n\n    def _hidden(self): ...\n'
    )
    rep = _tool().measure(("pkg",), root=tmp_path)
    assert (rep.total, rep.documented) == (4, 2) and len(rep.missing) == 2
