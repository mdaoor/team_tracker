# Load report

Server `http://localhost:8000`, workspace `acme`, 40 samples per endpoint (10 for the cold snapshot).

| Endpoint | p50 | p95 | Budget | Result |
| --- | --- | --- | --- | --- |
| capture (1 message) | 59 ms | 75 ms | 150 ms | meets |
| query items | 171 ms | 204 ms | 300 ms | meets |
| snapshot (cold) | 1359 ms | 1359 ms | 2000 ms | meets |
| list items | 28 ms | 37 ms | 300 ms | meets |
| review queue | 53 ms | 64 ms | 300 ms | meets |

## Setup and caveats
* Fixture (`tools/seed/load.py`): 200 members, 50 projects, **100,000 work items, 500,347 classifications, 1,000,726 events** on top of the demo workspace; PostgreSQL 16 on the same 4-vCPU / 15 GB container as the server (one uvicorn worker, dev auth, no TLS, no reverse proxy). Real deployments with a separate database host and several workers will differ.
* All items are clones of one template item (one project, one assignee), so the distribution is unrealistically uniform: per-project and per-person filters are *easier* than in a real tenant, the workspace-wide snapshot is representative of the worst case in size.
* Single client, sequential requests: this measures latency, **not throughput or contention**. No concurrent load test was run.
* `query items` uses an open/active filter sorted by priority, limit 50. Cold snapshot = `fresh=true`, per-viewer cache bypassed, scope `workspace` (heaviest, about 400 kB of JSON, ~1.0-1.4 s) and smaller scopes (~0.3 s). The in-test `test_snapshot_perf` still uses a relaxed 8 s bound because tests run inside a savepoint transaction on a shared container.
* Not measured: PDF/PNG render latency, LLM latency, Jira push latency, database growth/partitioning (events are **not** partitioned by month yet - open item).
