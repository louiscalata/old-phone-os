# SPDX-License-Identifier: GPL-3.0-or-later
"""The optional deploy rule grants only its two exact ModemManager actions."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "deploy/50-opos-modemmanager.rules"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is required to evaluate the polkit JavaScript rule")
class TestModemRule(unittest.TestCase):
    def test_exact_actions_and_user_only(self):
        allowed = (
            "org.freedesktop.ModemManager1.Messaging",
            "org.freedesktop.ModemManager1.Voice",
        )
        denied = ["", "org.freedesktop.ModemManager1.Device.Control"]
        for action in allowed:
            denied.extend((action + ".Control", action + "Extra",
                           action[:-1], "prefix." + action, action.lower()))
        cases = []
        for user in ("opos", "root", "other", "opos-extra", ""):
            for action in (*allowed, *denied):
                cases.append({"user": user, "action": action,
                              "allowed": user == "opos" and action in allowed})
        driver = r'''
const fs = require("fs");
const vm = require("vm");
const callbacks = [];
const context = {polkit: {
    addRule: callback => callbacks.push(callback),
    Result: {YES: "YES"},
}};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), context);
if (callbacks.length !== 1) throw new Error("expected one polkit rule");
const results = JSON.parse(process.argv[2]).map(test =>
    callbacks[0]({id: test.action}, {user: test.user}) === context.polkit.Result.YES);
process.stdout.write(JSON.stringify(results));
'''
        result = subprocess.run(
            [NODE, "-e", driver, str(RULE), json.dumps(cases)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = json.loads(result.stdout)
        self.assertEqual(len(decisions), len(cases))
        for case, granted in zip(cases, decisions):
            with self.subTest(user=case["user"], action=case["action"]):
                self.assertEqual(granted, case["allowed"])


if __name__ == "__main__":
    unittest.main()
