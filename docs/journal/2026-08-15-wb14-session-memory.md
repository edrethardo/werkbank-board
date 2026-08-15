---
title: WB-14 fixed — dispatch remembers and resumes the last ticket session
date: 2026-08-15
tags: [bugfix, feature]
summary: Dispatch now stores the last ticket session per project in state.json and forks it via --resume; live-proved that the fork descends from the remembered parent. Supersedes the --continue design note in the drag-dispatch entry.
outcome: done
---

# WB-14 fixed — dispatch remembers and resumes the last ticket session

## What was asked

Bug ticket WB-14: drag-started agents forked whatever session was ACTIVE last in
the target project (--continue semantics), not the last ticket session — proven
by WB-4/WB-7 forks containing live user-chat text. Fix sketch and staged red
tests were already in the tree.

## What I did

- Debugging first: the previous WB-14 run's failure (log
  /tmp/werkbank-agent-WB-14.log) was a 429 usage limit ("resets 4:30am"), not a
  code bug — no separate fix needed; the WB-15 Fehlgeschlagen column is the
  designed landing place for exactly this.
- `dispatch.py`: `load_last_session`/`save_last_session` (per-project map in
  `state.json`, repo root, gitignored; corrupt/missing → None),
  `attempt_modes` (resume → continue → fresh; continue only if project history
  exists), `build_command` takes a mode, `run_claude` walks the chain and saves
  the new `session_id` after every successful run.
- `server.py`: `state_path` config default; `.gitignore`: `state.json`.
- Tests: all 35 green, including the 11 pre-staged WB-14 tests (fake-claude
  binary exercises resume, stale-id fallback, re-remembering).
- Live proof (ticket acceptance): `claude -p --resume 61f11c26… --fork-session`
  (the WB-7 fork named in the ticket) exited 0; the answer came from the
  parent's memory ("WB-7 — Dark Mode…"), and the new session file
  5e63e172-….jsonl contains the parent's marker phrase "bewusst klein
  gehaltene" (1 hit) and 10 WB-7 mentions. Fork lineage confirmed at both the
  semantic and file level.
- docs/user wording ("setzt die letzte Ticket-Session fort") + CHANGELOG.

## What I tried that didn't work — and why

Nothing failed. Known residual risk, accepted consciously: the live proof ran a
second `claude -p` concurrently with this run (the serialization exists to
avoid the claude.json corruption bug); overlap was seconds and nothing
corrupted (verified: this run continued normally).

## Decisions made

- state.json in repo root (not ~/.claude): machine-local cache, gitignored —
  losing it degrades gracefully to --continue, so no backup needed.
- Session id saved on EVERY successful run (also fresh/continue starts), so the
  lineage begins with the first run in any project.
- The full board-level end-to-end proof (ticket run → chat → next run resumes)
  requires the restarted server; unit + live-resume proof cover the mechanism.

## Follow-ups

- Board restart still pending (also carries WB-13/WB-15 changes); after it, the
  next two ticket runs in any project double as the final end-to-end proof.
- The design note in 2026-08-15-drag-dispatch.md ("--continue delivers the
  last-session semantics") is superseded by this entry.
