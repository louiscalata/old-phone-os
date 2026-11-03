# SPDX-License-Identifier: GPL-3.0-or-later
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
    ['forbidden','This page is out of date. Reload it.'],
    ['offline','Phone service not reachable. Reload the page.'],
    ['too many requests','Too many taps. Wait a few seconds, then try again.'],
    ['unknown','That did not work. Reload the page, then try again.']
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

if __name__ == '__main__':
    unittest.main()
