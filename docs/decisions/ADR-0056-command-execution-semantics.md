# ADR-0056: How the server executes management commands
Date: 2026-10-25   Status: accepted (Phase 14)

## Decision
1. **One transaction per command.** A command is the same code path as the web console's endpoint (the handler calls the router
   function or domain service), so its events and ACL checks are identical. A failing command rolls back and clears the unit of
   work's event buffer; commands before and after it in a batch are unaffected. After a rollback the actor's rows are reloaded.
2. **Rejection is a result, not an HTTP status.** `{status: rejected, errors[{pointer, code, message}]}` per command; the batch
   answers `207` when any command was rejected. Unknown op, invalid args, actor mismatch, missing permission (`forbidden`,
   `details.permission`), invisible object (`not_found`) and conflicts are all rejections. Capture messages keep rule 1 (never rejected).
3. **Authorisation is declared per op** (`permission`, `scope`, `scope_arg`) and enforced before the handler runs; handlers that call
   router functions inherit those functions' own checks as well (never weaker).
4. **Actor must be the token owner.** `ClientCommand.actor` that differs from the authenticated person is rejected (`actor_mismatch`);
   `on_behalf_of` is recorded in the event payload, it grants nothing.
5. **Token scope.** Commands need scope `manage` or above; a `capture` token gets `403 insufficient_scope` on `/commands` and on a
   `/capture/batches` body that carries commands.
6. **Idempotency.** `command_log(workspace_id, client_id)` stores the result of an applied command; a repeat returns it with
   `deduplicated: true`. Rejected and previewed commands are not logged (a corrected retry must re-evaluate, a preview never writes).
   The log row is committed after the command's own commit; a crash in between can re-apply once (documented trade-off, the events
   remain the audit).
7. **Destructive ops.** An op flagged `destructive` is applied only when `_meta.confirmed` or `_meta.forced` is set; otherwise
   `409 confirmation_required` as a rejection. The client sets `confirmed` after the person confirms a preview (`--yes` is
   confirmation for ordinary ops; `--force` sets `forced`, which is logged in `payload.command.forced`).
8. **Audit.** Every event written by a command carries `source=client` and `payload.command = {op, client_id, client_version, mode,
   role_mode, forced?, on_behalf_of?}`.
9. **Previews never write.** Ops with `preview: true` compute their effect in the handler and the executor rolls back whatever the
   handler touched; ops without it answer with a generic description and are not executed.

## Amendments (PR #11 review)
10. **Per-op token scope.** Every op declares `token_scope` (`manage`, the default, or `admin`; also in the catalogue entry). People-admin
    ops (`team.create`, `team.add_members`, `team.remove_members`, `reporting_line.set`, `project.add_member`, `dimension.value.add`,
    `policy.activate`, `policy.deactivate`, `tag.merge`) need `admin`. The check runs before the permission check and rejects that one
    command with `insufficient_scope` (`details.required_scope`), on `/commands` and on `commands[]` of `/capture/batches`. An interactive
    session carries the person's full authority and is not narrowed.
11. **Rate limit and batch cap.** Commands count against a per-credential sliding window (`TT_RATE_COMMANDS_PER_MIN`, default 120; each
    command counts once, `POST /items:merge` counts as one): over the limit is `429` + `Retry-After` for the whole request. A
    `ClientBatch` carries at most 50 commands (`422`, pointer `/commands`, message names `POST /commands`).
12. **The idempotency row rides on an event.** An applied command emits `command.applied` (`op`, `client_id`, `status`), so the
    `CommandLog` row commits through the normal guard; the executor no longer uses `allow_unlogged`.
