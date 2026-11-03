# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from src.opos.assist import _with_idk_hint


class TestIdkHint(unittest.TestCase):
    def test_idk_answer_gets_hint(self):
        cases = ["I don't know.", "I don’t know.", "I do not know.", "I dont know...  "]
        for text in cases:
            with self.subTest(text=text, web=False):
                self.assertEqual(_with_idk_hint(text, False), text + " Try asking about this phone, for example what is paused, or tick Search Wikipedia for general questions.")
        for text in cases:
            with self.subTest(text=text, web=True):
                self.assertEqual(_with_idk_hint(text, True), text + " Try asking about this phone, for example what is paused.")
        for web in (False, True):
            for text in ("Paris.", "I don't know. Try restarting."):
                with self.subTest(text=text, web=web):
                    self.assertEqual(_with_idk_hint(text, web), text)
