"""WB-263: a patch doc that says APPLIED must be telling the truth.

`docs/dev/wb166-create-ticket-skill-patch.md` was stamped "APPLIED" after only
part of it had landed: the skill got the `epic` type and a version bump, but
not the gate-resolution section and not the creation snippet that forwards
`assignee` and `gate`. An agent following it turned an opencode recommendation
into a gate-less claude ticket — the exact failure the patch existed to fix.

A stamp nobody checks is worse than no stamp: it stops the next reader from
looking. These tests check the two documents that carry one.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"
DEV = ROOT / "docs" / "dev"


class Wb166PatchIsReallyAppliedTest(unittest.TestCase):
    def setUp(self):
        self.doc = (DEV / "wb166-create-ticket-skill-patch.md").read_text(encoding="utf-8")
        self.skill = (SKILLS / "create-ticket" / "SKILL.md").read_text(encoding="utf-8")

    def test_the_doc_still_claims_to_be_applied(self):
        """If somebody un-stamps it, these tests should stop applying."""
        self.assertIn("APPLIED", self.doc)

    def test_the_gate_resolution_section_is_in_the_skill(self):
        self.assertIn("## 1a.", self.skill)
        self.assertIn("project_gates", self.skill)

    def test_the_creation_snippet_forwards_assignee_and_gate(self):
        """store.create_ticket defaults to assignee='claude', gate='' — a
        snippet that omits them silently discards the recommendation."""
        self.assertIn("assignee=os.environ[", self.skill)
        self.assertIn("gate=os.environ[", self.skill)

    def test_the_type_list_matches_the_code(self):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from werkbank import store
        for t in store.TYPES:
            self.assertIn(f"`{t}`", self.skill, f"skill does not mention type {t}")


class Wb259PatchIsMarkedObsoleteTest(unittest.TestCase):
    """The opposite case: a patch that must NOT be applied any more."""

    def test_the_doc_warns_before_the_instructions(self):
        doc = (DEV / "wb259-watcher-loop-patch.md").read_text(encoding="utf-8")
        self.assertIn("OBSOLETE", doc)
        # BEFORE the instructions, not in a footnote: the loop appears in the
        # frontmatter summary too, so anchor on the first code block.
        self.assertLess(doc.index("OBSOLETE"), doc.index("end=$((SECONDS", doc.index("# WB-259")),
                        "the warning must come BEFORE the loop it warns about")

    def test_no_shipped_skill_starts_a_watcher_loop(self):
        for skill in SKILLS.rglob("SKILL.md"):
            text = skill.read_text(encoding="utf-8")
            self.assertNotIn("SECONDS+7200", text,
                             f"{skill.relative_to(ROOT)} still starts the watcher")


class DeliveredKitSkillsMatchTheProjectTest(unittest.TestCase):
    """WB-276: `_user-level/` shipped SIX developer-kit skills frozen at v1
    while the project had been following v3 for two revisions.

    Nobody noticed because no document mentions them — they were copied in at
    setup and left. A stale copy is worse than none: it looks maintained, and
    whoever installs it gets rules the owner stopped following. Found by an
    adversarial review of the 1.2.0 release, not by anyone reading them.
    """

    KIT = ("creating-skills", "documenting", "explaining-complexity",
           "finding-knowledge", "git-discipline", "journaling")

    def test_every_delivered_kit_skill_matches_the_one_in_use(self):
        for name in self.KIT:
            delivered = SKILLS / "_user-level" / name / "SKILL.md"
            in_use = SKILLS / name / "SKILL.md"
            self.assertTrue(delivered.exists(), f"{name} vanished from delivery")
            self.assertTrue(in_use.exists(), f"{name} vanished from the project")
            self.assertEqual(
                delivered.read_text(encoding="utf-8"),
                in_use.read_text(encoding="utf-8"),
                f"the delivered {name} has drifted from the one this project "
                f"follows — ship what you use, or stop shipping it")

    def test_the_werkbank_skills_are_delivery_only(self):
        """The five Werkbank skills exist ONLY in _user-level; a second copy
        would be the same drift trap from the other direction."""
        for name in ("werkbank-pull-ticket", "werkbank-register-session",
                     "werkbank-register-project", "werkbank-report-bug",
                     "werkbank-upload-files"):
            self.assertTrue((SKILLS / "_user-level" / name / "SKILL.md").exists(),
                            f"{name} is not delivered any more")
            self.assertFalse((SKILLS / name).exists(),
                             f"{name} now exists twice — pick one")


if __name__ == "__main__":
    unittest.main()
