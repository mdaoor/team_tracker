"""Deterministic parser for the common manager phrases -> `ClientCommand[]` (no model, no network).

"new epic Checkout redesign under WEB", "merge WEB-101 and WEB-105", "tag DATA-1 DATA-2 #q4", ... Anything it does not
recognise returns `matched=False` and falls through to the gateway's `/v1/translate/command`. The parser never invents a
person, project or key: a project that is neither an upper-case key nor in the pack is left for the gateway.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tt_schema.client import ClientCommand

from tt_client.catalogue import Catalogue
from tt_client.commands import new_command
from tt_client.pack import ContextPack

KEY = r"[A-Za-z][A-Za-z0-9]+-\d+"
KEY_RE = re.compile(KEY)
HANDLE_RE = re.compile(r"@[A-Za-z0-9_.-]+")
TAG_RE = re.compile(r"#([A-Za-z0-9_-]+)")
PROJ = (
    r"(?-i:[A-Z][A-Z0-9]{1,9})|[A-Za-z][A-Za-z0-9 ]{1,30}?"  # key, or an alias/name resolved through the pack
)
DATE = r"\d{4}-\d{2}-\d{2}"
PRIORITIES = "lowest|low|medium|high|critical"
HANDLE_OR_ME = r"@[A-Za-z0-9_.-]+|me"
KEYS = rf"(?:{KEY}(?:\s*(?:,|and|&)?\s*{KEY})*)"
HANDLES = r"(?:@[A-Za-z0-9_.-]+(?:\s*(?:,|and|&)?\s*@[A-Za-z0-9_.-]+)*)"


@dataclass
class CommandParseContext:
    actor: str
    now: datetime
    pack: ContextPack | None = None
    catalogue: Catalogue | None = None
    role_mode: str | None = None
    mode: str = "assisted"


@dataclass
class Incomplete:
    op: str
    args: dict[str, Any]
    issues: list[str]


@dataclass
class CommandParse:
    commands: list[ClientCommand] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)  # segments no rule recognised -> gateway
    incomplete: list[Incomplete] = field(default_factory=list)  # recognised but missing/invalid args -> form

    @property
    def matched(self) -> bool:
        return bool(self.commands or self.incomplete)

    @property
    def complete(self) -> bool:
        """Every segment was understood and valid: safe to preview without the gateway."""
        return bool(self.commands) and not self.unmatched and not self.incomplete


Built = list[tuple[str, dict[str, Any]]]
Handler = Callable[["re.Match[str]", CommandParseContext], Built | None]


def _keys(text: str) -> list[str]:
    out: list[str] = []
    for k in KEY_RE.findall(text):
        k = k.upper()
        if k not in out:
            out.append(k)
    return out


def _handles(text: str, ctx: CommandParseContext) -> list[str]:
    out: list[str] = []
    for h in HANDLE_RE.findall(text):
        if h not in out:
            out.append(h)
    return out


def _person(tok: str, ctx: CommandParseContext) -> str:
    return ctx.actor if tok.strip().lower() == "me" else tok.strip()


def _project(tok: str, ctx: CommandParseContext) -> str | None:
    tok = tok.strip()
    if ctx.pack is not None:
        hit = ctx.pack.project_by_alias(tok)
        if hit:
            return hit
        return (
            None
            if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", tok)
            else tok
            if tok in ctx.pack.project_keys
            else None
        )
    return tok if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", tok) else None


def _clean(text: str) -> str:
    return text.strip().strip("\"'“”").strip()


def _sprint(kind: str, name: str) -> str:
    name = _clean(name)
    return name if re.match(r"(?i)(sprint|iteration)\b", name) else f"{kind.capitalize()} {name}"


def _split_titles(text: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\band\b|&)\s*", text.strip())
    return [_clean(p) for p in parts if _clean(p)]


# ------------------------------------------------------------------ rules
RULES: list[tuple[re.Pattern[str], Handler]] = []


def rule(pattern: str) -> Callable[[Handler], Handler]:
    compiled = re.compile(pattern, re.I)

    def deco(fn: Handler) -> Handler:
        RULES.append((compiled, fn))
        return fn

    return deco


@rule(
    rf"^(?:please\s+)?(?:create|new|add|make)\s+(?:an?\s+)?(epic|story|task|bug|deliverable)\s+(.+)\s+(?:under|in|on|for)\s+({KEY}|{PROJ})$"
)
def _item_create(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    kind, title, where = m.group(1).lower(), _clean(m.group(2)), m.group(3).strip()
    if KEY_RE.fullmatch(where):
        parent = where.upper()
        return [
            (
                "item.create",
                {"title": title, "type": kind, "project": parent.rsplit("-", 1)[0], "parent": parent},
            )
        ]
    proj = _project(where, ctx)
    return [("item.create", {"title": title, "type": kind, "project": proj})] if proj else None


@rule(
    r"^(?:create|new|add)\s+project\s+(.+?)\s+(?:key\s+)?((?-i:[A-Z][A-Z0-9]{1,9}))(?:\s+(scrum|kanban|waterfall|hybrid))?$"
)
def _project_create(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    args: dict[str, Any] = {"name": _clean(m.group(1)), "key": m.group(2)}
    if m.group(3):
        args["methodology"] = m.group(3).lower()
    return [("project.create", args)]


@rule(rf"^rename\s+project\s+({PROJ})\s+to\s+(.+)$")
def _project_rename(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    proj = _project(m.group(1), ctx)
    return [("project.update", {"project": proj, "name": _clean(m.group(2))})] if proj else None


@rule(rf"^baseline\s+({PROJ})(?:\s+as\s+(.+))?$")
def _baseline(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    proj = _project(m.group(1), ctx)
    if not proj:
        return None
    args: dict[str, Any] = {"project": proj}
    if m.group(2):
        args["name"] = _clean(m.group(2))
    return [("project.baseline.capture", args)]


@rule(rf"^add\s+({HANDLES})\s+to\s+(?:project\s+)?({PROJ})\s+as\s+(member|lead|viewer|owner)$")
def _project_member(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    proj = _project(m.group(2), ctx)
    if not proj:
        return None
    return [
        ("project.add_member", {"project": proj, "person": h, "role": m.group(3).lower()})
        for h in _handles(m.group(1), ctx)
    ]


@rule(r"^merge\s+tags?\s+#?(\S+)\s+into\s+#?(\S+)$")
def _tag_merge(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("tag.merge", {"sources": [m.group(1).lstrip("#")], "target": m.group(2).lstrip("#")})]


@rule(
    rf"^merge\s+({KEYS})(?:\s+(?:into|to|with)\s+({KEY}))?(?:\s+(?:as|titled|called)\s+(.+?))?(?:\s+into\s+one)?$"
)
def _item_merge(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    sources = _keys(m.group(1))
    target = m.group(2).upper() if m.group(2) else None
    title = _clean(m.group(3)) if m.group(3) else None
    if target:
        sources = [s for s in sources if s != target]
    if len(sources) + (1 if target else 0) < 2:
        return None
    args: dict[str, Any] = {"sources": sources}
    if title:
        args["new_title"] = title
    else:  # "merge A and B": keep the first, fold the rest into it
        args["target"] = target or sources[0]
    return [("item.merge", args)]


@rule(rf"^split\s+({KEY})\s+into\s+(.+)$")
def _item_split(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    titles = _split_titles(m.group(2))
    return [("item.split", {"key": m.group(1).upper(), "titles": titles})] if len(titles) >= 2 else None


@rule(r"^(?:un-?tag|remove\s+tags?)\s+(.+)$")
def _untag(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    keys, tags = _keys(m.group(1)), TAG_RE.findall(m.group(1))
    return [("item.tag", {"keys": keys, "remove": tags})] if keys and tags else None


@rule(r"^tag\s+(.+)$")
def _tag(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    keys, tags = _keys(m.group(1)), TAG_RE.findall(m.group(1))
    return [("item.tag", {"keys": keys, "add": tags})] if keys and tags else None


@rule(r"^(?:create|new|add)\s+tag\s+#?(\S+)$")
def _tag_create(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("tag.create", {"name": m.group(1).lstrip("#")})]


@rule(r"^add\s+value\s+(.+?)\s+to\s+dimension\s+(\S+)$")
def _dim_value(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("dimension.value.add", {"dimension": m.group(2), "value": _clean(m.group(1))})]


@rule(rf"^classify\s+({KEY})\s+(\S+)\s+(.+)$")
def _classify(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [
        ("classify", {"target": m.group(1).upper(), "dimension": m.group(2), "value": _clean(m.group(3))})
    ]


@rule(
    rf"^(?:create|new|make)\s+(?:an?\s+)?(virtual\s+|ad[- ]?hoc\s+|org(?:anisation|anization)?\s+)?team\s+(.+?)(?:\s+with\s+({HANDLES}))?(?:\s+(?:led\s+by|lead(?:er)?(?:\s+is)?|lead)\s+({HANDLE_OR_ME}))?$"
)
def _team_create(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    kind_raw = (m.group(1) or "virtual").strip().lower()
    kind = "ad_hoc" if kind_raw.startswith("ad") else "org" if kind_raw.startswith("org") else "virtual"
    args: dict[str, Any] = {"name": _clean(m.group(2)), "kind": kind}
    if m.group(3):
        args["members"] = _handles(m.group(3), ctx)
    if m.group(4):
        args["lead"] = _person(m.group(4), ctx)
    return [("team.create", args)]


@rule(rf"^add\s+({HANDLES})\s+to\s+team\s+(.+)$")
def _team_add(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("team.add_members", {"team": _clean(m.group(2)), "members": _handles(m.group(1), ctx)})]


@rule(rf"^remove\s+({HANDLES})\s+from\s+team\s+(.+)$")
def _team_remove(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("team.remove_members", {"team": _clean(m.group(2)), "members": _handles(m.group(1), ctx)})]


@rule(rf"^({HANDLE_OR_ME})\s+reports\s+to\s+({HANDLE_OR_ME})(?:\s+(solid|dotted|functional))?$")
def _reporting(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [
        (
            "reporting_line.set",
            {
                "person": _person(m.group(1), ctx),
                "manager": _person(m.group(2), ctx),
                "kind": (m.group(3) or "solid").lower(),
            },
        )
    ]


@rule(rf"^(?:move|put)\s+({KEYS})\s+under\s+({KEY})$")
def _reparent(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.reparent", {"keys": _keys(m.group(1)), "parent": m.group(2).upper()})]


@rule(
    rf"^(?:move|put)\s+({KEYS})\s+(?:to|into|in)\s+(?:stage\s+)?(.+?)(?:\s+(?:because|reason|due\s+to|since)\s*:?\s+(.+))?$"
)
def _move(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    keys, target = _keys(m.group(1)), _clean(m.group(2))
    iterations = set(ctx.pack.iteration_names) if ctx.pack else set()
    if re.match(r"(?i)(sprint|iteration)\b", target) or target in iterations:
        return [
            (
                "item.move_to_iteration",
                {
                    "keys": keys,
                    "iteration": _sprint("sprint", target) if target not in iterations else target,
                },
            )
        ]
    args: dict[str, Any] = {"keys": keys, "stage": target}
    if m.group(3):
        args["reason"] = _clean(m.group(3))
    return [("stage.move", args)]


@rule(
    r"^(approve|reject)\s+(?:approval\s+)?([A-Za-z]{1,6}-\d+)(?:\s*(?::|,|-|because|with\s+comment)\s*(.+))?$"
)
def _approval(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    args: dict[str, Any] = {"approval": m.group(2).upper(), "decision": m.group(1).lower()}
    if m.group(3):
        args["comment"] = _clean(m.group(3))
    return [("approval.decide", args)]


VID = r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)"


@rule(rf"^waive\s+violation\s+{VID}\s*(?::|\s+because|\s+reason:?|\s+-)\s*(.+)$")
def _waive(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("violation.waive", {"id": m.group(1).upper(), "reason": _clean(m.group(2))})]


@rule(rf"^waive\s+violation\s+{VID}$")
def _waive_noreason(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [
        ("violation.waive", {"id": m.group(1).upper()})
    ]  # reason is required: becomes `incomplete` -> the form asks


@rule(rf"^(ack(?:nowledge)?|resolve)\s+violation\s+{VID}(?:\s*(?::|\s+because|\s+note:?|\s+-)\s*(.+))?$")
def _violation(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    op = "violation.ack" if m.group(1).lower().startswith("ack") else "violation.resolve"
    args: dict[str, Any] = {"id": m.group(2).upper()}
    if m.group(3):
        args["note"] = _clean(m.group(3))
    return [(op, args)]


@rule(
    r"^resolve\s+(?:review\s+)?rows?\s+((?:[A-Za-z]{1,4}-\d+[\s,]*(?:and\s+)?)+?)(?:\s+(accept|reject|dismiss|keep))?$"
)
def _review_bulk(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    rows = [r.upper() for r in re.findall(r"[A-Za-z]{1,4}-\d+", m.group(1))]
    if len(rows) < 2:
        return None
    args: dict[str, Any] = {"rows": rows}
    if m.group(2):
        args["choice"] = m.group(2).lower()
    return [("review.bulk", args)]


@rule(r"^resolve\s+(?:review\s+)?(?:row\s+)?([A-Za-z]{1,4}-\d+)\s+(\S+)$")
def _review_resolve(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("review.resolve", {"row": m.group(1).upper(), "choice": m.group(2).lower()})]


@rule(rf"^assign\s+({KEYS})\s+to\s+({HANDLE_OR_ME})(?:\s+as\s+(owner|contributor|reviewer))?$")
def _assign(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    who, role = _person(m.group(2), ctx), (m.group(3) or "owner").lower()
    return [("item.assign", {"key": k, "person": who, "role": role}) for k in _keys(m.group(1))]


@rule(rf"^(?:set\s+)?priority\s+(?:of\s+)?({KEY})\s+(?:to\s+)?({PRIORITIES})$")
def _priority_a(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.set_priority", {"key": m.group(1).upper(), "priority": m.group(2).lower()})]


@rule(rf"^({KEY})\s+priority\s+({PRIORITIES})$")
def _priority_b(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.set_priority", {"key": m.group(1).upper(), "priority": m.group(2).lower()})]


@rule(rf"^({KEY})\s+impact\s+(low|medium|high)$")
def _impact(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.set_impact", {"key": m.group(1).upper(), "impact": m.group(2).lower()})]


@rule(rf"^({KEY})\s+weight\s+(\d{{1,3}})$")
def _weight(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.set_weight", {"key": m.group(1).upper(), "weight": int(m.group(2))})]


@rule(rf"^({KEY})\s+stage\s+(.+)$")
def _stage(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.set_stage", {"key": m.group(1).upper(), "stage": _clean(m.group(2))})]


@rule(rf"^({KEY})\s+(?:is|now)\s+(blocked|done|doing|todo|cancelled)$")
def _state(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.set_state", {"key": m.group(1).upper(), "state": m.group(2).lower()})]


@rule(rf"^({KEY})\s+due\s+({DATE})$")
def _due(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.set_dates", {"key": m.group(1).upper(), "end": m.group(2)})]


@rule(rf"^({KEY})\s+estimate\s+(\d+(?:\.\d+)?)\s*(hours?|days?|points?|pts?|h|d)$")
def _estimate(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    unit = {"h": "hours", "d": "days", "p": "points"}[m.group(3)[0].lower()]
    n = float(m.group(2))
    return [
        (
            "item.set_estimate",
            {"key": m.group(1).upper(), "value": int(n) if n.is_integer() else n, "unit": unit},
        )
    ]


@rule(rf"^({KEY})\s+(blocks|relates\s+to|duplicates|depends\s+on)\s+({KEY})$")
def _link(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    kind = {
        "blocks": "blocks",
        "relates to": "relates",
        "duplicates": "duplicates",
        "depends on": "depends_on",
    }[re.sub(r"\s+", " ", m.group(2).lower())]
    return [("item.link", {"from": m.group(1).upper(), "to": m.group(3).upper(), "type": kind})]


@rule(rf"^rename\s+({KEY})\s+to\s+(.+)$")
def _rename(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("item.update", {"key": m.group(1).upper(), "fields": {"title": _clean(m.group(2))}})]


@rule(rf"^(?:new|create)\s+(sprint|iteration)\s+(.+?)\s+on\s+({PROJ})(?:\s+({DATE})\s+(?:to|-)\s+({DATE}))?$")
def _iteration_create(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    proj = _project(m.group(3), ctx)
    if not proj:
        return None
    args: dict[str, Any] = {"name": _sprint(m.group(1), m.group(2)), "project": proj}
    if m.group(4):
        args["start"], args["end"] = m.group(4), m.group(5)
    return [("iteration.create", args)]


@rule(rf"^(start|close|finish)\s+(sprint|iteration)\s+(.+?)(?:\s+on\s+({PROJ}))?$")
def _iteration_state(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    op = "iteration.start" if m.group(1).lower() == "start" else "iteration.close"
    args: dict[str, Any] = {"iteration": _sprint(m.group(2), m.group(3))}
    if m.group(4):
        proj = _project(m.group(4), ctx)
        if not proj:
            return None
        args["project"] = proj
    return [(op, args)]


@rule(rf"^(?:new|create)\s+milestone\s+(.+?)\s+on\s+({PROJ})(?:\s+due\s+({DATE}))?$")
def _milestone_create(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    proj = _project(m.group(2), ctx)
    if not proj:
        return None
    args: dict[str, Any] = {"name": _clean(m.group(1)), "project": proj}
    if m.group(3):
        args["due"] = m.group(3)
    return [("milestone.create", args)]


@rule(r"^(?:complete|finish|close)\s+milestone\s+(.+)$")
def _milestone_complete(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("milestone.complete", {"milestone": _clean(m.group(1))})]


@rule(rf"^(?:new|open|add)\s+risk\s+(.+?)\s+on\s+({PROJ})(?:\s+({PRIORITIES.replace('lowest|', '')}))?$")
def _risk_create(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    proj = _project(m.group(2), ctx)
    if not proj:
        return None
    args: dict[str, Any] = {"title": _clean(m.group(1)), "project": proj}
    if m.group(3):
        args["severity"] = m.group(3).lower()
    return [("risk.create", args)]


@rule(rf"^link\s+risk\s+(\S+)\s+to\s+({KEYS})$")
def _risk_link(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("risk.link", {"risk": m.group(1).upper(), "keys": _keys(m.group(2))})]


@rule(r"^risk\s+(\S+)\s+action\s+(.+)$")
def _risk_action(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("risk.action.add", {"risk": m.group(1).upper(), "action": _clean(m.group(2))})]


@rule(r"^confirm\s+(?:my\s+)?(?:day|daily\s+log|log)$")
def _confirm_day(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("dailylog.confirm", {})]


@rule(r"^note\s+on\s+(\S+?)\s*:\s*(.+)$")
def _context_note(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [("context_note.add", {"target": m.group(1), "note": _clean(m.group(2))})]


@rule(rf"^coach\s+note\s+on\s+(\S+)(?:\s+for\s+({HANDLE_OR_ME}))?\s*:\s*(.+)$")
def _coach_note(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    args: dict[str, Any] = {"habit": m.group(1), "note": _clean(m.group(3))}
    if m.group(2):
        args["person"] = _person(m.group(2), ctx)
    return [("habit.coach_note", args)]


@rule(r"^(pause|retire|resume|activate)\s+habit\s+(\S+)$")
def _habit_status(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    status = {"pause": "paused", "retire": "retired", "resume": "active", "activate": "active"}[
        m.group(1).lower()
    ]
    return [("habit.set_status", {"habit": m.group(2), "status": status})]


@rule(r"^(activate|deactivate)\s+policy\s+(\S+)$")
def _policy(m: re.Match[str], ctx: CommandParseContext) -> Built | None:
    return [(f"policy.{m.group(1).lower()}", {"policy": m.group(2)})]


# ------------------------------------------------------------------ driver
_FOR_RE = re.compile(rf"\s+(?:for:?|on\s+behalf\s+of)\s+({HANDLE_OR_ME})\s*$", re.I)


def _segments(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[;\n]+", text) if s.strip()]


def parse_commands(text: str, ctx: CommandParseContext) -> CommandParse:
    out = CommandParse()
    for seg in _segments(text):
        line = re.sub(r"\s+", " ", seg).rstrip(".").strip()
        behalf: str | None = None
        fm = _FOR_RE.search(line)
        if (
            fm
            and not re.search(r"\bteam\b|\bviolation\b|\bhabit\b", line, re.I)
            and not re.match(r"(?i)coach\s+note", line)
        ):
            behalf = _person(fm.group(1), ctx)
            line = line[: fm.start()].strip()
        built: Built | None = None
        for pattern, handler in RULES:
            m = pattern.match(line)
            if m is None:
                continue
            built = handler(m, ctx)
            if built:
                break
        if not built:
            out.unmatched.append(seg)
            continue
        for op, args in built:
            args = {k: v for k, v in args.items() if v not in (None, "", [])}
            if ctx.catalogue is not None:
                if op not in ctx.catalogue:
                    out.notes.append(f"{op}: not offered by this server")
                    out.unmatched.append(seg)
                    continue
                issues = ctx.catalogue.validate(op, args)
                if issues:
                    out.incomplete.append(Incomplete(op, args, [str(i) for i in issues]))
                    out.notes.append(f"{op}: " + "; ".join(str(i) for i in issues))
                    continue
            out.commands.append(
                new_command(
                    op,
                    args,
                    actor=ctx.actor,
                    now=ctx.now,
                    mode=ctx.mode,
                    role_mode=ctx.role_mode,
                    on_behalf_of=behalf,
                )
            )
    return out
