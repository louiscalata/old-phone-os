# SPDX-License-Identifier: GPL-3.0-or-later
"""UXP15: native meter and full-view touch targets."""
import re
import unittest

from opos.web import page
from tests.test_uxr_acceptance import KNOWN_GAPS


class TestUXP15Gaps(unittest.TestCase):
    def test_known_gaps_empty(self):
        self.assertEqual(KNOWN_GAPS, {})

    def test_meter_is_native_button(self):
        html = page(demo=True)
        tags = re.findall(r'<[^>]+\bid="meter"[^>]*>', html)
        self.assertEqual(len(tags), 1)
        self.assertRegex(tags[0], r'^<button\b')
        self.assertIn('type="button"', tags[0])
        self.assertNotIn('role="button"', tags[0])
        for attr in ('aria-label="Battery, pull down for details"',
                     'aria-expanded="false"', 'aria-controls="sheet"'):
            self.assertIn(attr, tags[0])
        self.assertEqual(self.css(html, '#meter')['height'], '4px')
        self.assertEqual(self.css(html, '#meter')['padding'], '0')
        self.assertEqual(self.css(html, '#meter')['border'], '0')
        self.assertIn("meterEl.addEventListener('keydown'", html)
        self.assertIn("hitEl.addEventListener('click'", html)

    def test_charge_hit_area_css(self):
        html = page(demo=True)
        self.assertRegex(html, r'<label[^>]*><input id="charge" type="checkbox"> Charging</label>')
        label = self.css(html, '#demo .box>label:last-child')
        self.assertEqual(label['min-height'], '48px')
        self.assertEqual(label['padding'], '12px')
        charge = self.css(html, '#charge')
        self.assertEqual(charge['width'], '48px')
        self.assertEqual(charge['height'], '48px')
        for selector in ('#number', '#msg', '#controls button', '#helpertoggle',
                         '#battery', '#temp', '#simbtn', '#visbtn'):
            self.assertEqual(self.css(html, selector)['min-height'], '48px', selector)
        for selector in ('#controls', '#demo-controls'):
            self.assertEqual(self.css(html, selector)['gap'], '8px')

    @staticmethod
    def css(html, selector):
        styles = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
        styles = re.sub(r'/\*.*?\*/', '', styles, flags=re.S)
        declarations = {}
        for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', styles):
            if selector in [s.strip() for s in selectors.split(',')]:
                declarations.update(part.strip().split(':', 1) for part in body.split(';') if ':' in part)
        return {k.strip(): v.strip() for k, v in declarations.items()}

    def test_meter_hit_has_reserved_space(self):
        html = page()
        hit = self.css(html, '#meter-hit')
        self.assertEqual(hit['top'], '0')
        self.assertEqual(hit['height'], '48px')
        # Both old-browser fallback and safe-area rule reserve the hit band.
        self.assertRegex(html, r'body\s*\{\s*padding-top\s*:\s*48px\s*;?\s*\}')
        self.assertEqual(self.css(html, 'body')['padding-top'],
                         'calc(48px + env(safe-area-inset-top,0px))')

    def test_legacy_text_scales_with_root(self):
        html = page()
        for selector in ('label', 'input', 'button', '#goal-row input', '#goal-row button'):
            self.assertEqual(self.css(html, selector)['font-size'], '1rem', selector)



if __name__ == '__main__':
    unittest.main()
