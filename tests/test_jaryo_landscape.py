# SPDX-License-Identifier: GPL-3.0-or-later
"""JRY30: landscape layout (docs/jaryo.md 4.1, 4.3, 4.6, section 13).

Rules live only in jaryo-layout.css. Lock in landscape at >= 840px is two columns (text left, buttons right);
the shade at >= 840px is a 24rem sidebar from the left edge with the page visible beside it. Slab keeps its
phone column, the foldable its UXP21 grid. The one rule outside the >=840px blocks drops the base button
margin-right:8px on #lock>button: centred by the lock's flex column, the 368px margin box of a 360px-wide
button sat at -4px to 356px in a 360px viewport (round-2 gate). On the foldable with All apps open the JRY08
letter-jump row spans both panes above the two-pane list, its buttons in the left pane (UXP21 reading order and
hinge rules). The module also pins the three base-suite repairs that landed with it (UXP12 show() skipping JRY08
letter headings; the data-size and helperCall markers).
"""
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from opos.web import page
from tests import test_uxp12_home as uxp12
from tests import test_uxr_acceptance as acceptance
from tests.test_uxp21_profiles import blocks
from tests.test_uxp30_budget import PAGE_BUDGET, rules

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"
LOCK = "@media (orientation:landscape) and (min-width:840px){"
WIDE = "@media (min-width:840px){"
NOT_FOLD = 'html:not([data-ff="slab"]):not([data-ff="foldable"])'
LOCK_MARGIN = "#lock>button{margin-right:0}"


def _css():
    return (STATIC / "jaryo-layout.css").read_text(encoding="utf-8")


def _strip(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


class LandscapeStatic(unittest.TestCase):
    def test_inlined_after_jaryo_css_and_within_budget(self):
        out = page("t" * 32, "n")
        style = out[:out.index("</style>")]
        self.assertIn(_css(), style)
        self.assertLess(style.index((STATIC / "jaryo.css").read_text(encoding="utf-8")), style.index(_css()))
        self.assertLessEqual(len(out.encode("utf-8")), PAGE_BUDGET)

    def test_two_media_blocks_and_one_portrait_rule(self):
        text = _strip(_css())
        lock, wide = blocks(text, LOCK), blocks(text, WIDE)
        self.assertEqual(len(lock), 1)
        self.assertEqual(len(wide), 1)
        # The file is shared with JRY08: its letter-row rules sit outside the two >=840px blocks and in the viewport-segments
        # block. The landscape layer itself adds exactly one rule outside the blocks (the lock button margin) and no third
        # media query; the one portrait-sized rule is unconditional.
        segments = "@media (horizontal-viewport-segments:2){"
        seg = blocks(text, segments)
        self.assertEqual(len(seg), 1)
        self.assertEqual(sorted(re.findall(r"@media[^{]*\{", text)), sorted([LOCK, WIDE, segments]))
        rest = text.replace(LOCK + lock[0] + "}", "").replace(WIDE + wide[0] + "}", "").replace(segments + seg[0] + "}", "")
        outside = [r.strip() for r in re.findall(r"[^{}]+\{[^{}]*\}", rest)]
        self.assertEqual(re.sub(r"[^{}]+\{[^{}]*\}", "", rest).strip(), "", "only whole rules outside the media blocks")
        self.assertEqual(outside.count(LOCK_MARGIN), 1)
        for rule in outside:
            if rule != LOCK_MARGIN:
                self.assertIn("#jaryo-letters", rule[:rule.index("{")], "JRY08's letter row owns every other rule outside the blocks: " + rule)
        self.assertEqual(len([r for r in outside if "#lock" in r]), 1, "the lock button margin is the only lock rule outside the two >=840px media blocks")
        self.assertLess(text.index(LOCK_MARGIN), text.index(LOCK))
        self.assertNotIn("max-width:839px", text)
        self.assertNotIn("orientation:portrait", text)

    def test_lock_buttons_fit_the_lock_width(self):
        # Round-2 gate at 360x640: both lock buttons measured at x=-4..356 inside a correctly sized 360x640 viewport. The
        # base rule below gives every button margin-right:8px, UXP23 makes #lock buttons width:min(100%,360px), and the
        # lock's flex column centres the 368px margin box, so 4px fell off both edges. The correction only zeroes the
        # right margin; the base rule itself (test_ux_u007_touch_css) and the 4px vertical margins are unchanged.
        out = page("t" * 32, "n")
        self.assertIn("button{min-height:44px;margin-right:8px}", out)
        self.assertIn("label,input,button{font-size:1rem;margin:4px 0}", out)
        self.assertIn("#lock button{width:min(100%,360px);min-height:56px}", out)
        self.assertIn(LOCK_MARGIN, out)
        self.assertLess(out.index("button{min-height:44px;margin-right:8px}"), out.index(LOCK_MARGIN))
        # Every lock button is a direct child of #lock, so the child selector reaches all of them.
        html = (STATIC / "phone-a.html").read_text(encoding="utf-8")
        start = html.index('<div id="lock"')
        lock = html[start:html.index("</div>", html.index('id="lock-hangup"', start))]
        self.assertEqual(lock.count("<button"), 4)
        self.assertEqual(lock.count("</button><button") + lock.count("</div><button"), 4)
        self.assertEqual(lock.count("<div"), 6, "#lock and its five text rows; no wrapper around the buttons")

    def test_lock_two_columns(self):
        body = blocks(_strip(_css()), LOCK)[0]
        self.assertIn(NOT_FOLD + " #lock:not([hidden]){display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:24px;grid-auto-flow:row dense;", body)
        self.assertIn(NOT_FOLD + " #lock>*{grid-column:1}", body)
        self.assertIn(NOT_FOLD + " #lock>div{text-align:left}", body)
        self.assertIn(NOT_FOLD + " #lock>#lock-notice," + NOT_FOLD + " #lock>button{grid-column:2}", body)
        self.assertIn(NOT_FOLD + " #lock>#lock-date{justify-self:stretch;border-bottom:3px solid currentColor;", body)
        # Safe areas: the landscape lock pads by the insets like UXP06's #lock rule.
        self.assertIn("padding-left:max(24px,env(safe-area-inset-left,0px));padding-right:max(24px,env(safe-area-inset-right,0px))", body)
        for sel in re.findall(r"([^{}]+)\{", body):
            for s in sel.split(","):
                self.assertTrue(s.strip().startswith(NOT_FOLD + " #lock"), s)

    def test_shade_sidebar(self):
        body = blocks(_strip(_css()), WIDE)[0]
        self.assertIn("#sheet{right:auto;width:min(100%,24rem);border-right:1px solid currentColor;transform:translateX(-100%)}", body)
        self.assertIn(".sheet-open #sheet{transform:translateX(0)}", body)
        self.assertIn(".sheet-open #scrim{opacity:0}", body)
        # The base #sheet keeps its visibility delay (test_uxp30_budget); the sidebar adds no transition of its own.
        rendered = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", page(), re.S))
        base, mine = {}, []
        for context, selector, declarations in rules(rendered):
            if not context and selector == "#sheet":
                base.update(declarations)
            if context == (WIDE[:-1],) and selector in ("#sheet", ".sheet-open #sheet", ".sheet-open #scrim"):
                mine.append(declarations)
        self.assertRegex(base.get("transition", ""), r"\bvisibility\s+0s\s+\.25s\b")
        self.assertEqual(len(mine), 3)
        for d in mine:
            for k in ("transition", "transition-property", "animation", "will-change"):
                self.assertNotIn(k, d)

    def test_foldable_letter_row_spans_both_panes(self):
        # UXP21 fold_allapps: #jaryo-letters precedes the full-width #apps-list in the DOM, so with All apps open it is a
        # full-width row as well; the right pane plus half the 24px gutter is padding, so its buttons never reach the hinge.
        css = _strip(_css())
        body = blocks(css, WIDE)[0]
        self.assertIn('html[data-ff="foldable"] #phone>#jaryo-letters{grid-column:1/-1;padding-right:calc(50% + 12px)}', body)
        # With All apps open the row's own two columns are 48px apart, like the two-pane list, so neither column crosses the hinge.
        self.assertIn('html[data-ff="foldable"] #phone[data-app="home"][data-allapps]>#jaryo-letters{grid-column:1/-1;column-gap:48px}', body)
        # Only while All apps is open: the row is display:none otherwise and a two-column grid of 48px buttons when shown (JRY08).
        self.assertIn("#jaryo-letters{display:none;margin:12px}", css)
        self.assertIn('#phone[data-app="home"][data-allapps] #jaryo-letters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}', css)
        self.assertIn("#jaryo-letters button{margin:0;min-width:48px;min-height:48px}", css)
        # With real viewport segments the row keeps to column 1 with the list and drops the padding.
        seg = blocks(css, "@media (horizontal-viewport-segments:2){")
        self.assertEqual(len(seg), 1)
        self.assertIn('html[data-ff="foldable"] #phone>#jaryo-letters{grid-column:1;padding-right:0}', seg[0])
        self.assertIn('html[data-ff="foldable"] #phone[data-app="home"][data-allapps]>#jaryo-letters{grid-column:1}', seg[0])
        # Every foldable rule in this file is a letter-row rule inside one of those two blocks; the lock block has none.
        foldable = re.findall(r'html\[data-ff="foldable"\][^{},]*', css)
        self.assertTrue(foldable)
        self.assertTrue(all(s.endswith("#jaryo-letters") for s in foldable), foldable)
        self.assertEqual(len(foldable), len(re.findall(r'html\[data-ff="foldable"\][^{},]*', body + seg[0])))
        self.assertNotIn('html[data-ff="foldable"]', blocks(css, LOCK)[0])
        # phone-b.css keeps its own foldable rules: the row spans both panes with #home and #apps-list, the list keeps its 48px
        # gap and the row's own padding-right matches this file's, in both the >=840px block and the segments block.
        pb = _strip((STATIC / "phone-b.css").read_text(encoding="utf-8"))
        self.assertIn('html[data-ff="foldable"] #phone>#home,html[data-ff="foldable"] #phone>#apps-list,html[data-ff="foldable"] #phone>#jaryo-letters,html[data-ff="foldable"] #phone>#jaryo-masthead{grid-column:1/-1}', blocks(pb, WIDE)[0])
        self.assertIn('html[data-ff="foldable"] #phone>#jaryo-letters{box-sizing:border-box;padding-right:calc(50% + 12px)}', blocks(pb, WIDE)[0])
        self.assertIn('html[data-ff="foldable"] #phone[data-app] #home:not([hidden]),html[data-ff="foldable"] #phone[data-app] #apps-list:not([hidden]){column-gap:48px}', blocks(pb, WIDE)[0])
        self.assertIn('html[data-ff="foldable"] #phone>#jaryo-letters{padding-right:0}', blocks(pb, "@media (horizontal-viewport-segments:2){")[0])

    def test_palette_motion_and_order(self):
        css = _strip(_css())
        self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)), set())
        self.assertNotRegex(css, r"[;{]\s*order\s*:|row-reverse|column-reverse|[;{]\s*direction\s*:|grid-template-areas")
        self.assertNotRegex(css, r"transition|animation|will-change|@font-face|url\(|gradient|filter|box-shadow")
        self.assertNotRegex(css, r"[;{]\s*height\s*:")

    def test_existing_landscape_rules_still_inlined(self):
        # 4.2 and 4.6 are owned elsewhere: JRY04's four tracks and UXP21's sticky slide-out action row.
        out = page("t" * 32, "n")
        self.assertIn('html:not([data-ff]) #phone[data-app] #home:not([hidden]){grid-template-columns:repeat(4,minmax(0,1fr))}', out)
        self.assertIn('html[data-ff="slideout"] #controls{position:sticky;bottom:0;', out)
        # phone-b.css keeps exactly one >=840px block (test_uxp22_twopane); this layer never adds a 839px block.
        self.assertEqual(len(blocks(_strip((STATIC / "phone-b.css").read_text(encoding="utf-8")), WIDE)), 1)
        self.assertEqual(len(blocks(out[:out.index("</style>")], "@media (max-width:839px){")), 1)
        self.assertNotIn("840", (STATIC / "jaryo-layout.js").read_text(encoding="utf-8"))


class BaseRepairsStatic(unittest.TestCase):
    """All apps skips list rows without a button, tiles keep their label after their attributes, and each inline helper handler appears once (docs/jaryo.md section 13, JRY30)."""

    def test_all_apps_skips_rows_without_a_button(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        show = js[js.index("function show(){"):js.index("a.addEventListener('click',show)")]
        self.assertIn("b=li.firstChild;if(!b||!b.dataset)continue;", show)

    def test_tiles_keep_label_after_attributes(self):
        out = page("t" * 32, "n")
        # Canonical tile markup (phone-b.html): type, then an optional JRY04 data-size, then data-open, then the label as the
        # button's only content. Each tile is rendered exactly once.
        for name, label in [("phone", "Phone"), ("messages", "Messages"), ("helper", "Helper"), ("settings", "Settings")]:
            pattern = r'<button type="button"(?: data-size="(?:small|medium|wide)")? data-open="%s">%s</button>' % (name, label)
            self.assertRegex(out, pattern)
            self.assertEqual(len(re.findall(pattern, out)), 1, name)
            self.assertEqual(out.count('data-open="%s"' % name), 1, name)
        self.assertNotRegex(out, r'<button type="button" data-open="[a-z]+" data-size=')
        self.assertNotRegex(out, r'data-open="(?:phone|messages|helper|settings)"[^>]*>\s*</button>')

    def test_helper_call_markers_are_unique(self):
        out = page()
        lines = out.splitlines()
        # Each inline handler is one line and each line is unique: Plan and Why call helperCall with their own literal action;
        # the Ask handler (UXP99 shared Ask request) has no literal action of its own, it hands helperCall to helperAsk as the
        # transport and helperAsk builds the one action:'ask' request.
        for marker in ("document.getElementById('askbtn').onclick=", "helperCall({action:'plan'", "helperCall({action:'explain'"):
            self.assertEqual(len([ln for ln in lines if marker in ln]), 1, marker)
        ask = next(ln for ln in lines if "document.getElementById('askbtn').onclick=" in ln)
        self.assertIn("helperAsk(asked,webq.checked,o=>helperCall(o,ans))", ask)
        self.assertIn("const asked=q.value", ask)
        self.assertIn("q.value===asked", ask)
        self.assertNotIn("helperCall({action:'ask'", out)
        for ln in lines:
            if "action:'ask'" in ln:
                self.assertIn("function helperAsk(text,web,request)", ln, "only helperAsk spells out the ask action")
        # UXP31's Helper screen and the inline handler share phone-b.js helperAsk, so the ask action is spelled out once.
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        self.assertIn("function helperAsk(text,web,request){return request({action:'ask',text:text,web:web})", js)
        self.assertEqual(js.count("action:'ask'"), 1)
        self.assertEqual(js.count("helperAsk("), 2, "the definition and the Helper screen caller, no stray copy")


# UXP12's vm harness with one JRY08-style letter heading (a row without a button) in front of the list.
ANCHOR = "click(all);out.opened=st();"
HEAD = "const head=el('LI');head.textContent='P';ul.children.unshift(head);click(all);out.opened=st();out.headHidden=head.hidden;ul.children.shift();"


@unittest.skipUnless(uxp12.NODE, "node not installed")
class AllAppsRuntime(unittest.TestCase):
    def test_letter_heading_does_not_abort_all_apps(self):
        self.assertIn(ANCHOR, uxp12.HARNESS)
        harness = uxp12.HARNESS.replace(ANCHOR, HEAD, 1)
        for mode, enabled in (("contacts-off", False), ("contacts-on", True)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                h = Path(d) / "h.js"
                h.write_text(harness, encoding="utf-8")
                r = subprocess.run([uxp12.NODE, str(h), str(STATIC / "phone-b.js"), mode, ""],
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
                o = json.loads(r.stdout)
                self.assertEqual(o["opened"], ["home", False, True, True, True, "Phone"])
                self.assertFalse(o["headHidden"])
                self.assertEqual(o["allapps"], "1")
                expected = uxp12.LIST if enabled else [name for name in uxp12.LIST if name != "Contacts"]
                self.assertEqual(o["shown"], "|".join(expected))
                for key in ("closed", "back", "esc"):
                    self.assertEqual(o[key], ["home", True, False, False, False, "allapps"])


CHECK = r"""
CHECKS.jaryo_landscape = async function() {
  const out = [];
  const tick = "new Promise(function(r){requestAnimationFrame(function(){requestAnimationFrame(r)})})";
  // The metrics are re-applied to the live document (resize) before any measurement and every off-screen report carries
  // the measured numbers. Round 2 measured, at 360x640 with the viewport and #lock both 360x640 and 100vw/100vh equal to
  // the viewport, both lock buttons at x=-4..356: the base button rule's margin-right:8px on a 360px-wide button, centred
  // by the lock's flex column. The round-1 guess at a stale 100vh was wrong (round 1 lacked the numbers to tell). The
  // centring check below catches that asymmetry at any portrait width, not only when it leaves the viewport.
  const probe = `(function(){var p=document.createElement('div');p.style.cssText='position:fixed;top:0;left:0;width:100vw;height:100vh;visibility:hidden;pointer-events:none';
    document.body.appendChild(p);var r=p.getBoundingClientRect();p.remove();return innerWidth+'x'+innerHeight+' 100vw/100vh '+Math.round(r.width)+'x'+Math.round(r.height)})()`;
  async function view(w,h) {
    await load('home',w,h,w>h);
    await resize(w,h);
    const vp = await ev("innerWidth+'x'+innerHeight");
    if (vp !== w+'x'+h) out.push(w+'x'+h+' viewport not applied: '+vp);
  }
  // Lock: two columns only in landscape at >= 840px on auto; Slab and portrait keep the single column.
  for (const [w,h,ff,cols] of [[360,640,'',1],[1280,720,'',2],[1280,720,'slab',1],[720,1280,'',1]]) {
    await view(w,h);
    if (ff) await ev("document.documentElement.dataset.ff='"+ff+"'");
    await ev("oposUI.lock();"+tick);
    out.push(...await ev(`(function(){
      var out=[],tag='${w}x${h} ${ff||'auto'} lock ',$=function(i){return document.getElementById(i)},r=function(i){return $(i).getBoundingClientRect()};
      var lock=$('lock'),grid=getComputedStyle(lock).display==='grid';
      if(lock.hidden||!__u.vis($('unlock')))return [tag+'not shown'];
      var time=r('lock-time'),date=r('lock-date'),batt=r('lock-batt'),unlock=r('unlock'),call=r('lock-call');
      if(${cols}===2){
        if(!grid)out.push(tag+'not a grid');
        if(unlock.left<time.right-1||call.left<batt.right-1)out.push(tag+'buttons not in a second column');
        if(date.top<time.bottom-1||batt.top<date.bottom-1)out.push(tag+'left column not stacked');
        if(call.top<unlock.bottom-1)out.push(tag+'buttons not stacked');
        if(Math.round(time.left)!==Math.round(date.left))out.push(tag+'left column not aligned');
        if(getComputedStyle($('lock-date')).borderBottomWidth!=='3px')out.push(tag+'dateline rule missing');
      }else{
        if(grid)out.push(tag+'grid outside landscape');
        if(unlock.top<batt.bottom-1)out.push(tag+'buttons beside the text');
        var lockBox=r('lock');
        ['unlock','lock-call'].forEach(function(id){var b=r(id);
          if(Math.abs((b.left-lockBox.left)-(lockBox.right-b.right))>1)out.push(tag+id+' not centred in the lock: '+Math.round(b.left-lockBox.left)+' left, '+Math.round(lockBox.right-b.right)+' right');
          if(b.width<Math.min(lockBox.width,360)-1)out.push(tag+id+' narrower than min(100%,360px): '+Math.round(b.width))});
      }
      var box=function(b){return Math.round(b.left)+','+Math.round(b.top)+'-'+Math.round(b.right)+','+Math.round(b.bottom)};
      ['unlock','lock-call'].forEach(function(id){var b=r(id);if(b.width<48||b.height<48)out.push(tag+id+' below 48px');
        if(b.left<-0.5||b.right>innerWidth+0.5||b.top<-0.5||b.bottom>innerHeight+0.5)out.push(tag+id+' off screen '+box(b)+' lock '+box(r('lock'))+' viewport '+${probe})});
      if(document.activeElement!==$('unlock'))out.push(tag+'focus not on Unlock');
      oposUI.unlock();return out})()`));
  }
  // Foldable with All apps open (UXP21 fold_allapps): the JRY08 letter row spans both panes above the two-pane list and
  // no letter sits in the 24px centre gutter; the order rule is the one tests/test_uxp21_profiles.py MEASURE applies.
  await view(1280,720);
  await ev("document.documentElement.dataset.ff='foldable'");
  await ev("document.getElementById('allapps').click();"+tick);
  out.push(...await ev(`(function(){
    var out=[],tag='1280x720 foldable allapps ',$=function(i){return document.getElementById(i)},p=$('phone'),L=$('jaryo-letters'),ul=$('apps-list');
    if(p.dataset.allapps!=='1'||!__u.vis(ul))return [tag+'did not open'];
    if(!L||!__u.vis(L)||!L.querySelector('button'))return [tag+'letter row missing'];
    var lo=innerWidth/2-12,hi=innerWidth/2+12,lr=L.getBoundingClientRect(),ur=ul.getBoundingClientRect();
    if(getComputedStyle(p).display!=='grid')out.push(tag+'#phone is not the foldable grid');
    if(!(lr.left<lo&&lr.right>hi))out.push(tag+'letter row does not span both panes '+Math.round(lr.left)+'-'+Math.round(lr.right));
    if(!(ur.left<lo&&ur.right>hi))out.push(tag+'list does not span both panes '+Math.round(ur.left)+'-'+Math.round(ur.right));
    if(ur.top<lr.bottom-0.5)out.push(tag+'list not below the letter row');
    Array.prototype.forEach.call(L.querySelectorAll('button'),function(b){var r=b.getBoundingClientRect(),id=b.textContent.trim();
      if(r.left<hi-0.5&&r.right>lo+0.5)out.push(tag+'letter '+id+' in the hinge '+Math.round(r.left)+'-'+Math.round(r.right));
      if(r.left<-0.5||r.right>innerWidth+0.5)out.push(tag+'letter '+id+' off screen');
      if(r.width<48||r.height<48)out.push(tag+'letter '+id+' below 48px')});
    // Reading order of the visible in-flow #phone children: full-width items first, then the panes (UXP21 MEASURE).
    var side=-1,y=-1;Array.prototype.forEach.call(p.children,function(e){if(!__u.vis(e))return;var c=getComputedStyle(e);if(c.position==='fixed'||c.position==='absolute')return;
      var r=e.getBoundingClientRect(),s=r.left<lo&&r.right>hi?-1:(r.right<=hi?0:1);
      if(s<side||(s===side&&r.top<y-0.5))out.push(tag+'order '+(e.id||e.tagName)+' side '+s+' top '+Math.round(r.top));side=s;y=r.top});
    oposUI.openApp('home');return out})()`));
  // Shade: a sidebar column at >= 840px (docs/jaryo.md 4.3); it still covers the viewport below (UXP23).
  for (const [w,h,side] of [[360,640,false],[1280,720,true],[720,1280,false]]) {
    await view(w,h);
    await ev("opos.open()");
    await sleep(450);
    out.push(...await ev(`(function(){
      var out=[],tag='${w}x${h} shade ',sheet=document.getElementById('sheet'),r=sheet.getBoundingClientRect(),c=getComputedStyle(sheet);
      if(!opos.isOpen()||c.visibility!=='visible')return [tag+'did not open'];
      if(r.top>0.5||r.left>0.5||r.bottom<innerHeight-0.5)out.push(tag+'not full height from the top-left corner');
      if(${side}){
        var rem=parseFloat(getComputedStyle(document.documentElement).fontSize);
        if(r.width>24*rem+1||r.width>innerWidth/2)out.push(tag+'wider than 24rem: '+Math.round(r.width));
        if(c.borderRightWidth!=='1px'||c.borderRightStyle!=='solid')out.push(tag+'no right rule');
        var home=document.getElementById('home').getBoundingClientRect();
        if(home.right<=r.right+1)out.push(tag+'page not visible beside the column');
        if(parseFloat(getComputedStyle(document.getElementById('scrim')).opacity)!==0)out.push(tag+'scrim paints over the page');
      }else if(r.right<innerWidth-0.5)out.push(tag+'does not cover the viewport');
      var b=document.querySelector('#qs button');
      if(!b||!__u.vis(b)||b.getBoundingClientRect().width<48||b.getBoundingClientRect().height<48)out.push(tag+'first shade button not usable');
      return out})()`));
    await ev("opos.close()");
    await sleep(450);
    out.push(...await ev(`(function(){
      var out=[],tag='${w}x${h} shade closed ',sheet=document.getElementById('sheet'),c=getComputedStyle(sheet);
      var m=/matrix\\(([^)]*)\\)/.exec(c.transform),v=m?m[1].split(',').map(parseFloat):null;
      if(opos.isOpen()||c.visibility!=='hidden')out.push(tag+'still open');
      if(!v)out.push(tag+'no transform: '+c.transform);
      else if(${side}){if(!(v[4]<=-sheet.getBoundingClientRect().width+1)||Math.round(v[5])!==0)out.push(tag+'not parked to the left: '+c.transform)}
      else if(!(v[5]<0)||Math.round(v[4])!==0)out.push(tag+'not parked above: '+c.transform);
      return out})()`));
  }
  return out;
};
"""


class LandscapeBrowser(unittest.TestCase):
    skip_reason = None
    mode = "port"
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown
    _run = acceptance.TestUxrAcceptance._run

    def test_landscape(self):
        driver = Path(self.run_dir) / "landscape.js"
        source = acceptance.DRIVER.read_text()
        self.assertIn("(async () => {", source)
        driver.write_text(source.replace("(async () => {", CHECK + "\n(async () => {", 1))
        target = str(self.port) if self.proc is not None else "pipe"
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile, TMPDIR=self.run_dir)
        r = subprocess.run([self.node, str(driver), target, self.url, "jaryo_landscape", self.state],
                           capture_output=True, text=True, timeout=90, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["violations"], [])

    def test_continuity_with_all_apps_open(self):
        # The UXP20 continuity check is where the JRY08 letter headings surfaced ("Escape skipped All apps level").
        self.assertEqual(self._run("continuity"), [])

    def test_touch_targets(self):
        self.assertEqual(self._run("touch_targets"), [])


if __name__ == "__main__":
    unittest.main()
