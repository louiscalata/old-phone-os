# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.sim import DrainSim, DRAIN_PER_HOUR

class TestDrainSim(unittest.TestCase):
    def test_normal_advance(self):
        sim = DrainSim(level=40, health=1.0, speed=3600)
        level = sim.advance(1, "NORMAL", [])
        self.assertAlmostEqual(level, 28.0)

    def test_worn_battery_drains_faster(self):
        sim_good = DrainSim(level=100, health=1.0, speed=3600)
        sim_worn = DrainSim(level=100, health=0.5, speed=3600)
        l_good = sim_good.advance(1, "NORMAL", [])
        l_worn = sim_worn.advance(1, "NORMAL", [])
        self.assertAlmostEqual(l_good, 88.0)
        self.assertAlmostEqual(l_worn, 76.0)
        self.assertLess(l_worn, l_good)

    def test_critical_less_than_normal(self):
        sim_n = DrainSim(level=100, health=1.0, speed=3600)
        sim_c = DrainSim(level=100, health=1.0, speed=3600)
        l_n = sim_n.advance(1, "NORMAL", [])
        l_c = sim_c.advance(1, "CRITICAL", [])
        self.assertGreater(l_c, l_n)

    def test_level_floor_zero(self):
        sim = DrainSim(level=5, health=1.0, speed=3600)
        level = sim.advance(10, "NORMAL", [])
        self.assertEqual(level, 0)
        self.assertEqual(sim.level, 0)

    def test_t_s_advances(self):
        sim = DrainSim(level=100, health=1.0, speed=60)
        sim.advance(10, "NORMAL", [])
        self.assertEqual(sim.t_s, 600)

    def test_negative_real_s_raises(self):
        sim = DrainSim(level=50, health=1.0, speed=1)
        with self.assertRaises(ValueError):
            sim.advance(-1, "NORMAL", [])

    def test_unknown_mode_raises(self):
        sim = DrainSim(level=50, health=1.0, speed=1)
        with self.assertRaises(KeyError):
            sim.advance(1, "UNKNOWN", [])

    def test_charge_caps_at_100(self):
        sim = DrainSim(level=95, health=1.0, speed=3600)
        sim.charge(1, rate_per_hour=60)
        self.assertEqual(sim.level, 100)

    def test_drain_per_hour_values(self):
        self.assertEqual(DRAIN_PER_HOUR["NORMAL"], 12.0)
        self.assertEqual(DRAIN_PER_HOUR["SAVER"], 5.0)
        self.assertEqual(DRAIN_PER_HOUR["CRITICAL"], 2.0)

if __name__ == "__main__":
    unittest.main()
