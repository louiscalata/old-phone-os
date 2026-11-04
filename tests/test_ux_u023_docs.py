# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import re
import unittest


class TestDuoHelperDocs(unittest.TestCase):
    def test_duo_doc_helper_section(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        text = (root / "docs" / "duo-usb-install.md").read_text(encoding="utf-8")
        new_heading = "## Turn on the helper"
        health_heading = "## No curl on the phone"
        push_heading = "## Push a build with push-to-duo.sh"
        self.assertEqual(text.count(new_heading), 1)
        health_pos = text.index(health_heading)
        new_pos = text.index(new_heading)
        push_pos = text.index(push_heading)
        self.assertLess(health_pos, new_pos)
        self.assertLess(new_pos, push_pos)
        body = text[new_pos + len(new_heading):push_pos].strip()
        expected = '“The helper starts hidden each time OPOS starts. Open the OPOS page with /?demo=1 in the phone’s browser to show the demo controls. Tap Show helper to show Why?, Plan and Ask; tap Hide helper to hide them. While the helper is hidden, these actions do not contact a model. Cancel stops waiting.”\n\nFor the measured desktop response times, see the [README capture table](../README.md#muse-glimmer-answering). These are not on-phone measurements.'
        self.assertEqual(body, expected)
        dot = chr(46)
        back = chr(92)
        ten = chr(49) + chr(48)
        net_a = chr(49) + chr(57) + chr(50)
        net_b = chr(49) + chr(54) + chr(56)
        net_c = chr(49) + chr(55) + chr(50)
        esc_dot = back + dot
        digit = back + "d"
        word = back + "b"
        pattern = word + "(" + ten + "|" + net_a + esc_dot + net_b + "|" + net_c + esc_dot + "(1[6-9]|2" + digit + "|3[01]))" + esc_dot + digit
        self.assertIsNone(re.search(pattern, text))
