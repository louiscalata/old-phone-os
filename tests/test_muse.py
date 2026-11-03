# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import http.server
import json
import re
import socket
import sys
import pathlib
import threading
import time
import unittest
import urllib.parse
import urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from unittest import mock
import opos.muse as muse_mod
from opos.muse import Route, glimmer_route, SPARK_OPEN, choose, is_hard, label, MuseError, ask, FACT_KEYS, facts, explain, plan, check


PRIVATE_TEST_HOST = "192.0.2.10"


class FakeResponse:
    """Fake HTTP response."""
    def __init__(self, data):
        self._data = data
        self.asked = None
    def read(self, n=-1):
        self.asked = n
        from opos.muse import MAX_RESPONSE_BYTES
        if n is None or n < 0 or n > MAX_RESPONSE_BYTES + 1:
            raise AssertionError("uncapped read")
        return self._data[:n]
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
class FakeOpener:
    """Record requests and return canned response."""
    def __init__(self, response=None, exc=None):
        self.requests = []
        self.response = response
        self.exc = exc
    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.exc:
            raise self.exc
        return self.response
def header(req, name):
    """Get header case-insensitive."""
    v = req.get_header(name)
    if v is not None:
        return v
    for k, v in req.headers.items():
        if k.lower() == name.lower():
            return v
    return None
def make_ask_opener(content):
    """Opener returning ask content."""
    data = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    return FakeOpener(FakeResponse(data))
class TestMuse(unittest.TestCase):
    """Verify Muse routing and helpers."""
    def test_choose_default(self):
        """Choose returns glimmer by default."""
        self.assertEqual(choose(False, {}).key, "glimmer")
        self.assertEqual(choose(True, {}).key, "glimmer")
        self.assertEqual(choose(True, {"OPOS_SPARK_OPEN": "1"}).key, "glimmer")
        self.assertEqual(choose(True, {"OPOS_SPARK_OPEN": "1", "BASETEN_API_KEY": ""}).key, "glimmer")
        self.assertEqual(choose(False, {"OPOS_SPARK_OPEN": "1", "BASETEN_API_KEY": "k"}).key, "glimmer")
    def test_choose_spark(self):
        """Choose returns spark only with all conditions."""
        env = {"OPOS_SPARK_OPEN": "1", "BASETEN_API_KEY": "secret"}
        self.assertEqual(choose(True, env).key, "spark-open")
        self.assertEqual(choose(True, env).model, SPARK_OPEN.model)
    def test_glimmer_url_non_loopback_ignored(self):
        """Non-loopback URL is ignored."""
        env = {"OPOS_GLIMMER_URL": "http://evil.com/v1"}
        self.assertEqual(glimmer_route(env).base_url, "http://127.0.0.1:1234/v1")
        env2 = {"OPOS_GLIMMER_URL": "https://127.0.0.1:1234/v1"}
        self.assertEqual(glimmer_route(env2).base_url, "http://127.0.0.1:1234/v1")
        env3 = {"OPOS_GLIMMER_URL": "http://example.org:8000/v1"}
        self.assertEqual(glimmer_route(env3).base_url, "http://127.0.0.1:1234/v1")
    def test_glimmer_url_loopback_allowed(self):
        """Loopback URL is used."""
        env = {"OPOS_GLIMMER_URL": "http://127.0.0.1:9999/v1"}
        self.assertEqual(glimmer_route(env).base_url, "http://127.0.0.1:9999/v1")
        env2 = {"OPOS_GLIMMER_URL": "http://localhost:1234/v1"}
        self.assertEqual(glimmer_route(env2).base_url, "http://localhost:1234/v1")
    def test_is_hard(self):
        """is_hard cases."""
        self.assertFalse(is_hard(None, 5))
        self.assertFalse(is_hard(10, None))
        self.assertTrue(is_hard(10, 5))
        self.assertFalse(is_hard(10, 10))
        self.assertFalse(is_hard(10, 15))
    def test_facts(self):
        """facts drops unknown keys."""
        d = {"mode": "a", "allowed": ["x"], "paused": [], "hours_left": 5, "reserve": 1, "goal_hours": 10, "contacts": "secret", "messages": "hi"}
        f = facts(d)
        self.assertEqual(set(f.keys()), set(k for k in FACT_KEYS if k in d))
        self.assertNotIn("contacts", f)
        self.assertNotIn("messages", f)
        self.assertEqual(f["mode"], "a")
    def test_ask_glimmer(self):
        """Ask sends correct URL/model/headers for glimmer."""
        route = glimmer_route({})
        opener = make_ask_opener(" hello ")
        env = {}
        res = ask(route, "hi", env=env, opener=opener)
        self.assertEqual(res, "hello")
        req = opener.requests[0]
        self.assertEqual(req.full_url, route.base_url + "/chat/completions")
        body = json.loads(req.data.decode())
        self.assertEqual(body["model"], route.model)
        self.assertEqual(body["messages"][0]["content"], "hi")
        self.assertEqual(body["max_tokens"], 1800)
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(header(req, "Content-Type"), "application/json")
        self.assertIsNone(header(req, "Authorization"))
    def test_ask_spark(self):
        """Ask sends Api-Key header for spark."""
        route = SPARK_OPEN
        env = {"BASETEN_API_KEY": "mykey123"}
        opener = make_ask_opener("ok")
        res = ask(route, "prompt", env=env, opener=opener)
        self.assertEqual(res, "ok")
        req = opener.requests[0]
        self.assertEqual(req.full_url, route.base_url + "/chat/completions")
        body = json.loads(req.data.decode())
        self.assertEqual(body["model"], route.model)
        self.assertEqual(header(req, "Authorization"), "Api-Key mykey123")
        self.assertEqual(header(req, "Content-Type"), "application/json")
    def test_ask_headroom(self):
        """Glimmer adds headroom, spark-open does not."""
        glimmer = glimmer_route({})
        opener = make_ask_opener("ok")
        ask(glimmer, "hi", env={}, opener=opener)
        body = json.loads(opener.requests[0].data.decode())
        self.assertEqual(body["max_tokens"], 1800)
        spark = SPARK_OPEN
        opener2 = make_ask_opener("ok")
        ask(spark, "hi", env={"BASETEN_API_KEY": "k"}, opener=opener2)
        body2 = json.loads(opener2.requests[0].data.decode())
        self.assertEqual(body2["max_tokens"], 600)

    def test_ask_error_no_leak(self):
        """Ask error never contains key."""
        route = SPARK_OPEN
        env = {"BASETEN_API_KEY": "supersecretkey123"}
        opener = FakeOpener(exc=OSError("fail"))
        try:
            ask(route, "hi", env=env, opener=opener)
            self.fail("expected MuseError")
        except MuseError as e:
            self.assertNotIn("supersecretkey123", str(e))
        opener2 = FakeOpener(FakeResponse(json.dumps({"choices": [{"message": {"content": "   "}}]}).encode()))
        try:
            ask(route, "hi", env=env, opener=opener2)
            self.fail("expected MuseError")
        except MuseError as e:
            self.assertNotIn("supersecretkey123", str(e))
    def test_explain_fallback(self):
        """Explain fallback on error."""
        route = glimmer_route({})
        opener = FakeOpener(exc=OSError("down"))
        out = explain({"mode": "a"}, route, env={}, opener=opener)
        self.assertEqual(out["text"], "Calls, messages and alarms stay on; other tasks are paused to save battery.")
        self.assertEqual(out["engine"], "rules")
        self.assertEqual(out["where"], "on this device")
        self.assertFalse(out["ok"])
    def test_explain_success(self):
        """Explain returns model answer on success."""
        route = glimmer_route({})
        opener = make_ask_opener("Battery low so pausing.")
        out = explain({"mode": "low"}, route, env={}, opener=opener)
        self.assertEqual(out["text"], "Battery low so pausing.")
        self.assertEqual(out["engine"], route.name)
        self.assertEqual(out["where"], route.where)
        self.assertTrue(out["ok"])
    def test_plan_reorders(self):
        """Plan reorders steps."""
        route = glimmer_route({})
        opener = make_ask_opener('["c","a"]')
        steps = ("a", "b", "c")
        res = plan({"goal_hours": 5}, steps, route, env={}, opener=opener)
        self.assertEqual(res, ["c", "a", "b"])
    def test_plan_rejects_unknown(self):
        """A candidate containing unknown names is not a usable step order."""
        route = glimmer_route({})
        opener = make_ask_opener('["c","unknown","a"]')
        steps = ("a", "b", "c")
        res = plan({}, steps, route, env={}, opener=opener)
        self.assertEqual(res, ["a", "b", "c"])
        self.assertIsNone(res.route)
        self.assertNotIn("unknown", res)
    def test_plan_appends_omitted(self):
        """Plan appends omitted in original order."""
        route = glimmer_route({})
        opener = make_ask_opener('["b"]')
        steps = ("a", "b", "c")
        res = plan({}, steps, route, env={}, opener=opener)
        self.assertEqual(res, ["b", "a", "c"])
    def test_plan_dedup(self):
        """Plan removes duplicates."""
        route = glimmer_route({})
        opener = make_ask_opener('["b","b","a"]')
        steps = ("a", "b", "c")
        res = plan({}, steps, route, env={}, opener=opener)
        self.assertEqual(res, ["b", "a", "c"])
    def test_plan_fallback_unparsable(self):
        """Plan falls back on unparsable answer."""
        route = glimmer_route({})
        opener = make_ask_opener("no json here")
        steps = ("a", "b")
        res = plan({}, steps, route, env={}, opener=opener)
        self.assertEqual(res, ["a", "b"])
    def test_plan_fallback_error(self):
        """Plan falls back on MuseError."""
        route = glimmer_route({})
        opener = FakeOpener(exc=OSError("fail"))
        steps = ("a", "b")
        res = plan({}, steps, route, env={}, opener=opener)
        self.assertEqual(res, ["a", "b"])
    def test_plan_empty_skips_ask(self):
        """Plan with empty steps makes no request."""
        route = glimmer_route({})
        opener = make_ask_opener('["a"]')
        res = plan({}, [], route, opener=opener)
        self.assertEqual(res, [])
        self.assertEqual(opener.requests, [])
        opener2 = make_ask_opener('["a"]')
        res2 = plan({}, ["a"], route, opener=opener2)
        self.assertEqual(res2, ["a"])
        self.assertEqual(len(opener2.requests), 1)
    def test_check_reachable(self):
        """Check reachable true."""
        data = json.dumps({"data": [{"id": "muse-glimmer-30b"}, {"id": "other"}]}).encode()
        opener = FakeOpener(FakeResponse(data))
        env = {}
        res = check(env=env, opener=opener)
        self.assertTrue(res["reachable"])
        self.assertIn("muse-glimmer-30b", res["served"])
        self.assertEqual(res["endpoint"], glimmer_route(env).base_url)
        self.assertEqual(res["model"], glimmer_route(env).model)
        req = opener.requests[0]
        self.assertEqual(req.full_url, glimmer_route(env).base_url + "/models")
    def test_check_unreachable(self):
        """Check unreachable false."""
        opener = FakeOpener(exc=OSError("down"))
        res = check(env={}, opener=opener)
        self.assertFalse(res["reachable"])
        self.assertEqual(res["served"], [])
    def test_route_model_regex(self):
        """Every Route model matches muse|glimmer|spark."""
        routes = [glimmer_route({}), SPARK_OPEN]
        for r in routes:
            self.assertIsNotNone(re.search(r"muse|glimmer|spark", r.model, re.I))
    def test_route_base_url_host(self):
        """Every Route base_url host is allowed."""
        routes = [glimmer_route({}), SPARK_OPEN]
        for r in routes:
            host = urllib.parse.urlparse(r.base_url).hostname
            self.assertIn(host, ("127.0.0.1", "inference.baseten.co"))
    def test_label(self):
        """Label formats correctly."""
        route = glimmer_route({})
        self.assertEqual(label(route), f"{route.name}, {route.where}")
    def test_facts_empty(self):
        """Facts handles empty dict."""
        self.assertEqual(facts({}), {})
    def test_is_hard_equal(self):
        """is_hard equal is not hard."""
        self.assertFalse(is_hard(5, 5))
    def test_timeout_s_default(self):
        from opos.muse import timeout_s, DEFAULT_TIMEOUT
        self.assertEqual(timeout_s({}), DEFAULT_TIMEOUT)
        self.assertEqual(timeout_s({}), 120.0)
    def test_timeout_s_valid(self):
        from opos.muse import timeout_s
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "90"}), 90.0)
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "120"}), 120.0)
    def test_timeout_s_clamp_low(self):
        from opos.muse import timeout_s
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "2"}), 5.0)
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "4.9"}), 5.0)
    def test_timeout_s_clamp_high(self):
        from opos.muse import timeout_s
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "700"}), 600.0)
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "1000"}), 600.0)
    def test_timeout_s_invalid(self):
        from opos.muse import timeout_s
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "text"}), 120.0)
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "0"}), 120.0)
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "-5"}), 120.0)
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "nan"}), 120.0)
        self.assertEqual(timeout_s({"OPOS_MUSE_TIMEOUT": "NaN"}), 120.0)
    def test_ask_env_timeout(self):
        route = glimmer_route({})
        captured = {}
        def opener(req, timeout=None):
            captured["timeout"] = timeout
            data = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
            return FakeResponse(data)
        res = ask(route, "hi", env={"OPOS_MUSE_TIMEOUT": "90"}, opener=opener)
        self.assertEqual(res, "ok")
        self.assertEqual(captured["timeout"], 90.0)
    def test_ask_explicit_timeout(self):
        route = glimmer_route({})
        captured = {}
        def opener(req, timeout=None):
            captured["timeout"] = timeout
            data = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
            return FakeResponse(data)
        res = ask(route, "hi", env={"OPOS_MUSE_TIMEOUT": "90"}, opener=opener, timeout=7)
        self.assertEqual(res, "ok")
        self.assertEqual(captured["timeout"], 7)
    def test_payload_reasoning_glimmer(self):
        route = glimmer_route({})
        opener = make_ask_opener("ok")
        ask(route, "hi", env={}, opener=opener)
        body = json.loads(opener.requests[0].data.decode())
        self.assertIn("reasoning_effort", body)
        self.assertEqual(body["reasoning_effort"], "low")
    def test_payload_reasoning_lan(self):
        lan = Route("lan", "Muse Glimmer", "on a computer you own", f"http://{PRIVATE_TEST_HOST}:1234/v1", "muse-glimmer-30b")
        opener = make_ask_opener("ok")
        ask(lan, "hi", env={}, opener=opener)
        body = json.loads(opener.requests[0].data.decode())
        self.assertEqual(body["reasoning_effort"], "low")
    def test_mock_endpoint_uses_documentation_address(self):
        src = pathlib.Path(__file__).read_text()
        tree = ast.parse(src)
        found = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PRIVATE_TEST_HOST" for t in n.targets)]
        self.assertEqual(len(found), 1)
        self.assertIsInstance(found[0].value, ast.Constant)
        self.assertEqual(found[0].value.value, "192.0.2.10")
    def test_payload_no_reasoning_spark(self):
        env = {"BASETEN_API_KEY": "k"}
        opener = make_ask_opener("ok")
        ask(SPARK_OPEN, "hi", env=env, opener=opener)
        body = json.loads(opener.requests[0].data.decode())
        self.assertNotIn("reasoning_effort", body)
    def test_payload_no_reasoning_local(self):
        local = Route("local", "Meta Llama 3.2 1B", "on this phone", "http://127.0.0.1:8080/v1", "llama-3.2-1b-instruct")
        opener = make_ask_opener("ok")
        ask(local, "hi", env={}, opener=opener)
        body = json.loads(opener.requests[0].data.decode())
        self.assertNotIn("reasoning_effort", body)
    def test_ask_over_cap_rejected(self):
        from opos.muse import MAX_RESPONSE_BYTES
        route = glimmer_route({})
        overhead = len(json.dumps({"choices": [{"message": {"content": ""}}]}).encode())
        big_content = "x" * (MAX_RESPONSE_BYTES - overhead + 20)
        big_data = json.dumps({"choices": [{"message": {"content": big_content}}]}).encode()
        self.assertGreater(len(big_data), MAX_RESPONSE_BYTES)
        opener = FakeOpener(FakeResponse(big_data))
        with self.assertRaises(MuseError):
            ask(route, "hi", env={}, opener=opener)
        ok_content = "y" * (MAX_RESPONSE_BYTES - overhead - 80)
        ok_data = json.dumps({"choices": [{"message": {"content": ok_content}}]}).encode()
        self.assertLessEqual(len(ok_data), MAX_RESPONSE_BYTES)
        opener_ok = FakeOpener(FakeResponse(ok_data))
        self.assertEqual(ask(route, "hi", env={}, opener=opener_ok), ok_content)
        class SizedBig:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, n=-1):
                return big_data[:n] if n != -1 else big_data
        opener_sized = FakeOpener(SizedBig())
        with self.assertRaises(MuseError):
            ask(route, "hi", env={}, opener=opener_sized)
    def test_ask_oversize_read_is_capped(self):
        from opos.muse import MAX_RESPONSE_BYTES
        route = glimmer_route({})
        overhead = len(json.dumps({"choices": [{"message": {"content": ""}}]}).encode())
        big_data = json.dumps({"choices": [{"message": {"content": "x" * (MAX_RESPONSE_BYTES - overhead + 20)}}]}).encode()
        self.assertGreater(len(big_data), MAX_RESPONSE_BYTES)
        resp = FakeResponse(big_data)
        with self.assertRaises(MuseError):
            ask(route, "hi", env={}, opener=FakeOpener(resp))
        self.assertIsInstance(resp.asked, int)
        self.assertGreater(resp.asked, 0)
        self.assertLessEqual(resp.asked, MAX_RESPONSE_BYTES + 1)
    def test_check_over_cap_unreachable(self):
        from opos.muse import MAX_RESPONSE_BYTES
        base = {"data": [{"id": "muse-glimmer-30b"}]}
        base_len = len(json.dumps(base).encode())
        pad_len = MAX_RESPONSE_BYTES - base_len + 100
        big_payload = {"data": [{"id": "muse-glimmer-30b"}], "pad": "x" * pad_len}
        big_data = json.dumps(big_payload).encode()
        self.assertGreater(len(big_data), MAX_RESPONSE_BYTES)
        opener = FakeOpener(FakeResponse(big_data))
        res = check(env={}, opener=opener)
        self.assertFalse(res["reachable"])
        self.assertEqual(res["served"], [])
        ok_pad = "y" * (MAX_RESPONSE_BYTES - base_len - 80)
        ok_payload = {"data": [{"id": "muse-glimmer-30b"}], "pad": ok_pad}
        ok_data = json.dumps(ok_payload).encode()
        self.assertLessEqual(len(ok_data), MAX_RESPONSE_BYTES)
        opener_ok = FakeOpener(FakeResponse(ok_data))
        res_ok = check(env={}, opener=opener_ok)
        self.assertTrue(res_ok["reachable"])
        self.assertIn("muse-glimmer-30b", res_ok["served"])
    def test_check_non_object_json(self):
        for payload in [b"[]", b"null", b'"x"']:
            opener = FakeOpener(FakeResponse(payload))
            try:
                res = check(env={}, opener=opener)
            except AttributeError as e:
                self.fail(f"check raised AttributeError for {payload!r}: {e}")
            self.assertFalse(res["reachable"], msg=f"payload {payload!r}")
            self.assertEqual(res["served"], [], msg=f"payload {payload!r}")
        ok_data = json.dumps({"data": [{"id": "ok"}]}).encode()
        opener_ok = FakeOpener(FakeResponse(ok_data))
        res_ok = check(env={}, opener=opener_ok)
        self.assertTrue(res_ok["reachable"])
        self.assertIn("ok", res_ok["served"])
    def test_check_deeply_nested_json_unreachable(self):
        from opos.muse import MAX_RESPONSE_BYTES
        depth = 5000
        nested = ("[" * depth + "]" * depth).encode()
        self.assertLess(len(nested), MAX_RESPONSE_BYTES)
        opener = FakeOpener(FakeResponse(nested))
        res = check(env={}, opener=opener)
        self.assertFalse(res["reachable"])
        self.assertEqual(res["served"], [])

    def test_rejects_reader_without_size_arg(self):
        class NoSizeReader:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        route = glimmer_route({})
        opener = FakeOpener(NoSizeReader())
        with self.assertRaises(MuseError):
            ask(route, "hi", env={}, opener=opener)
        class NoSizeCheckReader:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return json.dumps({"data": [{"id": "muse-glimmer-30b"}]}).encode()
        opener2 = FakeOpener(NoSizeCheckReader())
        res = check(env={}, opener=opener2)
        self.assertFalse(res["reachable"])
        self.assertEqual(res["served"], [])
    def test_redirect_not_followed(self):
        """Redirects are not followed and leak no headers."""
        host = ".".join(["127", "0", "0", "1"])
        hits = []
        post_auth = []
        model_requests = []
        get_redirects = []
        state = {"redirect": True}
        ok_model = glimmer_route({}).model
        api_key = "-".join(["redirect", "test", "key"])
        expected_auth = "".join(["Api-Key ", api_key])
        ask_env = {"BASETEN_API_KEY": api_key}
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                return None
            def _send_json(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def _redirect(self):
                loc = "".join(["/", "se", "cond"])
                self.send_response(302)
                self.send_header("Location", loc)
                self.send_header("Content-Length", "0")
                self.end_headers()
            def do_GET(self):
                if self.path == "/v1/models":
                    model_requests.append(self.path)
                    if state["redirect"]:
                        get_redirects.append(self.path)
                        self._redirect()
                    else:
                        self._send_json({"data": [{"id": ok_model}]})
                elif self.path == "".join(["/", "se", "cond"]):
                    hits.append(dict(self.headers.items()))
                    self._send_json({"data": []})
                else:
                    self.send_response(404)
                    self.end_headers()
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                target = "".join(["/chat", "/completions"])
                if self.path == target:
                    post_auth.append(self.headers.get("Authorization"))
                    if state["redirect"]:
                        self._redirect()
                    else:
                        self._send_json({"choices": [{"message": {"content": "ok"}}]})
                elif self.path == "".join(["/", "se", "cond"]):
                    hits.append(dict(self.headers.items()))
                    self._send_json({"choices": [{"message": {"content": "ok"}}]})
                else:
                    self.send_response(404)
                    self.end_headers()
        server = http.server.HTTPServer((host, 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            base = "".join(["http", "://", host, ":", str(port)])
            route = Route("spark-open", "Muse Glimmer", "here", base, ok_model)
            with self.assertRaises(MuseError):
                ask(route, "hi", env=ask_env)
            self.assertEqual(post_auth, [expected_auth])
            self.assertEqual(hits, [])
            env = {"OPOS_GLIMMER_URL": "".join([base, "/v1"])}
            res = check(env=env)
            self.assertFalse(res["reachable"])
            self.assertEqual(model_requests, ["/v1/models"])
            self.assertEqual(hits, [])
            self.assertEqual(get_redirects, ["/v1/models"])
            state["redirect"] = False
            res2 = check(env=env)
            self.assertTrue(res2["reachable"])
            self.assertEqual(res2["served"], [ok_model])
            self.assertEqual(get_redirects, ["/v1/models"])
            self.assertEqual(hits, [])
            self.assertEqual(ask(route, "hi", env=ask_env), "ok")
            self.assertEqual(post_auth, [expected_auth, expected_auth])
            self.assertEqual(hits, [])
        finally:
            server.shutdown()
            server.server_close()
    def test_ask_rejects_non_http_scheme(self):
        bad_url = "".join(["fi", "le", ":", "//", "/nonexistent", "/v1"])
        route = Route("glimmer", "Muse Glimmer", "on this device", bad_url, "muse-glimmer-30b")
        opener = make_ask_opener("ok")
        with self.assertRaises(MuseError):
            ask(route, "hi", env={}, opener=opener)
        self.assertEqual(opener.requests, [])
    def test_check_non_http_scheme_is_unreachable(self):
        bad_url = "".join(["fi", "le", ":", "//", "/nonexistent", "/v1"])
        route = Route("glimmer", "Muse Glimmer", "on this device", bad_url, "muse-glimmer-30b")
        opener = make_ask_opener("ok")
        with mock.patch.object(muse_mod, "glimmer_route", return_value=route):
            res = check(env={}, opener=opener)
        self.assertFalse(res["reachable"])
        self.assertEqual(res["served"], [])
        self.assertEqual(opener.requests, [])
class TestNoSilentExceptPass(unittest.TestCase):
    def test_no_except_handler_with_only_pass(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "src/opos/muse.py"
        tree = ast.parse(path.read_text())
        lines = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and len(n.body) == 1 and isinstance(n.body[0], ast.Pass)]
        self.assertEqual(lines, [], msg="except-pass at lines %s" % lines)


class RouteOpener:
    """Opener that fails for some base URLs and answers for the rest."""
    def __init__(self, content, down=()):
        self.down = tuple(down)
        self.calls = []
        self.data = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url, timeout))
        if any(url.startswith(d) for d in self.down):
            raise OSError("down")
        return FakeResponse(self.data)


class TestLaneCoreRegressions(unittest.TestCase):
    LOCAL = Route("local", "Meta Llama 3.2 1B", "on this phone", "http://127.0.0.1:8080/v1", "llama-3.2-1b-instruct")
    def test_fact_keys_carry_battery_facts_and_why_paused(self):
        # Regression (lane B): the model saw only mode/allowed/paused/hours_left/reserve/goal_hours
        # and answered "I do not know" to "Why is email paused?".
        for key in ("battery_percent", "battery_health_percent", "usable_percent", "temp_c", "charging", "why_paused"):
            self.assertIn(key, FACT_KEYS)
        helper = {"mode": "SAVER", "paused": ["email"], "why_paused": "The battery is worn.", "source": "router", "engine": "x"}
        self.assertEqual(facts(helper), {"mode": "SAVER", "paused": ["email"], "why_paused": "The battery is worn."})
    def test_explain_prompt_points_at_why_paused(self):
        opener = make_ask_opener("Because the battery is worn.")
        explain({"why_paused": "The battery is worn (60% health)."}, glimmer_route({}), env={}, opener=opener)
        body = json.loads(opener.requests[0].data.decode())
        self.assertIn("why_paused", body["messages"][0]["content"])
    def test_explain_falls_back_to_next_route(self):
        # Regression (lane D): Why tried one route only, so a dead Llama server hid a healthy Glimmer.
        glimmer = glimmer_route({})
        opener = RouteOpener("Battery is worn, so maps rest.", down=(self.LOCAL.base_url,))
        out = explain({"mode": "SAVER"}, [self.LOCAL, glimmer], env={}, opener=opener)
        self.assertTrue(out["ok"])
        self.assertEqual(out["engine"], glimmer.name)
        self.assertEqual([u for u, _ in opener.calls], [self.LOCAL.base_url + "/chat/completions", glimmer.base_url + "/chat/completions"])
        for _, timeout in opener.calls:
            self.assertLessEqual(timeout, muse_mod.HELPER_DEADLINE_S)
        out = explain({"mode": "SAVER"}, [self.LOCAL, glimmer], env={}, opener=RouteOpener("x", down=(self.LOCAL.base_url, glimmer.base_url)))
        self.assertFalse(out["ok"])
        self.assertEqual(out["engine"], "rules")
    def test_explain_shares_one_deadline_across_routes(self):
        glimmer = glimmer_route({})
        opener = RouteOpener("late", down=(self.LOCAL.base_url,))
        with mock.patch.object(muse_mod.time, "monotonic", side_effect=[0.0, 0.0, muse_mod.HELPER_DEADLINE_S + 1]):
            out = explain({"mode": "SAVER"}, [self.LOCAL, glimmer], env={}, opener=opener)
        self.assertEqual(len(opener.calls), 1, "no route attempt starts after the shared deadline")
        self.assertEqual(out["engine"], "rules")
    def test_slow_first_route_leaves_time_for_the_next(self):
        # Review: the first route used to get the whole 115 s budget, so a stalled server hid Glimmer.
        glimmer = glimmer_route({})
        opener = RouteOpener("Battery is worn.", down=(self.LOCAL.base_url,))
        explain({"mode": "SAVER"}, [self.LOCAL, glimmer], env={}, opener=opener)
        (first, t1), (second, t2) = opener.calls
        self.assertLessEqual(t1, muse_mod.EARLY_ROUTE_S)
        self.assertGreater(t2, muse_mod.EARLY_ROUTE_S - 5)
        only = RouteOpener("ok")
        explain({"mode": "SAVER"}, [glimmer], env={}, opener=only)
        self.assertLessEqual(only.calls[0][1], muse_mod.EARLY_ROUTE_S, "every route is capped at 60 seconds")
    def test_plan_falls_back_to_next_route_and_names_it(self):
        glimmer = glimmer_route({})
        opener = RouteOpener('["c","a"]', down=(self.LOCAL.base_url,))
        res = plan({}, ("a", "b", "c"), [self.LOCAL, glimmer], env={}, opener=opener)
        self.assertEqual(res, ["c", "a", "b"])
        self.assertEqual(res.route, glimmer)
        self.assertEqual(len(opener.calls), 2)
        fixed = plan({}, ("a", "b"), [self.LOCAL], env={}, opener=RouteOpener("x", down=(self.LOCAL.base_url,)))
        self.assertEqual(fixed, ["a", "b"])
        self.assertIsNone(fixed.route)
    def test_plan_finds_list_after_leading_bracket(self):
        # Regression (lane E): decoding from the first "[" made '[Note] ...' fall back to fixed order.
        route = glimmer_route({})
        for answer in ('[Note] Here is the plan: ["c","a"]', '[1, 2] are numbers; the order is ["c", "a"]', 'Plan:\n["c",\n "a"]\n[done]'):
            with self.subTest(answer=answer):
                res = plan({"goal_hours": 5}, ("a", "b", "c"), route, env={}, opener=make_ask_opener(answer))
                self.assertEqual(res, ["c", "a", "b"])
        res = plan({}, ("a", "b"), route, env={}, opener=make_ask_opener("[Note] nothing to order here"))
        self.assertEqual(res, ["a", "b"])
if __name__ == "__main__":
    unittest.main()
