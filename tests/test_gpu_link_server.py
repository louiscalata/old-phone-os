# SPDX-License-Identifier: GPL-3.0-or-later
"""Real loopback tests of bounded GPU Link HTTP transport and lifecycle."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, call, patch

from opos import gpu_link_proto as proto
from opos import gpu_link_server as server
from opos.ai_answers import AnswerStore
from opos.gpu_link import LinkService
from opos.store import Store
from tests.gpu_link_fake_pc import Clock, FakePC, RandomBytes


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state")
        self.answers = AnswerStore(self.store)
        self.model = self.root / "model.gguf"
        self.model.write_bytes(b"model\x00\xff" * 128)
        self.sha = hashlib.sha256(self.model.read_bytes()).hexdigest()
        self.clock = Clock()
        self.link = LinkService(self.answers, self.store, self.model, clock=self.clock, rand=RandomBytes())
        self.addCleanup(self.link.close)
        if self.link._hasher:
            self.link._hasher.join(2)
            self.assertFalse(self.link._hasher.is_alive())
        self.listener = server.Listener(self.link, port=0)
        self.addCleanup(self.listener.close)
        self.listener.run()
        self.address = self.listener.server_address
        self.pc = FakePC(address=self.address)
        self.pc.hello()

    def connect(self):
        sock = socket.create_connection(self.address, timeout=1.5)
        self.addCleanup(sock.close)
        return sock

    @staticmethod
    def receive(sock):
        result = bytearray()
        while True:
            try:
                chunk = sock.recv(65536)
            except ConnectionResetError:
                break
            if not chunk:
                break
            result.extend(chunk)
        return bytes(result)

    def raw(self, request):
        with self.connect() as sock:
            sock.sendall(request)
            return self.receive(sock)

    def assert_status(self, raw, status):
        self.assertTrue(raw.startswith(f"HTTP/1.1 {status} ".encode()), raw[:100])
        self.assertIn(b"Connection: close\r\n", raw)

    def enrolled(self):
        self.assertEqual(self.pc.enrol().status, 202)
        self.assertTrue(self.link.allow(self.pc.code))
        self.assertEqual(self.pc.hello().status, 200)

    def wait_connections(self, count):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with self.listener._connections_lock:
                if len(self.listener._connections) == count:
                    return
            threading.Event().wait(0.005)
        self.fail(f"connection count never became {count}")

    @staticmethod
    def mock_sockets(count):
        return [Mock(spec=socket.socket, name=f"sock{index}") for index in range(count)]

    def newest_handler(self, known):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            for thread in list(self.listener._handlers):
                if thread not in known and thread.is_alive():
                    known.add(thread)
                    return thread
            threading.Event().wait(0.002)
        self.fail("no handler thread appeared")

    def test_evicted_reader_threads_exit_at_once(self):
        # An evicted reader must not keep its thread until the 5 s or 10 s read deadline.
        known = set(self.listener._handlers)
        held = []
        for count in range(1, 5):
            sock = self.connect()
            self.wait_connections(count)
            held.append((sock, self.newest_handler(known)))
        evicted = []
        for _ in range(60):
            sock = self.connect()
            held.append((sock, self.newest_handler(known)))
            old, thread = held.pop(0)
            self.assertEqual(old.recv(1), b"")
            old.close()
            evicted.append(thread)
        deadline = time.monotonic() + 1
        for thread in evicted:
            thread.join(max(0, deadline - time.monotonic()))
        self.assertEqual(sum(thread.is_alive() for thread in evicted), 0)
        self.assertLessEqual(len([t for t in self.listener._handlers if t.is_alive()]), 4)
        started = time.monotonic()
        self.listener.close()
        self.assertLess(time.monotonic() - started, 1)

    def test_request_evicted_after_its_last_read_is_not_handled(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.listener.answering

        def answering(request):
            entered.set()
            release.wait(3)
            return original(request)

        with (
            patch.object(self.listener, "answering", answering),
            patch.object(self.link, "handle", side_effect=AssertionError("handled an evicted request")) as handle,
        ):
            first = self.connect()
            first.sendall(b"GET /v1/hello HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertTrue(entered.wait(1))
            others = [self.connect() for _ in range(4)]  # the fourth of these evicts `first`
            self.assertEqual(first.recv(1), b"")
            release.set()
            for sock in others:
                sock.close()
            self.wait_connections(0)
            handle.assert_not_called()

    def test_handler_list_is_pruned_and_close_joins_handlers(self):
        for _ in range(12):
            self.assert_status(self.raw(b"GET /unknown HTTP/1.1\r\nHost: localhost\r\n\r\n"), 401)
            self.wait_connections(0)
        self.wait_connections(0)
        self.assertLessEqual(len(self.listener._handlers), 2)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.link.handle

        def handle(*args):
            entered.set()
            release.wait(3)
            return original(*args)

        with patch.object(self.link, "handle", handle):
            sock = self.connect()
            sock.sendall(b"GET /v1/hello HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertTrue(entered.wait(1))
            deadline = time.monotonic() + 1  # the accept thread records the handler after starting it
            handlers = []
            while len(handlers) != 1 and time.monotonic() < deadline:
                threading.Event().wait(0.002)
                handlers = [thread for thread in list(self.listener._handlers) if thread.is_alive()]
            self.assertEqual(len(handlers), 1)
            joining = threading.Event()
            real_join = handlers[0].join

            def join(*args, **kwargs):
                joining.set()
                return real_join(*args, **kwargs)

            with patch.object(handlers[0], "join", side_effect=join) as joined:
                closer = threading.Thread(target=self.listener.close)
                closer.start()
                try:
                    self.assertTrue(joining.wait(2))
                finally:
                    release.set()
                    closer.join(3)
                self.assertFalse(closer.is_alive())
                joined.assert_called_once_with()
            self.assertFalse(handlers[0].is_alive())
            self.assertFalse(self.listener._handlers)

    def test_run_after_close_starts_nothing(self):
        self.listener.close()
        with patch.object(threading.Thread, "start", side_effect=AssertionError("started after close")) as start:
            self.listener.run()
            start.assert_not_called()
        self.assertFalse(self.listener._workers)

    def fill(self, connected, reading):
        self.wait_connections(0)
        with self.listener._connections_lock:
            self.listener._connections.update(connected)
            self.listener._reading.update(dict.fromkeys(reading))

    def test_eviction_shuts_down_and_forgets_the_oldest_reader_only(self):
        # process_request: the evicted reader is the oldest one that is still reading, is shut down
        # in both directions so its handler wakes, and leaves both tables before the new one joins.
        busy, *readers = self.mock_sockets(server.MAX_CONNECTIONS)
        new = Mock(spec=socket.socket, name="new")
        self.fill([busy, *readers], readers)
        with patch.object(self.listener, "process_request_thread"):
            self.listener.process_request(new, ("127.0.0.1", 1))
            for thread in list(self.listener._handlers):
                thread.join(2)
        oldest = readers[0]
        oldest.close.assert_not_called()
        self.assertEqual(oldest.shutdown.call_args_list[0], call(socket.SHUT_RDWR))
        for other in (busy, *readers[1:]):
            other.shutdown.assert_not_called()
            other.close.assert_not_called()
        with self.listener._connections_lock:
            self.assertEqual(self.listener._connections, {busy, *readers[1:], new})
            self.assertEqual(list(self.listener._reading), [*readers[1:], new])

    def test_refused_connection_is_closed_by_the_listener_not_by_garbage_collection(self):
        # process_request with nobody reading: the new socket is closed explicitly and not admitted.
        held = self.mock_sockets(server.MAX_CONNECTIONS)
        new = Mock(spec=socket.socket, name="new")
        self.fill(held, [])
        with patch.object(self.listener, "process_request_thread") as handler:
            self.listener.process_request(new, ("127.0.0.1", 1))
            handler.assert_not_called()
        new.close.assert_called_once_with()
        with self.listener._connections_lock:
            self.assertEqual(self.listener._connections, set(held))
            self.assertFalse(self.listener._reading)

    def test_failed_thread_start_forgets_and_closes_the_request(self):
        new = Mock(spec=socket.socket, name="new")
        self.wait_connections(0)
        handlers = list(self.listener._handlers)
        with patch.object(server.threading.Thread, "start", side_effect=RuntimeError("no threads")):
            self.listener.process_request(new, ("127.0.0.1", 1))
        new.close.assert_called_once_with()
        with self.listener._connections_lock:
            self.assertNotIn(new, self.listener._connections)
            self.assertNotIn(new, self.listener._reading)
        self.assertEqual(self.listener._handlers, handlers)

    def test_answering_protects_an_admitted_request_and_refuses_an_evicted_one(self):
        admitted, evicted = self.mock_sockets(2)
        self.fill([admitted], [admitted])
        self.assertTrue(self.listener.answering(admitted))
        with self.listener._connections_lock:
            self.assertIn(admitted, self.listener._connections)
            self.assertNotIn(admitted, self.listener._reading)
        self.assertFalse(self.listener.answering(evicted))
        with self.listener._connections_lock:
            self.assertNotIn(evicted, self.listener._connections)

    def test_failed_run_closes_the_listener_and_its_started_thread(self):
        listener = server.Listener(self.link, port=0)
        self.addCleanup(listener.close)
        real = threading.Thread.start
        starts = []

        def start(thread):
            starts.append(thread)
            if len(starts) == 2:
                raise RuntimeError("no threads")
            real(thread)

        with patch.object(server.threading.Thread, "start", start):
            with self.assertRaises(RuntimeError):
                listener.run()
        self.assertEqual(listener.socket.fileno(), -1)
        self.assertFalse(starts[0].is_alive())
        self.assertTrue(listener._halt.is_set())

    def test_bare_lf_line_is_refused_at_once(self):
        # _line(): a lone LF at the start of the buffered input (here the header terminator) is a
        # malformed line, refused with 400 without waiting for more input.
        started = time.monotonic()
        raw = self.raw(b"GET /v1/hello HTTP/1.1\r\nHost: localhost\r\n\n")
        self.assert_status(raw, 400)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_dribbled_body_does_not_restart_the_deadline(self):
        # _read(): every body recv is bounded by the one request deadline, so a peer that trickles
        # body bytes is cut off at BODY_SECONDS, not kept alive by each new byte.
        with patch.object(server, "BODY_SECONDS", 0.3):
            sock = self.connect()
            start = time.monotonic()
            sock.sendall(
                b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{"
            )
            closed = False
            for _ in range(14):
                if select.select([sock], [], [], 0.05)[0]:
                    closed = True
                    break
                try:
                    sock.sendall(b" ")
                except OSError:
                    closed = True
                    break
            elapsed = time.monotonic() - start
        self.assertTrue(closed)
        self.assertLess(elapsed, 0.6)

    def test_request_that_is_complete_only_after_its_deadline_is_refused(self):
        # _read(): a request already buffered in full is still checked against the request deadline.
        handler = server._Handler.__new__(server._Handler)
        handler.server = Mock()
        handler._buffer = bytearray()
        handler._request_deadline = 50.0
        state = {"late": False, "sent": False}

        class Peer:
            def settimeout(self, value):
                pass

            def recv(self, size):
                if state["sent"]:
                    return b""
                state["sent"] = True
                return b"GET /v1/hello HTTP/1.1\r\nHost: localhost\r\n\r\n"[:size]

        def size(*args):
            state["late"] = True
            return 0

        handler.request = Peer()
        clock = types.SimpleNamespace(monotonic=lambda: 100.0 if state["late"] else 0.0)
        with patch.object(server, "time", clock), patch.object(server, "_size", size):
            with self.assertRaises(TimeoutError):
                handler._read()

    def test_request_budget_covers_the_header_phase(self):
        # handle(): the whole request has BODY_SECONDS; the header phase takes the smaller of that and HEADER_SECONDS.
        with patch.object(server, "BODY_SECONDS", 0.3), patch.object(server, "HEADER_SECONDS", 5.0):
            sock = self.connect()
            start = time.monotonic()
            sock.sendall(b"GET /v1/hello HTTP/1.1\r\nHost: local")
            self.assertEqual(self.receive(sock), b"")
            self.assertLess(time.monotonic() - start, 1.2)

    def test_request_deadline_is_body_seconds_from_the_start_of_handling(self):
        handler = server._Handler.__new__(server._Handler)
        handler.request, handler.server = Mock(), Mock()
        before = time.monotonic()
        with patch.object(server._Handler, "_read", autospec=True, side_effect=ConnectionError):
            handler.handle()
        after = time.monotonic()
        self.assertGreaterEqual(handler._request_deadline, before + server.BODY_SECONDS)
        self.assertLessEqual(handler._request_deadline, after + server.BODY_SECONDS)

    def test_drain_reads_exactly_the_remaining_allowance(self):
        # _drain: at most 65536 bytes in all, counting what was already buffered; never one recv more.
        handler = server._Handler.__new__(server._Handler)
        sizes = []

        class Peer:
            def shutdown(self, how):
                pass

            def settimeout(self, value):
                pass

            def recv(self, size):
                sizes.append(size)
                return b"x" * size

        handler.request = Peer()
        handler._buffer = bytearray(b"y" * 1000)
        handler._request_deadline = time.monotonic() + 5
        handler._drain()
        self.assertEqual(sum(sizes), 65536 - 1000)
        self.assertLessEqual(max(sizes), 4096)
        self.assertEqual(sizes[-1], (65536 - 1000) % 4096)

    def test_close_works_after_the_hash_thread_could_not_start(self):
        model = self.root / "late.gguf"
        link = LinkService(self.answers, self.store, model, clock=self.clock)
        self.addCleanup(link.close)
        model.write_bytes(b"model bytes")
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("no threads")):
            with self.assertRaises(RuntimeError):
                link.tick()
        self.assertIsNone(link._hasher)
        listener = server.Listener(link, port=0)
        self.addCleanup(listener.close)
        listener.run()
        workers = list(listener._workers)
        listener.close()
        self.assertEqual(listener.socket.fileno(), -1)
        self.assertTrue(all(not worker.is_alive() for worker in workers))

    def test_close_cleans_up_even_when_link_close_raises(self):
        sock = self.connect()
        self.wait_connections(1)
        workers = list(self.listener._workers)
        handlers = list(self.listener._handlers)
        with patch.object(self.link, "close", side_effect=RuntimeError("close refused")):
            with self.assertRaisesRegex(RuntimeError, "close refused"):
                self.listener.close()
        self.assertEqual(self.listener.socket.fileno(), -1)
        self.assertTrue(all(not thread.is_alive() for thread in workers + handlers))
        self.assertFalse(self.listener._workers)
        self.assertFalse(self.listener._handlers)
        self.assertEqual(self.receive(sock), b"")

    def test_close_skips_an_unstarted_hash_thread(self):
        self.link._hasher = threading.Thread(target=lambda: None)
        self.link.close()
        self.assertIsNone(self.link._hasher.ident)

    def test_slow_headers_absolute_deadline(self):
        with patch.object(server, "HEADER_SECONDS", 0.3):
            sock = self.connect()
            start = time.monotonic()
            sock.sendall(b"GET /v1/hello HTTP/1.1\r\nHost: local")
            self.assertEqual(self.receive(sock), b"")
            self.assertLess(time.monotonic() - start, 1.5)
            self.assertGreaterEqual(time.monotonic() - start, 0.2)

    def test_dribbling_headers_do_not_restart_deadline(self):
        with patch.object(server, "HEADER_SECONDS", 0.3):
            sock = self.connect()
            sock.sendall(b"GET /v1/hello HTTP/1.1\r\nHost: ")
            start = time.monotonic()
            for _ in range(8):
                threading.Event().wait(0.05)
                try:
                    sock.sendall(b"x")
                except OSError:
                    break
            self.assertEqual(self.receive(sock), b"")
            self.assertLess(time.monotonic() - start, 1.5)

    def test_slow_body_absolute_deadline(self):
        with patch.object(server, "BODY_SECONDS", 0.3):
            sock = self.connect()
            start = time.monotonic()
            sock.sendall(
                b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{"
            )
            self.assertEqual(self.receive(sock), b"")
            self.assertLess(time.monotonic() - start, 1.5)
            self.assertGreaterEqual(time.monotonic() - start, 0.2)

    def test_fifth_connection_evicts_oldest_reader_and_serves_signed_status(self):
        self.enrolled()
        for prefix in (
            b"",
            b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\nContent-Length: 1024\r\n\r\n",
        ):
            with self.subTest(prefix=prefix):
                sockets = []
                for count in range(1, 5):
                    sock = self.connect()
                    sock.sendall(prefix)
                    sockets.append(sock)
                    self.wait_connections(count)
                started = time.monotonic()
                self.assertEqual(self.pc.status().status, 200)
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(sockets[0].recv(1), b"")
                for sock in sockets:
                    sock.close()
                self.wait_connections(0)

    def test_fifth_connection_refused_when_all_four_are_answering(self):
        entered = threading.Condition()
        release = threading.Event()
        calls = []
        original = self.link.handle

        def handle(*args):
            with entered:
                calls.append(args)
                entered.notify_all()
            release.wait(3)
            return original(*args)

        with patch.object(self.link, "handle", handle):
            try:
                sockets = [self.connect() for _ in range(4)]
                for sock in sockets:
                    sock.sendall(b"GET /v1/hello HTTP/1.1\r\nHost: localhost\r\n\r\n")
                with entered:
                    self.assertTrue(entered.wait_for(lambda: len(calls) == 4, timeout=1))
                fifth = self.connect()
                self.assertEqual(fifth.recv(1), b"")
            finally:
                release.set()
            for sock in sockets:
                self.assert_status(self.receive(sock), 200)
        self.wait_connections(0)

    def test_surv_run_twice_starts_threads_once(self):
        workers = list(self.listener._workers)
        before = threading.active_count()
        with patch.object(threading.Thread, "start", side_effect=AssertionError("started twice")) as start:
            self.listener.run()
            start.assert_not_called()
        self.assertEqual(self.listener._workers, workers)
        self.assertEqual(threading.active_count(), before)

    def test_signed_status_evicts_oldest_refusal_still_draining(self):
        self.enrolled()
        sockets = []
        for count in range(1, 5):
            sock = self.connect()
            sock.sendall(b"POST /v1/status HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2049\r\n\r\n")
            # The write side closes before the bounded drain. Keep our write
            # side open so each socket remains in the listener's reading phase.
            self.assert_status(self.receive(sock), 401)
            sockets.append(sock)
            self.wait_connections(count)
        self.assertEqual(self.pc.status().status, 200)
        with self.listener._connections_lock:
            self.assertEqual(len(self.listener._reading), 3)
        for sock in sockets:
            sock.close()
        self.wait_connections(0)

    def test_missing_content_length(self):
        raw = self.raw(b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n\r\n")
        self.assert_status(raw, 411)

    def test_transfer_encoding_is_refused_without_reading_body(self):
        for value in (b"chunked", b"identity"):
            with self.subTest(value=value):
                raw = self.raw(
                    b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: "
                    + value
                    + b"\r\nContent-Length: 10\r\n\r\n"
                )
                self.assert_status(raw, 400)

    def test_body_limit_before_read_hash_or_parse(self):
        with (
            patch.object(proto, "request_mac", side_effect=AssertionError("hashed")),
            patch.object(proto, "parse_enrol", side_effect=AssertionError("parsed")),
        ):
            raw = self.raw(
                b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: 1025\r\n\r\n"
            )
        self.assert_status(raw, 413)
        self.assertIsNone(self.link.snapshot()["pending"])

    def test_get_body_and_unknown_route_body_have_zero_limit(self):
        for line in (b"GET /v1/hello", b"POST /unknown"):
            raw = self.raw(line + b" HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\n\r\n")
            self.assert_status(raw, 413 if b"hello" in line else 401)

    def test_signed_framing_errors_are_empty_401_without_body_read(self):
        targets = (
            ("POST", "/v1/status", 2049),
            ("GET", "/v1/work?wait=0", 1),
            ("POST", "/v1/runs/" + "ab" * 16 + "/text", 8193),
            ("GET", f"/v1/models/{self.sha}", 1),
        )
        original = socket.socket.recv
        reads = []

        def recv(sock, size, *args):
            if sock.getsockname() == self.address:
                reads.append(size)
            return original(sock, size, *args)

        with patch.object(socket.socket, "recv", recv), patch.object(self.link, "handle") as handle:
            for method, target, size in targets:
                framing = [f"Content-Length: {size}\r\n", "Transfer-Encoding: chunked\r\n", "Content-Length: -1\r\n"]
                if method == "POST":
                    framing.append("")
                if "/models/" not in target:
                    framing.append("Range: bytes=0-1\r\nContent-Length: 100\r\n")
                for fields in framing:
                    with self.subTest(target=target, framing=fields):
                        reads.clear()
                        request = f"{method} {target} HTTP/1.1\r\nHost: localhost\r\n{fields}\r\n".encode()
                        # Send no body. The refusal precedes the bounded drain.
                        raw = self.raw(request)
                        self.assert_status(raw, 401)
                        self.assertEqual(raw.split(b"\r\n\r\n", 1)[1], b"")
                        self.assertIn(b"Content-Length: 0\r\n", raw)
                        self.assertNotIn(b"X-Link-Mac:", raw)
                        self.assertTrue(reads)
                        self.assertTrue(all(size > 1 for size in reads))
                        self.assertLess(len(reads), 5)
            handle.assert_not_called()

    def test_refusals_with_unread_body_end_without_reset(self):
        cases = (
            (400, b"POST /v1/enrol HTTP/1.0\r\nHost: localhost\r\nContent-Length: 6000\r\n\r\n"),
            (401, b"POST /v1/status HTTP/1.1\r\nHost: localhost\r\nContent-Length: 6000\r\n\r\n"),
            (411, b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\n\r\n"),
            (413, b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\nContent-Length: 6000\r\n\r\n"),
        )
        for status, head in cases:
            with self.subTest(status=status):
                sock = self.connect()
                sock.sendall(head + b"x" * 6000)
                # Do not half-close: a client may wait for the reply first.
                raw = bytearray()
                while chunk := sock.recv(4096):
                    raw.extend(chunk)
                self.assert_status(bytes(raw), status)
                body = b"" if status == 401 else proto.error_reply("too_long" if status == 413 else "request")
                self.assertEqual(bytes(raw).split(b"\r\n\r\n", 1)[1], body)
                sock.close()
        self.wait_connections(0)

    def test_buffered_headers_preserve_body_and_discard_pipeline(self):
        self.enrolled()
        body = proto.dumps(
            dict(state="idle", lease=None, copied=None, total=None, gpu=None, tokens=None, tps=96.123, error=None)
        )
        original = socket.socket.recv
        for fragment in (3, 4096):
            method, target, headers, _ = self.pc.prepare("POST", "/v1/status", body)
            head = f"{method} {target} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n"
            head += "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"

            def recv(sock, size, *args, fragment=fragment):
                if sock.getsockname() == self.address:
                    size = min(size, fragment)
                return original(sock, size, *args)

            with self.subTest(fragment=fragment), patch.object(socket.socket, "recv", recv):
                raw = self.raw(head.encode() + body + b"GET /ignored HTTP/1.1\r\n\r\n")
            self.assert_status(raw, 200)
            self.assertEqual(raw.count(b"HTTP/1.1"), 1)
            self.assertEqual(raw.split(b"\r\n\r\n", 1)[1], proto.status_reply(None))

    def test_handler_thread_start_failure_cleans_up_and_listener_continues(self):
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("handler start failed")):
            sock = self.connect()
            self.assertEqual(sock.recv(1), b"")
        self.wait_connections(0)
        self.assertEqual(self.pc.hello().status, 200)
        self.listener.close()
        self.listener.close()

    def test_bad_content_lengths_and_duplicates(self):
        for value in (b"-1", b"+1", b"01", b"1.0", b"99999999999999", b"0\r\nContent-Length: 0"):
            with self.subTest(value=value):
                raw = self.raw(b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\nContent-Length: " + value + b"\r\n\r\n")
                self.assert_status(raw, 400)

    def test_host_must_be_loopback_but_port_may_differ(self):
        for host in (b"localhost", b"127.0.0.1", b"localhost:42", b"127.0.0.1:65535"):
            self.assert_status(self.raw(b"GET /v1/hello HTTP/1.1\r\nHost: " + host + b"\r\n\r\n"), 200)
        for host in (
            b"example.test",
            b"localhost.example.test",
            b"[::1]",
            b"127.1",
            b"localhost:65536",
            b"localhost:",
            b"user@localhost",
        ):
            with self.subTest(host=host):
                self.assert_status(self.raw(b"GET /v1/hello HTTP/1.1\r\nHost: " + host + b"\r\n\r\n"), 400)

    def test_enrol_requires_exact_json_content_type(self):
        for content in (b"", b"Content-Type: text/plain\r\n", b"Content-Type: application/json; charset=utf-8\r\n"):
            raw = self.raw(b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\n" + content + b"Content-Length: 2\r\n\r\n{}")
            self.assert_status(raw, 400)

    def test_hello_flood_and_refill(self):
        for _ in range(9):
            self.assertEqual(self.pc.hello().status, 200)
        self.assertEqual(self.pc.hello().status, 429)
        self.clock.advance(0.11)
        self.assertEqual(self.pc.hello().status, 200)
        self.assertEqual(self.pc.hello().status, 429)

    def test_enrol_flood_and_refill(self):
        self.assertEqual(self.pc.enrol().status, 202)
        self.assertEqual(self.pc.enrol().status, 202)
        self.assertEqual(self.pc.enrol().status, 429)
        self.clock.advance(0.5)
        self.assertEqual(self.pc.enrol().status, 202)
        self.assertEqual(self.pc.enrol().status, 429)

    def test_every_reply_closes_connection_no_pipeline(self):
        request = b"GET /v1/hello HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
        raw = self.raw(request + request)
        self.assert_status(raw, 200)
        self.assertEqual(raw.count(b"HTTP/1.1"), 1)

    def test_request_line_maximum_read_before_auth(self):
        with patch.object(self.link, "handle", side_effect=AssertionError("dispatched")):
            raw = self.raw(b"GET /" + b"x" * 1995)
        self.assert_status(raw, 400)

    def test_request_line_exact_bound(self):
        prefix = b"GET /"
        suffix = b" HTTP/1.1\r\n"
        line = prefix + b"x" * (2000 - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(line), 2000)
        self.assert_status(self.raw(line + b"Host: localhost\r\n\r\n"), 401)

    def test_header_line_count_bound_and_exact_limit(self):
        base = b"GET /v1/hello HTTP/1.1\r\nHost: localhost\r\n"
        fields = b"".join(f"X-Test-{index}: a\r\n".encode() for index in range(39))
        self.assert_status(self.raw(base + fields + b"\r\n"), 200)
        with patch.object(self.link, "handle", side_effect=AssertionError("dispatched")):
            self.assert_status(self.raw(base + fields + b"One-More: x\r\n\r\n"), 400)

    def test_header_block_maximum_read_and_exact_limit(self):
        base = b"GET /v1/hello HTTP/1.1\r\n"
        fixed = b"Host: localhost\r\nX-Pad: "
        headers = fixed + b"a" * (8000 - len(fixed) - 4) + b"\r\n\r\n"
        self.assertEqual(len(headers), 8000)
        self.assert_status(self.raw(base + headers), 200)
        with patch.object(self.link, "handle", side_effect=AssertionError("dispatched")):
            self.assert_status(self.raw(base + fixed + b"a" * 8000), 400)

    def test_header_folding_duplicates_and_controls_refused(self):
        for extra in (b"Host: localhost\r\n", b" X-Folded: yes\r\n", b"X-A: a\x00b\r\n", b"X-A: a\tb\r\n"):
            self.assert_status(self.raw(b"GET /v1/hello HTTP/1.1\r\nHost: localhost\r\n" + extra + b"\r\n"), 400)

    def test_signed_status_text_errors_and_model_responses(self):
        self.enrolled()
        self.assertEqual(self.pc.work().status, 204)
        self.assertEqual(self.pc.status().status, 200)
        self.assertEqual(self.pc.text("ff" * 16, 0, "x").status, 404)
        self.assertEqual(self.pc.model("ff" * 32).status, 404)
        self.assertEqual(self.pc.model(self.sha, "bytes=9-2").status, 416)
        whole = self.pc.model(self.sha)
        self.assertEqual(whole.status, 200)
        self.assertEqual(whole.body, self.model.read_bytes())
        ranged = self.pc.model(self.sha, "bytes=2-12")
        self.assertEqual(ranged.status, 206)
        self.assertEqual(ranged.body, self.model.read_bytes()[2:13])
        self.assertEqual(dict(ranged.headers)["Content-Range"], f"bytes 2-12/{self.model.stat().st_size}")
        answer = self.answers.ask("gpu", "question")
        work = self.pc.data(self.pc.work())
        self.assertEqual(self.pc.text(work["lease"], 1, "bad offset").status, 409)
        self.assertEqual(self.pc.text(work["lease"], 0, "x" * 6000).status, 200)
        self.assertEqual(self.pc.text(work["lease"], 6000, "x" * 6000).status, 200)
        self.clock.advance(1)
        self.assertEqual(self.pc.text(work["lease"], 12000, "x" * 289).status, 413)
        self.assertEqual(self.answers.get(answer["id"])["bytes"], 12000)

    def test_range_trailing_space_is_not_normalised_before_authentication(self):
        self.enrolled()
        request = self.pc.prepare("GET", f"/v1/models/{self.sha}", range_value="bytes=0-1")
        request[2]["Range"] += " "
        reply = self.pc.send(*request)
        self.assertEqual((reply.status, reply.body), (401, b""))
        self.assertNotIn("X-Link-Mac", dict(reply.headers))
        self.assertEqual(self.pc.model(self.sha, "bytes=0-1 ").status, 416)

    def test_end_to_end_link_drop_resume_and_done(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "What is a GPU?")
        first = self.pc.data(self.pc.work())
        self.assertEqual(self.pc.status("busy", first["lease"]).status, 200)
        prefix = "A GPU is a parallel "
        self.assertEqual(self.pc.data(self.pc.text(first["lease"], 0, prefix)), {"acked": len(prefix)})
        self.clock.advance(2.5)
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
        resumed = self.pc.data(self.pc.work())
        self.assertNotEqual(resumed["lease"], first["lease"])
        self.assertEqual(resumed["partial"], prefix)
        self.assertEqual(self.pc.status("busy", resumed["lease"], tokens=10, tps=2.5).status, 200)
        self.clock.advance(0.1)
        self.assertEqual(self.pc.status("busy", resumed["lease"], tokens=15, tps=3.5).status, 200)
        self.assertEqual(self.pc.text(first["lease"], len(prefix), "stale").status, 404)
        self.assertEqual(
            self.pc.text(resumed["lease"], len(resumed["partial"].encode()), "processor.", True, "stop").status, 200
        )
        final = AnswerStore(self.store).get(answer["id"])
        self.assertEqual((final["state"], final["text"]), ("done", prefix + "processor."))
        self.assertEqual(
            (final["tokens"], final["tps"], final["model"], final["where"]), (15, 3.5, "model", self.pc.label)
        )

    def test_statuses_before_work_body_written_do_not_pause(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        entered, release = threading.Event(), threading.Event()
        original = socket.socket.sendall

        def sendall(sock, data, *args):
            if sock.getsockname() == self.address and b'"question"' in data:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test work gate")
            return original(sock, data, *args)

        with patch.object(socket.socket, "sendall", sendall), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.pc.work)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.pc.status().status, 200)
                self.assertEqual(self.pc.status().status, 200)
                self.clock.advance(100)
                self.link.tick()
                self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
                self.assertIsNone(self.link._lease["sent"])
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).status, 200)
        self.wait_connections(0)
        self.assertEqual(self.link._lease["sent"], self.clock())
        self.pc.status()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.pc.status()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")

    def test_failed_work_body_write_ends_lease_and_pauses(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        original = socket.socket.sendall
        attempted = threading.Event()

        def sendall(sock, data, *args):
            if sock.getsockname() == self.address and b'"question"' in data:
                attempted.set()
                raise BrokenPipeError("test work write")
            return original(sock, data, *args)

        with patch.object(socket.socket, "sendall", sendall), self.assertRaises(http.client.IncompleteRead):
            self.pc.work()
        self.wait_connections(0)
        self.assertTrue(attempted.is_set())
        self.assertIsNone(self.link._lease)
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
        self.assertEqual(self.pc.work().status, 200)

    def test_model_changed_during_send_closes_short_and_checks_again(self):
        self.enrolled()
        # Exercise a change after headers, and after one complete data chunk.
        for after_headers in (True, False):
            with self.subTest(after_headers=after_headers):
                self.model.write_bytes(b"m" * (3 * 65536))
                self.link.tick()
                self.link._hasher.join(5)
                sha = hashlib.sha256(self.model.read_bytes()).hexdigest()
                original = socket.socket.sendall
                changed = threading.Event()

                def sendall(sock, data, *args, original=original, after_headers=after_headers, changed=changed):
                    result = original(sock, data, *args)
                    is_headers = data.startswith(b"HTTP/1.1 200 ")
                    if sock.getsockname() == self.address and is_headers == after_headers and not changed.is_set():
                        info = self.model.stat()
                        self.model.write_bytes(b"n" * info.st_size)
                        os.utime(self.model, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
                        changed.set()
                    return result

                # Hold the timer so checking remains observable until the next
                # explicit tick, rather than racing the background rehash.
                with patch.object(self.link, "tick"), patch.object(socket.socket, "sendall", sendall):
                    with self.assertRaises(http.client.IncompleteRead) as caught:
                        self.pc.model(sha)
                    self.wait_connections(0)
                    self.assertTrue(changed.is_set())
                    self.assertEqual(len(caught.exception.partial), 0 if after_headers else 65536)
                    self.assertEqual(self.link.snapshot()["model"]["state"], "checking")
                self.link.tick()
                self.link._hasher.join(5)
                self.assertEqual(self.pc.model(hashlib.sha256(self.model.read_bytes()).hexdigest()).status, 200)

    def test_model_path_replaced_during_transfer_sends_open_file(self):
        self._model_path_changed_during_transfer(symlink=False)

    def test_model_symlink_retargeted_during_transfer_sends_open_file(self):
        self._model_path_changed_during_transfer(symlink=True)

    def _model_path_changed_during_transfer(self, symlink):
        self.enrolled()
        content = b"m" * (3 * 65536)
        self.model.write_bytes(content)
        if symlink:
            original_path = self.root / "original.gguf"
            self.model.rename(original_path)
            self.model.symlink_to(original_path)
        self.link.tick()
        self.link._hasher.join(5)
        self.assertFalse(self.link._hasher.is_alive())
        sha = hashlib.sha256(content).hexdigest()
        replacement = self.root / "replacement.gguf"
        replacement.write_bytes(b"n" * len(content))
        info = self.model.stat()
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertNotEqual(replacement.stat().st_ino, info.st_ino)
        original = socket.socket.sendall
        changed = threading.Event()

        def sendall(sock, data, *args):
            result = original(sock, data, *args)
            if sock.getsockname() == self.address and data == content[:65536] and not changed.is_set():
                if symlink:
                    new_link = self.root / "new-link.gguf"
                    new_link.symlink_to(replacement)
                    new_link.replace(self.model)
                else:
                    replacement.replace(self.model)
                changed.set()
            return result

        with patch.object(self.link, "tick"):
            with patch.object(socket.socket, "sendall", sendall):
                reply = self.pc.model(sha)
            self.wait_connections(0)
            self.assertTrue(changed.is_set())
            self.assertEqual((reply.status, reply.body), (200, content))
            self.assertEqual(self.model.read_bytes(), b"n" * len(content))
            # Defer hashing so the next request's checking state is observable.
            with patch.object(self.link, "_hash_model") as hash_model:
                next_reply = self.pc.model(sha)
                self.link._hasher.join(5)
                hash_model.assert_called_once()
                self.assertEqual((next_reply.status, self.pc.data(next_reply)), (404, {"error": "model"}))
                self.assertEqual(self.link.snapshot()["model"]["state"], "checking")

    def test_unchanged_authenticated_model_requests_always_have_signed_replies(self):
        self.enrolled()
        for value, expected in ((None, 200), ("bytes=0-2", 206), ("bytes=9-2", 416)):
            with self.subTest(range=value):
                self.assertEqual(self.pc.model(self.sha, value).status, expected)
        with patch.object(self.link, "open_model", side_effect=OSError("test open failure")):
            reply = self.pc.model(self.sha)
        self.assertEqual((reply.status, self.pc.data(reply)), (500, {"error": "state"}))
        self.assertEqual(self.pc.model(self.sha).body, self.model.read_bytes())

    def test_model_changed_after_handle_closes_before_headers(self):
        self.enrolled()
        original = self.link.handle

        def handle(*args):
            reply = original(*args)
            if reply.file is not None:
                self.model.write_bytes(b"changed before transmission")
            return reply

        request = self.pc.prepare("GET", f"/v1/models/{self.sha}")
        headers = "".join(f"{name}: {value}\r\n" for name, value in request[2].items())
        raw = (f"GET {request[1]} HTTP/1.1\r\nHost: localhost\r\n{headers}\r\n").encode()
        with patch.object(self.link, "handle", side_effect=handle):
            self.assertEqual(self.raw(raw), b"")

    def test_timer_survives_tick_exception_and_detects_link_drop(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "timer recovery")
        self.assertEqual(self.pc.work().status, 200)
        original = self.link.tick
        recovered = threading.Event()
        calls = 0

        def tick():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("one bad tick")
            original()
            recovered.set()

        with patch.object(self.link, "tick", side_effect=tick):
            self.clock.advance(3)
            self.assertTrue(recovered.wait(2))
            self.assertGreaterEqual(calls, 2)
            self.assertTrue(self.listener._workers[1].is_alive())
        self.assertIsNone(self.link._lease)
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")

    def model_download(self, content, buffer=16384):
        self.model.write_bytes(content)
        self.link.tick()
        self.link._hasher.join(2)
        self.assertFalse(self.link._hasher.is_alive())
        target = f"/v1/models/{hashlib.sha256(content).hexdigest()}"
        method, _, headers, _ = self.pc.prepare("GET", target)
        sock = self.connect()
        if buffer:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer)
        raw = f"{method} {target} HTTP/1.1\r\nHost: localhost\r\n"
        raw += "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"
        sock.sendall(raw.encode("ascii"))
        return sock

    def test_model_slow_steady_reader_outlives_chunk_limit(self):
        # The write limit applies to each chunk, not to the whole body: a reader that keeps taking data, however
        # slowly, is not cut off. The slow reader is simulated on the sending side (a pause after every chunk) because
        # real back-pressure with small socket buffers depends on the kernel: on Linux loopback, where segments are
        # 64 KiB, a 16 KiB window stalls the sender on window probes, which is not what this test is about.
        self.enrolled()
        content = b"model bytes\x00\xff" * (2 * 1024 * 1024 // 13)
        original = server._Handler._write_reply
        finished = threading.Event()
        elapsed = []

        class SlowSocket:
            def __init__(self, sock):
                self._sock = sock

            def sendall(self, data, *args):
                self._sock.sendall(data, *args)
                time.sleep(0.06)

            def __getattr__(self, name):
                return getattr(self._sock, name)

        def write(handler, reply, target):
            handler.request = SlowSocket(handler.request)
            started = time.monotonic()
            try:
                return original(handler, reply, target)
            finally:
                elapsed.append(time.monotonic() - started)
                finished.set()

        with patch.object(server, "WRITE_SECONDS", 0.3), patch.object(server._Handler, "_write_reply", write):
            sock = self.model_download(content, buffer=0)
            with http.client.HTTPResponse(sock) as response:
                response.begin()
                self.assertEqual(response.status, 200)
                received = bytearray()
                while chunk := response.read(16384):
                    received.extend(chunk)
                self.assertEqual(bytes(received), content)
            self.assertTrue(finished.wait(1))
            self.assertGreater(elapsed[0], 3 * server.WRITE_SECONDS)
        self.wait_connections(0)

    def test_model_stalled_reader_times_out_and_releases_slot(self):
        self.enrolled()
        original = server._Handler._write_reply
        timed_out = threading.Event()
        elapsed = []

        def write(handler, reply, target):
            handler.request.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)
            started = time.monotonic()
            try:
                return original(handler, reply, target)
            except TimeoutError:
                elapsed.append(time.monotonic() - started)
                timed_out.set()
                raise

        with patch.object(server, "WRITE_SECONDS", 0.3), patch.object(server._Handler, "_write_reply", write):
            sock = self.model_download(b"m" * (2 * 1024 * 1024))
            self.assertTrue(timed_out.wait(2))
            self.wait_connections(0)
            self.assertGreaterEqual(elapsed[0], 0.3)
            self.assertLess(elapsed[0], 0.9)
            self.assertLess(len(self.receive(sock)), 2 * 1024 * 1024)
        # All four admission slots are reusable, including the stalled one.
        sockets = [self.connect() for _ in range(server.MAX_CONNECTIONS)]
        for sock in sockets:
            sock.sendall(b"G")
        self.wait_connections(server.MAX_CONNECTIONS)
        for sock in sockets:
            sock.close()
        self.wait_connections(0)

    def test_timer_calls_tick_four_times_per_second(self):
        event = threading.Event()
        with patch.object(self.link, "tick", side_effect=event.set):
            self.assertTrue(event.wait(1.5))
        self.assertEqual(server.HEADER_SECONDS, 5)
        self.assertEqual(server.BODY_SECONDS, 10)

    def test_stop_closes_slow_sockets_polls_and_threads(self):
        self.enrolled()
        entered = threading.Event()
        original = self.answers.current

        def current(pane):
            entered.set()
            return original(pane)

        with patch.object(self.answers, "current", side_effect=current), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.pc.work, 20)
            self.assertTrue(entered.wait(1))
            slow = self.connect()
            slow.sendall(b"G")
            self.wait_connections(2)
            self.listener.close()
            try:
                result = future.result(timeout=1)
                self.assertEqual(result.status, 204)
            except (OSError, ConnectionError):
                pass
            self.assertEqual(self.receive(slow), b"")
        self.assertFalse(self.listener._workers)
        self.assertFalse(self.listener._connections)
        self.assertFalse(any(thread.name.startswith("gpu-link-") for thread in threading.enumerate()))

    def test_nothing_printed_on_bad_connections(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assert_status(self.raw(b"bad\r\nHost: localhost\r\n\r\n"), 400)
            self.assert_status(self.raw(b"GET /v1/hello HTTP/1.1\r\nHost: invalid\r\n\r\n"), 400)
        self.assertEqual(output.getvalue(), "")

    def test_nonloopback_bind_refused(self):
        with self.assertRaises(ValueError):
            server.Listener(self.link, host="example.test", port=0)

    def test_start_switch_lifecycle_and_name(self):
        self.addCleanup(server.stop)
        self.assertIsNone(server.start(self.answers, {}))
        self.assertIsNone(server.service())
        env = {
            "OPOS_GPU_LINK": "1",
            "OPOS_GPU_LINK_PORT": "0",
            "XDG_STATE_HOME": str(self.root / "start"),
            "OPOS_GPU_LINK_MODEL": str(self.model),
            "OPOS_GPU_LINK_MODEL_NAME": "Readable model",
        }
        link = server.start(self.answers, env)
        self.assertIs(server.service(), link)
        self.assertIs(server.start(self.answers, env), link)
        self.assertEqual(link.snapshot()["model"]["name"], "Readable model")
        server.stop()
        self.assertIsNone(server.service())
        server.stop()

    def test_start_bad_port_cleans_up(self):
        self.addCleanup(server.stop)
        with self.assertRaises(ValueError):
            server.start(
                self.answers,
                {"OPOS_GPU_LINK": "1", "OPOS_GPU_LINK_PORT": "bad", "XDG_STATE_HOME": str(self.root / "bad")},
            )
        self.assertIsNone(server.service())

    def test_shared_example_request_and_reply_signatures(self):
        examples = json.loads((Path(__file__).parent / "fixtures/gpu_link/examples.json").read_text(encoding="utf-8"))
        vector = examples["requests"][0]
        self.pc = FakePC(address=self.address, key=bytes.fromhex(vector["key_hex"]))
        self.pc.hello()
        self.enrolled()
        with self.link._lock:
            self.link._epoch = bytes.fromhex(vector["epoch_hex"])
        headers = {"X-Link-Pc": vector["pc_id"], "X-Link-Nonce": vector["nonce_hex"], "X-Link-Mac": vector["mac"]}
        reply = self.pc.send(vector["method"], vector["target"], headers, bytes.fromhex(vector["body_hex"]))
        self.pc.verify(reply, vector["target"], bytes.fromhex(vector["nonce_hex"]))
        expected = examples["replies"][0]
        self.assertEqual(reply.status, expected["status"])
        self.assertEqual(reply.body, bytes.fromhex(expected["body_hex"]))
        self.assertEqual(dict(reply.headers)["X-Link-Mac"], expected["mac"])


class StartStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(server.stop)
        self.env = {"OPOS_GPU_LINK": "1", "OPOS_GPU_LINK_PORT": "0", "XDG_STATE_HOME": self.temp.name}
        self.answers = AnswerStore(Store(Path(self.temp.name) / "answers"))

    def test_worker_start_failures_return_original_error_and_close_twice(self):
        # A child bounds a regression that would otherwise deadlock test cleanup.
        code = textwrap.dedent("""\
            import sys, threading
            from pathlib import Path
            from unittest.mock import patch
            from opos import gpu_link_server as server
            from opos.ai_answers import AnswerStore
            from opos.store import Store
            real_start = threading.Thread.start
            real_listener = server.Listener
            answers = AnswerStore(Store(Path(sys.argv[1]) / "answers"))
            env = {"OPOS_GPU_LINK": "1", "OPOS_GPU_LINK_PORT": "0", "XDG_STATE_HOME": sys.argv[1]}
            for name in ("gpu-link-listen", "gpu-link-timer"):
                made = []
                def listener(*args, **kwargs):
                    value = real_listener(*args, **kwargs)
                    made.append(value)
                    return value
                def start(thread):
                    if thread.name == name:
                        raise RuntimeError(name)
                    return real_start(thread)
                with patch.object(server, "Listener", listener), patch.object(threading.Thread, "start", start):
                    try:
                        server.start(answers, env)
                    except RuntimeError as err:
                        assert str(err) == name
                    else:
                        raise AssertionError("startup succeeded")
                made[0].close()
                made[0].close()
                assert made[0].socket.fileno() == -1
                assert not made[0]._workers
                assert server.service() is None
                seen = []
                thread = threading.Thread(target=lambda: seen.append(server.service()))
                thread.start()
                thread.join(1)
                assert seen == [None]
                server.stop()
                assert not any(t.name.startswith("gpu-link-") for t in threading.enumerate())
            """)
        result = subprocess.run(
            [sys.executable, "-c", code, self.temp.name],
            capture_output=True,
            timeout=8,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_surv_concurrent_start_builds_one_listener(self):
        built = []
        found = []
        real = server.Listener

        def slow(link, host="127.0.0.1", port=0):
            time.sleep(0.2)
            listener = real(link, host, port)
            built.append(listener)
            self.addCleanup(listener.close)
            return listener

        with patch.object(server, "Listener", slow), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(server.start, self.answers, self.env) for _ in range(2)]
            found = [future.result(timeout=3) for future in futures]
        self.assertEqual(len(built), 1)
        self.assertIs(found[0], found[1])

    def test_surv_concurrent_stop_closes_once(self):
        server.start(self.answers, self.env)
        listener = server._Running.listener
        closed = []
        real = listener.close

        def slow():
            closed.append(1)
            time.sleep(0.2)
            real()

        with patch.object(listener, "close", slow), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(server.stop) for _ in range(2)]
            for future in futures:
                future.result(timeout=3)
        self.assertEqual(len(closed), 1)


class DrainTests(unittest.TestCase):
    def handler(self, deadline=110):
        handler = server._Handler.__new__(server._Handler)
        handler.request = Mock()
        handler._buffer = bytearray(b"x" * 4096)
        handler._request_deadline = deadline
        return handler

    def test_drain_discards_at_most_64_kib_including_buffer(self):
        handler = self.handler()
        handler.request.recv.side_effect = lambda size: b"x" * size
        with patch.object(server.time, "monotonic", return_value=100):
            handler._drain()
        self.assertEqual(sum(args[0] for args, _ in handler.request.recv.call_args_list), 65536 - 4096)
        self.assertEqual(handler.request.shutdown.call_args_list, [call(socket.SHUT_WR), call(socket.SHUT_RD)])
        self.assertEqual(handler._buffer, b"")
        self.assertEqual(handler.request.settimeout.call_args_list, [call(1)] * 15)

    def test_drain_uses_one_second_or_original_request_deadline(self):
        for deadline, expected in ((110, 1), (100.25, 0.25)):
            handler = self.handler(deadline)
            handler.request.recv.return_value = b"x"
            with self.subTest(deadline=deadline), patch.object(server.time, "monotonic", side_effect=[100, 100, 102]):
                with self.assertRaises(TimeoutError):
                    handler._drain()
            handler.request.settimeout.assert_called_once_with(expected)
            handler.request.recv.assert_called_once_with(4096)
            self.assertEqual(handler.request.shutdown.call_args_list, [call(socket.SHUT_WR), call(socket.SHUT_RD)])


if __name__ == "__main__":
    unittest.main()
