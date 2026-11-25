#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Play the PC side of GPU Link through an existing loopback port forward.

Enrol through the owner's developer command, then approve the code on the phone:
  python3 -B tools/gpu-link-standin-pc.py --key-file ./pc.key --enrol-request | <phone enrolment command>
The command is adb shell running nc to the phone's loopback link port, or ssh
with strict host-key checking running the same. Then run practice mode:
  python3 -B tools/gpu-link-standin-pc.py --key-file ./pc.key
Existing chat-completions server example:
  python3 -B tools/gpu-link-standin-pc.py --key-file ./pc.key --text server \
    --server http://127.0.0.1:8080/v1/chat/completions --model local-model

Developer tool only. Enrolment writes one HTTP request to a pipe and never opens
a connection. The key travels only on the developer command's standard input.
No window, permission grant, forward creation, model download or engine startup.
Practice text is simulated; no speed or GPU hardware is claimed. Server
resume includes the stored partial as an assistant message; continuation quality
and prefix support depend on the server. Token counts fall back to word estimates.
Use the full endpoint URL. HTTP and HTTPS loopback literals or localhost only;
no proxies, redirects or compressed replies. Keys are raw 32-byte files; mode 0600
protects them on POSIX only. On Windows, mode 0600 does not make a key private on
NTFS. Use a dedicated private folder; see the first-run plan's Windows icacls steps.
The tool does not check Windows access rules.
Server generations have a 300-second total wall-clock limit (--generation-timeout),
including response headers and trickled bytes, plus a 30-second inactivity timeout.
Only a stop or length finish reason followed by [DONE] completes a server answer;
an answer that reaches the phone's size limit ends there as a length answer.
Questions and answers stay out of event lines unless --show-text is supplied.
Ctrl-C stops the tool and closes its current stream. Physical phone checks remain
NOT_RUN. The phone must already offer a ready model file before it hands out work.
"""

from __future__ import annotations

import argparse
import errno
import http.client
import io
import ipaddress
import json
import math
import os
import socket
import stat
import sys
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from opos import gpu_link_proto as proto

PRACTICE = (
    "Practice text from a stand-in PC. This is simulated text, with no model or GPU work.\n"
    + "A shared library starts with a room, a shelf and a list. Neighbours bring things and borrow them.\n" * 8
)
ENROL_REFUSED = "Enrolment request refused: stdout must be a pipe or socket to the phone enrolment command"


class ToolError(ValueError):
    """A safe diagnostic that contains no wire text or secret values."""


class GiveUpError(ToolError):
    """The consecutive failure window exceeded the configured limit."""


class RetryError(OSError):
    """A retryable reply; retain the current lease and exact pending post."""


class RejectedError(ToolError):
    """An unsigned, empty 401; only enrolment and epoch renewal may handle it."""


def loopback(host):
    """Accept literals only, mapping localhost without a DNS lookup."""
    if host == "localhost":
        return "127.0.0.1"
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise ToolError("host must be loopback; forward the port first")


def endpoint(value, phone=False):
    """Validate an endpoint before any socket, DNS request or key creation."""
    try:
        url = urlsplit("//" + value if phone else value)
        if (
            url.username is not None
            or url.password is not None
            or url.fragment
            or (phone and (url.path or url.query or url.port is None))
            or (not phone and (url.scheme not in ("http", "https") or not url.path.startswith("/")))
        ):
            raise ValueError
        host = loopback(url.hostname)
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError
        return host, port, url
    except (ValueError, TypeError) as err:
        raise ToolError("endpoint must name a loopback host and a valid port") from err


def key_file(path):
    """Create or read a regular key; owner/mode privacy checks are POSIX-only."""
    flags = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    getuid, fchmod = getattr(os, "getuid", None), getattr(os, "fchmod", None)
    # Windows keeps regular-file and size checks; inherited access rules are not checked.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | flags, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | flags)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or (
                getuid is not None and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != getuid())
            ):
                raise ToolError("key file must be a regular file owned by you with mode 0600") from None
            key = handle.read(33)
    else:
        with os.fdopen(fd, "wb") as handle:
            key = os.urandom(proto.KEY_BYTES)
            if fchmod is not None:
                fchmod(handle.fileno(), 0o600)
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
    if len(key) != proto.KEY_BYTES:
        raise ToolError("key file must contain exactly 32 bytes")
    return key


def clean(text):
    """Drop controls except LF/TAB and drop lone surrogate code points."""
    return "".join(
        c for c in text if (unicodedata.category(c) != "Cc" or c in "\n\t") and not 0xD800 <= ord(c) <= 0xDFFF
    )


def cut(data, limit):
    """Keep a UTF-8 prefix ending at a whole character."""
    return data[:limit].decode("utf-8", errors="ignore").encode("utf-8")


def checked_reply(target, status, raw):
    """Validate the complete reply body before a caller changes state."""
    try:
        if status == 204 and target.startswith("/v1/work?"):
            if raw:
                raise ValueError
            return {}
        value = proto.loads(raw, proto.WORK_REPLY_LIMIT)
        if status == 200 and target == "/v1/hello":
            keys = {"proto", "label", "epoch"}
            if (
                type(value.get("proto")) is not int
                or value["proto"] != proto.PROTO
                or not proto.valid_label(value.get("label"))
                or proto.unhex(value.get("epoch"), proto.EPOCH_BYTES) is None
            ):
                raise ValueError
        elif status == 200 and target == "/v1/status":
            keys = {"cancel"}
            cancel = value.get("cancel")
            if cancel is not None and proto.unhex(cancel, proto.LEASE_BYTES) is None:
                raise ValueError
        elif status == 200 and target.startswith("/v1/work?"):
            keys = {"lease", "model_sha256", "question", "partial"}
            lease = proto.unhex(value.get("lease"), proto.LEASE_BYTES)
            proto.work_reply(lease, value.get("model_sha256"), value.get("question"), value.get("partial"))
            if any(clean(value[field]) != value[field] for field in ("question", "partial")):
                raise ValueError
        elif target.endswith("/text") and (status == 200 or status in (409, 413) and "stored" in value):
            field = "acked" if status == 200 else "stored"
            keys = {field}
            count = value.get(field)
            if type(count) is not int or not 0 <= count <= proto.MAX_ANSWER_BYTES:
                raise ValueError
        else:
            keys = {"error"}
            proto.error_reply(value.get("error"))
        if set(value) != keys:
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError):
        raise RetryError("Invalid phone reply; waiting to retry") from None


class DeadlineReader(io.RawIOBase):
    """Apply one monotonic deadline to every socket read."""

    def __init__(self, sock, deadline, inactivity_timeout=None):
        self.sock, self.deadline = sock, deadline
        self.inactivity_timeout = inactivity_timeout
        self.stream = sock.makefile("rb", buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.sock.settimeout(min(remaining, self.inactivity_timeout) if self.inactivity_timeout else remaining)
        count = self.stream.readinto(buffer)
        if time.monotonic() >= self.deadline:
            raise TimeoutError
        return count

    def close(self):
        self.stream.close()
        super().close()


class ReplyReader(io.BufferedReader):
    """Bound status and header lines before the HTTP parser retains them."""

    def __init__(self, sock, deadline):
        super().__init__(DeadlineReader(sock, deadline))
        self.headers_done, self.used, self.lines = False, 0, 0
        self.terminated = False

    def readline(self, size=-1):
        if self.headers_done:
            return super().readline(size)
        bound = 8001 - self.used
        line = super().readline(bound if size < 0 else min(size, bound))
        self.used += len(line)
        self.lines += 1
        # Include the status line and final blank line in these bounds.
        if self.used > 8000 or self.lines > 42 or (self.lines > 41 and line != b"\r\n"):
            raise ToolError("reply headers too large")
        self.terminated = line == b"\r\n"
        return line


class BoundedResponse(http.client.HTTPResponse):
    """Use the standard HTTP parser with bounded, deadline-aware input."""

    def __init__(self, sock, deadline, **kwargs):
        class ReaderSocket:
            def makefile(self, *args):
                return ReplyReader(sock, deadline)

        super().__init__(ReaderSocket(), **kwargs)

    def begin(self):
        super().begin()
        if not self.fp.terminated:
            raise RetryError("Phone incomplete reply; waiting to retry")
        self.fp.headers_done = True


class PC:
    """One bounded connection per request, with fresh nonces and reply checks."""

    def __init__(self, phone, key, label="Stand-in PC", request_limit=1):
        self.host, self.port, _ = endpoint(phone, phone=True)
        if not proto.valid_label(label):
            raise ToolError("label must match the wire label rule (1 to 24 characters)")
        self.key, self.label, self.epoch = key, label, None
        self.request_limit = request_limit

    def send(self, method, target, body=b"", signed=True):
        headers = {"Host": "localhost", "Accept-Encoding": "identity", "Connection": "close"}
        if method == "POST":
            headers["Content-Type"] = "application/json"
        nonce = os.urandom(proto.NONCE_BYTES)
        if signed:
            headers.update(
                {
                    "X-Link-Pc": proto.pc_id(self.key),
                    "X-Link-Nonce": nonce.hex(),
                    "X-Link-Mac": proto.request_mac(self.key, self.epoch, method, target, "", nonce, body),
                }
            )
        deadline = time.monotonic() + self.request_limit
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.request_limit)
        connection.response_class = lambda sock, **kw: BoundedResponse(sock, deadline=deadline, **kw)
        try:
            connection.request(method, target, body=body, headers=headers)
            with connection.getresponse() as response:
                fields = response.getheaders()
                if len(fields) > 40 or sum(len(k) + len(v) for k, v in fields) > 8000:
                    raise ToolError("reply headers too large")
                if response.getheader("Content-Encoding") not in (None, "identity"):
                    raise ToolError("compressed reply refused")
                if response.status == 204 and response.getheader("Content-Length", "0") != "0":
                    raise RetryError("Invalid phone reply; waiting to retry")
                raw = response.read(proto.WORK_REPLY_LIMIT + 1)
                if time.monotonic() >= deadline:
                    raise TimeoutError
                if len(raw) > proto.WORK_REPLY_LIMIT:
                    raise ToolError("reply body too large")
                if response.length:
                    raise OSError("incomplete reply")
                if signed:
                    macs = [v for k, v in fields if k.lower() == "x-link-mac"]
                    if response.status == 401 and not raw and not macs:
                        raise RejectedError("signed request refused")
                    expected = proto.reply_mac(
                        self.key, nonce, response.status, response.getheader("Content-Range", ""), raw
                    )
                    if len(macs) != 1 or not proto.same_mac(macs[0], expected):
                        raise ToolError("reply signature missing or wrong; stopped")
                return response.status, checked_reply(target, response.status, raw)
        finally:
            connection.close()

    def hello(self):
        """Read the unsigned protocol greeting and current signing epoch."""
        status, value = self.send("GET", "/v1/hello", signed=False)
        if status == 429 or status >= 500:
            raise RetryError("Phone hello unavailable; waiting to retry")
        if status != 200:
            raise ToolError("hello refused")
        epoch = proto.unhex(value.get("epoch"), proto.EPOCH_BYTES)
        if (
            set(value) != {"proto", "label", "epoch"}
            or type(value.get("proto")) is not int
            or value.get("proto") != proto.PROTO
            or epoch is None
            or not proto.valid_label(value.get("label"))
        ):
            raise RetryError("Invalid phone reply; waiting to retry")
        self.epoch = epoch

    def status(self, lease=None, tokens=None, tps=None, error=None):
        """Publish liveness and optional speed without claiming GPU hardware."""
        payload = dict(
            state="error" if error else "busy" if lease else "ready",
            lease=lease,
            copied=None,
            total=None,
            gpu=None,
            tokens=tokens,
            tps=tps,
            error=error,
        )
        return self.send("POST", "/v1/status", proto.dumps(payload))

    def enrol_request(self):
        """Build one complete request for the owner's developer command stdin."""
        body = proto.dumps({"pc_label": self.label, "key": self.key.hex()})
        return (
            b"POST /v1/enrol HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + body
        )


class PracticeSource:
    """Fixed, explicitly simulated text with a resumable prefix."""

    def __init__(self, job):
        partial = job["partial"].encode()
        practice = clean(PRACTICE).encode()
        self.data = practice if practice.startswith(partial) else partial + practice
        self.finish = "length" if len(self.data) > proto.MAX_ANSWER_BYTES else "stop"
        self.data = cut(self.data, proto.MAX_ANSWER_BYTES)

    def snapshot(self):
        """Return buffered text, completion, finish reason, token estimate and error."""
        return self.data, True, self.finish, None, False

    def close(self):
        """There is no worker or socket in practice mode."""


class ServerResponse(http.client.HTTPResponse):
    """Apply the generation deadline and inactivity timeout inside every read."""

    def __init__(self, sock, deadline, **kwargs):
        class ReaderSocket:
            def makefile(self, *args):
                return io.BufferedReader(DeadlineReader(sock, deadline, inactivity_timeout=30))

        super().__init__(ReaderSocket(), **kwargs)


class ServerSource:
    """Read bounded SSE in a worker so phone status continues during server waits."""

    def __init__(self, job, url, model, generation_timeout=300):
        if not math.isfinite(generation_timeout) or generation_timeout <= 0:
            raise ToolError("generation-timeout must be finite and greater than zero")
        self.generation_timeout = generation_timeout
        self.host, self.port, self.url = endpoint(url)
        self.model, self.job = model, job
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.data = job["partial"].encode()
        self.partial_bytes = len(self.data)
        self.done, self.finish, self.tokens, self.error = False, "stop", None, False
        self.normal_stop = False
        self.sock = None
        self.worker = threading.Thread(target=self._run, name="standin-stream")
        self.worker.start()

    def snapshot(self):
        """Take a consistent copy of the reader state."""
        with self.lock:
            return (
                self.data,
                self.done,
                self.finish,
                self.tokens if self.tokens is not None else len(self.data[self.partial_bytes :].decode().split()),
                self.error,
            )

    def _event(self, value):
        if not isinstance(value, dict) or "error" in value:
            raise ToolError("invalid server stream")
        choices = value.get("choices", [])
        if choices:
            choice = choices[0]
            piece = choice.get("delta", {}).get("content") or ""
            if not isinstance(piece, str):
                raise ToolError("invalid server stream")
            with self.lock:
                cleaned = clean(piece).encode()
                room = proto.MAX_ANSWER_BYTES - len(self.data)
                self.data += cut(cleaned, room)
                if len(cleaned) >= room:
                    # The phone cannot keep more text: the answer ends here as a length-limited answer.
                    self.finish, self.normal_stop = "length", True
                    return True
                reason = choice.get("finish_reason")
                if reason is not None:
                    if reason not in ("stop", "length"):
                        raise ToolError("server stream did not stop normally")
                    self.finish, self.normal_stop = reason, True
        usage = value.get("usage") or {}
        tokens = usage.get("completion_tokens")
        if type(tokens) is int and 0 <= tokens <= proto.MAX_WHOLE:
            with self.lock:
                self.tokens = tokens
        return False

    def _read(self, response):
        total = 0
        while not self.stop.is_set():
            line = response.readline(65537)
            total += len(line)
            if len(line) > 65536 or total > 2 * 1024 * 1024:
                raise ToolError("server stream too large")
            if not line:
                raise ToolError("server stream ended early")
            if line.startswith(b"event:") and line[6:].strip() == b"error":
                raise ToolError("server stream error event")
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                if not self.normal_stop:
                    raise ToolError("server stream missing normal stop")
                return
            if self._event(json.loads(data)):
                return

    def _run(self):
        connection_type = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        deadline = time.monotonic() + self.generation_timeout
        connection = connection_type(self.host, self.port, timeout=min(2, self.generation_timeout))
        connection.response_class = lambda sock, **kwargs: ServerResponse(sock, deadline, **kwargs)
        try:
            messages = [{"role": "user", "content": self.job["question"]}]
            if self.job["partial"]:
                messages.append({"role": "assistant", "content": self.job["partial"]})
            payload = json.dumps(
                {"model": self.model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}},
                ensure_ascii=False,
            ).encode()
            connection.connect()
            with self.lock:
                self.sock = connection.sock
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                self.sock.settimeout(min(30, remaining))
            if self.stop.is_set():
                return
            target = self.url.path + ("?" + self.url.query if self.url.query else "")
            connection.request(
                "POST", target, payload, {"Content-Type": "application/json", "Accept-Encoding": "identity"}
            )
            with connection.getresponse() as response:
                if response.status != 200 or response.getheader("Content-Encoding") not in (None, "identity"):
                    raise ToolError("chat-completions server refused stream")
                if response.getheader("Content-Type", "").split(";")[0] != "text/event-stream":
                    raise ToolError("chat-completions server must stream events")
                self._read(response)
        except Exception:
            # Parser and unexpected worker failures must never publish successful completion.
            with self.lock:
                self.error = not self.stop.is_set()
        finally:
            connection.close()
            with self.lock:
                self.done = True

    def close(self):
        """Interrupt the read and join the worker before returning."""
        self.stop.set()
        with self.lock:
            sock = self.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # The reader may already have closed its connection.
        self.worker.join(4)
        if self.worker.is_alive():
            raise ToolError("server stream did not stop")


class StandIn:
    """A single-step reactor; pausing calls to step simulates a pulled cable."""

    def __init__(
        self, pc, source=PracticeSource, rate=4, emit=print, show_text=False, clock=time.monotonic, give_up_after=300
    ):
        if not math.isfinite(rate) or not 0 < rate <= 4:
            raise ToolError("rate must be greater than zero and at most 4")
        self.pc, self.source_factory, self.rate = pc, source, rate
        self.emit, self.show_text, self.clock = emit, show_text, clock
        self.job, self.source = None, None
        self.offset, self.next_post, self.next_status, self.next_poll = 0, 0, 0, 0
        if not math.isfinite(give_up_after) or give_up_after < 0:
            raise ToolError("give-up-after must be finite and nonnegative")
        self.give_up_after = give_up_after
        self.failed_since, self.last_failure_line = None, None
        self.post_failed_since = None
        self.retry_at, self.pending = 0, None
        self.error_posts, self.error_flush_stopped, self.error_recovered = 0, False, False
        self.started = clock()

    def close(self):
        """Drop generation; the phone keeps its acknowledged partial text."""
        if self.source is not None:
            self.source.close()
        self.source, self.job, self.pending = None, None, None
        self.post_failed_since = None
        self.error_posts, self.error_flush_stopped, self.error_recovered = 0, False, False

    def _error_tail_pending(self, data):
        # A UTF-8 cut can leave three bytes of a 256-byte piece unused.
        # 49 posts cover the entire 12,288-byte buffer, including that rounding.
        # The one offset recovery that a failed answer may use is not charged to them.
        return self.offset < len(data) and self.error_posts < 49 and not self.error_flush_stopped

    def _request(self, send):
        """Repeat the same operation once after an epoch change, with a fresh nonce."""
        try:
            return send()
        except RejectedError:
            previous = self.pc.epoch
            self.pc.hello()
            if previous == self.pc.epoch:
                raise ToolError("key not accepted; enrol with --enrol-request or check the phone") from None
            try:
                return send()
            except RejectedError:
                raise RetryError("Phone epoch renewed; waiting to retry") from None

    def _exchange(self, method, target, body=b""):
        return self._accepted(self._request(lambda: self.pc.send(method, target, body)))

    def _accepted(self, reply):
        status, value = reply
        if status >= 500:
            raise RetryError("Phone server error; waiting to retry")
        if status == 429 or status == 400 and value.get("error") == "state":
            raise RetryError("Phone service unavailable; waiting to retry")
        self.failed_since = None
        if self.post_failed_since is None:
            self.last_failure_line = None
        return status, value

    def _send_status(self, lease=None, tokens=None, tps=None, error=None):
        return self._accepted(self._request(lambda: self.pc.status(lease, tokens, tps, error)))

    def _failure(self, line):
        now = self.clock()
        if self.failed_since is None:
            self.failed_since = now
        start = min(value for value in (self.failed_since, self.post_failed_since) if value is not None)
        if self.give_up_after and now - start >= self.give_up_after:
            raise GiveUpError("Phone exchanges failed too long; stopped")
        if self.last_failure_line is None or now - self.last_failure_line >= 30:
            self.emit(line)
            self.last_failure_line = now
        self.retry_at = now + 1

    def _status(self):
        tokens, tps = None, None
        if self.source:
            data, _, _, tokens, error = self.source.snapshot()
            if error and not self._error_tail_pending(data):
                self._send_status(self.job["lease"], error="server")
                self.emit("Answer stopped: chat-completions server error")
                self.close()
                return
            if tokens is not None:
                tps = min(100000, round(tokens / max(0.001, self.clock() - self.started), 3))
        status, value = self._send_status(self.job["lease"] if self.job else None, tokens, tps)
        if status == 404 and self.job and value.get("error") == "lease":
            self.close()
            self.emit("Lease ended; polling again")
            self._status()
            return
        if status != 200:
            raise ToolError("status refused")
        self.next_status = self.clock() + 1
        if self.job and value.get("cancel") == self.job["lease"]:
            self.close()
            self.emit("Answer cancelled by phone")
        else:
            self.emit("Status busy" if self.job else "Status ready")

    def _work(self):
        status, job = self._exchange("GET", "/v1/work?wait=0")
        self.next_poll = self.clock() + 0.25
        if status == 204:
            return
        if status != 200:
            raise ToolError("work poll refused")
        self.job, self.offset = job, len(job["partial"].encode())
        self.source = self.source_factory(job)
        self.started, self.next_status = self.clock(), 0
        self.emit(f"Work received; partial {self.offset} bytes")
        if self.show_text:
            self.emit("Question: " + json.dumps(clean(job["question"]), ensure_ascii=True))

    def _post_reply(self, raw, piece, data):
        """Keep the pending post and its failure window until its reply is accepted."""
        try:
            status, value = self._exchange("POST", f"/v1/runs/{self.job['lease']}/text", raw)
            if status in (409, 413) and "stored" in value:
                stored = value["stored"]
                if stored > len(data) or cut(data, stored) != data[:stored]:
                    raise RetryError("Invalid stored byte offset; waiting to retry")
            if status == 200 and value["acked"] != self.offset + len(piece):
                raise RetryError("Invalid text acknowledgement; waiting to retry")
            return status, value
        except (OSError, http.client.HTTPException):
            if self.post_failed_since is None:
                self.post_failed_since = self.clock()
            raise

    def _post(self):
        data, done, finish, _, error = self.source.snapshot()
        if error:
            self._post_error_tail(data)
            return
        if self.offset > len(data):
            raise ToolError("stored offset exceeds buffered answer")
        if self.pending is None:
            piece = cut(data[self.offset :], 256)
            final = done and self.offset + len(piece) == len(data)
            if not piece and not final:
                return
            if final:
                self._status()  # Store the last speed before the final post.
                if self.job is None:
                    return
            raw = proto.dumps(
                dict(offset=self.offset, text=piece.decode(), done=final, finish=finish if final else None)
            )
            self.pending = raw, piece, final
        raw, piece, final = self.pending
        status, value = self._post_reply(raw, piece, data)
        self.pending = None
        if status == 200:
            self.post_failed_since = None
            self.last_failure_line = None
        self.next_post = self.clock() + 1 / self.rate
        if status in (409, 413) and "stored" in value:
            self.offset = value["stored"]
            self.emit(f"Offset recovered; stored {self.offset} bytes")
        elif status == 404:
            self.close()
            self.emit("Lease ended; polling again")
        elif status == 200:
            self.offset += len(piece)
            self.emit(f"Text acknowledged; {self.offset} bytes" + ("; final" if final else ""))
            if self.show_text:
                self.emit("Text: " + json.dumps(piece.decode(), ensure_ascii=True))
            if final:
                self.close()
        else:
            raise ToolError("text post refused")

    def _post_error_tail(self, data):
        """Drain at most 49 paced, non-final posts, plus one uncounted offset recovery; stop at the first refusal."""
        if self.offset > len(data) or len(data) > proto.MAX_ANSWER_BYTES:
            raise ToolError("invalid buffered answer size")
        if not self._error_tail_pending(data):
            self.next_status = 0
            return
        piece = cut(data[self.offset :], 256)
        raw = proto.dumps(dict(offset=self.offset, text=piece.decode(), done=False, finish=None))
        self.error_posts += 1
        self.pending = None
        # Do not retry a refused or unacknowledged flush piece. The failure
        # status is still attempted if the lease and connection remain usable.
        self.error_flush_stopped = True
        self.next_status = 0
        try:
            # Unlike normal posting, a refusal must not retry even after an
            # epoch change. Each flush attempt sends at most one text request.
            status, value = self._accepted(self.pc.send("POST", f"/v1/runs/{self.job['lease']}/text", raw))
        except RejectedError:
            return
        if status == 200 and value["acked"] != self.offset + len(piece):
            raise RetryError("Invalid text acknowledgement; waiting to retry")
        self.next_post = self.clock() + 1 / self.rate
        if status == 404:
            self.close()
            self.emit("Lease ended; polling again")
        elif status == 200:
            self.offset += len(piece)
            self.post_failed_since, self.last_failure_line = None, None
            self.error_flush_stopped = False
            self.emit(f"Text acknowledged; {self.offset} bytes")
            if self.show_text:
                self.emit("Text: " + json.dumps(piece.decode(), ensure_ascii=True))
        elif status == 409 and "stored" in value and not self.error_recovered:
            # A lost acknowledgement can leave the phone ahead of this tool, so the fresh piece starts inside text the
            # phone already holds and draws a 409 with its stored length (a 413 always repeats the tool's own offset).
            # Take the stored length as normal posting does and keep flushing from there. A failed
            # answer gets one such recovery, which is not charged to the 49 posts, so a full buffer is still flushed
            # and a phone that keeps changing its stored length cannot keep the flush going. A reply that makes no
            # progress, or reports a length that is not a whole-character prefix of the buffer, ends the flush.
            stored = value["stored"]
            if stored != self.offset and stored <= len(data) and cut(data, stored) == data[:stored]:
                self.offset = stored
                self.error_flush_stopped = False
                self.error_recovered = True
                self.error_posts -= 1
                self.emit(f"Offset recovered; stored {self.offset} bytes")

    def step(self):
        """Perform bounded work; callers close this object even after a failure."""
        if self.clock() < self.retry_at:
            return
        try:
            if self.pc.epoch is None:
                self.pc.hello()
                if self.failed_since is None:
                    self.emit("Hello accepted")
            if self.clock() >= self.next_status:
                self._status()
            if self.job is None and self.clock() >= self.next_poll:
                self._work()
            if self.job is not None and self.clock() >= self.next_post:
                self._post()
        except RetryError as err:
            self._failure(str(err))
        except (OSError, http.client.HTTPException):
            self.close()
            self.pc.epoch = None
            self.next_status = 0
            self._failure("Phone unreachable; waiting to retry")

    def run(self, stop):
        """Retry bounded failures until stopped or the consecutive window expires."""
        try:
            while not stop.is_set():
                self.step()
                stop.wait(max(0.05, self.retry_at - self.clock()))
        finally:
            self.close()


def arguments(argv=None):
    """Parse and validate all connection options before touching key storage."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phone", default="127.0.0.1:8767", help="forwarded loopback HOST:PORT (default: %(default)s)")
    parser.add_argument(
        "--label", default="Stand-in PC", help="wire label: letters, digits, spaces and hyphens; 1 to 24 chars"
    )
    parser.add_argument(
        "--key-file",
        required=True,
        help="raw 32-byte key; mode 0600 on POSIX only; Windows needs a dedicated private folder (see first-run plan)",
    )
    parser.add_argument(
        "--enrol-request", action="store_true", help="write enrolment HTTP request to a pipe or socket; never connect"
    )
    parser.add_argument(
        "--text", choices=("practice", "server"), default="practice", help="text source (default: practice)"
    )
    parser.add_argument("--server", help="full loopback chat-completions endpoint URL; existing server only")
    parser.add_argument("--model", help="model name for the existing chat-completions server")
    parser.add_argument(
        "--rate", type=float, default=4, help="text posts per second, greater than 0 and at most 4 (default: 4)"
    )
    parser.add_argument(
        "--give-up-after", type=float, default=300, help="consecutive failure limit in seconds (default: 300; 0: never)"
    )
    parser.add_argument(
        "--generation-timeout",
        type=float,
        default=300,
        help="total server generation limit in seconds, greater than zero (default: 300)",
    )
    parser.add_argument("--show-text", action="store_true", help="include questions and answer text in event lines")
    args = parser.parse_args(argv)
    endpoint(args.phone, phone=True)
    if args.server:
        endpoint(args.server)
    if not proto.valid_label(args.label):
        raise ToolError("invalid label; use 1 to 24 letters, digits, spaces or hyphens with alphanumeric ends")
    if not math.isfinite(args.rate) or not 0 < args.rate <= 4:
        raise ToolError("rate must be greater than zero and at most 4")
    if not math.isfinite(args.give_up_after) or args.give_up_after < 0:
        raise ToolError("give-up-after must be finite and nonnegative")
    if not math.isfinite(args.generation_timeout) or args.generation_timeout <= 0:
        raise ToolError("generation-timeout must be finite and greater than zero")
    if args.text == "server" and (not args.server or not args.model):
        raise ToolError("server text needs --server and --model")
    return args


def enrol_stdout():
    """Require a pipe or socket where descriptor type checks are available."""
    output = sys.stdout
    if output is None or output.closed or output.isatty():
        return False
    try:
        mode = os.fstat(output.fileno()).st_mode
    except OSError as err:
        return err.errno != errno.EBADF
    except (AttributeError, ValueError):
        return True  # Some platforms cannot identify the non-terminal descriptor type.
    return stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)


def main(argv=None):
    """Run until interrupted and print only safe event diagnostics."""
    try:
        args = arguments(argv)
        if args.enrol_request and not enrol_stdout():
            print(ENROL_REFUSED, file=sys.stderr)
            return 2
        pc = PC(args.phone, key_file(args.key_file), args.label)

        def emit(line):
            print(line, flush=True)

        if args.enrol_request:
            code = proto.code(pc.label, pc.key)
            print(f"Compare code on phone: {code[:3]} {code[3:]}", file=sys.stderr, flush=True)
            try:
                sys.stdout.buffer.write(pc.enrol_request())
                sys.stdout.buffer.flush()
            except (OSError, ValueError):
                # Avoid a second failing flush changing the interpreter's exit status to 120.
                fd = os.open(os.devnull, os.O_WRONLY)
                try:
                    os.dup2(fd, 1)
                finally:
                    os.close(fd)
                print(ENROL_REFUSED, file=sys.stderr)
                return 2
            return 0
        source = (
            PracticeSource
            if args.text == "practice"
            else lambda job: ServerSource(job, args.server, args.model, args.generation_timeout)
        )
        StandIn(pc, source, args.rate, emit, args.show_text, give_up_after=args.give_up_after).run(threading.Event())
        return 0
    except GiveUpError as err:
        print(str(err), file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("Stand-in stopped")
        return 0
    except (ToolError, proto.ProtoError) as err:
        print("Stand-in stopped: " + str(err), file=sys.stderr)
        return 1
    except (OSError, http.client.HTTPException):
        print("Stand-in stopped: local file or connection error", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
