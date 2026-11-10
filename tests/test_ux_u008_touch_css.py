# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import unittest

from opos.web import page


class TestAnswerSourcesArePlainText(unittest.TestCase):
    """Wikipedia sources are shown as text, never as links (interface plan W2-16, decision D11).

    A target=_blank link in the kiosk Chromium opens a window with no tab strip and no way back, so the answer
    carries "Source: title, Wikipedia" and nothing to tap. The 44 px link rule that used to be pinned here went with it.
    """

    def test_answer_has_no_link_style(self):
        root = Path(__file__).resolve().parents[1]
        src = (root / "src/opos/web.py").read_text(encoding="utf-8")
        self.assertNotIn("#answer a", src)
        self.assertNotIn("#answer a", page())

    def test_no_script_makes_a_link_or_a_window(self):
        out = page()
        for needle in ("target='_blank'", 'target="_blank"', "E('a')", "mk('a')", "rel='noopener"):
            self.assertNotIn(needle, out)
        self.assertEqual(out.count("', Wikipedia'"), 2)  # the Ask handler (js-2.js) and the helper screen (phone-b.js)


if __name__ == "__main__":
    unittest.main()
