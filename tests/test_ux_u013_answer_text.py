# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class WhyFallbackLabel(unittest.TestCase):
    def test_why_fallback_label(self):
        text = web_text()
        start_tag = "b.textContent='Why?'"
        end_tag = "h.appendChild(document.createElement('br'))"
        start = text.index(start_tag)
        end = text.index(end_tag, start)
        snippet = text[start:end]
        fallback = "ans.textContent=r.text+' (Standard answer: the helper did not reply. Try Why? again later.)'"
        original = "ans.textContent=r.text+' ('+r.engine+', '+r.where+')'"
        self.assertIn("r.ok===false", snippet)
        self.assertIn(fallback, snippet)
        self.assertIn(original, snippet)
        error_pos = snippet.index("if(r.error)")
        fallback_pos = snippet.index(fallback)
        else_pos = snippet.index("else", fallback_pos + len(fallback))
        original_pos = snippet.index(original, else_pos)
        self.assertLess(error_pos, fallback_pos)
        self.assertLess(fallback_pos, else_pos)
        self.assertLess(else_pos, original_pos)


if __name__ == "__main__":
    unittest.main()
