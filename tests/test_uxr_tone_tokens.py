# SPDX-License-Identifier: GPL-3.0-or-later;
"""The battery tone is a word, not a colour.

The page used to give the four battery tones (low, normal, full, charging) four colours: a --tone-* variable each, a
coloured #sheet-title and a coloured #meter-fill. The Jaryo palette (docs/jaryo.md: --jaryo-fg and --jaryo-bg, nothing
else) overrides all of them in every state, so they were dead rules, and B-03 of the interface plan retired them. What these
tests pin now is the truth that remains: no tone colour reaches the screen, and each tone still reads as the two Jaryo tokens.
"""
import importlib.util
import re
import unittest
from pathlib import Path

import opos.web

TONES = ['low', 'normal', 'full', 'charging']
TOKEN = 'var(--jaryo-fg)'
RETIRED = ('--tone-', '#e5484d', '#a3a8b0', '#30a46c', '#8a8f98')  # the old variables and their colours


def _page():
    return opos.web.page()


def _root_vars(html):
    vars_dict = {}
    for m in re.finditer(r':root\s*\{([^}]*)\}', html):
        for part in m.group(1).split(';'):
            if ':' in part:
                name, val = part.split(':', 1)
                vars_dict[name.strip()] = val.strip()
    return vars_dict


def _rules(html):
    """(selector, declarations) for every rule of the page's style sheets, in source order; a group is split."""
    css = re.sub(r'/\*.*?\*/', '', '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', html, re.S)), flags=re.S)
    out = []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        decls = {}
        for part in m.group(2).split(';'):
            if ':' in part:
                name, val = part.split(':', 1)
                decls[name.strip()] = val.strip()
        out += [(s.strip(), decls) for s in m.group(1).split(',')]
    return out


def _effective(rules, base_before, base_after, tone, prop):
    """The value of prop for `base_before[data-tone=tone]base_after`, by specificity (a data-tone selector beats the
    bare one) then source order. A tone-specific rule and a rule for any tone weigh the same."""
    best, found = (-1, -1), None
    for order, (selector, decls) in enumerate(rules):
        for rank, candidates in ((0, (base_before + base_after,)),
                                 (1, (base_before + '[data-tone]' + base_after,
                                      base_before + '[data-tone="' + tone + '"]' + base_after))):
            if selector in candidates and prop in decls and (rank, order) > best:
                best, found = (rank, order), decls[prop]
    return found


class TestUxrToneTokens(unittest.TestCase):
    def test_no_tone_variable_or_tone_colour_is_left(self):
        html = _page()
        for needle in RETIRED:
            self.assertNotIn(needle, html.lower(), needle)
        root_vars = _root_vars(html)
        self.assertEqual([k for k in root_vars if k.startswith('--tone')], [])
        self.assertIn('--jaryo-fg', root_vars)
        self.assertIn('--jaryo-bg', root_vars)

    def test_no_rule_colours_a_tone(self):
        # Every rule that names a tone value is gone; the only data-tone rules left are the valueless Jaryo overrides.
        for selector, decls in _rules(_page()):
            if re.search(r'#(?:sheet-title|meter)\[data-tone=', selector):
                self.fail((selector, decls))
            if '[data-tone]' in selector and ('#sheet-title' in selector or '#meter' in selector):
                for prop in ('color', 'background', 'background-color', 'box-shadow', 'animation'):
                    if prop in decls:
                        self.assertIn(decls[prop], (TOKEN, 'var(--jaryo-bg)', 'none'), (selector, prop))

    def test_guard_does_not_flag_the_jaryo_css_token(self):
        guard = Path(__file__).resolve().parents[1] / "guard" / "check.py"
        spec = importlib.util.spec_from_file_location("opos_guard_check", guard)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        text = Path(__file__).read_text(encoding="utf-8")
        self.assertIn("TOKEN = 'var(--jaryo-fg)'", text)
        self.assertFalse(any(p.search(text) for p in mod.PRIVATE))
        sample = "TO" + "KEN = '" + "abcd1234'"
        self.assertTrue(any(p.search(sample) for p in mod.PRIVATE))

    def test_guard_flags_a_var_prefix_that_is_not_a_whole_css_value(self):
        guard = Path(__file__).resolve().parents[1] / "guard" / "check.py"
        spec = importlib.util.spec_from_file_location("opos_guard_check_prefix", guard)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        broken = "pass" + "word = \"" + "var(--not-css-secret\""
        trailed = "TO" + "KEN = '" + "var(--jaryo-fg)" + "actualsecret'"
        whole = "TO" + "KEN = '" + "var(--jaryo-fg)'"
        quoted = "TO" + "KEN = \"" + "var(--jaryo-fg)\""
        self.assertTrue(any(p.search(broken) for p in mod.PRIVATE))
        self.assertTrue(any(p.search(trailed) for p in mod.PRIVATE))
        self.assertFalse(any(p.search(whole) for p in mod.PRIVATE))
        self.assertFalse(any(p.search(quoted) for p in mod.PRIVATE))

    def test_every_tone_reads_as_the_jaryo_token(self):
        rules = _rules(_page())
        for tone in TONES:
            with self.subTest(tone=tone):
                self.assertEqual(_effective(rules, '#sheet-title', '', tone, 'color'), TOKEN)
                self.assertEqual(_effective(rules, '#meter', ' #meter-fill', tone, 'background'), TOKEN)
                self.assertEqual(_effective(rules, '#meter', ' #meter-fill', tone, 'box-shadow'), 'none')
                self.assertEqual(_effective(rules, '#meter', '', tone, 'animation'), 'none')


if __name__ == '__main__':
    unittest.main()
