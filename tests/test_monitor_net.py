# SPDX-License-Identifier: GPL-3.0-or-later
"""Address rules and the fetcher. The fetcher tests talk only to a throwaway server on 127.0.0.1."""
import ipaddress
import json
import socket
import ssl
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from opos import monitor


def ip4(*octets):
    return ".".join(str(o) for o in octets)


def _loopback_ok():
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


LOOPBACK = _loopback_ok()
needs_loopback = unittest.skipUnless(LOOPBACK, "cannot bind 127.0.0.1 here")


class ParseRemoteTests(unittest.TestCase):
    def test_accepts_host_and_port_only(self):
        for url, label in (("http://" + ip4(192, 168, 1, 5) + ":8765", ip4(192, 168, 1, 5) + ":8765"), ("http://" + ip4(10, 0, 0, 2), ip4(10, 0, 0, 2) + ":80"),
                           ("https://monitor.local:9443/", "monitor.local:9443"), ("http://[fd00::1]:8765", "[fd00::1]:8765"),
                           ("  http://127.0.0.1:18766  ", "127.0.0.1:18766")):
            self.assertEqual(monitor.parse_remote(url).label, label, url)

    def test_rejects_everything_else(self):
        for url in (None, "", "   ", 7, "ftp://" + ip4(10, 0, 0, 1), ip4(10, 0, 0, 1) + ":8765", "//" + ip4(10, 0, 0, 1), "http://", "http://user@" + ip4(10, 0, 0, 1),
                    "http://user:pw@" + ip4(10, 0, 0, 1), "http://" + ip4(10, 0, 0, 1) + "/api/snapshot", "http://" + ip4(10, 0, 0, 1) + "/x/", "http://" + ip4(10, 0, 0, 1) + "?a=1",
                    "http://" + ip4(10, 0, 0, 1) + "/?", "http://" + ip4(10, 0, 0, 1) + "#frag", "http://" + ip4(10, 0, 0, 1) + ":99999", "http://" + ip4(10, 0, 0, 1) + ":abc",
                    "http://" + ip4(10, 0, 0, 1) + "\\evil", "http://" + ip4(10, 0, 0, 1) + ":80@evil.example", "http://café.example", "http://a" * 60,
                    "http://" + ip4(10, 0, 0, 1) + "\n", "javascript:alert(1)", "file:///etc/passwd"):
            with self.assertRaises(monitor.RemoteConfigError, msg=repr(url)):
                monitor.parse_remote(url)

    def test_host_header_override_is_strict(self):
        self.assertEqual(monitor.parse_remote("http://127.0.0.1:18766", "127.0.0.1:8765").host_header, "127.0.0.1:8765")
        self.assertEqual(monitor.parse_remote("http://127.0.0.1:18766", "localhost:8765").host_header, "localhost:8765")
        self.assertIsNone(monitor.parse_remote("http://127.0.0.1:18766").host_header)
        for bad in ("a b", "evil.example/x", "x\r\nInjected: 1", "127.0.0.1:8765, other", "h:1:2", "@x", 5):
            with self.assertRaises(monitor.RemoteConfigError, msg=repr(bad)):
                monitor.parse_remote("http://127.0.0.1:18766", bad)


class AddressTests(unittest.TestCase):
    def allowed(self, text):
        return monitor.address_allowed(ipaddress.ip_address(text))

    def test_private_loopback_link_local_and_ula_are_allowed(self):
        for ip in ("127.0.0.1", "127.9.9.9", "::1", ip4(10, 15, 19, 82), ip4(172, 16, 0, 1), ip4(172, 31, 255, 255), ip4(192, 168, 0, 7),
                   "169.254.1.1", "fe80::1", "fd12:3456::1", "fc00::1", "::ffff:" + ip4(192, 168, 1, 1), "::ffff:127.0.0.1"):
            self.assertTrue(self.allowed(ip), ip)

    def test_public_and_odd_addresses_are_refused(self):
        for ip in ("8.8.8.8", "1.1.1.1", "172.32.0.1", "172.15.255.255", "192.169.0.1", "11.0.0.1", "100.64.0.1",
                   "0.0.0.0", "::", "224.0.0.1", "ff02::1", "2001:4860:4860::8888", "::ffff:8.8.8.8", "198.18.0.1"):
            self.assertFalse(self.allowed(ip), ip)

    def test_every_resolved_address_must_pass(self):
        def resolver(address_list):
            return lambda host, port, type=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in address_list]
        self.assertEqual(len(monitor.resolve_allowed("h", 80, resolver([ip4(10, 0, 0, 1), ip4(192, 168, 0, 2)]))), 2)
        with self.assertRaises(monitor.FetchError) as raised:
            monitor.resolve_allowed("h", 80, resolver([ip4(10, 0, 0, 1), "8.8.8.8"]))
        self.assertEqual(raised.exception.code, "refused-address")
        with self.assertRaises(monitor.FetchError) as raised:
            monitor.resolve_allowed("h", 80, resolver([]))
        self.assertEqual(raised.exception.code, "unreachable")

        def failing(host, port, type=0):
            raise socket.gaierror("nope")
        with self.assertRaises(monitor.FetchError) as raised:
            monitor.resolve_allowed("h", 80, failing)
        self.assertEqual(raised.exception.code, "unreachable")

    def test_a_scoped_link_local_address_is_read_without_its_scope(self):
        def resolver(host, port, type=0):
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%usb0", port, 0, 3))]
        self.assertEqual(monitor.resolve_allowed("h", 80, resolver)[0][1][0], "fe80::1%usb0")


class Recorder:
    """Starts a loopback server whose one behaviour the test chooses; remembers what it was asked."""

    def __init__(self, behave):
        outer = self
        self.requests = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
                behave(self)

            def do_POST(self):
                outer.requests.append({"method": "POST", "path": self.path, "headers": dict(self.headers)})
                self.send_response(405)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def remote(self, host_header=None):
        return monitor.parse_remote(f"http://127.0.0.1:{self.port}", host_header)


def json_reply(payload, status=200, kind="application/json"):
    def behave(handler):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", kind)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    return behave


class FetchTests(unittest.TestCase):
    def run_case(self, behave, host_header=None):
        try:
            server = Recorder(behave)
        except OSError as error:
            self.skipTest(f"cannot bind loopback: {error}")
        self.addCleanup(server.close)
        return server, server.remote(host_header)

    def code_of(self, remote):
        with self.assertRaises(monitor.FetchError) as raised:
            monitor.fetch_snapshot(remote)
        return raised.exception.code

    def test_reads_the_snapshot_with_a_fixed_get(self):
        server, remote = self.run_case(json_reply({"host": "mac", "machines": []}))
        self.assertEqual(monitor.fetch_snapshot(remote), {"host": "mac", "machines": []})
        request = server.requests[0]
        self.assertEqual((request["method"], request["path"]), ("GET", "/api/snapshot"))
        self.assertEqual(request["headers"]["Host"], f"127.0.0.1:{server.port}")
        self.assertEqual(request["headers"]["User-Agent"], "opos-monitor/1")
        self.assertNotIn("Cookie", request["headers"])
        self.assertNotIn("Authorization", request["headers"])
        self.assertEqual(len(server.requests), 1)

    def test_host_header_override_is_what_the_monitor_sees(self):
        server, remote = self.run_case(json_reply({}), host_header="127.0.0.1:8765")
        monitor.fetch_snapshot(remote)
        self.assertEqual(server.requests[0]["headers"]["Host"], "127.0.0.1:8765")

    def test_redirects_are_never_followed(self):
        def behave(handler):
            handler.send_response(302)
            handler.send_header("Location", "http://127.0.0.1:1/elsewhere")
            handler.end_headers()
        server, remote = self.run_case(behave)
        self.assertEqual(self.code_of(remote), "redirect")
        self.assertEqual(len(server.requests), 1)

    def test_errors_wrong_types_and_bad_json_are_named(self):
        cases = ((json_reply({}, status=404), "status"), (json_reply({}, status=500), "status"),
                 (json_reply(b"<html>", kind="text/html"), "type"), (json_reply(b"{}", kind="text/plain"), "type"),
                 (json_reply(b"not json"), "not-json"), (json_reply(b'{"a": NaN}'), "not-json"),
                 (json_reply(b'{"a": Infinity}'), "not-json"), (json_reply(b"[1, 2]"), "invalid"),
                 (json_reply(b'"text"'), "invalid"), (json_reply(b"[" * 100000), "not-json"), (json_reply(b"\xff\xfe"), "not-json"))
        for behave, code in cases:
            server, remote = self.run_case(behave)
            self.assertEqual(self.code_of(remote), code)

    def test_content_type_may_carry_a_charset(self):
        _server, remote = self.run_case(json_reply({"ok": 1}, kind="application/json; charset=utf-8"))
        self.assertEqual(monitor.fetch_snapshot(remote), {"ok": 1})

    def test_an_oversize_answer_is_refused_with_or_without_a_length(self):
        big = b'{"a": "' + b"x" * (monitor.MAX_BYTES + 10) + b'"}'
        _server, remote = self.run_case(json_reply(big))
        self.assertEqual(self.code_of(remote), "too-large")

        def no_length(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            handler.wfile.write(big)
        _server, remote = self.run_case(no_length)
        self.assertEqual(self.code_of(remote), "too-large")

    def test_exactly_the_cap_is_accepted(self):
        pad = monitor.MAX_BYTES - len(b'{"a": ""}')
        _server, remote = self.run_case(json_reply(b'{"a": "' + b"x" * pad + b'"}'))
        self.assertEqual(len(monitor.fetch_snapshot(remote)["a"]), pad)

    def test_a_silent_server_times_out(self):
        def silent(handler):
            time.sleep(1.5)
        _server, remote = self.run_case(silent)
        with mock.patch.object(monitor, "READ_TIMEOUT", 0.3):
            self.assertEqual(self.code_of(remote), "timeout")

    def test_a_non_ascii_digit_in_content_length_is_not_a_crash(self):
        def odd(handler):
            handler.wfile.write(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: \xb2\r\n\r\n{\"ok\": 1}")
            handler.close_connection = True
        _server, remote = self.run_case(odd)
        self.assertEqual(monitor.fetch_snapshot(remote), {"ok": 1})

    def test_a_content_length_of_thousands_of_digits_is_too_large_not_a_crash(self):
        for digits in ("0" * 4400 + "2", "9" * 5000, "9" * 8, "1" + "0" * 6):
            def huge(handler, digits=digits):
                handler.wfile.write(("HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + digits + "\r\n\r\n{}").encode())
                handler.close_connection = True
            _server, remote = self.run_case(huge)
            if len(digits.lstrip("0")) <= 4:
                self.assertEqual(monitor.fetch_snapshot(remote), {})
            else:
                self.assertEqual(self.code_of(remote), "too-large", digits[:12])
        for digits in ("0000000000002", "2"):
            def small(handler, digits=digits):
                handler.wfile.write(("HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + digits + "\r\n\r\n{}").encode())
                handler.close_connection = True
            _server, remote = self.run_case(small)
            self.assertEqual(monitor.fetch_snapshot(remote), {}, digits)

    def test_a_reply_that_is_not_http_is_invalid_not_unreachable(self):
        for junk in (b"garbage\r\n\r\n", b"HTTP/9.9 200\r\n\r\n", b"\x00\x01\x02\r\n"):
            def say(handler, junk=junk):
                handler.wfile.write(junk)
                handler.close_connection = True
            _server, remote = self.run_case(say)
            self.assertIn(self.code_of(remote), ("invalid", "unreachable"), junk)
        def garbled(handler):
            handler.wfile.write(b"garbage\r\n\r\n")
            handler.close_connection = True
        _server, remote = self.run_case(garbled)
        self.assertEqual(self.code_of(remote), "invalid")

    def test_an_untrusted_certificate_is_not_hidden_by_another_address_that_did_not_answer(self):
        calls = []

        def connect(self_, *args):
            calls.append(self_._pin[1])
            if len(calls) == 1:
                raise ssl.SSLCertVerificationError(1, "certificate verify failed")
            raise ConnectionRefusedError()

        def resolver(host, port, type=0):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 2))]
        with mock.patch.object(monitor._Pinned, "connect", connect):
            with self.assertRaises(monitor.FetchError) as raised:
                monitor.fetch_snapshot(monitor.parse_remote("https://h.invalid:8443"), resolver=resolver)
        self.assertEqual(raised.exception.code, "certificate")
        self.assertEqual(len(calls), 2, "the second address was still tried")

    def test_a_certificate_the_phone_does_not_trust_is_named_not_called_unreachable(self):
        def refuse(self_, *args):
            raise ssl.SSLCertVerificationError(1, "certificate verify failed")
        with mock.patch.object(monitor._Pinned, "connect", refuse):
            self.assertEqual(self.code_of(monitor.parse_remote("https://" + ip4(127, 0, 0, 1) + ":9")), "certificate")
        word, text = monitor.FETCH_WORDS["certificate"]
        self.assertEqual(word, "REFUSED")
        self.assertIn("certificate", text)

    @needs_loopback
    def test_nothing_listening_is_unreachable(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertEqual(self.code_of(monitor.parse_remote(f"http://127.0.0.1:{port}")), "unreachable")

    def test_a_public_address_is_refused_before_any_connection(self):
        def resolver(host, port, type=0):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        with mock.patch.object(monitor, "_Pinned") as pinned:
            with self.assertRaises(monitor.FetchError) as raised:
                monitor.fetch_snapshot(monitor.parse_remote("http://example.invalid:8765"), resolver=resolver)
        self.assertEqual(raised.exception.code, "refused-address")
        pinned.assert_not_called()

    def test_the_checked_address_is_the_one_connected_to(self):
        seen = []

        class Boom(Exception):
            pass

        def spy(family, sockaddr, host, port, tls, **kwargs):
            seen.append(sockaddr)
            raise Boom

        def resolver(host, port, type=0):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip4(10, 1, 2, 3), port))]
        with mock.patch.object(monitor, "_Pinned", spy):
            with self.assertRaises(Boom):
                monitor.fetch_snapshot(monitor.parse_remote("http://rebind.example:8765"), resolver=resolver)
        self.assertEqual(seen, [(ip4(10, 1, 2, 3), 8765)])


class Hold:
    """A TCP server that accepts connections and never says anything."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.conns, self.stop = [], False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        self.sock.settimeout(0.1)
        while not self.stop:
            try:
                self.conns.append(self.sock.accept()[0])
            except OSError:
                continue

    def close(self):
        self.stop = True
        for conn in self.conns:
            conn.close()
        self.sock.close()


def keep_writing(handler, first, repeat, pause=0.05, limit=20.0):
    """Sends `first`, then `repeat` again and again until the other side goes away. Never finishes the reply."""
    end = time.monotonic() + limit
    try:
        handler.wfile.write(first)
        while time.monotonic() < end:
            handler.wfile.write(repeat)
            handler.wfile.flush()
            time.sleep(pause)
    except OSError:
        pass
    handler.close_connection = True


JSON_HEAD = b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
DRIPS = {
    "body drip": lambda h: keep_writing(h, JSON_HEAD + b'{"a": "', b"x"),
    "a complete document that never ends": lambda h: keep_writing(h, JSON_HEAD + b'{"a": 1}', b" "),
    "header drip": lambda h: keep_writing(h, b"HTTP/1.0 200 OK\r\nX-A: ", b"y"),
    "1xx flood": lambda h: keep_writing(h, b"", b"HTTP/1.1 100 Continue\r\n\r\n", pause=0.001),
    "trailer flood": lambda h: keep_writing(h, b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n", b"X-T: 1\r\n", pause=0.002),
}


class DeadlineTests(unittest.TestCase):
    """The 6 s budget is real: whatever the other side does, fetch_snapshot returns near TOTAL_DEADLINE."""

    TOTAL = 0.8

    def timed(self, remote, **kwargs):
        started = time.monotonic()
        with mock.patch.object(monitor, "TOTAL_DEADLINE", self.TOTAL), mock.patch.object(monitor, "READ_TIMEOUT", 3.0):
            with self.assertRaises(monitor.FetchError) as raised:
                monitor.fetch_snapshot(remote, **kwargs)
        return raised.exception.code, time.monotonic() - started

    def test_a_host_that_never_finishes_cannot_hold_the_fetch(self):
        for name, behave in DRIPS.items():
            try:
                server = Recorder(behave)
            except OSError as error:
                self.skipTest(f"cannot bind loopback: {error}")
            self.addCleanup(server.close)
            code, seconds = self.timed(server.remote())
            # Newer Pythons end an endless run of 1xx lines or trailers themselves (invalid); older ones need the watchdog.
            self.assertIn(code, ("timeout", "invalid") if name in ("1xx flood", "trailer flood") else ("timeout",), name)
            self.assertLess(seconds, self.TOTAL + 1.0, f"{name} took {seconds:.2f} s")
            if code == "timeout":
                self.assertGreater(seconds, self.TOTAL - 0.2, f"{name} ended early: {seconds:.2f} s")

    def test_the_watchdog_alone_ends_a_cut_body_even_if_the_clock_never_advances(self):
        # With a frozen clock the loop's own deadline check can never fire, so only the watchdog and the
        # "a cut body is a fragment" rule stand between a half-read document and a parsed answer.
        for name in ("body drip", "a complete document that never ends"):
            try:
                server = Recorder(DRIPS[name])
            except OSError as error:
                self.skipTest(f"cannot bind loopback: {error}")
            self.addCleanup(server.close)
            started = time.monotonic()
            with mock.patch.object(monitor, "TOTAL_DEADLINE", self.TOTAL), mock.patch.object(monitor, "READ_TIMEOUT", 3.0):
                with self.assertRaises(monitor.FetchError) as raised:
                    monitor.fetch_snapshot(server.remote(), clock=lambda: 100.0)
            self.assertEqual(raised.exception.code, "timeout", name)
            self.assertLess(time.monotonic() - started, self.TOTAL + 1.0, name)

    def test_a_tls_handshake_that_never_completes_is_cut_too(self):
        try:
            hold = Hold()
        except OSError as error:
            self.skipTest(f"cannot bind loopback: {error}")
        self.addCleanup(hold.close)
        code, seconds = self.timed(monitor.parse_remote(f"https://127.0.0.1:{hold.port}"))
        self.assertEqual(code, "timeout")
        self.assertLess(seconds, self.TOTAL + 1.0)

    def test_several_silent_addresses_share_one_budget(self):
        try:
            hold = Hold()
        except OSError as error:
            self.skipTest(f"cannot bind loopback: {error}")
        self.addCleanup(hold.close)

        def resolver(host, port, type=0):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", hold.port))] * 4
        started = time.monotonic()
        with mock.patch.object(monitor, "TOTAL_DEADLINE", self.TOTAL), mock.patch.object(monitor, "READ_TIMEOUT", 0.5):
            with self.assertRaises(monitor.FetchError) as raised:
                monitor.fetch_snapshot(monitor.parse_remote(f"http://h.invalid:{hold.port}"), resolver=resolver)
        self.assertEqual(raised.exception.code, "timeout")
        self.assertLess(time.monotonic() - started, self.TOTAL + 0.7, "four silent addresses must not take four read timeouts")

    def test_a_name_lookup_that_hangs_is_cut(self):
        def resolver(host, port, type=0):
            time.sleep(3)
            return []
        started = time.monotonic()
        with mock.patch.object(monitor, "DNS_DEADLINE", 0.3):
            with self.assertRaises(monitor.FetchError) as raised:
                monitor.fetch_snapshot(monitor.parse_remote("http://slow.invalid:80"), resolver=resolver)
        self.assertEqual(raised.exception.code, "timeout")
        self.assertLess(time.monotonic() - started, 1.5)

    @needs_loopback
    def test_the_watchdog_is_cancelled_and_nothing_is_left_running(self):
        server_ok = Recorder(json_reply({"ok": 1}))
        self.addCleanup(server_ok.close)
        before = threading.active_count()
        for _ in range(5):
            monitor.fetch_snapshot(server_ok.remote())
        time.sleep(0.2)
        self.assertLessEqual(threading.active_count(), before + 1, "watchdog timers must not pile up")

    def test_connect_is_bounded_by_what_is_left_of_the_budget(self):
        seen = []

        class Spy(monitor._Pinned):
            def __init__(self, *args, **kwargs):
                seen.append(kwargs.get("connect_timeout"))
                super().__init__(*args, **kwargs)
        with mock.patch.object(monitor, "_Pinned", Spy), mock.patch.object(monitor, "TOTAL_DEADLINE", 0.5):
            with self.assertRaises(monitor.FetchError):
                monitor.fetch_snapshot(monitor.parse_remote("http://127.0.0.1:9"))
        self.assertTrue(seen and all(0 < value <= 0.5 for value in seen), seen)


class PinnedConnectTests(unittest.TestCase):
    def test_the_connection_goes_to_the_checked_address_not_to_the_name(self):
        """A name that does not resolve is still reached through the address the resolver returned."""
        try:
            server = Recorder(json_reply({"ok": 1}))
        except OSError as error:
            self.skipTest(f"cannot bind loopback: {error}")
        self.addCleanup(server.close)

        def resolver(host, port, type=0):
            self.assertEqual(host, "pinned.invalid")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        remote = monitor.parse_remote(f"http://pinned.invalid:{server.port}")
        self.assertEqual(monitor.fetch_snapshot(remote, resolver=resolver), {"ok": 1})
        self.assertEqual(server.requests[0]["headers"]["Host"], f"pinned.invalid:{server.port}")


class ProbeLocalTests(unittest.TestCase):
    def probe(self, behave):
        try:
            server = Recorder(behave)
        except OSError as error:
            self.skipTest(f"cannot bind loopback: {error}")
        self.addCleanup(server.close)
        result = monitor.probe_local({"OPOS_LOCAL_URL": f"http://127.0.0.1:{server.port}/v1"})
        self.assertEqual(server.requests[0]["path"], "/v1/models")
        self.assertEqual(server.requests[0]["method"], "GET")
        return result

    def test_listed_models_empty_lists_and_bad_ids(self):
        self.assertEqual(self.probe(json_reply({"data": [{"id": "llama-3.2-1b-instruct"}, {"id": "b"}]})), ("model", ["llama-3.2-1b-instruct", "b"]))
        self.assertEqual(self.probe(json_reply({"data": []})), ("empty", []))
        self.assertEqual(self.probe(json_reply({"data": [{"id": "<img src=x>"}, {"id": 5}, "x", None]})), ("unknown", []))
        self.assertEqual(self.probe(json_reply({"data": [{"id": "<x>"}, {"id": "good"}]})), ("model", ["good"]))
        ids = self.probe(json_reply({"data": [{"id": f"m{i}"} for i in range(40)]}))[1]
        self.assertEqual(len(ids), monitor.MAX_MODELS + 1, "eight names and the marker")

    def test_anything_unreadable_is_unknown_never_no_model(self):
        for behave in (json_reply({}, status=404), json_reply({}, status=500), json_reply(b"nope"), json_reply(b'{"data": NaN}'),
                       json_reply(b"[]"), json_reply({"data": "x"}), json_reply({"models": []}), json_reply(b"[" * 100000),
                       json_reply(b'{"data": [], "pad": "' + b"x" * (monitor.MAX_BYTES + 5) + b'"}')):
            self.assertEqual(self.probe(behave), ("unknown", []))

    def test_a_server_that_does_not_answer_in_time_is_unknown(self):
        def silent(handler):
            time.sleep(1.5)
        with mock.patch.object(monitor, "LOCAL_TIMEOUT", 0.3):
            self.assertEqual(self.probe(silent), ("unknown", []))


    def test_a_probe_that_never_finishes_is_unknown_within_its_own_deadline(self):
        cases = dict(DRIPS)
        cases["a body that is promised and never comes"] = lambda h: keep_writing(h, b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: 300000\r\n\r\n", b"x", pause=0.2)
        # A complete, valid, model-listing body that is never closed must not become "model": the cut ends it.
        cases["a valid list that is never closed"] = lambda h: keep_writing(h, JSON_HEAD + b'{"data": [{"id": "m"}]}', b" ")
        for name, behave in cases.items():
            try:
                server = Recorder(behave)
            except OSError as error:
                self.skipTest(f"cannot bind loopback: {error}")
            self.addCleanup(server.close)
            started = time.monotonic()
            with mock.patch.object(monitor, "LOCAL_TOTAL", 0.6), mock.patch.object(monitor, "LOCAL_TIMEOUT", 3.0):
                result = monitor.probe_local({"OPOS_LOCAL_URL": f"http://127.0.0.1:{server.port}/v1"})
            self.assertEqual(result, ("unknown", []), name)
            self.assertLess(time.monotonic() - started, 0.6 + 1.0, name)

    def test_an_error_status_never_reads_as_a_model_list(self):
        listing = {"data": [{"id": "m"}]}
        for status in (500, 404, 403, 204, 301):
            self.assertEqual(self.probe(json_reply(listing, status=status)), ("unknown", []), status)

    def test_more_models_than_are_shown_say_so(self):
        ids = self.probe(json_reply({"data": [{"id": f"m{i}"} for i in range(40)]}))[1]
        self.assertEqual(ids[:-1], [f"m{i}" for i in range(monitor.MAX_MODELS)])
        self.assertEqual(ids[-1], monitor.MORE)
        self.assertNotIn(monitor.MORE, self.probe(json_reply({"data": [{"id": "a"}, {"id": "b"}]}))[1])


@needs_loopback
class EveryAddressRefusesTests(unittest.TestCase):
    """'No model' needs every address of the name to refuse; one hung listener means we do not know."""

    def setUp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.closed = probe.getsockname()[1]
        try:
            self.hold = Hold()
        except OSError as error:
            self.skipTest(f"cannot bind loopback: {error}")
        self.addCleanup(self.hold.close)

    def resolver(self, *ports):
        return lambda host, port, type=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p)) for p in ports]

    def test_only_unanimous_refusal_is_none(self):
        self.assertTrue(monitor._every_address_refuses("h", 1, self.resolver(self.closed, self.closed)))
        with mock.patch.object(monitor, "LOCAL_TIMEOUT", 0.3):
            self.assertFalse(monitor._every_address_refuses("h", 1, self.resolver(self.hold.port, self.closed)))
            self.assertFalse(monitor._every_address_refuses("h", 1, self.resolver(self.closed, self.hold.port)))

    def test_an_address_that_times_out_or_cannot_be_reached_is_not_a_refusal(self):
        for error in (TimeoutError("timed out"), OSError(65, "No route to host"), OSError(51, "Network is unreachable")):
            class Fake:
                def __init__(self, *args):
                    pass

                def settimeout(self, _seconds):
                    pass

                def connect(self, _address, error=error):
                    raise error

                def close(self):
                    pass
            with mock.patch.object(monitor.socket, "socket", Fake):
                self.assertFalse(monitor._every_address_refuses("h", 1, self.resolver(self.closed, self.closed)), repr(error))
        class Refusing(Fake):
            def connect(self, _address):
                raise ConnectionRefusedError()
        with mock.patch.object(monitor.socket, "socket", Refusing):
            self.assertTrue(monitor._every_address_refuses("h", 1, self.resolver(1, 2)))

    def test_no_addresses_or_a_failed_lookup_is_not_a_refusal(self):
        self.assertFalse(monitor._every_address_refuses("h", 1, lambda *a, **k: []))

        def broken(*args, **kwargs):
            raise socket.gaierror("nope")
        self.assertFalse(monitor._every_address_refuses("h", 1, broken))

    def test_the_probe_says_none_for_localhost_only_when_every_address_refuses(self):
        # The first stage (http.client's own connect) is made hermetic: whatever localhost lists on this host, it refuses.
        env = {"OPOS_LOCAL_URL": f"http://localhost:{self.closed}/v1"}

        def refuse(self_):
            raise ConnectionRefusedError()
        with mock.patch.object(monitor._Live, "connect", refuse):
            self.assertEqual(monitor.probe_local(env, resolver=self.resolver(self.closed)), ("none", []))
            with mock.patch.object(monitor, "LOCAL_TIMEOUT", 0.3):
                self.assertEqual(monitor.probe_local(env, resolver=self.resolver(self.hold.port, self.closed)), ("unknown", []))


if __name__ == "__main__":
    unittest.main()
