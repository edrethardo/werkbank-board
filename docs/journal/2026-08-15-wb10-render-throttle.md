---
title: WB-10 — board renders only on real changes, never mid-drag
date: 2026-08-15
tags: [feature]
summary: The 5s poll now compares a JSON snapshot before rendering and defers renders while a drag is active (pending render flushed at dragend); both script blocks pass node --check.
outcome: done
---

# WB-10 — board renders only on real changes, never mid-drag

## What was asked

Ticket WB-10 (pulled in chat): stop rebuilding all columns every 5 seconds —
render only when data actually changed, don't disturb an active drag or open
dialogs, stay vanilla JS.

## What I did

- board.html only: `refresh()` serializes `[tickets, errors]` and skips `render()`
  when the snapshot equals the last rendered one. While a drag is active
  (`dragstart`/`dragend` on cards) renders are deferred; a changed snapshot sets
  `pendingRender`, flushed at `dragend`. Dialogs were never part of `#board`, so
  they stay untouched either way.
- CHANGELOG entry; no server restart needed (board.html is re-read per GET), the
  user just reloads the page.

## What I tried that didn't work — and why

Nothing failed. Verified via `node --check` on both script blocks; the visual
no-flicker criterion is left to the user's review (no browser automation on this
machine, same limitation as WB-6/WB-7).

## Decisions made

- Whole-board snapshot compare instead of per-card diffing: with file-backed
  tickets the payload is small even at hundreds of tickets, JSON.stringify of the
  fetched array is O(payload), and skipping identical renders already removes the
  5s flicker — per-card patching would add complexity for no observable gain at
  this scale (acceptance criteria don't require it).
- `config` is still updated on every poll even when rendering is skipped (it
  feeds the create dialog default project).

## Follow-ups

- None. WB-9 (concurrent saves) remains the last open assessment ticket.
