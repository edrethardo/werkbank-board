---
title: WB-166 — proposed patch for the create-ticket skill (opencode needs a gate at creation time)
date: 2026-08-16
tags: [decision, docs]
summary: The create-ticket skill mentions opencode's gate requirement in the recommendation logic but the actual creation snippet forwards neither `assignee=` nor `gate=`, so an opencode recommendation silently becomes a claude ticket. WB-166 asks for the check to be resolved AT creation. This doc carries the exact replacement content for `.claude/skills/create-ticket/SKILL.md` — the write itself is blocked in dispatched runs (the skill infrastructure is intentionally not agent-writable), so the user or an interactive session has to apply it.
---

# WB-166 — proposed patch

> **APPLIED (2026-08-21).** The shipped `create-ticket` skill is v6 and carries
> the `epic` type and the gate requirement. Kept as the record of why; nothing
> here needs doing any more.

## Why the patch, not the patch itself, ships here

WB-166 asks for a skill update. The underlying code already refuses an
opencode ticket without a gate at dispatch time
(`opencode.no_gate_message`, since the WB-92 investigation), so there is
no source change on the Werkbank side — only the create-ticket skill
needs to catch the missing gate BEFORE the ticket is written to disk.

The dispatched run that worked WB-166 was denied write access to
`.claude/skills/create-ticket/SKILL.md` (and also to `/tmp/…`). That is
by design: an agent running from a dispatched context should not be able
to rewrite the skills that shape how future agents work. So this
document carries the full replacement content; the actual write is one
of these:

- **Interactive session:** open `.claude/skills/create-ticket/SKILL.md`
  and replace its content with the block below.
- **Command line:** with the skill text saved to
  `/tmp/create-ticket-skill.md` (write it from an interactive session),
  `cp /tmp/create-ticket-skill.md .claude/skills/create-ticket/SKILL.md`.

Diff vs the current v5 skill, in one paragraph: bump `version: 5` →
`version: 6`; add `epic` to the type list; make the "opencode needs a
gate" line in §1 mandatory and forward-refer to a new §1a; add §1a
titled "Gate for opencode — resolve it BEFORE creating (WB-166)" with
the read/pick/add/fall-back flow; make §2's snippet forward `assignee`
and `gate` explicitly via `WB_ASSIGNEE` / `WB_GATE` env vars; add a
"never create an opencode ticket without a gate" rule at the bottom.

## Full replacement content for `.claude/skills/create-ticket/SKILL.md`

Copy from the first `---` through the end.

    ---
    name: create-ticket
    description: Use when the user asks to create a ticket in chat — "erstelle ein Ticket", "leg ein Ticket an", "schreib das als Ticket auf", "mach daraus ein Ticket", "das sollten wir als Ticket festhalten".
    version: 6
    ---

    # Create a Ticket from Chat

    Turn what the user just described into a correctly formatted ticket on the board.
    ALWAYS go through `store.create_ticket` — never write a ticket file by hand; the
    store owns id numbering, filename slugs, and the file format.

    NEVER create a ticket from an isolated copy of this repo (git worktree, review
    clone, scratch checkout): its `tickets/` is frozen at fork time, so it assigns a
    number the live board may already have given away — that is exactly how two
    tickets ended up sharing WB-93 (WB-101). File tickets in the LIVE board repo, or
    hand the request to the session that owns it.

    ## 1. Gather the fields — ask only for what's missing

    From the conversation, fill as much as possible; ask for the rest in plain German,
    one short question at a time (AskUserQuestion or plain text):

    - **title** — required. Draft it yourself from the user's words; a good title says
      the outcome, not the activity.
    - **description** — required in practice: what should exist afterwards, and any
      acceptance criteria the user states. If the user gave only a title-sized wish,
      ask ONE question ("Woran erkennst du, dass es fertig ist?") rather than padding.
    - **type** — `aufgabe` (default), `bug`, or `epic` (WB-161). If the user is
      reporting broken behavior, prefer the `werkbank-report-bug` flow (it asks
      repro questions); a bigger package that will need to be broken up → `epic`;
      otherwise `aufgabe`.
    - **priority** — `hoch` | `normal` (default) | `niedrig`. Infer from urgency
      words; only ask when the user signals urgency but you cannot rank it.
    - **project** — absolute path. Default: `default_project` from `config.json`.
      The named project list lives in `config.json` under `projects` (name → path,
      WB-24): when the user says a project NAME, resolve it there; unknown name →
      ask instead of guessing. A raw path is fine too — verify it exists before
      creating. New projects can be registered via
      `werkbank.projects.add_project(config_path, name, path)`.
    - **assignee** — estimate who SHOULD work it (user request, 2026-08-16) and set
      it; the user overrides by naming one. Recommend `opencode` only when ALL of
      these hold, otherwise `claude`:
      1. Small and isolated — new files or one well-bounded change, no cross-module
         refactor, no security-sensitive or concurrency-sensitive code.
      2. A named check exists for the project in config.json `gates` — see §1a
         below. An opencode ticket without a gate FAILS at start (the local
         model's self-report is not the acceptance criterion, WB-92); so the
         `gate` field MUST be set at creation, not "later".
      3. Nobody is waiting: priority is `niedrig`, or the user signalled "irgendwann/
         nebenbei". The local lane is 5–10× slower (measured: WB-102, a ~20-line
         script, ≈20 min) but costs no quota and runs beside the Claude lane.
      Borderline → `claude`; a failed opencode attempt that escalates costs more
      than starting with Claude (measured: WB-92).

    Do NOT interrogate the user through all six fields — sensible defaults beat a
    questionnaire. One confirmation question maximum when everything was inferable.

    ## 1a. Gate for opencode — resolve it BEFORE creating (WB-166)

    If (and only if) you are about to recommend `assignee=opencode`, resolve the
    gate now. The ticket must carry a valid `gate:` name before it lands on the
    board — otherwise the first dispatch fails with the German refusal from
    `opencode.no_gate_message`. Read the project's gates from config.json:

    ```bash
    WB_PROJECT="<absolute project path>" python3 - << 'EOF'
    import json, os, sys; sys.path.insert(0, "src")
    from werkbank import opencode
    cfg = json.load(open("config.json"))
    print("\n".join(sorted(opencode.project_gates(os.environ["WB_PROJECT"], cfg)))
          or "(keine)")
    EOF
    ```

    Then decide:

    - **Exactly one gate configured** → use its name; mention it in the confirmation
      ("empfohlen: opencode — geprüft über „<name>"").
    - **Several gates configured** → ask the user which one belongs to this ticket;
      do NOT guess (a wrong gate greens on unrelated evidence).
    - **No gate configured for this project** → do NOT create an opencode ticket
      yet. Offer BOTH honest paths to the user in one sentence:
      1. **Fall back to `claude`** and create the ticket now (fastest path).
      2. **Add a gate first**: ask the user for a name (like „Tests laufen durch")
         and the shell command that proves this class of work is done in this
         project, then persist it before creating the ticket:

         ```bash
         WB_PROJECT="<abs path>" WB_GATE_NAME="Tests laufen durch" \
         WB_GATE_CMD="python3 -m pytest tests/ -q" python3 - << 'EOF'
         import json, os
         cfg = json.load(open("config.json"))
         cfg.setdefault("gates", {}).setdefault(os.environ["WB_PROJECT"], {})[
             os.environ["WB_GATE_NAME"]] = os.environ["WB_GATE_CMD"]
         open("config.json", "w").write(json.dumps(cfg, indent=2) + "\n")
         EOF
         ```

         Then set `gate=<the new name>` on the ticket. Do this only when the
         board is not actively working an opencode ticket for this project — a
         concurrent gate write can otherwise race the reader.

    The claude lane does NOT require a gate; leave the ticket's `gate` field empty
    for `assignee=claude` unless the user explicitly asks for one.

    ## 2. Create it

    Pass `assignee` and `gate` explicitly — a missing `assignee=` defaults to
    `claude` (silently ignoring the opencode recommendation), and a missing
    `gate=` for an opencode ticket sends it straight into the "no gate" refusal.

    ```bash
    # Values go through the ENVIRONMENT, never substituted into the snippet —
    # a quote or triple-quote in user text would otherwise end the literal and
    # execute the rest as Python (WB-35 review).
    WB_TITLE="<title>" WB_DESC="<description>" WB_PROJECT="<absolute project path>" \
    WB_PRIO="normal" WB_TYPE="aufgabe" WB_ASSIGNEE="claude" WB_GATE="" \
    python3 - << 'EOF'
    import os, sys; sys.path.insert(0, "src")
    from werkbank import store
    t = store.create_ticket("tickets", title=os.environ["WB_TITLE"],
                            description=os.environ["WB_DESC"],
                            project=os.environ["WB_PROJECT"],
                            priority=os.environ["WB_PRIO"], type=os.environ["WB_TYPE"],
                            assignee=os.environ["WB_ASSIGNEE"],
                            gate=os.environ["WB_GATE"])
    print(t.id)
    EOF
    ```

    (When another Werkbank checkout is the cwd, adjust paths accordingly — the
    tickets dir and `src/` sit in the Werkbank repo root.)

    ## 3. Confirm and commit

    - Tell the user the ticket number and title back in one sentence ("Angelegt:
      **WB-17 — <Titel>**, Priorität normal, liegt in Offen.") and where it will
      appear (board column Offen). Name the estimated assignee WITH its reason in
      half a sentence („empfohlen: opencode — klein, geprüft über ‚Tests laufen
      durch', keine Eile") so the user can veto before starting the ticket.
    - For opencode tickets, INCLUDE the gate name in the confirmation — the whole
      point of §1a is that the check is visible to the user before they start it.
    - Commit the new ticket file per `git-discipline`:
      `git add tickets/ && git commit -m "Add ticket <id>: <short title>"`.
      Do not push unless the session's normal push practice applies.
    - The board picks the file up on its next poll — no restart needed.

    ## Rules

    - Creating is not starting: the ticket stays in `offen`. Only the user's drag (or
      an explicit "arbeite es ab") dispatches it.
    - Never invent acceptance criteria the user didn't imply — a wrong criterion
      steers the working agent into the wrong build.
    - **Never** create an opencode ticket without a gate — that is not "starts and
      we deal with it later", it is a guaranteed dispatch refusal. Resolve the gate
      in §1a first.
