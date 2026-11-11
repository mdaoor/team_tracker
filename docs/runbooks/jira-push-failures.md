# Runbook: failed Jira pushes

**Alert:** `TTJiraPushFailures`. Setup -> Integrations -> Jira shows the sync log (`GET /integrations/jira/log`).

* `401/403`: the Jira credential expired or lost permission -> re-save it (stored encrypted with `TT_SECRET_KEY`). `404 project`: the project key mapping changed.
* `429/5xx`: Jira is down or throttling; the task retries with exponential backoff (`jira.on_event`, 8 attempts). Nothing is lost: pushes are keyed by the `tt:<id>` label so a retry never duplicates an issue.
* Field mapping errors (`400`): fix the mapping in the project's Jira settings and re-queue (`POST /integrations/jira/resync`).
* Never edit mirrored fields in Jira that Team Tracker owns; the loop guards will revert/ignore them and log it.
