# 8. Clients

| Client | Users | How it gets to them | Docs |
| --- | --- | --- | --- |
| Web console | everyone, in a browser | served by the server | [manager](../guides/manager.md), [admin](../guides/admin.md) guides |
| `tt` command line | reporters, managers | `ttctl client install` (from a checkout) or the **client kit** (offline, Windows/Linux/macOS) | [reporter guide](../guides/reporter.md), [management commands](../client/management-commands.md) |
| Desktop palette + personal tracker | reporters | browser preview; native Tauri app built from source | [desktop](../client/desktop.md), [personal tracker](../client/personal-tracker.md) |
| Mobile Lite | phones | installable web app served by your server | [personal tracker](../client/personal-tracker.md#mobile) |
| Slack bot | anyone in Slack | one service per company | [Slack](../client/slack.md) |

## The tt command line, from a checkout
```bash
scripts/ttctl client install
```
Installs `tt` with uv into `$(uv tool dir --bin)` (usually `~/.local/bin`; `uv tool update-shell` puts it on PATH).
On a bundle install it installs from the bundle's wheelhouse (offline). Dev server shortcut that mints a token through the
Development sign-in: `scripts/ttctl client login @sara`. Status: `scripts/ttctl client status`.

## Client kit
For users' computers - including air-gapped ones and Windows. Every bundle has a `client-kit/` folder; copy it (USB, file
share). It holds `install.sh` (Linux/macOS), `install.ps1` (Windows), `wheelhouse/` with every package for Linux x86_64,
macOS on Apple silicon and Windows x64 (Python 3.12), and `ca/` for your internal CA.

| | Linux / macOS | Windows (PowerShell) |
| --- | --- | --- |
| install | `./install.sh install --server https://tracker.example.com --workspace acme` | `.\install.ps1 install -Server https://tracker.example.com -Workspace acme` |
| sign in | `./install.sh login --browser` (or `--ldap-user 'CORP\jdoe'`, or `--token T --handle @me`) | `.\install.ps1 login -Browser` (or `-Token T -Handle @me`) |
| status / remove | `./install.sh status` · `./install.sh uninstall` | `.\install.ps1 status` · `.\install.ps1 uninstall` |
| details | `-v` | `-Verbose` |

* Needs Python 3.12+ (or uv). Offline is automatic when `wheelhouse/` is present; `--offline` / `-Offline` forces it.
* Internal CA: put it in `ca/ca.pem` before installing (or `--ca FILE` / `-Ca FILE`); `tt` then verifies the server with it.
* Intel Macs: the locked `cryptography` release ships macOS wheels for Apple silicon only, so the offline kit does not
  cover Intel Macs (an online `pip install` builds it from source with Rust).
* Exit codes as for ttctl: 0 ok, 2 usage, 3 Python missing, 4 offline and a file is missing.

### Working without a connection to the server
Ask an admin for the ContextPack file (`tt pack --export context-pack.json` on a connected machine, or the web console),
then:
```bash
tt pack --import context-pack.json
```
Captures queue in the outbox (`tt outbox`) and go out with `tt sync` when the server is reachable.

## Desktop palette and personal tracker
Preview in a browser: `scripts/ttctl start desktop` → <http://localhost:1420> (needs Node: `setup`, or a bundle built with
`--with-dev`). Web bundles: `scripts/ttctl build desktop`. The native app: `scripts/ttctl build desktop-native` (Rust +
Tauri libraries; installers and signing are not done yet, see [known limitations](15-known-limitations.md)).

## Slack bot
In `.env`: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, `TT_SLACK_LINK_SECRET`, `TT_SERVER_URL`.
Then `scripts/ttctl start slack`. Slack itself needs internet; on an air-gapped site the bot cannot work.
Details: [Slack](../client/slack.md).

## LLM gateway
Optional. Without it the deterministic parser handles the documented syntax. Laptop with Ollama:
`scripts/ttctl start gateway` (local models only). Team server: `prod install --with-gateway`. Offline: a bundle built
with `--with-llm MODEL`. Privacy modes and budgets: [gateway](../llm/gateway.md).
