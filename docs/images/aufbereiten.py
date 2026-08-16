"""Screenshots fürs README aufbereiten (WB-54).

Aufruf:  python3 docs/images/aufbereiten.py <board.png> <chat.png>

Schneidet bei Handy-Aufnahmen die Statusleiste oben und die Browser-Leiste
unten weg (das räumt nebenbei die Netzwerk-Adresse aus dem Bild), skaliert auf
eine README-taugliche Breite und legt die Ergebnisse als board-handy.png und
chat-tickets.png ab.
"""
import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent
TARGET_WIDTH = 420          # schmal genug für zwei Bilder nebeneinander


def prepare(src: Path, name: str, crop_top=0.055, crop_bottom=0.12):
    img = Image.open(src)
    w, h = img.size
    box = (0, int(h * crop_top), w, int(h * (1 - crop_bottom)))
    img = img.crop(box)
    ratio = TARGET_WIDTH / img.width
    img = img.resize((TARGET_WIDTH, int(img.height * ratio)), Image.LANCZOS)
    dest = OUT / name
    img.save(dest, optimize=True)
    print(f"{dest.name}: {img.width}x{img.height}, {dest.stat().st_size // 1024} KB")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    prepare(Path(sys.argv[1]), "board-handy.png")
    # Der Chat-Screenshot hat unten das Eingabefeld statt Browser-Leiste.
    prepare(Path(sys.argv[2]), "chat-tickets.png", crop_bottom=0.02)
