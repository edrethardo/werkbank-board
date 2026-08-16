---
title: Documentation index
date: 2026-08-14
tags: [meta]
summary: Topic-grouped index of every document in docs/user/ and docs/dev/.
---

# Documentation Index

One line per document, grouped by folder: `date — [title](path) — tags — summary`.
An unindexed document is a bug (see the `documenting` skill). Read this index and
`docs/journal/INDEX.md` before investigating anything (`finding-knowledge` skill).

## User documentation (docs/user/)

- 2026-08-14 — [Was ist die Werkbank?](user/about.md) — docs — Zweck des Tools in Kurzform: ein Ticket-System für Claude-Code-Agenten.
- 2026-08-15 — [Das Board und die Tickets benutzen](user/board-und-tickets.md) — docs, feature — Wie man das Kanban-Board öffnet, Tickets anlegt und sie von Agenten abarbeiten lässt; Einrichtung per „init".

## Developer documentation (docs/dev/)

- 2026-08-16 — [Releasing the public copy](dev/release-sync.md) — decision, setup — Why the export drops the journal while the sync preserves the published sample, why the publisher never ships, and the checks-that-exempt-themselves pattern.

- 2026-08-16 — [opencode dispatch — the named check is the acceptance criterion](dev/opencode-gate-dispatch.md) — decision — Why an opencode ticket is accepted only on a configured check, and why the ticket names the check instead of carrying the command.
- 2026-08-16 — [Board internals](dev/board-internals.md) — docs — How the board, queue, dispatch and hardening actually work, for someone reading the code for the first time.

- 2026-08-15 — [Release-Plan 1.0 (WB-46)](dev/release-plan-1.0.md) — decision, docs — Review-Ergebnisse und die Ticket-Kette bis zum 1.0-Release.

- 2026-08-14 — [Stack decision — plain files + stdlib Python board](dev/stack.md) — decision, setup — Tickets are markdown files with flat frontmatter; the board is a dependency-free Python stdlib server with a vanilla-JS page.
- 2026-08-14 — [Rule-to-mechanism map](dev/rule-mechanism-map.md) — meta — audit that every CLAUDE.md hard rule has an operational mechanism.
- 2026-08-14 — [OS coverage status](dev/os-coverage.md) — meta, setup — which OSes the init flow is actually verified on.
