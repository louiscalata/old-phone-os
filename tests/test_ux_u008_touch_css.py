# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import unittest


class TestAnswerLinkStyles(unittest.TestCase):
    def test_answer_links_styled(self):
        root = Path(__file__).resolve().parents[1]
        src = (root / "src/opos/web.py").read_text(encoding="utf-8")
        self.assertIn("#answer a{color:#9ecbff;display:inline-block;min-height:44px;line-height:44px}", src)


if __name__ == "__main__":
    unittest.main()
