# SPDX-License-Identifier: GPL-3.0-or-later
import re
import secrets
import unittest
from html import escape
from html.parser import HTMLParser

from opos.setup.wizard_page import page


class WizardPageTests(unittest.TestCase):
    def setUp(self):
        self.token = secrets.token_hex(16) + "<>&\"'"
        self.nonce = "testnonce123"
        self.html = page(self.token, self.nonce)

    def test_token_meta_tag_is_escaped_before_title(self):
        meta = f'<meta name="opos-token" content="{escape(self.token, quote=True)}">'
        self.assertIn(meta, self.html)
        self.assertNotIn(f'content="{self.token}"', self.html)
        self.assertLess(self.html.index(meta), self.html.index("<title>Old Phone OS Interface setup</title>"))

    def test_every_inline_script_and_style_has_nonce(self):
        tags = re.findall(r"<(?:script|style)\b[^>]*>", self.html)
        self.assertGreaterEqual(len(tags), 2)
        self.assertTrue(any(tag.startswith("<script") for tag in tags))
        self.assertTrue(any(tag.startswith("<style") for tag in tags))
        for tag in tags:
            self.assertIn(f'nonce="{self.nonce}"', tag)

    def test_no_inline_event_handler_attributes(self):
        self.assertIsNone(re.search(r"<[^>]+\son[a-z]+\s*=", self.html, re.IGNORECASE))

    def test_no_external_urls_except_app_link_built_in_js(self):
        urls = re.findall(r"https?://[^\s\"'<>`]+", self.html)
        self.assertTrue(urls)
        for url in urls:
            self.assertEqual(url, "http://127.0.0.1:")

    def test_seven_steps_and_headings_exist(self):
        for heading in ("Device", "Services", "Daily goal", "Battery health", "Security", "AI coding tools", "Review and apply"):
            self.assertIn(heading, self.html)
        self.assertIn(">Back<", self.html)
        self.assertIn(">Next<", self.html)
        self.assertIn(">Apply<", self.html)

    def test_required_element_ids_exist(self):
        for element_id in ("meter", "meter-fill", "profile", "goal-time", "ssh-key", "apply", "progress"):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_meter_is_accessible_progressbar(self):
        match = re.search(r'<[^>]+id="meter"[^>]*>', self.html)
        self.assertIsNotNone(match)
        tag = match.group(0)
        self.assertIn('role="progressbar"', tag)
        self.assertIn('aria-label="Setup progress"', tag)
        self.assertIn('aria-valuemin="0"', tag)
        self.assertIn('aria-valuemax="100"', tag)
        self.assertIn('aria-live="polite"', self.html)

    def test_api_paths_are_used(self):
        for path in ("/setup/api/state", "/setup/api/device", "/setup/api/apply", "/setup/api/progress"):
            self.assertIn(path, self.html)

    def test_specified_controls_and_defaults_exist(self):
        self.assertIn('id="goal-time"', self.html)
        self.assertIn('value="23:00"', self.html)
        for value in ("calls", "sms", "alarms", "apps", "auto", "worn", "normal"):
            self.assertIn(f'value="{value}"', self.html)

    def test_ai_elements_exist(self):
        for element_id in ("ai-list", "ai-note", "ai-off", "s7"):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_ai_mode_radios_exist(self):
        self.assertIn('value="lite"', self.html)
        self.assertIn('value="full"', self.html)
        self.assertIn('name="ai_mode"', self.html)
        self.assertRegex(self.html, r'value="lite"[^>]*checked')

    def test_collect_uses_ai_tools(self):
        self.assertIn("ai_tools:{tools:", self.html)

    def test_no_innerhtml_usage(self):
        self.assertNotIn("innerHTML", self.html)

    def test_ai_note_text_present(self):
        self.assertIn("Nothing is installed unless you tick one", self.html)

    def test_show_triggers_build_summary_at_six(self):
        self.assertIn("if(i===6)buildSummary()", self.html)
        self.assertNotIn("if(i===5)", self.html)

    def test_no_vendor_names_in_page(self):
        low = self.html.lower()
        self.assertNotIn("claude", low)
        self.assertNotIn("codex", low)

    def test_review_explains_per_user_install_and_optional_tools(self):
        expected = (
            "Setup saves choices and installs a background service for your user account on macOS or Linux. "
            "It does not replace your operating system. "
            "No model weights are bundled. "
            "Optional AI coding tools are installed only if you selected them above."
        )
        self.assertEqual(self.html.count(expected), 1)

        class ReviewParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.s7_children = []
                self.all_p = []
                self.active = []

            def handle_starttag(self, tag, attrs):
                attr = dict(attrs)
                is_direct = False
                child_idx = -1
                if self.stack:
                    parent_tag, parent_attr = self.stack[-1]
                    if parent_tag == "section" and parent_attr.get("id") == "s7":
                        is_direct = True
                        self.s7_children.append([tag, attr, None])
                        child_idx = len(self.s7_children) - 1
                if tag == "p":
                    inside = is_direct
                    if not inside:
                        for stk_tag, stk_attr in self.stack:
                            if stk_tag == "section" and stk_attr.get("id") == "s7":
                                inside = True
                                break
                    self.active.append([inside, is_direct, child_idx, ""])
                self.stack.append((tag, attr))

            def handle_endtag(self, tag):
                if tag == "p" and self.active:
                    inside, is_direct, child_idx, buf = self.active.pop()
                    self.all_p.append(buf)
                    if is_direct and child_idx >= 0:
                        self.s7_children[child_idx][2] = buf
                for idx in range(len(self.stack) - 1, -1, -1):
                    if self.stack[idx][0] == tag:
                        del self.stack[idx:]
                        break

            def handle_data(self, data):
                if self.active:
                    self.active[-1][3] += data

        parser = ReviewParser()
        parser.feed(self.html)
        self.assertEqual(parser.all_p.count(expected), 1)
        found = [c for c in parser.s7_children if c[0] == "p" and c[2] == expected]
        self.assertEqual(len(found), 1)
        idx = parser.s7_children.index(found[0])
        self.assertLess(idx + 1, len(parser.s7_children))
        nxt_tag, nxt_attr, _ = parser.s7_children[idx + 1]
        self.assertEqual(nxt_tag, "ul")
        self.assertEqual(nxt_attr.get("id"), "summary")

    def test_lean_explanation_preserves_wizard_steps(self):
        class StepsParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.sections = []
                self.jaryo_sections = []
                self.apply_attrs = []
                self.apply_texts = []
                self.current = None

            def handle_starttag(self, tag, attrs):
                attr = dict(attrs)
                if tag == "section":
                    tokens = attr.get("class", "").split()
                    if "step" in tokens:
                        target = self.jaryo_sections if "jaryo-step" in tokens else self.sections
                        target.append(attr.get("id"))
                if tag == "button" and attr.get("id") == "apply":
                    inside = False
                    for stk_tag, stk_attr in self.stack:
                        if stk_tag == "section" and stk_attr.get("id") == "s7":
                            inside = True
                            break
                    if inside:
                        self.current = ""
                        self.apply_attrs.append(attr)
                self.stack.append((tag, attr))

            def handle_endtag(self, tag):
                if tag == "button" and self.current is not None:
                    self.apply_texts.append(self.current)
                    self.current = None
                for idx in range(len(self.stack) - 1, -1, -1):
                    if self.stack[idx][0] == tag:
                        del self.stack[idx:]
                        break

            def handle_data(self, data):
                if self.current is not None:
                    self.current += data

        parser = StepsParser()
        parser.feed(self.html)
        self.assertEqual(parser.sections, ["s1", "s2", "s3", "s4", "s5", "s6", "s7"])
        # JRY32: the leading edition panel is a jaryo-step section (not a data-jstep) before the eight personal steps.
        self.assertEqual(parser.jaryo_sections, ["jaryo-edition", "j1", "j2", "j3", "j4", "j5", "j6", "j8", "j7"])
        self.assertIn('const steps=qa(".step:not(.jaryo-step)")', self.html)
        self.assertIn('if(window.jaryoSetup)window.jaryoSetup.start()', self.html)
        self.assertEqual(len(parser.apply_attrs), 1)
        self.assertEqual(parser.apply_attrs[0].get("type"), "button")
        self.assertEqual(parser.apply_texts, ["Apply"])


if __name__ == "__main__":
    unittest.main()
