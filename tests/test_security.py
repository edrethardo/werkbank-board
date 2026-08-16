"""Regression tests for the WB-35 security review findings."""
import os
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


@unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt",
                     "Symlinks brauchen unter Windows besondere Rechte")
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


class DefaultProjectGuardTest(unittest.TestCase):
    """WB-48: an unconfigured board must not aim a Bash-enabled agent at its
    own repository — but deliberately targeting it stays allowed."""

    def setUp(self):
        from werkbank import setup
        self.setup = setup
        self.repo = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.repo)

    def test_missing_config_file_warns(self):
        msg = self.setup.config_warning({"default_project": str(self.repo)},
                                        config_exists=False, repo_root=self.repo)
        self.assertIsNotNone(msg)
        self.assertIn("config.json", msg)

    def test_placeholder_warns(self):
        msg = self.setup.config_warning({"default_project": "/pfad/zu/deinem/projekt"},
                                        config_exists=True, repo_root=self.repo)
        self.assertIsNotNone(msg)

    def test_empty_or_missing_value_warns(self):
        for cfg in ({"default_project": ""}, {}):
            self.assertIsNotNone(self.setup.config_warning(
                cfg, config_exists=True, repo_root=self.repo))

    def test_deliberate_choice_is_silent(self):
        # The Werkbank working on itself is legitimate — it is how this tool
        # was built. Only the UNCONFIGURED fallback is dangerous.
        self.assertIsNone(self.setup.config_warning(
            {"default_project": str(self.repo)}, config_exists=True,
            repo_root=self.repo))
        self.assertIsNone(self.setup.config_warning(
            {"default_project": "/anderes/projekt"}, config_exists=True,
            repo_root=self.repo))

    def test_unconfigured_board_refuses_to_dispatch_at_itself(self):
        from werkbank import dispatch
        tickets = self.repo / "tickets"
        t = store.create_ticket(tickets, title="Gefährlich", description="",
                                project=str(self.repo))
        store.update_ticket(tickets, t.id, {"status": "in_arbeit"})
        started = []
        d = dispatch.Dispatcher(tickets, cfg={"default_project": str(self.repo),
                                              "repo_root": str(self.repo),
                                              "config_exists": False,
                                              "state_path": str(self.repo / "s.json")},
                                runner=lambda tk, on_start=None, on_event=None:
                                    (started.append(tk.id), ("x", None))[1])
        d.dispatch(t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(tickets)}[t.id]
        self.assertEqual(started, [])                     # never ran
        self.assertEqual(after.status, "fehlgeschlagen")
        self.assertIn("config.json", after.body)


class SkillPathTest(unittest.TestCase):
    """WB-47: a path inside a Python string must never contain `~` — the shell
    expands it, Python never does. This shipped broken once."""

    def _skill_files(self):
        root = Path(__file__).resolve().parent.parent
        return list(root.glob(".claude/skills/**/SKILL.md"))

    def test_no_tilde_paths_inside_quotes(self):
        import re
        offenders = []
        for p in self._skill_files():
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"""['"]~[/\w]""", line):
                    offenders.append(f"{p.name}:{n}: {line.strip()[:70]}")
        self.assertEqual(offenders, [], "Tilde in einer Zeichenkette gefunden")

    def test_werkbank_path_appears_once_per_command_block(self):
        # The path lives in a shell assignment, never in Python source.
        for name in ("werkbank-pull-ticket", "werkbank-report-bug"):
            p = [f for f in self._skill_files() if f.parent.name == name]
            if not p:
                continue
            text = p[0].read_text(encoding="utf-8")
            self.assertIn("WERKBANK=", text)
            self.assertNotIn('sys.path.insert(0, "/', text)   # no hardcoded path
            self.assertIn('os.environ["WERKBANK"]', text)


class FriendlyStartupTest(unittest.TestCase):
    """WB-49: the two failures every first-time user hits must read like German
    sentences, not like a Python traceback."""

    def setUp(self):
        from werkbank import setup
        self.setup = setup

    def test_port_in_use_message_names_the_board(self):
        msg = self.setup.port_busy_message(8765)
        self.assertIn("8765", msg)
        self.assertIn("http://127.0.0.1:8765", msg)
        self.assertIn("läuft", msg.lower())
        self.assertNotIn("Traceback", msg)

    def test_missing_claude_is_a_warning_not_a_stop(self):
        warn = self.setup.claude_warning(lambda name: None)
        self.assertIsNotNone(warn)
        self.assertIn("claude", warn.lower())
        self.assertIsNone(self.setup.claude_warning(lambda name: "/usr/bin/claude"))

    def test_service_unit_is_unbuffered(self):
        unit = Path.home() / ".config/systemd/user/werkbank-board.service"
        if not unit.exists():
            self.skipTest("kein systemd-Dienst auf dieser Maschine")
        self.assertIn("PYTHONUNBUFFERED=1", unit.read_text())


class ExposureRefusedAtTheBoundaryTest(unittest.TestCase):
    """Found by an adversarial review before the 1.0.0 release: the rule "no
    network access without a password" was enforced only in the CLI helper
    (`setup.set_lan`), not where the socket is opened. Hand-editing config.json
    — the obvious thing to try, the field is literally called `lan` — produced a
    board bound to 0.0.0.0 with `auth_required()` False: no login, whole
    network, on a tool whose tickets run shell commands. The README meanwhile
    promised that editing `host` by hand does not open the network path.
    """

    def setUp(self):
        from werkbank import server
        self.server = server

    def test_lan_without_a_password_refuses_to_start(self):
        why = self.server.exposure_refusal("0.0.0.0", True, "")
        self.assertIsNotNone(why)
        self.assertIn("Passwort", why)

    def test_hand_edited_host_without_lan_mode_refuses_to_start(self):
        why = self.server.exposure_refusal("0.0.0.0", False, "")
        self.assertIsNotNone(why)
        self.assertIn("host", why)

    def test_a_password_and_lan_mode_together_are_allowed(self):
        self.assertIsNone(self.server.exposure_refusal("0.0.0.0", True, "pbkdf2$x$y"))

    def test_localhost_is_always_fine(self):
        for host in ("127.0.0.1", "localhost", "::1", ""):
            with self.subTest(host=host):
                self.assertIsNone(self.server.exposure_refusal(host, False, ""))

    def test_a_bound_lan_ip_without_a_password_also_refuses(self):
        """Not just 0.0.0.0 — any non-local address is exposure."""
        self.assertIsNotNone(self.server.exposure_refusal("10.77.0.50", True, ""))
