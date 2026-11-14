# Team Tracker - install, operate and use

This guide is the one place for installing, running, stopping, upgrading and operating Team Tracker: the **server side**
(API, worker, database, web console, LLM gateway) and the **clients** (`tt` CLI, desktop, Slack). Every action is a
`scripts/ttctl` command; every command answers `--help`.

| # | Page | When |
| --- | --- | --- |
| 1 | [Requirements and support matrix](01-requirements.md) | before anything: which OS, which tools, root or not |
| 2 | [Install online](02-install-online.md) | a host with internet (or internal mirrors) |
| 3 | [Install air-gapped](03-install-airgapped.md) | a host without network: build a bundle, carry it over, install |
| 4 | [Run, stop, restart, status, logs](04-run-and-stop.md) | every day |
| 5 | [Database](05-database.md) | create, migrate, seed, back up, restore, reset |
| 6 | [Production](06-production.md) | systemd, nginx, TLS from PEM files, Active Directory |
| 7 | [Upgrade and rollback](07-upgrade-and-rollback.md) | online or from a bundle |
| 8 | [Clients](08-clients.md) | `tt` CLI (client kit for Windows/Linux/macOS, offline), desktop, Slack |
| 9 | [Configuration](09-configuration.md) | every setting (generated from the code) |
| 10 | [Scripts reference](10-scripts-reference.md) | every command's help (generated) |
| 11 | [Troubleshooting](11-troubleshooting.md) | an error code `TT-Ennn`, or a symptom |
| 12 | [Uninstall](12-uninstall.md) | removing it |
| 13 | [FAQ](13-faq.md) | short answers |
| 14 | [Repositories](14-repositories.md) | product vs private planning repository |
| 15 | [Known limitations](15-known-limitations.md) | what is not verified yet |

## Quick start (connected Linux or macOS host)
```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker
```
```bash
scripts/ttctl setup --with-db --install-missing
```
```bash
scripts/ttctl start
```
Open <http://localhost:5173/login> → **Development sign-in** (workspace `acme`, handle `@mahmoud`).
`scripts/ttctl status` shows what runs; `scripts/ttctl stop` stops it. Air-gapped host: [page 3](03-install-airgapped.md).

## What runs where
```text
             browser ──► web console (:5173 / nginx) ──┐
 tt CLI / desktop / Slack ──────────────────────────────┼──► API server (:8000) ──► PostgreSQL 16 (:5432)
                         └──► LLM gateway (:8700) ◄────┘          ▲
                                     └──► local model             │
                                                       worker (background jobs)
```

## Other documentation
Using the product: [reporter](../guides/reporter.md), [manager](../guides/manager.md), [admin](../guides/admin.md) guides.
Operations: [runbooks](../runbooks/README.md), [security](../security/README.md), [Helm](../ops/helm.md),
[configuration reference](../ops/config-reference.md). Clients and integrations: [`docs/client/`](../client),
[LLM gateway](../llm/gateway.md), [Jira](../integrations/jira.md). API: [`docs/api/`](../api).
