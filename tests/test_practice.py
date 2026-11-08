# SPDX-License-Identifier: GPL-3.0-or-later
"""W1-01: the practice phone says it is practice (words, the emergency-number rule, the Home card, the real-OS controls)."""
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from opos import practice
from opos.hw.live_battery import LiveBattery
from opos.web import Phone, make_server, page

STATIC = Path(__file__).resolve().parents[1] / "src/opos/web_static"
PRACTICE_NOTE = "Practice mode: calls, texts and tasks are simulated."


class Words(unittest.TestCase):
    def test_notice_is_empty_unless_simulated(self):
        for status, in_call in (("in call", True), ("call ended", False), ("sent", False)):
            self.assertEqual(practice.notice(status, in_call, False), "")
        self.assertEqual(practice.notice("in call", True, True), "Practice call. No real call is made.")
        self.assertEqual(practice.notice("call ended", False, True), "Practice call ended.")
        self.assertEqual(practice.notice("sent", False, True), "Practice message. Nothing was sent.")
        self.assertEqual(practice.notice("ready", False, True), "")
        self.assertEqual(practice.notice("message received", False, True), "")

    def test_the_words_never_claim_a_real_call_or_text(self):
        for text in (practice.CALL, practice.NOTICES["call ended"], practice.NOTICES["sent"], practice.HOME):
            self.assertRegex(text, r"(?i)practice|simulated")
        self.assertNotIn("In call", practice.CALL)
        self.assertNotRegex(practice.NOTICES["sent"], r"(?i)^message sent")

    def test_four_digits_or_fewer_without_a_plus_are_refused(self):
        for number in ("911", "112", "999", "000", "100", "1234", "(911)", "9-1-1", " 911 ", "0911"):
            with self.subTest(number=number):
                self.assertTrue(practice.refuses(number))

    def test_a_plus_or_five_digits_or_more_are_not_refused(self):
        for number in ("+911", "+112", "+15550100", "12345", "5550100", "1 800 555 0100", "00112233"):
            with self.subTest(number=number):
                self.assertFalse(practice.refuses(number))

    def test_a_number_that_is_not_valid_is_left_to_the_ordinary_error(self):
        for number in ("", "12", "abc", "1;2;3", "+", 911, None, "1" * 65):
            with self.subTest(number=number):
                self.assertFalse(practice.refuses(number))

    def test_simulated_is_exactly_the_screen_modem(self):
        p = Phone()
        self.assertTrue(practice.simulated(p.essentials))

        class Other:
            pass
        p.essentials.modem = Other()
        self.assertFalse(practice.simulated(p.essentials))
        self.assertFalse(practice.simulated(object()))

    def test_home_note(self):
        self.assertEqual(practice.home_note(True, []), PRACTICE_NOTE)
        self.assertEqual(practice.home_note(False, []), "Calls and messages are on.")
        paused = [{"name": "maps", "id": "#1"}, {"name": "photos backup", "id": "#2"}]
        self.assertEqual(practice.home_note(True, paused), PRACTICE_NOTE + "\nPaused to save power: maps, photos backup.")
        self.assertEqual(practice.home_note(False, paused), "Calls and messages are on.\nPaused to save power: maps, photos backup.")

    def test_home_note_skips_names_the_page_never_showed(self):
        # The old page script filtered with known(): blank and "not known" names are not a task.
        rows = [{"name": ""}, {"name": "  "}, {"name": None}, {"name": "Not known yet"}, {"name": "maps"}]
        self.assertEqual(practice.home_note(False, rows), "Calls and messages are on.\nPaused to save power: maps.")
        self.assertEqual(practice.home_note(False, ["maps"]), "Calls and messages are on.\nPaused to save power: maps.")


class PhoneState(unittest.TestCase):
    def test_home_note_follows_the_modem(self):
        p = Phone()
        s = p.state()
        self.assertTrue(s["simulation"]["modem"])
        self.assertTrue(s["home_note"].startswith(PRACTICE_NOTE))
        self.assertNotIn("Calls and messages are on", s["home_note"])
        p.essentials.modem = object()
        s = p.state()
        self.assertFalse(s["simulation"]["modem"])
        self.assertTrue(s["home_note"].startswith("Calls and messages are on."))

    def test_home_note_lists_what_the_battery_view_says_is_paused(self):
        p = Phone()
        p.set_battery(15)
        s = p.state()
        names = [row["name"] for row in s["battery"]["paused"]]
        self.assertTrue(names)
        self.assertEqual(s["home_note"], PRACTICE_NOTE + "\nPaused to save power: " + ", ".join(names) + ".")
        p = Phone()
        p.set_battery(100)
        p.charging = True
        self.assertEqual(p.state()["home_note"], PRACTICE_NOTE)

    def test_a_hidden_helper_never_leaks_a_word_into_the_note(self):
        for visible in (False, True):
            p = Phone(visible=visible)
            p.set_battery(20)
            note = p.state()["home_note"]
            if not visible:
                self.assertNotRegex(note, r"\b(AI|model|Governor|assistant)\b")

    def test_emergency_call_leaves_no_trace_of_a_call(self):
        p = Phone()
        p.call("911")
        s = p.state()
        self.assertEqual(s["status"], "error: this phone cannot call emergency numbers. Use another phone.")
        self.assertFalse(s["in_call"])
        self.assertEqual(s["notice"], "This phone cannot call emergency numbers. Use another phone.")
        self.assertEqual(p.essentials.modem.events, [])
        self.assertFalse(p.essentials.in_call)

    def test_the_rule_comes_before_the_battery_and_the_other_call_checks(self):
        p = Phone()
        p.call("+15550100")
        p.call("112")  # in a call already: the useful answer is still "use another phone"
        self.assertEqual(p.state()["notice"], "This phone cannot call emergency numbers. Use another phone.")
        self.assertTrue(p.state()["in_call"], "the practice call that was running is not ended by the refusal")
        p = Phone()
        p.set_battery(0)
        p.call("911")
        self.assertEqual(p.state()["notice"], "This phone cannot call emergency numbers. Use another phone.")

    def test_a_practice_call_to_a_normal_number(self):
        p = Phone()
        p.call("+15550100")
        s = p.state()
        self.assertTrue(s["in_call"])
        self.assertEqual(s["notice"], "Practice call. No real call is made.")
        p.hang_up()
        self.assertEqual(p.state()["notice"], "Practice call ended.")
        p.send("+15550100", "Hello")
        self.assertEqual(p.state()["notice"], "Practice message. Nothing was sent.")


class Server(unittest.TestCase):
    def serve(self, phone):
        server = make_server(0, phone)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        self.token = server.RequestHandlerClass.token
        return server

    def post(self, body):
        req = urllib.request.Request(self.base + "/api", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-OPOS-Token": self.token})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_post_call_911_then_a_normal_number(self):
        self.serve(Phone())
        code, s = self.post({"action": "call", "number": "911"})
        self.assertEqual(code, 200)
        self.assertFalse(s["in_call"])
        self.assertEqual(s["notice"], "This phone cannot call emergency numbers. Use another phone.")
        code, s = self.post({"action": "call", "number": "+15550100"})
        self.assertTrue(s["in_call"])
        self.assertEqual(s["notice"], "Practice call. No real call is made.")
        self.assertTrue(s["home_note"].startswith(PRACTICE_NOTE))

    def test_a_demo_phone_takes_an_injected_text_and_a_live_phone_does_not(self):
        self.serve(Phone())
        code, s = self.post({"action": "receive", "number": "+15550100", "text": "Hello"})
        self.assertEqual(code, 200)
        self.assertEqual(s["notice"], "New message")

    def test_a_live_phone_refuses_injected_text(self):
        live = LiveBattery(reader=lambda: {"level": 55.0, "temp_c": None, "charging": False, "source": "sysfs",
                                           "health": 1.0, "health_source": "assumed"})
        phone = Phone(live_battery=live)
        self.assertTrue(phone.live)
        server = self.serve(phone)
        self.assertFalse(server.RequestHandlerClass.allow_inject)
        code, body = self.post({"action": "receive", "number": "+15550100", "text": "Hello"})
        self.assertEqual((code, body), (403, {"error": "forbidden"}))
        self.assertEqual(phone.essentials.log, [])
        code, s = self.post({"action": "call", "number": "+15550100"})  # the practice calls and texts still work
        self.assertEqual(code, 200)
        self.assertTrue(s["in_call"])

    def test_make_server_inject_flag(self):
        server = make_server(0, Phone())
        self.addCleanup(server.server_close)
        self.assertTrue(server.RequestHandlerClass.allow_inject)


class Page(unittest.TestCase):
    def test_the_home_card_is_worded_by_the_server(self):
        h = page()
        self.assertIn("s.home_note", h)
        self.assertNotIn("Calls and messages are on.", h)
        self.assertNotIn("Paused to save power: ", h)

    def test_only_the_demo_panel_shows_the_simulator_buttons(self):
        css = (STATIC / "phone-b.css").read_text(encoding="utf-8")
        self.assertIn("body:has(#demo[hidden]) :is([data-go=receive],#helpertoggle){display:none}", css)
        self.assertIn('<div id="demo" hidden>', page())
        self.assertIn('<div id="demo" data-demo="1">', page(demo=True))
        self.assertNotIn('<div id="demo" hidden>', page(demo=True))

    def test_no_wording_left_that_calls_practice_a_demo_or_a_phone_line(self):
        h = page()
        for gone in ("Demo phone.", "Demo: calls and texts", "configured phone connection", "simulated in the demo"):
            self.assertNotIn(gone, h)
        self.assertIn("Practice only: this phone cannot make real calls or send real texts yet.", h)
        self.assertIn("Volume (practice only; sound is not changed)", h)
        self.assertIn("<b>Example contacts</b>", h)
        self.assertIn("No contacts yet.", h)

    def test_the_contacts_heading_is_never_the_owners(self):
        self.assertNotRegex(page(), r"<b>Contacts</b>")

    def test_the_module_is_in_the_runtime_inventory(self):
        listed = (Path(__file__).resolve().parents[1] / "packaging/pmos/runtime-files.txt").read_text(encoding="utf-8").splitlines()
        paths = [x for x in listed if x and not x.startswith("#")]
        self.assertIn("src/opos/practice.py", paths)
        self.assertEqual(paths, sorted(paths))
        self.assertTrue(re.search(r"^from opos import .*\bpractice\b", (Path(__file__).resolve().parents[1] / "src/opos/web.py").read_text(encoding="utf-8"), re.M))


if __name__ == "__main__":
    unittest.main()
