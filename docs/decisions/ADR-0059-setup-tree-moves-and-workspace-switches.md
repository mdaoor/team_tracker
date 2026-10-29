# ADR-0059: Setup tree moves stay inside a project; product links can be removed; two more workspace switches
Date: 2026-10-28   Status: accepted (web pass gaps, branch leftovers/server-gaps; amends the item move rules of plan P1 §1.2 and the product links of P3)

## Context
The Setup tree (product > project > epic) could only move products. A project could not leave a product (no `DELETE` on a product link), and
`POST /items/{key}/move` (the `item.reparent` command) accepted a parent from **another project** without complaint: only `parent_id` and `path` changed, so the
item stayed in its old project while hanging under a foreign tree. `external_packs` could not be set at all, and `allow_same_level_nesting` (needed to put
an epic under an epic) had no switch either.

## Decision
1. **`DELETE /products/{ref}/links/{project}`** (permission `product.admin`, 204): soft-deletes the live link between the product and the project (any role),
   emits `product_link.removed` (covered by the `product_link.*` catalogue entry), 404 when the product, the project or the link is unknown. Removing the
   *delivering* link also soft-deletes the `product` classifications that the project and its items inherited from it (source `inherited`; manual ones
   stay). Moving a project to another product is therefore `DELETE` + `POST /products/{new}/links` (role and weight carried over by the client); the delivering
   slot is free again after the delete.
2. **Item moves never cross a project.** A move whose new parent belongs to another project answers 400 `cross_project_move`. Re-keying was rejected: the key
   (`WEB-120`) is referenced by Jira links, evidence, saved filters, presentations and the CLI; the state is a row of the project's workflow, iterations and
   milestones belong to a project, and capture rows carry `project_id`. A cross-project move would need a key alias table and a state mapping, so it stays out
   until a product need appears (`item.merge` with `allow_cross_project` remains the supported way to fold work into another project). Because of this no extra
   `project.write` check on a second project is needed: the existing `item.write_overlay` on the item's own project covers the move.
3. **`external_packs: bool`** (workspace admin) joins `external_share*` in `PATCH/GET /settings`; the generation-pack endpoint already honours it (403
   `external_packs_disabled` while off). **`allow_same_level_nesting: bool`** (workspace admin) is added the same way so an epic can be nested under an epic from
   the Setup tree; default off, as before. One `workspace.settings_changed` event per change.

## Consequences
* The Setup tree moves projects between products (unlink then link; if the link step fails the client re-links the old product) and epics inside their project
  (top level, or under another epic when same-level nesting is on). Dropping an epic on another project is refused client-side with the reason above.
* Anyone who relied on the old silent cross-project parent now gets a 400; no shipped client did.
