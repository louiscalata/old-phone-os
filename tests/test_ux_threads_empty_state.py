# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class ThreadsEmptyStateTest(unittest.TestCase):
    def test_threads_empty_state(self):
        text = web_text()
        line = next(l for l in text.splitlines() if "getElementById('threads').innerHTML" in l)
        self.assertIn("<b>Threads</b><br>", line)
        self.assertIn("(s.threads||[]).length===0?'No messages yet. Type a number and a message, then tap Send.':", line)
        self.assertIn("(s.threads||[]).map(t=>{const n=t.number||''", line)
        self.assertIn("nm=t.name||n,tx=t.preview||t.last||t.text||''", line)
        self.assertIn("}).join(' ')", line)
