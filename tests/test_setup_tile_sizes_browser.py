# SPDX-License-Identifier: GPL-3.0-or-later
"""Personal setup size controls through real HTTP/prefs and installed Chromium.

Only the installation backend is fake: it marks its steps skipped without
running service commands. Preference endpoints, durable storage, the served
wizard and the kiosk/state/rendering are real, under an isolated temporary
configuration. No model request, screenshot or physical device is involved.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opos import web
from opos.setup import prefs, server as setup_server
from tests import test_uxr_acceptance as browser


DRIVER = r"""
'use strict';
const [port,url,mode]=process.argv.slice(2),pending=new Map();
let seq=0,session,ws;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function sendTo(s,method,params={}){return new Promise((resolve,reject)=>{
 const id=++seq;pending.set(id,{resolve,reject,method});
 ws.send(JSON.stringify(s?{id,sessionId:s,method,params}:{id,method,params}));
})}
const send=(m,p)=>sendTo(session,m,p);
async function ev(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
 if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);
 return r.result.value;}
async function wait(expression,ms=5000){const end=Date.now()+ms;
 while(Date.now()<end){if(await ev('!!('+expression+')'))return;await sleep(25)}
 throw Error('Timed out: '+expression);}
function ensure(value,message){if(!value)throw Error(message)}
async function visibleStep(name){await wait(`!document.querySelector('[data-jstep="${name}"]').hidden`)}
async function next(name,skip=false){await ev(`document.querySelector('[data-jstep="${name}"] [data-j${skip?'skip':'next'}]').click()`)}
const expected={phone:'wide',messages:'small',helper:'medium',settings:'wide'};
const defaults={phone:'medium',messages:'medium',helper:'small',settings:'small'};
async function selected(){return ev(`Object.fromEntries(Array.from(document.querySelectorAll('[data-jtile-size]'),e=>[e.dataset.jtileSize,e.value]))`)}
async function verifySelected(want){const got=await selected();ensure(JSON.stringify(got)===JSON.stringify(want),'Wrong restored sizes: '+JSON.stringify(got));return got}
async function startPersonal(apply){
 await wait(`window.jaryoSetup&&typeof show==='function'&&S&&document.getElementById('profile').options.length`);
 await ev(`show(6)`);
 if(apply)await ev(`document.getElementById('apply').click()`);else await ev(`window.jaryoSetup.start()`);
 await wait(`!document.getElementById('jaryo-setup').hidden`);
}
async function finishRemaining(skip){
 for(const [name,value] of [['text_size','normal'],['device_type','slab'],['language','en'],['wifi',null],['pin',null],['helper','off']]){
  await visibleStep(name);
  if(value&&!skip)await ev(`document.querySelector('[data-jstep="${name}"] input[value="${value}"]').click()`);
  await next(name,skip);
 }
 await wait(`!document.getElementById('jaryo-summary').hidden&&document.getElementById('jaryo-summary').textContent.includes('saved and applied')`);
 return ev(`document.getElementById('jaryo-summary').textContent`);
}
async function kiosk(want,override){
 await wait(`window.oposUI&&document.getElementById('qs')`);
 if(override){
  await ev(`localStorage.setItem('jaryo_tiles',JSON.stringify(${JSON.stringify(want)}))`);
  await send('Page.reload',{ignoreCache:true});await sleep(150);
  await wait(`window.oposUI&&document.getElementById('qs')`);
 }
 await ev(`oposUI.unlock();oposUI.openApp('home')`);
 await wait(`document.getElementById('phone').dataset.app==='home'`);
 await wait(`document.querySelector('#home button[data-open="phone"]').dataset.size===${JSON.stringify(want.phone)}`);
 const result=await ev(`(function(){const out={tiles:{},order:[]};
 for(const e of document.querySelectorAll('#home button[data-open]')){
  const r=e.getBoundingClientRect(),c=getComputedStyle(e);out.order.push(e.dataset.open);
  out.tiles[e.dataset.open]={size:e.dataset.size,width:r.width,height:r.height,column:c.gridColumn,row:c.gridRow,visible:!e.closest('[hidden]')&&r.width>0&&r.height>0};
 }return out})()`);
 ensure(JSON.stringify(result.order)===JSON.stringify(['phone','messages','helper','settings']),'Home DOM order changed');
 for(const [app,size]of Object.entries(want))ensure(result.tiles[app]?.size===size&&result.tiles[app].visible,'Missing/unapplied Home tile '+app);
 if(!override&&want.phone==='wide'){
  ensure(result.tiles.phone.column.includes('-1')&&result.tiles.phone.width>result.tiles.messages.width*1.5,'Wide tile has no full-width geometry');
  ensure(result.tiles.helper.row.includes('span 2')&&result.tiles.helper.height>=144,'Medium tile has no two-row geometry');
  ensure(result.tiles.messages.height>=96&&result.tiles.messages.row.includes('span 1'),'Small tile has wrong geometry');
 }
 await ev(`document.getElementById('allapps').click()`);await wait(`document.getElementById('phone').dataset.allapps==='1'`);
 const apps=await ev(`Array.from(document.querySelectorAll('#apps-list button[data-open]'),e=>e.dataset.open)`);
 for(const app of ['phone','messages','helper','settings'])ensure(apps.includes(app),'All apps lost '+app);
 await ev(`document.getElementById('apps-list').querySelector('button[data-open="settings"]').click()`);
 await wait(`document.getElementById('phone').dataset.app==='settings'`);
 result.allApps=apps;result.settingsReachable=true;return result;
}
(async()=>{
 const info=await(await fetch('http://127.0.0.1:'+port+'/json/version')).json();ws=new WebSocket(info.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
 ws.onmessage=e=>{const d=JSON.parse(e.data),p=pending.get(d.id);if(!p)return;pending.delete(d.id);d.error?p.reject(Error(p.method+':'+d.error.message)):p.resolve(d.result)};
 let targetId,result;const timeout=setTimeout(()=>process.exit(3),25000);
 try{
  ({targetId}=await sendTo(null,'Target.createTarget',{url:'about:blank'}));
  ({sessionId:session}=await sendTo(null,'Target.attachToTarget',{targetId,flatten:true}));
  await send('Page.enable');await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:360,height:800,deviceScaleFactor:1,mobile:true});
  await send('Page.navigate',{url});await wait(`document.readyState==='complete'`);
  if(mode==='edit'){
   await startPersonal(true);await wait(`!document.getElementById('jaryo-edition').hidden`);
   await ev(`document.getElementById('jaryo-customise').click()`);await visibleStep('theme');
   await ev(`document.querySelector('[data-jstep="theme"] input[value="dark"]').click()`);await next('theme');await visibleStep('layout');
   result=await ev(`(function(){const out=[];for(const e of document.querySelectorAll('[data-jtile-size]')){
    const r=e.getBoundingClientRect(),label=Array.from(e.labels||[],l=>l.textContent.trim()).join(' ');
    out.push({app:e.dataset.jtileSize,tag:e.tagName,label,width:r.width,height:r.height,options:Array.from(e.options,o=>o.value)});
   }return out})()`);
   ensure(result.length===4,'Expected exactly four native size controls');
   for(const e of result){ensure(e.tag==='SELECT'&&e.label.toLowerCase().includes(e.app==='messages'?'messages':e.app),'Missing native labelled select '+e.app);
    ensure(e.width>=48&&e.height>=48,'Small target '+e.app);ensure(JSON.stringify(e.options)===JSON.stringify(['small','medium','wide']),'Wrong options '+e.app)}
   ensure(new Set(result.map(e=>e.label)).size===4,'Size controls do not have distinct labels');
   const zoom=await ev(`(function(){document.documentElement.style.fontSize='200%';const out=[];
    if(innerWidth!==360||document.documentElement.scrollWidth>360)out.push('Actual viewport exceeds360px at200percent');
    for(const e of document.querySelectorAll('[data-jtile-size]')){const r=e.getBoundingClientRect();if(r.x<0||r.right>361||r.width<48||r.height<48)out.push(e.id+' outside360px/48px at200percent');if(parseFloat(getComputedStyle(e).fontSize)<32)out.push(e.id+' text did not reach32px')}
    const step=document.querySelector('[data-jstep="layout"]');if(step.scrollWidth>step.clientWidth+1)out.push('Layout step overflows at200percent');
    document.documentElement.style.fontSize='';return out})()`);ensure(zoom.length===0,zoom.join('; '));
   ensure(await ev(`/recorded only/i.test(document.querySelector('[data-jstep="layout"]').innerText)`),'Missing recorded-only favourite disclosure');
   await ev(`(function(){for(const [app,value]of Object.entries(${JSON.stringify(expected)})){const e=document.getElementById('jaryo-size-'+app);e.value=value;e.dispatchEvent(new Event('change',{bubbles:true}))}
    document.querySelector('[data-jstep="layout"] input[value="helper"]').checked=false;document.querySelector('[data-jstep="layout"] input[value="settings"]').checked=false;})()`);
   const focus=await ev(`(function(){const e=document.getElementById('jaryo-size-phone');e.focus();return document.activeElement===e})()`);ensure(focus,'Native select cannot receive focus');
   await next('layout');await visibleStep('text_size');await ev(`document.querySelector('[data-jstep="text_size"] [data-jback]').click()`);await visibleStep('layout');await verifySelected(expected);
   await next('layout');await visibleStep('text_size');result={controls:result,sizes:expected,savedBeforeRestart:true};
  }else if(mode==='resume-finish'){
   await startPersonal(false);await visibleStep('text_size');await ev(`document.querySelector('[data-jstep="text_size"] [data-jback]').click()`);await visibleStep('layout');await verifySelected(expected);
   await next('layout');const summary=await finishRemaining(false);
   for(const word of ['Phone: Wide','Messages: Small','Helper: Medium','Settings: Wide'])ensure(summary.includes(word),'Summary missing '+word);
   ensure(/recorded only/i.test(summary),'Summary implies favourite application');result={summary,restoredAfterRestart:true};
  }else if(mode==='old-skip'){
   await startPersonal(true);await visibleStep('text_size');await ev(`document.querySelector('[data-jstep="text_size"] [data-jback]').click()`);await visibleStep('layout');await verifySelected(defaults);
   await ev(`for(const e of document.querySelectorAll('[data-jtile-size]')){e.value='wide';e.dispatchEvent(new Event('change',{bubbles:true}))}`);
   await next('layout',true);const summary=await finishRemaining(true);result={summary,legacyDefaults:defaults,unsavedSizesSkipped:true};
  }else if(mode==='legacy-save'||mode==='legacy-resume'){
   await startPersonal(mode==='legacy-save');await visibleStep('text_size');
   await ev(`document.querySelector('[data-jstep="text_size"] [data-jback]').click()`);await visibleStep('layout');
   await verifySelected(mode==='legacy-save'?defaults:{...defaults,phone:'wide'});
   await ev(`const e=document.getElementById('jaryo-size-phone');e.value='wide';e.dispatchEvent(new Event('change',{bubbles:true}))`);
   await next('layout');await visibleStep('text_size');
   const sizes=await ev(`fetch('/setup/api/prefs',{headers:{'X-OPOS-Token':document.querySelector('meta[name="opos-token"]').content}}).then(r=>r.json()).then(j=>j.state.answers.layout.sizes)`);
   ensure(sizes.allapps==='wide','Saving visible sizes lost the accepted legacy All apps size');
   ensure(Object.keys(sizes).length===5&&Object.entries({allapps:'wide',phone:'wide',messages:'medium',helper:'small',settings:'small'}).every(([key,value])=>sizes[key]===value),
          'Unexpected saved legacy size map: '+JSON.stringify(sizes));
   result={sizesAfterSave:sizes,resumedAfterRestart:mode==='legacy-resume'};
  }else if(mode==='kiosk'||mode==='kiosk-restarted'){result=await kiosk(expected,false)}
  else if(mode==='kiosk-defaults'){result=await kiosk(defaults,false)}
  else if(mode==='kiosk-override'){result=await kiosk({phone:'small',messages:'wide',helper:'small',settings:'medium'},true)}
  else throw Error('Unknown mode');
  console.log(JSON.stringify({mode,result}));
 }finally{
  clearTimeout(timeout);if(targetId)await Promise.race([sendTo(null,'Target.closeTarget',{targetId}).catch(()=>{}),sleep(500)]);ws.close();
 }
})().catch(e=>{console.error(e.stack||String(e));process.exitCode=2});
"""


class SetupTileSizesBrowser(unittest.TestCase):
    """Actual personal-wizard browser/prefs flow; installer backend is a fake."""

    def setUp(self):
        self.chrome = browser.find_chrome()
        self.node = shutil.which('node')
        if not self.chrome or not self.node:
            self.skipTest('installed Chromium and Node required')
        self.tmp = tempfile.TemporaryDirectory(prefix='opos-tile-browser-')
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = self.root / 'cfg'
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env = dict(HOME=str(self.home), XDG_CONFIG_HOME=str(self.root / 'xdg'),
                        OPOS_CONFIG_DIR=str(self.cfg), OPOS_GLIMMER_ROUTING='0',
                        PATH='/opt/homebrew/bin:/Library/Developer/CommandLineTools/usr/bin:/usr/bin:/bin',
                        TMPDIR=str(self.root), LANG='en_US.UTF-8')
        self.environment = mock.patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.apply_calls = 0

        def fake_apply(cfg, **kw):
            self.apply_calls += 1
            for step in kw['progress'].snapshot():
                kw['progress'].skip(step['id'], 'Test-only backend; no services installed')

        self.apply_patch = mock.patch.object(setup_server._apply, 'apply', side_effect=fake_apply)
        self.apply_patch.start()
        self.addCleanup(self.apply_patch.stop)
        self.ctx = setup_server.SetupContext(repo_root=Path(__file__).resolve().parents[1],
            python=sys.executable, home=self.home, platform=sys.platform, env=self.env,
            dry_run=True, root=str(self.root))
        self.servers = []
        self.addCleanup(self.close_servers)
        self.setup = self.start_server(setup_server.make_server(0, self.ctx))
        self.driver = self.root / 'tile-browser.js'
        self.driver.write_text(DRIVER)
        self.proc, self.cdp_port = browser._launch(self.chrome, str(self.root))
        self.addCleanup(self.close_browser)
        self.evidence = []

    def start_server(self, srv):
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.servers.append((srv, thread))
        return srv

    def stop_server(self, srv):
        pair = next(pair for pair in self.servers if pair[0] is srv)
        srv.shutdown()
        srv.server_close()
        pair[1].join(timeout=2)
        self.assertFalse(pair[1].is_alive(), 'Owned fixture server did not exit')
        self.servers.remove(pair)

    def close_servers(self):
        for srv, thread in self.servers[:]:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), 'Owned fixture server did not exit')
            with socket.socket() as sock:
                sock.settimeout(0.2)
                self.assertNotEqual(sock.connect_ex(srv.server_address), 0,
                                    'Owned fixture port remained open')

    def close_browser(self):
        browser._kill(self.proc)
        self.assertIsNotNone(self.proc.poll(), 'Owned Chromium process was not reaped')

    def run_browser(self, srv, mode):
        url = f'http://127.0.0.1:{srv.server_address[1]}/'
        result = subprocess.run([self.node, str(self.driver), str(self.cdp_port), url, mode],
                                text=True, capture_output=True, timeout=32,
                                env=dict(os.environ, TMPDIR=str(self.root)))
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        value = json.loads(result.stdout.strip().splitlines()[-1])
        self.evidence.append(value)
        return value

    def test_sizes_save_restart_finish_and_render_without_hiding_apps(self):
        expected = dict(phone='wide', messages='small', helper='medium', settings='wide')
        self.run_browser(self.setup, 'edit')
        state = prefs.load()
        self.assertEqual(state['answers']['layout']['sizes'], expected)
        self.assertNotIn('settings', state['answers']['layout']['favourites'])
        self.assertFalse(state['applied'])
        self.assertEqual(prefs.prefs_path().stat().st_mode & 0o777, 0o600)
        port = self.setup.server_address[1]
        self.stop_server(self.setup)
        self.setup = self.start_server(setup_server.make_server(port, self.ctx))
        self.run_browser(self.setup, 'resume-finish')
        self.assertTrue(prefs.load()['applied'])
        self.assertEqual(prefs.effective()['layout']['sizes'], expected)
        self.assertEqual(self.apply_calls, 1, 'Resume unexpectedly reran installation')
        kiosk = self.start_server(web.make_server(0, web.Phone(visible=False)))
        self.run_browser(kiosk, 'kiosk')
        port = kiosk.server_address[1]
        self.stop_server(kiosk)
        kiosk = self.start_server(web.make_server(port, web.Phone(visible=False)))
        self.run_browser(kiosk, 'kiosk-restarted')
        self.run_browser(kiosk, 'kiosk-override')
        self.assertEqual(prefs.effective()['layout']['sizes'], expected,
                         'Client preference must not rewrite installer choices')
        print('TILE_BROWSER_EVIDENCE ' + json.dumps(self.evidence, sort_keys=True))

    def test_older_layout_and_skip_keep_default_sizes(self):
        prefs.answer('theme', 'light')
        prefs.answer('layout', {'favourites': ['phone']})
        self.run_browser(self.setup, 'old-skip')
        state = prefs.load()
        self.assertTrue(state['applied'])
        self.assertNotIn('layout', state['answers'])
        self.assertIn('layout', state['skipped'])
        self.assertEqual(prefs.effective()['layout']['sizes'], {})
        self.assertEqual(self.apply_calls, 1)
        kiosk = self.start_server(web.make_server(0, web.Phone(visible=False)))
        self.run_browser(kiosk, 'kiosk-defaults')
        print('TILE_BROWSER_EVIDENCE ' + json.dumps(self.evidence, sort_keys=True))

    def test_legacy_allapps_size_survives_visible_control_save_and_restart(self):
        prefs.answer('theme', 'light')
        prefs.answer('layout', {'favourites': ['phone'], 'sizes': {'allapps': 'wide'}})
        expected = dict(allapps='wide', phone='wide', messages='medium', helper='small', settings='small')
        self.run_browser(self.setup, 'legacy-save')
        self.assertEqual(prefs.load()['answers']['layout']['sizes'], expected)
        port = self.setup.server_address[1]
        self.stop_server(self.setup)
        self.setup = self.start_server(setup_server.make_server(port, self.ctx))
        self.run_browser(self.setup, 'legacy-resume')
        self.assertEqual(prefs.load()['answers']['layout']['sizes'], expected)
        self.assertEqual(self.apply_calls, 1, 'Resume unexpectedly reran installation')
        self.assertFalse(prefs.load()['applied'])
        print('TILE_BROWSER_EVIDENCE ' + json.dumps(self.evidence, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
