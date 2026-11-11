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
        self.assertIn('low=p<=15', out)  # the Shade title's LOW threshold (the bar's chip is pinned by test_uxp01_statusbar)
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


# Test-owned tracking is inserted before the page schedules its first automatic poll.
POLL_ENTRY = "\narmPoll(pollMs);\nfunction fetchState()"
POLL_TRACKER = r"""
(function(){
  var original=pollTick,tracked=window.__uxp23Polls={active:new Set(),calls:0};
  pollTick=function(){
    tracked.calls++;
    var result=original.apply(this,arguments);tracked.active.add(result);
    return Promise.resolve(result).finally(function(){tracked.active.delete(result)});
  };
})();
"""


def instrument_idle_polls(html):
    if html.count(POLL_ENTRY) != 1:
        raise ValueError("expected exactly one initial poll entrypoint")
    return html.replace(POLL_ENTRY, "\n" + POLL_TRACKER + POLL_ENTRY, 1)


IDLE_POLL_CHECK = r"""
(async function(){
  var out=[],records=[],inputs=[],mo=null,savedArm=armPoll,savedFetch=fetchState;
  var tracked=window.__uxp23Polls,sheet=document.getElementById('sheet');
  if(!tracked||!sheet)return ['idle fixture is missing poll tracking or Shade'];
  function describe(r){var e=r.target.nodeType===1?r.target:r.target.parentElement;return {type:r.type,target:e&&(e.id?'#'+e.id:e.tagName)||'document',attribute:r.attributeName||''}}
  function busy(){return {polls:tracked.active.size,shade:sheet.style.willChange,animations:document.getAnimations().filter(function(a){return a.playState==='running'||a.pending}).length}}
  async function settle(){
    var end=performance.now()+2000,quiet=0;
    while(true){var b=busy();
      if(!b.polls&&b.shade===''&&!b.animations){if(quiet>=2)return;quiet++}else quiet=0;
      if(performance.now()>=end)throw new Error('idle readiness timeout '+JSON.stringify(b));
      // jaryo.js onfinish/oncancel cleanup can follow the animation's finished state.
      // Two rendering frames plus their microtasks drain that queued cleanup before observation.
      await new Promise(function(resolve,reject){
        var timer=setTimeout(function(){reject(new Error('idle readiness timeout '+JSON.stringify(busy())))},Math.max(0,end-performance.now()));
        requestAnimationFrame(function(){Promise.resolve().then(function(){clearTimeout(timer);resolve()})});
      });
    }
  }
  try{
    armPoll=function(){};clearTimeout(pollTimer);pollTimer=null;
    // phone-a.js clears Shade will-change at transitionend or its 400ms fallback.
    // Wait for those earlier actions and already tracked polls, not for the assertion to pass.
    await settle();
    fetchState=async function(){var state=await savedFetch();inputs.push({key:stateKey(state),state:JSON.parse(JSON.stringify(state)),online:navigator.onLine});return state};
    await pollTick();await settle();
    var baseline=inputs[inputs.length-1];inputs=[];
    if(!baseline)throw new Error('warm-up poll did not read state');
    var startCalls=tracked.calls;
    mo=new MutationObserver(function(list){records.push.apply(records,list.map(describe))});
    mo.observe(document,{subtree:true,childList:true,attributes:true,characterData:true});
    await pollTick();await pollTick();await pollTick();
    records.push.apply(records,mo.takeRecords().map(describe));mo.disconnect();
    if(tracked.calls-startCalls!==3||inputs.length!==3)out.push('idle measured poll count '+(tracked.calls-startCalls)+' / state reads '+inputs.length);
    inputs.forEach(function(input,i){if(input.key!==baseline.key){var keys=Array.from(new Set(Object.keys(baseline.state).concat(Object.keys(input.state))));var changed=keys.filter(function(k){return JSON.stringify(baseline.state[k])!==JSON.stringify(input.state[k])});if(input.online!==baseline.online)changed.push('navigator.onLine');out.push('idle input key changed at poll '+(i+1)+': '+(changed.join(',')||'serialized key order'))}});
    if(records.length)out.push('three polls of an unchanged state wrote '+records.length+' DOM records; targets '+JSON.stringify(records.slice(0,12)));
  }catch(error){out.push('idle precondition failed: '+error.message)}
  finally{
    if(mo)mo.disconnect();fetchState=savedFetch;armPoll=savedArm;
    clearTimeout(pollTimer);savedArm(pollMs);
  }
  return out;
})()
"""


class IdlePollFixtures(unittest.TestCase):
    def test_instrumentation_has_one_entry_and_preserves_original_poller(self):
        raw = page(demo=True)
        altered = instrument_idle_polls(raw)
        self.assertEqual(altered.replace("\n" + POLL_TRACKER, "", 1), raw)
        poller = (Path(__file__).resolve().parents[1] / 'src/opos/web_static/js-8.js').read_text()
        body = poller[poller.index('async function pollTick()'):poller.index('\narmPoll(pollMs);')]
        self.assertEqual(altered.count(body), 1)
        for invalid in (raw.replace(POLL_ENTRY, ''), raw + POLL_ENTRY):
            with self.assertRaises(ValueError):
                instrument_idle_polls(invalid)

    @unittest.skipUnless(shutil.which('node'), 'node missing')
    def test_actual_poll_and_shade_lifecycle_positive_and_negative_fixtures(self):
        root = Path(__file__).resolve().parents[1]
        poller = (root / 'src/opos/web_static/js-8.js').read_text()
        poller = poller[poller.index('let pollMs='):poller.index('fetchState().then')]
        shade = (root / 'src/opos/web_static/phone-a.js').read_text()
        start = shade.index('try{(function(){')
        shade = shade[start:shade.index('})()}catch(e){}', start) + len('})()}catch(e){}')]
        payload = {'poller': instrument_idle_polls(poller), 'shade': shade, 'idle': IDLE_POLL_CHECK}
        result = subprocess.run([shutil.which('node'), str(root / 'tests/polish_idle_test.js')], input=json.dumps(payload), text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('actual poller and Shade fixtures passed', result.stdout)


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
  out.push(...await ev(`__IDLE_POLL_CHECK__`));
  await sleep(350);
  if(!await ev(`(function(){var r=document.getElementById('sheet').getBoundingClientRect();return r.top===0&&r.left===0&&r.right>=innerWidth&&r.bottom>=innerHeight})()`))out.push('shade does not cover viewport');
  await ev('oposUI.lock()');
  if(await ev("__u.vis(document.getElementById('lock-ask'))"))out.push('Helper visible on lock');
  await ev("document.getElementById('unlock').click()");
  if(!await ev("document.getElementById('lock').hidden&&!document.getElementById('phone').inert"))out.push('unlock failed');
  return out;
};
"""


CHECK = CHECK.replace('__IDLE_POLL_CHECK__', IDLE_POLL_CHECK)


class PolishBrowser(unittest.TestCase):
    skip_reason = None
    mode = 'port'
    @classmethod
    def setUpClass(cls):
        acceptance.TestUxrAcceptance.setUpClass.__func__(cls)
        if cls.skip_reason:
            return
        html = Path(cls.tmp) / 'page.html'
        html.write_text(instrument_idle_polls(html.read_text()), encoding='utf-8')
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
