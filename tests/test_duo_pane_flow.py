# SPDX-License-Identifier: GPL-3.0-or-later
"""At 1392 x 900, no app control sits on the hinge band from x 675 to x 717."""
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
PATHS = ("/apps", "/clock", "/contacts", "/notes", "/calc")
HINGE = r"""
document.documentElement.style.setProperty('--hinge','42px');
document.documentElement.style.setProperty('--pane','675px');
void document.body.offsetWidth;
var lo=675, hi=717, bad=[];
function shown(e){for(var p=e;p&&p.nodeType===1;p=p.parentElement){if(p.hidden)return false;var c=getComputedStyle(p);if(c.display==='none'||c.visibility==='hidden')return false}return true}
document.querySelectorAll('button,input,select,textarea,a').forEach(function(b){
  if(!shown(b))return;
  var r=b.getBoundingClientRect();
  if(r.width>0&&r.height>0&&r.left<hi-0.5&&r.right>lo+0.5)bad.push((b.id||b.textContent||b.tagName).trim().slice(0,24)+' '+Math.round(r.left)+'-'+Math.round(r.right));
});
return bad;
"""


class PaneFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None
        cls._old = os.environ.get("OPOS_APPS")
        cls._old_state = os.environ.get("OPOS_STATE_DIR")
        if not cls.chrome or not cls.node:
            cls.skip_reason = "no headless chromium or node"
            return
        cls.tmp = tempfile.mkdtemp(prefix="pane-flow-")
        os.environ["OPOS_APPS"] = "clock,contacts,notes,calc"
        os.environ["OPOS_STATE_DIR"] = cls.tmp
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        if cls._old is None:
            os.environ.pop("OPOS_APPS", None)
        else:
            os.environ["OPOS_APPS"] = cls._old
        if cls._old_state is None:
            os.environ.pop("OPOS_STATE_DIR", None)
        else:
            os.environ["OPOS_STATE_DIR"] = cls._old_state
        server = getattr(cls, "server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
            cls.thread.join(10)
        if getattr(cls, "tmp", None):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_doc_names_each_pattern(self):
        text = (Path(__file__).resolve().parents[1] / "docs/jaryo-panes.md").read_text(encoding="utf-8")
        for name in ("list-detail", "companion pane", "extended canvas", "Clock", "Contacts", "Notes", "Calculator"):
            self.assertIn(name, text)
        self.assertIn("https://learn.microsoft.com/en-us/previous-versions/xamarin/xamarin-forms/app-fundamentals/dual-screen/design-patterns", text)
        self.assertIn("675", text)
        self.assertIn("717", text)

    def test_no_control_crosses_the_hinge(self):
        if self.skip_reason:
            self.skipTest(self.skip_reason)
        run = tempfile.mkdtemp(dir=self.tmp)
        try:
            proc, port = acceptance._launch(self.chrome, run)
        except (OSError, RuntimeError) as exc:
            self.skipTest(str(exc)[-200:])
        try:
            cases = [{
                "url": self.origin + path,
                "width": 1392, "height": 900, "theme": "light", "scale": 1, "hand": "right",
                "script": HINGE,
            } for path in PATHS]
            plan = os.path.join(run, "plan.json")
            Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
            result = subprocess.run([self.node, str(DRIVER), str(port), plan], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr[-1500:])
            self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])
        finally:
            acceptance._kill(proc)
