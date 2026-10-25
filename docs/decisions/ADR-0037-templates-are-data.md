# ADR-0037: Presentation templates are data; the built-ins ship as JSON files
Date: 2026-10-24   Status: accepted (supersedes the "templates as Python constants" deviation noted in the 2026-10-24 review)

## Decision
Templates are rows (`presentation_templates`) holding a versioned JSON body validated by one schema. Built-in templates live in
`apps/presentation/templates/*.json` and are upserted by `(key, version)`. Slide *kinds* remain code and publish their config schemas.
Instantiating a template copies its slides; the deck only records `template_id`/`template_version`.

## Consequences
A new report format never needs a deploy. The shipped files are the single source for the gallery, the schema docs and the external-LLM pack.
Editing a built-in file requires bumping `version` (the loader refuses silent drift only by overwriting the same version's body, which is logged by the row's `updated_at`).
`Presentation.is_template` is no longer used (kept for old rows).
