# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import types
import opos.ui

def _ratio(a, b):
    def lum(hex_color):
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b_ = int(hex_color[4:6], 16) / 255.0
        def linear(c):
            if c <= 0.04045:
                return c / 12.92
            return ((c + 0.055) / 1.055) ** 2.4
        r_lin = linear(r)
        g_lin = linear(g)
        b_lin = linear(b_)
        return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin
    L1 = lum(a)
    L2 = lum(b)
    Lmax = max(L1, L2)
    Lmin = min(L1, L2)
    return (Lmax + 0.05) / (Lmin + 0.05)

class TestTkScreenContrast(unittest.TestCase):
    def test_bright_uses_black(self):
        self.assertEqual(opos.ui._fg(70), "#000000")

    def test_dim_uses_white(self):
        self.assertEqual(opos.ui._fg(20), "#ffffff")

    def test_threshold_edges(self):
        self.assertEqual(opos.ui._fg(46), "#000000")
        self.assertEqual(opos.ui._fg(45), "#ffffff")
        self.assertEqual(opos.ui._fg(150), "#000000")
        self.assertEqual(opos.ui._fg(-5), "#ffffff")

    def test_all_levels_readable(self):
        for b in range(0, 101):
            ratio = _ratio(opos.ui._grey(b), opos.ui._fg(b))
            self.assertGreaterEqual(ratio, 4.5, f"contrast too low at brightness {b}")

    def test_refresh_sets_readable_fg(self):
        def make_fake(B):
            battery = types.SimpleNamespace(get=lambda: 50)
            temp = types.SimpleNamespace(get=lambda: 25)
            tick = types.SimpleNamespace(screen=["hi"], budget=types.SimpleNamespace(screen_brightness=B))
            runtime = types.SimpleNamespace(step=lambda *args, **kwargs: tick)
            recorded = {}
            display = types.SimpleNamespace(config=lambda **kw: recorded.update(kw))
            fake = types.SimpleNamespace(battery=battery, temp=temp, runtime=runtime, display=display)
            return fake, recorded

        fake, recorded = make_fake(70)
        opos.ui.PhoneApp.refresh(fake)
        self.assertEqual(recorded.get("fg"), "#000000")
        self.assertEqual(recorded.get("bg"), "#b2b2b2")
        self.assertEqual(recorded.get("text"), "hi")

        fake2, recorded2 = make_fake(20)
        opos.ui.PhoneApp.refresh(fake2)
        self.assertEqual(recorded2.get("fg"), "#ffffff")

if __name__ == "__main__":
    unittest.main()
