---
title: WB-13 — bug ticket type, report-bug skill, debugging discipline
date: 2026-08-15
tags: [feature, skill]
summary: Tickets now carry type aufgabe/bug (board select + red BUG badge, legacy files stay valid); werkbank-report-bug skill staged; work-tickets/pull skills gain mandatory bug-debugging discipline.
outcome: done
---

# WB-13 — bug ticket type, report-bug skill, debugging discipline

## What was asked

Ticket WB-13 (dispatched run): (1) ticket `type` aufgabe|bug with board UI and red
BUG badge, legacy tickets without the field stay valid; (2) user-level skill
`werkbank-report-bug` (staged like werkbank-pull-ticket); (3) work-tickets/pull
skills demand debugging discipline for bugs (reproduce → fix → regression test,
evidence in Ergebnis).

## What I did

- TDD: 5 new tests in `tests/test_store.py` (type default, legacy parse fallback,
  create/update validation) and 1 in `tests/test_dispatch.py` (bug prompt) — red
  first (1 failure + 6 errors), then green. 23/23 pass.
- `store.py`: `TYPES`, `type` field (default `aufgabe`), in `KEYS` after `title`;
  parse falls back for legacy files (they upgrade on next write); create/update
  validate it. `server.py` passes `type` on create. `dispatch.py` injects a
  `BUG_DISCIPLINE` block into the prompt for `type: bug`.
- `board.html`: Typ select in create + detail dialogs, red BUG badge on cards
  (created via `textContent`, no injection surface), detail save sends `type`.
- Skills: `staged-skills/werkbank-report-bug/` (new, v1 — three questions, severity
  → priority, files via `store.create_ticket`, commit, never auto-fix);
  `staged-skills/werkbank-pull-ticket/` updated to v2 (type in format, bug
  discipline); `staged-skills/work-tickets/` v3 staged because `.claude/` is
  write-protected for dispatched runs (same block as WB-5) — README gained the
  project-skill exception for installs.
- Docs: user doc section "Bugs melden" + Typ paragraph; CHANGELOG entry.

## What I tried that didn't work — and why

- Editing `.claude/skills/work-tickets/SKILL.md` directly: permission denied
  (protected path for dispatched runs — second occurrence after WB-5). Staged the
  v3 copy instead; the Werkbank session installs it.
- Verifying the live board via curl: not permitted in this run's sandbox. Left as
  an explicit restart/visual check for review.

## Decisions made

- Frontmatter key stays English (`type`) with German values (`aufgabe`/`bug`) —
  consistent with `status`/`priority` style (English keys, German values).
- Legacy files are not rewritten in bulk; they upgrade whenever next written.
- Drag-to-dispatch also enforces bug discipline (BUG_DISCIPLINE in build_prompt),
  not just the chat skills — same feature, both paths.

## Follow-ups

- **Board restart required** ("Board neu starten"): the running server process has
  the old modules. Until restart, the freshly served page sends `type` on detail
  save and the old server rejects it with 400 ("cannot update keys") — board
  editing errors; creating works but drops the type. This dispatched run could not
  restart the board (it is its own parent process).
- Werkbank session: install staged skills (report-bug + pull v2 → `_user-level/` +
  `~/.claude/skills/`; work-tickets v3 → `.claude/skills/`), then CHANGELOG line
  for the chat bug reporting, then empty `staged-skills/`.
