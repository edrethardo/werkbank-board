---
title: WB-30 — tickets can be deleted from the detail dialog
date: 2026-08-15
tags: [feature]
summary: store.delete_ticket (locked), DELETE /api/tickets/WB-n, red delete button with confirm in the detail dialog; git history keeps deletions recoverable; 91 tests green.
outcome: done
---

# WB-30 — tickets can be deleted from the detail dialog

## What was asked

Ticket WB-30 (chat handover): "erlaube das löschen von tickets" — empty
description; the delete variant of the known Erledigt-grows-forever gap.

## What I did

- `store.delete_ticket` under the write lock; 2 new tests (red first; suite 91
  green). `server.do_DELETE` for `/api/tickets/WB-n` (404 German if gone).
- board.html: red "Löschen" button in the detail dialog with a native confirm
  (mentions git recoverability); `api()` helper gained a method parameter.
- Safety reasoning: deleting a queued/handed-over/running ticket is safe —
  dispatcher paths reload by id and no-op when the ticket is gone (existing
  behavior, covered by the `_run_one` guard); links to deleted ids show
  "unbekannt" and never block (WB-12). Deletion is committed, so git history
  keeps every deleted ticket recoverable.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- Hard delete instead of an archive folder: the user asked for deletion, and
  git history already provides the archive. Revisit an Archiv column only if
  the user misses deleted tickets in the board itself.
- Native `confirm()` instead of a third nested dialog — one destructive action,
  one plain question.

## Follow-ups

- Remaining gap-list candidates: board auto-commit, claim guard, live run log,
  autostart.
