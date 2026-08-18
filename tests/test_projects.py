import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import projects


class AddProjectTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.cfg = self.dir / "config.json"
        self.proj = self.dir / "mein-projekt"
        self.proj.mkdir()
        self.cfg.write_text(json.dumps({
            "port": 8765,
            "default_project": str(self.dir),
            "projects": {"Werkbank": str(self.dir)},
        }))

    def tearDown(self):
        remove_tree(self.dir)

    def test_add_persists_and_keeps_other_settings(self):
        result = projects.add_project(self.cfg, "Mein Projekt", str(self.proj))
        self.assertEqual(result["Mein Projekt"], str(self.proj))
        on_disk = json.loads(self.cfg.read_text(encoding="utf-8"))
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
        self.dir = temp_dir()
        (self.dir / "beta").mkdir()
        (self.dir / "Alpha").mkdir()
        (self.dir / ".versteckt").mkdir()
        (self.dir / "datei.txt").write_text("x", encoding="utf-8")

    def tearDown(self):
        remove_tree(self.dir)

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
        self.dir = temp_dir()
        self.cfg = self.dir / "config.json"
        self.cfg.write_text(json.dumps({"port": 8765, "projects": {"P": "/tmp"}}))

    def tearDown(self):
        remove_tree(self.dir)

    def test_set_and_unset_persists_and_keeps_settings(self):
        r = projects.set_review_mode(self.cfg, "/tmp", True)
        self.assertEqual(r, {"/tmp": True})
        on_disk = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertTrue(on_disk["nonblocking_review"]["/tmp"])
        self.assertEqual(on_disk["port"], 8765)
        self.assertEqual(projects.set_review_mode(self.cfg, "/tmp", False), {})

    def test_empty_path_rejected(self):
        with self.assertRaises(ValueError):
            projects.set_review_mode(self.cfg, "  ", True)


class DuplicatePathTest(unittest.TestCase):
    """WB-124: a project registering itself must not slip in a second time
    under a different name — the path is the identity the board dispatches on."""

    def setUp(self):
        self.dir = temp_dir()
        self.cfg = self.dir / "config.json"
        self.proj = self.dir / "mein-projekt"
        self.proj.mkdir()
        self.cfg.write_text(json.dumps({"projects": {"Alt": str(self.proj)}}))

    def tearDown(self):
        remove_tree(self.dir)

    def test_same_path_under_new_name_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            projects.add_project(self.cfg, "Neu", str(self.proj))
        msg = str(cm.exception)
        self.assertIn("Alt", msg)          # names the existing entry
        on_disk = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(list(on_disk["projects"]), ["Alt"])   # nothing added

    def test_trailing_slash_is_the_same_path(self):
        with self.assertRaises(ValueError):
            projects.add_project(self.cfg, "Neu", str(self.proj) + "/")


class HotReloadTest(unittest.TestCase):
    """WB-124: a project session registers by writing config.json directly (no
    password, no HTTP). The running board must see it without a restart — but
    only the harmless keys may be re-read live."""

    def setUp(self):
        self.dir = temp_dir()
        self.cfg_path = self.dir / "config.json"
        self.proj = self.dir / "neues-projekt"
        self.proj.mkdir()
        self.write({"projects": {"Werkbank": str(self.dir)},
                    "password_hash": "geheim", "port": 8765, "lan": True})
        self.cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))

    def tearDown(self):
        remove_tree(self.dir)

    def write(self, data):
        self.cfg_path.write_text(json.dumps(data), encoding="utf-8")

    def test_new_project_becomes_visible_without_restart(self):
        projects.refresh_from_disk(self.cfg, self.cfg_path)      # prime the stamp
        projects.add_project(self.cfg_path, "Neues Projekt", str(self.proj))
        self.assertTrue(projects.refresh_from_disk(self.cfg, self.cfg_path))
        self.assertEqual(self.cfg["projects"]["Neues Projekt"], str(self.proj))

    def test_security_relevant_keys_are_never_hot_reloaded(self):
        projects.refresh_from_disk(self.cfg, self.cfg_path)
        self.write({"projects": {}, "password_hash": "", "port": 1, "lan": False})
        projects.refresh_from_disk(self.cfg, self.cfg_path)
        self.assertEqual(self.cfg["password_hash"], "geheim")  # no auth bypass
        self.assertEqual(self.cfg["port"], 8765)
        self.assertIs(self.cfg["lan"], True)

    def test_unchanged_file_reports_no_change(self):
        projects.refresh_from_disk(self.cfg, self.cfg_path)
        self.assertFalse(projects.refresh_from_disk(self.cfg, self.cfg_path))

    def test_half_written_file_is_ignored_and_retried(self):
        projects.refresh_from_disk(self.cfg, self.cfg_path)
        self.cfg_path.write_text('{"projects": {"Kaputt"', encoding="utf-8")
        self.assertFalse(projects.refresh_from_disk(self.cfg, self.cfg_path))
        self.assertEqual(list(self.cfg["projects"]), ["Werkbank"])  # untouched
        # the bad read must not poison the stamp — the repaired file is seen
        self.write({"projects": {"Werkbank": str(self.dir), "Repariert": str(self.proj)}})
        self.assertTrue(projects.refresh_from_disk(self.cfg, self.cfg_path))
        self.assertIn("Repariert", self.cfg["projects"])
