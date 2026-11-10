# SPDX-License-Identifier: GPL-3.0-or-later
"""oFono call plumbing stays in practice mode until OPOS_CALLS=1."""
import unittest

from opos.ofono_phone import OfonoPhone, calls_enabled


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

    def test_a_short_number_is_refused_when_calls_are_on(self):
        bus = Bus()
        phone = OfonoPhone(bus, {"OPOS_CALLS": "1"})
        with self.assertRaises(ValueError):
            phone.dial("911")
        self.assertEqual(bus.calls, [])
        self.assertEqual(phone.calls, [])

    def test_the_phone_page_keeps_the_screen_modem(self):
        from opos.ui import ScreenModem
        from opos.web import Phone
        phone = Phone()
        self.assertIs(type(phone.modem), ScreenModem)
