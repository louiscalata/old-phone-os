# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import opos.home as m

class TestHomeDocstrings(unittest.TestCase):
    def test_docstrings(self):
        for obj in (m, m.home_screen, m.is_quiet):
            self.assertTrue((obj.__doc__ or "").strip())
