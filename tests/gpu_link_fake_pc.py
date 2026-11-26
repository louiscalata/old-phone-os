# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic owner-mode PC using the shared protocol and strict reply checks."""

from __future__ import annotations

import http.client
import json
import threading
from email.message import Message

from opos import gpu_link_proto as proto
from opos.gpu_link import Reply


class FakePC:
    """Exercise either a socket-free service or an isolated loopback listener.

    Keys are obvious test patterns. Every request gets a new counter nonce.
    Tests may retain a prepared request to exercise deliberate replay attacks.
    A missing/incorrect reply signature raises, except for expected unsigned 401.
    """

    def __init__(self, link=None, address=None, key=b"K" * 32, label="Test PC"):
        self.link, self.address = link, address
        self.key, self.label = key, label
        self.epoch = None
        self._counter = 0
        self._lock = threading.Lock()

    @staticmethod
    def data(reply):
        return json.loads(reply.body) if reply.body else None

    def send(self, method, target, headers=None, body=b""):
        headers = dict(headers or {})
        headers.setdefault("Host", "localhost:12345")
        if method == "POST":
            headers.setdefault("Content-Type", "application/json")
            headers.setdefault("Content-Length", str(len(body)))
        if self.link is not None:
            message = Message()
            for name, value in headers.items():
                message[name] = value
            reply = self.link.handle(method, target, message, body)
            if reply.sent is not None:
                reply.sent()
            return reply
        connection = http.client.HTTPConnection(*self.address, timeout=2)
        try:
            connection.request(method, target, body, headers)
            response = connection.getresponse()
            try:
                return Reply(response.status, tuple(response.getheaders()), response.read())
            finally:
                response.close()
        finally:
            connection.close()

    def hello(self):
        reply = self.send("GET", "/v1/hello")
        if reply.status == 200:
            value = self.data(reply)
            if value["proto"] != proto.PROTO:
                raise AssertionError("protocol")
            self.epoch = bytes.fromhex(value["epoch"])
        return reply

    def enrol(self):
        return self.send("POST", "/v1/enrol", body=proto.dumps({"pc_label": self.label, "key": self.key.hex()}))

    @property
    def code(self):
        return proto.code(self.label, self.key)

    def prepare(self, method, target, body=b"", range_value=None):
        with self._lock:
            self._counter += 1
            nonce = self._counter.to_bytes(12, "big")
        headers = {
            "X-Link-Pc": proto.pc_id(self.key),
            "X-Link-Nonce": nonce.hex(),
            "X-Link-Mac": proto.request_mac(self.key, self.epoch, method, target, range_value or "", nonce, body),
        }
        if range_value is not None:
            headers["Range"] = range_value
        return method, target, headers, body

    def verify(self, reply, target, nonce):
        headers = {name.lower(): value for name, value in reply.headers}
        if reply.status == 401:
            if reply.body or "x-link-mac" in headers:
                raise AssertionError("unsigned rejection")
            return reply
        model = target.startswith("/v1/models/") and reply.status in (200, 206)
        expected = proto.reply_mac(
            self.key, nonce, reply.status, headers.get("content-range", ""), None if model else reply.body
        )
        if not proto.same_mac(headers.get("x-link-mac"), expected):
            raise AssertionError("reply signature")
        if reply.file is not None and not model:
            raise AssertionError("file reply outside models")
        return reply

    def request(self, method, target, body=b"", range_value=None):
        request = self.prepare(method, target, body, range_value)
        reply = self.send(*request)
        return self.verify(reply, target, bytes.fromhex(request[2]["X-Link-Nonce"]))

    def status(self, state="idle", lease=None, **values):
        payload = dict(state=state, lease=lease, copied=None, total=None, gpu=None, tokens=None, tps=None, error=None)
        payload.update(values)
        return self.request("POST", "/v1/status", proto.dumps(payload))

    def work(self, wait=0):
        return self.request("GET", f"/v1/work?wait={wait}")

    def text(self, lease, offset, text, done=False, finish=None):
        return self.request(
            "POST", f"/v1/runs/{lease}/text", proto.dumps(dict(offset=offset, text=text, done=done, finish=finish))
        )

    def model(self, sha256, range_value=None):
        return self.request("GET", f"/v1/models/{sha256}", range_value=range_value)


class Clock:
    """Explicit monotonic test clock; callers advance it instead of sleeping."""

    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class RandomBytes:
    """Unique deterministic bytes for epochs and leases, never production keys."""

    def __init__(self):
        self.counter = 0

    def __call__(self, size):
        self.counter += 1
        return self.counter.to_bytes(size, "big")
