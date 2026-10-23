"""Deterministic parser for the syntax forms of UX §2.2/§2.3 (works with no model).

Grammar (all pieces optional, order-free, separated by `,` `|` or newline):
  verb at the start        doing/on/started/working on -> start, done/finished/shipped -> done, pause/parking,
                           resume/back on, blocked/stuck, note:, new task:/add bug:/create, switching to
  tokens                   #tag  @person  p:web / project:web  !high / prio:high  for: @sara  KEY-123  URLs
  time / plan / estimate   took 2h, spent 45 min | plan tomorrow 5pm, eta friday | est 3d, about 5 points
  numbered lists           "doing 1. fix login, 2. write tests" -> one message per element
Rule for lists: time / plan / estimate / evidence given once apply to the **first** element unless the
elements carry their own. Everything left over is the item title.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tt_schema.base import new_id
from tt_schema.client import ClientAction, ClientMessage, ClientTaskRef
from tt_schema.enums import ItemTypeKey, Priority

from tt_client.timeparse import parse_duration, parse_when


@dataclass
class ParseContext:
    actor: str
    now: datetime
    tz: str = "UTC"
    hours_per_day: int = 8
    default_action: ClientAction = ClientAction.NOTE


@dataclass
class ParseResult:
    messages: list[ClientMessage]
    notes: list[str] = field(default_factory=list)


_URL = re.compile(r"https?://[^\s,|]+")
_KEY = re.compile(r"(?<![\w-])([A-Z][A-Z0-9]+-\d+)(?![\w-])")
_TAG = re.compile(r"(?<![\w&])#([A-Za-z][\w-]*)")
_FOR = re.compile(r"(?<!\w)for:?\s+(@[\w.-]+)", re.I)
_PERSON = re.compile(r"(?<![\w.])@([A-Za-z][\w.-]*)")
_PROJECT = re.compile(r"(?<!\S)(?:p|project):(\S+)", re.I)
_PRIORITY = re.compile(r"(?<!\S)(?:!|(?:prio|priority)[:=])(\w+)", re.I)
_NUMBERED = re.compile(r"(?:^|(?<=\s))\d+[.)]\s+")

_TYPES = "|".join(t.value for t in ItemTypeKey)
_VERBS: list[tuple[re.Pattern[str], ClientAction]] = [
    (re.compile(r"^(?:new|add|create)\s+(?P<type>" + _TYPES + r")\s*:?\s*", re.I), ClientAction.CREATE),
    (re.compile(r"^create\s*:?\s*", re.I), ClientAction.CREATE),
    (re.compile(r"^note\s*:\s*", re.I), ClientAction.NOTE),
    (re.compile(r"^(?:blocked|stuck)\s*(?:on|by)?\s*:?\s*", re.I), ClientAction.BLOCKED),
    (
        re.compile(r"^(?:done|finished|shipped|closed|completed)\s+(?:with\s+|up\s+)?", re.I),
        ClientAction.DONE,
    ),
    (re.compile(r"^(?:done|finished|shipped|closed|completed)\s*:?\s*$", re.I), ClientAction.DONE),
    (re.compile(r"^(?:resume|resuming|back\s+on)\s*:?\s*", re.I), ClientAction.RESUME),
    (re.compile(r"^(?:pause|pausing|parking|park)\s*:?\s*", re.I), ClientAction.PAUSE),
    (
        re.compile(
            r"^(?:switch(?:ing)?\s+to|picking\s+up|working\s+on|doing|started|starting|start|on)(?:\s*:\s*|\s+|$)",
            re.I,
        ),
        ClientAction.START,
    ),
]
_PLAN_PREFIX = re.compile(
    r"^(?:plan(?:ned)?(?:\s+to\s+(?:finish|end|be\s+done|complete))?(?:\s+(?:by|on|for))?|eta|by|"
    r"finish(?:ing)?(?:\s+by)?|should\s+be\s+(?:done|finished)(?:\s+by)?|done\s+by|until|due|ends?)\s*:?\s*(?P<when>.+)$",
    re.I,
)
_EST_PREFIX = re.compile(r"^(?:est(?:imate)?|probably|about|around|roughly|~)\s*:?\s*(?P<amount>.+)$", re.I)
_EST_AMOUNT = re.compile(
    r"^(?P<n>\d+(?:\.\d+)?)\s*(?P<u>days?|d|hours?|hrs?|h|points?|pts?|sp|story\s+points?)\b", re.I
)
_TOOK = re.compile(
    r"(?:^|\b)(?:took|spent|logged|time)\s*:?\s*(?P<dur>\d[\w .]*?)(?=$|\s+(?:on|for|in|by|to)\b|[,|])", re.I
)
_BLOCK_SPLIT = re.compile(r"\s+(?:waiting\s+(?:for|on)|because|blocked\s+by|due\s+to)\b|\s+[—–-]\s+", re.I)


def _classify_url(url: str) -> dict[str, Any]:
    kind = "link"
    if re.search(r"github\.com/[^/]+/[^/]+/pull/\d+", url):
        kind = "pr"
    elif re.search(r"/commit/[0-9a-f]{7,40}", url):
        kind = "commit"
    return {"kind": kind, "url": url}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t,;|.:-")


def _estimate(amount: str) -> dict[str, Any] | None:
    mt = _EST_AMOUNT.match(amount.strip())
    if not mt:
        return None
    n = float(mt.group("n"))
    u = mt.group("u").lower()
    unit = "days" if u.startswith("d") else "hours" if u.startswith("h") else "points"
    return {"value": int(n) if n.is_integer() else n, "unit": unit}


@dataclass
class _Piece:
    """What one list element / clause contributed."""

    title: str = ""
    key: str | None = None
    minutes: int | None = None
    planned_end: datetime | None = None
    estimate: dict[str, Any] | None = None


class StructuredParser:
    def __init__(self, ctx: ParseContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ public

    def parse(self, line: str) -> ParseResult:
        raw = line.strip()
        if not raw:
            return ParseResult([])
        notes: list[str] = []
        text = raw

        evidence = []
        for url in _URL.findall(text):
            evidence.append(_classify_url(url.rstrip(".)")))
        text = _URL.sub(" ", text)

        on_behalf = None
        mt = _FOR.search(text)
        if mt:
            on_behalf = mt.group(1)
            text = _FOR.sub(" ", text)
        project = None
        mt = _PROJECT.search(text)
        if mt:
            project = mt.group(1).strip(",;")
            text = _PROJECT.sub(" ", text)
        priority = None
        mt = _PRIORITY.search(text)
        if mt:
            try:
                priority = Priority(mt.group(1).lower())
                text = _PRIORITY.sub(" ", text)
            except ValueError:
                notes.append(f"unknown priority {mt.group(1)!r}")
        tags = list(dict.fromkeys(t for t in _TAG.findall(text)))
        text = _TAG.sub(" ", text)
        people = list(dict.fromkeys("@" + p for p in _PERSON.findall(text)))
        text = _PERSON.sub(" ", text)

        clauses = [c for c in re.split(r"\||\n", text) if c.strip()]
        action, item_type, first_clause = self._verb(clauses)
        if first_clause is not None:
            clauses[0] = first_clause

        titles: list[str] = []
        global_piece = _Piece()
        list_elems: list[_Piece] = []
        for clause in clauses:
            if _NUMBERED.search(clause):
                head, *_ = _NUMBERED.split(clause, maxsplit=1)
                if _clean(head):
                    titles.append(_clean(head))
                for elem in re.split(r"(?:^|(?<=\s))\d+[.)]\s+", clause)[1:]:
                    list_elems.append(self._piece(elem, trailing_commas=False))
                continue
            for seg in [s for s in re.split(r"[,;]", clause) if s.strip()]:
                p = self._piece(seg, trailing_commas=True)
                if p.title:
                    titles.append(p.title)
                global_piece.key = global_piece.key or p.key
                global_piece.minutes = global_piece.minutes or p.minutes
                global_piece.planned_end = global_piece.planned_end or p.planned_end
                global_piece.estimate = global_piece.estimate or p.estimate

        elements = list_elems or [_Piece(title=", ".join(t for t in titles if t))]
        if list_elems and titles:
            notes.append(f"ignored leading text: {', '.join(titles)}")
        if global_piece.key and elements[0].key is None:
            elements[0].key = global_piece.key
        for field_name in ("minutes", "planned_end", "estimate"):
            g = getattr(global_piece, field_name)
            if g is not None and len(elements) > 1:
                notes.append(f"{field_name} applied to the first item")
            if g is not None and getattr(elements[0], field_name) is None:
                setattr(elements[0], field_name, g)

        messages = []
        for i, el in enumerate(elements):
            act = action or self._infer(el, evidence if i == 0 else [])
            title = el.title or None
            note = None
            if act == ClientAction.BLOCKED and title:
                note = title
                parts = _BLOCK_SPLIT.split(title, maxsplit=1)
                title = _clean(parts[0]) or None
            elif act == ClientAction.NOTE:
                note, title = title, None
            ref = ClientTaskRef(key=el.key, title=title) if el.key or title else None
            assignees = [
                {"person": p, "role": "owner" if act == ClientAction.CREATE else "contributor"}
                for p in people
            ]
            messages.append(
                ClientMessage(
                    actor=self.ctx.actor,
                    action=act,
                    at=self.ctx.now,
                    client_id=new_id(),
                    item=ref,
                    project=project,
                    tags=tags,
                    note=note,
                    minutes=el.minutes,
                    planned_end=el.planned_end,
                    estimate=el.estimate,
                    evidence=(evidence or None) if i == 0 else None,
                    on_behalf_of=on_behalf,
                    item_type=item_type,
                    priority=priority,
                    assignees=assignees,
                    raw_input=raw,
                )
            )
        return ParseResult(messages, notes)

    # ------------------------------------------------------------------ helpers

    def _verb(self, clauses: list[str]) -> tuple[ClientAction | None, ItemTypeKey | None, str | None]:
        if not clauses:
            return None, None, None
        first = clauses[0].strip()
        for pattern, action in _VERBS:
            mt = pattern.match(first)
            if mt:
                item_type = None
                if action == ClientAction.CREATE:
                    try:
                        item_type = ItemTypeKey(mt.groupdict().get("type", "").lower() or "task")
                    except ValueError:
                        item_type = ItemTypeKey.TASK
                rest = first[mt.end() :]
                rest = re.sub(r"\s+now$", "", rest, flags=re.I) if action == ClientAction.START else rest
                return action, item_type, rest
        return None, None, None

    def _piece(self, segment: str, *, trailing_commas: bool) -> _Piece:
        ctx = self.ctx
        seg = segment.strip()
        piece = _Piece()
        mk = _KEY.search(seg)
        if mk:
            piece.key = mk.group(1)
            seg = _KEY.sub(" ", seg, count=1).strip()
        # plan / estimate segments (whole segment)
        mt = _PLAN_PREFIX.match(seg)
        if mt:
            when = parse_when(mt.group("when"), ctx.now, ctx.tz)
            if when:
                piece.planned_end = when
                return piece
        mt = _EST_PREFIX.match(seg)
        if mt:
            est = _estimate(mt.group("amount"))
            if est:
                piece.estimate = est
                return piece
        # bare duration segment ("2h")
        bare = parse_duration(seg, ctx.hours_per_day)
        if bare is not None and re.fullmatch(
            r"[\d.\s]*(?:d|days?|h|hrs?|hours?|m|mins?|minutes?)(?:\s*\d.*)?", seg, re.I
        ):
            piece.minutes = bare
            return piece
        # inline "took 2h" / "spent 45 min"
        mt = _TOOK.search(seg)
        if mt:
            dur = parse_duration(mt.group("dur"), ctx.hours_per_day)
            if dur is not None:
                piece.minutes = dur
                seg = (seg[: mt.start()] + " " + seg[mt.end() :]).strip()
                seg = re.sub(r"^(?:on|for|in)\s+", "", seg, flags=re.I)
        # inline "(plan friday)" / "est 3d" at the end of an element
        tail = re.search(r"\s+(?:plan|eta|est(?:imate)?)\s*:?\s*([^,;|]+)$", seg, re.I)
        if tail:
            word = tail.group(0).split()[0].lower()
            if word.startswith("est"):
                est = _estimate(tail.group(1))
                if est:
                    piece.estimate, seg = est, seg[: tail.start()]
            else:
                when = parse_when(tail.group(1), ctx.now, ctx.tz)
                if when:
                    piece.planned_end, seg = when, seg[: tail.start()]
        piece.title = _clean(seg)
        return piece

    def _infer(self, el: _Piece, evidence: list[dict[str, Any]]) -> ClientAction:
        if el.minutes is not None:
            return ClientAction.LOG_TIME
        if el.planned_end is not None:
            return ClientAction.PLAN
        if el.estimate is not None:
            return ClientAction.ESTIMATE
        if evidence and not el.title:
            return ClientAction.EVIDENCE
        return self.ctx.default_action
