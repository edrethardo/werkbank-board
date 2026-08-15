---
title: WB-34 — board autostart via systemd user service, host stays localhost
date: 2026-08-15
tags: [feature, setup, skill, decision]
summary: User chose autostart + localhost-only in the mandated interview; werkbank-board.service (Restart=always) survives login and kill -9 (verified live); host now configurable but LAN exposure is a spelled-out-risk user decision; start-board skill v2.
outcome: done
---

# WB-34 — board autostart via systemd user service, host stays localhost

## What was asked

Ticket WB-34 (chat handover): create a skill for starting the Werkbank, ask the
user whether it should start every time, localhost-only vs local network — with
risk warnings for the network case.

## What I did

- Asked both mandated questions (AskUserQuestion, LAN option carried the full
  risk text: no login, LAN devices could read/change tickets and start
  command-executing agent runs). Answers: autostart YES, localhost only.
- `~/.config/systemd/user/werkbank-board.service`: ExecStart on server.py,
  WorkingDirectory repo root, `Restart=always`, RestartSec 3, enabled via
  `systemctl --user enable --now`.
- server.py binds `CONFIG["host"]` (default and configured: 127.0.0.1);
  config.json carries `host` explicitly with the never-without-owner rule
  commented at the bind site.
- Live verification: service active and serving; kill -9 of the main PID →
  back up within 5 s on a new PID (first probe with SIGTERM taught me that
  systemd counts SIGTERM as a CLEAN exit, so `Restart=on-failure` did NOT
  restart — switched to `Restart=always`, which also matches the "läuft
  einfach immer" expectation).
- start-board skill v2: systemctl-based operations, journalctl for logs,
  restart-safety rules carried over, LAN exposure gated on an informed user
  decision, manual fallback kept.
- User doc (board section) and CHANGELOG updated.

## What I tried that didn't work — and why

- `Restart=on-failure` + SIGTERM probe: service stayed `inactive` — SIGTERM is
  a clean stop for systemd (SIGHUP/INT/TERM/PIPE count as success). Evidence:
  `systemctl --user is-active` → inactive after the kill. Fixed with
  `Restart=always`, re-proven with SIGKILL.

## Decisions made

- systemd user unit (not linger, not a system unit): starts at login, runs as
  the user, removable with one disable command; the board is a per-user tool.
- `host` configurable but defaulting hard to 127.0.0.1; the skill forbids
  flipping it without the spelled-out risk conversation.

## Follow-ups

- The unit file lives outside the repo (~/.config) — documented here and in the
  skill; recreating it on a new machine is part of the skill's fallback story.
