"""WB-66: uploading images from the phone must be safe."""
import base64
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import uploads

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir)

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
