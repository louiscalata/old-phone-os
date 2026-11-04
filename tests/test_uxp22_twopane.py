# SPDX-License-Identifier: GPL-3.0-or-later
"""UXP22: Settings and Helper as two columns on screens >= 840px wide (CSS only, no JS, no DOM change).

Static asserts on phone-b.css / page(), plus one real-browser run over the DevTools pipe (headless Chromium
found like tests/test_uxr_acceptance.py; skips without it). The in-page helpers (window.__u: vis, inter, sel)
are read from tests/uxr_cdp.js so both suites measure targets the same way.
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
from tests.test_uxp21_profiles import blocks
from tests.test_uxr_acceptance import find_chrome

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "src" / "opos" / "web_static"
NODE = shutil.which("node")
WIDE = "@media (min-width:840px){"
X = "html:not([data-ff=slab]):not([data-ff=foldable])"
RULES = (
    X + " #phone[data-app=settings]," + X + " #phone[data-app=helper]{max-width:960px}",
    X + " #phone[data-app=settings] #settings:not([hidden]){display:grid;grid-template-columns:1fr 1fr;",
    X + " #settings>#ff,",
    X + " #phone[data-app=helper]{display:grid;grid-template-columns:1fr 1fr;",
    # The conversation (#ask, now first in the DOM and holding #helper-where) reads first; Technical details sit beside it.
    X + " #phone[data-app=helper]>#ask{grid-column:1}",
    X + " #phone[data-app=helper]>#helper{grid-column:2}",
)


def css():
    return (STATIC / "phone-b.css").read_text(encoding="utf-8")


class UXP22Static(unittest.TestCase):
    def test_rules_inside_wide_media_only(self):
        text = css()
        wide = blocks(text, WIDE)
        self.assertEqual(len(wide), 1)
        for rule in RULES:
            self.assertIn(rule, wide[0])
        outside = text.replace(wide[0], "")
        self.assertNotIn(X, outside)
        self.assertNotIn("max-width:960px", outside)

    def test_narrow_width_and_summary_targets(self):
        style = page().split("</style>", 1)[0]
        narrow = blocks(style, "@media (max-width:839px){")
        self.assertEqual(len(narrow), 1)
        # Root specificity keeps later device caps from overriding UXP23 below 840px.
        self.assertIn("html:root[data-ff] #phone,html:not([data-ff]) #phone{width:100%;max-width:none", narrow[0])
        self.assertIn("summary{min-height:48px;padding:12px 0;box-sizing:border-box}", style)
        # A non-list-item display hides the summary's open/close arrow in Chromium.
        for rule in re.findall(r"summary[^{]*\{([^}]*)\}", style):
            self.assertNotRegex(rule, r"display\s*:", rule)

    def test_inlined_in_page(self):
        out = page("t" * 32, "n")
        style = out[:out.index("</style>")]
        for rule in RULES:
            self.assertIn(rule, style)

    def test_no_js_or_markup_dependency(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        self.assertNotIn("840", js)
        self.assertNotIn("960", js)
        # The Settings grid places existing children; the markup keeps the order the columns read in.
        html = (STATIC / "phone-b.html").read_text(encoding="utf-8")
        order = [html.index(s) for s in ("<h2>Settings</h2>", '<fieldset id="ff">', 'id="ff-detected"', 'id="ff-saved"',
                                          'aria-labelledby="set-display"', 'aria-labelledby="set-about"')]
        self.assertEqual(order, sorted(order))
        body4 = (STATIC / "body-4.html").read_text(encoding="utf-8")
        self.assertLess(body4.index('id="ask"'), body4.index('id="helper"'))


# Starts Chromium with --remote-debugging-pipe (no local port), loads the page per case, prints one JSON result.
CDP = r"""
'use strict';
const fs = require('fs'), os = require('os'), path = require('path'), {spawn} = require('child_process');
const [chrome, fileUrl, stateFile, uxrFile] = process.argv.slice(2);
const STATE = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
// Reuse window.__u from tests/uxr_cdp.js (a template literal with no substitutions).
const LIB = eval(/const LIB = (`[\s\S]*?`);/.exec(fs.readFileSync(uxrFile, 'utf8'))[1]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'uxp22-'));
const child = spawn(chrome, ['--headless', '--no-sandbox', '--disable-gpu', ...(process.platform === 'darwin' ? ['--single-process'] : []), '--remote-debugging-pipe',
  '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check', 'about:blank'], {detached: true, stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe']});
// Own the process group so Linux's Chrome children cannot outlive cleanup.
async function closeBrowser() {
  if (child.pid) {
    try { process.kill(-child.pid, 'SIGKILL'); } catch (e) { if (e.code !== 'ESRCH') throw e; }
    if (child.exitCode === null && child.signalCode === null) {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Chromium did not exit after SIGKILL')), 10000);
        child.once('exit', () => { clearTimeout(timeout); resolve(); });
      });
    }
  }
  try { fs.rmSync(udd, {recursive: true, force: true}); } catch (e) {}
}
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
// Before page scripts: ?ff=<x> seeds the saved device type, ?text=<x> the text size; canned /api state so the shell renders.
const PRE = `(function(){var m=/[?&]ff=([a-z]+)/.exec(location.search),t=/[?&]text=([a-z]+)/.exec(location.search);
try{if(m)localStorage.setItem('opos_ff',m[1]);else localStorage.removeItem('opos_ff');if(t)localStorage.setItem('opos_text',t[1]);else localStorage.removeItem('opos_text')}catch(e){}
var S=${JSON.stringify(STATE)};S=/[?&]critical=1(?:&|$)/.test(location.search)?S.critical:S.ready;
var vis=false;window.__uxp22FixtureMode=S.visible.helper.mode;
function resp(o){return Promise.resolve(new Response(JSON.stringify(o),{status:200,headers:{'Content-Type':'application/json'}}))}
window.fetch=function(u,o){var url=String(u&&u.url||u),b={};try{b=JSON.parse(o&&o.body||'{}')}catch(e){}
 if(/[/]api[/]state$/.test(url))return resp(vis?S.visible:S.hidden);
 if(b.action==='visible')vis=!!b.visible;
 if(/^(ask|plan|explain)$/.test(b.action))return resp({text:'ok',engine:'rules',where:'on this phone',order:[]});
 return resp(vis?S.visible:S.hidden)}})();`;
// Tap target of a radio/checkbox is its wrapping label; everything else is its own box. #meter is the 4px bar
// whose taps land on #meter-hit (same allowance as tests/test_uxr_acceptance.py).
// Available controls must remain keyboard reachable. Disabled controls are
// measured separately and asserted explicitly for ready/critical policy states.
const MEASURE = `(function(){var U=__u,d=document.documentElement;
function box(e){if(!e||!U.vis(e))return null;var r=e.getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width}}
var small=[],untab=[],disabled=[],n=0,previous=document.activeElement;U.inter().forEach(function(e){if(e.id==='meter'||!e.closest('#phone'))return;n++;
 var t=/^(radio|checkbox)$/.test(e.type)&&e.closest('label')||e,r=t.getBoundingClientRect();
 if(r.width<48||r.height<48)small.push(U.sel(e)+' '+Math.round(r.width)+'x'+Math.round(r.height));
 if(e.disabled)disabled.push(U.sel(e));else if(e.tabIndex<0)untab.push(U.sel(e));else{e.focus({preventScroll:true});if(document.activeElement!==e)untab.push(U.sel(e))}});
if(previous&&previous.focus)previous.focus({preventScroll:true});
var send=document.getElementById('hs-send'),cancel=document.getElementById('hs-cancel'),status=document.getElementById('hs-status'),
 helper=document.getElementById('helper'),mode=helper&&helper.textContent.split('Mode: ')[1],plan=document.getElementById('planbtn'),clear=document.getElementById('hs-clear');
return {app:document.getElementById('phone').dataset.app||null,sw:d.scrollWidth,iw:innerWidth,phone:box(document.getElementById('phone')),
 ff:box(document.getElementById('ff')),display:box(document.querySelector('[aria-labelledby=set-display]')),
 about:box(document.querySelector('[aria-labelledby=set-about]')),helper:box(document.getElementById('helper')),
 ask:box(document.getElementById('ask')),n:n,small:small,untab:untab,disabled:disabled,fixture_mode:window.__uxp22FixtureMode,
 rendered_mode:mode?mode.split('Allowed:')[0].trim():null,send_disabled:send&&send.disabled,cancel_disabled:cancel&&cancel.disabled,rest_status:status&&status.textContent,
 plan_enabled:!!plan&&!plan.disabled,clear_enabled:!!clear&&!clear.disabled,
 why_enabled:Array.from(document.querySelectorAll('#ask button,#helper button')).some(b=>b.textContent==='Why?'&&!b.disabled)}})()`;
async function run(w, h, q, app) {
  await send('Emulation.setDeviceMetricsOverride', {width: w, height: h, deviceScaleFactor: 1, mobile: true,
    screenOrientation: w > h ? {type: 'landscapePrimary', angle: 90} : {type: 'portraitPrimary', angle: 0}});
  await send('Page.navigate', {url: fileUrl + '?lock=0' + q});
  await sleep(50);
  if (!await waitFor(`document.readyState==='complete'&&location.protocol==='file:'`)) throw new Error('page did not load ' + q);
  if (!await waitFor(`window.oposUI&&document.getElementById('phone').dataset.app`)) throw new Error('shell did not render ' + q);
  await ev(`oposUI.openApp('${app}')`);
  if (app === 'helper' && !await waitFor(`!document.getElementById('ask').hidden&&document.getElementById('phone').dataset.app==='helper'`))
    throw new Error('helper did not open ' + w + 'x' + h + q);
  await sleep(200);
  await ev(LIB);
  return ev(MEASURE);
}
(async () => {
  const timer = setTimeout(() => {
    process.stderr.write('timed out\n');
    closeBrowser().catch(e => process.stderr.write(String(e) + '\n')).finally(() => process.exit(3));
  }, 60000);
  const out = {};
  try {
    const {targetId} = await sendTo(null, 'Target.createTarget', {url: 'about:blank'});
    ({sessionId: session} = await sendTo(null, 'Target.attachToTarget', {targetId, flatten: true}));
    await send('Page.enable'); await send('Runtime.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {source: PRE});
    for (const [w, h] of [[1280, 720], [840, 1200], [720, 1280]])
      for (const app of ['settings', 'helper']) {
        out[app + '_' + w] = await run(w, h, '', app);
        out[app + '_' + w + '_xl'] = await run(w, h, '&text=xl', app);
      }
    out.settings_1280_tablet = await run(1280, 720, '&ff=tablet', 'settings');
    out.settings_1280_slab = await run(1280, 720, '&ff=slab', 'settings');
    out.helper_1280_critical = await run(1280, 720, '&critical=1', 'helper');
    await ev(`(function(){var b=document.createElement('button');b.id='keyboard-negative-control';b.type='button';b.tabIndex=-1;b.textContent='Keyboard negative control';document.getElementById('ask').appendChild(b)})()`);
    var negative = await ev(MEASURE);
    await ev("document.getElementById('keyboard-negative-control').remove()");
  } finally {
    clearTimeout(timer);
    await closeBrowser();
  }
  process.stdout.write(JSON.stringify({views:out,critical_keyboard_negative_control:negative}) + '\n', () => process.exit(0));
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(2); });
"""


def side_by_side(a, b):
    return b["l"] >= a["r"] - 0.5 and b["t"] < a["b"]


def stacked(a, b):
    return b["t"] >= a["b"] - 0.5 and b["l"] < a["r"]


class UXP22Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = None
        chrome = find_chrome()
        if not chrome or not NODE:
            return
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            html = Path(d) / "page.html"
            html.write_text(page(demo=True), encoding="utf-8")
            state = Path(d) / "state.json"
            scenarios = {}
            for name, level, expected in (("ready", 100.0, "NORMAL"), ("critical", 5.0, "CRITICAL")):
                pair = {}
                for visible, key in ((False, "hidden"), (True, "visible")):
                    phone = Phone(visible=visible)
                    phone.sim.level = level
                    phone.sim.health = 1.0
                    phone.sim.temp_c = 25.0
                    phone.dash.set_goal(None)
                    pair[key] = phone.state()
                if pair["visible"]["helper"]["mode"] != expected:
                    raise AssertionError("synthetic policy fixture is not " + expected)
                scenarios[name] = pair
            state.write_text(json.dumps(scenarios, default=str), encoding="utf-8")
            drv = Path(d) / "cdp.js"
            drv.write_text(CDP, encoding="utf-8")
            p = subprocess.run([NODE, str(drv), chrome, html.as_uri(), str(state), str(HERE / "uxr_cdp.js")],
                               check=False, capture_output=True, text=True, timeout=90, env=dict(os.environ, TMPDIR=d))
            if p.returncode:
                raise AssertionError("CDP driver failed: " + p.stderr[-5000:])
            payload = json.loads(p.stdout.strip().splitlines()[-1])
            cls.r = payload["views"]
            cls.critical_keyboard_negative_control = payload["critical_keyboard_negative_control"]

    def setUp(self):
        if self.r is None:
            self.skipTest("no headless chromium or node")

    def test_no_horizontal_scroll(self):
        for k, m in self.r.items():
            self.assertLessEqual(m["sw"], m["iw"], k)

    def test_settings_side_by_side_when_wide(self):
        for k in ("settings_1280", "settings_1280_xl", "settings_840", "settings_840_xl", "settings_1280_tablet"):
            m = self.r[k]
            self.assertEqual(m["app"], "settings", k)
            self.assertTrue(side_by_side(m["ff"], m["display"]), k)
            self.assertTrue(stacked(m["display"], m["about"]), k)
            self.assertLessEqual(m["phone"]["w"], 960, k)
        m = self.r["settings_1280"]
        self.assertEqual(m["phone"]["w"], 960)
        self.assertLessEqual(abs(m["phone"]["l"] - (m["iw"] - m["phone"]["r"])), 1)

    def test_single_column_below_840_and_on_slab(self):
        for k in ("settings_720", "settings_720_xl", "settings_1280_slab"):
            m = self.r[k]
            self.assertTrue(stacked(m["ff"], m["display"]), k)
            self.assertTrue(stacked(m["display"], m["about"]), k)
            # UXP23 fills narrow screens; explicit Slab stays capped on wide screens.
            self.assertEqual(m["phone"]["w"], 420 if k.endswith("_slab") else 720, k)
        for k in ("helper_720", "helper_720_xl"):
            m = self.r[k]
            self.assertEqual(m["app"], "helper", k)
            self.assertEqual(m["phone"]["w"], 720, k)
            self.assertTrue(stacked(m["ask"], m["helper"]), k)

    def test_helper_side_by_side_when_wide(self):
        for k in ("helper_1280", "helper_1280_xl", "helper_840", "helper_840_xl"):
            m = self.r[k]
            self.assertEqual(m["app"], "helper", k)
            self.assertTrue(side_by_side(m["ask"], m["helper"]), k)
            self.assertLessEqual(m["phone"]["w"], 960, k)

    def test_targets_and_keyboard(self):
        # Helper controls at < 840px are untouched by this spec (base 44px), so only the wide Helper views are sized here.
        for k, m in self.r.items():
            if k.startswith("helper_720"):
                continue
            self.assertGreater(m["n"], 0, k)
            self.assertEqual(m["small"], [], k)
        for k, m in self.r.items():
            self.assertEqual(m["untab"], [], k)
            expected = ["#hs-cancel"] if m["app"] == "helper" else []
            if k.endswith("_critical"):
                expected.append("#hs-send")
            self.assertCountEqual(m["disabled"], expected, k)
            self.assertEqual(m["fixture_mode"], "CRITICAL" if k.endswith("_critical") else "NORMAL", k)

    def test_critical_helper_disables_only_expected_actions_and_explains_rest(self):
        m = self.r["helper_1280_critical"]
        self.assertEqual(m["rendered_mode"], "CRITICAL")
        self.assertTrue(m["send_disabled"])
        self.assertTrue(m["cancel_disabled"])
        self.assertEqual(m["rest_status"], "Resting to save power. Calls and messages still work.")
        self.assertTrue(m["plan_enabled"])
        self.assertTrue(m["why_enabled"])
        self.assertTrue(m["clear_enabled"])
        self.assertEqual(m["untab"], [])

    def test_keyboard_guard_catches_enabled_negative_tabindex_while_critical(self):
        m = self.critical_keyboard_negative_control
        self.assertEqual(m["fixture_mode"], "CRITICAL")
        self.assertEqual(m["untab"], ["#keyboard-negative-control"])
        self.assertNotIn("#keyboard-negative-control", m["disabled"])


if __name__ == "__main__":
    unittest.main()
