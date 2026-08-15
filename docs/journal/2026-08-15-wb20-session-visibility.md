---
title: WB-20 — board shows who works a ticket, session persisted after the run
date: 2026-08-15
tags: [bugfix, feature]
summary: Dispatcher publishes active-run info (parent session, fork, start time) into the tickets API, in_arbeit cards and the detail dialog render it, and the run's real session id is persisted into a new `session` frontmatter field; 75 tests green.
outcome: done
---

# WB-20 — board shows who works a ticket, session persisted after the run

## What was asked

Bug ticket WB-20 (pulled in chat, user reported twice): nothing on the board
shows WHICH session is working an in_arbeit ticket, since when, or — after the
run — who did it.

## How the gap was shown (bug discipline)

The data plainly did not exist anywhere the board could read: run_claude knew
mode/parent only in a local loop variable and returned only the result text;
the GET payload had no run information. New tests written first (4 red:
active_runs missing, tuple return missing, session field missing), green after.

## What I did

- store: new frontmatter key `session` (default empty, legacy files valid),
  updatable; persisted by the finalizer from the run's real `session_id`.
- dispatch: `run_claude(t, cfg, on_start=None)` now returns `(result,
  session_id)` and reports per attempt what is CERTAIN at start time
  ({parent, forked, mode}) — nothing invented; the run's own id is only known
  after the JSON output. `Dispatcher.active_runs()` exposes the active run
  ({parent, forked, mode, started HH:MM}); registry cleared in a finally.
  Older single-arg runners (tests) still work via a TypeError fallback —
  pragmatic, noted: a TypeError raised inside a runner would double-call it.
- server: GET /api/tickets includes `runs`. board.html: in_arbeit cards get a
  run-info line ("⏱ seit 10:42 · ⑂ Abzweigung von 46eda3a7…", honest
  fallback text when no board run is active — queued or chat-worked); detail
  dialog shows full parent id, persisted `session`, and the log path; `runs`
  is part of the WB-10 render snapshot so the line appears without flicker
  hacks.
- Existing WB-14/18/19 tests updated to the tuple contract (their stubs
  already carried session ids); roundtrip SAMPLE gained the `session:` line.
  75 tests green, both script blocks node --check OK.
- Chat-session registration per WB-19: this session registered itself via
  $CLAUDE_CODE_SESSION_ID after finishing the ticket.

## What I tried that didn't work — and why

Nothing failed. Board-visual verification is again left to the user (no
browser automation here); the mechanism is fully covered by unit tests and
the stub-claude end-to-end test.

## Decisions made

- Transient run info lives in the dispatcher (API-only), the durable fact
  (who worked it) lives in the ticket file — the file stays the source of
  truth for history, the API for live state.
- During a run the card shows the PARENT session (what is provably known);
  the run's own id is written once known. Criterion "nichts erfinden" met.

## Follow-ups

- WB-21 (test ticket, waits on WB-20) is ready to be dragged once WB-20 is
  accepted — it will show the new line live.
