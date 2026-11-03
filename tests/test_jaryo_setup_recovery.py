# SPDX-License-Identifier: GPL-3.0-or-later
"""JRY22: personal setup steps recover from unreadable saved choices, describe a saved Wi-Fi
name honestly, keep real headings with focus on transition, and only claim the choices are
applied once /setup/api/prefs/finish has succeeded (with a visible, focusable retry when it fails).
"""
import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

from opos.setup import jaryo_steps, wizard_page

NODE = shutil.which('node')


def _script(page):
    return re.search(r'<script[^>]*>(.*?)</script>', page, re.S)[1]


class _Dom(HTMLParser):
    """Flat node list with parent links and accumulated text, for Python checks and the Node harness."""

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

    def by_id(self, element_id):
        return next(i for i, n in enumerate(self.nodes) if n['attrs'].get('id') == element_id)

    def inside(self, index, ancestor):
        p = self.nodes[index]['parent']
        while p >= 0:
            if p == ancestor:
                return True
            p = self.nodes[p]['parent']
        return False

    def descendants(self, ancestor, tag):
        return [i for i, n in enumerate(self.nodes) if n['tag'] == tag and self.inside(i, ancestor)]


class HeadingTests(unittest.TestCase):
    def setUp(self):
        self.page = wizard_page.page('tok', 'nonce123')
        self.dom = _Dom(self.page)

    def test_every_personal_step_has_one_focusable_h3(self):
        for name, title in zip(jaryo_steps.OPTIONS, jaryo_steps.TITLES, strict=True):
            sid = jaryo_steps.IDS[name]
            section = self.dom.by_id(sid)
            h3s = self.dom.descendants(section, 'h3')
            self.assertEqual(len(h3s), 1, f'{sid} must have exactly one heading')
            node = self.dom.nodes[h3s[0]]
            self.assertEqual(node['attrs'].get('tabindex'), '-1', f'{sid} heading must be focusable by script')
            self.assertEqual(node['text'].strip(), title)

    def test_choice_steps_keep_the_legend_and_put_the_heading_inside_it(self):
        for name, choices in jaryo_steps.OPTIONS.items():
            sid = jaryo_steps.IDS[name]
            section = self.dom.by_id(sid)
            h3 = self.dom.descendants(section, 'h3')[0]
            parent = self.dom.nodes[self.dom.nodes[h3]['parent']]
            if choices:
                self.assertEqual(parent['tag'], 'legend', name)
                self.assertNotIn('tabindex', parent['attrs'], 'focus goes to the heading, not the legend')
                fieldset = self.dom.nodes[self.dom.nodes[self.dom.nodes[h3]['parent']]['parent']]
                self.assertEqual(fieldset['tag'], 'fieldset', name)
                self.assertEqual(fieldset['attrs'].get('aria-describedby'), 'jaryo-error', name)
                self.assertEqual(len(self.dom.descendants(section, 'legend')), 1, name)
            else:
                self.assertEqual(parent['tag'], 'section', name)
                self.assertEqual(parent['attrs'].get('id'), sid)

    def test_heading_inside_legend_inherits_legend_type(self):
        self.assertEqual(self.page.count('#jaryo-setup legend h3{margin:0;font:inherit}'), 1)
        self.assertNotIn('<legend tabindex="-1">', self.page)

    def test_core_wizard_markup_untouched(self):
        self.assertEqual(self.page.count('class="step panel"'), 7)
        self.assertIn('<legend>What kind of phone is this?</legend>', self.page)
        for value in ('slab', 'foldable-book', 'dual-screen', 'slide-keyboard'):
            self.assertIn(f'name="form_factor" value="{value}"', self.page)
        self.assertEqual(self.page.count('<script'), 1)
        self.assertEqual(self.page.count('<script nonce="nonce123">'), 1)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.page = wizard_page.page('tok', 'nonce123')
        self.script = _script(self.page)
        start = self.script.index("document.getElementById('jaryo-setup')")
        self.jaryo = self.script[start:]

    def test_finish_is_posted_and_is_the_only_applied_claim(self):
        self.assertIn("request('/setup/api/prefs/finish', {})", self.jaryo)
        self.assertEqual(self.jaryo.count('Your choices are saved and applied.'), 1)
        finish = self.jaryo.index("request('/setup/api/prefs/finish', {})")
        self.assertIn('applied = true; complete();', self.jaryo[finish:])
        self.assertNotIn('applied = true', self.jaryo[:finish])

    def test_finish_completes_only_on_server_confirmation(self):
        self.assertIn('if (state.applied !== true) return false;', self.jaryo)
        self.assertIn('if (!confirmed(data)) {', self.jaryo)
        finish = self.jaryo.index("request('/setup/api/prefs/finish', {})")
        self.assertLess(self.jaryo.index('if (!confirmed(data)) {'), self.jaryo.index('applied = true; complete();', finish))
        self.assertIn("data.resume === null || data.resume === 'done'", self.jaryo)

    def test_unreadable_saved_state_no_longer_throws(self):
        self.assertNotIn("throw new Error('state')", self.jaryo)
        self.assertIn('Some saved choices could not be read', self.jaryo)

    def test_finish_failure_has_its_own_message_and_retry_label(self):
        self.assertIn('have not been applied yet', self.jaryo)
        self.assertIn("'Try finishing again'", self.jaryo)
        self.assertIn("'Try loading choices again'", self.jaryo)

    def test_wifi_summary_texts(self):
        self.assertIn('Set up later in Settings › Network', self.jaryo)
        self.assertIn('saved as a preference only, not connected', self.jaryo)
        self.assertNotRegex(self.jaryo, r'[Cc]onnected to')

    def test_no_innerhtml_or_eval(self):
        self.assertNotIn('innerHTML', self.script)
        self.assertNotIn('eval(', self.script)
        self.assertNotIn('document.write', self.script)

    def test_core_wizard_script_preserved(self):
        for marker in (
            'async function changeProfile(',
            'if(n!==window._pn||sel.value!==v)return;',
            'new Error("The device did not load. Try again.")',
            'const FF=["slab","foldable-book","dual-screen","slide-keyboard"]',
            'form_factor:getFF()',
            'const FIELDS={2:["goal-time"',
            '"/setup/api/validate"',
            'if(window.jaryoSetup)window.jaryoSetup.start()',
            'const steps=qa(".step:not(.jaryo-step)")',
        ):
            self.assertIn(marker, self.script, marker)
        self.assertLess(self.script.index("document.getElementById('jaryo-setup')"), self.script.index('\nload();updMeter();'))


@unittest.skipUnless(NODE, 'node not installed')
class RuntimeTests(unittest.TestCase):
    def test_recovery_wifi_focus_finish_and_queueing(self):
        page = wizard_page.page('tok', 'nonce123')
        payload = json.dumps({'nodes': _Dom(page).nodes, 'script': _script(page)})
        result = subprocess.run([NODE, '--unhandled-rejections=strict', '-e', RUNTIME], input=payload, text=True,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('scenarios ok: 19', result.stdout)


RUNTIME = r"""
const vm = require('vm'), assert = require('assert');
const NAMES = ['theme', 'layout', 'text_size', 'device_type', 'language', 'wifi', 'pin', 'helper'];
const FULL = () => ({theme: 'dark', layout: {favourites: ['phone', 'settings']}, text_size: 'large', device_type: 'slab', language: 'en', wifi: 'later', pin: 'later', helper: 'on'});
const cls = n => (n.attrs.class || '').split(/\s+/);
function matchOne(n, s) {
  s = s.trim(); let m;
  if ((m = s.match(/^(.*):not\(\.([\w-]+)\)$/))) return matchOne(n, m[1]) && !cls(n).includes(m[2]);
  if ((m = s.match(/^(.*):checked$/))) return matchOne(n, m[1]) && !!n.checked;
  if (s.startsWith('#')) return n.attrs.id === s.slice(1);
  if (s.startsWith('.')) return cls(n).includes(s.slice(1));
  const tag = s.match(/^[a-z][a-z0-9]*/);
  if (tag && n.tag !== tag[0]) return false;
  if (!tag && !s.startsWith('[')) throw new Error('unsupported selector ' + s);
  for (const a of s.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) if (!(a[1] in n.attrs) || (a[2] !== undefined && n.attrs[a[1]] !== a[2])) return false;
  return true;
}
function world(data) {
  const doc = {activeElement: null};
  const nodes = data.nodes.map(n => ({tag: n.tag, attrs: {...n.attrs}, parent: n.parent, children: [], dataset: {}, style: {}, events: {},
    hidden: 'hidden' in n.attrs, checked: 'checked' in n.attrs, value: n.attrs.value || '', textContent: n.text, content: n.attrs.content,
    focus() { doc.activeElement = this; }, setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(k, f) { this.events[k] = f; }, appendChild(c) { this.children.push(c); },
    querySelectorAll(s) { return select(s, this); }, querySelector(s) { return select(s, this)[0] || null; }}));
  nodes.forEach(n => { for (const [k, v] of Object.entries(n.attrs)) if (k.startsWith('data-')) n.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v; });
  nodes.forEach(n => { if (n.parent >= 0) { n.parentElement = nodes[n.parent]; nodes[n.parent].children.push(n); } });
  const inside = (n, root) => { for (let p = n.parentElement; p; p = p.parentElement) if (p === root) return true; return false; };
  const select = (s, root) => nodes.filter(n => (!root || inside(n, root)) && s.split(',').some(x => matchOne(n, x)));
  Object.assign(doc, {querySelector: s => select(s)[0] || null, querySelectorAll: s => select(s), getElementById: id => select('#' + id)[0] || null,
    createElement: tag => ({tag, attrs: {}, children: [], textContent: '', appendChild(c) { this.children.push(c); }, setAttribute(k, v) { this.attrs[k] = v; }}),
    createTextNode: text => ({textContent: text})});
  const get = id => doc.getElementById(id);
  const visible = n => { for (let p = n; p; p = p.parentElement) if (p.hidden) return false; return true; };
  const server = {state: {answers: {}, skipped: [], applied: false}, resume: undefined, failAll: false, finishOk: true, hook: null};
  const calls = [];
  const computed = () => NAMES.find(n => !(n in server.state.answers) && !server.state.skipped.includes(n)) || null;
  const reply = () => ({ok: true, json: async () => ({state: JSON.parse(JSON.stringify(server.state)), resume: server.resume !== undefined ? server.resume : computed(), effective: {}})});
  const ctx = {document: doc, sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}}, setTimeout: () => 0, setInterval: () => 0, clearInterval() {}, console,
    fetch: async (url, options) => {
      calls.push([url, options]);
      assert.equal(options.headers['X-OPOS-Token'], 'tok');
      if (server.hook) { const r = await server.hook(url, options); if (r) return r; }
      if (server.failAll) return {ok: false};
      const body = options.body ? JSON.parse(options.body) : null;
      if (url === '/setup/api/state') return {ok: true, json: async () => ({device: {name: 'Phone', radios_present: ['wifi']}, profiles: [], first_run: true})};
      if (url === '/setup/api/prefs/finish') { if (!server.finishOk) return {ok: false}; server.state.applied = true; return reply(); }
      if (url === '/setup/api/prefs/skip-all') { NAMES.forEach(n => { if (!(n in server.state.answers) && !server.state.skipped.includes(n)) server.state.skipped.push(n); }); return {ok: true, json: async () => ({})}; }
      if (url === '/setup/api/prefs/skip') { delete server.state.answers[body.step]; if (!server.state.skipped.includes(body.step)) server.state.skipped.push(body.step); return {ok: true, json: async () => ({})}; }
      if (url === '/setup/api/prefs' && options.method === 'POST') { server.state.answers[body.step] = body.value; server.state.skipped = server.state.skipped.filter(n => n !== body.step); return reply(); }
      if (url === '/setup/api/prefs') return reply();
      throw new Error('unexpected request ' + url);
    }};
  ctx.window = ctx;
  vm.createContext(ctx); vm.runInContext(data.script, ctx);
  const flush = async () => { for (let i = 0; i < 3; i++) await new Promise(r => setImmediate(r)); };
  const click = async n => { const h = n.onclick || n.events.click; assert(h, 'no click handler'); await h({target: n}); await flush(); };
  const posts = url => calls.filter(([u, o]) => u === url && o.method === 'POST').length;
  const headingOf = id => get(id).querySelector('h3');
  const pick = (id, value) => { get(id).querySelectorAll('input').find(n => n.value === value).checked = true; };
  const errorText = () => get('jaryo-error').textContent;
  return {ctx, doc, get, visible, server, calls, flush, click, posts, headingOf, pick, errorText};
}
let scenarios = 0;
async function scenario(title, fn) {
  const data = JSON.parse(INPUT);
  const w = world(data); await w.flush();
  assert.equal(typeof (w.ctx.jaryoSetup && w.ctx.jaryoSetup.start), 'function', 'personal steps script failed to initialise');
  assert.equal(vm.runInContext('steps.length', w.ctx), 7);
  try { await fn(w); } catch (e) { e.message = title + ': ' + e.message; throw e; }
  scenarios++;
}
let INPUT = ''; process.stdin.on('data', s => INPUT += s);
process.stdin.on('end', () => main().then(() => console.log('scenarios ok: ' + scenarios)).catch(e => { console.error(e.stack); process.exitCode = 1; }));
async function main() {
  await scenario('inconsistent resume cannot finish partial saved choices', async w => {
    for (const applied of [false, true]) for (const resume of [null, 'done', undefined, 'theme', 'helper']) {
      w.server.state = {answers: {theme: 'dark'}, skipped: [], applied};
      w.server.hook = (url, options) => url === '/setup/api/prefs' && options.method !== 'POST'
        ? {ok: true, json: async () => ({state: w.server.state, resume, effective: {}})} : null;
      await w.ctx.jaryoSetup.start();
      assert(w.visible(w.get('j2')), 'first unfinished step stays reachable');
      assert.equal(w.doc.activeElement, w.headingOf('j2'));
      assert.equal(w.get('jaryo-summary').hidden, true);
      assert.equal(w.posts('/setup/api/prefs/finish'), 0, 'resume cannot apply unfinished choices');
      assert(w.errorText().includes('could not be read'));
    }
  });
  await scenario('resuming or skipping theme restores the saved preview', async w => {
    for (const theme of ['dark', 'light', 'auto']) {
      w.server.state = {answers: {theme}, skipped: [], applied: false};
      await w.ctx.jaryoSetup.start();
      assert.equal(w.get('jaryo-setup').dataset.theme, theme);
      await w.click(w.get('j2').querySelector('[data-jback]'));
      await w.click(w.get('j1').querySelector('[data-jskip]'));
      assert.equal(w.get('jaryo-setup').dataset.theme, 'auto', 'skip restores the recommended theme');
    }
  });
  await scenario('mixed saved answers and skips still complete normally', async w => {
    w.server.state = {answers: {theme: 'dark'}, skipped: NAMES.filter(n => n !== 'theme'), applied: false};
    await w.ctx.jaryoSetup.start();
    assert.equal(w.posts('/setup/api/prefs/finish'), 1);
    assert(w.visible(w.get('jaryo-summary')));
    assert(w.get('jaryo-summary').textContent.startsWith('Your choices are saved and applied.'));
    assert.equal(w.get('jaryo-setup').dataset.theme, 'dark');
  });
  await scenario('conflicting saved and skipped choices cannot confirm a resumed setup', async w => {
    w.server.state = {answers: FULL(), skipped: ['theme'], applied: true};
    await w.ctx.jaryoSetup.start();
    assert.equal(w.get('jaryo-summary').hidden, true);
    assert(w.visible(w.get('jaryo-retry')));
    assert.equal(w.doc.activeElement, w.get('jaryo-retry'));
    assert.equal(w.posts('/setup/api/prefs/finish'), 1);
    w.server.state.skipped = [];
    await w.click(w.get('jaryo-retry'));
    assert(w.visible(w.get('jaryo-summary')));
    assert.equal(w.posts('/setup/api/prefs/finish'), 2);
  });
  await scenario('malformed saved values recover to the first unfinished step', async w => {
    w.server.state = {answers: {...FULL(), theme: 'purple', layout: {favourites: 'phone'}}, skipped: [], applied: false};
    w.server.resume = 'done';
    await w.ctx.jaryoSetup.start();
    assert.equal(w.get('jaryo-error').hidden, false);
    assert(w.errorText().includes('could not be read'), w.errorText());
    assert.equal(w.get('jaryo-retry').hidden, true, 'reloading the same unreadable file is not offered');
    assert(w.visible(w.get('j1')), 'first unfinished step is visible');
    assert(w.visible(w.get('jaryo-skip-all')), 'Skip all is reachable');
    assert.equal(w.get('jaryo-summary').hidden, true);
    assert.equal(w.posts('/setup/api/prefs/finish'), 0, 'nothing is applied while steps are unfinished');
    assert.equal(w.doc.activeElement, w.headingOf('j1'));
    assert.equal(w.headingOf('j1').textContent.trim(), 'Theme');
    assert(!w.get('j1').querySelectorAll('input').some(n => n.checked), 'dropped value leaves nothing selected');
    w.pick('j1', 'light');
    await w.click(w.get('j1').querySelector('[data-jnext]'));
    assert.equal(w.server.state.answers.theme, 'light');
    assert(w.visible(w.get('j2'))); assert.equal(w.doc.activeElement, w.headingOf('j2'));
    assert.equal(w.get('jaryo-error').hidden, true);
    assert(!w.get('j2').querySelectorAll('input').some(n => n.checked), 'unreadable layout leaves nothing selected');
    await w.click(w.get('j2').querySelector('[data-jskip]'));
    assert(w.visible(w.get('j3')));
    assert(w.get('j3').querySelectorAll('input').find(n => n.value === 'large').checked, 'valid saved answers stay selected');
    for (const id of ['j3', 'j4', 'j5']) await w.click(w.get(id).querySelector('[data-jnext]'));
    await w.click(w.get('j6').querySelector('[data-jwifi]'));
    assert(w.visible(w.get('j8'))); assert.equal(w.doc.activeElement, w.headingOf('j8'));
    await w.click(w.get('j8').querySelector('[data-jpin]'));
    assert(w.visible(w.get('j7')));
    await w.click(w.get('j7').querySelector('[data-jnext]'));
    assert.equal(w.errorText(), '');
    assert.equal(w.posts('/setup/api/prefs/finish'), 1);
    assert(w.visible(w.get('jaryo-summary')));
    assert(w.get('jaryo-summary').textContent.startsWith('Your choices are saved and applied.'));
    assert(w.get('jaryo-summary').textContent.includes('Theme: Light'));
    assert(w.get('jaryo-summary').textContent.includes('Layout and favourite apps: Skipped (recommended settings)'));
    assert.equal(w.doc.activeElement, w.get('jaryo-summary'));
  });
  await scenario('Skip all with an unreadable answer shows that step instead of finishing', async w => {
    w.server.state = {answers: {helper: 'maybe'}, skipped: [], applied: false};
    await w.ctx.jaryoSetup.start();
    assert(w.visible(w.get('j1')));
    await w.click(w.get('jaryo-skip-all'));
    assert.deepEqual(w.server.state.answers, {helper: 'maybe'}, 'server still holds the unreadable value');
    assert.equal(w.posts('/setup/api/prefs/finish'), 0);
    assert.equal(w.get('jaryo-summary').hidden, true);
    assert(w.visible(w.get('j7')), 'the unreadable step is shown again');
    assert.equal(w.doc.activeElement, w.headingOf('j7'));
    assert.equal(w.get('jaryo-error').hidden, false);
    assert.equal(w.get('jaryo-retry').hidden, true);
    await w.click(w.get('j7').querySelector('[data-jskip]'));
    assert.deepEqual(w.server.state.answers, {});
    assert.equal(w.posts('/setup/api/prefs/finish'), 1);
    assert(w.visible(w.get('jaryo-summary')));
    assert.equal(w.server.state.applied, true);
    assert(w.get('jaryo-summary').textContent.includes('Helper: Skipped (recommended settings)'));
  });
  await scenario('saved PIN sentinels are record-only summary choices', async w => {
    for (const pin of ['later', 'skip']) {
      w.server.state = {answers: {...FULL(), pin}, skipped: [], applied: true};
      await w.ctx.jaryoSetup.start();
      assert(w.visible(w.get('jaryo-summary')));
      assert.equal(w.errorText(), '');
      assert(w.get('jaryo-summary').textContent.split('\n').includes('Lock PIN: Set up later in Settings › Device'));
      assert.equal(w.posts('/setup/api/prefs/finish'), 0);
    }
  });
  await scenario('corrupt PIN recovers at PIN without displaying digits or finishing', async w => {
    for (const pin of ['1234', {digits: '1234'}]) {
      w.server.state = {answers: {...FULL(), pin}, skipped: [], applied: false};
      w.server.resume = 'done';
      await w.ctx.jaryoSetup.start();
      assert(w.visible(w.get('j8'))); assert.equal(w.doc.activeElement, w.headingOf('j8'));
      assert(w.errorText().includes('could not be read'));
      assert.equal(w.get('jaryo-summary').hidden, true);
      assert(!w.get('jaryo-summary').textContent.includes('1234'));
      assert(!w.errorText().includes('1234'));
      assert.equal(w.posts('/setup/api/prefs/finish'), 0);
      assert.equal(w.get('j8').querySelector('input'), null);
    }
    w.server.resume = undefined;
    await w.click(w.get('j8').querySelector('[data-jpin]'));
    assert(!('pin' in w.server.state.answers));
    assert(w.server.state.skipped.includes('pin'));
    assert(w.visible(w.get('j7')));
    await w.click(w.get('j7').querySelector('[data-jnext]'));
    assert(w.visible(w.get('jaryo-summary')));
    assert(!w.get('jaryo-summary').textContent.includes('1234'));
  });
  await scenario('all PIN controls save only a skip and recover from failed saves', async w => {
    for (const control of ['data-jpin', 'data-jnext', 'data-jskip']) {
      const answers = FULL(); delete answers.pin;
      w.server.state = {answers, skipped: [], applied: false};
      await w.ctx.jaryoSetup.start();
      assert(w.visible(w.get('j8')));
      const before = w.calls.length;
      w.server.failAll = true;
      await w.click(w.get('j8').querySelector('[' + control + ']'));
      assert(w.visible(w.get('j8')));
      assert(w.visible(w.get('jaryo-retry')));
      assert.equal(w.doc.activeElement, w.get('jaryo-retry'));
      assert.equal(w.get('jaryo-retry').textContent, 'Try loading choices again');
      w.server.failAll = false;
      await w.click(w.get('jaryo-retry'));
      assert(w.visible(w.get('j8'))); assert.equal(w.doc.activeElement, w.headingOf('j8'));
      await w.click(w.get('j8').querySelector('[' + control + ']'));
      const writes = w.calls.slice(before).filter(([, o]) => o.method === 'POST');
      assert.equal(writes.length, 2);
      for (const [url, options] of writes) {
        assert.equal(url, '/setup/api/prefs/skip');
        assert.deepEqual(JSON.parse(options.body), {step: 'pin'});
      }
      assert(w.visible(w.get('j7'))); assert.equal(w.doc.activeElement, w.headingOf('j7'));
      assert(w.server.state.skipped.includes('pin'));
      assert(!('pin' in w.server.state.answers));
      assert.equal(w.posts('/setup/api/prefs/finish'), 0);
    }
  });
  await scenario('saved Wi-Fi name is summarised as a preference, never as a connection', async w => {
    w.server.state = {answers: {...FULL(), wifi: {ssid: 'Home <Net>'}}, skipped: [], applied: true};
    await w.ctx.jaryoSetup.start();
    assert(w.visible(w.get('jaryo-summary')));
    assert.equal(w.posts('/setup/api/prefs/finish'), 0, 'already applied choices are not re-applied on resume');
    const lines = w.get('jaryo-summary').textContent.split('\n');
    assert.equal(lines[0], 'Your choices are saved and applied.');
    const line = lines.find(l => l.startsWith('Wi-Fi:'));
    assert(line, 'Wi-Fi line present');
    assert(line.includes('"Home <Net>"'), line);
    assert(line.includes('saved as a preference only'), line);
    assert(line.includes('not connected'), line);
    assert(!line.includes('Wi-Fi: Set up later'), line);
    assert(!/\b(is|are|now) connected\b|\bConnected\b/.test(line), line);
    assert(lines.includes('Text size: Large'));
  });
  await scenario('resumed fully answered but unapplied state finishes before the summary', async w => {
    w.server.state = {answers: FULL(), skipped: [], applied: false};
    let release;
    w.server.hook = url => url === '/setup/api/prefs/finish' ? new Promise(r => { release = r; }) : null;
    const started = w.ctx.jaryoSetup.start(); await w.flush();
    assert.equal(w.posts('/setup/api/prefs/finish'), 1);
    assert(release, 'finish request is in flight');
    assert.equal(w.get('jaryo-summary').hidden, true, 'summary waits for the server');
    assert(!w.get('jaryo-summary').textContent.includes('applied'));
    w.server.state.applied = true;
    release({ok: true, json: async () => ({state: w.server.state, resume: null, effective: {}})});
    await started; await w.flush();
    assert(w.visible(w.get('jaryo-summary')));
    assert(w.get('jaryo-summary').textContent.startsWith('Your choices are saved and applied.'));
    assert.equal(w.get('jaryo-error').hidden, true);
  });
  await scenario('a finish reply that does not confirm applied true is a failure with a focused retry', async w => {
    w.server.state = {answers: FULL(), skipped: [], applied: false};
    const envelope = (patch, over) => ({state: {answers: FULL(), skipped: [], applied: true, ...patch}, resume: null, effective: {}, ...over});
    const bad = [
      ['explicit false', envelope({applied: false})],
      ['empty object', {}],
      ['null body', null],
      ['array body', []],
      ['missing applied', (() => { const e = envelope({}); delete e.state.applied; return e; })()],
      ['string true', envelope({applied: 'true'})],
      ['missing state', {resume: null, effective: {}}],
      ['answers not an object', envelope({answers: null})],
      ['skipped not an array', envelope({skipped: 'nope'})],
      ['no choices', envelope({answers: {}})],
      ['partial choices', envelope({answers: {theme: 'dark'}})],
      ['missing last choice', envelope({answers: Object.fromEntries(Object.entries(FULL()).filter(([name]) => name !== 'helper'))})],
      ['answered and skipped', envelope({skipped: ['theme']})],
      ['unreadable answer', envelope({answers: {...FULL(), theme: 'purple'}})],
      ['unreadable PIN', envelope({answers: {...FULL(), pin: '1234'}})],
      ['unreadable PIN object', envelope({answers: {...FULL(), pin: {digits: '1234'}}})],
      ['unknown skipped name', envelope({skipped: ['ghost']})],
      ['resume not completed', envelope({}, {resume: 'helper'})],
      ['missing resume', (() => { const e = envelope({}); delete e.resume; return e; })()],
      ['missing effective', (() => { const e = envelope({}); delete e.effective; return e; })()],
    ];
    let n = 0;
    for (const [title, body] of bad) {
      w.server.hook = url => url === '/setup/api/prefs/finish' ? {ok: true, json: async () => body} : null;
      if (n === 0) await w.ctx.jaryoSetup.start(); else await w.click(w.get('jaryo-retry'));
      n++;
      assert.equal(w.posts('/setup/api/prefs/finish'), n, title + ': one finish post per attempt');
      assert.equal(w.server.state.applied, false, title + ': server never applied');
      assert.equal(w.get('jaryo-summary').hidden, true, title + ': no summary without confirmation');
      assert(!w.get('jaryo-summary').textContent.includes('applied'), title);
      assert.equal(w.get('jaryo-error').hidden, false, title + ': error shown');
      assert(w.errorText().includes('have not been applied yet'), title + ': ' + w.errorText());
      assert(w.visible(w.get('jaryo-retry')), title + ': retry visible');
      assert.equal(w.get('jaryo-retry').textContent, 'Try finishing again', title);
      assert.equal(w.doc.activeElement, w.get('jaryo-retry'), title + ': retry focused');
      assert(w.visible(w.get('j7')), title + ': last step stays visible');
    }
    w.server.hook = null;
    w.server.state.answers.helper = 'off';
    await w.click(w.get('jaryo-retry'));
    assert.equal(w.posts('/setup/api/prefs/finish'), n + 1, 'the valid retry posts finish once');
    assert.equal(w.posts('/setup/api/prefs'), 0, 'retry never resends answers');
    assert.equal(w.server.state.applied, true);
    assert.equal(w.get('jaryo-error').hidden, true);
    assert.equal(w.get('jaryo-retry').hidden, true);
    assert(w.visible(w.get('jaryo-summary')));
    assert(w.get('jaryo-summary').textContent.startsWith('Your choices are saved and applied.'));
    assert(w.get('jaryo-summary').textContent.includes('Helper: Off'), 'summary reflects the server-confirmed choice');
    assert.equal(w.doc.activeElement, w.get('jaryo-summary'));
    await w.flush();
    assert.equal(w.posts('/setup/api/prefs/finish'), n + 1, 'success does not finish again');
  });
  await scenario('finish failure after the last answer stays visible with a focused retry', async w => {
    const answers = FULL(); delete answers.helper;
    w.server.state = {answers, skipped: [], applied: false};
    w.server.finishOk = false;
    await w.ctx.jaryoSetup.start();
    assert(w.visible(w.get('j7'))); assert.equal(w.doc.activeElement, w.headingOf('j7'));
    w.pick('j7', 'off');
    await w.click(w.get('j7').querySelector('[data-jnext]'));
    assert.equal(w.server.state.answers.helper, 'off', 'answer was saved before finish');
    assert.equal(w.posts('/setup/api/prefs/finish'), 1);
    assert.equal(w.get('jaryo-summary').hidden, true, 'no summary until the server applied the choices');
    assert(!w.get('jaryo-summary').textContent.includes('applied'));
    assert.equal(w.get('jaryo-error').hidden, false);
    assert(w.errorText().includes('have not been applied yet'), w.errorText());
    assert(w.visible(w.get('jaryo-retry')));
    assert.equal(w.get('jaryo-retry').textContent, 'Try finishing again');
    assert.equal(w.doc.activeElement, w.get('jaryo-retry'));
    assert(w.visible(w.get('j7')), 'the last step stays visible for Back and Next');
    w.server.finishOk = true;
    await w.click(w.get('jaryo-retry'));
    assert.equal(w.posts('/setup/api/prefs/finish'), 2);
    assert.equal(w.posts('/setup/api/prefs'), 1, 'retry resends finish only, not the answer');
    assert.equal(w.get('jaryo-error').hidden, true);
    assert.equal(w.get('jaryo-retry').hidden, true);
    assert(w.visible(w.get('jaryo-summary')));
    assert(w.get('jaryo-summary').textContent.includes('Helper: Off'));
    assert.equal(w.doc.activeElement, w.get('jaryo-summary'));
  });
  await scenario('finish failure on resume opens the last step and Next retries the apply', async w => {
    w.server.state = {answers: FULL(), skipped: [], applied: false};
    w.server.finishOk = false;
    await w.ctx.jaryoSetup.start();
    assert.equal(w.get('jaryo-summary').hidden, true);
    assert(w.visible(w.get('j7')), 'a panel with controls is visible after a finish failure');
    assert(w.visible(w.get('jaryo-retry'))); assert.equal(w.doc.activeElement, w.get('jaryo-retry'));
    await w.click(w.get('j7').querySelector('[data-jback]'));
    assert(w.visible(w.get('j8'))); assert.equal(w.doc.activeElement, w.headingOf('j8'));
    await w.click(w.get('j8').querySelector('[data-jback]'));
    assert(w.visible(w.get('j6'))); assert.equal(w.get('jaryo-error').hidden, true);
    await w.click(w.get('j6').querySelector('[data-jwifi]'));
    assert(w.visible(w.get('j8'))); assert.equal(w.doc.activeElement, w.headingOf('j8'));
    await w.click(w.get('j8').querySelector('[data-jpin]'));
    w.server.finishOk = true;
    await w.click(w.get('j7').querySelector('[data-jnext]'));
    assert.equal(w.posts('/setup/api/prefs/finish'), 2);
    assert(w.visible(w.get('jaryo-summary')));
    assert(w.get('jaryo-summary').textContent.includes('Wi-Fi: Skipped (recommended settings); Set up later in Settings › Network'));
  });
  await scenario('load failure offers a focused reload retry that recovers', async w => {
    w.server.failAll = true;
    await w.ctx.jaryoSetup.start();
    assert.equal(w.get('jaryo-error').hidden, false);
    assert(w.visible(w.get('jaryo-retry')));
    assert.equal(w.get('jaryo-retry').textContent, 'Try loading choices again');
    assert.equal(w.doc.activeElement, w.get('jaryo-retry'));
    w.server.failAll = false;
    await w.click(w.get('jaryo-retry'));
    // JRY32: a readable fresh install leads with the edition panel; Customise reveals j1.
    assert(w.visible(w.get('jaryo-edition'))); assert.equal(w.get('jaryo-error').hidden, true); assert.equal(w.get('jaryo-retry').hidden, true);
    await w.click(w.get('jaryo-customise'));
    assert(w.visible(w.get('j1'))); assert.equal(w.get('jaryo-error').hidden, true); assert.equal(w.get('jaryo-retry').hidden, true);
    assert.equal(w.doc.activeElement, w.headingOf('j1'));
  });
  await scenario('every transition focuses the visible heading and validation keeps focus in the form', async w => {
    await w.ctx.jaryoSetup.start();
    // JRY32: the fresh install leads with the edition panel, whose heading is focused like every step's.
    assert(w.visible(w.get('jaryo-edition'))); assert.equal(w.doc.activeElement, w.headingOf('jaryo-edition'));
    await w.click(w.get('jaryo-customise'));
    for (let i = 1; i <= 8; i++) { const h = w.headingOf('j' + i); assert(h, 'j' + i + ' has an h3'); assert.equal(h.attrs.tabindex, '-1'); }
    assert.equal(w.headingOf('j1').parentElement.tag, 'legend');
    assert.equal(w.headingOf('j6').parentElement.tag, 'section');
    assert.equal(w.headingOf('j8').parentElement.tag, 'section');
    assert.equal(w.doc.activeElement, w.headingOf('j1'));
    await w.click(w.get('j1').querySelector('[data-jskip]')); assert.equal(w.doc.activeElement, w.headingOf('j2'));
    await w.click(w.get('j2').querySelector('[data-jback]')); assert.equal(w.doc.activeElement, w.headingOf('j1'));
    for (const id of ['j1', 'j2', 'j3', 'j4', 'j5']) await w.click(w.get(id).querySelector('[data-jskip]'));
    assert(w.visible(w.get('j6'))); assert.equal(w.doc.activeElement, w.headingOf('j6')); assert.equal(w.headingOf('j6').textContent.trim(), 'Wi-Fi');
    await w.click(w.get('j6').querySelector('[data-jwifi]'));
    assert(w.visible(w.get('j8'))); assert.equal(w.doc.activeElement, w.headingOf('j8'));
    await w.click(w.get('j8').querySelector('[data-jpin]'));
    assert.equal(w.doc.activeElement, w.headingOf('j7'));
    await w.click(w.get('j7').querySelector('[data-jnext]'));
    assert.equal(w.errorText(), 'Choose an option above, or use Skip to keep the recommended setting.');
    assert.equal(w.get('jaryo-retry').hidden, true);
    assert.equal(w.doc.activeElement, w.get('j7').querySelector('input'));
    assert.equal(w.posts('/setup/api/prefs/finish'), 0);
    assert(w.visible(w.get('j7')));
  });
  await scenario('a second Next while a save is in flight is not queued', async w => {
    await w.ctx.jaryoSetup.start();
    await w.click(w.get('jaryo-customise'));
    assert(w.visible(w.get('j1')));
    w.pick('j1', 'dark');
    let release;
    w.server.hook = (url, o) => url === '/setup/api/prefs' && o.method === 'POST' ? new Promise(r => { release = r; }) : null;
    const next = w.get('j1').querySelector('[data-jnext]');
    const first = next.onclick(); await w.flush();
    const second = next.onclick(); await w.flush();
    assert.equal(w.posts('/setup/api/prefs'), 1, 'one save request only');
    assert(w.visible(w.get('j1')), 'still on the step while saving');
    w.server.hook = null;
    w.server.state.answers.theme = 'dark';
    release({ok: true, json: async () => ({state: w.server.state, resume: 'layout', effective: {}})});
    await first; await second; await w.flush();
    assert.equal(w.posts('/setup/api/prefs'), 1);
    assert(w.visible(w.get('j2'))); assert.equal(w.doc.activeElement, w.headingOf('j2'));
  });
  await scenario('unknown server resume and unknown skipped names fall back to the first unfinished step', async w => {
    w.server.state = {answers: {theme: 'dark'}, skipped: ['layout', 'ghost'], applied: false};
    w.server.resume = 'ghost';
    await w.ctx.jaryoSetup.start();
    assert.equal(w.get('jaryo-error').hidden, false);
    assert(w.visible(w.get('j3')));
    assert.equal(w.doc.activeElement, w.headingOf('j3'));
    assert(w.get('j1').querySelectorAll('input').find(n => n.value === 'dark').checked, 'readable answers are kept');
    assert.equal(w.posts('/setup/api/prefs/finish'), 0);
  });
  await scenario('a wholly broken state shape still shows the first step', async w => {
    w.server.state = {answers: null, skipped: 'nope', applied: false};
    w.server.resume = 'done';
    await w.ctx.jaryoSetup.start();
    assert.equal(w.get('jaryo-error').hidden, false);
    assert(w.visible(w.get('j1'))); assert(w.visible(w.get('jaryo-skip-all')));
    assert.equal(w.posts('/setup/api/prefs/finish'), 0);
    assert.equal(w.get('jaryo-summary').hidden, true);
  });
}
"""

if __name__ == '__main__':
    unittest.main()
