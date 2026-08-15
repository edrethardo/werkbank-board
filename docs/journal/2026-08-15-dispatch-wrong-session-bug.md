---
title: Bug found — dispatch forks the latest session, not the last ticket session
date: 2026-08-15
tags: [investigation, bugfix]
summary: User-reported and verified — --continue forks whatever session was active last (WB-4/WB-7 forked the live user chat); filed as WB-14 (bug, hoch) with evidence and fix sketch.
outcome: partial
---

# Bug found — dispatch forks the latest session, not the last ticket session

## What was asked

The user reported a bug: a dispatched ticket agent should always continue the session
that last worked a ticket in the target project. Then: file it as a bug ticket.

## What I did

- Verified the report instead of taking it on faith (systematic-debugging):
  - WB-7's run log (`/tmp/werkbank-agent-WB-7.log`) names its fork session
    `61f11c26-6d07-4fc5-8183-c9636d649c55`.
  - `grep -l "bewusst klein gehaltene" ~/.claude/projects/-projekt-slug/*.jsonl`
    (a phrase unique to the live user chat) hits the chat session (20a6ae6e), the WB-7
    fork (61f11c26) AND the WB-4 fork (1b1a6fd8) — both dispatched runs forked the
    live interactive conversation, not the previous ticket worker.
- Root cause: `claude -p --continue` means "most recently modified session in the
  project directory". Any interleaved conversation steals the fork parent. The
  original design (journal 2026-08-15-drag-dispatch.md) said "session that last
  worked on that project"; the user has sharpened this to "…that last worked a
  TICKET"; `--continue` satisfies neither reliably.
- Filed **WB-14** (type bug, priority hoch) with the evidence, fix sketch and
  acceptance criteria; committed as "Add bug ticket WB-14: …".

## What I tried that didn't work — and why

Nothing failed. Note: not fixed yet — ticket only, at the user's choice.

## Decisions made

- Fix direction recorded in the ticket: persist the fork's `session_id` per project
  (JSON output is already parsed in `run_claude`), next dispatch uses
  `--resume <id> --fork-session`, falling back to `--continue`, then fresh.
  A nice side effect: ticket runs then chain, each fork inheriting prior ticket work.
- Priority hoch: the bug silently breaks the feature's core promise about context.

## Follow-ups

- Whoever works WB-14: verify `claude -p --resume <id> --fork-session` headless once
  for real before claiming done, and prove the fork parent via the session files
  (grep trick above).
