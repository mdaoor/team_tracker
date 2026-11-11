# Runbook: background-job backlog

**Alert:** `TTJobBacklog` (oldest waiting job > 10 min) or `TTJobsFailed`.

1. Is the worker running? `systemctl status tt-worker` / `kubectl get deploy <release>-worker`. Restart it if it exited; logs: `journalctl -u tt-worker -n 200`.
2. Look at the queue: `psql $DB -c "select status, task_name, count(*), min(scheduled_at) from procrastinate_jobs group by 1,2 order by 3 desc"`.
3. A task failing repeatedly: read `procrastinate_events` for the job id, fix the cause (usually a down dependency: Jira, LLM gateway), then
   `psql -c "update procrastinate_jobs set status='todo', scheduled_at=now() where id=<id> and status='failed'"`.
4. A job that is idempotent by design (all of ours are: events carry client ids, Jira pushes are keyed by `tt:<id>`, recurrence by occurrence marker) may be re-run safely.
5. If the whole queue is stuck behind a poison job, mark that job failed (`status='failed'`), let the rest drain, then open an issue with the job's args.
6. Scale: `replicas.worker: 2` in Helm values (queue locks stop two workers doing the same periodic job).
