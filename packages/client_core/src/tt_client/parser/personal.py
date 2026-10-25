"""Quick-add for the personal tracker (Phase 15 §15.2): one line -> a `Draft` (item and, for recurring phrases, an RRULE preview).

Deterministic like the rest of the parser (ADR-0003): the model may only suggest a category or a title; every time, duration, date and
recurrence below comes from these rules. Nothing here sends anything.

Grammar (all pieces optional, order-free):
  label        `course:`, `reading:`, `training:`, `work:`, `meeting:`, `sport:`, `errand:`, `habit:`, `focus:`, `personal:`, `task:`
  time         `9-10`, `9:30-10:45`, `9am-10am`, `at 6pm`, `6pm`, `18:00`, `7:00`   (a bare time means today)
  duration     `30m`, `1h`, `1h30m`, `90 min`, `for 45 minutes`
  date         `today`, `tomorrow`, `tonight`, `friday`, `next monday`, `oct 28`, `28 oct`, `2026-11-02`, `in 3 days`
  recurrence   `every weekday`, `every day`, `every 2 weeks on friday`, `Tue/Thu`, `mon wed fri`, `daily`, `weekly`, `monthly`, `weekdays`,
               `weekends`, with `until <date>`, `for 4 weeks`, `x10`
  tokens       `#tag` (`#development`, `#work`, `#personal`, `#meeting` also set the category), `p:WEB`, `@person`
Without a date or time the item is planned for today, "anytime" (`all_day`). A line may hold several items separated by `;`.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Literal

from tt_client.parser.structured import ParseContext
from tt_client.personal.model import CATEGORY_DEFAULT_SHARE, KIND_CATEGORY, Category, Draft, Kind
from tt_client.personal.recurrence import describe, occurrence_dates, resolve_local, zone
from tt_client.timeparse import parse_duration

__all__ = ["parse_personal", "parse_personal_line"]

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_ICAL = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_DAY_RE = r"(?:(?:mon(?:day)?s?|tue(?:s(?:day)?)?s?|wed(?:nesday)?s?|thu(?:r(?:s(?:day)?)?)?s?|fri(?:day)?s?|sat(?:urday)?s?|sun(?:day)?s?)\b)"
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_RE = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

_LABELS: dict[str, Kind] = {
    "work": Kind.TASK,
    "task": Kind.TASK,
    "meeting": Kind.MEETING,
    "focus": Kind.FOCUS,
    "course": Kind.COURSE,
    "training": Kind.TRAINING,
    "learning": Kind.TRAINING,
    "reading": Kind.READING,
    "read": Kind.READING,
    "sport": Kind.SPORT,
    "exercise": Kind.SPORT,
    "errand": Kind.ERRAND,
    "habit": Kind.HABIT,
    "personal": Kind.OTHER,
    "other": Kind.OTHER,
}
_TAG_CATEGORY = {
    "development": Category.DEVELOPMENT,
    "work": Category.WORK,
    "personal": Category.PERSONAL,
    "meeting": Category.MEETING,
}
_KEYWORDS: list[tuple[re.Pattern[str], Kind]] = [
    (
        re.compile(
            r"\b(?:stand-?up|sync|1:1|one-on-one|retro(?:spective)?|sprint planning|planning|call|meeting|interview|demo|review meeting|catch-?up|all-hands)\b",
            re.I,
        ),
        Kind.MEETING,
    ),
    (re.compile(r"\b(?:deep work|focus(?: time| block)?|heads-?down)\b", re.I), Kind.FOCUS),
    (re.compile(r"\b(?:course|class|lesson|lecture|tutorial|udemy|coursera|bootcamp)\b", re.I), Kind.COURSE),
    (
        re.compile(r"\b(?:training|workshop|certification|study|studying|practice|kata|exam)\b", re.I),
        Kind.TRAINING,
    ),
    (re.compile(r"\b(?:read|reading|book|article|paper|chapter)\b", re.I), Kind.READING),
    (
        re.compile(
            r"\b(?:run|running|gym|swim|swimming|yoga|workout|walk|cycling|bike|football|tennis|hike|pilates|climbing|jog|jogging|lift|lifting)\b",
            re.I,
        ),
        Kind.SPORT,
    ),
    (
        re.compile(r"\b(?:meditate|meditation|journal|journaling|stretch|stretching|floss|water)\b", re.I),
        Kind.HABIT,
    ),
    (
        re.compile(
            r"\b(?:prs?|pull requests?|deploy(?:ment)?|bugs?|tickets?|jira|code|coding|refactor|fix|debug|release|ship|reports?|docs?|documentation|design|spec|emails?|invoices?|clients?|sprint|backlog|roadmap|review|proposal)\b",
            re.I,
        ),
        Kind.TASK,
    ),
    (re.compile(r"\b(?:lunch|dinner|breakfast|nap|movie|family)\b", re.I), Kind.OTHER),
    (
        re.compile(
            r"\b(?:buy|groceries|errand|pick up|pay|bank|dentist|doctor|haircut|laundry|clean)\b", re.I
        ),
        Kind.ERRAND,
    ),
]

_URL = re.compile(r"https?://\S+")
_TAG = re.compile(r"(?<![\w&])#([A-Za-z][\w-]*)")
_PROJECT = re.compile(r"(?<!\S)(?:p|project):(\S+)", re.I)
_LABEL = re.compile(r"^\s*(" + "|".join(_LABELS) + r")\s*:\s*", re.I)
_DUR = re.compile(
    r"(?<![\w.:])(?:for\s+)?((?:\d+(?:\.\d+)?\s*(?:hours?|hrs?|h|minutes?|mins?|m)(?![a-zA-Z])\s*(?:and\s+)?)+)",
    re.I,
)
_RANGE = re.compile(
    r"(?<![\w.:/])(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?![\w:/]|\.\d)",
    re.I,
)
_AT = re.compile(
    r"(?<![\w.:/])(?:at\s+)?(\d{1,2})(?::(\d{2}))\s*(am|pm)?(?![\w:/])|(?<![\w.:/])(?:at\s+)?(\d{1,2})\s*(am|pm)(?!\w)|(?<![\w.:/])at\s+(\d{1,2})(?![\w:/.])",
    re.I,
)
_WORD_TIME = re.compile(r"\b(noon|midnight)\b", re.I)
_IN_DAYS = re.compile(r"\bin\s+(\d+)\s+(days?|weeks?)\b", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MD = re.compile(
    rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b|\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\b",
    re.I,
)
_REL_DAY = re.compile(r"\b(today|tomorrow|tonight|yesterday)\b", re.I)
_NEXT_DAY = re.compile(
    rf"\b(?:next|on)\s+({_DAY_RE})\b|\b({_DAY_RE})\b(?!\s*(?:/|,|&|and)\s*{_DAY_RE})", re.I
)

# --- recurrence
_EVERY_N = re.compile(
    rf"\bevery\s+(\d+)\s+(days?|weeks?|months?)(?:\s+on\s+({_DAY_RE}(?:\s*(?:,|/|&|and)\s*{_DAY_RE})*))?",
    re.I,
)
_EVERY_OTHER = re.compile(rf"\bevery\s+other\s+(day|week|month|{_DAY_RE})\b", re.I)
_EVERY_DAYS = re.compile(rf"\bevery\s+({_DAY_RE}(?:\s*(?:,|/|&|and)?\s*{_DAY_RE})*)\b", re.I)
_EVERY_UNIT = re.compile(r"\bevery\s+(weekday|weekend|day|week|month|year)s?\b", re.I)
_DAILY = re.compile(r"\b(daily|weekly|monthly|yearly|annually|weekdays|weekends)\b", re.I)
_DAY_LIST = re.compile(
    rf"(?<![\w-])({_DAY_RE}(?:\s*(?:,|/|&|and|\s)\s*{_DAY_RE})+)(?![\w-])|(?<![\w-])({_DAY_RE})\s*(?=/)", re.I
)
_UNTIL = re.compile(r"\buntil\s+(.+?)(?=$|\s+(?:at|for)\b|[,;])", re.I)
_FOR_SPAN = re.compile(r"\bfor\s+(\d+)\s+(days?|weeks?|months?)\b", re.I)
_TIMES = re.compile(r"(?:\bx\s*(\d+)\b|\b(\d+)\s+times\b)", re.I)


def _day_codes(text: str) -> list[str]:
    found = re.findall(_DAY_RE, text.lower())
    codes: list[str] = []
    for f in found:
        c = _ICAL[_DAYS.index(f[:3])]
        if c not in codes:
            codes.append(c)
    return sorted(codes, key=_ICAL.index)


class _Line:
    def __init__(self, raw: str, ctx: ParseContext) -> None:
        self.raw, self.ctx = raw, ctx
        self.tz = zone(ctx.tz)
        self.now = ctx.now.astimezone(self.tz)
        self.text = raw
        self.warnings: list[str] = []
        self.kind: Kind | None = None
        self.category: Category | None = None
        self.tags: list[str] = []
        self.project: str | None = None
        self.d: date | None = None
        self.t_start: tuple[int, int] | None = None
        self.t_end: tuple[int, int] | None = None
        self.duration: int | None = None
        self.freq: str | None = None
        self.interval = 1
        self.byday: list[str] = []
        self.until: date | None = None
        self.count: int | None = None
        self.span: tuple[int, str] | None = None
        self.kind_source: Literal["label", "keyword", "default"] = "default"
        self.category_source: Literal["tag", "kind"] = "kind"

    # --- small helpers
    def cut(self, m: re.Match[str]) -> None:
        self.text = (self.text[: m.start()] + " " + self.text[m.end() :]).strip()

    def cut_all(self, pattern: re.Pattern[str]) -> list[re.Match[str]]:
        found = list(pattern.finditer(self.text))
        for m in reversed(found):
            self.text = self.text[: m.start()] + " " + self.text[m.end() :]
        return found

    # --- steps
    def tokens(self) -> None:
        self.text = self.text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
        urls = _URL.findall(self.text)
        self.text = _URL.sub(" ", self.text)
        if urls:
            self.urls = urls
        mt = _LABEL.match(self.text)
        if mt:
            self.kind = _LABELS[mt.group(1).lower()]
            self.text = self.text[mt.end() :]
        for m in _TAG.finditer(self.text):
            tag = m.group(1)
            if tag.lower() in _TAG_CATEGORY:
                self.category = _TAG_CATEGORY[tag.lower()]
            self.tags.append(tag.lower())
        self.text = _TAG.sub(" ", self.text)
        mt = _PROJECT.search(self.text)
        if mt:
            self.project = mt.group(1).strip(",;").upper()
            self.cut(mt)

    def iso_date(self) -> None:
        """An ISO date first, so `2026-11-02` is never read as the time range 11-02."""
        if m := _ISO.search(self.text):
            try:
                self.d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                self.cut(m)
            except ValueError:
                self.warnings.append("ignored an impossible date")

    def recurrence(self) -> None:
        t = self.text
        if m := _EVERY_N.search(t):
            n, unit, days = int(m.group(1)), m.group(2).lower(), m.group(3)
            self.interval = n
            self.freq = (
                "DAILY" if unit.startswith("day") else "WEEKLY" if unit.startswith("week") else "MONTHLY"
            )
            if days:
                self.byday = _day_codes(days)
            self.cut(m)
        elif m := _EVERY_OTHER.search(t):
            unit = m.group(1).lower()
            self.interval = 2
            if unit[:3] in _DAYS:
                self.freq, self.byday = "WEEKLY", _day_codes(unit)
            else:
                self.freq = "DAILY" if unit == "day" else "WEEKLY" if unit == "week" else "MONTHLY"
            self.cut(m)
        elif m := _EVERY_DAYS.search(t):
            self.freq, self.byday = "WEEKLY", _day_codes(m.group(1))
            self.cut(m)
        elif m := _EVERY_UNIT.search(t):
            unit = m.group(1).lower()
            if unit == "weekday":
                self.freq, self.byday = "WEEKLY", _ICAL[:5]
            elif unit == "weekend":
                self.freq, self.byday = "WEEKLY", _ICAL[5:]
            else:
                self.freq = {"day": "DAILY", "week": "WEEKLY", "month": "MONTHLY", "year": "YEARLY"}[unit]
            self.cut(m)
        elif m := _DAILY.search(t):
            word = m.group(1).lower()
            if word == "weekdays":
                self.freq, self.byday = "WEEKLY", _ICAL[:5]
            elif word == "weekends":
                self.freq, self.byday = "WEEKLY", _ICAL[5:]
            else:
                self.freq = {
                    "daily": "DAILY",
                    "weekly": "WEEKLY",
                    "monthly": "MONTHLY",
                    "yearly": "YEARLY",
                    "annually": "YEARLY",
                }[word]
            self.cut(m)
        elif m := _DAY_LIST.search(t):
            self.freq, self.byday = "WEEKLY", _day_codes(m.group(0))
            if len(self.byday) < 2 and not re.search(r"/", m.group(0)):
                self.freq, self.byday = None, []
            else:
                self.cut(m)
        if self.freq is None:
            return
        if m := _UNTIL.search(self.text):
            self.until = self._date_from(m.group(1))
            if self.until is None:
                self.warnings.append(f"could not read the end date {m.group(1)!r}")
            self.cut(m)
        elif m := _FOR_SPAN.search(self.text):
            self.span = (int(m.group(1)), m.group(2).lower())
            self.cut(m)
        if m := _TIMES.search(self.text):
            self.count = int(m.group(1) or m.group(2))
            self.cut(m)

    def _date_from(self, text: str) -> date | None:
        saved = (self.text, self.d)
        self.text, self.d = text, None
        self.date_only()
        found = self.d
        self.text, self.d = saved
        return found

    def times(self) -> None:
        m = _RANGE.search(self.text)
        if m:
            h1, m1, ap1, h2, m2, ap2 = m.groups()
            bare = ap1 is None and ap2 is None and m1 is None and m2 is None
            if not (bare and (int(h1) > 24 or int(h2) > 24)) and int(h1) <= 24 and int(h2) <= 24:
                start, end = self._hm(int(h1), m1, ap1 or ap2), self._hm(int(h2), m2, ap2 or ap1)
                if (
                    ap2
                    and not ap1
                    and end[0] * 60 + end[1] <= start[0] * 60 + start[1]
                    and int(h1) < 12
                    and start[0] >= 12
                ):
                    start = (start[0] - 12, start[1])
                if end > start or (end[0] == 0 and not bare):
                    self.t_start, self.t_end = start, end
                    self.cut(m)
                    return
        m = _AT.search(self.text)
        if m:
            if m.group(1) is not None:
                self.t_start = self._hm(int(m.group(1)), m.group(2), m.group(3))
            elif m.group(4) is not None:
                self.t_start = self._hm(int(m.group(4)), None, m.group(5))
            else:
                self.t_start = self._hm(int(m.group(6)), None, None)
            if self.t_start[0] > 23 or self.t_start[1] > 59:
                self.t_start = None
                self.warnings.append("ignored an impossible time")
            else:
                self.cut(m)
                return
        m = _WORD_TIME.search(self.text)
        if m:
            self.t_start = (12, 0) if m.group(1).lower() == "noon" else (0, 0)
            self.cut(m)

    @staticmethod
    def _hm(hour: int, minute: str | None, meridiem: str | None) -> tuple[int, int]:
        if meridiem:
            hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
        return hour % 24 if hour == 24 else hour, int(minute) if minute else 0

    def duration_part(self) -> None:
        m = _DUR.search(self.text)
        if m:
            minutes = parse_duration(re.sub(r"\band\b", " ", m.group(1)), self.ctx.hours_per_day)
            if minutes:
                self.duration = minutes
                self.cut(m)

    def date_only(self) -> None:
        t = self.text
        if m := _ISO.search(t):
            try:
                self.d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                self.cut(m)
                return
            except ValueError:
                self.warnings.append("ignored an impossible date")
        if m := _IN_DAYS.search(t):
            n = int(m.group(1)) * (7 if m.group(2).lower().startswith("week") else 1)
            self.d = self.now.date() + timedelta(days=n)
            self.cut(m)
            return
        if m := _REL_DAY.search(t):
            word = m.group(1).lower()
            self.d = self.now.date() + timedelta(
                days={"today": 0, "tonight": 0, "tomorrow": 1, "yesterday": -1}[word]
            )
            if word == "tonight" and self.t_start is None:
                self.t_start = (20, 0)
            self.cut(m)
            return
        if m := _MD.search(t):
            mon, day = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
            month = _MONTHS.index(mon.lower()[:3]) + 1
            year = self.now.year
            try:
                cand = date(year, month, int(day))
                if cand < self.now.date():
                    cand = date(year + 1, month, int(day))
                self.d = cand
                self.cut(m)
                return
            except ValueError:
                self.warnings.append("ignored an impossible date")
        if m := _NEXT_DAY.search(t):
            name = (m.group(1) or m.group(2)).lower()
            target = _DAYS.index(name[:3])
            ahead = (target - self.now.weekday()) % 7
            if m.group(1) and m.group(0).lower().startswith("next") and ahead == 0:
                ahead = 7
            self.d = self.now.date() + timedelta(days=ahead)
            self.cut(m)

    def classify(self, title: str) -> None:
        self.kind_source = "label" if self.kind is not None else "default"
        self.category_source = "tag" if self.category is not None else "kind"
        if self.kind is None:
            for pattern, kind in _KEYWORDS:
                if pattern.search(title):
                    self.kind, self.kind_source = kind, "keyword"
                    break
        if self.kind is None:
            # nothing in the text says what this is. Private until the person (or an explicit tag/project) says otherwise.
            if self.category == Category.DEVELOPMENT:
                self.kind = Kind.TRAINING
            elif self.category == Category.MEETING:
                self.kind = Kind.MEETING
            elif self.category == Category.WORK or self.project:
                self.kind = Kind.TASK
            else:
                self.kind = Kind.OTHER
        if self.category is None:
            self.category = KIND_CATEGORY[self.kind]
        if self.category == Category.DEVELOPMENT and "development" not in self.tags:
            self.tags.append("development")


def _clean(title: str) -> str:
    title = re.sub(r"\b(?:at|on|from|starting|each|every)\b\s*$", "", title.strip(), flags=re.I)
    title = re.sub(r"^\s*(?:at|on|from|each|every|for)\b\s+", "", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" \t,;|.:-")


def parse_personal_line(raw: str, ctx: ParseContext) -> Draft | None:
    ln = _Line(raw.strip(), ctx)
    if not ln.text:
        return None
    ln.tokens()
    ln.iso_date()
    ln.recurrence()
    ln.times()
    ln.duration_part()
    ln.date_only()
    title = _clean(ln.text)
    if not title:
        if ln.kind is not None:
            title = ln.kind.value.capitalize()
        else:
            return None
    ln.classify(title)
    assert ln.kind and ln.category  # noqa: S101
    tz = ln.tz
    base_day = ln.d or ln.now.date()

    start: datetime | None = None
    end: datetime | None = None
    duration = ln.duration
    all_day = False
    if ln.t_start is not None:
        h, mi = ln.t_start
        start = resolve_local(datetime(base_day.year, base_day.month, base_day.day, h, mi), tz)
        if ln.t_end is not None:
            eh, em = ln.t_end
            end_day = base_day + (timedelta(days=1) if (eh, em) <= (h, mi) else timedelta(0))
            end = resolve_local(datetime(end_day.year, end_day.month, end_day.day, eh, em), tz)
            span = round((end - start).total_seconds() / 60)
            if duration and duration != span:
                ln.warnings.append(f"duration {duration} min ignored: the time range is {span} min")
            duration = span
        elif duration:
            end = start + timedelta(minutes=duration)
    else:
        all_day = True
        start = resolve_local(datetime(base_day.year, base_day.month, base_day.day), tz)
        if ln.t_end is not None:
            ln.warnings.append("ignored an end time without a start")

    rrule = rrule_text = None
    if ln.freq:
        rrule = _build_rrule(ln, start)
        first = _first_occurrence(rrule, start, ln.now, tz, all_day)
        if first is None:
            ln.warnings.append("the rule has no future occurrence")
        else:
            start = first
            end = first + timedelta(minutes=duration) if duration and not all_day else None
        rrule_text = describe(rrule, None if all_day else start.astimezone(tz))
    notes = ""
    if getattr(ln, "urls", None):
        notes = " ".join(ln.urls)
    return Draft(
        title=title,
        kind=ln.kind,
        category=ln.category,
        start=start,
        end=end,
        all_day=all_day,
        duration_min=duration,
        rrule=rrule,
        rrule_text=rrule_text,
        tags=list(dict.fromkeys(ln.tags)),
        project_hint=ln.project,
        notes=notes,
        warnings=ln.warnings,
        kind_source=ln.kind_source,
        category_source=ln.category_source,
    )


def _build_rrule(ln: _Line, start: datetime) -> str:
    parts = [f"FREQ={ln.freq}"]
    if ln.interval > 1:
        parts.append(f"INTERVAL={ln.interval}")
    if ln.byday:
        parts.append("BYDAY=" + ",".join(ln.byday))
    until = ln.until
    if until is None and ln.span:
        n, unit = ln.span
        base = start.astimezone(ln.tz).date()
        until = base + timedelta(
            days=n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
        )
    if ln.count:
        parts.append(f"COUNT={ln.count}")
    elif until:
        parts.append(f"UNTIL={until.strftime('%Y%m%d')}T235959")
    return ";".join(parts)


def _first_occurrence(
    rrule: str, start: datetime, now: datetime, tz: object, all_day: bool
) -> datetime | None:
    from tt_client.personal.model import PersonalItem

    probe = PersonalItem(
        title="x", start=start, tz=str(getattr(tz, "key", "UTC")), rrule=rrule, duration_min=1
    )
    floor = (
        now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        if all_day
        else now.replace(tzinfo=None)
    )
    first_naive = occurrence_dates(probe, floor, floor + timedelta(days=800))
    if not first_naive:
        return None
    return resolve_local(first_naive[0], zone(probe.tz))


def parse_personal(text: str, ctx: ParseContext) -> list[Draft]:
    """Every `;`-separated piece of the line that holds something."""
    drafts = [d for part in re.split(r"\s*;\s*", text) if (d := parse_personal_line(part, ctx))]
    return drafts


def default_share(category: Category) -> str:
    return CATEGORY_DEFAULT_SHARE[category].value
