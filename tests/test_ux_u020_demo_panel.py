# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class RenderBatterySync(unittest.TestCase):
    def test_render_syncs_battery_slider(self):
        text = web_text()
        start = text.index("async function render(s){")
        end = text.index("async function go", start)
        part = text[start:end]
        self.assertIn("document.getElementById('battery-val')", part)
        self.assertIn("document.createElement('span')", part)
        self.assertIn("insertAdjacentElement('afterend',", part)
        focus = "document.activeElement!==document.getElementById('battery')"
        slider = "document.getElementById('battery').value=s.battery.percent"
        label = "document.getElementById('battery-val').textContent=Math.round(s.battery.percent)+'%'"
        self.assertIn(focus, part)
        self.assertIn(slider, part)
        self.assertIn(label, part)
        g = part.index(focus)
        b = part.index("{", g)
        e = part.index("}", b)
        block = part[b:e + 1]
        self.assertIn(slider, block)
        self.assertIn(label, block)

    def test_render_syncs_temperature_and_charging(self):
        # Regression (lane F): temperature and Charging live on the server, but render never synced them.
        text = web_text()
        start = text.index("async function render(s){")
        end = text.index("async function go", start)
        part = text[start:end]
        sync = "if(s.temp_c!=null&&document.activeElement!==G('temp'))G('temp').value=s.temp_c;"
        self.assertIn(sync, part)
        self.assertIn("G('charge').checked=!!s.charging;", part)
        self.assertLess(part.index("document.getElementById('battery').value=s.battery.percent"), part.index(sync))
