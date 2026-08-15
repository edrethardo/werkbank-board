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

- 2026-08-14 — [Stack decision — plain files + stdlib Python board](dev/stack.md) — decision, setup — Tickets are markdown files with flat frontmatter; the board is a dependency-free Python stdlib server with a vanilla-JS page.
- 2026-08-14 — [Rule-to-mechanism map](dev/rule-mechanism-map.md) — meta — audit that every CLAUDE.md hard rule has an operational mechanism.
- 2026-08-14 — [OS coverage status](dev/os-coverage.md) — meta, setup — which OSes the init flow is actually verified on.
