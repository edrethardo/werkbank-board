---
title: Code assessment + backlog tickets WB-8–12
date: 2026-08-15
tags: [investigation, docs]
summary: Reviewed the whole codebase for the user, named three weaknesses honestly, and filed them plus two user-requested features as tickets WB-8–12.
outcome: done
---

# Code assessment + backlog tickets WB-8–12

## What was asked

The user asked what the project is about and for an honest opinion on code quality
and architecture; then to file tickets for the weaknesses found, plus a ticket for a
create-ticket-via-chat skill and (mid-turn) one for ticket links with hover tooltips.

## What I did

- Read all of `src/werkbank/` (~720 lines) plus `docs/dev/stack.md` and both indexes.
  Verdict delivered to the user: solid for its size — files as source of truth, zero
  dependencies, serialized dispatch, XSS-safe rendering — with three real weaknesses.
- Created tickets via `store.create_ticket` (canonical IDs/format, not hand-written):
  - **WB-8** robust board loading — one malformed ticket file currently 500s
    `/api/tickets` and blanks the whole board (`load_tickets` raises on first parse error).
  - **WB-9** lost-update protection — board edit vs. dispatcher `set_result` on the
    same file: last writer wins silently.
  - **WB-10** render only on change — board re-renders all columns every 5 s.
  - **WB-11** skill: create tickets via chat (user request).
  - **WB-12** ticket links: "must run after X", "not concurrently with X", shown on
    cards with hover tooltips; dispatcher must respect them (user request).
- Committed accumulated ticket-state files WB-3–7 that board/agent activity had left
  uncommitted ("Record ticket states from board activity"), then the new tickets
  ("Add backlog tickets WB-8–12 from code assessment + user requests").

## What I tried that didn't work — and why

Nothing failed. One surprise worth recording: while this session was answering, the
board's drag-to-dispatch worked WB-7 (dark mode) concurrently — commits 1d804d0/9142bff
appeared and the git snapshot from session start went stale. Checked `pgrep -af 'claude -p'`
and `/tmp/werkbank-agent-WB-7.log` before touching git to avoid racing the run.

## Decisions made

- Committed the WB-3–7 ticket files myself: dispatched agents are told not to touch
  ticket files, and the werkbank server writes but never commits them — so the
  interactive werkbank session is the only place they get committed.
- Priorities: WB-8 normal (first-worth-fixing, not acute), WB-9/10 niedrig (tiny window /
  only matters at scale), WB-11/12 normal (user-requested features).

## Follow-ups

- Ticket files accumulate uncommitted whenever the board runs without an interactive
  session — recurring; maybe the server should commit ticket writes itself (would need
  a decision + ticket).
- WB-8 is the one to work first.
