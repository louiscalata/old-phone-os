# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for ssh, for tests/test_selftest.py's run of deploy/duo-acceptance.ps1. Not a phone and not an ssh client.

Reads a JSON scenario from the path in OPOS_FAKE_SSH (set by the test, never by the script under test) and logs every
call to <scenario>.log. A call with -N -L is the DevTools tunnel: it listens on the forwarded local port and serves a
minimal Chrome DevTools endpoint (/json/list and a WebSocket that answers Page.captureScreenshot and Runtime.evaluate,
the screenshot split over several frames, preceded by an event with no id). Any other call is a remote command, answered
from the scenario. Only what the script needs is implemented.
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import zlib

SC = json.load(open(os.environ["OPOS_FAKE_SSH"], encoding="utf-8"))
LOG = os.environ["OPOS_FAKE_SSH"] + ".log"


def png(w, h):
    raw = b"".join(b"\0" + bytes(w) for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def frame(op, payload, fin=True):
    head = bytes([(0x80 if fin else 0) | op])
    n = len(payload)
    head += bytes([n]) if n < 126 else b"\x7e" + struct.pack(">H", n) if n < 65536 else b"\x7f" + struct.pack(">Q", n)
    return head + payload


def read_frame(conn):
    h = conn.recv(2)
    if len(h) < 2:
        return None, b""
    op, n = h[0] & 0x0F, h[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", conn.recv(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", conn.recv(8))[0]
    mask = conn.recv(4)
    data = b""
    while len(data) < n:
        data += conn.recv(n - len(data))
    return op, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def serve_ws(conn):
    for m in (json.dumps({"method": "Page.frameNavigated", "params": {}}),):
        conn.sendall(frame(1, m.encode()))
    while True:
        op, data = read_frame(conn)
        if op is None or op == 8:
            return
        req = json.loads(data)
        if req["method"] == "Page.captureScreenshot":
            body = json.dumps({"id": req["id"], "result": {"data": base64.b64encode(png(*SC["png"])).decode()}}).encode()
            parts = [body[i:i + 40000] for i in range(0, len(body), 40000)]
            for i, part in enumerate(parts):
                conn.sendall(frame(1 if i == 0 else 0, part, fin=i == len(parts) - 1))
        elif req["method"] == "Runtime.evaluate":
            v = json.dumps(SC["viewport"])
            conn.sendall(frame(1, json.dumps({"id": req["id"], "result": {"result": {"type": "string", "value": v}}}).encode()))
        else:
            conn.sendall(frame(1, json.dumps({"id": req["id"], "error": {"message": "unknown method"}}).encode()))


def handle(conn):
    with conn:
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = conn.recv(4096)
            if not c:
                return
            buf += c
        line, _, rest = buf.partition(b"\r\n")
        path = line.split()[1].decode()
        headers = {k.lower(): v.strip() for k, _, v in (ln.decode().partition(":") for ln in rest.split(b"\r\n") if b":" in ln)}
        if "origin" in headers:
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        if path == "/json/list":
            body = json.dumps([{"id": "PAGE1", "type": "page", "url": SC["page_url"], "title": "OPOS"}]).encode()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
        elif path == "/devtools/page/PAGE1" and "sec-websocket-key" in headers:
            acc = base64.b64encode(hashlib.sha1(headers["sec-websocket-key"].encode() + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")
            serve_ws(conn)
        else:
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")


def tunnel(lp):
    if not SC.get("devtools", True):
        # like ssh -L to a port nobody listens on: it accepts, then drops the connection
        srv = socket.socket()
        srv.bind(("127.0.0.1", lp))
        srv.listen(5)
        while True:
            c, _ = srv.accept()
            c.close()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", lp))
    srv.listen(5)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def main(argv):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(argv) + "\n")
    if SC.get("ssh_fail"):
        sys.stderr.write(SC["ssh_fail"] + "\n")
        return 255
    if "-N" in argv:
        spec = argv[argv.index("-L") + 1]
        tunnel(int(spec.split(":")[1]))
        return 0
    cmd = argv[-1]
    r = SC["commands"].get(cmd)
    if r is None:
        sys.stderr.write("fake ssh: no scenario for %r\n" % cmd)
        return 127
    sys.stdout.write(r["out"])
    sys.stdout.flush()
    sys.stderr.write(r.get("err", ""))
    return r["rc"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
