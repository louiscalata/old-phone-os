# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import re
import unittest


class TestHelperCallWording(unittest.TestCase):
    def test_unreachable_wording(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        text = (root / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        self.assertIn("return{error:'The helper did not reply. Wait a few seconds, then try again.'}", text)
        self.assertNotIn("The helper could not be reached.", text)
        self.assertNotIn("OPOS is not responding", text)

    def test_no_product_name_in_the_helper_errors(self):
        # W1-08: the owner never reads "OPOS". Every error this call can show says what happened in plain words.
        root = pathlib.Path(__file__).resolve().parents[1]
        text = (root / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        body = text[text.index("async function helperCall"):text.index("function helperReset")]
        errors = re.findall(r"error:'([^']*)'", body)
        self.assertEqual(len(errors), 4, errors)
        for e in errors:
            with self.subTest(error=e):
                self.assertNotRegex(e, r"(?i)\bOPOS\b|\bmodel\b|\bAI\b")
