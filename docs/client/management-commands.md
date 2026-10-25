# Management commands from the client (Phase 14)

The client that reporters use to send tasks is also the daily tool for managers, scrum masters, coaches, project managers and admins.
Switch to a **role mode** and the same CLI, desktop palette and Slack bot can create projects and epics, merge tasks, build virtual
teams, tag, move stages, approve, resolve review rows and waive violations. The web console stays the rich surface for setup and analysis.

## Two kinds of input, two contracts

| | Capture message (`messages[]`) | Management command (`commands[]`) |
|---|---|---|
| What it records | what you did (a fact) | a change to the shared overlay (an instruction) |
| Can the server refuse it? | **never** (rule 1: capture never blocks) | **yes**, with a readable reason (ADR-0052) |
| Confirmation | none; Enter sends | always a preview + one confirmation (`--yes` skips it); destructive: two |
| Role needed | none | the server checks your real roles on every command |

Both travel in one `ClientBatch` file (schema 0.2, ADR-0051). The server runs `messages[]` first, then `commands[]`, in order, under one
`correlation_id`; each entry has its own idempotency key and result. A rejected command changes nothing and does not affect the others.

## Role modes (a lens, not a permission)

`tt role` lists the modes the server allows you (`GET /me` → `role_modes`); `tt role manager` switches (kept in `config.toml`). A mode only
changes which verbs and forms are offered first (`packages/client_core/src/tt_client/roles.yaml`); `tt cmd <op>` and `tt ops --all` reach
every op in the catalogue and the server still authorises with your real roles.

| Mode | Surfaces first | Hidden |
|---|---|---|
| reporter | confirm my day, context notes | all management verbs |
| manager | create/merge/tag/assign items, stage moves, teams, approvals, review, waive | – |
| scrum master | iterations (create/start/close), move to iteration, stage moves, set state, review queue, approvals | team and tag admin |
| coach | habit coaching notes and status, acknowledge violations, context notes | everything that writes items |
| pm | projects, plans, baselines, milestones, risks, epics | tag admin |
| admin | teams, reporting lines, tags, dimensions, policies (activate/deactivate) | – |

Capture lines (`tt "fixed login, took 2h"`) work in every mode without switching.

## The catalogue

`GET /commands/catalogue` publishes every op with its JSON Schema (`args_schema`), `permission`, `preview`, `destructive`, a one-line
description and an `example {args, text}`. The client caches it with its ETag (`catalogue.json` next to the context pack) so forms and
`tt ops` work offline. Both the manual forms and the gateway's translator are generated from it, so a new server op appears in the client
and in the assisted mode without a client release.

```
tt ops                  # the verbs your role surfaces (★ = first; ! destructive; p has an effect preview)
tt ops --all            # everything the server offers
tt ops --refresh        # revalidate with If-None-Match
tt ops --import catalogue.json   # air-gapped: load a catalogue file
```

## Assisted: `tt do`

```
$ tt do "merge WEB-101 and WEB-105"
(parser)
╭────────── item.merge destructive ──────────╮
│ ↳ sources   WEB-101, WEB-105               │
│ ↳ target    WEB-101                        │
│                                            │
│ Effect preview                             │
│   Removes          WEB-105                 │
│   Affects          2 items                 │
│   Children         2 child items           │
│   Overrides kept   1                       │
╰─ role: Manager · needs item.write_overlay ─╯
Send? [y/N] y
This is destructive (item.merge) and cannot be undone from the client.
Really send? [y/N] y
✓ item.merge
```

1. The deterministic parser (`tt_client.parser.commands`, golden: 66 lines) handles the common phrases instantly and offline.
2. Anything it does not recognise goes to the gateway (`POST /v1/translate/command`, or the agentic `/v1/agent/command`), which sees only the
   catalogue's ops, validates every command against its schema, grounds people / projects / keys, and **never applies anything**.
3. A recognised phrase with a missing required value (e.g. "move WEB-1 to test" without a reason) opens the generated form pre-filled.
4. The client asks the server for an **Effect preview** (`preview_only`; it never writes) and shows it in the card.
5. You confirm; the batch is written to the outbox first (offline → queued, delivered exactly once by `tt sync`), then sent.

Examples the parser understands: `new epic Checkout redesign under WEB` · `merge WEB-101 and WEB-105` · `tag DATA-1 DATA-2 #q4` ·
`create virtual team payments with @sara @ali led by me` · `move WEB-1 WEB-2 to test because failed QA` · `move WEB-4 to Sprint 15` ·
`approve AP-12` · `waive violation V-9 because holiday week` · `assign WEB-1 to @sara` · `@sara reports to me` · `new sprint Sprint 15 on WEB
2026-11-02 to 2026-11-13` · `new risk Vendor slips on WEB high`. Chain with `;`. `for @sara` acts on someone's behalf (`on_behalf_of`).
A mixed batch: `tt do "new tag q4" --and "fixed login, took 2h"` puts the capture message and the command in one file.

## Manual: `tt cmd`

```
tt cmd team.create --arg name=payments --arg kind=virtual --arg members=sara,@ali --arg lead=me
tt cmd item.merge --form          # asks field by field (pickers for people / projects / teams / items come from your context pack)
```

`--arg k=v` values are typed from the schema (lists: `a,b,c`; objects: JSON); invalid arguments are rejected **before** anything is sent,
offline, with the field named. Everything accepted here is a schema-valid `ClientCommand`.

## Other commands

| Command | Does |
|---|---|
| `tt preview <id>` | Effect preview of a saved outbox entry; never writes |
| `tt send <id> [--yes] [--force]` | send a saved entry (entries with commands are previewed and confirmed first) |
| `tt queue [--accept-defaults]` | my review queue; resolve rows inline with the suggested default (`review.resolve`) |
| `tt team now` | what my reports are working on right now |
| `tt approve [AP-12] [-c comment] [--reject]` | decide a pending approval |
| `tt waive V-9 --reason "…"` | waive a violation (a reason is required) |
| `tt role [mode]` | show / switch the role mode |

Note: an unquoted first word that is one of these command names is a command, not capture text; quote the whole line (`tt "do the thing, 2h"`)
or use `tt say …` for capture lines that start with one of them.

## Safety rules

* **Capture is never blocked; commands may be.** A rejected command prints the server's reason, exits non-zero, is parked in
  `outbox/rejected/` and changes nothing; the commands before and after it still apply.
* **Preview never writes.** It is a separate `preview_only` request with its own idempotency key (`<id>:preview`).
* **Destructive ops** (`item.merge`, `item.split`, `team.remove_members`, `reporting_line.set`, `policy.deactivate`, and bulk
  `stage.move` / `item.tag` / `item.move_to_iteration` over more than 5 items) need confirmation **even with `--yes`**. Only `--force`
  skips it, and `--force` is recorded: `args._meta.forced = true`. Without a terminal and without the flags the entry stays in the outbox
  (exit code 3). The desktop sidecar and Slack enforce the same rule in code, not only in the UI (two confirmations; Slack uses its native
  confirmation dialog and refuses a click that did not come through it).
* **Audit.** Every command carries `args._meta = {client_version, mode, role_mode, forced}`; the server records it in the event payload
  next to `payload.command`. `on_behalf_of` is recorded too.
* **The gateway cannot be talked into new powers.** It offers the model only catalogue ops, drops any op or argument that is not in the
  catalogue's schema, drops people / projects / item keys that appear in neither the pack, the tool results nor your own words, strips
  `_meta`, and has read tools only.

## Desktop and mobile

The palette header has the **role switch** (only your modes) and **verb chips** for the mode (a chip prefills the input; it never sends).
Type a request and press Enter for the **command card** with its **Effect preview** section and **Confirm**; destructive commands ask a second
time. **Or fill a form** lists the mode's ops and renders the form from the catalogue. **My queue** and **Team now** are panels. A reporter with
no other role sees none of it. All of it goes through the sidecar verbs `cmd-*` (`tt_client.sidecar_commands`).

## Slack

`/tt do merge WEB-101 and WEB-105` answers with the command card (Confirm · Edit · Discard; a destructive Confirm opens Slack's confirmation
dialog). `/tt cmd team.create` opens a modal generated from the catalogue. `/tt approvals` lists pending approvals with **Approve / Reject**
buttons (one click decides); `/tt waive V-9` opens a one-field modal for the reason (or `/tt waive V-9 because holiday week`).
`/tt role [mode]` and `/tt ops` work as in the CLI.

## Tests

`packages/client_core/tests/test_commands.py` (catalogue cache and ETag, offline, every catalogue op → valid form → valid command, role matrix,
confirmation policy, mixed batch, parser golden), `test_sidecar_commands.py`, `apps/cli/tests/test_commands_cli.py` (e2e `tt do "merge …"`),
`apps/llm_gateway/gateway_tests/test_translate_command.py` (84-line golden with a scripted model: op accuracy ≥ 0.85, argument accuracy ≥ 0.90;
opt-in real-model run in `test_real_model_golden.py`), `apps/slack_bot/slack_tests/test_slack_commands.py`, `apps/desktop/src/palette/commands`
(vitest + axe). They run against `tt_client.testing.fake_command_server.FakeServer`, a fake of the contract, and
`packages/client_core/tests/fixtures/catalogue.json` (regenerate with `tools/gen_command_catalogue_fixture.py`; replace with the real server
catalogue at integration, the tests iterate whatever it contains).
