# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
from opos.web import Phone, notice_for, page


FORBIDDEN = ["AI", "model", "Governor", "assistant", "battery"]


class TestNoticeFor(unittest.TestCase):
    def test_error_number(self):
        self.assertEqual(notice_for("error: number not valid", False), "Number not valid")

    def test_error_already_in_call(self):
        self.assertEqual(notice_for("error: already in a call", False), "Already in a call")

    def test_error_not_in_call(self):
        self.assertEqual(notice_for("error: not in a call", False), "Not in a call")

    def test_error_message(self):
        self.assertEqual(notice_for("error: message empty or too long", False), "Message empty or too long")

    def test_demo_errors_hidden(self):
        self.assertEqual(notice_for("error: battery not valid", False), "")
        self.assertEqual(notice_for("error: temp not valid", False), "")
        self.assertEqual(notice_for("error: speed not valid", False), "")

    def test_error_beats_in_call(self):
        self.assertEqual(notice_for("error: number not valid", True), "Number not valid")
        self.assertEqual(notice_for("error: already in a call", True), "Already in a call")

    def test_in_call(self):
        self.assertEqual(notice_for("in call", True), "In call")

    def test_mapped_statuses(self):
        self.assertEqual(notice_for("call ended", False), "Call ended")
        self.assertEqual(notice_for("sent", False), "Message sent")
        self.assertEqual(notice_for("message received", False), "New message")

    def test_quiet_statuses(self):
        for s in ["ready", "thread open", "simulation running", "simulation paused", "in call"]:
            if s == "in call":
                continue
            self.assertEqual(notice_for(s, False), "")

    def test_forbidden_words_absent(self):
        cases = [
            ("error: number not valid", False),
            ("error: already in a call", False),
            ("error: not in a call", False),
            ("error: message empty or too long", False),
            ("error: battery not valid", False),
            ("error: temp not valid", False),
            ("error: speed not valid", False),
            ("in call", True),
            ("call ended", False),
            ("sent", False),
            ("message received", False),
            ("ready", False),
            ("thread open", False),
            ("simulation running", False),
            ("simulation paused", False),
        ]
        for status, in_call in cases:
            out = notice_for(status, in_call)
            for w in FORBIDDEN:
                self.assertNotIn(w.lower(), out.lower(), msg=f"{w} in {out!r} for {status!r}")


class TestPhoneNotice(unittest.TestCase):
    def test_state_contains_notice(self):
        p = Phone()
        self.assertIn("notice", p.state())

    def test_state_notice_flows(self):
        p = Phone()
        p.call("+1 555 0100")
        s = p.state()
        self.assertTrue(s["in_call"])
        self.assertEqual(s["notice"], "In call")
        p.hang_up()
        self.assertEqual(p.state()["notice"], "Call ended")
        p.send("+15550100", "On my way")
        self.assertEqual(p.state()["notice"], "Message sent")
        p.call("abc")
        self.assertIn("error", p.state()["status"].lower())
        self.assertEqual(p.state()["notice"], "Number not valid")

    def test_state_demo_errors_empty(self):
        p = Phone()
        p.set_battery("bad")
        self.assertEqual(p.state()["notice"], "")
        p.set_temp("bad")
        self.assertEqual(p.state()["notice"], "")


class TestPageNotice(unittest.TestCase):
    def test_notice_div_present(self):
        h = page()
        self.assertIn('id="notice"', h)
        self.assertIn('role="status"', h)
        self.assertIn('aria-live="polite"', h)
        self.assertIn("hidden", h)
        self.assertIn("s.notice", h)
        # The notice is sized by the Jaryo rule (jaryo.css, in rem so the Text size setting scales it); the old 13px was dead.
        self.assertRegex(h, r"#notice\b[^{}]*\{[^}]*font-size:[\d.]+rem")
        self.assertNotIn("font-size:13px", h)
        self.assertIn("text-align: center", h.replace("text-align:center", "text-align: center"))
        self.assertIn("padding: 4px 8px", h.replace("padding:4px 8px", "padding: 4px 8px"))

    def test_notice_before_demo_and_outside(self):
        h = page()
        i_notice = h.find('id="notice"')
        i_demo = h.find('id="demo"')
        self.assertGreaterEqual(i_notice, 0)
        self.assertGreaterEqual(i_demo, 0)
        self.assertLess(i_notice, i_demo)
        demo_start = h.find('<div id="demo"')
        demo_end = h.find("</div><script>")
        notice_tag = h.find('<div id="notice"')
        self.assertLess(notice_tag, demo_start)
        self.assertFalse(demo_start < notice_tag < demo_end and False)
        # notice must be inside #phone: after screen, before phone close + demo
        i_screen = h.find('<div id="screen"></div>')
        self.assertGreaterEqual(i_screen, 0)
        self.assertLess(i_screen, i_notice)
        phone_close_demo = h.find('</div><div id="demo"')
        self.assertGreaterEqual(phone_close_demo, 0)
        self.assertLess(i_notice, phone_close_demo)


if __name__ == "__main__":
    unittest.main()
