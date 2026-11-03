# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
from opos.setup.wizard_page import page

class TestDoneHonest(unittest.TestCase):
    def setUp(self):
        self.html = page("a"*32, "n1")
        start_poll = self.html.index("async function poll(")
        start_apply = self.html.index("async function doApply(")
        self.poll_src = self.html[start_poll:start_apply]
        end_apply_listener = self.html.index('q("#apply").addEventListener')
        self.apply_src = self.html[start_apply:end_apply_listener]

    def test_norun_element_exists_hidden_and_empty(self):
        m = re.search(r'<p\b[^>]*\bid="done-norun"[^>]*\bhidden\b[^>]*></p>', self.html)
        self.assertIsNotNone(m, "done-norun paragraph missing or not empty/hidden")
        self.assertIn('id="done"', self.html)
        self.assertIn("All set.", self.html)

    def test_poll_checks_health_before_all_set(self):
        src = self.poll_src
        self.assertIn('"health"', src)
        self.assertIn('"skipped"', src)
        self.assertIn('#done-norun', src)
        first_health = src.index('"health"')
        first_done_hide = src.index('q("#done").hidden=false')
        self.assertLess(first_health, first_done_hide, "health check must precede done reveal")

    def test_messages_are_plain_and_exact(self):
        src = self.poll_src
        self.assertIn('Dry run finished. No services or tools were installed. Personal choices are still saved.', src)
        self.assertIn('Your choices are saved, but Old Phone OS cannot start on its own here. See the README, section Quickstart, to start it by hand.', src)
        self.assertIn('S.dry_run', src)
        self.assertNotIn('innerHTML', src)

    def test_apply_resets_norun_message(self):
        src = self.apply_src
        self.assertIn('#done-norun', src)
        idx_norun = src.index('#done-norun')
        idx_collect = src.index('collect()')
        self.assertLess(idx_norun, idx_collect, "done-norun reset must be before collect")

if __name__ == "__main__":
    unittest.main()
