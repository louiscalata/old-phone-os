# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless voice-memo controls. The microphone itself is not used."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from opos import memos_page, web
from tests import test_uxr_acceptance as acceptance

DRIVER = Path(__file__).resolve().parent / "apps_cdp.js"

FLOW = r"""
const out=[];
const rec=document.getElementById('record');
const stop=document.getElementById('stop');
if(!rec||(rec.textContent||'').trim()!=='Record')return ['no Record'];
if(!stop||(stop.textContent||'').trim()!=='Stop')out.push('no Stop');
var rs=rec.getBoundingClientRect();
if(rs.height<48)out.push('Record '+Math.round(rs.height));
if(window.innerWidth>=1200){
  var mid=rs.left+rs.width/2;
  if(mid>=675&&mid<=717)out.push('Record on hinge');
  if(document.documentElement.getAttribute('data-hand')==='left'&&mid>717)out.push('Record stayed on the right');
}
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
return out;
"""

CASES = (
    (1392, 900, "light", 1, "right"),
    (1392, 900, "dark", 1, "right"),
    (1392, 900, "light", 1, "left"),
    (675, 900, "light", 1, "right"),
    (360, 640, "light", 2, "right"),
    (360, 640, "dark", 2, "right"),
)


class MemosBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None
        if not cls.chrome or not cls.node:
            cls.skip_reason = "no headless chromium or node"
            return
        cls.tmp = tempfile.mkdtemp(prefix="memos-browser-")
        cls._old = {key: os.environ.get(key) for key in ("OPOS_APPS", "OPOS_STATE_DIR", "OPOS_MEMOS_DIR")}
        os.environ.update({
            "OPOS_APPS": "memos", "OPOS_STATE_DIR": cls.tmp,
            "OPOS_MEMOS_DIR": os.path.join(cls.tmp, "memos"),
        })
        memos_page.reset()
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        memos_page.reset()
        for key, value in getattr(cls, "_old", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        server = getattr(cls, "server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
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

    def test_record_and_stop_are_text_buttons(self):
        cases = [{
            "url": self.origin + "/memos",
            "width": w, "height": h, "theme": theme, "scale": scale, "hand": hand,
            "script": FLOW,
        } for w, h, theme, scale, hand in CASES]
        plan = os.path.join(self.run, "plan.json")
        Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
        result = subprocess.run([self.node, str(DRIVER), str(self.port), plan], capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
