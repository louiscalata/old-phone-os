# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulation status follows the active transport without serializing backend details."""
import json
import unittest

from opos.ui import ScreenModem
from opos.web import Phone


class ExternalModemStub:
    private_detail = "external connection identity must stay private"

    def __repr__(self):
        raise AssertionError("backend identity must not be serialized")

    def dial(self, number):
        raise AssertionError("hardware calls forbidden in this test")

    def hang_up(self):
        raise AssertionError("hardware calls forbidden in this test")

    def send_sms(self, number, text):
        raise AssertionError("hardware calls forbidden in this test")


class TestSimulationState(unittest.TestCase):
    def test_known_simulator_is_reported_in_quiet_state(self):
        phone = Phone(visible=False)
        state = phone.state()
        self.assertEqual(state["simulation"], {"modem": True})
        self.assertNotIn("helper", state)
        self.assertEqual(json.dumps(state["simulation"]), '{"modem": true}')

    def test_external_active_transport_overrides_stale_simulator_alias(self):
        phone = Phone()
        phone.essentials.modem = ExternalModemStub()
        self.assertIs(type(phone.modem), ScreenModem)
        state = phone.state()
        self.assertEqual(state["simulation"], {"modem": False})
        encoded = json.dumps(state)
        self.assertNotIn(ExternalModemStub.private_detail, encoded)
        self.assertNotIn("ExternalModemStub", encoded)

    def test_unused_external_alias_does_not_override_active_simulator(self):
        phone = Phone()
        phone.modem = ExternalModemStub()
        self.assertIs(type(phone.essentials.modem), ScreenModem)
        self.assertEqual(phone.state()["simulation"], {"modem": True})

    def test_overridden_simulator_subclass_is_not_claimed_simulated(self):
        class OtherTransport(ScreenModem):
            def dial(self, number):
                raise AssertionError("hardware calls forbidden in this test")

        phone = Phone()
        phone.essentials.modem = OtherTransport()
        self.assertIsInstance(phone.essentials.modem, ScreenModem)
        self.assertEqual(phone.state()["simulation"], {"modem": False})

    def test_unknown_transport_is_not_claimed_simulated(self):
        phone = Phone()
        phone.essentials.modem = None
        self.assertEqual(phone.state()["simulation"], {"modem": False})


if __name__ == "__main__":
    unittest.main()
