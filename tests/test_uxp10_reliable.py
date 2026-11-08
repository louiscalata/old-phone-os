# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from opos.web import page

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'src/opos/web_static'
KIOSK = ROOT / 'packaging/pmos/opos/opos-kiosk'
NODE = shutil.which('node')
HARNESS = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const E={},observers=[];
const html=fs.readFileSync(0,'utf8');
function element(id='') {
  return {id,set textContent(v){this.text=v;observers.forEach(o=>{if(o.target===this)queueMicrotask(()=>o.callback())})},
    get textContent(){return this.text||''},hidden:true,inert:false,children:[],listeners:{},
    setAttribute(){},focus(){},toggleAttribute(n,on){if(n==='hidden')this.hidden=!!on},
    addEventListener(n,f){this.listeners[n]=f},
    removeEventListener(n,f){if(this.listeners[n]===f)delete this.listeners[n]},
    appendChild(e){this.children.push(e);if(e.id)E[e.id]=e},
    insertBefore(e){this.appendChild(e)}};
}
['phone','lock','unlock','lock-time','lock-date','lock-batt','lock-notice',
 'lock-ask','lock-hangup','sb-time','sb-net','sb-engine','sb-batt',
 'sheet','scrim','reload-shell','screen'].forEach(id=>E[id]=element(id));
let sheet=false,closed=0,reloads=0,result={},reject=false,status=200,badJSON=false,stale=false;const errors=[],shown=[];
const settle=async()=>{for(let i=0;i<6;i++)await new Promise(setImmediate)};
const boot=process.argv[2]==='boot';
const c={document:{getElementById:id=>E[id]||null,createElement:()=>element(),querySelector:()=>({content:'test-token'}),addEventListener(){},
   body:{toggleAttribute(n,on){if(n!=='data-stale')throw Error(n);stale=!!on}}},AbortSignal,showNotice:t=>shown.push(t),
 navigator:{onLine:true},location:{search:'?lock=0',reload(){reloads++}},setInterval(){},
 openSheet(){sheet=true},closeSheet(){sheet=false;closed++},
 MutationObserver:class{constructor(callback){this.callback=callback;observers.push(this)}
   observe(target){this.target=target}disconnect(){this.target=null}},
 async fetch(url){if(reject||(boot&&url==='/api/state'))throw Error('offline');
   return {ok:status<400,status,async json(){if(badJSON)throw Error('invalid JSON');return result}}},
 async render(s){if(s&&s.error)errors.push(s.error);else c.oposHooks.forEach(f=>f(s))}};
c.window=c;c.opos={open:c.openSheet,close:c.closeSheet};
c.document.getElementById('scrim').addEventListener('click',c.closeSheet);
vm.createContext(c);
vm.runInContext(html.match(/^async function api\(o\).*$/m)[0],c);
vm.runInContext(html.match(/^function healReload\(.*$/m)[0],c);
if(boot){
 vm.runInContext(html.match(/^function fetchState\(\).*$/m)[0],c);
 vm.runInContext(html.match(/^fetchState\(\)\.then\(.*$/m)[0],c);
}
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
function good(){c.oposHooks.forEach(f=>f({}));}
(async()=>{
 const out={};
 if(boot){await new Promise(setImmediate);console.log(JSON.stringify({hidden:E['reload-shell'].hidden,message:E.screen.textContent}));return;}
 good();out.initial=E['reload-shell'].hidden;
 result={error:'unavailable'};status=503;await c.api();out.one=E['reload-shell'].hidden;out.oneStale=stale;out.oneErrors=errors.length;
 reject=true;try{await c.api()}catch(e){}out.two=E['reload-shell'].hidden;out.twoStale=stale;out.twoErrors=errors.slice();
 E['reload-shell'].onclick();await settle();out.downReloads=reloads;out.downShown=shown.slice();
 good();out.good=E['reload-shell'].hidden;out.goodStale=stale;
 try{await c.api()}catch(e){}out.again=E['reload-shell'].hidden;
 good();result={};reject=false;status=200;await c.api();good();out.healthy=E['reload-shell'].hidden;
 E['reload-shell'].onclick();await settle();out.reloads=reloads;out.fetched=[reloads,shown.length];
 status=500;E['reload-shell'].onclick();await settle();out.notAPage=[reloads,shown.length];status=200;
 status=429;result={error:'too many requests'};await c.api();await c.api();out.limited=E['reload-shell'].hidden;
 status=400;result={error:'invalid input'};await c.api();await c.api();out.validation=E['reload-shell'].hidden;
 status=403;result={error:'forbidden'};await c.api();out.staleOne=E['reload-shell'].hidden;
 await c.api();out.staleTwo=E['reload-shell'].hidden;
 good();out.staleRecovered=E['reload-shell'].hidden;
 status=503;await c.api();status=429;await c.api();status=503;await c.api();out.interrupted=E['reload-shell'].hidden;
 good();status=200;badJSON=true;for(let i=0;i<2;i++){try{await c.api()}catch(e){}}out.nonJSON=E['reload-shell'].hidden;badJSON=false;
 good();
 c.oposPoll(0);out.pollOne=[E['reload-shell'].hidden,stale];c.oposPoll(0);out.pollTwo=[E['reload-shell'].hidden,stale,errors.length];c.oposPoll(0);out.pollThree=errors.length;c.oposPoll(1);out.pollGood=[E['reload-shell'].hidden,stale];
 out.errors=errors.slice();
 good();
 c.oposUI.lock();out.lock=[E.lock.hidden,E.phone.inert];
 c.openSheet();out.sheet=[sheet,E.lock.hidden,E.phone.inert];
 c.oposUI.lock();out.relock=[sheet,E.lock.hidden,E.phone.inert];
 c.opos.open();out.alias=[sheet,E.lock.hidden];
 E.scrim.listeners.click();out.scrim=[sheet];
 c.opos.open();c.opos.close();out.close=[sheet];
 c.oposUI.lock();c.oposUI.unlock();out.unlock=[E.lock.hidden,E.phone.inert];
 console.log(JSON.stringify(out));
})().catch(e=>{console.error(e);process.exitCode=1});
"""


class TestReliable(unittest.TestCase):
    def run_js(self, mode="normal"):
        if not NODE:
            self.skipTest('node not installed')
        result = subprocess.run(
            [NODE, '--unhandled-rejections=strict', '-e', HARNESS, str(STATIC / 'phone-a.js'), mode],
            input=page(), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_helper_newlines_are_real(self):
        out = page()
        line = next(line for line in out.splitlines() if "h.textContent='Helper" in line)
        self.assertIn(r"'Helper\nMode: '", line)
        self.assertNotIn(r'\\n', line)
        for field in ('Mode', 'Allowed', 'Paused', 'Hours left', 'Reserve', 'Source', 'Helper set to'):
            self.assertIn(r'\n' + field + ': ', line)
        self.assertRegex(out, r'#helper\{[^}]*white-space:pre-(?:line|wrap)')

    def test_reload_button_markup(self):
        out = page()
        button = re.search(r'<button\b[^>]*id="reload-shell"[^>]*>Reload</button>', out)
        self.assertIsNotNone(button)
        self.assertIn('type="button"', button[0])
        self.assertRegex(button[0], r'\bhidden\b')
        self.assertLess(out.index('id="sb"'), button.start())
        self.assertLess(button.end(), out.index('id="lock"'))
        self.assertRegex(out, r'#reload-shell\{[^}]*min-height:48px')
        # Its colours come from the Jaryo tokens (the shared surface rule in jaryo.css), not from a white-on-black pair.
        self.assertRegex(out, r'#reload-shell\b[^{}]*\{[^}]*background:var\(--jaryo-bg\)[^}]*color:var\(--jaryo-fg\)')
        self.assertNotRegex(out, r'#reload-shell\{[^}]*(?:background|color):#')

    def test_reload_shows_after_two_failures_and_hides_after_good(self):
        out = self.run_js()
        self.assertTrue(out['initial'])
        self.assertTrue(out['one'])
        self.assertFalse(out['two'])
        self.assertTrue(out['good'])
        self.assertTrue(out['again'])
        self.assertTrue(out['healthy'])
        # W1-02: the banner is drawn at the second miss (once), and the Reload button reloads only if the server answers
        self.assertFalse(out['oneStale'])
        self.assertTrue(out['twoStale'])
        self.assertEqual(out['twoErrors'], ['offline'])
        self.assertEqual(out['downReloads'], 0)
        self.assertEqual(out['downShown'], ['Still not reachable. Try again in a moment.'])
        self.assertFalse(out['goodStale'])
        self.assertEqual(out['reloads'], 1)
        self.assertEqual(out['fetched'], [1, 1])
        self.assertEqual(out['notAPage'], [1, 2], 'a server that answers with an error is not a page to reload into')
        self.assertTrue(out['limited'])
        self.assertTrue(out['validation'])
        self.assertTrue(out['staleOne'])
        self.assertFalse(out['staleTwo'])
        self.assertTrue(out['staleRecovered'])
        self.assertTrue(out['interrupted'])
        self.assertFalse(out['nonJSON'])
        # the page that polls reports through oposPoll: two misses show the banner, a third adds nothing, one good clears it
        self.assertEqual(out['pollOne'], [True, False])
        self.assertEqual(out['pollTwo'][:2], [False, True])
        self.assertEqual(out['pollThree'], out['pollTwo'][2], 'the banner is said once, not at every miss')
        self.assertEqual(out['pollGood'], [True, False])
        self.assertEqual(out['errors'], ['offline', 'offline', 'offline', 'offline'])

    def test_boot_fetch_rejection_shows_reload(self):
        out = self.run_js('boot')
        self.assertIn('Could not reach Old Phone OS Interface.', out['message'])
        self.assertFalse(out['hidden'])

    def test_one_overlay_at_a_time(self):
        out = self.run_js()
        self.assertEqual(out['lock'], [False, True])
        self.assertEqual(out['sheet'], [True, True, False])
        self.assertEqual(out['relock'], [False, False, True])
        self.assertEqual(out['alias'], [True, True])
        self.assertEqual(out['scrim'], [False])
        self.assertEqual(out['close'], [False])
        self.assertEqual(out['unlock'], [True, False])

    def test_kiosk_writes_boot_timing(self):
        text = KIOSK.read_text()
        self.assertIn('timing_file=${XDG_RUNTIME_DIR:-}/opos-boot-timing', text)
        self.assertIn('date +%s', text)
        self.assertIn('chmod a+r "$timing_file"', text)
        for name in ('kiosk-start', 'server-ready', 'chromium-launch'):
            self.assertIn('boot_timing ' + name, text)
        self.assertLess(text.index('boot_timing kiosk-start'), text.index('while '))
        self.assertIn('&& { boot_timing server-ready; break; }', text)
        self.assertIn('boot_timing chromium-launch\nexec ', text)
        result = subprocess.run(['sh', '-n', str(KIOSK)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Exercise append, permissions, and ignored write errors without writing /run.
        fn = text[text.index('timing_file='):text.index('boot_timing kiosk-start')]
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'opos-boot-timing'
            script = fn
            result = subprocess.run(['sh', '-c', script + '\nboot_timing kiosk-start\nboot_timing chromium-launch'],
                                    env={'PATH': '/usr/bin:/bin', 'XDG_RUNTIME_DIR': d},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(log.read_text(), r'^kiosk-start \d+\nchromium-launch \d+\n$')
            self.assertEqual(log.stat().st_mode & 0o444, 0o444)
            result = subprocess.run(['sh', '-c', script + '\nboot_timing kiosk-start'],
                                    env={'PATH': '/usr/bin:/bin', 'XDG_RUNTIME_DIR': str(log)},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, '')
            # An unset or empty runtime directory must not use the temp directory.
            log.unlink()
            target = Path(d) / 'private-file'
            target.write_text('unchanged')
            target.chmod(0o600)
            log.symlink_to(target)
            for runtime in ({}, {'XDG_RUNTIME_DIR': ''}):
                result = subprocess.run(['sh', '-c', script + '\nboot_timing kiosk-start'],
                                        env={'PATH': '/usr/bin:/bin', 'TMPDIR': d, **runtime},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertEqual(target.read_text(), 'unchanged')
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class TestStaleBannerCss(unittest.TestCase):
    """W1-02: what a stale page looks like. Tokens only, fixed (nothing moves), one pane wide when the window spans two."""
    BANNER = ('[data-stale] #notice:not([hidden]){position:fixed;top:28px;left:0;right:0;z-index:120;background:var(--jaryo-bg);'
              'border:3px double var(--jaryo-fg);padding:8px 16px;font-size:1rem;text-align:left}')

    def test_the_banner_is_a_fixed_boxed_notice_in_the_jaryo_tokens(self):
        out = page()
        self.assertIn(self.BANNER, out)
        self.assertIn('[data-stale] #sb-batt::before{content:"? "}', out)
        self.assertNotRegex(self.BANNER, r'#[0-9a-fA-F]{3,8}\b|rgb|hsl')
        self.assertLess(out.index('#notice{font-size'), out.index(self.BANNER), 'it wins by source order and by specificity')

    def test_reload_sits_at_the_bottom_right_and_nothing_is_pushed_for_it(self):
        out = page()
        self.assertIn('#reload-shell{position:fixed;bottom:16px;right:16px;z-index:110;', out)
        self.assertNotIn('#reload-shell{position:fixed;top:28px', out)
        self.assertNotIn('+#reload-shell', out, 'the old margin that pushed the status row under a top-left button')

    def test_the_banner_does_not_cross_the_hinge(self):
        from opos.span import parse
        out = page(span=parse('675,42'))
        tail = out[out.rindex('@media (min-width:1055px){'):]
        self.assertIn('[data-stale] #notice:not([hidden]){box-sizing:border-box;max-width:var(--pane)}', tail)
        self.assertNotIn('--pane', page(), 'one pane: no new rules, no span variables (tests/test_span.py)')


if __name__ == '__main__':
    unittest.main()
