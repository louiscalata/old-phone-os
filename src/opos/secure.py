# SPDX-License-Identifier: GPL-3.0-or-later
"""Loopback server security helpers."""
import hmac
import json
import secrets
import threading
import time
import unicodedata
from typing import Any, BinaryIO

LOOPBACK = ("127.0.0.1", "::1")
MAX_BODY = 4096


class BodyError(Exception):
    """Short safe client error with HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        """Store status and safe fixed message."""
        super().__init__(message)
        self.status = status
        self.message = message


def check_bind(host: str) -> None:
    """Reject non-loopback bind addresses."""
    if host not in LOOPBACK:
        raise ValueError("not loopback")


def new_token() -> str:
    """Create a fresh URL-safe session token."""
    return secrets.token_urlsafe(32)


def token_ok(supplied: Any, expected: Any) -> bool:
    """Compare tokens without leaking timing info."""
    if not isinstance(supplied, str) or not supplied:
        return False
    if not isinstance(expected, str) or not expected:
        return False
    try:
        left = supplied.encode("utf-8")
        right = expected.encode("utf-8")
    except (UnicodeError, ValueError):
        return False
    return hmac.compare_digest(left, right)


def host_ok(host_header: str | None, port: int) -> bool:
    """Accept only exact loopback host headers."""
    return host_header in (f"127.0.0.1:{port}", f"localhost:{port}")


def origin_ok(origin_header: str | None, port: int) -> bool:
    """Accept missing or exact loopback origins."""
    if origin_header is None:
        return True
    return origin_header in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")


def json_type_ok(content_type_header: str | None) -> bool:
    """Accept only application/json media type."""
    if not isinstance(content_type_header, str):
        return False
    return content_type_header.split(";")[0].strip().lower() == "application/json"


def read_json_body(rfile: BinaryIO, headers: Any, limit: int = MAX_BODY) -> dict:
    """Read and validate a small JSON object body."""
    if headers.get("Transfer-Encoding") is not None:
        raise BodyError(400, "unsupported encoding")
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        found = get_all("Content-Length")
        if not found:
            raise BodyError(411, "length required")
        if len(found) != 1:
            raise BodyError(400, "bad length")
        raw = found[0]
    else:
        raw = headers.get("Content-Length")
        if raw is None:
            raise BodyError(411, "length required")
    if not isinstance(raw, str) or not 1 <= len(raw) <= 9 or any(c not in "0123456789" for c in raw):
        raise BodyError(400, "bad length")
    size = int(raw)
    if size > limit:
        raise BodyError(413, "body too large")
    data = rfile.read(size)
    if len(data) < size:
        raise BodyError(400, "short body")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise BodyError(400, "bad json")
    def _bad(_v: str) -> Any:
        raise ValueError("bad constant")
    try:
        value = json.loads(text, parse_constant=_bad)
    except (ValueError, RecursionError):
        raise BodyError(400, "bad json")
    if not isinstance(value, dict):
        raise BodyError(400, "bad request")
    return value


def _nonce_ok(nonce: Any) -> bool:
    """Check nonce uses only URL-safe characters."""
    return isinstance(nonce, str) and 1 <= len(nonce) <= 128 and all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in nonce)


def csp(nonce: str) -> str:
    """Build the exact content security policy."""
    if not _nonce_ok(nonce):
        raise ValueError("bad nonce")
    return f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; style-src-attr 'unsafe-inline'; font-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def security_headers(nonce: str | None = None) -> dict[str, str]:
    """Build default hardening headers plus CSP."""
    out = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cache-Control": "no-store",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if nonce is not None:
        out["Content-Security-Policy"] = csp(nonce)
    return out


def add_nonce(html: str, nonce: str) -> str:
    """Attach nonce to inline script and style tags."""
    if not _nonce_ok(nonce):
        raise ValueError("bad nonce")
    return html.replace("<script>", f'<script nonce="{nonce}">').replace("<style>", f'<style nonce="{nonce}">')


class RateLimiter:
    """Per-key token bucket capped at 64 keys."""

    def __init__(self, rate_per_s: float, burst: int, clock: Any = time.monotonic) -> None:
        """Configure refill rate, burst, and clock."""
        self._rate = rate_per_s
        self._burst = burst
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        """Consume one token when available."""
        if not isinstance(key, str) or not key:
            return False
        now = self._clock()
        with self._lock:
            found = self._state.get(key)
            if found is None:
                if len(self._state) >= 64:
                    return False
                left = float(self._burst)
                last = now
            else:
                left, last = found
                left = min(float(self._burst), left + (now - last) * self._rate)
                last = now
            if left < 1.0:
                self._state[key] = [left, last]
                return False
            self._state[key] = [left - 1.0, last]
            return True


def clean_text(s: str, max_len: int) -> str:
    """Strip control/bidi/surrogate chars then truncate."""
    if not isinstance(s, str):
        return ""
    if not isinstance(max_len, int) or max_len <= 0:
        return ""
    return "".join(c for c in s if c in ("\n", "\t") or unicodedata.category(c) not in ("Cc", "Cf", "Cs"))[:max_len]


def mask_number(n: str) -> str:
    """Hide all but the last two characters."""
    if not isinstance(n, str):
        return "***"
    return "***" + n[-2:] if len(n) >= 2 else "***"
