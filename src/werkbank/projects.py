"""Named project list in config.json (WB-24).

`projects` maps a display name to an absolute project path. The board's
"Projekte" dialog adds entries through add_project; writes are serialized the
same way as ticket writes (in-process lock + flock sidecar) because board
handler threads and chat sessions may write concurrently.
"""

import json
import os
import threading
from pathlib import Path

from werkbank import filelock

_LOCK = threading.Lock()


def list_dirs(path=None, roots=None) -> dict:
    """Folder listing for the board's picker (WB-25). Directories only, hidden
    ones skipped, unreadable entries ignored. pathlib keeps this OS-neutral.

    F3 (WB-35): the listing is confined to `roots` (home plus the registered
    project folders) so the picker cannot be turned into a filesystem browser,
    and refusals never echo the requested path back (no existence oracle)."""
    roots = [Path(r).expanduser().resolve() for r in (roots or [Path.home()])]
    p = Path(path).expanduser() if path else roots[0]
    p = p.resolve()
    if not any(p == r or r in p.parents for r in roots):
        raise ValueError("Dieser Ordner liegt außerhalb der erlaubten Bereiche "
                         "(Home-Verzeichnis und angelegte Projekte).")
    if not p.is_dir():
        raise ValueError("Der Ordner existiert nicht.")
    dirs = []
    try:
        for child in p.iterdir():
            try:
                if child.is_dir() and not child.name.startswith("."):
                    dirs.append({"name": child.name, "path": str(child)})
            except OSError:
                continue  # unreadable entry — skip, don't fail the listing
    except PermissionError:
        raise ValueError(f"Keine Berechtigung für diesen Ordner: {p}")
    dirs.sort(key=lambda d: d["name"].lower())
    at_root = any(p == r for r in roots)
    parent = None if (p.parent == p or at_root) else str(p.parent)
    return {"path": str(p), "parent": parent, "dirs": dirs}


def set_review_mode(config_path, path: str, nonblocking: bool) -> dict:
    """Per-project queue rule (WB-40): with nonblocking=True a ticket waiting in
    Review no longer holds back the project's queue. Returns the updated map;
    entries are removed when switched back to blocking (the default)."""
    path = (path or "").strip()
    if not path:
        raise ValueError("Bitte ein Projekt angeben.")
    config_path = Path(config_path)
    with _LOCK:
        with filelock.exclusive(config_path.parent / ".config.lock"):
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                modes = cfg.get("nonblocking_review") or {}
                if nonblocking:
                    modes[path] = True
                else:
                    modes.pop(path, None)
                cfg["nonblocking_review"] = modes
                config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8", newline="\n")
                return modes
            finally:
                pass


def add_project(config_path, name: str, path: str) -> dict:
    """Validate and persist a new project; returns the updated projects map.
    Raises ValueError with a German, user-facing message on invalid input."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Bitte einen Projektnamen angeben.")
    p = Path((path or "").strip())
    if not p.is_absolute():
        raise ValueError("Der Projektpfad muss ein absoluter Pfad sein (beginnt mit /).")
    if not p.is_dir():
        raise ValueError(f"Der Ordner existiert nicht: {p}")
    config_path = Path(config_path)
    with _LOCK:
        with filelock.exclusive(config_path.parent / ".config.lock"):
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                projects = cfg.get("projects") or {}
                if any(name.lower() == existing.lower() for existing in projects):
                    raise ValueError(f"Es gibt schon ein Projekt namens „{name}“.")
                projects[name] = str(p)
                cfg["projects"] = projects
                config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8", newline="\n")
                return projects
            finally:
                pass
