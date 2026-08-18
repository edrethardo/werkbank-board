"""Regression tests for the WB-35 security review findings."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree

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
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

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
        path.write_text(path.read_text(encoding="utf-8").replace(f"id: {t.id}", "id: ../../tmp/pwn"),
                        encoding="utf-8")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"title": "Neu"})
        self.assertEqual(list(Path("/tmp").glob("pwn-*.md")), [])


@unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt",
                     "Symlinks brauchen unter Windows besondere Rechte")
class SymlinkTest(unittest.TestCase):
    """F8: a symlinked ticket file must not be read through."""

    def test_symlinks_in_tickets_dir_are_ignored(self):
        d = temp_dir()
        try:
            secret = d / "geheim.txt"
            secret.write_text("---\nid: WB-9\ntitle: geheim\n---\n\nInhalt\n", encoding="utf-8")
            (d / "WB-99-link.md").symlink_to(secret)
            tickets, errors = store.load_tickets_with_errors(d)
            self.assertEqual(tickets, [])
            self.assertEqual(errors, [])
        finally:
            remove_tree(d)


class BrowseContainmentTest(unittest.TestCase):
    """F3: the folder picker must not enumerate the whole filesystem."""

    def setUp(self):
        self.home = temp_dir()
        (self.home / "innen").mkdir()
        self.outside = temp_dir()
        (self.outside / "geheim").mkdir()

    def tearDown(self):
        remove_tree(self.home)
        remove_tree(self.outside)

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
        self.repo = temp_dir()

    def tearDown(self):
        remove_tree(self.repo)

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
        warn = self.setup.claude_warning(lambda name: None, candidates=[])
        self.assertIsNotNone(warn)
        self.assertIn("claude", warn.lower())
        self.assertIsNone(self.setup.claude_warning(lambda name: "/usr/bin/claude"))

    def test_no_warning_when_only_the_fallback_path_has_claude(self):
        """WB-213: the board runs as a systemd user service, whose PATH does
        not carry ~/.local/bin. `which` fails there while the dispatcher finds
        claude via the WB-76 fallback — and the user was told that starting a
        ticket would fail, which was false. Measured 2026-08-18 on this
        machine: the hint printed, ~/.local/bin/claude present, dispatch fine."""
        found = Path(__file__)                    # any path that exists
        self.assertIsNone(self.setup.claude_warning(lambda name: None,
                                                    candidates=[found]))

    def test_a_configured_claude_bin_silences_the_warning(self):
        self.assertIsNone(self.setup.claude_warning(
            lambda name: None, cfg={"claude_bin": "/eigenes/claude"},
            candidates=[]))

    def test_warning_and_dispatcher_ask_the_same_question(self):
        """The two used to disagree; they are now one function."""
        from werkbank import dispatch
        self.assertIs(dispatch.resolve_claude, self.setup.resolve_claude)

    def test_service_unit_is_unbuffered(self):
        unit = Path.home() / ".config/systemd/user/werkbank-board.service"
        if not unit.exists():
            self.skipTest("kein systemd-Dienst auf dieser Maschine")
        self.assertIn("PYTHONUNBUFFERED=1", unit.read_text(encoding="utf-8"))


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
        for host in ("127.0.0.1", "localhost", "::1", "127.0.0.5"):
            with self.subTest(host=host):
                self.assertIsNone(self.server.exposure_refusal(host, False, ""))

    def test_every_spelling_of_all_interfaces_is_refused(self):
        """Found by an adversarial review: `host: ""` was in the allow-list —
        and `bind(("", port))` is INADDR_ANY, i.e. 0.0.0.0. With `lan` off there
        is no login at all, so the value that reads like "nothing configured"
        was an unauthenticated path to running commands on this machine. The
        old test ASSERTED that hole as intended behaviour, which is why nothing
        caught it."""
        for host in ("", "0.0.0.0", "::", "*", "0", "::0", " "):
            with self.subTest(host=host):
                self.assertIsNotNone(
                    self.server.exposure_refusal(host, False, ""),
                    f"{host!r} binds every interface and must not pass")

    def test_a_name_we_cannot_resolve_counts_as_exposing(self):
        """Refusing to start is recoverable; guessing wrong is not."""
        self.assertIsNotNone(self.server.exposure_refusal("mein-rechner", False, ""))

    def test_a_bound_lan_ip_without_a_password_also_refuses(self):
        """Not just 0.0.0.0 — any non-local address is exposure."""
        self.assertIsNotNone(self.server.exposure_refusal("10.77.0.50", True, ""))


if __name__ == "__main__":
    unittest.main()


class RefusalHappensBeforeAnySideEffectTest(unittest.TestCase):
    """WB-184: a board that refuses to start must not change anything first.
    The fresh-machine test caught the startup sweep marking a healthy in_arbeit
    ticket as fehlgeschlagen on the way out of a refused start — the user then
    has a broken ticket AND no board."""

    def test_refusal_check_precedes_the_startup_sweep(self):
        """WB-229 moved the side effects (sweep, dispatcher, handover arming)
        into `boot()`. The property is unchanged: nothing may happen before
        the board has decided it is allowed to run at all."""
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parent.parent
               / "src" / "werkbank" / "server.py").read_text(encoding="utf-8")
        boot = src[src.index("def boot():"):src.index("def main():")]
        self.assertIn("sweep_orphaned", boot,
                      "the startup sweep is no longer where this test looks")
        body = src[src.index("def main():"):]
        self.assertLess(body.index("exposure_refusal"), body.index("boot()"),
                        "the board boots (sweep, dispatcher) before deciding "
                        "it will not start")
        before_boot = body[:body.index("boot()")]
        self.assertNotIn("sweep_orphaned", before_boot)


class SecurityPolicyMatchesTheCodeTest(unittest.TestCase):
    """An adversarial review found SECURITY.md promising that an attacker
    cannot aim an agent outside the configured project list. No such check
    exists — `project` is any absolute path, and the README says so honestly
    two files away. A security policy that overstates the guarantee misleads
    whoever deploys it and invites reports of designed behaviour."""

    def setUp(self):
        self.repo = Path(__file__).resolve().parent.parent
        self.policy = (self.repo / "SECURITY.md").read_text(encoding="utf-8")

    def test_it_does_not_promise_project_confinement(self):
        lowered = self.policy.lower()
        self.assertNotIn("outside the configured project list", lowered)

    def test_it_says_plainly_that_project_is_unconfined(self):
        self.assertIn("any absolute path", self.policy)

    def test_the_code_really_does_not_confine_it(self):
        """If someone ever ADDS confinement, this test fails and the policy
        should be updated to promise it."""
        server = (self.repo / "src" / "werkbank" / "server.py").read_text(encoding="utf-8")
        create = server.split("store.create_ticket(", 1)[1][:600]
        self.assertNotIn("projects", create.split("nach=")[0],
                         "project seems to be checked against the list now — "
                         "say so in SECURITY.md")
