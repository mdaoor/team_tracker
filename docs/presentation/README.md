# Presentations (Phase 7)

`GET /api/v1/w/{ws}/snapshot` returns the prototype's data object for a scope and range; `Presentation`s turn it, plus saved views and
charts, into a deck: one slide per screen, `→`/`←`, or the original scrolling page. Decisions: ADR-0011, ADR-0021.

## Pieces
| piece | where |
|---|---|
| snapshot assembler + `Snapshot` model | `apps/server/tt_server/domain/snapshot/`, schema `docs/api/snapshot-schema.json` |
| presentation model, slide kinds, templates | `domain/presentations.py`, schema `docs/api/presentation-schema.json` |
| API | `api/routers/presentations.py` (`/snapshot`, `/presentations*`, `/insights*`, `/p/...`) |
| deck / page / print renderer | `apps/presentation` -> `dist/presentation.html` (one file, ~47 KB, no external requests) |
| builder + "Add to presentation" | `apps/web/src/pages/present/`, `components/AddToPresentation.tsx` |
| exports | `exports/presentation.py` (HTML, PDF, PNG, CSV), `exports/pptx_builder.py` |
| jobs | `snapshot.refresh` (every 15 min), `insights.nightly` (03:45) |

Regenerate the schemas with `uv run python tools/gen_schemas.py` (a test fails if they drift); rebuild the bundle with
`pnpm --filter @tt/presentation build`; the golden fixture with `uv run python apps/presentation/tools/make_fixture.py`.

## Snapshot
`GET /snapshot?scope=&days=&weight_scheme=&fresh=&as_of=`; `scope` is `workspace`, `product:KEY`, `project:KEY`, `team:slug` or `person:@h`.
Items are those open/active plus any done, planned or touched inside `[today - days, today]`; epics are listed separately; hours are
range-bounded; `estimate_h` is hours (days x `settings.hours_per_day`, default 8; points give `null`); stages come from the dominant process
(`processes[]` appears when the scope spans several) and omit closed stages such as `retired`. Everything goes through `visible()`, so a
project manager's snapshot has no Inbox items and no wellbeing habits. `violations`/`habits` stay empty until Phase 8 writes them.
Cached per (viewer, scope, range, scheme) for 15 min; `fresh=1` bypasses.

## Slide kinds and config
| kind | config (defaults) | data |
|---|---|---|
| `title`, `section_divider` | - | - |
| `kpis`, `projects`, `now`, `people` | - | snapshot |
| `project_detail` | `project_key` (required) | snapshot |
| `pipeline` | `process` | snapshot |
| `explorer` | `dimA=team`, `dimB=stage`, `measure=count\|hours\|wt\|viol` | snapshot (bar chart without `dimB`) |
| `radar` | `days=14` | snapshot |
| `gantt` | `rows=epic\|item\|milestone`, `baseline` | snapshot |
| `roadmap` | `project_key`, `rows=phase\|epic\|milestone` (phase), `zoom=fit\|month\|week\|day` (fit) | plan (phases, deliverables, milestones); epic rows from reported items |
| `violations` | `with_habits=false` | snapshot; `restricted` flag |
| `habits` | - | snapshot; `restricted` flag |
| `insight` | `scope` (project key, empty = all) | stored insights, else the project's card text |
| `view` | `saved_view_id` (required), `layout` | the saved view run as the viewer |
| `chart` | `rows` (required), `cols`, `measure`, `filter` | `POST /query/aggregate` result |
| `table` | `filter`, `sort`, `limit=50` | `POST /query/items` result |
| `markdown` | `text` (required) | - |
| `image` | `url` (https or data:) | - |

Per slide: `title`, `subtitle`, `notes` (speaker notes), `layout` (`full\|split\|grid`, reserved), `live` (query slides re-run when presented).

## Templates
`portfolio_review` (the prototype's nine sections), `sprint_review`, `risk_review`, `team_1on1`, `exec_summary`: lists in `TEMPLATES`
(`domain/presentations.py`); add one by adding a list of `_s(kind, title, **config)`.

## Deck
`/p/{ws}/{id}` (session) or `/p/t/{token}` (external link) serve the bundle; it fetches the data endpoint once, same origin.
`?slide=n` (1-based) deep-links, `?mode=deck|page|print`, `?theme=`, `?src=file.json` for offline use; exported HTML inlines the data.

| key | action |
|---|---|
| `→` `Space` `PgDn` / `←` `PgUp` | next / previous |
| `Home` `End` | first / last |
| `G` | slide overview (thumbnails) |
| `N` | speaker notes |
| `F` | fullscreen |
| `T` | toggle theme |
| `Esc` | close drawer, drill-down, overview, notes |
| swipe left/right | next / previous (touch) |

Every number drills: tiles, pipeline columns, matrix cells and bars open the items behind them inside the deck; `Enter` on a focused
row opens the item drawer. Every chart has a table toggle. Tables longer than a page continue on a "(cont.)" slide (`fit: paginate`, the default) or scale to one slide, never below half size (`fit: shrink`); the Projects slide includes plan-only projects. A KPI tile that is not a plain item count says how its number relates to the list ("36 open violations on 31 items"). `prefers-reduced-motion`
turns transitions off. Print: `@page A4 landscape`, one slide per page, footer stamped.

## Exports (`POST /presentations/{id}/render?format=`)
Synchronous; stamped through `acl.export`.
| format | how | notes |
|---|---|---|
| `html` | bundle + inlined data | works offline |
| `pdf` | Chromium `page.pdf()` of print mode | needs Playwright + Chromium on the server (`TT_CHROMIUM`), network blocked |
| `png` | one screenshot per slide, zipped | same |
| `pptx` | python-pptx | title -> title slide; kpis -> 2x4 text boxes; explorer bar -> native bar chart, matrix -> table with cell fills; pipeline/radar/people/violations/habits/now/table/view -> tables (12 rows per slide, "(cont.)"); projects -> cards with a progress-bar shape; gantt -> shapes on a date axis; chart -> native bar or table; markdown/insight -> text; notes -> speaker notes. `image` is a text placeholder |
| `csv` | zip of one CSV per tabular slide + `stamp.txt` | |

## Insights
`POST /insights:generate {scope: "WEB"|"all"}` builds facts (counts, stuck items, top risk, next milestone, coverage) as the requester,
calls the gateway's `/v1/summarise/project`, and keeps the answer only if its `supporting_ids` are a subset of the facts' ids; otherwise
the deterministic text ("12/40 items done (30 %). 3 items over expected stage time, mostly in test. Next milestone ... Top risk ...") is
stored with `source: fallback`. `PATCH /insights/{id} {status}`; dismissed insights stop feeding project cards. The nightly job stores
gateway summaries only. `POST /daily-logs/{id}/summarise` re-words a draft day through the gateway under the same grounding rule.

## Measured
* Page mode vs the prototype: 0.02 % of pixels differ (light and dark), budget 2 %.
* 50 k-item snapshot (this container): ~2.7 s cold end to end on the first run, 4.3–6 s after a container restart (cold database caches; the bound asserted is 8 s), ~0.5 s cached; the plan asked < 2 s / < 100 ms. A 50 k snapshot is ~25 MB of JSON.
  Cold went from 11.5 s to 2.7 s by loading ids once into a temp table, joining instead of per-row subqueries, grouped enrichment queries,
  sampling the Pydantic check and bypassing `jsonable_encoder`.

## The shared Gantt (plan 15 R6.4)
`apps/presentation/src/gantt.js` is the only Gantt implementation: `layoutGantt({rows, deps, milestones, today, zoom})` (pure pixels: time axis ticks, today line,
bars with progress, baseline ghost and slip, milestone diamonds, elbow dependency arrows, `day|week|month|fit` zoom), `ganttSvg(model)` (escaped SVG string, `rm-*` classes in
`gantt.css`), `ganttTable` (the accessible twin) and `snapshotRows(D, items, {rows})`. The Roadmap slide draws with it. It is published as
`@tt/presentation/gantt` (types in `gantt.d.ts`, styles `@tt/presentation/gantt.css`) and as `dist/gantt.mjs` from `make presentation`.

To switch the web console (`apps/web`, not done here): add `"@tt/presentation": "workspace:*"` to its dependencies; in `pages/views/Gantt.tsx` replace
`layoutGantt` from `lib/gantt.ts` with the import (the model is a superset: same `bars/ticks/todayX/milestones/arrows`; rows keep the API's `GanttRow` shape, `dependencies`
map to `deps`, `rows=epic|item|milestone` stays a server query), render `ganttSvg(model, {label})` in a `dangerouslySetInnerHTML` region (output is escaped) or port the
10-line mapping to JSX, import `gantt.css`, and delete `lib/gantt.ts` (keep its tests as a contract by pointing them at the shared module). `zoom: "fit"` is new; the web
keeps `day|week|month`. The existing `.vgantt*` styles can go.
