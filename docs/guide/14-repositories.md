# 14. Repositories: product and private planning

| Repository | Visibility | Holds |
| --- | --- | --- |
| `mdaoor/team_tracker` (this one) | public today | the code, `scripts/`, deployment files and documentation that tells someone how to **install, operate or use** Team Tracker |
| `mdaoor/team_tracker-internal` | **private**, `main` protected | everything that records **why, plans, reviews, design and handover** |

## What stays and what moves
Rule: tells someone how to install, operate or use → stays. Records why, a plan, a review, a design, a UAT script or a UX
study → moves. Applied to every top-level `docs/` folder (and the planning folders outside `docs/`):

| Path | Content | Decision |
| --- | --- | --- |
| `docs/guide/` | install, run, operate, troubleshoot (this guide) | stays |
| `docs/guides/` | reporter, manager, admin user guides | stays |
| `docs/api/` | API reference, event catalogue, filter grammar, OpenAPI | stays |
| `docs/client/` | CLI, desktop, personal tracker, Slack, calendars | stays |
| `docs/integrations/` | Jira | stays |
| `docs/llm/` | operating the LLM gateway, prompt versions | stays |
| `docs/ops/` | configuration reference, Helm, versioning policy | stays |
| `docs/ops/load-report.md` | a load-test measurement | **moves** |
| `docs/presentation/` | decks, templates, generation pack | stays |
| `docs/runbooks/` | incident runbooks | stays |
| `docs/security/` | access control, Active Directory, TLS | stays |
| `docs/security/threat-model.md` | STRIDE threat model and pen-test checklist (design/review) | **moves** |
| `docs/governance/` | policy context schema, templates (generated references) | stays |
| `docs/governance/performance.md` | measured performance study | **moves** |
| `docs/release-notes-*.md`, `CHANGELOG.md` | what changed for users | stays |
| `docs/decisions/` | architecture decision records | **moves** (docs refer to ADRs by number) |
| `docs/spec/` | normative design spec (object model, UX, addenda) | **moves**; its two test oracles stay next to their tests: `packages/schema/tests/reference/team_tracker_models.py`, `apps/presentation/e2e/reference/team_tracker_presentation.html` |
| `docs/uat/` | UAT scripts and runbooks | **moves**; known limitations stay as [15-known-limitations.md](15-known-limitations.md) |
| `docs/ux/` | UX checklists | **moves** |
| `docs/web/` | web console architecture and screen design notes | **moves** |
| `docs/reviews/`, `docs/handover/`, `plans/` | reviews, handover notes, phase plans | **moves** (already removed from the tip) |
| `tools/handover.py` + its test | fills the handover prompts from the plans | **moves**; reads the product checkout from `TT_PRODUCT_DIR` |

The machine-readable list is `scripts/repo/internal-paths.txt`. `scripts/repo/rewrite_internal_refs.py --check` (run by
the ops tests) keeps product docs from linking to moved files.

## Creating the private repository
Needs the GitHub CLI with rights to create repositories for the owner:
```bash
gh auth login
```
```bash
scripts/ttctl repo split --repo mdaoor/team_tracker-internal --history --dry-run
```
The dry run builds the repository in `.run/internal-repo` and scans it; nothing leaves the machine. Then:
```bash
scripts/ttctl repo split --repo mdaoor/team_tracker-internal --history
```
It keeps the full history of every moved path (each taken from the last commit that had it), adds a README explaining the
layout (and that every plan names the product commit it targets), CODEOWNERS, and the handover tool adapted to
`TT_PRODUCT_DIR`; it creates the repository **private** (refuses an existing non-private one), pushes `main` and protects
it: pull request with one code-owner approval, no force-push, no deletion. Invite people with
`gh api -X PUT repos/mdaoor/team_tracker-internal/collaborators/USERNAME -f permission=push`.

## Secrets scan
```bash
scripts/ttctl repo secrets-scan
```
Scans the working tree **and every commit** (gitleaks when installed, otherwise detect-secrets). `repo split` runs it on
the private repository before publishing and stops on findings. Known fixtures it reports: the dev database URL
`tt:tt@localhost`, AWS's documented example keys and dummy tokens in tests, lockfile hashes.

## History
Removing files from the tip does **not** remove them from the history. `mdaoor/team_tracker` is public: every older
commit, every branch that still carries `plans/`, and every merged pull request (including its diff and the security
reviews) remain readable. Three ways to deal with that - none of them is run by these scripts:

| Option | What happens | Consequences |
| --- | --- | --- |
| **(a) make `mdaoor/team_tracker` private** | Settings → General → Change visibility | One click, reversible, nothing breaks for collaborators. Stars/watchers are lost; forks stay public as they are (check them); public links stop working. Afterwards publish a clean public repository later if wanted (option b). |
| **(b) fresh public repository from a squashed snapshot** | create e.g. `mdaoor/team-tracker` with one commit of the current tip (no internal paths), keep the old one private | Clean public history from day one; the old repository keeps the full history privately for the team. Issue/PR history and stars stay in the old one; links need updating; CI and integrations move to the new repository. |
| **(c) rewrite history and force-push** | `scripts/ttctl repo purge-history --i-understand-this-rewrites-public-history` rewrites a mirror; pushing is manual | Every commit id changes: every clone, fork and open PR breaks and must be redone. GitHub keeps `refs/pull/*` and cached views of the old commits until **GitHub Support** purges them, and forks keep the old history regardless. Highest effort, least certain. |

**Recommendation: (a) now, then (b) when a public repository is wanted.** (a) closes the exposure immediately and can be
undone; (b) gives a clean public repository without breaking anyone's work. (c) is only worth it if the repository must
stay public under the same name, and even then the old data may survive in forks.

Report what (c) would touch, without changing anything:
```bash
scripts/ttctl repo purge-history
```
