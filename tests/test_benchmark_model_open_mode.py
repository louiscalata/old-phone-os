# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class TestBenchmarkModelOpenMode(unittest.TestCase):
    def test_no_redundant_read_mode(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        text = (root / "src" / "opos" / "benchmark_model.py").read_text(encoding="utf-8")
        self.assertIn("open(path, encoding=\"utf-8\")", text)
        self.assertNotIn("open(path, \"r\"", text)


if __name__ == "__main__":
    unittest.main()
