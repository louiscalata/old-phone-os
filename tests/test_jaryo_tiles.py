# SPDX-License-Identifier: GPL-3.0-or-later
"""JRY04: the home front page.

The four-tile launcher is a newspaper column. The buttons stay in the same
order, each full width and at least 48px, with a one-line deck. data-size
remains on the markup. A wide window is still one column; the Duo span puts
the second column in the other pane (panes.css).
"""
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

from opos.web import page
from tests import test_uxp12_home as uxp12
from tests import test_uxr_acceptance as acceptance

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"


def _jaryo_css():
    return (STATIC / "jaryo.css").read_text(encoding="utf-8")


class TilesStatic(unittest.TestCase):
    def setUp(self):
        self.out = page("t" * 32, "n")
        self.nav = re.search(r'<nav id="home"[^>]*>(.*?)</nav>', self.out).group(1)

    def test_sizes_on_tiles(self):
        sizes = {}
        for tag in re.findall(r"<button[^>]*>", self.nav):
            name = re.search(r'data-open="(\w+)"|id="(\w+)"', tag).group(1, 2)
            size = re.search(r'data-size="(\w+)"', tag)
            if size:
                sizes[name[0] or name[1]] = size.group(1)
        self.assertEqual(sizes, {"phone": "medium", "messages": "medium", "helper": "small", "allapps": "wide"})
        self.assertEqual(set(re.findall(r'data-size="(\w+)"', self.nav)), {"small", "medium", "wide"})
        self.assertIn('<button type="button" data-open="settings">Settings</button>', self.nav)

    def test_order_and_ids_unchanged(self):
        self.assertEqual(re.findall(r"<button[^>]*>([^<]+)</button>", self.nav), uxp12.FAVS)
        self.assertEqual(re.findall(r'data-open="(\w+)"', self.nav), ["phone", "messages", "helper", "settings"])

    def test_tile_css(self):
        css = _jaryo_css().replace(" ", "")
        base = re.search(r"#homebutton\{([^}]*)\}", css).group(1)
        for d in ("display:block", "width:100%", "min-height:48px", "text-align:left",
                  "overflow-wrap:anywhere", "background:var(--jaryo-bg,#fff)", "border-radius:0", "box-shadow:none"):
            self.assertIn(d, base)
        self.assertNotRegex(base, r"(^|;)height:")
        self.assertNotIn("min-height:144px", css)
        self.assertNotIn("grid-template-columns:repeat(4", css)
        self.assertIn("#in-brief{", css)
        self.assertIn(".wordmark{", css)
        top = css.split("@media", 1)[0]
        for d in ('#phone[data-app="home"]#intro{display:none}', "#screen[hidden]{display:none}"):
            self.assertIn(d, top)
        notice = re.search(r"#home-notice\{([^}]*)\}", top).group(1)
        for d in ("margin-top:0", "border-top:1pxsolidvar(--jaryo-fg,#000)", "border-left:0",
                  "background:var(--jaryo-bg,#fff)", "color:var(--jaryo-fg,#000)"):
            self.assertIn(d, notice)
        self.assertIn('@medianotalland(min-width:840px){#phone:not([data-app="home"])#screen{margin-top:12px}}', css)
        self.assertNotIn("max-width:839px", css)

    def _assert_palette_and_no_reordering(self, source):
        css = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)) - {"#000", "#fff", "#1a1a1a", "#f3f0e8", "#e6e2d9"}, set())
        self.assertNotRegex(css, r"[;{]\s*order\s*:|row-reverse|column-reverse|@font-face|url\(|gradient")
        # Section2.3 requires neutralizing inherited paint effects. Permit only
        # exact neutral declarations; retain the effect ban on everything else,
        # including earlier declarations hidden by a later neutral reset.
        neutral = re.sub(r"([;{])\s*(?:filter\s*:\s*none|opacity\s*:\s*1)\s*"
                         r"(?:!important\s*)?(?=[;}])", r"\1", css, flags=re.IGNORECASE)
        self.assertNotRegex(neutral, re.compile(r"filter|opacity", re.IGNORECASE))

    def test_palette_and_no_reordering(self):
        self._assert_palette_and_no_reordering(_jaryo_css())

    def test_effect_guard_accepts_exact_neutral_resets(self):
        self._assert_palette_and_no_reordering(
            '#screen{filter:none!important}#why{opacity:1}'
            '#events{filter: none; opacity: 1 !important;}')

    def test_effect_guard_still_rejects_active_effects_and_forbidden_features(self):
        for declaration in ('filter:blur(1px)', 'filter:blur(0)', 'filter:brightness(1)', 'filter:var(--effect)',
                            'filter:none-blur', 'filter:none blur(0)', 'opacity:1 invalid',
                            'FILTER:blur(1px)', 'opacity:.5', 'opacity:.99', 'opacity:1.01', 'opacity:0', 'opacity:var(--alpha)',
                            'filter:blur(1px);filter:none', 'opacity:.5;opacity:1',
                            'backdrop-filter:none', 'color:#333', 'order:1',
                            'flex-direction:row-reverse', 'flex-direction:column-reverse',
                            'background:url(x)', 'background:linear-gradient(#000,#fff)'):
            with self.subTest(declaration=declaration), self.assertRaises(AssertionError):
                self._assert_palette_and_no_reordering(_jaryo_css() + '#home button{' + declaration + '}')
        with self.assertRaises(AssertionError):
            self._assert_palette_and_no_reordering(_jaryo_css() + '@font-face{font-family:downloaded}')

    def test_screen_hook(self):
        js = (STATIC / "phone-a.js").read_text(encoding="utf-8")
        self.assertIn("sc.hidden=!(s.in_call||(f&&!/^Calls$/.test(f)))}else if(sc)sc.hidden=false", js)
        # Navigation (a data-app change) re-checks #screen without waiting for the next state render.
        self.assertIn(".observe($('phone'),{attributes:true,attributeFilter:['data-app']})", js)
        self.assertIn("jl=s;jsc();", js)

    def test_in_brief_counts_threads(self):
        js = (STATIC / "phone-a.js").read_text(encoding="utf-8")
        self.assertIn("var brief=$('in-brief'),n=(s.threads||[]).length;if(brief)brief.textContent='In brief · '+n+' threads · Clock · No calendar';", js)
        self.assertNotIn("n+' messages · Clock", js)


@unittest.skipUnless(uxp12.NODE, "node not installed")
class TilesRuntime(unittest.TestCase):
    """The UXP12 vm harness: tile sizes must not change Home or All apps order."""
    run_js = uxp12.UXP12Runtime.run_js

    def test_home_order_unchanged(self):
        o = self.run_js()
        self.assertEqual(o["list"], "|".join(uxp12.LIST))
        self.assertEqual(o["start"], ["home", True, False, False, False, None])


CHECK = r"""
CHECKS.jaryo_tiles = async function() {
  await load('home',360,640,false);
  const out = await ev(`(async function(){
    var out=[],s=await (await fetch('/api/state',{headers:{'X-OPOS-Token':document.querySelector('[name=opos-token]').content}})).json();s.running=false;await render(s);
    var $=function(q){return document.querySelector(q)},hw=document.getElementById('home').getBoundingClientRect().width;
    var w=function(q){return $(q).getBoundingClientRect().width},h=function(q){return $(q).getBoundingClientRect().height};
    if(w('#allapps')<.9*hw)out.push('All apps not full width');
    ['phone','messages','helper','settings'].forEach(function(n){var b=$('#home [data-open='+n+']');if(w('#home [data-open='+n+']')<.9*hw)out.push(n+' headline not full width');if(h('#home [data-open='+n+']')<48)out.push(n+' below 48')});
    if(!document.getElementById('in-brief')||document.getElementById('in-brief').textContent.indexOf('In brief')<0)out.push('in brief missing');
    if(!document.getElementById('mast-time'))out.push('masthead time missing');
    if((document.querySelector('#jaryo-masthead .wordmark')||{}).textContent!=='OPOS')out.push('wordmark');
    // Allowed (text, background) pairs: each theme's own two tokens, either way round; never mixed across themes.
    var D=['rgb(230, 226, 217)','rgb(0, 0, 0)'],L=['rgb(26, 26, 26)','rgb(243, 240, 232)'];
    var PAIRS=[D.join('/'),D.slice().reverse().join('/'),L.join('/'),L.slice().reverse().join('/')];
    [].slice.call(document.querySelectorAll('#home button')).concat([$('#home-notice')]).forEach(function(e){
      var c=getComputedStyle(e),id=e.id||e.textContent;
      if(PAIRS.indexOf(c.color+'/'+c.backgroundColor)<0)out.push('palette '+id+' '+c.color+' on '+c.backgroundColor);
      ['Top','Right','Bottom','Left'].forEach(function(k){if(parseFloat(c['border'+k+'Width'])>0&&c['border'+k+'Color']!==c.color)out.push('border '+k+' '+id+' '+c['border'+k+'Color'])});
      if(c.backgroundImage!=='none'||c.boxShadow!=='none'||c.filter!=='none')out.push('paint effect '+id);
    });
    var nc=getComputedStyle($('#home-notice'));
    if(nc.borderLeftWidth!=='0px'||nc.borderTopWidth!=='1px')out.push('notice rules '+nc.borderLeftWidth+'/'+nc.borderTopWidth);
    document.querySelectorAll('#home button').forEach(function(b){if(getComputedStyle(b).textAlign!=='left')out.push('tile not left: '+b.textContent)});
    var hits=[].filter.call(document.querySelectorAll('body *'),function(e){return __u.vis(e)&&e.textContent.indexOf('Practice mode')>=0&&![].some.call(e.children,function(c){return c.textContent.indexOf('Practice mode')>=0})});
    if(hits.length!==1)out.push('Practice mode note shown '+hits.length+' times');
    if(__u.vis(document.getElementById('intro')))out.push('intro visible on home');
    var sc=document.getElementById('screen'),tick=function(){return new Promise(function(r){requestAnimationFrame(function(){requestAnimationFrame(r)})})};
    if(__u.vis(sc))out.push('screen visible with default state');
    var nb=document.getElementById('home-notice').getBoundingClientRect(),fb=document.getElementById('home-favourites').getBoundingClientRect();
    if(nb.top-fb.bottom>24)out.push('void above notice '+Math.round(nb.top-fb.bottom));
    // Home -> Phone -> Home with no state render in between (ticking is stopped).
    // Phone is the contacts app: the hook clears the hidden attribute on navigation, while the Jaryo app rule
    // (phone-b.css) keeps the Home status box off every app screen and Contacts lead instead.
    oposUI.openApp('phone');await tick();
    if(sc.hidden)out.push('screen hidden attribute kept in Phone without a render');
    if(__u.vis(sc))out.push('status box shown in Phone');
    if(!__u.vis(document.getElementById('contacts')))out.push('contacts hidden in Phone');
    oposUI.openApp('home');await tick();
    if(__u.vis(sc))out.push('screen visible back on Home without a render');
    s.screen=['Calling 5551234'];s.in_call=true;await render(s);
    if(!__u.vis(sc)||sc.textContent!=='Calling 5551234')out.push('call activity hidden');
    s.screen=['Calls','Messages'];s.in_call=false;await render(s);
    if(__u.vis(sc))out.push('screen visible after call ended');
    var tiles=[].slice.call(document.querySelectorAll('#home button')),total=function(){return tiles.reduce(function(a,b){return a+b.getBoundingClientRect().height},0)};
    var before=total(),fs=parseFloat(getComputedStyle(tiles[0]).fontSize);
    document.documentElement.style.fontSize='200%';
    await tick();
    if(parseFloat(getComputedStyle(tiles[0]).fontSize)<fs*1.9)out.push('tile text not doubled');
    tiles.forEach(function(b){if(b.scrollWidth>b.clientWidth+1||b.scrollHeight>b.clientHeight+1)out.push('tile clips at 200%: '+b.textContent)});
    if(total()<=before)out.push('tiles did not grow at 200%');
    if(document.documentElement.scrollWidth>innerWidth+1)out.push('horizontal scroll at 200%');
    document.documentElement.style.fontSize='';
    return out;
  })()`);
  // A wide window stays one column of headlines. The Duo span, not this width, makes the second column.
  for (const [w,h,ff] of [[1280,720,''],[1280,720,'tablet'],[1280,720,'foldable']]) {
    await load('home',w,h,true);
    out.push(...await ev(`(function(){
      if(${JSON.stringify(ff)})document.documentElement.dataset.ff=${JSON.stringify(ff)};
      var out=[],tag='${w}x${h} ${ff||'auto'} ',home=document.getElementById('home'),hr=home.getBoundingClientRect(),lefts={};
      var tiles=[].slice.call(document.querySelectorAll('#home [data-open]'));
      tiles.forEach(function(b){var r=b.getBoundingClientRect();lefts[Math.round(r.left)]=1;if(r.width<hr.width*.9)out.push(tag+b.textContent+' not full width')});
      if(Object.keys(lefts).length!==1)out.push(tag+'headline columns '+Object.keys(lefts).length);
      if(__u.vis(document.getElementById('intro')))out.push(tag+'intro visible on home');
      if(${JSON.stringify(ff)}==='foldable'&&hr.left<innerWidth/2&&hr.right>innerWidth/2+1)out.push(tag+'home crosses the hinge');
      return out;
    })()`));
  }
  return out;
};
"""


class TilesBrowser(unittest.TestCase):
    skip_reason = None
    mode = "port"
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown
    _run = acceptance.TestUxrAcceptance._run

    def test_tiles(self):
        driver = Path(self.run_dir) / "tiles.js"
        source = acceptance.DRIVER.read_text()
        self.assertIn("(async () => {", source)
        driver.write_text(source.replace("(async () => {", CHECK + "\n(async () => {", 1))
        target = str(self.port) if self.proc is not None else "pipe"
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile, TMPDIR=self.run_dir)
        r = subprocess.run([self.node, str(driver), target, self.url, "jaryo_tiles", self.state],
                           check=False, capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["violations"], [])

    def test_touch_targets(self):
        self.assertEqual(self._run("touch_targets"), [])

    def test_text_200(self):
        self.assertEqual(self._run("text_200"), [])


if __name__ == "__main__":
    unittest.main()
