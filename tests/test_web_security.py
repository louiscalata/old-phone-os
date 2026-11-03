# SPDX-License-Identifier: GPL-3.0-or-later
"""Web security tests part 1."""
import http.client
import json
import re
import socket
import threading
import time
import unittest
from copy import deepcopy
from unittest import mock
from src.opos import web
from src.opos.essentials import Message
from tests import webclient

_orig_request = http.client.HTTPConnection.request
def _compat(self, method, url, body=None, headers=None, **kw):
    """Strip newer skip flags on old Pythons."""
    kw.pop("skip_host", None)
    kw.pop("skip_accept_encoding", None)
    if headers is None:
        headers = {}
    return _orig_request(self, method, url, body=body, headers=headers, **kw)
http.client.HTTPConnection.request = _compat  # type: ignore[method-assign]


def _body(port: int, token: str, obj: dict) -> tuple[int, bytes]:
    """POST JSON object and return status and body."""
    raw = json.dumps(obj).encode()
    h = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-OPOS-Token": token}
    s, _, b = webclient.request(port, "POST", "/api", body=raw, headers=h)
    return s, b


def _safe(status: int, hdrs: dict, body: bytes) -> None:
    """Check error bodies leak no traceback."""
    assert isinstance(status, int)
    if 400 <= status <= 599:
        txt = body.decode("utf-8", "ignore")
        assert "traceback" not in txt.lower()
        assert "traceback" not in repr(hdrs).lower()


class TestLeanMemory(unittest.TestCase):
    """Memory measurements need no running server."""

    def test_status_values(self):
        for value, expected in ((40960, 40), (0, 0), (40961, 40)):
            with self.subTest(value=value):
                opened = mock.mock_open(read_data=f"Name: python\nVmRSS: {value} kB\n")
                with mock.patch("builtins.open", opened):
                    self.assertEqual(web.self_rss_mb(), expected)
                opened.assert_called_once_with("/proc/self/status", encoding="utf-8")
                opened().__exit__.assert_called_once()

    def test_missing_or_malformed_status(self):
        for data in ("Name: python\n", "VmRSS:\n", "VmRSS: invalid kB\n", ""):
            with self.subTest(data=data):
                with mock.patch("builtins.open", mock.mock_open(read_data=data)):
                    self.assertIsNone(web.self_rss_mb())
        for error in (FileNotFoundError, PermissionError):
            with self.subTest(error=error):
                with mock.patch("builtins.open", side_effect=error):
                    self.assertIsNone(web.self_rss_mb())

    def test_state_measures_each_call_for_both_visibility_settings(self):
        for visible in (False, True):
            with self.subTest(visible=visible):
                phone = web.Phone(visible=visible)
                with mock.patch.object(web, "self_rss_mb") as rss, mock.patch.object(web.assist, "free_mb") as free:
                    for opos_mb, free_mb in ((40, 512), (0, 0), (None, None), (0, None), (None, 0)):
                        rss.return_value = opos_mb
                        free.return_value = free_mb
                        self.assertEqual(phone.state()["lean"], {"opos_mb": opos_mb, "free_mb": free_mb})
                    self.assertEqual(rss.call_count, 5)
                    self.assertEqual(free.call_count, 5)


class TestWebSecurity(unittest.TestCase):
    """Hardened web server security checks."""

    def setUp(self) -> None:
        """Start one server for the test."""
        self._run = webclient.Running()
        self._run.__enter__()
        self.port: int = self._run.port
        self.tok: str = webclient.fetch_token(self.port)

    def tearDown(self) -> None:
        """Stop the server."""
        self._run.__exit__(None, None, None)

    def test_root_ok(self) -> None:
        """GET / returns the page."""
        s, _, _ = webclient.request(self.port, "GET", "/")
        self.assertEqual(s, 200)

    def test_path_traversal(self) -> None:
        """Traversal paths are rejected."""
        paths = ["/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/static/../../README.md", "//etc/passwd", "/api/../api/state/.."]
        for p in paths:
            s, _, b = webclient.request(self.port, "GET", p)
            self.assertIn(s, (403, 404))
            _safe(s, {}, b)
            self.assertNotIn(b"root:", b)
            self.assertNotIn(b"Old Phone OS", b)
        s, _, b = webclient.request(self.port, "POST", "/api/../x", body=b"{}", headers={"Content-Type": "application/json", "X-OPOS-Token": self.tok})
        self.assertIn(s, (403, 404))
        _safe(s, {}, b)
        self.assertNotIn(b"root:", b)

    def test_bad_methods(self) -> None:
        """Unsupported methods return 405 with Allow."""
        for m in ("PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            for p in ("/", "/api"):
                s, h, b = webclient.request(self.port, m, p)
                self.assertEqual(s, 405)
                self.assertIn("allow", h)
                _safe(s, h, b)
        s, _, _ = webclient.request(self.port, "GET", "/api")
        self.assertEqual(s, 404)

    @mock.patch.object(web, "self_rss_mb", new=lambda: 40)
    @mock.patch.object(web.assist, "free_mb", new=lambda: 512)
    def test_missing_csrf(self) -> None:
        """POST without a valid token is rejected."""
        s0, _, b0 = webclient.request(self.port, "GET", "/api/state", headers={"X-OPOS-Token": self.tok})
        self.assertEqual(s0, 200)
        before = b0
        good = json.dumps({"action": "tick"}).encode()
        base = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        s, _, b = webclient.request(self.port, "POST", "/api", body=good, headers=dict(base))
        self.assertEqual(s, 403)
        s, _, b = webclient.request(self.port, "POST", "/api", body=good, headers={**base, "X-OPOS-Token": "wrong"})
        self.assertEqual(s, 403)
        s, _, b = webclient.request(self.port, "POST", "/api", body=good, headers={**base, "X-OPOS-Token": ""})
        self.assertEqual(s, 403)
        s1, _, b1 = webclient.request(self.port, "GET", "/api/state", headers={"X-OPOS-Token": self.tok})
        self.assertEqual(s1, 200)
        self.assertEqual(before, b1)

    def test_wrong_host(self) -> None:
        """Non-loopback Host is rejected."""
        for p in ("/", "/api/state"):
            for h in (f"evil.example:{self.port}", "127.0.0.1"):
                s, hd, b = webclient.request(self.port, "GET", p, headers={"Host": h})
                self.assertEqual(s, 403)
                _safe(s, hd, b)
        raw = json.dumps({"action": "tick"}).encode()
        h = {"Host": "evil.example", "Content-Type": "application/json", "X-OPOS-Token": self.tok}
        s, hd, b = webclient.request(self.port, "POST", "/api", body=raw, headers=h)
        self.assertEqual(s, 403)
        _safe(s, hd, b)

    def test_rejected_post_with_body_gets_403(self) -> None:
        """Rejected POST with body still returns 403."""
        data = b"POST /api HTTP/1.1\r\nHost: evil.example\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n" + b"x" * 20
        resp = webclient.raw(self.port, data)
        self.assertTrue(resp.startswith(b"HTTP/1."))
        self.assertIn(b" 403 ", resp.split(b"\r\n", 1)[0])

    def test_cross_origin(self) -> None:
        """Foreign origins are rejected."""
        raw = json.dumps({"action": "tick"}).encode()
        base = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json", "X-OPOS-Token": self.tok}
        s, _, _ = webclient.request(self.port, "POST", "/api", body=raw, headers={**base, "Origin": "http://evil.example"})
        self.assertEqual(s, 403)
        s, _, _ = webclient.request(self.port, "POST", "/api", body=raw, headers={**base, "Origin": "null"})
        self.assertEqual(s, 403)
        s, _, _ = webclient.request(self.port, "POST", "/api", body=raw, headers={**base, "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(s, 200)

    def test_wrong_content_type(self) -> None:
        """Non-JSON content type is rejected."""
        raw = json.dumps({"action": "tick"}).encode()
        for ct in ("text/plain", "application/x-www-form-urlencoded"):
            h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": ct, "X-OPOS-Token": self.tok}
            s, hd, b = webclient.request(self.port, "POST", "/api", body=raw, headers=h)
            self.assertEqual(s, 415)
            _safe(s, hd, b)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/api", skip_host=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            conn.putheader("X-OPOS-Token", self.tok)
            conn.putheader("Content-Length", str(len(raw)))
            conn.endheaders(raw)
            res = conn.getresponse()
            self.assertEqual(res.status, 415)
        finally:
            conn.close()

    def test_oversized_body(self) -> None:
        """Bodies over the limit are rejected."""
        big = b"x" * 5000
        h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json", "X-OPOS-Token": self.tok}
        s, hd, b = webclient.request(self.port, "POST", "/api", body=big, headers=h)
        self.assertEqual(s, 413)
        _safe(s, hd, b)
        s, _, _ = webclient.request(self.port, "POST", "/api", body=b"x" * 4097, headers=h)
        self.assertEqual(s, 413)
        pad = json.dumps({"action": "tick", "pad": "x" * 4000}).encode()
        self.assertLessEqual(len(pad), 4096)
        s, hd, b = webclient.request(self.port, "POST", "/api", body=pad, headers=h)
        self.assertIn(s, (200, 400))
        self.assertLess(s, 500)

    def _raw_post(self, extra: bytes, body: bytes = b"{}") -> bytes:
        """Send a hand-written POST with given headers."""
        req = b"POST /api HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nX-OPOS-Token: %s\r\n%s\r\n%s" % (self.port, self.tok.encode(), extra, body)
        return webclient.raw(self.port, req)

    def test_hostile_length(self) -> None:
        """Hostile Content-Length values get 4xx and server survives."""
        vals = [b"-1", b"-9999999999", b"1" + b"0" * 400, b"abc", b"1e3", b"0x10", b" 5"]
        for v in vals:
            resp = self._raw_post(b"Content-Length: " + v + b"\r\nConnection: close")
            self.assertTrue(resp, "empty response")
            line = resp.split(b"\r\n", 1)[0].decode("latin-1")
            code = int(line.split()[1])
            self.assertGreaterEqual(code, 400)
            self.assertLess(code, 500)
            s, _, _ = webclient.request(self.port, "GET", "/")
            self.assertEqual(s, 200)
        resp = self._raw_post(b"Content-Length: 2\r\nContent-Length: 3\r\nConnection: close")
        self.assertTrue(resp, "empty response")
        code = int(resp.split(b"\r\n", 1)[0].decode("latin-1").split()[1])
        self.assertGreaterEqual(code, 400)
        self.assertLess(code, 500)
        s, _, _ = webclient.request(self.port, "GET", "/")
        self.assertEqual(s, 200)
        resp = self._raw_post(b"Transfer-Encoding: chunked\r\nConnection: close", body=b"0\r\n\r\n")
        self.assertTrue(resp, "empty response")
        code = int(resp.split(b"\r\n", 1)[0].decode("latin-1").split()[1])
        self.assertEqual(code, 400)
        s, _, _ = webclient.request(self.port, "GET", "/")
        self.assertEqual(s, 200)

    @mock.patch.object(web, "self_rss_mb", new=lambda: 40)
    @mock.patch.object(web.assist, "free_mb", new=lambda: 512)
    def test_duplicate_content_length_cannot_change_state(self) -> None:
        """Duplicate lengths cannot change state."""
        phone = self._run.server.RequestHandlerClass.phone
        phone.charging = False
        s, _, saved = webclient.request(self.port, "GET", "/api/state", headers={"X-OPOS-Token": self.tok})
        self.assertEqual(s, 200)
        body = b'{"action":"charge","on":true}'
        for first, second in ((b"29", b"30"), (b"30", b"29"), (b"29", b"29")):
            with self.subTest(first=first, second=second):
                extra = b"Content-Length: " + first + b"\r\nContent-Length: " + second + b"\r\nConnection: close\r\n"
                resp = self._raw_post(extra, body=body)
                self.assertTrue(resp, "empty response")
                code = int(resp.split(b"\r\n", 1)[0].decode("latin-1").split()[1])
                self.assertEqual(code, 400)
                s2, _, cur = webclient.request(self.port, "GET", "/api/state", headers={"X-OPOS-Token": self.tok})
                self.assertEqual(s2, 200)
                self.assertEqual(cur, saved)
                self.assertFalse(phone.charging)
        resp = self._raw_post(b"Content-Length: 29\r\nConnection: close\r\n", body=body)
        self.assertTrue(resp, "empty response")
        code = int(resp.split(b"\r\n", 1)[0].decode("latin-1").split()[1])
        self.assertEqual(code, 200)
        self.assertTrue(phone.charging)

    def test_bad_json_shapes(self) -> None:
        """Non-object JSON bodies are rejected."""
        for b in (b"[]", b"null", b"42", b'"x"', b"{bad", b"\xff\xfe", b'{"action": NaN}'):
            h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json", "X-OPOS-Token": self.tok}
            s, hd, rb = webclient.request(self.port, "POST", "/api", body=b, headers=h)
            self.assertEqual(s, 400)
            _safe(s, hd, rb)
        for obj in ({"action": 5}, {"action": ["tick"]}, {}):
            s, _ = _body(self.port, self.tok, obj)
            self.assertEqual(s, 400)
        s, _ = _body(self.port, self.tok, {"action": "nope"})
        self.assertEqual(s, 400)

    def test_errors_safe(self) -> None:
        """Error bodies are JSON without tracebacks."""
        s, h, b = webclient.request(self.port, "GET", "/nope")
        self.assertGreaterEqual(s, 400)
        _safe(s, h, b)
        obj = json.loads(b.decode())
        self.assertIn("error", obj)
        self.assertLess(len(str(obj["error"])), 200)
        s, h, b = webclient.request(self.port, "GET", "/api/state", headers={"Host": "evil.example"})
        _safe(s, h, b)
        obj = json.loads(b.decode())
        self.assertIn("error", obj)


class TestWebHardening(unittest.TestCase):
    """Part 2 hardened web checks."""
    def setUp(self) -> None:
        """Start one server for the test."""
        self._run = webclient.Running()
        self._run.__enter__()
        self.port: int = self._run.port
        self.tok: str = webclient.fetch_token(self.port)
    def tearDown(self) -> None:
        """Stop the server."""
        self._run.__exit__(None, None, None)
    def _root(self) -> tuple[int, dict, bytes]:
        """Fetch GET / once."""
        return webclient.request(self.port, "GET", "/")
    def _std(self, h: dict) -> None:
        """Check base headers present."""
        self.assertEqual(h.get("x-content-type-options"), "nosniff")
        self.assertEqual(h.get("cache-control"), "no-store")
        self.assertEqual(h.get("x-frame-options"), "DENY")
    def test_headers_root(self) -> None:
        """Root sends CSP and nonce headers."""
        s, h, b = self._root()
        self.assertEqual(s, 200)
        csp = h.get("content-security-policy", "")
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'nonce-", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("script-src 'unsafe-inline'", csp)
        self.assertEqual(h.get("x-content-type-options"), "nosniff")
        self.assertEqual(h.get("cache-control"), "no-store")
        self.assertEqual(h.get("x-frame-options"), "DENY")
        m = re.search(r"nonce-([A-Za-z0-9_\-]+)", csp)
        self.assertIsNotNone(m)
        nonce = m.group(1)  # type: ignore[union-attr]
        tags = re.findall(rb'<(?:script|style)\b[^>]*>', b)
        self.assertTrue(tags)
        for t in tags:
            mm = re.search(rb'nonce="([^"]+)"', t)
            self.assertIsNotNone(mm)
            self.assertEqual(mm.group(1).decode("ascii"), nonce)  # type: ignore[union-attr]
        s2, h2, _ = self._root()
        self.assertEqual(s2, 200)
        m2 = re.search(r"nonce-([A-Za-z0-9_\-]+)", h2.get("content-security-policy", ""))
        self.assertIsNotNone(m2)
        self.assertNotEqual(m.group(1), m2.group(1))  # type: ignore[union-attr]
        other = webclient.Running()
        other.__enter__()
        try:
            t1 = webclient.fetch_token(self.port)
            t2 = webclient.fetch_token(other.port)
            self.assertNotEqual(t1, t2)
        finally:
            other.__exit__(None, None, None)
    def test_headers_json_404_405(self) -> None:
        """JSON, 404 and 405 carry base headers."""
        s, h, _ = webclient.request(self.port, "GET", "/api/state", headers={"X-OPOS-Token": self.tok})
        self.assertEqual(s, 200)
        self._std(h)
        s, h, _ = webclient.request(self.port, "GET", "/nope")
        self.assertEqual(s, 404)
        self._std(h)
        s, h, _ = webclient.request(self.port, "PUT", "/")
        self.assertEqual(s, 405)
        self._std(h)
    def test_html_static_safe(self) -> None:
        """Page has no inline handlers or remote URLs."""
        _, _, b = self._root()
        self.assertIsNone(re.search(rb"\son[a-z]+=", b))
        self.assertNotIn(b"javascript:", b.lower())
        self.assertIsNone(re.search(rb'''(?:src|href)\s*=\s*["']https?://''', b.lower()))
    def test_why_handler_uses_answer(self) -> None:
        """Why handler writes to answer box."""
        _, _, b = self._root()
        self.assertIn(b"getElementById('answer')", b)
        self.assertNotIn(b"getElementById('why').textContent=r.text", b)
    def test_bind_validation(self) -> None:
        """Non-loopback binds are rejected."""
        for host in ("0.0.0.0", "", "192.0.2.1"):
            with self.assertRaises(ValueError):
                web.make_server(0, web.Phone(), host=host)
        try:
            srv = web.make_server(0, web.Phone(), host="::1")
        except OSError:
            self.skipTest("no ipv6")
            return
        srv.server_close()
    def test_loopback_address(self) -> None:
        """Server binds loopback only."""
        self.assertEqual(self._run.server.server_address[0], "127.0.0.1")
    def test_xss_stored_not_rendered(self) -> None:
        """Injected text round-trips in JSON not HTML."""
        evil = '<img src=x onerror=alert(1)><script>alert(2)</script>"\''
        s, _, _ = webclient.post_action(self.port, "receive", self.tok, number="+15551230000", text=evil)
        self.assertEqual(s, 200)
        _, _, b = webclient.request(self.port, "GET", "/api/state", headers={"X-OPOS-Token": self.tok})
        state = b.decode("utf-8")
        found = json.loads(state)
        self.assertIn(evil, self._all_str(found))
        _, _, html = self._root()
        self.assertNotIn(evil.encode(), html)
        i = html.find(b"esc")
        self.assertGreaterEqual(i, 0)
        snippet = html[i:i + 3000]
        for e in (b"&amp;", b"&lt;", b"&gt;", b"&quot;", b"&#39;"):
            self.assertIn(e, snippet)
    def _all_str(self, o: object) -> list[str]:
        """Collect all strings in JSON value."""
        out: list[str] = []
        if isinstance(o, str):
            return [o]
        if isinstance(o, dict):
            for v in o.values():
                out += self._all_str(v)
        elif isinstance(o, list):
            for v in o:
                out += self._all_str(v)
        return out
    def test_real_device_block(self) -> None:
        """Real device subclass blocks receive only."""
        class RealPhone(web.Phone):
            """Device-like phone."""
            pass
        run = webclient.Running(phone=RealPhone())
        run.__enter__()
        try:
            tok = webclient.fetch_token(run.port)
            s, _, _ = webclient.post_action(run.port, "receive", tok, number="+15551230000", text="hi")
            self.assertEqual(s, 403)
            s, _, _ = webclient.post_action(run.port, "tick", tok)
            self.assertEqual(s, 200)
            s, _, _ = webclient.request(run.port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
            self.assertEqual(s, 200)
        finally:
            run.__exit__(None, None, None)
    def test_f038_device_rejections_have_no_side_effects(self) -> None:
        """Rejected device sends leave modem and message state unchanged."""
        class RealPhone(web.Phone):
            """Device-like phone."""
            pass
        run = webclient.Running(phone=RealPhone())
        run.__enter__()
        try:
            tok = webclient.fetch_token(run.port)
            phone = run.server.RequestHandlerClass.phone
            num = "+15551230000"
            txt = "f038"
            base = {"Host": f"127.0.0.1:{run.port}", "Content-Type": "application/json", "X-OPOS-Token": tok}
            send_obj = {"action": "send", "number": num, "text": txt}
            seed_num1 = "+1555" + "1230001"
            seed_num2 = "+1555" + "1230002"
            seed_in = Message(number=seed_num1, text="seed incoming", outgoing=False, sent=False)
            seed_pend = Message(number=seed_num2, text="seed pending", outgoing=True, sent=False)
            phone.essentials.log.append(seed_in)
            phone.essentials.log.append(seed_pend)
            phone.essentials.outbox.append(seed_pend)
            log_before = deepcopy(phone.essentials.log)
            outbox_before = deepcopy(phone.essentials.outbox)
            events_before = list(phone.modem.events)
            status_before = phone.status
            def _clean() -> None:
                self.assertEqual(list(phone.modem.events), events_before)
                self.assertEqual(list(phone.essentials.log), log_before)
                self.assertEqual(list(phone.essentials.outbox), outbox_before)
                self.assertEqual(phone.status, status_before)
            def _hardened(h: dict) -> None:
                self.assertEqual(h.get("x-content-type-options"), "nosniff")
                self.assertEqual(h.get("x-frame-options"), "DENY")
                self.assertEqual(h.get("cache-control"), "no-store")
            def _send(h: dict, obj: dict) -> tuple[int, dict, bytes]:
                raw = json.dumps(obj).encode()
                return webclient.request(run.port, "POST", "/api", body=raw, headers=h)
            bad = tok[:-1] + ("A" if tok[-1:] != "A" else "B")
            no_tok = {"Host": base["Host"], "Content-Type": base["Content-Type"]}
            empty_tok = dict(base)
            empty_tok["X-OPOS-Token"] = ""
            wrong_tok = dict(base)
            wrong_tok["X-OPOS-Token"] = bad
            bad_host = dict(base)
            bad_host["Host"] = "evil.example"
            bad_origin = dict(base)
            bad_origin["Origin"] = "http://evil.example"
            bad_ct = dict(base)
            bad_ct["Content-Type"] = "text/plain"
            cases = [
                ("missing token", no_tok, send_obj, 403),
                ("empty token", empty_tok, send_obj, 403),
                ("wrong token", wrong_tok, send_obj, 403),
                ("hostile host", bad_host, send_obj, 403),
                ("hostile origin", bad_origin, send_obj, 403),
                ("content-type", bad_ct, send_obj, 415),
            ]
            for name, hdrs, obj, want in cases:
                with self.subTest(name):
                    s, h, _ = _send(hdrs, obj)
                    self.assertEqual(s, want)
                    _hardened(h)
                    _clean()
            with self.subTest("receive blocked"):
                s, h, _ = _send(dict(base), {"action": "receive", "number": num, "text": txt})
                self.assertEqual(s, 403)
                _hardened(h)
                _clean()
            hdl = run.server.RequestHandlerClass
            limiter = getattr(hdl, "limiter", None)
            if limiter is None:
                limiter = getattr(run.server, "limiter", None)
            self.assertIsNotNone(limiter)
            for target in ("post", "send"):
                orig = limiter.allow
                def _allow(*a: object, **kw: object) -> bool:
                    for v in list(a) + list(kw.values()):
                        if target in str(v):
                            return False
                    return True
                limiter.allow = _allow  # type: ignore[method-assign]
                try:
                    with self.subTest(f"rate {target}"):
                        s, h, _ = _send(dict(base), send_obj)
                        self.assertEqual(s, 429)
                        _hardened(h)
                        _clean()
                finally:
                    limiter.allow = orig  # type: ignore[method-assign]
            ok = dict(base)
            ok["Origin"] = f"http://127.0.0.1:{run.port}"
            s, _, _ = _send(ok, send_obj)
            self.assertEqual(s, 200)
            self.assertEqual(list(phone.modem.events), events_before + [("sms", num, txt)])
            logged = list(phone.essentials.log)
            self.assertEqual(logged[:-1], log_before)
            self.assertEqual(len(logged), len(log_before) + 1)
            last = logged[-1]
            self.assertEqual(last.number, num)
            self.assertEqual(last.text, txt)
            self.assertTrue(last.outgoing)
            self.assertTrue(last.sent)
            self.assertEqual(Message(number=num, text=txt, outgoing=True, sent=True).number, last.number)
            self.assertEqual(list(phone.essentials.outbox), outbox_before)
        finally:
            run.__exit__(None, None, None)
    def test_rate_limits(self) -> None:
        """POST bursts hit 429, GET still works."""
        hit = False
        for _ in range(100):
            s, _, _ = webclient.post_action(self.port, "tick", self.tok)
            if s == 429:
                hit = True
                break
        self.assertTrue(hit)
        s, _, _ = webclient.request(self.port, "GET", "/api/state", headers={"X-OPOS-Token": self.tok})
        self.assertEqual(s, 200)
        run = webclient.Running()
        run.__enter__()
        try:
            tok = webclient.fetch_token(run.port)
            seen = False
            for _ in range(12):
                s, _, _ = webclient.post_action(run.port, "send", tok, number="+15550000", text="hi")
                if s == 429:
                    seen = True
                    break
            self.assertTrue(seen)
        finally:
            run.__exit__(None, None, None)
    def test_string_bounds(self) -> None:
        """Overlong fields are rejected."""
        s, _, _ = webclient.post_action(self.port, "receive", self.tok, number="1" * 65, text="hi")
        self.assertEqual(s, 400)
        s, _, _ = webclient.post_action(self.port, "receive", self.tok, number="+15550000", text="x" * 1001)
        self.assertEqual(s, 400)
        s, _, _ = webclient.post_action(self.port, "receive", self.tok, number="+15550000", text="x" * 1000)
        self.assertEqual(s, 200)
    def test_in_use_rejects_bad_shape(self) -> None:
        """Non-string in_use name is rejected without a crash."""
        s, _, _ = webclient.post_action(self.port, "in_use", self.tok, name=5)
        self.assertEqual(s, 400)
        s, _, _ = webclient.request(self.port, "GET", "/api/state", headers={"X-OPOS-Token": self.tok})
        self.assertEqual(s, 200)
    def test_slow_client(self) -> None:
        """Partial request is dropped, server survives."""
        part = b"POST /api HTTP/1.1\r\nHost: 127.0.0.1:" + str(self.port).encode() + b"\r\n"
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.settimeout(15)
        out: list[int] = []
        def _probe() -> None:
            """Fetch root on second connection."""
            r, _, _ = webclient.request(self.port, "GET", "/")
            out.append(r)
        t = threading.Thread(target=_probe, daemon=True)
        try:
            s.sendall(part)
            t.start()
            t.join(timeout=10)
            self.assertEqual(out, [200])
            t0 = time.monotonic()
            try:
                data = s.recv(4096)
            except socket.timeout:
                data = b""
            dt = time.monotonic() - t0
            self.assertLess(dt, 15)
            self.assertEqual(data, b"")
        finally:
            try:
                s.close()
            except OSError:
                pass
            t.join(timeout=5)
    def test_log_no_secrets(self) -> None:
        """Rejected token log hides secrets."""
        bad = "bad-token-abc123"
        body = json.dumps({"action": "tick", "marker": "mzxqy"}).encode()
        h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json", "X-OPOS-Token": bad}
        with self.assertLogs("opos.security", level="WARNING") as cm:
            webclient.request(self.port, "POST", "/api", body=body, headers=h)
        msgs = [r.getMessage() for r in cm.records]
        self.assertIn("rejected: token", msgs)
        blob = "\n".join(cm.output)
        self.assertNotIn(bad, blob)
        self.assertNotIn("mzxqy", blob)


class TestWebSlowDoS(unittest.TestCase):
    """Slow-client and connection-cap checks."""
    def test_drip_closed_by_deadline(self) -> None:
        """Slow drip is closed within the deadline."""
        with webclient.Running() as run:
            run.server.RequestHandlerClass.DEADLINE = 1.0
            port = run.port
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.settimeout(5)
            closed = False
            try:
                t0 = time.monotonic()
                while time.monotonic() - t0 < 2.5:
                    try:
                        s.sendall(b"G")
                    except OSError:
                        closed = True
                        break
                    try:
                        s.settimeout(0.05)
                        if s.recv(1) == b"":
                            closed = True
                            break
                    except (OSError, socket.timeout):
                        pass
                    time.sleep(0.2)
                s.settimeout(5)
                if not closed:
                    try:
                        s.sendall(b"G")
                        d = s.recv(1)
                        closed = d == b""
                    except OSError:
                        closed = True
            finally:
                try:
                    s.close()
                except OSError:
                    pass
            self.assertTrue(closed)
            st, _, _ = webclient.request(port, "GET", "/")
            self.assertEqual(st, 200)
    def test_max_conns(self) -> None:
        """Third idle connection is refused at cap."""
        old = web._Server.MAX_CONNS
        web._Server.MAX_CONNS = 2
        try:
            with webclient.Running() as run:
                port = run.port
                a = socket.create_connection(("127.0.0.1", port), timeout=5)
                b = socket.create_connection(("127.0.0.1", port), timeout=5)
                try:
                    time.sleep(0.3)
                    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    c.settimeout(5)
                    try:
                        c.connect(("127.0.0.1", port))
                        c.settimeout(2)
                        try:
                            got = c.recv(4096)
                        except (OSError, socket.timeout):
                            got = b"__timeout__"
                        self.assertEqual(got, b"")
                    finally:
                        c.close()
                    self.assertEqual(a.fileno() >= 0, True)
                    self.assertEqual(b.fileno() >= 0, True)
                finally:
                    try:
                        a.close()
                    except OSError:
                        pass
                    try:
                        b.close()
                    except OSError:
                        pass
                time.sleep(0.5)
                st, _, _ = webclient.request(port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
                self.assertEqual(st, 200)
        finally:
            web._Server.MAX_CONNS = old
    def test_queue_size(self) -> None:
        """Listen queue size is configured on class."""
        self.assertEqual(web._Server.request_queue_size, 16)


class TestWebLockFree(unittest.TestCase):
    """Slow-reader must not hold the lock."""
    def test_lock_free_writes(self) -> None:
        """Blocked writer does not stall other requests."""
        with webclient.Running() as run:
            port = run.port
            phone = run.server.RequestHandlerClass.phone
            for i in range(150):
                phone.receive("+15550001000", "x" * 900 + str(i))
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                s.settimeout(5)
                s.sendall(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-OPOS-Token: %s\r\nConnection: close\r\n\r\n" % (port, run.token.encode()))
                head = b""
                while b"\r\n\r\n" not in head:
                    ch = s.recv(4096)
                    if not ch:
                        break
                    head += ch
                self.assertIn(b"200", head)
                t0 = time.monotonic()
                st, _, body = webclient.request(port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
                dt = time.monotonic() - t0
                self.assertEqual(st, 200)
                self.assertLess(dt, 2)
                self.assertTrue(body)
            finally:
                try:
                    s.close()
                except OSError:
                    pass


class TestDeadlineShutdown(unittest.TestCase):
    """Deadline must terminate buffered readers and reclaim slots."""

    @mock.patch.object(web, "self_rss_mb", new=lambda: 40)
    @mock.patch.object(web.assist, "free_mb", new=lambda: 512)
    def test_makefile_eof(self) -> None:
        """Makefile-backed read gets EOF/reset quickly, not timeout."""
        with webclient.Running() as run:
            run.server.RequestHandlerClass.DEADLINE = 1.0
            port = run.port
            s, _, body = webclient.request(port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
            self.assertEqual(s, 200)
            saved = body
            obj = json.loads(saved.decode())
            self.assertIn("status", obj)
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                sock.settimeout(5)
                sock.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % port)
                mf = sock.makefile("rb")
                t0 = time.monotonic()
                err = None
                data = None
                try:
                    data = mf.readline()
                except socket.timeout as e:
                    err = e
                except OSError as e:
                    err = e
                    data = b""
                dt = time.monotonic() - t0
                if isinstance(err, socket.timeout):
                    self.fail(f"client timed out after {dt:.2f}s, expected EOF/reset")
                self.assertIsNotNone(data)
                self.assertEqual(data, b"")
                self.assertLess(dt, 3.0, f"expiry too slow {dt:.2f}s")
                s2, _, b2 = webclient.request(port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
                self.assertEqual(s2, 200)
                self.assertEqual(b2, saved)
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

    @mock.patch.object(web, "self_rss_mb", new=lambda: 40)
    @mock.patch.object(web.assist, "free_mb", new=lambda: 512)
    def test_slow_drip_still_expires(self) -> None:
        """Slow drip must not prevent deadline expiry."""
        with webclient.Running() as run:
            run.server.RequestHandlerClass.DEADLINE = 1.0
            port = run.port
            s, _, saved = webclient.request(port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
            self.assertEqual(s, 200)
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                sock.settimeout(5)
                sock.sendall(b"G")
                mf = sock.makefile("rb")
                t0 = time.monotonic()
                result: dict = {}
                def reader() -> None:
                    try:
                        result["data"] = mf.readline()
                        result["err"] = None
                    except Exception as e:
                        result["err"] = e
                        result["data"] = None
                    result["dt"] = time.monotonic() - t0
                th = threading.Thread(target=reader, daemon=True)
                th.start()
                for _ in range(8):
                    time.sleep(0.2)
                    if not th.is_alive():
                        break
                    try:
                        sock.sendall(b"X")
                    except OSError:
                        break
                th.join(timeout=3)
                self.assertFalse(th.is_alive(), "deadline did not interrupt slow drip")
                if isinstance(result.get("err"), socket.timeout):
                    self.fail(f"slow drip timed out after {result.get('dt')}")
                if result.get("err") is None:
                    self.assertEqual(result.get("data"), b"")
                else:
                    self.assertIsInstance(result.get("err"), OSError)
                self.assertLess(result.get("dt", 10), 3.0)
                s2, _, b2 = webclient.request(port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
                self.assertEqual(s2, 200)
                self.assertEqual(b2, saved)
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

    @mock.patch.object(web, "self_rss_mb", new=lambda: 40)
    @mock.patch.object(web.assist, "free_mb", new=lambda: 512)
    def test_f040_bounded_admission_deadline_and_recovery(self) -> None:
        """Both stalls admit, reject excess, expire, and recover."""
        old = web._Server.MAX_CONNS
        web._Server.MAX_CONNS = 2
        try:
            with webclient.Running() as run:
                run.server.RequestHandlerClass.DEADLINE = 1.0
                port = run.port
                s, _, saved = webclient.request(port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
                self.assertEqual(s, 200)
                part = b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % port
                socks: list = []
                try:
                    t0 = time.monotonic()
                    a = socket.create_connection(("127.0.0.1", port), timeout=5)
                    socks.append(a)
                    b = socket.create_connection(("127.0.0.1", port), timeout=5)
                    socks.append(b)
                    a.sendall(part)
                    b.sendall(part)
                    for sck in (a, b):
                        sck.settimeout(0.3)
                        try:
                            got = sck.recv(4096)
                        except socket.timeout:
                            continue
                        except OSError as e:
                            self.fail(f"held client closed early: {e!r}")
                        self.fail(f"held client got {got!r}, expected timeout")
                    t1 = time.monotonic()
                    remain = 1.0 - (t1 - t0)
                    self.assertGreater(remain, 0.0)
                    c = socket.create_connection(("127.0.0.1", port), timeout=5)
                    socks.append(c)
                    try:
                        c.settimeout(remain)
                        t2 = time.monotonic()
                        try:
                            data = c.recv(4096)
                        except socket.timeout:
                            self.fail("excess connection not rejected before deadline")
                        except OSError:
                            data = b""
                        dt = time.monotonic() - t2
                        self.assertLess(dt, 1.0)
                        self.assertEqual(data, b"")
                    finally:
                        try:
                            c.close()
                        except OSError:
                            pass
                        socks.remove(c)
                    for sck in (a, b):
                        left = 3.0 - (time.monotonic() - t0)
                        self.assertGreater(left, 0.0)
                        sck.settimeout(left)
                        try:
                            d = sck.recv(1024)
                        except socket.timeout:
                            self.fail("held socket still open past deadline")
                        except OSError:
                            continue
                        self.assertEqual(d, b"")
                    self.assertLess(time.monotonic() - t0, 3.0)
                    for sck in (a, b):
                        try:
                            sck.close()
                        except OSError:
                            pass
                    socks.remove(a)
                    socks.remove(b)
                    d = socket.create_connection(("127.0.0.1", port), timeout=5)
                    socks.append(d)
                    e = socket.create_connection(("127.0.0.1", port), timeout=5)
                    socks.append(e)
                    d.sendall(part)
                    e.sendall(part)
                    for sck in (d, e):
                        sck.settimeout(0.3)
                        try:
                            got = sck.recv(4096)
                        except socket.timeout:
                            continue
                        except OSError as err:
                            self.fail(f"replacement closed early: {err!r}")
                        self.fail(f"replacement got {got!r}, expected timeout")
                    for sck in (d, e):
                        sck.settimeout(5)
                        sck.sendall(b"Connection: close\r\n\r\n")
                        resp = b""
                        while b"\r\n\r\n" not in resp:
                            chunk = sck.recv(4096)
                            if not chunk:
                                break
                            resp += chunk
                        self.assertIn(b" 200 ", resp.split(b"\r\n", 1)[0])
                    s2, _, b2 = webclient.request(port, "GET", "/api/state", headers={"X-OPOS-Token": run.token})
                    self.assertEqual(s2, 200)
                    self.assertEqual(b2, saved)
                    obj = json.loads(b2.decode())
                    self.assertIn("status", obj)
                finally:
                    for sck in socks:
                        try:
                            sck.close()
                        except OSError:
                            pass
        finally:
            web._Server.MAX_CONNS = old

    def test_deadline_override_is_isolated_between_running_instances(self) -> None:
        """Deadline override does not leak to later servers."""
        saved = web.Handler.DEADLINE
        with webclient.Running() as first:
            first_cls = first.server.RequestHandlerClass
            first_cls.DEADLINE = 1.0
            self.assertEqual(web.Handler.DEADLINE, saved)
        with webclient.Running() as second:
            second_cls = second.server.RequestHandlerClass
            self.assertIsNot(first_cls, second_cls)
            self.assertEqual(second_cls.DEADLINE, saved)
            self.assertEqual(first_cls.DEADLINE, 1.0)
            self.assertEqual(web.Handler.DEADLINE, saved)


if __name__ == "__main__":
    unittest.main()
