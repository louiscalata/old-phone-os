# SPDX-License-Identifier: GPL-3.0-or-later
"""Calculator arithmetic, including 0.1 + 0.2, and the page stays off the main page."""
import shutil
import subprocess
import unittest
from pathlib import Path

from opos import calc_page
from tests.test_panes_pages import TOKENS, css_hexes, style_of

ROOT = Path(__file__).resolve().parents[1]
JS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const ctx = {};
vm.runInNewContext(src, ctx);
const ev = ctx.oposCalc.evaluate;
const cases = [
  ['1+2', '3'],
  ['2*(3+4)', '14'],
  ['0.1+0.2', '0.3'],
  ['10/4', '2.5'],
  ['5-8', '-3'],
];
let failed = 0;
cases.forEach(function (pair) {
  const got = ev(pair[0]);
  if (got !== pair[1]) { console.log(pair[0], got, 'expected', pair[1]); failed++; }
});
try { ev('1/0'); console.log('divide by zero did not fail'); failed++; }
catch (e) { if (e.message !== 'Cannot divide by zero') { console.log(e.message); failed++; } }
if (failed) process.exit(1);
"""


class Calc(unittest.TestCase):
    def test_table(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("no node")
        script = self.id()
        path = Path(self.id().replace(".", "_") + ".js")
        # Keep the script next to the test run, not in the repo.
        import tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        file = Path(folder.name) / "table.js"
        file.write_text(JS, encoding="utf-8")
        result = subprocess.run([node, str(file), str(ROOT / "src/opos/apps_static/calc.js")], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_page_uses_only_token_colours(self):
        html = calc_page.render("t", "n")
        self.assertIn(">Copy<", html)
        self.assertIn("oposCalc", html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)


if __name__ == "__main__":
    unittest.main()
