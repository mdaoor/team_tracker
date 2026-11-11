# ADR-0010: Relative date tokens and time-zone resolution
Date: 2026-10-24   Status: accepted

## Decision
Filters store tokens (`today`, `-14d`, `end_of_month`), never dates. They are resolved when the query runs, in the *viewer's* time
zone (`time_zone` in the request, else the person's zone, else UTC), so a saved view means the same thing tomorrow and for a colleague in
another zone. Weeks start on Monday. `touched.since` and `between` are inclusive whole days; `between` on a timestamp column
(`actual_end`, `created`) compares its UTC date. The parser validates token syntax only; the compiler resolves.

## Consequences
Two people opening one shared view near midnight may see different "today" sets, by design. A view that must be frozen uses ISO dates.
