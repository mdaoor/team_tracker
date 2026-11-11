# ADR-0007: Weight formula grammar
Date: 2026-10-23   Status: accepted

## Decision
`WeightScheme.formula` is JSON: `{"base": "estimate_hours"|"points"|"constant", "constant": n, "multipliers": {...}, "clamp": [min,max]}`.
Multiplier keys: `priority.<value>`, `stage.<key>`, `tag.<name>`, `type.<key>`, `impact.<1-5>`, `dimension.<key>.<value>`.
`weight = base x product(applicable multipliers)`, clamped, rounded to 4 places. A missing estimate (or points when the unit is
hours) gives base 1.0 so a weight always exists. Days convert at 8 h; points are not converted to hours.
The workspace `default` scheme (`priority.high 2, highest 2.5, critical 3`) reproduces the seed's weights and is the only scheme
mirrored into `WorkItem.weight`; every other scheme has its own `Weight` rows. Manual overrides are never recomputed.
`value_score = impact x weight / max(estimate_hours, 1)`.
Unknown bases/keys and bad clamps are rejected on save (`invalid_formula`).
