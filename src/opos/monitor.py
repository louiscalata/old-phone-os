# SPDX-License-Identifier: GPL-3.0-or-later
"""Inference monitor data for the OPOS kiosk.

Read only. The page shows two things and keeps them apart:

* **This Duo**: whether a model server answers on this phone (loopback only), and the battery numbers the
  caller passes in. "NO MODEL" is said only when every address of the configured model port refuses a
  connection; anything unreadable is UNKNOWN.
* **AGIW monitor** (optional): a read-only ``GET /api/snapshot`` from an AGIW Inference Monitor that the
  owner points ``OPOS_AGIW_URL`` at. Unset means nothing is fetched.

Everything a remote document says is untrusted. Fields are copied through an allow-list into plain strings
and clamped numbers; the raw document never leaves this module. A value that is missing or fails its check
is unknown, never zero, idle or none.

Every network step has a real deadline. A watchdog cuts the socket it owns, so a host that drips bytes,
floods 1xx lines or trailers, or stalls a TLS handshake cannot hold the collector. A collection that is
somehow still old is shown as not current, never as live.

Standard library only (Python 3.11 to 3.14). ``Monitor.snapshot()`` is thread-safe and never raises. It must
be called outside the request handler's lock, because a slow LAN host can take several seconds.
"""
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse

SNAPSHOT_PATH = "/api/snapshot"
CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 3.0
TOTAL_DEADLINE = 6.0   # the whole remote fetch: name lookup, every address, headers and body
DNS_DEADLINE = 3.0     # of which the name lookup may use at most this much
MAX_BYTES = 256 * 1024
CACHE_SECONDS = 2.0
LOCAL_DEFAULT_URL = "http://127.0.0.1:8080/v1"
LOCAL_TIMEOUT = 1.0
LOCAL_TOTAL = 2.0      # the whole local probe: headers, 1xx lines and body
STALE_AFTER = CACHE_SECONDS + LOCAL_TOTAL + TOTAL_DEADLINE + 2   # a collection older than this is not current
MAX_MACHINES = 8
MAX_MODELS = 8
MORE = "and more"

# Built from octets so the repository's privacy guard does not read them as addresses.
_PRIVATE_NETS = (
    ipaddress.ip_network((bytes((10, 0, 0, 0)), 8)),
    ipaddress.ip_network((bytes((172, 16, 0, 0)), 12)),
    ipaddress.ip_network((bytes((192, 168, 0, 0)), 16)),
    ipaddress.ip_network("fc00::/7"),
)

# Plain words for every machine reason code AGIW can send (kept in step with AGIW web/monitor-board.mjs).
REASON_WORDS = {
    "observed": "observed", "reported_down": "reported down", "reported_degraded": "reported degraded",
    "not_observed": "never observed", "not_connected": "went silent", "unavailable": "unavailable",
    "timeout": "timed out", "refused": "connection refused", "non_json": "unreadable reply",
    "oversize": "reply too large", "schema_invalid": "unexpected reply", "stale": "reading too old",
    "clock_skew": "clock out of step", "host_key_mismatch": "host key changed", "share_unmounted": "share not mounted",
    "asleep": "asleep", "untrusted": "not trusted", "unverified": "not verified",
    "identity_mismatch": "identity does not match", "read_error": "read failed",
}

# What a failed fetch is called on the page. Fixed words only; nothing from the remote host is echoed.
FETCH_WORDS = {
    "refused-address": ("REFUSED", "That address is not on a private network, so this phone will not contact it."),
    "unreachable": ("UNREACHABLE", "The monitor did not answer."),
    "timeout": ("TIMED OUT", "The monitor took too long to answer."),
    "certificate": ("REFUSED", "This phone does not trust the monitor's certificate."),
    "redirect": ("REFUSED", "The monitor tried to send this phone somewhere else. Redirects are not followed."),
    "status": ("UNKNOWN", "The monitor answered with an error."),
    "type": ("UNKNOWN", "The monitor answered with something that is not JSON."),
    "too-large": ("UNKNOWN", "The monitor's answer was larger than this phone reads."),
    "not-json": ("UNKNOWN", "The monitor's answer could not be read."),
    "invalid": ("UNKNOWN", "The monitor's answer was not the expected shape."),
    "bad-config": ("NOT CONFIGURED", "The monitor address in the phone's settings is not usable."),
}


class RemoteConfigError(ValueError):
    pass


class FetchError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code if code in FETCH_WORDS else "invalid"


# ---------------------------------------------------------------- cleaning

# fullmatch, not match with $: "$" also matches just before a final newline.
_TEXT = re.compile(r"[A-Za-z0-9 _.:/+@()\[\]-]+")
_REASON = re.compile(r"[a-z_]{1,40}")
_TRUST = re.compile(r"[a-z-]{1,20}")


def clean_text(value, limit=40):
    """A short printable-ASCII string, or None. Never HTML-escaped: the page renders with textContent only."""
    if not isinstance(value, str) or not value or not _TEXT.fullmatch(value):
        return None
    return value[:limit]


def clean_number(value, low, high):
    """A finite number within [low, high], or None. Booleans are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if value < low or value > high:
        return None
    return value


def clean_int(value, low, high):
    number = clean_number(value, low, high)
    if number is None or number != int(number):
        return None
    return int(number)


def reason_words(code):
    if not isinstance(code, str) or not _REASON.fullmatch(code):
        return "not observed"
    return REASON_WORDS.get(code, code.replace("_", " "))


def _rate(value):
    number = clean_number(value, 0, 1_000_000)
    if number is None:
        return "Not measured"
    return f"{number:.1f} tok/s" if number < 10 else f"{round(number)} tok/s"


def _seconds(value):
    number = clean_number(value, 0, 7 * 86400)
    return "Unknown" if number is None else f"{round(number)} s"


# ---------------------------------------------------------------- remote address rules

class Remote:
    __slots__ = ("scheme", "host", "port", "host_header")

    def __init__(self, scheme, host, port, host_header):
        self.scheme, self.host, self.port, self.host_header = scheme, host, port, host_header

    @property
    def label(self):
        return f"{self.host}:{self.port}" if ":" not in self.host else f"[{self.host}]:{self.port}"


_HOST_HEADER = re.compile(r"(?:[A-Za-z0-9.-]{1,100}|\[[0-9A-Fa-f:.]{2,45}\])(?::[0-9]{1,5})?")


def parse_remote(url, host_header=None):
    """http(s)://host[:port] only. No userinfo, path, query or fragment; the path is fixed by this module."""
    if not isinstance(url, str) or not url.strip() or len(url) > 200 or not url.isascii() or not url.isprintable():
        raise RemoteConfigError("address")
    url = url.strip()
    if "?" in url or "#" in url or "@" in url or "\\" in url:
        raise RemoteConfigError("address")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise RemoteConfigError("address") from None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise RemoteConfigError("address")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise RemoteConfigError("address")
    port = port or (443 if parts.scheme == "https" else 80)
    header = None
    if host_header:
        if not isinstance(host_header, str) or not _HOST_HEADER.fullmatch(host_header.strip()):
            raise RemoteConfigError("host-header")
        header = host_header.strip()
    return Remote(parts.scheme, host, port, header)


def address_allowed(ip):
    """Loopback, RFC 1918, link-local and unique-local only. Public, unspecified and multicast are refused."""
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified or ip.is_multicast:
        return False
    return ip.is_loopback or ip.is_link_local or any(ip in net for net in _PRIVATE_NETS)


def _resolve_bounded(host, port, resolver, seconds):
    """A name lookup that cannot hold the caller longer than `seconds` (getaddrinfo has no timeout of its own)."""
    box = {}

    def work():
        try:
            box["infos"] = resolver(host, port, type=socket.SOCK_STREAM)
        except BaseException as error:  # handed back to the caller's thread
            box["error"] = error

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(max(0.0, seconds))
    if worker.is_alive():
        raise FetchError("timeout")
    if "error" in box:
        if isinstance(box["error"], (OSError, UnicodeError)):
            raise FetchError("unreachable") from None
        raise box["error"]
    return box["infos"]


def resolve_allowed(host, port, resolver=socket.getaddrinfo, timeout=DNS_DEADLINE):
    """Every address the name resolves to must be allowed; returns [(family, sockaddr), ...] or raises."""
    infos = _resolve_bounded(host, port, resolver, timeout)
    found = []
    for family, _kind, _proto, _name, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        except ValueError:
            raise FetchError("refused-address") from None
        if not address_allowed(ip):
            raise FetchError("refused-address")
        found.append((family, sockaddr))
    if not found:
        raise FetchError("unreachable")
    return found


# ---------------------------------------------------------------- fetching

def _cut(connection):
    """Shuts down the socket a connection owns, from any thread, whatever phase it is in."""
    live = getattr(connection, "live", None)
    if live is not None:
        try:
            live.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class _Pinned(http.client.HTTPConnection):
    """Connects to an address that was already checked, so the name cannot change between check and use.

    `live` is this connection's own reference to its socket. http.client drops `self.sock` after a reply that
    says Connection: close, so a watchdog reading `self.sock` would miss a body or trailer that drips.
    """

    def __init__(self, family, sockaddr, host, port, tls, connect_timeout=CONNECT_TIMEOUT):
        super().__init__(host, port, timeout=READ_TIMEOUT)
        self._pin = (family, sockaddr)
        self._tls = tls
        self._tls_host = host
        self._connect_timeout = connect_timeout
        self.live = None

    def connect(self):
        family, sockaddr = self._pin
        sock = self.live = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self._connect_timeout)
            sock.connect(sockaddr)
            sock.settimeout(READ_TIMEOUT)
            if self._tls:
                # The handshake is done by hand so the watchdog can cut a handshake that drips, too.
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self._tls_host, do_handshake_on_connect=False)
                self.live = sock
                sock.do_handshake()
        except BaseException:
            self.live.close()
            raise
        self.sock = sock


def fetch_snapshot(remote, resolver=socket.getaddrinfo, clock=time.monotonic):
    """GET <remote>/api/snapshot. Returns the parsed JSON object or raises FetchError(code).

    The whole call, name lookup included, is held to TOTAL_DEADLINE by a watchdog per address.
    """
    deadline = clock() + TOTAL_DEADLINE
    addresses = resolve_allowed(remote.host, remote.port, resolver, min(DNS_DEADLINE, TOTAL_DEADLINE))
    host_header = remote.host_header or (f"{remote.host}:{remote.port}" if ":" not in remote.host else f"[{remote.host}]:{remote.port}")
    last = "unreachable"
    untrusted = False
    for family, sockaddr in addresses:
        remaining = deadline - clock()
        if remaining <= 0:
            raise FetchError("timeout")
        conn = _Pinned(family, sockaddr, remote.host, remote.port, remote.scheme == "https",
                       connect_timeout=min(CONNECT_TIMEOUT, remaining))
        cut = threading.Event()

        def watchdog(conn=conn, cut=cut):
            cut.set()
            _cut(conn)

        timer = threading.Timer(remaining, watchdog)
        timer.daemon = True
        timer.start()
        try:
            conn.putrequest("GET", SNAPSHOT_PATH, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host_header)
            conn.putheader("Accept", "application/json")
            conn.putheader("User-Agent", "opos-monitor/1")
            conn.putheader("Connection", "close")
            conn.endheaders()
            response = conn.getresponse()
            if 300 <= response.status < 400:
                raise FetchError("redirect")
            if response.status != 200:
                raise FetchError("status")
            kind = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if kind != "application/json":
                raise FetchError("type")
            length = response.getheader("Content-Length")
            if length and length.isascii() and length.isdigit():
                digits = length.lstrip("0")
                # Compared as text first: int() refuses strings of more than 4300 digits.
                if len(digits) > len(str(MAX_BYTES)) or (digits and int(digits) > MAX_BYTES):
                    raise FetchError("too-large")
            chunks, size = [], 0
            while True:
                if clock() > deadline:
                    raise FetchError("timeout")
                chunk = response.read(8192)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise FetchError("too-large")
                chunks.append(chunk)
            if cut.is_set():
                # A cut body ends as a clean EOF; what was read is a fragment, not the monitor's answer.
                raise FetchError("timeout")
            return _parse_json(b"".join(chunks))
        except FetchError as error:
            if cut.is_set():
                raise FetchError("timeout") from None
            if error.code in ("unreachable", "timeout"):
                last = error.code
                continue
            raise
        except ssl.SSLCertVerificationError:
            # Before OSError: it is one. A name the phone does not trust is not a monitor that did not answer.
            if cut.is_set():
                raise FetchError("timeout") from None
            last = "certificate"
            untrusted = True
        except (socket.timeout, TimeoutError):
            last = "timeout"
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            # The watchdog's shutdown surfaces as a disconnect or a short read; say what really happened.
            if cut.is_set():
                raise FetchError("timeout") from None
            if (isinstance(error, http.client.HTTPException)
                    and not isinstance(error, (http.client.RemoteDisconnected, http.client.IncompleteRead))):
                # It answered, but not in HTTP: a garbled status line, too many interim replies, a header too long.
                raise FetchError("invalid") from None
            last = "unreachable"
        finally:
            timer.cancel()
            conn.close()
    # An untrusted certificate on one address is not hidden by a later address that simply did not answer.
    raise FetchError("certificate" if untrusted and last == "unreachable" else last)


def _reject_constant(_name):
    raise ValueError("not a JSON number")


def _parse_json(raw):
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise FetchError("not-json") from None
    if not isinstance(value, dict):
        raise FetchError("invalid")
    return value


# ---------------------------------------------------------------- this phone

def local_models_url(env):
    """OPOS_LOCAL_URL when it is http on 127.0.0.1 or localhost (as assist.local_route reads it), else the default."""
    url = (env or {}).get("OPOS_LOCAL_URL") or LOCAL_DEFAULT_URL
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost") or parts.username or parts.password:
            raise ValueError
        port = parts.port or 80
    except ValueError:
        parts = urllib.parse.urlsplit(LOCAL_DEFAULT_URL)
        port = parts.port
    path = (parts.path or "").rstrip("/") + "/models"
    return parts.hostname, port, path


class _Live(http.client.HTTPConnection):
    """Keeps its own reference to the socket, for the same reason as _Pinned."""

    live = None

    def connect(self):
        super().connect()
        self.live = self.sock


def _every_address_refuses(host, port, resolver=socket.getaddrinfo):
    """True only if every address the name resolves to actively refuses a connection."""
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    if not infos:
        return False
    for family, _kind, _proto, _name, sockaddr in infos:
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(LOCAL_TIMEOUT)
            sock.connect(sockaddr)
        except ConnectionRefusedError:
            continue
        except OSError:
            return False
        else:
            return False
        finally:
            sock.close()
    return True


def _probe_request(conn, path):
    conn.request("GET", path, headers={"Accept": "application/json", "User-Agent": "opos-monitor/1"})
    response = conn.getresponse()
    if response.status != 200:
        return "unknown", []
    body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        return "unknown", []
    payload = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return "unknown", []
    ids = []
    for entry in entries[:MAX_MODELS]:
        name = clean_text(entry.get("id") if isinstance(entry, dict) else None, 60)
        if name:
            ids.append(name)
    if entries and not ids:
        return "unknown", []
    if len(entries) > MAX_MODELS and ids:
        ids.append(MORE)
    return ("model", ids) if ids else ("empty", [])


def probe_local(env=None, resolver=socket.getaddrinfo):
    """('model', [ids]) | ('empty', []) | ('none', []) | ('unknown', []).

    'none' means every address of the configured port actively refused: the only answer that justifies saying
    no model server is there. A probe that is cut by its deadline is always 'unknown'.
    """
    host, port, path = local_models_url(env)
    conn = _Live(host, port, timeout=LOCAL_TIMEOUT)
    fired = threading.Event()

    def watchdog():
        fired.set()
        _cut(conn)

    timer = threading.Timer(LOCAL_TOTAL, watchdog)
    timer.daemon = True
    timer.start()
    try:
        outcome = _probe_request(conn, path)
    except ConnectionRefusedError:
        outcome = ("refused", [])
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        outcome = ("unknown", [])
    finally:
        timer.cancel()
        conn.close()
    if fired.is_set():
        # A cut read can return a short body without an error; it must never become "model" or "none".
        return "unknown", []
    if outcome[0] == "refused":
        return ("none", []) if _every_address_refuses(host, port, resolver) else ("unknown", [])
    return outcome


# ---------------------------------------------------------------- remote document to plain rows

_PLATFORMS = {"mac": "MAC", "windows": "PC", "linux": "LINUX"}
_STATES = {"up": "UP", "down": "DOWN"}


def machine_rows(document):
    rows = []
    raw = document.get("machines")
    for entry in (raw if isinstance(raw, list) else [])[:MAX_MACHINES]:
        if not isinstance(entry, dict):
            # Keep the position: the first row is the machine that served the document, and an unreadable
            # first row must not promote a peer to that role.
            entry = {}
        state_raw = entry.get("state")
        state = _STATES.get(state_raw, "UNKNOWN") if isinstance(state_raw, str) else "UNKNOWN"
        degraded = state == "UP" and entry.get("reasonCode") == "reported_degraded"
        platform_raw = entry.get("platform")
        platform = platform_raw if isinstance(platform_raw, str) and platform_raw in _PLATFORMS else None
        host = clean_text(entry.get("host"), 24)
        complete = entry.get("modelsComplete") is True
        loaded = clean_int(entry.get("loadedCount"), 0, 256) if complete else None
        busy = clean_int(entry.get("busyCount"), 0, 256) if complete else None
        trust_raw = entry.get("trust")
        trust = trust_raw if isinstance(trust_raw, str) and _TRUST.fullmatch(trust_raw) else None
        age = clean_number(entry.get("ageSeconds"), 0, 7 * 86400)
        fresh_for = clean_number(entry.get("freshForSeconds"), 0, 7 * 86400)
        rows.append({
            "name": _PLATFORMS.get(platform) or (host or "MACHINE").upper()[:12],
            "host": host,
            "state": "DEGRADED" if degraded else state,
            "reason": "observed" if state == "UP" and not degraded else reason_words(entry.get("reasonCode")),
            "trust": trust,
            "age": age,
            "freshFor": fresh_for,
            "loaded": loaded,
            "busy": busy,
            "prompt": entry.get("promptTokensPerSecond"),
            "generation": entry.get("generationTokensPerSecond"),
            "handsOff": entry.get("handsOff"),
        })
    return rows


def peer_map(rows, fresh):
    """The machine rows as monospace text: first row is the machine that served the document."""
    if not rows:
        return "MACHINES  UNKNOWN\n          no machine rows in this sample"

    def describe(row):
        state = row["state"]
        link = "-x-" if state == "DOWN" else "-?-" if state == "UNKNOWN" else "==="
        parts = []
        if state == "UNKNOWN":
            parts.append(row["reason"])
        elif state == "DOWN":
            parts.append("reported down")
        elif row["loaded"] is not None:
            parts.append(f"{row['loaded']} loaded / {row['busy'] if row['busy'] is not None else '?'} busy")
        else:
            parts.append("model list incomplete")
        if row["trust"]:
            parts.append(row["trust"])
        if row["age"] is not None:
            parts.append(f"{round(row['age'])} s")
        return link, " · ".join(parts)

    _hub_link, hub_detail = describe(rows[0])
    lines = [f"{rows[0]['name']:<14}{rows[0]['state']:<9}{hub_detail}"]
    for row in rows[1:]:
        link, detail = describe(row)
        lines.append(f" |{link} {row['name']:<8} {row['state']:<9}{detail}")
    if len(rows) == 1:
        lines.append(" |    no other machine in this sample")
    if not fresh:
        lines.append("          last sample, not live")
    return "\n".join(lines)


def remote_section(document, label, fetched_ago, stale=False):
    rows = machine_rows(document)
    hub = rows[0] if rows else None
    fresh = bool(hub and hub["state"] in ("UP", "DEGRADED") and hub["age"] is not None
                 and hub["freshFor"] is not None and hub["age"] <= hub["freshFor"]) and not stale
    if hub is None:
        status, glyph = "UNKNOWN", "[?]"
    elif fresh:
        status, glyph = "LIVE", "[x]"
    else:
        status, glyph = "STALE", "[?]"
    lines = [f"AGIW monitor at {label}, fetched {fetched_ago} s ago."]
    if hub is None:
        lines.append("The answer had no machine rows. Nothing about those machines is known.")
    elif stale:
        lines.append("This answer is old, so these numbers are not live.")
    elif hub["state"] == "DEGRADED":
        lines.append("This monitor reports itself degraded.")
    elif not fresh:
        lines.append("The monitor's own sample is old or unverified, so its numbers are not live.")
    raw_machines = document.get("machines")
    if isinstance(raw_machines, list) and len(raw_machines) > MAX_MACHINES:
        lines.append("More machines are reported than are shown here.")
    facts = []
    for row in rows:
        name = row["name"]
        facts.append([name, f"{row['state']} · {row['reason']}"])
        if row["loaded"] is not None:
            facts.append([f"{name} models", f"{row['loaded']} loaded" + (f" ({row['busy']} busy)" if row["busy"] is not None else "")])
        else:
            facts.append([f"{name} models", "Model list incomplete"])
        facts.append([f"{name} prompts", _rate(row["prompt"])])
        facts.append([f"{name} writing", _rate(row["generation"])])
        facts.append([f"{name} age", _seconds(row["age"])])
    models = []
    raw_models = document.get("models")
    raw_models = raw_models if isinstance(raw_models, list) else []
    for entry in raw_models[:MAX_MODELS]:
        if not isinstance(entry, dict):
            continue
        name = clean_text(entry.get("name") or entry.get("id"), 48)
        if not name:
            continue
        loaded = entry.get("loaded")
        models.append(f"{name} · " + ("loaded" if loaded is True else "not loaded" if loaded is False else "unknown"))
    if models:
        lines.append("Models on that monitor: " + "; ".join(models + ([MORE] if len(raw_models) > MAX_MODELS else [])) + ".")
    return {"id": "remote", "title": "AGIW monitor", "status": status, "glyph": glyph, "lines": lines,
            "facts": facts[:40], "pre": peer_map(rows, fresh)}


# ---------------------------------------------------------------- the monitor

def _local_section(result, local_state, checked_ago, address, stale=False):
    kind, names = result
    if stale:
        status, glyph = "UNKNOWN", "[?]"
        text = f"Last checked {checked_ago} s ago. That is not current."
    elif kind == "model":
        status, glyph, text = "MODEL LISTED", "[x]", f"A model server at {address} lists: " + "; ".join(names) + "."
    elif kind == "empty":
        status, glyph, text = "NO MODEL LISTED", "[ ]", f"A model server at {address} answers, but lists no model."
    elif kind == "none":
        status, glyph, text = "NO MODEL", "[ ]", f"Nothing is listening on {address}, so no model server answers there."
    else:
        status, glyph, text = "UNKNOWN", "[?]", f"The model server at {address} did not give a readable answer."
    state = local_state if isinstance(local_state, dict) else {}
    percent = clean_number(state.get("percent"), 0, 100)
    health = clean_number(state.get("healthPercent"), 0, 100)
    source = clean_text(state.get("source"), 24)
    facts = [
        ["Battery", "Not reported" if percent is None else f"{round(percent)}%"],
        ["Battery health", "Not reported" if health is None else f"{round(health)}%"],
        ["Battery source", source or "Not reported"],
        ["Model server checked", f"{checked_ago} s ago"],
    ]
    return {"id": "duo", "title": "This Duo", "status": status, "glyph": glyph, "lines": [text], "facts": facts, "pre": None}


def _remote_error_section(code, label):
    status, text = FETCH_WORDS.get(code, FETCH_WORDS["invalid"])
    where = f" at {label}" if label else ""
    return {"id": "remote", "title": "AGIW monitor", "status": status, "glyph": "[?]",
            "lines": [f"AGIW monitor{where}: {text}", "Nothing about other machines is known."], "facts": [], "pre": None}


def _unconfigured_section():
    return {"id": "remote", "title": "AGIW monitor", "status": "NOT CONFIGURED", "glyph": "[ ]",
            "lines": ["No AGIW monitor is configured on this phone.", "Nothing about other machines is known."], "facts": [], "pre": None}


def _checking_section(title, what):
    return {"id": "duo" if title == "This Duo" else "remote", "title": title, "status": "CHECKING", "glyph": "[?]",
            "lines": [f"Checking {what}. Nothing is known yet."], "facts": [], "pre": None}


class Monitor:
    """Caches one collection for CACHE_SECONDS and lets one caller at a time do the collecting."""

    def __init__(self, env=None, *, fetch=fetch_snapshot, probe=probe_local, clock=time.monotonic):
        self._env = dict(os.environ if env is None else env)
        self._fetch, self._probe, self._clock = fetch, probe, clock
        self._lock = threading.Lock()
        self._cache = None
        self._busy = False
        self._remote, self._config_error = None, False
        url = self._env.get("OPOS_AGIW_URL")
        if url:
            try:
                self._remote = parse_remote(url, self._env.get("OPOS_AGIW_HOST"))
            except RemoteConfigError:
                self._config_error = True

    def _collect(self):
        try:
            local = self._probe(self._env)
        except Exception:
            local = ("unknown", [])
        local_at = self._clock()
        remote = ("off", None)
        if self._config_error:
            remote = ("error", "bad-config")
        elif self._remote is not None:
            with self._lock:
                if self._cache is None:
                    # First look: show the phone's own answer now instead of waiting for the remote fetch.
                    self._cache = {"at": local_at, "local_at": local_at, "local": local, "remote": ("checking", None)}
            try:
                remote = ("ok", self._fetch(self._remote))
            except FetchError as error:
                remote = ("error", error.code)
            except Exception:
                remote = ("error", "unreachable")
        return {"at": self._clock(), "local_at": local_at, "local": local, "remote": remote}

    def _view(self, cache, local_state, now):
        ago = max(0, round(now - cache["at"]))
        local_ago = max(0, round(now - cache.get("local_at", cache["at"])))
        address = "%s:%s" % local_models_url(self._env)[:2]
        sections = [_local_section(cache["local"], local_state, local_ago, address, stale=local_ago > STALE_AFTER)]
        kind, value = cache["remote"]
        label = self._remote.label if self._remote else None
        try:
            if kind == "ok":
                sections.append(remote_section(value, label, ago, stale=ago > STALE_AFTER))
            elif kind == "error":
                sections.append(_remote_error_section(value, label))
            elif kind == "checking":
                sections.append(_checking_section("AGIW monitor", "the AGIW monitor"))
            else:
                sections.append(_unconfigured_section())
        except Exception:
            # A document that got past the checks but still broke a reader must not take the phone's own section with it.
            sections.append(_remote_error_section("invalid", label))
        return {"version": 1, "title": "Inference", "updatedAgoSeconds": ago, "sections": sections}

    def snapshot(self, local_state=None):
        try:
            return self._snapshot(local_state)
        except Exception:
            return {"version": 1, "title": "Inference", "updatedAgoSeconds": None, "sections": [
                {"id": "duo", "title": "This Duo", "status": "UNKNOWN", "glyph": "[?]",
                 "lines": ["The monitor could not build this view. Nothing is known."], "facts": [], "pre": None}]}

    def _snapshot(self, local_state):
        collect = False
        now = self._clock()
        with self._lock:
            cache = self._cache
            if cache is not None and now - cache["at"] < CACHE_SECONDS:
                pass
            elif self._busy:
                pass
            else:
                self._busy = True
                collect = True
        if collect:
            fresh = None
            try:
                fresh = self._collect()
            finally:
                with self._lock:
                    self._busy = False
                    if fresh is not None:
                        self._cache = fresh
            cache = fresh if fresh is not None else self._cache
        now = self._clock()
        if cache is None:
            return {"version": 1, "title": "Inference", "updatedAgoSeconds": None, "sections": [
                _checking_section("This Duo", "this phone")]}
        return self._view(cache, local_state, now)


_default = None
_default_lock = threading.Lock()


def get():
    """The process-wide monitor, built from the environment on first use."""
    global _default
    with _default_lock:
        if _default is None:
            _default = Monitor()
        return _default
