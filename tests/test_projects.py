import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import projects


class AddProjectTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = self.dir / "config.json"
        self.proj = self.dir / "mein-projekt"
        self.proj.mkdir()
        self.cfg.write_text(json.dumps({
            "port": 8765,
            "default_project": str(self.dir),
            "projects": {"Werkbank": str(self.dir)},
        }))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_add_persists_and_keeps_other_settings(self):
        result = projects.add_project(self.cfg, "Mein Projekt", str(self.proj))
        self.assertEqual(result["Mein Projekt"], str(self.proj))
        on_disk = json.loads(self.cfg.read_text())
        self.assertEqual(on_disk["projects"]["Mein Projekt"], str(self.proj))
        self.assertEqual(on_disk["port"], 8765)  # untouched
        self.assertEqual(on_disk["projects"]["Werkbank"], str(self.dir))

    def test_missing_directory_rejected_german(self):
        with self.assertRaises(ValueError) as cm:
            projects.add_project(self.cfg, "Kaputt", str(self.dir / "gibtsnicht"))
        self.assertIn("existiert nicht", str(cm.exception))

    def test_duplicate_name_rejected(self):
        with self.assertRaises(ValueError):
            projects.add_project(self.cfg, "Werkbank", str(self.proj))
        with self.assertRaises(ValueError):
            projects.add_project(self.cfg, "  werkbank ", str(self.proj))

    def test_empty_name_and_relative_path_rejected(self):
        with self.assertRaises(ValueError):
            projects.add_project(self.cfg, "  ", str(self.proj))
        with self.assertRaises(ValueError):
            projects.add_project(self.cfg, "Relativ", "mein-projekt")

    def test_works_without_projects_key(self):
        self.cfg.write_text(json.dumps({"port": 1, "default_project": str(self.dir)}))
        result = projects.add_project(self.cfg, "Neu", str(self.proj))
        self.assertEqual(result, {"Neu": str(self.proj)})


if __name__ == "__main__":
    unittest.main()


class ListDirsTest(unittest.TestCase):
    """WB-25: server-side folder browsing for the picker dialog."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "beta").mkdir()
        (self.dir / "Alpha").mkdir()
        (self.dir / ".versteckt").mkdir()
        (self.dir / "datei.txt").write_text("x")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_lists_only_visible_dirs_sorted(self):
        r = projects.list_dirs(str(self.dir), roots=[self.dir])
        self.assertEqual([d["name"] for d in r["dirs"]], ["Alpha", "beta"])
        self.assertEqual(r["dirs"][0]["path"], str(self.dir / "Alpha"))
        self.assertEqual(r["path"], str(self.dir))
        self.assertIsNone(r["parent"])  # F3: no way above the allowed root

    def test_root_has_no_parent(self):
        # F3 (WB-35): the allowed root itself is the top — no way further up.
        r = projects.list_dirs(str(self.dir), roots=[self.dir])
        self.assertIsNone(r["parent"])

    def test_missing_path_rejected_german(self):
        with self.assertRaises(ValueError) as cm:
            projects.list_dirs(str(self.dir / "nix"), roots=[self.dir])
        self.assertIn("existiert nicht", str(cm.exception))

    def test_default_is_the_first_root(self):
        r = projects.list_dirs(None, roots=[self.dir])
        self.assertEqual(r["path"], str(self.dir.resolve()))


class ReviewModeTest(unittest.TestCase):
    """WB-40: per-project switch — does a pending review block the queue?"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = self.dir / "config.json"
        self.cfg.write_text(json.dumps({"port": 8765, "projects": {"P": "/tmp"}}))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_set_and_unset_persists_and_keeps_settings(self):
        r = projects.set_review_mode(self.cfg, "/tmp", True)
        self.assertEqual(r, {"/tmp": True})
        on_disk = json.loads(self.cfg.read_text())
        self.assertTrue(on_disk["nonblocking_review"]["/tmp"])
        self.assertEqual(on_disk["port"], 8765)
        self.assertEqual(projects.set_review_mode(self.cfg, "/tmp", False), {})

    def test_empty_path_rejected(self):
        with self.assertRaises(ValueError):
            projects.set_review_mode(self.cfg, "  ", True)
