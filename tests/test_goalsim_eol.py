# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from opos.goalsim import simulate

class TestGoalsimEol(unittest.TestCase):
    def test_profile_none_returns_none(self):
        for p in ("basic", "router"):
            r = simulate(p, 80, 0.35, 25, 6, profile=None)
            self.assertIsNone(r["profile"])
            self.assertIsNone(r["shutdown_level"])

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            simulate("basic", 80, 0.35, 25, 6, profile="weird")

    def test_basic_eol_shutdown_range_and_hours(self):
        eol = simulate("basic", 80, 0.35, 25, 6, profile="eol")
        none = simulate("basic", 80, 0.35, 25, 6, profile=None)
        self.assertIsInstance(eol["shutdown_level"], float)
        self.assertGreaterEqual(eol["shutdown_level"], 10)
        self.assertLessEqual(eol["shutdown_level"], 35)
        self.assertLess(eol["hours"], none["hours"])

    def test_router_eol_profile_and_hours(self):
        router = simulate("router", 80, 0.35, 25, 6, profile="eol")
        basic = simulate("basic", 80, 0.35, 25, 6, profile="eol")
        self.assertEqual(router["profile"], "eol")
        self.assertGreater(router["hours"], basic["hours"])

    def test_eol_ignores_health(self):
        h1 = simulate("basic", 80, 0.99, 25, 6, profile="eol")["hours"]
        h2 = simulate("basic", 80, 0.35, 25, 6, profile="eol")["hours"]
        self.assertEqual(h1, h2)

    def test_cold_shuts_higher_than_warm(self):
        cold = simulate("basic", 80, 0.35, 0, 6, profile="eol")
        warm = simulate("basic", 80, 0.35, 30, 6, profile="eol")
        self.assertGreater(cold["shutdown_level"], warm["shutdown_level"])

    def test_router_call_admission_accounts_for_sustained_load(self):
        for drain in (0, 40, 80):
            with self.subTest(drain=drain):
                tick = SimpleNamespace(decision=SimpleNamespace(deferred=()), steps=())
                with patch("opos.goalsim.total_drain", return_value=drain), patch("opos.goalsim.Runtime.step", return_value=tick), patch("opos.reserve.max_safe_amps", return_value=0.5), patch("opos.goalsim.should_shutdown", return_value=False):
                    r = simulate("router", level=80.0, profile="eol", tasks=(), step_s=60.0, max_h=1/60)
                if drain == 0:
                    self.assertEqual(r["hours"], 1/60)
                    self.assertIsNone(r["shutdown_level"])
                else:
                    self.assertEqual(r["hours"], 0)
                    self.assertEqual(r["shutdown_level"], 80.0)
