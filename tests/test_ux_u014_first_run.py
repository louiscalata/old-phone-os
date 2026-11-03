# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest

from tests.web_source import web_text


class TestInitialLoad(unittest.TestCase):
    def test_initial_load_failure_text(self):
        text = web_text()
        hits = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("fetch('/api/state',")]
        # UXP23 retries once after DOMContentLoaded; only the initial fetch sets failure text.
        self.assertEqual(len(hits), 2)
        self.assertIn("if(s&&!s.error)return render(s)", hits[1])
        self.assertIn(".catch(function(){})", hits[1])
        line = hits[0]
        def norm(s):
            return re.sub(r"\s+", " ", s).strip()
        nline = norm(line)
        self.assertIn(norm("r.json()"), nline)
        self.assertLess(nline.find(norm("!s")), nline.find(norm("throw new Error")))
        self.assertLess(nline.find(norm("s.error")), nline.find(norm("throw new Error")))
        self.assertLess(nline.find(norm("throw new Error")), nline.find(norm("render(s)")))
        self.assertLess(nline.find(norm("render(s)")), nline.find(norm(".catch")))
        self.assertIn(norm("document.getElementById('screen').textContent"), nline)
        self.assertLess(nline.find(norm(".catch")), nline.find(norm("Could not reach Old Phone OS. Reload the page in a few seconds.")))
        self.assertIn(norm("Could not reach Old Phone OS. Reload the page in a few seconds."), nline)
