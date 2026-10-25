# External-LLM generation pack, import and "Generate with AI"

Three ways to get an AI-drafted deck, all ending in the same strict validation as a person typing the deck by hand.

| Path | Endpoint | Where the model runs |
| --- | --- | --- |
| In product | `POST /presentations:author` `{brief, scope, range_days}` | The LLM gateway (`/v1/presentation/author`), under its privacy mode and budget |
| Pack out | `POST /presentations:generation-pack` | Anywhere you choose (ChatGPT, Gemini, a laptop model) |
| Pack in | `POST /presentations:import` | n/a - validation only |

Template drafts: the gateway also serves `POST /v1/presentation/template-author`; the result is validated with
`POST /presentation-templates:validate` before it is saved.

## Pack layout (zip, or `format=json`)

```
manifest.json            pack_id, generated_for, scope, range_days, redaction, snapshot_sha256, files, notice
data/snapshot.json       the viewer's own snapshot, redacted (no wellbeing, habits or live sessions, ever)
context/slide-kinds.json the only kinds allowed, with config schema, `requires` and `render_empty`
template/deck.schema.json        shape of the answer
template/template.schema.json    shape of a template
template/examples/*.json         shipped templates, for style
prompt/author.md         instructions + the brief
```

Same inputs, same bytes (fixed zip timestamps; the manifest alone carries the time).

## Switches and levels

* `workspace.settings.external_packs` - default **off**. While off, `generation-pack` answers 403 `external_packs_disabled`.
  Import and in-product authoring are unaffected (import accepts text only; it exports nothing).
* `workspace.settings.external_pack_redaction` - `standard` (default) or `strict` (people pseudonymised as `Person N`).
  A request may pick either level.
* Permission: `view.share`. Each pack emits `presentation.pack_generated` (scope, range, redaction, file count).

## Import rules

* Strict by default: all problems return 422 `invalid_deck` with `details.errors[] = {pointer, message}`; nothing is saved.
* `lenient: true` drops failing slides with a warning each; fails only if none survive. `dry_run: true` validates only.
* Unknown slide kinds, config errors, off-origin image URLs and more than 60 slides are rejected. A `project_key` the importer
  cannot read is treated like an unknown one.
* Titles, notes and markdown are stripped of `<script>`, `<iframe>`, `on*=` attributes and `javascript:`/`data:` links.
* Provenance is stored on the deck: `source = {kind: "external_llm", pack_id, imported_at, lenient}`, or
  `{kind: "gateway_llm", run_id, model, brief}` for in-product drafts.
