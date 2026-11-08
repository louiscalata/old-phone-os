# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared notices stay readable beside the changing helper-status paragraph."""
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import unittest
from unittest import mock

from opos import notices, web
from tests import test_uxr_acceptance as acceptance


class _Elements(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.ids = {}
        self.stack = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        item = {"tag": tag, "attrs": attrs, "text": [],
                "parent": self.stack[-1] if self.stack else None}
        if "id" in attrs:
            self.ids[attrs["id"]] = item
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(item)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for item in self.stack:
            item["text"].append(data)


class TestAboutNotices(unittest.TestCase):
    def test_complete_shared_notices_with_visible_disclaimer(self):
        parsed = _Elements(web.page())
        wrapper = parsed.ids["about-notices"]
        disclaimer = parsed.ids["about-disclaimer"]
        detail = parsed.ids["about-notices-details"]
        body = parsed.ids["about-notices-text"]
        summary = parsed.ids["about-notices-summary"]
        self.assertEqual(disclaimer["tag"], "p")
        self.assertEqual("".join(disclaimer["text"]), notices.DISCLAIMER)
        self.assertIs(disclaimer["parent"], wrapper)
        self.assertNotIn("hidden", wrapper["attrs"])
        self.assertNotIn("hidden", disclaimer["attrs"])
        self.assertEqual(detail["tag"], "details")
        self.assertIs(detail["parent"], wrapper)
        self.assertEqual(summary["tag"], "summary")
        self.assertEqual("".join(summary["text"]), "Licence, privacy and tested limits")
        self.assertIs(summary["parent"], detail)
        self.assertEqual(body["tag"], "pre")
        self.assertIs(body["parent"], detail)
        self.assertEqual("".join(body["text"]), notices.as_text())

    def test_shared_text_is_escaped_and_not_active_markup(self):
        attack = '<img id="notice-attack" src=x onerror="alert(1)">& <script>bad()</script>'
        with mock.patch.object(notices, "DISCLAIMER", attack), \
                mock.patch.object(notices, "as_text", return_value=attack) as text:
            page = web.page()
        text.assert_called_once_with()
        parsed = _Elements(page)
        self.assertNotIn("notice-attack", parsed.ids)
        self.assertEqual("".join(parsed.ids["about-disclaimer"]["text"]), attack)
        self.assertEqual("".join(parsed.ids["about-notices-text"]["text"]), attack)
        self.assertEqual(page.count(html.escape(attack)), 2)

    def test_helper_status_and_static_asset_stay_independent(self):
        # The About line has no script of its own any more (the status bar says "Helper on" and the About line no longer copies it,
        # interface plan W1-05); the notices block is still moved beside it by one line, and is static markup first.
        page = web.page()
        self.assertIn(web._asset("phone-b.html"), page)
        parsed = _Elements(page)
        self.assertEqual(parsed.ids["about"]["tag"], "p")
        self.assertIsNot(parsed.ids["about"]["parent"], parsed.ids["about-notices"])
        self.assertNotIn("Licence", "".join(parsed.ids["about"]["text"]))
        self.assertIn("a.insertAdjacentElement('afterend',n)", page)
        self.assertNotIn("Helper set to ", page)
        self.assertNotIn("'Old Phone OS Interface. '", page)

    def test_native_readability_and_nonce_contract(self):
        page = web.page(nonce="test-about-nonce")
        parsed = _Elements(page)
        self.assertIn("white-space:pre-wrap", parsed.ids["about-notices-text"]["attrs"]["style"])
        self.assertIn("overflow-wrap:anywhere", parsed.ids["about-notices-text"]["attrs"]["style"])
        self.assertIn("min-height:48px", parsed.ids["about-notices-summary"]["attrs"]["style"])
        for attrs in re.findall(r"<script([^>]*)>", page):
            self.assertIn('nonce="test-about-nonce"', attrs)
        for name in ("about-notices", "about-disclaimer", "about-notices-details",
                     "about-notices-summary", "about-notices-text"):
            self.assertEqual(page.count('id="' + name + '"'), 1)


CHECK = r"""
CHECKS.about_notices = async function() {
  await load('home',390,844,false);
  await ev("oposUI.openApp('settings')");
  return await ev(`(async function(){
    var out=[],a=document.getElementById('about'),w=document.getElementById('about-notices'),
      d=document.getElementById('about-disclaimer'),p=document.getElementById('about-notices-text'),
      details=document.getElementById('about-notices-details'),summary=document.getElementById('about-notices-summary');
    if(!a||!w||!d||!p||!details||!summary)return ['missing About notices'];
    if(!document.getElementById('settings').contains(w)||w.previousElementSibling!==a)out.push('not beside helper status in Settings');
    if(!__u.vis(d)||details.open)out.push('disclaimer hidden or details initially open');
    var original=w.textContent,aboutBefore=a.textContent;
    (window.oposHooks||[]).forEach(function(f){f({helper:{engine:'review route'}})});
    if(a.textContent!==aboutBefore||a.textContent.includes('review route'))out.push('a state render changed the About line');
    if(w.textContent!==original)out.push('a state render overwrote notices');
    summary.click();
    if(!details.open||!__u.vis(p))out.push('native details did not open');
    if(!p.textContent.includes('GNU GPL-3.0-or-later')||!p.textContent.includes('Privacy')||!p.textContent.includes('untested for this corrected source export'))out.push('shared fields absent');
    [d,summary,p].forEach(function(e){
      var c=getComputedStyle(e),fg=__u.rgba(c.color),layers=[];
      for(var x=e;x;x=x.parentElement){var b=__u.rgba(getComputedStyle(x).backgroundColor);if(b&&b[3]>0)layers.push(b)}
      var bg=[255,255,255,1];while(layers.length)bg=__u.over(layers.pop(),bg);
      var a=__u.lum(fg)+.05,b=__u.lum(bg)+.05;
      if(Math.max(a,b)/Math.min(a,b)<4.5)out.push(e.id+' contrast below4.5');
    });
    summary.focus();if(document.activeElement!==summary)out.push('summary not keyboard focusable');
    document.documentElement.style.fontSize='200%';
    await new Promise(function(r){requestAnimationFrame(r)});
    if(getComputedStyle(p).whiteSpace!=='pre-wrap')out.push('pre does not wrap');
    if(p.scrollWidth>p.clientWidth+1||d.scrollWidth>d.clientWidth+1)out.push('About text clips horizontally at 200%');
    if(summary.getBoundingClientRect().height<48)out.push('summary target below48px');
    return out;
  })()`);
};
"""


class TestAboutNoticesBrowser(unittest.TestCase):
    skip_reason = None
    mode = "port"
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown

    def test_notices_survive_helper_hook_and_read_at_200_percent(self):
        driver = Path(self.run_dir) / "about-notices.js"
        source = acceptance.DRIVER.read_text()
        self.assertIn("(async () => {", source)
        driver.write_text(source.replace("(async () => {", CHECK + "\n(async () => {", 1))
        target = str(self.port) if self.proc is not None else "pipe"
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile,
                   TMPDIR=self.run_dir)
        result = subprocess.run([self.node, str(driver), target, self.url,
                                 "about_notices", self.state],
                                capture_output=True, text=True, timeout=40, env=env)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
