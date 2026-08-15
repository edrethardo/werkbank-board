---
title: WB-4 — "init" onboarding command added to CLAUDE.md
date: 2026-08-15
tags: [feature]
summary: Typing "init" in the Werkbank chat now triggers an onboarding dialog — ask for the default project, explain ticket pulling, offer to install the werkbank-pull-ticket skill.
outcome: done
---

# WB-4 — "init" onboarding command added to CLAUDE.md

## What was asked

Ticket WB-4: the project should initialize itself when the user types "init" in
the chat — ask the user about the projects, explain how a session pulls its
ticket from the board, and offer to install the pull-ticket skill.

## What I did

(Board-dispatched run, forked continuation of the Werkbank session, headless.)

- Extended the `## Init` section of `CLAUDE.md` with a three-step onboarding
  dialog triggered by the user typing `init`: (1) ask for the default target
  project and write it to `default_project` in `config.json`, (2) explain the
  pull-your-ticket flow („zieh dir dein Ticket“), (3) offer to install the
  `werkbank-pull-ticket` skill — install only on explicit yes.
- The install step handles both states: skill still in `staged-skills/`
  (current state, per WB-5) or already moved to `_user-level/` — so the CLAUDE.md
  text stays correct after the WB-5 install happens.
- Documented the command in `docs/user/board-und-tickets.md` (new section
  „Einrichten mit ‚init‘“) and added the CHANGELOG entry; updated `docs/INDEX.md`.

## What was verified

- Instruction-only change: no code to test. Checked that the referenced paths
  (`staged-skills/README.md`, `.claude/skills/_user-level/`, `config.json`)
  exist and that the flow matches the staged skill's actual behaviour
  (clarity gate → claim → report), so the explanation given to the user is true.

## Decisions made

- Plain CLAUDE.md section instead of a new skill: stack policy step 1 (no new
  code). The flow is a short conversational script that runs rarely; a skill
  would duplicate the Init pattern already living in CLAUDE.md.
- The onboarding asks for ONE default project (that is all `config.json`
  supports) and tells the user that each ticket can name a different project —
  the ticket said "Projekte" (plural), and per-ticket project fields are how
  multiple projects are actually supported.

## Follow-ups

- WB-5's actual skill installation (staged → `~/.claude/skills/`) is still the
  interactive Werkbank session's job; the `init` dialog now offers exactly that.
