# ADR-0003: Deterministic parser wins over the LLM for numbers and dates
Date: 2026-10-23   Status: accepted

## Context
Small instruct models are good at intent and entity matching but unreliable with durations, dates and
estimates ("took 1h30" vs 130 minutes, "friday" vs the wrong week). Reporters rely on those numbers for time sheets.

## Decision
`translate_line` always runs the structured parser too. When both produce a value for `minutes`, `planned_end`,
`estimate` or `tags` and they differ, the parser value is used (a note records the override). The parser also owns
`on_behalf_of` and explicitly typed item keys. The model contributes `action`, item matching, project, and overlay hints.

## Consequences
- Numbers are reproducible and testable with the golden set (no model needed).
- A line the parser cannot read at all still gets a model-supplied value (the parser only overrides when it found one).
