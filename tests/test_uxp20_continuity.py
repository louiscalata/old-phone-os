# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport continuity uses the existing #msg id (the spec calls it #message)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from opos.web import page
from tests import test_uxr_acceptance as acceptance

JS = Path(__file__).resolve().parents[1] / 'src/opos/web_static/phone-a.js'
NODE = shutil.which('node')
HARNESS = r"""
const assert=require('assert'),vm=require('vm'),fs=require('fs');
// UXP23 is a separate render module, outside the continuity handler.
const source=fs.readFileSync(process.argv[1],'utf8').split('// UXP20:')[1].split('// UXP23:')[0];
const handlers={},frames=[],els={};
function listen(scope){return function(n,fn,opts){(handlers[scope+':'+n]||=[]).push({fn,opts})}}
function fire(scope,n,e={}){for(const h of handlers[scope+':'+n]||[])h.fn(e)}
let c,open=false,sends=0,calls=0;
function el(id,tagName='DIV'){
 return els[id]={id,tagName,hidden:true,dataset:{},value:'',scrollTop:0,
  selectionStart:0,selectionEnd:0,selectionDirection:'none',
  addEventListener:listen(id),
  focus(){c.document.activeElement=this;fire('doc','focusin')},
  setSelectionRange(a,b,d){this.selectionStart=a;this.selectionEnd=b;this.selectionDirection=d}};
}
const msg=el('msg','INPUT'),number=el('number','INPUT'),sheet=el('sheet'),lock=el('lock'),phone=el('phone');
const body=el('body'),html=el('html');html.setAttribute=function(k,v){this[k]=v};
c={document:{body,documentElement:html,activeElement:body,getElementById:id=>els[id],addEventListener:listen('doc')},
 addEventListener:listen('win'),visualViewport:{addEventListener:listen('vv')},
 scrollX:0,scrollY:0,scrollTo(x,y){this.scrollX=x;this.scrollY=y},
 requestAnimationFrame:f=>frames.push(f),opos:{isOpen:()=>open},
 oposUI:{openApp:n=>phone.dataset.app=n},go:a=>{assert.ok(['send','call'].includes(a));if(a==='send')sends++;else calls++;return Promise.resolve()}};
c.window=c;vm.runInNewContext('// UXP20:'+source,c);
function flush(){while(frames.length)frames.shift()()}
function key(target,key,extra={}){const e=Object.assign({target,key,code:key,isTrusted:true,preventDefault(){this.defaultPrevented=true}},extra);fire('win','keydown',e);return e}
msg.focus();msg.value='keep this draft';msg.setSelectionRange(2,9,'backward');fire('doc','input');
c.scrollY=140;sheet.scrollTop=60;fire('sheet','scroll');
// Reflow clamps scroll and drops focus before the resize event arrives.
c.scrollY=0;sheet.scrollTop=0;c.document.activeElement=body;
fire('win','resize');msg.setSelectionRange(0,0,'none');fire('sheet','scroll');flush();
assert.equal(c.document.activeElement,msg);assert.equal(msg.value,'keep this draft');
assert.deepEqual([msg.selectionStart,msg.selectionEnd,msg.selectionDirection],[2,9,'backward']);
assert.equal(c.scrollY,0);assert.equal(sheet.scrollTop,60);
// A callback that never runs must not prevent recording a later edit.
fire('win','resize');msg.setSelectionRange(3,5,'forward');fire('doc','input');flush();
assert.equal(msg.selectionStart,3);
open=true;lock.hidden=false;fire('doc','focusin');fire('vv','resize');flush();
assert.equal(open,true);assert.equal(lock.hidden,false);
// A deliberate edit or focus change wins over a queued restore.
fire('win','resize');msg.value='new edit';fire('doc','input');flush();assert.equal(msg.value,'new edit');
fire('win','resize');number.focus();flush();assert.equal(c.document.activeElement,number);
assert.equal(html['data-kbd'],undefined);key(msg,'Tab',{isTrusted:false});assert.equal(html['data-kbd'],undefined);
key(msg,'Tab',{isComposing:true});assert.equal(html['data-kbd'],undefined);
msg.focus();key(msg,'Tab');assert.equal(html['data-kbd'],'1');
assert.equal(key(msg,'Enter').defaultPrevented,true);assert.equal(sends,1);
for(const extra of [{repeat:true},{defaultPrevented:true},{ctrlKey:true},{isComposing:true}])key(msg,'Enter',extra);
key(msg,'a');assert.equal(sends,1);
// Enter in the dialer must never dial (or send).
number.focus();const e=key(number,'Enter');assert.equal(e.defaultPrevented,undefined);assert.equal(calls,0);assert.equal(sends,1);
for(const scope of ['win','vv'])assert.equal(handlers[scope+':resize'][0].opts.passive,true);
// Callback faults are contained, including an input type without selection support.
msg.setSelectionRange=()=>{throw Error('unsupported')};msg.focus();fire('win','resize');flush();
c.document.getElementById=()=>{throw Error('broken')};fire('vv','resize');key(msg,'Enter');flush();
console.log(JSON.stringify({ok:true}));
"""


class TestContinuityLogic(unittest.TestCase):
    @unittest.skipUnless(NODE, 'no node')
    def test_restore_keyboard_and_fail_open(self):
        result = subprocess.run([NODE, '-e', HARNESS, str(JS)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'ok': True})

    def test_no_rebuild_or_layout_handlers(self):
        # UXP23 follows continuity and builds its own panels, not viewport handlers.
        source = JS.read_text().split('// UXP20:', 1)[1].split('// UXP23:', 1)[0]
        self.assertIn(source, page())
        for forbidden in ('innerHTML', 'createElement', 'appendChild', 'replaceChild',
                          'location.reload', 'matchMedia', 'orientationchange'):
            self.assertNotIn(forbidden, source)
        self.assertIn('id="msg"', page())


class TestContinuityBrowser(unittest.TestCase):
    # Reuse exactly the acceptance harness's Chromium discovery and sandbox fallback.
    skip_reason = None
    mode = 'port'
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown
    _run = acceptance.TestUxrAcceptance._run

    def test_rotation_draft_sheet_escape_and_keyboard(self):
        self.assertEqual(self._run('continuity'), [])


if __name__ == '__main__':
    unittest.main()
