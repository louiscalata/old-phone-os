# SPDX-License-Identifier: GPL-3.0-or-later
"""Converter: fixed factors, a plain page, and a main page that does not grow."""
import json
import unittest
from unittest import mock
import os

from opos import apps, convert, convert_page
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, page, served_home_page


class Factors(unittest.TestCase):
    def test_everyday_figures(self):
        self.assertEqual(convert.convert("length", "1", "km", "mi")["line"], "1 km = 0.621371 mile")
        self.assertEqual(convert.convert("length", "1", "ft", "in")["text"], "12")
        self.assertEqual(convert.convert("mass", "1", "kg", "lb")["text"], "2.20462")
        self.assertEqual(convert.convert("volume", "1", "gal", "l")["text"], "3.78541")
        self.assertEqual(convert.convert("temperature", "0", "c", "f")["text"], "32")
        self.assertEqual(convert.convert("temperature", "100", "c", "f")["text"], "212")
        self.assertEqual(convert.convert("temperature", "-40", "c", "f")["text"], "-40")
        self.assertEqual(convert.convert("temperature", "0", "c", "k")["text"], "273.15")
        self.assertEqual(convert.convert("length", "1", "km", "km")["text"], "1")

    def test_refuses_a_bad_amount_a_negative_length_and_absolute_zero(self):
        for value, src, dst, code in (
            ("", "km", "mi", "value"),
            ("1e2", "km", "mi", "value"),
            ("-1", "km", "mi", "sign"),
            ("-1", "k", "c", "cold"),
            ("-300", "c", "f", "cold"),
        ):
            kind = "temperature" if src in {"c", "f", "k"} else "length"
            with self.assertRaises(ValueError) as caught:
                convert.convert(kind, value, src, dst)
            self.assertEqual(str(caught.exception), code)
        with self.assertRaises(ValueError):
            convert.convert("area", "1", "km", "mi")
        with self.assertRaises(ValueError):
            convert.convert("length", "1", "km", "lb")


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

    def test_the_api_answers_and_the_main_page_does_not_grow(self):
        before = page()
        with running({"OPOS_APPS": "convert"}) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/convert")
            self.assertEqual(status, 200)
            self.assertIn(b"Amount", body)
            status, body = get(server, "/api/convert", token)
            kinds = json.loads(body)["kinds"]
            self.assertEqual([item["id"] for item in kinds], ["length", "mass", "volume", "temperature"])
            self.assertIn("US gal", [unit["title"] for unit in kinds[2]["units"]])
            code, raw = post(server, "/api/convert", {"kind": "temperature", "value": "0", "src": "c", "dst": "f"}, token)
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


if __name__ == "__main__":
    unittest.main()
