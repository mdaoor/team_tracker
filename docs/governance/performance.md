# Governance performance (measured)

Measured on the CI-class container (1 vCPU share, local Postgres), `apps/server/tests/api/test_governance_perf.py`:

| case | result | bound asserted |
|---|---|---|
| CEL on-event evaluation, CPU only (compiled once) | p95 ≈ 0.6 ms | < 20 ms |
| Candidate filter and scope pushed into SQL | confirmed by `EXPLAIN` | filter in the plan |
| `stuck_in_stage` sweep, 50 k placements, every one opens a violation + event | ≈ 180 s | < 400 s |
| Same sweep again (nothing new to open) | ≈ 77 s (~1.5 ms/row, dominated by celpy evaluation) | < 150 s |

The plan's tighter sweep target was not met; the cost is per-row CEL evaluation in `celpy` plus ORM writes for new violations. A
realistic workspace (a few thousand open placements, a handful of matches) sweeps in seconds. Faster paths (compiling simple
conditions to SQL) are not built.
