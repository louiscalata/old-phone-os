# SPDX-License-Identifier: GPL-3.0-or-later
"""UXP21: device-type layouts (Auto / Slab / Foldable / Slide-out / Tablet), safe areas and the hinge gutter.

Static asserts on phone-b.* and page() output, a node vm run of the Settings init, and one real-browser
check over the DevTools pipe (headless Chromium found like tests/test_uxr_acceptance.py; skips without it).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from opos.web import Phone, page
from tests.test_uxr_acceptance import find_chrome

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"
NODE = shutil.which("node")


def css():
    return (STATIC / "phone-b.css").read_text(encoding="utf-8")


def blocks(text, head):
    """Bodies of every top-level `head{...}` block, braces matched."""
    out, i = [], 0
    while True:
        i = text.find(head, i)
        if i < 0:
            return out
        j, depth = i + len(head), 1
        while depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        out.append(text[i + len(head):j - 1])
        i = j


class UXP21Static(unittest.TestCase):
    def test_device_type_radios(self):
        out = page("t" * 32, "n")
        values = re.findall(r'<input type="radio" name="ff" value="([^"]+)">', out)
        self.assertEqual(values, ["auto", "slab", "foldable", "slideout", "tablet"])
        for label in ("Auto", "Slab", "Foldable", "Slide-out", "Tablet"):
            self.assertIn(">%s</label>" % label, out)

    def test_whitelist_and_storage_key(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        self.assertLess(js.index("['slab','foldable','slideout','tablet']"), js.index("dataset.ff="))
        self.assertIn("localStorage.getItem('opos_ff')", js)
        self.assertIn("localStorage.setItem('opos_ff',v)", js)

    def test_foldable_plain_grid_without_experimental_api(self):
        wide = blocks(css(), "@media (min-width:840px){")
        self.assertEqual(len(wide), 1)
        body = wide[0]
        self.assertIn('html[data-ff="foldable"] #phone{max-width:none;display:grid;grid-template-columns:1fr 1fr;column-gap:24px;', body)
        self.assertNotIn("viewport-segment", body)
        # Nested grids stay even and wider-gapped so their middle gap covers the 24px centre.
        self.assertIn("column-gap:48px", body)
        self.assertIn('html[data-ff="foldable"] #home #allapps{grid-column:1/3}', body)
        self.assertIn('html[data-ff="foldable"] #lock:not([hidden]){display:grid;grid-template-columns:1fr 1fr;column-gap:24px;', body)

    def test_foldable_columns_follow_dom_order(self):
        # Column 2 is one contiguous DOM run (#threads onward), so dense placement never reorders reading/Tab order.
        text = css()
        self.assertIn('html[data-ff="foldable"] #phone>#threads,html[data-ff="foldable"] #phone>#threads~*,html[data-ff="foldable"] #lock>button{grid-column:2}', text)
        body4 = (STATIC / "body-4.html").read_text(encoding="utf-8")
        self.assertTrue(body4.lstrip().startswith('<div id="threads"'))

    def test_letter_jump_row_tracks_allapps_grid(self):
        layout = (STATIC / "jaryo-layout.css").read_text(encoding="utf-8")
        self.assertIn('#jaryo-letters{display:none;', layout)
        self.assertIn('#phone[data-app="home"][data-allapps] #jaryo-letters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}', layout)
        wide = blocks(layout, "@media (min-width:840px){")[0]
        self.assertIn('#phone[data-app="home"][data-allapps]>#jaryo-letters{grid-column:1/-1;column-gap:48px}', wide)
        segment = blocks(layout, "@media (horizontal-viewport-segments:2){")[0]
        self.assertIn('#phone[data-app="home"][data-allapps]>#jaryo-letters{grid-column:1}', segment)

    def test_experimental_queries_only_inside_media(self):
        text = css()
        seg = blocks(text, "@media (horizontal-viewport-segments:2){")
        self.assertEqual(len(seg), 1)
        self.assertIn("env(viewport-segment-width 0 0)", seg[0])
        self.assertIn("env(viewport-segment-left 1 0)", seg[0])
        # Column 1 ends at the first segment's right edge even though #phone/#lock pad by the left inset.
        self.assertIn("grid-template-columns:calc(env(viewport-segment-width 0 0) - env(safe-area-inset-left,0px)) 1fr;", seg[0])
        outside = text
        for b in seg:
            outside = outside.replace(b, "")
        self.assertEqual(outside.count("viewport-segment"), 1)  # only the @media prelude itself
        self.assertNotRegex(outside, r"env\(viewport-segment")

    def test_slideout_and_tablet_rules(self):
        text = css()
        land = [b for b in blocks(text, "@media (orientation:landscape){") if "slideout" in b]
        self.assertEqual(len(land), 1)
        self.assertIn('html[data-ff="slideout"] #phone[data-app] #home:not([hidden]){grid-template-columns:1fr;', land[0])
        self.assertIn('html[data-ff="slideout"] #controls{position:sticky;bottom:0;', land[0])
        self.assertIn('html[data-ff="slideout"] #controls button{min-height:64px', land[0])
        self.assertIn('html[data-ff="tablet"] #phone{max-width:720px}', text)
        # Legacy 'slide' selectors still match the same rules.
        self.assertIn('html[data-ff="slideout"] #phone,html[data-ff="slide"] #phone{max-width:none}', text)

    def test_safe_areas(self):
        out = page("t" * 32, "n")
        style = out[:out.index("</style>")]
        for sel in ("#sb{", "#lock{", "#phone[data-app] #home:not([hidden]),#phone #settings{", 'html[data-ff="slideout"] #controls{'):
            rules = [m for m in re.findall(re.escape(sel) + r"[^}]*\}", style) if "env(safe-area-inset-" in m]
            self.assertTrue(rules, sel)
        self.assertIn("calc(8px + env(safe-area-inset-bottom,0px))", style)
        self.assertIn("margin-left:max(0px,12px - env(safe-area-inset-left,0px))", style)


JSVM = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8'), out = {};
for (const stored of ['slide', 'slideout', 'tablet', 'foldable', 'bogus', null]) {
  const store = {}; if (stored !== null) store.opos_ff = stored;
  const de = {dataset: {}};
  const doc = {documentElement: de, getElementById() { return null; }, querySelector() { return null; }};
  const win = {addEventListener() {}, matchMedia() { return {matches: false}; }};
  const ctx = {document: doc, window: win, location: {search: ''}, setTimeout, Promise,
    localStorage: {getItem(k) { return k in store ? store[k] : null; }, setItem(k, v) { store[k] = String(v); }, removeItem(k) { delete store[k]; }}};
  win.window = win; win.document = doc;
  vm.runInNewContext(src, ctx);
  out[String(stored)] = de.dataset.ff === undefined ? null : de.dataset.ff;
}
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node missing")
class UXP21Runtime(unittest.TestCase):
    def test_saved_values(self):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / "h.js"
            h.write_text(JSVM, encoding="utf-8")
            r = subprocess.run([NODE, str(h), str(STATIC / "phone-b.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"slide": "slideout", "slideout": "slideout", "tablet": "tablet",
                                                "foldable": "foldable", "bogus": None, "null": None})


# Starts Chromium with --remote-debugging-pipe (no local port), loads the page per case, prints one JSON result.
CDP = r"""
'use strict';
const fs = require('fs'), os = require('os'), path = require('path'), {spawn} = require('child_process');
const [chrome, fileUrl, stateFile] = process.argv.slice(2);
const STATE = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'uxp21-'));
const child = spawn(chrome, ['--headless', '--no-sandbox', '--disable-gpu', ...(process.platform === 'darwin' ? ['--single-process'] : []), '--remote-debugging-pipe',
  '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check', 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe']});
let buf = Buffer.alloc(0), seq = 0, session;
const pending = new Map();
let browserError = null, browserStderr = '';
child.stderr.on('data', chunk => { browserStderr = (browserStderr + chunk).slice(-4000); });
function browserFailed(reason) {
  browserError = new Error(reason + '\nChromium stderr: ' + browserStderr);
  for (const p of pending.values()) p.rej(browserError);
  pending.clear();
}
child.on('error', error => browserFailed('Chromium launch: ' + error.message));
child.on('exit', (code, signal) => browserFailed('Chromium exited: ' + code + ' / ' + signal));
for (const pipe of [child.stdio[3], child.stdio[4]])
  pipe.on('error', error => browserFailed('CDP pipe: ' + error.message));
child.stdio[4].on('data', c => {
  buf = Buffer.concat([buf, c]);
  for (let i; (i = buf.indexOf(0)) >= 0; buf = buf.subarray(i + 1)) {
    const d = JSON.parse(buf.subarray(0, i).toString()), p = d.id && pending.get(d.id);
    if (p) { pending.delete(d.id); d.error ? p.rej(new Error(p.m + ': ' + d.error.message)) : p.res(d.result); }
  }
});
function sendTo(sessionId, m, params = {}) {
  if (browserError) return Promise.reject(browserError);
  return new Promise((res, rej) => { const id = ++seq; pending.set(id, {res, rej, m});
    child.stdio[3].write(JSON.stringify(sessionId ? {id, method: m, params, sessionId} : {id, method: m, params}) + '\0'); });
}
const send = (m, p) => sendTo(session, m, p);
async function ev(expression) {
  const r = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (r.exceptionDetails) throw new Error('page error: ' + JSON.stringify(r.exceptionDetails).slice(0, 300));
  return r.result.value;
}
async function waitFor(expr, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await ev('!!(' + expr + ')')) return true; await sleep(25); }
  return false;
}
// Before page scripts: ?ff=<x> seeds the saved device type; ?stub=1 serves canned /api state so the shell renders.
const PRE = `(function(){var m=/[?&]ff=([a-z]+)/.exec(location.search);try{if(m)localStorage.setItem('opos_ff',m[1]);else localStorage.removeItem('opos_ff')}catch(e){}
if(!/[?&]stub=1/.test(location.search))return;var S=${JSON.stringify(STATE)},vis=false;
function resp(o){return Promise.resolve(new Response(JSON.stringify(o),{status:200,headers:{'Content-Type':'application/json'}}))}
window.fetch=function(u,o){var url=String(u&&u.url||u),b={};try{b=JSON.parse(o&&o.body||'{}')}catch(e){}
 if(/[/]api[/]state$/.test(url))return resp(vis?S.visible:S.hidden);
 if(b.action==='visible')vis=!!b.visible;
 if(/^(ask|plan|explain)$/.test(b.action))return resp({text:'ok',engine:'rules',where:'on this phone',order:[]});
 return resp(vis?S.visible:S.hidden)}})();`;
const MEASURE = `(function(){
function vis(e){if(e.closest('[hidden]')||e.closest('[inert]'))return false;var c=getComputedStyle(e);if(c.display==='none'||c.visibility!=='visible')return false;var r=e.getBoundingClientRect();return r.width>0&&r.height>0}
var W=innerWidth,H=window.__hinge||[W/2-12,W/2+12],lo=H[0],hi=H[1],bad=[],n=0;
// Buttons and text inputs: none may cross the hinge or stick out of the viewport.
Array.prototype.forEach.call(document.querySelectorAll('button,input[type=text]'),function(b){if(b.id==='meter'||!vis(b))return;n++;var r=b.getBoundingClientRect();
 if((r.left<hi-0.5&&r.right>lo+0.5)||r.left<-0.5||r.right>W+0.5)bad.push((b.id||b.getAttribute('data-open')||b.getAttribute('data-go')||b.textContent.trim())+' '+Math.round(r.left)+'-'+Math.round(r.right))});
// Reading order: walking the in-flow grid items in DOM (= Tab) order, the left pane comes first, then the right pane,
// each top to bottom; a full-width item may only come before any pane item.
var order=[],lock=document.getElementById('lock'),box=lock&&!lock.hidden?lock:document.getElementById('phone');
var side=-1,y=-1;Array.prototype.forEach.call(box.children,function(e){if(!vis(e))return;var c=getComputedStyle(e);if(c.position==='fixed'||c.position==='absolute')return;
 var r=e.getBoundingClientRect(),s=r.left<lo&&r.right>hi?-1:(r.right<=hi?0:1),id=e.id||e.tagName;
 if(s<side||(s===side&&r.top<y-0.5))order.push(id+' side '+s+' top '+Math.round(r.top));side=s;y=r.top});
var p=document.getElementById('phone').getBoundingClientRect();
return {ff:document.documentElement.dataset.ff||null,display:getComputedStyle(document.getElementById('phone')).display,
 n:n,bad:bad,order:order,left:p.left,right:W-p.right,width:p.width,
 seg:matchMedia('(horizontal-viewport-segments:2)').matches,padL:getComputedStyle(box).paddingLeft}})()`;
async function run(w, h, q, after, pre) {
  await send('Emulation.setDeviceMetricsOverride', {width: w, height: h, deviceScaleFactor: 1, mobile: true,
    screenOrientation: w > h ? {type: 'landscapePrimary', angle: 90} : {type: 'portraitPrimary', angle: 0}});
  if (pre) await pre();
  await send('Page.navigate', {url: fileUrl + q});
  await sleep(50);
  if (!await waitFor(`document.readyState==='complete'&&location.protocol==='file:'`)) throw new Error('page did not load ' + q);
  if (/stub=1/.test(q) && !await waitFor(`window.oposUI&&document.getElementById('phone').dataset.app`)) throw new Error('shell did not render ' + q);
  if (after) await ev(after);
  await sleep(250);
  return ev(MEASURE);
}
(async () => {
  const timer = setTimeout(() => { process.stderr.write('timed out\n'); try { child.kill('SIGKILL'); } catch (e) {} process.exit(3); }, 60000);
  const out = {};
  try {
    const {targetId} = await sendTo(null, 'Target.createTarget', {url: 'about:blank'});
    ({sessionId: session} = await sendTo(null, 'Target.attachToTarget', {targetId, flatten: true}));
    await send('Page.enable'); await send('Runtime.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {source: PRE});
    out.fold_full = await run(1280, 720, '?ff=foldable');
    for (const app of ['home', 'phone', 'messages', 'helper', 'settings'])
      out['fold_' + app] = await run(1280, 720, '?lock=0&stub=1&ff=foldable', `oposUI.openApp('${app}')`);
    out.fold_allapps = await run(1280, 720, '?lock=0&stub=1&ff=foldable', `oposUI.openApp('home');document.getElementById('allapps').click()`);
    out.fold_lock = await run(1280, 720, '?stub=1&ff=foldable', `oposUI.lock&&oposUI.lock()`);
    out.tablet_full = await run(1280, 720, '?ff=tablet');
    out.tablet_home = await run(1280, 720, '?lock=0&stub=1&ff=tablet', `oposUI.openApp('home')`);
    out.legacy = await run(1280, 720, '?ff=slide');
    out.none = await run(1280, 720, '');
    // Bonus branch: an off-centre 40px hinge at x 600-640 plus a 40px left safe-area inset. Older Chromium lacks
    // these overrides; then seg_* stays absent and the test skips.
    let segOk = true;
    const seg = async () => {
      await send('Emulation.setSafeAreaInsetsOverride', {insets: {left: 40, leftMax: 40, top: 0, topMax: 0, right: 0, rightMax: 0, bottom: 0, bottomMax: 0}});
      await send('Emulation.setDisplayFeaturesOverride', {features: [{orientation: 'vertical', offset: 600, maskLength: 40}]});
    };
    try { await seg(); } catch (e) { segOk = false; }
    if (segOk) {
      const hinge = 'window.__hinge=[600,640];';
      out.seg_full = await run(1280, 720, '?ff=foldable', hinge, seg);
      for (const app of ['home', 'phone', 'messages', 'helper', 'settings'])
        out['seg_' + app] = await run(1280, 720, '?lock=0&stub=1&ff=foldable', hinge + `oposUI.openApp('${app}')`, seg);
      out.seg_lock = await run(1280, 720, '?stub=1&ff=foldable', hinge + `oposUI.lock&&oposUI.lock()`, seg);
    }
  } finally {
    clearTimeout(timer);
    try { child.kill('SIGKILL'); } catch (e) {}
    try { fs.rmSync(udd, {recursive: true, force: true}); } catch (e) {}
  }
  process.stdout.write(JSON.stringify(out) + '\n', () => process.exit(0));
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); try { child.kill('SIGKILL'); } catch (_) {} process.exit(2); });
"""


class UXP21Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = None
        chrome = find_chrome()
        if not chrome or not NODE:
            return
        with tempfile.TemporaryDirectory() as d:
            html = Path(d) / "page.html"
            html.write_text(page(demo=True), encoding="utf-8")
            state = Path(d) / "state.json"
            state.write_text(json.dumps({"hidden": Phone().state(), "visible": Phone(visible=True).state()}, default=str), encoding="utf-8")
            drv = Path(d) / "cdp.js"
            drv.write_text(CDP, encoding="utf-8")
            p = subprocess.run([NODE, str(drv), chrome, html.as_uri(), str(state)], capture_output=True, text=True,
                               timeout=90, env=dict(os.environ, TMPDIR=d))
            if p.returncode:
                raise AssertionError("CDP driver failed: " + p.stderr[-5000:])
            cls.r = json.loads(p.stdout.strip().splitlines()[-1])

    def setUp(self):
        if self.r is None:
            self.skipTest("no headless chromium or node")

    def test_no_button_in_hinge_gutter(self):
        for k in ("fold_full", "fold_home", "fold_phone", "fold_messages", "fold_helper", "fold_settings", "fold_allapps", "fold_lock"):
            m = self.r[k]
            self.assertEqual(m["ff"], "foldable", k)
            self.assertGreater(m["n"], 0, k)
            self.assertEqual(m["bad"], [], k)
            self.assertEqual(m["order"], [], k)
        self.assertEqual(self.r["fold_full"]["display"], "grid")
        self.assertEqual(self.r["fold_home"]["width"], 1280)

    def test_segments_with_left_inset_keep_hinge_clear(self):
        # Skip only when the emulation itself is unsupported; a missing padding is a real failure.
        if "seg_full" not in self.r or not self.r["seg_full"]["seg"]:
            self.skipTest("this Chromium cannot emulate viewport segments with a safe-area inset")
        for k in ("seg_full", "seg_home", "seg_phone", "seg_messages", "seg_helper", "seg_settings", "seg_lock"):
            m = self.r[k]
            self.assertTrue(m["seg"], k)
            self.assertEqual(m["padL"], "40px", k)
            self.assertGreater(m["n"], 0, k)
            self.assertEqual(m["bad"], [], k)
            self.assertEqual(m["order"], [], k)

    def test_tablet_max_width_centred(self):
        for k in ("tablet_full", "tablet_home"):
            m = self.r[k]
            self.assertEqual(m["ff"], "tablet", k)
            self.assertEqual(m["width"], 720, k)
            self.assertLessEqual(abs(m["left"] - m["right"]), 1, k)

    def test_legacy_and_default(self):
        self.assertEqual(self.r["legacy"]["ff"], "slideout")
        self.assertIsNone(self.r["none"]["ff"])
        self.assertEqual(self.r["none"]["width"], 420)
        self.assertNotEqual(self.r["none"]["display"], "grid")


if __name__ == "__main__":
    unittest.main()
