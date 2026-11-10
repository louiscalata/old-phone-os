# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo image polkit rule grants four NetworkManager actions, and only to opos-server.service as droidian."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from opos import net

ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "packaging/duo-os/50-opos-network.rules"
NODE = shutil.which("node")
# Pinned. Adding a NetworkManager action here, in the rule, or in opos.net.ACTIONS fails this test.
PINNED = (
    "org.freedesktop.NetworkManager.enable-disable-wifi",
    "org.freedesktop.NetworkManager.network-control",
    "org.freedesktop.NetworkManager.settings.modify.system",
    "org.freedesktop.NetworkManager.wifi.scan",
)
UNIT = "opos-server.service"


@unittest.skipUnless(NODE, "node is required to evaluate the polkit JavaScript rule")
class TestNetworkPolkitRule(unittest.TestCase):
    def test_allowed_actions_are_pinned(self):
        self.assertEqual(net.ACTIONS, PINNED)
        text = RULE.read_text(encoding="utf-8")
        for action in PINNED:
            self.assertEqual(text.count('"' + action + '"'), 1, action)
        self.assertNotIn("NetworkManager.*", text)
        self.assertEqual(text.count("polkit.addRule"), 1)
        self.assertEqual(text.count("polkit.Result.YES"), 1)

    def test_only_the_server_unit_and_the_pinned_actions(self):
        denied_actions = ["", "org.freedesktop.NetworkManager.enable-disable-wwan",
                          "org.freedesktop.NetworkManager.settings.modify.own",
                          "org.freedesktop.NetworkManager.wifi.share.open"]
        for action in PINNED:
            denied_actions.extend((action + ".extra", action[:-1], "prefix." + action, action.upper()))
        users = ("droidian", "root", "opos", "", "droidian2", "Droidian")
        units = (UNIT, "", "sshd.service", "opos-server.service.extra", "user@1000.service", None)
        cases = []
        for user in users:
            for unit in units:
                for action in (*PINNED, *denied_actions):
                    cases.append({
                        "user": user,
                        "system_unit": unit,
                        "action": action,
                        "allowed": user == "droidian" and unit == UNIT and action in PINNED,
                    })
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
const results = JSON.parse(process.argv[2]).map(test => {
    const subject = {user: test.user};
    if (test.system_unit !== null) subject.system_unit = test.system_unit;
    return callbacks[0]({id: test.action}, subject) === context.polkit.Result.YES;
});
process.stdout.write(JSON.stringify(results));
'''
        result = subprocess.run(
            [NODE, "-e", driver, str(RULE), json.dumps(cases)],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = json.loads(result.stdout)
        self.assertEqual(len(decisions), len(cases))
        for case, granted in zip(cases, decisions):
            with self.subTest(user=case["user"], unit=case["system_unit"], action=case["action"]):
                self.assertEqual(granted, case["allowed"])


if __name__ == "__main__":
    unittest.main()
