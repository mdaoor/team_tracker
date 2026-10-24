# Presentation templates (data, not code)

A template is a JSON document (`docs/api/presentation-template-schema.json`). Built-ins ship as `apps/presentation/templates/*.json` and are upserted
by `(key, version)` the first time they are needed; workspace templates are rows owned by their author and private until shared.

```json
{ "schema_version": 1, "key": "weekly_status", "name": "Weekly status: ${project}",
  "params": [{"name": "project", "type": "project", "required": true}, {"name": "horizon", "type": "int", "default": 7}],
  "slides": [{"kind": "milestones", "title_template": "Milestones for ${project}", "config": {"project_key": "${project}"}},
             {"kind": "radar", "title_template": "Next ${horizon} days", "config": {"days": "${horizon}"}, "requires": "activity", "render_empty": "note"}],
  "post_rules": [{"when": "scope.items == 0", "action": "drop_slides", "kinds": ["radar"]}] }
```
* **Placeholders** `${name}`: a parameter or a variable (`scope.label|kind|ref|items|projects`, `project.key|name`, `range.days|from|to`, `viewer.handle`,
  `workspace.name`, `today`). A string that is only one placeholder keeps the value's type (`"days": "${horizon}"` becomes the number 3).
* **post_rules** drop slide kinds when the scope fails a simple test (`scope.items|projects|plan_items <op> <number>`), so a deck for an empty project
  does not carry empty activity slides.
* **Copy semantics.** Starting from a template copies the slides; editing or deleting the template later never changes decks made from it
  (`Presentation.template_id/template_version` are provenance only). Editing bumps the template's version.
* **Slide kinds are code**, templates are not: `GET /presentation/slide-kinds` lists every kind with its config JSON Schema, defaults and
  `requires`/`render_empty`, so a template author (or an external LLM) never reads source.

## API (all under `/api/v1/w/{ws}`)
`GET|POST /presentation-templates`, `GET|PUT|DELETE /presentation-templates/{key|id}`, `:validate` (errors carry JSON pointers), `{ref}:preview`
(`{scope, params}` or an unsaved `body`), `{ref}:export`, `:import`, `{ref}:duplicate`, `{ref}:share`, `POST /presentations/{id}:save-as-template`
(`params_to_extract` lifts concrete values such as a project key into parameters), `POST /presentations:from-template {template, scope, params}`.

Permissions: `view.save` to create/edit/import/duplicate, `view.share` to share; built-ins are read-only (duplicate first).

## UI
Present → **Templates**: gallery (built-in / mine / shared), Use (asks only for parameters), Duplicate/Edit, Export, Import, Share, Delete; the editor has a
form view (parameters, slide list with kind, title and config) and a JSON view, both validated 400 ms after the last keystroke with inline pointers.
