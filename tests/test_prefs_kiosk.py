# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from unittest.mock import patch

from opos import web
from opos.setup import prefs
from opos.web import Phone, page

STATIC = Path(__file__).resolve().parents[1] / 'src/opos/web_static'


class TestPrefsKiosk(unittest.TestCase):
    def test_state_defaults_and_skip_all(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPOS_CONFIG_DIR=directory):
            self.assertEqual(Phone().state()['prefs'], prefs.defaults())
            prefs.skip_all()
            self.assertEqual(Phone().state()['prefs'], prefs.defaults())

    def test_effective_and_redaction(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPOS_CONFIG_DIR=directory):
            prefs.answer('theme', 'dark')
            prefs.answer('text_size', 'large')
            prefs.answer('helper', 'off')
            prefs.answer('wifi', {'ssid': 'private-network'})
            prefs.answer('pin', 'skip')
            prefs.finish()
            state = Phone().state()
            self.assertEqual(state['prefs']['theme'], 'dark')
            self.assertEqual(state['prefs']['text_size'], 'large')
            self.assertEqual(state['prefs']['helper'], 'off')
            self.assertNotIn('ssid', json.dumps(state))
            self.assertNotIn('private-', json.dumps(state))
            self.assertEqual(state['prefs']['wifi'], 'later')
            # JRY34: the lock PIN step is record-only and never published; the kiosk only ever sees the default.
            self.assertEqual(state['prefs']['pin'], 'later')

    def test_answers_and_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPOS_CONFIG_DIR=directory):
            prefs.answer('theme', 'dark')
            self.assertEqual(Phone().state()['prefs']['theme'], 'auto')
            prefs.finish()
            self.assertEqual(Phone().state()['prefs']['theme'], 'dark')
            prefs.prefs_path().write_text('{broken')
            self.assertEqual(Phone().state()['prefs'], prefs.defaults())

    def _main_phone(self, argv):
        captured = {}

        def fake_make_server(port, phone, host='127.0.0.1', token=None):
            captured['phone'] = phone
            return mock.Mock(serve_forever=mock.Mock())

        with mock.patch.object(web, '_route_with_glimmer'), \
                mock.patch.object(web, 'make_server', side_effect=fake_make_server):
            web.main(argv)
        return captured['phone']

    def test_main_applies_helper_preference(self):
        # JRY24-rfix2: step j7 is shared server state, so the service start reads it (docs/jaryo.md section 6).
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPOS_CONFIG_DIR=directory):
            self.assertFalse(self._main_phone([]).visible)  # no prefs file: the old default
            prefs.answer('helper', 'on')
            self.assertFalse(self._main_phone([]).visible)  # answered but setup not finished
            prefs.finish()
            self.assertTrue(self._main_phone([]).visible)
            prefs.answer('helper', 'off')
            self.assertFalse(self._main_phone([]).visible)
            self.assertTrue(self._main_phone(['--visible']).visible)  # the flag always wins
            prefs.prefs_path().write_text('{broken')
            self.assertFalse(self._main_phone([]).visible)

    def test_browser_defaults_local_choices_and_refresh(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        # Files load whole and in page order (phone-b.js, jaryo.js, jaryo-layout.js), as web.py inlines them.
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const files=process.argv.slice(1,4).map(f=>fs.readFileSync(f,'utf8'));
function run(saved,prefs){
 prefs=Object.assign({theme:'dark',text_size:'large',device_type:'tablet',helper:'on',layout:{favourites:['phone','settings'],sizes:{phone:'wide'}}},prefs);
 const store={...saved},listeners={},tiles=['phone','messages','helper','settings'].map(id=>({dataset:{open:id}}));
 const text={addEventListener(){}},radio={},root={dataset:{}},calls=[];
 const ff={querySelector(){return radio},addEventListener(k,f){this[k]=f}};
 const home={querySelectorAll(){return tiles},querySelector(){return null},addEventListener(){}};
 const c={Promise,location:{search:''},addEventListener(){},document:{documentElement:root,addEventListener(k,f){listeners[k]=f},
 getElementById(id){return id==='textsize'?text:id==='ff'?ff:id==='home'?home:null}},
 localStorage:{getItem(k){return k in store?store[k]:null},setItem(k,v){store[k]=v},removeItem(k){delete store[k]}},
 api(v){calls.push(v);return Promise.resolve({})},render(s){for(const hook of c.oposHooks)hook(s)},
 toggleVis(){calls.push('toggle');return Promise.resolve()}};
 c.window=c;vm.createContext(c);
 for(const src of files)vm.runInContext(src,c);
 const s={helper_visible:false,prefs};
 c.render(s);
 assert.equal(text.value,root.dataset.text);assert.equal(radio.checked,true);
 const first={...root.dataset};root.dataset.text='xl';
 c.render(s);
 assert.equal(root.dataset.text,'xl');
 // Helper on/off is server state: loading the page never calls the API or toggles Helper.
 assert.equal(calls.length,0);
 assert.equal(tiles[0].dataset.size,saved.jaryo_tiles?'small':'wide');
 assert.equal(tiles[1].dataset.size,'medium');assert.equal(tiles[3].dataset.size,'small');
 // Favourites are recorded only: no tile is ever hidden by the layout hook.
 assert(tiles.every(t=>t.hidden===undefined));
 // A corrupted size store still leaves the default sizes applied.
 store.jaryo_tiles='{broken';for(const t of tiles)delete t.dataset.size;c.render(s);
 assert.equal(tiles[0].dataset.size,'medium');assert.equal(tiles[2].dataset.size,'small');
 // A real fieldset change: phone-b's #ff handler (removes the key for Auto) then the document bubble listener.
 const target={value:'auto',closest(){return ff}};
 assert.equal(typeof ff.change,'function');
 ff.change({target});listeners.change({target});
 assert.equal(store.opos_ff,'auto');
 assert.equal(root.dataset.ff,undefined);
 if(prefs.theme==='auto')assert.equal(store.jaryo_theme,undefined);
 return {first,store};
}
assert.deepEqual(run({}).first,{theme:'dark',text:'l',ff:'tablet'});
assert.deepEqual(run({jaryo_theme:'light',opos_text:'n',opos_ff:'slab',jaryo_tiles:'{"phone":"small"}'}).first,{theme:'light',text:'n',ff:'slab'});
assert.deepEqual(run({},{theme:'auto'}).first,{text:'l',ff:'tablet'});
// An explicit Auto survives the next load: the install default never overwrites it.
const again=run({opos_ff:'auto'});
assert.equal(again.first.ff,undefined);assert.equal(again.store.opos_ff,'auto');
assert.equal(run({},{helper:'off'}).store.opos_helper_visible,undefined);
// Storage failures and absent preferences must leave the shell usable.
const c={location:{search:''},addEventListener(){},document:{documentElement:{},addEventListener(){},getElementById(){return null}},localStorage:{getItem(){throw Error('blocked')}}};c.window=c;
vm.createContext(c);for(const src of files)vm.runInContext(src,c);
for(const hook of c.oposHooks){hook({});hook({prefs:{}})}
'''
        result = subprocess.run([node, '-e', script, str(STATIC / 'phone-b.js'),
                                 str(STATIC / 'jaryo.js'),
                                 str(STATIC / 'jaryo-layout.js')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inline_and_budget(self):
        from tests.test_uxp30_budget import PAGE_BUDGET
        rendered = page()
        for name in ('jaryo.js', 'jaryo-layout.js'):
            self.assertIn((STATIC / name).read_text(), rendered)
        self.assertLessEqual(len(rendered.encode()), PAGE_BUDGET)
