# ADR-0034: Exports run in the request; browser exports are concurrency-bounded
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
HTML, CSV and PPTX render in the request. PDF/PNG launch headless Chromium; at most two run at once and a third waits five seconds, then gets `503 render_busy`.

## Why
A job + download URL is the long-term shape (plan P7), but the exports are interactive and small; the semaphore removes the denial-of-service property the review flagged.

## Consequences
Large decks hold a worker for seconds. Moving to a job needs a result store; tracked as follow-up work.
