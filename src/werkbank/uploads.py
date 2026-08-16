"""Image uploads from the phone (WB-65).

The board is already reachable from the phone and password-protected, so the
upload rides on that. Everything here is defensive: the file name from a phone
is attacker-influenced in principle, and an "image" that is really a script
must never land on disk.

Standard library only — no image library is needed to tell PNG/JPEG/HEIC apart
by their magic bytes.
"""

import base64
import binascii
import re
from datetime import datetime
from pathlib import Path

MAX_BYTES = 15 * 1024 * 1024          # phone screenshots are a few MB

# (magic bytes, extension). HEIC carries 'ftyp' at offset 4.
SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
]


def decode_payload(data: str) -> bytes:
    """Accept a bare base64 string or a `data:image/...;base64,...` URL."""
    if not isinstance(data, str) or not data.strip():
        raise ValueError("Keine Bilddaten empfangen.")
    if data.startswith("data:"):
        _, _, data = data.partition(",")
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Bilddaten sind beschädigt.")


def _extension(raw: bytes):
    for magic, ext in SIGNATURES:
        if raw.startswith(magic):
            return ext
    if len(raw) > 12 and raw[4:8] == b"ftyp":      # HEIC/HEIF from an iPhone
        return ".heic"
    return None


def safe_name(original: str, ext: str) -> str:
    """A file name that cannot escape the target folder or surprise a shell."""
    stem = Path(original or "bild").name          # drops any directory part
    stem = Path(stem).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or "bild"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stem[:40]}-{stamp}{ext}"


def save_image(target_dir, original_name: str, raw: bytes,
               max_bytes: int = MAX_BYTES) -> str:
    """Store `raw` as an image in `target_dir`; returns the file name used."""
    if not raw:
        raise ValueError("Leere Datei.")
    if len(raw) > max_bytes:
        raise ValueError(f"Bild ist zu groß (max. {max_bytes // (1024 * 1024)} MB).")
    ext = _extension(raw)
    if ext is None:
        raise ValueError("Das ist kein Bild (PNG, JPEG, GIF oder HEIC erwartet).")
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = safe_name(original_name, ext)
    while (target_dir / name).exists():            # never overwrite
        name = safe_name(original_name + "-1", ext)
    (target_dir / name).write_bytes(raw)
    return name
