---
title: WB-17 — startup sweep for orphaned in_arbeit tickets + no-restart rule
date: 2026-08-15
tags: [bugfix, feature]
summary: Board restarts during a run no longer strand tickets in in_arbeit — server startup sweeps them to fehlgeschlagen with an explanation, and dispatched agents are told never to restart the board; 46 tests green.
outcome: done
---

# WB-17 — startup sweep for orphaned in_arbeit tickets + no-restart rule

## What was asked

User pulled WB-17 (bug, filed after the WB-12 incident): a ticket agent restarting
the board kills the dispatcher's finalization step, leaving the ticket forever in
in_arbeit with empty Ergebnis and no log.

## What I did

Bug discipline: the WB-12 incident is the documented reproduction (work committed
in 9829d4a, ticket stuck, /tmp/werkbank-agent-WB-12.log missing); the mechanism
was reproduced as failing tests first (3 red), then fixed (46 green:
`python3 -m unittest tests.test_store tests.test_dispatch`).

- `dispatch.sweep_orphaned(tickets_dir)`: at server startup (before any dispatch,
  queue empty ⇒ nothing in_arbeit can have a live finalizer) every in_arbeit
  ticket moves to `fehlgeschlagen` with a German explanation pointing at git
  history/journal for whether the work itself finished. Hooked into
  `server.main()`; prints swept ids.
- `dispatch.build_prompt`: agents are now told to NEVER restart the board and to
  put restart requests into their final answer instead.
- Tests: sweep moves only in_arbeit (offen/review untouched), idempotent on a
  clean board, prompt contains the no-restart rule.
- CHANGELOG + user doc (Fehlgeschlagen section) updated.

## What I tried that didn't work — and why

Nothing failed. Ordering trap noted: WB-17 itself was in_arbeit while I worked it
interactively — finalizing it to review BEFORE restarting the board was required,
or the new sweep would have flagged my own active ticket.

## Decisions made

- Sweep to `fehlgeschlagen` (not back to `offen`): the retry button lives there,
  and the user should see that a run was cut off rather than have the ticket
  silently re-queue.
- Accepted limitation: tickets worked INTERACTIVELY (pull flow) that are in
  in_arbeit during a board restart get swept too — rare, self-explaining note,
  and the worker session can simply set review afterwards.
- Prompt rule over technical prevention (e.g. blocking port kills): an agent that
  needs a restart now has a sanctioned path (ask in Ergebnis); enforcement would
  add fragile process-inspection for a case the sweep now catches anyway.

## Follow-ups

- User wants to test WB-14 next by dragging a ticket — board restarted on this
  code, state.json intact.
