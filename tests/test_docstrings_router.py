# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import opos.router as m


class TestRouterDocstrings(unittest.TestCase):
    def test_docstrings(self):
        for obj in (m, m.afford, m.Router):
            self.assertTrue((obj.__doc__ or "").strip())
