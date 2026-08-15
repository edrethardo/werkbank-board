---
title: WB-7 — dark mode default with light/dark toggle
date: 2026-08-15
tags: [feature]
summary: Board now defaults to the dark palette; a header toggle (sun/moon) switches themes and persists the choice in localStorage. Syntax-verified headless; visual check left to the user.
outcome: done
---

# WB-7 — dark mode default with light/dark toggle

## What was asked

Ticket WB-7: implement dark mode, make it the default, add a light/dark switch.

## What I did

(Board-dispatched run, forked continuation of the Werkbank session, headless.)

- `board.html` already had a dark palette behind `prefers-color-scheme: dark`.
  Restructured: the dark variables are now the `:root` default, the light set
  moved to `:root[data-theme="light"]`; the media query is gone (explicit
  choice beats OS preference now that there is a switch).
- Added a `ghost` header button (sun in dark mode, moon in light) that toggles
  `data-theme` and persists to `localStorage` key `werkbank-theme`.
- A tiny inline script in `<head>` applies the stored theme before first paint
  so light-mode users get no dark flash.

## What was verified

- Both script blocks pass `node --check`; the running board serves the new
  page (it reads `board.html` per request, so no restart was needed).
- NOT verified: actual look in a browser — this run is headless. Left to the
  user's review, same as WB-6.

## Decisions made

- Dropped `prefers-color-scheme` entirely instead of layering it under the
  toggle: three-state logic (auto/light/dark) buys little for a single-user
  tool and the ticket explicitly wants dark as the standard.
- Theme state lives in the browser (`localStorage`), not `config.json`: it is
  per-browser presentation, not board configuration, and needs no server round
  trip.
