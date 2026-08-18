"""The board page must PARSE. Nothing else in the suite checks that.

Every other board test lifts a single function out of `board.html` and runs
it under node, so a syntax error anywhere else in the 1300-line script block
stays invisible to the suite — while in a browser it blanks the whole board:
no columns, no cards, no error the user can act on. One `node --check` per
script block closes that hole for every future edit to the page.
"""

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree          # noqa: E402

BOARD_PATH = (Path(__file__).resolve().parent.parent
              / "src" / "werkbank" / "board.html")
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node nicht installiert — die Board-Logik bleibt ungeprueft")
class BoardScriptSyntaxTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_every_script_block_parses(self):
        page = BOARD_PATH.read_text(encoding="utf-8")
        blocks = re.findall(r"<script>(.*?)</script>", page, re.S)
        self.assertTrue(blocks, "keine <script>-Bloecke in board.html gefunden")
        for i, code in enumerate(blocks):
            f = self.dir / f"block{i}.js"
            f.write_text(code, encoding="utf-8")
            out = subprocess.run([NODE, "--check", str(f)], capture_output=True,
                                 text=True, encoding="utf-8", timeout=60)
            self.assertEqual(out.returncode, 0,
                             f"Script-Block {i} in board.html hat einen "
                             f"Syntaxfehler:\n{out.stderr}")


if __name__ == "__main__":
    unittest.main()
