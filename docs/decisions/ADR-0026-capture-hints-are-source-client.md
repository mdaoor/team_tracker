# ADR-0026: Capture-side overlay hints are recorded as `source=client`
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
When a reporter's message carries hints (priority, tags, stage, risk mentions), the resulting overlay events carry `source=client`, not `rule`.

## Why
The author of the value is the reporter, not a system rule; provenance chips show "set by @x via capture" and managers can tell it from a policy decision.

## Consequences
The overlay-never-rewrites-capture guard treats these writes as client facts. Policy-driven writes keep `source=rule`.
