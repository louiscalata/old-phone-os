# SPDX-License-Identifier: GPL-3.0-or-later
import json
import unittest
import pathlib
import shutil
import subprocess
import sys

class TestRenderErrorNotice(unittest.TestCase):
    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_f2536_error_notice_contract(self):
        js_path = pathlib.Path('src/opos/web_static/js-4.js')
        src = js_path.read_text()
        self.assertIn('async function render(s){', src)
        self.assertIn('running=!!s.running', src)

        idx = src.find('async function render(s){')
        self.assertNotEqual(idx, -1)
        running_idx = src.find('running=!!s.running', idx)
        self.assertNotEqual(running_idx, -1)
        prefix = src[idx:running_idx]

        sentinel = 'throw new Error("normal_render_reached")'
        harness = prefix + '\n' + sentinel + '\n}\n'

        def build_secret():
            import random, string
            chars = string.ascii_letters + string.digits
            return ''.join(random.choice(chars) for _ in range(32))

        secret = build_secret()
        harness_js = '''
const document = {
  _notice: null,
  getElementById(id){ 
    if(id !== 'notice') throw new Error('unexpected id '+id);
    return this._notice;
  }
};
''' + harness + '''
async function runTests(){
  const cases = [
    ['forbidden','Restarting the screen. One moment.'],
    ['offline','Not updating. Values shown may be old. Tap Reload.'],
    ['too many requests','Too many taps. Wait a few seconds, then try again.'],
    ['unknown','That did not work. Try again.']
  ];
  for(const [err,msg] of cases){
    document._notice = { textContent:'old', hidden:true, dataset:{tone:'call'}, set innerHTML(v){ throw new Error('innerHTML used'); } };
    await render({error:err});
    if(document._notice.textContent !== msg) throw new Error('msg mismatch '+err);
    if(document._notice.hidden !== false) throw new Error('hidden not false '+err);
    if(document._notice.dataset.tone !== '') throw new Error('tone not empty '+err);
  }
  for(const [err,msg] of cases){
    document._notice = null;
    await render({error:err});
  }
  try{ document.getElementById('other'); }catch(e){}
  await render(null);
}
runTests().then(()=>{ process.exit(0); }).catch(e=>{ console.error(e); process.exit(1); });
'''
        proc = subprocess.run([sys.executable, '-c', 'import sys; open("/tmp/harness.js","w").write(sys.stdin.read())'], input=harness_js.encode(), capture_output=True, timeout=5)
        # Actually write file directly
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix='.js')
        try:
            os.write(fd, harness_js.encode())
            os.close(fd)
            result = subprocess.run(['node', path], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, msg='stderr: '+result.stderr)
        finally:
            try: os.unlink(path)
            except: pass


HEAL = r"""
const sleep=()=>new Promise(r=>setImmediate(r));
let now=1700000000000,timers=[],reloads=0,fetches=[],shown=[],up=true,ok=true,storeBroken=false;
const store={};
Object.defineProperty(globalThis,'sessionStorage',{get(){if(storeBroken)throw new Error('blocked');return store},configurable:true});
globalThis.Date={now:()=>now};
globalThis.setTimeout=(f,ms)=>{timers.push({f,ms})};
globalThis.location={reload(){reloads++}};
globalThis.fetch=async(url,o)=>{fetches.push([url,o&&o.cache]);if(!up)throw new Error('down');return{ok}};
function showNotice(t){shown.push(t)}
let notice;
const document={getElementById(id){if(id!=='notice')throw new Error('unexpected id '+id);return notice}};
"""

HEAL_DRIVER = r"""
async function fire(){const t=timers.splice(0);for(const x of t)x.f();for(let i=0;i<5;i++)await sleep()}
(async()=>{
 const out={};
 const fresh=()=>{notice={textContent:'',hidden:true,dataset:{tone:'call'}}};
 fresh();
 await render({error:'forbidden'});
 out.first={text:notice.textContent,timers:timers.map(t=>t.ms),stored:store.opos_h===String(now)||store.opos_h===now};
 await fire();
 out.reloadedAfterOk={reloads,fetches:fetches.slice()};
 // a second 403 inside ten seconds schedules nothing: no reload loop
 now+=5000;await render({error:'forbidden'});out.inside={timers:timers.length};
 now+=5001;await render({error:'forbidden'});out.after={timers:timers.map(t=>t.ms)};
 // the server is not reachable when the timer fires: no reload, the page says so
 up=false;reloads=0;shown.length=0;await fire();out.down={reloads,shown:shown.slice()};
 // answers, but not with a page
 up=true;ok=false;now+=20000;await render({error:'forbidden'});shown.length=0;await fire();out.notPage={reloads,shown:shown.slice()};
 // other errors never reload
 ok=true;now+=20000;timers.length=0;
 for(const e of ['offline','too many requests','unknown'])await render({error:e});
 out.others={timers:timers.length};
 // storage blocked: no guard is possible, so no reload either, and render still returns
 storeBroken=true;now+=20000;timers.length=0;fresh();await render({error:'forbidden'});
 out.blocked={timers:timers.length,text:notice.textContent};
 console.log(JSON.stringify(out));
})().catch(e=>{console.error(e);process.exit(1)});
"""


@unittest.skipIf(shutil.which('node') is None, 'node missing')
class TestSelfHeal(unittest.TestCase):
    """A stale token (the server restarted with a new one) heals itself, once per ten seconds, and only if the server answers."""

    def run_node(self):
        src = pathlib.Path('src/opos/web_static/js-4.js').read_text()
        heal = src[src.index('function healReload('):src.index('async function render(s){')]
        render = src[src.index('async function render(s){'):]
        render = render[:render.index('running=!!s.running')] + '}\n'
        code = HEAL + heal + render + HEAL_DRIVER
        proc = subprocess.run(['node', '--unhandled-rejections=strict', '-e', code], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_forbidden_heals_once_per_ten_seconds_and_only_when_the_server_answers(self):
        out = self.run_node()
        self.assertEqual(out['first'], {'text': 'Restarting the screen. One moment.', 'timers': [500], 'stored': True})
        self.assertEqual(out['reloadedAfterOk'], {'reloads': 1, 'fetches': [['/', 'no-store']]})
        self.assertEqual(out['inside'], {'timers': 0})
        self.assertEqual(out['after'], {'timers': [500]})
        self.assertEqual(out['down'], {'reloads': 0, 'shown': ['Still not reachable. Try again in a moment.']})
        self.assertEqual(out['notPage'], {'reloads': 0, 'shown': ['Still not reachable. Try again in a moment.']})
        self.assertEqual(out['others'], {'timers': 0})
        self.assertEqual(out['blocked'], {'timers': 0, 'text': 'Restarting the screen. One moment.'})

if __name__ == '__main__':
    unittest.main()
