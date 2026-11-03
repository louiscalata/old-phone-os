# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos import eol
from opos.sim import DrainSim, eol_sim

class TestEol(unittest.TestCase):
    def test_is_worn(self):
        self.assertTrue(eol.is_worn(0.35))
        self.assertFalse(eol.is_worn(0.9))

    def test_voltage_sags(self):
        self.assertLess(eol.voltage(50, 1.0, 25, 0.2), eol.voltage(50, 0.1, 25, 0.2))

    def test_call_spike_floor_range(self):
        floor = eol.burst_floor(eol.BURST_A["call"], 30, 0.2)
        self.assertGreaterEqual(floor, 10)
        self.assertLessEqual(floor, 20)

    def test_temp_worse(self):
        self.assertGreater(eol.burst_floor(1.0, 0, 0.2), eol.burst_floor(1.0, 30, 0.2))
        self.assertGreater(eol.burst_floor(1.0, 45, 0.2), eol.burst_floor(1.0, 30, 0.2))

    def test_healthy_lower_floor(self):
        self.assertLess(eol.burst_floor(1.0, 30, 0.05), eol.burst_floor(1.0, 30, 0.2))

    def test_idle_no_brownout(self):
        self.assertFalse(eol.brownout(5, eol.load_amps(0.0), 30, 0.2))

    def test_max_safe_shrinks(self):
        self.assertLess(eol.max_safe_amps(10, 30, 0.2), eol.max_safe_amps(60, 30, 0.2))

    def test_call_reserve_above_floor(self):
        self.assertGreater(eol.call_reserve(30, 0.2), eol.burst_floor(eol.MIN_CALL_A, 30, 0.2, 0.05))

    def test_r0_for_health(self):
        self.assertAlmostEqual(eol.r0_for_health(0.35), 0.2, delta=0.01)
        self.assertLess(eol.r0_for_health(1.0), eol.r0_for_health(0.35))

    def test_drainsim_no_dead_without_r0(self):
        sim = DrainSim(level=50, health=0.6, temp_c=30, speed=1.0)
        sim.advance(60, "NORMAL", [1.0], burst_a=5.0)
        self.assertFalse(sim.dead)

    def test_eol_sim_browns_out(self):
        sim = eol_sim(level=14, temp_c=30, speed=1.0)
        sim.advance(60, "NORMAL", [], burst_a=1.0)
        self.assertTrue(sim.dead)
        self.assertEqual(sim.shutdown_level, 14)

    def test_eol_sim_quiet_survives(self):
        sim = eol_sim(level=14, temp_c=30, speed=1.0)
        sim.advance(60, "CRITICAL", [])
        self.assertFalse(sim.dead)

    def test_cold_temp_kept(self):
        s = eol_sim(level=60, temp_c=0, speed=1.0)
        s.advance(60, "NORMAL", [1.0])
        self.assertLess(s.temp_c, 5)

if __name__ == "__main__":
    unittest.main()
