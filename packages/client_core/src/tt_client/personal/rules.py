"""Rules and suggestions (Phase 15 §15.3, §15.2 "ready to send"): deterministic, testable, explainable.

A suggestion answers "which project does this meeting/item belong to, and should it be offered?" from, in order of strength:

1. recurring series memory (a series that was mapped once stays mapped)      0.95
2. a user rule, then a learned rule, that matches the item                   0.90 / 0.85 (+0.01 per hit, capped at 0.95)
3. attendee email domain  <->  client/product (ContextPack hint)             0.80
4. title keywords  <->  glossary / project aliases                           0.75 / 0.70
5. attendee set  <->  team  <->  the team's projects                         0.70 (one project) / 0.40 (several: a choice)
6. iteration ceremony words ("sprint planning", "retro") with one active project  0.60

The gateway may *rank* alternatives but never decides (handover note); nothing in this module calls a model. The score is shown as the
usual cue: `✓` >= 0.8, `suggested` >= 0.6, `ask` below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tt_client.personal.model import (
    CATEGORY_DEFAULT_SHARE,
    Category,
    PersonalItem,
    Rule,
    RuleMatch,
    RuleSet,
    SharePolicy,
)
from tt_client.personal.store import PersonalStore

CEREMONY = re.compile(
    r"\b(sprint planning|planning|retro(?:spective)?|sprint review|backlog (?:grooming|refinement)|stand-?up|demo)\b",
    re.I,
)


@dataclass
class Hints:
    """What the ContextPack lets a client reason with, without the server ever seeing a calendar."""

    projects: list[dict[str, Any]] = field(default_factory=list)  # {key,name,aliases}
    glossary: dict[str, str] = field(default_factory=dict)  # phrase -> "PROJECT:KEY"
    people: list[dict[str, Any]] = field(default_factory=list)  # {handle,name,team}
    teams: list[dict[str, Any]] = field(default_factory=list)  # {team, projects[]}
    client_domains: dict[str, str] = field(default_factory=dict)  # domain -> project key
    iterations: list[dict[str, Any]] = field(default_factory=list)  # {name,project,state}
    development_project: str = "DEV"
    capacity_hours_per_week: float | None = None
    my_email_domain: str | None = None

    @classmethod
    def from_pack(cls, pack: dict[str, Any] | None, *, my_email: str | None = None) -> Hints:
        if not pack:
            return cls(my_email_domain=_domain(my_email))
        hints = pack.get("calendar_mapping_hints") or {}
        return cls(
            projects=list(pack.get("projects", [])),
            glossary={str(k).lower(): str(v) for k, v in (pack.get("glossary") or {}).items()},
            people=list(pack.get("people", [])),
            teams=list(hints.get("teams", [])),
            client_domains={
                str(d["domain"]).lower(): str(d["project"]) for d in hints.get("client_domains", [])
            },
            iterations=list(pack.get("iterations", [])),
            development_project=str((pack.get("development_project") or {}).get("key") or "DEV"),
            capacity_hours_per_week=pack.get("my_capacity_hours_per_week"),
            my_email_domain=_domain(my_email),
        )

    @property
    def project_keys(self) -> list[str]:
        return [str(p["key"]) for p in self.projects]


def _domain(email: str | None) -> str | None:
    return email.rsplit("@", 1)[1].lower() if email and "@" in email else None


@dataclass
class Suggestion:
    project: str | None
    category: Category
    share_policy: SharePolicy
    confidence: float
    source: str | None
    reasons: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)  # up to 3 project keys for the chip row
    rule_id: str | None = None
    blocked: bool = False  # a rule says never offer this ("Not work")

    @property
    def cue(self) -> str:
        return "✓" if self.confidence >= 0.8 else "suggested" if self.confidence >= 0.6 else "ask"

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "category": self.category.value,
            "share_policy": self.share_policy.value,
            "confidence": round(self.confidence, 2),
            "cue": self.cue,
            "source": self.source,
            "reasons": self.reasons,
            "alternatives": self.alternatives,
            "rule_id": self.rule_id,
        }


@dataclass
class _Cand:
    project: str
    score: float
    source: str
    reason: str


def _emails(item: PersonalItem) -> list[str]:
    return [a.lower() for a in item.attendees if "@" in a]


def _series_key(item: PersonalItem) -> str | None:
    if item.calendar_ref and item.calendar_ref.series_id:
        return f"{item.calendar_ref.provider}:{item.calendar_ref.series_id}"
    return item.series_id


def rule_matches(rule: Rule, item: PersonalItem) -> bool:
    m: RuleMatch = rule.match
    if not any(
        getattr(m, f)
        for f in (
            "kind",
            "title_regex",
            "attendee_domain",
            "calendar_id",
            "keyword",
            "series_id",
            "attendees",
        )
    ):
        return False  # an empty match would hit everything
    if m.kind and item.kind != m.kind:
        return False
    if m.title_regex and not re.search(m.title_regex, item.title, re.I):
        return False
    if m.attendee_domain and m.attendee_domain.lower() not in {e.rsplit("@", 1)[1] for e in _emails(item)}:
        return False
    if m.calendar_id and not (item.calendar_ref and item.calendar_ref.calendar_id == m.calendar_id):
        return False
    if m.keyword and m.keyword.lower() not in item.title.lower():
        return False
    if m.series_id and m.series_id != _series_key(item):
        return False
    if m.attendees is not None and {a.lower() for a in m.attendees} != set(_emails(item)):
        return False
    return True


def _ceiling(rule: Rule) -> float:
    base = 0.9 if rule.origin == "user" else 0.85
    return min(0.95, base + 0.01 * rule.hits)


def suggest(item: PersonalItem, hints: Hints, rules: list[Rule]) -> Suggestion:
    category, share = item.category, item.share_policy
    cands: list[_Cand] = []
    used_rule: Rule | None = None
    cat_done = share_done = blocked = (
        False  # the strongest rule that sets a field wins; later (weaker) rules never override it
    )

    # 1 + 2: series memory and rules (rules decide category and share too)
    for rule in sorted(rules, key=lambda r: (r.origin != "user", -r.hits)):
        if not rule_matches(rule, item):
            continue
        used_rule = used_rule or rule
        if rule.set.category is not None and not cat_done:
            category, cat_done = rule.set.category, True
        if rule.set.share_policy is not None and not share_done:
            share, share_done = rule.set.share_policy, True
            blocked = share == SharePolicy.NEVER
        if rule.set.project_hint:
            is_series = bool(rule.match.series_id)
            cands.append(
                _Cand(
                    rule.set.project_hint,
                    0.95 if is_series else _ceiling(rule),
                    "series" if is_series else "rule",
                    "this recurring meeting was mapped before" if is_series else f"{rule.origin} rule",
                )
            )
    if item.project_hint:  # an explicit choice always wins
        cands.append(_Cand(item.project_hint, 1.0, "item", "chosen earlier"))

    # 3: attendee domain <-> client/product
    for dom in {e.rsplit("@", 1)[1] for e in _emails(item)}:
        if dom in hints.client_domains:
            cands.append(_Cand(hints.client_domains[dom], 0.80, "domain", f"attendees from {dom}"))

    # 4: title keywords <-> glossary, aliases
    low = item.title.lower()
    for phrase, target in hints.glossary.items():
        if phrase and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", low) and target.startswith("PROJECT:"):
            cands.append(_Cand(target.split(":", 1)[1], 0.75, "glossary", f"“{phrase}” is in the glossary"))
    for p in hints.projects:
        names = {
            str(p["key"]).lower(),
            str(p.get("name", "")).lower(),
            *(str(a).lower() for a in p.get("aliases", [])),
        }
        for n in names:
            if n and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", low):
                cands.append(_Cand(str(p["key"]), 0.70, "alias", f"“{n}” names {p['key']}"))
                break

    # 5: attendee set <-> team <-> the team's projects
    team_hits = _attendee_teams(item, hints)
    if team_hits:
        team, attendee_count = team_hits[0]
        projects: list[str] = next((t["projects"] for t in hints.teams if t["team"] == team), [])
        if len(projects) == 1:
            cands.append(_Cand(projects[0], 0.70, "team", f"{attendee_count} attendee(s) are on team {team}"))
        elif projects:
            for k in projects:
                cands.append(_Cand(k, 0.40, "team", f"team {team} has several projects"))

    # 6: ceremony words with exactly one active iteration
    if CEREMONY.search(item.title):
        active = {str(i["project"]) for i in hints.iterations if i.get("state", "active") != "closed"}
        if len(active) == 1:
            cands.append(
                _Cand(next(iter(active)), 0.60, "ceremony", "iteration ceremony of the active project")
            )

    sug = _decide(cands, category, share, used_rule)
    sug.blocked = blocked
    return sug


def _attendee_teams(item: PersonalItem, hints: Hints) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for email in _emails(item):
        local = email.split("@", 1)[0].lower()
        for person in hints.people:
            handle = str(person.get("handle", "")).lstrip("@").lower()
            name = str(person.get("name", "")).lower().replace(" ", ".")
            if local in (handle, name) and person.get("team"):
                counts[str(person["team"])] = counts.get(str(person["team"]), 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _decide(cands: list[_Cand], category: Category, share: SharePolicy, rule: Rule | None) -> Suggestion:
    if not cands:
        return Suggestion(
            None,
            category,
            share,
            0.0,
            None,
            ["no hint matched: pick a project"],
            rule_id=rule.id if rule else None,
        )
    best_per_project: dict[str, _Cand] = {}
    for c in cands:
        cur = best_per_project.get(c.project)
        if cur is None or c.score > cur.score:
            best_per_project[c.project] = c
    ranked = sorted(best_per_project.values(), key=lambda c: (-c.score, c.project))
    top = ranked[0]
    alternatives = [c.project for c in ranked[:3]]
    reasons = [c.reason for c in cands if c.project == top.project]
    confidence = top.score
    if (
        len(ranked) > 1 and ranked[1].score >= top.score - 0.1
    ):  # two projects almost tie: say so, let the person choose
        confidence = min(top.score, 0.55)
        reasons.append(f"also plausible: {ranked[1].project}")
    return Suggestion(
        top.project, category, share, confidence, top.source, reasons, alternatives, rule.id if rule else None
    )


# ------------------------------------------------------------------ learning
def learn_mapping(store: PersonalStore, item: PersonalItem, project: str) -> Rule:
    """An accepted suggestion becomes a learned rule: the series if there is one, else the attendee set, else the exact title."""
    key = _series_key(item)
    if key:
        match = RuleMatch(series_id=key)
    elif _emails(item):
        match = RuleMatch(attendees=sorted(_emails(item)))
    else:
        match = RuleMatch(title_regex="^" + re.escape(item.title.strip()) + "$")
    return _upsert(store, match, RuleSet(project_hint=project, category=item.category), origin="learned")


def learn_not_work(store: PersonalStore, item: PersonalItem) -> Rule:
    """ "Not work": remember that this series (or title) is personal and never to be offered again."""
    key = _series_key(item)
    match = (
        RuleMatch(series_id=key) if key else RuleMatch(title_regex="^" + re.escape(item.title.strip()) + "$")
    )
    return _upsert(
        store, match, RuleSet(category=Category.PERSONAL, share_policy=SharePolicy.NEVER), origin="learned"
    )


def _upsert(store: PersonalStore, match: RuleMatch, rset: RuleSet, *, origin: str) -> Rule:
    for r in store.rules():
        if r.match == match and r.origin == origin:
            r.set = rset
            r.hits += 1
            return store.put_rule(r)
    return store.put_rule(Rule(match=match, set=rset, origin=origin))


def category_default(category: Category) -> SharePolicy:
    return CATEGORY_DEFAULT_SHARE[category]
