# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class TestUxU022Docs(unittest.TestCase):
    def test_readme_demo_steps_open_sheet(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        text = (root / "README.md").read_text(encoding="utf-8")
        quickstart = text.split("## Try the desktop simulation", 1)[1].split("## Try the battery features", 1)[0]
        for phrase in ("Python 3.11+", "PYTHONPATH=src python3 -m opos.web --port 8765",
                       "http://127.0.0.1:8765/?demo=1", "Leave this terminal running; Ctrl-C to stop"):
            self.assertIn(phrase, quickstart)
        tour = text.split("## Try the battery features", 1)[1].split("Demo walkthrough:", 1)[0]
        order = ["Goal and time left", "Soft pauses and Undo", "Low-battery floor and resting helper",
                 "Charging |", "Heat floor", "Call reserve |", "Plan's saving steps", "usable charge and Why?"]
        positions = [tour.find(phrase) for phrase in order]
        self.assertNotIn(-1, positions, dict(zip(order, positions)))
        self.assertEqual(positions, sorted(positions))
        self.assertIn("60%", tour)
        self.assertIn("20%", tour)
        self.assertIn("helper is resting", tour)
        self.assertIn("Public Ask instead explains the rules", tour)



if __name__ == "__main__":
    unittest.main()
