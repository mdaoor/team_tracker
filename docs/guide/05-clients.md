# 5. Clients

| Client | Who | Install | Detailed docs |
| --- | --- | --- | --- |
| **Web console** | managers, admins, everyone in a browser | nothing - open the server URL | [manager](../guides/manager.md), [admin](../guides/admin.md) guides |
| **`tt` command line** | reporters, managers in a terminal | `scripts/ttctl client install` or a wheel | [reporter guide](../guides/reporter.md), [management commands](../client/management-commands.md) |
| **Desktop palette + personal tracker** | reporters on a laptop | browser preview now; native Tauri app is built from source | [desktop](../client/desktop.md), [personal tracker](../client/personal-tracker.md) |
| **Mobile Lite** | phone | installable web app served by your server | [personal tracker - Mobile](../client/personal-tracker.md#mobile) |
| **Slack bot** | anyone in Slack | one service per company | [Slack](../client/slack.md) |
| **LLM gateway** | optional helper for all clients | `scripts/ttctl start gateway` | [gateway](../llm/gateway.md) |

---

## The `tt` command line

### Install
From a checkout of this repository:
```bash
scripts/ttctl client install
```
This installs `tt` as a user tool with uv (in `$(uv tool dir --bin)`, usually `~/.local/bin`). If `tt` is not found afterwards:
```bash
uv tool update-shell
```

For people without the repository, build wheels once and share the folder (or put it on an internal package index):
```bash
scripts/ttctl client wheels dist/client
```
They install with:
```bash
uv tool install --find-links dist/client tt-cli
```

Run it without installing (inside the repository): `scripts/ttctl client run <command>` (= `uv run tt <command>`).
Remove it: `scripts/ttctl client uninstall`.

### Sign in
Against a real server (pick the method your admin configured):
```bash
tt login --url https://tracker.example.com --workspace acme --browser
```
```bash
tt login --url https://tracker.example.com --workspace acme --ldap-user 'CORP\jdoe'
```
```bash
tt login --url https://tracker.example.com --workspace acme --handle @jdoe --token tt_xxxxx
```
Add `--ca-bundle /path/ca.pem` for an internal CA, `--time-zone Asia/Riyadh` for your zone, and `--gateway-url http://127.0.0.1:8700`
if you run the LLM gateway. The configuration is saved in `~/.config/tt/config.toml` (mode 600); the outbox and personal store in
`~/.local/share/tt`. Override with `TT_CONFIG_DIR` / `TT_DATA_DIR`.

Against the local dev server (Development sign-in, mints a token for you):
```bash
scripts/ttctl client login
```
```bash
scripts/ttctl client login @sara
```
Sign out: `scripts/ttctl client logout` (keeps the outbox).

### Use it
```bash
tt say "fixed the login loop WEB-142, took 2h #auth plan tomorrow 5pm"
```
```bash
tt now
```
```bash
tt today --confirm
```

| Area | Commands |
| --- | --- |
| report work | `say`, shorthands `doing done pause resume blocked note time plan est new`, `form` (manual mode) |
| outbox / offline | `outbox`, `edit`, `send`, `sync` (delivers everything queued, exactly once) |
| your day | `now`, `today [--confirm]`, `day`, `week`, `month`, `eod`, `review` |
| personal planner (local first) | `add`, `timer ...`, `cal ...` (Google / Outlook / Teams), `tray ...` (ready to send), `recur ...`, `item ...`, `sent`, `privacy`, `export`, `import`, `erase-local` |
| managers | `role`, `ops`, `do` (assisted command with preview), `cmd` (manual), `preview`, `queue`, `approve`, `waive`, `team ...` |
| setup | `login`, `pack` (refresh the context pack), `gateway serve` |

`tt <command> --help` shows every option.

---

## Desktop palette and personal tracker

Preview in a browser (no native build needed):
```bash
scripts/ttctl start desktop
```
Open <http://localhost:1420> (palette), `/personal.html` (personal tracker) or `/demo.html` (in-memory demo data).
Stop with `scripts/ttctl stop desktop`.

Build the web bundles (palette, personal tracker, Mobile Lite):
```bash
scripts/ttctl build desktop
```
Native desktop app (tray, notifications, global hotkey): install Rust and the Tauri system libraries, then
```bash
scripts/ttctl build desktop-native
```
The native shell is verified on Linux to start and run its background loop; installers, signing and the updater are not yet
done - see [desktop](../client/desktop.md) before rolling it out.

## Mobile Lite (phone)
Build with `scripts/ttctl build desktop` (for a sub-path: `TT_BASE=/m/ scripts/ttctl build desktop`) and serve
`apps/desktop/dist` from the **same origin as the API** (for example an extra `location /m/` in the nginx config). On the phone
open `https://<server>/m/mobile.html` and choose **Add to Home screen**. Limits (no calendar sync, data not encrypted on the
phone): [personal tracker - Mobile](../client/personal-tracker.md#mobile).

## Slack bot
Put these in `.env` (from your Slack app with Socket Mode, scopes `chat:write`, `im:history`, `commands`, the `message.im` event
and a `/tt` command):
```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
TT_SLACK_LINK_SECRET=<openssl rand -hex 32>
TT_SERVER_URL=https://tracker.example.com
```
```bash
scripts/ttctl start slack
```
Details and status: [Slack](../client/slack.md).

## LLM gateway
Optional. Without it the deterministic parser handles the documented syntax; with it you get free-text understanding,
insights, review suggestions and "Generate with AI" decks.

| Where | How |
| --- | --- |
| your laptop, local model only | install [Ollama](https://ollama.com), `ollama pull qwen2.5:7b-instruct`, then `scripts/ttctl start gateway` (= `tt-llm serve --local-only`) and `tt login ... --gateway-url http://127.0.0.1:8700` |
| team server | `scripts/ttctl prod install --with-gateway` (systemd) or `scripts/ttctl docker up --llm`; set `TT_LLM_GATEWAY_URL`, `TT_LLM_GATEWAY_TOKEN`, `TT_FEATURES__LLM_SUGGESTIONS=true` on the server |
| with your YAML config | `TT_GATEWAY_MODE=config TT_LLM_CONFIG=/etc/tt/llm_gateway.yaml scripts/ttctl start gateway` |

Privacy modes (`local_only`, `hybrid`, `cloud`), budgets and endpoints: [gateway](../llm/gateway.md).
