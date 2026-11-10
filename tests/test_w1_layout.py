# SPDX-License-Identifier: GPL-3.0-or-later
"""Wave 1, layout track: scroll and long words (W1-10), the hinge (W1-11), compose with the keyboard up (W1-12).

Static checks on the page's own CSS and scripts. What the real layout does (a 100 character link in a thread, nothing on the hinge,
the field and Send above the keyboard) can only be measured in a browser: tests/test_span.py does it for the hinge, and the
measurements for this track are in docs/jaryo-panes.md section 11.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from opos import osk
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


class Keyboard(unittest.TestCase):
    """W1-12: Send, Call and the Helper field end up under or far from the on-screen keyboard (OPOS_OSK=1 publishes its height as --osk-h)."""

    def test_the_scroller_keeps_the_keyboards_height_clear(self):
        # With this, the browser's own scroll-into-view (the keyboard's reveal() calls it) puts a field above the keyboard, not above the
        # bottom of the window. It does not add scroll room: the keyboard's own padding element (#opos-osk-pad) does that.
        self.assertEqual(declarations(inline_css(), "body").get("scroll-padding-bottom"), "var(--osk-h,0px)")

    def test_a_field_leaves_room_for_the_buttons_under_it(self):
        # Send and Call sit 97 to 133 px below the bottom of their field; the Helper's Send bar 64 px.
        for sel in ("#number", "#msg", "#q"):
            self.assertEqual(declarations(inline_css(), sel).get("scroll-margin-bottom"), "140px", sel)
        # Only text fields: a margin on every input would also move checkboxes and sliders when they take focus.
        self.assertIsNone(declarations(inline_css(), "input").get("scroll-margin-bottom"))

    def test_the_helper_log_has_a_fixed_height_and_scrolls_inside(self):
        # A fixed height, not a maximum: the log is above the field, so every line it gained used to push the field down 60 to 140 px.
        d = declarations(inline_css(), "#hs-log")
        self.assertEqual((d.get("height"), d.get("overflow-y")), ("35dvh", "auto"))

    def test_sticky_controls_were_tried_and_are_not_used(self):
        # position:sticky on #controls is the plan's first idea: in the spanned grid the bar is stretched to its grid row and cannot leave
        # it (Send ended above the field), and without the keyboard it covers #msg. A scroll margin does the job without either.
        css = re.sub(r'html\[data-ff="slideout"\][^{}]*\{[^{}]*\}', "", (STATIC / "phone-b.css").read_text(encoding="utf-8"))
        self.assertNotIn("sticky", css)


HELPER_FOLLOWS = r"""
 async followlog(){const w=world();w.hooks();await w.open('helper');const log=()=>w.$('hs-log');const reveals=[];w.ids.q.scrollIntoView=o=>reveals.push(o);
  w.queue.push('ok','ok');await w.ask('first');log().scrollHeight=500;
  const unfocused={scrollTop:log().scrollTop,reveals:reveals.length};
  w.ctx.document.activeElement=w.ids.q;log().scrollHeight=777;await w.ask('second');
  return{unfocused,scrollTop:log().scrollTop,reveals,rows:log().children.length}},
"""


@unittest.skipUnless(NODE, "node not installed")
class HelperLog(unittest.TestCase):
    """The log has a fixed height now, so the newest turn has to be scrolled into it, and a focused field has to stay above the keyboard."""

    @classmethod
    def setUpClass(cls):
        from tests import test_uxp31_helper_screen as h
        from opos import assist
        script = h.HARNESS.replace("const S={", "const S={" + HELPER_FOLLOWS, 1)
        assert script != h.HARNESS, "tests/test_uxp31_helper_screen.py's harness changed: no 'const S={' to add a scenario to"
        p = subprocess.run([NODE, "-e", script, str(STATIC / "phone-b.js"), h.WHY,
                            json.dumps(assist.answer("what is paused?", env={}, free=0, context={"mode": "CRITICAL"}))],
                           capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, p.stderr
        cls.r = json.loads(p.stdout.strip().splitlines()[-1])

    def test_the_newest_turn_is_scrolled_into_the_log(self):
        self.assertNotIn("error", self.r)
        self.assertEqual(self.r["followlog"]["scrollTop"], 777)

    def test_a_focused_field_is_scrolled_back_above_the_keyboard_when_the_log_grows_but_a_resting_one_is_left_alone(self):
        r = self.r["followlog"]
        self.assertEqual(r["unfocused"]["reveals"], 0)
        self.assertGreater(len(r["reveals"]), 0)
        self.assertTrue(all(o == {"block": "nearest"} for o in r["reveals"]))


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
const plainUrl = process.argv[5], oskUrl = process.argv[6];  // argv: chrome, span page, state file, plain page, span page with the keyboard
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
async function tap(sel) {  // a real mouse press: the keyboard opens only for a field that was tapped
  const c = await ev(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});e.scrollIntoView({block:'nearest'});const b=e.getBoundingClientRect();return {x:b.left+b.width/2,y:b.top+b.height/2}})()`);
  await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: c.x, y: c.y});
  await send('Input.dispatchMouseEvent', {type: 'mousePressed', x: c.x, y: c.y, button: 'left', clickCount: 1});
  await send('Input.dispatchMouseEvent', {type: 'mouseReleased', x: c.x, y: c.y, button: 'left', clickCount: 1});
  await sleep(700);
}
const KBD = `(()=>{const ps=[...document.querySelectorAll('#opos-osk .osk-panel')].map(e=>e.getBoundingClientRect().top),k=document.getElementById('opos-osk');
  const top=ps.length&&k&&!k.hidden?Math.min(...ps):innerHeight;
  const r=s=>{const e=document.querySelector(s);if(!e)return null;const b=e.getBoundingClientRect(),h=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2);return {top:b.top,bottom:b.bottom,covered:!(h===e||e.contains(h))}};
  return {kbd:top,shown:top<innerHeight,msg:r('#msg'),send:r('[data-go=send]'),number:r('#number'),call:r('[data-go=call]'),q:r('#q'),hs:r('#hs-send')}})()`;
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
    // W1-12: with the keyboard up (a real tap on the field), the field and the button that goes with it are above the keyboard.
    for (const [name, w, h] of [['span', 1392, 900], ['pane', 675, 900]]) {
      await run(oskUrl, w, h, q, `oposUI.openApp('messages')`);
      await tap('#msg');
      out['kbd_msg_' + name] = await ev(KBD);
      await run(oskUrl, w, h, q, `oposUI.openApp('phone')`);
      await tap('#number');
      out['kbd_phone_' + name] = await ev(KBD);
      // The Helper: ask three times with the keyboard up; the field must not move when an answer arrives, and stays above the keyboard.
      await run(oskUrl, w, h, q, `oposUI.openApp('helper')`);
      const turns = [];
      if (await waitFor(`document.getElementById('hs-send')&&!document.getElementById('ask').hidden`, 5000)) {
        await sleep(400);
        await tap('#q');
        for (let i = 0; i < 3; i++) {
          await ev(`document.getElementById('q').value='';`);
          await send('Input.insertText', {text: 'What is draining my battery? ' + i});
          await ev(`document.getElementById('hs-send').click()`);
          await sleep(120);
          const asked = await ev(KBD);
          await sleep(500);
          turns.push({asked: asked.q.top, answered: (await ev(KBD)), logs: await ev(`document.getElementById('hs-log').children.length`)});
        }
      }
      out['kbd_helper_' + name] = turns;
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
            # The keyboard's two tags point at the server's /osk.js and /osk.css; beside a file they are plain relative names.
            for asset in ("osk.js", "osk.css"):
                shutil.copy(Path(osk.__file__).with_name("osk_static") / asset, Path(d) / asset)
            files["osk"] = Path(d) / "osk.html"
            files["osk"].write_text(osk.inject(page(span=DUO), "n" * 22, {"OPOS_OSK": "1"}).replace('"/osk.', '"osk.'), encoding="utf-8")
            state = Path(d) / "state.json"
            state.write_text(json.dumps(states(), default=str), encoding="utf-8")
            drv = Path(d) / "cdp.js"
            drv.write_text(script, encoding="utf-8")
            p = subprocess.run([NODE, str(drv), chrome, files["span"].as_uri(), str(state), files["plain"].as_uri(), files["osk"].as_uri()],
                               capture_output=True, text=True, timeout=180, env=dict(os.environ, TMPDIR=d))
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

    def test_the_field_and_its_button_are_above_the_keyboard(self):
        for name in ("span", "pane"):
            for k, field, button in (("kbd_msg_", "msg", "send"), ("kbd_phone_", "number", "call")):
                m = self.r[k + name]
                self.assertTrue(m["shown"], k + name + ": the keyboard did not open")
                for what in (field, button):
                    self.assertLessEqual(m[what]["bottom"], m["kbd"] + 0.5, k + name + " " + what)
                    self.assertGreaterEqual(m[what]["top"], 0, k + name + " " + what)
                    self.assertFalse(m[what]["covered"], k + name + " " + what)

    def test_the_helper_field_does_not_move_when_an_answer_arrives_and_stays_above_the_keyboard(self):
        for name in ("span", "pane"):
            turns = self.r["kbd_helper_" + name]
            self.assertEqual(len(turns), 3, name + ": the Helper screen did not open")
            for t in turns:
                self.assertTrue(t["answered"]["shown"], name)
                self.assertGreaterEqual(t["logs"], 1, name)
                self.assertAlmostEqual(t["asked"], t["answered"]["q"]["top"], delta=1, msg=name + ": the field moved when the answer arrived")
                self.assertLessEqual(t["answered"]["q"]["bottom"], t["answered"]["kbd"] + 0.5, name)
                self.assertLessEqual(t["answered"]["hs"]["bottom"], t["answered"]["kbd"] + 0.5, name + ": Send is under the keyboard")

    def test_changing_app_scrolls_the_page_back_to_the_top(self):
        for name in ("phone", "pane", "span"):
            self.assertGreater(self.r["scrolled_" + name], 0, name + ": the page has to be scrolled for this to mean anything")
            self.assertEqual(self.r["settings_" + name], 0, name)


if __name__ == "__main__":
    unittest.main()
