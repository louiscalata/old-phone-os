# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opos.hw.detect import detect, detect_generic, detect_or_generic
from opos.hw.store import builtin_names, builtin_profile


class TestHW(unittest.TestCase):
    def _write(self, root: str, rel: str, content: bytes) -> None:
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def test_detect_surface_duo(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "proc/device-tree/model", b"Surface Duo DV_B\x00")
            prof = detect(root=root)
            self.assertIsNotNone(prof)
            self.assertEqual(prof.name, "Surface Duo")

    def test_f045_duo_modem_detail_response(self):
        import copy
        import json
        fixture_path = Path(__file__).parent / "fixtures" / "surface-duo-sysfs.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        def make_runner(data):
            def run(args):
                key = " ".join(args)
                entry = data["cmds"][key]
                return entry["rc"], entry["out"], entry["err"]
            return run
        rc, out, err = make_runner(fixture)(["mmcli", "-m", "0", "-J"])
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        expected = '{"modem": {"general": {"manufacturer": "ofono", "model": "binder", "plugin": "ofono2mm", "power-state": "on", "state": "failed"}, "numbers": ["REDACTED"], "technologies": {"access": [], "current": ["lte"], "supported": ["lte"]}}}'
        self.assertEqual(out, expected)
        detail = json.loads(out)
        general = detail["modem"]["general"]
        self.assertEqual(general["manufacturer"], "ofono")
        self.assertEqual(general["model"], "binder")
        self.assertEqual(general["plugin"], "ofono2mm")
        self.assertEqual(general["state"], "failed")
        self.assertEqual(general["power-state"], "on")
        tech = detail["modem"]["technologies"]
        self.assertEqual(tech["supported"], ["lte"])
        self.assertEqual(tech["current"], ["lte"])
        self.assertEqual(tech["access"], [])
        self.assertIn("mmcli -m 0 -J", fixture.get("filtered_commands", []))
        self.assertNotIn("mmcli -m 0 -J (filtered)", fixture["cmds"])
        tampered = copy.deepcopy(fixture)
        tampered["cmds"]["mmcli -m 0 -J (filtered)"] = tampered["cmds"].pop("mmcli -m 0 -J")
        with self.assertRaises(KeyError):
            make_runner(tampered)(["mmcli", "-m", "0", "-J"])

    def test_detect_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "proc/device-tree/model", b"Unknown Model XYZ")
            prof = detect(root=root)
            self.assertEqual(prof.name, "Generic phone")
            self.assertEqual(prof.radios, ())
            self.assertIs(prof.modem, False)
            self.assertIsNone(prof.battery.health)

    def test_detect_unknown_model_infers_sysfs(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "proc/device-tree/model", b"Unknown Model XYZ")
            Path(root, "sys/class/net/wlan0/wireless").mkdir(parents=True)
            Path(root, "sys/class/wwan/wwan0").mkdir(parents=True)
            prof = detect(root=root)
            self.assertEqual(prof.name, "Generic phone")
            self.assertEqual(prof.radios, ("wifi", "lte"))
            self.assertIs(prof.modem, True)
            self.assertIsNone(prof.battery.health)
            with patch("opos.hw.detect.store.BUILTIN", None):
                prof = detect(root=root)
                self.assertEqual(prof.name, "Generic phone")
                self.assertEqual(prof.radios, ("wifi", "lte"))
                self.assertIs(prof.modem, True)
                self.assertIsNone(prof.battery.health)

    def test_detect_empty_root(self):
        with tempfile.TemporaryDirectory() as root:
            prof = detect(root=root)
            self.assertEqual(prof.name, "Generic phone")
            self.assertEqual(prof.radios, ())
            self.assertIs(prof.modem, False)
            self.assertIsNone(prof.battery.health)
            Path(root, "sys/class/net/wlan0/wireless").mkdir(parents=True)
            Path(root, "sys/class/wwan/wwan0").mkdir(parents=True)
            prof = detect(root=root)
            self.assertEqual(prof.radios, ("wifi", "lte"))
            self.assertIs(prof.modem, True)

    def test_detect_or_generic(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "proc/device-tree/model", b"Surface Duo")
            self.assertEqual(detect_or_generic(root).name, "Surface Duo")
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "proc/device-tree/model", b"Unknown Model XYZ")
            Path(root, "sys/class/bluetooth/hci0").mkdir(parents=True)
            prof = detect_or_generic(root)
            self.assertEqual((prof.name, prof.radios), ("Generic phone", ("bluetooth",)))
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(detect_or_generic(root).radios, ())
            self.assertEqual(detect(root).name, "Generic phone")

    def test_builtin_names_and_profile(self):
        names = builtin_names()
        self.assertIn("Surface Duo", names)
        with self.assertRaises(KeyError):
            builtin_profile("nope")

    def test_list_profiles_and_choose(self):
        from opos.setup.device import choose_profile, list_profiles

        profiles = list_profiles()
        ids: list[str] = []
        for p in profiles:
            if isinstance(p, str):
                ids.append(p)
            elif isinstance(p, dict):
                if "id" in p:
                    ids.append(p["id"])
                elif "name" in p:
                    ids.append(str(p["name"]).lower().replace(" ", "-"))
                else:
                    ids.append(str(p))
            elif isinstance(p, (list, tuple)) and len(p) >= 1:
                first = p[0]
                if isinstance(first, str):
                    ids.append(first)
            elif hasattr(p, "id"):
                ids.append(getattr(p, "id"))
            elif hasattr(p, "name"):
                ids.append(str(getattr(p, "name")).lower().replace(" ", "-"))
            else:
                ids.append(str(p))
        self.assertIn("surface-duo", ids)
        dev = choose_profile("surface-duo")
        if isinstance(dev, dict):
            self.assertEqual(dev.get("source"), "manual")
            self.assertEqual(dev.get("name"), "Surface Duo")
        else:
            self.assertEqual(getattr(dev, "source"), "manual")
            self.assertEqual(getattr(dev, "name"), "Surface Duo")


    def test_f026_missing_cpu_frequency_preserves_positive_drain(self):
        from unittest.mock import patch
        from opos.power import FEATURE_COST, feature_drain, total_drain
        with patch("builtins.open", side_effect=FileNotFoundError("CPU frequency unavailable")), patch("pathlib.Path.open", side_effect=FileNotFoundError("CPU frequency unavailable")):
            self.assertAlmostEqual(feature_drain()["model"], 0.8)
            self.assertAlmostEqual(total_drain(()), 10.8)
            with patch.dict(FEATURE_COST, {"model": 1.6}):
                self.assertAlmostEqual(feature_drain()["model"], 1.6)
                self.assertAlmostEqual(total_drain(()), 11.6)

    def test_generic_wifi_sysfs(self):
        with tempfile.TemporaryDirectory() as root:
            prof = detect_generic(root)
            self.assertEqual(prof.name, "Generic phone")
            self.assertEqual(prof.radios, ())
            self.assertIs(prof.modem, False)
            self.assertIsNone(prof.battery.health)
            Path(root, "sys/class/net/wlan0/wireless").mkdir(parents=True)
            self.assertEqual(detect_generic(root).radios, ("wifi",))
            with patch("opos.hw.detect.os.listdir", side_effect=OSError):
                self.assertEqual(detect_generic(root).radios, ())
        with tempfile.TemporaryDirectory() as root2:
            Path(root2, "sys/class/ieee80211/phy0").mkdir(parents=True)
            self.assertEqual(detect_generic(root2).radios, ("wifi",))

    def test_generic_radios_order_and_modem(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sys/class/net/wlan0/wireless").mkdir(parents=True)
            Path(root, "sys/class/bluetooth/hci0").mkdir(parents=True)
            Path(root, "sys/class/wwan/wwan0").mkdir(parents=True)
            Path(root, "sys/class/net/wwan0").mkdir(parents=True)
            prof = detect_generic(root)
            self.assertEqual(prof.radios, ("wifi", "bluetooth", "lte"))
            self.assertIs(prof.modem, True)
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sys/class/net/wwan0").mkdir(parents=True)
            prof = detect_generic(root)
            self.assertEqual(prof.radios, ("lte",))
            self.assertIs(prof.modem, True)
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sys/class/bluetooth/hci0").mkdir(parents=True)
            prof = detect_generic(root)
            self.assertEqual(prof.radios, ("bluetooth",))
            self.assertIs(prof.modem, False)

    def test_generic_bluetooth_lte_sysfs(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sys/class/net/wlan0/wireless").mkdir(parents=True)
            Path(root, "sys/class/bluetooth/hci0").mkdir(parents=True)
            Path(root, "sys/class/wwan/wwan0").mkdir(parents=True)
            prof = detect_generic(root)
            self.assertEqual((prof.radios, prof.modem), (("wifi", "bluetooth", "lte"), True))
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sys/class/net/wwan0").mkdir(parents=True)
            prof = detect_generic(root)
            self.assertEqual((prof.radios, prof.modem), (("lte",), True))
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sys/class/bluetooth/hci0").mkdir(parents=True)
            prof = detect_generic(root)
            self.assertEqual((prof.radios, prof.modem), (("bluetooth",), False))


if __name__ == "__main__":
    unittest.main()
