# Team Tracker - internal planning (private)

This repository is where Team Tracker is **planned, reviewed and handed over**. The product code and its install /
operate / use documentation live in the product repository (`mdaoor/team_tracker`); nothing here ships.

| Path | What |
| --- | --- |
| `plans/` | phase plans and appendices - start at `plans/README.md`. Appendix E maps every requirement to its tests |
| `docs/spec/` | the normative design spec: object model, UX design and addenda (the two files the product tests use - the reference models and the presentation prototype - live in the product repo next to those tests) |
| `docs/decisions/` | architecture decision records (ADR-0001 ...); product docs refer to them by number |
| `docs/reviews/` | implementation, technical, security and UX reviews with screenshots and transcripts |
| `docs/handover/` | what is left after each implementation pass |
| `docs/uat/`, `docs/ux/` | UAT scripts and runbooks, UX checklists |
| `docs/web/`, `docs/governance/performance.md`, `docs/ops/load-report.md`, `docs/security/threat-model.md` | design notes, measurements and the threat model |
| `tools/handover.py`, `tests/` | fills the appendix-D handover prompts (`python tools/handover.py kickoff 11`) |

## Every plan names the product commit it targets
A plan or prompt is written against a specific state of the product. Put the product commit in the plan's header
(`Targets: mdaoor/team_tracker@<commit>`); `tools/handover.py` stamps it into every prompt automatically:

```bash
TT_PRODUCT_DIR=../team_tracker python tools/handover.py kickoff 11
```
`TT_PRODUCT_DIR` is the product checkout (default `../team_tracker`); `TT_PRODUCT_URL` overrides the URL in prompts.

## Rules
* `main` is protected: changes come through pull requests with one approval; no force-push, no deletion.
* Do not copy credentials, customer data or production logs into reviews; screenshots use the demo workspace.
* When a plan is implemented, the product PR links the plan section; the plan links the product PR back.

Imported from the product repository by `scripts/repo/split-internal-docs.sh` (history of these paths kept).
