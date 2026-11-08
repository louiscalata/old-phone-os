# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo OS on-screen keyboard: a small in-page web keyboard that opos.web serves only on request.

The Duo OS has no physical keyboard and OPOS's kiosk Chromium is the only client of phoc, so nothing raises a
keyboard when a field is touched. OPOS_OSK=1 (or "web") in the server's environment fixes that in the page itself:
the served page gets two extra tags (a style sheet and a script, both from this server and both under the page's
own CSP nonce) and the server answers /osk.js and /osk.css. With OPOS_OSK unset, or anything else, the page and every
route are exactly what they were. OPOS_OSK=stevia means the port's Wayland keyboard is used instead (packaging/duo-os/
opos-osk); the server then adds nothing, so there is only ever one keyboard. packaging/duo-os/OSK.md has the reasons.

The page itself is never changed (web.page does not know about this module), so the main page's size budget is
untouched: web.Handler passes the finished page through inject_bytes, which returns it unchanged while the keyboard is off.
Any other full-page route that wants typing by touch passes its HTML through inject() the same way.
"""
import os
import re
from pathlib import Path

from opos import secure

_STATIC = Path(__file__).with_name("osk_static")
_ASSETS = {"/osk.js": ("osk.js", "text/javascript; charset=utf-8"), "/osk.css": ("osk.css", "text/css; charset=utf-8")}
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
HINGE_DEFAULT = 42  # CSS px: the Duo's 84 device px hinge at the output scale 2 (phoc.ini)
HINGE_MAX = 200


def mode(env=None):
    """"web" for the in-page keyboard, "stevia" for the port's Wayland keyboard, "" for neither."""
    v = (os.environ if env is None else env).get("OPOS_OSK", "").strip().lower()
    return "web" if v in ("1", "web") else "stevia" if v == "stevia" else ""


def enabled(env=None):
    """Whether the server adds the in-page keyboard to its pages."""
    return mode(env) == "web"


def hinge(env=None):
    """CSS px between the two halves of a split keyboard when the page does not state the hinge itself (0: never split).

    OPOS_OSK_HINGE overrides the Duo's 42. The page's own --hinge (OPOS_SPAN) or the browser's viewport segments win over it.
    """
    v = (os.environ if env is None else env).get("OPOS_OSK_HINGE", "").strip().lower()
    if v in ("0", "off", "none"):
        return 0
    try:
        n = int(v)
    except ValueError:
        return HINGE_DEFAULT
    return n if 0 <= n <= HINGE_MAX else HINGE_DEFAULT


def asset(path, env=None):
    """(body bytes, content type) for /osk.js or /osk.css while the keyboard is on; None otherwise."""
    spec = _ASSETS.get(path)
    if spec is None or not enabled(env):
        return None
    try:
        return (_STATIC / spec[0]).read_bytes(), spec[1]
    except OSError:
        return None


def tags(nonce, env=None):
    """The two tags that load the keyboard, carrying the page's CSP nonce."""
    if not isinstance(nonce, str) or _NONCE_RE.fullmatch(nonce) is None:
        raise ValueError("bad nonce")
    return (f'<link rel="stylesheet" href="/osk.css" nonce="{nonce}">'
            f'<script src="/osk.js" nonce="{nonce}" data-hinge="{hinge(env)}" defer></script>')


def inject(html, nonce, env=None):
    """html with the keyboard's tags before its last </body>; unchanged while the keyboard is off or already there."""
    if not enabled(env) or 'src="/osk.js"' in html:
        return html
    i = html.lower().rfind("</body>")
    t = tags(nonce, env)
    return html + t if i < 0 else html[:i] + t + html[i:]


def inject_bytes(body, nonce, env=None):
    """inject() for an encoded page; the very same bytes object while the keyboard is off."""
    if not enabled(env):
        return body
    return inject(body.decode("utf-8"), nonce, env).encode("utf-8")


def serve(handler, path, env=None):
    """Answer GET/HEAD for /osk.js and /osk.css on a web.Handler; False (nothing sent) for any other path or while off."""
    a = asset(path, env)
    if a is None:
        return False
    body, ctype = a
    handler.send_response(200)
    for k, v in secure.security_headers().items():
        handler.send_header(k, v)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)
    return True
