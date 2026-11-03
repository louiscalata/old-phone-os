# SPDX-License-Identifier: GPL-3.0-or-later;
import unittest
from pathlib import Path
import opos.web

class TestEnterKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = opos.web.page()
        js_path = Path(opos.web.__file__).parent / 'web_static' / 'js-8.js'
        cls.js8 = js_path.read_text(encoding='utf-8')

    def _handler(self, src, el):
        anchor = "document.getElementById('" + el + "').addEventListener('keydown'"
        self.assertEqual(src.count(anchor), 1)
        i = src.find(anchor)
        j = src.find('});', i)
        self.assertNotEqual(j, -1)
        return src[i:j+3]

    def test_number_enter_moves_to_msg(self):
        slice_ = self._handler(self.js8, 'number')
        self.assertIn("'Enter'", slice_)
        self.assertIn('isComposing', slice_)
        self.assertIn('preventDefault()', slice_)
        self.assertIn("getElementById('msg').focus()", slice_)
        self.assertNotIn('go(', slice_)
        self.assertNotIn('click()', slice_)

    def test_msg_enter_sends(self):
        slice_ = self._handler(self.js8, 'msg')
        self.assertIn("'Enter'", slice_)
        self.assertIn('isComposing', slice_)
        self.assertIn('repeat', slice_)
        self.assertIn('preventDefault()', slice_)
        self.assertIn("go('send')", slice_)

    def test_q_enter_asks(self):
        slice_ = self._handler(self.js8, 'q')
        self.assertIn("'Enter'", slice_)
        self.assertIn('isComposing', slice_)
        self.assertIn('repeat', slice_)
        self.assertIn('preventDefault()', slice_)
        self.assertIn("getElementById('askbtn').click()", slice_)
        self.assertNotIn('helperCall', slice_)

    def test_position(self):
        html = self.html
        start_marker = "document.getElementById('goal-clear').onclick="
        end_marker = "let pollMs=1000,pollTimer=null"
        anchors = [
            "document.getElementById('number').addEventListener('keydown'",
            "document.getElementById('msg').addEventListener('keydown'",
            "document.getElementById('q').addEventListener('keydown'",
        ]
        start_idx = html.find(start_marker)
        end_idx = html.find(end_marker)
        self.assertNotEqual(start_idx, -1)
        self.assertNotEqual(end_idx, -1)
        prev = start_idx
        for a in anchors:
            idx = html.find(a, prev)
            self.assertNotEqual(idx, -1)
            self.assertGreater(idx, start_idx)
            self.assertLess(idx, end_idx)
            prev = idx

    def test_lives_in_js8(self):
        for el in ('number', 'msg', 'q'):
            self._handler(self.js8, el)

    def test_no_backslashes_added(self):
        for el in ('number', 'msg', 'q'):
            slice_ = self._handler(self.js8, el)
            self.assertNotIn(chr(92), slice_)
