# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the developer PC against the real loopback link listener."""

from __future__ import annotations

import contextlib
import http.client
import importlib.util
import io
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from opos import gpu_link_proto as proto
from opos import gpu_link_server
from opos.ai_answers import AnswerStore, text_ok
from opos.gpu_link import LinkService, Reply
from opos.store import Store
from tests.gpu_link_fake_pc import Clock, RandomBytes

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("standin_pc", ROOT / "tools/gpu-link-standin-pc.py")
standin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standin)
ENROL_ERROR = b"Enrolment request refused: stdout must be a pipe or socket to the phone enrolment command\n"


class StandInTests(unittest.TestCase):
    """Real requests, deterministic phone time and private throwaway state."""

    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        store = self.store = Store(self.root / "state")
        self.answers = AnswerStore(store)
        model = self.model = self.root / "practice.bin"
        model.write_bytes(b"practice fixture, not a model")
        self.clock = Clock()
        self.link = LinkService(self.answers, store, model, clock=self.clock, rand=RandomBytes())
        self.addCleanup(self.link.close)
        self.link._hasher.join(3)
        self.assertFalse(self.link._hasher.is_alive())
        self.listener = gpu_link_server.Listener(self.link, port=0)
        self.addCleanup(self.listener.close)
        self.listener.run()
        host, port = self.listener.server_address
        self.phone = f"{host}:{port}"
        self.pc = standin.PC(self.phone, b"K" * 32)
        self.lines = []
        self.runner = standin.StandIn(self.pc, emit=self.lines.append, clock=self.clock)
        self.addCleanup(self.runner.close)

    def deliver_enrol_request(self, request):
        """Send the captured stdin bytes through the real listener's framing checks."""
        with socket.create_connection(self.listener.server_address, timeout=2) as sock:
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
            with http.client.HTTPResponse(sock) as response:
                response.begin()
                return Reply(response.status, tuple(response.getheaders()), response.read())

    def enrolled(self):
        """Deliver the tool's stdin request and approve its code as the owner would."""
        self.assertEqual(self.deliver_enrol_request(self.pc.enrol_request()).status, 202)
        self.assertTrue(self.link.allow(proto.code(self.pc.label, self.pc.key)))
        self.pc.hello()
        self.assertEqual(self.pc.status()[0], 200)

    def enrol_output(self):
        """Capture binary stdout without decoding or logging the key-bearing request."""
        read_fd, write_fd = os.pipe()
        error = io.StringIO()
        args = ["--key-file", str(self.root / "pc.key"), "--enrol-request"]
        with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "w") as output:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                result = standin.main(args)
            output.close()
            return result, reader.read(), error.getvalue()

    def drive(self, final=True, limit=100):
        """Drive one reactor with time for stream-reader threads to run."""
        for _ in range(limit):
            self.runner.step()
            answer = self.answers.latest("gpu")
            if answer and answer["state"] in ("done", "failed", "cancelled"):
                return answer
            if not final and answer and answer["bytes"]:
                return answer
            self.clock.advance(0.26)
            threading.Event().wait(0.005)
        self.fail("answer did not reach expected state")

    def test_enrol_request_accepted_and_matching_pending_code(self):
        result, request, error = self.enrol_output()
        self.assertEqual(result, 0)
        self.assertEqual(self.deliver_enrol_request(request).status, 202)
        pending = self.link.snapshot()["pending"]
        key = (self.root / "pc.key").read_bytes()
        code = proto.code(self.pc.label, key)
        self.assertEqual(pending["code"], code)
        self.assertEqual(error, f"Compare code on phone: {code[:3]} {code[3:]}\n")
        self.assertNotIn(key.hex(), error)
        self.assertEqual(stat.S_IMODE((self.root / "pc.key").stat().st_mode), 0o600)

    def test_enrol_request_refuses_terminal_without_output(self):
        output, error = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
            patch.object(output, "isatty", return_value=True),
        ):
            result = standin.main(["--key-file", str(self.root / "unused"), "--enrol-request"])
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("pipe or socket", error.getvalue())
        self.assertFalse((self.root / "unused").exists())

    def test_enrol_request_never_connects(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("connection forbidden")) as create,
            patch.object(socket.socket, "connect", side_effect=AssertionError("connection forbidden")) as connect,
        ):
            result, request, _ = self.enrol_output()
        self.assertEqual(result, 0)
        self.assertTrue(request.startswith(b"POST /v1/enrol HTTP/1.1\r\n"))
        create.assert_not_called()
        connect.assert_not_called()

    def test_practice_end_to_end_and_no_text_in_events(self):
        self.enrolled()
        self.answers.ask("gpu", "PRIVATE QUESTION marker")
        result = self.drive()
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["text"].encode(), standin.PRACTICE.encode())
        self.assertIsNone(result["tokens"])
        self.assertIsNone(result["tps"])
        events = "\n".join(self.lines)
        for secret in (self.pc.key.hex(), "PRIVATE QUESTION", "A shared library", "Practice text"):
            self.assertNotIn(secret, events)
        self.assertIsNone(self.link.snapshot()["pc"]["gpu"])
        self.assertTrue(any("final" in line for line in self.lines))

    def test_pause_resume_preserves_every_byte_and_new_lease(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        with patch.object(standin, "PRACTICE", "Practice é 🌿\n\t" * 50):
            first = self.drive(final=False)
            prefix = first["text"].encode()
            old_lease = self.runner.job["lease"]
            old_source = self.runner.source
            self.clock.advance(2.6)
            self.link.tick()
            self.assertEqual(self.answers.get(first["id"])["state"], "paused")
            self.runner.step()
            self.assertIsNot(self.runner.source, old_source)
            self.assertIn("Lease ended; polling again", self.lines)
            self.assertIn("Status ready", self.lines)
            self.assertNotEqual(self.runner.job["lease"], old_lease)
            self.assertEqual(self.runner.job["partial"].encode(), prefix)
            result = self.drive()
            self.assertEqual(result["text"].encode(), standin.PRACTICE.encode())
            self.assertTrue(result["text"].encode().startswith(prefix))

    def test_cancel_from_phone_stops_sending(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        first = self.drive(final=False)
        self.link.cancel(first["id"])
        self.clock.advance(1.01)
        self.runner.step()
        self.assertIsNone(self.runner.job)
        self.assertIn("Answer cancelled by phone", self.lines)
        self.assertEqual(self.answers.get(first["id"])["text"], first["text"])
        self.assertEqual(self.answers.get(first["id"])["state"], "cancelled")

    def test_409_recovery_from_phone_stored_length(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        first = self.drive(final=False)
        # The stand-in thinks more was acknowledged than the phone has stored.
        # A real signed 409 then moves it back to the phone's byte offset.
        self.runner.offset += 1
        self.clock.advance(0.26)
        self.runner.step()
        self.assertEqual(self.runner.offset, first["bytes"])
        self.assertIn(f"Offset recovered; stored {first['bytes']} bytes", self.lines)
        self.assertEqual(self.drive()["text"], standin.PRACTICE)

    def test_lost_ack_repeats_identical_bytes(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        first = self.drive(final=False)
        self.runner.offset = 0
        self.clock.advance(0.26)
        self.runner.step()
        self.assertEqual(self.answers.get(first["id"])["text"], first["text"])
        self.assertEqual(self.drive()["text"], standin.PRACTICE)

    def test_missing_or_wrong_signature_is_fatal(self):
        self.enrolled()
        original = self.link.handle
        for mac in (None, "00" * 32):

            def handle(*args, mac=mac):
                reply = original(*args)
                headers = tuple((k, v) for k, v in reply.headers if k.lower() != "x-link-mac")
                if mac is not None:
                    headers += (("X-Link-Mac", mac),)
                return Reply(reply.status, headers, reply.body)

            with (
                patch.object(self.link, "handle", side_effect=handle),
                self.assertRaisesRegex(standin.ToolError, "signature"),
            ):
                self.pc.status()

    def test_epoch_rollover_and_removed_key(self):
        self.enrolled()
        previous = self.pc.epoch
        with self.link._lock:
            self.link._epoch = b"E" * 16
        self.runner.step()
        self.assertNotEqual(previous, self.pc.epoch)
        self.assertIn("Status ready", self.lines)
        self.assertIsNone(self.runner.failed_since)
        self.clock.advance(1.1)
        self.runner.step()
        self.link.forget()
        self.clock.advance(1.1)
        # Forgetting the PC starts a new epoch, so the first refusal looks like a renewal.
        self.runner.step()
        self.assertEqual(self.lines.count("Phone epoch renewed; waiting to retry"), 1)
        self.clock.advance(1.1)
        with self.assertRaisesRegex(standin.ToolError, "key not accepted"):
            self.runner.step()

    def test_unreachable_retry_then_resume(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        first = self.drive(final=False)
        # Close the real listener, preserving the phone's service and store.
        address = self.listener.server_address
        self.listener.close()
        self.clock.advance(3)
        self.link.tick()
        stop = threading.Event()

        def reconnect(line):
            self.lines.append(line)
            if "unreachable" in line:
                self.link = LinkService(self.answers, self.store, self.model, clock=self.clock)
                self.addCleanup(self.link.close)
                if self.link._hasher:
                    self.link._hasher.join(3)
                listener = gpu_link_server.Listener(self.link, host=address[0], port=address[1])
                self.addCleanup(listener.close)
                listener.run()
            if "; final" in line:
                stop.set()

        self.runner.emit = reconnect
        # Use real time in this one loop; the phone clock advances on each event.
        original_emit = self.runner.emit

        def emit(line):
            self.clock.advance(0.26)
            original_emit(line)

        self.runner.emit = emit
        self.runner.next_post = self.runner.next_status = 0

        # A bounded event wait advances the fake reactor clock on every iteration.
        clock = self.clock
        deadline = clock() + 60

        class Stop:
            def is_set(self):
                return stop.is_set() or clock() >= deadline

            def wait(self, seconds):
                clock.advance(seconds)
                return stop.wait(min(seconds, 0.01))

        self.runner.run(Stop())
        self.assertEqual(self.answers.get(first["id"])["text"], standin.PRACTICE)
        self.assertTrue(any("unreachable" in line for line in self.lines))

    def test_rate_limit_and_whole_clean_characters(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        posts = []
        original = self.pc.send

        def send(method, target, *args, **kwargs):
            if target.endswith("/text"):
                posts.append((self.clock(), proto.parse_text(args[0])))
            return original(method, target, *args, **kwargs)

        dirty = "Practice\r\x00\x7f\x85\ud800 é🌿\n\t" * 30
        with patch.object(standin, "PRACTICE", dirty), patch.object(self.pc, "send", side_effect=send):
            result = self.drive()
        self.assertEqual(result["text"], standin.clean(dirty))
        self.assertTrue(all(text_ok(value["data"]) for _, value in posts))
        self.assertTrue(all(b[0] - a[0] >= 0.25 for a, b in zip(posts, posts[1:], strict=False)))
        self.assertNotIn("\r", result["text"])

    def test_show_text_is_explicit_and_one_line(self):
        self.enrolled()
        self.runner.show_text = True
        self.answers.ask("gpu", "question\nsecond line")
        self.drive()
        self.assertTrue(any(line.startswith("Question: ") for line in self.lines))
        self.assertTrue(any(line.startswith("Text: ") for line in self.lines))
        self.assertTrue(all("\n" not in line for line in self.lines))

    def test_key_file_mode_size_and_reuse(self):
        path = self.root / "pc.key"
        key = standin.key_file(path)
        self.assertEqual(len(key), 32)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(standin.key_file(path), key)
        path.chmod(0o644)
        with self.assertRaises(standin.ToolError):
            standin.key_file(path)
        path.chmod(0o600)
        path.write_bytes(b"short")
        with self.assertRaises(standin.ToolError):
            standin.key_file(path)
        alias = self.root / "alias"
        alias.symlink_to(path)
        with self.assertRaises(OSError):
            standin.key_file(alias)

    def test_key_file_without_posix_names(self):
        path = self.root / "windows.key"
        with contextlib.ExitStack() as stack:
            for name in ("O_NOFOLLOW", "O_NONBLOCK", "getuid", "fchmod"):
                if hasattr(standin.os, name):
                    stack.enter_context(patch.object(standin.os, name))
                    delattr(standin.os, name)
            key = standin.key_file(path)
            self.assertEqual(len(key), 32)
            self.assertEqual(standin.key_file(path), key)
            path.write_bytes(b"short")
            with self.assertRaises(standin.ToolError):
                standin.key_file(path)

    def raw_reply(self, prefix, tail=b"", interval=0, close=False):
        """Open a loopback fixture and stop its reader and writer at cleanup."""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        release = threading.Event()

        def serve():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(3)
                    request = b""
                    while b"\r\n\r\n" not in request:
                        request += conn.recv(4096)
                    head, body = request.split(b"\r\n\r\n", 1)
                    fields = http.client.parse_headers(io.BytesIO(head.split(b"\r\n", 1)[1] + b"\r\n\r\n"))
                    needed = int(fields.get("Content-Length", "0")) - len(body)
                    while needed > 0:
                        chunk = conn.recv(needed)
                        if not chunk:
                            return
                        request += chunk
                        needed -= len(chunk)
                    conn.sendall(prefix(request) if callable(prefix) else prefix)
                    for byte in tail:
                        if release.wait(interval):
                            return
                        conn.sendall(bytes([byte]))
                    if not close:
                        release.wait(3)
            except OSError:
                pass  # The client closes on its deadline or header bound.

        worker = threading.Thread(target=serve)
        worker.start()

        def close():
            release.set()
            listener.close()
            worker.join(4)
            self.assertFalse(worker.is_alive())

        self.addCleanup(close)
        host, port = listener.getsockname()
        return standin.PC(f"{host}:{port}", self.pc.key, request_limit=0.65)

    def signed_fixture(self, status, raw, extra=b"", length=None):
        """MAC the actual request nonce, including deliberately malformed JSON bodies."""

        def reply(request):
            headers = http.client.parse_headers(io.BytesIO(request.split(b"\r\n", 1)[1]))
            nonce = bytes.fromhex(headers["X-Link-Nonce"])
            mac = proto.reply_mac(self.pc.key, nonce, status, "", raw).encode()
            return (
                f"HTTP/1.1 {status} Fixture\r\nContent-Length: {len(raw) if length is None else length}\r\n".encode()
                + b"X-Link-Mac: "
                + mac
                + b"\r\n"
                + extra
                + b"\r\n"
                + raw
            )

        return reply

    def test_reply_schemas_reject_bad_fields_after_valid_mac(self):
        lease, digest = "ab" * 16, "cd" * 32
        work = dict(lease=lease, model_sha256=digest, question="question", partial="")
        cases = [
            ("/v1/hello", 200, dict(proto=1, label="Phone", epoch=lease), "proto", [True, "1", 2]),
            ("/v1/status", 200, {"cancel": lease}, "cancel", [True, 1, "a" * 31, "AB" * 16]),
            ("/v1/work?wait=0", 200, work, "lease", [True, 1, "a" * 31, "AB" * 16]),
            (f"/v1/runs/{lease}/text", 200, {"acked": 1}, "acked", [True, "1", -1, 12289, 1.0]),
            (f"/v1/runs/{lease}/text", 409, {"stored": 1}, "stored", [True, "1", -1, 12289, 1.0]),
            (f"/v1/runs/{lease}/text", 413, {"stored": 1}, "stored", [True, "1", -1, 12289, 1.0]),
            ("/v1/status", 400, {"error": "state"}, "error", [True, 1, "unknown", "STATE", ""]),
        ]
        for target, status, valid, field, bad_values in cases:
            wrong = [{"wrong": None}, {}, {**valid, "extra": None}]
            wrong.append({key: value for key, value in valid.items() if key != field})
            wrong.extend({**valid, field: value} for value in bad_values)
            if target == "/v1/hello":
                wrong.extend(
                    {**valid, **change}
                    for change in (
                        {"epoch": "ab" * 15},
                        {"epoch": "AB" * 16},
                        {"epoch": True},
                        {"label": "A" * 25},
                        {"label": "bad_"},
                        {"label": True},
                    )
                )
            if target.startswith("/v1/work"):
                wrong.extend(
                    {**valid, **change}
                    for change in (
                        {"model_sha256": "cd" * 31},
                        {"model_sha256": "CD" * 32},
                        {"model_sha256": True},
                        {"question": True},
                        {"partial": 1},
                        {"question": "a" * 4097},
                        {"partial": "a" * 12289},
                        {"question": "\x85"},
                        {"partial": "\x7f"},
                    )
                )
            for index, value in enumerate(wrong):
                with self.subTest(target=target, status=status, case=index):
                    raw = json.dumps(value).encode()
                    if target == "/v1/hello":
                        prefix = f"HTTP/1.1 {status} OK\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw
                    else:
                        prefix = self.signed_fixture(status, raw)
                    pc = self.raw_reply(prefix, close=True)
                    pc.epoch = b"E" * 16
                    with self.assertRaises(standin.RetryError):
                        pc.send("GET", target, signed=target != "/v1/hello")

    def test_empty_work_reply_requires_empty_body_and_json_parse_failures_retry(self):
        for status, raw in ((204, b"{}"), (204, b'{"extra":1}'), (200, b"["), (200, b'{"cancel":null,}')):
            with self.subTest(status=status, raw=raw):
                pc = self.raw_reply(self.signed_fixture(status, raw), close=True)
                pc.epoch = b"E" * 16
                with self.assertRaises(standin.RetryError):
                    pc.send("GET", "/v1/work?wait=0")

    def test_malformed_reply_keeps_pending_offset_and_retries(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        self.drive(final=False)
        self.clock.advance(0.26)
        offset, source = self.runner.offset, self.runner.source
        original = self.link.handle

        def malformed(method, target, headers, body):
            if target.endswith("/text"):
                raw = proto.dumps({"acked": offset + 256 + 1})
                mac = proto.reply_mac(self.pc.key, bytes.fromhex(headers.get("X-Link-Nonce")), 200, "", raw)
                return Reply(200, (("X-Link-Mac", mac),), raw)
            return original(method, target, headers, body)

        with patch.object(self.link, "handle", side_effect=malformed):
            self.runner.step()
        self.assertEqual(self.runner.offset, offset)
        self.assertIs(self.runner.source, source)
        self.assertIsNotNone(self.runner.pending)
        self.clock.advance(1)
        self.assertEqual(self.drive()["text"], standin.PRACTICE)

    def test_reply_transport_checks_with_valid_signed_body(self):
        raw = b'{"cancel":null}'
        for kind in ("duplicate_mac", "body_limit", "incomplete", "gzip"):
            with self.subTest(kind=kind):
                body = raw + b" " * (65537 - len(raw)) if kind == "body_limit" else raw
                extra = b"Content-Encoding: gzip\r\n" if kind == "gzip" else b""
                if kind == "duplicate_mac":
                    base = self.signed_fixture(200, body)

                    def prefix(request, base=base):
                        reply = base(request)
                        mac_line = next(line for line in reply.split(b"\r\n") if line.startswith(b"X-Link-Mac:"))
                        return reply.replace(b"\r\n\r\n", b"\r\n" + mac_line + b"\r\n\r\n")
                else:
                    prefix = self.signed_fixture(200, body, extra, len(body) + 4 if kind == "incomplete" else None)
                pc = self.raw_reply(prefix, close=True)
                pc.epoch = b"E" * 16
                error = "reply body too large" if kind == "body_limit" else "."
                with self.assertRaisesRegex((standin.ToolError, OSError, http.client.HTTPException), error):
                    pc.status()

    def test_header_eof_is_retryable_and_retains_generation(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        self.drive(final=False)
        for head in (b"HTTP/1.1 200 OK\r\n", b"HTTP/1.1 200 OK\r\nConnection: close\r\n"):
            with self.subTest(head=head):
                pc = self.raw_reply(head, close=True)
                pc.epoch = self.pc.epoch
                self.runner.pc = pc
                self.runner.next_status = 0
                self.runner.retry_at = 0
                self.runner.step()
                self.assertIsNotNone(self.runner.job)
                self.assertIn("incomplete reply", self.lines[-1])
                self.clock.advance(31)

    def test_real_listener_refuses_wrong_enrol_host(self):
        request = self.pc.enrol_request().replace(b"Host: localhost", b"Host: phone.example")
        self.assertEqual(self.deliver_enrol_request(request).status, 400)
        self.assertIsNone(self.link.snapshot()["pending"])
        self.assertEqual(self.deliver_enrol_request(self.pc.enrol_request()).status, 202)

    def test_hello_bucket_and_closing_service_retry(self):
        self.enrolled()
        # The injected phone clock does not refill the real listener's hello bucket.
        replies = [self.pc.send("GET", "/v1/hello", signed=False)[0] for _ in range(11)]
        self.assertIn(429, replies)
        self.pc.epoch = None
        self.runner.step()
        self.assertIsNotNone(self.runner.failed_since)
        self.clock.advance(1.1)
        self.runner.step()
        self.assertIsNone(self.runner.failed_since)
        self.link.close()
        for operation in (self.runner._status, self.runner._work):
            with self.subTest(operation=operation.__name__), self.assertRaises(standin.RetryError):
                operation()
        self.runner.job = {"lease": "ab" * 16}
        self.runner.source = standin.PracticeSource({"partial": ""})
        with self.assertRaises(standin.RetryError):
            self.runner._post()

    def test_hello_5xx_retries(self):
        for status in (500, 503):
            with self.subTest(status=status), patch.object(self.pc, "send", return_value=(status, {"error": "state"})):
                with self.assertRaises(standin.RetryError):
                    self.pc.hello()

    def test_rollover_repeats_exact_post_keeps_source_and_lease(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        self.drive(final=False)
        source, job = self.runner.source, self.runner.job
        self.runner.next_status = self.clock() + 1
        self.clock.advance(0.26)
        posts, original = [], self.link.handle

        def record(method, target, headers, body):
            if target.endswith("/text"):
                posts.append((body, headers.get("X-Link-Nonce"), headers.get("X-Link-Mac")))
            return original(method, target, headers, body)

        with self.link._lock:
            self.link._nonces = {index.to_bytes(12, "big") for index in range(proto.NONCES_PER_PC)}
        with patch.object(self.link, "handle", side_effect=record):
            self.runner.step()
        self.assertIs(self.runner.source, source)
        self.assertIs(self.runner.job, job)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][0], posts[1][0])
        self.assertNotEqual(posts[0][1], posts[1][1])
        self.assertNotEqual(posts[0][2], posts[1][2])
        self.assertEqual(self.drive()["text"].encode(), source.data)

    def test_persistent_text_500_expires_despite_status_success(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        first = self.drive(final=False)
        self.runner.give_up_after = 65
        self.lines.clear()
        write = self.store.write_json
        failures = []

        def fail_answer(name, *args, **kwargs):
            if "answer" in str(name):
                failures.append(self.clock())
                raise OSError("fixture storage failure")
            return write(name, *args, **kwargs)

        with patch.object(self.store, "write_json", side_effect=fail_answer):
            for _ in range(65):
                self.clock.advance(1)
                self.runner.step()
            self.clock.advance(1)
            with (
                patch.object(standin, "StandIn", return_value=self.runner),
                patch.object(self.runner, "run", side_effect=lambda stop: self.runner.step()),
                contextlib.redirect_stderr(io.StringIO()) as error,
            ):
                result = standin.main(["--key-file", str(self.root / "pc.key")])
            self.assertEqual(result, 3)
            self.assertEqual(error.getvalue(), "Phone exchanges failed too long; stopped\n")
        self.assertGreater(len(failures), 60)
        self.assertEqual(self.lines.count("Phone server error; waiting to retry"), 3)
        self.assertEqual(self.answers.get(first["id"])["bytes"], first["bytes"])

    def test_offset_reconciliation_keeps_post_failure_window_until_ack(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        self.drive(final=False)
        with patch.object(self.runner, "_exchange", side_effect=standin.RetryError("fixture")):
            with self.assertRaises(standin.RetryError):
                self.runner._post()
        failed_since = self.runner.post_failed_since
        with patch.object(self.runner, "_exchange", return_value=(409, {"stored": 0})):
            self.runner._post()
        self.assertEqual(self.runner.post_failed_since, failed_since)
        self.assertEqual(self.drive()["text"], standin.PRACTICE)
        self.assertIsNone(self.runner.post_failed_since)

    def enrol_command(self):
        return [
            sys.executable,
            "-B",
            str(ROOT / "tools/gpu-link-standin-pc.py"),
            "--key-file",
            str(self.root / "unused"),
            "--enrol-request",
        ]

    def test_enrol_stdout_regular_file_exit_two(self):
        with (self.root / "request").open("wb") as output:
            result = subprocess.run(self.enrol_command(), stdout=output, stderr=subprocess.PIPE, timeout=5, check=False)
        self.assertEqual((result.returncode, result.stderr), (2, ENROL_ERROR))
        self.assertEqual((self.root / "request").read_bytes(), b"")
        self.assertFalse((self.root / "unused").exists())

    @unittest.skipUnless(os.name == "posix", "closing stdout before interpreter startup needs POSIX")
    def test_enrol_closed_stdout_exit_two(self):
        result = subprocess.run(
            self.enrol_command(), preexec_fn=lambda: os.close(1), capture_output=True, timeout=5, check=False
        )
        self.assertEqual((result.returncode, result.stderr), (2, ENROL_ERROR))
        self.assertFalse((self.root / "unused").exists())

    def test_enrol_broken_pipe_exit_two_without_shutdown_flush_error(self):
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        with os.fdopen(write_fd, "wb") as output:
            result = subprocess.run(self.enrol_command(), stdout=output, stderr=subprocess.PIPE, timeout=5, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.endswith(ENROL_ERROR))
        self.assertNotIn(b"Exception ignored", result.stderr)
        if (self.root / "unused").exists():
            self.assertNotIn((self.root / "unused").read_bytes().hex().encode(), result.stderr)

    def test_enrol_stdout_socket_and_fstat_fallback_and_help(self):
        left, right = socket.socketpair()
        with left, right, left.makefile("w") as output:
            error = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertEqual(
                    standin.main(
                        [
                            "--key-file",
                            str(self.root / "socket.key"),
                            "--enrol-request",
                        ]
                    ),
                    0,
                )
            self.assertTrue(right.recv(2048).startswith(b"POST /v1/enrol"))
        output, error = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
            patch.object(standin.os, "fstat", side_effect=OSError),
            patch.object(standin, "key_file", return_value=self.pc.key),
        ):
            # An unavailable descriptor type keeps the non-terminal fallback.
            with patch.object(output, "buffer", io.BytesIO(), create=True):
                self.assertEqual(
                    standin.main(
                        [
                            "--key-file",
                            str(self.root / "unused"),
                            "--enrol-request",
                        ]
                    ),
                    0,
                )
        with contextlib.redirect_stdout(io.StringIO()) as help_text, self.assertRaises(SystemExit):
            standin.arguments(["--help"])
        self.assertIn("pipe or socket", help_text.getvalue())

    def test_readme_documents_resume_count_and_warm_server(self):
        readme = (ROOT / "tools/README.md").read_text()
        self.assertIn("token count shown on the phone covers text produced\nsince the resume", readme)
        self.assertIn("Keep the chat-completions server warm", readme)
        self.assertIn("30 seconds for its first bytes", readme)

    def test_reply_total_deadline_covers_status_headers_and_body(self):
        for phase, prefix, tail in (
            ("status", b"", b"HTTP/1"),
            ("headers", b"HTTP/1.1 200 OK\r\n", b"X-Slow"),
            ("body", b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\n", b"{}    "),
        ):
            with self.subTest(phase=phase):
                pc = self.raw_reply(prefix, tail, 0.3)
                start = time.monotonic()
                with self.assertRaises((OSError, http.client.HTTPException)):
                    pc.send("GET", "/v1/hello", signed=False)
                self.assertLess(time.monotonic() - start, pc.request_limit + 0.4)

    def test_reply_header_limits_apply_before_terminator(self):
        for headers in (b"X-Large: " + b"a" * 8001, b"X: a\r\n" * 41):
            with self.subTest(size=len(headers)):
                pc = self.raw_reply(b"HTTP/1.1 200 OK\r\n" + headers)
                with self.assertRaisesRegex(standin.ToolError, "headers too large"):
                    pc.send("GET", "/v1/hello", signed=False)

    def test_retry_window_gives_up_and_throttles_with_injected_clock(self):
        self.runner.give_up_after = 65
        with patch.object(self.pc, "hello", side_effect=OSError):
            for _ in range(65):
                self.runner.step()
                self.clock.advance(1)
            with self.assertRaises(standin.GiveUpError):
                self.runner.step()
        self.assertEqual(self.lines, ["Phone unreachable; waiting to retry"] * 3)
        self.assertIsNone(self.runner.source)

    def test_successful_exchange_resets_retry_window_and_zero_never_gives_up(self):
        self.enrolled()
        self.runner.give_up_after = 2
        with patch.object(self.pc, "send", side_effect=OSError):
            self.runner.step()
        self.clock.advance(1.1)
        self.runner.step()  # Successful signed status and work exchanges reset the window.
        with patch.object(self.pc, "send", side_effect=OSError):
            self.clock.advance(1.1)
            self.runner.step()
            self.clock.advance(1.1)
            self.runner.step()  # More than 2 seconds since the first, but not consecutive.
            self.runner.give_up_after = 0
            self.clock.advance(10000)
            self.runner.step()
        self.assertEqual(self.lines.count("Phone unreachable; waiting to retry"), 3)

    def test_retry_messages_stay_throttled_when_only_hello_succeeds(self):
        self.runner.give_up_after = 0

        def send(method, target, body=b"", signed=True):
            if not signed:
                return 200, dict(proto=1, label="Phone", epoch="11" * 16)
            raise OSError

        with patch.object(self.pc, "send", side_effect=send):
            for _ in range(31):
                self.runner.step()
                self.clock.advance(1)
        self.assertEqual(self.lines, ["Hello accepted"] + ["Phone unreachable; waiting to retry"] * 2)

    def test_unenrolled_key_stops_normal_run(self):
        path = self.root / "pc.key"
        path.write_bytes(self.pc.key)
        path.chmod(0o600)
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            result = standin.main(["--key-file", str(path), "--phone", self.phone])
        self.assertEqual(result, 1)
        self.assertIn("key not accepted", error.getvalue())
        self.assertNotIn(self.pc.key.hex(), output.getvalue() + error.getvalue())

    def test_give_up_exit_status_and_option_validation(self):
        base = ["--key-file", str(self.root / "pc.key")]
        self.assertEqual(standin.arguments(base).give_up_after, 300)
        self.assertEqual(standin.arguments([*base, "--give-up-after", "0"]).give_up_after, 0)
        for value in ("-1", "nan", "inf"):
            with self.assertRaises(standin.ToolError):
                standin.arguments([*base, "--give-up-after", value])
        output, error = io.StringIO(), io.StringIO()
        with (
            patch.object(
                standin.StandIn, "run", side_effect=standin.GiveUpError("Phone exchanges failed too long; stopped")
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
        ):
            result = standin.main(base)
        self.assertEqual(result, 3)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(error.getvalue(), "Phone exchanges failed too long; stopped\n")

    def test_signed_500_retries_identical_text_with_new_nonce(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        first = self.drive(final=False)
        lease = self.runner.job["lease"]

        class GrowingSource:
            data = first["text"].encode() + b"next"
            done = False

            def snapshot(self):
                return self.data, self.done, "stop", None, False

            def close(self):
                pass

        self.runner.source.close()
        self.runner.source = GrowingSource()
        posts, sources = [], []
        handle, write = self.link.handle, self.store.write_json
        failed = False

        def fail_once(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("fixture write failure")
            return write(*args, **kwargs)

        def record(method, target, headers, body):
            if target.endswith("/text"):
                posts.append((target, headers.get("X-Link-Nonce"), body))
                sources.append(self.runner.source)
            return handle(method, target, headers, body)

        self.runner._status()
        self.clock.advance(0.26)  # The status is not due, so the failure is in the text write.
        with patch.object(self.link, "handle", side_effect=record), patch.object(self.store, "write_json", fail_once):
            self.runner.step()
            self.assertTrue(failed)
            self.assertEqual(self.answers.get(first["id"])["bytes"], first["bytes"])
            self.assertEqual(self.runner.job["lease"], lease)
            self.assertEqual(self.runner.offset, first["bytes"])
            # Newly available source bytes must not change the pending post.
            self.runner.source.data += b" extra"
            self.clock.advance(1)
            self.runner.step()
            self.assertGreaterEqual(len(posts), 2)
            self.assertEqual(posts[0][0], posts[1][0])
            self.assertEqual(posts[0][2], posts[1][2])
            self.assertNotEqual(posts[0][1], posts[1][1])
            self.assertIs(sources[0], sources[1])
            self.runner.source.done = True
            self.assertEqual(self.drive()["text"], first["text"] + "next extra")

    def test_signed_500_status_and_work_share_retry_window(self):
        self.enrolled()
        for target in ("/v1/status", "/v1/work?wait=0"):
            with self.subTest(target=target):
                self.runner.close()
                self.runner.failed_since = None
                self.runner.retry_at = 0
                self.runner.next_status = self.clock() + 100 if target.startswith("/v1/work") else 0
                self.runner.next_poll = 0
                self.runner.give_up_after = 2
                self.lines.clear()
                original = self.link.handle

                def fail(method, path, headers, body, target=target, original=original):
                    if path != target:
                        return original(method, path, headers, body)
                    raw = proto.error_reply("state")
                    mac = proto.reply_mac(self.pc.key, bytes.fromhex(headers.get("X-Link-Nonce")), 500, "", raw)
                    return Reply(500, (("X-Link-Mac", mac),), raw)

                with patch.object(self.link, "handle", side_effect=fail):
                    self.runner.step()
                    self.clock.advance(1)
                    self.runner.step()
                    self.clock.advance(1)
                    with self.assertRaises(standin.GiveUpError):
                        self.runner.step()
                self.assertEqual(self.lines, ["Phone server error; waiting to retry"])
                self.clock.advance(1)
                self.runner.step()
                self.assertIsNone(self.runner.failed_since)

    def test_resumed_server_speed_counts_only_new_text(self):
        job = dict(partial="kept " * 1000, lease="11" * 16, question="question")
        with patch.object(standin.ServerSource, "_run"):
            source = standin.ServerSource(job, "http://127.0.0.1:1/v1/chat/completions", "fixture")
        self.addCleanup(source.close)
        source.worker.join(2)
        self.assertEqual(source.snapshot()[3], 0)
        source._event({"choices": [{"delta": {"content": "new"}}]})
        self.runner.job, self.runner.source = job, source
        self.runner.started = self.clock()
        self.clock.advance(1)
        with patch.object(self.pc, "status", return_value=(200, {"cancel": None})) as status:
            self.runner._status()
        self.assertEqual(status.call_args.args[1:3], (1, 1.0))
        source._event({"usage": {"completion_tokens": 7}})
        self.assertEqual(source.snapshot()[3], 7)

    def test_restart_with_held_job_discards_source_and_resumes(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        first = self.drive(final=False)
        old_lease, old_source = self.runner.job["lease"], self.runner.source
        self.link.close()
        self.answers = AnswerStore(self.store)
        self.link = LinkService(self.answers, self.store, self.model, clock=self.clock)
        self.addCleanup(self.link.close)
        if self.link._hasher:
            self.link._hasher.join(3)
        self.listener.link = self.link
        self.clock.advance(1.1)
        self.runner.step()
        self.assertIn("Lease ended; polling again", self.lines)
        self.assertNotEqual(self.runner.job["lease"], old_lease)
        self.assertIsNot(self.runner.source, old_source)
        self.assertEqual(self.runner.job["partial"], first["text"])
        self.assertEqual(self.drive()["text"], standin.PRACTICE)

    def test_repeated_epoch_changes_back_off_throttle_and_give_up(self):
        requests, hellos = [], []
        self.runner.give_up_after = 35

        def send(method, target, body=b"", signed=True):
            if not signed:
                hellos.append(self.clock())
                return 200, dict(proto=1, label="Phone", epoch=len(hellos).to_bytes(16, "big").hex())
            requests.append(self.clock())
            raise standin.RejectedError("signed request refused")

        clock = self.clock
        deadline = clock() + 40
        waits = []

        class Stop:
            def is_set(self):
                return clock() >= deadline

            def wait(self, seconds):
                waits.append(seconds)
                clock.advance(seconds)

        with patch.object(self.pc, "send", side_effect=send), self.assertRaises(standin.GiveUpError):
            self.runner.run(Stop())
        self.assertTrue(all(wait >= 1 for wait in waits))
        self.assertEqual(requests[::2], requests[1::2])
        self.assertTrue(all(b - a >= 1 for a, b in zip(requests[::2], requests[2::2], strict=False)))
        self.assertEqual(self.lines, ["Hello accepted"] + ["Phone epoch renewed; waiting to retry"] * 2)
        self.assertLessEqual(len(requests), 72)

    def test_nonloopback_options_refused_before_key_or_connection(self):
        base = ["--key-file", str(self.root / "unused")]
        for option, value in (
            ("--phone", "192.0.2.1:8767"),
            ("--phone", "example.test:8767"),
            ("--phone", "127.0.0.1:0"),
            ("--server", "http://192.0.2.1/v1/chat/completions"),
            ("--server", "http://localhost.example.test/v1/chat/completions"),
            ("--server", "http://user:secret@localhost/v1/chat/completions"),
        ):
            with (
                self.subTest(option=option, value=value),
                patch.object(http.client.HTTPConnection, "connect") as connect,
            ):
                with contextlib.redirect_stderr(io.StringIO()) as output:
                    self.assertEqual(standin.main([*base, option, value]), 1)
                connect.assert_not_called()
                self.assertNotIn("secret", output.getvalue())
                self.assertFalse((self.root / "unused").exists())
        for rate in ("0", "-1", "4.01", "nan", "inf"):
            with self.assertRaises(standin.ToolError):
                standin.arguments([*base, "--rate", rate])
        with self.assertRaises(standin.ToolError):
            standin.arguments([*base, "--label", "bad_label"])

    def chat_stub(self, stalled=False, redirect=False, body=None):
        """Serve SSE fixtures only; never run a model or an external service."""
        requests, release, entered = [], threading.Event(), threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(302 if redirect else 200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                entered.set()
                if stalled:
                    release.wait(5)
                    return
                if redirect:
                    return
                if body is not None:
                    self.wfile.write(body)
                    return
                for text in ("Hello é", " 🌿\r\x00\x85\ud800\n", "world"):
                    data = json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]})
                    self.wfile.write(("data: " + data + "\r\n\r\n").encode())
                    self.wfile.flush()
                self.wfile.write(
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                    b'"usage":{"completion_tokens":7}}\n\ndata: [DONE]\n\n'
                )

            def log_message(self, *args):
                """Fixture requests never log their content."""

        stub = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=stub.serve_forever)
        worker.start()

        def close():
            release.set()
            stub.shutdown()
            stub.server_close()
            worker.join(3)
            self.assertFalse(worker.is_alive())

        self.addCleanup(close)
        url = f"http://127.0.0.1:{stub.server_port}/v1/chat/completions"
        return url, requests, entered, release

    def test_server_stream_cleans_text_usage_and_resume_prefix(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "PRIVATE QUESTION")
        self.answers.begin(answer["id"])
        self.answers.append(answer["id"], 0, "Saved é ".encode())
        self.answers.pause(answer["id"])
        url, requests, _, _ = self.chat_stub()
        self.runner.source_factory = lambda job: standin.ServerSource(job, url, "fixture-model")
        result = self.drive()
        self.assertEqual(result["text"], "Saved é Hello é 🌿\nworld")
        self.assertEqual(result["tokens"], 7)
        self.assertEqual(
            requests[0]["messages"],
            [
                {"role": "user", "content": "PRIVATE QUESTION"},
                {"role": "assistant", "content": "Saved é "},
            ],
        )
        self.assertNotIn("PRIVATE QUESTION", "\n".join(self.lines))
        self.assertNotIn("Hello", "\n".join(self.lines))
        self.assertFalse(any(t.name == "standin-stream" for t in threading.enumerate()))

    def test_stalled_server_keeps_status_and_cancel_closes_reader(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        url, _, entered, release = self.chat_stub(stalled=True)
        self.runner.source_factory = lambda job: standin.ServerSource(job, url, "fixture-model")
        self.runner.step()
        self.assertTrue(entered.wait(2))
        source = self.runner.source
        for _ in range(4):
            self.clock.advance(1)
            self.runner.step()
            self.link.tick()
            self.assertTrue(self.link.snapshot()["pc"]["live"])
        self.link.cancel(answer["id"])
        self.clock.advance(1)
        self.runner.step()
        release.set()
        self.assertFalse(source.worker.is_alive())
        self.assertIsNone(self.runner.source)

    def test_broken_stream_reports_failure_and_preserves_stored_prefix(self):
        self.assert_failed_stream_keeps_tail(
            b'data: {"error":{"message":"failed"}}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        )

    def test_stop_without_marker_fails_after_flushing_tail(self):
        self.assert_failed_stream_keeps_tail(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')

    def assert_failed_stream_keeps_tail(self, ending):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        self.answers.begin(answer["id"])
        self.answers.append(answer["id"], 0, b"Saved prefix")
        self.answers.pause(answer["id"])
        tail = "more é🌿 " * 300
        content = proto.dumps(dict(choices=[dict(delta=dict(content=tail))]))
        url, _, _, _ = self.chat_stub(body=b"data: " + content + b"\n\n" + ending)

        def source(job):
            reader = standin.ServerSource(job, url, "fixture-model")
            reader.worker.join(2)
            self.assertFalse(reader.worker.is_alive())
            return reader

        self.runner.source_factory = source
        send = self.pc.send
        posts = []

        def record(method, target, body=b"", **kwargs):
            if target.endswith("/text"):
                posts.append(json.loads(body))
            return send(method, target, body, **kwargs)

        with patch.object(self.pc, "send", side_effect=record):
            result = self.drive()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["text"], "Saved prefix" + tail)
        self.assertFalse(any(post["done"] for post in posts))
        self.assertIn("Answer stopped: chat-completions server error", self.lines)
        self.assertEqual(result["error"], "server")
        self.assertEqual(self.answers.get(answer["id"])["text"], result["text"])

    def test_server_redirect_fails_answer_without_following(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        url, requests, _, _ = self.chat_stub(redirect=True)
        self.runner.source_factory = lambda job: standin.ServerSource(job, url, "fixture-model")
        self.assertEqual(self.drive()["state"], "failed")
        self.assertEqual(len(requests), 1)


class ServerStreamTests(unittest.TestCase):
    """Exercise stream completion and per-read deadlines without a real model."""

    def source(self, body, trickle=None, limit=300, headers=None, from_main=False, read_delay=0):
        clock = Clock()
        started = clock()
        self.socket_timeouts = []
        self.requests = []
        timeouts, requests = self.socket_timeouts, self.requests

        class Trickle(io.RawIOBase):
            reads = 0

            def readable(self):
                return True

            def readinto(self, buffer):
                clock.advance(read_delay)
                chunk = raw.read(len(buffer))
                if not chunk and trickle is not None and self.reads < 100:
                    self.reads += 1
                    clock.advance(0.1)
                    chunk = trickle
                buffer[: len(chunk)] = chunk
                return len(chunk)

        class Socket:
            def makefile(self, mode, buffering=-1):
                stream = Trickle()
                return stream if buffering == 0 else io.BufferedReader(stream)

            def settimeout(self, value):
                timeouts.append((clock() - started, value))

            def shutdown(self, how):
                pass

        class Connection:
            sock = Socket()
            response_class = http.client.HTTPResponse

            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                pass

            def request(self, *args):
                requests.append(args)

            def getresponse(self):
                response = self.response_class(self.sock)
                response.begin()
                return response

            def close(self):
                pass

        if headers is None:
            headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
        raw = io.BytesIO(headers + body)
        job = dict(question="question", partial="Saved ")
        source = None

        def build(runner, stop):
            nonlocal source
            source = runner.source_factory(job)
            source.worker.join(2)

        with (
            patch.object(standin.http.client, "HTTPConnection", Connection),
            patch.object(standin.time, "monotonic", clock),
        ):
            if from_main:
                with (
                    patch.object(standin, "key_file", return_value=b"K" * 32),
                    patch.object(standin.StandIn, "run", build),
                ):
                    result = standin.main(
                        [
                            "--key-file",
                            "unused",
                            "--text",
                            "server",
                            "--server",
                            "http://127.0.0.1:1/chat",
                            "--model",
                            "fixture",
                            "--generation-timeout",
                            str(limit),
                        ]
                    )
                self.assertEqual(result, 0)
            else:
                source = standin.ServerSource(job, "http://127.0.0.1:1/chat", "fixture", limit)
            self.addCleanup(source.close)
            source.worker.join(2)
            self.assertFalse(source.worker.is_alive())
        return source, clock() - started

    def test_main_generation_timeout_ends_trickle(self):
        source, elapsed = self.source(b"", trickle=b": keep-alive\n\n", limit=0.25, from_main=True)
        self.assertTrue(source.snapshot()[4])
        self.assertAlmostEqual(elapsed, 0.3)
        self.assertEqual(self.socket_timeouts[0], (0, 0.25))
        method, target, payload, _ = self.requests[0]
        self.assertEqual((method, target), ("POST", "/chat"))
        self.assertEqual(json.loads(payload)["model"], "fixture")

    def test_main_refuses_invalid_generation_timeout_before_key_access(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with (
                self.subTest(value=value),
                patch.object(standin, "key_file", return_value=b"K" * 32) as key,
                patch.object(standin.StandIn, "run"),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(standin.main(["--key-file", "unused", "--generation-timeout=" + value]), 1)
                key.assert_not_called()

    def test_socket_timeouts_use_inactivity_and_remaining_deadline(self):
        source, _ = self.source(b"", trickle=b": keep-alive\n\n", limit=35, read_delay=7)
        self.assertTrue(source.snapshot()[4])
        self.assertEqual(self.socket_timeouts[0], (0, 30))
        self.assertGreater(len(self.socket_timeouts), 4)
        self.assertTrue(any(elapsed > 5 for elapsed, _ in self.socket_timeouts))
        for elapsed, timeout in self.socket_timeouts[1:]:
            self.assertAlmostEqual(timeout, min(30, 35 - elapsed))

    def test_recorded_llama_streams_complete(self):
        for name in ("user", "assistant-continuation"):
            with self.subTest(stream=name):
                body = (ROOT / "tests/fixtures/gpu_link" / f"llama-{name}.sse").read_bytes()
                values = [
                    json.loads(line[5:])
                    for line in body.splitlines()
                    if line.startswith(b"data:") and line[5:].strip() != b"[DONE]"
                ]
                text = "".join(
                    v.get("choices", [{}])[0].get("delta", {}).get("content") or "" for v in values if v.get("choices")
                )
                tokens = next(v["usage"]["completion_tokens"] for v in values if "usage" in v)
                source, _ = self.source(body)
                self.assertEqual(source.snapshot(), (b"Saved " + text.encode(), True, "stop", tokens, False))

    def assert_failed(self, ending):
        source, _ = self.source(b'data: {"choices":[{"delta":{"content":"received"}}]}\n\n' + ending)
        data, done, _, _, error = source.snapshot()
        self.assertEqual(data, b"Saved received")
        self.assertTrue(done)
        self.assertTrue(error, "broken stream must enter the server-error path")

    def test_error_chunk_mid_stream(self):
        self.assert_failed(
            b'data: {"error":{"message":"failed"}}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        )

    def test_stream_cut_after_stop_without_marker(self):
        self.assert_failed(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')

    def test_non_normal_finish_reason(self):
        for reason in ("error", "content_filter", "tool_calls"):
            with self.subTest(reason=reason):
                self.assert_failed(
                    b'data: {"choices":[{"delta":{},"finish_reason":"' + reason.encode() + b'"}]}\n\ndata: [DONE]\n\n'
                )

    def test_invalid_json_chunk(self):
        self.assert_failed(b"data: {invalid json}\n\ndata: [DONE]\n\n")

    def test_marker_without_stop(self):
        self.assert_failed(b"data: [DONE]\n\n")

    def test_deep_json_worker_failure(self):
        self.assert_failed(b"data: " + b"[" * 15000 + b"0" + b"]" * 15000 + b"\n\n")

    def test_unexpected_parser_failure(self):
        with patch.object(standin.ServerSource, "_read", side_effect=RuntimeError("parser failed")):
            source, _ = self.source(b"data: [DONE]\n\n")
        self.assertTrue(source.snapshot()[1])
        self.assertTrue(source.snapshot()[4])

    def test_explicit_error_event(self):
        self.assert_failed(
            b'event: error\ndata: {}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        )

    def test_normal_stream_completes(self):
        source, _ = self.source(
            b'data: {"choices":[{"delta":{"content":"received"}}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: {"usage":{"completion_tokens":7}}\n\ndata: [DONE]\n\n'
        )
        self.assertEqual(source.snapshot(), (b"Saved received", True, "stop", 7, False))

    def test_length_finish_reason_completes_as_length(self):
        source, _ = self.source(
            b'data: {"choices":[{"delta":{"content":"received"}}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n'
        )
        self.assertEqual(source.snapshot(), (b"Saved received", True, "length", 1, False))

    def test_answer_at_the_phone_size_limit_ends_as_length(self):
        piece = b"x" * (standin.proto.MAX_ANSWER_BYTES + 100)
        source, _ = self.source(b'data: {"choices":[{"delta":{"content":"' + piece + b'"}}]}\n\n')
        data, done, finish, _, error = source.snapshot()
        self.assertEqual(len(data), standin.proto.MAX_ANSWER_BYTES)
        self.assertEqual((done, finish, error), (True, "length", False))

    def test_total_bound_interrupts_trickling_reads(self):
        for trickle in (
            b": keep-alive\n\n",
            b'data: {"choices":[{"delta":{"reasoning":"x"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
            b":",
        ):
            with self.subTest(trickle=trickle):
                source, elapsed = self.source(b"", trickle=trickle, limit=0.25)
                self.assertTrue(source.snapshot()[4])
                self.assertLessEqual(elapsed, 0.31)
                self.assertTrue(source.snapshot()[0].startswith(b"Saved "))

    def test_total_bound_interrupts_trickling_headers(self):
        source, elapsed = self.source(b"", trickle=b"x", limit=0.25, headers=b"HTTP/1.1 200 OK\r\nX: ")
        self.assertTrue(source.snapshot()[4])
        self.assertLessEqual(elapsed, 0.31)

    def test_generation_timeout_option(self):
        base = ["--key-file", "unused"]
        self.assertEqual(standin.arguments(base).generation_timeout, 300)
        self.assertEqual(standin.arguments([*base, "--generation-timeout", "0.25"]).generation_timeout, 0.25)
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(standin.ToolError):
                standin.arguments([*base, "--generation-timeout", value])


class ErrorFlushTests(unittest.TestCase):
    """A finished reader can outrun phone posting; preserve its bounded backlog."""

    def exercise(self, source, refusal=None, pending=False, prefix=b"Saved ", ahead=b"", show_text=False, runner=None):
        """Drive one failed job. `ahead` is text the phone already stored whose acknowledgement the tool never saw;
        `runner` reuses one StandIn for a second job. The StandIn is kept in self.runner."""
        events = []
        if runner is None:
            runner = standin.StandIn(None, emit=print, clock=Clock())
            self.addCleanup(runner.close)
            runner.pc = type("PC", (), {"epoch": b"fixture"})()
        self.runner, clock = runner, runner.clock
        runner.emit, runner.show_text = (lambda line: events.append(("line", line))), show_text
        runner.job = dict(lease="fixture", partial=prefix.decode())
        runner.source, runner.offset = source, len(prefix)
        stored = bytearray(prefix + ahead)
        self.assertEqual(source.snapshot()[0][len(prefix) : len(prefix) + len(ahead)], ahead)
        posts, statuses = [], []

        def status(lease=None, tokens=None, tps=None, error=None):
            statuses.append((clock(), error))
            events.append(("status", error))
            return 200, {}

        def exchange(method, target, raw):
            post = json.loads(raw)
            posts.append((clock(), post))
            events.append(("post", post))
            if refusal:
                if isinstance(refusal, Exception):
                    raise refusal
                if refusal == 200:
                    return 200, dict(acked=1)
                return refusal, {"stored": len(stored)} if refusal in (409, 413) else {}
            offset, text = post["offset"], post["text"].encode()
            if offset == len(stored):
                stored.extend(text)
                return 200, dict(acked=len(stored))
            if offset < len(stored) and stored[offset : offset + len(text)] == text:  # a repeat inside the stored text
                return 200, dict(acked=offset + len(text))
            return 409, dict(stored=len(stored))  # the phone's rule for a longer piece that starts inside its text

        if pending:
            piece = ahead or standin.cut(source.snapshot()[0][runner.offset :], 256)
            runner.pending = (
                proto.dumps(dict(offset=runner.offset, text=piece.decode(), done=False, finish=None)),
                piece,
                False,
            )
        with (
            patch.object(runner, "_send_status", side_effect=status),
            patch.object(runner.pc, "send", side_effect=exchange, create=True),
            patch.object(runner, "_work"),
        ):
            for _ in range(1000):
                runner.step()
                if runner.job is None:
                    break
                clock.advance(0.05)
            else:
                self.fail("error flush did not terminate")
        self.assertFalse(any(post["done"] for _, post in posts))
        self.assertTrue(all(post["finish"] is None for _, post in posts))
        self.assertTrue(all(0 < len(post["text"].encode()) <= 256 for _, post in posts))
        self.assertTrue(all(b[0] - a[0] >= 0.25 - 1e-7 for a, b in zip(posts, posts[1:], strict=False)))
        self.assertLessEqual(len(posts), 49)
        return bytes(stored), posts, statuses, events

    def failed_source(self, ending, tail="é🌿 " * 600):
        streams = ServerStreamTests()
        self.addCleanup(streams.doCleanups)
        content = proto.dumps(dict(choices=[dict(delta=dict(content=tail))]))
        source, _ = streams.source(b"data: " + content + b"\n\n" + ending)
        self.assertTrue(source.snapshot()[4])
        return source

    def test_buffered_tail_precedes_failure_without_done(self):
        for ending in (
            b'data: {"error":{"message":"failed"}}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"content_filter"}]}\n\ndata: [DONE]\n\n',
        ):
            for pending in (False, True):
                with self.subTest(ending=ending, pending=pending):
                    source = self.failed_source(ending)
                    stored, _, statuses, events = self.exercise(source, pending=pending)
                    self.assertEqual(stored, source.snapshot()[0])
                    self.assertEqual(statuses[-1][1], "server")
                    self.assertGreater(sum(error is None for _, error in statuses), 1)
                    self.assertEqual(
                        events[-2:], [("status", "server"), ("line", "Answer stopped: chat-completions server error")]
                    )

    def test_stop_without_done_keeps_all_buffered_text_but_fails(self):
        source = self.failed_source(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')
        stored, _, statuses, _ = self.exercise(source)
        self.assertEqual(stored, source.snapshot()[0])
        self.assertEqual(statuses[-1][1], "server")

    def test_error_flush_stops_at_first_refusal_or_lease_loss(self):
        for refusal in (200, 400, 404, 409, 413, 500, standin.RejectedError(), OSError("connection lost")):
            with self.subTest(refusal=refusal):
                source = self.failed_source(b"data: {}\n\n")
                stored, posts, statuses, events = self.exercise(source, refusal=refusal)
                self.assertEqual(len(posts), 1)
                self.assertEqual(stored, b"Saved ")
                if refusal != 404 and not isinstance(refusal, OSError):
                    self.assertEqual(statuses[-1][1], "server")
                if refusal == 404:
                    # The lease is gone: the job is closed and no failure status follows.
                    self.assertIsNone(self.runner.job)
                    self.assertFalse(any(error for _, error in statuses))
                    self.assertIn(("line", "Lease ended; polling again"), events)

    def test_error_flush_recovers_after_a_lost_acknowledgement(self):
        # The phone stored a piece whose acknowledgement never arrived, so the tool's offset is behind the phone's and
        # the first fresh 256-byte flush piece is a longer piece that starts inside the stored text (409 with `stored`).
        data = self.failed_source(b"data: {}\n\n").snapshot()[0]
        for length in (7, 100, 255, len(data) - 6):  # whole characters are 7 bytes ("é🌿 "), so 100 and 255 are cut
            for pending in (False, True):
                with self.subTest(ahead=length, pending=pending):
                    source = self.failed_source(b"data: {}\n\n")
                    ahead = standin.cut(data[6 : 6 + length], length)
                    stored, posts, statuses, events = self.exercise(source, ahead=ahead, pending=pending)
                    self.assertEqual(stored, data)
                    self.assertEqual(statuses[-1][1], "server")
                    self.assertEqual(posts[0][1]["offset"], 6)
                    if len(ahead) < len(standin.cut(data[6:], 256)):  # shorter: the phone answers 409 with its length
                        self.assertEqual(posts[1][1]["offset"], 6 + len(ahead))
                        self.assertIn(("line", f"Offset recovered; stored {6 + len(ahead)} bytes"), events)

    def test_error_flush_ignores_a_stored_length_inside_a_character_or_past_the_buffer(self):
        for stored_length in (7, 10**6):  # byte 7 is inside the two-byte first character after "Saved "
            with self.subTest(stored=stored_length):
                source = self.failed_source(b"data: {}\n\n")
                runner = standin.StandIn(None, emit=lambda line: None, clock=Clock())
                self.addCleanup(runner.close)
                runner.pc = Mock(epoch=b"fixture")
                runner.pc.send.return_value = (409, dict(stored=stored_length))
                runner.job, runner.source, runner.offset = dict(lease="fixture", partial="Saved "), source, 6
                runner._post_error_tail(source.snapshot()[0])
                self.assertEqual((runner.offset, runner.error_flush_stopped, runner.pc.send.call_count), (6, True, 1))

    def test_flush_state_is_reset_for_the_next_job(self):
        first = self.failed_source(b"data: {}\n\n")
        _, posts, statuses, _ = self.exercise(first, refusal=500)  # the flush is refused, then the failure is sent
        self.assertEqual((len(posts), statuses[-1][1]), (1, "server"))
        runner = self.runner
        self.assertIsNone(runner.job)
        self.assertEqual((runner.error_posts, runner.error_flush_stopped), (0, False))
        second = self.failed_source(b"data: {}\n\n")
        stored, posts, _, _ = self.exercise(second, runner=runner)
        self.assertEqual(stored, second.snapshot()[0])
        self.assertGreater(len(posts), 1)

    def test_flush_keeps_answer_text_out_of_event_lines_unless_asked(self):
        for show_text in (False, True):
            with self.subTest(show_text=show_text):
                source = self.failed_source(b"data: {}\n\n")
                _, posts, _, events = self.exercise(source, show_text=show_text)
                lines = [line for kind, line in events if kind == "line" and line.startswith("Text: ")]
                self.assertEqual(len(lines), len(posts) if show_text else 0)

    def test_flush_acknowledgement_clears_the_post_failure_window(self):
        source = self.failed_source(b"data: {}\n\n")
        data = source.snapshot()[0]
        piece = standin.cut(data[6:], 256)
        runner = standin.StandIn(None, emit=lambda line: None, clock=Clock())
        self.addCleanup(runner.close)
        runner.pc = Mock(epoch=b"fixture")
        runner.pc.send.return_value = (200, dict(acked=6 + len(piece)))
        runner.job, runner.source, runner.offset = dict(lease="fixture", partial="Saved "), source, 6
        runner.post_failed_since, runner.last_failure_line = 5.0, 7.0
        runner._post_error_tail(data)
        self.assertEqual((runner.post_failed_since, runner.last_failure_line), (None, None))
        self.assertEqual((runner.offset, runner.error_flush_stopped), (6 + len(piece), False))

    def test_maximum_utf8_backlog_fits_49_posts(self):
        source = self.failed_source(b"data: {}\n\n")
        data = b"a" + "🌿".encode() * 3071 + b"xyz"
        self.assertEqual(len(data), proto.MAX_ANSWER_BYTES)
        with patch.object(source, "snapshot", return_value=(data, True, "stop", 0, True)):
            stored, posts, statuses, _ = self.exercise(source, prefix=b"")
        self.assertEqual(stored, data)
        self.assertEqual(len(posts), 49)
        self.assertEqual(statuses[-1][1], "server")


class DiagnosticTests(unittest.TestCase):
    def test_show_question_on_legacy_stdout(self):
        question = "A question with 🌿"
        for encoding in ("cp1252", "cp437"):
            with self.subTest(encoding=encoding), io.TextIOWrapper(io.BytesIO(), encoding=encoding) as output:
                runner = standin.StandIn(None, show_text=True)
                self.addCleanup(runner.close)
                with (
                    contextlib.redirect_stdout(output),
                    patch.object(runner, "_exchange", return_value=(200, dict(question=question, partial=""))),
                ):
                    runner._work()
                output.flush()
                line = output.buffer.getvalue().decode(encoding).splitlines()[-1]
                self.assertEqual(json.loads(line.removeprefix("Question: ")), question)

    def test_show_answer_on_legacy_stdout(self):
        text = "An answer with 🌿"
        for encoding in ("cp1252", "cp437"):
            with self.subTest(encoding=encoding), io.TextIOWrapper(io.BytesIO(), encoding=encoding) as output:
                runner = standin.StandIn(None, show_text=True)
                self.addCleanup(runner.close)
                runner.job = dict(lease="fixture")
                runner.source = standin.PracticeSource(dict(partial=""))
                runner.pending = b"{}", text.encode(), False
                with (
                    contextlib.redirect_stdout(output),
                    patch.object(runner, "_exchange", return_value=(200, dict(acked=len(text.encode())))),
                ):
                    runner._post()
                output.flush()
                line = output.buffer.getvalue().decode(encoding).splitlines()[-1]
                self.assertEqual(json.loads(line.removeprefix("Text: ")), text)
                self.assertEqual(runner.offset, len(text.encode()))


if __name__ == "__main__":
    unittest.main()
