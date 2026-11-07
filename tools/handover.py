"""Fill the handover prompts of plans/appendix-d-handover-prompt-templates.md for a phase (the appendix stays the single source).

uv run python tools/handover.py kickoff 10            # D.1  brief a fresh agent
uv run python tools/handover.py resume 10             # D.2  continue a partly done phase
uv run python tools/handover.py review 10 --pr URL    # D.3  independent review of the phase PR
uv run python tools/handover.py spec-change "text"    # D.4  design change via ADR
uv run python tools/handover.py testplan 10           # TESTPLAN.md skeleton from the acceptance criteria (D.1 work method)
uv run python tools/handover.py phases                # the phases and their files
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / "plans"
APPENDIX = PLANS / "appendix-d-handover-prompt-templates.md"
PHASE_FILE = re.compile(r"^\d+-phase-(\d+b?)-(.+)\.md$")


@dataclass(frozen=True)
class Phase:
    label: str  # "0", "10", "2b"
    slug: str
    file: Path

    @property
    def order(self) -> tuple[int, str]:
        n = re.match(r"\d+", self.label)
        assert n  # noqa: S101
        return int(n.group()), self.label[len(n.group()) :]


def phases(plans: Path = PLANS) -> list[Phase]:
    out = [Phase(m.group(1), m.group(2), p) for p in plans.glob("*.md") if (m := PHASE_FILE.match(p.name))]
    return sorted(out, key=lambda p: p.order)


def find(label: str, plans: Path = PLANS) -> Phase:
    for p in phases(plans):
        if p.label == label.lower().removeprefix("phase-"):
            return p
    raise SystemExit(f"unknown phase {label!r}; known: {', '.join(p.label for p in phases(plans))}")


def neighbour(p: Phase, step: int, plans: Path = PLANS) -> Phase | None:
    ps = phases(plans)
    i = ps.index(p) + step
    return ps[i] if 0 <= i < len(ps) else None


def templates(text: str | None = None) -> dict[str, str]:
    """`{"D.1": prompt, ...}` parsed from the fenced blocks under each `## D.n` heading."""
    text = text if text is not None else APPENDIX.read_text()
    out: dict[str, str] = {}
    for m in re.finditer(r"^## (D\.\d+)\b.*?\n```\n(.*?)\n```", text, re.S | re.M):
        out[m.group(1)] = m.group(2)
    return out


def repo_url() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return str(ROOT)


def fill(template: str, values: dict[str, str]) -> str:
    """Replace `<key>` placeholders; any placeholder left over is reported rather than silently kept."""
    out = template
    for k, v in values.items():
        out = out.replace(f"<{k}>", v)
    return out


def kickoff(p: Phase, plans: Path = PLANS, url: str | None = None) -> str:
    nxt = neighbour(p, +1, plans)
    prev = neighbour(p, -1, plans)
    text = fill(
        templates()["D.1"],
        {
            "N+1": nxt.label if nxt else "(none: this is the last phase)",
            "N": p.label,
            "path/URL": url or repo_url(),
            "slug": p.slug,
            "phase file": p.file.name,
        },
    )
    if prev:  # item 6 of the reading list points at the previous phase file's handover notes
        text += f'\n\nPrevious phase: plans/{prev.file.name} (read its "Handover notes" section).'
    return text


def resume(p: Phase) -> str:
    return fill(templates()["D.2"], {"N": p.label, "slug": p.slug, "phase file": p.file.name})


def review(p: Phase, pr: str) -> str:
    return fill(templates()["D.3"], {"link": pr, "N": p.label, "phase file": p.file.name})


def spec_change(description: str) -> str:
    return fill(templates()["D.4"], {"describe": description})


def acceptance(p: Phase) -> list[str]:
    """The phase file's `- [ ] ...` lines under its Acceptance criteria heading."""
    text = p.file.read_text()
    m = re.search(r"^## Acceptance criteria\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return [ln[6:].strip() for ln in (m.group(1) if m else "").splitlines() if ln.startswith("- [ ] ")]


def testplan(p: Phase) -> str:
    rows = acceptance(p)
    lines = [
        f"# Test plan: Phase {p.label} ({p.slug})",
        "",
        f"Derived from the acceptance criteria in `plans/{p.file.name}`. Fill in the test that would fail if the behaviour regressed.",
        "",
        "| # | Acceptance criterion | Test (file::name) | Status |",
        "|---|---|---|---|",
    ]
    lines += [f"| {i} | {r.replace('|', '/')} | _todo_ | open |" for i, r in enumerate(rows, 1)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("kickoff", "resume", "testplan"):
        sp = sub.add_parser(name)
        sp.add_argument("phase")
    sp = sub.add_parser("review")
    sp.add_argument("phase")
    sp.add_argument("--pr", required=True, help="link to the pull request")
    sp = sub.add_parser("spec-change")
    sp.add_argument("description")
    sub.add_parser("phases")
    a = ap.parse_args(argv)
    if a.cmd == "phases":
        for p in phases():
            sys.stdout.write(f"{p.label:>3}  {p.slug:<40} plans/{p.file.name}\n")
        return 0
    if a.cmd == "spec-change":
        sys.stdout.write(spec_change(a.description) + "\n")
        return 0
    p = find(a.phase)
    sys.stdout.write(
        {
            "kickoff": lambda: kickoff(p),
            "resume": lambda: resume(p),
            "review": lambda: review(p, a.pr),
            "testplan": lambda: testplan(p),
        }[a.cmd]()
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
