# Team Tracker

Two-layer work tracking: reporters type one line (or fill a form), a local/independent LLM gateway structures it, managers enrich it server-side, and everything is rendered as views, decks and a CEL-governed policy engine.

- **Design spec (normative):** `docs/spec/` — object model v0.4, UX design v0.1, Python reference models, presentation-page prototype.
- **Implementation plans (handover to an implementing agent):** `plans/README.md` — start there. 14 phases + 5 appendices.
- **Tools:** `tools/gen_snapshot.py` — sample snapshot generator used by the prototype and as the test oracle.

Status: planning complete; no application code yet. Phase 0 (`plans/01-phase-0-bootstrap.md`) is the first implementation step.
