# Werkbank

A Jira-like ticket board for [Claude Code](https://claude.com/claude-code) agents —
built entirely by talking to Claude Code, for a non-technical owner, in two days.

You (or an agent) write tickets. You drag a ticket to **In Arbeit** — and a Claude
agent picks it up, works it in the target project, and reports back into the
ticket for your review. If the remembered worker is a live chat conversation, the
ticket is handed over there and worked **visibly in front of you**.

## What it does

- **Kanban board** (vanilla JS, single HTML page): Offen → Zu bearbeiten →
  In Arbeit → Review → Fehlgeschlagen → Erledigt, drag & drop, dark/light
  theme, project filter, delete, resizable dialogs.
- **A queue that runs itself**: park tickets in *Zu bearbeiten* and the board
  starts them one after another; a pending review holds the project's queue
  unless you switch that off per project.
- **Live agent status**: running cards show steps, last tool, tokens and the
  CLI's own quota reporting, flag a silent run, and name failures in plain
  language ("usage limit reached") instead of error codes; the run log is
  written while the agent works.
- **Tickets are plain markdown files** in `tickets/` — the files are the source
  of truth, every change is committed to git, nothing hides in a database.
- **Drag to dispatch**: moving a ticket to In Arbeit spawns a headless
  `claude -p` run that resumes the project's remembered *ticket session*
  (per-project lineage, forked when the lineage is an open conversation), or
  hands the ticket to the live chat session for visible work.
- **Review loop**: agents stop at Review with an honest result — including
  failures; only the human moves tickets to Erledigt. Reject with a reason and
  the ticket goes back to Offen carrying your feedback.
- **Robust by paranoia**: lost-update protection (version counter + file locks
  across processes), orphan sweep after restarts, broken ticket files quarantined
  instead of taking the board down, serialized agent runs.
- **Multi-project**: named project list, folder picker, per-ticket target
  project; other projects' Claude sessions pull their tickets themselves via a
  user-level skill.
- **Ticket links**: "must wait for" ordering and mutual exclusion, enforced at
  start and dequeue time.

## How it's built

Python 3 standard library only (no dependencies), one `http.server` process on
`127.0.0.1:8765` (a systemd user service starts it at login), one static HTML
page. `python3 -m unittest discover -s tests` runs the whole suite (108 tests).
`fcntl` makes it Unix-only today. See `docs/dev/stack.md` for the reasoning and
`docs/journal/` for the complete, honest build history — every feature, every
bug, every wrong turn, written down as it happened.

## Security — read this before you deploy it

**A ticket is an executable prompt.** Dragging a ticket to *In Arbeit* runs
`claude -p` in the ticket's target directory with `--permission-mode
acceptEdits --allowedTools Bash` (the shipped defaults) and no human in the
loop. Anything that can create a ticket and move it can therefore run commands
as you.

That is fine for its actual purpose — one person, one machine — and the board
is built around that assumption:

- It binds to `127.0.0.1` only. **Never expose it to a network** (`host` in
  `config.json`): there is no login, so every device on that network would get
  the shell access described above.
- Writes require an `application/json` content type and a same-origin `Origin`,
  and every request must carry a localhost `Host` — so a web page you happen to
  visit cannot drive the board (CSRF), and DNS rebinding is refused.
- The page never renders ticket data as HTML, ticket fields cannot smuggle
  extra frontmatter, the folder picker is confined to your home directory plus
  registered projects, and agent logs are written to a private `0700`
  directory.
- Publishing this repository publishes `tickets/` — the live board, including
  future tickets. Fork the code, not the board, if you don't want that.

If you hand the board to anyone else, or point it at content you did not
write, treat that as granting shell access and tighten
`agent_permission_mode` / `agent_allowed_tools` first. The findings behind
these measures are in `docs/journal/2026-08-15-wb35-security-review.md`.

## A note on language

The owner speaks German: the user manual (`docs/user/`), the CHANGELOG and all
ticket content are German; code, commits, developer docs and the journal are
English. `CLAUDE.md` is the working contract between the owner and the AI
developer.

## License

MIT — see [LICENSE](LICENSE).
