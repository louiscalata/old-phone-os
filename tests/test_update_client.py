# SPDX-License-Identifier: GPL-3.0-or-later
"""OTA update client (docs/ota-update.md, Client): config, check, download, rollback request, status, the /update page and its routes.

The update source is a real http.server on loopback; folders are temporary. Nothing here needs root, a phone or the internet.
"""
import collections
import hashlib
import html.parser
import http.client
import http.server
import io
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

from opos import notices, update_client as uc, update_page
from tests import webclient


_saved = []


def setUpModule():
    """tests/webclient.py passes putrequest-only flags to HTTPConnection.request; drop them here (tests/test_web_security.py does the same globally)."""
    orig = http.client.HTTPConnection.request
    _saved.append(orig)

    def request(self, method, url, body=None, headers=None, **kw):
        kw.pop("skip_host", None)
        kw.pop("skip_accept_encoding", None)
        return orig(self, method, url, body=body, headers=headers or {}, **kw)
    http.client.HTTPConnection.request = request


def tearDownModule():
    http.client.HTTPConnection.request = _saved.pop()


def ip(*parts):
    """An address literal built from numbers, so no private address sits in the source."""
    return ".".join(str(p) for p in parts)


def tar_bundle(serial=7, fmt=tarfile.USTAR_FORMAT, pad=2048):
    """Three members named like a real bundle. The content is not meaningful: root verifies, the client does not."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as t:
        for name, data in (("manifest.json", json.dumps({"format": 1, "serial": serial}).encode()),
                           ("manifest.json.sig", b"sig"), ("payload.tar.gz", b"\0" * pad)):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = 1700000000.5 if fmt == tarfile.PAX_FORMAT else 1700000000
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class Source:
    """A loopback update source: routes map a path to (status, headers, body) or a callable taking the handler."""

    def __init__(self):
        self.routes, self.hits, self.release = {}, [], threading.Event()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                r = outer.routes.get(self.path.split("?")[0])
                if r is None:
                    r = (404, {}, b"")
                if callable(r):
                    r(self)
                    return
                status, headers, body = r
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class S(http.server.ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

            def handle_error(self, *a):
                pass

        self.httpd = S(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.01,), daemon=True).start()  # a short poll keeps shutdown quick

    def url(self, path="/latest.json"):
        return f"http://127.0.0.1:{self.port}{path}"

    def add(self, path, body=b"", status=200, **headers):
        self.routes[path] = (status, headers, body)

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


class Env(unittest.TestCase):
    """A temporary install: config file, update folder with inbox, REVISION, and a loopback source."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.paths = uc.Paths(config=os.path.join(t, "update.json"), root=os.path.join(t, "lib"), revision=os.path.join(t, "REVISION"))
        os.makedirs(self.paths.inbox)
        self.src = Source()
        self.addCleanup(self.src.close)

    def put(self, path, obj):
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))

    def conf(self, **kw):
        self.put(self.paths.config, {"url": self.src.url(), "allow_lan_http": True, **kw})

    def state(self, serial=0, previous=(), failed=(), stepped_back=None, pending_boot=None, floor=0, **cur):
        """floor=None: the state file of an updater from before the floor existed (it does not know the cancel-staged request)."""
        doc = {"base": 1, "current": {"serial": serial, "version": "0.1.0", "revision": "a" * 40, "release": f"{serial}-aaaaaaaaaaaa", **cur},
               "previous": list(previous), "failed": list(failed), "pending_boot": pending_boot, "stepped_back": stepped_back}
        if floor is not None:
            doc["floor"] = floor
        self.put(self.paths.state, doc)

    def status(self, **kw):
        self.put(self.paths.status, {"phase": "idle", "message": "", "serial": None, "time": "2026-11-08T12:00:00Z", **kw})

    def client(self, **limits):
        return uc.UpdateClient(self.paths, uc.Limits(**limits))

    def publish(self, serial=7, bundle=None, *, size=True, sha=True, name=None, **extra):
        bundle = tar_bundle(serial) if bundle is None else bundle
        name = name or f"opos-{serial}.opos-update"
        idx = {"serial": serial, "version": f"0.{serial}.0", "url": name, **extra}
        if size:
            idx["size"] = len(bundle)
        if sha:
            idx["sha256"] = hashlib.sha256(bundle).hexdigest()
        self.src.add("/latest.json", json.dumps(idx).encode())
        self.src.add("/" + name, bundle)
        return bundle

    def inbox(self):
        return sorted(os.listdir(self.paths.inbox))


def no_network():
    """Any outbound connection or name lookup raises. Connections to loopback sockets of the test itself are the exception below."""
    def boom(*a, **k):
        raise AssertionError("network used")
    return [mock.patch.object(socket.socket, "connect", boom), mock.patch.object(socket, "create_connection", boom),
            mock.patch.object(socket, "getaddrinfo", boom), mock.patch.object(urllib.request.OpenerDirector, "open", boom),
            mock.patch.object(urllib.request, "urlopen", boom), mock.patch.object(http.client.HTTPConnection, "connect", boom)]


class AddressPolicy(unittest.TestCase):
    def test_https_anywhere_http_only_on_a_private_network_when_allowed(self):
        self.assertEqual(uc.check_url("https://updates.example.org/latest.json").scheme, "https")
        for host in ("127.0.0.1", "127.1.2.3", "[::1]", "localhost", ip(10, 15, 19, 82), ip(172, 16, 0, 9), ip(172, 31, 255, 1), ip(192, 168, 1, 20), "[fd12:3456::1]"):
            with self.subTest(host=host):
                self.assertEqual(uc.check_url(f"http://{host}:8080/latest.json", True).scheme, "http")
                with self.assertRaises(ValueError):  # the flag is off by default
                    uc.check_url(f"http://{host}:8080/latest.json")

    def test_http_to_anything_else_is_refused_even_when_allowed(self):
        for host in ("updates.example.org", "phone.local", "127.0.0.1.example.org", "localhost.example.org", "0x7f.1", "2130706433",
                     ip(8, 8, 8, 8), ip(172, 32, 0, 1), ip(172, 15, 0, 1), ip(11, 0, 0, 1), ip(192, 169, 0, 1), ip(169, 254, 1, 1),
                     ip(203, 0, 113, 9), "[2001:db8::1]", "[fe80::1]", "[::ffff:" + ip(8, 8, 8, 8) + "]", "localhost."):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    uc.check_url(f"http://{host}/latest.json", True)

    def test_mapped_loopback_is_loopback(self):
        self.assertTrue(uc.lan_host("::ffff:127.0.0.1"))
        self.assertFalse(uc.lan_host("::ffff:" + ip(8, 8, 8, 8)))

    def test_other_schemes_credentials_and_odd_addresses_are_refused(self):
        for url in ("ftp://updates.example.org/x", "file:///etc/passwd", "javascript:alert(1)", "//updates.example.org/x", "updates.example.org/x",
                    "https://", "https:///x", "https://user@updates.example.org/x", "https://user:pw@updates.example.org/x",
                    "https://updates.example.org/x#frag", "https://updates.example.org:0/x", "https://updates.example.org:99999/x",
                    "https://updates.example.org/ x", "https://updates.example.org/\tx", "https://updates.example.org\\@evil.example/x",
                    "https://updates.example.org/é", "", "https://" + "a" * 2100, None, 5, b"https://updates.example.org/"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    uc.check_url(url, True)


class ConfigFile(Env):
    def test_absent_means_not_configured(self):
        self.assertIsNone(uc.load_config(self.paths.config))
        snap = self.client().snapshot()
        self.assertFalse(snap["source"]["configured"])
        self.assertIsNone(snap["source"]["error"])

    def test_valid(self):
        self.put(self.paths.config, {"url": "https://updates.example.org/opos/latest.json"})
        cfg = uc.load_config(self.paths.config)
        self.assertEqual(cfg, uc.Config("https://updates.example.org/opos/latest.json", False, False))
        self.put(self.paths.config, {"url": "http://127.0.0.1:1/latest.json", "auto_check": True, "allow_lan_http": True, "future_key": [1]})
        self.assertEqual(uc.load_config(self.paths.config), uc.Config("http://127.0.0.1:1/latest.json", True, True))

    def test_no_url_is_not_configured(self):
        for body in ({}, {"url": ""}, {"auto_check": True}, {"url": None}):
            with self.subTest(body=body):
                self.put(self.paths.config, body)
                self.assertIsNone(uc.load_config(self.paths.config))

    def test_unusable_files_are_named_not_guessed(self):
        bad = ["not json", "[]", '"x"', "{", '{"url": 5}', '{"url": "http://updates.example.org/latest.json"}',
               '{"url": "http://127.0.0.1/latest.json"}', '{"url": "https://updates.example.org/x", "auto_check": "yes"}',
               '{"url": "https://updates.example.org/x", "allow_lan_http": 1}', '{"url": "https://u.example.org/x", "url": "https://v.example.org/x"}',
               '{"url": "https://u.example.org/x", "auto_check": NaN}', "﻿" + '{"url": "https://u.example.org/x"}']
        for text in bad:
            with self.subTest(text=text):
                self.put(self.paths.config, text)
                with self.assertRaises(uc.UpdateError) as cm:
                    uc.load_config(self.paths.config)
                self.assertEqual(cm.exception.code, "bad_config")
                self.assertNotIn(self.tmp.name, cm.exception.message)

    def test_huge_or_non_file_config_is_refused(self):
        self.put(self.paths.config, " " * (uc.MAX_SMALL + 1))
        with self.assertRaises(uc.UpdateError):
            uc.load_config(self.paths.config)
        os.unlink(self.paths.config)
        os.mkdir(self.paths.config)
        with self.assertRaises(uc.UpdateError):
            uc.load_config(self.paths.config)

    def test_snapshot_shows_the_problem_and_hides_the_query(self):
        self.put(self.paths.config, "oops")
        self.assertIn("not valid JSON", self.client().snapshot()["source"]["error"])
        self.put(self.paths.config, {"url": "https://updates.example.org/latest.json?key=abc123"})
        src = self.client().snapshot()["source"]
        self.assertTrue(src["configured"])
        self.assertNotIn("abc123", json.dumps(src))


class NothingWithoutOptIn(Env):
    """With no source set, no code path opens a socket, resolves a name or builds a request."""

    def run_all(self, client):
        client.snapshot()
        for call in (client.check, client.start_download):
            with self.assertRaises(uc.UpdateError) as cm:
                call()
            self.assertIn(cm.exception.code, ("not_configured", "bad_config", "not_available"))
        self.assertIsNone(client.start_auto_check(first_delay=0, interval=0.01))

    def guarded(self, fn):
        patches = no_network()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        fn()

    def test_no_config_file(self):
        self.state(0)
        self.guarded(lambda: self.run_all(self.client()))

    def test_empty_config(self):
        self.put(self.paths.config, {})
        self.guarded(lambda: self.run_all(self.client()))

    def test_unusable_config(self):
        self.put(self.paths.config, {"url": "http://updates.example.org/latest.json"})
        self.guarded(lambda: self.run_all(self.client()))

    def test_config_but_not_an_update_capable_install(self):
        self.conf(auto_check=True)
        shutil.rmtree(self.paths.root)
        self.guarded(lambda: self.run_all(self.client()))

    def test_auto_check_off_never_polls(self):
        self.conf(auto_check=False)
        self.state(0)
        c = self.client()
        self.assertIsNone(c.start_auto_check(first_delay=0, interval=0.01))
        time.sleep(0.15)
        self.assertEqual(self.src.hits, [])

    def test_rollback_request_is_local_files_only(self):
        self.state(1, previous=["0-aaaaaaaaaaaa"])
        self.guarded(lambda: self.client().request_rollback())
        self.assertEqual(self.inbox(), ["rollback.request"])


class IndexParsing(unittest.TestCase):
    BASE = "http://127.0.0.1:9/dir/latest.json"

    def parse(self, obj, **kw):
        raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        return uc.parse_index(raw, self.BASE, True, **kw)

    def test_minimal_and_full(self):
        i = self.parse({"serial": 7, "version": "0.2.0", "url": "opos-7.opos-update"})
        self.assertEqual((i.serial, i.version, i.url, i.sha256, i.size), (7, "0.2.0", "http://127.0.0.1:9/dir/opos-7.opos-update", None, None))
        i = self.parse({"serial": 0, "version": "1", "url": "/b/x.opos-update", "sha256": "AB" * 32, "size": 4096, "notes": "ignored", "extra": {"a": 1}})
        self.assertEqual((i.serial, i.url, i.sha256, i.size), (0, "http://127.0.0.1:9/b/x.opos-update", "ab" * 32, 4096))

    def test_absolute_url_on_the_same_origin_is_fine(self):
        self.assertEqual(self.parse({"serial": 1, "version": "1", "url": "http://127.0.0.1:9/x"}).url, "http://127.0.0.1:9/x")

    def test_malformed(self):
        ok = {"serial": 7, "version": "0.2.0", "url": "x"}
        bad = [b"", b"nope", b"[]", b"7", b'"s"', b"\xff\xfe", b'{"serial": 7, "serial": 8, "version": "1", "url": "x"}',
               b'{"serial": NaN, "version": "1", "url": "x"}', b'{"serial": Infinity, "version": "1", "url": "x"}']
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.parse(raw)
        mutate = [("serial", None), ("serial", True), ("serial", False), ("serial", 7.0), ("serial", "7"), ("serial", -1), ("serial", 2 ** 31), ("serial", [7]),
                  ("version", None), ("version", 1), ("version", ""), ("version", "-1"), ("version", "a b"), ("version", "a" * 65), ("version", "1<script>"),
                  ("url", None), ("url", 5), ("url", ""), ("url", "a b"), ("url", "x\n"), ("url", "https://updates.example.org/x"),
                  ("url", "file:///etc/passwd"), ("url", "//127.0.0.1:10/x"), ("url", "http://127.0.0.1:10/x"), ("url", "http://localhost:9/x"),
                  ("url", "ftp://127.0.0.1:9/x"), ("url", "x#frag"), ("url", "\\\\evil\\x"),
                  ("sha256", 5), ("sha256", "abc"), ("sha256", "g" * 64), ("sha256", "a" * 65), ("size", 0), ("size", -5), ("size", 1.5), ("size", True), ("size", "9"),
                  ("size", uc.MAX_BUNDLE + 1)]
        for key, value in mutate:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.parse({**ok, key: value})
        for key in ("serial", "version", "url"):
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.parse({k: v for k, v in ok.items() if k != key})

    def test_size_at_the_cap_is_fine_and_the_cap_is_256_mib(self):
        self.assertEqual(uc.MAX_BUNDLE, 256 * 1024 * 1024)
        self.assertEqual(uc.Limits().max_bundle, 256 * 1024 * 1024)
        self.assertEqual(self.parse({"serial": 1, "version": "1", "url": "x", "size": uc.MAX_BUNDLE}).size, uc.MAX_BUNDLE)

    def test_https_index_may_only_point_at_https(self):
        with self.assertRaises(ValueError):
            uc.parse_index(b'{"serial": 1, "version": "1", "url": "http://updates.example.org/x"}', "https://updates.example.org/latest.json", True)
        self.assertEqual(uc.parse_index(b'{"serial": 1, "version": "1", "url": "x"}', "https://updates.example.org/latest.json").url, "https://updates.example.org/x")


class Check(Env):
    def test_newer_equal_older_failed(self):
        self.conf()
        c = self.client()
        cases = [(0, 7, [], True), (6, 7, [], True), (7, 7, [], False), (9, 7, [], False), (0, 7, [7], False), (0, 7, [3], True)]
        for current, offered, failed, newer in cases:
            with self.subTest(current=current, offered=offered, failed=failed):
                self.state(current, failed=failed)
                self.publish(offered)
                res = c.check()
                self.assertEqual((res["ok"], res["newer"], res["serial"], res["version"]), (True, newer, offered, f"0.{offered}.0"))
                self.assertTrue(res["message"])
        self.assertEqual(self.inbox(), [])  # a check never downloads
        self.assertNotIn("/opos-7.opos-update", self.src.hits)

    def test_message_names_the_version_when_newer(self):
        self.conf()
        self.state(0)
        self.publish(7)
        self.assertEqual(self.client().check()["message"], "Version 0.7.0 (update 7) is available.")

    def test_unreadable_state_never_offers(self):
        self.conf()
        self.publish(7)
        c = self.client()
        self.assertFalse(c.check()["newer"])  # no state.json at all
        for text in ("garbage", "[]", '{"current": {"serial": "7"}}', '{"current": {"serial": -3}}', '{"current": 5}'):
            with self.subTest(text=text):
                self.put(self.paths.state, text)
                self.assertFalse(c.check()["newer"])
        with self.assertRaises(uc.UpdateError) as cm:
            c.start_download()
        self.assertEqual(cm.exception.code, "no_update")
        self.assertEqual(self.inbox(), [])

    def test_a_stale_check_is_reworked_against_the_installed_serial(self):
        self.conf()
        self.state(0)
        self.publish(7)
        c = self.client()
        self.assertTrue(c.check()["newer"])
        self.assertTrue(c.snapshot()["check"]["newer"])
        self.state(7)  # root applied it meanwhile
        snap = c.snapshot()["check"]
        self.assertFalse(snap["newer"])
        self.assertEqual(snap["message"], "You have the latest update.")

    def test_malformed_index_is_a_clear_failure(self):
        self.conf()
        self.state(0)
        c = self.client()
        for body in (b"<html>captive portal</html>", b"[]", b"{}", json.dumps({"serial": "7", "version": "1", "url": "x"}).encode(), b""):
            with self.subTest(body=body):
                self.src.add("/latest.json", body)
                with self.assertRaises(uc.UpdateError) as cm:
                    c.check()
                self.assertEqual((cm.exception.code, cm.exception.status), ("bad_index", 502))
                last = c.snapshot()["check"]
                self.assertEqual((last["ok"], last["newer"]), (False, None))

    def test_index_cap_and_content_encoding(self):
        self.conf()
        self.state(0)
        c = self.client()
        self.src.add("/latest.json", b" " * (uc.MAX_INDEX + 10))
        with self.assertRaises(uc.UpdateError) as cm:
            c.check()
        self.assertEqual(cm.exception.code, "too_large")

        def lie(h):  # no Content-Length: the stream itself is cut off
            h.send_response(200)
            h.send_header("Transfer-Encoding", "chunked")
            h.end_headers()
            for _ in range(80):
                h.wfile.write(b"400\r\n" + b" " * 1024 + b"\r\n")
            h.wfile.write(b"0\r\n\r\n")
        self.src.routes["/latest.json"] = lie
        with self.assertRaises(uc.UpdateError) as cm:
            c.check()
        self.assertEqual(cm.exception.code, "too_large")
        self.src.add("/latest.json", b"{}", **{"Content-Encoding": "gzip"})
        with self.assertRaises(uc.UpdateError) as cm:
            c.check()
        self.assertIn("compressed", cm.exception.message)

    def test_http_errors_and_unreachable_source_have_safe_messages(self):
        self.conf()
        self.state(0)
        c = self.client()
        for status in (404, 500, 301):
            self.src.add("/latest.json", b"", status=status)
            with self.subTest(status=status), self.assertRaises(uc.UpdateError) as cm:
                c.check()
            self.assertEqual((cm.exception.code, cm.exception.status), ("fetch", 502))
        self.src.add("/latest.json", b"{}", status=204)
        with self.assertRaises(uc.UpdateError):
            c.check()
        self.src.close()
        with self.assertRaises(uc.UpdateError) as cm:
            c.check()
        self.assertIn("could not be reached", cm.exception.message)
        self.assertNotIn("127.0.0.1", cm.exception.message)
        self.assertNotIn("Errno", cm.exception.message)

    def test_slow_server_times_out(self):
        self.conf()
        self.state(0)

        def stall(h):
            h.send_response(200)
            h.send_header("Content-Length", "100")
            h.end_headers()
            self.src.release.wait(5)
        self.src.routes["/latest.json"] = stall
        t0 = time.monotonic()
        with self.assertRaises(uc.UpdateError) as cm:
            self.client(io_timeout=0.3).check()
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(cm.exception.code, "fetch")

    def test_a_content_length_of_non_ascii_digits_is_a_bad_length_not_a_crash(self):
        """Review finding: "²".isdigit() is true and int("²") raises ValueError, which no caller of check() expects."""
        self.conf()
        self.state(0)
        for bad in ("²", "³", "1¹"):
            with self.subTest(bad):
                def lie(h, bad=bad):
                    h.send_response(200)
                    h.send_header("Content-Length", bad)
                    h.end_headers()
                self.src.routes["/latest.json"] = lie
                with self.assertRaises(uc.UpdateError) as cm:
                    self.client().check()
                self.assertEqual((cm.exception.code, cm.exception.message), ("fetch", "The update server sent a bad length."))

    def test_a_trickle_hits_the_deadline_not_just_the_socket_timeout(self):
        self.conf()
        self.state(0)

        def drip(h):
            h.send_response(200)
            h.send_header("Content-Length", "100")
            h.end_headers()
            for _ in range(60):
                if self.src.release.wait(0.05):
                    return
                try:
                    h.wfile.write(b" ")
                    h.wfile.flush()
                except OSError:
                    return
        self.src.routes["/latest.json"] = drip
        t0 = time.monotonic()
        with self.assertRaises(uc.UpdateError) as cm:
            self.client(io_timeout=2, index_deadline=0.4).check()
        self.assertLess(time.monotonic() - t0, 2.5)
        self.assertIn("too slow", cm.exception.message)

    def test_one_job_at_a_time(self):
        self.conf()
        self.state(0)
        entered = threading.Event()

        def hold(h):
            entered.set()
            self.src.release.wait(5)
            h.send_response(404)
            h.send_header("Content-Length", "0")
            h.end_headers()
        self.src.routes["/latest.json"] = hold
        c = self.client()
        t = threading.Thread(target=lambda: self.assertRaises(uc.UpdateError, c.check))
        t.start()
        self.assertTrue(entered.wait(5))
        for call in (c.check, c.start_download):
            with self.assertRaises(uc.UpdateError) as cm:
                call()
            self.assertEqual((cm.exception.code, cm.exception.status), ("busy", 409))
        self.src.release.set()
        t.join(5)


class Redirects(Env):
    def setUp(self):
        super().setUp()
        self.conf()
        self.state(0)
        self.publish(7)

    def test_same_origin_redirect_is_followed_and_relative_urls_use_the_final_address(self):
        idx = self.src.routes.pop("/latest.json")
        self.src.routes["/dir/latest.json"] = idx
        self.src.add("/old.json", b"", status=302, Location="/dir/latest.json")
        self.src.add("/dir/opos-7.opos-update", self.src.routes["/opos-7.opos-update"][2])
        self.put(self.paths.config, {"url": self.src.url("/old.json"), "allow_lan_http": True})
        c = self.client()
        self.assertTrue(c.check()["newer"])
        c.start_download()
        self.assertTrue(c.wait(10))
        self.assertEqual(c.job()["phase"], "done")

    def test_redirect_to_another_host_or_port_or_scheme_is_refused_and_never_connected(self):
        other = Source()
        self.addCleanup(other.close)
        other.add("/latest.json", self.src.routes["/latest.json"][2])
        for target in (f"http://localhost:{self.src.port}/latest.json", other.url(), "file:///etc/passwd", "ftp://127.0.0.1/x",
                       "https://updates.example.org/latest.json", f"http://127.0.0.1.example.org:{self.src.port}/x"):
            self.src.add("/hop.json", b"", status=301, Location=target)
            self.put(self.paths.config, {"url": self.src.url("/hop.json"), "allow_lan_http": True})
            with self.subTest(target=target), self.assertRaises(uc.UpdateError) as cm:
                self.client().check()
            # urllib itself refuses schemes other than http, https and ftp before our handler sees them
            self.assertIn(cm.exception.code, ("redirect", "fetch") if target.startswith("file:") else ("redirect",))
        self.assertEqual(other.hits, [])

    def test_redirect_loops_end(self):
        self.src.add("/a", b"", status=302, Location="/b")
        self.src.add("/b", b"", status=302, Location="/a")
        self.put(self.paths.config, {"url": self.src.url("/a"), "allow_lan_http": True})
        with self.assertRaises(uc.UpdateError):
            self.client().check()

    def test_bundle_redirect_off_origin_is_refused(self):
        other = Source()
        self.addCleanup(other.close)
        other.add("/opos-7.opos-update", tar_bundle(7))
        self.src.add("/opos-7.opos-update", b"", status=302, Location=other.url("/opos-7.opos-update"))
        c = self.client()
        c.start_download()
        c.wait(10)
        self.assertEqual(c.job()["phase"], "failed")
        self.assertIn("another address", c.job()["message"])
        self.assertEqual((other.hits, self.inbox()), ([], []))


@unittest.skipUnless(shutil.which("openssl"), "needs openssl to make a throw-away certificate")
class Tls(Env):
    def test_a_self_signed_certificate_is_not_accepted(self):
        d = self.tmp.name
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", os.path.join(d, "k.pem"), "-out", os.path.join(d, "c.pem"),
                        "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(d, "c.pem"), os.path.join(d, "k.pem"))
        self.src.httpd.socket = ctx.wrap_socket(self.src.httpd.socket, server_side=True)
        self.put(self.paths.config, {"url": f"https://127.0.0.1:{self.src.port}/latest.json"})
        with self.assertRaises(uc.UpdateError) as cm:
            self.client().check()
        self.assertEqual(cm.exception.code, "fetch")
        self.assertIn("certificate", cm.exception.message)


class Download(Env):
    def setUp(self):
        super().setUp()
        self.conf()
        self.state(0)

    def run_job(self, **limits):
        c = self.client(**limits)
        c.start_download()
        self.assertTrue(c.wait(10))
        return c

    def test_happy_path_leaves_one_complete_bundle_and_nothing_else(self):
        b = self.publish(7)
        c = self.run_job()
        self.assertEqual(c.job()["phase"], "done")
        self.assertEqual((c.job()["bytes"], c.job()["total"], c.job()["serial"]), (len(b), len(b), 7))
        self.assertEqual(self.inbox(), ["opos-7.opos-update"])
        with open(os.path.join(self.paths.inbox, "opos-7.opos-update"), "rb") as f:
            self.assertEqual(f.read(), b)
        self.assertEqual(os.stat(os.path.join(self.paths.inbox, "opos-7.opos-update")).st_mode & 0o007, 0)
        self.assertEqual(c.snapshot()["pending"], ["opos-7.opos-update"])

    def test_pax_tar_from_the_release_tool_is_fine_and_no_hash_in_the_index_is_fine(self):
        self.publish(7, tar_bundle(7, tarfile.PAX_FORMAT), size=False, sha=False)
        self.run_job()
        self.assertEqual(self.inbox(), ["opos-7.opos-update"])

    def test_the_inbox_name_comes_from_the_serial_not_the_server(self):
        self.publish(7, name="..%2f..%2fevil.opos-update")
        self.src.add("/..%2f..%2fevil.opos-update", tar_bundle(7))
        self.run_job()
        self.assertEqual(self.inbox(), ["opos-7.opos-update"])
        self.assertEqual(os.listdir(self.tmp.name).count("evil.opos-update"), 0)

    def test_a_half_file_is_only_ever_a_partial(self):
        b = tar_bundle(7, pad=200000)
        self.publish(7, b)
        gate, started = threading.Event(), threading.Event()

        def slow(h):
            h.send_response(200)
            h.send_header("Content-Length", str(len(b)))
            h.end_headers()
            h.wfile.write(b[:50000])
            h.wfile.flush()
            started.set()
            gate.wait(10)
            h.wfile.write(b[50000:])
        self.src.routes["/opos-7.opos-update"] = slow
        c = self.client()
        self.assertEqual(c.start_download()["phase"], "downloading")
        self.assertTrue(started.wait(5))
        deadline = time.monotonic() + 5
        while "opos-7.opos-update.partial" not in self.inbox() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.inbox(), ["opos-7.opos-update.partial"])
        self.assertEqual(c.snapshot()["pending"], [])  # root's inbox glob never sees it
        self.assertEqual(c.job()["phase"], "downloading")
        gate.set()
        self.assertTrue(c.wait(10))
        self.assertEqual(self.inbox(), ["opos-7.opos-update"])

    def test_the_partial_is_synced_before_the_rename(self):
        self.publish(7)
        order = []
        real_fsync, real_replace = os.fsync, os.replace
        with mock.patch("os.fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1]), \
                mock.patch("os.replace", lambda a, b: (order.append("replace"), real_replace(a, b))[1]):
            self.run_job()
        self.assertEqual(order[:2], ["fsync", "replace"])

    def test_early_end_leaves_nothing(self):
        b = tar_bundle(7, pad=50000)
        self.publish(7, b, size=False, sha=False)

        def cut(h):
            h.send_response(200)
            h.send_header("Content-Length", str(len(b)))
            h.end_headers()
            h.wfile.write(b[:10000])
            h.wfile.flush()
            h.close_connection = True
        self.src.routes["/opos-7.opos-update"] = cut
        c = self.run_job()
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("stopped early", c.job()["message"])

    def test_wrong_hash_or_size_in_the_index_leaves_nothing(self):
        b = self.publish(7)
        idx = json.loads(self.src.routes["/latest.json"][2])
        for field, value in (("sha256", "0" * 64), ("size", len(b) + 1), ("size", len(b) - 1)):
            self.src.add("/latest.json", json.dumps({**idx, field: value}).encode())
            with self.subTest(field=field, value=value):
                c = self.run_job()
                self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))

    def test_wrong_size_without_a_length_header_is_caught_at_the_end(self):
        b = tar_bundle(7)
        self.publish(7, b)

        def chunked(h):
            h.send_response(200)
            h.send_header("Transfer-Encoding", "chunked")
            h.end_headers()
            h.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(b), b))
        self.src.routes["/opos-7.opos-update"] = chunked
        idx = json.loads(self.src.routes["/latest.json"][2])
        self.src.add("/latest.json", json.dumps({**idx, "size": len(b) + 1}).encode())
        c = self.run_job()
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))

    def test_over_the_cap_is_refused_before_a_byte_is_written(self):
        body = tar_bundle(7, pad=20000)
        self.publish(7, body, size=False, sha=False)
        c = self.run_job(max_bundle=4096)
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("larger than OPOS accepts", c.job()["message"])

    def test_over_the_cap_while_streaming_stops_and_cleans_up(self):
        self.publish(7, tar_bundle(7), size=False, sha=False)

        def endless(h):
            h.send_response(200)
            h.send_header("Transfer-Encoding", "chunked")
            h.end_headers()
            for _ in range(200):
                h.wfile.write(b"400\r\n" + b"x" * 1024 + b"\r\n")
            h.wfile.write(b"0\r\n\r\n")
        self.src.routes["/opos-7.opos-update"] = endless
        c = self.run_job(max_bundle=8192)
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("more data than OPOS accepts", c.job()["message"])

    def test_index_size_over_the_cap_is_refused_at_check_time(self):
        self.publish(7, size=False, sha=False)
        idx = json.loads(self.src.routes["/latest.json"][2])
        self.src.add("/latest.json", json.dumps({**idx, "size": 5000}).encode())
        with self.assertRaises(uc.UpdateError) as cm:
            self.client(max_bundle=4096).check()
        self.assertEqual(cm.exception.code, "bad_index")

    def test_an_error_page_is_not_saved_as_a_bundle(self):
        self.publish(7, b"<html>sign in to the hotel wifi</html>" * 100, size=False, sha=False)
        c = self.run_job()
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("not an update bundle", c.job()["message"])
        self.assertEqual(self.client().snapshot()["pending"], [])

    def test_a_stale_partial_is_replaced_and_a_planted_symlink_is_not_written_through(self):
        b = self.publish(7)
        victim = os.path.join(self.tmp.name, "victim")
        with open(victim, "w") as f:
            f.write("keep")
        os.symlink(victim, os.path.join(self.paths.inbox, "opos-7.opos-update.partial"))
        self.run_job()
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")
        self.assertEqual(self.inbox(), ["opos-7.opos-update"])
        with open(os.path.join(self.paths.inbox, "opos-7.opos-update"), "rb") as f:
            self.assertEqual(f.read(), b)
        os.unlink(os.path.join(self.paths.inbox, "opos-7.opos-update"))
        with open(os.path.join(self.paths.inbox, "opos-7.opos-update.partial"), "wb") as f:
            f.write(b"left over from a crash")
        self.run_job()
        self.assertEqual(self.inbox(), ["opos-7.opos-update"])

    def test_a_bundle_already_waiting_is_not_stacked(self):
        self.publish(7)
        with open(os.path.join(self.paths.inbox, "opos-3.opos-update"), "wb") as f:
            f.write(b"x")
        c = self.client()
        with self.assertRaises(uc.UpdateError) as cm:
            c.start_download()
        self.assertEqual(cm.exception.code, "pending")
        self.assertNotIn("/opos-7.opos-update", self.src.hits)
        self.assertEqual(self.inbox(), ["opos-3.opos-update"])

    def test_not_newer_downloads_nothing(self):
        self.publish(7)
        self.state(7)
        with self.assertRaises(uc.UpdateError) as cm:
            self.client().start_download()
        self.assertEqual((cm.exception.code, cm.exception.status), ("no_update", 409))
        self.assertNotIn("/opos-7.opos-update", self.src.hits)

    def test_index_unreachable_downloads_nothing(self):
        self.src.close()
        with self.assertRaises(uc.UpdateError):
            self.client().start_download()
        self.assertEqual(self.inbox(), [])

    def test_not_enough_space_is_refused_up_front(self):
        self.publish(7)
        with mock.patch("shutil.disk_usage", return_value=collections.namedtuple("usage", "total used free")(100, 99, 1)):
            c = self.run_job()
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("free space", c.job()["message"])

    # Review finding 9: a response with no length, or an index with no (or a small) size, used to skip the space check or stream
    # far past what was promised. The stand-in below reports `free` bytes minus what the inbox already holds, like a real disk.
    def disk(self, free):
        usage = collections.namedtuple("usage", "total used free")
        held = lambda: sum(os.path.getsize(os.path.join(self.paths.inbox, n)) for n in os.listdir(self.paths.inbox))
        return mock.patch("shutil.disk_usage", lambda path: usage(free, held(), free - held()))

    def chunked(self, body, path="/opos-7.opos-update", piece=1024):
        """Serve `body` with Transfer-Encoding: chunked, so there is no Content-Length."""
        def serve(h):
            h.send_response(200)
            h.send_header("Transfer-Encoding", "chunked")
            h.end_headers()
            try:
                for i in range(0, len(body), piece):
                    h.wfile.write(b"%x\r\n%s\r\n" % (len(body[i:i + piece]), body[i:i + piece]))
                h.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass  # the client hung up, which is what it should do
        self.src.routes[path] = serve

    def test_a_response_with_no_length_still_keeps_the_headroom_while_it_streams(self):
        body = tar_bundle(7, pad=300000)
        self.publish(7, body, size=False, sha=False)
        self.chunked(body)
        with self.disk(250000):  # 100 KB of headroom: about 150 KB of the 300 KB may be written
            c = self.run_job(headroom=100000, space_step=8192)
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("free space", c.job()["message"])
        self.assertLess(c.job()["bytes"], 150000 + 8192 + uc.CHUNK, "it stopped when the headroom was reached, not at the end")

    def test_the_production_limits_look_at_the_free_space_every_mib_while_a_bundle_streams_in(self):
        """Every test above passes a small space_step, so a production default that was huge (or never reached) kept them all green.
        This one uses the default Limits: 32 MiB of headroom, looked at every MiB, on a reply with no length (so nothing is
        reserved up front and only the look while it streams can stop it)."""
        limits = uc.Limits()
        self.assertEqual((limits.space_step, limits.headroom), (1 << 20, 32 << 20))
        body = tar_bundle(7, pad=5 << 20)
        self.publish(7, body, size=False, sha=False)
        self.chunked(body, piece=1 << 16)
        with self.disk(limits.headroom + 9 * (1 << 18)):  # 2.25 MiB may be written; the looks at 1 and 2 MiB pass, the one at 3 MiB stops it
            c = uc.UpdateClient(self.paths, limits)
            c.start_download()
            self.assertTrue(c.wait(30))
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("free space", c.job()["message"])
        self.assertGreaterEqual(c.job()["bytes"], 3 << 20)
        self.assertLess(c.job()["bytes"], (3 << 20) + 3 * uc.CHUNK, "it stopped at the look after 3 MiB, not at the end of the 5 MiB")

    def test_a_response_with_no_length_is_refused_before_a_byte_when_there_is_no_headroom(self):
        body = tar_bundle(7)
        self.publish(7, body, size=False, sha=False)
        self.chunked(body)
        with self.disk(99999):
            c = self.run_job(headroom=100000)
        self.assertEqual((c.job()["phase"], c.job()["bytes"], self.inbox()), ("failed", 0, []))
        self.assertIn("free space", c.job()["message"])
        with self.disk(100000):  # exactly the headroom is enough for a bundle of unknown length that then turns out small
            c = self.run_job(headroom=100000)
        self.assertEqual((c.job()["phase"], self.inbox()), ("done", ["opos-7.opos-update"]))

    def test_a_promised_size_is_reserved_up_front_and_with_the_headroom(self):
        body = tar_bundle(7, pad=50000)
        for with_index_size in (True, False):  # the index's size, or only the server's Content-Length
            self.publish(7, body, size=with_index_size, sha=False)
            with self.subTest(index_size=with_index_size):
                with self.disk(len(body) + 1000 - 1):
                    c = self.run_job(headroom=1000)
                self.assertEqual((c.job()["phase"], c.job()["bytes"], self.inbox()), ("failed", 0, []))
                with self.disk(len(body) + 1000):
                    c = self.run_job(headroom=1000)
                self.assertEqual((c.job()["phase"], self.inbox()), ("done", ["opos-7.opos-update"]))
                os.unlink(os.path.join(self.paths.inbox, "opos-7.opos-update"))

    def test_a_promised_size_is_a_ceiling_for_the_stream(self):
        """The index says 5 KB; the server sends 300 KB with no length. The client stops at 5 KB, not after all 300 KB."""
        small = tar_bundle(7)
        self.publish(7, small, sha=False)
        self.chunked(small + b"\0" * 300000)
        c = self.run_job()
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("bigger than its list promised", c.job()["message"])
        self.assertLessEqual(c.job()["bytes"], len(small), "no byte past the promise was written")

    def test_a_stale_partial_does_not_count_against_the_room(self):
        body = tar_bundle(7)
        self.publish(7, body)
        with open(os.path.join(self.paths.inbox, "opos-7.opos-update.partial"), "wb") as f:
            f.write(b"x" * 100000)
        with self.disk(len(body) + 1000 + 50000):  # room for the bundle and the headroom, but only once the 100 KB leftover is gone
            c = self.run_job(headroom=1000)
        self.assertEqual((c.job()["phase"], self.inbox()), ("done", ["opos-7.opos-update"]))

    def test_missing_inbox(self):
        self.publish(7)
        shutil.rmtree(self.paths.inbox)
        with self.assertRaises(uc.UpdateError) as cm:
            self.client().start_download()
        self.assertEqual(cm.exception.code, "inbox")

    def test_download_always_refetches_the_index(self):
        self.publish(7)
        c = self.client()
        c.check()
        self.state(7)  # installed meanwhile
        with self.assertRaises(uc.UpdateError):
            c.start_download()
        self.assertEqual(self.src.hits.count("/latest.json"), 2)

    def test_a_second_download_after_a_failure_works(self):
        self.publish(7, b"<html>nope</html>" * 100, size=False, sha=False)
        c = self.run_job()
        self.assertEqual(c.job()["phase"], "failed")
        self.publish(7)
        c.start_download()
        self.assertTrue(c.wait(10))
        self.assertEqual((c.job()["phase"], self.inbox()), ("done", ["opos-7.opos-update"]))

    def test_slow_bundle_hits_its_deadline(self):
        b = tar_bundle(7)
        self.publish(7, b, size=False, sha=False)

        def drip(h):
            h.send_response(200)
            h.send_header("Content-Length", str(len(b)))
            h.end_headers()
            for i in range(len(b)):
                if self.src.release.wait(0.02):
                    return
                try:
                    h.wfile.write(b[i:i + 1])
                    h.wfile.flush()
                except OSError:
                    return
        self.src.routes["/opos-7.opos-update"] = drip
        c = self.run_job(bundle_deadline=0.3, io_timeout=2)
        self.assertEqual((c.job()["phase"], self.inbox()), ("failed", []))
        self.assertIn("too slow", c.job()["message"])


class RollbackRequest(Env):
    def test_creates_an_empty_request_once(self):
        self.state(2, previous=["1-bbbbbbbbbbbb"])
        c = self.client()
        self.assertEqual(c.request_rollback(), {"requested": True, "already": False})
        p = os.path.join(self.paths.inbox, "rollback.request")
        self.assertEqual(os.path.getsize(p), 0)
        self.assertEqual(c.request_rollback(), {"requested": True, "already": True})
        snap = c.snapshot()
        self.assertEqual(snap["rollback"], {"possible": True, "requested": True})
        self.assertEqual(snap["pending"], [])

    def test_refused_without_an_earlier_version_or_a_readable_state(self):
        c = self.client()
        with self.assertRaises(uc.UpdateError) as cm:
            c.request_rollback()  # no state.json
        self.assertEqual(cm.exception.code, "no_rollback")
        self.state(0, previous=[])
        with self.assertRaises(uc.UpdateError) as cm:
            c.request_rollback()
        self.assertEqual(cm.exception.code, "no_rollback")
        self.assertEqual(self.inbox(), [])
        self.assertEqual(c.snapshot()["rollback"], {"possible": False, "requested": False})

    def test_only_one_step_back_per_applied_update(self):
        """Review finding: any process of the service user could ask again and again and walk the phone back through its
        releases. Root honours one request per applied update; the client says so and does not even write the file."""
        self.state(1, previous=["0-aaaaaaaaaaaa"], stepped_back=2)
        c = self.client()
        with self.assertRaises(uc.UpdateError) as cm:
            c.request_rollback()
        self.assertEqual((cm.exception.code, cm.exception.status, cm.exception.message), ("no_rollback", 409, uc.ONCE_ONLY))
        self.assertEqual(self.inbox(), [])
        self.assertEqual(c.snapshot()["rollback"], {"possible": False, "requested": False, "once": True})
        self.state(1, previous=["0-aaaaaaaaaaaa"], stepped_back=None)  # an update was applied since: the marker is gone
        self.assertEqual(c.snapshot()["rollback"], {"possible": True, "requested": False})
        self.assertEqual(c.request_rollback(), {"requested": True, "already": False})

    def test_nothing_earlier_beats_the_once_note(self):
        self.state(0, previous=[], stepped_back=3)
        self.assertEqual(self.client().snapshot()["rollback"], {"possible": False, "requested": False})

    def test_not_on_an_install_without_the_update_folder(self):
        shutil.rmtree(self.paths.root)
        with self.assertRaises(uc.UpdateError) as cm:
            self.client().request_rollback()
        self.assertEqual((cm.exception.code, cm.exception.status), ("not_available", 404))

    # Review finding 11: the first staged boot update could not be cancelled, because the client wanted an earlier release first.
    # The updater (docs/ota-update.md, "Inbox") reads rollback.request as empty = go back, or `cancel-staged` = drop the staged update
    # and nothing else; the cancel form must be written as rollback.request.partial and renamed.
    STAGED = {"serial": 3, "version": "0.3.0", "revision": "b" * 40, "release": "3-bbbbbbbbbbbb"}

    def request_text(self):
        with open(os.path.join(self.paths.inbox, "rollback.request"), "rb") as f:
            return f.read()

    def test_a_staged_update_is_cancelled_with_the_cancel_text_even_without_an_earlier_release(self):
        self.state(0, previous=[], pending_boot=self.STAGED)
        c = self.client()
        self.assertEqual(c.snapshot()["rollback"], {"possible": True, "requested": False, "staged": 3})
        self.assertEqual(c.request_rollback(), {"requested": True, "already": False, "cancel": True})
        self.assertEqual((self.request_text(), self.inbox()), (b"cancel-staged", ["rollback.request"]), "no .partial is left behind")
        self.assertEqual(c.snapshot()["rollback"], {"possible": True, "requested": True, "staged": 3})
        self.assertEqual(c.request_rollback(), {"requested": True, "already": True, "cancel": True})
        self.assertEqual(self.request_text(), b"cancel-staged")

    def test_the_cancel_text_is_whole_before_root_can_see_a_file_with_the_request_name(self):
        self.state(0, previous=[], pending_boot=self.STAGED)
        path = os.path.join(self.paths.inbox, "rollback.request")
        seen = []
        real = os.replace

        def replace(a, b):
            with open(a, "rb") as f:
                seen.append((os.path.basename(a), f.read(), os.path.lexists(b)))
            return real(a, b)
        with mock.patch("os.replace", replace):
            self.client().request_rollback()
        self.assertEqual(seen, [("rollback.request.partial", b"cancel-staged", False)])
        self.assertTrue(os.path.exists(path))

    def test_a_staged_update_can_be_cancelled_after_the_one_step_back_was_used(self):
        self.state(1, previous=["0-aaaaaaaaaaaa"], stepped_back=2, pending_boot=self.STAGED)
        c = self.client()
        self.assertEqual(c.snapshot()["rollback"], {"possible": True, "requested": False, "once": True, "staged": 3})
        self.assertEqual(c.request_rollback(), {"requested": True, "already": False, "cancel": True})
        self.assertEqual(self.request_text(), b"cancel-staged")

    def test_a_cancel_never_asks_for_the_installed_release_to_go_back_too(self):
        """With an earlier release and an update staged, the empty request would roll the installed release back as well."""
        self.state(2, previous=["1-aaaaaaaaaaaa"], pending_boot=self.STAGED)
        self.client().request_rollback()
        self.assertEqual(self.request_text(), b"cancel-staged")

    def test_a_request_that_is_already_there_is_left_alone(self):
        self.state(2, previous=["1-aaaaaaaaaaaa"])
        c = self.client()
        c.request_rollback()  # an empty one: go back
        self.state(2, previous=["1-aaaaaaaaaaaa"], pending_boot=self.STAGED)
        self.assertEqual(c.request_rollback(), {"requested": True, "already": True, "cancel": True})
        self.assertEqual(self.request_text(), b"", "it was not replaced")

    def test_a_leftover_or_planted_partial_is_not_written_through(self):
        self.state(0, previous=[], pending_boot=self.STAGED)
        victim = os.path.join(self.tmp.name, "victim")
        with open(victim, "w") as f:
            f.write("keep")
        part = os.path.join(self.paths.inbox, "rollback.request.partial")
        os.symlink(victim, part)
        self.client().request_rollback()
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")
        self.assertEqual((self.request_text(), self.inbox()), (b"cancel-staged", ["rollback.request"]))
        os.unlink(os.path.join(self.paths.inbox, "rollback.request"))
        with open(part, "wb") as f:
            f.write(b"junk from a crash")
        self.client().request_rollback()
        self.assertEqual((self.request_text(), self.inbox()), (b"cancel-staged", ["rollback.request"]))

    def test_an_updater_from_before_the_cancel_form_is_never_sent_it(self):
        """The updater is in the image and this client in the app, so the client can be newer. The old updater reads any request file as
        'go back', so the cancel text would roll the installed release back, or do nothing: such a phone keeps the old behaviour."""
        self.state(0, previous=[], pending_boot=self.STAGED, floor=None)
        c = self.client()
        self.assertEqual(c.snapshot()["rollback"], {"possible": False, "requested": False})
        with self.assertRaises(uc.UpdateError) as cm:
            c.request_rollback()
        self.assertEqual(cm.exception.code, "no_rollback")
        self.state(2, previous=["1-aaaaaaaaaaaa"], pending_boot=self.STAGED, floor=None)
        self.assertEqual(c.snapshot()["rollback"], {"possible": True, "requested": False})
        self.assertEqual(c.request_rollback(), {"requested": True, "already": False})
        self.assertEqual(self.request_text(), b"", "the plain request, as before")
        for odd in ("0", -1, True, [0]):
            with self.subTest(floor=odd):
                self.state(0, previous=[], pending_boot=self.STAGED, floor=odd)
                self.assertNotIn("staged", c.snapshot()["rollback"])

    def test_with_nothing_staged_the_old_rules_stand(self):
        for kw, code in (({"previous": []}, "no_rollback"), ({"previous": ["0-aaaaaaaaaaaa"], "stepped_back": 2}, "no_rollback")):
            with self.subTest(kw):
                self.state(1, **kw)
                with self.assertRaises(uc.UpdateError) as cm:
                    self.client().request_rollback()
                self.assertEqual(cm.exception.code, code)
        self.assertEqual(self.inbox(), [])

    def test_an_odd_pending_boot_entry_counts_as_nothing_staged(self):
        for odd in ({"serial": "3"}, {"serial": -1}, {"serial": True}, {"version": "0.3.0"}, [3], "3", 3, {}):
            with self.subTest(odd):
                self.state(0, previous=[], pending_boot=odd)
                self.assertEqual(self.client().snapshot()["rollback"], {"possible": False, "requested": False})
                with self.assertRaises(uc.UpdateError):
                    self.client().request_rollback()

    def test_planted_symlink_is_not_followed(self):
        self.state(2, previous=["1-bbbbbbbbbbbb"])
        victim = os.path.join(self.tmp.name, "victim")
        os.symlink(victim, os.path.join(self.paths.inbox, "rollback.request"))
        self.client().request_rollback()
        self.assertFalse(os.path.exists(victim))

    def test_missing_inbox(self):
        self.state(2, previous=["1-bbbbbbbbbbbb"])
        shutil.rmtree(self.paths.inbox)
        with self.assertRaises(uc.UpdateError) as cm:
            self.client().request_rollback()
        self.assertEqual(cm.exception.code, "inbox")


class StatusAndVersion(Env):
    def test_status_is_passed_through_cleaned(self):
        c = self.client()
        self.assertIsNone(c.read_status())
        self.status(phase="applying", message="Step 2 of 4\n\x1b[31m done‮", serial=7)
        self.assertEqual(c.read_status(), {"phase": "applying", "message": "Step 2 of 4 [31m done", "serial": 7, "time": "2026-11-08T12:00:00Z"})
        for phase in uc._PHASES:
            self.status(phase=phase)
            self.assertEqual(c.read_status()["phase"], phase)
        self.status(phase="exploding")
        self.assertEqual(c.read_status()["phase"], "unknown")

    def test_status_bounds_and_garbage(self):
        c = self.client()
        self.status(phase="failed", message="x" * 5000)
        self.assertEqual(len(c.read_status()["message"]), 300)
        for text in ("garbage", "[]", '"idle"', "", " " * 9000):
            self.put(self.paths.status, text)
            self.assertIsNone(c.read_status())
        self.put(self.paths.status, {"phase": "done", "message": 5, "serial": "7", "time": 3})
        self.assertEqual(c.read_status(), {"phase": "done", "message": "", "serial": None, "time": None})

    def test_running_version_comes_from_state_then_revision_file_then_the_build(self):
        c = self.client()
        with mock.patch.object(notices, "BUILD", notices.UNPUBLISHED_BUILD):
            self.assertEqual(c.running(), {"version": notices.VERSION, "revision": None, "serial": None, "release": None})
        with mock.patch.object(notices, "BUILD", "abcdef1234567"[:12]):
            self.assertEqual(c.running()["revision"], "abcdef123456")
        with open(self.paths.revision, "w") as f:
            f.write("1" * 40 + "\n")
        self.assertEqual(c.running()["revision"], "1" * 40)
        self.state(5, version="0.9.1", revision="2" * 40, release="5-222222222222")
        self.assertEqual(c.running(), {"version": "0.9.1", "revision": "1" * 40, "serial": 5, "release": "5-222222222222"})
        os.unlink(self.paths.revision)
        self.assertEqual(c.running()["revision"], "2" * 40)
        self.state(5, version="<b>x</b>", revision="not hex")
        run = c.running()
        self.assertEqual(run["version"], notices.VERSION)

    def test_snapshot_is_json_and_complete(self):
        self.conf()
        self.state(3, previous=["2-cccccccccccc"], failed=[4, "x", -1])
        self.status(phase="done", serial=3, message="Updated.")
        snap = self.client().snapshot()
        json.dumps(snap)
        self.assertEqual(set(snap), {"available", "message", "running", "source", "check", "job", "pending", "stalled", "rollback", "status"})
        self.assertTrue(snap["available"])
        self.assertIsNone(snap["message"])
        self.assertEqual(snap["status"]["message"], "Updated.")
        self.assertEqual(snap["job"]["phase"], "idle")

    def test_not_available_without_the_update_folder(self):
        shutil.rmtree(self.paths.root)
        c = self.client()
        snap = c.snapshot()
        self.assertFalse(snap["available"])
        self.assertEqual(snap["message"], "Updates are not available on this install.")
        self.assertEqual((snap["status"], snap["pending"], snap["rollback"]), (None, [], {"possible": False, "requested": False}))
        for call in (c.check, c.start_download, c.request_rollback):
            with self.assertRaises(uc.UpdateError) as cm:
                call()
            self.assertEqual((cm.exception.code, cm.exception.status), ("not_available", 404))


class AutoCheck(Env):
    def test_on_it_checks_and_never_downloads(self):
        self.conf(auto_check=True)
        self.state(0)
        self.publish(7)
        c = self.client()
        t = c.start_auto_check(first_delay=0.01, interval=0.05)
        self.assertIsNotNone(t)
        self.assertIs(c.start_auto_check(first_delay=0.01, interval=0.05), t)
        deadline = time.monotonic() + 5
        while self.src.hits.count("/latest.json") < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        c.stop()
        t.join(2)
        self.assertGreaterEqual(self.src.hits.count("/latest.json"), 2)
        self.assertNotIn("/opos-7.opos-update", self.src.hits)
        self.assertEqual(self.inbox(), [])
        self.assertTrue(c.snapshot()["check"]["newer"])

    def test_turning_it_off_in_the_file_stops_the_fetches(self):
        self.conf(auto_check=True)
        self.state(0)
        self.publish(7)
        c = self.client()
        t = c.start_auto_check(first_delay=0.01, interval=0.05)
        deadline = time.monotonic() + 5
        while not self.src.hits and time.monotonic() < deadline:
            time.sleep(0.01)
        self.conf(auto_check=False)
        time.sleep(0.2)
        n = len(self.src.hits)
        time.sleep(0.3)
        c.stop()
        t.join(2)
        self.assertEqual(len(self.src.hits), n)

    def test_failures_do_not_kill_the_loop(self):
        self.conf(auto_check=True)
        self.state(0)
        c = self.client()
        t = c.start_auto_check(first_delay=0.01, interval=0.05)  # /latest.json is a 404
        deadline = time.monotonic() + 5
        while self.src.hits.count("/latest.json") < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        c.stop()
        t.join(2)
        self.assertGreaterEqual(self.src.hits.count("/latest.json"), 3)

    def test_the_defaults_are_slow_enough_for_a_battery_phone(self):
        self.assertGreaterEqual(uc.AUTO_EVERY_S, 6 * 3600)
        self.assertGreaterEqual(uc.AUTO_FIRST_S, 60)


class Stalled(Env):
    """A delivered bundle or rollback request that root has not taken for minutes means the updater is not running."""

    def put_file(self, name):
        p = os.path.join(self.paths.inbox, name)
        with open(p, "wb") as f:
            f.write(b"x")
        return p

    def test_fresh_files_are_not_stalled(self):
        self.state(0, previous=["0-aaaaaaaaaaaa"])
        self.put_file("opos-8.opos-update")
        self.put_file("rollback.request")
        self.assertFalse(self.client().snapshot()["stalled"])

    def test_an_old_bundle_or_request_is_stalled_even_if_its_mtime_was_kept_old(self):
        self.state(0, previous=["0-aaaaaaaaaaaa"])
        later = lambda: time.time() + uc.STALL_S + 60  # noqa: E731
        for name in ("opos-8.opos-update", "rollback.request"):
            with self.subTest(name):
                p = self.put_file(name)
                c = uc.UpdateClient(self.paths, uc.Limits(), clock=later)
                self.assertTrue(c.snapshot()["stalled"])
                self.assertFalse(self.client().snapshot()["stalled"])
                os.utime(p, (1, 1))  # `cp -p` of an old file: the change time still says it was delivered just now
                self.assertFalse(self.client().snapshot()["stalled"])
                os.unlink(p)
                self.assertFalse(c.snapshot()["stalled"])

    def test_partial_files_and_other_names_are_not_deliveries(self):
        self.state(0)
        for name in ("opos-8.opos-update.partial", "notes.txt"):
            self.put_file(name)
        later = lambda: time.time() + uc.STALL_S + 60  # noqa: E731
        self.assertFalse(uc.UpdateClient(self.paths, uc.Limits(), clock=later).snapshot()["stalled"])

    def test_not_on_an_install_without_the_update_folder(self):
        shutil.rmtree(self.paths.root)
        self.assertIs(self.client().snapshot()["stalled"], False)


class Page(unittest.TestCase):
    def snap(self, **kw):
        base = {"available": True, "message": None, "running": {"version": "0.1.0", "revision": "ab" * 20, "serial": 0, "release": "0-abababababab"},
                "source": {"configured": True, "url": "https://updates.example.org/latest.json", "auto_check": False, "allow_lan_http": False, "error": None},
                "check": None, "job": {"kind": None, "phase": "idle", "bytes": 0, "total": None, "serial": None, "message": ""},
                "pending": [], "rollback": {"possible": True, "requested": False}, "status": {"phase": "idle", "message": "", "serial": None, "time": None}}
        base.update(kw)
        return base

    def test_once_only_rollback_is_explained_not_called_missing(self):
        h = update_page.page("t" * 43, "n" * 22, self.snap(rollback={"possible": False, "requested": False, "once": True}))
        self.assertIn(uc.ONCE_ONLY, h.split('<p id="u-rb-why">', 1)[1].split("</p>", 1)[0])
        self.assertIn('id="u-rb" disabled', h)
        h = update_page.page("t" * 43, "n" * 22, self.snap(rollback={"possible": False, "requested": False}))
        self.assertIn("There is no earlier version to go back to.", h.split('<p id="u-rb-why">', 1)[1].split("</p>", 1)[0])
        script = h.split("</main>", 1)[1]
        self.assertIn("rb.once?ONCE:", script)
        self.assertIn("var ONCE=" + json.dumps(uc.ONCE_ONLY) + ",", script)
        self.assertNotIn("@@ONCE@@", h)

    def test_a_staged_update_is_explained_and_can_be_cancelled_from_the_page(self):
        plain = update_page.page("t" * 43, "n" * 22, self.snap())
        self.assertIn("Asks the updater to return to the earlier version.", plain.split('<p id="u-rb-intro">', 1)[1].split("</p>", 1)[0])
        self.assertIn(">Request rollback</button>", plain)
        staged = {"possible": True, "requested": False, "staged": 3}
        h = update_page.page("t" * 43, "n" * 22, self.snap(rollback=staged))
        self.assertEqual(h.split('<p id="u-rb-intro">', 1)[1].split("</p>", 1)[0],
                         "Update 3 is staged and installs at the next restart. The button cancels it; what is installed stays as it is.")
        self.assertIn(">Cancel staged update</button>", h)
        self.assertNotIn('id="u-rb" disabled', h)
        self.assertIn(">Cancel requested</button>", update_page.page("t", "n", self.snap(rollback=dict(staged, requested=True))))
        script = h.split("</main>", 1)[1]
        self.assertNotIn("@@", script)
        for needle in ("STAGED=" + json.dumps(update_page._STAGED), "CANCEL=" + json.dumps(update_page._CANCEL), "BACK=" + json.dumps(update_page._BACK),
                       "LAB=" + json.dumps(update_page._LABELS), "rb.staged==null?BACK:STAGED.replace('{}',String(rb.staged))",
                       "last.rollback.staged!=null?CANCEL:", "LAB[rb.staged==null?'back':'cancel'][rb.requested?1:0]"):
            self.assertIn(needle, script)
        evil = update_page.page("t", "n", self.snap(rollback={"possible": True, "requested": False, "staged": "<img src=x>"}))
        self.assertNotIn("<img", evil)

    def test_a_stalled_updater_is_said_out_loud(self):
        quiet = update_page.page("t" * 43, "n" * 22, self.snap(stalled=False))
        loud = update_page.page("t" * 43, "n" * 22, self.snap(stalled=True))
        self.assertIn('<p id="u-stall" role="alert" hidden>', quiet)
        self.assertIn('<p id="u-stall" role="alert">The updater has not picked up what was delivered', loud)
        self.assertIn("H('u-stall',!s.stalled)", quiet)

    def test_facts_are_in_the_html(self):
        h = update_page.page("t" * 43, "n" * 22, self.snap(check={"time": "2026-11-08 12:00:00Z", "ok": True, "newer": True, "serial": 7, "version": "0.7.0",
                                                                    "size": 10, "message": "Version 0.7.0 (update 7) is available."},
                                                          status={"phase": "rolled-back", "message": "Update 6 failed its health check.", "serial": 6, "time": None}))
        for needle in ("Software updates", "0.1.0", "abababababab", "https://updates.example.org/latest.json", "Check for updates", "Download and apply",
                       "Request rollback", "Version 0.7.0 (update 7) is available.", "rolled-back", "Update 6 failed its health check."):
            self.assertIn(needle, h)
        self.assertNotIn("ab" * 20, h)  # twelve characters of the revision
        self.assertNotIn('id="u-go" hidden', h)
        self.assertIn('<meta name="opos-token" content="' + "t" * 43 + '">', h)

    def test_download_button_is_hidden_until_a_newer_update_is_known(self):
        self.assertIn('id="u-go" hidden', update_page.page("t", "n", self.snap()))
        h = update_page.page("t", "n", self.snap(check={"time": "x", "ok": True, "newer": False, "serial": 0, "version": "0.1.0", "size": None, "message": "You have the latest update."}))
        self.assertIn('id="u-go" hidden', h)
        self.assertIn("You have the latest update.", h)

    def test_unconfigured_says_nothing_is_contacted_and_disables_check(self):
        h = update_page.page("t", "n", self.snap(source={"configured": False, "url": None, "auto_check": False, "allow_lan_http": False, "error": None}))
        self.assertIn("Not set. OPOS contacts no server for updates.", h)
        self.assertRegex(h, r'id="u-check" disabled')

    def test_not_available_says_so_plainly(self):
        h = update_page.page("t", "n", {"available": False, "message": "Updates are not available on this install."})
        self.assertIn('<p id="u-na" role="status">Updates are not available on this install.</p>', h)
        self.assertIn('<div id="u-live" hidden>', h)
        self.assertIn("Back to the phone", h)
        self.assertIsNotNone(update_page.page())  # no snapshot at all is the same as not available
        self.assertIn('id="u-live" hidden', update_page.page())

    def test_everything_dynamic_is_escaped(self):
        evil = '</script><img src=x onerror=alert(1)>"\''
        h = update_page.page(evil, "n", self.snap(
            running={"version": evil, "revision": evil, "serial": evil, "release": None},
            source={"configured": True, "url": evil, "auto_check": True, "allow_lan_http": False, "error": None},
            check={"time": evil, "ok": True, "newer": True, "serial": 1, "version": "1", "size": None, "message": evil},
            job={"kind": "download", "phase": "failed", "bytes": 0, "total": None, "serial": 1, "message": evil},
            pending=[evil], status={"phase": evil, "message": evil, "serial": None, "time": None}))
        self.assertNotIn("<img", h)
        self.assertEqual(h.count("</script>"), 1)  # only the page's own
        self.assertNotIn("onerror=alert(1)>", h.replace("&gt;", ""))
        self.assertIn("&lt;/script&gt;", h)

    def test_nonce_goes_on_every_script_and_style_and_nothing_loads_from_outside(self):
        h = update_page.page("t" * 43, "n" * 22, self.snap())
        self.assertEqual(len(re.findall(r"<script nonce=\"n{22}\">", h)), 1)
        self.assertEqual(len(re.findall(r"<style nonce=\"n{22}\">", h)), 1)
        self.assertEqual(len(re.findall(r"<(?:script|style)\b", h)), 2)
        self.assertNotRegex(h, r"""(?i)\b(?:src|href)\s*=\s*["'](?:https?:)?//""")
        markup = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", "", h)
        self.assertNotRegex(markup, r"(?i)<[^>]*\son[a-z]+\s*=")  # the CSP forbids inline handlers
        self.assertNotRegex(h, r"(?i)@import|url\(\s*[\"']?https?:|<link[^>]*stylesheet|<iframe|<form")

    def test_accessible_structure(self):
        h = update_page.page("t", "n", self.snap())
        ids = []

        class P(html.parser.HTMLParser):
            def __init__(s):
                super().__init__()
                s.tags, s.buttons, s.labelled = [], [], []

            def handle_starttag(s, tag, attrs):
                a = dict(attrs)
                s.tags.append(tag)
                if "id" in a:
                    ids.append(a["id"])
                if tag == "button":
                    s.buttons.append(a)
                if tag == "section":
                    s.labelled.append(a.get("aria-labelledby"))
                self.assertNotIn("tabindex", a)
        p = P()
        p.feed(h)
        self.assertTrue(h.startswith("<!doctype html><html lang=\"en\">"))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(p.tags.count("h1"), 1)
        self.assertEqual(p.tags.count("main"), 1)
        self.assertIn('name="viewport"', h)
        self.assertRegex(h, r"<title>[^<]+</title>")
        self.assertGreaterEqual(len(p.buttons), 4)
        for b in p.buttons:
            self.assertEqual(b.get("type"), "button")
        for ref in p.labelled:
            self.assertIn(ref, ids)
        self.assertEqual(p.tags.count("section"), 5)
        self.assertIn('aria-live="polite"', h)
        self.assertRegex(h, r'<progress[^>]*aria-label="Download progress"')
        self.assertIn("<noscript>", h)
        # keyboard: only native buttons and one link carry actions, and every focus ring comes from the shared stylesheet
        self.assertNotIn("role=\"button\"", h)
        self.assertIn(":focus-visible", h)

    def test_it_reuses_the_main_pages_tokens(self):
        h = update_page.page("t", "n", self.snap())
        css = (update_page._STATIC / "jaryo.css").read_text(encoding="utf-8")
        self.assertIn(css, h)
        for token in ("--jaryo-fg", "--jaryo-bg", "--jaryo-serif", "prefers-color-scheme:dark", 'html[data-theme="dark"]'):
            self.assertIn(token, h)
        self.assertIn('html[data-text="xl"]{font-size:150%}', h)  # same text sizes as phone-b.css
        main_css = (update_page._STATIC / "phone-b.css").read_text(encoding="utf-8")
        self.assertIn('html[data-text="xl"]{font-size:150%}', main_css)

    def test_it_is_small(self):
        self.assertLess(len(update_page.page("t" * 43, "n" * 22, self.snap()).encode()), 40 * 1024)

    def test_script_reads_the_main_pages_saved_theme_and_text_size(self):
        self.assertIn("jaryo_theme", update_page._JS)
        self.assertIn("opos_text", update_page._JS)
        self.assertIn("jaryo_theme", (update_page._STATIC / "jaryo.js").read_text(encoding="utf-8"))
        self.assertIn("opos_text", (update_page._STATIC / "phone-b.js").read_text(encoding="utf-8"))


def csp_nonce(headers):
    m = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", headers["content-security-policy"])
    assert m
    return m.group(1)


class Routes(Env):
    def serve(self, client=None, **kw):
        run = webclient.Running(updater=client or self.client(), **kw)
        run.__enter__()
        self.addCleanup(run.__exit__, None, None, None)
        return run

    def post(self, run, action, **kw):
        return webclient.post_action(run.port, action, run.token, **kw)

    def get_json(self, run, path="/api/update", token=True):
        return webclient.request(run.port, "GET", path, headers={"X-OPOS-Token": run.token} if token else {})

    def test_get_update_page(self):
        self.conf()
        self.state(0)
        run = self.serve()
        s, h, b = webclient.request(run.port, "GET", "/update")
        self.assertEqual(s, 200)
        self.assertEqual(h["content-type"], "text/html; charset=utf-8")
        self.assertEqual(h["cache-control"], "no-store")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertIn("default-src 'none'", h["content-security-policy"])
        n = csp_nonce(h)
        text = b.decode()
        self.assertEqual(len(re.findall(rf'<script nonce="{n}">', text)), 1)
        self.assertEqual(len(re.findall(rf'<style nonce="{n}">', text)), 1)
        self.assertIn(f'<meta name="opos-token" content="{run.token}">', text)
        self.assertIn("Software updates", text)
        s2, _, b2 = webclient.request(run.port, "HEAD", "/update")
        self.assertEqual((s2, b2), (200, b""))

    def test_page_and_api_refuse_a_foreign_host(self):
        run = self.serve()
        for path in ("/update", "/api/update"):
            s, _, _ = webclient.request(run.port, "GET", path, headers={"Host": "evil.example", "X-OPOS-Token": run.token})
            self.assertEqual(s, 403, path)

    def test_get_api_update_needs_the_token(self):
        self.conf()
        self.state(2, previous=["1-aaaaaaaaaaaa"])
        run = self.serve()
        s, _, _ = self.get_json(run, token=False)
        self.assertEqual(s, 403)
        s, _, _ = webclient.request(run.port, "GET", "/api/update", headers={"X-OPOS-Token": "wrong"})
        self.assertEqual(s, 403)
        s, h, b = self.get_json(run)
        self.assertEqual(s, 200)
        self.assertEqual(h["content-type"], "application/json")
        snap = json.loads(b)
        self.assertEqual((snap["available"], snap["running"]["serial"], snap["rollback"]["possible"]), (True, 2, True))
        self.assertEqual(self.src.hits, [])  # reading the page and the snapshot never touches the source

    def test_post_rules_apply_to_the_update_actions(self):
        self.conf()
        self.state(0)
        run = self.serve()
        base = {"Host": f"127.0.0.1:{run.port}", "X-OPOS-Token": run.token, "Content-Type": "application/json"}
        body = json.dumps({"action": "update_check"}).encode()

        def go(**over):
            hdr = {**base, **over}
            hdr = {k: v for k, v in hdr.items() if v is not None}
            return webclient.request(run.port, "POST", "/api", body=body, headers=hdr)[0]
        self.assertEqual(go(**{"X-OPOS-Token": None}), 403)
        self.assertEqual(go(**{"X-OPOS-Token": "x" * 43}), 403)
        self.assertEqual(go(Origin="http://evil.example"), 403)
        self.assertEqual(go(Host="evil.example"), 403)
        self.assertEqual(go(**{"Content-Type": "text/plain"}), 415)
        self.assertEqual(go(**{"Content-Type": None}), 415)
        self.assertEqual(self.src.hits, [])
        self.assertEqual(go(Origin=f"http://127.0.0.1:{run.port}"), 502)  # allowed through: the empty source answers 404

    def test_update_actions_take_no_arguments(self):
        self.conf()
        self.state(0)
        run = self.serve()
        for extra in ({"url": "http://127.0.0.1:1/x"}, {"serial": 3}, {"force": True}):
            s, _, j = self.post(run, "update_check", **extra)
            self.assertEqual((s, j["error"]), (400, "bad request"))
        self.assertEqual(self.src.hits, [])

    def test_unknown_update_action_is_unknown(self):
        run = self.serve()
        s, _, j = self.post(run, "update_install")
        self.assertEqual((s, j["error"]), (400, "unknown action"))

    def test_rate_limits(self):
        self.conf()
        self.state(0)
        run = self.serve()
        for action, burst in (("update_check", 3), ("update_download", 2), ("update_rollback", 2)):
            codes = [self.post(run, action)[0] for _ in range(burst + 2)]
            self.assertEqual(codes[burst:], [429, 429], (action, codes))
            self.assertNotIn(429, codes[:burst], (action, codes))

    def test_check_download_and_status_through_the_routes(self):
        self.conf()
        self.state(0)
        self.status(phase="idle")
        b = self.publish(7)
        run = self.serve()
        s, _, j = self.post(run, "update_check")
        self.assertEqual(s, 200)
        self.assertTrue(j["ok"])
        self.assertEqual((j["check"]["newer"], j["check"]["serial"]), (True, 7))
        s, _, j = self.post(run, "update_download")
        self.assertEqual(s, 200)
        self.assertIn(j["job"]["phase"], ("downloading", "done"))
        deadline = time.monotonic() + 10
        snap = j
        while snap["job"]["phase"] == "downloading" and time.monotonic() < deadline:
            time.sleep(0.02)
            snap = json.loads(self.get_json(run)[2])
        self.assertEqual(snap["job"]["phase"], "done")
        self.assertEqual(snap["pending"], ["opos-7.opos-update"])
        with open(os.path.join(self.paths.inbox, "opos-7.opos-update"), "rb") as f:
            self.assertEqual(f.read(), b)
        s, _, j = self.post(run, "update_rollback")
        self.assertEqual((s, j["code"]), (409, "no_rollback"))  # state has no earlier version

    def test_rollback_through_the_route(self):
        self.state(2, previous=["1-aaaaaaaaaaaa"])
        run = self.serve()
        s, _, j = self.post(run, "update_rollback")
        self.assertEqual(s, 200)
        self.assertTrue(j["rollback"]["requested"])
        self.assertEqual(self.inbox(), ["rollback.request"])

    def test_error_mapping(self):
        run = self.serve()
        s, _, j = self.post(run, "update_check")  # no config
        self.assertEqual((s, j["code"]), (409, "not_configured"))
        self.assertIn("/etc/opos/update.json", j["error"])
        self.conf()
        s, _, j = self.post(run, "update_download")  # source answers 404
        self.assertEqual((s, j["code"]), (502, "fetch"))
        self.put(self.paths.config, "oops")
        s, _, j = self.post(run, "update_check")
        self.assertEqual((s, j["code"]), (409, "bad_config"))
        self.assertNotIn("Traceback", json.dumps(j))

    def test_nothing_leaves_the_machine_without_a_source(self):
        self.state(1, previous=["0-aaaaaaaaaaaa"])
        run = self.serve()
        real, real_gai = socket.socket.connect, socket.getaddrinfo

        def connect(sock, addr):
            if isinstance(addr, tuple) and addr[0] == "127.0.0.1" and addr[1] == run.port:
                return real(sock, addr)
            raise AssertionError(f"outbound connection {addr!r}")

        def gai(host, *a, **k):
            if host in ("127.0.0.1", "localhost"):
                return real_gai(host, *a, **k)
            raise AssertionError(f"name lookup {host!r}")
        with mock.patch.object(socket.socket, "connect", connect), mock.patch.object(socket, "getaddrinfo", gai):
            self.assertEqual(webclient.request(run.port, "GET", "/update")[0], 200)
            self.assertEqual(self.get_json(run)[0], 200)
            for action in ("update_check", "update_download"):
                s, _, j = self.post(run, action)
                self.assertEqual((s, j["code"]), (409, "not_configured"))
            self.assertEqual(self.post(run, "update_rollback")[0], 200)

    def test_without_the_update_folder_the_routes_say_not_available(self):
        shutil.rmtree(self.paths.root)
        run = self.serve()
        s, _, b = webclient.request(run.port, "GET", "/update")
        self.assertEqual(s, 200)
        self.assertIn("Updates are not available on this install.", b.decode())
        self.assertFalse(json.loads(self.get_json(run)[2])["available"])
        for action in ("update_check", "update_download", "update_rollback"):
            s, _, j = self.post(run, action)
            self.assertEqual((s, j["code"], j["error"]), (404, "not_available", "Updates are not available on this install."))


class MainPageIsUntouched(Env):
    """The main page is at its size budget and frozen: only the one link to /update (Settings, About) may differ from the base."""

    # page(token "t"*43, nonce "n"*22) with the build id fixed to 12 f's. At base e471cf4 (before the update client) these were
    # 9cf38030... and 29878bcd...; on ota/final (df2a4e0) 6b3d7b7c... and 02bfa2ed.... Re-recorded when ota/final was merged into
    # duo-acceptance (which had removed dead CSS and shortened the same phone-b.js comment): against duo-acceptance 702c4d3 the
    # page differs by exactly two things, the one CSS rule of the link and the Settings link to /update (checked by diffing the two).
    MAIN_SHA256 = "fde0645de6f182beb46bc50f4304f3da0ce67a7d434e922b5b942579e9aabf1e"
    DEMO_SHA256 = "896327bddc61c221dccbe27ad5fc5630535219fbb56ff3a857e5411906528438"
    LINK = '<a id=upd href=/update>Updates</a>'

    def test_recorded_digests(self):
        page = webclient.web.page
        with mock.patch("opos.notices.BUILD", "f" * 12):
            main = page("t" * 43, "n" * 22).encode("utf-8")
            demo = page("t" * 43, "n" * 22, demo=True).encode("utf-8")
        self.assertEqual(hashlib.sha256(main).hexdigest(), self.MAIN_SHA256, "the main page changed; if that is intended, record the new digest")
        self.assertEqual(hashlib.sha256(demo).hexdigest(), self.DEMO_SHA256)

    def test_it_knows_nothing_about_updates_but_one_link(self):
        text = webclient.web.page("t" * 43, "n" * 22, demo=True)
        for needle in ("update_check", "update_download", "update_rollback", "api/update", "opos-update"):
            self.assertNotIn(needle, text)
        self.assertEqual(text.count("/update"), 1)
        self.assertEqual(text.count(self.LINK), 1)

    def test_the_link_is_in_settings_about_and_leads_back_from_the_page(self):
        text = webclient.web.page("t" * 43, "n" * 22)
        settings = text[text.index('<section id="settings"'):]
        settings = settings[:settings.index("</section></section>")]
        self.assertIn('<h3 id="set-about">About</h3>' + self.LINK + '<p id="about">', settings)
        self.assertRegex(text, r"#upd\{[^}]*display:block[^}]*line-height:48px[^}]*\}")  # a 48 px touch target, like the buttons
        # a plain full-page navigation: no script handles it, no attribute that would make it open elsewhere
        self.assertNotRegex(self.LINK, r"target=|onclick|download|rel=")
        snap = {"available": False, "message": "x"}
        self.assertIn('<a id="u-back" href="/">Back to the phone</a>', update_page.page("t" * 43, "", snap))

    def test_the_link_fits_the_page_budget_with_the_served_token_sizes(self):
        with mock.patch("opos.notices.BUILD", "f" * 12):
            size = len(webclient.web.page("t" * 43, "n" * 22, demo=True).encode("utf-8"))
        self.assertLessEqual(size, 100 * 1024)

    def test_the_public_demo_has_no_link_to_a_page_it_does_not_serve(self):
        from opos import public_demo
        for demo in (False, True):
            self.assertNotIn("/update", public_demo.landing("t" * 43, "n" * 22, demo=demo))

    def test_served_main_page_equals_page_whether_or_not_updates_exist(self):
        self.conf()
        self.state(0)
        for have_updates in (True, False):
            if not have_updates:
                shutil.rmtree(self.paths.root)
            with webclient.Running(updater=self.client()) as run:
                s, h, b = webclient.request(run.port, "GET", "/")
                self.assertEqual(s, 200)
                self.assertEqual(b, webclient.web.page(run.token, csp_nonce(h)).encode(), have_updates)

    def test_state_and_demo_actions_are_unchanged(self):
        run = webclient.Running(updater=self.client())
        with run:
            tok = run.token
            s, _, b = webclient.request(run.port, "GET", "/api/state", headers={"X-OPOS-Token": tok})
            self.assertEqual(s, 200)
            state = json.loads(b)
            self.assertNotIn("update", json.dumps(sorted(state)).lower())
            self.assertIn("battery", state)
            for action, fields in (("battery", {"level": 55}), ("charge", {"on": True}), ("sim", {"running": False}), ("visible", {"visible": True})):
                s, _, j = webclient.post_action(run.port, action, tok, **fields)
                self.assertEqual(s, 200, action)
                self.assertNotIn("error", j)
            s, _, j = webclient.post_action(run.port, "receive", tok, number="+15550100", text="hi")
            self.assertEqual(s, 200)

    def test_existing_endpoints_and_404s_are_unchanged(self):
        run = webclient.Running(updater=self.client())
        with run:
            self.assertEqual(webclient.request(run.port, "GET", "/favicon.ico")[0], 200)
            for path in ("/updates", "/update/", "/update.json", "/api/updates", "/static/x"):
                self.assertEqual(webclient.request(run.port, "GET", path, headers={"X-OPOS-Token": run.token})[0], 404, path)

    def test_handler_without_an_updater_serves_no_update_routes(self):
        server = webclient.web.make_server(0, webclient.web.Phone())
        server.RequestHandlerClass.updater = None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port, token = server.server_address[1], server.RequestHandlerClass.token
        self.assertEqual(webclient.request(port, "GET", "/update")[0], 404)
        self.assertEqual(webclient.request(port, "GET", "/api/update", headers={"X-OPOS-Token": token})[0], 404)
        self.assertEqual(webclient.post_action(port, "update_check", token)[0], 400)

    def test_default_server_construction_does_no_io_and_starts_no_thread(self):
        phone = webclient.web.Phone()
        before = threading.active_count()
        for p in no_network():
            p.start()
            self.addCleanup(p.stop)
        server = webclient.web.make_server(0, phone)
        server.server_close()
        self.assertEqual(threading.active_count(), before)
        self.assertIsInstance(server.RequestHandlerClass.updater, uc.UpdateClient)
        self.assertEqual(server.RequestHandlerClass.updater.paths, uc.Paths())  # production defaults


class MainStartsAutoCheck(unittest.TestCase):
    def test_main_asks_the_updater_to_start_its_optional_check_once(self):
        srv = mock.Mock()
        with mock.patch.object(webclient.web, "make_server", return_value=srv) as make:
            webclient.web.main(["--port", "0"])
        make.assert_called_once()
        self.assertNotIn("updater", make.call_args.kwargs)  # the server builds its own, so main's call is unchanged
        srv.RequestHandlerClass.updater.start_auto_check.assert_called_once_with()
        srv.serve_forever.assert_called_once_with()

    def test_main_prints_the_port_the_kernel_chose(self):
        """The update smoke test starts the new release's server with --port 0 and reads its port from this line."""
        made, out = [], io.StringIO()
        real = webclient.web.make_server

        def spy(*a, **k):
            srv = real(*a, **k)
            srv.serve_forever = lambda: None
            made.append(srv)
            return srv
        with mock.patch.object(webclient.web, "make_server", spy), mock.patch("sys.stdout", out):
            webclient.web.main(["--port", "0"])
        self.addCleanup(made[0].server_close)
        port = made[0].server_address[1]
        self.assertGreater(port, 0)
        self.assertIn("http://127.0.0.1:%d/" % port, out.getvalue())
        self.assertNotIn("127.0.0.1:0/", out.getvalue())

    def test_make_server_attaches_the_given_updater(self):
        mine = uc.UpdateClient(uc.Paths(root="/nonexistent/opos-update"))
        server = webclient.web.make_server(0, webclient.web.Phone(), updater=mine)
        self.addCleanup(server.server_close)
        self.assertIs(server.RequestHandlerClass.updater, mine)


class CancelStagedWithTheRealUpdater(unittest.TestCase):
    """The client's cancel request, read by the real updater (tests/test_ota_roundtrip.py's fake phone). The two halves agree on a
    format that lives in docs/ota-update.md, so this is the check that they do. It needs an updater that knows the `cancel-staged`
    form (the updater review fixes, finding 11) and is skipped, saying so, while the updater in this tree does not."""

    def test_a_staged_boot_update_is_cancelled_with_no_earlier_release(self):
        updater = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging", "duo-os", "updater", "opos-updater")
        with open(updater, encoding="utf-8") as f:
            if "cancel-staged" not in f.read():
                self.skipTest("the updater in this tree has no cancel-staged request yet")
        import importlib
        rt = importlib.import_module("tests.test_ota_roundtrip")  # skips itself away from Linux
        if not rt.KEYGEN:
            self.skipTest("ssh-keygen is not installed")
        rt.setUpModule()
        self.addCleanup(rt.tearDownModule)
        w = rt.World()
        self.addCleanup(w.close)
        w.release(1, scope="boot")
        self.assertEqual(w.fetch_and_apply(1), 0, w.logs)
        self.assertEqual(w.state()["pending_boot"]["serial"], 1)
        self.assertEqual(w.state()["previous"], [], "the first update: there is nothing to go back to")
        c = w.client
        self.assertEqual(c.snapshot()["rollback"], {"possible": True, "requested": False, "staged": 1})
        self.assertEqual(c.request_rollback(), {"requested": True, "already": False, "cancel": True})
        self.assertEqual(w.run_updater("apply-inbox"), 0, w.logs)
        self.assertEqual((w.status()["phase"], w.state()["pending_boot"]), ("rolled-back", None))
        self.assertIn("cancelled", w.status()["message"])
        self.assertEqual(os.listdir(w.inbox), [], "the request was taken and nothing was left behind")
        w.boot = "boot-2"
        self.assertEqual(w.run_updater("apply-pending-boot"), 0, w.logs)
        w.assert_on(self, 0)  # the cancelled update did not apply at the next boot
        self.assertEqual(c.snapshot()["rollback"], {"possible": False, "requested": False})
        # With an earlier release too, a cancel leaves what is installed alone (the empty request would go back as well).
        w.release(2)
        self.assertEqual(w.fetch_and_apply(2), 0, w.logs)
        w.release(3, scope="boot")
        self.assertEqual(w.fetch_and_apply(3), 0, w.logs)
        self.assertEqual(c.snapshot()["rollback"], {"possible": True, "requested": False, "staged": 3})
        self.assertEqual(c.request_rollback()["cancel"], True)
        self.assertEqual(w.run_updater("apply-inbox"), 0, w.logs)
        self.assertIn("cancelled", w.status()["message"])
        w.assert_on(self, 2, previous=[0], failed=[])
        self.assertEqual(w.client.snapshot()["rollback"], {"possible": True, "requested": False})


class Defaults(unittest.TestCase):
    def test_production_paths(self):
        p = uc.Paths()
        self.assertEqual((p.config, p.root, p.revision), ("/etc/opos/update.json", "/var/lib/opos-update", "/opt/opos/REVISION"))
        self.assertEqual((p.inbox, p.state, p.status), ("/var/lib/opos-update/inbox", "/var/lib/opos-update/state.json", "/var/lib/opos-update/status.json"))

    def test_the_spec_says_the_cancel_gate_is_a_proxy_and_why_that_is_safe(self):
        """The client offers the cancel because state.json has a `floor`: an inference about the updater, not a handshake (a review
        note). The spec has to say so next to the gate, with the reasons it is safe, or the next reader takes it for a guarantee."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "docs", "ota-update.md"), encoding="utf-8") as f:
            doc = f.read()
        start = doc.index('the button becomes "Cancel staged update"')
        bullet = doc[start:doc.index("\n\n", start)]  # the UI bullet of the Client section
        for needle in ("proxy", "not a handshake", "`floor`", "`cancel-staged`", 'wrong "no"', 'wrong "yes"', "`stepped_back`", "CancelStagedWithTheRealUpdater"):
            self.assertIn(needle, bullet)

    def test_error_text_never_carries_a_path_or_a_traceback(self):
        for e in (uc.UpdateError("x", "m"), uc._net_error(OSError("[Errno 111] Connection refused: /var/lib/opos-update")),
                  uc._net_error(ssl.SSLCertVerificationError("bad")), uc._net_error(TimeoutError("t")), uc._net_error(http.client.BadStatusLine("x"))):
            self.assertNotIn("/var/lib", e.message)
            self.assertNotIn("Errno", e.message)


if __name__ == "__main__":
    unittest.main()
