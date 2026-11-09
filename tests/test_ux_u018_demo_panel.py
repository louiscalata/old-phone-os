# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from opos.web import page


class TestDemoPanel(unittest.TestCase):
    def test_battery_value_output(self):
        h = page()
        output = '<output id="battery-val" for="battery">60%</output>'
        group = '<input id="battery" type="range" min="0" max="100" step="0.5" value="60">' + output + '</label>'
        self.assertIn(group, h)
        demo = h.index('id="demo"')
        temp = h.index('id="temp"')
        pos = h.index(group)
        self.assertLess(demo, pos)
        self.assertLess(pos, temp)
        self.assertIn("G('battery').oninput=e=>G('battery-val').textContent=Math.round(parseFloat(e.target.value))+'%';", h)
        self.assertIn("G('battery').onchange=e=>api({action:'battery',level:parseFloat(e.target.value)}).then(render);", h)

    def test_demo_slider_starts_at_the_phone_level(self):
        # Regression (lane A): the slider said 5% while the phone starts at 60% (Saver, helper answering).
        from opos.web import Phone
        h = page(demo=True)
        self.assertEqual(Phone().sim.level, 60.0)
        self.assertIn('id="battery" type="range" min="0" max="100" step="0.5" value="60"', h)
        self.assertIn('<output id="battery-val" for="battery">60%</output>', h)
        self.assertNotIn('value="5">', h)

    def test_battery_value_live(self):
        h = page()
        self.assertEqual(h.count('id="battery-val"'), 1)
        self.assertIn("G('battery').oninput=e=>G('battery-val').textContent=Math.round(parseFloat(e.target.value))+'%';", h)
        self.assertIn("G('battery').onchange=e=>api({action:'battery',level:parseFloat(e.target.value)}).then(render);", h)


if __name__ == "__main__":
    unittest.main()
