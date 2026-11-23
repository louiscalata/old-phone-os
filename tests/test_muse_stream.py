# SPDX-License-Identifier: GPL-3.0-or-later
"""Stream parsing, request policy, bounded reads and response lifetimes."""

import contextlib
import gc
import http.server
import io
import json
import os
import threading
import time
import unittest
import urllib.error
import warnings
from collections import deque
from dataclasses import replace
from unittest import mock

from opos import muse


def event(content=None, finish=None, **extra):
    delta = {} if content is None else {"content": content}
    return data_line({"choices": [{"delta": delta, "finish_reason": finish}], **extra})


def data_line(obj):
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n"


DONE = b"data: [DONE]\n\n"
ROUTE = muse.Route("local", "Test helper", "here", "http://127.0.0.1:1/v1", "test-model")


class FakeResponse:
    def __init__(self, *chunks):
        self.chunks = deque(chunks)
        self.close_calls = 0
        self.read_calls = 0

    def read(self, size):
        self.read_calls += 1
        if not self.chunks:
            return b""
        chunk = self.chunks.popleft()
        if isinstance(chunk, Exception):
            raise chunk
        if len(chunk) > size:
            self.chunks.appendleft(chunk[size:])
        return chunk[:size]

    def close(self):
        self.close_calls += 1


class FakeOpener:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []
        self.timeouts = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        return self.responses.popleft()


class TestStream(unittest.TestCase):
    def open_fake(self, *chunks, route=ROUTE, **kwargs):
        response = FakeResponse(*chunks)
        opener = FakeOpener(response)
        self.addCleanup(lambda: self.assertGreater(response.close_calls, 0))
        result = muse.stream(route, "test question", env={}, opener=opener, **kwargs)
        self.addCleanup(result.close)
        return result, response, opener

    def test_pieces_order_metadata_and_done(self):
        for finish in ("stop", "length"):
            with self.subTest(finish=finish):
                result, response, _ = self.open_fake(
                    event("one "),
                    event("two", finish),
                    data_line({"choices": [], "usage": {"completion_tokens": 7}}),
                    data_line({"choices": [], "timings": {"predicted_per_second": 12.5}}),
                    DONE,
                    event("never read"),
                )
                self.assertIsNone(result.finish)
                pieces = list(result)
                self.assertEqual(pieces, ["one ", "two"])
                self.assertTrue(all(pieces))
                self.assertEqual("".join(pieces), "one two")
                self.assertEqual(result.finish, finish)
                self.assertEqual(result.tokens, 7)
                self.assertEqual(result.tps, 12.5)
                self.assertEqual(response.read_calls, 5)
                self.assertGreater(response.close_calls, 0)

    def test_byte_splits_crlf_comments_and_blank_lines(self):
        raw = b": comment\r\n\r\nevent: message\r\n" + event("café 🍵").replace(b"\n", b"\r\n") + DONE
        result, _, _ = self.open_fake(*(raw[i : i + 1] for i in range(len(raw))))
        self.assertEqual(list(result), ["café 🍵"])
        self.assertIsNone(result.finish)
        self.assertIsNone(result.tokens)
        self.assertIsNone(result.tps)

    def test_reasoning_role_null_and_empty_content_are_skipped(self):
        raw = data_line({"choices": [{"delta": {"reasoning_content": "hidden", "role": "assistant"}}]})
        raw += data_line({"choices": [{"delta": {"content": None}}]}) + event("")
        raw += event("visible") + DONE + event("ignored in same read")
        result, _, _ = self.open_fake(raw)
        self.assertEqual(list(result), ["visible"])

    def test_eof_delivers_arrived_text_then_raises(self):
        result, response, _ = self.open_fake(event("partial"))
        iterator = iter(result)
        self.assertEqual(next(iterator), "partial")
        with self.assertRaisesRegex(muse.MuseError, "ended early"):
            next(iterator)
        self.assertGreater(response.close_calls, 0)

    def test_eof_after_finish_and_unterminated_final_line(self):
        result, _, _ = self.open_fake(event("whole", "stop").rstrip(b"\n"))
        self.assertEqual(list(result), ["whole"])
        self.assertEqual(result.finish, "stop")

    def test_bad_data_and_shapes_close_response(self):
        bad = [
            b"data: not json\n",
            b"data: \xff\n",
            b'data: {"choices":[{"delta":{"content":"\\ud800"}}]}\n',
            data_line([]),
            data_line(None),
            data_line({}),
            data_line({"choices": {}}),
            data_line({"choices": [None]}),
            data_line({"choices": [{"delta": []}]}),
            data_line({"choices": [{"delta": {"content": 2}}]}),
            event(finish="other"),
            event(usage=[]),
            event(usage={"completion_tokens": True}),
            event(usage={"completion_tokens": -1}),
            event(timings=[]),
            event(timings={"predicted_per_second": float("inf")}),
            event(timings={"predicted_per_second": True}),
            event(timings={"predicted_per_second": 10**400}),
        ]
        for raw in bad:
            with self.subTest(index=bad.index(raw)):
                result, response, _ = self.open_fake(raw)
                with self.assertRaises(muse.MuseError):
                    list(result)
                self.assertGreater(response.close_calls, 0)

    def test_total_size_limit(self):
        with mock.patch.object(muse, "MAX_RESPONSE_BYTES", 32):
            result, response, _ = self.open_fake(b": comment\n" * 4)
            with self.assertRaisesRegex(muse.MuseError, "too large"):
                list(result)
            self.assertGreater(response.close_calls, 0)

    def test_line_limit_with_and_without_newline(self):
        for ending in (b"", b"\n"):
            with self.subTest(ending=ending), mock.patch.object(muse, "STREAM_LINE_BYTES", 16):
                result, response, _ = self.open_fake(b":" + b"x" * 16 + ending)
                with self.assertRaisesRegex(muse.MuseError, "line too large"):
                    list(result)
                self.assertGreater(response.close_calls, 0)
        with mock.patch.object(muse, "STREAM_LINE_BYTES", 16):
            result, _, _ = self.open_fake(b":" + b"x" * 15 + b"\n" + DONE)
            self.assertEqual(list(result), [])

    def test_request_is_payload_plus_stream_fields(self):
        for route in (ROUTE, muse.glimmer_route({})):
            with self.subTest(key=route.key):
                result, _, opener = self.open_fake(DONE, route=route, max_tokens=17)
                list(result)
                expected = muse._payload(route, "test question", 17)
                expected.update(stream=True, stream_options={"include_usage": True})
                request = opener.requests[0]
                self.assertEqual(json.loads(request.data), expected)
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.full_url, route.base_url + "/chat/completions")
                self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_verify_model_first_and_select_alternate(self):
        for listed in ([], [{"id": "alternate"}]):
            with self.subTest(listed=listed):
                models = FakeResponse(json.dumps({"data": listed}).encode())
                answer = FakeResponse(DONE)
                opener = FakeOpener(models, answer)
                route = replace(ROUTE, verify_model=True, alternates=(("alternate", "Alternate"),))
                if listed:
                    with muse.stream(route, "test", env={}, opener=opener) as result:
                        self.assertEqual(list(result), [])
                        self.assertEqual(result.route.model, "alternate")
                        self.assertEqual(result.route.name, "Alternate")
                    self.assertEqual(json.loads(opener.requests[1].data)["model"], "alternate")
                    self.assertGreater(answer.close_calls, 0)
                else:
                    with self.assertRaisesRegex(muse.MuseError, "model not listed"):
                        muse.stream(route, "test", env={}, opener=opener)
                    self.assertEqual(len(opener.requests), 1)
                self.assertGreater(models.close_calls, 0)
                self.assertEqual(opener.requests[0].get_method(), "GET")
                self.assertTrue(opener.requests[0].full_url.endswith("/models"))
                self.assertIsNone(opener.requests[0].data)

    def test_header_for_baseten_route_only(self):
        for key in ("spark-open", "local", "glimmer", "lan"):
            with self.subTest(key=key):
                response = FakeResponse(DONE)
                opener = FakeOpener(response)
                with muse.stream(
                    replace(ROUTE, key=key), "test", env={"BASETEN_API_KEY": "test-key"}, opener=opener
                ) as result:
                    list(result)
                expected = "Api-Key test-key" if key == "spark-open" else None
                self.assertEqual(opener.requests[0].get_header("Authorization"), expected)
                self.assertGreater(response.close_calls, 0)
        opener = FakeOpener()
        with self.assertRaisesRegex(muse.MuseError, "auth missing"):
            muse.stream(replace(ROUTE, key="spark-open"), "test", env={}, opener=opener)
        self.assertEqual(opener.requests, [])

    def test_bad_scheme_refused_before_verification_or_post(self):
        opener = FakeOpener()
        for url in ("file:///unused", "ftp://example.invalid", "http://["):
            with self.subTest(url=url), self.assertRaises(muse.MuseError):
                muse.stream(replace(ROUTE, base_url=url, verify_model=True), "test", env={}, opener=opener)
        self.assertEqual(opener.requests, [])

    def test_timeout_environment_explicit_and_invalid(self):
        for timeout, expected in ((None, 7.0), (0.5, 0.5)):
            response = FakeResponse(DONE)
            opener = FakeOpener(response)
            with muse.stream(ROUTE, "test", env={"OPOS_MUSE_TIMEOUT": "7"}, timeout=timeout, opener=opener):
                self.assertGreater(opener.timeouts[0], 0)
                self.assertLessEqual(opener.timeouts[0], expected)
            self.assertGreater(response.close_calls, 0)
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(muse.MuseError):
                muse.stream(ROUTE, "test", timeout=timeout, opener=FakeOpener())

    def test_total_deadline_includes_open_and_buffered_text(self):
        response = FakeResponse(event("one") + event("two") + DONE)
        opener = FakeOpener(response)
        with mock.patch.object(muse.time, "monotonic", return_value=0):
            result = muse.stream(ROUTE, "test", timeout=1, opener=opener)
            iterator = iter(result)
            self.assertEqual(next(iterator), "one")
        with mock.patch.object(muse.time, "monotonic", return_value=2):
            with self.assertRaisesRegex(muse.MuseError, "timed out"):
                next(iterator)
        self.assertGreater(response.close_calls, 0)
        response = FakeResponse(DONE)
        with mock.patch.object(muse.time, "monotonic", side_effect=[0, 0, 2]):
            with self.assertRaises(muse.MuseError):
                muse.stream(ROUTE, "test", timeout=1, opener=FakeOpener(response))
        self.assertGreater(response.close_calls, 0)

    def test_transport_error_closes_and_http_error_body_closes(self):
        for error, message in ((OSError("test failure"), "stream failed"), (TimeoutError(), "timed out")):
            with self.subTest(error=type(error).__name__):
                result, response, _ = self.open_fake(error)
                with self.assertRaisesRegex(muse.MuseError, message):
                    list(result)
                self.assertGreater(response.close_calls, 0)
        body = io.BytesIO(b"test")
        error = urllib.error.HTTPError(ROUTE.base_url, 302, "test redirect", {}, body)
        with self.assertRaises(muse.MuseError):
            muse.stream(ROUTE, "test", opener=mock.Mock(side_effect=error))
        self.assertTrue(body.closed)

    def test_close_twice_context_exception_and_dropped_iterators(self):
        result, response, _ = self.open_fake(DONE)
        result.close()
        calls = response.close_calls
        result.close()
        self.assertEqual(response.close_calls, calls)
        self.assertEqual(list(result), [])
        result, response, _ = self.open_fake(event("one"), event("two"))
        with self.assertRaisesRegex(RuntimeError, "test"):
            with result:
                raise RuntimeError("test")
        self.assertGreater(response.close_calls, 0)
        result, response, _ = self.open_fake(event("one"), event("two"))
        iterator = iter(result)
        self.assertEqual(next(iterator), "one")
        del iterator
        if response.close_calls == 0:
            gc.collect()
        self.assertGreater(response.close_calls, 0)

    def test_generator_close_closes_response(self):
        result, response, _ = self.open_fake(event("one"), event("two"))
        iterator = iter(result)
        self.assertEqual(next(iterator), "one")
        iterator.close()
        self.assertGreater(response.close_calls, 0)

    def test_uniterated_stream_is_closed_by_user(self):
        response = FakeResponse(DONE)
        with muse.stream(ROUTE, "test", opener=FakeOpener(response)):
            self.assertEqual(response.close_calls, 0)
        self.assertGreater(response.close_calls, 0)

    def test_unstarted_iterator_is_closed_by_context_or_stream_close(self):
        result, response, _ = self.open_fake(DONE)
        with result:
            iterator = iter(result)
            del iterator
            gc.collect()
            self.assertEqual(response.close_calls, 0)
        self.assertEqual(response.close_calls, 1)
        result, response, _ = self.open_fake(DONE)
        iterator = iter(result)
        del iterator
        gc.collect()
        self.assertEqual(response.close_calls, 0)
        result.close()
        self.assertEqual(response.close_calls, 1)

    def test_cancel_before_call_sends_no_request(self):
        cancel = threading.Event()
        cancel.set()
        opener = FakeOpener()
        with self.assertRaisesRegex(muse.MuseError, "^cancelled$"):
            muse.stream(replace(ROUTE, verify_model=True), "test", opener=opener, cancel=cancel)
        self.assertEqual(opener.requests, [])

    def test_cancel_during_model_check_sends_no_prompt(self):
        cancel = threading.Event()
        models = FakeResponse(b'{"data":[{"id":"test-model"}]}')
        opener = FakeOpener(models)

        def cancelling_opener(req, timeout):
            cancel.set()
            return opener(req, timeout=timeout)

        with self.assertRaisesRegex(muse.MuseError, "^cancelled$"):
            muse.stream(replace(ROUTE, verify_model=True), "test", opener=cancelling_opener, cancel=cancel)
        self.assertEqual([req.get_method() for req in opener.requests], ["GET"])
        self.assertEqual(models.close_calls, 1)

    def test_cancel_before_prompt_open_sends_no_request(self):
        cancel = threading.Event()
        opener = FakeOpener()
        payload = muse._payload(ROUTE, "test", muse.DEFAULT_MAX_TOKENS)

        def cancelling_payload(*args):
            cancel.set()
            return payload

        with mock.patch.object(muse, "_payload", side_effect=cancelling_payload):
            with self.assertRaisesRegex(muse.MuseError, "^cancelled$"):
                muse.stream(ROUTE, "test", opener=opener, cancel=cancel)
        self.assertEqual(opener.requests, [])

    def test_failure_between_open_and_return_closes_response(self):
        class BrokenResponse(FakeResponse):
            @property
            def read1(self):
                raise RuntimeError("first use failed")

        for verify_model in (False, True):
            with self.subTest(verify_model=verify_model):
                response = BrokenResponse(DONE)
                with self.assertRaisesRegex(muse.MuseError, "request failed") as caught:
                    muse.stream(replace(ROUTE, verify_model=verify_model), "test", opener=FakeOpener(response))
                self.assertIsInstance(caught.exception.__cause__, RuntimeError)
                self.assertEqual(response.close_calls, 1)

    def test_cancel_before_first_read(self):
        cancel = threading.Event()
        result, response, _ = self.open_fake(event("unread"), DONE, cancel=cancel)
        cancel.set()
        self.assertEqual(list(result), [])
        self.assertEqual(response.read_calls, 0)
        self.assertGreater(response.close_calls, 0)

    def test_cancel_with_buffered_text(self):
        cancel = threading.Event()
        result, response, _ = self.open_fake(event("one") + event("two") + DONE, cancel=cancel)
        iterator = iter(result)
        self.assertEqual(next(iterator), "one")
        cancel.set()
        self.assertEqual(list(iterator), [])
        self.assertGreater(response.close_calls, 0)

    def test_cancel_after_read_discards_returned_bytes_or_error(self):
        for error in (None, TimeoutError(), OSError("read failed")):
            with self.subTest(error=type(error).__name__):
                cancel = threading.Event()
                response = FakeResponse()

                def read(size, cancel=cancel, error=error):
                    cancel.set()
                    if error is not None:
                        raise error
                    return event("discarded")

                with mock.patch.object(response, "read", side_effect=read):
                    with muse.stream(ROUTE, "test", opener=FakeOpener(response), cancel=cancel) as result:
                        self.assertEqual(list(result), [])
                self.assertEqual(response.close_calls, 1)

    def test_deadline_after_read_discards_returned_text(self):
        response = FakeResponse()
        with mock.patch.object(muse.time, "monotonic", return_value=0) as clock:

            def read(size):
                clock.return_value = 2
                return event("too late")

            with mock.patch.object(response, "read", side_effect=read):
                with muse.stream(ROUTE, "test", timeout=1, opener=FakeOpener(response)) as result:
                    with self.assertRaisesRegex(muse.MuseError, "timed out"):
                        next(iter(result))
        self.assertEqual(response.close_calls, 1)

    def test_model_list_read_errors_close_response_without_posting(self):
        for error in (TimeoutError(), OSError("read failed")):
            with self.subTest(error=type(error).__name__):
                response = FakeResponse(error)
                opener = FakeOpener(response)
                with self.assertRaises(muse.MuseError):
                    muse.stream(replace(ROUTE, verify_model=True), "test", opener=opener)
                self.assertEqual(response.close_calls, 1)
                self.assertEqual(len(opener.requests), 1)
                self.assertEqual(opener.requests[0].get_method(), "GET")

    def test_model_check_uses_idle_timeout_and_deadline_between_reads(self):
        models = FakeResponse(b'{"data":', b'[{"id":"test-model"}]}')
        answer = FakeResponse(DONE)
        opener = FakeOpener(models, answer)
        with mock.patch.object(muse, "STREAM_IDLE_S", 0.5):
            with muse.stream(replace(ROUTE, verify_model=True), "test", opener=opener) as result:
                self.assertEqual(list(result), [])
        self.assertTrue(all(0 < value <= 0.5 for value in opener.timeouts))
        self.assertGreater(models.close_calls, 0)
        models = FakeResponse(b'{"data":', b'[{"id":"test-model"}]}')
        opener = FakeOpener(models)
        # Opening, first read, and second read fit; the second read returns late.
        with mock.patch.object(muse.time, "monotonic", side_effect=[0, 0, 0, 0, 0, 0, 2]):
            with self.assertRaisesRegex(muse.MuseError, "timed out"):
                muse.stream(replace(ROUTE, verify_model=True), "test", timeout=1, opener=opener)
        self.assertEqual(len(opener.requests), 1)
        self.assertGreater(models.close_calls, 0)


@contextlib.contextmanager
def loopback_server(script):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            return None

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.server.hits.append(self.path)
            try:
                script(self)
            except (BrokenPipeError, ConnectionResetError):
                self.server.disconnected.set()

        def do_GET(self):
            self.do_POST()

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.hits = []
    server.disconnected = threading.Event()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server, replace(ROUTE, base_url=f"http://127.0.0.1:{server.server_port}/v1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise AssertionError("loopback server did not stop")


def start_reply(handler):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()


class TestStreamHTTP(unittest.TestCase):
    def test_small_writes_end_to_end_without_resource_warnings(self):
        def script(handler):
            start_reply(handler)
            raw = event("café") + event(" tea", "stop") + DONE
            for offset in range(0, len(raw), 7):
                handler.wfile.write(raw[offset : offset + 7])
                handler.wfile.flush()
                time.sleep(0.002)

        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            with loopback_server(script) as (_, route), muse.stream(route, "test", timeout=5) as result:
                self.assertEqual("".join(result), "café tea")
                self.assertEqual(result.finish, "stop")
            gc.collect()

    def test_redirect_is_refused(self):
        def script(handler):
            handler.send_response(302)
            handler.send_header("Location", "/redirect-target")
            handler.send_header("Content-Length", "0")
            handler.end_headers()

        with loopback_server(script) as (server, route):
            with self.assertRaises(muse.MuseError):
                muse.stream(route, "test", timeout=5)
            self.assertEqual(server.hits, ["/v1/chat/completions"])
        gc.collect()

    def test_environment_proxy_is_not_used(self):
        def script(handler):
            start_reply(handler)
            handler.wfile.write(event("direct", "stop") + DONE)

        with loopback_server(script) as (proxy, proxy_route), loopback_server(script) as (server, route):
            proxy_url = proxy_route.base_url.removesuffix("/v1")
            env = {"http_proxy": proxy_url, "HTTP_PROXY": proxy_url, "ALL_PROXY": proxy_url, "no_proxy": ""}
            with mock.patch.dict(os.environ, env, clear=True), muse.stream(route, "test", timeout=5) as result:
                self.assertEqual(list(result), ["direct"])
            self.assertEqual(proxy.hits, [])
            self.assertEqual(server.hits, ["/v1/chat/completions"])

    def test_idle_read_limit(self):
        def script(handler):
            start_reply(handler)
            handler.wfile.write(event("first"))
            handler.wfile.flush()
            # Stay silent until the client closes after its per-read timeout.
            if handler.connection.recv(1) == b"":
                handler.server.disconnected.set()

        with loopback_server(script) as (server, route), mock.patch.object(muse, "STREAM_IDLE_S", 0.3):
            started = time.monotonic()
            with muse.stream(route, "test", timeout=5) as result:
                iterator = iter(result)
                self.assertEqual(next(iterator), "first")
                with self.assertRaisesRegex(muse.MuseError, "timed out"):
                    next(iterator)
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(server.disconnected.wait(5))

    def test_total_deadline_despite_active_stream(self):
        def script(handler):
            start_reply(handler)
            # No DONE or finish reason: EOF must never pass as a deadline error.
            # The safety limit bounds a broken implementation to about 5 s.
            stop = time.monotonic() + 5
            while time.monotonic() < stop:
                handler.wfile.write(event("piece"))
                handler.wfile.flush()
                time.sleep(0.02)

        with loopback_server(script) as (server, route):
            started = time.monotonic()
            with muse.stream(route, "test", timeout=0.3) as result:
                with self.assertRaisesRegex(muse.MuseError, "timed out"):
                    list(result)
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(server.disconnected.wait(5))

    def test_total_deadline_includes_model_list_body(self):
        def script(handler):
            start_reply(handler)
            handler.wfile.write(b'{"data":[')
            stop = time.monotonic() + 5
            while time.monotonic() < stop:
                handler.wfile.write(b" ")
                handler.wfile.flush()
                time.sleep(0.02)

        with loopback_server(script) as (server, route):
            started = time.monotonic()
            with self.assertRaisesRegex(muse.MuseError, "timed out"):
                muse.stream(replace(route, verify_model=True), "test", timeout=0.3)
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(server.hits, ["/v1/models"])
            self.assertTrue(server.disconnected.wait(5))

    def test_cancel_during_read_closes_connection(self):
        waiting = threading.Event()
        reading = threading.Event()
        cancel = threading.Event()

        def script(handler):
            start_reply(handler)
            handler.wfile.write(event("first"))
            handler.wfile.flush()
            waiting.set()
            if handler.connection.recv(1) == b"":
                handler.server.disconnected.set()

        class ObservedResponse:
            def __init__(self, response):
                self.response = response
                self.reads = 0

            def read1(self, size):
                self.reads += 1
                if self.reads == 2:
                    reading.set()
                return self.response.read1(size)

            def close(self):
                self.response.close()

        def opener(req, timeout):
            return ObservedResponse(muse._open_no_redirect(req, timeout=timeout))

        with loopback_server(script) as (server, route), mock.patch.object(muse, "STREAM_IDLE_S", 5):
            with muse.stream(route, "test", timeout=15, cancel=cancel, opener=opener) as result:
                failures = []
                pieces = []

                def consume():
                    try:
                        pieces.extend(result)
                    except Exception as err:
                        failures.append(err)

                thread = threading.Thread(target=consume)
                thread.start()
                try:
                    self.assertTrue(waiting.wait(3))
                    self.assertTrue(reading.wait(3))
                    cancel.set()
                finally:
                    cancel.set()
                    thread.join(timeout=8)
                self.assertFalse(thread.is_alive())
                self.assertEqual(pieces, ["first"])
                self.assertEqual(failures, [])
                self.assertTrue(server.disconnected.wait(5))


if __name__ == "__main__":
    unittest.main()
