# ADR-0043: The development project: private items, visible to the person and their line manager
Date: 2026-10-25   Status: accepted (plan P15 §15.4; the plan names this ADR-0031, already taken by "static CEL checker and limits")

## Context
Training, courses and reading are `development` items. A person may choose to send them ("send as development") so a manager can see learning
consistency, but they are not project work and are not for the rest of the team.

## Decision
1. Workspace setting `development.project_key` (default `DEV`). The project is **created on first use**, when a message names it; its
   owner is the system bot, so no human gains rights over it. It has `visibility=private` and `settings.development=true` (the flag, not the
   key, identifies it, so renaming the key is safe).
2. Items created in it get `visibility=private` and the tag `development` (added by the server even if the client forgot; the tag is created
   non-provisional, so no review item is raised). Private items are visible to the people on them (reporter/assignee) and anyone who line-manages
   them (the existing `own` rule of `acl/visibility.py`); grants never widen them.
3. **Admins** follow the platform rule for private items and can see them; the Inbox (ADR-0024) is the only tier hidden from admins. Making
   development items admin-invisible would need a second project kind and is left open (Appendix C).
4. Governance: policy template `development_hours_below {hours}` (category `wellbeing`, so it follows the wellbeing rule: subject and line
   manager only) and the positive habit `learning_consistency` (share of weeks in a window with time on `#development` work), both read time
   entries on items tagged `development` through the new fact group `development` / CEL function `metrics.development_minutes(person, window)`.
5. ContextPack gains `development_project{key, tag, exists}`, `calendar_mapping_hints{teams[], client_domains[]}` (domains come from workspace
   setting `calendar.client_domains`) and `my_capacity_hours_per_week`.

## Consequences
* A person sees their learning in the same timeline as work, and exactly their line manager sees it too; project managers, peers and
  members of other teams get 404 (ACL test `test_development_items_are_visible_to_the_person_and_their_line_manager_only`).
* The policy category `wellbeing` was chosen because the enum is fixed by the spec and `wellbeing` carries the restricted visibility wanted here.
* One DEV project per workspace: per-team learning budgets would need a project per team.

## Alternatives considered
* A project per member: noisy project list, key sprawl.
* Tag-only on ordinary projects: leaks to project members.
* A new `ProjectKind.DEVELOPMENT`: breaks spec parity for a rule that `private` already expresses.
