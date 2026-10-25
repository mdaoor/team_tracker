# Slack bot (phase 11 §4)

**What it does.** DM the bot what you did (`fixed login loop WEB-142, took 2h #auth`). It answers with a card (one section per parsed row,
each with its confidence cue) and the batch JSON as a thread snippet. Buttons: **Send · Edit · Discard · Manual…** (+ **Jira: on/off** when the
project has Jira). `Manual…`/`Edit` open a modal built from your context pack: it lists exactly the pack's projects, tags, people and open
tasks, and refuses to submit while a field the workspace requires is empty (naming each). Slash: `/tt now`, `/tt today`, `/tt unlink`.
One end-of-day DM offers **Confirm** for the day's log; it is not sent when the log is missing or already confirmed.

**Account linking.** The first message returns a signed, 10-minute link. After sign-in the bot stores `Slack user -> handle + API token` in
`links.json` (mode 0600) on the bot host; the token never appears in Slack. `/tt unlink` removes it.

**Exactly once.** Send acts on the outbox file; the sent file moves to `sent/`, so a second click, a retry or a replaced card is a no-op.
Offline: the file stays queued and the card says so.

**Run it.**
```
pip install 'tt-slack[bolt]'
export TT_SERVER_URL=https://tt.corp.example SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... SLACK_SIGNING_SECRET=... \
       TT_SLACK_LINK_SECRET=$(openssl rand -hex 32) TT_GATEWAY_URL=https://llm.corp.example  # gateway optional
tt-slack
```
Create the Slack app with Socket Mode, the `chat:write`, `im:history`, `commands` scopes, the `message.im` event and a `/tt` command.

**Tests.** `apps/slack_bot/slack_tests` (13): card and modal snapshots, mandatory fields, Jira chip, exactly-once send, offline queue,
end-of-day, linking (signature, expiry, 0600). The Bolt transport (`bolt_app.py`) is thin and **has not been run against a real Slack
workspace**; the Block Kit payloads are the contract and are covered.
