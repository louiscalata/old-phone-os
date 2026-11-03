# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from opos.setup.config import ConfigError, default_config, form_factor, load, save, validate
from opos.setup.device import to_config_device


class FormFactorConfigTest(unittest.TestCase):
    def test_form_factor_accepted(self):
        for value in ("slab", "foldable-book", "dual-screen", "slide-keyboard"):
            with self.subTest(value=value):
                cfg = default_config()
                cfg["device"]["form_factor"] = value
                self.assertEqual(validate(cfg), [])

    def test_form_factor_rejects_invalid(self):
        cases = [
            ("phablet", "device.form_factor must be one of slab, foldable-book, dual-screen, slide-keyboard"),
            ("", "device.form_factor must be one of slab, foldable-book, dual-screen, slide-keyboard"),
            (None, "device.form_factor must be str"),
            (True, "device.form_factor must be str"),
            (1, "device.form_factor must be str"),
            ([], "device.form_factor must be str"),
            ({}, "device.form_factor must be str"),
        ]
        for value, expected in cases:
            with self.subTest(value=repr(value)):
                cfg = default_config()
                cfg["device"]["form_factor"] = value
                self.assertIn(expected, validate(cfg))

    def test_form_factor_default_slab(self):
        legacy = default_config()
        snapshot = copy.deepcopy(legacy)
        self.assertEqual(form_factor(legacy), "slab")
        self.assertEqual(legacy, snapshot)
        for cfg in ({}, {"device": {}}):
            with self.subTest(cfg=repr(cfg)):
                before = copy.deepcopy(cfg)
                self.assertEqual(form_factor(cfg), "slab")
                self.assertEqual(cfg, before)
        for value in ("slab", "foldable-book", "dual-screen", "slide-keyboard"):
            with self.subTest(value=value):
                cfg = default_config()
                cfg["device"]["form_factor"] = value
                before = copy.deepcopy(cfg)
                self.assertEqual(form_factor(cfg), value)
                self.assertEqual(cfg, before)

    def test_to_config_device_keeps_form_factor(self):
        device = {
            "id": "test-device",
            "name": "Test Device",
            "source": "stub",
            "battery_health": None,
            "form_factor": "slide-keyboard",
            "unrelated": "extra",
        }
        before = copy.deepcopy(device)
        result = to_config_device(device)
        self.assertEqual(result["form_factor"], "slide-keyboard")
        self.assertNotIn("unrelated", result)
        self.assertEqual(device, before)
        self.assertEqual(to_config_device(result), result)
        plain = {"id": "test-device", "name": "Test Device", "source": "stub", "battery_health": None}
        self.assertNotIn("form_factor", to_config_device(plain))

    def test_form_factor_roundtrip(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = default_config()
            cfg["device"]["form_factor"] = "dual-screen"
            save(cfg, path)
            loaded = load(path)
            self.assertEqual(loaded["device"]["form_factor"], "dual-screen")
            self.assertEqual(form_factor(loaded), "dual-screen")
        for value in ("phablet", "", None, True, 1, [], {}):
            with self.subTest(value=repr(value)):
                with TemporaryDirectory() as tmp:
                    cfg = default_config()
                    cfg["device"]["form_factor"] = value
                    with self.assertRaises(ConfigError):
                        save(cfg, Path(tmp) / "config.json")


if __name__ == "__main__":
    unittest.main()
