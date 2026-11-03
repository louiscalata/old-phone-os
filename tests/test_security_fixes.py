# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regressions for inference transport, state access and body deadlines."""
import contextlib
import io
import json
import os
import re
import threading
import unittest
import urllib.request
import urllib.response
from email.message import Message
from unittest import mock

from opos import assist, muse, secure, web


def response(req, body, code=200, location=None):
    headers = Message()
    if location:
        headers["Location"] = location
    result = urllib.response.addinfourl(io.BytesIO(json.dumps(body).encode()), headers, req.full_url, code)
    result.msg = "Found" if code == 302 else "OK"
    return result


@contextlib.contextmanager
def transport(reply, proxy=False):
    """Keep urllib routing real, replacing only the socket transport."""
    calls = []
    def open_http(_handler, _connection, req, **_kw):
        calls.append(req)
        return reply(req)
    env = {"http_proxy": "http://proxy.example:8080", "no_proxy": ""} if proxy else {}
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(urllib.request, "_opener", None), \
            mock.patch.object(urllib.request, "proxy_bypass", return_value=False), \
            mock.patch.object(urllib.request.HTTPHandler, "do_open", autospec=True, side_effect=open_http):
        yield calls


CHAT = {"choices": [{"message": {"content": "Local answer"}}]}


class TestInferenceTransport(unittest.TestCase):
    def test_muse_inference_ignores_inherited_proxy(self):
        route = muse.glimmer_route({})
        with transport(lambda req: response(req, CHAT), proxy=True) as calls:
            self.assertEqual(muse.ask(route, "PRIVATE QUESTION", env={}), "Local answer")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].host, "127.0.0.1:1234")
        self.assertEqual(json.loads(calls[0].data)["messages"][0]["content"], "PRIVATE QUESTION")

    def test_ask_inference_ignores_inherited_proxy(self):
        with transport(lambda req: response(req, CHAT), proxy=True) as calls:
            result = assist.answer("PRIVATE QUESTION", env={}, context={"paused": ["Photos"]})
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].host, "127.0.0.1:1234")
        self.assertIn(b"PRIVATE QUESTION", calls[0].data)
        self.assertIn(b"Photos", calls[0].data)

    def test_ask_blocks_redirect_without_a_local_answer_label(self):
        target = "http://redirect.example/result"
        def reply(req):
            if req.full_url == target:
                return response(req, CHAT)
            return response(req, {}, 302, target)
        with transport(reply) as calls:
            result = assist.answer("What is paused?", env={})
        self.assertEqual([req.full_url for req in calls], ["http://127.0.0.1:1234/v1/chat/completions"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["engine"], "none")
        self.assertEqual(result["where"], "")

    def test_wikipedia_uses_the_no_redirect_proxy_free_transport(self):
        wiki = {"query": {"search": [{"title": "Battery", "snippet": "Stored energy"}]}}
        def reply(req, **kw):
            return response(req, wiki if req.host == "en.wikipedia.org" else CHAT)
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("default urlopen used")) as default, \
                mock.patch.object(muse, "_open_no_redirect", side_effect=reply) as opener:
            result = assist.answer("What is a battery?", web=True, env={})
        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "Muse Glimmer")
        self.assertEqual(result["sources"], [{"title": "Battery", "url": "https://en.wikipedia.org/wiki/Battery"}])
        default.assert_not_called()
        self.assertEqual([call.args[0].host for call in opener.call_args_list], ["en.wikipedia.org", "127.0.0.1:1234"])
        self.assertTrue(opener.call_args_list[0].args[0].full_url.startswith("https://en.wikipedia.org/w/api.php?"))

    def test_custom_opener_still_handles_search_and_inference(self):
        calls = []
        def opener(req, **kw):
            calls.append(req.full_url)
            body = {"query": {"search": []}} if req.host == "en.wikipedia.org" else CHAT
            return response(req, body)
        result = assist.answer("What is paused?", web=True, env={}, opener=opener)
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 2)


def handler(method="GET", token=None, host="127.0.0.1:8765"):
    """Build a handler without binding a socket; retain real response serialization."""
    h = web.Handler.__new__(web.Handler)
    h.command = method
    h.path = "/api/state" if method == "GET" else "/api"
    h.request_version = "HTTP/1.1"
    h.requestline = f"{method} {h.path} HTTP/1.1"
    h.port = 8765
    h.token = "t" * 43
    h.headers = Message()
    h.headers["Host"] = host
    if token is not None:
        h.headers["X-OPOS-Token"] = token
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO()
    h.connection = mock.Mock()
    h.phone = mock.Mock()
    h.phone.state.return_value = {"thread": "PRIVATE MESSAGE"}
    h.lock = threading.Lock()
    h._deadline_lock = threading.Lock()
    h._deadline_at = float("inf")
    h._expired = False
    h._timer = mock.Mock()
    h.limiter = mock.Mock()
    return h


def result(h):
    head, body = h.wfile.getvalue().split(b"\r\n\r\n", 1)
    return int(head.split()[1]), json.loads(body)


class TestStateToken(unittest.TestCase):
    def test_state_requires_token_before_reading_private_data(self):
        for token in (None, "wrong"):
            with self.subTest(token=token):
                h = handler(token=token)
                h.do_GET()
                self.assertEqual(result(h), (403, {"error": "forbidden"}))
                h.phone.state.assert_not_called()
                self.assertNotIn(b"PRIVATE MESSAGE", h.wfile.getvalue())

    def test_state_accepts_valid_token(self):
        h = handler(token="t" * 43)
        h.do_GET()
        self.assertEqual(result(h), (200, {"thread": "PRIVATE MESSAGE"}))
        h.phone.state.assert_called_once()

    def test_valid_state_token_does_not_bypass_host(self):
        h = handler(token="t" * 43, host="evil.example")
        h.do_GET()
        self.assertEqual(result(h), (403, {"error": "forbidden"}))
        h.phone.state.assert_not_called()

    def test_post_origin_gate_is_preserved(self):
        for origin in ("http://evil.example", "null"):
            with self.subTest(origin=origin):
                h = handler("POST", token="t" * 43)
                h.headers["Origin"] = origin
                h.headers["Content-Type"] = "application/json"
                h.headers["Content-Length"] = "2"
                h.rfile = io.BytesIO(b"{}")
                h.do_POST()
                self.assertEqual(result(h), (403, {"error": "forbidden"}))
                h.limiter.allow.assert_not_called()

    def test_both_frontend_state_fetches_supply_token_independently(self):
        html = web.page("t" * 43, "n" * 22, demo=True)
        calls = re.findall(r"fetch\('/api/state'[^;\n]+", html)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("'X-OPOS-Token':document.querySelector('[name=opos-token]').content", call)


class TestBodyDeadline(unittest.TestCase):
    def test_oversized_body_drain_expires_and_preserves_error(self):
        h = handler("POST", token="t" * 43)
        h.headers["Content-Type"] = "application/json"
        h.headers["Content-Length"] = "5000"
        class Deadline:
            cancelled = False
            ticks = 0
            def cancel(self):
                self.cancelled = True
            def advance(self):
                self.ticks += 1
                if self.ticks == 10 and not self.cancelled:
                    h._expire()
        h._timer = Deadline()
        reads = []
        class Trickle:
            def read(self, n):
                reads.append(n)
                # Bytes keep arriving before the inactivity timeout. The absolute
                # deadline must interrupt this drain, even while input progresses.
                for _ in range(20):
                    h._timer.advance()
                    if h._expired:
                        raise OSError("connection closed at deadline")
                return b"x" * n
        h.rfile = Trickle()
        h.do_POST()
        self.assertEqual(result(h), (413, {"error": "body too large"}))
        self.assertEqual(reads, [secure.MAX_BODY])
        self.assertTrue(h._expired)
        self.assertEqual(h._timer.ticks, 10)
        h.connection.shutdown.assert_called_once()
        h.connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
