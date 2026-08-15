---
title: WB-25 — built-in folder picker for the projects dialog
date: 2026-08-15
tags: [feature]
summary: A native OS file dialog is impossible from a browser (no absolute paths for web pages), so the board got a server-backed folder browser (GET /api/browse, pathlib) with click navigation; 88 tests green.
outcome: done
---

# WB-25 — built-in folder picker for the projects dialog

## What was asked

Ticket WB-25 (handed over to the chat session via WB-22 mechanics): folder
selection via file explorer, compatible with every OS.

## What I did

- Honest constraint stated up front: browsers never reveal absolute filesystem
  paths to web pages (`webkitdirectory` yields relative paths only), so a real
  native dialog cannot fill a server-side path field. Built the working
  equivalent instead: a server-backed folder browser.
- `projects.list_dirs(path=None)`: directories only, hidden ones skipped,
  unreadable entries ignored, German errors, home as default, `parent: None`
  at filesystem root — pathlib keeps it OS-neutral (4 new tests, red first;
  suite now 88 green).
- server: `GET /api/browse?path=…` → listing or 400 with the German reason.
- board.html: "📂 Durchsuchen" next to the path input in the projects dialog →
  browse dialog with ⬆️-parent row, clickable folders, "Diesen Ordner wählen".
  Invalid text in the path field falls back to home instead of a dead dialog.
  Both script blocks node --check OK.

## What I tried that didn't work — and why

Nothing failed at build time; the native-dialog route was ruled out by research,
not by trial (browser security model, documented above).

## Decisions made

- Hidden folders are not listed — the picker is for choosing project folders,
  not a general file manager; the text field still accepts any path.
- The browse API is bound to 127.0.0.1 like everything else; it lists directory
  NAMES of the local machine, same trust boundary as the rest of the board.

## Follow-ups

- None specific. Visual check of the picker is with the user (usual limitation).
