# SPDX-License-Identifier: GPL-3.0-or-later
"""#sb-batt stays a plain status label.

Turning the 28px status-bar <span id="sb-batt"> into a role=button pseudo-control failed
test_uxr_acceptance (test_real_controls: not a native button; test_touch_targets: 24x48 and
overlapping #meter). docs/jaryo.md 4.2/4.3 put the shade on a native [ Shade ] <button>
(#jaryo-shade) instead, with #meter / #meter-hit as the existing shortcut.
"""
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

from opos.web import page
from tests import test_uxr_acceptance as acceptance

STATIC = Path(__file__).resolve().parents[1] / 'src/opos/web_static'


class TestShadeButtonStatic(unittest.TestCase):
    def test_row_on_page(self):
        out = page('t' * 32, 'n')
        for s in ("'jaryo-status-row'", "'jaryo-shade','Shade'", "'jaryo-lock','Lock'",
                  "sb.insertAdjacentElement('afterend',row)", '#jaryo-status-row button{min-height:48px;min-width:48px'):
            self.assertIn(s, out)


# Browser check (tests/uxr_cdp.js pattern): the visible native [ Shade ] button.
CHECK = r"""
CHECKS.jaryo_shade = async function() {
  await load('home',360,640,false);
  const out = [];
  const pre = await ev(`(function(){var o=[],b=document.getElementById('jaryo-shade'),row=document.getElementById('jaryo-status-row');
    if(!b||b.tagName!=='BUTTON'||b.type!=='button')return ['no native #jaryo-shade button'];
    if(!__u.vis(b)||b.textContent!=='Shade')o.push('Shade not visible');
    if(document.getElementById('sb').nextElementSibling!==row||!row.contains(b)||!row.contains(document.getElementById('jaryo-lock')))o.push('row not right after #sb');
    var sb=document.getElementById('sb-batt');if(sb.hasAttribute('role')||sb.hasAttribute('tabindex'))o.push('#sb-batt became a control');
    if(opos.isOpen())o.push('shade open at start');b.focus();return o})()`);
  out.push(...pre);
  if (pre.length) return out;
  await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});
  await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  if (!await waitFor(`opos.isOpen()&&document.getElementById('sheet').getAttribute('aria-hidden')==='false'`)) out.push('Enter on Shade did not open the shade');
  out.push(...await ev(`(async function(){var o=[],tick=function(){return new Promise(function(r){requestAnimationFrame(function(){requestAnimationFrame(r)})})};
    await tick();opos.close();await tick();
    if(opos.isOpen())o.push('shade did not close');
    if(document.activeElement.id!=='jaryo-shade')o.push('focus not returned: '+document.activeElement.id);
    document.getElementById('jaryo-shade').click();if(!opos.isOpen())o.push('click did not open');closeSheet();
    if(document.activeElement.id!=='jaryo-shade')o.push('focus not returned after closeSheet');
    document.getElementById('jaryo-shade').click();document.getElementById('scrim').click();await tick();
    if(opos.isOpen()||document.activeElement.id!=='jaryo-shade')o.push('scrim dismissal did not restore Shade focus');
    document.getElementById('jaryo-shade').click();document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();
    if(opos.isOpen()||document.activeElement.id!=='jaryo-shade')o.push('Escape dismissal did not restore Shade focus');
    // A later meter-owned opening must not inherit the previous Shade focus target.
    opos.open();document.getElementById('scrim').click();await tick();
    if(document.activeElement.id!=='meter')o.push('meter-owned dismissal returned to Shade');
    for(var opener of ['shade','meter']){
      oposUI.openApp('home');await tick();
      if(opener==='shade')document.getElementById('jaryo-shade').click();else opos.open();
      await tick();document.getElementById('qs-settings').click();await tick();
      if(opos.isOpen()||document.activeElement.id!=='back')o.push(opener+' navigation lost Back focus');
    }
    oposUI.openApp('home');await tick();
    // The kiosk currently uses fallback colours; no data-theme inversion is installed.
    var b=document.getElementById('jaryo-shade');b.focus();await tick();var c=getComputedStyle(b);
    if(c.outlineWidth!=='3px'||c.outlineOffset!=='3px')o.push('focus ring '+c.outlineWidth+'/'+c.outlineOffset);
    if(c.color===c.backgroundColor||c.outlineColor===c.backgroundColor)o.push('unreadable Shade contrast');
    document.documentElement.style.fontSize='200%';await tick();
    ['jaryo-shade','jaryo-lock'].forEach(function(id){var b=document.getElementById(id),r=b.getBoundingClientRect(),c=getComputedStyle(b);
      if(r.width<48||r.height<48)o.push(id+' '+Math.round(r.width)+'x'+Math.round(r.height)+' at 200%');
      if(b.scrollWidth>b.clientWidth+1||b.scrollHeight>b.clientHeight+1)o.push(id+' clips at 200%');
      if(c.backgroundImage!=='none'||c.boxShadow!=='none')o.push(id+' paint effect')});
    if(document.documentElement.scrollWidth>innerWidth+1)o.push('horizontal scroll at 200%');
    document.documentElement.style.fontSize='';await tick();
    document.getElementById('jaryo-lock').click();await tick();
    if(document.getElementById('lock').hidden)o.push('Lock button did not lock');
    else if(!document.getElementById('jaryo-status-row').inert)o.push('row reachable behind lock');
    oposUI.unlock();return o})()`));
  return out;
};
"""


class ShadeBrowser(unittest.TestCase):
    skip_reason = None
    mode = "port"
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown

    def test_shade_button(self):
        driver = Path(self.run_dir) / "shade.js"
        source = acceptance.DRIVER.read_text()
        self.assertIn("(async () => {", source)
        driver.write_text(source.replace("(async () => {", CHECK + "\n(async () => {", 1))
        target = str(self.port) if self.proc is not None else "pipe"
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile, TMPDIR=self.run_dir)
        r = subprocess.run([self.node, str(driver), target, self.url, "jaryo_shade", self.state],
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["violations"], [])


class TestSbBattNotPseudoButton(unittest.TestCase):
    def test_jaryo_js_leaves_sb_batt_alone(self):
        for name in ('jaryo.js', 'jaryo-layout.js'):
            src = (STATIC / name).read_text(encoding='utf-8')
            self.assertNotRegex(src, r"getElementById\(['\"]sb-batt['\"]\)", name)

    def test_sb_batt_markup_is_not_a_control(self):
        html = (STATIC / 'phone-a.html').read_text(encoding='utf-8')
        m = re.search(r'<span id="sb-batt"([^>]*)>', html)
        self.assertIsNotNone(m)
        self.assertNotIn('role=', m.group(1))
        self.assertNotIn('tabindex', m.group(1))


if __name__ == '__main__':
    unittest.main()
