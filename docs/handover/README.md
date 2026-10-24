# Handover prompts

`plans/appendix-d-handover-prompt-templates.md` holds four prompts (D.1 kick-off, D.2 resume, D.3 review, D.4 spec change). It stays the single
source; `tools/handover.py` reads it and fills the placeholders from the plans, so a brief is one command.

```
uv run python tools/handover.py phases                     # phase labels, slugs, files
uv run python tools/handover.py kickoff 11                 # D.1: branch phase/11-desktop-mobile-chat-clients, file, next phase
uv run python tools/handover.py resume 11                  # D.2
uv run python tools/handover.py review 10 --pr <PR link>   # D.3
uv run python tools/handover.py spec-change "what changes" # D.4 (ADR first, no code)
uv run python tools/handover.py testplan 11 > apps/server/tests/<phase>/TESTPLAN.md   # the D.1 "test list" skeleton
```
(`make handover ARGS="kickoff 11"` does the same.)

* The phase label is the number in the phase file name (`2b` sorts after `2`). The previous phase's "Handover notes" are named in the kick-off.
* Placeholders the tool cannot know (the PR link, the description of a spec change) are required arguments rather than left as `<…>`.
* `tests/unit/test_handover.py` fails if the appendix loses a template or a phase file stops matching `NN-phase-<label>-<slug>.md`.
* One phase per session; do not paste a kick-off into a session that already holds another phase's context.
