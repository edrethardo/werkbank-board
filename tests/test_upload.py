"""WB-66: uploading images from the phone must be safe."""
import base64
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import uploads

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_saves_a_real_png(self):
        name = uploads.save_image(self.dir, "Foto 1.PNG", PNG)
        self.assertTrue((self.dir / name).exists())
        self.assertTrue(name.endswith(".png"))
        self.assertNotIn(" ", name)

    def test_path_traversal_is_defused(self):
        name = uploads.save_image(self.dir, "../../../etc/passwd.png", PNG)
        self.assertNotIn("/", name)
        self.assertTrue((self.dir / name).exists())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [name])

    def test_non_image_is_refused(self):
        with self.assertRaises(ValueError):
            uploads.save_image(self.dir, "böse.png", b"#!/bin/sh\nrm -rf /\n")

    def test_size_limit(self):
        with self.assertRaises(ValueError):
            uploads.save_image(self.dir, "gross.png", PNG, max_bytes=10)

    def test_names_never_collide(self):
        a = uploads.save_image(self.dir, "bild.png", PNG)
        b = uploads.save_image(self.dir, "bild.png", PNG)
        self.assertNotEqual(a, b)

    def test_data_url_is_accepted(self):
        raw = uploads.decode_payload("data:image/png;base64," +
                                     base64.b64encode(PNG).decode())
        self.assertEqual(raw, PNG)


class Wb104CollisionTest(unittest.TestCase):
    """WB-104: name collisions used to retry with the SAME fallback name — a
    busy-spin proven by a frozen-clock run that hit the 6 s timeout — and
    exists->write was not atomic."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.dir = temp_dir()

    def tearDown(self):
        import shutil
        remove_tree(self.dir)

    def test_frozen_clock_collisions_resolve_promptly(self):
        import threading
        from unittest import mock
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
        frozen = mock.Mock()
        frozen.strftime = lambda fmt: "frozen-stamp"
        result = []
        with mock.patch.object(uploads, "datetime") as dt:
            dt.now.return_value = frozen
            first = uploads.save_image(self.dir, "foto.png", png)

            def second():
                result.append(uploads.save_image(self.dir, "foto.png", png))

            t = threading.Thread(target=second, daemon=True)
            t.start()
            t.join(timeout=5)   # the old code spun here forever
        self.assertFalse(t.is_alive(), "save_image hängt bei Namenskollision")
        self.assertEqual(len(result), 1)
        self.assertNotEqual(result[0], first)
        self.assertTrue((self.dir / result[0]).exists())

    def test_many_same_second_uploads_get_numbered_names(self):
        from unittest import mock
        png = b"\x89PNG\r\n\x1a\n" + b"y" * 10
        frozen = mock.Mock()
        frozen.strftime = lambda fmt: "frozen-stamp"
        with mock.patch.object(uploads, "datetime") as dt:
            dt.now.return_value = frozen
            names = [uploads.save_image(self.dir, "a.png", png) for _ in range(4)]
        self.assertEqual(len(set(names)), 4, names)


class Wb104LocationTest(unittest.TestCase):
    """Uploads must live OUTSIDE the published/committed tree."""

    def test_upload_dir_is_gitignored_and_not_under_docs(self):
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root / "src"))
        from werkbank import server
        rel = server.UPLOAD_DIR.relative_to(server.REPO_ROOT)
        self.assertEqual(rel.parts[0], "uploads")
        self.assertIn("uploads/", (root / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
