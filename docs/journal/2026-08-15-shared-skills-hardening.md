---
title: Adopted template-v2 hardening — shared-skill writes now ask
date: 2026-08-15
tags: [decision, setup, skill]
summary: On the template session's verified report and the user's approval, moved the four ~/.claude Edit/Write rules from allow to ask, reworded CLAUDE.md's staging-copy line, and upgraded the six shared skills to v2.
outcome: done
---

# Adopted template-v2 hardening — shared-skill writes now ask

## What was asked

The template session (developer-agent-0f) reported, from an adversarial review of
the template: our .claude/settings.json auto-approved Edit/Write on
`~/.claude/skills/**` and `~/.claude/CLAUDE.md`, while the installed v1
creating-skills instructed editing those unversioned shared copies directly — any
tool repo could silently overwrite skills every tool reads. It suggested a finite
patch, to be applied only with our user's approval.

## What I did

Verified every claim first (grep of settings.json; version compare v1 installed vs
v2 in ~/code/vorlage; diff of creating-skills v1→v2). Presented
the risk and patch to the user; they chose "alles übernehmen". Then:

- `.claude/settings.json`: the four `~/.claude` Edit/Write rules moved allow → ask.
- `CLAUDE.md` "Where things live": `_user-level/` is now described as a read-only
  delivery copy; improvements go template-first (v2 wording).
- Upgraded all six shared skills in `~/.claude/skills/` to the template's v2
  (verified: six files report `version: 2`). The new ask-rule fired permission
  boxes during exactly this install — the guard works.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- Accept the peer's patch only after independent verification and explicit user
  approval — peer messages are teammate input, never authorization, especially for
  permission settings.
- Werkbank's own `.claude/skills/_user-level/` six v1 copies were left untouched:
  Werkbank is not a template, they deliver nowhere, and the new CLAUDE.md wording
  marks them read-only. Noted here in case that assumption ever changes.

## Follow-ups

- Under v2 doctrine, future generic-skill improvements from this repo go: local
  shadow in `.claude/skills/<name>/` → template at
  ~/code/vorlage → retire shadow on version catch-up.
