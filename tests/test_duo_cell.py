# SPDX-License-Identifier: GPL-3.0-or-later
"""Cellular lines from oFono and ModemManager fixtures. No equipment identifiers."""
import json
import unittest

from opos.cell import explain, parse_props, read_ofono
from opos.net import Net, Run as Tool


class Run:
    def __init__(self, ok=True, out="", missing=False):
        self.ok = ok
        self.out = out
        self.missing = missing


class Explain(unittest.TestCase):
    def test_each_plain_line(self):
        cases = (
            ({"sim.Present": "false"}, "No SIM card"),
            ({"sim.Present": "true", "sim.PinRequired": "pin"}, "SIM locked (PIN)"),
            ({"sim.Present": "true", "net.Status": "denied"}, "SIM not accepted (carrier lock)"),
            ({"sim.Present": "true", "net.Status": "searching"}, "Searching"),
            ({"sim.Present": "true", "net.Status": "registered", "data.Attached": "false"}, "Data off: using Wi-Fi"),
            ({"sim.Present": "true", "net.Status": "registered", "ims.Registered": "false", "data.Attached": "true"},
             "Calls need VoLTE, not available on this build"),
            ({"sim.Present": "true", "net.Status": "registered", "net.Name": "Example", "net.Strength": "80",
              "net.Technology": "lte", "data.Attached": "true"},
             "LTE, operator Example, signal 4 bars"),
        )
        for props, line in cases:
            with self.subTest(line=line):
                wifi = line.startswith("Data off")
                self.assertEqual(explain(props, wifi_connected=wifi)["line"], line)

    def test_identifiers_are_dropped(self):
        props = parse_props("\n".join((
            "sim.Present=true",
            "net.Status=registered",
            "net.Name=Example",
            "net.Strength=40",
            "net.Technology=lte",
            "modem.Serial=SENTINEL-IMEI",
            "sim.CardIdentifier=SENTINEL-ICCID",
            "net.SubscriberIdentity=SENTINEL-IMSI",
        )))
        self.assertNotIn("SENTINEL", json.dumps(props))
        spoken = explain(props)
        blob = json.dumps(spoken)
        self.assertNotIn("SENTINEL", blob)
        self.assertNotIn("imei", blob.lower())
        self.assertEqual(spoken["line"], "LTE, operator Example, signal 2 bars")

    def test_mm_fallback_and_missing_ofono(self):
        mm = {
            "modem.generic.state": "searching",
            "modem.generic.equipment-identifier": "SENTINEL-IMEI",
        }
        spoken = explain(None, mm)
        self.assertEqual(spoken["line"], "Searching")
        self.assertNotIn("SENTINEL", json.dumps(spoken))
        self.assertIsNone(read_ofono(lambda *a: Run(missing=True)))

    def test_status_keeps_the_plain_line_and_drops_the_identifier(self):
        def runner(argv, timeout=None, stdin=None):
            if argv and argv[0] == "mmcli":
                return Tool(0, "modem.generic.state : searching\nmodem.generic.equipment-identifier : SENTINEL-IMEI\n")
            return Tool(127, missing=True)

        status = Net(runner).status(True)
        self.assertEqual(status["modem"]["cellular"], "Searching")
        self.assertNotIn("SENTINEL", json.dumps(status))

    def test_gdbus_pairs_ignore_a_banned_key(self):
        text = "([('Status', <'registered'>), ('Serial', <'SENTINEL-IMEI'>), ('Name', <'Example'>), ('Strength', <uint8 20>), ('Technology', <'lte'>)],)"
        props = parse_props(text)
        self.assertNotIn("SENTINEL", json.dumps(props))
        self.assertEqual(explain({"sim.Present": "true", **props})["line"], "LTE, operator Example, signal 1 bars")
