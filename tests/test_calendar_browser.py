# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless Calendar flows at the spanned, single-pane and 200% phone sizes, light and dark."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from opos import calendar_page, web
from tests import test_uxr_acceptance as acceptance

DRIVER = Path(__file__).resolve().parent / "apps_cdp.js"

FLOW = r"""
const out=[];
const title=document.getElementById('ev-title');
if(!title)return ['no title'];
title.value='Kitchen';
document.getElementById('ev-date').value='2026-11-10';
document.getElementById('ev-time').value='15:00';
document.getElementById('save').click();
return new Promise(function(resolve){
var n=0,t=setInterval(function(){
n++;
var row=document.querySelector('#today button');
if(!row&&n<40)return;
clearInterval(t);
if(!row||row.textContent.indexOf('Kitchen')<0){resolve(out.concat(['no Kitchen']));return}
if(row.textContent.indexOf('15:00')<0)out.push('no time');
var save=document.getElementById('save');
var rs=save.getBoundingClientRect();
if(rs.height<48)out.push('Save '+Math.round(rs.height));
var month=document.getElementById('month');
var shown=getComputedStyle(month).display!=='none';
if(window.innerWidth>=1200){
  var mid=rs.left+rs.width/2;
  if(mid>=675&&mid<=717)out.push('Save on hinge');
  if(!shown)out.push('month hidden');
  var cell=month.querySelector('button');
  if(!cell)out.push('no day');
  else {
    var box=cell.getBoundingClientRect();
    var cmid=box.left+box.width/2;
    if(cmid>=675&&cmid<=717)out.push('day on hinge');
    if(box.height<48)out.push('day '+Math.round(box.height));
  }
  if(document.documentElement.getAttribute('data-hand')==='left'&&mid>717)out.push('Save stayed on the right');
} else if(shown) out.push('month shown on one pane');
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
resolve(out);
},80);
});
"""

LOCK = r"""
const out=[];
return new Promise(function(resolve){
var n=0,t=setInterval(function(){
n++;
var line=document.getElementById('lock-next');
if((!line||!line.textContent)&&n<50)return;
clearInterval(t);
if(!line){resolve(['no lock line']);return}
if(line.textContent!=='Next: 15:00')out.push('lock '+line.textContent);
if(line.textContent.indexOf('Kitchen')>=0||line.textContent.indexOf('Secret')>=0)out.push('title on lock');
resolve(out);
},80);
});
"""

CASES = (
    (1392, 900, "light", 1, "right"),
    (1392, 900, "dark", 1, "right"),
    (1392, 900, "light", 1, "left"),
    (675, 900, "light", 1, "right"),
    (675, 900, "dark", 1, "right"),
    (360, 640, "light", 2, "right"),
    (360, 640, "dark", 2, "right"),
)


class CalendarBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None
        if not cls.chrome or not cls.node:
            cls.skip_reason = "no headless chromium or node"
            return
        cls.tmp = tempfile.mkdtemp(prefix="calendar-browser-")
        cls._old = {key: os.environ.get(key) for key in ("OPOS_APPS", "OPOS_STATE_DIR", "OPOS_CLOCK_ZONE", "OPOS_CLOCK_OK", "OPOS_CALENDAR_TEST")}
        os.environ.update({
            "OPOS_APPS": "calendar", "OPOS_STATE_DIR": cls.tmp, "OPOS_CLOCK_ZONE": "UTC",
            "OPOS_CLOCK_OK": "1", "OPOS_CALENDAR_TEST": "1",
        })
        calendar_page.reset()
        calendar_page._FAKE = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        calendar_page.reset()
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

    def test_add_event_month_and_lock_line(self):
        cases = [{
            "url": self.origin + "/calendar",
            "width": w, "height": h, "theme": theme, "scale": scale, "hand": hand,
            "script": FLOW,
        } for w, h, theme, scale, hand in CASES]
        cases.append({
            "url": self.origin + "/",
            "width": 1392, "height": 900, "theme": "light", "scale": 1, "hand": "right",
            "script": LOCK,
        })
        plan = os.path.join(self.run, "plan.json")
        Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
        result = subprocess.run([self.node, str(DRIVER), str(self.port), plan], capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
