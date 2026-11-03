# SPDX-License-Identifier: GPL-3.0-or-later;
# JRY08 All apps A-Z: sorting, letter headings, letter jump, and integration with the real phone-b.js All apps shell
# (open, close, reopen; letter row only visible with the list). Node is fed on stdin (no temp files).
import json
import shutil
import subprocess
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / 'src' / 'opos' / 'web_static'
NODE = shutil.which('node')

HARNESS = r"""
const fs=require('fs'),vm=require('vm');
let active=null;const E={},docL={click:[]},timers=[],obs=[];
function el(tag,id){const o={tagName:tag,id:id||'',_h:false,dataset:{},children:[],parentNode:null,l:{},textContent:'',type:'',className:'',attrs:{},scrolled:0,
 get hidden(){return this._h},set hidden(v){v=!!v;const c=v!==this._h;this._h=v;if(c)obs.forEach(x=>{if(x.t===this)x.f()})},
 get classList(){const s=this;return{contains:c=>s.className.split(' ').indexOf(c)>=0}},
 setAttribute(k,v){this.attrs[k]=String(v)},getAttribute(k){return k in this.attrs?this.attrs[k]:null},
 addEventListener(t,f){(this.l[t]=this.l[t]||[]).push(f)},focus(){active=this},
 appendChild(c){if(c.parentNode)c.parentNode.removeChild(c);c.parentNode=this;this.children.push(c);return c},
 insertBefore(n,r){if(n.parentNode)n.parentNode.removeChild(n);const i=this.children.indexOf(r);if(i<0)this.children.push(n);else this.children.splice(i,0,n);n.parentNode=this;return n},
 removeChild(c){const i=this.children.indexOf(c);if(i>=0)this.children.splice(i,1);c.parentNode=null;return c},
 set innerHTML(v){this.children.forEach(c=>c.parentNode=null);this.children=[]},
 get firstChild(){return this.children[0]||null},get firstElementChild(){return this.children[0]||null},
 get nextElementSibling(){const p=this.parentNode;if(!p)return null;return p.children[p.children.indexOf(this)+1]||null},
 closest(s){let n=this;while(n){if(s==='button'&&n.tagName==='BUTTON')return n;if(s[0]==='#'&&n.id===s.slice(1))return n;if(s==='[data-open]'&&n.dataset.open)return n;n=n.parentNode}return null},
 querySelector(s){const all=walk(this).filter(n=>n!==this);if(s==='button')return all.find(n=>n.tagName==='BUTTON')||null;const m=/data-open=?"?(\w+)/.exec(s);return m?all.find(n=>n.dataset.open===m[1])||null:null},
 querySelectorAll(s){return s==='li.jaryo-letter-head'?walk(this).filter(n=>n!==this&&n.tagName==='LI'&&n.classList.contains('jaryo-letter-head')):[]},
 scrollIntoView(){this.scrolled++}};if(id)E[id]=o;return o}
function walk(n){return [n].concat(...n.children.map(walk))}
function click(n){const ev={target:n};docL.click.forEach(x=>{if(x.cap)x.f(ev)});let t=n;while(t){(t.l.click||[]).forEach(f=>f(ev));t=t.parentNode}docL.click.forEach(x=>{if(!x.cap)x.f(ev)})}
function flush(){while(timers.length)timers.shift()()}
const phone=el('DIV','phone'),home=el('NAV','home'),head=el('HEAD');phone.appendChild(home);
const ul=el('UL','apps-list');ul.hidden=true;
const ctx={setTimeout(f){timers.push(f);return timers.length},clearTimeout(){},
 MutationObserver:function(f){this.observe=t=>obs.push({t,f})},
 window:{addEventListener(){},scrollTo(){},matchMedia(){return{matches:false}}},
 document:{readyState:'complete',head,getElementById:i=>E[i]||walk(phone).find(n=>n.id===i)||null,documentElement:{dataset:{}},createElement:t=>el(t.toUpperCase()),
  addEventListener(t,f,cap){(docL[t]=docL[t]||[]).push({f,cap:!!cap})}},
 localStorage:{getItem:()=>null,setItem(){},removeItem(){}},location:{search:''},Promise};
ctx.window.window=ctx.window;ctx.window.localStorage=ctx.localStorage;ctx.window.document=ctx.document;vm.createContext(ctx);
const out={};
if(MODE==='solo'){
 phone.appendChild(ul);
 ['Settings','Calls','Messages','Alarms','Camera','Contacts'].forEach(t=>{const li=el('LI'),b=el('BUTTON');b.textContent=t;if(t==='Contacts')b.dataset.scroll='contacts';li.appendChild(b);ul.appendChild(li)});
 vm.runInContext(fs.readFileSync(JARYO,'utf8'),ctx);
}else{
 ['phone','messages','helper','settings'].forEach(n=>{const b=el('BUTTON');b.dataset.open=n;b.textContent=n[0].toUpperCase()+n.slice(1);home.appendChild(b)});
 const all=el('BUTTON','allapps');all.textContent='All apps';home.appendChild(all);
 phone.appendChild(ul);
 const close=el('BUTTON','apps-close');close.hidden=true;phone.appendChild(close);
 const back=el('BUTTON','back');back.hidden=true;phone.appendChild(back);
 el('SECTION','settings').hidden=true;el('DIV','threads');el('DIV','contacts');el('DIV','ask').hidden=true;
 vm.runInContext(fs.readFileSync(PHONEB,'utf8'),ctx);
 vm.runInContext(fs.readFileSync(JARYO,'utf8'),ctx);
 (ctx.window.oposHooks||[]).forEach(f=>{try{f({})}catch(e){}});
}
const L=()=>ctx.document.getElementById('jaryo-letters');
const st=()=>({app:phone.dataset.app||null,home:!home.hidden,list:!ul.hidden,letters:!!L()&&!L().hidden,allapps:phone.dataset.allapps||null,
 back:!E.back.hidden,close:!E['apps-close'].hidden});
function snap(){return{apps:ul.children.filter(c=>!c.classList.contains('jaryo-letter')).map(c=>c.querySelector('button').textContent),
 headers:ul.children.filter(c=>c.classList.contains('jaryo-letter')).map(c=>[c.textContent,c.getAttribute('aria-hidden'),c.id]),
 letters:L()?L().children.map(b=>b.textContent):[],shown:ul.children.filter(c=>!c.hidden&&!c.classList.contains('jaryo-letter')).map(c=>c.querySelector('button').textContent)}}
if(MODE==='solo'){out.build=snap()}else{
 out.start=st();
 click(E.allapps);flush();out.open1=st();out.snap1=snap();
 const pb=L().children.find(b=>b.textContent==='P');click(pb);
 const h=ctx.document.getElementById('jaryo-letter-P');out.jump=[h.scrolled,active&&active.textContent];
 click(E['apps-close']);flush();out.closed=st();
 click(E.allapps);flush();out.open2=st();out.snap2=snap();
 const phoneRow=ul.children.find(c=>{const b=c.querySelector('button');return b&&b.dataset.open==='phone'});
 click(phoneRow.querySelector('button'));flush();out.navigated=st();
 click(E.back);flush();out.returned=st();
 click(E.allapps);flush();out.open3=st();
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, 'node not installed')
class TestJaryoApps(unittest.TestCase):
    def run_js(self, mode):
        pre = 'const MODE=%s,PHONEB=%s,JARYO=%s;\n' % (
            json.dumps(mode), json.dumps(str(STATIC / 'phone-b.js')), json.dumps(str(STATIC / 'jaryo-layout.js')))
        r = subprocess.run([NODE], input=pre + HARNESS, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_build(self):
        o = self.run_js('solo')['build']
        self.assertEqual(o['apps'], ['Alarms', 'Calls', 'Camera', 'Messages', 'Settings'])  # Contacts dropped
        self.assertEqual([h[0] for h in o['headers']], ['A', 'C', 'M', 'S'])
        for letter, aria, hid in o['headers']:
            self.assertEqual(aria, 'true')
            self.assertEqual(hid, 'jaryo-letter-' + letter)
        self.assertEqual(o['letters'], ['A', 'C', 'M', 'S'])

    def test_shell_open_close_reopen(self):
        o = self.run_js('shell')
        self.assertEqual(o['start']['app'], 'home')
        self.assertFalse(o['start']['letters'])  # letter row hidden while the list is hidden
        for k in ('open1', 'open2', 'open3'):
            self.assertEqual(o[k], {'app': 'home', 'home': False, 'list': True, 'letters': True, 'allapps': '1', 'back': False, 'close': True}, k)
        for k in ('closed', 'returned'):
            self.assertEqual(o[k], {'app': 'home', 'home': True, 'list': False, 'letters': False, 'allapps': None, 'back': False, 'close': False}, k)
        self.assertEqual(o['navigated'], {'app': 'phone', 'home': False, 'list': False, 'letters': False, 'allapps': None, 'back': True, 'close': False})
        for k in ('snap1', 'snap2'):
            s = o[k]
            self.assertEqual(s['apps'], ['Helper', 'Messages', 'Phone', 'Settings'], k)
            self.assertEqual(s['shown'], s['apps'], k)
            self.assertNotIn('Contacts', s['apps'])
            self.assertNotIn('Threads', s['apps'])
            self.assertEqual([h[0] for h in s['headers']], ['H', 'M', 'P', 'S'], k)
            self.assertTrue(all(h[1] == 'true' for h in s['headers']), k)
            self.assertEqual(s['letters'], ['H', 'M', 'P', 'S'], k)
        self.assertEqual(o['jump'], [1, 'Phone'])  # heading scrolled, focus on the first app under it



class TestJaryoLettersFoldable(unittest.TestCase):
    CSS = 'src/opos/web_static/jaryo-layout.css'

    def test_letter_row_spans_both_columns_and_keeps_the_gutter_clear(self):
        # build() inserts #jaryo-letters before #apps-list as a #phone child. On the UXP21 Foldable grid (>=840px) the list
        # spans both columns, so the row must too (DOM order = reading order; test_uxp21_profiles.test_no_button_in_hinge_gutter),
        # with its buttons kept to the left pane. Under real viewport segments both stay in column 1.
        # The stylesheet is production code the kiosk loads on every page: if it is missing, this test fails.
        with open(self.CSS, encoding='utf-8') as f:
            css = f.read()
        wide = css[css.index('@media (min-width:840px){'):]
        wide = wide[:wide.index('}}') + 2]
        self.assertIn('html[data-ff="foldable"] #phone>#jaryo-letters{grid-column:1/-1;padding-right:calc(50% + 12px)}', wide)
        seg = css[css.index('@media (horizontal-viewport-segments:2){'):]
        seg = seg[:seg.index('}}') + 2]
        self.assertIn('html[data-ff="foldable"] #phone>#jaryo-letters{grid-column:1;padding-right:0}', seg)
        # No rule outside those media blocks places the row on the hinge grid: a folded (one-column) phone keeps the
        # plain row. Integration note: the UXP99 baseline layer in the same file (row hidden until All apps opens,
        # two-column button grid; pinned by test_uxp21_profiles.test_letter_jump_row_tracks_allapps_grid) styles the
        # row outside these blocks, so the original "no rule at all" check is obsolete; it now checks that no such
        # rule sets a column or padding, which is what the one-column guarantee depends on.
        rest = css.replace(wide, '').replace(seg, '')
        for rule in rest.split('}'):
            if '#jaryo-letters{' in rule:
                decl = rule.split('{', 1)[1]
                self.assertNotIn('grid-column', decl, rule)
                self.assertNotIn('padding', decl, rule)

if __name__ == '__main__':
    unittest.main()
