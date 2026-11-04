# SPDX-License-Identifier: GPL-3.0-or-later
"""Honesty and transport regression tests for the Meta helper."""
import io
import json
import unittest
from unittest import mock

from opos import assist, muse
from opos.advisor import GlimmerAdvisor
from opos.governor import PowerState
from opos.router import Router
from opos.runtime import DEFAULT_TASKS
from tests.test_duo_assist_grounding import FACTS_PREFIX


class CapturingOpener:
    def __init__(self, content="ok", *, model="muse-glimmer-30b", finish_reason="stop", error=None):
        self.content, self.model, self.finish_reason, self.error = content, model, finish_reason, error
        self.requests, self.timeouts = [], []
    def __call__(self, req, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        body = {"model": self.model, "choices": [{"message": {"content": self.content}, "finish_reason": self.finish_reason}]}
        return io.BytesIO(json.dumps(body).encode())


class MetaHelperHonestyTest(unittest.TestCase):
    def test_explain_fallback_uses_reason_and_safety_sentence(self):
        op = CapturingOpener(error=OSError("offline"))
        facts = {"why_paused": "The battery is worn (60% health), so 60% charge is about 36% usable and it is not charging; to keep calls and messages working until 16:00, the phone paused maps.", "paused": ["maps"]}
        out = muse.explain(facts, muse.glimmer_route({}), env={}, opener=op)
        self.assertTrue(out["text"].startswith(facts["why_paused"]))
        self.assertTrue(out["text"].endswith("Calls, messages and alarms always stay on."))
        self.assertEqual((out["engine"], out["where"], out["ok"]), ("rules", "on this device", False))

    def test_paused_empty_without_why_explains_nothing_paused(self):
        op = CapturingOpener(error=OSError("offline"))
        out = muse.explain({"paused": []}, muse.glimmer_route({}), env={}, opener=op)
        self.assertEqual(out["text"], "Calls, messages and alarms stay on; nothing is paused right now.")
        self.assertNotIn("paused to save battery", out["text"])

    def test_charging_why_preserves_reason_without_false_pause_claim(self):
        op = CapturingOpener(error=OSError("offline"))
        why = "The phone is charging, so nothing is paused, so everything is on."
        out = muse.explain({"paused": [], "why_paused": why}, muse.glimmer_route({}), env={}, opener=op)
        self.assertTrue(out["text"].startswith(why))
        self.assertTrue(out["text"].endswith("Calls, messages and alarms always stay on."))
        self.assertNotIn("paused to save battery", out["text"].lower())

    def test_invalid_why_uses_legacy_fallback(self):
        legacy = "Calls, messages and alarms stay on; other tasks are paused to save battery."
        for why in ("", "   ", None, 5):
            with self.subTest(why=why):
                op = CapturingOpener(error=OSError("offline"))
                out = muse.explain({"why_paused": why, "paused": ["maps"]}, muse.glimmer_route({}), env={}, opener=op)
                self.assertEqual(out["text"], legacy)

    def test_glimmer_pause_attribution_is_explicit(self):
        op = CapturingOpener(error=OSError("offline"))
        facts = {"why_paused": "Battery is low", "paused": ["email"], "paused_by_glimmer": ["email"]}
        out = muse.explain(facts, muse.glimmer_route({}), env={}, opener=op)
        self.assertIn("Muse Glimmer chose to pause email.", out["text"])

    def test_explain_prompt_states_attribution_and_preserves_plain_prompt(self):
        route = muse.glimmer_route({})
        op = CapturingOpener(content="Model explanation.")
        out = muse.explain({"paused": ["maps"], "paused_by_glimmer": ["maps"]}, route, env={}, opener=op)
        self.assertEqual(out, {"text": "Model explanation.", "engine": "Muse Glimmer", "where": "on this device", "ok": True})
        prompt = json.loads(op.requests[0].data)["messages"][0]["content"]
        self.assertIn("Muse Glimmer chose to pause those tasks", prompt)
        self.assertIn('"paused_by_glimmer": ["maps"]', prompt)
        op2 = CapturingOpener(content="Model explanation.")
        muse.explain({"paused": ["maps"]}, route, env={}, opener=op2)
        prompt = json.loads(op2.requests[0].data)["messages"][0]["content"]
        self.assertIn('"paused": ["maps"]', prompt)
        self.assertIn("four short sentences", prompt)
        self.assertIn("calls, messages and alarms stay on", prompt)
        self.assertNotIn("Muse Glimmer chose", prompt)

    def test_assist_grounding_includes_attribution(self):
        facts = {"paused": ["email"], "paused_by_glimmer": ["email"]}
        op = CapturingOpener(content="It chose email.")
        result = assist.answer("Why is email paused?", env={}, free=None, opener=op, context=facts)
        self.assertTrue(result["ok"])
        prompt = json.loads(op.requests[0].data)["messages"][0]["content"]
        self.assertTrue(prompt.startswith(FACTS_PREFIX))
        self.assertIn('"paused_by_glimmer": ["email"]', prompt)
        self.assertIn("Muse Glimmer chose to pause it", prompt)
        lines = prompt.splitlines()
        facts_line = next(i for i, line in enumerate(lines) if line.startswith("Facts about this phone"))
        self.assertEqual(lines[facts_line + 1], "If asked about a task listed in paused_by_glimmer, say that Muse Glimmer chose to pause it.")

    def test_probe_length_reports_token_budget_error(self):
        op = CapturingOpener(content="", finish_reason="length")
        out = muse.probe(env={}, opener=op)
        self.assertFalse(out["answered"])
        self.assertEqual(out["error"], "reasoning used the whole token budget")
        self.assertIsInstance(out["latency_s"], float)
        self.assertEqual(out["reply_model"], "muse-glimmer-30b")
        self.assertEqual(out["finish_reason"], "length")
        self.assertNotIn("reply", out)

    def test_probe_none_content_reports_empty_reply(self):
        op = CapturingOpener(content=None, finish_reason="stop")
        out = muse.probe(env={}, opener=op)
        self.assertFalse(out["answered"])
        self.assertEqual(out["error"], "empty reply")

    def test_probe_timeout_comes_from_environment(self):
        op = CapturingOpener()
        muse.probe(env={"OPOS_MUSE_TIMEOUT": "40"}, opener=op)
        self.assertEqual(op.timeouts, [40.0])

    def test_probe_truncates_reply_and_reported_model(self):
        op = CapturingOpener(content="x" * 200, model="z" * 500)
        out = muse.probe(env={}, opener=op)
        self.assertEqual(out["reply"], "x" * 60)
        self.assertEqual(out["reply_model"], "z" * 128)

    def test_probe_rejects_unterminated_reasoning(self):
        op = CapturingOpener(content="<think>hidden", finish_reason="stop")
        self.assertFalse(muse.probe(env={}, opener=op)["answered"])

    def test_payload_helper_matches_exact_ask_request_body(self):
        route = muse.glimmer_route({})
        op = CapturingOpener()
        muse.ask(route, "hi", env={}, opener=op, max_tokens=600)
        body = json.loads(op.requests[0].data)
        self.assertEqual(muse._payload(route, "hi", 600), body)
        legacy = {"model": route.model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1800, "temperature": 0.2, "reasoning_effort": "low"}
        self.assertEqual(op.requests[0].data, json.dumps(legacy).encode("utf-8"))
        self.assertEqual(body["max_tokens"], 1800)
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["temperature"], 0.2)

    def test_visible_reply_strips_reasoning_and_preserves_answer_after_last_close(self):
        route = muse.glimmer_route({})
        cases = {
            '<think>a {"defer":["maps"]}</think>OK': "OK",
            "notes</think>Answer": "Answer",
            "<think>one</think>  Answer  ": "Answer",
            "<think>one</think>middle<THINK>two</ThInK  >last": "last",
            "<think>secret": "",
            " </think>  <THINK>unfinished": "",
        }
        for content, expected in cases.items():
            with self.subTest(content=content):
                op = CapturingOpener(content=content)
                if expected:
                    self.assertEqual(muse.ask(route, "hi", env={}, opener=op), expected)
                else:
                    with self.assertRaisesRegex(muse.MuseError, "^empty response$"):
                        muse.ask(route, "hi", env={}, opener=op)
        for content in (None, 4, "   "):
            with self.subTest(content=content), self.assertRaisesRegex(muse.MuseError, "^empty response$"):
                muse.ask(route, "hi", env={}, opener=CapturingOpener(content=content))

    def test_advisor_ignores_hidden_defer_and_uses_visible_json(self):
        op = CapturingOpener(content='<think>{"defer":["email"]}</think>{"defer":[]}')
        def ask(route, prompt, **kwargs):
            return muse.ask(route, prompt, env={}, opener=op, **kwargs)
        captured = []
        advisor = GlimmerAdvisor(Router(visible=True), route=muse.glimmer_route({}), ask=ask,
                                 spawn=captured.append, clock=lambda: 0)
        initial = advisor(PowerState(90, False, 1, 25), DEFAULT_TASKS)
        self.assertIn("email", initial.allowed)
        self.assertEqual(len(captured), 1)
        captured.pop()()
        self.assertEqual(advisor.down_until, 0)
        self.assertEqual(list(advisor.cache.values()), [()])
        decision = advisor(PowerState(90, False, 1, 25), DEFAULT_TASKS)
        self.assertIn("email", decision.allowed)
        self.assertNotIn("email", decision.deferred)
        self.assertEqual(len(op.requests), 1)

    def test_fallback_trims_and_caps_reason_before_safety_sentence(self):
        why = "  " + "reason " * 100 + "  "
        out = muse.explain({"why_paused": why}, muse.glimmer_route({}), env={},
                           opener=CapturingOpener(error=OSError("offline")))
        self.assertEqual(out["text"], why.strip()[:500] + " Calls, messages and alarms always stay on.")

    def test_fallback_ignores_malformed_attribution_and_paused_values(self):
        for names in (None, "email", 5, [], ["email", None], [5]):
            with self.subTest(names=names):
                out = muse.explain({"why_paused": "Reason.", "paused_by_glimmer": names},
                                   muse.glimmer_route({}), env={}, opener=CapturingOpener(error=OSError("offline")))
                self.assertEqual(out["text"], "Reason. Calls, messages and alarms always stay on.")
        for paused in (None, "", 0, {}, ()):
            with self.subTest(paused=paused):
                out = muse.explain({"paused": paused}, muse.glimmer_route({}), env={},
                                   opener=CapturingOpener(error=OSError("offline")))
                self.assertEqual(out["text"], "Calls, messages and alarms stay on; other tasks are paused to save battery.")

    def test_attribution_prompt_requires_nonempty_list(self):
        for names in (None, "email", 5, []):
            with self.subTest(names=names):
                facts = {"paused_by_glimmer": names}
                op = CapturingOpener()
                muse.explain(facts, muse.glimmer_route({}), env={}, opener=op)
                prompt = json.loads(op.requests[0].data)["messages"][0]["content"]
                self.assertNotIn("Muse Glimmer chose", prompt)
                op = CapturingOpener()
                assist.answer("Why is email paused?", env={}, free=None, context=facts, opener=op)
                prompt = json.loads(op.requests[0].data)["messages"][0]["content"]
                self.assertNotIn("Muse Glimmer chose", prompt)

    def test_probe_success_reports_visible_text_and_default_timeout(self):
        op = CapturingOpener(content="<think>hidden</think> OK ")
        out = muse.probe(env={}, opener=op)
        self.assertEqual(out, {"answered": True, "latency_s": mock.ANY, "reply": "OK",
                               "finish_reason": "stop", "reply_model": "muse-glimmer-30b"})
        self.assertIsInstance(out["latency_s"], float)
        self.assertEqual(op.timeouts, [90.0])
        self.assertEqual(json.loads(op.requests[0].data), {
            "model": "muse-glimmer-30b", "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 1800, "reasoning_effort": "low", "temperature": 0})

    def test_probe_latency_includes_opener_and_capped_read(self):
        events = []
        times = iter((10.0, 11.26))
        def clock():
            events.append("clock")
            return next(times)
        class Response:
            def read(self, cap):
                events.append("read")
                if cap != muse.MAX_RESPONSE_BYTES + 1:
                    raise AssertionError("uncapped response")
                return b'{"choices": [{"message": {"content": "OK"}}]}'
        def opener(req, timeout=None):
            events.append("open")
            return Response()
        with mock.patch.object(muse.time, "monotonic", side_effect=clock):
            out = muse.probe(env={}, opener=opener)
        self.assertEqual(events, ["clock", "open", "read", "clock"])
        self.assertEqual(out["latency_s"], 1.3)

    def test_probe_metadata_types_and_malformed_choices(self):
        out = muse.probe(env={}, opener=CapturingOpener(content="OK", finish_reason=5, model=[]))
        self.assertTrue(out["answered"])
        self.assertIsNone(out["finish_reason"])
        self.assertIsNone(out["reply_model"])
        for choice in (None, [], {"message": None}, {"message": []}):
            with self.subTest(choice=choice):
                def opener(req, timeout=None):
                    return io.BytesIO(json.dumps({"choices": [choice]}).encode())
                self.assertEqual(muse.probe(env={}, opener=opener), {"answered": False, "error": "bad response"})

    def test_fact_keys_include_glimmer_attribution(self):
        self.assertIn("paused_by_glimmer", muse.FACT_KEYS)
        self.assertEqual(muse.facts({"paused_by_glimmer": ["email"], "private": "omit"}), {"paused_by_glimmer": ["email"]})


if __name__ == "__main__":
    unittest.main()
