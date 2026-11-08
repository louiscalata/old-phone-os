# SPDX-License-Identifier: GPL-3.0-or-later
"""Wave 1, layout track: scroll and long words (W1-10), the hinge (W1-11), compose with the keyboard up (W1-12).

Static checks on the page's own CSS and scripts. What the real layout does (a 100 character link in a thread, nothing on the hinge,
the field and Send above the keyboard) can only be measured in a browser: tests/test_span.py does it for the hinge, and the
measurements for this track are in docs/jaryo-panes.md section 9.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from opos.span import Span
from opos.web import Phone, page
from tests import test_uxp21_profiles as profiles
from tests.test_uxr_acceptance import find_chrome

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"
NODE = shutil.which("node")
DUO = Span(675, 42)
LINK = "https://example.com/" + "a" * 80  # 100 characters without a space


def inline_css():
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", page("t" * 32, "n"), re.S))


def declarations(css, selector):
    """The declarations of every rule whose selector list names `selector` exactly, in source order, as one dict."""
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        if selector in [s.strip() for s in sel.split(",")]:
            for d in body.split(";"):
                if ":" in d:
                    k, v = d.split(":", 1)
                    out[k.strip()] = v.strip()
    return out


class LongWords(unittest.TestCase):
    """W1-10: a 100 character link in a message widened its thread button to 838 px and gave the page a sideways scroll."""

    def test_a_thread_button_may_break_inside_a_word(self):
        # `overflow-wrap:anywhere` (not `break-word`, which the shell already sets on #phone) lowers the button's min-content width, so a
        # button, which shrinks to fit, ends at the width of its box instead of the width of the longest word.
        self.assertEqual(declarations(inline_css(), "#threads button").get("overflow-wrap"), "anywhere")

    def test_the_shell_keeps_its_own_break_word(self):
        self.assertEqual(declarations(inline_css(), "#phone").get("overflow-wrap"), "break-word")


class ScrollTop(unittest.TestCase):
    def test_the_app_switch_resets_the_scroller_that_exists(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        # window.scrollTo stays for a page that does scroll the document; body.scrollTop is the one that scrolls (html and body are
        # height:100% with overflow-x:hidden). Both in the same guard, so a window without either cannot stop the app from opening.
        self.assertIn("try{window.scrollTo(0,0);document.body.scrollTop=0}catch(e){}", js)
        self.assertEqual(js.count("document.body.scrollTop=0"), 1)
        self.assertLess(js.index("p.dataset.app=name;"), js.index("document.body.scrollTop=0"))


# The browser run: the UXP21 driver up to its cases (start-up, DevTools plumbing, the canned /api state), then this file's cases.
MAIN = r"""
const LINK = %s;
const plainUrl = process.argv[5];  // argv: chrome, span page, state file, plain page
async function run(url, w, h, q, after) {
  await send('Emulation.setDeviceMetricsOverride', {width: w, height: h, deviceScaleFactor: 1, mobile: true,
    screenOrientation: w > h ? {type: 'landscapePrimary', angle: 90} : {type: 'portraitPrimary', angle: 0}});
  await send('Page.navigate', {url: url + q});
  await sleep(50);
  if (!await waitFor(`document.readyState==='complete'&&location.protocol==='file:'`)) throw new Error('page did not load ' + q);
  if (!await waitFor(`window.oposUI&&document.getElementById('phone').dataset.app`)) throw new Error('shell did not render ' + q);
  if (after) await ev(after);
  await sleep(250);
}
const THREADS = `(()=>{const t=document.getElementById('threads'),r=[];t.querySelectorAll('button').forEach(b=>{const x=b.getBoundingClientRect();r.push({right:x.right,w:x.width})});
  return {threads:r,iw:innerWidth,sideways:document.body.scrollWidth>document.body.clientWidth+1||document.documentElement.scrollWidth>innerWidth+1}})()`;
(async () => {
  const timer = setTimeout(() => {
    process.stderr.write('timed out\n');
    closeBrowser().catch(e => process.stderr.write(String(e) + '\n')).finally(() => process.exit(3));
  }, 90000);
  const out = {};
  try {
    const {targetId} = await sendTo(null, 'Target.createTarget', {url: 'about:blank'});
    ({sessionId: session} = await sendTo(null, 'Target.attachToTarget', {targetId, flatten: true}));
    await send('Page.enable'); await send('Runtime.enable');
    await send('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'reduce'}]});
    await send('Page.addScriptToEvaluateOnNewDocument', {source: PRE});
    const q = '?lock=0&stub=1';
    // W1-10: a 100 character link in a message, and a page scrolled down when the app changes.
    for (const [name, url, w, h] of [['phone', plainUrl, 390, 844], ['pane', plainUrl, 675, 900], ['span', fileUrl, 1392, 900]]) {
      await run(url, w, h, q, `oposUI.openApp('messages')`);
      out['threads_' + name] = await ev(THREADS);
      await ev(`document.body.scrollTop=300`);
      out['scrolled_' + name] = await ev(`document.body.scrollTop`);
      await ev(`oposUI.openApp('settings')`);
      out['settings_' + name] = await ev(`document.body.scrollTop`);
    }
  } finally {
    clearTimeout(timer);
    await closeBrowser();
  }
  process.stdout.write(JSON.stringify(out) + '\n', () => process.exit(0));
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(2); });
""" % json.dumps(LINK)


def driver():
    """The UXP21 driver up to its cases, then this file's (the way tests/test_span.py builds its own)."""
    head, sep, _ = profiles.CDP.partition("async function run(")
    if not sep:
        raise AssertionError("tests/test_uxp21_profiles.py's driver changed: it has no 'async function run(' for "
                             "tests/test_w1_layout.py to replace; update MAIN here to match")
    return head + MAIN


def states():
    """The canned /api/state answers the page boots from: the real ones plus eight threads, one with a 100 character link."""
    hidden, visible = Phone().state(), Phone(visible=True).state()
    for s in (hidden, visible):
        s["threads"] = [{"number": "+1555010%d" % i, "name": "+1555010%d" % i,
                         "preview": LINK if i == 3 else "Message number %d with some words in it to fill the page" % i} for i in range(8)]
    return {"hidden": hidden, "visible": visible}


class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = None
        script = driver()
        chrome = find_chrome()
        if not chrome or not NODE:
            return
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            files = {}
            for name, span in (("span", DUO), ("plain", None)):
                files[name] = Path(d) / (name + ".html")
                files[name].write_text(page(span=span), encoding="utf-8")
            state = Path(d) / "state.json"
            state.write_text(json.dumps(states(), default=str), encoding="utf-8")
            drv = Path(d) / "cdp.js"
            drv.write_text(script, encoding="utf-8")
            p = subprocess.run([NODE, str(drv), chrome, files["span"].as_uri(), str(state), files["plain"].as_uri()],
                               capture_output=True, text=True, timeout=120, env=dict(os.environ, TMPDIR=d))
            if p.returncode:
                raise AssertionError("CDP driver failed: " + p.stderr[-5000:])
            cls.r = json.loads(p.stdout.strip().splitlines()[-1])

    def setUp(self):
        if self.r is None:
            self.skipTest("no headless chromium or node")

    def test_a_100_character_link_stays_inside_its_pane(self):
        for name in ("phone", "pane", "span"):
            m = self.r["threads_" + name]
            self.assertEqual(len(m["threads"]), 8, name)
            for t in m["threads"]:
                self.assertLessEqual(t["right"], m["iw"] - 16, name)
            self.assertFalse(m["sideways"], name)

    def test_changing_app_scrolls_the_page_back_to_the_top(self):
        for name in ("phone", "pane", "span"):
            self.assertGreater(self.r["scrolled_" + name], 0, name + ": the page has to be scrolled for this to mean anything")
            self.assertEqual(self.r["settings_" + name], 0, name)


if __name__ == "__main__":
    unittest.main()
