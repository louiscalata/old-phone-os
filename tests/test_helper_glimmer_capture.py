# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressions from the November 3 capture; no model or listening socket needed."""
import io
import json
import shutil
import unittest
import urllib.error
from unittest import mock

from opos import assist, muse, web
from opos.advisor import GlimmerAdvisor
from tests import test_glimmer_credit as credit


GLIMMER = muse.glimmer_route({})
FACTS = {
    "mode": "CRITICAL", "allowed": ["calls", "messages"],
    "paused": ["email", "maps", "photos backup"], "hours_left": None, "reserve": 0,
    "battery_percent": 60, "battery_health_percent": 60, "usable_percent": 36,
    "temp_c": 30, "charging": False, "goal": "16:00", "goal_met": True,
    "time_left": "About 4 h 29 min left", "paused_by_glimmer": ["email"],
    "why_paused": "The battery is worn (60% health), so 60% charge is about 36% usable and it is not charging; to keep calls and messages working until 16:00, the phone paused email, maps and photos backup.",
}


def completion(content, finish="stop", reasoning="private reasoning"):
    return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish, "message": {
        "content": content, "reasoning_content": reasoning}}]}).encode())


class ExplainCapture(unittest.TestCase):
    def test_why_sends_small_grounded_rewrite_with_ask_budget_and_low_effort(self):
        opener = mock.Mock(side_effect=lambda *a, **k: completion("A complete explanation."))
        with mock.patch.object(muse.time, "monotonic", return_value=0):
            result = muse.explain(FACTS, [GLIMMER], env={}, opener=opener)
        body = json.loads(opener.call_args.args[0].data)
        prompt = body["messages"][0]["content"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "Muse Glimmer")
        self.assertEqual(result["text"], "A complete explanation.")
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["max_tokens"], 1600)  # same 400 + 1200 as Ask
        self.assertEqual(opener.call_args.kwargs["timeout"], 115)
        self.assertLess(len(prompt), 650)
        self.assertIn(FACTS["why_paused"], prompt)
        self.assertIn('"paused_by_glimmer": ["email"]', prompt)
        self.assertNotIn('"hours_left"', prompt)
        self.assertNotIn('"goal_met"', prompt)
        self.assertIn("four short sentences", prompt)
        self.assertEqual(FACTS["paused_by_glimmer"], ["email"])

    def test_missing_reason_keeps_known_facts_and_sanitizes_attribution(self):
        opener = mock.Mock(return_value=completion("Maps rest."))
        muse.explain({"paused": ["maps"], "battery_percent": 20,
                      "paused_by_glimmer": ["calls", "email"], "private": "omit"},
                     GLIMMER, env={}, opener=opener)
        prompt = json.loads(opener.call_args.args[0].data)["messages"][0]["content"]
        self.assertIn('"paused": ["maps"]', prompt)
        self.assertIn('"battery_percent": 20', prompt)
        self.assertNotIn("paused_by_glimmer", prompt)
        self.assertNotIn("private", prompt)

    def test_slow_first_route_leaves_a_capture_length_glimmer_answer_time(self):
        local = assist.local_route({"OPOS_LOCAL_URL": "http://127.0.0.1:8080/v1"}, 2000)
        now, calls = [0.0], []
        def opener(req, timeout):
            body = json.loads(req.data)
            calls.append((body["model"], timeout, body["max_tokens"]))
            if body["model"] == local.model:
                now[0] += timeout
                raise TimeoutError("local route stalled")
            # The slower captured Ask reply would fit in the remaining half.
            self.assertGreaterEqual(timeout, 56.685)
            now[0] += 56.685
            return completion("The battery is worn, so optional tasks rest.")
        with mock.patch.object(muse.time, "monotonic", side_effect=lambda: now[0]):
            out = muse.explain(FACTS, [local, GLIMMER], env={}, opener=opener)
        self.assertEqual(calls, [(local.model, 57.5, 200), (GLIMMER.model, 57.5, 1600)])
        self.assertLess(now[0], 115)
        self.assertEqual(out["engine"], "Muse Glimmer")

    def test_no_cut_off_or_reasoning_only_reply_reaches_owner(self):
        for content, finish in (("private partial thought", "length"),
                                ("", "stop"), ("<think>private unfinished thought", "stop")):
            with self.subTest(content=content):
                out = muse.explain(FACTS, GLIMMER, env={},
                                   opener=lambda *a, **k: completion(content, finish))
                self.assertFalse(out["ok"])
                self.assertEqual(out["engine"], "rules")
                self.assertNotIn("private", out["text"])
                self.assertIn("Muse Glimmer chose to pause email", out["text"])

    def test_cut_off_route_falls_through_to_complete_visible_reply(self):
        lan = muse.Route("lan", "Muse Glimmer", "on a computer you own", "http://127.0.0.1:1235/v1", GLIMMER.model)
        opener = mock.Mock(side_effect=[completion("private", "length"),
                                       completion("<think>private</think>Maps rest.")])
        out = muse.explain(FACTS, [lan, GLIMMER], env={}, opener=opener)
        self.assertEqual(out, {"ok": True, "engine": "Muse Glimmer", "where": "on a local server", "text": "Maps rest."})


class StatusCapture(unittest.TestCase):
    def phone(self, opener):
        phone = web.Phone(visible=True)
        advisor = GlimmerAdvisor(phone.runtime.governor.advisor, route=GLIMMER,
                                 ask=lambda r, p, **kw: muse.ask(r, p, env={}, opener=opener, **kw),
                                 spawn=lambda fn: fn(), clock=lambda: 0)
        phone.runtime.governor.advisor = advisor
        return phone

    def test_state_distinguishes_off_unreachable_bad_reply_and_applied_pauses(self):
        self.assertEqual(web.Phone(visible=True).state()["helper"]["glimmer_status"], "off")
        def unavailable(*args, **kwargs):
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        self.assertEqual(self.phone(unavailable).state()["helper"]["glimmer_status"], "not reachable")
        def bad_gateway(*args, **kwargs):
            raise urllib.error.HTTPError(GLIMMER.base_url, 502, "bad gateway", {}, io.BytesIO())
        self.assertEqual(self.phone(bad_gateway).state()["helper"]["glimmer_status"], "no reply")
        self.assertEqual(self.phone(lambda *a, **k: completion("not JSON")).state()["helper"]["glimmer_status"], "no reply")
        phone = self.phone(lambda *a, **k: completion('{"defer":["email"]}'))
        phone.state()
        state = phone.state()
        self.assertEqual(state["helper"]["glimmer_status"], "ready")
        self.assertEqual(state["helper"]["paused_by_glimmer"], ["email"])
        email = next(p for p in state["battery"]["paused"] if p["name"] == "email")
        self.assertIn("Muse Glimmer", email["reason"])

    @unittest.skipUnless(shutil.which("node"), "node missing")
    def test_line_uses_pause_field_even_when_diagnostic_reason_disagrees(self):
        render = credit.TestContributionLine().render
        states = [{"helper": {"paused_by_glimmer": ["email"], "glimmer_status": "ready"},
                   "battery": {"why": "router: allowed 2, deferred 3"}},
                  {"helper": {"glimmer_status": "not reachable"}},
                  {"helper": {"glimmer_status": "off"}}, {},
                  {"helper": {"glimmer_status": "ready"}, "battery": {"why": "router+glimmer"}}]
        self.assertEqual(render(states)["lines"], ["Helper: also paused email", "Helper: not reachable",
                                                  "Helper: off", "Helper: off", "Helper: no extra pauses"])

    @unittest.skipUnless(shutil.which("node"), "node missing")
    def test_plan_credit_does_not_hide_pauses_and_new_decision_clears_it(self):
        state = {"helper_visible": True, "helper": {"glimmer_status": "ready", "paused_by_glimmer": ["email"]},
                 "battery": {"why": "first decision"}}
        changed = {**state, "battery": {"why": "new goal decision"}}
        plan = {"order": ["dim screen"], "engine": "Muse Glimmer", "where": "on this device",
                "credit": "Muse Glimmer kept this order"}
        out = credit.TestContributionLine().render(events=[{"state": state}, {"plan": plan},
                                                         {"state": state}, {"state": changed}])
        self.assertEqual(out["eventLines"], ["Helper: also paused email",
                                            "Helper: also paused email; kept the plan order",
                                            "Helper: also paused email; kept the plan order",
                                            "Helper: also paused email"])


if __name__ == "__main__":
    unittest.main()
