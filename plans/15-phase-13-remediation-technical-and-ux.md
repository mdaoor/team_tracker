# Phase 13 — Remediation: technical debt and UX polish from the 2026‑10‑25 reviews

## Goal
Close every item in `docs/reviews/2026-10-25-technical-and-ux-review.md` (Part 1 technical risks 1–15 and the "below the top 15" list; Part 2 UX issues 1–15, the "also" list, the accessibility table and the quick wins) so that a non‑technical manager can trust the product and the codebase can carry Phases 14–15 without compounding debt. Work is grouped into packages that can be executed as separate PRs; order matters within the first three.

## Depends on
Implementation head `472650d` or later on `claude/listed-plan-implementation-hygguz` (PRs 4–9 merged). The 2026‑10‑24 packages A–C (appendix D.5) are assumed done; if a check shows otherwise, do them first.

## Inputs
- `docs/reviews/2026-10-25-technical-and-ux-review.md` (normative work list; file:line evidence)
- `docs/spec/team_tracker_ux.md` §2–§6 (behaviour targets the UX review measured against)
- `plans/00-architecture-and-stack.md` §4 (patterns the technical fixes restore)
- `plans/appendix-b-testing-and-dod.md` (coverage floors; the DB suite must become hermetic and faster)

## Scope and packages

### R1 — Blocking UX bugs (one PR, first; all S)
| id | review ref | change | acceptance |
|---|---|---|---|
| R1.1 | UX 1 | `apps/web/src/api/http.ts` `postBlob`: read `response.blob()` once (no `clone()`); pending toast "Preparing PDF…" and success toast naming the stamp; error toast with request id | Every Export/Download button (Views CSV; Present HTML/PDF/PPTX/PNG/CSV; Download pack) downloads a file in Playwright; a unit test asserts no double body read |
| R1.2 | UX 2 | Deck shell CSP `frame-ancestors 'self'`; builder iframe shows a skeleton while loading | Builder preview renders the selected slide; CSP test still blocks third‑party framing |
| R1.3 | UX 6 | Confirm dialog on presentation/template/view delete; redirect to the list; 10 s undo toast (soft delete + restore endpoint) | e2e: delete → dialog → undo restores |
| R1.4 | UX 9 | Toast de‑duplication by key; undo on review resolve (3 s) with "Don't learn" affordance | Resolving a row shows one toast with Undo and Don't learn; undo reverts the write and the glossary entry |
| R1.5 | UX 12 | Navigation gated on `me.can.*`; direct URL to a forbidden page shows an access message, not an empty page | Viewer `@ali` sees only permitted nav entries; e2e for 3 routes |
| R1.6 | UX 13 | Share link: when `settings.external_share` is off, the button is disabled with the reason and a link to Settings for admins; the TypeError is gone (R1.1) | e2e |
| R1.7 | UX "also" | Weight scheme selector lists each scheme once; seed gains `effort-only`; pipeline column counts are links; `?f=garbage` → toast "filter ignored" | tests |

### R2 — Names, feedback and small UX fixes (second PR; S–M)
| id | review ref | change |
|---|---|---|
| R2.1 | UX 5 | Server resolves names in habits (team/person), item drawer "reported by", provenance (`set by @sara · 2 d ago · inherited from Product X`), digest, violations; a response‑shape test asserts no bare UUID in these payloads' display fields |
| R2.2 | UX 15 | CLI: project cue only says "will be sorted by your manager" when the project is unresolved; `tt today` creates/returns the draft when time was logged; `--yes` honoured and non‑TTY asks nothing but says what it sent |
| R2.3 | UX "also" | New item keys continue the project sequence (seed sequences set to max+1); calendar shows titles and month nav; 409 dialog names who and when; policy template list has a selected state; `stuck_in_stage` headings show names |
| R2.4 | UX 8 (part) | Review queue rows show `@reporter · 2 h ago`; heading shows count; Enter opens the picker when no suggestion exists; `j/k` move DOM focus (announced) |
| R2.5 | A11y table | Review queue: remove `nested-interactive` (row is a group, controls are siblings); board/pipeline cards: dnd‑kit handle separate from the card button; matrix `--seq6` cell contrast fixed; deck muted text ≥ 4.5:1; `aria-label` on deck progressbar; `<h1>` on Views pages; `heading-order` and `empty-table-header` fixes in Setup |
| R2.6 | UX 14 (part) | Deck: KPI tiles focusable and drill; drill rows open the item drawer inside the deck; table toggle on every chart kind |

### R3 — Technical hardening (third PR; mostly S)
| id | review ref | change | acceptance |
|---|---|---|---|
| R3.1 | T3 | Migration `0011_fk_indexes` for all 60 unindexed FK columns; test asserting every FK has a leading index | query on `pg_catalog` returns 0 rows |
| R3.2 | T2, T10 | Tests hermetic: `Settings(_env_file=None)` under pytest; `committed_engine` fixture with TRUNCATE for concurrency tests; template DB for schema; `pytest-timeout` 120 s | 3 previously failing tests pass on a machine with a `.env`; full DB suite < 12 min |
| R3.3 | T5 | `RetryStrategy(max_attempts=5, exponential_wait=10)` on `governance.on_event`, `llm.suggest_for_event`, all event‑driven jobs; `tt_jobs_failed` metric and alert rule in `ops/` | job failure test shows retry then dead‑letter metric |
| R3.4 | T1 | Rate limiter honours `X-Forwarded-For` only from `settings.trusted_proxies`; keys sign‑in by (ip, username) | test behind simulated proxy |
| R3.5 | T8 | `ws_today(ws)` / `person_today(member)` helpers; ruff rule banning `date.today()` and `datetime.now()` without tz in `tt_server` | grep clean; reporting‑line validity test across zones |
| R3.6 | T9 | Multi‑stage Dockerfiles, `USER 10001`, `.dockerignore`, pinned `uv` by digest; compose and systemd non‑root | image scan; `docker run` as non‑root |
| R3.7 | T13 | Per‑response CSP nonce for the three inline scripts; remove `'unsafe-inline'` from `HTML_CSP` | Playwright asserts zero CSP violations on deck, page, export |
| R3.8 | T14 | Gateway budget reserve‑then‑settle; `Decimal` for cost; concurrency test at the limit | ≤ 1 request passes at the boundary |
| R3.9 | below‑15 | `TT_SECRET_KEY` mandatory outside dev (Fernet no longer derives from the session secret); shared engine for periodic jobs; worker concurrency configurable (default 4); OTel `FastAPIInstrumentor`, `SQLAlchemyInstrumentor`, procrastinate spans; `tt_slack/bolt_app.py` tests ≥ 70 % | trace shows API → job span chain in a test exporter |

### R4 — Architecture refactors (two PRs; M)
| id | review ref | change |
|---|---|---|
| R4.1 | T7 | `tt_server/domain/errors.py` (`NotFound`, `Conflict`, `Invalid`, `Forbidden`) raised in domain/acl/governance/integrations/exports; single mapping to `ApiError` in `install_error_handlers`; remove the 27 inverted imports and the two cycles; function‑level imports cut below 30 with a lint budget |
| R4.2 | T6 | Extract `items.update_overlay`, `review.resolve`, `bulk.apply`, `governance.*` orchestration into services; split `overlay.py`, `core.py`, `governance.py` routers by resource (≤ 400 lines each); routers only parse, authorise, call, serialise |
| R4.3 | T4 | Builtins seeded at `tt-admin migrate`/startup; no `session.commit()` outside `db/uow.py` and `commit_bookkeeping()`; grep test |
| R4.4 | T15 | DB CHECK constraints for enums (or native enums) and explicit `ON DELETE` per FK in one migration; model‑metadata test; replace the four hard deletes with soft delete or document why not |
| R4.5 | T code quality | Deduplicate the four repeated blocks into `domain/common.py` / `domain/items/queries.py`; docstrings on public service functions ≥ 80 %; `type: ignore` in non‑test code ≤ 20 |

### R5 — Frontend architecture (one PR; M)
| id | review ref | change |
|---|---|---|
| R5.1 | T11 | Typed per‑endpoint wrappers (`openapi-fetch` typed paths or `openapi-react-query`) for all mutating pages; `Row = Record<string, any>` removed from pages (≤ 10 remaining uses, all in generic table plumbing) |
| R5.2 | T12 | `React.lazy` per route group (setup, views, present, governance); CodeMirror only in the policy chunk; ErrorBoundary per outlet with toast and "Reload"; main chunk < 250 kB gzip |
| R5.3 | UX 7 | Responsive shell: drawer navigation < 900 px, wrapping toolbars, `min-width: 0` on grid children, no horizontal page scroll at 390 px on any console screen; Playwright mobile pass on 14 screens |

### R6 — UX feature gaps (two PRs; M–L)
| id | review ref | change |
|---|---|---|
| R6.1 | UX 3, 8 | Review‑queue routing: primary line manager first, then project owner; an `unclassified_project` row for every item created in an Inbox; deterministic suggestion engine (matched item's project → glossary → reporter's most‑used project in 30 d → team default) so a primary button always exists; seed makes `@sara` a member, not owner, of WEB |
| R6.2 | UX 4 | Capture intent: a verb‑less noun phrase defaults to `create` (never `note` on the active item); the card always shows the target row; `ask` cue when ambiguous; `@unknown` handles kept in the title text |
| R6.3 | UX 10 | Setup editors to §3.3: product → project → epic tree with drag‑to‑reparent and `⌥←/→`; stage chip popover (duration, WIP, approval, category) with live mini‑pipeline; milestones/iterations calendar; risk heat‑map axis labels and cell filtering; people org tree with capacity; dimension CSV paste |
| R6.4 | UX 11 | Gantt: reuse the deck Plan renderer (time axis, today line, bar labels, milestones, dependency arrows, zoom); default rows = epics; `rows=milestone` supported |
| R6.5 | UX 14 | Deck auto‑fit: `fit: shrink|paginate` with "(cont.)" slides; Projects slide includes plan‑only projects |
| R6.6 | UX persona metric | "Next week" preset in Views and a one‑click "What's shipping next week" entry on the Views landing; calendar shows titles |
| R6.7 | UX missing | CLI first‑run ghost examples and empty state; end‑of‑day confirm notification path through `NotificationPort` (CLI prints, desktop in P11) |

## Out of scope
New features beyond the reviews (Phases 14–15); Kerberos/Postgres TLS unless the product owner un‑waives them (then add to R3).

## Acceptance criteria (phase level)
- [ ] Every row in R1–R6 has a linked test or Playwright check; the two review documents' tables are annotated "fixed in <PR>" in a follow‑up commit to `docs/reviews/`.
- [ ] axe: 0 serious violations on all 14 console screens and the deck; Lighthouse accessibility ≥ 95 on Review queue, Views, Present.
- [ ] Mobile 390 px: no horizontal scroll on any console screen.
- [ ] Full DB suite < 12 min, green on a machine with a developer `.env`, with per‑test timeouts.
- [ ] `pg_catalog` FK‑index check returns 0; `session.commit(` grep returns only `db/uow.py`; zero `date.today()` in `tt_server`.
- [ ] Main web chunk < 250 kB gzip; an induced render error shows the boundary, not a blank page.
- [ ] Re‑run of the UX walkthrough script (`docs/reviews/2026-10-25-ux/` boot commands) shows: all downloads work, builder preview renders, no UUIDs in display fields, review queue has a primary action on every row, `tt say "refactor the ingestion retry"` creates an item.
- [ ] Technical grades re‑assessed by an independent reviewer (appendix D.3 prompt): no area below B.

## Exit checklist
- [ ] ADRs: domain error hierarchy (R4.1), builtins seeding (R4.3), DB constraint policy (R4.4), CSP nonce (R3.7), budget reservation (R3.8).
- [ ] `docs/web/README.md` updated for typed wrappers, lazy routes, responsive rules.
- [ ] `docs/reviews/2026-10-25-technical-and-ux-review.md` gets a "Resolution" section listing PRs per item.

## Handover notes
Phases 14 and 15 extend the client heavily; R6.2 (capture intent) and R6.1 (suggestions) are prerequisites for the management verbs in Phase 14, and R5.3 (responsive shell) is a prerequisite for the mobile shells in Phase 15.
