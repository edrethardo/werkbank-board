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

## Getting it running

### Requirements

- **Claude Code**, installed and logged in — `claude --version` must work in a
  terminal, and `claude -p "hi"` must answer. The board shells out to that
  binary; agent runs consume your Claude quota.
- **Python 3.10+** (standard library only, nothing to install).
- **Linux or macOS.** `fcntl` file locking makes it Unix-only today.
- **git**, if you want the ticket history that this design assumes.

### 1. Get the code and configure it

```bash
git clone https://github.com/edrethardo/werkbank-board.git werkbank
cd werkbank
cp config.example.json config.json
```

Edit `config.json`: set `default_project` to the absolute path of the project
your tickets are about, and list your projects under `projects` (name → path).
You can also do this later in the board's **📁 Projekte** dialog, which has a
folder picker. `config.json` is gitignored — it is yours, not the repo's.

### 2. Start the board

```bash
python3 src/werkbank/server.py
```

Open <http://127.0.0.1:8765>. That is the whole installation — one process, no
database, no build step. Stop it with Ctrl-C.

### 3. Create a ticket and let an agent work it

1. **+ Neues Ticket** — title, description, target project, priority.
2. Drag it from **Offen** to **In Arbeit**.
3. The board starts `claude -p` in that project's directory and writes what it
   is doing onto the card (steps, last tool, tokens, quota).
4. When the agent is done, its honest summary lands in the ticket's *Ergebnis*
   and the card moves to **Review** — failures included, with the reason.
5. You press **Annehmen** (→ Erledigt) or **Ablehnen** with a reason (→ back to
   Offen, your reason recorded in the ticket).

Nothing starts by itself: only your drag (or an explicit chat request)
dispatches an agent.

### 4. Talk to it from Claude Code (optional but this is the point)

Open the Werkbank folder in Claude Code. The repo ships project skills in
`.claude/skills/`, so you can say things like:

- *"Erstelle ein Ticket für …"* — the session drafts and files it properly.
- *"Arbeite die Tickets ab"* / *"erledige WB-7"* — works tickets in the chat,
  visibly.
- *"Starte das Board"* — starts/restarts the server and verifies it answers.

To let **other projects'** Claude sessions pull their own tickets, install the
two user-level skills once:

```bash
mkdir -p ~/.claude/skills
cp -r .claude/skills/_user-level/werkbank-pull-ticket ~/.claude/skills/
cp -r .claude/skills/_user-level/werkbank-report-bug  ~/.claude/skills/
```

Then edit the repo path at the top of both `SKILL.md` files to wherever you
cloned Werkbank. In any project session you can now say *"zieh dir dein
Ticket"* or *"ich hab einen Bug gefunden"*.

### 5. Start it automatically (optional)

On a systemd machine, `~/.config/systemd/user/werkbank-board.service`:

```ini
[Unit]
Description=Werkbank Board

[Service]
ExecStart=/usr/bin/python3 %h/code/werkbank/src/werkbank/server.py
WorkingDirectory=%h/code/werkbank
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now werkbank-board
```

The board then starts at login and restarts itself after a crash. Logs:
`journalctl --user -u werkbank-board`; agent run logs live in
`~/.local/state/werkbank/logs/`.

### Configuration reference

| Key | Meaning |
|---|---|
| `port`, `host` | Where the board listens. Leave `host` at `127.0.0.1` — see Security. |
| `default_project` | Target project for new tickets that name none. |
| `projects` | Named project list (name → absolute path) shown in the dialogs. |
| `agent_permission_mode` | Permission mode for dispatched runs (default `acceptEdits`). |
| `agent_allowed_tools` | Extra allowed tools for dispatched runs (default `Bash`). |
| `agent_timeout_minutes` | Hard limit per run (default 30). |
| `chat_handover_minutes` | How long a live chat session may claim a handed-over ticket before a background run takes over (default 5). |
| `nonblocking_review` | Per project: may the queue continue while a ticket waits in Review? |

### Tests

```bash
python3 -m unittest discover -s tests
```

122 tests, no dependencies, no network access needed.

## How it's built

Python 3 standard library only, one `http.server` process, one static HTML
page. See `docs/dev/stack.md` for the reasoning and `docs/journal/` for the
complete, honest build history — every feature, every bug, every wrong turn,
written down as it happened (including a security review that found a
CSRF→RCE chain in this very tool and how it was closed).

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
- Publishing a Werkbank checkout publishes `tickets/` — the live board,
  including future tickets. Fork the code, not the board, if you don't want
  that.

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
