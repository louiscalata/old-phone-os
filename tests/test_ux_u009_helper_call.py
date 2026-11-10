# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class HelperCallStatusTest(unittest.TestCase):
    def test_helpercall_maps_http_status(self):
        base = pathlib.Path(__file__).resolve().parent.parent
        text = (base / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        start = text.index("async function helperCall")
        end = text.index("function helperReset")
        self.assertLess(start, end)
        body = text[start:end]
        first = "if(r.status===429)"
        second = "else if(r.status===403)"
        third = "else if(r.status>=500)"
        first_msg = "Too many questions in a row. Wait a few seconds, then try again."
        second_msg = "This page is out of date. Reload it, then try again."
        third_msg = "Something went wrong. Try again in a minute."
        timing = "if(!j.error&&j.engine"
        cleanup = "if(btn.parentNode)btn.parentNode.removeChild(btn);"
        parsed = body.index("await r.json()")
        cleaned = body.index(cleanup, parsed)
        i_first = body.index(first)
        i_second = body.index(second)
        i_third = body.index(third)
        i_timing = body.index(timing)
        self.assertLess(parsed, cleaned)
        self.assertLess(cleaned, i_first)
        self.assertLess(i_first, i_second)
        self.assertLess(i_second, i_third)
        self.assertLess(i_third, i_timing)
        self.assertIn("}else if(r.status===403)", body)
        self.assertIn("}else if(r.status>=500)", body)
        self.assertIn("j={error:'" + first_msg + "'}", body[i_first:i_second])
        self.assertIn("j={error:'" + second_msg + "'}", body[i_second:i_third])
        self.assertIn("j={error:'" + third_msg + "'}", body[i_third:i_timing])
        tail = body[i_third + len(third):i_timing]
        self.assertNotIn("else", tail)
        self.assertIn("return j", body[i_timing:])
