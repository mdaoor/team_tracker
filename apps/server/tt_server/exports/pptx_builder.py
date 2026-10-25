"""PPTX export: one slide per `Slide` (plan §7.2 mapping table). Native charts for bar-shaped data, tables for the rest.

Theme colours come from the deck's token set. Speaker notes are carried over. Slide kinds with no native mapping (`image`) are
reported on the slide as a text box instead of silently dropped.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Inches, Pt

from tt_server.exports import compute

W, H = Inches(13.333), Inches(7.5)
INK, MUTED, LINE = RGBColor(0x0B, 0x0B, 0x0B), RGBColor(0x52, 0x51, 0x4E), RGBColor(0xE3, 0xE1, 0xDC)
SEQ = [RGBColor.from_string(c) for c in ("CDE2FB", "9EC5F4", "6DA7EC", "3987E5", "1C5CAB", "0D366B")]
ACCENT, SERIOUS = RGBColor(0x2A, 0x78, 0xD6), RGBColor(0xEC, 0x83, 0x5A)
ROWS_PER_SLIDE = 12


def _text(
    slide: Any,
    x: Any,
    y: Any,
    w: Any,
    h: Any,
    text: str,
    *,
    size: int = 14,
    bold: bool = False,
    color: RGBColor = INK,
) -> Any:
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.text = text
    for p in tf.paragraphs:
        for r in p.runs:
            r.font.size, r.font.bold, r.font.color.rgb = Pt(size), bold, color
    return tb


def _frame(
    prs: Any,
    sl: dict[str, Any],
    page: int,
    total: int,
    stamp_text: str,
    scope: str,
    as_of: str,
    title: str | None = None,
    cont: bool = False,
) -> Any:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    t = (title or sl.get("title") or sl["kind"].replace("_", " ").title()) + (" (cont.)" if cont else "")
    _text(s, Inches(0.5), Inches(0.3), Inches(10), Inches(0.7), t, size=26, bold=True)
    _text(
        s,
        Inches(10.3),
        Inches(0.4),
        Inches(2.6),
        Inches(0.4),
        f"{scope} · as of {as_of[:16].replace('T', ' ')}",
        size=10,
        color=MUTED,
    )
    if sl.get("subtitle"):
        _text(s, Inches(0.5), Inches(0.95), Inches(12), Inches(0.4), sl["subtitle"], size=14, color=MUTED)
    _text(
        s,
        Inches(0.5),
        Inches(7.05),
        Inches(11.5),
        Inches(0.3),
        f"{stamp_text} · {page}/{total}",
        size=9,
        color=MUTED,
    )
    if sl.get("notes"):
        s.notes_slide.notes_text_frame.text = sl["notes"]
    return s


def _table(
    slide: Any,
    header: list[str],
    rows: list[list[str]],
    *,
    y: Any = None,
    fills: dict[tuple[int, int], RGBColor] | None = None,
) -> None:
    y = y if y is not None else Inches(1.5)
    shape = slide.shapes.add_table(
        len(rows) + 1, len(header), Inches(0.5), y, Inches(12.3), Inches(0.35) * (len(rows) + 1)
    )
    tbl = shape.table
    for c, h in enumerate(header):
        tbl.cell(0, c).text = str(h)
    for r, row in enumerate(rows, 1):
        for c, v in enumerate(row):
            tbl.cell(r, c).text = str(v)
            if fills and (r - 1, c) in fills:
                tbl.cell(r, c).fill.solid()
                tbl.cell(r, c).fill.fore_color.rgb = fills[(r - 1, c)]
    for r in range(len(rows) + 1):
        for c in range(len(header)):
            for p in tbl.cell(r, c).text_frame.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(11)


def _bar_chart(slide: Any, cats: list[str], series: dict[str, list[float]], *, y: Any = None) -> None:
    y = y if y is not None else Inches(1.5)
    if not cats or not series or not any(series.values()):  # python-pptx refuses an empty category axis
        box = slide.shapes.add_textbox(Inches(0.5), y, Inches(12.3), Inches(1))
        box.text_frame.text = "No data in scope."
        return
    cd = CategoryChartData()
    cd.categories = cats
    for name, vals in series.items():
        cd.add_series(name, vals)
    slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.5), y, Inches(12.3), Inches(5.3), cd)


def _pages(rows: list[Any]) -> list[list[Any]]:
    return [rows[i : i + ROWS_PER_SLIDE] for i in range(0, len(rows), ROWS_PER_SLIDE)] or [[]]


PLAN_KINDS = {"roadmap", "milestones", "deliverables", "plan_vs_actual", "phase_status", "project_overview"}
STATUS_FILL = {
    "overdue": RGBColor(0xEC, 0x83, 0x5A),
    "at_risk": RGBColor(0xF2, 0xB7, 0x5A),
    "done": RGBColor(0x3C, 0xA6, 0x6B),
}


def _plan_slide(frame: Any, sl: dict[str, Any], kind: str, cfg: dict[str, Any], snap: dict[str, Any]) -> None:
    """Plan kinds: tables from the manager-entered plan; the roadmap as shapes on a date axis. Empty plans say so."""
    pk = cfg.get("project_key")
    phases = [p for p in snap.get("phases", []) if not pk or p["project"] == pk]
    delivs = [
        d
        for d in snap.get("deliverables", [])
        if (not pk or d["project"] == pk) and (not cfg.get("phase") or d["phase"] == cfg["phase"])
    ]
    mss = [x for x in snap.get("milestones", []) if not pk or x["project"] == pk]
    projects = [
        p
        for p in snap["projects"]
        if (pk and p["key"] == pk) or (not pk and any(x["project"] == p["key"] for x in phases + mss))
    ]
    empty = {
        "roadmap": phases,
        "milestones": mss,
        "deliverables": delivs,
        "plan_vs_actual": delivs,
        "phase_status": phases,
        "project_overview": projects,
    }[kind]
    if not empty:
        _text(
            frame(sl),
            Inches(0.5),
            Inches(1.6),
            Inches(12),
            Inches(1),
            "No plan for this scope yet.",
            size=16,
            color=MUTED,
        )
        return
    dash = lambda v: "—" if v in (None, "") else str(v)  # noqa: E731
    if kind == "project_overview":
        s = frame(sl)
        y: int = int(Inches(1.5))
        for p in projects:
            nxt = next((x for x in mss if x["project"] == p["key"] and x["status"] != "completed"), None)
            _text(
                s,
                Inches(0.5),
                y,
                Inches(12),
                Inches(0.5),
                f"{p['name']} ({p['key']})  ·  health: {dash(p.get('health'))}",
                size=18,
                bold=True,
            )
            _text(
                s, Inches(0.5), y + Inches(0.5), Inches(12), Inches(1.4),
                f"Owner {dash(p.get('owner'))}\nDates {dash(p.get('planned_start'))} → {dash(p.get('planned_end'))}\n"
                f"Status {dash(p.get('status'))}   Baseline {dash(p.get('baseline_name'))}\n"
                f"Next milestone {nxt['name'] + ' (' + nxt['due'] + ')' if nxt else '—'}\n{p.get('summary') or ''}",
                size=12,
            )  # fmt: skip
            y += int(Inches(2.3))
    elif kind == "milestones":
        _table(
            frame(sl),
            ["Milestone", "Kind", "Due", "Status", "Deliverables"],
            [
                [
                    x["name"],
                    x["kind"],
                    x["due"],
                    x["status"],
                    f"{x['deliverables_done']}/{x['deliverables_total']}" if x["deliverables_total"] else "—",
                ]
                for x in mss
            ],
        )
    elif kind == "phase_status":
        _table(
            frame(sl),
            ["Phase", "Dates", "Status", "Progress", "Deliverables"],
            [
                [
                    p["title"],
                    f"{dash(p['planned_start'])} → {dash(p['planned_end'])}",
                    p["status"],
                    f"{p['progress_pct']}%" if p["progress_pct"] is not None else "—",
                    p["deliverable_count"],
                ]
                for p in phases
            ],
        )
    elif kind in {"deliverables", "plan_vs_actual"}:
        for k, chunk in enumerate(_pages(delivs)):
            s = frame(sl, cont=k > 0)
            if kind == "deliverables":
                _table(
                    s,
                    ["Deliverable", "Owner", "Planned", "Status", "Progress", "Tasks", "Hours"],
                    [
                        [
                            d["title"],
                            dash(d["owner"]),
                            f"{dash(d['planned_start'])} → {dash(d['planned_end'])}",
                            d["status"],
                            f"{d['progress_pct']}%" if d["progress_pct"] is not None else "—",
                            d["linked_tasks"] or "—",
                            d["hours_logged"] or "—",
                        ]
                        for d in chunk
                    ],
                )
            else:
                _table(
                    s,
                    ["Deliverable", "Baseline end", "Current end", "Variance (d)", "Actual", "Status"],
                    [
                        [
                            d["title"],
                            dash(d["baseline_end"]),
                            dash(d["planned_end"]),
                            dash(d["baseline_variance_days"]),
                            dash(d["actual_end"]),
                            d["status"],
                        ]
                        for d in chunk
                    ],
                )
    else:  # roadmap: bars on a date axis (native shapes, so it stays editable)
        s = frame(sl)
        rows = [("phase", p) for p in phases for p in [p]]
        rows = [
            r
            for p in phases
            for r in [("phase", p), *[("deliverable", d) for d in delivs if d["phase"] == p["key"]]]
        ]
        stamps = [
            date.fromisoformat(v[:10]) for _, r in rows for v in (r["planned_start"], r["planned_end"]) if v
        ] + [date.fromisoformat(x["due"][:10]) for x in mss]
        if not stamps:
            return
        lo, hi = min(stamps), max(stamps)
        span = max((hi - lo).days, 7)
        left, width, top = Inches(3.4), Inches(9.4), Inches(1.5)
        step = min(int(Inches(0.32)), int(Inches(5.2) / max(len(rows), 1)))
        for i, (rk, r) in enumerate(rows[:18]):
            y = top + step * i
            _text(
                s,
                Inches(0.5),
                y,
                Inches(2.9),
                step,
                ("" if rk == "phase" else "   ") + r["title"],
                size=10,
                bold=rk == "phase",
            )
            if r["planned_start"] and r["planned_end"]:
                a, b = date.fromisoformat(r["planned_start"][:10]), date.fromisoformat(r["planned_end"][:10])
                x = left + int(width * (a - lo).days / span)
                w = max(Emu(Inches(0.05)), int(width * (b - a).days / span))
                bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y + int(step * 0.15), w, int(step * 0.6))
                bar.fill.solid(), bar.line.fill.background()
                bar.fill.fore_color.rgb = STATUS_FILL.get(r["status"], ACCENT)
        for x in mss:
            xx = left + int(width * (date.fromisoformat(x["due"][:10]) - lo).days / span)
            m_ = s.shapes.add_shape(
                MSO_SHAPE.DIAMOND, xx - Inches(0.08), Inches(1.2), Inches(0.16), Inches(0.16)
            )
            m_.fill.solid(), m_.line.fill.background()
            m_.fill.fore_color.rgb = SERIOUS if x["status"] == "overdue" else RGBColor(0x8F, 0x5B, 0xD6)
        _text(
            s,
            left,
            Inches(6.9),
            width,
            Inches(0.3),
            f"{lo.isoformat()}  →  {hi.isoformat()}   ◆ milestones",
            size=9,
            color=MUTED,
        )


def build_pptx(payload: dict[str, Any], stamp_text: str) -> bytes:
    snap, sd, pres = payload["snapshot"], payload["slide_data"], payload["presentation"]
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    scope, as_of = snap["meta"]["scope_label"], snap["as_of"]
    slides = pres["slides"]
    # pagination makes the page count unknown until built; count after the fact via the placeholder and patch the footers
    built: list[Any] = []

    def frame(sl: dict[str, Any], **kw: Any) -> Any:
        s = _frame(prs, sl, 0, 0, stamp_text, scope, as_of, **kw)
        built.append(s)
        return s

    for sl in slides:
        kind, cfg, d = sl["kind"], sl["config"], sd.get(sl["id"]) or {}
        if kind in {"title", "section_divider"}:
            s = frame(sl, title=sl.get("title") or pres["title"])
            _text(
                s,
                Inches(0.5),
                Inches(3),
                Inches(12),
                Inches(1),
                sl.get("subtitle") or scope,
                size=20,
                color=MUTED,
            )
        elif kind == "kpis":
            s = frame(sl)
            for n, (label, val, sub) in enumerate(compute.kpis(snap)):
                x, y = Inches(0.5 + (n % 4) * 3.1), Inches(1.7 + (n // 4) * 2.3)
                box = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, Inches(2.9), Inches(2.0))
                box.fill.background()
                box.line.color.rgb = LINE
                _text(
                    s, x + Inches(0.15), y + Inches(0.15), Inches(2.6), Inches(0.9), val, size=34, bold=True
                )
                _text(
                    s, x + Inches(0.15), y + Inches(1.0), Inches(2.6), Inches(0.4), label, size=14, bold=True
                )
                _text(
                    s, x + Inches(0.15), y + Inches(1.4), Inches(2.6), Inches(0.4), sub, size=10, color=MUTED
                )
        elif kind == "projects":
            for chunk in _pages(snap["projects"]) if snap["projects"] else [[]]:
                s = frame(sl, cont=chunk is not _pages(snap["projects"])[0])
                for n, p in enumerate(chunk):
                    its = [i for i in snap["items"] if i["project"] == p["key"]]
                    done = sum(1 for i in its if i["state"] == "done")
                    pct = round(done / len(its) * 100) if its else 0
                    x, y = Inches(0.5 + (n % 2) * 6.3), Inches(1.5 + (n // 2) * 2.7)
                    _text(
                        s,
                        x,
                        y,
                        Inches(6),
                        Inches(0.4),
                        f"{p['name']}  ({p['key']} · {p['methodology']})",
                        size=15,
                        bold=True,
                    )
                    _text(s, x, y + Inches(0.4), Inches(6), Inches(1.2), p["summary"], size=10, color=MUTED)
                    track = s.shapes.add_shape(
                        MSO_SHAPE.RECTANGLE, x, y + Inches(1.65), Inches(5.8), Inches(0.14)
                    )
                    track.fill.solid(), track.line.fill.background()
                    track.fill.fore_color.rgb = LINE
                    bar = s.shapes.add_shape(
                        MSO_SHAPE.RECTANGLE,
                        x,
                        y + Inches(1.65),
                        max(Emu(1), int(Inches(5.8) * pct / 100)),
                        Inches(0.14),
                    )
                    bar.fill.solid(), bar.line.fill.background()
                    bar.fill.fore_color.rgb = ACCENT
                    _text(
                        s,
                        x,
                        y + Inches(1.8),
                        Inches(6),
                        Inches(0.3),
                        f"{pct}% done ({done}/{len(its)})",
                        size=10,
                    )
        elif kind == "pipeline":
            s = frame(sl)
            rows = []
            exp = snap["stage_expected_days"]
            for st in snap["stages"]:
                its = [i for i in snap["items"] if i["stage"] == st]
                over = sum(
                    1
                    for i in its
                    if i["state"] != "done" and exp.get(st) and (i["stage_age_days"] or 0) > exp[st]
                )
                rows.append(
                    [
                        st,
                        str(len(its)),
                        f"{sum(i['weight'] for i in its):g}",
                        f"{over} over {exp.get(st)}d" if over else "—",
                    ]
                )
            _table(s, ["stage", "items", "Σ weight", "over expected"], rows)
        elif kind == "explorer":
            ex = compute.explorer(snap, cfg["dimA"], cfg.get("dimB"), cfg["measure"])
            s = frame(sl)
            if ex["cols"] is None:
                _bar_chart(s, ex["rows"], {cfg["measure"]: [c[0] for c in ex["cells"]]})
            else:
                mx = max([v for r in ex["cells"] for v in r] + [1])
                fills = {
                    (ri, ci + 1): SEQ[min(5, int(v / mx * 5.999))]
                    for ri, r in enumerate(ex["cells"])
                    for ci, v in enumerate(r)
                    if v
                }
                _table(
                    s,
                    [f"{cfg['dimA']} \\ {cfg['dimB']}", *ex["cols"]],
                    [
                        [r, *[str(int(v) if v else "") for v in cells]]
                        for r, cells in zip(ex["rows"], ex["cells"], strict=True)
                    ],
                    fills=fills,
                )
        elif kind == "radar":
            rd = compute.radar(snap, int(cfg["days"]))
            for k, chunk in enumerate(_pages(rd["due"])):
                s = frame(sl, cont=k > 0)
                if k == 0 and rd["overdue"]:
                    _text(
                        s,
                        Inches(0.5),
                        Inches(1.2),
                        Inches(8),
                        Inches(0.3),
                        f"{len(rd['overdue'])} overdue",
                        size=12,
                        bold=True,
                        color=SERIOUS,
                    )
                _table(
                    s,
                    ["due", "key", "item", "owner", "stage"],
                    [
                        [
                            i["planned_end"],
                            i["key"],
                            i["title"],
                            i["assignee"] or "—",
                            f"{i['stage']} {i['stage_age_days']}d",
                        ]
                        for i in chunk
                    ],
                    y=Inches(1.6),
                )
        elif kind == "gantt":
            s = frame(sl)
            _gantt(s, snap, cfg)
        elif kind == "now":
            s = frame(sl)
            _table(
                s,
                ["person", "item", "project", "since (min)", "kind"],
                [
                    [n["person"], f"{n['item']} {n['title']}", n["project"], str(n["since_min"]), n["kind"]]
                    for n in snap["now"]
                ][:ROWS_PER_SLIDE],
            )
        elif kind == "people":
            for k, chunk in enumerate(_pages(compute.people_rows(snap))):
                _table(frame(sl, cont=k > 0), compute.PEOPLE_HEADER, chunk)
        elif kind in {"violations", "habits"}:
            if d.get("restricted"):
                s = frame(sl)
                _text(
                    s,
                    Inches(0.5),
                    Inches(3),
                    Inches(12),
                    Inches(1),
                    "Restricted: you do not have access to this data.",
                    size=20,
                    color=MUTED,
                )
                continue
            rows = (
                [
                    [
                        v["severity"],
                        v["target"],
                        v["policy"].replace("_", " "),
                        v["detail"],
                        v["person"] or "",
                    ]
                    for v in compute.scoped_violations(snap, snap["items"])
                ]
                if kind == "violations"
                else [
                    [
                        h["subject"],
                        h["key"].replace("_", " "),
                        h["polarity"],
                        f"{round(h['rate'] * 100)}%",
                        h["trend"],
                    ]
                    for h in snap["habits"]
                ]
            )
            hdr = (
                ["severity", "target", "policy", "detail", "person"]
                if kind == "violations"
                else ["subject", "habit", "polarity", "rate", "trend"]
            )
            for k, chunk in enumerate(_pages(rows)):
                s = frame(sl, cont=k > 0)
                if not rows:
                    _text(
                        s,
                        Inches(0.5),
                        Inches(2),
                        Inches(12),
                        Inches(1),
                        "No open violations in scope." if kind == "violations" else "No habits in scope.",
                        size=18,
                        color=MUTED,
                    )
                else:
                    _table(s, hdr, chunk)
        elif kind == "chart":
            s = frame(sl)
            if d.get("cols"):
                mx = max([v for r in d["cells"] for v in r] + [1])
                fills = {
                    (ri, ci + 1): SEQ[min(5, int(v / mx * 5.999))]
                    for ri, r in enumerate(d["cells"])
                    for ci, v in enumerate(r)
                    if v
                }
                _table(
                    s,
                    [cfg["rows"], *d["cols"]],
                    [
                        [r["value"], *[f"{v:g}" if v else "" for v in c]]
                        for r, c in zip(d["rows"], d["cells"], strict=True)
                    ],
                    fills=fills,
                )
            else:
                _bar_chart(
                    s,
                    [r["value"] for r in d.get("rows", [])],
                    {cfg.get("measure", "count"): [c[0] for c in d.get("cells", [])]},
                )
        elif kind in {"table", "view"}:
            items = d.get("items", [])
            for k, chunk in enumerate(_pages(items)):
                _table(
                    frame(sl, title=sl.get("title") or (d.get("view") or {}).get("name"), cont=k > 0),
                    ["key", "title", "stage", "priority", "weight", "assignee"],
                    [
                        [
                            i["key"],
                            i["title"],
                            i["stage"] or "",
                            i["priority"],
                            f"{i['weight']:g}",
                            i["assignee"] or "",
                        ]
                        for i in chunk
                    ],
                )
        elif kind in {"insight", "project_detail"}:
            s = frame(sl)
            ins = d.get("insights", [])
            _text(
                s,
                Inches(0.5),
                Inches(1.5),
                Inches(12),
                Inches(5),
                "\n\n".join(f"{i['project']}: {i['body']}" for i in ins)
                or "\n\n".join(
                    f"{p['key']}: {p['summary']}"
                    for p in snap["projects"]
                    if kind == "insight" or p["key"] == cfg.get("project_key")
                ),
                size=14,
            )
        elif kind in PLAN_KINDS:
            _plan_slide(frame, sl, kind, cfg, snap)
        elif kind == "markdown":
            _text(frame(sl), Inches(0.5), Inches(1.5), Inches(12), Inches(5), cfg["text"], size=16)
        elif kind == "image":
            _text(
                frame(sl),
                Inches(0.5),
                Inches(3),
                Inches(12),
                Inches(1),
                f"[image: {cfg.get('url') or cfg.get('attachment_id') or 'none'}]",
                size=16,
                color=MUTED,
            )
    total = len(built)
    for n, s in enumerate(built, 1):  # footers carry the page counter now that pagination is known
        for shape in s.shapes:
            if shape.has_text_frame and shape.text_frame.text.endswith(" · 0/0"):
                shape.text_frame.paragraphs[0].runs[0].text = (
                    shape.text_frame.text[: -len("0/0")] + f"{n}/{total}"
                )
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _gantt(slide: Any, snap: dict[str, Any], cfg: dict[str, Any]) -> None:
    rows = []
    for e in snap["epics"]:
        its = [i for i in snap["items"] if i["epic"] == e["key"] and i["planned_start"] and i["planned_end"]]
        if its:
            rows.append(
                (
                    f"{e['key']} {e['title']}"[:34],
                    min(i["planned_start"] for i in its),
                    max(i["planned_end"] for i in its),
                    sum(1 for i in its if i["state"] == "done"),
                    len(its),
                )
            )
    if not rows:
        _text(
            slide,
            Inches(0.5),
            Inches(2),
            Inches(12),
            Inches(1),
            "No scheduled epics in scope.",
            size=18,
            color=MUTED,
        )
        return
    d0 = min(date.fromisoformat(r[1]) for r in rows)
    d1 = max(date.fromisoformat(r[2]) for r in rows)
    span = max(1, (d1 - d0).days)
    x0, wd = Inches(3.6), Inches(9.2)
    for n, (label, a, b, done, tot) in enumerate(rows[:14]):
        y = Inches(1.6 + n * 0.38)
        _text(slide, Inches(0.5), y - Inches(0.05), Inches(3), Inches(0.3), label, size=10)
        xa = x0 + int(wd * (date.fromisoformat(a) - d0).days / span)
        wbar = max(Emu(Inches(0.05)), int(wd * ((date.fromisoformat(b) - date.fromisoformat(a)).days) / span))
        track = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, xa, y, wbar, Inches(0.22))
        track.fill.solid(), track.line.fill.background()
        track.fill.fore_color.rgb = LINE
        bar = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, xa, y, max(Emu(1), int(wbar * done / max(1, tot))), Inches(0.22)
        )
        bar.fill.solid(), bar.line.fill.background()
        bar.fill.fore_color.rgb = ACCENT
    today = date.fromisoformat(snap["as_of"][:10])
    if d0 <= today <= d1:
        ln = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            x0 + int(wd * (today - d0).days / span),
            Inches(1.5),
            Emu(12000),
            Inches(0.38 * min(len(rows), 14) + 0.2),
        )
        ln.fill.solid(), ln.line.fill.background()
        ln.fill.fore_color.rgb = SERIOUS
