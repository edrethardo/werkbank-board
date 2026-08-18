# Security

Werkbank is a **single-user, localhost-only tool**. It listens on `127.0.0.1`
by default; opening it to the LAN requires a password (`--set-password`)
and is off unless you turn it on (`--lan-on`).

## In scope

- The board's HTTP handlers (`src/werkbank/`): auth bypass, CSRF/DNS-rebinding,
  path traversal, RCE via ticket content, log/upload leakage.
- The dispatcher: nobody should be able to reach the board in the first place
  who is not supposed to. Note what is NOT promised — a ticket's `project` may
  name any absolute path, and the folder picker's confinement is convenience,
  not containment. Whoever can create or move a ticket can already run commands
  as you; that is the design, and the boundary being defended is the one around
  the board itself.
- The published skills (`.claude/skills/_user-level/`): they run in other
  projects' Claude Code sessions, so a broken skill is a real vulnerability.

## Out of scope

- Anything an attacker with shell access on the host can do. If they already
  have a shell, the board is not the interesting target.
- Claude Code itself, and what an agent chooses to do inside its configured
  permission scope (that is a Claude Code / prompt-injection concern, not a
  Werkbank one).
- Malicious ticket content authored by the owner — treated as user input to
  their own machine, not an attack surface.

## Reporting

Please **open an issue** in this repository and start the title with
`security:`. Expect a reply within a week; a fix lands when it lands.
**No bounty** — this is a personal tool released for others to look at and
reuse, not a funded project.

If a finding is severe enough that a public issue would put users at risk
before a fix exists, say so in one sentence without the details and wait for
a reply.

If you want to demonstrate a finding safely, you can point the board at an
empty tickets directory and use the tests under `tests/test_security.py` as
a starting harness.
