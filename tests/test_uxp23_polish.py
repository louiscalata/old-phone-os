# SPDX-License-Identifier: GPL-3.0-or-later
"""Small portrait polish, with the existing optional CDP browser harness."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from opos.web import page
from tests import test_uxr_acceptance as acceptance


class PolishStatic(unittest.TestCase):
    def test_portrait_and_notice(self):
        out = page()
        self.assertIn('@media (max-width:839px)', out)
        self.assertRegex(out, r'#phone\{width:100%;max-width:none;box-sizing:border-box;padding:16px')
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', out)
        # JRY04: the notice is the front-page lede directly under the tiles (margin-top:0, no void). The old grey
        # card with a yellow bar (phone-a.css) is gone (B-03 of the interface plan); the Jaryo rule (docs/jaryo.md 2.3)
        # is the only one that sets its colours.
        self.assertNotIn('#1e1e1e', out)
        self.assertNotIn('#f2c14e', out)
        self.assertNotRegex(out, r'#home-notice\{(?:[^}]*;)?(?:background|color):#')
        self.assertIn('#home-notice{margin-top:0;border-top:1px solid var(--jaryo-fg,#000);border-left:0;background:var(--jaryo-bg,#fff);color:var(--jaryo-fg,#000);', out)
        # W1-01: the card's words are made once, on the server (opos.practice.home_note), so a practice phone cannot say
        # "Calls and messages are on."; the page only shows what the state carries.
        self.assertIn("var notice=s.home_note||'';", out)
        self.assertNotIn('Calls and messages are on.', out)
        self.assertNotIn('Paused to save power: ', out)

    def test_helper_and_lock(self):
        out = page()
        self.assertRegex(out, r'<label class="wiki-consent"><input id="webq" type="checkbox"><span>')
        self.assertRegex(out, r'\.wiki-consent\{[^}]*gap:12px;align-items:flex-start;min-height:48px')
        # The model actions share one equal-width row (Why? joins Plan after the first render); Reset sits last, under Technical details.
        self.assertIn('.helper-actions{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:8px}', out)
        self.assertRegex(out, r'<div class="helper-actions"><button id="askbtn">Ask</button><button id="planbtn">Plan</button></div><p id="glimmer-credit">[^<]*</p><div id="answer"></div><label class="wiki-consent">')
        self.assertRegex(out, r'<button id="helperrestart" hidden>Restart helper</button><button id="helperreset">Reset</button></div>\n<div id="helper" class="box" hidden></div>')
        self.assertIn('id="lock-ask" type="button" hidden', out)
        self.assertIn('width:min(100%,360px);min-height:56px', out)

    def test_shade_and_status(self):
        out = page()
        self.assertIn('#sheet{top:0;right:0;bottom:0;left:0;height:100%;padding-bottom:calc(16px + env(safe-area-inset-bottom,0px))}', out)
        self.assertIn('<details id="shade-technical"><summary>Technical details</summary>', out)
        self.assertIn('#features:empty', out)
        self.assertIn('scrollbar-width:none', out)
        self.assertIn('percent<=15', out)
        # The low-battery chip is bold with a double border; its colour is the Jaryo ink (jaryo.css), not #ff7075.
        self.assertIn('#sb-batt[data-low="true"]{font-weight:700}', out)
        self.assertRegex(out, r'#sb-batt\[data-low="true"\][^{}]*\{[^}]*color:var\(--jaryo-fg\)')
        self.assertNotIn('#ff7075', out)


class KeyedSyncStatic(unittest.TestCase):
    """W1-09: a node is rebuilt only when its markup changed, so a tap or the focus on it survives a poll."""

    def test_lists_are_rebuilt_only_when_their_markup_changed(self):
        out = page()
        self.assertEqual(out.count("const sync=(el,html,wire)=>{if(el._k!==html){el._k=html;el.innerHTML=html;wire&&wire(el)}};"), 1)
        for el in ("contacts", "threads", "thread", "features", "events"):
            self.assertIn("sync(G('%s')," % el, out, el)
            self.assertNotIn("G('%s').innerHTML" % el, out, el)
            self.assertNotIn("getElementById('%s').innerHTML" % el, out, el)
        # the buttons are wired inside the callback, not by a second pass over the document on every poll
        self.assertNotIn("document.querySelectorAll('#contacts button')", out)
        self.assertNotIn("document.querySelectorAll('#threads button')", out)

    def test_the_helper_box_is_rebuilt_once_per_change_and_enhanced_once(self):
        out = page()
        self.assertIn("const h=G('helper'),hk=JSON.stringify(s.helper);", out)
        self.assertIn("if(h._k!==hk){h._k=hk;h.textContent='Helper", out)
        self.assertIn("else{h.hidden=true;h.textContent='';h._k=''}", out)
        # phone-a.js: an enhanced box holds a details element; its button branch would take Reset or Restart for "Why?"
        self.assertIn("button=helper.querySelector('details')?null:helper.querySelector('button')", out)

    @unittest.skipUnless(shutil.which('node'), 'node missing')
    def test_sync_writes_and_wires_only_on_a_change(self):
        src = (Path(__file__).resolve().parent.parent / 'src/opos/web_static/js-4.js').read_text(encoding='utf-8')
        line = next(l for l in src.splitlines() if l.startswith('const sync='))
        code = line + r"""
const el={writes:0,set innerHTML(v){this.writes++;this.html=v}};let wired=0;
sync(el,'<b>a</b>',()=>wired++);sync(el,'<b>a</b>',()=>wired++);sync(el,'<b>a</b>');
const same=[el.writes,wired];
sync(el,'<b>b</b>',()=>wired++);sync(el,'<b>b</b>',()=>wired++);
sync(el,'',()=>wired++);sync(el,'');
console.log(JSON.stringify({same,after:[el.writes,wired],last:el.html}));
"""
        r = subprocess.run(['node', '-e', code], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out['same'], [1, 1], 'three identical renders: one write, one wiring')
        self.assertEqual(out['after'], [3, 3], 'a changed markup, then an emptied one: one write and one wiring each')
        self.assertEqual(out['last'], '')


CHECK = r"""
CHECKS.polish = async function() {
  await load('home',720,1280,false);
  const out = await ev(`(function(){
    var out=[],home=document.getElementById('home'),n=document.getElementById('home-notice'),c=getComputedStyle(n);
    if(home.getBoundingClientRect().width<innerWidth*.9)out.push('Home narrower than 90%');
    // JRY04: the Jaryo notice may be dark on light, so take the contrast ratio in either direction.
    var a=__u.lum(__u.rgba(c.color))+.05,b=__u.lum(__u.rgba(c.backgroundColor))+.05,ratio=Math.max(a,b)/Math.min(a,b);
    if(ratio<4.5)out.push('notice contrast '+ratio);
    if(!n.textContent.startsWith('Practice mode: calls, texts and tasks are simulated.'))out.push('notice copy: '+n.textContent);
    return out;
  })()`);
  out.push(...await ev(`(async function(){
    var out=[],s=await (await fetch('/api/state',{headers:{'X-OPOS-Token':document.querySelector('[name=opos-token]').content}})).json();s.running=false;
    for(var text of ['Calling 5551234', 'New message: hello']){
      s.in_call=text.indexOf('Calling')===0;s.screen=[text];await render(s);
      var screen=document.getElementById('screen');
      if(!__u.vis(screen)||screen.textContent!==text)out.push('Home activity hidden: '+text);
    }
    return out;
  })()`));
  await ev("oposUI.openApp('helper')");
  await waitFor("!document.getElementById('ask').hidden");
  // UXP31 replaces Ask with Send after the Helper screen builds successfully.
  out.push(...await ev(`[document.getElementById('ask').dataset.hs?'hs-send':'askbtn','planbtn','helperreset'].filter(function(id){var e=document.getElementById(id);return !__u.vis(e)||e.getBoundingClientRect().height<48}).map(id=>id+' too small or hidden')`));
  out.push(...await ev(`(async function(){
    var out=[],s=await (await fetch('/api/state',{headers:{'X-OPOS-Token':document.querySelector('[name=opos-token]').content}})).json();s.running=false;
    await render(s);
    var details=document.querySelector('#helper details');
    if(!details)return ['Helper details missing'];
    if(details.open)out.push('Helper details initially open');
    for(var open of [true,false]){
      document.querySelector('#helper details').open=open;
      await render(s);await render(s);
      if(document.querySelector('#helper details').open!==open)out.push('Helper details state lost: '+open);
    }
    s.battery.why='Saving power for calls and messages';await render(s);
    opos.open();var technical=document.getElementById('shade-technical');technical.open=false;
    var why=document.getElementById('why');
    // Closed native details can retain layout boxes; check actual visibility too.
    if(why.closest('details')!==technical||why.checkVisibility()||why.textContent!==s.battery.why)out.push('Battery explanation leaks outside closed details');
    technical.open=true;
    if(!why.checkVisibility()||!__u.vis(why)||why.textContent!==s.battery.why)out.push('Battery explanation missing from open details');
    technical.open=false;
    return out;
  })()`));
  out.push(...await ev(`(async function(){
    var out=[],s=await (await fetch('/api/state',{headers:{'X-OPOS-Token':document.querySelector('[name=opos-token]').content}})).json();s.running=false;
    await render(s);
    var snap=function(){return {contact:document.querySelector('#contacts button'),why:document.getElementById('whybtn'),details:document.querySelector('#helper details'),feature:document.querySelector('#features>*'),event:document.querySelector('#events>*')}};
    var a=snap();await render(s);await render(s);var b=snap();
    ['contact','why','details'].forEach(function(k){if(!a[k])out.push('missing '+k);else if(a[k]!==b[k])out.push('rebuilt by an unchanged render: '+k)});
    ['feature','event'].forEach(function(k){if(a[k]&&a[k]!==b[k])out.push('rebuilt by an unchanged render: '+k)});
    s.helper=Object.assign({},s.helper,{hours_left:123.4});await render(s);
    if(document.querySelectorAll('#helper details').length!==1)out.push('changed helper: details x'+document.querySelectorAll('#helper details').length);
    if(document.querySelectorAll('#whybtn').length!==1)out.push('changed helper: whybtn x'+document.querySelectorAll('#whybtn').length);
    if(document.querySelectorAll('.helper-actions button').length!==3)out.push('changed helper: actions '+document.querySelectorAll('.helper-actions button').length);
    return out;
  })()`));
  await sleep(350);
  if(!await ev(`(function(){var r=document.getElementById('sheet').getBoundingClientRect();return r.top===0&&r.left===0&&r.right>=innerWidth&&r.bottom>=innerHeight})()`))out.push('shade does not cover viewport');
  await ev('oposUI.lock()');
  if(await ev("__u.vis(document.getElementById('lock-ask'))"))out.push('Helper visible on lock');
  await ev("document.getElementById('unlock').click()");
  if(!await ev("document.getElementById('lock').hidden&&!document.getElementById('phone').inert"))out.push('unlock failed');
  return out;
};
"""


class PolishBrowser(unittest.TestCase):
    skip_reason = None
    mode = 'port'
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown

    def test_portrait(self):
        # Extend a temporary copy so the shared CDP driver remains untouched.
        driver = Path(self.run_dir) / 'polish.js'
        source = acceptance.DRIVER.read_text()
        self.assertIn('(async () => {', source)
        driver.write_text(source.replace('(async () => {', CHECK + '\n(async () => {', 1))
        target = str(self.port) if self.proc is not None else 'pipe'
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile, TMPDIR=self.run_dir)
        result = subprocess.run([self.node, str(driver), target, self.url, 'polish', self.state],
                                capture_output=True, text=True, timeout=40, env=env)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])['violations'], [])
