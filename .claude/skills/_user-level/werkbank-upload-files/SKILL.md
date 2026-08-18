---
name: werkbank-upload-files
description: Use when the user wants to get pictures or screenshots from their phone onto this machine — "vom Handy hochladen", "Screenshot schicken", "Bild hochladen", "wie kriege ich das Foto auf den Rechner" — point them at the Werkbank upload page and use the files afterwards.
version: 2
---

# Werkbank: Bilder vom Handy hochladen

## Path to the Werkbank — the ONLY line you adapt

    WERKBANK=/pfad/zur/werkbank

Never put this path inside a Python string: `~` is expanded by the SHELL only
(WB-47).

## When to use this

The user has an image on their phone (screenshot, photo) that is needed here —
for a README, a bug report, a design discussion. Chat-pasted images are visible
to the assistant but are NOT files on disk; the upload page turns them into
real files.

## 1. Make sure the board is reachable from the phone

    ls "$WERKBANK/config.json" && grep -q '"lan": true' "$WERKBANK/config.json" \
      && echo "LAN-Modus an" || echo "LAN-Modus AUS"

- LAN off → the phone cannot reach the board. Explain the trade-off and let the
  user decide (see the security section of the README); never switch it on
  unasked.
- Get the address the phone must open:

      WERKBANK=/pfad/zur/werkbank python3 - <<'EOF'
      import json, os, socket
      cfg = json.load(open(os.path.join(os.environ["WERKBANK"], "config.json")))
      ip = socket.gethostbyname(socket.gethostname())
      print(f"http://{ip}:{cfg.get('port', 8765)}/upload")
      EOF

## 2. Tell the user what to do (one message, no jargon)

> Öffne auf dem Handy **http://192.168.x.x:8765/upload**, wähle die Bilder aus
> und tippe „Hochladen". Falls es nach dem Passwort fragt: dasselbe wie beim
> Board.

## 3. Pick the files up

    ls -lt "$WERKBANK/uploads/" | head

Files are named `<originalname>-<datum>-<uhrzeit>.<ext>`, so the newest are
yours. Confirm with the user which file is which before using them.

## 4. Use them, then keep the folder honest

- Uploads land in `uploads/` — gitignored and never published (WB-104: an
  uploaded file reached the PUBLIC repo when uploads still went to
  docs/images/). An image that should BECOME part of the repo (README,
  docs) is MOVED to `docs/images/`, committed with the change that uses
  it, and added to the publisher's BINARY_ALLOWLIST — three deliberate
  steps, never automatic.
- One-off images (a screenshot for a bug report) should be deleted again once
  they served their purpose — say so instead of silently leaving clutter.
- Before publishing an image, LOOK at it: network addresses, private project
  names, other people's data. Crop or refuse rather than publish blindly.

## Notes

- Only real images are accepted (checked by content, not by extension), max
  15 MB each; names are sanitised, nothing can escape `uploads/`.
- The page needs the board's password when LAN mode is on — the phone stays
  logged in for 30 days.
