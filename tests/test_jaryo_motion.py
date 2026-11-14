# SPDX-License-Identifier: GPL-3.0-or-later;
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

from tests import portable

SRC = 'src/opos/web_static/jaryo.js'

# Node vm harness: queued rAF, shell-dependent rectangles and recorded keyframes.
# Reading offsetHeight throws: motion must not force synchronous layout.
HARNESS = r"""
const fs=require('fs'),vm=require('vm');const src=fs.readFileSync(%(src)s,'utf8');
const cfg=JSON.parse(process.argv[1]);const log=[],queue=[];let throwOn=-1,n=0;
function style(){const values={},priorities={};return new Proxy({
 getPropertyValue(k){return values[k]||''},getPropertyPriority(k){return priorities[k]||''},
 setProperty(k,v,p){values[k]=String(v);priorities[k]=p||''},removeProperty(k){delete values[k];delete priorities[k]}
},{get(t,k){return k in t?t[k]:values[k]||''},set(t,k,v){values[k]=v;return true}})}
function el(id){const e={id,style:style(),dataset:{},anims:[],inert:false,
 getBoundingClientRect(){
  if(e.style.position==='fixed')return {left:parseFloat(e.style.left),top:parseFloat(e.style.top),width:parseFloat(e.style.width),height:parseFloat(e.style.height)};
  if(id==='home-messages')return phone.dataset.app==='home'?{left:10,top:20,width:100,height:50}:{left:0,top:0,width:0,height:0};
  if(id==='threads'&&phone.dataset.app==='home'&&!e.style.display)return {left:0,top:0,width:0,height:0};
  return {left:0,top:0,width:400,height:600}},
 animate(k,o){if(n++===throwOn)throw new Error('boom');const a={k,o,playState:'running',cancel(){this.playState='idle';this.oncancel&&this.oncancel()}};
  const r=e.getBoundingClientRect();log.push({el:id,app:phone.dataset.app,o,k,visible:r.width>0&&r.height>0,inert:e.inert});e.anims.push(a);return a},
 focus(){doc.activeElement=e}};
 Object.defineProperty(e,'offsetHeight',{get(){throw new Error('offsetHeight read')}});return e}
const phone=el('phone'),tile=el('home-messages'),threads=el('threads'),screen=el('screen');phone.dataset.app='home';
const byId={phone,threads,screen,home:{querySelector(s){return s.indexOf('messages')>=0?tile:null}}};
const doc={activeElement:null,getElementById:i=>byId[i]||null,querySelector:()=>null,querySelectorAll:()=>[]};
const store={jaryo_motion:cfg.off?'off':null};
const win={matchMedia:()=>({matches:!!cfg.reduce}),requestAnimationFrame:f=>queue.push(f),
 getComputedStyle:e=>({transform:'none',display:e===threads&&phone.dataset.app==='home'?'none':'block'}),
 localStorage:{getItem:k=>store[k]===undefined?null:store[k]},oposUI:{openApp(name){phone.dataset.app=name}}};
function flush(){while(queue.length)queue.shift()()}
function finish(){[tile,threads].forEach(e=>e.anims.forEach(a=>{if(a.playState==='running'){a.playState='finished';a.onfinish&&a.onfinish()}}))}
vm.runInNewContext(src,{window:win,document:doc,console});const J=win.jaryo,out={};
out.motion=J.motion();
if(cfg.fail)throwOn=1;
if(cfg.restore){threads.style.setProperty('transform-origin','25%% 75%%','important');threads.style.setProperty('margin','7px','important')}
win.oposUI.openApp('messages');out.synchronousOpen=phone.dataset.app;
if(!cfg.rapid)flush();
out.appAtFirstAnimate=log.length?log[0].app:null;out.lastTile=J._lastTile;out.openDur=log.map(a=>a.o.duration);
const first=[tile,threads].map(e=>e.anims[0]).filter(Boolean);
if(cfg.failBack)throwOn=n+1;
if(!cfg.fail){win.oposUI.openApp('home');}out.synchronousBack=phone.dataset.app;
flush();out.reverse=log.slice(cfg.rapid?0:out.openDur.length);
if(cfg.interrupt){win.oposUI.openApp('messages');flush()}
out.app=phone.dataset.app;out.focused=doc.activeElement&&doc.activeElement.id;
out.firstStates=first.map(a=>a.playState);
out.running=[tile,threads].map(e=>[e.style.opacity||'',e.style.transform||'']);
finish();
out.styles=[tile,threads].map(e=>[e.style.opacity||'',e.style.transform||'',e.style.getPropertyValue('will-change')]);
out.restored={origin:threads.style.getPropertyValue('transform-origin'),originPriority:threads.style.getPropertyPriority('transform-origin'),margin:threads.style.getPropertyValue('margin'),marginPriority:threads.style.getPropertyPriority('margin'),position:threads.style.position,display:threads.style.display,inert:threads.inert};
out.frames=log.map(a=>a.k);out.fills=log.map(a=>a.o.fill);out.count=log.length;
console.log(JSON.stringify(out));
""" % {'src': json.dumps(SRC)}


def run(**cfg):
    out = subprocess.check_output(['node', '-e', HARNESS, json.dumps(cfg)], text=True)
    return json.loads(out.strip().splitlines()[-1])


class TestJaryoMotion(unittest.TestCase):
    def test_static(self):
        txt = Path(SRC).read_text()
        for s in ('cubic-bezier(.2,.8,.2,1)', 'cubic-bezier(.4,0,1,1)', '240', '180', '120',
                  'prefers-reduced-motion', "'jaryo_motion'", 'cancel()', 'requestAnimationFrame'):
            self.assertIn(s, txt)
        self.assertNotIn('150', txt, 'reduced motion is 120 ms, not 150')
        self.assertNotIn('offsetHeight', txt, 'no forced synchronous layout')
        self.assertNotIn("fill:'forwards'", txt, 'finished/cancelled motion must not pin styles')
        self.assertNotIn('No automatic openApp hook', txt)
        forb = ['width', 'height', 'top', 'left', 'margin']
        for line in txt.splitlines():
            for stmt in line.split(';'):
                s = stmt.lower()
                if 'animate' in s or 'keyframes' in s:
                    for w in forb:
                        self.assertNotIn(w, s, f'Forbidden {w} in animate line')

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_open_back_focus_and_cancel(self):
        r = run()
        self.assertEqual(r['motion'], 'full')
        # (a) state changes before motion; the return tile is remembered
        self.assertEqual(r['appAtFirstAnimate'], 'messages')
        self.assertEqual(r['lastTile'], 'messages')
        self.assertEqual(r['openDur'], [240, 240])
        # (b) back lands focus on the Messages tile
        self.assertEqual(r['app'], 'home')
        self.assertEqual(r['focused'], 'home-messages')
        # (c) the in-flight open is cancelled and leaves no inline start state
        self.assertEqual(r['firstStates'], ['idle', 'idle'])
        self.assertEqual(r['running'], [['', ''], ['', '']])
        self.assertEqual(r['styles'], [['', '', ''], ['', '', '']])
        self.assertTrue(all(f == 'none' for f in r['fills']))

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_partial_failure_fails_open(self):
        # (d) the second animate() throws: the first is cancelled, nothing stays hidden
        r = run(fail=True)
        self.assertEqual(r['app'], 'messages')
        self.assertEqual(r['count'], 1)
        self.assertEqual(r['firstStates'], ['idle'])
        self.assertEqual([s[0] for s in r['styles']], ['', ''])
        self.assertEqual([s[2] for s in r['styles']], ['', ''])

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_queued_open_back_invalidates_stale_batch(self):
        r = run(rapid=True)
        self.assertEqual(r['synchronousOpen'], 'messages')
        self.assertEqual(r['synchronousBack'], 'home')
        self.assertEqual(r['count'], 2, 'only the latest navigation may animate')
        self.assertEqual(r['app'], 'home')

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_back_moves_visible_outgoing_article_to_tile(self):
        r = run()
        outgoing, incoming = r['reverse']
        self.assertEqual(outgoing['el'], 'threads')
        self.assertTrue(outgoing['visible'], 'shell-hidden article must remain visible during reverse FLIP')
        self.assertTrue(outgoing['inert'], 'outgoing article must not intercept home interaction')
        self.assertEqual(outgoing['k'][0]['transform'], 'none')
        self.assertEqual(outgoing['k'][1]['transform'], 'translate(10px,20px) scale(0.25,0.08333333333333333)')
        self.assertEqual(incoming['el'], 'home-messages')
        self.assertEqual(incoming['k'], [{'opacity': 0}, {'opacity': 1}])

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_keyframes_only_animate_transform_and_opacity(self):
        for cfg in ({}, {'rapid': True}, {'reduce': True}, {'off': True}):
            r = run(**cfg)
            allowed = {'opacity'} if cfg.get('reduce') or cfg.get('off') else {'transform', 'opacity'}
            for frames in r['frames']:
                for frame in frames:
                    self.assertLessEqual(set(frame), allowed)

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_finish_and_interrupt_restore_prior_styles(self):
        for interrupt in (False, True):
            r = run(restore=True, interrupt=interrupt)
            self.assertEqual(r['restored'], {'origin': '25% 75%', 'originPriority': 'important',
                'margin': '7px', 'marginPriority': 'important', 'position': '', 'display': '', 'inert': False})

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_reverse_animation_failure_restores_visible_state(self):
        r = run(failBack=True)
        self.assertEqual(r['app'], 'home')
        self.assertEqual(r['focused'], 'home-messages')
        self.assertEqual(r['count'], 3, 'one reverse animation starts before the second throws')
        self.assertEqual(r['restored']['position'], '')
        self.assertEqual(r['restored']['display'], '')
        self.assertFalse(r['restored']['inert'])
        self.assertEqual(r['styles'], [['', '', ''], ['', '', '']])

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_real_shell_reverse_geometry_and_rapid_navigation(self):
        from opos.web import Phone, page
        from tests.test_uxr_acceptance import find_chrome

        chrome, node = find_chrome(), shutil.which('node')
        # Linux (CI) must have a browser: there a missing one fails. A developer machine without one (or one that opts
        # out with OPOS_TEST_CHROME pointing at a missing path) skips instead.
        if chrome is None and not portable.LINUX:
            self.skipTest('no headless Chromium on this %s host (required on Linux)' % sys.platform)
        self.assertIsNotNone(chrome, 'installed Chromium required for motion acceptance')
        self.assertIsNotNone(node, 'installed Node required for motion acceptance')
        with tempfile.TemporaryDirectory(prefix='jaryo-motion-page-') as folder:
            html, state = Path(folder) / 'page.html', Path(folder) / 'state.json'
            html.write_text(page(demo=True), encoding='utf-8')
            state.write_text(json.dumps(Phone().state(), default=str), encoding='utf-8')
            result = subprocess.run([node, 'tests/jaryo_motion_cdp.js', chrome,
                                     html.as_uri(), str(state)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])['violations'], [])

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_reduced_motion(self):
        # (e) media query → 120 ms crossfade, opacity only
        r = run(reduce=True)
        self.assertEqual(r['motion'], 'reduced')
        self.assertEqual(r['openDur'], [120, 120])
        # (f) stored preference alone also reduces
        r = run(off=True)
        self.assertEqual(r['motion'], 'reduced')
        self.assertEqual(r['openDur'], [120, 120])
        self.assertEqual(r['focused'], 'home-messages')


if __name__ == '__main__':
    unittest.main()
