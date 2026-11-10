# SPDX-License-Identifier: GPL-3.0-or-later
import html
import re
import unittest
import json
import shutil
import subprocess
from html.parser import HTMLParser
from unittest.mock import patch
from opos import notices
from opos.setup import wizard_page, jaryo_steps, jaryo_steps_js

class TestSetupScreens(unittest.TestCase):
    def test_disclaimer_literal_and_escaped_in_page(self):
        literal = "Old Phone OS Interface is Louis Calata's hackathon demo. It is experimental software, provided as-is with no warranty (GPL-3.0). Installing it replaces your phone's operating system and may erase data; back up first and use at your own risk."
        self.assertEqual(notices.DISCLAIMER, literal)
        page = wizard_page.page('tok', 'nonce123')
        escaped = html.escape(notices.DISCLAIMER)
        self.assertIn(escaped, page)
        self.assertIn(html.escape(literal), page)
        self.assertIn(html.escape(notices.INSTALL_SCOPE), page)

    def test_fields_titles_in_fragment(self):
        fragment = jaryo_steps.fragment()
        for key in notices.FIELDS:
            if key == 'disclaimer':
                continue
            self.assertIn(f'<dt data-notice="{html.escape(key, quote=True)}">{html.escape(notices._TITLES[key])}</dt>', fragment)

    def test_jstep_blocks_and_skip_all(self):
        fragment = jaryo_steps.fragment()
        names = ['theme', 'layout', 'text_size', 'device_type', 'language', 'wifi', 'pin', 'helper']
        self.assertEqual(fragment.count('data-jstep="'), len(names))
        for name in names:
            self.assertIn(f'data-jstep="{name}"', fragment)
        prev_end = 0
        for name in names:
            start = fragment.find(f'data-jstep="{name}"', prev_end)
            self.assertNotEqual(start, -1)
            next_start = len(fragment)
            for other in names:
                if other == name:
                    continue
                idx = fragment.find(f'data-jstep="{other}"', start + 1)
                if idx != -1 and idx < next_start:
                    next_start = idx
            block = fragment[start:next_start]
            self.assertIn('data-jskip', block)
            self.assertIn('>Skip</button>', block)
            prev_end = start + 1
        self.assertIn('Skip all', fragment)

    def test_pin_step_is_record_only(self):
        # JRY34: the page steps and the server steps are the same list, in the same order. The lock PIN step
        # follows Wi-Fi (approved decisions, item 6) and Helper stays last; the section ids stay with their
        # steps (Helper j7, Lock PIN j8), so page order and id order differ on purpose. The PIN step asks for
        # nothing: no input of any kind, only the "set up later" button and Skip.
        from opos.setup import prefs
        self.assertEqual(tuple(jaryo_steps.OPTIONS), prefs.STEPS)
        self.assertEqual(prefs.STEPS.index('pin'), prefs.STEPS.index('wifi') + 1)
        self.assertEqual(prefs.STEPS[-1], 'helper')
        fragment = jaryo_steps.fragment()
        for step, sid in jaryo_steps.IDS.items():
            self.assertIn(f'<section class="step jaryo-step" id="{sid}" data-jstep="{step}" hidden>', fragment)
        # Exact composition: the accepted "Choose your edition" panel (JRY32, not a data-jstep) leads, then the
        # eight stateful steps in prefs.STEPS order. Nine jaryo-step sections in total, no more and no fewer.
        sections = re.findall(r'<section class="step jaryo-step" id="([^"]+)" (data-jedition|data-jstep="[^"]+") hidden>', fragment)
        self.assertEqual(len(sections), 9)
        self.assertEqual(len(re.findall(r'<section class="step jaryo-step"', fragment)), 9)
        self.assertEqual(sections[0], ('jaryo-edition', 'data-jedition'))
        self.assertEqual(fragment.count('data-jedition'), 1)
        self.assertEqual(fragment.count('id="jaryo-edition"'), 1)
        stateful = sections[1:]
        self.assertEqual(len(stateful), 8)
        self.assertEqual([attr for _, attr in stateful], [f'data-jstep="{step}"' for step in prefs.STEPS])
        self.assertEqual([sid for sid, _ in stateful], [jaryo_steps.IDS[step] for step in prefs.STEPS])
        self.assertEqual([sid for sid, _ in stateful], ['j1', 'j2', 'j3', 'j4', 'j5', 'j6', 'j8', 'j7'])
        self.assertEqual(stateful[-3:], [('j6', 'data-jstep="wifi"'), ('j8', 'data-jstep="pin"'), ('j7', 'data-jstep="helper"')])
        # The seven legacy identities keep their ids and class attributes; the PIN step is the explicit eighth, j8.
        self.assertEqual(sorted(jaryo_steps.IDS[s] for s in prefs.STEPS if s != 'pin'), [f'j{i}' for i in range(1, 8)])
        self.assertEqual(jaryo_steps.IDS['pin'], 'j8')
        for i in range(1, 9):
            self.assertEqual(fragment.count(f'<section class="step jaryo-step" id="j{i}" data-jstep="'), 1)
        self.assertEqual(fragment.count('data-jstep="'), 8)
        self.assertLess(fragment.index('data-jstep="wifi"'), fragment.index('data-jstep="pin"'))
        self.assertLess(fragment.index('data-jstep="pin"'), fragment.index('data-jstep="helper"'))
        start = fragment.index('data-jstep="pin"')
        block = fragment[start:fragment.index('</section>', start)]
        self.assertNotIn('<input', block)
        self.assertIn('<h3 tabindex="-1">Lock PIN</h3>', block)
        self.assertIn('does not save a lock PIN', block)
        self.assertNotIn('not available in this build', block)
        self.assertIn('<button type="button" data-jpin>Set up later in Settings › Device</button>', block)
        for word in ('password', 'inputmode', 'digits'):
            self.assertNotIn(word, block)
            self.assertNotIn(word, jaryo_steps_js.SCRIPT)
        self.assertIn("[data-jpin]", jaryo_steps_js.SCRIPT)

    def test_style_no_hex_or_rgb_except_definitions(self):
        style = jaryo_steps.STYLE
        clean = re.sub(r'--jaryo-fg:[^;]+', '', style)
        clean = re.sub(r'--jaryo-bg:[^;]+', '', clean)
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,6}', clean))
        self.assertNotIn('rgb(', clean)

    def test_fragment_no_forbidden_elements_or_words(self):
        fragment = jaryo_steps.fragment()
        self.assertNotIn('<img', fragment)
        self.assertNotIn('<svg', fragment)
        self.assertNotIn('type="password"', fragment)
        for word in ['AI', 'model', 'governor', 'assistant']:
            self.assertIsNone(re.search(r'\b' + re.escape(word) + r'\b', fragment, re.I))

    def test_wifi_is_read_only_and_explains_detection(self):
        fragment = jaryo_steps.fragment()
        wifi = fragment.split('data-jstep="wifi"', 1)[1].split('</section>', 1)[0]
        self.assertIn('role="status"', wifi)
        self.assertIn('Radio detection does not show whether you are connected.', wifi)
        self.assertNotIn('your edition', wifi)
        self.assertIn('Joining a network is not available in this build', wifi)
        self.assertIn('data-jwifi>Set up later in Settings › Network', wifi)
        self.assertNotIn('<input', wifi)
        for control in ('data-jback', 'data-jnext', 'data-jskip'):
            self.assertIn(control, wifi)

    def test_script_contents(self):
        script = jaryo_steps_js.SCRIPT
        self.assertIn('/setup/api/prefs', script)
        self.assertIn('skip-all', script)
        self.assertNotIn('innerHTML', script)
        # JRY32: Use <edition> saves the edition as the existing device_type answer, then skip-all.
        self.assertIn("{step: 'device_type', value: picked.value}", script)
        self.assertIn("'/setup/api/prefs/skip-all'", script)
        self.assertNotRegex(script, r'\b(AI|model|assistant)\b')

    def test_edition_panel_leads_and_maps_to_device_type(self):
        # JRY32 (design decision 6): one leading "Choose your edition" panel. It is not a data-jstep, so the
        # seven steps, resume and skip-all keep their pinned behaviour (test_jstep_blocks_and_skip_all).
        from opos.setup import prefs
        fragment = jaryo_steps.fragment()
        start = fragment.index('<section class="step jaryo-step" id="jaryo-edition" data-jedition hidden>')
        block = fragment[start:fragment.index('</section>', start)]
        self.assertLess(start, fragment.index('data-jstep="theme"'))
        self.assertNotIn('data-jstep', block)
        self.assertIn('<legend tabindex="-1">Choose your edition</legend>', block)
        radios = re.findall(r'<input type="radio" name="edition" value="([a-z]+)" data-jname="([^"]+)"( checked)?>', block)
        self.assertEqual([(v, n) for v, n, _ in radios], [('slab', 'Pocket'), ('foldable', 'Foldable'), ('slideout', 'Slide-out'), ('tablet', 'Tablet')])
        for value, _, _ in radios:
            self.assertIn(value, prefs.VALUES['device_type'])
        self.assertEqual([n for _, n, c in radios if c], ['Pocket'])
        self.assertEqual(block.count('class="jaryo-edition-desc"'), 4)
        self.assertEqual(block.count('data-jkicker='), 4)
        self.assertEqual(block.count('>Suggested</span>'), 1)
        self.assertIn('based on the browser window, not hardware detection', block)
        self.assertIn('<button type="button" id="jaryo-customise">Customise instead</button>', block)
        self.assertIn('<button type="button" id="jaryo-use-edition">Use Pocket</button>', block)
        self.assertIn('Settings', block)
        self.assertNotIn('type="password"', block)
        self.assertNotIn('<img', block)
        # Primary action is inverted (bg text on fg) with a 3 px rule; both selectors outrank the base button rule.
        rule = re.search(r'#jaryo-setup #jaryo-use-edition\{([^}]+)', jaryo_steps.STYLE)[1]
        self.assertIn('background:var(--jaryo-fg)', rule)
        self.assertIn('color:var(--jaryo-bg)', rule)
        self.assertIn('border:3px solid var(--jaryo-fg)', rule)

    def test_page_contains_existing_ids(self):
        page = wizard_page.page('tok', 'nonce123')
        for i in range(1, 8):
            self.assertIn(f'id="s{i}"', page)
        self.assertIn('id="apply"', page)
        self.assertIn('id="progress"', page)

    def test_page_script_nonced(self):
        page = wizard_page.page('tok', 'nonce123')
        self.assertIn('nonce="nonce123"', page)

    def test_personal_steps_follow_complete_review_step(self):
        page = wizard_page.page('tok', 'nonce123')
        review_start = page.index('<section class="step panel" id="s7"')
        review_end = page.index('</section>', review_start)
        self.assertIn('<button type="button" data-back>Back</button>',
                      page[review_start:review_end])
        self.assertGreater(page.index('<section id="jaryo-setup"'), review_end)
        self.assertLess(page.index('<section id="jaryo-setup"'), page.index('</main>'))

class NodeDOM(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.nodes.append(dict(tag=tag, attrs=dict(attrs), parent=self.stack[-1] if self.stack else -1, text=''))
        if tag not in ('meta', 'input', 'br', 'hr', 'link'):
            self.stack.append(len(self.nodes) - 1)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.nodes[self.stack[i]]['tag'] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        for i in self.stack:
            self.nodes[i]['text'] += data


class TestSetupRuntime(unittest.TestCase):
    def test_markup_escaping_and_targets(self):
        with patch.dict(jaryo_steps.OPTIONS, theme=[('light', '<Light & bright>')]), patch.object(jaryo_steps, 'TITLES', ['<Theme & look>'] + jaryo_steps.TITLES[1:]), patch.object(notices, 'notices', return_value={'a"&': '<value>'}), patch.dict(notices._TITLES, {'a"&': '<Title>'}):
            fragment = jaryo_steps.fragment()
        self.assertIn('<legend tabindex="-1">&lt;Theme &amp; look&gt;</legend>', fragment)
        self.assertIn('> &lt;Light &amp; bright&gt;</label>', fragment)
        self.assertIn('<dt data-notice="a&quot;&amp;">&lt;Title&gt;</dt><dd>&lt;value&gt;</dd>', fragment)
        self.assertNotIn('<h3 tabindex="-1">&lt;Theme', fragment)
        self.assertNotRegex(fragment, r'<input[^>]*aria-describedby')
        for selector in ('label', 'button'):
            rule = re.search(r'#jaryo-setup ' + selector + r'\{([^}]+)', jaryo_steps.STYLE)[1]
            self.assertIn('min-height:48px', rule)
            self.assertIn('min-width:48px', rule)
        self.assertIn('.row{display:flex;flex-wrap:wrap;gap:8px}', jaryo_steps.STYLE)

    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable')
    def test_generated_script_navigation_and_recovery(self):
        page = wizard_page.page('tok', 'nonce123')
        script = re.search(r'<script[^>]*>(.*?)</script>', page, re.S)[1]
        payload = json.dumps({'nodes': NodeDOM(page).nodes, 'script': script})
        result = subprocess.run(['node', '-e', RUNTIME], input=payload, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


RUNTIME = r"""
const vm = require('vm'), assert = require('assert');
let input = ''; process.stdin.on('data', s => input += s);
process.stdin.on('end', async () => {
try {
 const data = JSON.parse(input);
 for (const present of [true, false]) {
  const nodes = data.nodes.map(n => ({...n, attrs: {...n.attrs}, children: [], dataset: {}, style: {},
   hidden: 'hidden' in n.attrs, checked: 'checked' in n.attrs, value: n.attrs.value || '',
   textContent: n.text, content: n.attrs.content, events: {},
   focus() { doc.activeElement = this; },
   setAttribute(k,v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
   addEventListener(k,f) { this.events[k] = f; },
   appendChild(n) { this.children.push(n); },
   querySelectorAll(s) { return select(s, this); }, querySelector(s) { return select(s, this)[0] || null; }
  }));
  nodes.forEach(n => { for (const [k,v] of Object.entries(n.attrs)) if(k.startsWith('data-')) n.dataset[k.slice(5).replace(/-([a-z])/g, (_,c)=>c.toUpperCase())] = v; });
  nodes.forEach(n => { if(n.parent >= 0) { n.parentElement = nodes[n.parent]; nodes[n.parent].children.push(n); } });
  function matches(n,s) {
   if(s === '.step:not(.jaryo-step)') return (n.attrs.class || '').split(' ').includes('step') && !(n.attrs.class || '').includes('jaryo-step');
   if(s.startsWith('#')) return n.attrs.id === s.slice(1) && (present || n.attrs.id !== 'jaryo-setup');
   if(s.startsWith('.')) return (n.attrs.class || '').split(' ').includes(s.slice(1));
   const tag = s.match(/^[a-z][a-z0-9]*/); if(tag && n.tag !== tag[0]) return false;
   for(const m of s.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) if(!(m[1] in n.attrs) || (m[2] !== undefined && n.attrs[m[1]] !== m[2])) return false;
   if(s.endsWith(':checked') && !n.checked) return false;
   return true;
  }
  function select(s, root) {
   return nodes.filter(n => (!root || (()=>{let p=n.parentElement; while(p){if(p===root)return true;p=p.parentElement;}return false;})()) && s.split(',').some(x=>matches(n,x.trim())));
  }
  const doc = {querySelector:s=>select(s)[0] || null, querySelectorAll:s=>select(s), getElementById:id=>select('#'+id)[0] || null,
    createElement:tag=>({tag, children:[], appendChild(n){this.children.push(n);}}), createTextNode:text=>({textContent:text})};
  const get = id => doc.getElementById(id);
  get('goal-time').value = '23:00';
  let state = {answers:{}, skipped:[]}, resume = 'theme', fail = false;
  const calls = [];
  const originalAlert = () => {};
  const ctx = {document:doc, alert:originalAlert, sessionStorage:{getItem:()=>null}, setTimeout:()=>0, setInterval:()=>0, clearInterval:()=>{},
   fetch:async (url,options) => {
    calls.push([url,options]); assert.equal(options.headers['X-OPOS-Token'],'tok');
    if(fail) return {ok:false};
    let body = options.body && JSON.parse(options.body);
    if(url === '/setup/api/state') return {ok:true,json:async()=>({device:{name:'Phone',radios_present:['wifi']},profiles:[],first_run:true})};
    if(url === '/setup/api/validate') return {ok:true,json:async()=>({errors:[]})};
    if(body && body.step) {
      if(url.endsWith('/skip')) {
        delete state.answers[body.step];
        if(!state.skipped.includes(body.step)) state.skipped.push(body.step);
      } else {
        state.answers[body.step]=body.value;
        state.skipped=state.skipped.filter(n=>n!==body.step);
      }
      resume=['theme','layout','text_size','device_type','language','wifi','pin','helper'].find(n=>!(n in state.answers)&&!state.skipped.includes(n)) || null;
    }
    // Like prefs.skip_all: answered steps stay answered, every other step is skipped.
    if(url.endsWith('/skip-all')) { const all=['theme','layout','text_size','device_type','language','wifi','pin','helper']; state = {answers:state.answers,skipped:all.filter(n=>!(n in state.answers))}; resume='done'; }
    // JRY22: finish is the only call that applies; it answers with the standard envelope.
    if(url.endsWith('/finish')) { state.applied = true; return {ok:true,json:async()=>({state,resume:'done',effective:{}})}; }
    return {ok:true,json:async()=>({state,resume})};
   }};
  ctx.window=ctx; vm.createContext(ctx); vm.runInContext(data.script,ctx);
  const flush = async()=>{for(let i=0;i<30;i++)await Promise.resolve();}; await flush();
  assert.strictEqual(ctx.alert,originalAlert);
  assert.equal(vm.runInContext('steps.length',ctx),7);
  const click = async n=>{await (n.onclick || n.events.click)();await flush();};
  await click(get('s1').querySelector('[data-next]')); assert.equal(get('s2').hidden,false);
  assert.equal(get('meter-fill').style.width,'29%'); assert.equal(get('meter').attrs['aria-valuenow'],'29');
  await click(get('s2').querySelector('[data-back]')); assert.equal(get('s1').hidden,false);
  for(let i=1;i<7;i++) await click(get('s'+i).querySelector('[data-next]'));
  assert.equal(get('s7').hidden,false);assert.equal(get('meter').attrs['aria-valuenow'],'100');
  assert(get('summary').children.some(n=>n.textContent==='Device: Phone'));
  if(!present){assert.equal(ctx.jaryoSetup,undefined);continue;}
  // JRY32: a fresh install leads with Choose your edition. Without matchMedia the detected edition is Pocket.
  await ctx.jaryoSetup.start(); assert.equal(get('jaryo-edition').hidden,false); assert.equal(get('j1').hidden,true); assert.equal(get('jaryo-radio').textContent,'Wi-Fi radio: present');
  const ed=()=>get('jaryo-edition').querySelectorAll('input');
  assert.deepEqual(ed().map(n=>[n.value,n.checked]),[['slab',true],['foldable',false],['slideout',false],['tablet',false]]);
  assert.equal(get('jaryo-use-edition').textContent,'Use Pocket');
  await click(get('jaryo-customise')); assert.equal(get('j1').hidden,false); assert.equal(get('jaryo-edition').hidden,true);
  await click(get('j1').querySelector('[data-jback]')); assert.equal(get('jaryo-edition').hidden,false); assert.equal(get('j1').hidden,true);
  await click(get('jaryo-customise')); assert.equal(get('j1').hidden,false);
  get('j1').querySelectorAll('input').find(n=>n.value==='light').checked=true;
  await click(get('j1').querySelector('[data-jnext]'));assert.equal(get('j2').hidden,false);
  await click(get('j2').querySelector('[data-jback]'));assert.equal(get('j1').hidden,false);
  await click(get('j1').querySelector('[data-jskip]'));assert.equal(get('j2').hidden,false);
  get('j2').querySelector('input').checked=true;await click(get('j2').querySelector('[data-jnext]'));
  for(let i=3;i<=5;i++) await click(get('j'+i).querySelector('[data-jskip]'));
  assert.equal(get('j6').hidden,false);
  assert.equal(doc.activeElement,get('j6').querySelector('h3'));
  await click(get('j6').querySelector('[data-jback]'));assert.equal(get('j5').hidden,false);
  await click(get('j5').querySelector('[data-jskip]'));
  // JRY34: the lock PIN step (j8) follows Wi-Fi and is record-only: no input, and "Set up later" records a skip like Wi-Fi.
  await click(get('j6').querySelector('[data-jwifi]'));assert.equal(get('j8').hidden,false);assert.equal(get('j7').hidden,true);
  for(const control of ['data-jnext','data-jskip']) {
   await click(get('j8').querySelector('[data-jback]'));
   const before=calls.length;
   fail=true;await click(get('j6').querySelector('['+control+']'));
   assert.equal(get('j6').hidden,false);assert.equal(get('jaryo-error').hidden,false);
   fail=false;await click(get('j6').querySelector('['+control+']'));
   assert.equal(get('j8').hidden,false);
   const writes=calls.slice(before).filter(([,o])=>o.method==='POST');
   assert.equal(writes.length,2);
   for(const [url,o] of writes){
    assert.equal(url,'/setup/api/prefs/skip');
    assert.deepEqual(JSON.parse(o.body),{step:'wifi'});
   }
  }
  assert.equal(get('j8').querySelector('input'),null);
  const pinCalls=calls.length;await click(get('j8').querySelector('[data-jpin]'));
  assert(calls.slice(pinCalls).some(([url,o])=>url==='/setup/api/prefs/skip'&&JSON.parse(o.body).step==='pin'));
  assert(!calls.slice(pinCalls).some(([url,o])=>url==='/setup/api/prefs'&&o.method==='POST'));
  assert.equal(get('j7').hidden,false);assert.equal(get('j8').hidden,true);
  get('j7').querySelector('input').checked=true; await click(get('j7').querySelector('[data-jnext]'));
  assert.equal(get('jaryo-summary').hidden,false, get('jaryo-error').textContent);
  assert(get('jaryo-summary').textContent.includes('Layout and favourite apps: Phone'));
  assert(get('jaryo-summary').textContent.includes('Helper: On'));
  assert(get('jaryo-summary').textContent.includes('Skipped (recommended settings)'));
  assert(get('jaryo-summary').textContent.includes('Lock PIN: Skipped (recommended settings); Set up later in Settings › Device'));
  // Back from the summary lands on Helper (last); Back from Helper lands on the PIN step; Back from it on Wi-Fi.
  await click(get('jaryo-summary-back')); assert.equal(get('j7').hidden,false);
  await click(get('j7').querySelector('[data-jback]'));assert.equal(get('j8').hidden,false);
  await click(get('j8').querySelector('[data-jback]'));assert.equal(get('j6').hidden,false);
  await click(get('j6').querySelector('[data-jnext]'));assert.equal(get('j8').hidden,false);
  await click(get('j8').querySelector('[data-jnext]'));assert.equal(get('j7').hidden,false);
  await click(get('j7').querySelector('[data-jnext]'));
  assert.equal(get('jaryo-summary').hidden,false, get('jaryo-error').textContent);
  await click(get('jaryo-skip-all'));assert.equal(get('jaryo-summary').hidden,false, get('jaryo-error').textContent);
  await click(get('jaryo-summary-back'));assert.equal(get('j7').hidden,false);
  // Use <edition>: detection picks Foldable, a change re-labels the button, Use posts device_type then skip-all.
  state={answers:{},skipped:[]};resume='theme';ctx.matchMedia=q=>({matches:q.indexOf('min-width:600px')>=0});
  await ctx.jaryoSetup.start(); assert.equal(get('jaryo-edition').hidden,false);
  assert.deepEqual(ed().filter(n=>n.checked).map(n=>n.value),['foldable']);
  const kick=v=>ed().find(n=>n.value===v).parentElement.querySelector('[data-jkicker]').textContent;
  assert.equal(kick('foldable'),'Suggested'); assert.equal(kick('slab'),'Regular phones');
  assert.equal(get('jaryo-use-edition').textContent,'Use Foldable');
  ed().forEach(n=>{n.checked=n.value==='tablet';}); get('jaryo-setup').events.change({target:{name:'edition',value:'tablet'}});
  assert.equal(get('jaryo-use-edition').textContent,'Use Tablet');
  const before=calls.length; await click(get('jaryo-use-edition'));
  const posted=calls.slice(before).map(([url,o])=>[url,o.body?JSON.parse(o.body):null]);
  assert.deepEqual(posted[0],['/setup/api/prefs',{step:'device_type',value:'tablet'}]);
  assert.deepEqual(posted[1],['/setup/api/prefs/skip-all',{}]);
  assert.equal(get('jaryo-summary').hidden,false, get('jaryo-error').textContent);
  assert(get('jaryo-summary').textContent.includes('Device type: Tablet'));
  assert(get('jaryo-summary').textContent.includes('Theme: Skipped (recommended settings)'));
  delete ctx.matchMedia;
  // JRY32 review: an edition picked but not yet used survives Customise and Back. A device type saved at j4 after that
  // is what Back to the panel offers, so Use never overwrites it with the earlier pick.
  state={answers:{},skipped:[]};resume='theme';await ctx.jaryoSetup.start(); assert.equal(get('jaryo-edition').hidden,false);
  assert.deepEqual(ed().filter(n=>n.checked).map(n=>n.value),['slab']);
  ed().forEach(n=>{n.checked=n.value==='foldable';}); get('jaryo-setup').events.change({target:{name:'edition',value:'foldable'}});
  await click(get('jaryo-customise')); assert.equal(get('j1').hidden,false);
  await click(get('j1').querySelector('[data-jback]')); assert.equal(get('jaryo-edition').hidden,false);
  assert.deepEqual(ed().filter(n=>n.checked).map(n=>n.value),['foldable']); assert.equal(get('jaryo-use-edition').textContent,'Use Foldable');
  await click(get('jaryo-customise')); assert.equal(get('j1').hidden,false);
  for(let i=1;i<=3;i++) await click(get('j'+i).querySelector('[data-jskip]'));
  assert.equal(get('j4').hidden,false);
  get('j4').querySelectorAll('input').forEach(n=>{n.checked=n.value==='tablet';}); await click(get('j4').querySelector('[data-jnext]'));
  assert.equal(get('j5').hidden,false); assert.equal(state.answers.device_type,'tablet');
  for(let i=5;i>=1;i--) await click(get('j'+i).querySelector('[data-jback]'));
  assert.equal(get('jaryo-edition').hidden,false); assert.equal(get('j1').hidden,true);
  assert.deepEqual(ed().filter(n=>n.checked).map(n=>n.value),['tablet']); assert.equal(get('jaryo-use-edition').textContent,'Use Tablet');
  assert.equal(kick('slab'),'Suggested');
  const before2=calls.length; await click(get('jaryo-use-edition'));
  const posted2=calls.slice(before2).map(([url,o])=>[url,o.body?JSON.parse(o.body):null]);
  assert.deepEqual(posted2[0],['/setup/api/prefs',{step:'device_type',value:'tablet'}]);
  assert.deepEqual(posted2[1],['/setup/api/prefs/skip-all',{}]);
  assert.equal(state.answers.device_type,'tablet'); assert.deepEqual(state.skipped,['theme','layout','text_size','language','wifi','pin','helper']);
  assert(get('jaryo-summary').textContent.includes('Device type: Tablet'));
  // Resuming part-way never shows the edition panel again.
  state={answers:{theme:'dark'},skipped:[]};resume='layout';await ctx.jaryoSetup.start();
  assert.equal(get('jaryo-edition').hidden,true);assert.equal(get('j2').hidden,false);
  state={answers:{wifi:'later'},skipped:['theme','layout','text_size','device_type','language','pin','helper']};resume='done';await ctx.jaryoSetup.start();
  assert.equal(get('jaryo-summary').hidden,false);
  assert(get('jaryo-summary').textContent.includes('Wi-Fi: Set up later in Settings › Network'));
  state={answers:{pin:'later'},skipped:['theme','layout','text_size','device_type','language','wifi','helper']};resume='done';await ctx.jaryoSetup.start();
  assert.equal(get('jaryo-summary').hidden,false);
  assert(get('jaryo-summary').textContent.includes('Lock PIN: Set up later in Settings › Device'));
  assert(!get('jaryo-summary').textContent.includes('Lock PIN: Skipped'));
  fail=true;await ctx.jaryoSetup.start();assert.equal(get('jaryo-error').hidden,false);assert.equal(get('jaryo-retry').hidden,false);
  fail=false;state={answers:{},skipped:[]};resume='theme';ctx._curDev={radios_present:[]};await click(get('jaryo-retry'));
  assert.equal(get('jaryo-error').hidden,true);assert.equal(get('jaryo-edition').hidden,false);assert.equal(get('j1').hidden,true);assert.equal(get('jaryo-radio').textContent,'Wi-Fi radio: not detected');
  await click(get('jaryo-customise'));assert.equal(get('j1').hidden,false);
  for(const bad of [null,[],{layout:{favourites:'phone'}},{layout:null},{theme:'bad'},{pin:'1234'},{pin:{digits:'1234'}}]){
   state={answers:bad,skipped:[]};await ctx.jaryoSetup.start();assert.equal(get('jaryo-error').hidden,false);
  }
  state={answers:{},skipped:['unknown']};await ctx.jaryoSetup.start();assert.equal(get('jaryo-error').hidden,false);
  state={answers:{},skipped:[]};resume='unknown';await ctx.jaryoSetup.start();assert.equal(get('jaryo-error').hidden,false);
  for(const stale of [null,'done']){resume=stale;await ctx.jaryoSetup.start();assert.equal(get('jaryo-error').hidden,false);assert.equal(get('j1').hidden,false);assert.equal(get('jaryo-summary').hidden,true);}
  resume='theme';await ctx.jaryoSetup.start();assert.equal(get('jaryo-error').hidden,true);
  assert(calls.some(([url,o])=>url==='/setup/api/prefs/skip' && o.method==='POST'));
 }
} catch(e) { console.error(e.stack);process.exitCode=1; }
});
"""

if __name__ == '__main__':
    unittest.main()
