# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class HelperCallTest(unittest.TestCase):
    def test_progress_named(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        text = (root / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if "async function helperCall(o,ans)" in l)
        self.assertIn("prog.id='helperwait';prog.setAttribute('aria-label','Waiting for the helper');prog.max=expected;prog.value=0;", line)


if __name__ == "__main__":
    unittest.main()
