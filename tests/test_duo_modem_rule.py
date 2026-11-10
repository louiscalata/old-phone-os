# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo modem rule names droidian. The postmarketOS rule still names opos."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging/duo-os/50-opos-modem.rules"
PMOS = ROOT / "deploy/50-opos-modemmanager.rules"
NODE = shutil.which("node")

DRIVER = r'''
const fs = require("fs");
const vm = require("vm");
const callbacks = [];
const context = {polkit: {addRule: cb => callbacks.push(cb), Result: {YES: "YES"}}};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), context);
if (callbacks.length !== 1) throw new Error("expected one rule");
const out = JSON.parse(process.argv[2]).map(test => callbacks[0]({id: test.action}, {user: test.user}) === "YES");
process.stdout.write(JSON.stringify(out));
'''


@unittest.skipUnless(NODE, "node is required to evaluate the polkit JavaScript rule")
class DuoModemRule(unittest.TestCase):
    def allows(self, path, user, action):
        cases = [{"user": user, "action": action}]
        proc = subprocess.run([NODE, "-e", DRIVER, str(path), json.dumps(cases)], capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)[0]

    def test_droidian_gets_voice_and_messaging_only(self):
        for action in ("org.freedesktop.ModemManager1.Voice", "org.freedesktop.ModemManager1.Messaging"):
            self.assertTrue(self.allows(DUO, "droidian", action), action)
            self.assertFalse(self.allows(DUO, "opos", action), action)
        self.assertFalse(self.allows(DUO, "droidian", "org.freedesktop.ModemManager1.Device.Control"))

    def test_the_postmarketos_rule_still_names_opos(self):
        self.assertTrue(self.allows(PMOS, "opos", "org.freedesktop.ModemManager1.Voice"))
        self.assertFalse(self.allows(PMOS, "droidian", "org.freedesktop.ModemManager1.Voice"))

    def test_the_image_build_installs_the_duo_rule(self):
        text = (ROOT / "packaging/duo-os/build-image.sh").read_text(encoding="utf-8")
        self.assertIn("50-opos-modem.rules", text)
        self.assertNotIn("50-opos-modemmanager.rules", text)


if __name__ == "__main__":
    unittest.main()
