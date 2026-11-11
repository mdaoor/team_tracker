# ADR-0002: Fuzzy match thresholds and tie policy
Date: 2026-10-23   Status: accepted

## Context
Capture lines refer to items by free-text title (UX §7.1, object model §12). The server must decide between
"this is an existing item" and "create a new one" without ever asking the reporter (capture never blocks).

## Decision
Candidates are the subject's non-done items (assigned, reported or owned), within the resolved project, or across
the workspace when the project fell back to the Inbox. Score = `0.5 * similarity + 0.5 * word_similarity`
(`pg_trgm`), plus `+0.1` when the item was touched in the last 7 days. Single-token queries use `similarity` only,
so a lone word such as "fix" cannot hit via `word_similarity`. The best candidate is matched when
`score >= 0.6` **and** it leads the runner-up by `>= 0.1`; otherwise a new item is created. Done items are matched
only by key or id (starting a done item by key reopens it).

## Consequences
- "fix login redirect" matches "fix the login redirect loop"; "fix checkout" does not.
- Ties and weak matches create a duplicate rather than corrupting an existing item; managers merge later.
- Thresholds are constants in `domain/capture/ingest.py` (`MATCH_THRESHOLD`, `MATCH_GAP`, `RECENCY_BONUS`).

## Alternatives considered
- `similarity` only: misses partial phrases ("fix login redirect").
- Asking the reporter on ties: violates "capture never blocks".
