# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in AI mode browser checks; never discover or launch an implicit browser."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opos import ai_answers, ai_page, apps, assist
from opos.store import Store
from tests.ai_page_support import LONG_ANSWER, PC, FakeLink, running

ROOT = Path(__file__).resolve().parents[1]
FLOW = r"""
return new Promise(function(resolve){
 var count=0,timer=setInterval(function(){
  if(++count<80&&document.getElementById('gpu-answer').textContent.length!==12000)return;
  clearInterval(timer);var out=[],pane=new URLSearchParams(location.search).get('pane');
  var phone=document.getElementById('list'),gpu=document.getElementById('thumb');
  function visible(e){return !e.hidden&&getComputedStyle(e).display!=='none'}
  function rect(id){return document.getElementById(id).getBoundingClientRect()}
  if(!document.getElementById('phone-pane')||!document.getElementById('gpu-pane'))out.push('missing pane');
  if(visible(phone)===(pane==='gpu'))out.push('phone visibility');
  if(visible(gpu)===(pane==='phone'))out.push('GPU visibility');
  var text=document.getElementById('gpu-answer');
  if(!text.textContent.startsWith('<script>literal</script><b>text</b>')||text.querySelector('script,b'))out.push('answer markup');
  if(parseFloat(getComputedStyle(document.body).fontSize)<18)out.push('small body text');
  if(document.documentElement.scrollWidth>innerWidth+2)out.push('horizontal scroll');
  if(!pane&&innerWidth>=1000){
   var a=rect('phone-pane'),b=rect('gpu-pane'),gap=rect('gap');
   if(a.right>gap.left+1||b.left<gap.right-1)out.push('hinge overlap');
   if(Math.abs(a.top-b.top)>1)out.push('panes not side by side');
   if(Math.abs(rect('phone-answer').top-rect('gpu-answer').top)>1)out.push('answer boxes not aligned');
   if(Math.abs(rect('phone-title').top-rect('gpu-title').top)>1)out.push('headers not aligned');
   var controls=rect('controls'),home=rect('home');
   if(controls.right>gap.left+1||home.left<gap.right-1)out.push('controls or Home in wrong pane');
   if(controls.bottom>a.top+1||home.top<b.bottom-1)out.push('wide part order');
  }
  var order=['ai-title','controls'];
  if(!pane||pane==='phone')order.push('phone-pane');
  if(!pane||pane==='gpu')order.push('gpu-pane');
  order.push('home');
  if(pane||innerWidth<1000)for(var i=1;i<order.length;i++){
   if(rect(order[i]).top<rect(order[i-1]).bottom-1)out.push('stacked part order '+order[i]);
  }
  ['phone','gpu'].forEach(function(n){
   if(pane&&pane!==n)return;
   var box=document.getElementById(n+'-answer'),style=getComputedStyle(box),r=box.getBoundingClientRect();
   var limit=innerHeight*(!pane&&innerWidth>=1000?0.55:0.30);
   if(box.textContent.length!==12000)out.push('missing long answer');
   if(r.height>parseFloat(style.maxHeight)+1||r.height>limit+1)out.push('answer exceeds greatest height');
   if(box.scrollHeight<=box.clientHeight||style.overflowY!=='auto')out.push('answer not scrolling');
   if(parseFloat(getComputedStyle(document.getElementById(n+'-speed')).fontSize)<32)out.push('small speed headline');
   if(!box.getAttribute('aria-label')||box.tabIndex!==0)out.push('answer accessibility');
   box.focus({preventScroll:true});if(document.activeElement!==box)out.push('answer keyboard focus');
   var header=['title','model','speed','state'];if(n==='gpu')header.push('cable','disclosure');header.push('answer');
   for(var j=1;j<header.length;j++){
    if(rect(n+'-'+header[j]).top<rect(n+'-'+header[j-1]).bottom-1)out.push('header order');
   }
  });
  var ask=rect('ask');if(ask.top<0||ask.bottom>innerHeight)out.push('Ask outside window');
  var q=document.getElementById('question');q.focus();if(document.activeElement!==q)out.push('question focus');
  document.querySelectorAll('button').forEach(function(b){if(!b.textContent.trim())out.push('unlabelled button')});
  var controls=document.getElementById('controls').getBoundingClientRect();
  if(controls.width<innerWidth/2&&pane)out.push('single pane too narrow');
  resolve(out);
 },100);
});
"""


class AiBrowser(unittest.TestCase):
    def test_three_layouts_and_literal_answer(self):
        chrome = os.environ.get("OPOS_TEST_CHROME") or os.environ.get("CHROME")
        if not chrome:
            self.skipTest("OPOS_TEST_CHROME and CHROME are unset; no browser was launched")
        chrome = shutil.which(chrome)
        node = shutil.which("node")
        if not chrome or not node:
            self.skipTest("the explicitly selected browser or Node.js is unavailable")
        tmp = self.enterContext(tempfile.TemporaryDirectory(dir=ROOT, ignore_cleanup_errors=True))
        self.enterContext(mock.patch.dict(os.environ, {"OPOS_APPS": "ai", "OPOS_STATE_DIR": tmp, "OPOS_LOCK": "0"}))
        state = ai_page.PageState()
        self.enterContext(mock.patch.object(ai_page, "_STATE", state))
        self.addCleanup(state.close)
        answers = ai_answers.AnswerStore(Store(tmp))
        self.enterContext(mock.patch.object(ai_answers, "shared", return_value=answers))
        self.enterContext(mock.patch.object(assist, "local_route", return_value=None))
        link = FakeLink(answers)
        link.value["pc"] = dict(PC)
        self.enterContext(mock.patch.object(ai_page, "gpu_link_server", SimpleNamespace(service=lambda: link)))
        apps.register(ai_page.APP)
        self.addCleanup(apps.unregister, "ai")
        self.assertEqual(len(LONG_ANSWER.encode("utf-8")), 12000)
        for pane in ("phone", "gpu"):
            answer = answers.ask(pane, "Public browser fixture", model="Fixture model")
            answers.begin(answer["id"])
            answers.note(answer["id"], where="Fixture location", tps=18, tokens=2000)
            answers.append(answer["id"], 0, LONG_ANSWER.encode("utf-8"), done=True, finish="stop")
        proc, port = self.launch(chrome, Path(tmp))
        self.addCleanup(self.stop, proc)
        with running() as server:
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            cases = [
                {
                    "url": origin + "/ai" + query,
                    "width": width,
                    "height": 900,
                    "theme": theme,
                    "hand": hand,
                    "scale": scale,
                    "script": FLOW,
                }
                for width, scale in ((1392, 1), (675, 1), (360, 2))
                for theme in ("light", "dark")
                for hand in ("left", "right")
                for query in ("", "?pane=phone", "?pane=gpu")
            ]
            plan = Path(tmp) / "plan.json"
            plan.write_text(json.dumps({"cases": cases}), encoding="utf-8")
            result = subprocess.run(
                [node, str(ROOT / "tests/apps_cdp.js"), str(port), str(plan)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-1200:])
            self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])

    def launch(self, chrome, tmp):
        profile = tmp / "browser-profile"
        proc = subprocess.Popen(
            [
                chrome,
                "--headless",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-sync",
                "--no-first-run",
                "--no-default-browser-check",
                "--use-mock-keychain",
                "--password-store=basic",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
                "--remote-debugging-port=0",
                "--user-data-dir=" + str(profile),
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **({"start_new_session": True} if os.name == "posix" else {}),
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                port = int((profile / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()[0])
                return proc, port
            except (OSError, ValueError, IndexError):
                time.sleep(0.05)
        self.stop(proc)
        self.fail("The explicitly selected browser did not open its local test port.")

    def stop(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self.end_group(proc)

    @staticmethod
    def end_group(proc):
        """End Chrome's helper processes too: one that still writes into the profile folder breaks its removal."""
        if os.name != "posix":
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
