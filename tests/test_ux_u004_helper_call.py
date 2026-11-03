# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class HelperCallRepeatTapTest(unittest.TestCase):
    def test_helper_call_ignores_repeat_tap(self):
        text = web_text()
        start = text.index("async function helperCall(o,ans){")
        end = text.index("function helperReset()", start)
        body = text[start:end]
        guard = "if(document.getElementById('helperwait'))return null;"
        self.assertIn(guard, body)
        self.assertLess(body.index(guard), body.index("if(helperTimer)"))
        self.assertLess(body.index(guard), body.index("helperCtl=new AbortController()"))
        self.assertNotIn("if(helperCtl){helperCtl.abort()}if(helperTimer)", body)
