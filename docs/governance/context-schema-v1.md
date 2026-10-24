# Policy context schema v1

_Generated from `packages/cel_policies/src/tt_cel/schema.py` by `tools/gen_governance_docs.py`; do not edit by hand._

A policy condition is one [CEL](https://github.com/google/cel-spec) expression over these roots. It is type-checked when the
policy is saved: an unknown root, field or function, or a clear type mismatch (`item.priority > 3`), is rejected with the offending
field named. A policy stores `context_schema_version`, so a later v2 never changes how a v1 policy is checked or evaluated.

Dates the plan keeps as days (`planned_end`, `actual_end`, `due_date`, `date` of a daily log) are timestamps at UTC midnight; compare
them with each other, not with `now`. `x == null` / `x != null` never raise. Expressions are limited to 4 KB and 400 syntax nodes.

## Roots

| root | type | what it is |
|---|---|---|
| `item` | `Item` | the work item (also present for placement, activity and violation targets that have one) |
| `activity` | `Activity` | the work session being evaluated |
| `person` | `Person` | the accountable person (assignee, activity owner, log owner, or the person target) |
| `project` | `Project` | the item's project, or the project target |
| `stage` | `Stage` | the item's current stage (`stage.wip_count()` counts open items of this project in the stage) |
| `placement` | `Placement` | the item's current stage placement |
| `event` | `Event` | the event that triggered an `on_event` evaluation; blank for scheduled runs |
| `daily_log` | `DailyLog` | the daily log being evaluated |
| `metrics` | `Metrics` | namespace of aggregate functions (`metrics.minutes_logged(person.id, "today")`) |
| `now` | `timestamp` | the evaluation time (the `as_of` of a replay) |

## `Item`

| field | type |
|---|---|
| `id` | `string` |
| `key` | `string` |
| `title` | `string` |
| `project` | `Project` |
| `type` | `KeyOnly` |
| `state` | `State` |
| `priority` | `string` |
| `priority_source` | `string` |
| `weight` | `double` |
| `impact` | `int` |
| `estimate_value` | `double` |
| `estimate_unit` | `string` |
| `remaining_value` | `double` |
| `planned_start` | `timestamp` |
| `planned_end` | `timestamp` |
| `due_date` | `timestamp` |
| `actual_start` | `timestamp` |
| `actual_end` | `timestamp` |
| `assignee` | `Assignee` |
| `team` | `Team` |
| `tags` | `list<string>` |
| `risks` | `list<Risk>` |
| `stage` | `Stage` |
| `placement` | `Placement` |
| `classification` | `map<string,dyn>` |
| `is_provisional` | `bool` |
| `created_at` | `timestamp` |
| `closed_at` | `timestamp` |

## `Project`

| field | type |
|---|---|
| `id` | `string` |
| `key` | `string` |
| `methodology` | `string` |
| `status` | `string` |
| `planned_end` | `timestamp` |
| `team` | `string` |
| `product` | `string` |

## `KeyOnly`

| field | type |
|---|---|
| `key` | `string` |

## `State`

| field | type |
|---|---|
| `key` | `string` |
| `category` | `string` |

## `Assignee`

| field | type |
|---|---|
| `id` | `string` |
| `handle` | `string` |
| `team` | `string` |

## `Team`

| field | type |
|---|---|
| `slug` | `string` |

## `Risk`

| field | type |
|---|---|
| `key` | `string` |
| `score` | `int` |
| `status` | `string` |

## `Stage`

| field | type |
|---|---|
| `key` | `string` |
| `category` | `string` |
| `expected_duration_days` | `int` |
| `wip_limit` | `int` |
| `process` | `string` |

## `Placement`

| field | type |
|---|---|
| `entered_at` | `timestamp` |
| `exited_at` | `timestamp` |
| `process` | `string` |
| `stage` | `string` |

## `Activity`

| field | type |
|---|---|
| `id` | `string` |
| `kind` | `string` |
| `status` | `string` |
| `started_at` | `timestamp` |
| `ended_at` | `timestamp` |
| `duration` | `duration` |
| `person` | `string` |
| `item` | `string` |

## `Person`

| field | type |
|---|---|
| `id` | `string` |
| `handle` | `string` |
| `team` | `string` |
| `capacity_hours_per_week` | `double` |
| `time_zone` | `string` |

## `Event`

| field | type |
|---|---|
| `type` | `string` |
| `source` | `string` |
| `occurred_at` | `timestamp` |
| `payload` | `map<string,dyn>` |

## `DailyLog`

| field | type |
|---|---|
| `date` | `timestamp` |
| `status` | `string` |
| `minutes_total` | `int` |
| `item_ids` | `list<string>` |

## Registered functions (Tier 3)

Heavy lookups are implemented once in server code and read facts the engine preloads per batch (one grouped query per fact group, never one per item).

| call | returns | what it does |
|---|---|---|
| `<item>.evidence_last_at()` | `timestamp` | When the newest evidence on the item was captured (the epoch if there is none). |
| `<item>.evidence_count(string?)` | `int` | Number of evidence rows on the item, optionally of one kind (`pr`, `note`, ...). |
| `<item>.hours_logged(string)` | `double` | Hours logged on the item in a window (`today`, `7d`, `30d`). |
| `<item>.reopen_count()` | `int` | How many times the item left a done state. |
| `<item>.days_in_stage()` | `int` | Whole days since the item entered its current stage. |
| `<item>.children_count()` | `int` | Direct children of the item. |
| `<item>.open_violations(string?)` | `int` | Open violations on the item, optionally of one category. |
| `<item>.missing_required_dimensions()` | `list<string>` | Keys of dimensions required for the item's stage that have no confirmed value. |
| `<stage>.wip_count()` | `int` | Open items in this stage within the policy's scope (the project for project-grouped policies). |
| `metrics.minutes_logged(string, string)` | `int` | Minutes a person logged in a window (`today`, `7d`). |
| `metrics.active_items(string)` | `int` | Items a person currently has an active session on. |
| `metrics.distinct_items(string, string)` | `int` | Distinct items a person worked on in a window. |
| `metrics.wip(string, string?)` | `int` | Open items of a project, optionally in one stage. |
| `metrics.overrun_rate(string, string)` | `double` | Share of items finished late in a project in a window. |
| `business_days(int)` | `duration` | A duration of n working days (calendar days x 7/5, rounded up): `now - business_days(3)`. |

Also available: `size`, `has`, `string`, `int`, `double`, `bool`, `duration`, `timestamp`, `contains`, `startsWith`, `endsWith`, `matches`, and the macros `exists`, `all`, `exists_one`, `map`, `filter`.
