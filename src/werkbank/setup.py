"""First-run sanity checks (WB-48).

Without a `config.json` the board falls back to `default_project = <the
Werkbank checkout>`. Dragging a ticket then points a Bash-enabled agent at the
board's own repository — the opposite of what anyone wants on their first try.

Deliberately targeting the Werkbank stays allowed (this tool was built that
way): only the UNCONFIGURED fallback is treated as dangerous.
"""

import json
import shutil
import socket
from pathlib import Path

PLACEHOLDER = "/pfad/zu/deinem/projekt"


def config_warning(cfg: dict, config_exists: bool, repo_root):
    """German warning if the board is unconfigured, else None."""
    project = (cfg or {}).get("default_project") or ""
    if not config_exists:
        return ("Keine config.json vorhanden — das Standard-Projekt zeigt damit auf "
                "die Werkbank selbst. Kopiere config.example.json nach config.json "
                "und trage dein Projekt ein, bevor du ein Ticket startest.")
    if not project.strip():
        return ("In config.json fehlt das Feld default_project — trage den Ordner "
                "deines Projekts ein, bevor du ein Ticket startest.")
    if project.strip() == PLACEHOLDER:
        return ("In config.json steht noch der Beispielpfad " + PLACEHOLDER + " — "
                "trage deinen echten Projektordner ein, bevor du ein Ticket startest.")
    return None


def dispatch_refusal(cfg: dict, ticket_project: str):
    """German reason why this ticket must not start, or None.

    Only fires while the board is unconfigured AND the ticket would run inside
    the Werkbank checkout itself."""
    repo_root = (cfg or {}).get("repo_root")
    if not repo_root:
        return None
    if cfg.get("config_exists", True):
        return None
    try:
        same = Path(ticket_project or "").resolve() == Path(repo_root).resolve()
    except OSError:
        return None
    if not same:
        return None
    return ("Nicht gestartet: Die Werkbank ist noch nicht eingerichtet (keine "
            "config.json), und dieses Ticket würde einen Agenten mit "
            "Befehls-Rechten auf den Werkbank-Ordner selbst loslassen. Lege erst "
            "config.json an und trage dein Projekt ein.")


def port_busy_message(port: int) -> str:
    """What a first-time user should read instead of OSError: [Errno 98]."""
    return (
        f"Der Port {port} ist schon belegt — läuft das Board vielleicht schon? "
        f"Schau unter http://127.0.0.1:{port} nach.\n"
        f"Falls es als Dienst läuft:  systemctl --user restart werkbank-board\n"
        f"Anderen Port wählen: Feld 'port' in config.json ändern.\n"
        f"(English: port {port} is already in use - the board may be running "
        f"already.)"
    )


def claude_warning(which=shutil.which):
    """Warn (not stop) when the claude CLI is missing: the board is still
    useful for writing and organising tickets."""
    if which("claude"):
        return None
    return (
        "Das Programm 'claude' wurde nicht gefunden. Tickets anlegen und "
        "sortieren geht trotzdem — aber ein Ticket zu starten schlägt fehl, "
        "bis Claude Code installiert und angemeldet ist."
    )


def _load(config_path):
    config_path = Path(config_path)
    if not config_path.exists():
        raise ValueError(
            "Es gibt noch keine config.json. Kopiere config.example.json nach "
            "config.json und trage dein Projekt ein."
        )
    return config_path, json.loads(config_path.read_text(encoding="utf-8"))


def _save(config_path, cfg):
    Path(config_path).write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")


def set_password(config_path, password: str, secret_path=None) -> str:
    """Store ONLY the hash of `password` in config.json (WB-50), and end every
    session that is already logged in.

    Changing the password is what a person does after losing the phone that was
    logged in. Without rotating the signing secret that gesture would achieve
    nothing: the old cookie keeps working for its full 30 days."""
    from werkbank import auth
    config_path, cfg = _load(config_path)
    cfg["password_hash"] = auth.hash_password(password)   # raises if empty
    _save(config_path, cfg)
    ended = ""
    if secret_path is not None:
        auth.rotate_secret(secret_path)
        ended = ("Alle angemeldeten Geräte wurden abgemeldet und müssen sich "
                 "neu anmelden. ")
    return ("Passwort gespeichert (nur als nicht rückrechenbarer Fingerabdruck). "
            + ended
            + "Netzwerk-Modus einschalten:  python3 src/werkbank/server.py --lan-on")


def local_address(port) -> str:
    """Best guess at the address a phone in the same network must open."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.255.255", 1))     # no packet is sent
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return f"http://{ip}:{port}"


def set_lan(config_path, enabled: bool) -> str:
    """Turn network access on/off. Refuses to open up without a password."""
    config_path, cfg = _load(config_path)
    if enabled and not cfg.get("password_hash"):
        raise ValueError(
            "Erst ein Passwort setzen:  python3 src/werkbank/server.py "
            "--set-password   (ohne Passwort wäre das Board für jedes Gerät im "
            "Netz offen — und wer darauf kommt, kann Agenten starten.)"
        )
    cfg["lan"] = bool(enabled)
    cfg["host"] = "0.0.0.0" if enabled else "127.0.0.1"
    _save(config_path, cfg)
    if not enabled:
        return ("Netzwerk-Modus aus — das Board ist wieder nur auf diesem "
                "Rechner erreichbar. Jetzt neu starten:  systemctl --user "
                "restart werkbank-board")
    addr = local_address(cfg.get("port", 8765))
    return (f"Netzwerk-Modus an. Vom Handy im selben WLAN: {addr}\n"
            "Erst neu starten:  systemctl --user restart werkbank-board\n"
            "Achtung: Wer das Passwort hat, kann Agenten starten — also Befehle "
            "auf diesem Rechner ausführen. Der Verkehr im WLAN ist "
            "unverschlüsselt; in fremden Netzen besser aus lassen.")
