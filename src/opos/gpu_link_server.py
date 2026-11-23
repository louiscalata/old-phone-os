# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded loopback listener for the owner-mode GPU Link service.

Read caps include line endings: 2,000 request-line bytes, 40 header fields and
8,000 header-block bytes. No parsing or hashing occurs until those bounds pass.
Absolute deadlines defeat byte-at-a-time clients. The design's ten-second limit
for the complete incoming request is stricter than a separate body deadline;
both are enforced. Four connections include long polls and file transfers.
"""

from __future__ import annotations

import os
import re
import socket
import socketserver
import threading
import time
from collections.abc import Mapping
from contextlib import ExitStack
from http import HTTPStatus
from typing import Any

from opos import gpu_link_proto as proto
from opos import secure
from opos.ai_answers import AnswerStore
from opos.gpu_link import LinkService, ModelChangedError, Reply
from opos.store import Store

HEADER_SECONDS = 5.0
BODY_SECONDS = 10.0
WRITE_SECONDS = 10.0
LINE_BYTES = 2000
HEADER_BYTES = 8000
HEADER_LINES = 40
MAX_CONNECTIONS = 4
_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?")
_FIELD = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class _RequestError(Exception):
    def __init__(self, status: int = 400, code: str = "request") -> None:
        self.reply = Reply(401) if status == 401 else Reply(status, (), proto.error_reply(code))


class _Headers(dict):
    def get(self, name: str, default: Any = None) -> Any:
        return super().get(name.lower(), default)


def _deadline(sock: socket.socket, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    sock.settimeout(remaining)


def _line(sock: socket.socket, bound: int, deadline: float, buffer: bytearray) -> bytes:
    while True:
        _deadline(sock, deadline)
        end = buffer.find(b"\n", 0, bound)
        if end >= 0:
            raw = bytes(buffer[: end + 1])
            del buffer[: end + 1]
            if not raw.endswith(b"\r\n"):
                raise _RequestError()
            return raw
        if len(buffer) >= bound:
            raise _RequestError()
        chunk = sock.recv(min(4096, bound - len(buffer)))
        if not chunk:
            raise ConnectionError
        buffer.extend(chunk)


def _headers(sock: socket.socket, deadline: float, buffer: bytearray) -> _Headers:
    headers = _Headers()
    used = 0
    for _ in range(HEADER_LINES + 1):
        # After field 40 only the two-byte terminator is admissible. Do not read
        # a 41st field just to find out that it is too many.
        bound = min(2, HEADER_BYTES - used) if len(headers) == HEADER_LINES else HEADER_BYTES - used
        line = _line(sock, bound, deadline, buffer)
        used += len(line)
        if line == b"\r\n":
            return headers
        if len(headers) == HEADER_LINES:
            raise _RequestError()
        try:
            name, value = line[:-2].decode("ascii").split(":", 1)
        except (UnicodeError, ValueError) as err:
            raise _RequestError() from err
        if _FIELD.fullmatch(name) is None or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise _RequestError()
        name = name.lower()
        if name in headers:
            raise _RequestError()
        # Match HTTPMessage.get: leading separator spaces are HTTP framing.
        # Retain trailing spaces, especially in the signed Range value.
        headers[name] = value.lstrip(" ")
    raise _RequestError()


def _size(headers: _Headers, method: str, target: str, link: LinkService) -> int:
    host = headers.get("Host", "")
    if _HOST.fullmatch(host) is None or (":" in host and int(host.rsplit(":", 1)[1]) > 65535):
        raise _RequestError()
    unsigned = link.unsigned(method, target)
    if headers.get("Transfer-Encoding") is not None or (
        headers.get("Range") is not None and not link.permits_range(method, target)
    ):
        raise _RequestError(400 if unsigned else 401)
    if method == "POST" and headers.get("Content-Length") is None:
        raise _RequestError(411 if unsigned else 401)
    raw = headers.get("Content-Length", "0")
    if not re.fullmatch(r"0|[1-9][0-9]{0,8}", raw):
        raise _RequestError(400 if unsigned else 401)
    size = int(raw)
    if size > link.body_limit(method, target):
        raise _RequestError(413 if unsigned else 401, "too_long")
    if (method, target) == ("POST", "/v1/enrol") and headers.get("Content-Type") != "application/json":
        raise _RequestError()
    return size


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self._request_deadline = time.monotonic() + BODY_SECONDS
        self._buffer = bytearray()
        try:
            try:
                method, target, headers, body = self._read()
            except _RequestError as err:
                # A refused request was not handed to the service. Keep its
                # remaining input eligible for eviction during the drain.
                self._write(err.reply)
                self._drain()
                return
            if not self.server.answering(self.request):
                return
            self._write(self.server.link.handle(method, target, headers, body), target)
        except (OSError, ConnectionError):
            # A timed-out or disconnected peer is closed without logs or a retry.
            return

    def _read(self) -> tuple:
        deadline = min(self._request_deadline, time.monotonic() + HEADER_SECONDS)
        raw = _line(self.request, LINE_BYTES, deadline, self._buffer)
        headers = _headers(self.request, deadline, self._buffer)
        try:
            method, target, version = raw[:-2].decode("ascii").split(" ")
        except (UnicodeError, ValueError) as err:
            raise _RequestError() from err
        if version != "HTTP/1.1" or method not in ("GET", "POST") or not target.startswith("/"):
            raise _RequestError()
        size = _size(headers, method, target, self.server.link)
        body = self._buffer[:size]
        self._buffer.clear()
        while len(body) < size:
            _deadline(self.request, self._request_deadline)
            chunk = self.request.recv(size - len(body))
            if not chunk:
                raise ConnectionError
            body.extend(chunk)
        _deadline(self.request, self._request_deadline)
        return method, target, headers, bytes(body)

    def _drain(self) -> None:
        # Let the peer read the refusal before closing with unread input.
        self.request.shutdown(socket.SHUT_WR)
        remaining = max(0, 65536 - len(self._buffer))
        self._buffer.clear()
        deadline = min(self._request_deadline, time.monotonic() + 1.0)
        try:
            while remaining:
                _deadline(self.request, deadline)
                chunk = self.request.recv(min(4096, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
        finally:
            self.request.shutdown(socket.SHUT_RD)

    def _write(self, reply: Reply, target: str = "") -> None:
        try:
            self._write_reply(reply, target)
        except (OSError, ConnectionError):
            if reply.failed is not None:
                reply.failed()
            raise
        else:
            if reply.sent is not None:
                reply.sent()

    def _write_reply(self, reply: Reply, target: str) -> None:
        deadline = time.monotonic() + WRITE_SECONDS
        with ExitStack() as stack:
            stream = None
            length = len(reply.body)
            if reply.file is not None:
                path, start, length = reply.file
                try:
                    stream = stack.enter_context(self.server.link.open_model(path, target, reply.file_identity))
                    stream.seek(start)
                    self.server.link.check_model_stream(stream, reply.file_identity)
                except ModelChangedError:
                    raise
                except OSError:
                    # Authentication already succeeded. An ordinary open/seek
                    # failure must still have a signed response before headers.
                    if reply.file_error is None:
                        raise
                    self._write_reply(reply.file_error, target)
                    return
            headers = (
                ("Content-Length", str(length)),
                ("Connection", "close"),
                ("Content-Type", "application/octet-stream" if stream else "application/json"),
                ("Cache-Control", "no-store"),
            ) + reply.headers
            head = f"HTTP/1.1 {reply.status} {HTTPStatus(reply.status).phrase}\r\n"
            head += "".join(f"{key}: {value}\r\n" for key, value in headers) + "\r\n"
            _deadline(self.request, deadline)
            self.request.sendall(head.encode("ascii"))
            if stream is None:
                _deadline(self.request, deadline)
                self.request.sendall(reply.body)
            else:
                while length:
                    chunk = stream.read(min(length, 65536))
                    self.server.link.check_model_stream(stream, reply.file_identity)
                    if not chunk:
                        raise OSError("short model read")
                    _deadline(self.request, time.monotonic() + WRITE_SECONDS)
                    self.request.sendall(chunk)
                    length -= len(chunk)

    def log_message(self, format: str, *args: Any) -> None:
        """Deliberately emit nothing, including on failures."""


class Listener(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Four-connection loopback server; close() joins every owned thread.

    Construction binds but does not start threads. run() starts the accept and
    quarter-second timer threads. This separate object permits isolated tests.
    """

    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(self, link: LinkService, host: str = "127.0.0.1", port: int = 8767) -> None:
        secure.check_bind(host)
        self.address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET
        self.link = link
        self._connections: set[socket.socket] = set()
        self._reading: dict[socket.socket, None] = {}
        self._connections_lock = threading.Lock()
        self._halt = threading.Event()
        self._workers: list[threading.Thread] = []
        self._handlers: list[threading.Thread] = []
        super().__init__((host, port), _Handler)

    def run(self) -> None:
        """Start accepting requests and ticking, once per listener instance."""
        if self._workers:
            return
        if self._halt.is_set():
            return
        workers = [
            threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, name="gpu-link-listen"),
            threading.Thread(target=self._timer, name="gpu-link-timer"),
        ]
        try:
            for worker in workers:
                worker.start()
                self._workers.append(worker)
        except RuntimeError:
            self.close()
            raise

    def _timer(self) -> None:
        while not self._halt.wait(0.25):
            try:
                self.link.tick()
            except Exception:
                # One failed tick must not disable future link-down detection.
                continue

    def process_request(self, request: socket.socket, client_address: tuple) -> None:
        """Replace the oldest reader at capacity, preserving active replies."""
        with self._connections_lock:
            if len(self._connections) == MAX_CONNECTIONS:
                oldest = next(iter(self._reading), None)
                if oldest is None:
                    self.shutdown_request(request)
                    return
                try:
                    oldest.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                # The handler owns the close; shutdown wakes its blocked read.
                self._connections.remove(oldest)
                del self._reading[oldest]
            self._connections.add(request)
            self._reading[request] = None
        worker = threading.Thread(target=self.process_request_thread, args=(request, client_address))
        try:
            worker.start()
        except RuntimeError:
            with self._connections_lock:
                self._connections.discard(request)
                self._reading.pop(request, None)
            self.shutdown_request(request)
        else:
            self._handlers = [thread for thread in self._handlers if thread.is_alive()]
            self._handlers.append(worker)

    def answering(self, request: socket.socket) -> bool:
        """Protect a fully read request from eviction, unless already evicted."""
        with self._connections_lock:
            if request not in self._connections:
                return False
            self._reading.pop(request, None)
            return True

    def process_request_thread(self, request: socket.socket, client_address: tuple) -> None:
        """Release admission only after the request socket has been closed."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._connections_lock:
                self._connections.discard(request)
                self._reading.pop(request, None)

    def handle_error(self, request: socket.socket, client_address: tuple) -> None:
        """Close unexpected handler failures without printing request information."""

    def close(self) -> None:
        """Stop admission, wake polls, close sockets, and join all workers."""
        self._halt.set()
        if self._workers:
            self.shutdown()
        try:
            self.link.close()
        finally:
            with self._connections_lock:
                for connection in self._connections:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        # It may already have closed between the snapshot and shutdown.
                        continue
            self.server_close()
            for worker in self._workers:
                worker.join()
            self._workers.clear()
            for worker in self._handlers:
                worker.join()
            self._handlers.clear()


class _Running:
    lock = threading.RLock()
    listener: Listener | None = None


def service() -> LinkService | None:
    """Return the running link service, or None when its switch is off."""
    with _Running.lock:
        return _Running.listener.link if _Running.listener else None


def start(answers: AnswerStore, env: Mapping[str, str] | None = None) -> LinkService | None:
    """Start only for OPOS_GPU_LINK=1; invalid configuration fails closed."""
    env = os.environ if env is None else env
    with _Running.lock:
        if env.get("OPOS_GPU_LINK") != "1":
            return None
        if _Running.listener is not None:
            return _Running.listener.link
        root = env.get("XDG_STATE_HOME")
        store = Store(os.path.join(root, "opos")) if root else Store()
        link = LinkService(answers, store, env.get("OPOS_GPU_LINK_MODEL"), env.get("OPOS_GPU_LINK_LABEL", "OPOS phone"))
        if "OPOS_GPU_LINK_MODEL_NAME" in env:
            link.set_model_name(env["OPOS_GPU_LINK_MODEL_NAME"])
        listener = None
        try:
            listener = Listener(link, port=int(env.get("OPOS_GPU_LINK_PORT", "8767")))
            listener.run()
        except (OSError, ValueError, OverflowError, RuntimeError):
            if listener is not None:
                listener.close()
            else:
                link.close()
            raise
        _Running.listener = listener
        return link


def stop() -> None:
    """Stop the process listener and join its threads; safe when already off."""
    with _Running.lock:
        if _Running.listener is not None:
            _Running.listener.close()
            _Running.listener = None
