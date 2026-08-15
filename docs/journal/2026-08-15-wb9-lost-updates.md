---
title: WB-9 — concurrent saves can no longer swallow changes
date: 2026-08-15
tags: [bugfix, decision]
summary: Write lock plus version counter — set_result merges with concurrent user edits by construction, stale board saves are rejected with a 409 and a German message; 56 tests green incl. a threaded hammer test; restart pending.
outcome: done
---

# WB-9 — concurrent saves can no longer swallow changes

## What was asked

Ticket WB-9 (board-dispatched run): user edits and dispatcher result writes to
the same ticket file could lose one side silently (lost update). Wanted: a
protection matching the project's size, decision documented; a test with two
competing writes where nothing is silently lost; stdlib only.

## What I did

- store.py — two complementary mechanisms (the decision the ticket asked me to
  document):
  1. **Process-wide `threading.RLock`** around create/update/set_result. All
     writers in the server process (HTTP handler threads + dispatcher worker)
     go through these functions, so read-modify-write is now atomic; set_result
     additionally re-reads under the lock and only replaces `## Ergebnis`, so a
     user edit saved in between is merged, not overwritten. Reentrant because
     set_result calls update_ticket.
  2. **Optimistic version counter** for stale-base saves (the lock cannot catch
     these: the user's dialog may hold minutes-old data). New frontmatter field
     `version` (int as string, legacy files default 1), bumped on every write.
     A write carrying `version` is rejected with `ConflictError` (German
     message) when the file has moved on.
- server.py: `ConflictError` → HTTP 409. board.html: detail-save and
  reject-with-reason send their base version (the two writes that can destroy
  body content); status-only writes (drag, accept, retry) carry none. On error
  the board shows the message and refreshes.
- Tests first (red: 1 failure/2 errors → green): stale write rejected and
  nothing overwritten; current-version write accepted and bumps; set_result
  merges with a concurrent user edit; threaded hammer (2×25 competing writes →
  file parses, last title and last result both present, version == 1+2n so
  every write is accounted for). Suite: 56 tests green; both script blocks pass
  `node --check`.
- CHANGELOG + docs/user bullet.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- Lock + version: the in-process RLock closes the handler-vs-dispatcher race,
  the version check covers the human-timescale stale-dialog case the lock
  can't. While this run was working, a concurrent edit (other session) added a
  third layer — an fcntl flock on `tickets/.lock` inside a `_locked`
  contextmanager — extending protection to OTHER processes (chat sessions
  writing through this module), and a `ConcurrentWriteTest` expecting an
  explicit `expected_version=` kwarg. Reconciled: update_ticket now accepts
  the base version both ways (kwarg wins), all 61 tests green together.
  Hand edits with a plain editor remain outside any lock; git is the backstop.

## Follow-ups

- Board restart needed to activate (server.py changed) — requested in the
  Ergebnis; dispatched runs must not restart the board (WB-17 rule).
