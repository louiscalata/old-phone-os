# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
import subprocess
import unittest

from opos.web import page
from tests.test_uxp03_shade import HARNESS as BASE, JS, NODE

HARNESS = BASE.split("const hook=c.oposHooks[0],out={};", 1)[0]
HARNESS = HARNESS.replace(
    "set textContent(value){this.text=value;this.children=[]}",
    "set textContent(value){this.writes=(this.writes||0)+1;this.text=value;this.children=[]}")
HARNESS = HARNESS.replace(
    "c.window=c;",
    """
Element.prototype.addEventListener=function(){};
Element.prototype.focus=function(){};
for(const id of ['phone','lock','lock-time','lock-date','lock-batt',
                 'lock-notice','unlock','lock-ask','lock-call','lock-hangup']){
  root.appendChild(new Element(id));
}
get('lock').hidden=true;
c.location={search:''};
c.document.addEventListener=function(){};
get('time-left').scrollIntoView=function(){calls.push('battery')};
c.window=c;""")
HARNESS = HARNESS.replace(
    "c.oposUI={lock(){calls.push('lock')},openApp(name){calls.push(name)}};",
    "c.oposUI.openApp=function(name){calls.push(name)};")
HARNESS += r"""
const out={},render=s=>c.oposHooks.forEach(f=>f(s));
render({notice:'secret demo text',helper_visible:false,battery:{percent:42}});
out.lock=get('lock-notice').textContent;
out.locked=!get('lock').hidden;
out.shade=get('qs-notices').textContent;
out.off=[get('qs-helper').textContent,get('qs-helper').attrs['aria-pressed']];
out.battery=get('qs-batt').textContent;
calls.length=0;
get('qs-batt').onclick();
get('qs-close').onclick();
out.close=[get('qs-close').type,get('qs-close').textContent,calls.slice()];
const item=get('qs-notices').children[0];
out.item=item.children.map(e=>[e.tag,e.textContent,e.attrs]);
out.dismissType=item.children[3].type;
const writes=get('lock-notice').writes;
render({notice:'secret demo text',helper_visible:false,battery:{percent:41}});
out.sameDismiss=get('qs-notices').children[0].children[3]===item.children[3];
out.sameStatusWrites=get('lock-notice').writes===writes;
render({notice:'second',helper_visible:true});
out.addStatusWrites=get('lock-notice').writes===writes;
out.on=[get('qs-helper').textContent,get('qs-helper').attrs['aria-pressed']];
get('qs-notices').children[0].children[3].onclick();
out.remaining=get('qs-notices').children.map(e=>e.children[2].textContent);
render({notice:'second'});
out.afterPoll=get('qs-notices').children.map(e=>e.children[2].textContent);
render({});
out.pendingLock=get('lock-notice').textContent;
get('qs-notices').children[0].children[3].onclick();
out.dismissedLock=get('lock-notice').textContent;
out.emptyShade=get('qs-notices').textContent;
render({notice:'third'});
get('qs-notices').children[0].children[3].onclick();
render({notice:'third'});
out.dismissedPollLock=get('lock-notice').textContent;
get('unlock').onclick();
out.unlocked=[get('lock').hidden,get('phone').inert];
calls.length=0;
c.oposUI.lock();
calls.length=0;
get('lock-call').onclick();
out.call=[calls.slice(),get('lock').hidden,get('phone').inert];
delete c.toggleVis;
delete get('time-left').scrollIntoView;
render({});
out.unavailable=['qs-helper','qs-batt'].map(id=>{
  const b=get(id);return [b.disabled,b.attrs['aria-disabled'],b.textContent];
});
out.emptyLock=get('lock-notice').textContent;
console.log(JSON.stringify(out));
"""
HARNESS = HARNESS.replace(
    "createElement:()=>new Element()",
    "createElement:tag=>{const e=new Element();e.tag=tag;return e}")


class TestPrivateLockShade(unittest.TestCase):
    def run_js(self):
        if not NODE:
            self.skipTest('node not installed')
        result = subprocess.run(
            [NODE, '--unhandled-rejections=strict', '-e', HARNESS, str(JS)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_private_lock_and_shade(self):
        out = self.run_js()
        self.assertTrue(out['locked'])
        self.assertEqual(out['lock'], 'New notice')
        self.assertNotIn('secret demo text', out['lock'])
        self.assertIn('secret demo text', out['shade'])
        self.assertEqual(out['emptyLock'], '')
        self.assertEqual(out['unlocked'], [True, False])
        self.assertEqual(out['close'], ['button', 'Close', ['battery', 'close']])
        self.assertEqual(out['battery'], 'Battery: 42%')
        self.assertEqual(out['call'], [['phone'], True, False])

    def test_unchanged_poll_preserves_dismiss_button_and_live_region(self):
        out = self.run_js()
        self.assertTrue(out['sameDismiss'])
        self.assertTrue(out['sameStatusWrites'])
        self.assertTrue(out['addStatusWrites'])

    def test_lock_status_tracks_pending_notices(self):
        out = self.run_js()
        self.assertEqual(out['pendingLock'], 'New notice')
        self.assertEqual(out['dismissedLock'], '')
        self.assertEqual(out['emptyShade'], 'No recent notices')
        self.assertEqual(out['dismissedPollLock'], '')

    def test_notice_structure_and_dismiss(self):
        out = self.run_js()
        item = out['item']
        self.assertEqual([e[0] for e in item], ['b', 'time', 'span', 'button'])
        self.assertEqual(item[0][1], 'Notice')
        self.assertRegex(item[1][1], r'\d{2}:\d{2}')
        self.assertIn('datetime', item[1][2])
        self.assertEqual(item[3][1:], ['Dismiss', {'aria-label': 'Dismiss notice'}])
        self.assertEqual(out['dismissType'], 'button')
        self.assertEqual(out['remaining'], ['secret demo text'])
        self.assertEqual(out['afterPoll'], ['secret demo text'])

    def test_helper_state_and_unavailable(self):
        out = self.run_js()
        self.assertEqual(out['off'], ['Helper: Off', 'false'])
        self.assertEqual(out['on'], ['Helper: On', 'true'])
        self.assertEqual(out['unavailable'], [
            [True, 'true', 'Helper: Off unavailable'],
            [True, 'true', 'Battery: unknown unavailable']])

    def test_accessible_markup_and_sizes(self):
        html = page()
        self.assertRegex(html, r'<div id="lock-notice"[^>]*role="status"[^>]*aria-live="polite"')
        css = JS.with_suffix('.css').read_text()
        rule = re.search(r'#qs button\{([^}]+)\}', css).group(1)
        self.assertGreaterEqual(int(re.search(r'min-height:(\d+)px', rule).group(1)), 48)
        self.assertRegex(css, r'#qs\{[^}]*gap:8px')
        self.assertRegex(css, r'#qs-notices li\{[^}]*gap:8px')
        self.assertRegex(css, r'#lock-time\{[^}]*font-size:[\d.]+rem')
        self.assertRegex(css, r'#lock-time\{[^}]*overflow-wrap:anywhere')


if __name__ == '__main__':
    unittest.main()
