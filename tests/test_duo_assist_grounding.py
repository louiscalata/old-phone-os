# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import patch

from opos import assist


QUESTION = "Battery is low. What should the phone pause first?"
ORIGINAL_PREFIX = "Answer in at most four short sentences. If sources are listed, use only them and cite them as [1], [2]. If you do not know, say so.\n"
FACTS_PREFIX = "Answer in at most four short sentences. Use the phone facts for questions about the phone and any listed sources for other information; cite sources as [1], [2]. When asked why something is paused, off or not working, the why_paused fact is the reason: restate it in plain words. If neither the facts nor the sources covers the question, say you do not know.\n"


def make_context():
    return dict([(chr(112) + "aused", [chr(115) + "ync"]), ("mode", "reserve")])


class AssistGroundingTest(unittest.TestCase):
    def test_context_is_added_to_prompt(self):
        with patch("opos.assist.muse.ask", return_value="Pause background work.") as ask:
            result = assist.answer(QUESTION, env={}, free=2000, web=False, context=make_context())
        prompt = ask.call_args[0][1]
        expected = FACTS_PREFIX + "Facts about this phone (use them for questions about the phone): " + '{"mode": "reserve", "paused": ["sync"]}' + "\n\nQuestion: " + QUESTION
        self.assertEqual(prompt, expected)
        self.assertEqual(result["text"], "Pause background work.")

    def test_no_context_prompt_unchanged(self):
        expected = ORIGINAL_PREFIX + "\nQuestion: " + QUESTION
        cases = [{"args": {}}, {"args": {"context": None}}, {"args": {"context": {}}}, {"args": {"context": "ignored"}}]
        for case in cases:
            with self.subTest(case=case["args"]):
                with patch("opos.assist.muse.ask", return_value="Pause background work.") as ask:
                    assist.answer(QUESTION, env={}, free=2000, web=False, **case["args"])
                self.assertEqual(ask.call_args[0][1], expected)


if __name__ == "__main__":
    unittest.main()
