# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import opos.web


class TestHelperScroll(unittest.TestCase):
    def test_show_helper_scrolls_to_ask(self):
        html = opos.web.page()
        start = "async function toggleVis(){"
        self.assertEqual(html.count(start), 1)
        begin = html.find(start)
        line_end = html.find("\n", begin)
        if line_end == -1:
            line_end = len(html)
        src = html[begin:line_end]
        self.assertEqual(src.count("scrollIntoView"), 1)
        self.assertIn("G('ask')", src)
        self.assertIn("block:'nearest'", src)
        render_mark = "render(await api({action:'visible'"
        render_pos = src.find(render_mark)
        scroll_pos = src.find("scrollIntoView")
        self.assertNotEqual(render_pos, -1)
        self.assertNotEqual(scroll_pos, -1)
        self.assertLess(render_pos, scroll_pos)
        render_end = src.find("))", render_pos) + 2
        self.assertIn("vis", src[render_end:scroll_pos])
        self.assertIn("G('helpertoggle').onclick=toggleVis;", html)
