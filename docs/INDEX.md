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
- 2026-08-17 — [opencode-Beispiel — vom Anlegen bis zum grünen Gate](user/opencode-beispiel.md) — docs, feature — Ein durchgespieltes Beispiel: einen kleinen Bugfix an das lokale Modell (opencode) geben, mit einer Prüfung als Abnahmekriterium. Ende zu Ende — von der `config.json` bis zur grünen Karte.

## Developer documentation (docs/dev/)

- 2026-08-17 — [Release testing](dev/release-testing.md) — setup, decision — The fresh-machine check before every public release: frozen worktree, sandboxed smoke test of the README's own steps, both CI jobs; what it has caught and what it still does not cover.

- 2026-08-20 — [OBSOLET, nicht anwenden — WB-259 — proposed patch for the werkbank-pull-ticket skill (watcher loop uses wall-clock deadline)](dev/wb259-watcher-loop-patch.md) — decision, docs — Step 5's watcher loop used `$SECONDS` which fired immediately in the way the background bash is wrapped, and a `/tmp`-based workaround aborted on cleanup; carries the exact v10 replacement (wall-clock `date +%s` deadline, inline, `TIMEOUT` exits 0, verify-after-seconds note) for the user to apply — dispatched runs cannot write to `.claude/skills/`.
- 2026-08-16 — [ANGEWANDT — WB-166 — proposed patch for the create-ticket skill (opencode needs a gate at creation)](dev/wb166-create-ticket-skill-patch.md) — decision, docs — The create-ticket skill's current snippet forwards neither `assignee=` nor `gate=`, so an opencode recommendation silently becomes a claude ticket; carries the exact v6 replacement (adds §1a "Gate for opencode — resolve it BEFORE creating") for the user to apply — dispatched runs cannot write to `.claude/skills/`.
- 2026-08-16 — [Windows release plan (WB-160)](dev/windows-release-plan.md) — decision, docs — **RESOLVED 2026-08-17 (WB-182): option 2 taken, Windows CI is green.** The record of how it looked when every push was red, and the categories of breakage that had to close.
- 2026-08-16 — [Benchmark — opencode lane vs a forked Claude run](dev/benchmarks/README.md) — investigation, decision — Measured three real tickets: local lane ~96 % cheaper, 3–8× slower, and it did not finish the hardest one inside its hour.
- 2026-08-16 — [Releasing the public copy](dev/release-sync.md) — decision, setup — Why the export drops the journal while the sync preserves the published sample, why the publisher never ships, and the checks-that-exempt-themselves pattern.

- 2026-08-16 — [opencode dispatch — the named check is the acceptance criterion](dev/opencode-gate-dispatch.md) — decision — Why an opencode ticket is accepted only on a configured check, and why the ticket names the check instead of carrying the command.
- 2026-08-16 — [Board internals](dev/board-internals.md) — docs — How the board, queue, dispatch and hardening actually work, for someone reading the code for the first time.

- 2026-08-15 — [Release-Plan 1.0 (WB-46)](dev/release-plan-1.0.md) — decision, docs — Review-Ergebnisse und die Ticket-Kette bis zum 1.0-Release.

- 2026-08-14 — [Stack decision — plain files + stdlib Python board](dev/stack.md) — decision, setup — Tickets are markdown files with flat frontmatter; the board is a dependency-free Python stdlib server with a vanilla-JS page.
- 2026-08-14 — [Rule-to-mechanism map](dev/rule-mechanism-map.md) — meta — audit that every CLAUDE.md hard rule has an operational mechanism.
- 2026-08-14 — [OS coverage status](dev/os-coverage.md) — meta, setup — which OSes the init flow is actually verified on.
