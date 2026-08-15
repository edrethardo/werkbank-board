---
title: Migrated to project-local kit skills (v3) + .developer-agent.json
date: 2026-08-15
tags: [decision, setup, skill]
summary: On the template session's notice and the user's approval, the six kit skills (v3) plus syncing-the-kit now live in this repo's .claude/skills/; .developer-agent.json records kit origin/version for future syncs.
outcome: done
---

# Migrated to project-local kit skills (v3) + .developer-agent.json

## What was asked

The template session announced an architecture change (verified by me against
~/code/vorlage before acting): the machine-wide kit
(~/.claude/skills + CLAUDE.md block) leaked kit behavior into unrelated repos and
went stale, so the kit is now project-local and the shared copies will be retired
once the owner approves cleaning ~/.claude. The user chose "jetzt umziehen".

## What I did

- Copied seven skills from the kit into `.claude/skills/`: the six core skills at
  version 3 (descriptions no longer carry the tool-repo prefix) plus
  `syncing-the-kit` v1 (the future update channel). Project skills shadow the
  still-present ~/.claude v2 copies, so behavior switches to v3 immediately.
- Wrote `.developer-agent.json` (role tool, kit_origin
  ~/code/vorlage, kit_version 7 = the kit's initialize-tool
  version at sync time).
- CLAUDE.md "Where things live": kit skills are project-local now;
  `_user-level/` + `staged-skills/` re-declared as Werkbank's OWN delivery area
  for skills that target projects install (werkbank-pull-ticket) — explicitly not
  kit territory.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- Did NOT delete the six old copies in ~/.claude/skills — that cleanup belongs to
  the template session with the owner's explicit approval.
- Did NOT pull initialize-tool v7 over our project copy (v3): init is complete
  here, the skill can never legitimately run again in this repo.
- Kept werkbank-pull-ticket's machine-wide installation model: it is Werkbank
  product function (target projects must see it), not kit leakage. Flagged to the
  template session so the ~/.claude cleanup spares it.

## Follow-ups

- When the ~/.claude cleanup actually happens, verify Werkbank sessions still see
  all seven project-local skills and that werkbank-pull-ticket (if installed at
  user level by then) survived.
- Future kit updates: run `syncing-the-kit` (per-skill version compare, user
  decides, branch + one commit).
