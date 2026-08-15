---
name: start-board
description: Use when the user asks to start, open, stop, or restart the Werkbank board ("starte das Board", "mach das Board auf", "Board neu starten"), or when server code changed and needs a restart.
version: 2
---

# Start the Board

Since WB-34 the board runs as a systemd USER SERVICE (`werkbank-board.service`,
unit at `~/.config/systemd/user/`): it starts at login, restarts itself after
crashes (`Restart=always`), and binds to the host in `config.json`
(`host`, default `127.0.0.1`; port default 8765).

## Normal operations

- Status:  `systemctl --user is-active werkbank-board.service`
- Start:   `systemctl --user start werkbank-board.service`
- Restart (after changes to server.py/store.py/dispatch.py/config.json):
  `systemctl --user restart werkbank-board.service`
  — board.html needs NO restart (re-read per GET), reload the page instead.
- Stop:    `systemctl --user stop werkbank-board.service` (stays stopped until
  next login or manual start).
- ALWAYS verify afterwards with
  `curl -s http://127.0.0.1:8765/api/tickets` before telling the user anything
  runs — never claim it unverified. Logs: `journalctl --user -u werkbank-board
  -n 50` (the old /tmp/werkbank-board.log is no longer written).

## Restart safety (unchanged rules)

- Dispatched ticket agents must NEVER restart the board (their prompt says so);
  restarts are for the interactive session only.
- Before restarting, finalize any ticket this session holds in `in_arbeit`
  (set review) — the startup sweep moves stranded in_arbeit tickets to
  Fehlgeschlagen (handover markers and live chat claims are spared).

## Network exposure — user decision only

`host: 127.0.0.1` means only this machine. NEVER switch to `0.0.0.0` (LAN)
without the user's explicit, informed decision: the board has no login, and
anyone on the network could read/change tickets, browse folder names, and
START AGENT RUNS that execute commands on this machine. Spell that out first.

## Fallback without systemd (unit missing/broken)

`(setsid python3 src/werkbank/server.py > /tmp/werkbank-board.log 2>&1 &)`
then verify with curl as above.
