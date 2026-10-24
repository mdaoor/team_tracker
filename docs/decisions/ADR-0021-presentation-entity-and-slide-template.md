# ADR-0021: Presentation entity and the generic slide template
Date: 2026-10-24   Status: accepted

## Decision
A `Presentation` is `{owner, title, scope (kind:ref), range_days, weight_scheme, theme, slides[], is_shared, share_token?}` stored in one
row; `slides` is a JSONB array validated per kind on every write (`domain/presentations.py`), each slide
`{id, kind, title?, subtitle?, config, notes?, layout, live, position}`. Reasons: a deck is always read and written whole, reordering is a
list operation, and a slide has no identity outside its deck. There is no `slides` table to keep in order.

A slide is drawn by one template: header (title, subtitle, scope chip, as-of) + body (a renderer's HTML) + footer (stamp, page n/N). The
same renderer draws a deck slide, a page-mode section and a Views embed. `view`, `chart` and `table` slides carry a frozen query
(`config`) and re-run it with the *viewer's* ACL when `live` (the data endpoint always does, per viewer: nothing is cached across people).

## Rules
* Snapshot cache rows are per viewer (`person_id` is part of the key); a snapshot is never shared between people.
* A slide whose data is hidden from the viewer (wellbeing habits, someone else's private view) is marked `restricted` and drawn as such.
* Events use entity type `saved_view` (`presentation.created|updated|deleted|slide_added|rendered|shared`): a presentation is a saved,
  shareable bundle of views, so no new `TargetType` (which would break parity with the object model).
* External sharing is a random `pt_...` token, off unless the workspace sets `external_share`; it renders with the *owner's* visibility
  and expires (default 14 days). Only `view.share` holders can create it.
* Templates live in code (`TEMPLATES`), not as `is_template` rows: nothing to seed or migrate per workspace; `is_template` stays for later.

## Consequences
Concurrent edits of one deck last-write-wins per slide patch (no merge). A deck with hundreds of slides would want a table; the
validators cap nothing yet. Exports (PDF/PNG) need Chromium on the server; PPTX and CSV do not.
