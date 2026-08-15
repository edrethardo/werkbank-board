---
title: Drag-to-dispatch — dragging to In Arbeit spawns a forked claude run
date: 2026-08-15
tags: [feature, decision, investigation]
summary: Dragging a ticket to In Arbeit now spawns claude -p (forked continuation of the target project's last session, serialized); verified by a live smoke test triggered by the user's own drag.
outcome: done
---

# Drag-to-dispatch — dragging to In Arbeit spawns a forked claude run

## What was asked

User: dragging a ticket into "In Arbeit" should START the work; the worker is "the
session that last worked on that project".

## What I did

- Feasibility first: probed whether an external process can message a running
  session, consulted the claude-code-guide agent (sources: sessions.md,
  headless.md, permission-modes.md, remote-control.md). Findings: no supported
  external push into a live session; `claude -p --continue --fork-session` is the
  supported way to continue a project's latest conversation without touching it;
  concurrent `claude -p` corrupts ~/.claude/claude.json (issues #29051/#28813) →
  serialize.
- `src/werkbank/dispatch.py`: project_slug/has_history detection via
  ~/.claude/projects/<slug>/*.jsonl; build_command (fresh-session fallback, plus
  runtime retry without --continue); run_claude with timeout + per-ticket log
  /tmp/werkbank-agent-<id>.log; Dispatcher = single worker thread, FIFO, duplicate
  dispatch ignored, every outcome (success, DispatchError, internal error) lands in
  `## Ergebnis` + status `review`.
- store.set_result (replaces Ergebnis, keeps Beschreibung). Server triggers
  dispatch only on the offen→in_arbeit transition via the API and only for
  assignee `claude` (others get a not-supported note in Ergebnis).
- Agent runs use `--permission-mode acceptEdits --allowedTools Bash
  --output-format json`, configurable in config.json (also agent_timeout_minutes).
- Tests: 18 green (`python3 -m unittest tests.test_store tests.test_dispatch`).
- Live verification: smoke ticket WB-3 targeting a scratch dir; the USER dragged it
  in the real board → fresh-session path created hallo.txt with exact content,
  honest German Ergebnis, status review. The continue+fork path verified directly
  with the dispatcher's exact flags against this repo → answered from forked
  context ("OK Werkbank"), original session untouched.
- Docs/user updated, CHANGELOG, work-tickets skill v2 (notes the second dispatch
  path), this journal.

## What I tried that didn't work — and why

- **Raw UDS probe:** sent a JSON line to my own session socket
  (/run/user/1000/cc-socks/1670845.sock) — no reply, message never arrived.
  Confirmed by docs research: the sockets are internal, no public contract. Do not
  build on them.
- **Auto-mode classifier blocks:** restarting the board (now an agent-spawner) and
  even one commit were blocked mid-work. Correct response was to stop and get the
  user's explicit go (they approved "scharf schalten"); after approval everything
  ran. Expect the same classifier friction when server.py's capabilities grow.
- pkill/pgrep matching the invoking shell's own command line again (exit 144 /
  phantom PIDs) — check the PORT with curl instead of pgrep to determine server
  state.

## Decisions made

- Spawn-per-ticket over any socket hackery: only documented mechanism, and
  --fork-session avoids the two-writers problem while delivering "the session that
  last worked here" semantics.
- Strictly serialized runs (known config-corruption bug with concurrent -p runs).
- acceptEdits + Bash allowlist as default power level — explained to the user with
  worst case before arming; they explicitly approved. Tightening knob exists in
  config.json.

## Follow-ups

- WB-3 (smoke ticket) sits in review for the user to accept/delete.
- The in-board drag was user-verified; assignee≠claude drag path (note in
  Ergebnis) is unit-level only.
- If parallel runs are ever wanted, revisit the claude.json corruption issue first.
