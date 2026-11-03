# SPDX-License-Identifier: GPL-3.0-or-later;
import unittest
from opos.web import page

class TestUxU010AnswerText(unittest.TestCase):
    def _ask_slice(self):
        h = page()
        start = h.find("getElementById('askbtn').onclick")
        self.assertNotEqual(start, -1)
        end = h.find("getElementById('planbtn').onclick", start)
        self.assertGreater(end, start)
        return h[start:end]

    def test_ask_tag_skips_none_engine(self):
        self.assertIn("'none'", self._ask_slice())

    def test_ask_no_unconditional_tag(self):
        self.assertNotIn("ans.textContent=r.text+' ('", self._ask_slice())

    def test_ask_keeps_sources_loop(self):
        self.assertIn("r.sources||[]", self._ask_slice())

if __name__ == "__main__":
    unittest.main()
