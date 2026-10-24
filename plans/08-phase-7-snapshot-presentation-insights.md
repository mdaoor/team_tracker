# Phase 7 — Snapshot API, presentation builder (deck + page), insights

## Goal
`GET /api/snapshot?scope=…&range=…&weight_scheme=…` returns the JSON the prototype already renders. On top of it, a **presentation builder**: the user chooses what to present (scope, range, scheme, and which sections/reports/views), and the output is a **deck** — one slide per screen, `→`/`Next` advances, `←` goes back — rendered from a **generic slide template** so that *any* report, saved view or chart in the product can be exported as a slide. The original one‑page scrolling layout (`docs/spec/team_tracker_presentation.html`) remains available as the "page" mode of the same deck. Project summaries are generated as `Insight{kind=summary}` through the LLM gateway (Phase 2b) with a deterministic fallback.

## Depends on
Phase 6 (queries, layouts), Phase 4 (ACL/redaction), Phase 2b (summaries). P8 adds violations/habits later; until then those sections render "No open violations in scope".

## Inputs
- UX §5 (presentation layer), §7.3; product requirement (added during planning): *choose what to present → slide per screen → next/previous; a generic template that can export a presentation from any report*
- `docs/spec/team_tracker_presentation.html` (prototype of the section renderers) and `tools/gen_snapshot.py` (snapshot shape)
- Object model §8 `Insight`, §9 derived views

## Scope
1. **Snapshot assembler** (`domain/snapshot`): exact shape of the prototype's `D` object from live data, scoped, ACL‑filtered, for a date range and weight scheme; `docs/api/snapshot-schema.json` generated from a Pydantic `Snapshot` model.
2. **Presentation model**: new entity `Presentation` (ADR‑0021) = `{id, workspace_id, owner_id, title, scope_type, scope_id, range, weight_scheme_id, theme, slides: [Slide], is_shared, created/updated}`, `Slide = {id, kind, title?, subtitle?, config: json, notes?: text, layout: full|split|grid, position}`. Slide **kinds** = one per section renderer + generic ones: `title`, `kpis`, `projects`, `project_detail(project_key)`, `pipeline(process)`, `explorer(dimA, dimB?, measure)`, `radar(days)`, `gantt(rows)`, `now`, `people`, `violations`, `habits`, `insight(scope)`, `view(saved_view_id, layout)` (any P6 layout rendered as a slide), `chart(aggregate query)`, `table(query)`, `markdown(text)`, `image(attachment)`, `section_divider`.
3. **Presentation builder UI** (web, Views area → "Present"): left: pick scope/range/scheme; middle: ordered slide list with add/remove/reorder/duplicate; right: slide preview + per‑slide config form + speaker notes; "Start from template" (Portfolio review, Sprint review, Risk review, Team 1:1, Exec summary — each a predefined slide list); "Add current view as slide" button available on **every** view/report/chart in the app (P6 layouts, P9 governance screens) — this is the "export any report" path.
4. **Deck renderer** (`apps/presentation`, vanilla JS, single self‑contained file): modes `deck` (one slide per screen, keyboard `→ ← Space PgUp PgDn Home End`, `F` fullscreen, `N` notes, `G` grid overview, `Esc`, touch swipe, progress bar, slide counter, remote‑friendly), `page` (the original scrolling layout = all sections stacked), `print` (one slide per A4‑landscape page; or the page mode printed). Data loading: `fetch('/api/presentations/{id}/data')` same‑origin, `window.__DECK__` inlined for export, `?src=file.json` offline.
5. **Generic slide template**: a slide is `header (title, subtitle, scope chip, as‑of) + body (renderer output) + footer (stamp: viewer · scope · as of · page n/N)`; renderers are the prototype's section functions refactored to `render<Kind>(container, data, config)` so the same code draws a slide, a page section, or a P6 view embed. Every chart keeps the table toggle; every number still drills down (opens the item drawer inside the deck).
6. **Exports**: self‑contained HTML (deck + inlined data), PDF (headless Chromium print of `print` mode via Playwright on the server, A4 landscape), PPTX (`python-pptx`: one slide per `Slide`, charts re‑drawn as native PowerPoint charts where the kind maps — bar/heatmap‑as‑table/gantt‑as‑shapes — else PNG snapshot of the rendered slide), PNG per slide, CSV of each table slide. All exports stamped (P4 helper).
7. **Sharing**: `/p/{presentation_id}` URL respects ACL (viewer sees only what they may; slides whose data is fully hidden render "restricted"); `is_shared` + optional expiring share token for read‑only external viewing (admin‑enabled).
8. **Insights**: `Insight` CRUD + generator job via the gateway (`/v1/summarise/*`), grounding checks in the core server, deterministic fallback, status workflow; `insight` slide kind and project‑card summaries use them. DailyLog LLM summary (deferred from P1) via `/v1/summarise/daily-log`.
9. **Snapshot/deck data cache**: `snapshot_cache(scope, range, scheme, as_of, json)` refreshed by job every 15 min for scopes with saved presentations/views; `?fresh=1` bypass.

## Out of scope
Violations/habits computation (P8). Live collaborative editing of a deck. Animations beyond slide transitions.

## Detailed tasks

### 7.1 Snapshot model (field‑for‑field with the prototype)
```
Snapshot { schema_version, meta{viewer, scope_label, generated_for}, as_of, workspace, range{from,to}, weight_scheme,
  stages[], stage_expected_days{}, processes[]?, people[{handle,name,team}], teams[], products[{key,name}],
  projects[{key,name,product,methodology,team,summary}], epics[{key,title,project}],
  milestones[{name,project,due}], risks[{key,title,project,p,i,status}],
  items[{key,title,project,product,epic,type,assignee,team,stage,stage_age_days,state,priority,weight,
         estimate_h,hours_logged,planned_start,planned_end,actual_end,tags[],risks[],last_touched,evidence_count}],
  violations[{policy,category,severity,target,person,team,project,detail}],
  habits[{subject,key,polarity,rate,trend}], now[{person,item,title,project,since_min,kind}] }
```
Mapping rules: `stages`/`stage_expected_days` from the scope's dominant `Process` (union for multi‑process scopes with `processes[]`); `items.state` = state category; `stage` = current placement key; `estimate_h` in hours (`days × hours_per_day`, points → null); `weight` from the requested scheme; `risks` incl. inherited; `hours_logged` range‑bounded; `projects.summary` = latest non‑dismissed `Insight{kind=summary}` else fallback text; `violations/habits` from P8 tables redacted by ACL; `now` from active activities (note omitted). Range rule: items touched/done/planned in `[from,to]` plus all open/active items.

### 7.2 Presentation builder & deck
- **Data endpoint** `GET /api/presentations/{id}/data` → `{presentation, snapshot, slide_data: {slide_id: payload}}` where section‑kind slides read from `snapshot` and `view/chart/table` slides carry their own P6 query results (ACL applied per slide). `POST /api/presentations/{id}/render?format=html|pdf|pptx|png|csv` → job → download URL.
- **Builder UI**: slide kinds palette with icons; config forms generated per kind (e.g. `explorer`: rows dim, cols dim, measure; `pipeline`: process; `radar`: horizon days; `gantt`: rows epic|item|milestone, baseline; `view`: pick a `SavedView` and a layout; `chart`: an aggregate query built with the filter chips; `markdown`: text editor); drag reorder; duplicate; per‑slide notes; live preview uses the same renderer bundle as the deck (embedded as a component via an iframe to `/presentation?preview=1` with postMessage, or by importing the renderer module — prefer import; the single‑file build is only for export); templates stored as `Presentation{is_template=true}` rows seeded per workspace.
- **"Add to presentation"** menu on every view/chart/report (P6 views, P9 governance screens, item drawer charts): choose an existing presentation or create one → appends a `view|chart|table` slide with the current filter/layout/scheme frozen in `config` (and a `live: true` flag so it refreshes when presented).
- **Deck renderer**: `apps/presentation/src/{deck.js, page.js, renderers/*.js, template.js, styles.css}`; build → `dist/presentation.html` (self‑contained, < 500 KB without data, no external requests). Slide transitions CSS only; `prefers-reduced-motion` respected. Keyboard map above; `?slide=n` deep links; `G` grid overview shows thumbnails; `N` toggles a notes pane (presenter view in a second window via `BroadcastChannel`, optional). Each slide auto‑fits content: renderers receive the slide's pixel box and scale fonts/rows (`fit: shrink|paginate` — long tables paginate into continuation slides automatically, marked "(cont.)").
- **Page mode** = the prototype layout: header strip + all section renderers in prototype order, drill‑down breadcrumb kept; it is simply `Presentation{slides: default list}` rendered stacked. Keep the prototype's behaviour pixel‑comparable (±2 %).
- **Print**: `@page { size: A4 landscape; margin: 12mm }`; deck mode prints one slide per page with the footer stamp; page mode prints sections with `break-inside: avoid`.
- **Export pipeline** (`jobs/presentation_export.py`): HTML = inline data + bundle; PDF = Playwright Chromium `page.pdf()` of `print` mode; PPTX = `python-pptx` mapping table: `title → title slide`, `kpis → 2×4 text boxes`, `explorer bar → native bar chart`, `explorer matrix → table with cell fills`, `pipeline → column table + counts`, `gantt → shapes on a date axis`, `radar/table/people/violations/habits → tables (paginated)`, `projects → one card per project as text + native progress bar shape`, `chart → native chart when bar/line, else PNG`, `markdown → text`, `image → picture`; fallback for any kind = PNG screenshot of the rendered slide; theme colours from the token set; speaker notes carried into PPTX notes.

### 7.3 Insights
- Facts assembled server‑side (counts, stuck items, top risk, next milestone, velocity, estimate/evidence coverage) → gateway `/v1/summarise/project` → `{body, supporting_ids[]}`; validate `supporting_ids ⊆ facts.ids` else fallback template: "{done}/{total} items done ({pct} %). {stuck} items over expected stage time{, mostly in <stage>}. Next milestone {name} on {date}. Top risk {key} ({score})."
- Schedule nightly + on demand `POST /insights:generate {scope}`; per‑workspace budget enforced by the gateway; `PATCH /insights/{id} {status}`.
- `insight` slide kind renders body + supporting evidence chips (drill to items/violations).

### 7.4 Tests
- Golden: seed at `as_of = 2026‑10‑23T18:30+03:00`, range 14 d, default scheme → assembler output equals `tools/gen_snapshot.py` for the seed‑controlled fields; `violations/habits/now` after P8.
- Deck e2e (Playwright on the built file with the golden data): default "Portfolio review" template produces the prototype's 9 sections as slides in order; `→` ×8 reaches the last slide, `←` returns, `G` shows 9 thumbnails, `?slide=4` opens the pipeline slide; clicking `test` column opens the drill list inside the deck; page mode screenshot diff vs prototype ≤ 2 %; print emulation yields one page per slide with the stamp; no external requests.
- "Add to presentation" e2e: from a P6 matrix view (team × stage) add a slide; the deck renders the same numbers as the view; PPTX export opens (validated with `python-pptx` reading it back: slide count, titles, a native chart present on the bar slide).
- ACL: a presentation shared to a viewer without rights to squad‑b items shows the pipeline without those items and "restricted" on the habits slide.
- Insight grounding: mocked gateway returning an unsupported id is rejected and the fallback is used.

## Data & API contracts
- `GET /api/snapshot?scope=&from=&to=&weight_scheme=&fresh=` (unchanged contract from the prototype + `meta`, `schema_version`, `processes[]`).
- `/api/presentations` CRUD (+ `:duplicate`, `:from-template`), `/api/presentations/{id}/slides` (reorder, add, patch), `/api/presentations/{id}/data`, `/api/presentations/{id}/render?format=`, `/api/presentations/{id}/share` (token), `GET /p/{id}` (deck shell), `GET /presentation` (legacy page mode = default template for the scope in the query).
- Events: `presentation.created|updated|slide_added|rendered|shared`, `insight.generated|status_changed`.

## Acceptance criteria
- [ ] A user can pick scope + range + scheme, choose sections/views, press Present, and navigate one slide per screen with next/previous (keyboard, click, touch); a deep link to a slide works.
- [ ] Any P6 view or chart has "Add to presentation" and renders identically as a slide (numbers equal the view's).
- [ ] Exports: self‑contained HTML, PDF (one slide per page, stamped), PPTX (opens in PowerPoint/LibreOffice; native charts for bar/line), PNG per slide.
- [ ] Page mode is pixel‑comparable (±2 %) to the prototype in both themes with the golden snapshot.
- [ ] `GET /api/snapshot` for a project manager omits Inbox items and redacts wellbeing habits; a line manager sees them; a shared deck obeys the same rules per slide.
- [ ] No external network calls from the deck/page (CI intercepts requests).
- [ ] Table toggle on every chart; keyboard‑only user can reach any item drawer; `prefers-reduced-motion` disables transitions.
- [ ] Insights: flag off → deterministic fallback; flag on → grounded summaries via the gateway with `supporting_ids`.
- [ ] Snapshot for the 50 k‑item fixture < 2 s cold, < 100 ms cached; deck data for a 12‑slide presentation < 3 s cold.

## Exit checklist
- [ ] `docs/api/snapshot-schema.json` and `docs/api/presentation-schema.json` generated and committed; `tools/gen_snapshot.py` validated against the former in CI.
- [ ] `docs/presentation/README.md`: slide kinds, config schemas, template authoring, export mapping table, keyboard map.
- [ ] ADR‑0011 "Prototype renderers are the spec for sections; additive changes only"; ADR‑0021 "Presentation entity and generic slide template".
- [ ] P9 TODO: governance screens get the "Add to presentation" menu.

> **Update (Phase 2b built):** generate insight text through `LLMGatewayClient.summarise` (`/v1/summarise/*`); the gateway already flags ungrounded ids, grounding checks stay in the core server.
