"""Regression tests for the WB-35 security review findings."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import guard, store


class RequestGuardTest(unittest.TestCase):
    """F1/F2: only same-origin JSON requests from the local board may write."""

    def test_browser_cross_origin_post_is_rejected(self):
        ok, _ = guard.check_write({"Host": "127.0.0.1:8765",
                                   "Origin": "https://evil.example",
                                   "Content-Type": "application/json"}, 8765)
        self.assertFalse(ok)

    def test_form_content_type_is_rejected(self):
        # text/plain needs no preflight — the CSRF vector from the review.
        ok, _ = guard.check_write({"Host": "127.0.0.1:8765",
                                   "Content-Type": "text/plain"}, 8765)
        self.assertFalse(ok)

    def test_rebound_host_header_is_rejected(self):
        ok, _ = guard.check_write({"Host": "evil.test:8765",
                                   "Content-Type": "application/json"}, 8765)
        self.assertFalse(ok)

    def test_board_request_passes(self):
        for host in ("127.0.0.1:8765", "localhost:8765"):
            ok, _ = guard.check_write({"Host": host, "Origin": f"http://{host}",
                                       "Content-Type": "application/json"}, 8765)
            self.assertTrue(ok, host)

    def test_curl_without_origin_passes(self):
        ok, _ = guard.check_write({"Host": "127.0.0.1:8765",
                                   "Content-Type": "application/json"}, 8765)
        self.assertTrue(ok)

    def test_reads_only_need_a_local_host_header(self):
        self.assertTrue(guard.check_read({"Host": "127.0.0.1:8765"}, 8765)[0])
        self.assertFalse(guard.check_read({"Host": "evil.test:8765"}, 8765)[0])


class FrontmatterInjectionTest(unittest.TestCase):
    """F4: no field may smuggle extra frontmatter lines."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_newline_in_title_is_refused(self):
        with self.assertRaises(ValueError):
            store.create_ticket(self.dir, title="brav\nid: /tmp/pwn",
                                description="x")

    def test_newline_in_updatable_field_is_refused(self):
        t = store.create_ticket(self.dir, title="Normal", description="x")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id,
                                {"handover": "y\nid: ../../../tmp/pwn\nstatus: erledigt"})
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.id, t.id)
        self.assertEqual(after.status, "offen")

    def test_duplicate_frontmatter_keys_are_refused(self):
        with self.assertRaises(ValueError):
            store.parse_ticket("---\nid: WB-1\ntitle: A\nid: WB-2\n---\n\nBody\n")

    def test_foreign_id_never_renames_outside_the_folder(self):
        t = store.create_ticket(self.dir, title="Normal", description="x")
        path = next(self.dir.glob("WB-*.md"))
        path.write_text(path.read_text().replace(f"id: {t.id}", "id: ../../tmp/pwn"),
                        encoding="utf-8")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"title": "Neu"})
        self.assertEqual(list(Path("/tmp").glob("pwn-*.md")), [])


class SymlinkTest(unittest.TestCase):
    """F8: a symlinked ticket file must not be read through."""

    def test_symlinks_in_tickets_dir_are_ignored(self):
        d = Path(tempfile.mkdtemp())
        try:
            secret = d / "geheim.txt"
            secret.write_text("---\nid: WB-9\ntitle: geheim\n---\n\nInhalt\n")
            (d / "WB-99-link.md").symlink_to(secret)
            tickets, errors = store.load_tickets_with_errors(d)
            self.assertEqual(tickets, [])
            self.assertEqual(errors, [])
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()


class BrowseContainmentTest(unittest.TestCase):
    """F3: the folder picker must not enumerate the whole filesystem."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "innen").mkdir()
        self.outside = Path(tempfile.mkdtemp())
        (self.outside / "geheim").mkdir()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def test_path_outside_the_roots_is_refused(self):
        from werkbank import projects
        with self.assertRaises(ValueError) as cm:
            projects.list_dirs(str(self.outside), roots=[self.home])
        self.assertNotIn(str(self.outside), str(cm.exception))  # no path oracle

    def test_registered_project_root_is_allowed(self):
        from werkbank import projects
        r = projects.list_dirs(str(self.outside), roots=[self.home, self.outside])
        self.assertEqual([d["name"] for d in r["dirs"]], ["geheim"])

    def test_inside_home_is_allowed(self):
        from werkbank import projects
        r = projects.list_dirs(str(self.home / "innen"), roots=[self.home])
        self.assertEqual(r["path"], str(self.home / "innen"))
