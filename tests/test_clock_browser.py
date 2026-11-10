# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless Clock flows: save 07:00, ring, snooze, stop, at three sizes in light and dark."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from opos import clock_page, web
from tests import test_uxr_acceptance as acceptance

DRIVER = Path(__file__).resolve().parent / "apps_cdp.js"
FIRE = "2026-11-16T07:00:30+00:00"
SNOOZE = "2026-11-16T07:10:30+00:00"

FLOW = r"""
const out=[];
const save=document.getElementById('save');
if(!save)return ['no Save'];
const token0=document.querySelector('meta[name=opos-token]').content;
function send0(body){return fetch('/api/clock',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':token0},body:JSON.stringify(body)}).then(function(r){return r.json()})}
const warn=document.getElementById('warn');
if(!warn)out.push('no clock-not-set line');
return fetch('/api/clock',{headers:{'X-OPOS-Token':token0}}).then(function(r){return r.json()}).then(function(s){
var chain=Promise.resolve();
(s.alarms||[]).forEach(function(a){chain=chain.then(function(){return send0({action:'delete',id:a.id})})});
return chain;
}).then(function(){
return fetch('/api/clock',{headers:{'X-OPOS-Token':token0}}).then(function(r){return r.json()});
}).then(function(s){
var fire=new Date(new Date(s.now).getTime()+120000);
function pad(n){return (n<10?'0':'')+n}
var stamp=pad(fire.getUTCHours())+':'+pad(fire.getUTCMinutes());
var input=document.getElementById('clock-time');
input.value=stamp;input.dispatchEvent(new Event('change'));
var tickAt=new Date(fire.getTime()+30000).toISOString();
var snoozeAt=new Date(fire.getTime()+630000).toISOString();
save.click();
return new Promise(function(resolve){
var n=0,t=setInterval(function(){
n++;
var row=document.querySelector('#rows button');
if(!row&&n<30)return;
clearInterval(t);
if(!row||row.textContent.indexOf(stamp)<0){resolve(['no '+stamp+' row']);return;}
if(row.textContent.indexOf('\u00b7 On')<0)out.push('not on '+row.textContent);
var token=document.querySelector('meta[name=opos-token]').content;
function send(body){return fetch('/api/clock',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':token},body:JSON.stringify(body)}).then(function(r){return r.json()})}
send({action:'tick',now:tickAt}).then(function(){return window.oposClock.refresh()}).then(function(){
var stop=document.getElementById('stop'),sn=document.getElementById('snooze');
var rs=stop.getBoundingClientRect();
if(rs.height<70)out.push('Stop '+Math.round(rs.height));
if((sn.textContent||'').trim()!=='Snooze 10 min')out.push('snooze label');
if(window.innerWidth>=1200){
  var mid=stop.getBoundingClientRect().left+stop.getBoundingClientRect().width/2;
  if(mid>=675&&mid<=717)out.push('Stop on hinge');
}
sn.click();
return window.oposClock.refresh();
}).then(function(){return send({action:'tick',now:snoozeAt})}).then(function(){return window.oposClock.refresh()}).then(function(){
document.getElementById('stop').click();
return new Promise(function(res){setTimeout(res,300)}).then(function(){return window.oposClock.refresh()});
}).then(function(){
if(!document.getElementById('ring').hidden)out.push('still ringing');
var bed=document.getElementById('bedside');
bed.click();
if(document.documentElement.getAttribute('data-bed')!=='1')out.push('bedside did not open');
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
resolve(out);
}).catch(function(e){resolve(out.concat([String(e)]))});
},80);
});
});
"""

CASES = (
    (1392, 900, "light", 1),
    (1392, 900, "dark", 1),
    (675, 900, "light", 1),
    (675, 900, "dark", 1),
    (360, 640, "light", 2),
    (360, 640, "dark", 2),
)


class ClockBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None
        if not cls.chrome or not cls.node:
            cls.skip_reason = "no headless chromium or node"
            return
        cls.tmp = tempfile.mkdtemp(prefix="clock-browser-")
        cls._old = {key: os.environ.get(key) for key in ("OPOS_APPS", "OPOS_STATE_DIR", "OPOS_CLOCK_ZONE", "OPOS_CLOCK_OK", "OPOS_CLOCK_TEST")}
        os.environ.update({
            "OPOS_APPS": "clock", "OPOS_STATE_DIR": cls.tmp, "OPOS_CLOCK_ZONE": "UTC",
            "OPOS_CLOCK_OK": "1", "OPOS_CLOCK_TEST": "1",
        })
        clock_page.reset()
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        clock_page.reset()
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
        clock_page.reset()
        # A fresh book for the next viewport, same directory, so a previous Stop does not linger.
        for name in os.listdir(self.tmp):
            if name.endswith(".json"):
                os.remove(os.path.join(self.tmp, name))

    def test_morning_alarm_flow(self):
        cases = [{
            "url": self.origin + "/clock",
            "width": w, "height": h, "theme": theme, "scale": scale, "hand": "right",
            "script": FLOW,
        } for w, h, theme, scale in CASES]
        plan = os.path.join(self.run, "plan.json")
        Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
        result = subprocess.run([self.node, str(DRIVER), str(self.port), plan], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
