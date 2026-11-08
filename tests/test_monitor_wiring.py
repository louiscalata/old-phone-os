# SPDX-License-Identifier: GPL-3.0-or-later
"""The Inference page as the server serves it: GET /monitor, GET /api/monitor and the Settings link, all behind OPOS_MONITOR=1.

The drop-in's own tests (test_monitor_net, test_monitor_view, test_monitor_page) cover the data and the document; these
cover the wiring in opos.web, so a new revision of the drop-in is a file swap and these keep passing.
"""
import http.client
import json
import os
import re
import secrets
import socket
import threading
import unittest
from pathlib import Path
from unittest import mock

from opos import monitor, monitor_web, notices, secure, web
from opos.span import parse as parse_span

ROOT = Path(__file__).resolve().parents[1]
MONITOR_FILES = ("src/opos/monitor.py", "src/opos/monitor_web.py", "src/opos/web_static/monitor-theme.js",
                 "src/opos/web_static/monitor.css", "src/opos/web_static/monitor.html", "src/opos/web_static/monitor.js")
VIEW_KEYS = {"version", "title", "updatedAgoSeconds", "sections"}
SECTION_KEYS = {"id", "title", "status", "glyph", "lines", "facts", "pre"}


_ASSETS = {}
_READ_ASSET = web._asset


def cached_asset(name):
    """web._asset, read once per process: page() reads about fifty files, which is slow on some file systems."""
    if name not in _ASSETS:
        _ASSETS[name] = _READ_ASSET(name)
    return _ASSETS[name]


def clean_env(**extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_")}
    e.update(extra)
    return e


def request(port, path, headers=None, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        c.close()


def raw_exchange(port, request):
    """Send these exact bytes and return everything the server sends back until it closes: head and body unparsed.

    http.client throws away what follows the headers of a HEAD response, so it cannot tell a server that wrote a body
    from one that did not; a socket can.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=10) as c:
        c.sendall(request)
        data = b""
        while True:
            chunk = c.recv(65536)
            if not chunk:
                return data
            data += chunk


class FakeClock:
    """A clock the test moves by hand; secure.RateLimiter takes one as its third argument."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def nonce_of(headers):
    return re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", headers["content-security-policy"]).group(1)


class FakeMonitor:
    """Stands where opos.monitor.Monitor stands and records how snapshot() was called."""

    def __init__(self):
        self.calls = []
        self.lock_free = []

    def snapshot(self, local_state=None):
        self.calls.append(local_state)
        self.lock_free.append(not web.Handler.lock.locked())
        return {"version": 1, "title": "Inference", "updatedAgoSeconds": 0, "sections": []}


class FakeSim:
    def __init__(self, health):
        self.health = health


class FakePhone:
    """Only what the monitor route reads: state() (under the lock) and sim.health."""

    def __init__(self, state, health=None):
        self._state = state
        self.sim = FakeSim(health)
        self.lock_held = []

    def state(self):
        self.lock_held.append(web.Handler.lock.locked())
        return self._state


def live_state(percent=63, source="sysfs", health_source="sysfs"):
    return {"battery": {"percent": percent}, "battery_source": {"source": source, "health_source": health_source}}


class Server:
    """The real server in a thread; the environment is whatever the caller has patched in."""

    def __init__(self, phone=None, span=None):
        self.token = secrets.token_urlsafe(32)
        self.srv = web.make_server(0, phone if phone is not None else web.Phone(), token=self.token, span=span)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def get(self, path, headers=None, method="GET", token=True):
        h = dict(headers or {})
        if token is True:
            h.setdefault("X-OPOS-Token", self.token)
        elif token:
            h["X-OPOS-Token"] = token
        return request(self.port, path, h, method)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class WiringCase(unittest.TestCase):
    ENV = {}

    def setUp(self):
        patcher = mock.patch.dict(os.environ, clean_env(**self.ENV), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        assets = mock.patch.object(web, "_asset", cached_asset)
        assets.start()
        self.addCleanup(assets.stop)

    def server(self, **kw):
        s = Server(**kw)
        self.addCleanup(s.close)
        return s

    def fake_monitor(self):
        fake = FakeMonitor()
        patcher = mock.patch.object(monitor, "get", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class SwitchOff(WiringCase):
    """Unset, or any value but exactly 1: the pages are what they were and the two routes are not there."""

    def test_the_routes_answer_like_any_unknown_path(self):
        s = self.server()
        for value in (None, "", "0", "true", "yes", "on", "11", " 1", "1 "):
            env = {} if value is None else {"OPOS_MONITOR": value}
            with mock.patch.dict(os.environ, env):
                unknown = s.get("/no-such-page")
                for path in ("/monitor", "/api/monitor", "/monitor?x=1"):
                    for method in ("GET", "HEAD"):
                        st, h, body = s.get(path, method=method)
                        self.assertEqual(st, 404, (value, path, method))
                        if method == "GET":
                            self.assertEqual(body, unknown[2], (value, path))
                            self.assertEqual(json.loads(body), {"error": "not found"})

    def test_the_main_page_is_what_page_makes_and_has_no_link(self):
        s = self.server()
        st, h, body = s.get("/")
        self.assertEqual(st, 200)
        self.assertEqual(body, web.page(s.token, nonce_of(h)).encode("utf-8"), "byte for byte")
        self.assertNotIn(b"/monitor", body)
        self.assertNotIn(b"mon-link", body)

    def test_a_value_other_than_1_adds_no_link_either(self):
        s = self.server()
        for value in ("0", "true", "", "yes"):
            with mock.patch.dict(os.environ, {"OPOS_MONITOR": value}):
                st, h, body = s.get("/")
                self.assertEqual(body, web.page(s.token, nonce_of(h)).encode("utf-8"), value)

    def test_the_monitor_is_never_asked_for_anything(self):
        fake = self.fake_monitor()
        s = self.server()
        s.get("/")
        s.get("/api/monitor")
        s.get("/monitor")
        self.assertEqual(fake.calls, [])


class SwitchOn(WiringCase):
    ENV = {"OPOS_MONITOR": "1"}

    def test_the_document(self):
        s = self.server()
        st, h, body = s.get("/monitor", token=False)  # the document itself needs no token; the data does
        self.assertEqual(st, 200)
        self.assertEqual(h["content-type"], "text/html; charset=utf-8")
        self.assertEqual(int(h["content-length"]), len(body))
        self.assertEqual(h["cache-control"], "no-store")
        self.assertEqual(h["x-frame-options"], "DENY")
        html = body.decode("utf-8")
        nonce = nonce_of(h)
        self.assertIn("style-src 'nonce-" + nonce + "'", h["content-security-policy"])
        tags = re.findall(r"<(?:script|style)\b[^>]*>", html)
        self.assertGreaterEqual(len(tags), 3, "theme script, style, page script")
        for tag in tags:
            self.assertRegex(tag, r'^<(?:script|style) nonce="' + re.escape(nonce) + '">$')
        self.assertEqual(len(re.findall(r"<(?:script|style)\b", html)), len(tags))
        meta = '<meta name="opos-token" content="' + s.token + '">'
        self.assertEqual(html.count(meta), 1)
        self.assertLess(html.index(meta), html.index("<title>Inference - OPOS</title>"), "the token meta comes before the title")
        self.assertNotIn("@@OPOS:", html)
        self.assertNotIn("/osk.js", html, "no text input on this page, so no keyboard")
        self.assertLess(len(body), 36 * 1024)

    def test_each_request_gets_its_own_nonce(self):
        s = self.server()
        a, b = s.get("/monitor")[1], s.get("/monitor")[1]
        self.assertNotEqual(nonce_of(a), nonce_of(b))

    def test_head_gives_the_headers_of_get_and_no_body(self):
        s = self.server()
        st, h, body = s.get("/monitor")
        hst, hh, hbody = s.get("/monitor", method="HEAD")
        self.assertEqual((hst, hbody), (200, b""))
        self.assertEqual(int(hh["content-length"]), len(body))
        self.assertEqual(hh["content-type"], h["content-type"])
        self.assertIn("nonce-", hh["content-security-policy"])

    def test_head_writes_no_bytes_after_the_headers(self):
        # http.client discards a HEAD response's body, so the test above passes even if the server writes one; count the bytes on the wire.
        s = self.server()
        get_length = len(s.get("/monitor")[2])
        self.assertGreater(get_length, 1000)
        host = "Host: 127.0.0.1:%d\r\n" % s.port
        data = raw_exchange(s.port, ("HEAD /monitor HTTP/1.1\r\n" + host + "Connection: close\r\n\r\n").encode("ascii"))
        head, sep, rest = data.partition(b"\r\n\r\n")
        self.assertTrue(sep, data[:200])
        self.assertTrue(head.startswith(b"HTTP/1.") and head.split(b"\r\n")[0].split()[1] == b"200", head.split(b"\r\n")[0])
        self.assertIn(("content-length: %d" % get_length).encode("ascii"), head.lower(), "the length GET would send, in the header only")
        self.assertEqual(rest, b"", "nothing after the blank line that ends the headers")
        data = raw_exchange(s.port, ("HEAD /api/monitor HTTP/1.1\r\n" + host + "X-OPOS-Token: " + s.token + "\r\nConnection: close\r\n\r\n").encode("ascii"))
        head, sep, rest = data.partition(b"\r\n\r\n")
        self.assertTrue(sep and head.split(b"\r\n")[0].split()[1] == b"405", head.split(b"\r\n")[0])
        self.assertEqual(rest, b"")

    def test_a_query_string_does_not_change_the_route(self):
        s = self.server()
        self.assertEqual(s.get("/monitor?x=1")[0], 200)

    def test_the_osk_is_not_injected_into_the_document_but_is_on_the_home_page(self):
        with mock.patch.dict(os.environ, {"OPOS_OSK": "1"}):
            s = self.server()
            self.assertNotIn(b"osk.js", s.get("/monitor")[2])
            self.assertIn(b"osk.js", s.get("/")[2])

    def test_spanned_layout_when_a_span_is_configured(self):
        plain = self.server().get("/monitor")[2].decode("utf-8")
        self.assertIn("(horizontal-viewport-segments:2)", plain, "no span: the document as the drop-in wrote it")
        self.assertNotIn("--pane", plain)
        for text in ("675,42", "675,60"):
            span = parse_span(text)
            s = self.server(span=span)
            st, h, body = s.get("/monitor")
            html = body.decode("utf-8")
            self.assertEqual(st, 200)
            self.assertNotIn("horizontal-viewport-segments", html, "Chromium on Linux reports no segments: the query is a width test")
            self.assertIn("@media(min-width:", html)
            self.assertIn(":root{--pane:675px;--hinge:%spx}" % text.split(",")[1], html, "the configured hinge, not just the default 42 px")
            self.assertLess(html.rindex(":root{--pane"), html.index("</style>"), "inside the style sheet")
            nonce = nonce_of(h)
            for tag in re.findall(r"<(?:script|style)\b[^>]*>", html):
                self.assertEqual(tag[tag.index(" "):-1], ' nonce="%s"' % nonce, "the rewrite's own script is under the nonce too")
            meta = '<meta name="opos-token" content="' + s.token + '">'
            self.assertLess(html.index(meta), html.index("<title>"))
            self.assertLess(len(body), 36 * 1024)

    def test_a_foreign_host_is_refused(self):
        fake = self.fake_monitor()
        s = self.server()
        for path in ("/monitor", "/api/monitor"):
            for host in ("evil.example", "evil.example:%d" % s.port, "127.0.0.1", "127.0.0.1:1", "localhost"):
                for method in ("GET", "HEAD"):
                    self.assertEqual(s.get(path, {"Host": host}, method=method)[0], 403, (path, host, method))
        self.assertEqual(s.get("/monitor", {"Host": "localhost:%d" % s.port})[0], 200, "localhost with the right port is fine")
        self.assertEqual(fake.calls, [])

    def test_the_data_needs_the_session_token(self):
        fake = self.fake_monitor()
        s = self.server()
        self.assertEqual(s.get("/api/monitor", token=False)[0], 403)
        self.assertEqual(s.get("/api/monitor", token=False, headers={"X-OPOS-Token": ""})[0], 403)
        self.assertEqual(s.get("/api/monitor", token=secrets.token_urlsafe(32))[0], 403)
        self.assertEqual(s.get("/api/monitor", token=s.token[:-1])[0], 403)
        self.assertEqual(s.get("/api/monitor", token=False, headers={"X-OPOS-Token": s.token + "x"})[0], 403)
        self.assertEqual(fake.calls, [], "refused before the monitor is asked")
        self.assertEqual(s.get("/api/monitor")[0], 200)
        self.assertEqual(len(fake.calls), 1)

    def test_the_data_has_the_documented_shape(self):
        real = monitor.Monitor(env={}, probe=lambda e: ("none", []), fetch=lambda r: self.fail("nothing is fetched by default"))
        with mock.patch.object(monitor, "get", return_value=real):
            s = self.server()
            st, h, body = s.get("/api/monitor")
        self.assertEqual(st, 200)
        self.assertEqual(h["content-type"], "application/json")
        self.assertEqual(h["cache-control"], "no-store")
        self.assertEqual(int(h["content-length"]), len(body))
        view = json.loads(body)
        self.assertEqual(set(view), VIEW_KEYS)
        self.assertEqual(view["version"], 1)
        self.assertEqual([x["id"] for x in view["sections"]], ["duo", "remote"])
        for sec in view["sections"]:
            self.assertEqual(set(sec), SECTION_KEYS)
        duo, remote = view["sections"]
        self.assertEqual(duo["status"], "NO MODEL")
        self.assertEqual(remote["status"], "NOT CONFIGURED", "nothing is fetched unless OPOS_AGIW_URL says so")
        facts = dict(map(tuple, duo["facts"]))
        self.assertEqual(facts["Battery"], "Not reported", "the demo's battery is a simulation, not this phone's")
        self.assertEqual(facts["Battery health"], "Not reported")
        self.assertEqual(facts["Battery source"], "Not reported")

    def test_the_snapshot_is_taken_under_the_lock_and_the_monitor_is_asked_outside_it(self):
        fake = self.fake_monitor()
        phone = FakePhone(live_state(), health=0.873)
        s = self.server(phone=phone)
        self.assertFalse(web.Handler.lock.locked())
        self.assertEqual(s.get("/api/monitor")[0], 200)
        self.assertEqual(phone.lock_held, [True], "phone.state() runs under Handler.lock")
        self.assertEqual(fake.lock_free, [True], "Monitor.snapshot() runs with the lock released")
        self.assertFalse(web.Handler.lock.locked())

    def test_a_slow_monitor_does_not_hold_up_the_phone(self):
        release = threading.Event()
        started = threading.Event()

        class Slow(FakeMonitor):
            def snapshot(self, local_state=None):
                started.set()
                release.wait(10)
                return super().snapshot(local_state)

        with mock.patch.object(monitor, "get", return_value=Slow()):
            s = self.server()
            t = threading.Thread(target=lambda: s.get("/api/monitor"), daemon=True)
            t.start()
            self.assertTrue(started.wait(10))
            try:
                self.assertEqual(s.get("/api/state")[0], 200, "the phone still answers while the monitor waits")
            finally:
                release.set()
                t.join(10)

    def test_local_state_reaches_the_monitor_as_derived(self):
        fake = self.fake_monitor()
        for state, health, want in (
            (live_state(63, "sysfs", "sysfs"), 0.873, {"percent": 63, "healthPercent": 87.3, "source": "sysfs"}),
            (live_state(41, "termux-api", "charge-counter"), 0.5, {"percent": 41, "source": "termux-api"}),  # an estimate: not shown
            (live_state(63, "sysfs", "assumed"), 1.0, {"percent": 63, "source": "sysfs"}),
            (live_state(63, "sysfs", "OPOS_BATTERY_HEALTH"), 0.9, {"percent": 63, "source": "sysfs"}),
            ({"battery": {"percent": 60}}, 0.6, {}),
        ):
            fake.calls.clear()
            s = self.server(phone=FakePhone(state, health))
            self.assertEqual(s.get("/api/monitor")[0], 200)
            self.assertEqual(fake.calls, [want], (state, health))

    def test_a_failed_document_is_a_500_and_a_log_line_not_half_a_page(self):
        real = web._asset
        s = self.server()
        for html in ("<!doctype html><html><head><title>Something else</title></head><body></body></html>", ""):
            with mock.patch.object(web, "_asset", side_effect=lambda n: html if n == "monitor.html" else real(n)):
                with self.assertLogs("opos.security", "WARNING") as logs:
                    st, h, body = s.get("/monitor")
            self.assertEqual(st, 500)
            self.assertEqual(json.loads(body), {"error": "internal error"})
            self.assertNotIn("opos-token", body.decode("utf-8"))
            self.assertTrue(any("monitor page not built" in line for line in logs.output), logs.output)
        with mock.patch.object(web, "_asset", side_effect=FileNotFoundError("monitor.html")):
            with self.assertLogs("opos.security", "WARNING") as logs:
                self.assertEqual(s.get("/monitor")[0], 500)
        self.assertEqual(s.get("/monitor")[0], 200, "and the next request is fine")

    def test_a_runtime_without_the_monitor_modules_serves_what_it_always_did(self):
        with mock.patch.object(web, "monitor_web", None), mock.patch.object(web, "monitor", None):
            s = self.server()
            self.assertEqual(s.get("/monitor")[0], 404)
            self.assertEqual(s.get("/api/monitor")[0], 404)
            st, h, body = s.get("/")
            self.assertEqual(body, web.page(s.token, nonce_of(h)).encode("utf-8"))


class Limits(WiringCase):
    ENV = {"OPOS_MONITOR": "1"}

    def test_the_bucket_exists_and_an_unknown_key_is_refused(self):
        limits = web._Limits()
        self.assertIn(monitor_web.LIMIT_KEY, limits._buckets)
        self.assertTrue(limits.allow(monitor_web.LIMIT_KEY))
        self.assertFalse(limits.allow("no-such-bucket"))
        self.assertFalse(limits.allow(""))

    def test_the_bucket_is_per_server_and_empties(self):
        a, b = web._Limits(), web._Limits()
        self.assertEqual(sum(a.allow(monitor_web.LIMIT_KEY) for _ in range(50)), 10)
        self.assertTrue(b.allow(monitor_web.LIMIT_KEY), "another server's bucket is full")

    def test_the_bucket_refills_at_two_a_second_and_never_holds_more_than_ten(self):
        # Fake clock, so the refill is measured and not slept for. RateLimiter reads its clock from its third argument, and
        # web._Limits builds it through secure.RateLimiter, so the clock goes in there. Steps are binary fractions: no rounding.
        clock = FakeClock()
        real = secure.RateLimiter
        with mock.patch.object(secure, "RateLimiter", lambda rate, burst: real(rate, burst, clock)):
            limits = web._Limits()

        def allow():
            return limits.allow(monitor_web.LIMIT_KEY)

        def drain():
            n = 0
            while allow():
                n += 1
                self.assertLessEqual(n, 10, "more than the burst in one go")
            return n

        self.assertEqual(drain(), 10, "the burst")
        clock.now += 0.25
        self.assertFalse(allow(), "a quarter second is half a token")
        clock.now += 0.25
        self.assertTrue(allow(), "half a second is one token at 2 a second")
        self.assertFalse(allow(), "and that token is spent")
        # Sustained: asked every quarter second the bucket lets every other request through, 20 in 10 seconds.
        passed = 0
        for _ in range(40):
            clock.now += 0.25
            passed += allow()
        self.assertEqual(passed, 20, "2 a second, sustained")
        # The page asks every 2 seconds: that is never refused, however long it goes on.
        drain()
        for _ in range(60):
            clock.now += 2.0
            self.assertTrue(allow())
        # A long quiet spell refills to the burst and no further.
        drain()
        clock.now += 3600
        self.assertEqual(drain(), 10)

    def test_the_route_answers_429_when_the_bucket_is_empty(self):
        fake = self.fake_monitor()
        s = self.server()
        limits = web._Limits()
        limits._buckets[monitor_web.LIMIT_KEY] = secure.RateLimiter(0.0001, 2)
        s.srv.RequestHandlerClass.limiter = limits
        self.assertEqual([s.get("/api/monitor")[0] for _ in range(4)], [200, 200, 429, 429])
        self.assertEqual(len(fake.calls), 2)
        st, h, body = s.get("/api/monitor")
        self.assertEqual(json.loads(body), {"error": "too many requests"})

    def test_a_refused_request_does_not_use_up_the_bucket(self):
        self.fake_monitor()
        s = self.server()
        limits = web._Limits()
        limits._buckets[monitor_web.LIMIT_KEY] = secure.RateLimiter(0.0001, 2)
        s.srv.RequestHandlerClass.limiter = limits
        for _ in range(6):
            self.assertEqual(s.get("/api/monitor", token=False)[0], 403)
            self.assertEqual(s.get("/api/monitor", {"Host": "evil.example"})[0], 403)
        self.assertEqual(s.get("/api/monitor")[0], 200)

    def test_a_server_without_the_bucket_fails_closed(self):
        fake = self.fake_monitor()
        s = self.server()
        limits = web._Limits()
        del limits._buckets[monitor_web.LIMIT_KEY]
        s.srv.RequestHandlerClass.limiter = limits
        self.assertEqual(s.get("/api/monitor")[0], 429)
        self.assertEqual(fake.calls, [])

    def test_head_on_the_data_is_refused_without_starting_a_collection(self):
        fake = self.fake_monitor()
        s = self.server()
        st, h, body = s.get("/api/monitor", method="HEAD")
        self.assertEqual((st, h["allow"], body), (405, "GET", b""))
        self.assertEqual(s.get("/api/monitor", method="HEAD", token=False)[0], 403, "the token is checked first")
        self.assertEqual(fake.calls, [])


class SettingsLink(WiringCase):
    def test_only_where_the_switch_is_on(self):
        with mock.patch.dict(os.environ, {"OPOS_MONITOR": "1"}):
            s = self.server()
            st, h, body = s.get("/")
        home = body.decode("utf-8")
        self.assertEqual(home.count('href="/monitor"'), 1)
        self.assertEqual(home.count(monitor_web.LINK), 1)
        self.assertEqual(home.count('aria-labelledby="set-about"'), 1)
        self.assertLess(home.index('href="/monitor"'), home.index('<section aria-labelledby="set-about">'), "ahead of About")
        self.assertEqual(home.replace(monitor_web.LINK, "", 1).encode("utf-8"), web.page(s.token, nonce_of(h)).encode("utf-8"),
                         "the link is the only difference")
        self.assertEqual(int(h["content-length"]), len(body))
        with mock.patch.dict(os.environ, {"OPOS_MONITOR": "0"}):
            self.assertNotIn(b'href="/monitor"', s.get("/")[2])

    def test_it_is_at_least_48px_tall_and_links_to_the_page(self):
        tag = re.search(r'<a id="mon-link"[^>]*>', monitor_web.LINK).group(0)
        self.assertIn('href="/monitor"', tag)
        self.assertRegex(tag, r"min-height:48px")
        self.assertRegex(tag, r"display:inline-block")
        self.assertNotRegex(monitor_web.LINK, r"<(?:script|style)\b|\bon\w+=", "no tag the page's CSP would need a nonce for")
        ids = re.findall(r'id="([^"]+)"', monitor_web.LINK)
        self.assertEqual(sorted(ids), ["mon-link", "set-inference"])
        self.assertTrue(all(i not in web.page() for i in ('id="%s"' % x for x in ids)), "no clash with an id on the page")

    def test_page_itself_does_not_grow_with_the_switch(self):
        base = web.page("t" * 43, "n" * 22, demo=True)
        with mock.patch.dict(os.environ, {"OPOS_MONITOR": "1"}):
            self.assertEqual(web.page("t" * 43, "n" * 22, demo=True), base, "the link is added when the page is served")

    def test_add_settings_link_needs_the_anchor_and_the_exact_value(self):
        page = "<p>x</p>" + monitor_web.ANCHOR + "<p>y</p>"
        self.assertEqual(monitor_web.add_settings_link(page, {"OPOS_MONITOR": "1"}), "<p>x</p>" + monitor_web.LINK + monitor_web.ANCHOR + "<p>y</p>")
        for env in ({}, {"OPOS_MONITOR": "0"}, {"OPOS_MONITOR": "true"}, {"OPOS_MONITOR": "1 "}):
            self.assertIs(monitor_web.add_settings_link(page, env), page)
        self.assertEqual(monitor_web.add_settings_link("<p>no settings</p>", {"OPOS_MONITOR": "1"}), "<p>no settings</p>")
        body = b"<p>x</p>"
        self.assertIs(monitor_web.link_bytes(body, {}), body)


class Document(unittest.TestCase):
    TEMPLATE = "<!doctype html><html><head><title>Inference - OPOS</title><style>\n/*c*/a{b:c}\n@media(horizontal-viewport-segments:2){x{y:z}}\n</style>\n</head><body><script>1</script></body></html>"

    def test_the_token_is_escaped_and_the_nonce_added(self):
        out = monitor_web.document(self.TEMPLATE, 'a"b<c', "N1", None)
        self.assertIn('<meta name="opos-token" content="a&quot;b&lt;c"><title>', out)
        self.assertIn('<script nonce="N1">1</script>', out)
        self.assertIn('<style nonce="N1">', out)

    def test_no_title_anchor_raises_and_nothing_is_returned(self):
        for template in ("<html><head></head></html>", self.TEMPLATE.replace("Inference - OPOS", "Inference"), ""):
            with self.assertRaises(ValueError):
                monitor_web.document(template, "t" * 43, "N1", None)
            with self.assertRaises(ValueError):
                monitor_web.document(template, "t" * 43, "N1", parse_span("675,42"))

    def test_a_bad_nonce_is_refused(self):
        with self.assertRaises(ValueError):
            monitor_web.document(self.TEMPLATE, "t" * 43, 'x"><script>', None)

    def test_with_a_span_the_head_ends_where_span_looks_for_it(self):
        out = monitor_web.document(self.TEMPLATE, "t" * 43, "N1", parse_span("675,60"))
        self.assertIn("(min-width:", out)
        self.assertNotIn("horizontal-viewport-segments", out)
        self.assertIn(":root{--pane:675px;--hinge:60px}", out)


class LocalState(unittest.TestCase):
    def test_a_measured_health_is_passed_on(self):
        for hs in sorted(monitor_web.MEASURED_HEALTH):
            self.assertEqual(monitor_web.local_state(live_state(63, "sysfs", hs), 0.873), {"percent": 63, "healthPercent": 87.3, "source": "sysfs"})
        self.assertEqual(monitor_web.MEASURED_HEALTH, {"sysfs"}, "only the gauge's own reading; the charge-counter figure is an estimate")

    def test_an_estimated_assumed_or_typed_in_health_is_left_out(self):
        # live_battery.counter_health is counter / level / design capacity (a model-table constant or OPOS_BATTERY_DESIGN_MAH),
        # clamped to 0.1..1.0: an estimate, and local_state has no way to label one, so the page would print it as a reading.
        for hs in ("charge-counter", "assumed", "OPOS_BATTERY_HEALTH", "", None, "Sysfs", 7, ["sysfs"], {"sysfs": 1}):
            self.assertEqual(monitor_web.local_state(live_state(63, "termux-api", hs), 0.9), {"percent": 63, "source": "termux-api"}, hs)

    def test_the_estimate_the_live_battery_makes_is_not_a_reading(self):
        # The real producer, not a hand-written state: a Termux:API reading with a charge counter and no sysfs health.
        from opos.hw import live_battery
        with mock.patch.object(live_battery, "read_battery", return_value={"health": None}):
            health, where = live_battery._health({}, "/", {"level": 80.0, "charge_counter_uah": 2_000_000}, 4000)
        self.assertEqual(where, "charge-counter", "this is the label the live battery gives an estimate")
        self.assertAlmostEqual(health, 0.625)
        self.assertEqual(monitor_web.local_state(live_state(80, "termux-api", where), health), {"percent": 80, "source": "termux-api"})

    def test_a_simulated_battery_reports_nothing(self):
        self.assertEqual(monitor_web.local_state({"battery": {"percent": 60}}, 0.6), {})
        self.assertEqual(monitor_web.local_state(live_state(60, None, "sysfs"), 0.6), {}, "the live battery cannot be read just now")
        self.assertEqual(monitor_web.local_state(live_state(60, "", "sysfs"), 0.6), {})

    def test_nothing_that_is_not_a_plain_number_in_range_gets_through(self):
        bad = (True, False, None, "63", float("nan"), float("inf"), -1, 101, [63], {"a": 1})
        for v in bad:
            self.assertNotIn("percent", monitor_web.local_state(live_state(v), 0.5), v)
        for v in (True, None, "0.5", float("nan"), float("inf"), -0.1, 1.01, 0, 0.0, [0.5]):
            self.assertNotIn("healthPercent", monitor_web.local_state(live_state(50), v), v)
        for snap in (None, [], "x", 3, {}, {"battery": 1, "battery_source": {}}, {"battery": {}, "battery_source": 1}):
            self.assertEqual(monitor_web.local_state(snap, 0.5), {}, snap)

    def test_the_edges_of_the_ranges(self):
        self.assertEqual(monitor_web.local_state(live_state(0), 1.0), {"percent": 0, "healthPercent": 100.0, "source": "sysfs"})
        self.assertEqual(monitor_web.local_state(live_state(100), 0.1), {"percent": 100, "healthPercent": 10.0, "source": "sysfs"})


class Shipping(unittest.TestCase):
    def test_the_inventory_lists_the_files_and_stays_sorted(self):
        lines = [x for x in (ROOT / "packaging/pmos/runtime-files.txt").read_text(encoding="utf-8").splitlines() if x and not x.startswith("#")]
        self.assertEqual(lines, sorted(set(lines)))
        for name in MONITOR_FILES + ("src/opos/hw/live_battery.py",):
            self.assertIn(name, lines)
            self.assertTrue((ROOT / name).is_file(), name)

    def test_every_placeholder_in_the_document_is_a_file(self):
        template = (ROOT / "src/opos/web_static/monitor.html").read_text(encoding="utf-8")
        names = re.findall(r"@@OPOS:([A-Za-z0-9_.-]+)@@", template)
        self.assertEqual(sorted(names), ["monitor-theme.js", "monitor.css", "monitor.js"])
        for name in names:
            self.assertTrue((ROOT / "src/opos/web_static" / name).is_file(), name)

    def test_the_privacy_notice_names_the_inference_page(self):
        text = " ".join(notices.PRIVACY)
        self.assertIn("pointing the Helper or the Inference page at a computer you own on your private network", text)
        self.assertIsNone(re.search(r"\b(AI|model|models|governor|assistant)\b", text, re.I))
        doc = (ROOT / "docs/notices-verification.md").read_text(encoding="utf-8")
        self.assertIn("src/opos/monitor.py", doc)


def build_example():
    """The text build-image.sh writes to /etc/opos/monitor.env.example."""
    script = (ROOT / "packaging/duo-os/build-image.sh").read_text(encoding="utf-8")
    m = re.search(r"cat > \"\$r/etc/opos/monitor\.env\.example\" <<'EOF'\n(.*?)\nEOF\n", script, re.S)
    assert m, "build-image.sh no longer writes monitor.env.example from a quoted here-document"
    return m.group(1)


def table_rows(path):
    """(first cell, whole row) for each row of each markdown table in the file; backticks and surrounding space removed from the cell."""
    rows = []
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and not line.startswith("|--") and not line.startswith("|---"):
            rows.append((line.strip().strip("|").split("|")[0].strip().replace("`", ""), line))
    return rows


class LocalUrlBelongsToTheHelper(unittest.TestCase):
    """OPOS_LOCAL_URL is the Helper's variable (assist.local_route). The Inference page's probe reads it as well, but setting it,
    even to the default, turns off the Helper's /models check and drops its alternates, so nothing may offer it as a page setting."""
    DOCS = ("packaging/duo-os/README.md", "docs/duo-os-final.md")

    def test_the_helper_changes_when_it_is_set_even_to_the_default(self):
        from opos import assist
        plain = assist.local_route({}, 8192)
        same = assist.local_route({"OPOS_LOCAL_URL": monitor.LOCAL_DEFAULT_URL}, 8192)
        self.assertEqual(plain.base_url, same.base_url)
        self.assertTrue(plain.verify_model)
        self.assertTrue(plain.alternates)
        self.assertFalse(same.verify_model, "the /models check is off")
        self.assertEqual(same.alternates, (), "and the alternates are gone")

    def test_the_page_probe_follows_it_when_it_is_set_and_is_8080_when_not(self):
        self.assertEqual(monitor.local_models_url({}), ("127.0.0.1", 8080, "/v1/models"))
        self.assertEqual(monitor.local_models_url({"OPOS_LOCAL_URL": "http://127.0.0.1:9000/v1"}), ("127.0.0.1", 9000, "/v1/models"))

    def test_the_example_does_not_offer_it(self):
        example = build_example()
        self.assertNotIn("OPOS_LOCAL_URL", example)
        self.assertEqual(re.findall(r"(?m)^#(OPOS_[A-Z_]+)=", example), ["OPOS_AGIW_URL", "OPOS_AGIW_HOST"], "the only settings it lists")
        self.assertEqual([x for x in example.splitlines() if x.strip() and not x.lstrip().startswith("#")], [], "and every line is a comment")

    def test_the_server_unit_does_not_name_it(self):
        unit = (ROOT / "packaging/duo-os/opos-server.service").read_text(encoding="utf-8")
        self.assertNotIn("OPOS_LOCAL_URL", unit)
        self.assertIn("OPOS_AGIW_URL and OPOS_AGIW_HOST", unit, "the comment names what the optional file sets")

    def test_the_docs_say_it_is_the_helpers_and_that_setting_it_changes_the_helper(self):
        for path in self.DOCS:
            rows = table_rows(path)
            for first, row in rows:
                if "OPOS_AGIW_URL" in first:
                    self.assertNotIn("OPOS_LOCAL_URL", first, path + ": listed with the Inference page's settings")
            own = [row for first, row in rows if first == "OPOS_LOCAL_URL"]
            self.assertEqual(len(own), 1, path)
            flat = " ".join(own[0].lower().replace("`", "").split())
            self.assertIn("only if you already set it for the helper", flat, path)
            self.assertIn("changes the helper too", flat, path)
            self.assertIn("not an inference page setting", flat, path)

    def test_the_changelog_does_not_list_it_as_an_inference_setting(self):
        entry = next(x for x in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines() if x.startswith("- Inference page ("))
        self.assertIn("(`OPOS_AGIW_URL`, `OPOS_AGIW_HOST`;", entry)
        self.assertNotIn("`OPOS_AGIW_HOST`, `OPOS_LOCAL_URL`", entry)
        self.assertIn("only if you already set it for the Helper, and setting it changes the Helper too", entry)


class HealthIsOnlyTheGauges(unittest.TestCase):
    def test_the_readme_says_an_estimated_health_is_not_shown(self):
        text = (ROOT / "packaging/duo-os/README.md").read_text(encoding="utf-8")
        section = text.split("\n## Inference page\n", 1)[1].split("\n## ", 1)[0]
        flat = " ".join(section.split())
        self.assertIn('a health that is estimated from the charge counter, assumed, or typed in (`OPOS_BATTERY_HEALTH`) reads "Not reported"', flat)

    def test_the_changelog_says_so_too(self):
        entry = next(x for x in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines() if x.startswith("- Inference page ("))
        self.assertIn("only when the battery's own gauge reports it, never an estimated, assumed or typed-in value", entry)


class NoticesEvidence(unittest.TestCase):
    CLAIM = next(c for c in notices.PRIVACY if "Inference page" in c)

    def cells(self):
        doc = (ROOT / "docs/notices-verification.md").read_text(encoding="utf-8")
        row = next(x for x in doc.splitlines() if x.startswith("| " + self.CLAIM + " |"))
        return [c.strip() for c in row[len("| " + self.CLAIM + " |"):].strip().strip("|").split(" | ")]

    def test_the_evidence_says_the_page_always_asks_the_phones_own_loopback_port(self):
        evidence, scope = self.cells()
        flat = evidence.replace("`", "")
        self.assertIn("src/opos/monitor.py sends nothing off the phone unless OPOS_AGIW_URL is set; "
                      "it always asks the phone's own loopback model port", flat)
        self.assertNotIn("fetches nothing", flat)

    def test_it_does_ask_without_an_agiw_url(self):
        asked = []
        page = monitor.Monitor(env={}, probe=lambda env: asked.append(env) or ("none", []), fetch=lambda r: self.fail("fetched"))
        page.snapshot()
        self.assertEqual(len(asked), 1, "one uncached poll, one ask of the loopback port, with nothing configured")


if __name__ == "__main__":
    unittest.main()
