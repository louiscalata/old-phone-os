# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from opos.setup import config
from opos.web import page

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"


class UXP05Settings(unittest.TestCase):
    def test_settings_markup(self):
        out = page("t" * 32, "n")
        self.assertIn('id="settings"', out)
        self.assertIn('<section id="settings" class="box" hidden>', out)
        self.assertIn('id="ff"', out)
        self.assertIn('id="ff-detected"', out)
        self.assertIn('id="ff-saved"', out)
        values = re.findall(r'<input type="radio" name="ff" value="([^"]+)">', out)
        # UXP21 renamed Slide-out and added Tablet.
        self.assertEqual(values, ["auto", "slab", "foldable", "slideout", "tablet"])
        for label in ("Auto", "Slab", "Foldable", "Slide-out", "Tablet"):
            self.assertIn(">%s</label>" % label, out)

    def test_whitelist_precedes_assignment(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        self.assertLess(js.index("['slab','foldable','slideout','tablet']"), js.index("dataset.ff="))
        self.assertIn("opos_ff", js)
        self.assertIn("try{", js)
        self.assertIn("Could not save", js)
        self.assertIn("delete d.dataset.ff", js)
        self.assertNotIn("location.reload", js)

    def test_no_server_or_config_change(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        # UXP31 adds requests in a separate Helper module; Settings stays local.
        js = js.split("})()}catch(e){}", 1)[0]
        self.assertNotIn("fetch(", js)
        self.assertNotRegex(js, r"\bapi\(")
        cfg = config.default_config()
        self.assertEqual(config.validate(cfg), [])
        cfg["opos_ff"] = "slab"
        errors = config.validate(cfg)
        self.assertTrue(errors)
        self.assertIn("unknown key: opos_ff", errors)

    def test_save_feedback_is_announced(self):
        self.assertIn('<p id="ff-saved" role="status"></p>', page("t" * 32, "n"))


# Runs phone-b.js in a node vm against a tiny fake DOM; prints one JSON result per scenario.
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
function el(extra) {
  const e = {hidden: true, dataset: {}, textContent: '', l: {}, checked: false,
    addEventListener(t, f) { this.l[t] = f; }, focus() {}, querySelector() { return null; }};
  return Object.assign(e, extra || {});
}
function run(o) {
  const store = {}; if (o.stored !== undefined) store.opos_ff = o.stored;
  const radios = {auto: el(), slab: el(), foldable: el(), slideout: el(), tablet: el()};
  const E = {phone: el(), home: el(), back: el(), settings: el(), 'ff-detected': el(), 'ff-saved': el(),
    ff: el({querySelector: s => radios[(s.match(/value="([^"]*)"/) || [])[1]] || null})};
  const root = {dataset: {}};
  const doc = {getElementById: i => E[i] || null};
  if (o.breakSettings) Object.defineProperty(doc, 'documentElement', {get() { throw Error('boom'); }});
  else doc.documentElement = root;
  const ls = {getItem(k) { if (o.blocked) throw Error('denied'); return k in store ? store[k] : null; },
    setItem(k, v) { if (o.blocked) throw Error('denied'); store[k] = String(v); },
    removeItem(k) { if (o.blocked) throw Error('denied'); delete store[k]; }};
  const win = {addEventListener() {}, matchMedia: () => ({matches: false}), scrollTo() {}};
  const c = {document: doc, window: win, localStorage: ls, location: {search: ''}, Promise};
  vm.runInNewContext(src, c);
  const r = {checked: Object.keys(radios).filter(k => radios[k].checked),
    ff: root.dataset.ff === undefined ? null : root.dataset.ff, detected: E['ff-detected'].textContent};
  if (o.change !== undefined) {
    E.ff.l.change({target: {value: o.change}});
    r.ffAfter = root.dataset.ff === undefined ? null : root.dataset.ff;
    r.hasAttr = 'ff' in root.dataset; r.saved = E['ff-saved'].textContent;
    r.store = store.opos_ff === undefined ? null : store.opos_ff;
  }
  r.openApp = typeof (win.oposUI && win.oposUI.openApp);
  if (r.openApp === 'function') {
    win.oposUI.openApp('settings');
    r.settingsShown = !E.settings.hidden; r.app = E.phone.dataset.app;
    win.oposUI.openApp('home');
    r.settingsHiddenAtHome = E.settings.hidden; r.homeShown = !E.home.hidden;
  }
  return r;
}
const S = {
  restore: {stored: 'foldable'},
  invalid: {stored: 'x" onload=1'},
  empty: {},
  save: {change: 'slideout'},
  toAuto: {stored: 'slab', change: 'auto'},
  // Tablet is valid since UXP21; use an unsupported value to test rejection.
  badChange: {stored: 'slab', change: 'unknown'},
  blocked: {stored: 'slab', blocked: true, change: 'slideout'},
  broken: {breakSettings: true},
};
const out = {}; for (const k in S) out[k] = run(S[k]);
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node missing")
class UXP05SettingsRuntime(unittest.TestCase):
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

    def test_restore_saved_value(self):
        r = self.r["restore"]
        self.assertEqual(r["ff"], "foldable")
        self.assertEqual(r["checked"], ["foldable"])
        self.assertEqual(r["detected"], "Auto looks like: slab")

    def test_invalid_or_missing_value_means_auto(self):
        for key in ("invalid", "empty"):
            self.assertIsNone(self.r[key]["ff"], key)
            self.assertEqual(self.r[key]["checked"], ["auto"], key)

    def test_change_applies_and_saves(self):
        r = self.r["save"]
        self.assertEqual(r["ffAfter"], "slideout")
        self.assertEqual(r["store"], "slideout")
        self.assertEqual(r["saved"], "Saved")

    def test_auto_removes_attribute_and_storage(self):
        r = self.r["toAuto"]
        self.assertEqual(r["ff"], "slab")
        self.assertFalse(r["hasAttr"])
        self.assertIsNone(r["store"])
        self.assertEqual(r["saved"], "Saved")

    def test_unknown_change_value_is_not_applied(self):
        r = self.r["badChange"]
        self.assertFalse(r["hasAttr"])
        self.assertIsNone(r["store"])

    def test_denied_storage(self):
        r = self.r["blocked"]
        self.assertIsNone(r["ff"])
        self.assertEqual(r["checked"], ["auto"])
        self.assertEqual(r["ffAfter"], "slideout")
        self.assertEqual(r["saved"], "Could not save")

    def test_settings_navigation(self):
        r = self.r["restore"]
        self.assertTrue(r["settingsShown"])
        self.assertEqual(r["app"], "settings")
        self.assertTrue(r["settingsHiddenAtHome"])
        self.assertTrue(r["homeShown"])

    def test_launcher_survives_broken_settings_init(self):
        r = self.r["broken"]
        self.assertEqual(r["openApp"], "function")
        self.assertTrue(r["settingsShown"])
        self.assertTrue(r["homeShown"])


if __name__ == "__main__":
    unittest.main()
