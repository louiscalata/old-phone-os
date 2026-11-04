# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit public, ephemeral simulation. Never start this through opos.web."""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
import ipaddress
import json
import os
from pathlib import Path
import re
import socketserver
import threading
import time

from opos import secure, web

NOTICE = "Public demo: answers come from the rules; Muse Glimmer runs only on your own computer."
COOKIE = "opos_demo"
MAX_SESSIONS = 300
IDLE_SECONDS = 15 * 60
MAX_SPEED = 60
MAX_MESSAGES = 100
MAX_IPS = 4096
ACTIONS = frozenset({"battery", "temp", "call", "hang_up", "send", "receive", "open",
                     "visible", "sim", "tick", "charge", "goal", "undo", "in_use",
                     "explain", "plan", "ask"})


def parse_hosts(value):
    """Accept exact ASCII DNS names / IPv4 authorities, optionally with a port. No globs."""
    hosts = value.split(",")
    if not 1 <= len(hosts) <= 32:
        raise ValueError("OPOS_PUBLIC_HOSTS needs 1 to 32 exact hosts")
    result = set()
    for raw in hosts:
        host = raw.strip()
        name, sep, port = host.partition(":")
        labels = name.split(".")
        if (len(host) > 253 or not name or
                any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p)
                    for p in labels) or
                (sep and (not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535))):
            raise ValueError("OPOS_PUBLIC_HOSTS must contain exact hosts, without schemes, paths or wildcards")
        result.add(host)
    return frozenset(result)


class DemoPhone(web.Phone):
    """Reuse the simulation with no host preferences, diagnostics or helper routes."""
    def __init__(self):
        super().__init__(visible=True)  # Runtime constructs only the deterministic Router.
        self.modem.events = deque(maxlen=MAX_MESSAGES)
        self.essentials.log = deque(maxlen=MAX_MESSAGES)
        self.dash.set_goal("16:00", self.clock_s)

    def _preferences(self):
        return web.prefs.defaults()  # Pure constants; never load or save a file.

    def _environment(self):
        return None, None, "Battery rules (public demo)"

    def _helper_request(self):
        # Inherited route selectors cannot resolve a local, LAN or paid model.
        raise RuntimeError("public demo has no model routes")

    def set_sim(self, running, speed=None):
        if speed is not None:
            speed = web._num(speed, 0.001, MAX_SPEED)
        super().set_sim(running, speed)

    def reply(self, action):
        state = self.state()
        if not self.visible:
            return {"error": "helper hidden"}
        if action == "plan":
            return {"order": list(state["battery"]["steps"]), "engine": "rules",
                    "where": "battery rules", "credit": NOTICE}
        return {"ok": True, "text": state["helper"]["why_paused"] + " " + NOTICE,
                "engine": "rules", "where": "battery rules", "sources": []}


@dataclass
class Session:
    touched: float
    phone: DemoPhone = field(default_factory=DemoPhone)
    token: str = field(default_factory=secure.new_token)
    lock: object = field(default_factory=threading.Lock)
    actions: object = field(default_factory=web._Limits)


class Sessions:
    """Bounded idle-expiring LRU. In-flight references are bounded by server connections."""
    def __init__(self, capacity=MAX_SESSIONS, idle=IDLE_SECONDS, clock=time.monotonic):
        if not 1 <= capacity <= MAX_SESSIONS or not 0 < idle <= IDLE_SECONDS:
            raise ValueError("invalid session limits")
        self.capacity, self.idle, self.clock = capacity, idle, clock
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def get(self, sid):
        with self.lock:
            now = self.clock()
            while self.items and next(iter(self.items.values())).touched <= now - self.idle:
                self.items.popitem(last=False)
            found = self.items.get(sid)
            if found is not None:
                found.touched = now
                self.items.move_to_end(sid)
                return sid, found, False
            while len(self.items) >= self.capacity:
                self.items.popitem(last=False)
            sid = secure.new_token()  # Never accept a client-chosen session identifier.
            found = Session(now)
            self.items[sid] = found
            return sid, found, True


class IPLimits:
    """Bounded token buckets: fail closed at capacity, reclaim only idle entries."""
    def __init__(self, rate=8.0, burst=40, capacity=MAX_IPS, clock=time.monotonic):
        self.rate, self.burst, self.capacity, self.clock = rate, burst, capacity, clock
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key):
        with self.lock:
            now = self.clock()
            while self.items and next(iter(self.items.values()))[1] <= now - IDLE_SECONDS:
                self.items.popitem(last=False)
            old = self.items.get(key)
            if old is None:
                if len(self.items) >= self.capacity:
                    return False
                left = self.burst
            else:
                left = min(self.burst, old[0] + max(0, now - old[1]) * self.rate)
            allowed = left >= 1
            self.items[key] = (left - int(allowed), now)
            self.items.move_to_end(key)
            return allowed


def landing(token, nonce, demo=False):
    """Compose around the unchanged phone page; its byte budget stays independent."""
    page = web.page(token, nonce, demo=demo)
    page = page.replace('<div id="about-notices">', '<div id="about-notices">'
                        '<p id="public-about">This public simulation runs on a web server. '
                        'It makes no model calls, changes no device settings and places no real calls. '
                        'The notices below describe the separate installable OPOS prototype.</p>', 1)
    page = page.replace('Licence, privacy and tested limits</summary>',
                        'Installed OPOS notices</summary>', 1)
    assets = Path(__file__).with_name("public_static")
    css = (assets / "public-demo.css").read_text(encoding="utf-8")
    script = (assets / "public-demo.js").read_text(encoding="utf-8")
    intro = (assets / "public-demo.html").read_text(encoding="utf-8")
    page = page.replace("</head>", f'<style nonce="{nonce}">{css}</style></head>', 1)
    page = page.replace("<body>", '<body class="public-demo">' + intro +
                        '<main id="try-phone"><div class="public-device" aria-label="Simulated phone">', 1)
    page = page.replace('<div id="demo"', '</div><div id="demo"', 1)
    # Install token rotation before the phone scripts start their initial fetch.
    marker = f'<script nonce="{nonce}">'
    page = page.replace(marker, f'</main>{marker}{script}</script>{marker}', 1)
    return page


class Handler(web.Handler):
    """Reuse deadlines / bounded server plumbing, never the local route dispatcher."""
    server_version = "OPOS"
    sys_version = ""

    def _one(self, name):
        values = self.headers.get_all(name, [])
        return values[0] if len(values) == 1 else None

    def _https(self):
        return self.trust_proxy and self._one("X-Forwarded-Proto") == "https"

    def _respond(self, body, code=200, content_type="application/json", nonce=None):
        self.close_connection = True
        self.send_response(code)
        headers = secure.security_headers(nonce or secure.new_token())
        if hasattr(self, "headers") and self._https():
            headers["Strict-Transport-Security"] = "max-age=31536000"
        if getattr(self, "session", None) is not None:
            cookie = SimpleCookie()
            cookie[COOKIE] = self.sid
            cookie[COOKIE]["path"] = "/"
            cookie[COOKIE]["httponly"] = True
            # Safe top-level visits must carry the existing session cookie.
            cookie[COOKIE]["samesite"] = "Lax"
            cookie[COOKIE]["max-age"] = IDLE_SECONDS
            cookie[COOKIE]["secure"] = self._https()
            headers["Set-Cookie"] = cookie[COOKIE].OutputString()
            headers["X-OPOS-Token"] = self.session.token
            if self.fresh:
                headers["X-OPOS-Session-Reset"] = "1"
        if code == 429:
            headers["Retry-After"] = "5"
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._respond(json.dumps(obj).encode(), code)

    def _deny(self, code, message, reason):
        # Do not consume an attacker's rejected body, or echo their input.
        self._json({"error": message}, code)

    def _boundary(self):
        host = self._one("Host")
        if host not in self.hosts:
            self._deny(421, "host not allowed", "host")
            return False
        if self.trust_proxy:
            proto = self.headers.get_all("X-Forwarded-Proto", [])
            if proto and proto not in (["http"], ["https"]):
                self._deny(400, "invalid forwarded protocol", "proxy")
                return False
        origins = self.headers.get_all("Origin", [])
        scheme = "https" if self._https() else "http"
        if origins and origins != [f"{scheme}://{host}"]:
            self._deny(403, "origin not allowed", "origin")
            return False
        navigation = (self.command == "GET" and self.path in ("/", "/?demo=1") and
                      self._one("Sec-Fetch-Mode") == "navigate" and
                      self._one("Sec-Fetch-Dest") == "document")
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not navigation:
            self._deny(403, "cross-site request", "origin")
            return False
        return True

    def _ip_allowed(self):
        ip = self.client_address[0]
        if self.trust_proxy:
            if len(self.headers.get_all("X-Forwarded-For", [])) > 1:
                self._deny(400, "invalid forwarded address", "proxy")
                return False
            forwarded = self._one("X-Forwarded-For")
            if forwarded:
                try:
                    # The trusted ingress appends the client address. Earlier
                    # hops may be supplied by the client and cannot key limits.
                    last = forwarded.rsplit(",", 1)[-1].strip()
                    if len(last) > 45 or "%" in last:
                        raise ValueError("not a public forwarding address")
                    ip = str(ipaddress.ip_address(last))
                except ValueError:
                    self._deny(400, "invalid forwarded address", "proxy")
                    return False
        if not self.ip_limits.allow(ip):
            self._deny(429, "too many requests", "rate")
            return False
        return True

    def _session(self):
        raw = self._one("Cookie") or ""
        cookie = SimpleCookie()
        try:
            if len(raw) <= 4096:
                cookie.load(raw)
        except CookieError:
            pass
        supplied = cookie[COOKIE].value if COOKIE in cookie else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied):
            supplied = ""
        sid, session, fresh = self.sessions.get(supplied)
        # An existing session always requires its own token, never another visitor's.
        if not fresh and not secure.token_ok(self._one("X-OPOS-Token"), session.token):
            self._deny(403, "forbidden", "token")
            return False
        self.sid, self.session, self.fresh = sid, session, fresh
        return True

    def _get(self):
        if not self._boundary():
            return
        if self.path == "/healthz":
            # No phone, token, cookie, rate bucket or simulation state is created.
            self._respond(b"ok", content_type="text/plain; charset=utf-8")
            return
        if not self._ip_allowed():
            return
        if self.path in ("/", "/?demo=1"):
            # Root establishes the cookie/token pair; it never discloses another session.
            raw = self._one("Cookie") or ""
            cookie = SimpleCookie()
            try:
                if len(raw) <= 4096:
                    cookie.load(raw)
            except CookieError:
                pass
            sid = cookie[COOKIE].value if COOKIE in cookie else ""
            self.sid, self.session, self.fresh = self.sessions.get(sid)
            nonce = secure.new_token()
            self._respond(landing(self.session.token, nonce, self.path.endswith("demo=1")).encode(),
                          content_type="text/html; charset=utf-8", nonce=nonce)
        elif self.path == "/api/state":
            if self._session():
                with self.session.lock:
                    snap = self.session.phone.state()
                self._json(snap)
        else:
            self._deny(404, "not found", "path")

    def _post(self):
        if not self._boundary() or not self._ip_allowed():
            return
        if self.path != "/api":
            self._deny(404, "not found", "path")
            return
        if not secure.json_type_ok(self._one("Content-Type")):
            self._deny(415, "unsupported media type", "type")
            return
        try:
            data = secure.read_json_body(self.rfile, self.headers)
        except secure.BodyError as exc:
            self._deny(exc.status, exc.message, "body")
            return
        action = data.get("action")
        if not isinstance(action, str) or action not in ACTIONS:
            self._deny(404, "not found", "action")
            return
        for key, maximum in (("number", 64), ("name", 64), ("time", 64), ("text", 1000)):
            if isinstance(data.get(key), str) and len(data[key]) > maximum:
                self._deny(400, "bad request", "size")
                return
        if not self._session():
            return
        session = self.session
        if not session.actions.allow("post") or (action in web._RATE_ACTIONS and not session.actions.allow(action)):
            self._deny(429, "too many requests", "rate")
            return
        if action == "ask" and not isinstance(data.get("text"), str):
            self._deny(400, "bad request", "shape")
            return
        valid = True
        with session.lock:
            if self.fresh:
                # Expiry/eviction is a reset, never an implicit replay of an old action.
                snap = session.phone.state()
                if action in {"ask", "plan", "explain"}:
                    snap.update(session.phone.reply(action))
            elif action in {"ask", "plan", "explain"}:
                snap = session.phone.reply(action)  # 'web' is ignored; no network capability exists.
            else:
                try:
                    valid = web.apply_phone_action(session.phone, data)
                except ValueError:
                    valid = False
                if valid:
                    snap = session.phone.state()
        if not valid:
            self._deny(400, "bad request", "shape")
            return
        self._json(snap)

    def _no(self):
        if self._admit_request() and self._boundary() and self._ip_allowed():
            self._deny(404, "not found", "method")

    do_HEAD = do_OPTIONS = do_PUT = do_PATCH = do_DELETE = do_TRACE = do_CONNECT = _no

    def send_error(self, code, message=None, explain=None):
        if code == 501:  # Unknown methods cannot fall through to a local route.
            self._no()
        else:
            super().send_error(code, message, explain)


class PublicServer(web._Server):
    """Retain bounded admission without HTTPServer's reverse-DNS startup lookup."""
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


def make_server(port, hosts, *, trust_proxy=False, sessions=None, ip_limits=None):
    """The sole explicit public bind path. Does not call or weaken web.make_server."""
    allowed = parse_hosts(hosts)
    class Bound(Handler):
        pass
    Bound.hosts = allowed
    Bound.trust_proxy = trust_proxy
    Bound.sessions = sessions if sessions is not None else Sessions()
    Bound.ip_limits = ip_limits if ip_limits is not None else IPLimits()
    return PublicServer(("0.0.0.0", port), Bound)


def main():
    try:
        port = int(os.environ.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be 1 to 65535")
        server = make_server(port, os.environ.get("OPOS_PUBLIC_HOSTS", ""),
                             trust_proxy=os.environ.get("OPOS_TRUST_PROXY") == "1")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"OPOS public simulation listening on 0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
