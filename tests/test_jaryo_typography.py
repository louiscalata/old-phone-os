# SPDX-License-Identifier: GPL-3.0-or-later
"""JRY03 computed typography and real section rules in the existing kiosk DOM."""
import json
import os
import subprocess
import unittest
from pathlib import Path

from tests import test_uxr_acceptance as acceptance


CHECK = r"""
CHECKS.jaryo_typography = async function() {
  const out=[];
  for(const [w,h] of [[360,640],[1280,720]]) {
    await load('home',w,h,false);
    out.push(...await ev(`(async function(){
      const errors=[], tick=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
      const root=parseFloat(getComputedStyle(document.documentElement).fontSize);
      const check=(selector,family,size,weight,line)=>{
        const el=document.querySelector(selector);if(!el){errors.push('missing '+selector);return}
        const s=getComputedStyle(el);
        if(!s.fontFamily.includes(family))errors.push(selector+' font '+s.fontFamily);
        if(Math.abs(parseFloat(s.fontSize)-size*root)>.1)errors.push(selector+' size '+s.fontSize);
        if(s.fontWeight!==weight)errors.push(selector+' weight '+s.fontWeight);
        if(Math.abs(parseFloat(s.lineHeight)-size*root*line)>.2)errors.push(selector+' line '+s.lineHeight);
      };
      check('body','system-ui',1,'400',1.45);
      check('#jaryo-masthead','Georgia',2.25,'700',1.05);
      check('#settings h2','Georgia',1.5,'700',1.15);
      check('#settings h3','Arial Narrow',1.125,'400',1.2);
      check('#apps-list .jaryo-letter','Arial Narrow',1.125,'400',1.2);
      check('#sb','Arial Narrow',.875,'400',1.3);
      check('#lock-time','Georgia',5,'700',1);
      check('#sheet-title','Georgia',2.25,'700',1.05);
      for(const selector of ['#jaryo-masthead','#sheet-title']){
        const s=getComputedStyle(document.querySelector(selector));
        if(s.borderBottomWidth!=='3px'||s.borderBottomStyle!=='solid')errors.push(selector+' masthead rule');
      }
      for(const selector of ['#settings section','#qs>*','#apps-list li']){
        const items=document.querySelectorAll(selector);
        if(!items.length)errors.push('no real sections '+selector);
        items.forEach(el=>{const s=getComputedStyle(el);if(s.borderTopWidth!=='1px'||s.borderTopStyle!=='solid')errors.push(selector+' section rule')});
      }
      if(document.querySelectorAll('#jaryo-masthead').length!==1)errors.push('duplicate masthead');
      window.dispatchEvent(new Event('resize'));await tick();
      if(document.querySelectorAll('#jaryo-masthead').length!==1)errors.push('duplicate masthead after resize');
      document.documentElement.style.fontSize='200%';await tick();
      for(const app of ['home','settings']){
        oposUI.openApp(app);await tick();
        if(document.documentElement.scrollWidth>innerWidth+1)errors.push(app+' horizontal overflow at 200%');
        const el=document.getElementById('jaryo-masthead');
        if(el.scrollWidth>el.clientWidth+1)errors.push(app+' clipped masthead at 200%');
      }
      document.documentElement.style.fontSize='';return errors;
    })()`));
  }
  return out;
};
"""


class TestJaryoTypography(unittest.TestCase):
    skip_reason = None
    mode = "port"
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown

    def test_computed_typography_and_text_zoom(self):
        driver = Path(self.run_dir) / "typography.js"
        source = acceptance.DRIVER.read_text()
        self.assertIn("(async () => {", source)
        driver.write_text(source.replace("(async () => {", CHECK + "\n(async () => {", 1))
        target = str(self.port) if self.proc is not None else "pipe"
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile,
                   TMPDIR=self.run_dir)
        result = subprocess.run(
            [self.node, str(driver), target, self.url, "jaryo_typography", self.state],
            capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["violations"], [])


if __name__ == "__main__":
    unittest.main()
