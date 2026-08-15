---
title: WB-5 — werkbank-pull-ticket skill written, staged for installation
date: 2026-08-15
tags: [skill, feature]
summary: Dispatched run wrote the pull-your-ticket skill (find the open ticket for this project, clarity gate with mandatory questions, claim, work, report) but could not install it — .claude/ is permission-protected in headless runs, so it sits in staged-skills/ awaiting the Werkbank session.
outcome: partial
---

# WB-5 — werkbank-pull-ticket skill written, staged for installation

## What was asked

Ticket WB-5: write a skill that lets an agent find the ticket matching its project
and start working it, asking clarifying questions when the description is vague or
the agent is unsure.

## What I did

(This entry was written by a board-dispatched run — a forked continuation of the
Werkbank session, headless.)

- Wrote `staged-skills/werkbank-pull-ticket/SKILL.md` (version 1): find candidates
  by status `offen` + exact project path + assignee `claude`; order hoch > normal >
  niedrig then lowest id; a "clarity gate" that REQUIRES asking the user before
  claiming when the description is vague/destructive/unfalsifiable — with a
  non-interactive branch (write questions into `## Ergebnis`, set `review`, stop;
  the board's Ablehnen-mit-Grund loop carries the answers back to `offen`); claim =
  `in_arbeit`; report = honest German `## Ergebnis`, `review`, commit in the
  Werkbank repo without pushing.
- Wrote `staged-skills/README.md` documenting the hand-off convention.

## What I tried that didn't work — and why

- Writing the skill to its real home `.claude/skills/_user-level/…` was denied:
  "Claude requested permissions to write … but you haven't granted it yet" —
  `.claude/` is a protected path that headless acceptEdits runs may not touch (per
  the permissions research in 2026-08-15-drag-dispatch.md). `~/.claude/skills/`
  is additionally outside the project directory, which this run was forbidden to
  change. Hence the staging area.

## Decisions made

- Stage in `staged-skills/` at repo root rather than docs/dev/ — docs frontmatter
  rules would clash with skill frontmatter, and the folder makes the pending state
  visible.
- No CHANGELOG entry yet: the feature is not user-visible until the skill is
  installed to `~/.claude/skills/` (documenting rule: changelog records what
  changed FOR THE USER).
- Skill targets user level (not project level) because it must fire in ANY
  project's session; per creating-skills that means both `~/.claude/skills/` and
  this repo's `_user-level/` staging copy — both are the installer's job.

## Follow-ups

- Werkbank session: review, then install per staged-skills/README.md (move to
  `_user-level/`, copy to `~/.claude/skills/`, CHANGELOG entry, delete staging
  folder).
- Ticket WB-4 ("passe claude md an") exists untouched — not part of this run.
