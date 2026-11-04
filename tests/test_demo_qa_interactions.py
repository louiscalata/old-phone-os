# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the served app/notice/Ask handlers in the existing Node DOM fixture."""
import json
import shutil
import subprocess
import unittest

from opos.public_demo import DemoPhone
from tests.test_helper_enter_dispatch import HARNESS, ROOT


PRELUDE = HARNESS.split('(async()=>{', 1)[0].replace(
    'return{ids,doc,posts,flush,open,key,send,state}',
    'return{ctx,ids,doc,posts,flush,open,key,send,state}')
SCENARIOS = r"""
(async()=>{
 const out={},w=world(),ctx=w.ctx;
 const notice=w.doc.createElement('div');notice.id='notice';w.ids.phone.appendChild(notice);
 const scope=read('js-3.js');vm.runInContext(scope.slice(scope.indexOf('function scopeNotice(')),ctx);
 const paint=vm.runInContext('(function(s){'+read('js-5.js').split('\n')[0]+'})',ctx);
 ctx.oposUI.openApp('phone');paint({notice:'Call ended',notice_app:'phone'});
 out.callVisible=!notice.hidden;
 ctx.oposUI.openApp('messages');out.callHidden=notice.hidden;
 paint({notice:'New message',notice_app:'messages'});out.messageVisible=!notice.hidden;
 ctx.oposUI.openApp('settings');out.settingsHidden=notice.hidden;
 ctx.oposUI.openApp('home');out.homeVisible=!notice.hidden;
 await w.open();out.helperHidden=notice.hidden;
 ctx.oposUI.openApp('messages');out.returnVisible=!notice.hidden;
 let requests=0,renders=0;
 ctx.api=async()=>{requests++;return{}};ctx.render=()=>{renders++};
 vm.runInContext(read('js-7.js').slice(read('js-7.js').indexOf('async function go(')),ctx);
 for(const text of ['', '   ']){w.ids.msg.value=text;await ctx.go('receive')}
 out.empty={requests,renders,text:notice.textContent,hidden:notice.hidden};
 w.ids.msg.value='Hello';await ctx.go('receive');out.valid={requests,renders};
 // Run the real notice-history hook; errors must not become durable notices.
 const source=read('phone-a.js'),start=source.indexOf('var notices=[]'),end=source.indexOf('})();',start);
 vm.runInContext('(function(){'+source.slice(start,end)+'})();',ctx);
 ctx.demoTime='Demo time 12:00';
 ctx.oposHooks.at(-1)({status:'error: message empty or too long',notice:'Message empty or too long',battery:{percent:60},clock:'12:04'});
 out.noErrorNotice=w.doc.getElementById('qs-notices').textContent;
 ctx.oposHooks.at(-1)({status:'message received',notice:'New message',battery:{percent:60},clock:'12:06'});
 out.noticeClock=w.doc.getElementById('qs-notices').children[0].children.find(e=>e.tagName==='TIME').textContent;
 // Both the stacked simulator and enhanced app clear only the answered question.
 for(const enhanced of [false,true])for(const kind of ['success','error','declined','edited']){
  const v=world();if(enhanced)await v.open();vm.runInContext('vis=true',v.ctx);
  v.ids.q.value='Will calls last until 4 PM?';
  if(enhanced)await v.send();else {v.ids.askbtn.click();await v.flush()}
  if(kind==='edited')v.ids.q.value='Next question';
  v.posts[0].reply(kind==='error'?{error:'Try again'}:{ok:kind!=='declined',text:'Yes. Calls and messages until ~4:30 PM.',engine:'rules',where:'battery rules'});
  await v.flush();out[(enhanced?'app':'stacked')+kind]=v.ids.q.value;
 }
 console.log(JSON.stringify(out));
})().catch(e=>{console.error(e.stack);process.exitCode=1});
"""


class StatusSource(unittest.TestCase):
    def test_phone_and_message_results_keep_their_origin(self):
        phone = DemoPhone()
        for action, args, app in (
                ('call', ('+15550100',), 'phone'), ('hang_up', (), 'phone'),
                ('receive', ('+15550100', 'Hello'), 'messages'),
                ('send', ('+15550100', 'Hello'), 'messages'),
                ('call', ('bad',), 'phone'),
                ('receive', ('bad\nname', 'Hello'), 'messages')):
            with self.subTest(action=action, args=args):
                getattr(phone, action)(*args)
                self.assertEqual(phone.state()['notice_app'], app)


@unittest.skipUnless(shutil.which('node'), 'Node.js unavailable')
class AppInteractions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['node', '-e', PRELUDE + SCENARIOS, str(ROOT)],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.result = json.loads(result.stdout)

    def test_status_follows_its_app_across_navigation(self):
        for key in ('callVisible', 'callHidden', 'messageVisible', 'settingsHidden', 'homeVisible',
                    'helperHidden', 'returnVisible'):
            self.assertTrue(self.result[key], key)

    def test_empty_incoming_text_is_guidance_without_request_or_notice(self):
        self.assertEqual(self.result['empty'], {'requests': 0, 'renders': 0,
                         'text': 'Type the incoming text first.', 'hidden': False})
        self.assertEqual(self.result['valid'], {'requests': 1, 'renders': 1})
        self.assertEqual(self.result['noErrorNotice'], 'No recent notices')

    def test_notices_use_the_simulator_clock_in_status_bar_format(self):
        self.assertEqual(self.result['noticeClock'], '12:06')

    def test_both_ask_views_clear_success_but_keep_errors_and_new_drafts(self):
        for view in ('app', 'stacked'):
            self.assertEqual(self.result[view + 'success'], '')
            self.assertEqual(self.result[view + 'error'], 'Will calls last until 4 PM?')
            self.assertEqual(self.result[view + 'declined'], 'Will calls last until 4 PM?')
            self.assertEqual(self.result[view + 'edited'], 'Next question')


if __name__ == '__main__':
    unittest.main()
