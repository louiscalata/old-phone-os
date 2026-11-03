# SPDX-License-Identifier: GPL-3.0-or-later
import html
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from opos import notices
from opos.setup import notices_screen, wizard_page
from tests.test_setup_screens import NodeDOM


class SetupNotices(unittest.TestCase):
    def test_visible_before_setup_and_apply_without_script(self):
        page = wizard_page.page('tok', 'nonce')
        nodes = NodeDOM(page).nodes
        by_id = {n['attrs']['id']: n for n in nodes if 'id' in n['attrs']}
        self.assertEqual(sum(n['attrs'].get('id') == 'jaryo-notices' for n in nodes), 1)
        node = by_id['jaryo-notices']
        while True:
            self.assertNotIn('hidden', node['attrs'])
            if node['parent'] < 0:
                break
            node = nodes[node['parent']]
        self.assertLess(page.index('id="jaryo-notices"'), page.index('id="s1"'))
        self.assertLess(page.index('id="jaryo-notices"'), page.index('id="apply"'))
        self.assertIn('href="#profile"', notices_screen.fragment())
        self.assertIn('id="profile"', page)
        self.assertNotIn('<script', notices_screen.fragment())

    def test_all_shared_notices_and_visible_risks(self):
        fragment = notices_screen.fragment()
        for key, value in notices.notices().items():
            for item in value if isinstance(value, list) else [value]:
                self.assertIn(html.escape(str(item)), fragment, key)
        self.assertIn('<details open><summary>Before installing</summary>', fragment)
        self.assertEqual(fragment.count('<details'), 3)
        self.assertIn('[restore guide: unpublished]', fragment)
        self.assertIn(html.escape(notices.INSTALL_SCOPE), fragment)

    def test_notice_values_are_escaped(self):
        data = notices.notices()
        data['build'] = '<script>bad()</script>'
        data['risks'] = ['<img src=x onerror=bad()>']
        with patch.object(notices, 'notices', return_value=data):
            fragment = notices_screen.fragment()
        self.assertNotIn('<script>', fragment)
        self.assertNotIn('<img', fragment)
        self.assertIn(html.escape(data['build']), fragment)
        self.assertIn(html.escape(data['risks'][0]), fragment)

    @unittest.skipIf(shutil.which('node') is None, 'node missing')
    def test_continue_and_step_navigation_dismiss_notices(self):
        page = wizard_page.page('tok', 'nonce')
        continuation = re.search(r'q\("#notices-continue"\)\.addEventListener\("click",(function\(e\)\{[^\n]+})\);', page).group(1)
        show = re.search(r'function show\(i\)\{[^\n]+', page).group(0)
        script = """
const assert = require('assert');
const notices = {hidden:false}, profile = {focus(){this.focused=true}};
const q = s => ({'#jaryo-notices':notices,'#profile':profile})[s];
let prevented=false, cur=0, meters=0, summaries=0;
const steps=Array.from({length:7},()=>({hidden:true,querySelector(){return null}}));
function updMeter(){meters++} function buildSummary(){summaries++}
const proceed = CONTINUE;
proceed({preventDefault(){prevented=true}});
assert(prevented); assert(notices.hidden); assert(profile.focused);
SHOW
notices.hidden=false;
show(1);
assert(notices.hidden); assert.strictEqual(cur,1);
assert.deepStrictEqual(steps.map(s=>s.hidden),[true,false,true,true,true,true,true]);
show(0); assert(notices.hidden); assert(!steps[0].hidden);
show(6); assert.strictEqual(summaries,1); assert.strictEqual(meters,3);
""".replace('CONTINUE', continuation).replace('SHOW', show)
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
