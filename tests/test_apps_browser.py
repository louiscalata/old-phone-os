# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless flows for /apps at the spanned, single-pane and 200% phone sizes, light and dark."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from opos import apps, web
from tests import test_uxr_acceptance as acceptance
from tests.test_apps_shell import SAMPLE

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "apps_cdp.js"

SHELL = r"""
const out=[];
const home=document.getElementById('home');
if(!home)out.push('no Home');
else{
  const r=home.getBoundingClientRect();
  if(r.height<55)out.push('Home height '+Math.round(r.height));
  if((home.textContent||'').trim()!=='Home')out.push('Home label');
}
const h1=document.querySelector('h1');
if(!h1||h1.textContent.trim()!=='Apps')out.push('title');
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll '+document.documentElement.scrollWidth);
document.querySelectorAll('a,button').forEach(function(el){
  const r=el.getBoundingClientRect();
  if(r.width<1||r.height<1)return;
  if(r.height<47)out.push('target '+(el.id||el.textContent.trim().slice(0,16))+' '+Math.round(r.height));
});
if(window.innerWidth>=1200){
  document.querySelectorAll('h1,h2,p,a,button,li').forEach(function(el){
    const r=el.getBoundingClientRect();
    if(r.width<2||r.height<2)return;
    const mid=r.left+r.width/2;
    if(mid>=675&&mid<=717)out.push('hinge '+el.tagName+' '+(el.textContent||'').trim().slice(0,24));
  });
  const list=document.getElementById('list'),thumb=document.getElementById('thumb');
  if(!list||!thumb)out.push('panes missing');
  else{
    const L=list.getBoundingClientRect(),R=thumb.getBoundingClientRect();
    const hand=document.documentElement.getAttribute('data-hand');
    if(hand==='left'){
      if(!(R.right<=676&&L.left>=716))out.push('hand left list '+Math.round(L.left)+' thumb '+Math.round(R.right));
    }else if(!(L.right<=676&&R.left>=716))out.push('hand right list '+Math.round(L.right)+' thumb '+Math.round(R.left));
  }
}
const bg=getComputedStyle(document.body).backgroundColor;
const dark=document.documentElement.getAttribute('data-theme')==='dark';
if(dark){if(bg!=='rgb(0, 0, 0)')out.push('bg '+bg)}
else if(bg!=='rgb(243, 240, 232)')out.push('bg '+bg);
const card=document.body.innerText||'';
if(card.indexOf('Works offline: yes.')<0)out.push('no disclosure');
if(card.indexOf('Practice')>=0&&card.indexOf('Real or practice')<0)out.push('practice word missing its label');
return out;
"""

FLOW = r"""
return new Promise(function(resolve){
var n=0,t=setInterval(function(){
n++;
var all=document.getElementById('allapps'),home=document.getElementById('home');
if(!(all&&home&&!home.hidden)&&n<40)return;
clearInterval(t);
if(!(all&&home&&!home.hidden)){resolve({problems:['home not shown']});return;}
all.click();
setTimeout(function(){
var out=[],link=document.getElementById('more-apps');
if(!link){resolve({problems:['no More apps']});return;}
if((link.textContent||'').trim()!=='More apps')out.push('link label');
if(link.getAttribute('href')!=='/apps')out.push('link href');
var names=[].map.call(document.querySelectorAll('#apps-list button'),function(b){return (b.textContent||'').trim()});
['Phone','Messages','Helper','Settings'].forEach(function(name){if(names.indexOf(name)<0)out.push('missing '+name)});
var r=link.getBoundingClientRect();
if(r.height<47)out.push('link height '+Math.round(r.height));
resolve({problems:out,next:link.href});
},60);
},50);
});
"""

AFTER = r"""
const out=[];
const h=document.querySelector('h1');
if(!h||h.textContent.trim()!=='Apps')out.push('did not open Apps');
const sample=[].filter.call(document.querySelectorAll('a'),function(a){return (a.textContent||'').trim()==='Sample'});
if(!sample.length)out.push('Sample missing');
else if((sample[0].getAttribute('data-card')||'').indexOf('Works offline:')<0)out.push('card missing');
const home=document.getElementById('home');
if(!home||home.getAttribute('href')!=='/')out.push('Home');
else if(home.getBoundingClientRect().height<55)out.push('Home height');
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
return out;
"""

CASES = (
    (1392, 900, "light", 1, "right"),
    (1392, 900, "dark", 1, "right"),
    (675, 900, "light", 1, "right"),
    (675, 900, "dark", 1, "right"),
    (360, 640, "light", 2, "right"),
    (360, 640, "dark", 2, "right"),
    (1392, 900, "light", 1, "left"),
    (1392, 900, "dark", 1, "left"),
)


class AppsBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None
        if not cls.chrome:
            cls.skip_reason = "no headless chromium"
            return
        if not cls.node:
            cls.skip_reason = "no node"
            return
        cls._saved = os.environ.get("OPOS_APPS")
        os.environ["OPOS_APPS"] = "sample"
        apps.register(SAMPLE)
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.tmp = tempfile.mkdtemp(prefix="apps-browser-")

    @classmethod
    def tearDownClass(cls):
        apps.unregister("sample")
        if getattr(cls, "_saved", None) is None:
            os.environ.pop("OPOS_APPS", None)
        elif getattr(cls, "_saved", None) is not None:
            os.environ["OPOS_APPS"] = cls._saved
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
        self.proc = None
        self.run = tempfile.mkdtemp(dir=self.tmp)
        try:
            self.proc, self.port = acceptance._launch(self.chrome, self.run)
        except (OSError, RuntimeError) as exc:
            self.skipTest(str(exc)[-200:])

    def tearDown(self):
        acceptance._kill(getattr(self, "proc", None))

    def _run(self, cases):
        plan = self.run + "/plan.json"
        Path(plan).write_text(json.dumps({"cases": cases}), encoding="utf-8")
        result = subprocess.run([self.node, str(DRIVER), str(self.port), plan], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        return json.loads(result.stdout.strip().splitlines()[-1])["violations"]

    def test_index_at_three_sizes_in_light_and_dark(self):
        cases = [{
            "url": self.origin + "/apps",
            "width": w, "height": h, "theme": theme, "scale": scale, "hand": hand,
            "script": SHELL,
        } for w, h, theme, scale, hand in CASES]
        self.assertEqual(self._run(cases), [])

    def test_more_apps_opens_the_index_and_home_returns(self):
        cases = [{
            "url": self.origin + "/?lock=0",
            "width": w, "height": h, "theme": theme, "scale": scale, "hand": "right",
            "script": FLOW, "after": AFTER,
        } for w, h, theme, scale, hand in CASES if hand == "right"]
        self.assertEqual(self._run(cases), [])


if __name__ == "__main__":
    unittest.main()
