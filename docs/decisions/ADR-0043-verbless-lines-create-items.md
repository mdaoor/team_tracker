# ADR-0043: A verb-less noun phrase creates an item; only observations and key-addressed text become notes
Date: 2026-10-25   Status: accepted (Phase 13 R6.2; UX review 4)

## Context
`tt say "refactor the ingestion retry"` with a timer running became a *note on the active item*; the success line then named
a different item. That is a surprising, data-corrupting default.

## Decision
The deterministic parser infers the action when no verb/time/plan/estimate is present:
* text containing a key (`WEB-114 looks fine`) → `note` on that key;
* an observation (is/are/keeps/can't/need to/should/maybe…) → `note`;
* anything else → `create` (never glued to the running item); phrases of one or two words are flagged
  (`llm_confidence.intent = 0.4`) so the card shows an `ask` cue ("new — or a note?").
* `@handles` the pack does not know stay in the title text (they are not assignees); known handles become assignees.
* The preview card always names the row the message lands on ("running now", or "(no task running)" with an `ask` cue),
  and shows the project of a matched task instead of "will be sorted by your manager".
`ParseContext.default_action=None` means infer; callers may still force one.

## Consequences
New goldens pin the behaviour; the LLM translator is unchanged (it already decides intent itself).
