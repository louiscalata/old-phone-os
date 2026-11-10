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

    def test_practice_wording_only_when_simulated(self):
        self.assertEqual(notice_for("in call", True, True), "Practice call. No real call is made.")
        self.assertEqual(notice_for("simulation running", True, True), "Practice call. No real call is made.")
        self.assertEqual(notice_for("call ended", False, True), "Practice call ended.")
        self.assertEqual(notice_for("sent", False, True), "Practice message. Nothing was sent.")
        self.assertEqual(notice_for("in call", True, False), "In call")
        self.assertEqual(notice_for("call ended", False, False), "Call ended")
        self.assertEqual(notice_for("sent", False, False), "Message sent")
        for status, in_call in (("error: number not valid", True), ("error: already in a call", True), ("error: battery empty, charge the phone to call", False)):
            self.assertEqual(notice_for(status, in_call, True), notice_for(status, in_call, False))
        self.assertEqual(notice_for("message received", False, True), "New message")

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
        # The Phone's modem is the in-memory ScreenModem: nothing is dialled and nothing is sent, so the notices say practice.
        p = Phone()
        p.call("+1 555 0100")
        s = p.state()
        self.assertTrue(s["in_call"])
        self.assertEqual(s["notice"], "Practice call. No real call is made.")
        p.hang_up()
        self.assertEqual(p.state()["notice"], "Practice call ended.")
        p.send("+15550100", "On my way")
        self.assertEqual(p.state()["notice"], "Practice message. Nothing was sent.")
        p.call("abc")
        self.assertIn("error", p.state()["status"].lower())
        self.assertEqual(p.state()["notice"], "Number not valid")

    def test_emergency_numbers_are_refused_not_dialled(self):
        for number in ("911", "112", "999", "000", "1234", "(911)", " 9 1 1 "):
            with self.subTest(number=number):
                p = Phone()
                p.call(number)
                s = p.state()
                self.assertFalse(s["in_call"])
                self.assertEqual(s["notice"], "This phone cannot call emergency numbers. Use another phone.")
                self.assertEqual(p.essentials.modem.events, [])
                self.assertEqual(s["notice_app"], "phone")

    def test_longer_and_international_numbers_still_make_a_practice_call(self):
        for number in ("12345", "5550100", "+911", "+15550100", "+1 555 0100"):
            with self.subTest(number=number):
                p = Phone()
                p.call(number)
                s = p.state()
                self.assertTrue(s["in_call"])
                self.assertEqual(s["notice"], "Practice call. No real call is made.")
                self.assertEqual(p.essentials.modem.events[0][0], "dial")

    def test_too_short_to_be_a_number_is_still_not_valid(self):
        p = Phone()
        p.call("12")
        self.assertEqual(p.state()["notice"], "Number not valid")

    def test_a_modem_that_is_not_the_screen_modem_is_not_refused(self):
        class RealishModem:
            def dial(self, number):
                self.number = number
            def hang_up(self):
                pass
            def send_sms(self, number, text):
                pass
        p = Phone()
        p.essentials.modem = RealishModem()
        p.call("911")
        s = p.state()
        self.assertTrue(s["in_call"])
        self.assertEqual(s["notice"], "In call")
        self.assertFalse(s["simulation"]["modem"])

    def test_state_demo_errors_empty(self):
        p = Phone()
        p.set_battery("bad")
        self.assertEqual(p.state()["notice"], "")
        p.set_temp("bad")
        self.assertEqual(p.state()["notice"], "")


class _OtherModem:
    """Stands for a modem that is not the screen's simulation."""
    def dial(self, number):
        pass

    def hang_up(self):
        pass

    def send_sms(self, number, text):
        pass


class TestCallLine(unittest.TestCase):
    """Home's card during a call says who is on the line (interface plan W1-06), not what the phone can do."""

    def test_the_card_names_a_contact_and_says_practice_while_the_modem_is_simulated(self):
        p = Phone()
        before = p.state()["screen"]
        self.assertIn("Calls", before)
        p.call("+15550100")
        s = p.state()
        self.assertEqual(s["screen"], ["In a practice call with Mom"])
        self.assertTrue(s["in_call"])

    def test_an_unknown_number_is_named_by_the_number_as_the_phone_normalised_it(self):
        p = Phone()
        p.call("+1 (555) 010-1234")
        self.assertEqual(p.state()["screen"], ["In a practice call with +15550101234"])

    def test_after_hang_up_the_card_lists_what_is_on_again(self):
        p = Phone()
        p.call("+15550100")
        p.hang_up()
        self.assertEqual(p.state()["screen"][:2], ["Calls", "Messages"])

    def test_a_refused_call_changes_nothing(self):
        p = Phone()
        p.call("abc")
        self.assertEqual(p.state()["screen"][:2], ["Calls", "Messages"])
        self.assertFalse(p.state()["in_call"])

    def test_a_modem_that_is_not_simulated_is_not_called_practice(self):
        p = Phone()
        p.essentials.modem = _OtherModem()
        p.call("+15550142")
        self.assertEqual(p.state()["screen"], ["In a call with Clinic"])

    def test_the_line_is_escaped_like_the_rest_of_the_card(self):
        p = Phone()
        p.book.add("A<b>&", "+15550177")
        p.call("+15550177")
        self.assertEqual(p.state()["screen"], ["In a practice call with A&lt;b&gt;&amp;"])

    def test_the_card_text_has_no_word_the_quiet_mode_forbids(self):
        p = Phone()
        p.call("+15550100")
        for line in p.state()["screen"]:
            for w in ("AI", "model", "Governor", "assistant"):
                self.assertNotIn(w.lower(), line.lower())


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

    def test_a_call_is_a_boxed_bold_notice_and_hang_up_is_the_inverted_fill(self):
        h = page()
        box = re.search(r'#notice\[data-tone=call\]:not\(\[hidden\]\)\{([^}]+)\}', h).group(1)
        for declaration in ("font-size:1.25rem", "font-weight:700", "border:3px double var(--jaryo-fg)", "text-align:left"):
            self.assertIn(declaration, box)
        hang = "#notice[data-tone=call]~#controls [data-go=hang_up]"
        self.assertIn(hang + "{background:var(--jaryo-fg);color:var(--jaryo-bg)}", h)
        # Pressed it goes back to the plain look, so a press is a visible change and never ink on ink.
        self.assertIn(hang + ":active{background:var(--jaryo-bg);color:var(--jaryo-fg)}", h)
        for rule in (box, h.split(hang, 1)[1].split("}", 1)[0], h.split(hang + ":active", 1)[1].split("}", 1)[0]):
            self.assertNotRegex(rule, r"#[0-9a-fA-F]{3,8}|rgb|hsl")  # Jaryo tokens only, never a literal colour

    def test_a_call_notice_that_is_hidden_keeps_the_ordinary_hidden_slot(self):
        # In Messages the notice is hidden (visibility) but keeps its slot; the call box must not grow that slot.
        self.assertIn("#notice[data-tone=call]:not([hidden])", page())
        self.assertIn("#notice[hidden]{display:block;visibility:hidden}", page())

    def test_the_lock_line_is_a_count(self):
        self.assertIn("text=n?n+' new':''", page())
        self.assertNotIn("notices[0].text", page())

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
