# SPDX-License-Identifier: GPL-3.0-or-later
"""Public QA layout regressions; source checks work without a browser socket."""
import re
import unittest

from opos import public_demo, web
from tests.test_uxp30_budget import rules


class PublicQALayout(unittest.TestCase):
    def setUp(self):
        self.local = web.page("t" * 43, "n", demo=True)
        self.public = public_demo.landing("t" * 43, "n", demo=True)
        self.styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", self.public, re.S))
        self.rules = list(rules(self.styles))

    def declaration(self, selector, prop):
        return [(context, values[prop]) for context, name, values in self.rules
                if name == selector and prop in values]

    def test_public_settings_does_not_reserve_hidden_device_column(self):
        # This later selector outranks the desktop two-column rule; block layout
        # also ignores the children's grid-column:2 placement. Local device
        # settings still keep their existing desktop grid.
        selector = 'html body.public-demo .public-device #phone[data-app=settings] #settings:not([hidden])'
        self.assertEqual(self.declaration(selector, 'display'), [((), 'block')])
        self.assertNotIn(selector, self.local)
        grid = 'html:not([data-ff=slab]):not([data-ff=foldable]) #phone[data-app=settings] #settings:not([hidden])'
        self.assertEqual(self.declaration(grid, 'display')[-1][1], 'grid')
        self.assertLess(self.styles.index(grid), self.styles.index(selector))
        self.assertIn('.public-device #ff,', self.styles)

    def test_history_stroke_uses_theme_ink_in_light_and_dark(self):
        self.assertEqual(self.declaration('#history polyline', 'stroke')[-1][1], 'var(--jaryo-fg)')
        polyline = re.search(r'<svg id="history".*?</svg>', self.public, re.S).group()
        self.assertNotIn('stroke="#fff"', polyline)
        self.assertIn('stroke="var(--jaryo-fg)"', polyline)
        for theme in ('light', 'dark'):
            selector = f'html[data-theme="{theme}"]'
            ink = self.declaration(selector, '--jaryo-fg')[-1][1]
            paper = self.declaration(selector, '--jaryo-bg')[-1][1]
            self.assertNotEqual(ink, paper)

    def test_landing_tour_starts_unlocked_and_undo_precedes_low_battery(self):
        self.assertIn('href="/?demo=1#try-phone"', self.public)
        self.assertIn('Starts unlocked at noon: 60% charge × 60% health is about 36% usable.', self.public)
        self.assertLess(self.public.index('Undo a soft pause first'),
                        self.public.index('Set Battery to 20%'))
        self.assertNotIn('Unlock the phone, then explore.', self.public)
        self.assertIn('<div id="demo" data-demo="1">', self.public)
        self.assertIn('not a phone operating system and has not run on a physical phone', self.public)

    def test_narrow_simulator_keeps_phone_and_controls_in_one_viewport(self):
        selector = '#try-phone:has(>#demo:not([hidden]))'
        narrow = ('@media(max-width:599px)',)
        self.assertEqual(self.declaration(selector, 'height'), [(narrow, '100svh')])
        self.assertEqual(self.declaration(selector, 'grid-template-rows'),
                         [(narrow, 'minmax(0,3fr) minmax(0,2fr)')])
        self.assertEqual(self.declaration(selector + ' .public-device', 'min-height'), [(narrow, '0')])
        panel = '.public-demo #demo:not([hidden])'
        self.assertEqual(self.declaration(panel, 'overflow'), [(narrow, 'auto')])
        self.assertEqual(self.declaration(panel, 'overscroll-behavior'), [(narrow, 'contain')])
        # Both regions remain scrollable; normal locked mode keeps its tall phone.
        self.assertEqual(self.declaration('.public-device', 'overflow')[0][1], 'auto')
        self.assertIn('.public-device{width:100%;height:820px;height:100svh;min-height:640px', self.styles)

    def test_layout_styles_remain_nonce_authorized(self):
        self.assertTrue(re.findall(r'<style nonce="n">', self.public))
        self.assertFalse(re.findall(r'<style(?! nonce="n")', self.public))


if __name__ == '__main__':
    unittest.main()
