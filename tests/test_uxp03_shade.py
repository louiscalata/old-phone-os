# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
import http.client

from opos.web import Phone, make_server, page

JS = Path(__file__).resolve().parents[1] / 'src/opos/web_static/phone-a.js'
NODE = shutil.which('node')
# The page builds window.oposUI itself and no script replaces it (a notice is stamped with its hhmm), so the stubs below go onto that object.
HARNESS = r"""
const fs=require('fs'),vm=require('vm');
class Element {
  constructor(id=''){this.id=id;this.hidden=false;this.children=[];this.attrs={};this.text=''}
  appendChild(child){this.children.push(child);child.parent=this;return child}
  insertBefore(child,before){
    if(before===null)this.appendChild(child);
    else {const i=this.children.indexOf(before);if(i<0)throw Error('not a child');
      this.children.splice(i,0,child);child.parent=this}
  }
  get firstChild(){return this.children[0]||null}
  get nextSibling(){return this.parent.children[this.parent.children.indexOf(this)+1]||null}
  set textContent(value){this.text=value;this.children=[]}
  get textContent(){return this.text+this.children.map(c=>c.textContent).join('')}
  setAttribute(key,value){this.attrs[key]=String(value)}
}
const root=new Element(),sheet=new Element('sheet');
sheet.appendChild(new Element('sheet-handle'));sheet.appendChild(new Element('time-left'));
const mode=process.argv[2];if(mode!=='missing')root.appendChild(sheet);
function find(node,id){if(node.id===id)return node;
  for(const child of node.children){const found=find(child,id);if(found)return found}return null}
const get=id=>find(root,id),calls=[];
const c={document:{getElementById:get,createElement:()=>new Element()},
  setInterval(){},navigator:{onLine:true},
  closeSheet(){calls.push('close')},toggleVis(){calls.push('toggle');return Promise.reject(Error('offline'))}};
c.window=c;vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
c.oposUI=Object.assign(c.oposUI||{},{lock(){calls.push('lock')},openApp(name){calls.push(name)}});
// Exercise the shade hook registered by the complete, unmodified script.
const hook=c.oposHooks[0],out={};
hook({helper_visible:false,notice:''});
if(mode==='missing'){
  out.missing=get('qs')===null;root.appendChild(sheet);hook({});out.recovered=!!get('qs');
}else{
  const qs=get('qs'),helper=get('qs-helper'),settings=get('qs-settings');
  out.initial=[qs.hidden,helper.textContent,helper.attrs['aria-pressed'],settings.hidden];
  out.order=sheet.children.map(e=>e.id);
  out.controls=qs.children.map(e=>[e.id,e.type||null]);
  out.empty=get('qs-notices').children.map(e=>e.textContent);
  out.name=get('qs-notices').attrs['aria-label'];
  hook({helper_visible:true,notice:'first'});hook({helper_visible:true,notice:'first'});
  out.dedup=get('qs-notices').children.map(e=>e.children[2].textContent);
  out.on=[helper.textContent,helper.attrs['aria-pressed']];
  for(const notice of ['second','third','fourth','fifth','<b>sixth</b>'])hook({notice});
  hook({notice:'   '});hook({notice:null});
  out.notices=get('qs-notices').children.map(e=>e.children[2].textContent);
  out.off=[helper.textContent,helper.attrs['aria-pressed']];
  out.same=qs===get('qs');out.count=sheet.children.filter(e=>e.id==='qs').length;
  root.appendChild(new Element('settings'));hook({});out.settingsVisible=!settings.hidden;
  helper.onclick();get('qs-lock').onclick();settings.onclick();
  c.toggleVis=()=>{throw Error('offline')};helper.onclick();
  delete c.toggleVis;helper.onclick();delete c.closeSheet;delete c.oposUI;
  get('qs-lock').onclick();settings.onclick();
  out.calls=calls;
}
setImmediate(()=>console.log(JSON.stringify(out)));
"""


class TestShade(unittest.TestCase):
    def run_js(self, mode='normal'):
        if not NODE:
            self.skipTest('node not installed')
        result = subprocess.run(
            [NODE, '--unhandled-rejections=strict', '-e', HARNESS, str(JS), mode],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_shade_js_has_controls(self):
        self.assertIn(JS.read_text(), page())
        out = self.run_js()
        self.assertEqual(out['initial'], [False, 'Helper: Off', 'false', True])
        self.assertEqual(out['order'], ['sheet-handle', 'qs', 'time-left'])
        self.assertEqual(out['controls'], [['qs-close', 'button'], ['qs-batt', 'button'],
                                           ['qs-helper', 'button'], ['qs-lock', 'button'],
                                           ['qs-settings', 'button'], ['qs-notices', None]])
        self.assertTrue(out['same'])
        self.assertEqual(out['count'], 1)
        self.assertTrue(out['settingsVisible'])
        self.assertEqual(out['calls'], ['toggle', 'close', 'lock', 'close', 'settings'])
        self.assertEqual(out['name'], 'Recent notices')

    def test_toggle_is_server_truth(self):
        server = make_server(0, Phone())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for visible in (True, False):
                with self.subTest(visible=visible):
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
                    try:
                        connection.request(
                            'POST', '/api',
                            body=json.dumps({'action': 'visible', 'visible': visible}).encode(),
                            headers={'Content-Type': 'application/json',
                                     'X-OPOS-Token': server.RequestHandlerClass.token})
                        with connection.getresponse() as response:
                            self.assertEqual(response.status, 200)
                            state = json.load(response)
                        self.assertIs(state['helper_visible'], visible)
                    finally:
                        connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        out = self.run_js()
        self.assertEqual(out['on'], ['Helper: On', 'true'])
        self.assertEqual(out['off'], ['Helper: Off', 'false'])

    def test_notice_dedup_logic(self):
        out = self.run_js()
        self.assertEqual(out['empty'], ['No recent notices'])
        self.assertEqual(out['dedup'], ['first'])
        self.assertEqual(out['notices'], ['<b>sixth</b>', 'fifth', 'fourth', 'third', 'second'])

    def test_missing_sheet_is_fail_open(self):
        out = self.run_js('missing')
        self.assertTrue(out['missing'])
        self.assertTrue(out['recovered'])


if __name__ == '__main__':
    unittest.main()
