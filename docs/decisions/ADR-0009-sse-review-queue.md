# ADR-0009: Server-sent events for review-queue updates
Date: 2026-10-24   Status: accepted

## Decision
`GET /review-queue/stream` streams `event: queue` (count + open refs) whenever the caller's queue changes. The server polls its own
database every 2 s per open stream instead of using LISTEN/NOTIFY, so it works through any pooler and needs no new infrastructure;
the browser uses `EventSource` and refetches the list on each event, with a 30 s poll as fallback if the stream drops.

## Consequences
One short query per open stream every 2 s (a handful of managers). The stream keeps the ACL context it was opened with; closing and
reopening the page refreshes it. Switch to NOTIFY when stream counts grow.
