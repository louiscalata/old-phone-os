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
FAVS = ["Phone", "Messages", "Helper", "Settings", "All apps"]
LIST = ["Phone", "Messages", "Helper", "Settings", "Contacts", "Threads"]


def _css_rules(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", text)


def _css():
    return (STATIC / "phone-b.css").read_text(encoding="utf-8")


class UXP12Static(unittest.TestCase):
    def setUp(self):
        self.out = page("t" * 32, "n")

    def test_markup(self):
        self.assertIn('<nav id="home" aria-label="Apps" hidden>', self.out)
        # JRY04: All apps is a wide tile; the attribute is the only markup change.
        self.assertIn('<button id="allapps" type="button" data-size="wide">All apps</button></nav>', self.out)
        self.assertIn('<ul id="apps-list" aria-label="All apps" hidden></ul>', self.out)
        self.assertIn('<button id="apps-close" type="button" hidden>Close</button>', self.out)
        self.assertEqual(len(re.findall(r'data-open="', self.out)), 4)
        nav = re.search(r'<nav id="home"[^>]*>(.*?)</nav>', self.out).group(1)
        self.assertEqual(re.findall(r"<button[^>]*>([^<]+)</button>", nav), FAVS)
        self.assertLess(self.out.index('id="home"'), self.out.index('id="apps-list"'))
        self.assertLess(self.out.index('id="apps-list"'), self.out.index('id="apps-close"'))
        self.assertLess(self.out.index('id="apps-close"'), self.out.index('id="back"'))

    def test_no_reordering_css(self):
        for sel, decl in _css_rules(_css()):
            d = decl.replace(" ", "")
            if "#home" in sel or "#apps-list" in sel or "#allapps" in sel:
                self.assertNotRegex(d, r"(^|;)order:", sel)
                self.assertNotIn("row-reverse", d, sel)
                self.assertNotIn("column-reverse", d, sel)
                self.assertNotRegex(d, r"(^|;)direction:", sel)
                self.assertNotRegex(d, r"grid-template-areas|grid-auto-flow:[a-z ]*dense", sel)
        self.assertNotRegex(_css(), r"[;{]\s*order\s*:")
        self.assertNotIn("row-reverse", _css())

    def test_column_count_changes_per_device_type(self):
        css = _css()
        for ff in ("foldable", "slide"):
            self.assertIn('html[data-ff="%s"] #phone[data-app] #apps-list:not([hidden]){grid-template-columns:' % ff, css)
        self.assertIn("#phone[data-app=\"home\"] #apps-list:not([hidden]){display:grid;grid-template-columns:1fr;gap:8px}", css)

    def test_focus_visible(self):
        found = {}
        for sel, decl in _css_rules(_css()):
            d = decl.replace(" ", "")
            for s in sel.split(","):
                s = s.strip()
                if s.endswith(":focus-visible"):
                    found[s] = d
        for s in ("#home button:focus-visible", "#apps-list button:focus-visible", "#apps-close:focus-visible"):
            self.assertIn(s, found)
            self.assertIn("outline:2pxsolid#fff", found[s])
        # White on the #111 phone background is far above 3:1.

    def test_min_height_and_gaps(self):
        heights = {}
        for sel, decl in _css_rules(_css()):
            m = re.search(r"min-height:(\d+)px", decl)
            if m:
                for s in sel.split(","):
                    heights[s.strip()] = int(m.group(1))
        for s in ("#home button", "#home #allapps", "#apps-list button", "#apps-close"):
            self.assertGreaterEqual(heights.get(s, 0), 48, s)
        for sel, decl in _css_rules(_css()):
            m = re.search(r"(?:^|;)gap:(\d+)px", decl.replace(" ", ""))
            if m and ("#home" in sel or "#apps-list" in sel):
                self.assertGreaterEqual(int(m.group(1)), 8, sel)

    def test_quiet_words(self):
        html = (STATIC / "phone-b.html").read_text(encoding="utf-8")
        for w in ("AI", "model", "governor", "assistant"):
            self.assertNotRegex(html + " ".join(LIST), r"\b%s\b" % w)


NODE = shutil.which("node")
HARNESS = r"""
const fs=require('fs'),vm=require('vm');
let active=null;const E={};
function el(tag,id){const o={tagName:tag,id:id||'',hidden:false,dataset:{},children:[],parentNode:null,l:{},textContent:'',type:'',scrolled:0,
 addEventListener(t,f){(this.l[t]=this.l[t]||[]).push(f)},focus(){active=this},
 appendChild(c){c.parentNode=this;this.children.push(c);return c},
 get firstChild(){return this.children[0]||null},
 closest(s){let n=this;while(n){if(s==='button'&&n.tagName==='BUTTON')return n;if(s==='[data-open]'&&n.dataset&&n.dataset.open)return n;n=n.parentNode}return null},
 querySelector(s){const m=/data-open=?"?(\w+)/.exec(s);if(!m)return null;return walk(this).find(n=>n!==this&&n.dataset.open===m[1])||null},
 scrollIntoView(){this.scrolled++}};if(id)E[id]=o;return o}
function walk(n){return [n].concat(...n.children.map(walk))}
function click(n){let t=n;while(t){(t.l.click||[]).forEach(f=>f({target:n}));t=t.parentNode}}
const phone=el('DIV','phone'),home=el('NAV','home');home.hidden=true;phone.appendChild(home);
['phone','messages','helper','settings'].forEach(n=>{const b=el('BUTTON');b.dataset.open=n;b.textContent=n[0].toUpperCase()+n.slice(1);home.appendChild(b)});
const all=el('BUTTON','allapps');all.textContent='All apps';home.appendChild(all);
const ul=el('UL','apps-list');ul.hidden=true;phone.appendChild(ul);
const close=el('BUTTON','apps-close');close.hidden=true;phone.appendChild(close);
const back=el('BUTTON','back');back.hidden=true;phone.appendChild(back);
const mode=process.argv[3]||'';
if(mode.indexOf('nosettings')<0)el('SECTION','settings').hidden=true;
el('DIV','threads');if(mode.indexOf('nocontacts')<0)el('DIV','contacts');el('DIV','ask').hidden=true;
// JRY08 (jaryo-layout.js) inserts letter headings: an li whose first child is a text node, not a button.
if(mode.indexOf('heading')>=0){const head=el('LI');head.className='jaryo-letter-head';head.children.push({textContent:'C'});head.parentNode=ul;ul.children.unshift(head);}
const ff=process.argv[4]||'';const docEl={dataset:{}};
const keys=[];
const c={window:{addEventListener(t,f){if(t==='keydown')keys.push(f)},scrollTo(){},matchMedia(){return{matches:false}}},
 document:{getElementById:i=>E[i]||null,documentElement:docEl,createElement:t=>el(t.toUpperCase())},
 localStorage:{getItem:()=>ff||null,setItem(){},removeItem(){}},location:{search:''},Promise};
c.window.window=c.window;c.window.localStorage=c.localStorage;vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),c);
const ui=c.window.oposUI,out={};
c.window.oposHooks.forEach(f=>f({}));
const vis=n=>!n.hidden;
const st=()=>[phone.dataset.app,vis(home),vis(ul),vis(close),vis(back),active&&(active.id||active.textContent)];
out.ff=docEl.dataset.ff||'auto';out.start=st();
out.list=ul.children.map(li=>li.firstChild.textContent).join('|');
out.listOpen=ul.children.map(li=>(li.firstChild.dataset||{}).open).join('|');
click(all);out.opened=st();out.shown=ul.children.filter(li=>!li.hidden).map(li=>li.firstChild.textContent).join('|');
out.allapps=phone.dataset.allapps||null;
click(close);out.closed=st();out.allapps2=phone.dataset.allapps||null;
click(all);click(back);out.back=st();
click(all);keys.forEach(f=>f({key:'Escape'}));out.esc=st();
click(all);click(ul.children[4].firstChild);out.contacts=[phone.dataset.app,vis(ul),E.contacts?E.contacts.scrolled:null];
ui.openApp('home');click(all);click(ul.children[5].firstChild);out.threads=[phone.dataset.app,vis(ul),E.threads.scrolled];
ui.openApp('home');click(all);click(ul.children[1].firstChild);out.messages=[phone.dataset.app,vis(ul),vis(back)];
function failOn(n,bad){Object.defineProperty(n,'hidden',{configurable:true,get(){return this._h},set(v){if(v===bad)throw Error('injected UI failure');this._h=v}});n._h=bad===false}
let err=null;
if(mode.indexOf('failshow')>=0){ui.openApp('home');active=null;failOn(ul,false);try{click(all)}catch(e){err=String(e)}
 out.failshow=st().concat([phone.dataset.allapps||null,err]);ui.openApp('phone');ui.openApp('home');out.failshow2=st()}
if(mode.indexOf('failhide')>=0){ui.openApp('home');click(all);failOn(close,true);try{click(back)}catch(e){err=String(e)}
 out.failhide=[phone.dataset.app,vis(home),vis(ul),phone.dataset.allapps||null,err];try{click(home.children[1])}catch(e){}ui.openApp('messages');out.failhide2=phone.dataset.app}
if(mode==='headings'){
 ui.openApp('home');const heading=el('LI');heading.appendChild({textContent:'A'});ul.children.unshift(heading);
 click(all);out.withHeading=st();out.headingAllapps=phone.dataset.allapps;
 let shade=true;c.opos=c.window.opos={isOpen:()=>shade};
 keys.forEach(f=>f({key:'Escape'}));out.headingUnderShade=st();shade=false;
 keys.forEach(f=>f({key:'Escape'}));out.headingEscape=st();
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node not installed")
class UXP12Runtime(unittest.TestCase):
    def run_js(self, mode="", ff=""):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / "h.js"
            h.write_text(HARNESS, encoding="utf-8")
            r = subprocess.run([NODE, str(h), str(STATIC / "phone-b.js"), mode, ff],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_order_same_for_every_device_type(self):
        seen = set()
        for ff in ("slab", "foldable", "slideout", "slide", "tablet", ""):
            o = self.run_js(ff=ff)
            seen.add((o["list"], o["listOpen"]))
        self.assertEqual(seen, {("|".join(LIST), "phone|messages|helper|settings|phone|messages")})
        self.assertEqual(self.run_js(ff="foldable")["ff"], "foldable")

    def test_open_close_back_escape(self):
        o = self.run_js()
        self.assertEqual(o["start"], ["home", True, False, False, False, None])
        self.assertEqual(o["opened"], ["home", False, True, True, True, "Phone"])
        self.assertEqual(o["shown"], "Phone|Messages|Helper|Settings|Threads")
        self.assertEqual(o["allapps"], "1")
        for k in ("closed", "back", "esc"):
            self.assertEqual(o[k], ["home", True, False, False, False, "allapps"], k)
        self.assertIsNone(o["allapps2"])

    def test_letter_headings_are_not_entries(self):
        # JRY08 adds <li class="jaryo-letter-head"> rows holding only text. The list must still open
        # (test_uxp20_continuity "Escape skipped All apps level"): headings are skipped, not hidden.
        # Integration: the UXP99 baseline test (test_letter_headings_do_not_break_all_apps) covered the same
        # heading row with Close and Back as well as Escape; its checks are folded in here so that one
        # harness mode ("heading") exercises both repairs.
        o = self.run_js("heading")
        self.assertEqual(o["opened"], ["home", False, True, True, True, "Phone"])
        self.assertEqual(o["allapps"], "1")
        self.assertEqual(o["shown"], "C|Phone|Messages|Helper|Settings|Threads")
        for key in ("closed", "back", "esc"):
            self.assertEqual(o[key], ["home", True, False, False, False, "allapps"], key)

    def test_letter_headings_preserve_navigation(self):
        o = self.run_js('headings')
        self.assertEqual(o['withHeading'], ['home', False, True, True, True, 'Phone'])
        self.assertEqual(o['headingAllapps'], '1')
        self.assertEqual(o['headingUnderShade'], o['withHeading'])
        self.assertEqual(o['headingEscape'], ['home', True, False, False, False, 'allapps'])

    def test_entries_open_and_scroll(self):
        o = self.run_js()
        self.assertEqual(o["contacts"], ["phone", False, 1])
        self.assertEqual(o["threads"], ["messages", False, 1])
        self.assertEqual(o["messages"], ["messages", False, True])

    def test_missing_targets_hide_entries(self):
        o = self.run_js("nocontacts-nosettings")
        self.assertEqual(o["shown"], "Phone|Messages|Helper|Threads")
        self.assertEqual(o["contacts"][2], None)

    def test_show_failure_keeps_home_usable(self):
        o = self.run_js("failshow")
        # Revealing the list throws: Home stays visible, nothing else is shown, no error escapes.
        self.assertEqual(o["failshow"], ["home", True, False, False, False, None, None, None])
        self.assertEqual(o["failshow2"][:5], ["home", True, False, False, False])

    def test_cleanup_failure_still_navigates(self):
        o = self.run_js("failhide")
        # Hiding Close throws inside the openApp wrapper: Back still reaches Home.
        self.assertEqual(o["failhide"], ["home", True, False, None, None])
        self.assertEqual(o["failhide2"], "messages")


if __name__ == "__main__":
    unittest.main()
