"""Consistency of the ops scripts and their documentation (run by tests/ops/run.sh and CI).

* every ttctl command and subcommand answers --help with exit 0 and a complete page
* every docs path (and #anchor) any script prints exists
* error codes: every code a script uses is in scripts/lib/errors.tsv, every catalogue code has a troubleshooting
  section, every documented code exists in the catalogue and is used by a script
* the generated guide pages are current; product docs do not link to files that moved to the private repository
* no script prints an error outside the WHAT / WHY / FIX / DOCS format
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
GUIDE = ROOT / "docs" / "guide"
ENV = {**os.environ, "NO_COLOR": "1", "TT_LOG_FILE": "/dev/null", "TT_TTCTL": "scripts/ttctl"}

# (ttctl arguments) for every command and subcommand that has its own --help
HELP_CASES = [
    [],
    ["prereqs"],
    ["prereqs", "check"],
    ["prereqs", "install"],
    ["setup"],
    ["doctor"],
    ["start"],
    ["run"],
    ["stop"],
    ["restart"],
    ["status"],
    ["logs"],
    ["db"],
    ["db", "migrate"],
    ["db", "backup"],
    ["bundle"],
    ["bundle", "create"],
    ["bundle", "install"],
    ["bundle", "verify"],
    ["docker"],
    ["docker", "up"],
    ["client"],
    ["client", "install"],
    ["build"],
    ["check"],
    ["clean"],
    ["prod"],
    ["prod", "install"],
    ["prod", "upgrade"],
    ["prod", "rollback"],
    ["prod", "status"],
    ["repo", "split"],
    ["repo", "purge-history"],
    ["repo", "secrets-scan"],
    ["support-bundle"],
]


def ttctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPTS / "ttctl"), *args], capture_output=True, text=True, env=ENV, cwd=ROOT, check=False
    )  # noqa: S603


@pytest.mark.parametrize("args", HELP_CASES, ids=lambda a: " ".join(a) or "ttctl")
def test_every_command_answers_help(args: list[str]) -> None:
    r = ttctl(*args, "--help")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "SYNOPSIS" in out, out[:300]
    assert "EXAMPLES" in out
    examples = out.split("EXAMPLES", 1)[1].split("\n\n", 1)[0]
    assert len([ln for ln in examples.splitlines() if ln.strip()]) >= 3, f"fewer than 3 examples:\n{examples}"
    assert "EXIT CODES" in out and "4 blocked because offline" in out
    assert "SEE ALSO" in out and "docs/guide/" in out
    assert "GLOBAL OPTIONS" in out


def test_ttctl_help_command_and_version() -> None:
    r = ttctl("help", "db")
    assert r.returncode == 0 and "ttctl db" in r.stdout
    v = ttctl("version")
    assert v.returncode == 0 and "team-tracker" in v.stdout
    bad = ttctl("no-such-command")
    assert bad.returncode == 2 and "TT-E011" in bad.stderr


def _slug(heading: str) -> str:
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    text = md.read_text()
    out = {_slug(m.group(1)) for m in re.finditer(r"^#{1,6} (.+)$", text, re.M)}
    out |= set(re.findall(r'<a id="([^"]+)">', text))
    return out


def _script_files() -> list[Path]:
    return [
        p for p in SCRIPTS.rglob("*") if p.is_file() and (p.suffix in (".sh", ".py") or p.name == "ttctl")
    ]


def test_every_docs_path_printed_by_a_script_exists() -> None:
    refs: set[str] = set()
    for f in _script_files():
        for m in re.finditer(
            r"(docs/guide/[0-9A-Za-z_.\-]+\.md|\$GUIDE/[0-9A-Za-z_.\-]+\.md|\$TROUBLE)(#[a-z0-9\-]+)?",
            f.read_text(),
        ):
            path = (
                m.group(1)
                .replace("$GUIDE", "docs/guide")
                .replace("$TROUBLE", "docs/guide/11-troubleshooting.md")
            )
            refs.add(path + (m.group(2) or ""))
    for c in HELP_CASES:  # and what the help pages print
        for m in re.finditer(r"(docs/guide/[0-9A-Za-z_.\-]+\.md)(#[a-z0-9\-]+)?", ttctl(*c, "--help").stdout):
            refs.add(m.group(1) + (m.group(2) or ""))
    assert refs
    for ref in sorted(refs):
        path, _, anchor = ref.partition("#")
        assert (ROOT / path).exists(), f"scripts print {ref}, but {path} does not exist"
        if anchor and not anchor.startswith("tt-e"):
            assert anchor in anchors(ROOT / path), f"scripts print {ref}, but {path} has no #{anchor}"


def catalogue() -> dict[str, tuple[int, str]]:
    out = {}
    for line in (SCRIPTS / "lib" / "errors.tsv").read_text().splitlines():
        if line.startswith("TT-E"):
            code, rc, summary = line.split("\t")
            out[code] = (int(rc), summary)
    return out


def used_codes() -> set[str]:
    codes: set[str] = set()
    for f in _script_files():
        codes |= set(re.findall(r"\bTT-E\d{3}\b", f.read_text()))
    return codes


def test_every_code_used_by_a_script_is_catalogued() -> None:
    missing = used_codes() - set(catalogue())
    assert not missing, f"codes used in scripts but not in errors.tsv: {sorted(missing)}"


def test_every_catalogued_code_is_used_and_documented() -> None:
    doc = (GUIDE / "11-troubleshooting.md").read_text()
    documented = set(re.findall(r"^## (TT-E\d{3})\s*$", doc, re.M))
    cat = set(catalogue())
    assert cat - documented == set(), f"no troubleshooting section for {sorted(cat - documented)}"
    assert documented - cat == set(), f"documented but not in errors.tsv: {sorted(documented - cat)}"
    unused = cat - (used_codes() - {"TT-E001", "TT-E002"}) - {"TT-E001", "TT-E002"}
    assert not unused, f"catalogued but never used by a script: {sorted(unused)}"
    for code, (rc, _) in catalogue().items():
        section = doc.split(f"## {code}\n", 1)[1].split("\n## ", 1)[0]
        assert f"(exit {rc})" in section, f"{code}: section does not state exit {rc}"


def test_exit_codes_are_the_documented_set() -> None:
    assert {rc for rc, _ in catalogue().values()} <= {1, 2, 3, 4, 5}


def test_generated_guide_pages_are_current() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_guide_docs.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=ENV,
        check=False,
    )  # noqa: S603
    assert r.returncode == 0, r.stdout + r.stderr


def test_product_docs_do_not_reference_moved_files() -> None:
    # the two files that DESCRIBE the split name the moved paths on purpose
    exempt = {"docs/guide/14-repositories.md", "scripts/repo/internal-overlay/README.md"}
    files = [
        f
        for f in subprocess.run(
            ["git", "ls-files", "*.md"], capture_output=True, text=True, cwd=ROOT, check=True
        ).stdout.split()  # noqa: S603,S607
        if f not in exempt
    ]
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "repo" / "rewrite_internal_refs.py"), "--check", *files],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )  # noqa: S603
    assert r.returncode == 0, r.stdout


def test_internal_paths_are_gone_from_the_tree() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout.splitlines()  # noqa: S603,S607
    for line in (SCRIPTS / "repo" / "internal-paths.txt").read_text().splitlines():
        p = line.strip()
        if p and not p.startswith("#"):
            hits = [t for t in tracked if t == p or t.startswith(p + "/")]
            assert not hits, f"{p} should live in the private repository: {hits[:3]}"


def test_no_error_is_printed_outside_the_fail_format() -> None:
    bad = []
    for f in _script_files():
        if f.suffix != ".sh" and f.name != "ttctl":
            continue
        if f.parent.name in (
            "client",
            "bundle",
        ):  # stand-alone kits and the in-container builder have their own format
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"(^|[;&|{(]\s*|\s)(err|die)\s+\"", code) or re.search(
                r"echo\s+[\"']?(error|ERROR)\b", code
            ):
                bad.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not bad, "errors printed without WHAT/WHY/FIX/DOCS:\n" + "\n".join(bad)


def test_runnable_blocks_exist_for_both_paths() -> None:
    online = re.findall(r"^```bash runnable\n", "".join(p.read_text() for p in GUIDE.glob("*.md")), re.M)
    offline = re.findall(
        r"^```bash runnable-offline\n", (GUIDE / "03-install-airgapped.md").read_text(), re.M
    )
    assert len(online) >= 5 and len(offline) >= 5
