# SPDX-License-Identifier: GPL-3.0-or-later
"""Setup tile sizes: native controls, stored contract and active wizard behavior.

The VM uses the existing recovery harness with mocked HTTP only. Browser-level
selection/persistence is covered separately; these tests start no application server.
"""
import json
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from opos.setup import prefs, wizard_page
from tests.test_jaryo_setup_recovery import _Dom, _script, RUNTIME as _RECOVERY_RUNTIME

NODE = shutil.which('node')
APPS = ('phone', 'messages', 'helper', 'settings')
DEFAULTS = {'phone': 'medium', 'messages': 'medium', 'helper': 'small', 'settings': 'small'}
SIZES = ('small', 'medium', 'wide')


class TileSizeMarkupTests(unittest.TestCase):
    def test_four_native_labelled_controls_and_defaults_are_in_layout_step(self):
        dom = _Dom(wizard_page.page('tok', 'nonce123'))
        section = dom.by_id('j2')
        selects = [i for i in dom.descendants(section, 'select')
                   if 'data-jtile-size' in dom.nodes[i]['attrs']]
        self.assertEqual([dom.nodes[i]['attrs']['data-jtile-size'] for i in selects], list(APPS))
        for app in APPS:
            with self.subTest(app=app):
                element_id = 'jaryo-size-' + app
                matches = [i for i, n in enumerate(dom.nodes) if n['attrs'].get('id') == element_id]
                self.assertEqual(len(matches), 1, 'Each control has a unique native label target')
                index = matches[0]
                node = dom.nodes[index]
                self.assertEqual(node['tag'], 'select')
                self.assertTrue(dom.inside(index, section))
                self.assertNotIn('disabled', node['attrs'])
                options = [dom.nodes[i] for i in dom.descendants(index, 'option')]
                self.assertEqual([n['attrs'].get('value') for n in options], list(SIZES))
                selected = [n['attrs']['value'] for n in options if 'selected' in n['attrs']]
                self.assertEqual(selected, [DEFAULTS[app]])
                labels = [n['text'] for n in dom.nodes
                          if n['tag'] == 'label' and n['attrs'].get('for') == element_id]
                parent = node['parent']
                while parent >= 0:
                    ancestor = dom.nodes[parent]
                    if ancestor['tag'] == 'label':
                        labels.append(ancestor['text'])
                    parent = ancestor['parent']
                if node['attrs'].get('aria-label'):
                    labels.append(node['attrs']['aria-label'])
                self.assertTrue(labels, 'The native select has an accessible text label')
                self.assertIn(app, ' '.join(labels).lower())
                self.assertRegex(' '.join(labels).lower(), r'tile|size')
        # The four app checkboxes remain an independent recorded preference.
        checkboxes = [dom.nodes[i]['attrs'].get('value') for i in dom.descendants(section, 'input')
                      if dom.nodes[i]['attrs'].get('type') == 'checkbox']
        self.assertEqual(checkboxes, list(APPS))

    def test_chosen_sizes_persist_independently_of_favourites(self):
        selected = {'phone': 'wide', 'messages': 'small', 'helper': 'medium', 'settings': 'wide'}
        value = {'favourites': ['settings'], 'sizes': selected}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prefs.json'
            prefs.answer('layout', value, path)
            prefs.skip_all(path)
            effective = prefs.finish(path)
            self.assertEqual(prefs.load(path)['answers']['layout'], value)
            self.assertEqual(effective['layout'], value)
            self.assertTrue(prefs.load(path)['applied'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_backend_keeps_legacy_null_partial_and_allapps_compatibility(self):
        for sizes in (None, {}, {'phone': 'wide'}, {'allapps': 'wide'}):
            with self.subTest(sizes=sizes):
                prefs.validate_answer('layout', {'favourites': [], 'sizes': sizes})
        for sizes in ([], 'wide', {'phone': 'huge'}, {'unknown': 'small'}, {'settings': None}):
            with self.subTest(sizes=sizes):
                with self.assertRaises(ValueError):
                    prefs.validate_answer('layout', {'favourites': [], 'sizes': sizes})


# Reuse the fake-DOM/HTTP boundary, not any existing scenario or unittest method.
_PREFIX = _RECOVERY_RUNTIME.split('let scenarios = 0;', 1)[0]
_HELPERS = r"""
const APPS = ['phone', 'messages', 'helper', 'settings'];
const DEFAULTS = {phone:'medium', messages:'medium', helper:'small', settings:'small'};
const CUSTOM = {phone:'wide', messages:'small', helper:'medium', settings:'wide'};
const sizeControls = w => Object.fromEntries(APPS.map(id => [id, w.get('jaryo-size-' + id)]));
const values = w => Object.fromEntries(APPS.map(id => [id, sizeControls(w)[id].value]));
async function fresh() {
  const w = world(JSON.parse(INPUT));
  // The shared fixture has plain values. Supply native option defaults in this
  // test's fixture before start(), without changing the existing harness/tests.
  for (const select of Object.values(sizeControls(w))) {
    assert(select, 'missing native size select');
    select.options = select.children.filter(n => n.tag === 'option');
    const option = select.options.find(n => 'selected' in n.attrs) || select.options[0];
    select.value = option ? option.value : '';
  }
  await w.flush();
  return w;
}
function setSizes(w, sizes) { for (const app of APPS) sizeControls(w)[app].value = sizes[app]; }
function setFavourites(w, apps) { w.get('j2').querySelectorAll('input').forEach(n => { n.checked = apps.includes(n.value); }); }
function layoutPosts(w) {
  return w.calls.filter(([url, o]) => url === '/setup/api/prefs' && o.method === 'POST')
    .map(([, o]) => JSON.parse(o.body)).filter(b => b.step === 'layout');
}
function completeState(layout, applied=false) {
  return {answers: {...FULL(), layout}, skipped: [], applied};
}
function assertSizes(w, expected, note) { assert.deepStrictEqual(values(w), expected, note); }
function assertNamedSizes(text, expected) {
  for (const app of APPS) {
    const title = app[0].toUpperCase() + app.slice(1);
    const size = expected[app][0].toUpperCase() + expected[app].slice(1);
    assert(text.includes(title + ': ' + size), 'Summary must name ' + title + ' size ' + size + ': ' + text);
  }
}
let INPUT=''; process.stdin.on('data', s => INPUT += s);
"""


@unittest.skipUnless(NODE, 'node not installed')
class TileSizeWizardRuntimeTests(unittest.TestCase):
    def run_scenario(self, body):
        page = wizard_page.page('tok', 'nonce123')
        payload = json.dumps({'nodes': _Dom(page).nodes, 'script': _script(page)})
        program = (_PREFIX + _HELPERS + '\nasync function main() {\n' + body + '\n}\n'
                   "process.stdin.on('end', () => main().then(() => console.log('tile-size scenario ok'))"
                   ".catch(e => { console.error(e.stack); process.exitCode=1; }));\n")
        result = subprocess.run([NODE, '--unhandled-rejections=strict', '-e', program],
                                input=payload, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('tile-size scenario ok', result.stdout)

    def test_save_all_four_sizes_independent_of_checked_favourites(self):
        self.run_scenario(r"""
const w = await fresh();
w.server.state = {answers:{theme:'dark'}, skipped:[], applied:false};
await w.ctx.jaryoSetup.start();
assert(w.visible(w.get('j2')));
assertSizes(w, DEFAULTS, 'fresh defaults');
setFavourites(w, ['settings']); setSizes(w, CUSTOM);
await w.click(w.get('j2').querySelector('[data-jnext]'));
assert.deepStrictEqual(layoutPosts(w), [{step:'layout', value:{favourites:['settings'], sizes:CUSTOM}}]);
assert.deepStrictEqual(w.server.state.answers.layout, {favourites:['settings'], sizes:CUSTOM});
assert(w.visible(w.get('j3')));
await w.click(w.get('j3').querySelector('[data-jback]'));
assert(w.visible(w.get('j2')));
assertSizes(w, CUSTOM, 'back restores the saved sizes');
assert.deepStrictEqual(w.get('j2').querySelectorAll('input').filter(n=>n.checked).map(n=>n.value), ['settings']);
""")

    def test_resume_absent_null_partial_and_optional_allapps_sizes(self):
        self.run_scenario(r"""
for (const [layout, expected] of [
  [{favourites:['phone']}, DEFAULTS],
  [{favourites:['phone'], sizes:null}, DEFAULTS],
  [{favourites:['phone'], sizes:{}}, DEFAULTS],
  [{favourites:['phone'], sizes:{phone:'wide',settings:'medium'}}, {...DEFAULTS,phone:'wide',settings:'medium'}],
  [{favourites:['phone'], sizes:{allapps:'wide'}}, DEFAULTS]
]) {
  const w = await fresh(); w.server.state = completeState(layout);
  await w.ctx.jaryoSetup.start();
  assertSizes(w, expected, 'legacy/partial data');
  assert.equal(w.posts('/setup/api/prefs/finish'), 1);
  assert(w.visible(w.get('jaryo-summary')));
  assert(w.get('jaryo-summary').textContent.startsWith('Your choices are saved and applied.'));
  assertNamedSizes(w.get('jaryo-summary').textContent, expected);
  assert.equal(w.errorText(), '');
}
""")

    def test_corrupt_saved_sizes_drop_layout_and_allow_explicit_recovery(self):
        self.run_scenario(r"""
for (const bad of [[], 'wide', {phone:'huge'}, {unknown:'small'}, {settings:null}]) {
  const w = await fresh(); w.server.state = completeState({favourites:['phone'],sizes:bad}, true);
  await w.ctx.jaryoSetup.start();
  assert(w.visible(w.get('j2')), 'invalid sizes keep layout reachable');
  assert.equal(w.doc.activeElement, w.headingOf('j2'));
  assert(w.errorText().includes('could not be read'));
  assert.equal(w.posts('/setup/api/prefs/finish'), 0);
  assert.equal(w.get('jaryo-summary').hidden, true);
  assertSizes(w, DEFAULTS, 'invalid data does not enter native controls');
  setFavourites(w, ['messages']); setSizes(w, CUSTOM);
  await w.click(w.get('j2').querySelector('[data-jnext]'));
  assert.deepStrictEqual(layoutPosts(w).at(-1), {step:'layout', value:{favourites:['messages'],sizes:CUSTOM}});
  assert.equal(w.errorText(), '');
}
""")

    def test_invalid_finish_sizes_cannot_confirm_but_finish_retry_recovers(self):
        self.run_scenario(r"""
for (const bad of [[], {phone:'huge'}, {intruder:'wide'}]) {
  const w = await fresh(); w.server.state = completeState({favourites:['settings'],sizes:CUSTOM});
  w.server.hook = url => url === '/setup/api/prefs/finish'
    ? {ok:true,json:async()=>({state:completeState({favourites:['settings'],sizes:bad},true),resume:null,effective:{}})} : null;
  await w.ctx.jaryoSetup.start();
  assert.equal(w.posts('/setup/api/prefs/finish'), 1);
  assert.equal(w.get('jaryo-summary').hidden, true);
  assert(w.errorText().includes('have not been applied yet'));
  assert(w.visible(w.get('jaryo-retry')));
  assert.equal(w.get('jaryo-retry').textContent, 'Try finishing again');
  assert.equal(w.doc.activeElement, w.get('jaryo-retry'));
  w.server.hook = null;
  await w.click(w.get('jaryo-retry'));
  assert.equal(w.posts('/setup/api/prefs/finish'), 2);
  assert(w.visible(w.get('jaryo-summary')));
  assertNamedSizes(w.get('jaryo-summary').textContent, CUSTOM);
}
""")

    def test_back_skip_and_skip_all_do_not_save_unsent_sizes(self):
        self.run_scenario(r"""
const w = await fresh();
w.server.state = {answers:{theme:'dark'}, skipped:[], applied:false};
await w.ctx.jaryoSetup.start(); setSizes(w, CUSTOM);
await w.click(w.get('j2').querySelector('[data-jback]'));
assert(w.visible(w.get('j1'))); assert.equal(layoutPosts(w).length,0);
await w.click(w.get('j1').querySelector('[data-jskip]'));
assert(w.visible(w.get('j2'))); assertSizes(w,DEFAULTS,'unsaved values are not a saved layout');
await w.click(w.get('j2').querySelector('[data-jskip]'));
assert.equal(layoutPosts(w).length,0); assert(!('layout' in w.server.state.answers));
assert(w.server.state.skipped.includes('layout'));
await w.click(w.get('j3').querySelector('[data-jback]'));
await w.click(w.get('j2').querySelector('[data-jback]'));
assert(w.visible(w.get('j1')));
await w.click(w.get('jaryo-skip-all'));
assert.equal(layoutPosts(w).length,0);
assert(w.visible(w.get('jaryo-summary')));
const text=w.get('jaryo-summary').textContent;
assert(text.includes('Layout and favourite apps: Skipped (recommended settings)'));
assertNamedSizes(text,DEFAULTS);
""")

    def test_edit_save_preserves_optional_allapps_and_restores_after_reopen(self):
        self.run_scenario(r"""
const w = await fresh();
w.server.state = {answers:{theme:'dark',layout:{favourites:['settings'],sizes:{allapps:'wide',phone:'small'}}},skipped:[],applied:false};
await w.ctx.jaryoSetup.start();
assert(w.visible(w.get('j3')));
await w.click(w.get('j3').querySelector('[data-jback]'));
assert(w.visible(w.get('j2')));
assertSizes(w,{...DEFAULTS,phone:'small'},'partial legacy map restores before editing');
setSizes(w,CUSTOM);
await w.click(w.get('j2').querySelector('[data-jnext]'));
const expected={favourites:['settings'],sizes:{...CUSTOM,allapps:'wide'}};
assert.deepStrictEqual(layoutPosts(w),[{step:'layout',value:expected}], 'saving rendered controls retains the supported unrendered key');
assert.deepStrictEqual(w.server.state.answers.layout,expected,'stored readback keeps allapps and the four edited sizes');
await w.click(w.get('j3').querySelector('[data-jback]'));
assertSizes(w,CUSTOM,'Back keeps edited controls');
assert.equal(w.server.state.answers.layout.sizes.allapps,'wide');
const reopened=await fresh();
reopened.server.state=JSON.parse(JSON.stringify(w.server.state));
await reopened.ctx.jaryoSetup.start();
assert(reopened.visible(reopened.get('j3')));
await reopened.click(reopened.get('j3').querySelector('[data-jback]'));
assert(reopened.visible(reopened.get('j2')));
assertSizes(reopened,CUSTOM,'new wizard restores the stored roundtrip');
assert.equal(reopened.server.state.answers.layout.sizes.allapps,'wide');
assert.equal(reopened.get('j2').querySelectorAll('[data-jtile-size]').length,4,'legacy compatibility does not add a fifth control');
assert.equal(layoutPosts(reopened).length,0,'reading and Back do not save again');
""")

    def test_summary_distinguishes_recorded_favourites_from_actual_sizes(self):
        self.run_scenario(r"""
const w = await fresh(); w.server.state=completeState({favourites:['settings'],sizes:CUSTOM});
await w.ctx.jaryoSetup.start();
assert(w.visible(w.get('jaryo-summary')));
const text=w.get('jaryo-summary').textContent;
assert(/favourite(?: choices|s)[^\n]*recorded only/i.test(text), text);
assert(/home[^\n]*unchanged/i.test(text), text);
assert(text.includes('Starting tile sizes: '));
assertNamedSizes(text,CUSTOM);
""")


if __name__ == '__main__':
    unittest.main()
