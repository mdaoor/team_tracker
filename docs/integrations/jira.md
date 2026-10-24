# Jira integration (Jira Cloud, REST v3)

Jira is a **projection** of Team Tracker (ADR-0015): the server creates and updates issues; only status, assignee and comments flow back.
Jira Server / Data Center is not supported (different auth and webhook shapes).

## Setup
1. Create an API token for a dedicated Jira user (the *integration user*). Give it create/edit/transition/worklog rights on the project.
2. Set `TT_SECRET_KEY` (credential encryption) and `TT_FEATURES__JIRA_SYNC=true`, then run a worker (`procrastinate worker`).
3. Setup → **Jira**: URL, e-mail, token → *Save connection* → *Test connection* (this learns the integration user so its own edits are never echoed back).
4. Same page: choose a project, set its Jira project key, fetch Jira options, and fill the three maps (item type → issue type, priority → priority,
   workflow state → status). Tick **Linked**. Optional: *Push every item* (`auto_send`), worklogs, inbound comments, a custom field id for the delivery stage.
5. In Jira → System → Webhooks add `…/api/v1/integrations/jira/webhook/<workspace>?token=<secret>` for *issue updated* and *comment created*.
   The secret is shown once (rotate it from the same page). Restrict the source to Atlassian's published IP ranges at your proxy.

## What is pushed
| Team Tracker | Jira |
|---|---|
| title / description | summary / description (ADF, plain paragraphs) |
| type, priority | issuetype, priority (via the maps) |
| assignee | assignee (account looked up by e-mail, cached) |
| planned end | duedate |
| estimate (hours/days) | timetracking.originalEstimate |
| tags | labels (plus `tt:<item id>`, used to find the issue again) |
| stage | the configured custom field |
| workflow state | transition to the mapped status; if no transition exists a comment is added |
| time entries | one worklog each, exactly once |

Not pushed: iteration → sprint (needs the Agile API), outbound comments, attachments, links.

## When does an item go to Jira?
Only if the project is *Linked* and either `auto_send` is on, or the reporter sent it with `--jira` (the item is then remembered as opted in), or someone pressed *Push* on it.

## Direction rules
Inbound status → state via the reverse of `status_map`; unknown statuses create a **review row** ("Jira status X means state…") and change nothing.
Inbound assignee is matched by e-mail; unmatched is ignored. Everything else in Jira is overwritten by the next differing push.

## Failure behaviour
Jira down or rate limited: the capture path is unaffected; the job retries (exponential backoff, `Retry-After` honoured). After 5 failed
attempts, or on a 4xx from Jira, a `jira_sync_failed` review row is created. Mapping gaps create `jira_mapping_gap` rows with a one-click fix.
The sync log (last 50) is on the Jira setup page and at `GET /integrations/jira/log`.

## API
`PUT /integrations/jira`, `POST /integrations/jira:test`, `POST /integrations/jira:rotate-secret`, `GET|PUT /projects/{key}/jira`,
`POST /projects/{key}/jira:link-existing`, `GET /items/{key}/jira`, `POST /items/{key}/jira:push`, `POST /integrations/jira/webhook/{workspace}`.
