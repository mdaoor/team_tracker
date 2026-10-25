# ADR-0053: Local-first personal store; the server sees only what is sent
Date: 2026-10-25   Status: accepted (plan P15; the plan names this ADR-0029, which was already taken by "agent runs in memory by default", so Phase 15 uses 0053-0055)

## Context
Phase 15 turns the client into a personal time tracker (day/week/month, recurring items, a timer, calendar meetings). Most of that is private:
sport, errands, reading, a dentist appointment. The product rule is that nothing leaves the device unless the person sends it, and that
sending is one tap. Spec reference: plan 17 §15.1, §15.6; UX §2 (the reporter's effort is one line or one tap).

## Decision
1. **A separate SQLite store** (`~/.local/share/tt/personal.db`, stdlib `sqlite3`) with its own versioned migrations (`tt_client.personal.store`),
   independent of the outbox's `queue.db`. A database written by a newer client is refused, never downgraded.
2. **Field-level encryption with Fernet** (`cryptography`; AES-128-CBC + HMAC-SHA256) over each row's JSON. The 32-byte key is generated on
   first run and kept in the **OS keyring** through `keyring` (macOS Keychain, Windows Credential Manager, libsecret); on machines without a
   keyring (headless, CI) it falls back to a `secrets.json` file with mode 0600 and logs one warning. `TT_SECRETS=file|keyring` forces a choice.
   SQLCipher was rejected: it is not installable everywhere the CLI must run (no wheels for the sidecar's PyInstaller build on all three OSs).
3. **What stays readable in the file**: row ids, the UTC *date* an item starts on (a range index), the series id and a deleted flag. Titles,
   notes, attendees, links, rules, settings and calendar cursors are encrypted. The leak is the day an item exists, not what it is.
4. **Everything is stored as UTC instants**; local time is only for display. (Python compares and subtracts two datetimes that share a
   `ZoneInfo` by wall clock, which is wrong across a DST change; the recurrence tests caught it.)
5. **Categories carry the sharing default** (`work` ask once and remember, `meeting` suggest after each, `development` off with one toggle,
   `personal` never). A `personal` item **has no send path**: the model forces `share_policy=never`, `send.build_message` raises
   `NotSendable`, the tray never lists it and no setting or sidecar command can change that. The server cannot know a forged `personal`
   origin from any other, so the guarantee is a property of the client.
6. **Calendar tokens** live in the same secret store (`oauth:<provider>`), never in the config file, the outbox, the personal store or a log
   line (tests scan all of them). "Erase all local data" removes the store (and WAL/SHM files), the store key, the tokens and sync state.
7. **Export/import** is plaintext JSON on purpose (the person asked for a backup), written 0600.
8. Sending = a normal `ClientBatch` in the existing outbox (same files, same exactly-once sync).

## Consequences
* The personal tracker works with no server, no account and no network (a test denies every non-loopback socket).
* Losing the keyring entry loses the store: the app says so (`WrongKey`) instead of showing garbage; backups are the export.
* Date index leak is accepted and documented; a stricter mode would need per-query decryption of every row (a few thousand rows: possible later).
* Three-OS keyring behaviour is **not verified here** (only a fake backend and the file fallback are tested).

## Alternatives considered
* SQLCipher: stronger (whole-file) but a native dependency we cannot ship uniformly.
* OS-level disk encryption only: invisible to the user, no protection against another local account or a synced home directory.
* Storing personal items on the server as private: contradicts "the server never sees what I did not send".
