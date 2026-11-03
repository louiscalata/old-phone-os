# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from opos.web import page

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"
WHITELIST = "['home','phone','messages','helper','settings']"


def _css_rules(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", text)


class UXP04Launcher(unittest.TestCase):
    def setUp(self):
        self.out = page("t" * 32, "n")

    def test_launcher_tiles_exact(self):
        opens = re.findall(r'data-open="([^"]+)"', self.out)
        self.assertEqual(set(opens), {"phone", "messages", "helper", "settings"})
        self.assertEqual(len(opens), 4)
        for name, label in [("phone", "Phone"), ("messages", "Messages"), ("helper", "Helper"), ("settings", "Settings")]:
            # Jaryo gives Phone/Messages medium tiles and Helper a small tile.
            size = {'phone': 'medium', 'messages': 'medium', 'helper': 'small'}.get(name)
            # Canonical and setup-Wi-Fi markup order attributes differently.
            # Compare exact attributes and labels without prescribing their order.
            tiles = [(attrs, text) for attrs, text in
                     re.findall(r'<button\b([^>]*)>([^<]*)</button>', self.out)
                     if f'data-open="{name}"' in attrs.split()]
            self.assertEqual(len(tiles), 1)
            attrs, text = tiles[0]
            expected = ['type="button"', f'data-open="{name}"']
            if size:
                expected.append(f'data-size="{size}"')
            self.assertEqual(sorted(attrs.split()), sorted(expected))
            self.assertEqual(text, label)
        self.assertIn('<nav id="home" aria-label="Apps" hidden>', self.out)
        self.assertIn('<button id="back" type="button" hidden>Home</button>', self.out)

    def test_every_existing_id_survives(self):
        ids = set()
        for name in ("body-4.html", "body-1.html"):
            ids.update(re.findall(r'id="([^"]+)"', (STATIC / name).read_text(encoding="utf-8")))
        self.assertIn("helper", ids)
        self.assertIn("meter", ids)
        for i in ids:
            self.assertIn('id="%s"' % i, self.out, i)

    def test_no_data_app_hides_nothing(self):
        rules = _css_rules((STATIC / "phone-b.css").read_text(encoding="utf-8"))
        self.assertTrue(rules)
        hiding = 0
        for sel, decl in rules:
            d = decl.replace(" ", "")
            if re.search(r"display:none|visibility:hidden|opacity:0(?![.\d])", d):
                hiding += 1
                for s in sel.split(","):
                    # UXP31 hides Ask only after its replacement has been built.
                    self.assertTrue(s.strip().startswith("#phone[data-app") or
                                    s.strip() == "#ask[data-hs] #askbtn", s)
            # UXP21/22 use flex/grid without data-app; only hiding needs the guard.
        self.assertGreaterEqual(hiding, 5)

    def test_navigation_is_local_only(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        # UXP31 requests belong to Helper, not the launcher/navigation modules.
        js = js.split("// UXP31:", 1)[0]
        self.assertNotIn("fetch(", js)
        self.assertNotRegex(js, r"\bapi\(")
        self.assertIn(WHITELIST, js)
        self.assertIn("dataset.app", js)
        self.assertNotIn("location.reload", js)
        self.assertNotIn("innerHTML", js)

    def test_open_app_whitelist_before_use(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        check = js.index("APPS.indexOf(name)<0")
        self.assertLess(js.index(WHITELIST), check)
        self.assertLess(check, js.index("dataset.app="))
        self.assertIn("ui.openApp('home')", js)

    def test_status_notice_contacts_stay_visible(self):
        hidden = set()
        for sel, decl in _css_rules((STATIC / "phone-b.css").read_text(encoding="utf-8")):
            if "display:none" in decl.replace(" ", ""):
                hidden.update(s.strip() for s in sel.split(","))
        self.assertFalse([s for s in hidden if s.endswith("#notice")])
        for app in ("phone", "messages"):
            for el in ("#number", "#controls"):
                self.assertNotIn('#phone[data-app="%s"] %s' % (app, el), hidden)
                self.assertNotIn("#phone[data-app] %s" % el, hidden)
        # Phone is the contacts app and Messages the inbox: contacts stay only in Phone, and the Home status box
        # (#screen, repeated on Home and in the battery panel) leaves every app screen. No rule hides them without data-app.
        self.assertIn('#phone[data-app]:not([data-app="phone"]) #contacts', hidden)
        self.assertIn('#phone[data-app]:not([data-app="home"]) #screen', hidden)
        for el in ("#screen", "#contacts"):
            self.assertNotIn("#phone[data-app] %s" % el, hidden)
        self.assertNotIn('#phone[data-app="phone"] #contacts', hidden)


NODE = shutil.which("node")
HARNESS = r"""
const fs=require('fs'),vm=require('vm');
function el(id,hidden){return {id,hidden:!!hidden,dataset:{},l:{},focused:0,
 addEventListener(t,f){this.l[t]=f},focus(){this.focused++;active=this},querySelector(){return null}}}
let active=null;const E={};
['phone','back','ask','q','number','msg','answer'].forEach(i=>E[i]=el(i));
E.back.hidden=true;E.ask.hidden=true;E.home=el('home',true);
E.number.value='555';E.msg.value='hi';E.answer.textContent='old';
const tiles={};['phone','messages','helper','settings'].forEach(n=>{tiles[n]=el('t-'+n);tiles[n].dataset.open=n});
E.home.querySelector=s=>{const m=/data-open="?(\w+)/.exec(s);return m?tiles[m[1]]:null};
const mode=process.argv[3];let calls=0,net=0,unhandled=0;
process.on('unhandledRejection',()=>{unhandled++});
if(mode==='focus-throws')E.q.focus=()=>{throw Error('focus failed')};
const c={window:{addEventListener(){},scrollTo(){}},document:{getElementById:i=>E[i]||null},
 // UXP07 defers Helper completion with the browser timer; provide it in the VM.
 location:{search:process.argv[4]||''},Promise,setTimeout,fetch(){net++},api(){net++}};
if(mode!=='none')c.toggleVis=function(){calls++;if(mode==='reject')return Promise.reject(Error('x'));
 if(mode==='ok'||mode==='focus-throws')E.ask.hidden=false;return Promise.resolve()};
c.window.window=c.window;vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),c);
const ui=c.window.oposUI,out={};
c.window.oposHooks.forEach(f=>f({}));c.window.oposHooks.forEach(f=>f({}));
out.start=E.phone.dataset.app;out.settingsTile=tiles.settings.hidden;
ui.openApp('phone');out.phone=[E.phone.dataset.app,E.home.hidden,E.back.hidden,active&&active.id];
ui.openApp('home');out.home=[E.phone.dataset.app,E.home.hidden,E.back.hidden,active&&active.id];
ui.openApp('settings');out.settings=E.phone.dataset.app;
ui.openApp('bogus');out.bogus=E.phone.dataset.app;
E.home.l.click({target:{closest(){return tiles.helper}}});
setTimeout(()=>{c.window.oposHooks.forEach(f=>f({}));out.helper=[E.phone.dataset.app===undefined?null:E.phone.dataset.app,E.home.hidden,E.back.hidden,active&&active.id];
 out.calls=calls;out.net=net;out.unhandled=unhandled;out.drafts=[E.number.value,E.msg.value,E.answer.textContent];
 E.back.l.click();out.back=[E.phone.dataset.app,E.home.hidden,E.back.hidden,active&&active.id];
 console.log(JSON.stringify(out))},20);
"""


@unittest.skipUnless(NODE, "node not installed")
class UXP04Runtime(unittest.TestCase):
    def run_js(self, mode, search=""):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / "h.js"
            h.write_text(HARNESS, encoding="utf-8")
            r = subprocess.run([NODE, str(h), str(STATIC / "phone-b.js"), mode, search],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_navigation_and_focus(self):
        o = self.run_js("ok")
        self.assertEqual(o["start"], "home")
        self.assertTrue(o["settingsTile"])
        self.assertEqual(o["phone"], ["phone", True, False, "back"])
        self.assertEqual(o["home"], ["home", False, True, "t-phone"])
        self.assertEqual(o["settings"], "home")
        self.assertEqual(o["bogus"], "home")
        self.assertEqual(o["helper"], ["helper", True, False, "q"])
        self.assertEqual(o["back"], ["home", False, True, "t-helper"])
        self.assertEqual(o["calls"], 1)
        self.assertEqual(o["net"], 0)
        self.assertEqual(o["drafts"], ["555", "hi", "old"])

    def test_helper_failure_falls_back_to_full_view(self):
        for mode in ("reject", "stay-hidden", "none", "focus-throws"):
            o = self.run_js(mode)
            self.assertEqual(o["helper"][:3], [None, True, True], mode)
            self.assertEqual(o["unhandled"], 0, mode)
            self.assertEqual(o["drafts"], ["555", "hi", "old"], mode)

    def test_demo_keeps_full_view(self):
        o = self.run_js("ok", "?demo=1")
        self.assertIsNone(o.get("start"))


if __name__ == "__main__":
    unittest.main()
