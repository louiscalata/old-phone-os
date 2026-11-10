# SPDX-License-Identifier: GPL-3.0-or-later
"""oFono call plumbing stays in practice mode until OPOS_CALLS=1, and still refuses a short number."""
import os
import unittest
from pathlib import Path
from unittest import mock

from opos import practice
from opos.ofono_phone import OfonoPhone, calls_enabled
from opos.ui import ScreenModem
from opos.web import Phone

ROOT = Path(__file__).resolve().parents[1]


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

    def test_a_short_or_emergency_number_is_refused_before_the_bus(self):
        bus = Bus()
        phone = OfonoPhone(bus, {"OPOS_CALLS": "1"})
        for number in ("911", "112", "999", "1234", "9-1-1", " 911 "):
            with self.subTest(number=number):
                with self.assertRaises(ValueError) as caught:
                    phone.dial(number)
                self.assertEqual(str(caught.exception), practice.REFUSED)
        self.assertEqual(bus.calls, [])
        self.assertEqual(phone.calls, [])
        phone.dial("+911")
        phone.dial("5550100")
        self.assertEqual(phone.calls, ["+911", "5550100"])
        self.assertEqual(len(bus.calls), 2)

    def test_ofono_is_not_installed_as_the_phone_modem(self):
        for path in (ROOT / "src").rglob("*.py"):
            if path.name == "ofono_phone.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("OfonoPhone", text, str(path))
            self.assertNotIn("ofono_phone", text, str(path))
        with mock.patch.dict(os.environ, {"OPOS_CALLS": "1"}):
            phone = Phone()
        self.assertIs(type(phone.modem), ScreenModem)
        self.assertIs(type(phone.essentials.modem), ScreenModem)


if __name__ == "__main__":
    unittest.main()
