# SPDX-License-Identifier: GPL-3.0-or-later
"""Airplane mode touches phy0 and hci0 only, and confirms when Wi-Fi is the only route."""
import unittest

from opos.airplane import confirm_reason, engage_plan, restore_plan

LIST = """0: phy0: Wireless LAN
\tSoft blocked: no
\tHard blocked: no
1: hci0: Bluetooth
\tSoft blocked: no
2: something-else: Wireless WAN
\tSoft blocked: no
"""


class Airplane(unittest.TestCase):
    def test_only_phy0_and_hci0_are_blocked(self):
        plan = engage_plan(LIST)
        indexes = [cmd[2] for cmd in plan["commands"] if cmd[0] == "rfkill"]
        self.assertEqual(indexes, ["0", "1"])
        self.assertNotIn("2", indexes)
        self.assertEqual(plan["commands"][-1][-1], "false")
        restored = restore_plan(plan["saved"])
        self.assertEqual([cmd[2] for cmd in restored if cmd[0] == "rfkill"], ["0", "1"])
        self.assertEqual(restored[-1][-1], "true")

    def test_a_radio_that_was_already_blocked_stays_blocked(self):
        text = LIST.replace("0: phy0: Wireless LAN\n\tSoft blocked: no", "0: phy0: Wireless LAN\n\tSoft blocked: yes", 1)
        plan = engage_plan(text)
        self.assertNotIn(["rfkill", "block", "0"], plan["commands"])
        self.assertNotIn(["rfkill", "unblock", "0"], restore_plan(plan["saved"]))

    def test_confirm_only_when_wifi_is_the_only_route(self):
        wifi = {"wifi": {"state": "connected"}, "usb": {"state": "down"}, "data": {"state": "off"}}
        self.assertIn("only route", confirm_reason("wifi-off", wifi))
        self.assertIn("only route", confirm_reason("forget", wifi))
        self.assertIsNone(confirm_reason("wifi-off", {**wifi, "usb": {"state": "up"}}))
        self.assertIsNone(confirm_reason("scan", wifi))
