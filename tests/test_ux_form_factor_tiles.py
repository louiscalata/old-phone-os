# SPDX-License-Identifier: GPL-3.0-or-later
"""F3017: device-type tiles in setup step 1."""
import copy
import json
import re
import unittest
from html.parser import HTMLParser

from opos.setup.config import FORM_FACTORS, config_path, load, validate
from opos.setup.wizard_page import page

try:
    from tests.test_setup_apply import BaseCase, make_cfg
except ImportError:
    from test_setup_apply import BaseCase, make_cfg

EXPECTED = [
    ("slab", "Regular phone"),
    ("foldable-book", "Foldable"),
    ("dual-screen", "Two screens (Surface Duo)"),
    ("slide-keyboard", "Slide-out keyboard"),
]


class _Step1Parser(HTMLParser):
    """Collect step 1 controls in document order."""

    def __init__(self):
        super().__init__()
        self.in_s1 = False
        self.order = []
        self.in_fieldset = False
        self.in_legend = False
        self.legend = ""
        self.radios = []
        self.labels = {}
        self.ids = []
        self._label_for = None
        self._label_text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "section" and a.get("id") == "s1":
            self.in_s1 = True
        if not self.in_s1:
            return
        if tag == "select":
            self.order.append(("select", a.get("id")))
        elif tag == "fieldset":
            self.order.append(("fieldset", a.get("id")))
            self.in_fieldset = True
        elif tag == "legend" and self.in_fieldset:
            self.in_legend = True
        elif tag == "input" and a.get("name") == "form_factor":
            self.radios.append(a)
        elif tag == "label" and self.in_fieldset:
            self._label_for = a.get("for")
            self._label_text = ""

    def handle_endtag(self, tag):
        if tag == "section":
            self.in_s1 = False
        elif tag == "fieldset":
            self.in_fieldset = False
        elif tag == "legend":
            self.in_legend = False
        elif tag == "label" and self._label_for is not None:
            self.labels[self._label_for] = self._label_text.strip()
            self._label_for = None

    def handle_data(self, data):
        if self.in_legend:
            self.legend += data
        if self._label_for is not None:
            self._label_text += data


def _parse():
    p = _Step1Parser()
    p.feed(page("tok", "nonce1"))
    return p


def _script(html):
    m = re.search(r"<script\b[^>]*>(.*?)</script>", html, re.S)
    return m.group(1) if m else ""


class FormFactorTilesTest(unittest.TestCase):
    def test_tiles_present(self):
        p = _parse()
        self.assertEqual(p.order[:2], [("select", "profile"), ("fieldset", "ff-tiles")])
        self.assertEqual(p.legend.strip(), "What kind of phone is this?")
        self.assertEqual([(r["value"], p.labels.get(r.get("id"))) for r in p.radios], EXPECTED)
        self.assertTrue(all(r.get("type") == "radio" for r in p.radios))
        radio_ids = [r["id"] for r in p.radios]
        self.assertEqual(len(set(radio_ids)), 4)
        self.assertEqual(len(p.ids), len(set(p.ids)), "page IDs must be unique")
        self.assertEqual(set(p.labels), set(radio_ids))
        self.assertEqual(tuple(v for v, _ in EXPECTED), FORM_FACTORS)

    def test_slab_default_checked(self):
        checked = [r["value"] for r in _parse().radios if "checked" in r]
        self.assertEqual(checked, ["slab"])

    def test_tile_style_scoped_with_checked_state(self):
        html = page("tok", "nonce1")
        self.assertIn(".ff-tiles label{", html)
        self.assertIn(".ff-tiles input:checked+label{", html)
        self.assertIn("input[type=checkbox],input[type=radio]{width:24px;height:24px", html)
        self.assertIn(":focus-visible{outline:", html)
        height = re.search(r"\.ff-tiles label\{[^}]*min-height:(\d+)px", html)
        self.assertIsNotNone(height)
        self.assertGreaterEqual(int(height.group(1)), 44, "tiles keep at least a 44px touch target")

    # The following are source assertions on the rendered script, not executed browser coverage.
    def test_script_allowlist_matches_form_factors(self):
        js = _script(page("tok", "nonce1"))
        m = re.search(r"const FF=\[([^\]]*)\];", js)
        self.assertIsNotNone(m)
        self.assertEqual(tuple(json.loads("[" + m.group(1) + "]")), FORM_FACTORS)

    def test_script_collects_checked_radio_with_slab_default(self):
        js = _script(page("tok", "nonce1"))
        self.assertIn('function getFF(){let r=q(\'input[name="form_factor"]:checked\');return r&&FF.includes(r.value)?r.value:"slab";}', js)
        self.assertIn("battery_health:d.battery_health,form_factor:getFF()}", js)
        self.assertIn("JSON.stringify({config:cfg})", js)

    def test_script_restores_saved_choice_without_selector_interpolation(self):
        js = _script(page("tok", "nonce1"))
        self.assertIn('let w=FF.includes(v)?v:"slab";qa(\'input[name="form_factor"]\').forEach(e=>{e.checked=e.value===w;});', js)
        self.assertIn('setFF(S.config&&S.config.device&&!S.first_run?S.config.device.form_factor:"slab");', js)
        self.assertNotIn('[value="\'+', js.split("function setFF", 1)[1].split("}", 1)[0])
        change = js.split("async function changeProfile", 1)[1].split("\n", 1)[0]
        self.assertNotIn("FF(", change)

    def test_invalid_form_factor_rejected(self):
        cfg = make_cfg()
        cfg["device"]["form_factor"] = "phablet"
        self.assertIn(
            "device.form_factor must be one of slab, foldable-book, dual-screen, slide-keyboard",
            validate(cfg),
        )


class FormFactorSavedTest(BaseCase):
    def test_selected_form_factor_saved(self):
        cfg = make_cfg()
        cfg["device"]["form_factor"] = "dual-screen"
        snapshot = copy.deepcopy(cfg)
        progress = self.run_apply(cfg)
        self.assertTrue(progress.ok(), progress.snapshot())
        saved = load(config_path(self.env, "linux"))
        self.assertEqual(saved["device"]["form_factor"], "dual-screen")
        self.assertEqual(saved["device"]["id"], snapshot["device"]["id"])
        self.assertEqual(validate(saved), [])


if __name__ == "__main__":
    unittest.main()
