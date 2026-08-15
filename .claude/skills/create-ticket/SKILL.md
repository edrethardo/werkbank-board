---
name: create-ticket
description: Use when the user asks to create a ticket in chat — "erstelle ein Ticket", "leg ein Ticket an", "schreib das als Ticket auf", "mach daraus ein Ticket", "das sollten wir als Ticket festhalten".
version: 3
---

# Create a Ticket from Chat

Turn what the user just described into a correctly formatted ticket on the board.
ALWAYS go through `store.create_ticket` — never write a ticket file by hand; the
store owns id numbering, filename slugs, and the file format.

## 1. Gather the fields — ask only for what's missing

From the conversation, fill as much as possible; ask for the rest in plain German,
one short question at a time (AskUserQuestion or plain text):

- **title** — required. Draft it yourself from the user's words; a good title says
  the outcome, not the activity.
- **description** — required in practice: what should exist afterwards, and any
  acceptance criteria the user states. If the user gave only a title-sized wish,
  ask ONE question ("Woran erkennst du, dass es fertig ist?") rather than padding.
- **type** — `aufgabe` (default) or `bug`. If the user is reporting broken
  behavior, prefer the `werkbank-report-bug` flow (it asks repro questions);
  otherwise `aufgabe`.
- **priority** — `hoch` | `normal` (default) | `niedrig`. Infer from urgency
  words; only ask when the user signals urgency but you cannot rank it.
- **project** — absolute path. Default: `default_project` from `config.json`.
  The named project list lives in `config.json` under `projects` (name → path,
  WB-24): when the user says a project NAME, resolve it there; unknown name →
  ask instead of guessing. A raw path is fine too — verify it exists before
  creating. New projects can be registered via
  `werkbank.projects.add_project(config_path, name, path)`.
- **assignee** — default `claude`; only set differently on explicit request.

Do NOT interrogate the user through all six fields — sensible defaults beat a
questionnaire. One confirmation question maximum when everything was inferable.

## 2. Create it

```bash
# Values go through the ENVIRONMENT, never substituted into the snippet —
# a quote or triple-quote in user text would otherwise end the literal and
# execute the rest as Python (WB-35 review).
WB_TITLE="<title>" WB_DESC="<description>" WB_PROJECT="<absolute project path>" \
WB_PRIO="normal" WB_TYPE="aufgabe" python3 - << 'EOF'
import os, sys; sys.path.insert(0, "src")
from werkbank import store
t = store.create_ticket("tickets", title=os.environ["WB_TITLE"],
                        description=os.environ["WB_DESC"],
                        project=os.environ["WB_PROJECT"],
                        priority=os.environ["WB_PRIO"], type=os.environ["WB_TYPE"])
print(t.id)
EOF
```

(When another Werkbank checkout is the cwd, adjust paths accordingly — the
tickets dir and `src/` sit in the Werkbank repo root.)

## 3. Confirm and commit

- Tell the user the ticket number and title back in one sentence ("Angelegt:
  **WB-17 — <Titel>**, Priorität normal, liegt in Offen.") and where it will
  appear (board column Offen).
- Commit the new ticket file per `git-discipline`:
  `git add tickets/ && git commit -m "Add ticket <id>: <short title>"`.
  Do not push unless the session's normal push practice applies.
- The board picks the file up on its next poll — no restart needed.

## Rules

- Creating is not starting: the ticket stays in `offen`. Only the user's drag (or
  an explicit "arbeite es ab") dispatches it.
- Never invent acceptance criteria the user didn't imply — a wrong criterion
  steers the working agent into the wrong build.
