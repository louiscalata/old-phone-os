# SPDX-License-Identifier: GPL-3.0-or-later
"""Converter: fixed factors, a plain page, and a main page that does not grow."""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from opos import apps, convert, convert_page
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, page, served_home_page

ROOT = Path(__file__).resolve().parents[1]


class Factors(unittest.TestCase):
    def test_everyday_figures(self):
        self.assertEqual(convert.convert("length", "1", "km", "mi")["line"], "1 km = 0.621371 mile")
        self.assertEqual(convert.convert("length", "1", "ft", "in")["text"], "12")
        self.assertEqual(convert.convert("length", "1", "km", "km")["text"], "1")
        self.assertEqual(convert.convert("mass", "1", "kg", "lb")["text"], "2.20462")
        self.assertEqual(convert.convert("mass", "16", "oz", "lb")["text"], "1")
        self.assertEqual(convert.convert("volume", "1", "gal", "l")["text"], "3.78541")
        self.assertEqual(convert.convert("volume", "2", "cup", "pt")["text"], "1")
        self.assertEqual(convert.convert("temperature", "0", "c", "f")["text"], "32")
        self.assertEqual(convert.convert("temperature", "100", "c", "f")["text"], "212")
        self.assertEqual(convert.convert("temperature", "-40", "c", "f")["text"], "-40")
        self.assertEqual(convert.convert("temperature", "0", "c", "k")["text"], "273.15")
        self.assertEqual(convert.convert("temperature", "98.6", "f", "c")["text"], "37")
        self.assertEqual(convert.convert("length", 3, "m", "cm")["text"], "300")

    def test_the_line_repeats_the_amount_as_typed(self):
        self.assertEqual(convert.convert("length", "1234567", "m", "km")["line"], "1234567 m = 1234.57 km")
        self.assertEqual(convert.convert("length", "2.50", "m", "cm")["line"], "2.5 m = 250 cm")

    def test_refuses_a_bad_amount_a_negative_length_and_absolute_zero(self):
        for kind, value, src, dst, code in (
            ("length", "", "km", "mi", "value"),
            ("length", " ", "km", "mi", "value"),
            ("length", "1e2", "km", "mi", "value"),
            ("length", "1.", "km", "mi", "value"),
            ("length", "nan", "km", "mi", "value"),
            ("length", "١", "km", "mi", "value"),
            ("length", "1" * 41, "km", "mi", "value"),
            ("length", "0." + "0" * 38 + "1", "km", "mi", "value"),
            ("length", "1000000000001", "km", "mi", "value"),
            ("length", 1.5, "km", "mi", "value"),
            ("length", True, "km", "mi", "value"),
            ("length", None, "km", "mi", "value"),
            ("length", "-1", "km", "mi", "sign"),
            ("temperature", "-1", "k", "c", "cold"),
            ("temperature", "-300", "c", "f", "cold"),
            ("area", "1", "km", "mi", "kind"),
            (["length"], "1", "km", "mi", "kind"),
            ("length", "1", "km", "lb", "unit"),
            ("length", "1", ["km"], "mi", "unit"),
            ("length", "1", "km", {"mi": 1}, "unit"),
        ):
            with self.subTest(kind=kind, value=value, src=src, dst=dst):
                with self.assertRaises(ValueError) as caught:
                    convert.convert(kind, value, src, dst)
                self.assertEqual(str(caught.exception), code)

    def test_every_unit_round_trips(self):
        for spec in convert.KINDS:
            for unit in spec["units"]:
                there = convert.convert(spec["id"], "1", unit[0], spec["units"][0][0])["text"]
                back = convert.convert(spec["id"], there, spec["units"][0][0], unit[0])["text"]
                self.assertAlmostEqual(float(back), 1.0, places=3, msg=unit[0])


class ConverterPage(unittest.TestCase):
    def test_the_page_is_plain_and_uses_only_the_token_colours(self):
        html = convert_page.render("tok", "nonce")
        self.assertIn(">Converter<", html)
        self.assertIn("Newsreader", html)
        self.assertIn("Inter Tight", html)
        self.assertIn("below absolute zero", html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        self.assertEqual(
            "Works offline: yes. Sends data: no. Uses: none. Real or practice: real.",
            apps.by_id("convert").card(),
        )

    def test_the_script_keeps_the_newest_answer_and_says_why_it_refused(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        result = subprocess.run(
            [node, str(Path(__file__).with_name("convert_page_test.js"))],
            input=json.dumps(convert_page._SCRIPT),
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("converter script checks passed", result.stdout)

    def test_post_refuses_what_is_not_a_conversion(self):
        self.assertEqual(convert_page.post([]), (400, {"error": "bad request"}))
        self.assertEqual(
            convert_page.post({"kind": "length", "value": "1", "src": [], "dst": "mi"}), (400, {"error": "unit"})
        )
        status, body = convert_page.post({"kind": "length", "value": "1", "src": "m", "dst": "cm"})
        self.assertEqual((status, body["text"]), (200, "100"))

    def test_the_api_answers_and_the_main_page_does_not_grow(self):
        before = page()
        with running({"OPOS_APPS": "convert"}) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/convert")
            self.assertEqual(status, 200)
            self.assertIn(b"Amount", body)
            self.assertEqual(get(server, "/api/convert")[0], 403, "the read needs the token")
            status, body = get(server, "/api/convert", token)
            kinds = json.loads(body)["kinds"]
            self.assertEqual([item["id"] for item in kinds], ["length", "mass", "volume", "temperature"])
            self.assertIn("US gal", [unit["title"] for unit in kinds[2]["units"]])
            code, raw = post(
                server, "/api/convert", {"kind": "temperature", "value": "0", "src": "c", "dst": "f"}, token
            )
            self.assertEqual(code, 200, raw)
            self.assertEqual(json.loads(raw)["line"], "0 C = 32 F")
            code, raw = post(server, "/api/convert", {"kind": "length", "value": "-2", "src": "m", "dst": "ft"}, token)
            self.assertEqual(code, 400)
            self.assertEqual(json.loads(raw)["error"], "sign")
        with mock.patch.dict(os.environ, {"OPOS_APPS": "convert"}):
            self.assertEqual(page(), before)
            self.assertLessEqual(len(page().encode("utf-8")), WAVE_GATE)
        served = served_home_page({"OPOS_APPS": "convert"})
        self.assertLessEqual(len(served), WAVE_GATE)
        self.assertNotIn(b">Converter<", served)


class Packaging(unittest.TestCase):
    def test_the_duo_drop_in_and_the_runtime_list_carry_the_converter(self):
        conf = (ROOT / "packaging/duo-os/opos-apps.conf").read_text(encoding="utf-8")
        self.assertIn("Environment=OPOS_APPS=clock,contacts,notes,calc,calls,convert,notices", conf.splitlines())
        listed = (ROOT / "packaging/pmos/runtime-files.txt").read_text(encoding="utf-8").splitlines()
        for path in ("src/opos/convert.py", "src/opos/convert_page.py"):
            self.assertIn(path, listed)
        self.assertEqual(apps.MODULES["convert"], "opos.convert_page")


if __name__ == "__main__":
    unittest.main()
