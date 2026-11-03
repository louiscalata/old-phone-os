# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import re
from opos.storyboard import run

class TestStoryboard(unittest.TestCase):
    def test_run_returns_non_empty_list(self):
        out = run(fast=True)
        self.assertIsInstance(out, list)
        self.assertGreater(len(out), 0)
        self.assertTrue(all(isinstance(l, str) for l in out))

    def test_first_and_last_line_exact(self):
        out = run(fast=True)
        msg = "We're giving more capabilities and life to an old phone."
        self.assertEqual(out[0], msg)
        self.assertEqual(out[-1], msg)

    def test_scene_titles_order(self):
        out = run(fast=True)
        titles = [
            "== Old phone, worn battery ==",
            "== More life: last until 11 PM ==",
            "== More life: the rule router drives the goal ==",
            "== Battery falling ==",
            "== At 5%: a call and a message ==",
            "== Quiet by default ==",
            "== More capabilities: the rule router, shown when you opt in ==",
            "== Charging ==",
        ]
        indices = [out.index(t) for t in titles]
        for i in range(len(indices) - 1):
            self.assertLess(indices[i], indices[i + 1])

    def test_required_strings_present(self):
        out = run(fast=True)
        self.assertIn("  called +15550100", out)
        self.assertIn("  sent: On my way", out)
        self.assertIn("  quiet: yes", out)

    def test_more_life_scene_details(self):
        out = run(fast=True)
        start = out.index("== More life: last until 11 PM ==")
        end = out.index("== More life: the rule router drives the goal ==")
        section = out[start + 1:end]
        self.assertTrue(any(l.startswith("  Basic low-power mode: calls and messages work for about ") for l in section))
        self.assertTrue(any(l.startswith("  Smart low-power mode: calls and messages work for about ") for l in section))
        gain_lines = [l for l in section if l.startswith("  Gain: ") and l.endswith(" more minutes for calls and messages")]
        self.assertTrue(gain_lines)
        m = re.search(r"  Gain: (\d+) more minutes for calls and messages", gain_lines[0])
        self.assertIsNotNone(m)
        self.assertGreater(int(m.group(1)), 0)
        self.assertTrue(any(l.startswith("  Reaches 11 PM: yes") or l.startswith("  Reaches 11 PM: no") for l in section))

    def test_more_life_router_scene_details(self):
        out = run(fast=True)
        start = out.index("== More life: the rule router drives the goal ==")
        end = out.index("== Battery falling ==")
        section = out[start + 1:end]
        self.assertTrue(any(l.startswith("  Basic low-power mode: calls and messages work for about ") for l in section))
        self.assertTrue(any(l.startswith("  Rule router, last until 11 PM: calls and messages work for about ") for l in section))
        self.assertTrue(any(l.startswith("  Router gain over basic: ") and l.endswith(" minutes") for l in section))
        self.assertTrue(any(l.startswith("  Router reaches 11 PM: yes") or l.startswith("  Router reaches 11 PM: no") for l in section))

    def test_battery_lines_count_and_monotonic(self):
        out = run(fast=True)
        battery_lines = [l for l in out if l.startswith("  battery ")]
        self.assertLessEqual(len(battery_lines), 40)
        percents = []
        for l in battery_lines:
            m = re.search(r"(\d+)", l)
            if m:
                percents.append(int(m.group(1)))
        for i in range(1, len(percents)):
            self.assertLessEqual(percents[i], percents[i - 1])

    def test_quiet_scene_no_forbidden_words(self):
        out = run(fast=True)
        start = out.index("== Quiet by default ==")
        end = out.index("== More capabilities: the rule router, shown when you opt in ==")
        middle = out[start + 1:end]
        pattern = re.compile(r"\b(AI|model|Governor|assistant)\b", re.I)
        for line in middle:
            self.assertIsNone(pattern.search(line))

    def test_more_capabilities_helper(self):
        out = run(fast=True)
        idx = out.index("== More capabilities: the rule router, shown when you opt in ==")
        found = any(l.startswith("  Helper") for l in out[idx + 1:])
        self.assertTrue(found)

    def test_charging_scene(self):
        out = run(fast=True)
        idx = out.index("== Charging ==")
        section = out[idx + 1:]
        self.assertTrue(any("Charging: everything back on" in l for l in section))

if __name__ == "__main__":
    unittest.main()
