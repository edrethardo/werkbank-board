---
title: Removed dead initialize-tool skill; pull-ticket trigger confirmed intent-based
date: 2026-08-15
tags: [decision, skill]
summary: Deleted .claude/skills/initialize-tool (init complete, could only misfire) and the CLAUDE.md marker paragraph; peer's trigger-scoping concern for werkbank-pull-ticket was already satisfied by its v2 description.
outcome: done
---

# Removed dead initialize-tool skill; pull-ticket trigger confirmed intent-based

## What was asked

Template session follow-up: (a) machine-wide skills must trigger on intent, not
situation, or they hijack unrelated repos' sessions; (b) recommended deleting
Werkbank's frozen initialize-tool v3 as dead weight in an initialized tool.

## What I did

- (a) Verified staged-skills/werkbank-pull-ticket/SKILL.md v2: trigger is already
  intent-based ("Use when the user asks this session to pull or work its Werkbank
  ticket — 'zieh dir ein Ticket' …"). No change made. Note: no copy exists in
  .claude/skills/_user-level/ or ~/.claude/skills — staged-skills/ is currently
  the only copy, which CLAUDE.md's init dialog handles.
- (b) Deleted `.claude/skills/initialize-tool/` (rm approved via permission box)
  and removed the UNINITIALIZED-marker paragraph from CLAUDE.md's Init section;
  the `init` chat-command dialog stays. Recoverable from git history.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- Keep pull-ticket description as-is — it already encodes the intent-only rule the
  peer described; changing it would be churn.
- Delete rather than freeze initialize-tool: the marker is gone, init can never
  legitimately re-run, and syncing-the-kit + the kit's adopting-a-project cover
  the remaining use cases.

## Follow-ups

- None. (~/.claude cleanup remains with the owner and the template session.)
