# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import opos.sim as m

class TestSimDocstrings(unittest.TestCase):
    def test_docstrings(self):
        for obj in (m, m.DrainSim, m.eol_sim):
            with self.subTest(obj=getattr(obj, "__name__", repr(obj))):
                self.assertTrue((obj.__doc__ or "").strip())
