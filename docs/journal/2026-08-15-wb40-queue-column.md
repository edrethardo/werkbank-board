---
title: WB-40 — "Zu bearbeiten" queue column with per-project review gate
date: 2026-08-15
tags: [feature, decision]
summary: New status zu_bearbeiten queues tickets and the dispatcher pulls the next one itself; a pending review blocks the project's queue unless the project is switched to nonblocking; 101 tests green plus a live chain on the running board.
outcome: done
---

# WB-40 — "Zu bearbeiten" queue column with per-project review gate

## What was asked

Ticket WB-40 (chat handover): add a column "Zu bearbeiten" meaning the agent
should pull that ticket next once the previous one is done; allow switching a
project to "nonblocking review" so the next ticket starts without the previous
one being reviewed — finished tickets still land in Review.

## What I did

- store: new status `zu_bearbeiten` between offen and in_arbeit (+ German
  label for tooltips).
- dispatch: `Dispatcher.pump_queue()` picks the next queued ticket
  (priority hoch>normal>niedrig, then lowest id), refusing while
  `_queue_blocked_reason` says no; called after every finished run
  (chaining), on board status changes, and when the review mode is flipped.
  Blocking reasons in order: a ticket of the SAME project is in_arbeit; a
  ticket of the same project waits in review (unless the project is
  nonblocking); link blockers (`nach`/`nicht_mit`).
- projects: `set_review_mode(config, path, nonblocking)` writing
  `nonblocking_review` under the config lock; server endpoint
  `POST /api/projects/review-mode`; checkbox per project in the board's
  Projekte dialog with an explaining tooltip.
- board: sixth column, queued cards show the SAME reason text the dispatcher
  uses (mirrored function, kept in sync by wording).
- Tests: 7 new dispatch tests + 2 projects tests, red first, suite 101 green.

## Live verification on the running board

1. Probe ticket → `zu_bearbeiten` while WB-40 itself was in_arbeit: stayed
   queued. ✓
2. WB-40 finalized to review → probe still queued ("wartet auf deine Abnahme
   in Review"). ✓ (review gate)
3. Werkbank project switched to nonblocking via the endpoint → probe started
   by itself within a second, arriving as a chat handover (interactive
   lineage) instead of a spawned run. ✓
4. Cleanup: probe deleted, project switched back to blocking.

## What I tried that didn't work — and why

First implementation blocked the queue on ANY ticket in_arbeit, anywhere. The
live board exposed it immediately: WB-36 (another project, handed to that
project's own chat session) would have frozen the Werkbank queue indefinitely.
Fixed to per-project scoping plus the dispatcher's existing global
one-run-at-a-time guard; pinned by
`test_other_projects_running_ticket_does_not_block`.

## Decisions made

- Queue state lives in the ticket status, not in a side list — the board file
  stays the single source of truth and a restart cannot lose the queue.
- Blocking review is the DEFAULT (the user keeps control); nonblocking is an
  explicit per-project opt-in, and even then finished tickets stay in Review
  exactly as the ticket demanded.

## Follow-ups

- WB-35 (final pre-public review) was interrupted by an API session limit and
  sits in Offen again; its three review agents need a re-run.
