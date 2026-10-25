# Built-in policy templates

_Generated from `packages/cel_policies/src/tt_cel/templates/builtin/*.yaml` by `tools/gen_governance_docs.py`._

Tier 1 of the policy design: a manager picks a template and fills in its parameters; the template compiles to CEL (tier 2) which is
evaluated against the context schema (see `context-schema-v1.md`). Raw CEL needs `policy.admin`. Parameter syntax inside a template:
`{name}` (typed literal), `{name|expr}` (fall back to an expression when unset), `<<?name ... ?>>` (text only when set).

## `development_hours_below` — Little time on development

A person logged fewer hours than planned on #development work (training, courses, reading) in the last 7 days. Positive-habit nudge, wellbeing category: visible to the person and their line manager only.

* category `wellbeing`, severity `info`, target `person`, trigger `scheduled` (cron `0 8 * * 1`)

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `hours` | int | 2 | 1..40 | Hours per week on development work |

```cel
metrics.development_minutes(person.id, "7d") < 60 * {hours}
```

Violation text: `{metrics.development_minutes(person.id, "7d")} min on development in 7 days (target {hours} h)` — e.g. _35 min on development in 7 days (target 2 h)_

## `evidence_gap` — No evidence while active

An active item has no evidence at all, or (with `days`) none in the last N working days.

* category `quality`, severity `info`, target `work_item`, trigger `scheduled` (cron `30 6 * * *`)
* candidate filter: `{'state': {'category': ['active']}}`

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `days` | int | — | 1..90 | Working days without evidence; empty = never any evidence |

```cel
item.state.category == "active" && (item.evidence_count() == 0<<?days || item.evidence_last_at() < now - business_days({days})?>>)
```

Violation text: `no evidence while active` — e.g. _no evidence while active_

## `late_daily_log` — Daily log not confirmed

A draft daily log has been waiting longer than 36 hours.

* category `collaboration`, severity `info`, target `daily_log`, trigger `scheduled` (cron `0 9 * * *`)

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `hours` | int | 36 | 1..720 | Hours a draft may wait |

```cel
daily_log.status == "draft" && now - daily_log.date > duration(string({hours}) + "h")
```

Violation text: `draft since {daily_log.date}` — e.g. _draft since 2026-10-21_

## `long_session` — Very long work session

An activity has been running longer than N hours without a pause. Wellbeing-sensitive.

* category `wellbeing`, severity `info`, target `activity`, trigger `scheduled` (cron `0 * * * *`)

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `hours` | int | 4 | 1..24 | Hours |

```cel
activity.status == "active" && now - activity.started_at > duration(string({hours}) + "h")
```

Violation text: `running for more than {hours} h` — e.g. _running for more than 4 h_

## `missing_required_dimension` — Required dimension missing

A dimension that is required for the item's stage has no confirmed value.

* category `compliance`, severity `warning`, target `work_item`, trigger `on_event`, events `item.stage_changed, item.created, classification.confirmed`

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| — | | | | no parameters |

```cel
item.missing_required_dimensions().size() > 0
```

Violation text: `missing {item.missing_required_dimensions()}` — e.g. _missing [product]_

## `no_estimate_weighted` — Weighted item started with no estimate

An active item whose weight is above the threshold has no estimate.

* category `planning`, severity `warning`, target `work_item`, trigger `on_event`, events `activity.started, item.state_changed, item.created`
* candidate filter: `{'state': {'category': ['active']}}`

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `min_weight` | number | 1 | 0..100 | Weight above which an estimate is expected |

```cel
item.state.category == "active" && item.estimate_value == null && item.weight > {min_weight}
```

Violation text: `weight {item.weight}, no estimate` — e.g. _weight 2, no estimate_

## `overrun` — Finished after the planned end

A finished item's actual end is later than its planned end (optionally by more than N hours).

* category `time`, severity `violation`, target `work_item`, trigger `on_event`, events `item.finished, item.state_changed`
* candidate filter: `{'state': {'category': ['done']}}`

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `hours` | int | — | 0..8760 | Tolerance in hours; empty = any lateness |

```cel
item.state.category == "done" && item.actual_end != null && item.planned_end != null && item.actual_end > item.planned_end<<?hours && item.actual_end - item.planned_end > duration(string({hours}) + "h")?>>
```

Violation text: `finished {item.actual_end} vs plan {item.planned_end}` — e.g. _finished 2026-10-25 vs plan 2026-10-22_

## `overwork` — Too much time logged in a day

A person logged more than the allowed minutes today. Wellbeing-sensitive: visible to the person and their line manager only.

* category `wellbeing`, severity `warning`, target `person`, trigger `scheduled` (cron `0 20 * * *`)

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `minutes` | int | 600 | 60..1440 | Minutes per day |

```cel
metrics.minutes_logged(person.id, "today") > {minutes}
```

Violation text: `{metrics.minutes_logged(person.id, "today")} min logged today (limit {minutes})` — e.g. _640 min logged today (limit 600)_

## `stale_inbox_item` — Provisional item left unresolved

A provisional (inbox) item has waited longer than N days for a project.

* category `process`, severity `info`, target `work_item`, trigger `scheduled` (cron `0 7 * * *`)
* candidate filter: `{'include_inbox': True}`

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `days` | int | 7 | 1..365 | Days an item may stay provisional |

```cel
item.is_provisional && now - item.created_at > duration(string({days} * 24) + "h")
```

Violation text: `provisional for more than {days} days` — e.g. _provisional for more than 7 days_

## `stuck_in_stage` — Item stuck in a stage

An item has been in its current stage longer than allowed. Defaults to the stage's own expected duration.

* category `process`, severity `warning`, target `stage_placement`, trigger `scheduled` (cron `0 * * * *`)
* candidate filter: `{'state': {'category': ['open', 'active']}}`

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `days` | int | — | 1..365 | Allowed days in stage; empty = the stage's expected duration |

```cel
placement.exited_at == null && stage.expected_duration_days > 0 && item.days_in_stage() > {days|stage.expected_duration_days}
```

Violation text: `{item.stage.key} for {item.days_in_stage()}d (expected ≤ {stage.expected_duration_days})` — e.g. _Build for 12d (expected ≤ 10)_

## `unclassified_in_stage` — Unclassified work in a stage

An item in the given stage has no value for the given dimension.

* category `compliance`, severity `warning`, target `work_item`, trigger `on_event`, events `item.stage_changed, classification.confirmed`

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `stage` | string | production |  | Stage key |
| `dimension` | ident | product |  | Dimension key |

```cel
item.stage.key == {stage} && !has(item.classification.{dimension})
```

Violation text: `no {dimension} in {stage}` — e.g. _no product in production_

## `wip_breach` — WIP above the limit

More items sit in a stage than its WIP limit (or the `limit` parameter). One violation per project and stage.

* category `process`, severity `violation`, target `stage_placement`, trigger `on_event`, events `item.stage_changed, stage.moved`, one violation per `project_stage`
* candidate filter: `{'state': {'category': ['open', 'active']}}`

| parameter | type | default | bounds | meaning |
|---|---|---|---|---|
| `limit` | int | — | 1..1000 | Limit; empty = the stage's own WIP limit |

```cel
{limit|stage.wip_limit} != null && stage.wip_count() > {limit|stage.wip_limit}
```

Violation text: `WIP {stage.wip_count()} > limit {limit|stage.wip_limit}` — e.g. _WIP 6 > limit 4_
