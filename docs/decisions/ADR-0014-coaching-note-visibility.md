# ADR-0014: Coaching notes are manager-only
Date: 2026-10-24   Status: accepted

## Decision
`Habit.coaching_note` is returned only to holders of `habit.coach` who are not the habit's subject. The subject sees the habit and its
`status` (`observed → flagged → in_coaching → resolved`) but never the note, in lists or in the response to their own `PATCH`. A subject
cannot change their own habit status either (the UI disables it; the note is stripped server-side regardless of what the client does).

## Why
A coaching note is a manager's working note for a 1:1. Showing it to the subject turns it into a performance record; hiding it keeps it a
tool. Status is shown so the person knows they are being coached and is never surprised.

## Consequences
Exports for a 1:1 (`Export for 1:1`) contain status and numbers, never notes. The restriction is enforced in `habit_view` (server), not
only in the UI; `test_coaching_notes_never_reach_the_subject` pins it.
