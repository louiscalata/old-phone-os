# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless calculator: 1 + 2 = 3 at three sizes, light and dark."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from opos import web
from tests import test_uxr_acceptance as acceptance

DRIVER = Path(__file__).resolve().parent / "apps_cdp.js"
FLOW = r"""
const out=[];
function press(k){var b=document.querySelector('#keys button[data-k="'+k+'"]');if(!b){out.push('no '+k);return;}b.click();}
press('1');press('+');press('2');press('=');
var result=document.getElementById('result');
if(!result||result.textContent.trim()!=='3')out.push('result '+(result&&result.textContent));
var key=document.querySelector('#keys button');
if(key&&key.getBoundingClientRect().height<60)out.push('key '+Math.round(key.getBoundingClientRect().height));
if(window.innerWidth>=1200){
  var eq=document.getElementById('eq').getBoundingClientRect();
  var mid=eq.left+eq.width/2;
  if(mid>=675&&mid<=717)out.push('equals on hinge');
}
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
return out;
"""
CASES = ((1392, 900, "light", 1), (1392, 900, "dark", 1), (675, 900, "light", 1), (675, 900, "dark", 1), (360, 640, "light", 2), (360, 640, "dark", 2))


class CalcBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None if cls.chrome and cls.node else "no headless chromium or node"
        if cls.skip_reason:
            return
        cls._old = os.environ.get("OPOS_APPS")
        os.environ["OPOS_APPS"] = "calc"
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.tmp = tempfile.mkdtemp(prefix="calc-browser-")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_old", None) is None:
            os.environ.pop("OPOS_APPS", None)
        else:
            os.environ["OPOS_APPS"] = cls._old
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

    def test_one_plus_two(self):
        cases = [{"url": self.origin + "/calc", "width": w, "height": h, "theme": t, "scale": s, "hand": "right", "script": FLOW} for w, h, t, s in CASES]
        plan = os.path.join(self.run, "plan.json")
        Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
        result = subprocess.run([self.node, str(DRIVER), str(self.port), plan], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
