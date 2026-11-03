# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP helpers for hardened server tests."""
import http.client
import json
import re
import socket
import threading
from src.opos import web
class Running:
    """Run test server in a thread."""
    def __init__(self, phone=None, **kw):
        self.server = web.make_server(0, phone or web.Phone(), **kw)
        self.port = self.server.server_address[1]
        self.token = self.server.RequestHandlerClass.token
        self._t = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self):
        self._t.start()
        return self
    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False
def fetch_token(port: int) -> str:
    """Fetch page token via GET /."""
    _, _, body = request(port, "GET", "/", headers={"Host": f"127.0.0.1:{port}"})
    m = re.search(rb'<meta name="opos-token" content="([^"]+)">', body)
    assert m is not None
    return m.group(1).decode("ascii")
def request(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
    """Send one request and read the response."""
    assert isinstance(port, int) and 0 < port < 65536
    assert isinstance(method, str) and isinstance(path, str)
    hdrs = dict(headers) if headers else {}
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in hdrs.items())
    assert body is None or isinstance(body, bytes)
    if "Host" not in hdrs and not any(k.lower() == "host" for k in hdrs):
        hdrs["Host"] = f"127.0.0.1:{port}"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=hdrs, skip_host=True, skip_accept_encoding=True)
        res = conn.getresponse()
        return res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
    finally:
        conn.close()
def post_action(port: int, action: str, token: str | None = None, **fields) -> tuple[int, dict, dict | list | None]:
    """POST one JSON action to /api."""
    assert isinstance(action, str)
    tok = token if token is not None else fetch_token(port)
    assert isinstance(tok, str)
    raw_body = json.dumps({"action": action, **fields}).encode()
    status, hdrs, body = request(port, "POST", "/api", body=raw_body, headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-OPOS-Token": tok})
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = None
    if parsed is not None and not isinstance(parsed, (dict, list)):
        parsed = None
    return status, hdrs, parsed
def raw(port: int, data: bytes) -> bytes:
    """Send raw bytes and read until close."""
    assert isinstance(port, int) and 0 < port < 65536
    assert isinstance(data, bytes)
    out = bytearray()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        try:
            s.connect(("127.0.0.1", port))
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
        except OSError:
            return b""
        while True:
            try:
                chunk = s.recv(65536)
            except (OSError, socket.timeout):
                break
            if not chunk:
                break
            out += chunk
        return bytes(out)
    finally:
        s.close()
