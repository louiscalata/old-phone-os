# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless contacts flow: add Ana, mark her a favorite, and read the practice call line."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from opos import contacts_page, web
from tests import test_uxr_acceptance as acceptance

DRIVER = Path(__file__).resolve().parent / "apps_cdp.js"
FLOW = r"""
const out=[];
document.getElementById('fresh').click();
document.getElementById('cname').value='Ana';
document.getElementById('cnum').value='+1 555 0101';
document.getElementById('fav').click();
document.getElementById('save').click();
return new Promise(function(resolve){
var n=0,t=setInterval(function(){
n++;
var row=[].filter.call(document.querySelectorAll('#rows button'),function(b){return b.textContent.indexOf('Ana')>=0})[0];
if(!row&&n<30)return;
clearInterval(t);
if(!row){resolve(['Ana missing']);return;}
if(row.textContent.indexOf('Favorite')<0)out.push('not favorite');
row.click();
document.getElementById('call').click();
var line=document.getElementById('call-line').textContent||'';
if(line.indexOf('Practice call')<0)out.push('call line '+line);
if(window.innerWidth>=1200){
  var call=document.getElementById('call').getBoundingClientRect();
  var mid=call.left+call.width/2;
  if(mid>=675&&mid<=717)out.push('Call on hinge');
}
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
resolve(out);
},80);
});
"""
CASES = ((1392, 900, "light", 1), (1392, 900, "dark", 1), (675, 900, "light", 1), (675, 900, "dark", 1), (360, 640, "light", 2), (360, 640, "dark", 2))


class ContactsBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None if cls.chrome and cls.node else "no headless chromium or node"
        if cls.skip_reason:
            return
        cls.tmp = tempfile.mkdtemp(prefix="contacts-browser-")
        cls._old = {k: os.environ.get(k) for k in ("OPOS_APPS", "OPOS_STATE_DIR")}
        os.environ["OPOS_APPS"] = "contacts"
        os.environ["OPOS_STATE_DIR"] = cls.tmp
        contacts_page.reset()
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        contacts_page.reset()
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

    def test_add_ana_and_practice_call(self):
        cases = [{"url": self.origin + "/contacts", "width": w, "height": h, "theme": t, "scale": s, "hand": "right", "script": FLOW} for w, h, t, s in CASES]
        plan = os.path.join(self.run, "plan.json")
        Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
        result = subprocess.run([self.node, str(DRIVER), str(self.port), plan], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-1200:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
