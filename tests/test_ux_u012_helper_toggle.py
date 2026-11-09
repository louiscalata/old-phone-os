# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import opos.web


class HelperToggleTest(unittest.TestCase):
    def test_helper_toggle_label_synced(self):
        rendered = opos.web.page()
        start = rendered.index("async function render(s){")
        end = rendered.index("async function go", start)
        section = rendered[start:end]
        self.assertIn("G('visbtn').textContent=vis?'Hide helper':'Show helper';", section)
        self.assertIn("const helperToggle=G('helpertoggle');", section)
        self.assertIn("if(helperToggle){helperToggle.textContent=vis?'Hide helper':'Show helper';}", section)


if __name__ == "__main__":
    unittest.main()
