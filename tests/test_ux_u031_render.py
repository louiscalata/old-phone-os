# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.web import page


class RenderErrorTest(unittest.TestCase):
    def test_render_surfaces_api_error(self):
        h = page()
        self.assertNotIn("if(!s||s.error)return;", h)
        start = h.index("async function render(s){")
        end = h.index("running=!!s.running", start)
        part = h[start:end]
        self.assertIn("if(!s)return;", part)
        self.assertIn("if(s.error){", part)
        self.assertIn("const errorNotice=document.getElementById('notice');", part)
        self.assertIn("s.error==='too many requests'", part)
        rate = "Too many taps. Wait a few seconds, then try again."
        fallback = "That did not work. Reload the page, then try again."
        assign = "errorNotice.textContent"
        self.assertIn(assign, part)
        ai = part.index(assign)
        after = part[ai:]
        self.assertIn(rate, after)
        self.assertIn(fallback, after)
        ri = after.index(rate)
        fi = after.index(fallback)
        self.assertLess(ri, fi)
        hi = after.index("errorNotice.hidden=false;")
        self.assertGreater(hi, fi)
        reti = after.index("return;", hi)
        self.assertGreater(reti, hi)


if __name__ == "__main__":
    unittest.main()
