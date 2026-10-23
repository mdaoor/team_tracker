# Team Tracker — UX Design (v0.1)

Companion to `team_tracker_object_model.md` (v0.4) and `team_tracker_models.py`. This document covers the three user-facing surfaces and the flows between them. It is written from a UX-lead perspective: what the user sees, what they type, what happens, what they never have to think about.

---

## 0. Who uses it and what they want

| persona | goal | tolerance | success metric |
|---|---|---|---|
| **Reporter** (engineer, analyst, designer) | Say what I'm doing and move on | < 10 seconds per log; zero forms | ≥ 1 log/day for 90 % of people without reminders |
| **Manager** (PM, tech lead, line manager) | Keep the picture complete without chasing people | 15 min/week of upkeep | ≥ 85 % of items auto-classified correctly; < 5 "unclassified" items/week |
| **Viewer** (senior manager, stakeholder, team) | See the state of things at any zoom level | Zero setup | Can answer "what's shipping next week" in one click |

The central UX law: **the reporter's effort is constant (one line); the manager's effort is front-loaded (context); the system does the join.**

---

## 1. Surface map

```
┌────────────────────────┐    ContextPack (read-only)    ┌────────────────────────────┐
│ Reporter client        │ ◄──────────────────────────── │ Server                     │
│ (desktop tray / mobile │                               │  canonical store + rules    │
│  / CLI / Slack)        │ ──── ClientBatch (JSON) ────► │  + Jira sync                │
│ free text → local LLM  │                               └────────────┬───────────────┘
└────────────────────────┘                                            │
                                                                       │ query API
┌────────────────────────┐                                            ▼
│ Manager console (web)  │ ── writes ContextPack ───►   ┌────────────────────────────┐
│ context, dimensions,   │                              │ Views & presentation (web) │
│ review queue, policies │                              │ boards · gantt · summary   │
└────────────────────────┘                              │ drill-down · exports       │
                                                        └────────────────────────────┘
```

---

## 2. Reporter client

### 2.1 Form factors (same core, different shells)
1. **Desktop quick-capture** — global hotkey opens a single-line palette (Spotlight/Raycast style). Primary.
2. **Mobile** — one text field, voice-to-text, same palette logic.
3. **CLI** — `tt doing "fix login" -p web #backend`; same parser, no LLM required for structured input.
4. **Chat bot** (Slack/Teams) — DM the bot; replies with the parsed card.

### 2.2 The capture palette

```
┌──────────────────────────────────────────────────────────────────┐
│ ▶  fix the login redirect loop, took 2h, plan to finish tomorrow │
│                                                                  │
│   ↳ Task      fix the login redirect loop            WEB-142 ✓   │
│   ↳ Project   Website (WEB)                          92 %        │
│   ↳ Time      2 h logged today                                   │
│   ↳ Plan      ends Sat 24 Oct 17:00                              │
│   ↳ Tags      #auth  #bug                            suggested   │
│   ↳ Stage     build → test ?                         ask         │
│                                                                  │
│   Enter = send   Tab = edit a row   Esc = discard   ⌘J = Jira    │
└──────────────────────────────────────────────────────────────────┘
```

Behaviour:
- **Type naturally.** No syntax required. Syntax (`#tag`, `@person`, `p:project`, `!high`) is accepted but never necessary.
- **Live parse.** As you type, the local LLM produces a `ClientMessage`; the preview rows update with ~300 ms debounce. Each row shows a confidence cue: ✓ matched existing, % fuzzy score, "suggested", or "ask".
- **One keystroke to send.** Enter sends. The preview is informational; the reporter does *not* have to look at it.
- **Tab to correct.** Tab cycles through rows; typing on a row overrides that field. Overrides are fed back as training examples for the local model (opt-in).
- **"Ask" rows never block.** If the LLM is unsure (stage, parent, priority), the row says "ask" and the message is still sent; the server marks the field `is_confirmed=false` and it lands in the manager's review queue, not the reporter's.
- **Multi-item lines.** "doing 1. X, 2. Y, 3. Z" → three `ClientMessage`s in one `ClientBatch`; preview shows three stacked cards.
- **Running timer.** After a `start`, the tray icon shows the active item and elapsed time. Clicking offers `pause · done · switch`. Starting a new item auto-pauses the previous one (model rule). No separate "timer" UI.
- **Offline-first.** Batches queue locally; sync on reconnect; `client_id` guarantees idempotency.

### 2.3 Verbs the reporter can use (all optional, all inferable from plain English)

| intent | examples the LLM maps | → `action` |
|---|---|---|
| start | "doing X", "on X now", "started X" | `start` |
| finish | "done with X", "finished X", "shipped X" | `done` |
| pause / switch | "parking X", "switching to Y" | `pause` + `start` |
| blocked | "stuck on X waiting for Y", "blocked by DB access" | `blocked` (note = reason) |
| time | "took 2h", "spent 45 min on X yesterday" | `log_time` |
| plan | "should be done by Friday", "ETA tomorrow 5pm" | `plan` (`planned_end`) |
| estimate | "probably 3 days", "about 5 points" | `estimate` |
| evidence | paste a URL / PR link / screenshot | `evidence` |
| note | anything else | `note` |
| create | "new task: …", "add bug: …" | `create` |

### 2.4 The local LLM translator

Runs on-device (or on a team-local inference box). Input: raw text + **ContextPack**. Output: `ClientBatch` JSON conforming to `to_json_schema(ClientMessage)` via constrained decoding.

**ContextPack** (what the server publishes to each reporter; refreshed on login and every 15 min; ~5–30 KB):
```json
{
  "actor": "@mahmoud",
  "workspace": "acme",
  "generated_at": "2026-10-23T18:00:00Z",
  "projects":   [{"key":"WEB","name":"Website","aliases":["site","web"],"process":"sw-delivery","default_stage":"build","jira":"WEB"}],
  "my_open_items": [{"key":"WEB-142","title":"fix the login redirect loop","stage":"build","project":"WEB"}],
  "people":     [{"handle":"@sara","name":"Sara H.","team":"squad-b"}],
  "tags":       ["auth","bug","backend","tech-debt"],
  "milestones": [{"name":"Beta release","project":"WEB","due":"2026-11-10"}],
  "iterations": [{"name":"Sprint 14","project":"WEB","state":"active"}],
  "stages":     {"sw-delivery": ["idea","discovery","design","build","test","staging","production"]},
  "priorities": ["lowest","low","medium","high","highest","critical"],
  "risks":      [{"key":"WEB-R7","title":"OAuth provider deprecation","project":"WEB"}],
  "manager_rules": [
    "Anything mentioning 'prod incident' → priority critical, tag #incident",
    "Items in project DATA default to stage 'discovery' unless a PR link is present"
  ],
  "glossary":   {"the pipeline": "DATA project, ingestion component"}
}
```

Translator prompt skeleton (system):
1. You convert one chat line into `ClientBatch` JSON. Output JSON only.
2. Prefer matching `my_open_items` by meaning over creating new items; put the match in `item.key` and set `llm_confidence.item`.
3. Resolve project via `projects.aliases`, else `glossary`, else leave null (server → Inbox).
4. Apply `manager_rules` literally.
5. Never invent people, milestones, or risks not in the pack.
6. Set `llm_confidence` per filled overlay field; anything < 0.7 stays null.

Model sizing: 3–8 B instruct model with JSON-schema constrained decoding is sufficient; latency target < 400 ms on a laptop GPU / < 1.5 s CPU. Fallback: regex parser for the syntax forms when no model is available.

### 2.5 Jira hand-off (reporter side)
- If the resolved project has `external_refs.jira`, the preview shows a Jira chip. `⌘J` toggles "also send to Jira" (default from project setting).
- Server does the Jira write (never the client) so credentials stay central and the Jira key comes back as `WorkItem.external_refs.jira`.
- Mapping (server, configurable per project): `title→summary`, `description→description`, `item_type→issuetype`, `priority→priority`, `assignee→assignee`, `planned_end→duedate`, `estimate→timeoriginalestimate`, `tags→labels`, `iteration→sprint`, `stage→custom field "Delivery Stage"`, time logs → worklog, `done` → transition to the project's mapped done status.
- Jira is a **projection**, not a second source of truth: Jira webhooks feed back only `status`, `assignee`, `comments` as `Event{source: import}`.

### 2.6 Micro-interactions that matter
- **Empty state:** "What are you working on?" with three ghost examples that cycle.
- **First-run:** three sample lines the user can press to see the parse — teaches without a tutorial.
- **Failure:** LLM unavailable → palette still works; rows show "will be sorted by your manager"; nothing is lost.
- **End of day:** a single notification: "Confirm today's log?" opens the `DailyLog` draft with one Confirm button and an editable summary. Dismissable; never nags twice.

---

## 3. Manager console

Goal: make the ContextPack rich enough that reporters' lines land fully classified, then keep the model clean with a short review queue.

### 3.1 Information architecture
```
Console
├── Setup          (one-time, per project/product)
│   ├── Projects & products        tree editor, Jira link, default process
│   ├── Process & stages           pipeline editor, expected durations, WIP limits
│   ├── Dimensions                 add custom dimensions / values
│   ├── People & teams             org / virtual teams, reporting lines, capacity
│   ├── Milestones & iterations    calendar editor
│   ├── Risks                      register with p×i matrix, attach to projects/items
│   ├── Tags & glossary            aliases that help the LLM
│   └── Rules for the translator   plain-English rules → ContextPack.manager_rules
├── Review queue   (daily, 5 min)
├── Policies       (templates + CEL, dry-run)
└── Weights        (schemes)
```

### 3.2 Review queue — the manager's one daily screen
Everything the LLM or a reporter could not settle lands here, as a flat list with inline actions:

```
Unclassified / unconfirmed (12)                           [Bulk: assign project ▾] [Confirm all suggested]
──────────────────────────────────────────────────────────────────────────────────────────────────
● "refactor the ingestion retry"   @ali   today 10:12   Inbox → suggested: DATA (0.81)   [DATA ✓] [other…]
● "stage?"  WEB-142  fix the login redirect loop        build → test? (0.62)             [test ✓] [keep build]
● Provisional tag  #retry-logic  used 3×                 merge into → #resilience?         [merge] [keep]
● No estimate  WEB-150  (weight 3)                        asked @sara 1 day ago            [set 4h] [waive]
● Risk?  "oauth provider sunsets v1 in Dec" mentioned by @sara   → link WEB-R7?            [link] [new risk] [ignore]
```

Rules:
- Each row is resolvable with one click; the default button is the LLM's suggestion.
- Confirming writes `Classification{is_confirmed:true, confirmed_by}`; the pair (raw text → chosen value) is appended to that project's glossary suggestions so the next line parses correctly.
- Queue is scoped by the manager's `ReportingLine` + `ProjectMember` rows; two managers see different queues; the same item never appears twice.

### 3.3 Context editors — design notes
- **Projects & products**: a left tree (product → project → epic) with drag-to-reparent; right pane shows fields. Adding a project asks only `name`, `key`, `process`; everything else has defaults.
- **Process editor**: horizontal stage chips; drag to reorder; each chip opens expected-duration, WIP limit, approval toggle, `maps_to_state_category`. A live mini-pipeline below shows how current items would distribute.
- **Dimension editor**: "Add dimension" → name, tree/flat, single/multi, applies-to. Values are added inline; bulk paste from a CSV column.
- **Risks**: register table + 5×5 heat-map; dragging a risk card onto a project/epic in the tree creates a `RiskLink`.
- **Rules for the translator**: free-text list; each saved rule is also dry-run against the last 200 reporter lines to show "would have changed N parses".
- **Priority / weight on bulk**: multi-select items in any view → set priority / impact / weight; propagation preview ("will affect 14 children; 2 have manual overrides and will be kept").

### 3.4 Many managers, no collisions
- Edits are per-row `Event{source: overlay}`; two managers editing different dimensions of one item never conflict.
- Same field edited by two managers → last write wins, both notified with a diff and a one-click revert.
- Every overlay value shows a small provenance chip on hover: `set by @lead-b · 2d ago · inherited from Product X`.

---

## 4. Views

All views are `SavedView` instances: same filter grammar, different `layout`. A view is a URL; URLs are shareable and respect ACL.

### 4.1 Global controls (persistent top bar)
`[Scope ▾ workspace / product / project / team / person]  [Dimension filters ▾]  [Date range]  [Layout: list · board · pipeline · gantt · matrix · calendar · summary]  [Weight scheme ▾]  [Save view]`

Dimension filters are chips; adding one opens a typeahead over `DimensionValue`s. Every chip can be negated. "Group by" is a dimension too.

### 4.2 Layouts

| layout | columns / axes | best for |
|---|---|---|
| **List / table** | configurable columns, inline edit for managers | triage, bulk edits |
| **Board** | workflow states of one project | sprint work |
| **Pipeline** | stages of a `Process`, items from any project | the generic agile board; cross-team delivery |
| **Matrix** | any dimension × any dimension (e.g. team × stage, person × priority) | load and bottlenecks |
| **Gantt** | time × items; bars = planned, overlay = actual, baseline ghost, dependency arrows | waterfall, releases |
| **Calendar** | due / planned_end / milestones | next-week delivery |
| **Now** | one card per active `Activity` | who is doing what right now |
| **Summary** | narrative + KPIs per project / epic / product | the presentation (§5) |

Card anatomy (shared by board, pipeline, matrix):
```
┌──────────────────────────────┐
│ WEB-142  ● high   ⚖ 3  ⚠ R7  │   key · priority dot · weight · risk chip
│ Fix the login redirect loop  │
│ @sara  · build 4d · ⏱ 6h    │   assignee · stage age · time logged
│ #auth #bug      ▸ Beta rel.  │   tags · milestone
└──────────────────────────────┘
```
Stage-age turns amber at `expected_duration_days`, red at 2×. Risk chip shows highest open `Risk.score`.

### 4.3 Drill-down model
Every number anywhere is a link to the list that produced it. Breadcrumb keeps the path: `Workspace › Product X › Project WEB › Epic WEB-120 › 14 items`. Backspace goes up. The filter chips update to match so the user can fork off at any level.

---

## 5. Presentation layer (the HTML/JS/CSS deliverable)

A single self-contained page (`team_tracker_presentation.html`) that reads a JSON snapshot from the server (`/api/snapshot?scope=…&range=…`) and renders:

1. **Header strip** — scope selector, date range, weight scheme, "as of" timestamp.
2. **KPI row** — open / active / done this period, hours logged, weighted throughput, open violations, risk exposure, on-time %. Each tile drills down.
3. **Projects & epics summary** — one card per project with an LLM-written 2–3 sentence summary (`Insight{kind:summary}`), progress bar, stage distribution sparkline, top risk, next milestone.
4. **Dimension explorer** — pick a primary dimension (person / team / project / product / stage / tag / priority) and optionally a secondary; renders a bar chart or a heat-map matrix of item counts, hours, or weighted throughput.
5. **Pipeline** — stage columns with counts; click a column to list.
6. **Delivery radar** — what is planned to finish in the next 7 / 14 days, grouped by day, with risk flags and the responsible person.
7. **Gantt** — epics and milestones for the selected range.
8. **Team activity** — per person: items touched, hours, habits (positive/negative) if viewer is permitted.
9. **Violations & habits** — open violations by category; habit trends.

Design rules: print-friendly (A4 landscape), keyboard navigable, every chart has a table toggle, no external network calls (embedded CSS/JS), dark/light by `prefers-color-scheme`.

---

## 6. Privacy in the UI
- Scope selector only lists scopes the viewer can read.
- Person-level pages show habits/wellbeing only to the subject and line managers; other viewers see the section collapsed with "restricted".
- Inbox items never appear in team views until moved.
- Every export (CSV/print) is stamped with viewer and scope.

---

## 7. Flow walk-throughs

### 7.1 Reporter, morning
1. Hotkey → "picking up the retry refactor on the pipeline, probably 2 days".
2. Local LLM: `start`, item fuzzy-matched to DATA-88 (0.78), project DATA via glossary "the pipeline", estimate 2 d.
3. Enter. Timer starts. Done in 6 seconds.
4. Server: pauses previous active activity, creates `Activity`, `Estimate`; stage inherited from DATA default (`discovery`); policies run; nothing fires.

### 7.2 Manager, mid-morning
1. Review queue shows "DATA-88 matched at 0.78 — confirm?" → one click.
2. Drags risk DATA-R3 onto the epic DATA-80; 9 items inherit the risk chip.
3. Sets weight 3 on DATA-88 via inline edit. Done in 90 seconds.

### 7.3 Senior manager, Friday
1. Opens the presentation at product scope, last 2 weeks.
2. Reads three project summaries; notices "Website" has 4 items stuck in `test`.
3. Clicks the pipeline `test` column → list → opens WEB-142 → sees stage age 9 d, risk R7, 2 open violations.
4. Switches dimension explorer to team × stage → sees squad-b carries all of `test`; forwards the view URL.

---

## 8. Build order (UX-driven)
1. Capture palette + local LLM + ContextPack (reporter value on day 1).
2. Review queue + project/process/dimension editors (manager value).
3. List, pipeline, now views.
4. Presentation page.
5. Gantt, matrix, calendar.
6. Policies UI, habits, insights.
7. Jira sync (can run in parallel from step 2 as it is server-only).
