# UAT script

Run with the seeded demo workspace `acme` (see `docs/uat/README.md`). Sign in as **@mahmoud** (admin) unless a step says otherwise.
Record Pass/Fail and notes in the table at the end. "Expected" is what the implementation is tested to do; anything else is a finding.

## 1. Capture in one line (reporter)
1. CLI: `tt say "fixed the cookie parser WEB-111, took 90m #bug plan tomorrow 5pm"`.
2. **Expected:** a preview card with rows Task / Project / Time (1 h 30 min) / Plan / Tags, then `✓ WEB-111 ...`. `tt today` lists it.
3. Offline check: stop the API, run `tt say ...`: **expected** "offline - queued"; start the API, `tt sync`: delivered exactly once.

## 2. Review queue (manager)
1. Web -> **Review queue**. Items the system could not place (the demo has several from `@youssef`/`@nour`) appear with suggestions and confidence cues.
2. Resolve one with a suggestion, ignore another. **Expected:** the row disappears, the item shows the chosen project/tags with provenance (`manual`).
3. As `@sara` (contributor) open `/w/acme/review`. **Expected:** only what her role allows; no access to others' Inbox.

## 3. Views
Web -> **Views**: list, board, pipeline, matrix, gantt, calendar, now. Add a filter chip (project WEB, priority >= high), switch layouts, save the view,
reload. **Expected:** state survives reload (URL carries it); the matrix numbers equal the list's counts.

## 4. Top-down project plan (ERP)
1. Web -> **Plans** -> ERP. **Expected:** phases/deliverables as bars, health pill, baseline name.
2. Change a deliverable's end date. **Expected:** saved on blur; an end before the start is refused inline; the status/variance updates after reload.
3. Capture a task that belongs to a deliverable: `tt say "new task: draft the data migration plan for the ERP"`. **Expected:** it links under the best
   matching deliverable or appears in the review queue as "unlinked task" with the top three candidates.
4. Click **Present this plan**. **Expected:** a deck opens in the builder (overview, roadmap, milestones, deliverables, plan vs actual, phases).

## 5. Presentations
1. **Present** -> start from the *Portfolio review* template. Reorder slides, edit a title, add a slide via **Add to presentation** from any Views layout.
2. Present (full screen), export **PPTX**, **PDF**, **PNG**. **Expected:** numbers equal the screen; PDF/PNG need Chromium installed.
3. Share link (needs *External sharing* enabled in settings): open it in a private window. **Expected:** no habits, no live sessions, no wellbeing data.
4. **Templates** tab: duplicate a built-in template, add a slide, see inline JSON-pointer errors for a bad kind, save, instantiate with parameters.

## 6. Generate with AI (needs a gateway) and external packs
1. **Present** -> *Generate with AI*: "Quarterly steering review of the ERP rollout". **Expected:** a draft deck using only known slide kinds and project keys.
2. Without the switch, **Download pack** answers "not allowed" (403 `external_packs_disabled`). An admin then enables it: `PATCH /api/v1/w/<ws>/settings` with `{"external_packs": true}`
   (workspace admins only; `GET .../settings` shows the value; the change is one `workspace.settings_changed` event; `{"external_packs": false}` switches it off again). **Download pack** -> a zip with manifest, snapshot, slide-kinds,
   schemas, prompt. Give it to any external model, paste its JSON into **Import a deck an external LLM wrote**. **Expected:** invalid slides are dropped with
   warnings (lenient) and the deck records its provenance. Back to step 2 with the switch off: the download is refused again.

## 7. Policies and violations (manager)
1. **Policies** -> new policy from template *Item stuck in a stage*; set scope, candidate filter, notify, automatic action; **Create**.
2. **Run dry run** (nothing is written), then **Activate**. **Expected:** activation is blocked until a dry run after the last edit.
3. **Violations**: acknowledge, resolve, waive (reason required). **Expected:** a notification arrives for the right people (bell).
4. Edit the policy and open **Version history**; restore an older version. **Expected:** history is append-only; restore deactivates until re-tested.

## 8. Habits and wellbeing privacy
**Habits** shows per-person rows with 8-week sparklines (numbers in the tooltip/label). As `@sara` you see your own habits; a wellbeing habit is visible only to
her and her line managers. As `@mahmoud` **Export for 1:1** writes a CSV stamped with who/when.

## 9. Weights, performance, digest, insights
Open each page, change a weight scheme (preview first), check **Performance** series vs **Violations**, mark a daily log reviewed in **Digest**. **Add to presentation**
exists on all of them.

## 10. Items: comments, checklists, links, custom fields, attachments
(API; the item drawer shows the results.) `POST /items/{key}/comments`, checklists, `links` (a cycle is refused with 409), custom fields (typed values), upload a PNG and a
`.html` (the HTML downloads, never renders). See `/docs`.

## 11. Recurring work
Create a rule on a template item (`POST /projects/WEB/recurrence-rules`, `FREQ=WEEKLY;BYDAY=MO`). **Expected:** the worker creates one item per occurrence, once.

## 12. Access control spot checks
As `@ali` (no roles) open a project he is not on: **expected 404**, not 403. Try the same API call with a capture-scoped token against a write endpoint: **expected 403**.

## 13. Privacy and retention (admin)
`GET /me/export` (own data), `GET /people/@sara/export` (no Inbox/wellbeing), `POST /people/@x:erase` with the handle typed again, `POST /retention:purge-raw-input`.

## 14. Operations
`/readyz`, `/metrics` (with `TT_METRICS_TOKEN`), rate limit (hammer `POST /capture/batches`: **expected 429 + Retry-After**), certificate reload (`kill -HUP`).

## 15. Management commands from the client (Phase 14)
Sign in as `@mahmoud` (admin) and, with a `manage`-scoped token (`"scopes":["manage"]`), log the CLI in with it.
- [ ] `tt role manager`, then `tt ops` lists the catalogue (51 ops); `tt role` shows only the modes the person has (`@ali` sees only reporter).
- [ ] `tt do "create project Payments PAY"` shows an **Effect preview**, asks once, then creates PAY; the web console shows it, and the event log has `payload.command`.
- [ ] `tt do "new epic Checkout redesign under PAY"`, `tt cmd team.create --arg name=payments --arg members=@sara,@ali`, tag ten items (`item.tag`).
- [ ] `tt do "merge WEB-101 and WEB-105 into one"`: preview lists minutes/evidence moved; `--yes` alone does **not** confirm a destructive op, a second confirmation (or `--force`) does; the source disappears, totals are unchanged.
- [ ] A reporter (`@ali`) running `tt cmd project.create ...` gets a readable "missing permission" rejection and nothing is written.
- [ ] A `capture`-scoped token cannot send commands (`403 insufficient_scope`).
- [ ] `tt queue` resolves review rows; `tt team now` shows the active work of the people you manage; `tt approve` / `tt waive` work.

## 16. Personal time tracker (Phase 15)
See `docs/uat/uat-personal-tracker.md`.

## Sign-off
| # | Scenario | Pass / Fail | Notes | Tester | Date |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |
| 6 | | | | | |
| 7 | | | | | |
| 8 | | | | | |
| 9 | | | | | |
| 10 | | | | | |
| 11 | | | | | |
| 12 | | | | | |
| 13 | | | | | |
| 14 | | | | | |
