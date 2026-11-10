# SPDX-License-Identifier: GPL-3.0-or-later
"""Radio watch lines and the restart actions. Fakes only."""
import tempfile
import unittest
from pathlib import Path

from opos.net import Net, Run
from opos.radio_watch import watch


class Watch(unittest.TestCase):
    def test_missing_and_present(self):
        root = self._root()
        self.assertEqual(watch(root), ["watch: wlan0 missing", "watch: ril_0 missing", "watch: adsp missing"])
        (root / "sys/class/net/wlan0").mkdir(parents=True)
        (root / "ril_0").write_text("x", encoding="utf-8")
        state = root / "sys/class/remoteproc/remoteproc0"
        state.mkdir(parents=True)
        (state / "state").write_text("offline\n", encoding="utf-8")
        self.assertEqual(watch(root), ["watch: wlan0 present", "watch: ril_0 present", "watch: adsp not ONLINE"])
        (state / "state").write_text("running\n", encoding="utf-8")
        self.assertEqual(watch(root)[-1], "watch: adsp ONLINE")

    def test_the_raw_state_is_not_in_the_line(self):
        root = self._root()
        state = root / "sys/class/remoteproc/adsp"
        state.mkdir(parents=True)
        (state / "state").write_text("crashed-SENTINEL\n", encoding="utf-8")
        self.assertNotIn("SENTINEL", "\n".join(watch(root)))

    def _root(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)


class Restart(unittest.TestCase):
    def test_wifi_off_then_on(self):
        calls = []

        def runner(argv, timeout=None, stdin=None):
            calls.append(list(argv))
            if argv[:3] == ["nmcli", "radio", "wifi"]:
                return Run(0, "")
            if argv[:4] == ["nmcli", "-t", "-f", "RUNNING,STATE,CONNECTIVITY,WIFI-HW,WIFI,WWAN-HW,WWAN"] and argv[4:] == ["general", "status"]:
                return Run(0, "running:connected:full:enabled:enabled:enabled:enabled\n")
            if argv[:3] == ["nmcli", "-t", "general"] and argv[3:] == ["permissions"]:
                return Run(0, "org.freedesktop.NetworkManager.enable-disable-wifi:yes\n")
            return Run(0, "")

        out = Net(runner, env={"OPOS_NETWORK": "1"}).restart_wifi()
        self.assertTrue(out["ok"], out)
        radios = [c for c in calls if c[:3] == ["nmcli", "radio", "wifi"]]
        self.assertEqual(radios, [["nmcli", "radio", "wifi", "off"], ["nmcli", "radio", "wifi", "on"]])

    def test_modem_offline_then_online_and_a_missing_tool(self):
        calls = []

        def runner(argv, timeout=None, stdin=None):
            calls.append(list(argv))
            return Run(0, "")

        out = Net(runner, env={"OPOS_NETWORK": "1"}).restart_modem()
        self.assertTrue(out["ok"], out)
        self.assertEqual([c[-1] for c in calls], ["false", "true"])
        self.assertTrue(all("Online" in c for c in calls))
        missing = Net(lambda *a: Run(127, missing=True), env={"OPOS_NETWORK": "1"}).restart_modem()
        self.assertFalse(missing["ok"])
        self.assertIn("not installed", missing["error"])
