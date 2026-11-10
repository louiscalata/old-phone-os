# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless notices: a dismiss button and quiet hours, at three sizes."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from opos import inbox, web
from tests import test_uxr_acceptance as acceptance

DRIVER = Path(__file__).resolve().parent / "apps_cdp.js"
FLOW = r"""
const out=[];
const token=document.querySelector('meta[name=opos-token]').content;
function api(b){return fetch('/api/notices',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':token},body:JSON.stringify(b)}).then(function(r){return r.json()})}
return api({action:'add',kind:'message',text:'New message'}).then(function(){
return api({action:'quiet',on:true,from:'22:00',to:'07:00'});
}).then(function(){return window.oposNotices.refresh()}).then(function(){
var dismiss=document.querySelector('#rows button');
if(!dismiss||dismiss.getBoundingClientRect().height<48)out.push('Dismiss short');
if((document.getElementById('qon').textContent||'').indexOf('On')<0)out.push('quiet');
if((document.body.textContent||'').indexOf('Alarms always ring')<0)out.push('alarms');
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
return out;
});
"""
CASES = ((1392, 900, "light", 1), (1392, 900, "dark", 1), (675, 900, "light", 1), (675, 900, "dark", 1), (360, 640, "light", 2), (360, 640, "dark", 2))


class NoticesBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None if cls.chrome and cls.node else "no headless chromium or node"
        if cls.skip_reason:
            return
        cls.tmp = tempfile.mkdtemp(prefix="notices-browser-")
        cls._old = {k: os.environ.get(k) for k in ("OPOS_APPS", "OPOS_STATE_DIR")}
        os.environ.update({"OPOS_APPS": "notices", "OPOS_STATE_DIR": cls.tmp})
        inbox.reset()
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        inbox.reset()
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

    def test_dismiss_and_quiet_hours(self):
        cases = [{"url": self.origin + "/notices", "width": w, "height": h, "theme": t, "scale": s, "hand": "right", "script": FLOW} for w, h, t, s in CASES]
        plan = os.path.join(self.run, "plan.json")
        Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
        result = subprocess.run([self.node, str(DRIVER), str(self.port), plan], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
