# SPDX-License-Identifier: GPL-3.0-or-later
"""Model text may not claim pauses the final decision does not have.

The contradicting replies below are verbatim answers from Llama 3.2 1B on a Surface Duo
(November 7, 2026) while it was charging with nothing paused.
"""
import unittest
from unittest import mock

from opos import assist, muse

CHARGING = {"paused": [], "allowed": ["calls", "messages", "email", "maps", "photos backup"],
            "why_paused": "The battery is at 44% (98% health, about 42% usable) and it is charging; nothing is paused, so everything is on."}
SAVER = {"paused": ["maps", "photos backup"], "allowed": ["calls", "messages", "email"],
         "why_paused": "The battery is worn (60% health), so 60% charge is about 36% usable and it is not charging; to keep calls and messages working until 16:00, the phone paused maps and photos backup."}
DUO_WHY = ("Tasks are paused because the battery level is low, indicating that the device is running on low power. "
           "This means that the device is not performing any tasks, and the paused state is necessary to conserve power.")
DUO_ASK = ("The maps are paused because the battery is at 44% (98% health, about 42% usable) and it is charging.\n\n"
           "This is the reason why the maps are paused, as the phone is currently charging and the battery is low.")
GLIMMER = ("Maps are paused to save power. The battery is worn at 60% health, so a 60% charge is only about 36% usable "
           "and the phone is not charging. The phone paused maps and photos backup to keep calls and messages working until 16:00.")


class ContradictsTests(unittest.TestCase):
    def test_duo_replies_contradict_nothing_paused(self):
        self.assertTrue(muse.contradicts(DUO_WHY, CHARGING))
        self.assertTrue(muse.contradicts(DUO_ASK, CHARGING))

    def test_naming_a_running_task_as_paused(self):
        self.assertTrue(muse.contradicts("Email is paused to stretch the battery.", SAVER))

    def test_true_answers_pass(self):
        self.assertFalse(muse.contradicts(GLIMMER, SAVER))
        self.assertFalse(muse.contradicts("Nothing is paused because the phone is charging.", CHARGING))
        self.assertFalse(muse.contradicts("Maps isn't paused; everything is on while it charges.", CHARGING))
        self.assertFalse(muse.contradicts("Calls and messages are never paused.", SAVER))
        self.assertFalse(muse.contradicts("The battery is at 44% and charging.", CHARGING))

    def test_without_a_paused_list_nothing_is_judged(self):
        self.assertFalse(muse.contradicts(DUO_WHY, {}))
        self.assertFalse(muse.contradicts(DUO_WHY, None))


class ExplainTests(unittest.TestCase):
    def test_nothing_paused_answers_from_rules_without_a_model(self):
        with mock.patch.object(muse, "ask") as ask:
            out = muse.explain(CHARGING, muse.glimmer_route({}), env={})
        ask.assert_not_called()
        self.assertEqual((out["engine"], out["ok"]), ("rules", False))
        self.assertTrue(out["text"].startswith(CHARGING["why_paused"]))

    def test_contradicting_reply_falls_back_to_rules(self):
        with mock.patch.object(muse, "ask", return_value="Email is paused to save power."):
            out = muse.explain(SAVER, muse.glimmer_route({}), env={})
        self.assertEqual(out["engine"], "rules")
        self.assertTrue(out["text"].startswith(SAVER["why_paused"]))

    def test_true_reply_is_kept(self):
        with mock.patch.object(muse, "ask", return_value=GLIMMER):
            out = muse.explain(SAVER, muse.glimmer_route({}), env={})
        self.assertEqual((out["text"], out["ok"]), (GLIMMER, True))


class AskTests(unittest.TestCase):
    def _answer(self, reply, facts, question="Tell me about maps."):
        route = muse.Route("local", "Meta Llama 3.2 1B", "on this phone", "http://127.0.0.1:8080/v1", "llama-3.2-1b-instruct")
        with mock.patch.object(assist, "ask_routes", return_value=[route]), mock.patch.object(muse, "ask", return_value=reply) as ask:
            return assist.answer(question, env={}, free=4096, context=facts), ask

    def test_duo_ask_reply_replaced_by_rules(self):
        out, _ = self._answer(DUO_ASK, CHARGING)
        # ok stays True: on the page, ok False with a rules engine means the helper is resting.
        self.assertEqual((out["engine"], out["where"], out["ok"]), ("battery rules", "on this phone", True))
        self.assertTrue(out["text"].startswith(CHARGING["why_paused"]))
        self.assertNotIn("maps are paused", out["text"].lower())

    def test_grounded_reply_kept(self):
        out, _ = self._answer(GLIMMER, SAVER)
        self.assertEqual((out["engine"], out["ok"]), ("Meta Llama 3.2 1B", True))

    def test_pause_question_answered_by_rules_without_a_model(self):
        out, ask = self._answer(DUO_ASK, CHARGING, "Why are maps paused?")
        ask.assert_not_called()
        self.assertEqual(out["engine"], "battery rules")
        self.assertTrue(out["text"].startswith(CHARGING["why_paused"]))

    def test_time_questions_answered_by_rules(self):
        # The Duo's 1B model answered "16 hours" here, reading the 16:00 goal as a duration.
        charging = dict(CHARGING, time_left="Charging", charging=True, goal="16:00", goal_met=True)
        out, ask = self._answer("16 hours", charging, "How long will my battery last?")
        ask.assert_not_called()
        self.assertTrue(out["text"].startswith("The phone is charging, so the battery is filling"))
        unplugged = dict(SAVER, time_left="About 3 h 10 min left", charging=False, goal="16:00", goal_met=False)
        out, _ = self._answer("x", unplugged, "When will it run out?")
        self.assertTrue(out["text"].startswith("About 3 h 10 min left at the current rate. That falls short of your 16:00 goal"))
        out, _ = self._answer("x", dict(unplugged, goal_met=True), "how long will it last")
        self.assertIn("That reaches your 16:00 goal.", out["text"])

    def test_other_questions_and_web_search_still_use_the_model(self):
        out, ask = self._answer("A capital city.", CHARGING, "What is the capital of France?")
        ask.assert_called_once()
        self.assertEqual(out["engine"], "Meta Llama 3.2 1B")
        self.assertIsNone(assist.rules_answer("Why is maps paused?", {}))


if __name__ == "__main__":
    unittest.main()
