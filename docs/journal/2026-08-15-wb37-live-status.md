---
title: WB-37 — live agent status via stream-json (progress, stalls, quota)
date: 2026-08-15
tags: [feature, investigation, decision]
summary: Dispatch switched from buffered json to stream-json + Popen, so runs report progress, tool use, tokens and the CLI's own rate_limit_event live; board shows it, stalls and quota exhaustion are named in German; 108 tests plus a real live run.
outcome: done
---

# WB-37 — live agent status via stream-json (progress, stalls, quota)

## What was asked

Ticket WB-37 (chat handover): a status display for the agent — see whether it
aborts, needs something, or hit a token limit; orient on the agent_monitor repo.

## Prior art check (as the ticket asked)

Read `~/code/agent_monitor` (agent-deck): it watches INTERACTIVE
sessions from the outside — Claude Code hooks + transcript tails + /proc scan,
rendered on a macropad. Werkbank's case is the opposite and simpler: we OWN the
`claude -p` subprocess, so the honest source of truth is its own event stream —
no hooks, no transcript scraping. Concept borrowed: distinguish "working",
"blocked/needs you", "hit a limit" as first-class states.

## What I did

- `build_command`: `--output-format stream-json --verbose` (was buffered json).
- `run_claude`: Popen + line loop instead of `subprocess.run`. Each line is
  written to the log AND flushed (log is live now), parsed, folded into a
  `progress` dict by `_consume_event` (steps = tool_use count, last_tool,
  tokens = max(input+output), session id, quota) and pushed to a new
  `on_event` callback. Watchdog timer kills the process at the timeout.
  Result event carries the final text/session; `is_error`/non-success subtype
  becomes a DispatchError. Backwards compatible with the old single-object
  json line (tests' stubs) — a line with `result` and no `type` still counts.
- `classify_failure`: usage/rate/session limit and auth failures become plain
  German causes; a limit failure aborts the fallback chain instead of burning
  another attempt.
- `Dispatcher`: `_runs[id]` now carries steps/last_tool/tokens/error/limit plus
  `last`/`last_ts`; `active_runs()` derives `idle_seconds` (stall detection).
  Runner kwargs are chosen via `inspect.signature`, so old runner signatures in
  tests keep working without try/except TypeError guessing.
- board: in_arbeit cards show progress, quota (≥75 % or blocked), stall
  (≥60 s "still seit", ≥180 s red "keine Rückmeldung") and agent errors in red;
  tooltip carries the last heartbeat and log path. The refresh snapshot buckets
  `idle_seconds` (30 s) so the ticking counter cannot break WB-10's no-flicker.
- Docs: user manual section rewritten, CHANGELOG entry.

## Verification

- 108 tests green, incl. 7 new: stream parsing/progress, usage-limit wording,
  error result event, log-is-live-during-run, idle_seconds, quota event
  (warning + rejected).
- **Live against the real CLI:** a scratch ticket ran a real `claude -p`
  through the dispatcher — 6 progress snapshots during the run, tokens and the
  real `rate_limit_event` (82 %, seven_day) captured live, ticket finalized to
  review with its session id, 18 KB log written while running.

## What I tried that didn't work — and why

Nothing failed outright, but two assumptions needed checking against reality
rather than docs: (1) that `stream-json` works in `-p` mode at all — verified
by running it (event types: system, assistant, rate_limit_event, result);
(2) that quota state is even observable — it turned out the CLI emits
`rate_limit_event` with utilization/status/resetsAt, which is strictly better
than inferring a limit from a crash. The stub-only tests would have "passed"
against a wrong format, so the live check was the load-bearing one.

## Decisions made

- Read the agent's own event stream instead of copying agent-deck's hook/
  transcript approach: our runs are headless children, hooks would not fire.
- Quota is shown from the CLI's own numbers only — never estimated.

## Follow-ups

- A blocked-on-permission state is not distinguishable yet in headless runs
  (acceptEdits + allowlist means denials appear as tool errors); if that ever
  matters, the tool_result events would be the place to look.
