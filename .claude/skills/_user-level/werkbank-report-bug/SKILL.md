---
name: werkbank-report-bug
description: Use when the user reports a bug in any session — "ich hab einen bug gefunden", "da ist ein Fehler", "das ist kaputt", wrong behavior described — capture it properly and file a bug ticket on the Werkbank board.
version: 2
---

# Werkbank: Report a Bug

The Werkbank repo lives at `~/code/werkbank` (verify the path exists
first; the repo may have moved). Reporting and fixing are separate: this skill only
FILES the bug. Do not start fixing unless the user explicitly asks.

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

Create it through the Werkbank's own store (guarantees ID numbering and format —
never write the ticket file by hand). Project = THIS session's working directory
unless the user names another. Description structure:

    # Values travel through the ENVIRONMENT: a quote in the user's bug text
    # would otherwise break out of the Python literal (WB-35 review).
    WB_TITLE="<short bug title>" WB_PRIO="<hoch|normal|niedrig>" \
    WB_PROJECT="<absolute path of this session's project>" \
    WB_DESC="**Was passiert:** ...

    **Erwartet:** ...

    **Nachstellen:** 1. ... 2. ..." python3 - <<'EOF'
    import os, sys; sys.path.insert(0, "~/code/werkbank/src")
    from werkbank import store
    t = store.create_ticket("~/code/werkbank/tickets",
                            title=os.environ["WB_TITLE"],
                            description=os.environ["WB_DESC"],
                            project=os.environ["WB_PROJECT"],
                            priority=os.environ["WB_PRIO"], type="bug")
    print(t.id)
    EOF

## 4. Commit and confirm

Commit the new ticket file in the Werkbank repo (do not push):

    git -C ~/code/werkbank add tickets/
    git -C ~/code/werkbank commit -m "Report bug: <id> <title>"

Then tell the user the ticket ID and that it now sits in **Offen** on the board —
they can drag it to In Arbeit or say „arbeite die Tickets ab" whenever they want it
fixed.
