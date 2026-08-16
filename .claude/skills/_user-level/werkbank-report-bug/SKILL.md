---
name: werkbank-report-bug
description: Use when the user reports a bug in any session — "ich hab einen bug gefunden", "da ist ein Fehler", "das ist kaputt", wrong behavior described — capture it properly and file a bug ticket on the Werkbank board.
version: 3
---

# Werkbank: Report a Bug

## Path to the Werkbank — the ONLY line you adapt

    WERKBANK=/home/USER/code/agent_ticket

Every command below starts with that assignment. Never write the path into a
Python string: `~` is expanded by the SHELL, never by Python (WB-47).
Check it first: `ls "$WERKBANK/tickets" >/dev/null` — if that fails, say so and
stop.

Reporting and fixing are separate: this skill only FILES the bug. Do not start
fixing unless the user explicitly asks.

## 1. Capture the bug — three questions

Ask in the user's language, one question per message, and only what the
conversation has not already answered (fill in what you can observe yourself and
confirm it instead of re-asking):

1. **Was passiert?** — the actual behavior, as concrete as possible.
2. **Was hast du erwartet?** — the expected behavior.
3. **Wie stellt man es nach?** — steps to reproduce (where, which action, which data).

## 2. Priority by severity

- `hoch` — data loss, crash, or the user is blocked.
- `normal` — wrong behavior, but a workaround exists.
- `niedrig` — cosmetic, typo, minor annoyance.

Say which one you chose and why; let the user override.

## 3. File the ticket

Values travel through the ENVIRONMENT — a quote in the user's text must never
end up inside the Python source (WB-35):

    WERKBANK=/home/USER/code/agent_ticket \
    WB_TITLE="<kurzer Bug-Titel>" \
    WB_PRIO="<hoch|normal|niedrig>" \
    WB_PROJECT="$PWD" \
    WB_DESC="**Was passiert:** …

    **Erwartet:** …

    **Nachstellen:** 1. … 2. …" python3 - <<'EOF'
    import os, sys
    sys.path.insert(0, os.path.join(os.environ["WERKBANK"], "src"))
    from werkbank import store
    t = store.create_ticket(os.path.join(os.environ["WERKBANK"], "tickets"),
                            title=os.environ["WB_TITLE"],
                            description=os.environ["WB_DESC"],
                            project=os.environ["WB_PROJECT"],
                            priority=os.environ["WB_PRIO"], type="bug")
    print(t.id)
    EOF

## 3b. Name a check, or the bug is Claude-only

A bug ticket can only go to the local model (`assignee: opencode`) if it names a
check that FAILS now and must pass afterwards:

    gate: Tests laufen durch

**That field takes a NAME, never a command.** The commands live in the board's
`config.json` under `gates` -> `<project path>`; only names configured there can
be used, and the board refuses to dispatch an opencode ticket whose name it does
not know. (A ticket field that carried a command would be remote code execution
on a LAN-reachable board.)

So: look in `config.json` for what this project offers. If one of those checks
demonstrates the bug, name it. If none does, leave the ticket with Claude and say
why in the description — a bug with no reproducible check must not go to a model
whose self-report we do not trust. If a suitable check EXISTS but is not
configured, say that too; the owner can have it added.

Prefer a check that RUNS the behaviour over one that merely compiles it: a
type-check passes happily while every test fails.

## 4. Commit and confirm

    git -C "$WERKBANK" add tickets/
    git -C "$WERKBANK" commit -m "Report bug: <id> <title>"

Then tell the user the ticket ID and that it now sits in **Offen** on the board —
they can drag it to In Arbeit or say „arbeite die Tickets ab" whenever they want
it fixed.
