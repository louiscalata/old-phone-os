# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import ipaddress
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock
import urllib.parse
from pathlib import Path
sys.path.insert(0, "src")
from opos import assist
from opos import muse
class FakeResp:
    def __init__(self, data):
        self._data = data
    def read(self, n=-1):
        if n == -1:
            return self._data
        return self._data[:n]
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
def get_url(req):
    try:
        return req.full_url
    except AttributeError:
        return req.get_full_url()
LAN_HOST = "192.0.2.10"
# The LAN route takes RFC 1918 hosts only (192.0.2.0/24 is a documentation range); built from octets.
RFC1918_HOST = ".".join(str(octet) for octet in (192, 168, 0, 10))
BUSY_RFC1918_HOST = ".".join(str(octet) for octet in (192, 168, 0, 20))
def models_reply(model_id):
    """The /models list of a local Llama server that serves model_id."""
    return FakeResp(json.dumps({"data": [{"id": model_id}]}).encode())
class TestFreeMb(unittest.TestCase):
    def test_reads_temp(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tf:
            tf.write("MemTotal: 4096 kB\nMemAvailable:    2097152 kB\n")
            name = tf.name
        try:
            self.assertEqual(assist.free_mb(name), 2048)
        finally:
            os.unlink(name)
    def test_missing(self):
        self.assertIsNone(assist.free_mb("/nonexistent/path/missing"))
class TestRecommend(unittest.TestCase):
    def test_none(self):
        self.assertEqual(assist.recommend(None), "llama-3.2-1b-instruct")
    def test_1000(self):
        self.assertIsNone(assist.recommend(1000))
    def test_2000(self):
        self.assertEqual(assist.recommend(2000), "llama-3.2-1b-instruct")
    def test_5000(self):
        self.assertEqual(assist.recommend(5000), "llama-3.2-3b-instruct")
class TestLocalRoute(unittest.TestCase):
    def test_ignores_non_loopback(self):
        env = {"OPOS_LOCAL_URL": "http://" + LAN_HOST + ":8080/v1"}
        r = assist.local_route(env, free=5000)
        self.assertIsNotNone(r)
        self.assertEqual(r.base_url, "http://127.0.0.1:8080/v1")
    def test_honours_loopback(self):
        env = {"OPOS_LOCAL_URL": "http://127.0.0.1:8080/v1"}
        r = assist.local_route(env, free=2000)
        self.assertEqual(r.base_url, "http://127.0.0.1:8080/v1")
    def test_none_when_low(self):
        self.assertIsNone(assist.local_route({}, free=1000))
    def test_free_none(self):
        self.assertIsNone(assist.local_route({}, free=None))
class TestLanRoute(unittest.TestCase):
    def test_accepts_private(self):
        url = "http://" + RFC1918_HOST + ":1234/v1"
        r = assist.lan_route({"OPOS_LAN_URL": url})
        self.assertIsNotNone(r)
        self.assertEqual(r.base_url, url)
    def test_rejects_public(self):
        self.assertIsNone(assist.lan_route({"OPOS_LAN_URL": "http://8.8.8.8:1234/v1"}))
    def test_rejects_hostname(self):
        self.assertIsNone(assist.lan_route({"OPOS_LAN_URL": "http://example.com:1234/v1"}))
    def test_rejects_https(self):
        url = "https://" + RFC1918_HOST + ":1234/v1"
        self.assertIsNone(assist.lan_route({"OPOS_LAN_URL": url}))
class TestSearch(unittest.TestCase):
    def test_parses(self):
        payload = {"query": {"search": [{"title": "Test Title", "snippet": "hello <b>world</b>"}, {"title": "A B", "snippet": "<span>x</span> y"}]}}
        def opener(req, timeout=8):
            url = get_url(req)
            self.assertIn("wikipedia", url)
            return FakeResp(json.dumps(payload).encode())
        res = assist.search("test", opener=opener)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["title"], "Test Title")
        self.assertEqual(res[0]["snippet"], "hello world")
        self.assertEqual(res[0]["url"], "https://en.wikipedia.org/wiki/Test_Title")
        self.assertEqual(res[1]["url"], "https://en.wikipedia.org/wiki/A_B")
    def test_error(self):
        def opener(req, timeout=8):
            raise OSError("fail")
        self.assertEqual(assist.search("q", opener=opener), [])
class TestAnswer(unittest.TestCase):
    def test_critical_mode_skips_models_and_web(self):
        calls = []
        def opener(req, timeout=None):
            calls.append(req)
            raise AssertionError("must not be called")
        res = assist.answer("what is paused?", web=True, env={}, opener=opener, free=5000, context={"mode": "CRITICAL"})
        self.assertEqual(calls, [])
        self.assertEqual(res["engine"], "rules")
        self.assertIs(res["ok"], False)
        self.assertTrue(res["text"].startswith("Battery is critical"))
        self.assertEqual(res["where"], "on this phone")
        self.assertEqual(res["sources"], [])
        self.assertEqual(res["text"], "Battery is critical, so the helper is resting to save power. Calls and messages are never paused. Ask again once the battery recovers or the phone is charging.")
        res = assist.answer("what is paused?", web=False, env={"OPOS_LAN_URL": "http://glimmer.test:1234"}, opener=opener, free=5000, context={"mode": "CRITICAL"})
        self.assertEqual(calls, [])
        self.assertEqual(res["engine"], "rules")
    def test_saver_mode_still_uses_models(self):
        calls = []
        chat = {"choices": [{"message": {"content": "hello model"}}]}
        def opener(req, timeout=None):
            calls.append(get_url(req))
            if "wikipedia" in get_url(req):
                return FakeResp(json.dumps({"query": {"search": []}}).encode())
            if get_url(req).endswith("/models"):
                return models_reply("llama-3.2-3b-instruct")
            return FakeResp(json.dumps(chat).encode())
        res = assist.answer("what is paused?", web=True, env={}, opener=opener, free=5000, context={"mode": "SAVER"})
        self.assertTrue(calls)
        self.assertEqual(len(calls), 3)
        self.assertIn("wikipedia", calls[0])
        self.assertTrue(calls[1].endswith("/models"), "the default Llama port is checked before it gets the question")
        self.assertTrue(calls[2].endswith("/chat/completions"))
        self.assertEqual(res["engine"], "Meta Llama 3.2 3B")
        self.assertIs(res["ok"], True)
        self.assertEqual(res["text"], "hello model")
    def test_web_returns_model_text_plus_sources(self):
        wiki = {"query": {"search": [{"title": "T1", "snippet": "s1 <b>b</b>"}, {"title": "T2", "snippet": "s2"}]}}
        chat = {"choices": [{"message": {"content": "hello model"}}]}
        def opener(req, timeout=30):
            url = get_url(req)
            if "wikipedia" in url:
                return FakeResp(json.dumps(wiki).encode())
            if url.endswith("/models"):
                return models_reply("llama-3.2-1b-instruct")
            if "chat/completions" in url:
                return FakeResp(json.dumps(chat).encode())
            raise OSError("unknown")
        res = assist.answer("what is test?", web=True, env={}, opener=opener, free=2000)
        self.assertTrue(res["ok"])
        self.assertEqual(res["text"], "hello model")
        self.assertEqual(len(res["sources"]), 2)
        self.assertEqual(res["engine"], "Meta Llama 3.2 1B")
    def test_hard_fallback(self):
        hard_q = " ".join(["word"] * 20)
        private = "http://" + RFC1918_HOST + ":1234/v1"
        env = {"OPOS_LAN_URL": private}
        chat_local = {"choices": [{"message": {"content": "local answer"}}]}
        def opener(req, timeout=30):
            url = get_url(req)
            if RFC1918_HOST in url:
                raise OSError("lan down")
            if "127.0.0.1:8080" in url and url.endswith("/models"):
                return models_reply("llama-3.2-3b-instruct")
            if "127.0.0.1:8080" in url:
                return FakeResp(json.dumps(chat_local).encode())
            raise OSError("unknown")
        res = assist.answer(hard_q, web=False, env=env, opener=opener, free=5000)
        self.assertTrue(res["ok"])
        self.assertEqual(res["text"], "local answer")
    def test_no_route_with_sources(self):
        wiki = {"query": {"search": [{"title": "T1", "snippet": "s1"}, {"title": "T2", "snippet": "s2"}]}}
        def opener(req, timeout=8):
            url = get_url(req)
            if "wikipedia" in url:
                return FakeResp(json.dumps(wiki).encode())
            raise OSError("no model")
        res = assist.answer("q", web=True, env={}, opener=opener, free=1000)
        self.assertTrue(res["ok"])
        self.assertEqual(res["engine"], "none")
        self.assertEqual(len(res["sources"]), 2)
        self.assertEqual(res["text"], "The helper did not answer, so here are the sources I found.")
    def test_no_route_no_sources_low_memory(self):
        def opener(req, timeout=30):
            raise OSError("down")
        res = assist.answer("hello?", web=False, env={}, opener=opener, free=1000)
        self.assertFalse(res["ok"])
        self.assertEqual(res["text"], "The helper did not answer. The phone has only about 1000 MB of free memory and the helper needs 1536 MB.")
    def test_empty(self):
        res = assist.answer("   ", web=False, env={}, free=2000)
        self.assertFalse(res["ok"])
        self.assertEqual(res["text"], "Ask a question.")
    def test_glimmer_fallback(self):
        chat = {"choices": [{"message": {"content": "hi"}}]}
        def opener(req, timeout=30):
            return FakeResp(json.dumps(chat).encode())
        res = assist.answer("hello there", web=False, env={}, free=None, opener=opener)
        self.assertTrue(res["ok"])
        self.assertEqual(res["engine"], "Muse Glimmer")
    def test_failed_routes_report_neutral_detail_with_lan_configured(self):
        env = {"OPOS_LAN_URL": "http://" + BUSY_RFC1918_HOST + ":1234/v1"}
        def opener(req, timeout=None):
            raise OSError("down")
        res = assist.answer("hello world", web=False, env=env, opener=opener, free=2000)
        self.assertFalse(res["ok"])
        self.assertTrue(res["text"].startswith("The helper did not answer."))
        self.assertEqual(res["engine"], "none")
        self.assertEqual(res["sources"], [])
    def test_no_model_reachable_no_lan(self):
        def opener(req, timeout=None):
            raise OSError("down")
        res = assist.answer("hello", web=False, env={}, opener=opener, free=None)
        self.assertFalse(res["ok"])
        self.assertTrue(res["text"].startswith("The helper did not answer."))
        self.assertIn("The battery rules still work.", res["text"])
    def test_unavailable_helper_explains_what_still_works(self):
        def opener(req, timeout=None):
            raise OSError("down")
        res = assist.answer("hello", web=False, env={}, opener=opener, free=None)
        expected = {"ok": False, "text": "The helper did not answer. The battery rules still work.", "engine": "none", "where": "", "sources": []}
        self.assertEqual(res, expected)
        self.assertNotIn("OPOS_LAN_URL", res["text"])
    def test_user_text_claims_only_what_is_true(self):
        """W1-08: no alarm app exists, so no helper text promises alarms; no model or OPOS words reach the user."""
        def opener(req, timeout=None):
            raise OSError("down")
        facts = {"why_paused": "Email is paused to save battery.", "time_left": "About 4 h left", "goal": "4 PM", "goal_met": True}
        texts = [assist.answer(q, web=False, env={}, opener=opener, free=f, context=c)["text"]
                 for q, f, c in (("hello", None, None), ("hello", 900, None), ("what is paused?", None, facts),
                                 ("how long will it last?", None, facts), ("what is paused?", 5000, {"mode": "CRITICAL"}))]
        texts.append(assist.rules_answer("why is email paused?", facts)["text"])
        self.assertIn("Email is paused to save battery. Calls and messages are never paused.", texts)
        for t in texts:
            with self.subTest(text=t):
                self.assertNotRegex(t, r"(?i)alarm|model|OPOS|AI|on-phone")
    def test_invalid_fact_dictionaries_return_controlled_error(self):
        circular = {}
        circular["self"] = circular
        cases = [{"b": 1, 1: 2}, circular, {"k": object()}]
        expected = {"ok": False, "text": "Invalid phone facts.", "engine": "none", "where": "", "sources": []}
        for case in cases:
            with self.subTest(context=str(sorted([str(type(v)) for v in case.values()]))):
                calls = []
                def opener(req, timeout=None):
                    calls.append(req)
                    raise OSError("must not be called")
                res = assist.answer("phone?", web=False, env={}, free=2000, context=case, opener=opener)
                self.assertEqual(res, expected)
                self.assertEqual(calls, [])
class TestSyntheticAddressFixtures(unittest.TestCase):
    def test_fixtures_use_documentation_range(self):
        text = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(text)
        names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        self.assertNotIn("_private_address", names)
        marker = chr(49) + chr(55) + chr(50)
        bad = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == marker]
        self.assertEqual(bad, [])
        doc = ipaddress.ip_network("192.0.2.0/24")
        consts = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        found = []
        for value in consts:
            found.extend(re.findall(r"\d+\.\d+\.\d+\.\d+", value))
        allowed = {"127.0.0.1", "8.8.8.8"}
        for addr in found:
            if addr in allowed:
                continue
            self.assertIn(ipaddress.ip_address(addr), doc, addr)
class TestNoSilentExceptPass(unittest.TestCase):
    def test_no_except_handler_with_only_pass(self):
        path = Path(__file__).resolve().parents[1] / "src" / "opos" / "assist.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        lines = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass)]
        self.assertEqual(lines, [], f"except-pass at lines {lines}")
class TestLaneCoreRegressions(unittest.TestCase):
    def test_explain_routes_fall_back_in_order(self):
        # Regression (lane D): explain_route() picked the optional Llama server without checking it
        # runs and Why/Plan tried only that one route. Why and Plan now get LAN first, then the on-phone
        # Llama, then Glimmer on this device.
        host = ".".join(str(octet) for octet in (192, 168, 0, 10))
        env = {"OPOS_LAN_URL": f"http://{host}:1234/v1"}
        keys = [r.key for r in assist.explain_routes(env=env, free=5000)]
        self.assertEqual(keys, ["lan", "local", "glimmer"])
        self.assertEqual([r.key for r in assist.explain_routes(env={}, free=5000)], ["local", "glimmer"])
        self.assertEqual([r.key for r in assist.explain_routes(env={}, free=None)], ["glimmer"])
        self.assertEqual(assist.explain_route(env=env, free=5000), assist.explain_routes(env=env, free=5000)[0])
    def test_answer_shares_one_deadline_across_routes(self):
        # Regression (lane D): 60 s for the Llama route plus 120 s for Glimmer overran the page's wait.
        chat = {"choices": [{"message": {"content": "hello"}}]}
        timeouts = []
        def opener(req, timeout=None):
            timeouts.append(timeout)
            if "127.0.0.1:8080" in get_url(req):
                raise OSError("llama down")
            return FakeResp(json.dumps(chat).encode())
        res = assist.answer("what is paused?", env={}, opener=opener, free=5000, context={"mode": "SAVER"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["engine"], "Muse Glimmer")
        self.assertEqual(len(timeouts), 2)
        self.assertLessEqual(timeouts[0], 60)
        self.assertLessEqual(timeouts[1], muse.HELPER_DEADLINE_S)
        self.assertLess(muse.HELPER_DEADLINE_S, 125)
        calls = []
        def late(req, timeout=None):
            calls.append(timeout)
            raise OSError("down")
        with mock.patch.object(assist.time, "monotonic", side_effect=[0.0, 0.0, muse.HELPER_DEADLINE_S + 1]):
            res = assist.answer("what is paused?", env={}, opener=late, free=5000, context={"mode": "SAVER"})
        self.assertEqual(len(calls), 1, "no route attempt starts after the shared deadline")
        self.assertFalse(res["ok"])
    def test_answer_prompt_points_at_why_paused(self):
        # Regression (lane B): with richer facts the model still needs to be told why_paused is the answer.
        seen = {}
        def opener(req, timeout=None):
            seen["prompt"] = json.loads(req.data.decode())["messages"][0]["content"]
            return FakeResp(json.dumps({"choices": [{"message": {"content": "Email is paused because the battery is worn."}}]}).encode())
        facts = {"mode": "SAVER", "paused": ["email"], "why_paused": "The battery is worn (60% health), so 30% charge is about 18% usable and it is not charging; to keep calls and messages working, the phone paused email."}
        res = assist.answer("Why is email paused?", env={}, opener=opener, free=None, context=facts)
        self.assertTrue(res["ok"])
        self.assertIn("why_paused fact is the reason", seen["prompt"])
        self.assertIn(facts["why_paused"], seen["prompt"])
    def test_answer_drops_invented_citations_without_sources(self):
        # Live check 2026-11-02: Glimmer ended a facts-only answer with "[1]" although no source was listed.
        chat = {"choices": [{"message": {"content": "Email is paused to save power [1]. Calls still work [2]."}}]}
        def opener(req, timeout=None):
            return FakeResp(json.dumps(chat).encode())
        res = assist.answer("Why is email paused?", env={}, opener=opener, free=None, context={"mode": "SAVER"})
        self.assertEqual(res["text"], "Email is paused to save power. Calls still work.")
        wiki = {"query": {"search": [{"title": "T1", "snippet": "s1"}]}}
        def opener_web(req, timeout=None):
            if "wikipedia" in get_url(req):
                return FakeResp(json.dumps(wiki).encode())
            return FakeResp(json.dumps(chat).encode())
        res = assist.answer("what is paused?", web=True, env={}, opener=opener_web, free=None)
        self.assertEqual(res["text"], "Email is paused to save power [1]. Calls still work [2].", "listed sources keep their citations")
if __name__ == "__main__":
    unittest.main()


class TestRestOnlyWhenBatteryIsLow(unittest.TestCase):
    def test_is_resting(self):
        self.assertTrue(assist.is_resting({"mode": "CRITICAL"}))
        self.assertTrue(assist.is_resting({"mode": "CRITICAL", "usable_percent": 9}))
        self.assertFalse(assist.is_resting({"mode": "CRITICAL", "usable_percent": 36}))
        self.assertFalse(assist.is_resting({"mode": "SAVER", "usable_percent": 5}))
        self.assertTrue(assist.is_resting({"mode": "CRITICAL", "usable_percent": True}))
        self.assertFalse(assist.is_resting(None))

    def test_goal_driven_critical_still_asks_the_model(self):
        calls = []
        def opener(req, timeout=None):
            calls.append(req.full_url)
            return FakeResp(json.dumps({"choices": [{"message": {"content": "Maps are paused to reach your goal."}}]}).encode())
        res = assist.answer("why are maps paused?", env={}, opener=opener, free=0, context={"mode": "CRITICAL", "usable_percent": 36})
        self.assertTrue(res["ok"])
        self.assertEqual(res["engine"], "Muse Glimmer")
        self.assertEqual(len(calls), 1)
        low = assist.answer("why are maps paused?", env={}, opener=opener, free=0, context={"mode": "CRITICAL", "usable_percent": 9})
        self.assertFalse(low["ok"])
        self.assertEqual(low["engine"], "rules")
        self.assertEqual(len(calls), 1)

