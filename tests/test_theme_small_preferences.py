# SPDX-License-Identifier: GPL-3.0-or-later
"""Real served preference hooks: supported Small text and durable native theme control."""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from opos.web import page
from tests import test_uxr_acceptance as acceptance
from tests.test_uxp30_budget import rules

STATIC = Path(__file__).resolve().parents[1] / 'src/opos/web_static'

HARNESS = r"""
const fs=require('fs'),vm=require('vm'),root=process.argv[1];
function world(saved={},blocked=false){
 const store={...saved},text={value:'',events:{},addEventListener(k,f){this.events[k]=f}},
  button={textContent:'Theme: System',onclick:null},html={dataset:{}};
 const ctx={location:{search:''},addEventListener(){},document:{readyState:'complete',documentElement:html,
  addEventListener(){},getElementById(id){return id==='textsize'?text:id==='jaryo-theme'?button:null}},
  localStorage:{getItem(k){if(blocked)throw Error('unavailable');return k in store?store[k]:null},
   setItem(k,v){if(blocked)throw Error('unavailable');store[k]=String(v)},
   removeItem(k){if(blocked)throw Error('unavailable');delete store[k]}}};
 ctx.window=ctx;vm.createContext(ctx);
 for(const file of ['phone-b.js','jaryo.js'])vm.runInContext(fs.readFileSync(root+'/'+file,'utf8'),ctx,{filename:file,timeout:1000});
 const state=()=>({theme:html.dataset.theme??null,label:button.textContent,text:html.dataset.text,store:{...store}});
 const prefs=(p={})=>{for(const hook of ctx.oposHooks||[])hook({prefs:{theme:'dark',text_size:'normal',device_type:'auto',...p}});return state()};
 const click=()=>{if(typeof button.onclick!=='function')return {missing_control_handler:true,...state()};button.onclick();return state()};
 return{prefs,click,state,text,store};
}
const out={};
for(const size of ['small','normal','large','xlarge']){let w=world();out[size]=w.prefs({text_size:size})}
let w=world();out.firstBefore=w.state();out.firstSeed=w.prefs();out.clickSystem=w.click();out.clickLight=w.click();out.clickDark=w.click();
out.reloadSystem=(()=>{let x=world();x.prefs();x.click();return world(x.store).prefs()})();
out.savedImmediate=world({jaryo_theme:'light'}).state();out.savedOverrides=world({jaryo_theme:'light',opos_text:'s'}).prefs();
w=world({},true);out.blockedSeed=w.prefs({text_size:'small'});out.blockedSystem=w.click();out.blockedLight=w.click();out.blockedPoll=w.prefs();
w=world();out.auto=w.prefs({theme:'auto'});
w=world({jaryo_theme:'invalid'});out.invalid=w.prefs({theme:'light'});
w=world({opos_text:'s'});out.restoredSmall=w.state();w.text.value='s';w.text.events.change({target:w.text});out.changedSmall=w.state();
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which('node'), 'node unavailable')
class ThemePreferenceRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([shutil.which('node'), '-e', HARNESS, str(STATIC)],
                                text=True, capture_output=True, check=True, timeout=15)
        cls.actual = json.loads(result.stdout)

    def test_small_choice_has_its_own_scale_and_restores(self):
        self.assertEqual([self.actual[x]['text'] for x in ('small','normal','large','xlarge')],
                         ['s','n','l','xl'])
        self.assertEqual(self.actual['restoredSmall']['text'], 's')
        self.assertEqual(self.actual['changedSmall']['store']['opos_text'], 's')

    def test_native_theme_cycles_dark_system_light(self):
        self.assertEqual(self.actual['firstSeed']['theme'], 'dark')
        for name, theme, label in [('clickSystem',None,'Theme: System'),
                                   ('clickLight','light','Theme: Light'),
                                   ('clickDark','dark','Theme: Dark')]:
            self.assertNotIn('missing_control_handler', self.actual[name])
            self.assertEqual((self.actual[name]['theme'], self.actual[name]['label']), (theme,label))

    def test_explicit_system_survives_reload_with_dark_installer_default(self):
        self.assertIsNone(self.actual['reloadSystem']['theme'])
        self.assertEqual(self.actual['reloadSystem']['label'], 'Theme: System')

    def test_existing_saved_theme_applies_immediately_and_beats_defaults(self):
        self.assertEqual(self.actual['savedImmediate']['theme'], 'light')
        self.assertEqual(self.actual['savedOverrides']['theme'], 'light')
        self.assertEqual(self.actual['savedOverrides']['text'], 's')

    def test_blocked_storage_still_allows_in_memory_theme_and_manual_choice(self):
        self.assertEqual(self.actual['blockedSeed']['theme'], 'dark')
        self.assertEqual(self.actual['blockedSeed']['text'], 's')
        self.assertIsNone(self.actual['blockedSystem']['theme'])
        self.assertEqual(self.actual['blockedLight']['theme'], 'light')
        self.assertEqual(self.actual['blockedPoll']['theme'], 'light')

    def test_auto_invalid_storage_and_absent_matchmedia_are_safe(self):
        self.assertIsNone(self.actual['auto']['theme'])
        self.assertNotIn('jaryo_theme', self.actual['auto']['store'])
        self.assertEqual(self.actual['invalid']['theme'], 'light')


class ThemePreferenceMarkupAndCSS(unittest.TestCase):
    def test_visible_native_controls_and_small_label(self):
        rendered = page()
        self.assertIn('<button id="jaryo-theme" type="button">Theme: System</button>', rendered)
        self.assertIn('<option value="s">Small</option>', rendered)
        self.assertEqual(rendered.count('id="jaryo-theme"'), 1)
        css = (STATIC / 'phone-b.css').read_text()
        self.assertIn('html[data-text="s"]{font-size:87.5%}', css)
        self.assertRegex(css, r'#jaryo-theme\{[^}]*min-height:48px')

    def test_palette_tokens_follow_explicit_theme_and_system_media(self):
        sheet = list(rules((STATIC / 'jaryo.css').read_text()))
        for explicit, system_dark, expected in [(None,False,('#1a1a1a','#f3f0e8','light')),
                                                (None,True,('#e6e2d9','#000','dark')),
                                                ('light',True,('#1a1a1a','#f3f0e8','light')),
                                                ('dark',False,('#e6e2d9','#000','dark'))]:
            actual = {}
            for contexts, selector, declarations in sheet:
                if contexts and not (system_dark and all('prefers-color-scheme:dark' in c for c in contexts)):
                    continue
                applies = (selector == ':root' or
                           selector == 'html:not([data-theme])' and explicit is None or
                           selector == f'html[data-theme="{explicit}"]')
                if applies:
                    actual.update(declarations)
            self.assertEqual(tuple(actual.get(k) for k in ('--jaryo-fg','--jaryo-bg','color-scheme')),
                             expected, (explicit,system_dark))

    def test_theme_overrides_legacy_surfaces_controls_focus_and_disabled(self):
        sheet = list(rules((STATIC / 'jaryo.css').read_text()))
        flat = {}
        for contexts, selector, declarations in sheet:
            if not contexts:
                flat.setdefault(selector, {}).update(declarations)
        for selector in ('body','#phone','#lock','#sheet','#sb','.box','#screen'):
            self.assertEqual(flat[selector]['background'], 'var(--jaryo-bg)', selector)
            self.assertEqual(flat[selector]['color'], 'var(--jaryo-fg)', selector)
        for selector in ('#textsize','#goal-clear','#goal-row input[type=time]','#askbtn'):
            self.assertEqual(flat[selector]['background'], 'var(--jaryo-bg)', selector)
            self.assertEqual(flat[selector]['color'], 'var(--jaryo-fg)', selector)
        self.assertEqual(flat['input[type=text]']['color-scheme'], 'inherit')
        self.assertEqual(flat['input[type=time]']['color-scheme'], 'inherit')
        self.assertEqual(flat['#textsize:focus-visible']['outline'], '3px solid var(--jaryo-fg)')
        self.assertEqual(flat['button:disabled']['border-style'], 'dashed')
        self.assertEqual(flat['#meter[data-tone]']['animation'], 'none')
        self.assertEqual(flat['#history polyline']['stroke'], 'var(--jaryo-fg)')
        self.assertNotRegex((STATIC / 'jaryo.css').read_text(), r'@font-face|url\(')


BROWSER_CHECK = r"""
CHECKS.theme_small = async function() {
  const out=[];
  await load('home',390,844,false);
  await ev("api({action:'visible',visible:true}).then(render)");
  await ev("oposUI.openApp('settings')");
  await sleep(300);
  out.push(...await ev(`(function(){
    const e=[],d=document.documentElement,t=document.getElementById('textsize'),b=document.getElementById('jaryo-theme');
    if(d.dataset.text!=='s'||t.value!=='s'||parseFloat(getComputedStyle(d).fontSize)!==14)e.push('Small is not distinct14px at87.5percent');
    for(const x of [t,b]){const r=x.getBoundingClientRect();if(r.width<48||r.height<48)e.push(x.id+' target below48px')}
    if(b.textContent!=='Theme: Dark'||d.dataset.theme!=='dark')e.push('installerDark not applied');
    b.click();if(b.textContent!=='Theme: System'||d.hasAttribute('data-theme'))e.push('Dark did not cycle toSystem');
    return e;
  })()`));
  for(const dark of [true,false]){
    await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:dark?'dark':'light'}]});
    await sleep(50);
    const expected=dark?'rgb(0, 0, 0)':'rgb(243, 240, 232)';
    const actual=await ev("getComputedStyle(document.body).backgroundColor");
    if(actual!==expected)out.push('System failed liveOSchange '+actual+' expected'+expected);
  }
  await load('home',390,844,false);
  out.push(...await ev(`(function(){const d=document.documentElement,b=document.getElementById('jaryo-theme');
    return b.textContent==='Theme: System'&&!d.hasAttribute('data-theme')&&d.dataset.text==='s'
      ?[]:['System/Small did not persist across reload against installerDark'];})()`));
  for(const width of [390,1280]){
    await resize(width,width===390?844:720);
    for(const mode of ['light','dark','system']){
      await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:mode==='light'?'dark':'light'}]});
      const dark=mode==='dark',fg=dark?'rgb(230, 226, 217)':'rgb(26, 26, 26)',bg=dark?'rgb(0, 0, 0)':'rgb(243, 240, 232)';
      await ev(`(function(){const b=document.getElementById('jaryo-theme'),want='Theme: ${mode[0].toUpperCase()+mode.slice(1)}';
        for(let i=0;i<3&&b.textContent!==want;i++)b.click();})()`);
      await ev("api({action:'visible',visible:true}).then(render)");
      for(const view of ['home','settings','helper','lock','shade']){
        await ev(`opos.close();oposUI.unlock();oposUI.openApp('${view==='lock'||view==='shade'?'home':view}');`+
                 (view==='lock'?'oposUI.lock();':view==='shade'?'opos.open();':''));
        await sleep(300);
        const failures=await ev(`(function(){
          const out=[],wantFg=${JSON.stringify(fg)},wantBg=${JSON.stringify(bg)},view=${JSON.stringify(view)};
          const surfaces={home:['#phone','#home-notice'],settings:['#settings'],helper:['#helper','#ask'],lock:['#lock'],shade:['#sheet']};
          const controls={home:['#home button'],settings:['#jaryo-theme','#textsize'],helper:['#q','#hs-send','#hs-cancel'],lock:['#unlock'],shade:['#goal','#goal-clear']};
          for(const selector of surfaces[view]){const e=document.querySelector(selector);if(!e||!__u.vis(e)){out.push('missingvisible '+selector);continue}
            const c=getComputedStyle(e);if(c.color!==wantFg||c.backgroundColor!==wantBg)out.push(selector+' palette '+c.color+'/'+c.backgroundColor)}
          for(const selector of controls[view]){const e=document.querySelector(selector);if(!e||!__u.vis(e)){out.push('missingcontrol '+selector);continue}
            const c=getComputedStyle(e);if(c.color!==wantFg||c.backgroundColor!==wantBg)out.push(selector+' controlpalette '+c.color+'/'+c.backgroundColor);
            if(c.colorScheme!==${JSON.stringify(dark?'dark':'light')})out.push(selector+' nativecolor-scheme '+c.colorScheme);
            const r=e.getBoundingClientRect();if(r.width<48||r.height<48)out.push(selector+' smalltarget '+r.width+'x'+r.height)}
          const seen=new Set(),walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
          while(walker.nextNode()){const n=walker.currentNode,e=n.parentElement;if(!e||seen.has(e)||!n.nodeValue.trim()||/^(SCRIPT|STYLE|TITLE)$/.test(e.tagName)||!__u.vis(e))continue;seen.add(e);
            const c=getComputedStyle(e),fg=__u.rgba(c.color);if(!fg)continue;let opacity=1,layers=[];
            for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);opacity*=parseFloat(s.opacity);const b=__u.rgba(s.backgroundColor);if(b&&b[3]>0)layers.push(b)}
            let background=[255,255,255,1];while(layers.length)background=__u.over(layers.pop(),background);
            const ink=__u.over([fg[0],fg[1],fg[2],fg[3]*opacity],background),a=__u.lum(ink)+.05,b=__u.lum(background)+.05;
            if(Math.max(a,b)/Math.min(a,b)<4.5)out.push(__u.sel(e)+' contrast below4.5')}
          return out;
        })()`);
        out.push(...failures.map(x=>width+' '+mode+' '+view+' '+x));
      }
      await ev("opos.close();oposUI.unlock();oposUI.openApp('settings')");
      await sleep(300);
      await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Shift',code:'ShiftLeft',windowsVirtualKeyCode:16});
      await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Shift',code:'ShiftLeft',windowsVirtualKeyCode:16});
      out.push(...await ev(`(function(){const out=[];for(const id of ['jaryo-theme','textsize']){const e=document.getElementById(id);e.blur();e.focus();const c=getComputedStyle(e);
        if(!e.matches(':focus-visible')||c.outlineStyle==='none'||parseFloat(c.outlineWidth)<3||c.outlineColor!==${JSON.stringify(fg)})out.push(id+' nativefocusring missing/wrong')}
        return out;})()`));
    }
  }
  return out;
};
"""


class ThemePreferenceBrowser(unittest.TestCase):
    skip_reason = None
    mode = 'port'
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown

    @classmethod
    def setUpClass(cls):
        acceptance.TestUxrAcceptance.setUpClass.__func__(cls)
        if cls.skip_reason:
            return
        state = json.loads(Path(cls.state).read_text())
        for name in ('hidden','visible'):
            state[name]['prefs'].update(theme='dark', text_size='small')
        Path(cls.state).write_text(json.dumps(state))

    def test_computed_themes_system_reload_small_targets_and_focus(self):
        driver = Path(self.run_dir) / 'theme-small.js'
        source = acceptance.DRIVER.read_text()
        self.assertIn('(async () => {', source)
        driver.write_text(source.replace('(async () => {', BROWSER_CHECK + '\n(async () => {', 1))
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile,
                   TMPDIR=self.run_dir)
        target = str(self.port) if self.proc is not None else 'pipe'
        result = subprocess.run([self.node, str(driver), target, self.url, 'theme_small', self.state],
                                check=False, text=True, capture_output=True, timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])['violations'], [])
