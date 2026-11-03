# SPDX-License-Identifier: GPL-3.0-or-later
import html.parser
import unittest
from opos.setup.wizard_page import page
class _Ids(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.order = []
        self.infos = {}
        self.counts = {}
        self.stack = []
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            i = d["id"]
            self.order.append(i)
            self.counts[i] = self.counts.get(i, 0) + 1
            self.infos.setdefault(i, []).append({"attrs": d, "text": ""})
            self.stack.append(i)
        else:
            self.stack.append(None)
    def handle_data(self, data):
        if self.stack and self.stack[-1] is not None:
            self.infos[self.stack[-1]][-1]["text"] += data
    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()
class TestSetupRecovery(unittest.TestCase):
    def setUp(self):
        self.html = page("a" * 32, "n1")
        a = self.html.find("async function load(")
        b = self.html.find("async function changeProfile(")
        self.load = self.html[a:b]
        c = self.html.find("async function poll(")
        d = self.html.find("async function doApply(")
        self.poll = self.html[c:d]
        e = self.html.find("async function doApply(")
        f = self.html.find("\n", e)
        self.apply = self.html[e:f]
        p = _Ids()
        p.feed(self.html)
        self.ids = p
    def test_config_error_warns_owner(self):
        s = "Your saved settings could not be read, so the recommended settings are shown. Check each step, then tap Apply."
        self.assertIn("S.config_error", self.load)
        self.assertIn(s, self.load)
        a = self.load.find("S=await r.json()")
        b = self.load.find("S.config_error")
        c = self.load.find("S.dry_run")
        self.assertNotEqual(a, -1)
        self.assertNotEqual(b, -1)
        self.assertNotEqual(c, -1)
        self.assertLess(a, b)
        self.assertLess(b, c)
    def test_failed_help_element_hidden_with_guidance(self):
        s = "Your choices are kept. Tap Try again. If it still fails, restart the device and run opos again."
        self.assertEqual(self.ids.counts.get("failed-help", 0), 1)
        rec = self.ids.infos["failed-help"][0]
        self.assertIn("hidden", rec["attrs"])
        self.assertEqual(rec["attrs"].get("class"), "muted")
        self.assertEqual(rec["text"], s)
        self.assertLess(self.ids.order.index("failed"), self.ids.order.index("failed-help"))
    def test_failed_help_shown_on_failure_and_cleared_on_retry(self):
        self.assertIn('q("#retry").hidden=false', self.poll)
        a = self.poll.find('q("#retry").hidden=false')
        b = self.poll.find('"#failed-help"', a)
        self.assertNotEqual(b, -1)
        self.assertIn('q("#failed-help").hidden=false', self.poll)
        self.assertIn("#failed-help", self.apply)
        self.assertIn("hidden=true", self.apply)
        self.assertIn('q("#failed-help").hidden=true', self.apply)
        self.assertNotIn('q("#failed-help").hidden=false', self.load)
        self.assertNotIn('q("#failed-help").hidden=false', self.apply)
        self.assertEqual(self.html.count('q("#failed-help").hidden=false'), 1)
    def test_no_unsafe_markup(self):
        a = "Your saved settings could not be read, so the recommended settings are shown. Check each step, then tap Apply."
        b = "Your choices are kept. Tap Try again. If it still fails, restart the device and run opos again."
        self.assertIn(a, self.html)
        self.assertIn(b, self.html)
        self.assertNotIn("innerHTML", self.html)
