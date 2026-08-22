---
name: werkbank-register-session
description: Use when the user asks THIS chat session to receive a project's Werkbank tickets — "registrier dich für dieses Projekt", "melde dich als Session an", "du sollst die Tickets für X bekommen", "register this session" — make the board hand this project's handovers to THIS conversation.
version: 1
---

# Werkbank: Register THIS session for a project

The board hands a dragged ticket to the session registered for the ticket's
**project**. That registration normally happens as a side effect of
`werkbank-pull-ticket`, and always for the session's own working directory —
so a project nobody has pulled a ticket in has no chat to hand to, and its
tickets silently take the background lane instead (measured 2026-08-20 with
WB-253: dragged, no chat registered, straight back to `offen`).

This skill does that registration deliberately, and for any project — not only
the current directory.

**Interactive chats only.** A dispatched background run must never register
itself: the run ends, and every later handover for that project would be
addressed to a session that no longer exists. If you are a dispatched run, say
so and stop.

## Path to the Werkbank — the ONLY line you adapt

    WERKBANK=/pfad/zur/werkbank

Verify it first: `ls "$WERKBANK/state.json" >/dev/null` — if that fails, say so
and stop instead of guessing. Never put the path inside a Python string: `~` is
expanded by the SHELL, never by Python (that mistake shipped once — WB-47).

## 1. Which project?

- **Default:** this session's own working directory, absolute and resolved
  (`pwd -P`).
- **Another project:** only when the user names it. Use its absolute path
  exactly as the board knows it — compare against the keys already in
  `state.json` and the `projects` list in `config.json`. A path that matches
  no project means the tickets you are trying to receive do not exist yet;
  say that instead of registering a typo.

## 2. Register

The id comes ONLY from the environment. If `$CLAUDE_CODE_SESSION_ID` is empty,
skip the registration and say so — never guess an id, or you hand a project's
tickets to a session that is not you.

    WERKBANK=/pfad/zur/werkbank WB_PROJECT="$(pwd -P)" python3 - <<'PYEOF'
    import os, sys
    sys.path.insert(0, os.path.join(os.environ["WERKBANK"], "src"))
    from werkbank import dispatch
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if not sid:
        sys.exit("CLAUDE_CODE_SESSION_ID is empty — registration skipped, never guess an id")
    dispatch.register_ticket_session(os.environ["WB_PROJECT"], sid)
    print("registered", sid, "for", os.environ["WB_PROJECT"])
    PYEOF

Set `WB_PROJECT` to the other project's path when the user named one.

## 3. Verify — do not trust the write

    WERKBANK=/pfad/zur/werkbank WB_PROJECT="$(pwd -P)" python3 - <<'PYEOF'
    import json, os
    d = json.load(open(os.path.join(os.environ["WERKBANK"], "state.json")))
    print(os.environ["WB_PROJECT"], "->", json.dumps(d.get(os.environ["WB_PROJECT"])))
    PYEOF

It must show your session id AND `"interactive": true`. A bare string without
that flag is a background run's entry, not a chat — the board will not treat it
as a conversation to hand work to.

## 4. Tell the user what changed

One sentence, and be concrete: which project's tickets now arrive in this chat,
and that this **replaced** whatever session was registered before — the previous
chat stops receiving that project's handovers. If the previous entry was another
interactive session that is still open, say so plainly; the user may not want it
taken over.

## What this skill does NOT do

- **It does not add the project to the board.** A project the board does not
  know has no tickets to hand out — that is `werkbank-register-project`.
- **It does not pull or claim a ticket.** That is `werkbank-pull-ticket`.
- **It does not start a handover watcher.** Since WB-258 the board delivers
  into the chat directly over the session's messaging socket; registration is
  what makes this conversation addressable at all.
