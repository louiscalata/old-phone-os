# SPDX-License-Identifier: GPL-3.0-or-later
"""Over-the-air updates, the unprivileged half (docs/ota-update.md, "Client").

OPOS only delivers. It asks the owner's chosen source whether a newer bundle exists and drops the bundle in
/var/lib/opos-update/inbox. Root verifies it (signature, serial, every file hash) and applies it; nothing here is
trusted, and nothing here verifies for trust.

Opt-in. With no /etc/opos/update.json nothing is ever fetched. The file is JSON:
{"url": "<address of latest.json>", "auto_check": false, "allow_lan_http": false}.
https only; http only for this phone or a private network (127.0.0.0/8, ::1, RFC 1918, fc00::/7, the name localhost)
and only when allow_lan_http is true. A redirect may not leave the scheme, host and port it started from. Proxy
settings in the environment are ignored. A bundle is at most 256 MiB. The only background work is one index check
every 6 hours when auto_check is true; it never downloads.

Web surface (opos.web): GET /update (the page; local files only), GET /api/update (token; the snapshot below),
POST /api {"action": "update_check" | "update_download" | "update_rollback"} (token, origin, rate limited). Each action
answers the snapshot plus "ok": true, or {"error", "code"} with the status of the UpdateError. update_download refetches
the index itself, so it never trusts an old check; it answers at once and a thread streams the bundle (progress in "job").

snapshot() keys: available, message, running {version, revision, serial, release}, source {configured, url, auto_check,
allow_lan_http, error}, check (the last check: time, ok, newer, serial, version, size, message; or null), job {kind,
phase, bytes, total, serial, message}, pending (bundles waiting in the inbox), stalled (a bundle or rollback request has
waited so long that the updater is probably not running), rollback {possible, requested, and once: true when the only
thing against it is that the phone already went back once since the last update; and, only while a boot-scope update is
staged, staged: its serial, and possible is then true whatever the earlier releases; see below}, status
(status.json: phase, message, serial, time; or null).

A rollback request is the one thing this unprivileged side can ask of root that moves the phone to older code, so it
is limited: root honours one per applied update and does not burn the serial it leaves (docs/ota-update.md, trust model).
While a boot-scope update is staged, the page offers to cancel it instead: the request file then holds the text
`cancel-staged` (written as rollback.request.partial and renamed, so root never reads it half written), which the updater
takes as "drop the staged update and touch nothing that is installed". That needs no earlier release, is not subject to the
one-step limit and burns no serial (docs/ota-update.md). With an update staged the empty request is not written: cancel
first, then go back if that is wanted. The root updater is part of the image and this client is part of the app, so a new
client can run on an old updater, which reads any request file as "go back" whatever it holds; the cancel form is therefore
offered only when state.json shows the updater that wrote it knows the form (it keeps a `floor`, which the older one does not).
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum

from opos import notices
from opos.secure import clean_text

MAX_BUNDLE = 256 * 1024 * 1024
MAX_INDEX = 64 * 1024
MAX_SMALL = 64 * 1024  # config.json, state.json, status.json
MAX_SERIAL = 2 ** 31 - 1
CHUNK = 64 * 1024
BUNDLE_EXT = ".opos-update"
ROLLBACK_REQUEST = "rollback.request"
CANCEL_STAGED = b"cancel-staged"  # what rollback.request holds to cancel a staged update and nothing else (empty: go back)
STALL_S = 300.0  # a delivered bundle or request that root has not taken in this long: the updater is not running
AUTO_FIRST_S = 120.0
AUTO_EVERY_S = 6 * 3600.0
NOT_AVAILABLE = "Updates are not available on this install."
ONCE_ONLY = "This phone already went back once since its last update. Install an update before going back again."
NOT_CONFIGURED = "No update source is set. OPOS contacts no server for updates until one is set in /etc/opos/update.json."
_PHASES = ("idle", "verifying", "applying", "checking", "done", "rolled-back", "failed")
_URL_OK = re.compile(r"[\x21-\x5b\x5d-\x7e]{1,2048}")  # printable ASCII; no space, control character or backslash
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+~-]{0,63}")
_REVISION = re.compile(r"[0-9a-f]{7,40}")
_RELEASE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,79}")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,79}")
# RFC 1918 built from numbers so no address literal sits in the source.
_LAN4 = (ipaddress.ip_network((10 << 24, 8)), ipaddress.ip_network(((172 << 24) | (16 << 16), 12)),
         ipaddress.ip_network(((192 << 24) | (168 << 16), 16)))
_ULA6 = ipaddress.ip_network("fc00::/7")
_HEADERS = {"User-Agent": "opos-update/1", "Accept": "*/*", "Accept-Encoding": "identity"}
_log = logging.getLogger("opos.update")


class UpdateError(Exception):
    """A short, safe, owner-readable failure with a stable code and the HTTP status the web layer answers."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class JobState(Enum):
    """Client work only; delivered says nothing about root's independent result."""
    IDLE = "idle"
    CHECKING = "checking"  # internal only: the legacy job stays unchanged during a check
    DOWNLOADING = "downloading"
    DELIVERED = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class Paths:
    """Where the client reads and writes. Tests pass temporary folders."""
    config: str = "/etc/opos/update.json"
    root: str = "/var/lib/opos-update"
    revision: str = "/opt/opos/REVISION"

    @property
    def inbox(self) -> str:
        return os.path.join(self.root, "inbox")

    @property
    def state(self) -> str:
        return os.path.join(self.root, "state.json")

    @property
    def status(self) -> str:
        return os.path.join(self.root, "status.json")


@dataclass(frozen=True)
class Limits:
    """Caps and timeouts, injectable so tests need not move 256 MiB or wait minutes."""
    max_bundle: int = MAX_BUNDLE
    max_index: int = MAX_INDEX
    io_timeout: float = 15.0  # one socket operation
    index_deadline: float = 30.0  # the whole index fetch
    bundle_deadline: float = 3600.0  # the whole bundle download
    headroom: int = 32 * 1024 * 1024  # free space to leave after the bundle
    space_step: int = 1024 * 1024  # bytes written between two looks at the free space while a bundle streams in


@dataclass(frozen=True)
class Config:
    url: str
    auto_check: bool = False
    allow_lan_http: bool = False


@dataclass(frozen=True)
class Index:
    serial: int
    version: str
    url: str  # absolute, on the index's own origin
    sha256: str | None = None
    size: int | None = None


def _loads(raw: bytes):
    """Strict JSON: UTF-8, no NaN or Infinity, no duplicate keys."""
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError("duplicate key")
            out[k] = v
        return out

    def bad(_c):
        raise ValueError("bad constant")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=bad)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError("not valid JSON")


def _read_small(path: str, cap: int = MAX_SMALL) -> bytes:
    """Read a small regular file; FileNotFoundError when absent, ValueError when odd or large."""
    with open(path, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("not a regular file")
        data = f.read(cap + 1)
    if len(data) > cap:
        raise ValueError("file too large")
    return data


def _int(v):
    return v if type(v) is int and 0 <= v <= MAX_SERIAL else None


def _origin(u: urllib.parse.SplitResult):
    return (u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80))


def lan_host(host: str) -> bool:
    """True for this phone or a private network: an IP literal or the name localhost. Names are never resolved."""
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    return any(ip in n for n in (_LAN4 if ip.version == 4 else (_ULA6,)))


def check_url(url, allow_lan_http: bool = False) -> urllib.parse.SplitResult:
    """The address policy. Raises ValueError with a safe reason (never the address itself)."""
    if not isinstance(url, str) or _URL_OK.fullmatch(url) is None:
        raise ValueError("the address is empty, too long or has unusual characters")
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        raise ValueError("the address is not valid")
    if u.scheme not in ("https", "http") or not u.hostname:
        raise ValueError("the address must start with https://")
    if port == 0 or "@" in u.netloc:
        raise ValueError("the address must not have a user name, password or port 0")
    if u.fragment:
        raise ValueError("the address must not have a # part")
    if u.scheme == "http" and not (allow_lan_http and lan_host(u.hostname)):
        raise ValueError("plain http is allowed only for this phone or a private network, and only when allow_lan_http is true")
    return u


def parse_config(raw: bytes) -> Config | None:
    """None when the file names no source; UpdateError when it is unusable."""
    try:
        d = _loads(raw)
    except ValueError:
        raise UpdateError("bad_config", "The update source file is not valid JSON.")
    if not isinstance(d, dict):
        raise UpdateError("bad_config", "The update source file must be a JSON object.")
    flags = {}
    for k in ("auto_check", "allow_lan_http"):
        v = d.get(k, False)
        if not isinstance(v, bool):
            raise UpdateError("bad_config", f"{k} in the update source file must be true or false.")
        flags[k] = v
    url = d.get("url")
    if url is None or url == "":
        return None
    try:
        check_url(url, flags["allow_lan_http"])
    except ValueError as e:
        raise UpdateError("bad_config", f"The update address cannot be used: {e}.")
    return Config(url, **flags)


def load_config(path: str) -> Config | None:
    """None when there is no file (the default: nothing is ever fetched)."""
    try:
        raw = _read_small(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise UpdateError("bad_config", "The update source file cannot be read.")
    return parse_config(raw)


def parse_index(raw: bytes, base_url: str, allow_lan_http: bool = False, max_bundle: int = MAX_BUNDLE) -> Index:
    """Strictly validate latest.json. Raises ValueError with a safe reason. Unknown keys are ignored."""
    d = _loads(raw)
    if not isinstance(d, dict):
        raise ValueError("it is not a JSON object")
    serial = _int(d.get("serial"))
    if serial is None:
        raise ValueError("serial is not a whole number")
    version = d.get("version")
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ValueError("version is not valid")
    rel = d.get("url")
    if not isinstance(rel, str) or _URL_OK.fullmatch(rel) is None:
        raise ValueError("url is not valid")
    try:
        full = check_url(urllib.parse.urljoin(base_url, rel), allow_lan_http)
    except ValueError:
        raise ValueError("url is not allowed")
    if _origin(full) != _origin(urllib.parse.urlsplit(base_url)):
        raise ValueError("url points to another host")
    sha = d.get("sha256")
    if sha is not None:
        if not isinstance(sha, str) or _SHA256.fullmatch(sha) is None:
            raise ValueError("sha256 is not valid")
        sha = sha.lower()
    size = d.get("size")
    if size is not None:
        if type(size) is not int or size < 1:
            raise ValueError("size is not valid")
        if size > max_bundle:
            raise ValueError("size is over the limit OPOS accepts")
    return Index(serial, version, full.geturl(), sha, size)


class _SameOrigin(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the same scheme, host and port, and only to an address the policy allows."""
    max_redirections = 3

    def __init__(self, allow_lan_http: bool) -> None:
        self.allow_lan_http = allow_lan_http

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            new = check_url(urllib.parse.urljoin(req.full_url, newurl), self.allow_lan_http)
            same = _origin(new) == _origin(urllib.parse.urlsplit(req.full_url))
        except ValueError:
            same = False
        if not same:
            fp.close()
            raise UpdateError("redirect", "The update server tried to send OPOS to another address, so OPOS stopped.", 502)
        return super().redirect_request(req, fp, code, msg, headers, new.geturl())


def _net_error(e: BaseException) -> UpdateError:
    """A safe message for a network failure; the exception text is never shown."""
    r = getattr(e, "reason", e)
    if isinstance(r, ssl.SSLCertVerificationError) or isinstance(e, ssl.SSLCertVerificationError):
        return UpdateError("fetch", "The update server's certificate was not accepted.", 502)
    if isinstance(r, ssl.SSLError):
        return UpdateError("fetch", "A secure connection to the update server could not be made.", 502)
    if isinstance(r, (TimeoutError, socket.timeout)):
        return UpdateError("fetch", "The update server did not answer in time.", 502)
    if isinstance(e, http.client.HTTPException):
        return UpdateError("fetch", "The update server sent a reply OPOS could not read.", 502)
    return UpdateError("fetch", "The update server could not be reached. Check the phone's network.", 502)


class UpdateClient:
    """Config, check, download, rollback request and status. One network job at a time."""

    def __init__(self, paths: Paths | None = None, limits: Limits | None = None, clock=time.time) -> None:
        self.paths = paths or Paths()
        self.limits = limits or Limits()
        self._clock = clock
        self._lock = threading.Lock()  # guards _job, _check and _thread
        self._busy = threading.Lock()  # held for the length of one network job
        self._transition("init")
        self._auto: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- what is installed and what the updater says (local files only) ----

    def available(self) -> bool:
        """Only an install with the update folder (the Duo OS image) can take updates."""
        return os.path.isdir(self.paths.root)

    def config(self) -> Config | None:
        return load_config(self.paths.config)

    def read_state(self) -> dict | None:
        """state.json reduced to what the page needs; None when it is missing or unreadable."""
        try:
            d = _loads(_read_small(self.paths.state))
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict):
            return None
        cur = d.get("current") if isinstance(d.get("current"), dict) else {}
        version, revision, release = cur.get("version"), cur.get("revision"), cur.get("release")
        failed = d.get("failed")
        failed = sorted({n for n in (_int(x) for x in failed) if n is not None}) if isinstance(failed, list) else []
        floor = d.get("floor", 0)
        previous = d.get("previous")
        staged = d.get("pending_boot")
        return {
            "serial": _int(cur.get("serial")),
            "version": version if isinstance(version, str) and _VERSION.fullmatch(version) else None,
            "revision": revision if isinstance(revision, str) and _REVISION.fullmatch(revision) else None,
            "release": release if isinstance(release, str) and _RELEASE.fullmatch(release) else None,
            "previous": len(previous) if isinstance(previous, list) else 0,
            "stepped_back": _int(d.get("stepped_back")),
            "staged": _int(staged.get("serial")) if isinstance(staged, dict) else None,
            "cancels_staged": type(d.get("floor")) is int and d["floor"] >= 0,  # a proxy, not a handshake: an updater that keeps a floor reads `cancel-staged` (docs/ota-update.md, Client)
            "failed": failed,
            "floor": max(floor, max(failed, default=0)) if type(floor) is int and floor >= 0 else None,
        }

    def read_status(self) -> dict | None:
        """status.json (written by root): phase, message, serial, time. Anything odd reads as phase "unknown"."""
        try:
            d = _loads(_read_small(self.paths.status, 8192))
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict):
            return None
        phase = d.get("phase")

        def text(k, n):
            v = d.get(k)
            return " ".join(clean_text(v, n).split()) if isinstance(v, str) else ""
        return {"phase": phase if phase in _PHASES else "unknown", "message": text("message", 300),
                "serial": _int(d.get("serial")), "time": text("time", 40) or None}

    def running(self) -> dict:
        """The version running now: state.json, /opt/opos/REVISION, then what was built into this tree."""
        st = self.read_state() or {}
        revision = None
        try:
            first = _read_small(self.paths.revision, 256).decode("ascii", "ignore").split()
            if first and _REVISION.fullmatch(first[0]):
                revision = first[0]
        except (OSError, ValueError):
            pass
        revision = revision or st.get("revision")
        if revision is None and _REVISION.fullmatch(notices.BUILD):
            revision = notices.BUILD
        return {"version": st.get("version") or notices.VERSION, "revision": revision,
                "serial": st.get("serial"), "release": st.get("release")}

    def pending(self) -> list[str]:
        """Bundles in the inbox that root has not picked up yet."""
        try:
            names = os.listdir(self.paths.inbox)
        except OSError:
            return []
        return sorted(n for n in names if n.endswith(BUNDLE_EXT) and _NAME.fullmatch(n))[:8]

    def stalled(self) -> bool:
        """A bundle or rollback request has sat in the inbox for STALL_S seconds. The updater takes one within seconds,
        so this means its path unit is not running (stopped, failed or never enabled)."""
        now = self._clock()
        for n in self.pending() + [ROLLBACK_REQUEST]:
            try:
                st = os.lstat(os.path.join(self.paths.inbox, n))
            except OSError:
                continue
            if now - max(st.st_mtime, st.st_ctime) > STALL_S:  # ctime: a rename or a copy that kept an old mtime still counts as new
                return True
        return False

    def _source(self) -> dict:
        try:
            cfg = self.config()
        except UpdateError as e:
            return {"configured": False, "url": None, "auto_check": False, "allow_lan_http": False, "error": e.message}
        if cfg is None:
            return {"configured": False, "url": None, "auto_check": False, "allow_lan_http": False, "error": None}
        u = urllib.parse.urlsplit(cfg.url)  # shown without its query, which may carry a key
        shown = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, "", "")) + ("?..." if u.query else "")
        return {"configured": True, "url": shown, "auto_check": cfg.auto_check, "allow_lan_http": cfg.allow_lan_http, "error": None}

    def snapshot(self) -> dict:
        """Everything the page shows. Reads local files only; never touches the network."""
        avail = self.available()
        st = self.read_state() if avail else None
        with self._lock:
            check = dict(self._check) if self._check else None
            job = dict(self._job)
        if check and check["ok"]:  # the answer may have changed since the check (an update was applied)
            check["newer"], check["message"] = self._verdict(check["serial"], check["version"])
        requested = avail and os.path.lexists(os.path.join(self.paths.inbox, ROLLBACK_REQUEST))
        earlier = bool(st and st["previous"] > 0)
        once = earlier and st["stepped_back"] is not None
        staged = st["staged"] if st and st["cancels_staged"] else None  # an old updater would turn the cancel into a rollback
        rollback = dict({"possible": (earlier and not once) or staged is not None, "requested": bool(requested)}, **({"once": True} if once else {}))
        if staged is not None:
            rollback["staged"] = staged
        return {
            "available": avail,
            "message": None if avail else NOT_AVAILABLE,
            "running": self.running(),
            "source": self._source(),
            "check": check,
            "job": job,
            "pending": self.pending() if avail else [],
            "stalled": bool(avail and self.stalled()),
            "rollback": rollback,
            "status": self.read_status() if avail else None,
        }

    # ---- network ----

    def _opener(self, cfg: Config) -> urllib.request.OpenerDirector:
        """http and https only, certificates verified, no proxies, no file or ftp, redirects kept on one origin."""
        o = urllib.request.OpenerDirector()
        for h in (urllib.request.HTTPHandler(), urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                  _SameOrigin(cfg.allow_lan_http), urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor()):
            o.add_handler(h)
        return o

    def _open(self, url: str, cfg: Config):
        try:
            resp = self._opener(cfg).open(urllib.request.Request(url, headers=_HEADERS), timeout=self.limits.io_timeout)
        except UpdateError:
            raise
        except urllib.error.HTTPError as e:
            e.close()
            raise UpdateError("fetch", f"The update server answered {int(e.code)}.", 502)
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            raise _net_error(e)
        if resp.status != 200:
            resp.close()
            raise UpdateError("fetch", f"The update server answered {int(resp.status)}.", 502)
        return resp

    @staticmethod
    def _length(resp, limit: int, what: str) -> int | None:
        enc = (resp.headers.get("Content-Encoding") or "identity").strip().lower()
        if enc != "identity":
            raise UpdateError("fetch", "The update server sent compressed data, which OPOS does not accept.", 502)
        n = resp.headers.get("Content-Length")
        if n is None:
            return None
        if not (n.isascii() and n.isdigit()) or len(n) > 12:  # str.isdigit() is true for "²", which int() refuses
            raise UpdateError("fetch", "The update server sent a bad length.", 502)
        if int(n) > limit:
            raise UpdateError("too_large", f"The {what} is larger than OPOS accepts.", 502)
        return int(n)

    @staticmethod
    def _chunks(resp, limit: int, deadline: float, over: UpdateError | None = None):
        """Yield the body in pieces, never more than limit bytes in all and never past the deadline. The piece that would
        pass the limit is not yielded: `over` (default: the server sent more than OPOS accepts) is raised instead."""
        total = 0
        read = getattr(resp, "read1", resp.read)  # read1 returns after one receive, so the deadline is checked on a slow trickle too
        while True:
            try:
                piece = read(min(CHUNK, limit + 1 - total))
            except (OSError, http.client.HTTPException) as e:
                raise _net_error(e)
            if not piece:
                return
            total += len(piece)
            if total > limit:
                raise over or UpdateError("too_large", "The update server sent more data than OPOS accepts.", 502)
            if time.monotonic() > deadline:
                raise UpdateError("fetch", "The update server was too slow.", 502)
            yield piece

    def _fetch_index(self, cfg: Config) -> Index:
        deadline = time.monotonic() + self.limits.index_deadline
        with contextlib.closing(self._open(cfg.url, cfg)) as resp:
            self._length(resp, self.limits.max_index, "update list")
            raw = b"".join(self._chunks(resp, self.limits.max_index, deadline))
            base = resp.geturl()
        try:
            return parse_index(raw, base, cfg.allow_lan_http, self.limits.max_bundle)
        except ValueError as e:
            raise UpdateError("bad_index", f"The update list from the source cannot be used: {e}.", 502)

    def _verdict(self, serial: int, version: str) -> tuple[bool, str]:
        """Is an offered serial newer than what is installed? Root repeats this check; this only avoids waste."""
        st = self.read_state()
        if st is None or st["serial"] is None:
            return False, "OPOS cannot read which update is installed, so it will not download one."
        if serial in st["failed"]:
            return False, f"Update {serial} was tried on this phone and rolled back, so OPOS will not fetch it again."
        if serial == st["serial"]:
            return False, "You have the latest update."
        if serial < st["serial"]:
            return False, f"The source offers update {serial}, older than the installed update {st['serial']}."
        if st["floor"] is None:
            return False, "OPOS cannot read which update is installed, so it will not download one."
        if serial <= st["floor"]:
            return False, f"Update {serial} is not above {st['floor']}, the highest update that failed on this phone, so OPOS will not fetch it."
        return True, f"Version {version} (update {serial}) is available."

    def _check_locked(self, cfg: Config) -> tuple[dict, Index]:
        stamp = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(self._clock()))
        try:
            idx = self._fetch_index(cfg)
        except UpdateError as e:
            self._transition("remember", check={"time": stamp, "ok": False, "newer": None, "serial": None, "version": None, "size": None, "message": e.message})
            raise
        newer, why = self._verdict(idx.serial, idx.version)
        res = {"time": stamp, "ok": True, "newer": newer, "serial": idx.serial, "version": idx.version, "size": idx.size, "message": why}
        self._transition("remember", check=res)
        return res, idx

    def _need(self) -> Config:
        if not self.available():
            raise UpdateError("not_available", NOT_AVAILABLE, 404)
        cfg = self.config()
        if cfg is None:
            raise UpdateError("not_configured", NOT_CONFIGURED, 409)
        return cfg

    def check(self) -> dict:
        """Fetch latest.json and compare serials. Returns the check result."""
        cfg = self._need()
        self._transition("begin")
        try:
            return self._check_locked(cfg)[0]
        finally:
            self._transition("end-check")

    # ---- download ----

    @staticmethod
    def _idle_job() -> dict:
        return {"kind": None, "phase": "idle", "bytes": 0, "total": None, "serial": None, "message": ""}

    def _job_state(self) -> JobState:
        """Derive the state under _lock; the public job retains the last download while checking."""
        phase = JobState(self._job["phase"])
        return JobState.CHECKING if self._busy.locked() and phase != JobState.DOWNLOADING else phase

    def _transition(self, event: str, **kw) -> None:
        """Only writer of the job, check, worker and busy ownership. Root observations are independent."""
        with self._lock:
            if event == "init":
                self._job = self._idle_job()
                self._check = None
                self._thread = None
                return
            state = self._job_state()
            if event == "begin":
                if not self._busy.acquire(blocking=False):
                    raise UpdateError("busy", "An update check or download is already running.", 409)
            elif event == "remember":
                assert state == JobState.CHECKING
                self._check = kw["check"]
            elif event == "end-check":
                assert state == JobState.CHECKING
                self._busy.release()
            elif event == "download":
                assert state == JobState.CHECKING
                idx = kw["idx"]
                self._job.update(kind="download", phase=JobState.DOWNLOADING.value, bytes=0,
                                 total=idx.size, serial=idx.serial, message="Downloading.")
                try:
                    self._thread = threading.Thread(target=self._download_thread, args=(kw["cfg"], idx),
                                                    daemon=True, name="opos-update-download")
                    self._thread.start()
                except BaseException:
                    self._job.update(phase=JobState.FAILED.value, message="The download failed.")
                    self._thread = None  # wait() must never join a thread that did not start
                    self._busy.release()
                    raise
            elif event == "progress":
                self._job["bytes"] = kw["bytes"]
            elif event == "finish":
                assert state == JobState.DOWNLOADING
                assert kw["state"] in (JobState.DELIVERED, JobState.FAILED)
                self._job.update(phase=kw["state"].value, message=kw["message"])
                self._busy.release()
            else:
                raise ValueError("unknown update job event")

    def job(self) -> dict:
        with self._lock:
            return dict(self._job)

    def start_download(self) -> dict:
        """Refetch the index, and if it is newer start streaming its bundle into the inbox. Returns at once."""
        cfg = self._need()
        self._transition("begin")
        try:
            res, idx = self._check_locked(cfg)
            if not res["newer"]:
                raise UpdateError("no_update", res["message"], 409)
            waiting = self.pending()
            if waiting:
                raise UpdateError("pending", f"An update is already waiting to be applied ({waiting[0]}).", 409)
            if not os.path.isdir(self.paths.inbox):
                raise UpdateError("inbox", "The update inbox is missing.", 503)
        except BaseException:
            self._transition("end-check")
            raise
        self._transition("download", cfg=cfg, idx=idx)
        return self.job()

    def wait(self, timeout: float | None = None) -> bool:
        """Join the download thread (tests and shutdown). True when no download is running."""
        with self._lock:
            t = self._thread
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True

    def _download_thread(self, cfg: Config, idx: Index) -> None:
        state, message = JobState.FAILED, "The download failed."
        try:
            self.download(cfg, idx)
            state, message = JobState.DELIVERED, "Downloaded. The updater applies it next."
        except UpdateError as e:
            message = e.message
        except Exception:
            _log.warning("download failed", exc_info=True)
        finally:
            self._transition("finish", state=state, message=message)

    def download(self, cfg: Config, idx: Index) -> str:
        """Stream idx's bundle to inbox/<name>.partial, fsync, rename to .opos-update. Returns the final path.

        The name comes from the validated serial, never from the server. A leftover or planted .partial is
        unlinked (not written through) first. Root only looks at *.opos-update, so a half file is never applied.
        """
        inbox = self.paths.inbox
        final = os.path.join(inbox, f"opos-{idx.serial}{BUNDLE_EXT}")
        part = final + ".partial"
        cap = self.limits.max_bundle
        deadline = time.monotonic() + self.limits.bundle_deadline
        with contextlib.closing(self._open(idx.url, cfg)) as resp:
            declared = self._length(resp, cap, "update")
            if idx.size is not None and declared is not None and declared != idx.size:
                raise UpdateError("mismatch", "The update is not the size its list promised.", 502)
            # What the download may add up to: a size the index or the server committed to, else only the cap. Either way the
            # free space is looked at before the first byte and again while the bytes arrive, and a promised size is a ceiling
            # for the stream, so a response without a length (or with a small promise) cannot fill the phone.
            promised = idx.size if idx.size is not None else declared
            with contextlib.suppress(FileNotFoundError):
                os.unlink(part)  # a leftover is not counted against the room
            self._room(inbox, promised)
            try:
                fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o660)
            except OSError:
                raise UpdateError("inbox", "OPOS cannot write to the update inbox.", 500)
            done = False
            try:
                with os.fdopen(fd, "wb") as f:
                    h = hashlib.sha256()
                    n = looked = 0
                    head = bytearray()
                    over = UpdateError("mismatch", "The update is bigger than its list promised.", 502) if idx.size is not None else None
                    for piece in self._chunks(resp, promised if promised is not None else cap, deadline, over):
                        h.update(piece)
                        f.write(piece)
                        n += len(piece)
                        if len(head) < 512:
                            head += piece[:512 - len(head)]
                        self._transition("progress", bytes=n)
                        if n - looked >= self.limits.space_step:
                            looked = n
                            f.flush()  # so that what the file system reports is what has been written
                            self._room(inbox, None if promised is None else promised - n)
                    if (declared is not None and n != declared) or (idx.size is not None and n != idx.size):
                        raise UpdateError("mismatch", "The download stopped early or ran long.", 502)
                    if idx.sha256 is not None and h.hexdigest() != idx.sha256:
                        raise UpdateError("mismatch", "The download does not match the hash in the update list.", 502)
                    if len(head) < 512 or head[257:262] != b"ustar":  # a cheap guard against an error page; root verifies
                        raise UpdateError("not_bundle", "The download is not an update bundle.", 502)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(part, final)
                done = True
            finally:
                if not done:
                    with contextlib.suppress(OSError):
                        os.unlink(part)
        self._sync_dir(inbox)
        return final

    def _room(self, inbox: str, still: int | None) -> None:
        """Refuse when `still` more bytes (nothing known: none) would leave less than the headroom free in the inbox's filesystem."""
        try:
            free = shutil.disk_usage(inbox).free
        except OSError:
            return  # cannot tell; the caps on the size still hold
        if free < (still or 0) + self.limits.headroom:
            raise UpdateError("space", "There is not enough free space for the update.", 409)

    @staticmethod
    def _sync_dir(path: str) -> None:
        with contextlib.suppress(OSError):
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    # ---- rollback ----

    def request_rollback(self) -> dict:
        """Ask root to go back: create inbox/rollback.request (empty). Root does the rollback; this only asks.

        With a boot-scope update staged this cancels it instead (the file holds `cancel-staged`): that needs no earlier
        release and is not limited, and it is the only request made while something is staged, so a cancel never also
        moves the installed release."""
        if not self.available():
            raise UpdateError("not_available", NOT_AVAILABLE, 404)
        st = self.read_state()
        if st is None:
            raise UpdateError("no_rollback", "OPOS cannot read which versions are installed, so it will not ask for a rollback.", 409)
        cancel = st["staged"] is not None and st["cancels_staged"]
        if not cancel:
            if st["previous"] < 1:
                raise UpdateError("no_rollback", "There is no earlier version to go back to.", 409)
            if st["stepped_back"] is not None:
                raise UpdateError("no_rollback", ONCE_ONLY, 409)
        path = os.path.join(self.paths.inbox, ROLLBACK_REQUEST)
        if cancel and os.path.lexists(path):
            return {"requested": True, "already": True, "cancel": True}
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        part = path + ".partial"
        try:
            if cancel:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(part)  # a leftover is not written through
            fd = os.open(part if cancel else path, flags, 0o660)
        except FileExistsError:
            return {"requested": True, "already": True}
        except FileNotFoundError:
            raise UpdateError("inbox", "The update inbox is missing.", 503)
        except OSError:
            raise UpdateError("inbox", "OPOS cannot write to the update inbox.", 500)
        try:
            if cancel:
                os.write(fd, CANCEL_STAGED)
            os.fsync(fd)
        except OSError:
            os.close(fd)
            with contextlib.suppress(OSError):
                os.unlink(part if cancel else path)
            raise UpdateError("inbox", "OPOS cannot write to the update inbox.", 500)
        os.close(fd)
        if cancel:
            try:
                os.replace(part, path)  # the whole text is there before root can see a file of this name
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(part)
                raise UpdateError("inbox", "OPOS cannot write to the update inbox.", 500)
        self._sync_dir(self.paths.inbox)
        return {"requested": True, "already": False, **({"cancel": True} if cancel else {})}

    # ---- optional background check ----

    def auto_check_enabled(self) -> bool:
        try:
            cfg = self.config()
        except UpdateError:
            return False
        return bool(self.available() and cfg is not None and cfg.auto_check)

    def start_auto_check(self, first_delay: float = AUTO_FIRST_S, interval: float = AUTO_EVERY_S) -> threading.Thread | None:
        """Start the only background work, and only when auto_check is true. It checks; it never downloads."""
        if not self.auto_check_enabled():
            return None
        if self._auto is not None and self._auto.is_alive():
            return self._auto
        self._stop.clear()
        self._auto = threading.Thread(target=self._auto_loop, args=(first_delay, interval), daemon=True, name="opos-update-auto")
        self._auto.start()
        return self._auto

    def stop(self) -> None:
        self._stop.set()

    def _auto_loop(self, delay: float, every: float) -> None:
        while not self._stop.wait(delay):
            delay = every
            try:
                cfg = self.config()
                if cfg is not None and cfg.auto_check:  # re-read each time: the owner may have turned it off
                    self.check()
            except UpdateError:
                pass  # busy, unreachable or unset: try again next time
            except Exception:
                _log.warning("automatic check failed", exc_info=True)
