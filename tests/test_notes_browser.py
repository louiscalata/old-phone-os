# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless notes: type, see Saved, leave, and find the text again."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from opos import notes_page, web
from tests import test_uxr_acceptance as acceptance

DRIVER = Path(__file__).resolve().parent / "apps_cdp.js"
FLOW = r"""
const out=[];
var typed=false;
var rowsBefore=document.querySelectorAll('#rows button').length;
document.getElementById('fresh').click();
return new Promise(function(resolve){
var n=0,t=setInterval(function(){
n++;
var box=document.getElementById('body');
var rows=document.querySelectorAll('#rows button');
if(!typed){
  if(!box||rows.length<=rowsBefore||rows[0].textContent!=='Empty note'||box.value!==''){
    if(n<120)return;
    clearInterval(t);resolve(['new note not ready']);return;
  }
  typed=true;box.value='Hello note';box.dispatchEvent(new Event('input'));return;
}
var saved=box&&document.getElementById('saved').textContent.indexOf('Saved')>=0&&rows[0]&&rows[0].textContent==='Hello note';
if(!saved&&n<120)return;
clearInterval(t);
if(!box||box.value!=='Hello note'){resolve(['text missing']);return;}
if(!saved)out.push('not saved');
var rect=box.getBoundingClientRect();
if(rect.height<48)out.push('field '+Math.round(rect.height));
if(window.innerWidth>=1200){
  var mid=rect.left+rect.width/2;
  if(mid>=675&&mid<=717)out.push('field on hinge');
}
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
resolve(out);
},100);
});
"""
CASES = ((1392, 900, "light", 1), (1392, 900, "dark", 1), (675, 900, "light", 1), (675, 900, "dark", 1), (360, 640, "light", 2), (360, 640, "dark", 2))


class NotesBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None if cls.chrome and cls.node else "no headless chromium or node"
        if cls.skip_reason:
            return
        cls.tmp = tempfile.mkdtemp(prefix="notes-browser-")
        cls._old = {k: os.environ.get(k) for k in ("OPOS_APPS", "OPOS_STATE_DIR")}
        os.environ["OPOS_APPS"] = "notes"
        os.environ["OPOS_STATE_DIR"] = cls.tmp
        notes_page.reset()
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        notes_page.reset()
        for key, value in getattr(cls, "_old", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if getattr(cls, "server", None):
            cls.server.shutdown()
            cls.server.server_close()
            cls.thread.join(10)
        if getattr(cls, "tmp", None):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        if self.skip_reason:
            self.skipTest(self.skip_reason)
        self.run = tempfile.mkdtemp(dir=self.tmp)
        try:
            self.proc, self.port = acceptance._launch(self.chrome, self.run)
        except (OSError, RuntimeError) as exc:
            self.skipTest(str(exc)[-200:])

    def tearDown(self):
        acceptance._kill(getattr(self, "proc", None))

    def test_type_and_autosave(self):
        cases = [{"url": self.origin + "/notes", "width": w, "height": h, "theme": t, "scale": s, "hand": "right", "script": FLOW} for w, h, t, s in CASES]
        plan = os.path.join(self.run, "plan.json")
        Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
        result = subprocess.run([self.node, str(DRIVER), str(self.port), plan], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-1200:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
