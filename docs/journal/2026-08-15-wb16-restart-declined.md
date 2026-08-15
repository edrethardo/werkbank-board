---
title: WB-16 — restart request: verified current, deliberately not executed
date: 2026-08-15
tags: [investigation]
summary: The restart ticket hit the self-kill trap (a dispatched run restarting the board kills its own dispatcher) plus a queued WB-12 run; board was verified to already run the newest code, so the run declined and put the decision back to the user per the ticket's own instruction.
outcome: partial
---

# WB-16 — restart request: verified current, deliberately not executed

## What was asked

Ticket WB-16 "starte werkbank neu." with the description "frag im zweifelsfall
den nutzer um erlaubnis" — dispatched to a headless run that cannot ask.

## What I did

- Verified the running server (PID 787823, port 8765) already serves the NEWEST
  code: `/api/tickets` answers and includes the `nach` field introduced by
  WB-12's commit — i.e. the board was restarted at the end of the WB-12 run and
  nothing server-side changed since. A restart would be a no-op.
- Found `in_arbeit: [WB-12, WB-16]` — WB-16 is this run; WB-12 is queued/running
  again behind it. A scheduled restart (the only way a dispatched run can restart
  the board without killing itself — the dispatcher is this run's parent) would
  risk killing that queued run mid-flight, leaving WB-12 stuck in in_arbeit.
- Decision per the ticket's own instruction ("frag im zweifelsfall"): declined to
  restart, wrote the evidence and the open question into `## Ergebnis`, status
  review. No code, docs/user or CHANGELOG changes — operational, nothing
  user-visible shipped.

## What I tried that didn't work — and why

Nothing failed; the restart was not attempted by design. (Known trap documented
for future runs: a board-dispatched run must NEVER kill/restart the server
synchronously — the dispatcher waiting on it dies before writing the result. A
delayed detached restart is possible but unsafe while other tickets are queued.)

## Decisions made

- "Already current + would kill a queued run + ticket demands asking when in
  doubt" → don't act, surface via review. The review/reject loop is the
  headless substitute for asking permission.

## Follow-ups

- If the user confirms they want a hard restart anyway: the CHAT session should
  do it via start-board (safe: not a child of the dispatcher), ideally when no
  ticket is in_arbeit. Also: start-board skill still needs the port-PID stop
  route (follow-up from WB-12, still open).
