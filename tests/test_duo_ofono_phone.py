# SPDX-License-Identifier: GPL-3.0-or-later
"""oFono stays off until OPOS_CALLS=1. Emergency numbers then reach Dial. Other short numbers do not."""
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from opos import lockpin, practice
from opos.ofono_phone import OfonoPhone, calls_enabled
from opos.ui import ScreenModem
from opos.web import Phone, make_server
from tests.test_apps_shell import post


class Bus:
    def __init__(self):
        self.calls = []

    def call(self, *args):
        self.calls.append(args)


class Ofono(unittest.TestCase):
    def test_practice_mode_does_not_touch_the_bus(self):
        self.assertFalse(calls_enabled({}))
        bus = Bus()
        phone = OfonoPhone(bus, {})
        with self.assertRaises(RuntimeError):
            phone.dial("5550100")
        with self.assertRaises(RuntimeError):
            phone.dial("911")
        with self.assertRaises(RuntimeError):
            phone.send_sms("5550100", "hello")
        self.assertEqual(bus.calls, [])

    def test_flag_dials_and_sends(self):
        bus = Bus()
        phone = OfonoPhone(bus, {"OPOS_CALLS": "1"})
        phone.dial("5550100")
        phone.send_sms("5550100", "hello")
        phone.hang_up()
        self.assertEqual(phone.calls, ["5550100"])
        self.assertEqual(phone.sent, [("5550100", "hello")])
        self.assertEqual(bus.calls[0][3], "Dial")
        self.assertEqual(bus.calls[1][3], "SendMessage")
        self.assertEqual(bus.calls[2][3], "HangupAll")

    def test_an_emergency_number_reaches_the_dial_call(self):
        bus = Bus()
        phone = OfonoPhone(bus, {"OPOS_CALLS": "1"})
        for number in ("911", "112", "999", "000", "110", "119", "9-1-1"):
            with self.subTest(number=number):
                before = len(bus.calls)
                phone.dial(number)
                self.assertEqual(bus.calls[-1][3], "Dial")
                self.assertEqual(bus.calls[-1][4], number)
                self.assertEqual(len(bus.calls), before + 1)
        self.assertEqual(phone.calls, ["911", "112", "999", "000", "110", "119", "9-1-1"])

    def test_another_short_number_is_still_refused(self):
        bus = Bus()
        phone = OfonoPhone(bus, {"OPOS_CALLS": "1"})
        for number in ("1234", "100", "123"):
            with self.subTest(number=number):
                with self.assertRaises(ValueError) as caught:
                    phone.dial(number)
                self.assertEqual(str(caught.exception), practice.REFUSED)
        self.assertEqual(bus.calls, [])
        self.assertFalse(practice.refuses("08"))
        self.assertFalse(practice.emergency("08"))

    def test_a_longer_number_still_dials(self):
        bus = Bus()
        phone = OfonoPhone(bus, {"OPOS_CALLS": "1"})
        phone.dial("+911")
        phone.dial("5550100")
        self.assertEqual(phone.calls, ["+911", "5550100"])
        self.assertEqual([row[3] for row in bus.calls], ["Dial", "Dial"])

    def test_with_calls_off_an_emergency_number_says_calls_are_off(self):
        for number in ("911", "112", "999", "000", "110", "119"):
            with self.subTest(number=number):
                screen = ScreenModem()
                with self.assertRaises(RuntimeError) as caught:
                    screen.dial(number)
                self.assertEqual(str(caught.exception), "calls are off")
                self.assertEqual(screen.events, [])
                phone = Phone()
                phone.call(number)
                state = phone.state()
                self.assertEqual(state["notice"], "Calls are off.")
                self.assertFalse(state["in_call"])
                self.assertEqual(phone.essentials.modem.events, [])

    def test_calls_on_uses_ofono_and_calls_off_stays_on_the_practice_modem(self):
        phone = Phone()
        self.assertIs(type(phone.modem), ScreenModem)
        with mock.patch.dict(os.environ, {"OPOS_CALLS": "1"}):
            live = Phone()
        self.assertIs(type(live.modem), OfonoPhone)
        self.assertIs(type(live.essentials.modem), OfonoPhone)

    def test_an_emergency_call_is_not_blocked_by_the_lock(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        bus = Bus()
        env = {"OPOS_CALLS": "1", "OPOS_LOCK": "1", "OPOS_STATE_DIR": folder.name}
        with mock.patch.dict(os.environ, env):
            phone = Phone()
            phone.modem.bus = bus
            lockpin.set_pin("1234")
            for number in ("911", "112", "999", "000", "110", "119"):
                self.assertFalse(lockpin.blocks("call", "/api", number))
            self.assertTrue(lockpin.blocks("call", "/api", "5550100"))
            self.assertTrue(lockpin.blocks("call", "/api"))
            server = make_server(0, phone)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api", {"action": "call", "number": "911"}, token)
            self.assertEqual(code, 200, raw)
            body = json.loads(raw)
            self.assertNotEqual(body.get("error"), "locked")
            self.assertTrue(body["in_call"])
            self.assertEqual(bus.calls[-1][3], "Dial")
            self.assertEqual(bus.calls[-1][4], "911")
            code, raw = post(server, "/api", {"action": "call", "number": "5550100"}, token)
            self.assertEqual(code, 403)
            self.assertEqual(json.loads(raw)["error"], "locked")
            self.assertEqual([row[4] for row in bus.calls], ["911"])


if __name__ == "__main__":
    unittest.main()
