# SPDX-License-Identifier: GPL-3.0-or-later
# UXP13: shallow Settings (Device type, Display, About) and a setup wizard that cannot trap.
# Defects found in the setup wizard and fixed minimally in src/opos/setup/wizard_page.py:
#  1. Validation reasons only appeared at the top of the page after Apply on the last step, far from the
#     field, and no field was ever marked aria-invalid. Next on Daily goal and Security now asks the existing
#     /setup/api/validate route and shows the reason under the field with aria-invalid; if the check cannot
#     run, Next still moves on. The check gives up after 4 s and moves on; a late answer after Back is ignored.
#  2. A reload forgot the chosen device profile. The choice is now kept in sessionStorage for this wizard tab
#     and re-applied on load through the existing /setup/api/device route. Load waits for it, and the select only
#     keeps a profile once its device has loaded (a failed load puts the select back), so shown = what Apply sends.
#     While a device loads, Next and Apply are disabled; a load over 8 s counts as failed. The rest of the saved
#     choices (incl. Helper tools) render before the restore starts, so a stalled restore cannot drop them.
#  3. The profile deadline missed stalled response bodies, and stale requests could mutate device state.
#     The deadline now covers body reads; only the latest selection can update state, including Use detected.
#  4. (phone-b.css) Home, All apps and device-type text used px under ID rules, so Text size missed them; now rem.
# Checked and not defects: steps 2-7 have Back, steps 1-6 have Next, step 7 has Apply (step 1 has nothing to go
# back to); `opos run` only opens the wizard while the saved config is missing, unreadable or not applied.
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opos.setup import config
from opos.setup import wizard_page
from opos.setup.cli import main as cli_main
from opos.web import page

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"


def _settings(out):
    s = out.index('<section id="settings"')
    e = out.index('<p id="about">', s)
    return out[s:out.index("</section></section>", e) + len("</section></section>")]


def _css():
    return re.sub(r"/\*.*?\*/", "", (STATIC / "phone-b.css").read_text(encoding="utf-8"), flags=re.S)


class UXP13SettingsStatic(unittest.TestCase):
    def setUp(self):
        self.out = page("t" * 32, "n")
        self.s = _settings(self.out)

    def test_three_labelled_sections(self):
        self.assertIn('<fieldset id="ff"><legend>Device type</legend>', self.s)
        self.assertIn('<section aria-labelledby="set-display"><h3 id="set-display">Display</h3>', self.s)
        self.assertIn('<section aria-labelledby="set-about"><h3 id="set-about">About</h3>', self.s)
        self.assertEqual(self.s.count("<section aria-labelledby="), 2)
        self.assertEqual(self.s.count("<fieldset"), 1)
        self.assertLess(self.s.index('id="ff"'), self.s.index('id="set-display"'))
        self.assertLess(self.s.index('id="set-display"'), self.s.index('id="set-about"'))

    def test_textsize_select_is_labelled(self):
        self.assertIn('<label for="textsize">Text size</label><select id="textsize">', self.s)
        opts = re.findall(r'<option value="([^"]+)">([^<]+)</option>', self.s)
        self.assertEqual(opts, [("s", "Small"), ("n", "Normal"), ("l", "Large"), ("xl", "Largest")])
        self.assertNotIn("selected", self.s)

    def test_every_control_has_visible_label(self):
        for m in re.finditer(r'<input type="radio" name="ff" value="[^"]+">([^<]+)</label>', self.s):
            self.assertTrue(m.group(1).strip())
        self.assertEqual(len(re.findall(r'<input type="radio" name="ff"', self.s)), 5)
        # Text size plus the Top bar choice; each sits inside or after its visible label.
        self.assertEqual(len(re.findall(r"<select ", self.s)), 2)
        self.assertIn('<label>Top bar<select id="topbar">', self.s)
        self.assertIn('<label><input id="night" type="checkbox" role="switch">Dim at night</label>', self.s)
        for text, ident in (("Page brightness", "bright"), ("Volume (practice only; sound is not changed)", "vol")):
            self.assertIn('<label>%s<input id="%s" type="range"' % (text, ident), self.s)

    def test_about_default_and_quiet_words(self):
        self.assertIn('<p id="about">Old Phone OS Interface</p>', self.s)
        text = re.sub(r"<[^>]+>", " ", self.s)
        for w in ("AI", "model", "governor", "assistant"):
            self.assertNotRegex(text, r"\b%s\b" % w)

    def test_no_gesture_only_entry(self):
        self.assertIn('<button type="button" data-open="settings">Settings</button>', self.out)
        self.assertIn('<button id="back" type="button" hidden>Home</button>', self.out)
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        self.assertNotRegex(js, r"contextmenu|touchstart|pointerdown|longpress")

    def test_text_size_css(self):
        css = _css()
        for k, pct in (("n", 100), ("l", 125), ("xl", 150)):
            self.assertIn('html[data-text="%s"]{font-size:%d%%}' % (k, pct), css)
        self.assertIn("html[data-text] label,html[data-text] input,html[data-text] button,html[data-text] select{font-size:1rem}", css)
        # ID rules beat the html[data-text] rule, so Home, All apps and device-type text must be rem themselves.
        for sel in ("#home button", "#home #allapps", "#apps-list button", "#phone[data-app] #ff label", "#settings h2", "#settings h3"):
            m = re.search(re.escape(sel) + r"\{([^}]*)\}", css)
            self.assertIsNotNone(m, sel)
            self.assertRegex(m.group(1), r"font-size:[\d.]+rem", sel)
        self.assertNotRegex(css, r"#(home|apps-list|ff|settings)\b[^{}]*\{[^}]*font-size:\d+px")

    def test_hit_areas(self):
        css = _css()
        self.assertIn("#phone[data-app] #ff label{display:flex;align-items:center;gap:12px;min-height:48px;font-size:1.125rem;margin:0 0 8px}", css)
        self.assertRegex(css, r"#textsize\{[^}]*min-height:48px")
        self.assertRegex(css, r"label\[for=textsize\]\{[^}]*margin:0 0 8px")
        self.assertRegex(css, r'#phone\[data-app\]:not\(\[data-app="home"\]\) #back:not\(\[hidden\]\)\{[^}]*min-height:48px')
        self.assertRegex(css, r"#settings section\{margin-top:16px\}")


# Runs phone-b.js in a node vm against a tiny fake DOM; prints one JSON result per scenario.
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
function el(extra) {
  const e = {hidden: true, dataset: {}, textContent: '', value: '', l: {}, checked: false,
    addEventListener(t, f) { this.l[t] = f; }, focus() { focused = this; }, querySelector() { return null; }};
  return Object.assign(e, extra || {});
}
let focused = null;
function run(o) {
  const store = {}; if (o.stored !== undefined) store.opos_text = o.stored;
  const E = {phone: el(), home: el({querySelector: () => null}), back: el(), settings: el(), ff: el(),
    'ff-detected': el(), 'ff-saved': el(), textsize: el(), about: el({textContent: 'Old Phone OS Interface'}),
    'sb-engine': el({textContent: o.engine || ''})};
  if (o.noSelect) delete E.textsize;
  const root = {dataset: {}};
  const ls = {getItem(k) { if (o.blocked) throw Error('denied'); return k in store ? store[k] : null; },
    setItem(k, v) { if (o.blocked) throw Error('denied'); store[k] = String(v); }, removeItem(k) { delete store[k]; }};
  const win = {addEventListener() {}, matchMedia: () => ({matches: false}), scrollTo() {}};
  const c = {document: {getElementById: i => E[i] || null, documentElement: root}, window: win, localStorage: ls,
    location: {search: ''}, Promise};
  vm.runInNewContext(src, c);
  const r = {text: root.dataset.text === undefined ? null : root.dataset.text, select: E.textsize ? E.textsize.value : null};
  if (o.change !== undefined) {
    E.textsize.value = o.change; E.textsize.l.change({target: E.textsize});
    r.textAfter = root.dataset.text; r.selectAfter = E.textsize.value;
    r.store = store.opos_text === undefined ? null : store.opos_text;
  }
  if (o.nav) {
    win.oposUI.openApp('settings');
    r.app = E.phone.dataset.app; r.settingsShown = !E.settings.hidden; r.backShown = !E.back.hidden;
    r.backHasClick = typeof E.back.l.click === 'function';
    E.back.l.click();
    r.appAfterBack = E.phone.dataset.app; r.settingsAfterBack = !E.settings.hidden; r.homeAfterBack = !E.home.hidden;
  }
  (win.oposHooks || []).forEach(f => { try { f({}); } catch (e) {} });
  r.about = E.about.textContent;
  return r;
}
const S = {
  empty: {}, restore: {stored: 'xl'}, invalid: {stored: '"><b>'}, save: {change: 'l'},
  badChange: {stored: 'l', change: 'huge'}, blocked: {stored: 'xl', blocked: true, change: 'l'},
  nav: {nav: true}, engine: {engine: 'on this phone'}, noSelect: {noSelect: true, stored: 'l'},
};
const out = {}; for (const k in S) out[k] = run(S[k]);
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node missing")
class UXP13SettingsRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            proc = subprocess.run(["node", str(harness), str(STATIC / "phone-b.js")],
                                  capture_output=True, text=True, timeout=30)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_default_is_normal(self):
        self.assertEqual(self.r["empty"]["text"], "n")
        self.assertEqual(self.r["empty"]["select"], "n")

    def test_restores_saved_size(self):
        self.assertEqual(self.r["restore"]["text"], "xl")
        self.assertEqual(self.r["restore"]["select"], "xl")

    def test_invalid_saved_value_means_normal(self):
        self.assertEqual(self.r["invalid"]["text"], "n")

    def test_change_applies_and_persists(self):
        r = self.r["save"]
        self.assertEqual(r["textAfter"], "l")
        self.assertEqual(r["store"], "l")

    def test_unknown_change_falls_back_to_normal(self):
        r = self.r["badChange"]
        self.assertEqual(r["text"], "l")
        self.assertEqual(r["textAfter"], "n")
        self.assertEqual(r["selectAfter"], "n")
        self.assertEqual(r["store"], "n")

    def test_denied_storage_still_applies(self):
        r = self.r["blocked"]
        self.assertEqual(r["text"], "n")
        self.assertEqual(r["textAfter"], "l")
        self.assertEqual(r["store"], "xl")

    def test_missing_select_still_applies_saved_size(self):
        self.assertEqual(self.r["noSelect"]["text"], "l")

    def test_settings_back_is_real(self):
        r = self.r["nav"]
        self.assertEqual(r["app"], "settings")
        self.assertTrue(r["settingsShown"])
        self.assertTrue(r["backShown"])
        self.assertTrue(r["backHasClick"])
        self.assertEqual(r["appAfterBack"], "home")
        self.assertFalse(r["settingsAfterBack"])
        self.assertTrue(r["homeAfterBack"])

    def test_about_uses_helper_label_or_name(self):
        self.assertEqual(self.r["empty"]["about"], "Old Phone OS Interface")
        self.assertEqual(self.r["engine"]["about"], "Old Phone OS Interface. Helper set to on this phone")


class UXP13Wizard(unittest.TestCase):
    def setUp(self):
        self.html = wizard_page.page("t" * 32, "n1")
        self.steps = re.findall(r'<section class="step panel" id="s(\d)"[^>]*>(.*?)</section>', self.html, re.S)

    def test_next_and_back_buttons(self):
        self.assertEqual([n for n, _ in self.steps], [str(i) for i in range(1, 8)])
        for n, body in self.steps:
            n = int(n)
            if n > 1:
                self.assertIn('<button type="button" data-back>Back</button>', body, n)
            if n < 7:
                self.assertIn('<button type="button" data-next>Next</button>', body, n)
        self.assertIn('<button type="button" id="apply">Apply</button>', self.steps[6][1])
        self.assertIn('qa("[data-back]").forEach(b=>b.addEventListener("click",()=>show(cur-1)))', self.html)
        self.assertIn('qa("[data-next]").forEach(b=>b.addEventListener("click",next))', self.html)

    def test_field_errors_sit_next_to_field(self):
        for fid, step in (("goal-time", "3"), ("ssh-key", "5")):
            body = dict(self.steps)[step]
            self.assertRegex(body, r'id="%s"[^>]*aria-describedby="%s-err"' % (fid, fid))
            self.assertRegex(body, r'id="%s"[^>]*>(</textarea>)?\n<p id="%s-err" class="ferr" hidden></p>' % (fid, fid))
        self.assertIn('f.setAttribute("aria-invalid","true")', self.html)
        self.assertIn('f.removeAttribute("aria-invalid")', self.html)
        self.assertIn("/setup/api/validate", self.html)
        self.assertIn(".ferr{color:#e5484d", self.html)

    def test_client_patterns_match_server_errors(self):
        cfg = config.default_config()
        cfg["goal"]["time"] = ""
        goal = [e for e in config.validate(cfg) if re.match(r"^goal", e)]
        self.assertTrue(goal)
        cfg = config.default_config()
        cfg["security"]["ssh_public_key"] = "not a key"
        ssh = [e for e in config.validate(cfg) if "ssh_public_key" in e]
        self.assertTrue(ssh)
        self.assertIn('2:["goal-time",/^goal/', self.html)
        self.assertIn('4:["ssh-key",/ssh_public_key/', self.html)

    def test_profile_choice_survives_reload(self):
        self.assertIn('sessionStorage.setItem("opos_setup_profile",v)', self.html)
        load = self.html[self.html.index("async function load(){"):self.html.index("async function changeProfile(")]
        self.assertIn('sessionStorage.getItem("opos_setup_profile")', load)
        self.assertIn("changeProfile();", load)
        self.assertLess(load.index("fillProfiles()"), load.index("opos_setup_profile"))

    def test_load_renders_choices_before_profile_restore(self):
        load = self.html[self.html.index("async function load(){"):self.html.index("function busy(")]
        self.assertNotIn("await changeProfile", load)
        self.assertLess(load.index("renderAi();"), load.index("changeProfile();"))

    def test_wizard_only_while_setup_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            env = {"OPOS_CONFIG_DIR": str(Path(td) / "cfg")}
            with patch("opos.setup.server.main", return_value=7) as srv:
                self.assertEqual(cli_main(["run"], env=env, home=home, platform="linux", out=io.StringIO()), 7)
                cfg = config.default_config()
                config.save(cfg, config.config_path(env, "linux"))
                self.assertEqual(cli_main(["run"], env=env, home=home, platform="linux", out=io.StringIO()), 7)
                cfg["applied"] = True
                config.save(cfg, config.config_path(env, "linux"))
                out = io.StringIO()
                self.assertEqual(cli_main(["run"], env=env, home=home, platform="linux", out=out), 0)
                self.assertIn("already set up", out.getvalue())
                self.assertEqual(srv.call_count, 2)


# Runs the wizard's real next() and changeProfile() (sliced from the template) in a node vm with stub DOM and fetch.
WIZ_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const tick = () => new Promise(r => setImmediate(r));
function make(fetchImpl) {
  const els = {}, store = {};
  const el = id => els[id] || (els[id] = {id, value: '', hidden: true, textContent: '', attrs: {}, focused: false,
    setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    focus() { this.focused = true; }, addEventListener() {}, disabled: false});
  const btns = ['next1', 'apply', 'retry'].map(id => el(id));
  const c = {cur: 2, shown: [], errs: [], S: {device: {id: 'det'}}, window: {}, JSON, Promise, store,
    q: s => el(s.replace(/^#/, '')), qa: () => btns, H: () => ({}), collect: () => ({}), showErr: m => c.errs.push(m),
    renderDevice: () => {}, fetch: fetchImpl,
    setTimeout: f => setImmediate(f),
    sessionStorage: {setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; }}};
  c.show = i => { c.cur = i; c.shown.push(i); };
  vm.createContext(c); vm.runInContext(src + '\nthis.next=next;this.changeProfile=changeProfile;', c);
  return {c, el};
}
const ok = j => Promise.resolve({ok: true, json: async () => j});
(async () => {
  const out = {};
  let w = make(() => new Promise(() => {}));
  await w.c.next(); await tick();
  out.stall = {cur: w.c.cur, shown: w.c.shown, invalid: w.el('goal-time').attrs['aria-invalid'] || null};
  let release; w = make(() => new Promise(r => { release = r; }));
  w.c.setTimeout = () => {};
  let p = w.c.next(); await tick(); w.c.cur = 1; release({ok: true, json: async () => ({errors: ['goal.time must be HH:MM 24-hour']})}); await p;
  out.backDuring = {cur: w.c.cur, shown: w.c.shown, focused: w.el('goal-time').focused, err: w.el('goal-time-err').textContent};
  w = make(() => ok({errors: ['goal.time must be HH:MM 24-hour']})); w.c.setTimeout = () => {};
  await w.c.next();
  out.bad = {cur: w.c.cur, shown: w.c.shown, focused: w.el('goal-time').focused, invalid: w.el('goal-time').attrs['aria-invalid'],
    err: w.el('goal-time-err').textContent, errHidden: w.el('goal-time-err').hidden};
  w = make(() => ok({errors: []})); w.c.setTimeout = () => {};
  await w.c.next();
  out.good = {cur: w.c.cur, invalid: w.el('goal-time').attrs['aria-invalid'] || null};
  w = make(() => Promise.reject(new Error('offline')));
  w.el('profile').value = 'duo'; await w.c.changeProfile();
  out.profileFail = {select: w.el('profile').value, store: w.c.store.opos_setup_profile || null, dev: w.c.window._curDev || null, errs: w.c.errs};
  w = make(() => ok({id: 'duo'}));
  w.el('profile').value = 'duo'; await w.c.changeProfile();
  out.profileOk = {select: w.el('profile').value, store: w.c.store.opos_setup_profile || null, dev: w.c.window._curDev};
  w = make(() => ok({id: 'duo'}));
  w.el('profile').value = 'duo'; p = w.c.changeProfile(); w.el('profile').value = ''; await p;
  out.profileStale = {select: w.el('profile').value, store: w.c.store.opos_setup_profile || null, dev: w.c.window._curDev || null};
  let late; w = make(() => new Promise(() => {})); w.c.setTimeout = f => { late = f; };
  w.c.window._curDev = {id: 'det'}; w.el('profile').value = 'duo'; p = w.c.changeProfile(); await tick();
  out.pending = {select: w.el('profile').value, dev: w.c.window._curDev, disabled: w.el('next1').disabled && w.el('apply').disabled};
  late(); await p;
  out.timedOut = {select: w.el('profile').value, dev: w.c.window._curDev, disabled: w.el('next1').disabled, errs: w.c.errs};
  const snapshot = w => ({select: w.el('profile').value, pid: w.c.window._pid || '',
    store: w.c.store.opos_setup_profile || null, dev: w.c.window._curDev,
    disabled: ['next1', 'apply', 'retry'].some(id => w.el(id).disabled), errs: [...w.c.errs]});
  for (const kind of ['success', 'errorJson', 'errorText']) {
    let finish, deadline;
    const body = new Promise(r => { finish = r; });
    w = make(() => Promise.resolve({ok: kind === 'success',
      json: () => kind === 'errorText' ? Promise.reject(Error('bad json')) : body,
      text: () => body}));
    w.c.setTimeout = f => { deadline = f; };
    w.c.window._curDev = {id: 'det'}; w.el('profile').value = 'duo';
    p = w.c.changeProfile(); await tick(); deadline(); await tick();
    out[kind + 'BodyTimeout'] = snapshot(w);
    finish(kind === 'success' ? {id: 'duo'} : {error: 'late error'}); await p; await tick();
    out[kind + 'BodyLate'] = snapshot(w);
  }
  for (const fail of [false, true]) {
    const requests = [];
    w = make(() => new Promise((resolve, reject) => requests.push({resolve, reject})));
    w.c.setTimeout = () => {};
    w.c.window._curDev = {id: 'det'};
    w.el('profile').value = 'duo'; const a = w.c.changeProfile();
    w.el('profile').value = 'other'; const b = w.c.changeProfile();
    w.el('profile').value = 'duo'; const c = w.c.changeProfile();
    if (fail) requests[0].reject(Error('stale failure'));
    else requests[0].resolve({ok: true, json: async () => ({id: 'duo'})});
    await a;
    out['overlapPending' + fail] = snapshot(w);
    requests[2].reject(Error('latest failure')); await c;
    requests[1].resolve({ok: true, json: async () => ({id: 'other'})}); await b;
    out['overlapFailed' + fail] = snapshot(w);
  }
  for (const fail of [false, true]) {
    let resolve, reject;
    w = make(() => new Promise((r, j) => { resolve = r; reject = j; }));
    w.c.setTimeout = () => {};
    w.c.window._curDev = {id: 'saved'}; w.c.keepPid('saved');
    w.el('profile').value = 'duo'; p = w.c.changeProfile();
    w.el('profile').value = ''; await w.c.changeProfile();
    out['detectedPending' + fail] = snapshot(w);
    if (fail) reject(Error('stale failure'));
    else resolve({ok: true, json: async () => ({id: 'duo'})});
    await p;
    out['detectedDone' + fail] = snapshot(w);
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node missing")
class UXP13WizardRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = wizard_page._TEMPLATE
        src = s[s.index("async function errMsg("):s.index("function updMeter(")] + s[s.index("function busy("):s.index("let pollId")] + s[s.index("const FIELDS="):s.index("Object.values(FIELDS)")]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "h.js").write_text(WIZ_HARNESS, encoding="utf-8")
            (Path(tmp) / "w.js").write_text(src, encoding="utf-8")
            proc = subprocess.run(["node", str(Path(tmp) / "h.js"), str(Path(tmp) / "w.js")],
                                  capture_output=True, text=True, timeout=30)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_stalled_check_still_moves_on(self):
        self.assertEqual(self.r["stall"], {"cur": 3, "shown": [3], "invalid": None})

    def test_late_answer_after_back_is_ignored(self):
        self.assertEqual(self.r["backDuring"], {"cur": 1, "shown": [], "focused": False, "err": ""})

    def test_bad_field_stays_and_marks_field(self):
        r = self.r["bad"]
        self.assertEqual((r["cur"], r["shown"]), (2, []))
        self.assertTrue(r["focused"])
        self.assertEqual(r["invalid"], "true")
        self.assertEqual(r["err"], "Enter a time like 23:00.")
        self.assertFalse(r["errHidden"])

    def test_good_field_moves_on(self):
        self.assertEqual(self.r["good"], {"cur": 3, "invalid": None})

    def test_failed_profile_reverts_select(self):
        r = self.r["profileFail"]
        self.assertEqual((r["select"], r["store"], r["dev"]), ("", None, None))
        self.assertEqual(r["errs"], ["offline"])

    def test_loaded_profile_is_kept(self):
        self.assertEqual(self.r["profileOk"], {"select": "duo", "store": "duo", "dev": {"id": "duo"}})

    def test_pending_profile_blocks_next_and_apply(self):
        self.assertEqual(self.r["pending"], {"select": "duo", "dev": {"id": "det"}, "disabled": True})

    def test_stalled_profile_times_out_and_reverts(self):
        self.assertEqual(self.r["timedOut"], {"select": "", "dev": {"id": "det"}, "disabled": False,
                                              "errs": ["The device did not load. Try again."]})

    def test_response_body_deadline_releases_controls_and_ignores_late_body(self):
        expected = {"select": "", "pid": "", "store": None, "dev": {"id": "det"},
                    "disabled": False, "errs": ["The device did not load. Try again."]}
        for kind in ("success", "errorJson", "errorText"):
            with self.subTest(kind=kind):
                self.assertEqual(self.r[kind + "BodyTimeout"], expected)
                self.assertEqual(self.r[kind + "BodyLate"], expected)

    def test_overlapping_requests_only_allow_latest_to_mutate(self):
        for fail in ("false", "true"):
            with self.subTest(stale_failure=fail):
                self.assertEqual(self.r["overlapPending" + fail], {
                    "select": "duo", "pid": "", "store": None, "dev": {"id": "det"},
                    "disabled": True, "errs": []})
                self.assertEqual(self.r["overlapFailed" + fail], {
                    "select": "", "pid": "", "store": None, "dev": {"id": "det"},
                    "disabled": False, "errs": ["latest failure"]})

    def test_use_detected_invalidates_pending_success_and_failure(self):
        expected = {"select": "", "pid": "", "store": None, "dev": {"id": "det"},
                    "disabled": False, "errs": []}
        for fail in ("false", "true"):
            with self.subTest(stale_failure=fail):
                self.assertEqual(self.r["detectedPending" + fail], expected)
                self.assertEqual(self.r["detectedDone" + fail], expected)

    def test_stale_profile_answer_is_dropped(self):
        self.assertEqual(self.r["profileStale"], {"select": "", "store": None, "dev": None})


if __name__ == "__main__":
    unittest.main()
