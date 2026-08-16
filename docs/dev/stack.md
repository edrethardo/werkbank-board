---
title: Stack decision — plain files + stdlib Python board
date: 2026-08-14
tags: [decision, setup]
summary: Tickets are markdown files with flat frontmatter; the Kanban board is a dependency-free Python 3 stdlib HTTP server serving one vanilla-JS page.
---

# Stack decision

Decided 2026-08-14 at first build (journal entry `2026-08-14-init.md`).

## What

- **Ticket storage:** one markdown file per ticket in `tickets/`, named
  `WB-<n>-<slug>.md`. Flat `key: value` frontmatter (no YAML nesting), parsed by our
  own ~30-line parser — no PyYAML dependency. Human-readable, git-diffable, and agents
  can read/write tickets with plain file tools.
- **Board:** `src/werkbank/server.py`, Python 3 stdlib only (`http.server`), serving
  `src/werkbank/board.html` (vanilla JS, drag & drop) plus a small JSON API
  (list/create/update). Default port 8765, configurable in `config.json`.
- **Agent dispatch:** no daemon. The user asks the assistant to work the tickets; the
  assistant follows the `work-tickets` project skill and spawns subagents per ticket.
- **Statuses:** `offen → in_arbeit → review → erledigt`. Agents stop at `review`; only
  the user moves tickets to `erledigt`.

## Why

Stack policy says simplest thing that works. Plain files make the ticket data
inspectable and recoverable without any running service; the board is read/write UI on
top, not the source of truth. Python 3 is present on this machine (Ubuntu); zero
dependencies means nothing to install or update.

## Rejected

- **Web framework (Flask/FastAPI) + database:** dependencies and a schema for what is
  a handful of markdown files. Nothing here needs queries or concurrency beyond one
  user.
- **Background watcher that auto-dispatches agents:** user explicitly chose on-demand
  dispatch; a daemon adds moving parts that can silently die.
- **Node/npm frontend build:** a single static HTML file needs no toolchain.
