# SPDX-License-Identifier: GPL-3.0-or-later
"""Follow-up regressions for the Meta helper: hidden reasoning, cut-off replies, attribution and transport."""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import unittest
import urllib.request
from unittest import mock

from opos import advisor as advisor_mod, assist, muse
from opos.advisor import GlimmerAdvisor
from opos.governor import PowerState, Priority
from opos.router import Router
from opos.runtime import DEFAULT_TASKS
from tests.test_security_fixes import response

DEEP = "[" * 1000000  # deep enough that json raises RecursionError


class Opener:
    def __init__(self, content="ok", finish_reason="stop", error=None):
        self.content, self.finish_reason, self.error = content, finish_reason, error
        self.requests, self.timeouts = [], []
    def __call__(self, req, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        body = {"model": "muse-glimmer-30b", "choices": [{"message": {"content": self.content}, "finish_reason": self.finish_reason}]}
        return io.BytesIO(json.dumps(body).encode())


class BrokenRead:
    """A response whose body ends early, as http.client reports it."""
    def read(self, n=-1):
        raise http.client.IncompleteRead(b"{")
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def run_advisor(ask):
    queued = []
    adv = GlimmerAdvisor(Router(visible=True), route=muse.glimmer_route({}), ask=ask,
                         spawn=queued.append, clock=lambda: 0, backoff=60.0)
    state = PowerState(90, False, 1, 25)
    adv(state, DEFAULT_TASKS)
    queued.pop()()
    return adv, adv(state, DEFAULT_TASKS)


def prompt_of(opener):
    return json.loads(opener.requests[0].data)["messages"][0]["content"]


class AdvisorStatusTest(unittest.TestCase):
    def test_idle_without_optional_work_is_ready(self):
        for state in (None, PowerState(5, False, 1, 25), PowerState(90, False, 1, 45)):
            with self.subTest(state=state):
                queued = []
                adv = GlimmerAdvisor(Router(visible=True), route=muse.glimmer_route({}),
                                     spawn=queued.append, clock=lambda: 0)
                if state is not None:
                    decision = adv(state, DEFAULT_TASKS)
                    self.assertEqual(set(decision.allowed),
                                     {t.name for t in DEFAULT_TASKS if t.priority == Priority.ESSENTIAL})
                self.assertEqual(queued, [])
                self.assertEqual(adv.pending, set())
                self.assertEqual(adv.cache, {})
                self.assertEqual(adv.status(), "ready")

    def test_waiting_tracks_inflight_work_even_with_cached_reply(self):
        queued = []
        adv = GlimmerAdvisor(Router(visible=True), route=muse.glimmer_route({}),
                             ask=lambda *a, **k: '{"defer":[]}',
                             spawn=queued.append, clock=lambda: 0)
        for level in (90, 100):
            adv(PowerState(level, False, 1, 25), DEFAULT_TASKS)
            self.assertEqual(len(queued), 1)
            self.assertEqual(adv.status(), "waiting")
            queued.pop()()
            self.assertEqual(adv.status(), "ready")
        self.assertEqual(len(adv.cache), 2)

    def test_failed_reply_stays_visible_until_backoff_expires_then_is_idle(self):
        now, queued = [0], []
        def ask(*a, **k):
            raise muse.MuseError("no response")
        adv = GlimmerAdvisor(Router(visible=True), route=muse.glimmer_route({}), ask=ask,
                             spawn=queued.append, clock=lambda: now[0], backoff=60)
        adv(PowerState(90, False, 1, 25), DEFAULT_TASKS)
        queued.pop()()
        now[0] = 59
        self.assertEqual(adv.status(), "no reply")
        now[0] = 60
        self.assertEqual(adv.pending, set())
        self.assertEqual(adv.cache, {})
        self.assertEqual(adv.status(), "ready")


class UnclosedThinkTest(unittest.TestCase):
    HIDDEN = 'Note <think>{"defer":["email"]}'

    def test_visible_drops_unclosed_think_after_text(self):
        for content, expected in ((self.HIDDEN, "Note"), ('Note <THINK >{"defer":["email"]}', "Note"),
                                  ("<think>a</think>Answer <think>more", "Answer"),
                                  ("<think>a</think>Answer", "Answer")):
            with self.subTest(content=content):
                self.assertEqual(muse._visible(content), expected)
                self.assertEqual(muse.ask(muse.glimmer_route({}), "hi", env={}, opener=Opener(content)), expected)

    def test_probe_reply_never_shows_hidden_reasoning(self):
        out = muse.probe(env={}, opener=Opener(self.HIDDEN))
        self.assertEqual(out["reply"], "Note")

    def test_advisor_never_parses_hidden_reasoning(self):
        op = Opener(self.HIDDEN)
        adv, decision = run_advisor(lambda route, prompt, **kw: muse.ask(route, prompt, env={}, opener=op, **kw))
        self.assertIn("email", decision.allowed)
        self.assertNotIn(("email",), adv.cache.values())


class CutOffReplyTest(unittest.TestCase):
    PARTIAL = "Okay, the user wants me to reply with OK. Let me think about whether"

    def test_probe_length_with_partial_text_is_not_answered(self):
        out = muse.probe(env={}, opener=Opener(self.PARTIAL, finish_reason="length"))
        self.assertEqual(out, {"answered": False, "latency_s": mock.ANY, "finish_reason": "length",
                               "reply_model": "muse-glimmer-30b", "reply": self.PARTIAL[:60],
                               "error": "reasoning used the whole token budget"})
        self.assertIsInstance(out["latency_s"], float)

    def test_probe_length_with_nonstring_content_is_bad_response(self):
        for content in (None, [{"type": "text", "text": "OK"}], 5):
            with self.subTest(content=content):
                out = muse.probe(env={}, opener=Opener(content, finish_reason="length"))
                self.assertFalse(out["answered"])
                self.assertEqual(out["error"], "bad response")
                self.assertEqual(out["finish_reason"], "length")

    def test_ask_rejects_a_reply_cut_off_at_the_token_budget(self):
        op = Opener('The tasks are email, maps. Maybe {"defer": ["email"]} but', finish_reason="length")
        with self.assertRaises(muse.MuseError):
            muse.ask(muse.glimmer_route({}), "hi", env={}, opener=op)

    def test_advisor_ignores_cut_off_reply_and_backs_off(self):
        op = Opener('x {"defer":["email"]} y', finish_reason="length")
        adv, decision = run_advisor(lambda route, prompt, **kw: muse.ask(route, prompt, env={}, opener=op, **kw))
        self.assertIn("email", decision.allowed)
        self.assertEqual(adv.down_until, 60.0)
        self.assertEqual(adv.cache, {})


class CheckHintTest(unittest.TestCase):
    GOOD = {"route": "Muse Glimmer, on this device", "model": "muse-glimmer-30b", "endpoint": "http://127.0.0.1:1234/v1",
            "reachable": True, "served": ["muse-glimmer-30b"], "model_served": True}

    def run_main(self, check_result, probe_result):
        out = io.StringIO()
        with mock.patch.object(muse, "check", return_value=dict(check_result)), \
                mock.patch.object(muse, "probe", return_value=probe_result), contextlib.redirect_stdout(out):
            code = muse.main(["muse", "--check", "--probe"])
        return code, json.loads(out.getvalue())

    def test_no_visible_text_gets_its_own_hint(self):
        for error in ("empty reply", "reasoning used the whole token budget"):
            with self.subTest(error=error):
                code, printed = self.run_main(self.GOOD, {"answered": False, "error": error})
                self.assertEqual(code, 1)
                self.assertEqual(printed["hint"], muse.NO_TEXT_HINT)
                self.assertIn("answered", printed["hint"])
                self.assertNotIn("Serve the model", printed["hint"])

    def test_other_failures_keep_the_serve_hint(self):
        for check_result, error in ((self.GOOD, "HTTP 502"), (self.GOOD, "bad response"),
                                    (dict(self.GOOD, model_served=False), "empty reply")):
            with self.subTest(error=error, served=check_result["model_served"]):
                code, printed = self.run_main(check_result, {"answered": False, "error": error})
                self.assertEqual(code, 1)
                self.assertEqual(printed["hint"], muse.CHECK_HINT)


class GlimmerAttributionTest(unittest.TestCase):
    def explain_offline(self, facts):
        return muse.explain(facts, muse.glimmer_route({}), env={}, opener=Opener(error=OSError("offline")))["text"]

    def test_names_only_tasks_the_facts_list_as_paused(self):
        why = "The phone is charging, so nothing is paused, so everything is on."
        text = self.explain_offline({"why_paused": why, "paused": [], "paused_by_glimmer": ["maps"]})
        self.assertEqual(text, why + " Calls, messages and alarms always stay on.")

    def test_never_names_calls_messages_alarms_or_blank_names(self):
        text = self.explain_offline({"why_paused": "Reason.", "paused": ["calls", "email", "Alarms", " "],
                                     "paused_by_glimmer": ["calls", "email", "Alarms", " ", "maps"]})
        self.assertEqual(text, "Reason. Muse Glimmer chose to pause email. Calls, messages and alarms always stay on.")

    def test_joined_names_are_capped(self):
        names = ["task %03d %s" % (i, "x" * 30) for i in range(40)]
        text = self.explain_offline({"why_paused": "Reason.", "paused": names, "paused_by_glimmer": names})
        part = text[len("Reason. Muse Glimmer chose to pause "):text.index(". Calls")]
        self.assertLessEqual(len(part), muse.GLIMMER_NAMES_CAP + len(" and other tasks"))
        self.assertTrue(part.endswith(" and other tasks"))
        self.assertTrue(part.startswith(names[0] + ", "))

    def test_prompts_use_the_same_filter_and_add_no_purpose(self):
        bad = ({"paused": [], "paused_by_glimmer": ["maps"]}, {"paused": ["calls"], "paused_by_glimmer": ["calls"]},
               {"paused_by_glimmer": [5]}, {"paused_by_glimmer": [""]})
        for facts in bad:
            with self.subTest(facts=facts):
                op = Opener("Model explanation.")
                muse.explain(facts, muse.glimmer_route({}), env={}, opener=op)
                self.assertNotIn("Muse Glimmer chose", prompt_of(op))
                op = Opener("It chose.")
                assist.answer("Why is maps paused?", env={}, free=None, context=facts, opener=op)
                self.assertNotIn("Muse Glimmer chose", prompt_of(op))
        op = Opener("Model explanation.")
        muse.explain({"paused": ["maps"], "paused_by_glimmer": ["maps"]}, muse.glimmer_route({}), env={}, opener=op)
        self.assertIn(" If a paused_by_glimmer fact is present, also say that Muse Glimmer chose to pause those tasks.", prompt_of(op))
        self.assertNotIn("to protect", prompt_of(op))


class TransportErrorTest(unittest.TestCase):
    def test_ask_turns_http_client_errors_into_muse_error(self):
        for opener in (Opener(error=http.client.BadStatusLine("x")), lambda req, timeout=None: BrokenRead()):
            with self.subTest(opener=opener), self.assertRaisesRegex(muse.MuseError, "^request failed$"):
                muse.ask(muse.glimmer_route({}), "hi", env={}, opener=opener)

    def test_why_falls_back_to_rules_on_http_client_error(self):
        out = muse.explain({"paused": []}, muse.glimmer_route({}), env={}, opener=Opener(error=http.client.BadStatusLine("x")))
        self.assertEqual(out["engine"], "rules")

    def test_advisor_worker_backs_off_on_http_client_error(self):
        def ask(route, prompt, **kw):
            raise http.client.IncompleteRead(b"")
        adv, decision = run_advisor(ask)
        self.assertEqual(adv.down_until, 60.0)
        self.assertEqual(adv.pending, set())
        self.assertIn("email", decision.allowed)

    def test_advisor_worker_backs_off_on_deep_json(self):
        adv, decision = run_advisor(lambda route, prompt, **kw: '{"defer": ' + DEEP)
        self.assertEqual(adv.down_until, 60.0)
        self.assertIn("email", decision.allowed)

    def test_plan_keeps_fixed_order_on_deep_json(self):
        res = muse.plan({}, ("a", "b"), muse.glimmer_route({}), env={}, opener=Opener(DEEP))
        self.assertEqual(res, ["a", "b"])
        self.assertIsNone(res.route)

    def test_search_returns_no_sources_on_http_client_error(self):
        self.assertEqual(assist.search("q", opener=lambda req, timeout=None: BrokenRead()), [])
        self.assertEqual(assist.search("q", opener=Opener(error=http.client.BadStatusLine("x"))), [])
        # Python 3.11 to 3.13 raise RecursionError for deep JSON well inside the 64 KB read.
        with mock.patch.object(assist.json, "loads", side_effect=RecursionError):
            self.assertEqual(assist.search("q", opener=Opener()), [])


class RouteBudgetTest(unittest.TestCase):
    LOCAL = muse.Route("local", "Meta Llama 3.2 1B", "on this phone", "http://127.0.0.1:8080/v1", "llama-3.2-1b-instruct")

    def test_last_route_gets_all_remaining_time(self):
        with mock.patch.object(muse.time, "monotonic", return_value=0):
            self.assertEqual(list(muse._attempts([muse.glimmer_route({})], {}, 115)), [(muse.glimmer_route({}), 115)])
            self.assertEqual(list(muse._attempts([muse.glimmer_route({})], {"OPOS_MUSE_TIMEOUT": "30"}, 115))[0][1], 30)
            first, last = muse._attempts([self.LOCAL, muse.glimmer_route({})], {}, 115)
        self.assertEqual(first[1], 115 / 2)  # an earlier route still shares the budget
        self.assertEqual(last[1], 115)


class ParseDeferTest(unittest.TestCase):
    def test_keeps_the_last_defer_list(self):
        self.assertEqual(advisor_mod._parse_defer('{"defer":["email"]} {"defer":[]}'), [])
        self.assertEqual(advisor_mod._parse_defer('{"defer":[]} then {"defer":["email"]}'), ["email"])

    def test_nested_defer_does_not_override_its_parent(self):
        self.assertEqual(advisor_mod._parse_defer('{"defer":["email"],"note":{"defer":[]}}'), ["email"])


@contextlib.contextmanager
def https_transport(reply):
    """Keep urllib routing real (proxies, redirects), replacing only the HTTPS socket transport."""
    calls = []
    def open_https(_handler, _connection, req, **_kw):
        calls.append(req)
        return reply(req)
    env = {"https_proxy": "http://proxy.example:8080", "no_proxy": ""}
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(urllib.request, "_opener", None), \
            mock.patch.object(urllib.request, "proxy_bypass", return_value=False), \
            mock.patch.object(urllib.request.HTTPSHandler, "do_open", autospec=True, side_effect=open_https):
        yield calls


class WikipediaTransportTest(unittest.TestCase):
    WIKI = {"query": {"search": [{"title": "Battery", "snippet": "Stored energy"}]}}

    def test_search_ignores_environment_proxy(self):
        with https_transport(lambda req: response(req, self.WIKI)) as calls:
            out = assist.search("PRIVATE QUESTION")
        self.assertEqual(out[0]["title"], "Battery")
        self.assertEqual([req.host for req in calls], ["en.wikipedia.org"])
        self.assertIn("srsearch=PRIVATE+QUESTION", calls[0].full_url)

    def test_search_does_not_follow_redirects(self):
        target = "https://redirect.example/result"
        def reply(req):
            if req.full_url == target:
                return response(req, self.WIKI)
            return response(req, {}, 302, target)
        with https_transport(reply) as calls:
            out = assist.search("What is a battery?")
        self.assertEqual(out, [])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].full_url.startswith("https://en.wikipedia.org/w/api.php?"))


DOCS = Path(__file__).resolve().parents[1] / "docs"


class HelperDocsTest(unittest.TestCase):
    def test_meta_worn_hours_read_as_before_and_after_pairs(self):
        line = next(l for l in (DOCS / "META.md").read_text(encoding="utf-8").splitlines() if l.startswith("Worn-battery hours"))
        self.assertIn("averaged over 40 simulated worn phones, without and then with OPOS's worn-battery reserve", line)
        self.assertNotRegex(line, r"\d to \d")
        self.assertIn("router 5.52 and 6.54", line)

    def test_meta_probe_timeout_matches_code(self):
        text = (DOCS / "META.md").read_text(encoding="utf-8")
        self.assertIn("a 90 s timeout, or the helper timeout when that is lower", text)
        self.assertIn("held between 5 and 600; when it is unset or invalid it is 120 s", text)
        self.assertEqual((muse.timeout_s({"OPOS_MUSE_TIMEOUT": "1"}), muse.timeout_s({"OPOS_MUSE_TIMEOUT": "9999"}),
                          muse.timeout_s({"OPOS_MUSE_TIMEOUT": "x"}), muse.timeout_s({})), (5.0, 600.0, 120.0, 120.0))
        for value, expected in (("9999", 90.0), ("40", 40.0), ("1", 5.0), ("x", 90.0)):
            op = Opener("OK")
            muse.probe(env={"OPOS_MUSE_TIMEOUT": value}, opener=op)
            self.assertEqual(op.timeouts, [expected], value)

    def test_docs_say_what_wikipedia_search_sends(self):
        seen = []
        def opener(req, timeout=None):
            seen.append((req, timeout))
            return io.BytesIO(b'{"query": {"search": []}}')
        assist.search("battery", opener=opener)
        req, timeout = seen[0]
        doc = (DOCS / "ai-tools.md").read_text(encoding="utf-8")
        for pair in req.full_url.split("?", 1)[1].split("&"):
            if not pair.startswith("srsearch="):
                self.assertIn("`" + pair + "`", doc)
        self.assertIn("`" + req.full_url.split("?", 1)[0] + "`", doc)
        self.assertIn("`User-Agent: " + req.get_header("User-agent") + "`", doc)
        self.assertIn("%d-second network timeout" % timeout, doc)
        self.assertIn("ignores `HTTP_PROXY`, `HTTPS_PROXY` and system proxy settings and does not follow redirects", doc)
        meta = (DOCS / "META.md").read_text(encoding="utf-8")
        self.assertNotIn("Wikipedia search uses urllib's default redirect and proxy handling", meta)
        self.assertIn("Wikipedia search and model requests ignore HTTP_PROXY", meta)


if __name__ == "__main__":
    unittest.main()
