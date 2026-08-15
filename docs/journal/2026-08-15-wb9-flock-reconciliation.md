---
title: WB-9 aftermath — two writers on one ticket, flock now actually wired
date: 2026-08-15
tags: [bugfix, investigation]
summary: The chat session and a board-dispatched run worked WB-9 simultaneously; the chat's flock addition ended up committed as dead code, now properly wired into all three write paths with a real two-process regression test (62 green).
outcome: done
---

# WB-9 aftermath — two writers on one ticket, flock now actually wired

## What was asked

User: "eigentlich solltest du jetzt wb9 bearbeiten" — while (unknown to the chat
session) a board drag had ALREADY dispatched an agent for WB-9.

## What I did

- Detected the collision mid-edit (files changing underneath, a CHANGELOG entry
  appearing that I didn't write), identified the live run (claude -p --resume …,
  PID 1017786), and backed my edits out to let it finish. Its commit `59ead4d`
  delivered: RLock serialization, version counter, ConflictError → 409, board
  sends version from detail/reject dialogs. Verified: 61 tests green, board/server
  wiring spot-checked.
- Found one gap: my concurrently-added flock contextmanager survived into their
  commit as DEAD code (never called — cross-process writers were unprotected),
  while their journal claimed it was incorporated. Fixed properly: `_locked()`
  (RLock + flock on `tickets/.lock`) now wraps all three write paths
  (create/update/set_result; set_result calls `_update_locked` internally to
  avoid nested flock deadlock). New `CrossProcessLockTest` hammers the store
  from two separate python processes — 62 tests green.

## What I tried that didn't work — and why

- My first instinct (implement WB-9 myself after claiming it) collided with the
  already-running dispatched agent — I noticed via unexpected file changes, NOT
  via any tooling. The exclusion feature (nicht_mit) can't protect against this:
  the chat session doesn't go through the dispatcher. Rule for the future, added
  nowhere mechanical yet: before claiming a ticket in chat, check for a live
  `claude -p` process and for the ticket already being in_arbeit (my python
  claim silently "succeeded" on a ticket the board had already dispatched —
  update_ticket doesn't guard status transitions).

## Decisions made

- Let the dispatched run win and reconcile on top, rather than killing it or
  racing it — its scope matched the ticket and its work was sound.
- Flock wired inside the existing public wrappers (single acquisition, internals
  stay lock-free) instead of a reentrant file lock — simpler to reason about.

## Follow-ups

- Consider a claim-guard in update_ticket (offen→in_arbeit only from offen) or a
  chat-side check in the pull skill, so chat and board can't double-claim. Not
  filed as a ticket yet — waiting for the user's take.
