---
title: WB-15 — separate Fehlgeschlagen column for failed agent runs
date: 2026-08-15
tags: [feature]
summary: New status/column "fehlgeschlagen" — technically failed dispatch runs no longer land in review; retry via card button or drag; server restart required to activate.
outcome: done
---

# WB-15 — separate Fehlgeschlagen column for failed agent runs

## What was asked

Ticket WB-15: failed tickets need their own column, because in review one can
easily accept an obviously failed ticket by mistake.

## What I did

- `store.py`: new status `fehlgeschlagen` (between review and erledigt).
- `dispatch.py`: DispatchError and internal worker errors now set
  `fehlgeschlagen` instead of `review`; successful runs still go to `review`.
- `server.py`: drag to in_arbeit now also dispatches from `fehlgeschlagen`
  (retry); the assignee≠claude case also lands in `fehlgeschlagen` instead of
  silently staying in_arbeit.
- `board.html`: fifth column (red header), grid widened to 5, "Erneut
  versuchen" button on failed cards.
- Tests: failure-path expectations updated + new internal-error test + new
  status test (test_store). My tests green; the 11 red tests in test_dispatch
  are WB-14's staged TDD tests (resume/session-state), untouched by me and
  red by design until WB-14 is worked. node --check on both script blocks OK.
- User doc column table + CHANGELOG updated same commit.

## What I tried that didn't work — and why

Nothing failed. Not done on purpose: restarting the board — I run INSIDE the
board's dispatcher process; killing it would kill this run and lose the result
write-back. Restart must happen after this run ends.

## Decisions made

- Only PROCESS-level failures (crash, timeout, missing binary, internal error)
  go to `fehlgeschlagen`. A run that exits cleanly but reports problems still
  goes to `review` — the Werkbank cannot judge content, the human does.
- Committed test_dispatch.py including WB-14's staged red tests, clearly
  labeled: the tree must end committed (hard rule 1), and the WB-14 run will
  find its red tests in git. Ticket files left uncommitted — they are the live
  state of the running dispatcher.

## Follow-ups

- Board restart needed to activate (start-board skill) — until then the new
  column renders but moves into it are rejected by the old running server.
- WB-14 (wrong-session bug) is unchanged and still pending; its tests are the
  11 red ones.
