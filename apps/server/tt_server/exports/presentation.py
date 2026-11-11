"""Presentation exports (Phase 7 §7.2): self-contained HTML, PDF and PNG via headless Chromium, PPTX via python-pptx, CSV.

Every export is stamped through `acl.export` (who, what scope, when): CSV lines, the PDF/print footer (rendered by the deck), the
PPTX footer text box and a `stamp.txt` inside the PNG/CSV archives.
"""

from __future__ import annotations

import glob
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from tt_server.acl.export import csv_export, stamp
from tt_server.domain.errors import Conflict, Invalid, Unavailable
from tt_server.exports import compute
from tt_server.exports.pptx_builder import build_pptx


def inline_html(bundle: Path, payload: dict[str, Any], *, mode: str = "deck", handle: str = "") -> str:
    if not bundle.exists():
        raise Unavailable(
            "bundle_missing",
            "the presentation bundle is not built (pnpm --filter @tt/presentation build)",
        )
    data = (
        json.dumps(payload, default=str, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
    )
    boot = f"window.__DECK__={data};window.__DECK_MODE__={json.dumps(mode)};window.__DECK_STAMP__={json.dumps(stamp(handle, payload['snapshot']['meta']['scope_label']))};"
    return bundle.read_text().replace("</head>", f"<script>{boot}</script></head>", 1)


def _chromium() -> str | None:
    if p := os.environ.get("TT_CHROMIUM"):
        return p
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))
    return found[-1] if found else None


async def _pdf(html: str) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        try:
            page = await browser.new_page(viewport={"width": 1123, "height": 794})
            await page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if not route.request.url.startswith(("data:", "about:"))
                    else route.continue_()
                ),
            )
            await page.set_content(html)
            await page.wait_for_selector("html[data-ready='1']", state="attached", timeout=15000)
            await page.emulate_media(media="print")
            return await page.pdf(
                format="A4", landscape=True, print_background=True, prefer_css_page_size=True
            )
        finally:
            await browser.close()


async def _pngs(html: str, count: int) -> list[bytes]:
    from playwright.async_api import async_playwright

    out: list[bytes] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if not route.request.url.startswith(("data:", "about:"))
                    else route.continue_()
                ),
            )
            await page.set_content(html)
            await page.wait_for_selector("html[data-ready='1']", state="attached", timeout=15000)
            n = await page.evaluate("window.__deck.count")
            for i in range(min(n, count) if count else n):
                await page.evaluate(f"window.__deck.go({i})")
                out.append(await page.screenshot(type="png"))
        finally:
            await browser.close()
    return out


def _csvs(payload: dict[str, Any], handle: str) -> dict[str, str]:
    snap, sd = payload["snapshot"], payload["slide_data"]
    scope = snap["meta"]["scope_label"]
    out: dict[str, str] = {}
    cols = [
        "key",
        "title",
        "project",
        "type",
        "assignee",
        "stage",
        "state",
        "priority",
        "weight",
        "due_date",
        "hours_logged",
    ]
    for n, sl in enumerate(payload["presentation"]["slides"], 1):
        name = f"{n:02d}-{sl['kind']}.csv"
        d = sd.get(sl["id"]) or {}
        if sl["kind"] in {"table", "view"} and d.get("items"):
            out[name] = csv_export(handle, scope, cols, [[i.get(c) for c in cols] for i in d["items"]])
        elif sl["kind"] == "chart" and d.get("rows"):
            out[name] = csv_export(
                handle,
                scope,
                [sl["config"]["rows"], *(d["cols"] or [d["measure"]])],
                [[r["value"], *c] for r, c in zip(d["rows"], d["cells"], strict=True)],
            )
        elif sl["kind"] == "explorer":
            c = sl["config"]
            ex = compute.explorer(snap, c["dimA"], c.get("dimB"), c["measure"])
            out[name] = csv_export(
                handle,
                scope,
                [c["dimA"], *(ex["cols"] or [c["measure"]])],
                [[r, *cells] for r, cells in zip(ex["rows"], ex["cells"], strict=True)],
            )
        elif sl["kind"] == "people":
            out[name] = csv_export(handle, scope, compute.PEOPLE_HEADER, compute.people_rows(snap))
        elif sl["kind"] == "violations":
            hdr = ["policy", "category", "severity", "target", "person", "project", "detail"]
            out[name] = csv_export(
                handle,
                scope,
                hdr,
                [[v[h] for h in hdr] for v in compute.scoped_violations(snap, snap["items"])],
            )
        elif sl["kind"] == "habits":
            hdr = ["subject", "key", "polarity", "rate", "trend"]
            out[name] = csv_export(handle, scope, hdr, [[h[k] for k in hdr] for h in snap["habits"]])
    return out


async def render(fmt: str, payload: dict[str, Any], bundle: Path, *, handle: str) -> bytes:
    scope = payload["snapshot"]["meta"]["scope_label"]
    if fmt == "html":
        return inline_html(bundle, payload, handle=handle).encode()
    if fmt == "pdf":
        return await _pdf(inline_html(bundle, payload, mode="print", handle=handle))
    if fmt == "png":
        shots = await _pngs(inline_html(bundle, payload, handle=handle), 0)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in enumerate(shots, 1):
                z.writestr(f"slide-{n:02d}.png", b)
            z.writestr("stamp.txt", stamp(handle, scope) + "\n")
        return buf.getvalue()
    if fmt == "csv":
        files = _csvs(payload, handle)
        if not files:
            raise Conflict("nothing_to_export", "this presentation has no tabular slide")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in files.items():
                z.writestr(name, text)
            z.writestr("stamp.txt", stamp(handle, scope) + "\n")
        return buf.getvalue()
    if fmt == "pptx":
        return build_pptx(payload, stamp(handle, scope))
    raise Invalid("invalid_format", f"unknown format {fmt!r}")
