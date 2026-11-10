# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless music: a silent file, text controls, and the position line, at three sizes."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from opos import music_page, web
from tests import test_uxr_acceptance as acceptance

DRIVER = Path(__file__).resolve().parent / "apps_cdp.js"

FLOW = r"""
const out=[];
const play=document.getElementById('play');
const audio=document.getElementById('player');
if(!play||!audio)return ['no player'];
if(audio.hasAttribute('controls'))out.push('native controls');
return new Promise(function(resolve){
var n=0,clicked=false,t=setInterval(function(){
n++;
var row=document.querySelector('#rows button');
if(!row&&n<50)return;
if(!row||row.textContent.indexOf('quiet')<0){clearInterval(t);resolve(out.concat(['no quiet']));return}
if(!clicked){row.click();clicked=true;return}
var pos=document.getElementById('pos').textContent||'';
if(pos.indexOf('/ 0:01')<0&&n<50)return;
clearInterval(t);
if(pos.indexOf('/ 0:01')<0)out.push('pos '+pos);
var rs=play.getBoundingClientRect();
if(rs.height<48)out.push('Play '+Math.round(rs.height));
if(window.innerWidth>=1200){
  var mid=rs.left+rs.width/2;
  if(mid>=675&&mid<=717)out.push('Play on hinge');
  if(document.documentElement.getAttribute('data-hand')==='left'&&mid>717)out.push('Play stayed on the right');
}
if(document.documentElement.scrollWidth>window.innerWidth+2)out.push('hscroll');
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


def write_wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 8000)


class MusicBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = acceptance.find_chrome()
        cls.node = shutil.which("node")
        cls.skip_reason = None
        if not cls.chrome or not cls.node:
            cls.skip_reason = "no headless chromium or node"
            return
        cls.tmp = tempfile.mkdtemp(prefix="music-browser-")
        cls.music = os.path.join(cls.tmp, "Music")
        write_wav(Path(cls.music) / "Kitchen" / "quiet.wav")
        cls._old = {key: os.environ.get(key) for key in ("OPOS_APPS", "OPOS_STATE_DIR", "OPOS_MUSIC_DIR")}
        os.environ.update({"OPOS_APPS": "music", "OPOS_STATE_DIR": cls.tmp, "OPOS_MUSIC_DIR": cls.music})
        music_page.reset()
        cls.server = web.make_server(0, web.Phone())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        music_page.reset()
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

    def test_silent_file_position_and_controls(self):
        cases = [{
            "url": self.origin + "/music",
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
