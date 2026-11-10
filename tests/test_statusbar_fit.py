# SPDX-License-Identifier: GPL-3.0-or-later
"""The status bar at its worst: the longest note, the low-battery chip and the Helper word, at the largest text and the narrowest windows.

Interface plan acceptance 15: the bar is not clipped in the low-battery state at Largest text on a 390 px window. When the three do
not fit, the note ("Battery not readable") gives way with an ellipsis; the chip and the Helper word are never cut and nothing runs
off the edge of the bar. The fit is measured in a real browser (skipped where there is none); the CSS rules are checked in text.
"""
import json
import os
from pathlib import Path
import subprocess
import unittest

from opos.web import page
from tests import test_uxr_acceptance as acceptance

CHECK = r"""
CHECKS.statusbar = async function() {
  const out = [];
  for (const [w, h] of [[390, 844], [320, 640]]) {
    await load('home', w, h, false);
    // The helper on, so that "Helper on" is in the bar; the stub serves the same state to every later poll, so each size is measured
    // in the one evaluation that renders the worst case. The page is the demo build, whose bar says "Demo time 12:00" and is not the
    // product's bar: the demo label is cleared so that the clock is the plain "12:00".
    await ev("demoTime='';api({action:'visible',visible:true}).then(function(s){window.__worst=s})");
    for (const size of ['n', 'l', 'xl']) {
      const m = await ev(`(function(){
        document.documentElement.dataset.text='${size}';
        var s=JSON.parse(JSON.stringify(window.__worst));s.battery.percent=12;s.status_note='Battery not readable';
        return render(s).then(function(){
          var sb=document.getElementById('sb'),b=document.getElementById('sb-batt'),e=document.getElementById('sb-engine'),r=b.getBoundingClientRect();
          return {width:innerWidth,sb:[sb.scrollWidth,sb.clientWidth],chipRight:Math.round(r.right),chip:b.textContent,engine:e.textContent,
                  engineCut:e.scrollWidth>e.clientWidth,note:document.getElementById('sb-net').textContent}})})()`);
      const tag = w + ' ' + size + ': ';
      if (m.chip !== 'LOW 12%') out.push(tag + 'chip says ' + m.chip);
      if (m.engine !== 'Helper on') out.push(tag + 'engine says ' + m.engine);
      if (m.note !== 'Battery not readable') out.push(tag + 'note says ' + m.note);
      if (m.sb[0] > m.sb[1]) out.push(tag + 'the bar is clipped: scroll width ' + m.sb[0] + ' > ' + m.sb[1]);
      if (m.chipRight > m.width) out.push(tag + 'the chip runs off the window: right edge ' + m.chipRight + ' > ' + m.width);
      if (m.engineCut) out.push(tag + 'the Helper word is cut');
    }
  }
  return out;
};
"""


class StatusBarCss(unittest.TestCase):
    def test_the_note_gives_way_before_the_helper_word(self):
        css = page()
        self.assertIn('#sb-net,#sb-engine{overflow:hidden;text-overflow:ellipsis}', css)
        # The Helper word keeps its own width (shrink 0) and takes the room that is left; the note is the one that shrinks.
        self.assertRegex(css, r'#sb-engine\{flex:1 0 auto;text-align:center\}')
        self.assertNotRegex(css, r'#sb-engine\{flex:1;')


class StatusBarFit(unittest.TestCase):
    skip_reason = None
    mode = 'port'
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown

    def test_worst_case_fits_at_390_and_320_px(self):
        driver = Path(self.run_dir) / 'statusbar.js'
        source = acceptance.DRIVER.read_text()
        self.assertIn('(async () => {', source)
        driver.write_text(source.replace('(async () => {', CHECK + '\n(async () => {', 1))
        target = str(self.port) if self.proc is not None else 'pipe'
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile, TMPDIR=self.run_dir)
        result = subprocess.run([self.node, str(driver), target, self.url, 'statusbar', self.state],
                                capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])['violations'], [])


if __name__ == '__main__':
    unittest.main()
