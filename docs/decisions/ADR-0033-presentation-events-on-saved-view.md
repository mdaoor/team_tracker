# ADR-0033: Presentation events use entity type `saved_view`
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
A presentation is a shareable bundle of views, so its events use `TargetType.SAVED_VIEW` rather than a new target type (see ADR-0021).

## Why
A new `TargetType` would break parity with the object model's enum.

## Consequences
Event consumers filter on `presentation.*` event types, not the entity type.
