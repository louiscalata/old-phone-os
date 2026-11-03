# SPDX-License-Identifier: GPL-3.0-or-later;
import re
import unittest
import opos.web

def _page():
    return opos.web.page()

def _root_vars(html):
    vars_dict = {}
    for m in re.finditer(r':root\s*\{([^}]*)\}', html):
        content = m.group(1)
        for part in content.split(';'):
            if not part.strip():
                continue
            if ':' not in part:
                continue
            name, val = part.split(':', 1)
            vars_dict[name.strip()] = val.strip()
    return vars_dict

def _decl(html, selector, prop):
    esc_sel = re.escape(selector)
    pattern = re.compile(esc_sel + r'\s*\{([^}]*)\}', re.DOTALL)
    m = pattern.search(html)
    if not m:
        return None
    block = m.group(1)
    prop_pat = re.compile(r'(?:^|;)\s*' + re.escape(prop) + r'\s*:\s*([^;}]+)')
    m2 = prop_pat.search(block)
    if not m2:
        return None
    return m2.group(1).strip()

def _resolve(value, vars_dict):
    m = re.match(r'var\s*\(\s*(--[\w-]+)\s*\)', value)
    if m:
        name = m.group(1)
        return vars_dict.get(name)
    return value

class TestUxrToneTokens(unittest.TestCase):
    def test_normal_matches_meter(self):
        html = _page()
        root_vars = _root_vars(html)
        meter_color = _decl(html, '#meter[data-tone="normal"] #meter-fill', 'background')
        if not meter_color:
            meter_color = _decl(html, '#meter[data-tone="normal"] #meter-fill', 'background-color')
        self.assertIsNotNone(meter_color)
        self.assertEqual(meter_color.strip().lower(), '#a3a8b0')
        sheet_raw = _decl(html, '#sheet-title[data-tone="normal"]', 'color')
        self.assertIsNotNone(sheet_raw)
        self.assertRegex(sheet_raw, r'^var\s*\(')
        self.assertFalse(sheet_raw.lstrip().startswith('#'))
        resolved = _resolve(sheet_raw, root_vars)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.strip().lower(), meter_color.strip().lower())
        for v in root_vars.values():
            self.assertNotIn('#8a8f98', v.lower())
        for tone in ['low', 'normal', 'full', 'charging']:
            sel = f'#sheet-title[data-tone="{tone}"]'
            raw = _decl(html, sel, 'color')
            self.assertIsNotNone(raw)
            self.assertNotIn('#8a8f98', raw)

    def test_every_tone_tokenised(self):
        html = _page()
        root_vars = _root_vars(html)
        tones = ['low', 'normal', 'full', 'charging']
        for tone in tones:
            with self.subTest(tone=tone):
                prop_name = f'--tone-{tone}'
                self.assertIn(prop_name, root_vars)
                sel = f'#sheet-title[data-tone="{tone}"]'
                sheet_raw = _decl(html, sel, 'color')
                self.assertIsNotNone(sheet_raw)
                self.assertRegex(sheet_raw, rf'^var\s*\(\s*{re.escape(prop_name)}\s*\)$')
                resolved = _resolve(sheet_raw, root_vars)
                meter_sel = f'#meter[data-tone="{tone}"] #meter-fill'
                meter_color = _decl(html, meter_sel, 'background')
                if not meter_color:
                    meter_color = _decl(html, meter_sel, 'background-color')
                self.assertIsNotNone(meter_color)
                self.assertEqual(resolved.strip().lower(), meter_color.strip().lower())

if __name__ == '__main__':
    unittest.main()
