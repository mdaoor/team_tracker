# ADR-0015: Jira is a projection; inbound is limited to status, assignee and comments
Date: 2026-10-24   Status: accepted

## Decision
Team Tracker is the source of truth. The server pushes an item to a Jira issue (never a client, never Jira-first creation). From Jira only
three things come back, each as an `Event{source: import}` through the normal services so stage sync and policies run:
`status` (via the project's `status_map`), `assignee` (matched by e-mail), and new comments (`[Jira · name]` prefix when the author is unknown).
Every other Jira edit is ignored and is overwritten by the next push that differs.

## Consequences
* **No echo loops.** Three independent guards: (1) edits by the integration user (`bot_account_id`, learned by "Test connection") are dropped;
  (2) an inbound status equal to the item's current state is a no-op; (3) an inbound change records `last_state_key`, so the follow-up push
  sends nothing back. Pushes are also diffed by a hash of the desired fields and send only on change.
* **Idempotent creates and worklogs.** A create is never retried automatically on a 5xx/timeout (we cannot know whether Jira did it); the
  job retry first looks the issue up by its `tt:<item id>` label. Worklogs carry a `[tt:<time entry id>]` marker that is looked up before
  posting, plus a unique `jira_worklogs` row.
* **Pushes are asynchronous.** The outbox enqueues `jira.on_event` for item/time/stage/classification events and Procrastinate retries with
  backoff. The one exception is a reporter who opted in with `--jira` / the toggle: the capture path tries the push for at most
  `TT_JIRA_INLINE_TIMEOUT_S` (default 4 s) so the key can be returned; on timeout or outage the response says `deferred` and the queue finishes it.
* Credentials are encrypted at rest with a key from `TT_SECRET_KEY`; the webhook is authenticated by a per-workspace secret (query token or HMAC
  `X-Hub-Signature: sha256=`).
