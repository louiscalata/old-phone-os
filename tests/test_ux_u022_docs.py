# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class TestUxU022Docs(unittest.TestCase):
    def test_readme_demo_steps_open_sheet(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        text = (root / "README.md").read_text(encoding="utf-8")
        lines = text.splitlines()
        first = next(i for i, line in enumerate(lines) if "?demo=1" in line)
        steps = "\n".join(lines[first:first + 5])
        order = ["?demo=1", "60% battery", "thin bar at the top to open battery details", "Start simulation", "Show helper", "Why?", "Plan", "Ask", "answers from the rules", "below about 25%", "Ask rests"]
        positions = [steps.find(s) for s in order]
        self.assertNotIn(-1, positions, dict(zip(order, positions)))
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
