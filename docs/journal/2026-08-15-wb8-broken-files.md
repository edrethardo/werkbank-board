---
title: WB-8 — a broken ticket file no longer takes down the board
date: 2026-08-15
tags: [bugfix, feature]
summary: load_tickets now skips unreadable files; /api/tickets returns them in an errors list and the board shows a red per-file banner with a German reason; 49 tests green, server restart still pending.
outcome: done
---

# WB-8 — a broken ticket file no longer takes down the board

## What was asked

Ticket WB-8 (board-dispatched run): one malformed file in tickets/ used to break
/api/tickets entirely. A broken file must only affect itself; the board must show
which file is broken and why, in plain language. Tests required.

## What I did

- store.py: new `load_tickets_with_errors` returns (tickets, errors) where errors
  carry `file` + German `error`; `load_tickets` wraps it and silently skips broken
  files — so dispatcher, blocking checks and skills also survive a bad file.
  parse_ticket's three error messages are now German (they are user-visible via
  the board banner): missing frontmatter block, unreadable line, missing required
  keys. Catches ValueError/OSError/UnicodeDecodeError per file.
- server.py: GET /api/tickets now returns `errors` alongside tickets.
- board.html: red-bordered banner rows above the board, one per broken file
  ("⚠️ Kaputte Ticket-Datei „<name>“ — <Grund>…"), hidden when empty.
- Tests first (red → green): broken file between healthy ones is skipped;
  errors list names file + reason for two distinct corruption kinds; healthy dir
  yields empty errors. Suite: 49 tests, all green. Both script blocks pass
  `node --check`.
- docs/user bullet + CHANGELOG entry (marked as active after next restart).

## What I tried that didn't work — and why

Nothing failed. Note: the running server still has the old code — this run is
forbidden to restart the board (WB-17 rule: the restart would kill the step that
writes this very result). Restart request goes in the Ergebnis.

## Decisions made

- Harden `load_tickets` itself instead of only the API path: every caller
  (dispatcher recheck, pull skills) gets the only-affects-itself behavior for
  free, and the API stays the single place that surfaces the error list.
- German parse-error texts: they are end-user-facing now; code and identifiers
  stay English.

## Follow-ups

- Board restart needed to activate (user or chat session; not from a dispatched
  run).
