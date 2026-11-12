# SPDX-License-Identifier: GPL-3.0-or-later
"""Panes: a black screen and a white screen on a window that spans both panels of a two-screen phone.

OPOS_PANES=1 (in the server's environment) makes the served pages carry two extra tags, a style sheet and a script, both from
this server and both under the page's own CSP nonce, and makes the server answer /panes.css and /panes.js. On a window that
covers both panels (OPOS_SPAN, or a browser that reports two viewport segments) the style sheet paints the thumb pane (the
app or detail pane) black with white text and the other pane (the list, the masthead, the shade) white with black text, and the
Hand setting decides which physical pane is the thumb pane. On a one-pane window nothing changes. In short: with OPOS_PANES unset the
panes assets and tags are absent and page() is unchanged; the Network, first-run and time zone pages carry the Jaryo conformance
fixes either way. docs/jaryo-panes.md has the model.

Like opos.osk this never touches web.page(), so the main page's size budget is untouched: pages pass their finished bytes through
inject_bytes, which returns them unchanged while the flag is off.
"""
import os
import re
from pathlib import Path

from opos import secure, span as spanmod

_STATIC = Path(__file__).with_name("panes_static")
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_HEAD_END = re.compile(r"</head\s*>", re.I)
_BODY_END = re.compile(r"</body\s*>", re.I)
# "touch" (docs/jaryo-panes.md section 7) is specified, not built: it is not a word, so it means off, until it is built.
_WORDS = {"1", "on", "mirror", "invert", "theme", "pure"}
# Surface constants: the two token pairs of jaryo.css (dark: #e6e2d9 on #000, light: #1a1a1a on #f3f0e8). "pure" is the strict AGIW
# pair (primitives.css). "theme" paints both panes in the page's own Light/Dark/System tokens.
SOFT = {"@K@": "#000", "@KI@": "#e6e2d9", "@KCS@": "dark", "@W@": "#f3f0e8", "@WI@": "#1a1a1a", "@WCS@": "light"}
PURE = {"@K@": "#000", "@KI@": "#fff", "@KCS@": "dark", "@W@": "#fff", "@WI@": "#000", "@WCS@": "light"}
THEME = {"@K@": "var(--jaryo-bg)", "@KI@": "var(--jaryo-fg)", "@KCS@": "normal", "@W@": "var(--jaryo-bg)", "@WI@": "var(--jaryo-fg)", "@WCS@": "normal"}


def modes(env=None):
    """The words of OPOS_PANES, or an empty set when it is unset or holds a word nobody knows."""
    v = (os.environ if env is None else env).get("OPOS_PANES", "").strip().lower()
    words = {w.strip() for w in v.split(",") if w.strip()}
    return words if words and words <= _WORDS else set()


def enabled(env=None):
    return bool(modes(env))


def css_text(span=None, env=None):
    """The style sheet: panes.css with the surface constants filled in and, for a span, the geometry rewritten as span.py does."""
    m = modes(env)
    text = (_STATIC / "panes.css").read_text(encoding="utf-8")
    keep = "" if "invert" in m else (_STATIC / "panes-mirror.css").read_text(encoding="utf-8")
    text = text.replace("@MIRROR@", keep)
    for k, v in (THEME if "theme" in m else PURE if "pure" in m else SOFT).items():
        text = text.replace(k, v)
    if span is not None:
        for old, new in spanmod.rewrites(span):
            text = text.replace(old, new)
        text = spanmod.variables(span) + text
    if Path("/etc/sudoers.d/90-opos-dev").exists():
        text += ('\nbody::after{content:"DEV: passwordless sudo for droidian";'
                 'position:fixed;top:0;left:0;right:0;z-index:2147483647;'
                 'background:#000;color:#fff;text-align:center;font: bold 16px sans-serif;'
                 'padding:8px;pointer-events:none}')
    return text


def asset(path, span=None, env=None):
    """(body bytes, content type) for /panes.css or /panes.js while the flag is on; None otherwise."""
    if not enabled(env):
        return None
    try:
        if path == "/panes.css":
            return css_text(span, env).encode("utf-8"), "text/css; charset=utf-8"
        if path == "/panes.js":
            js = (_STATIC / "panes.js").read_text(encoding="utf-8")
            return js.replace("@MODES@", ",".join(sorted(modes(env)))).encode("utf-8"), "text/javascript; charset=utf-8"
    except OSError:
        return None
    return None


def tags(nonce):
    if not isinstance(nonce, str) or _NONCE_RE.fullmatch(nonce) is None:
        raise ValueError("bad nonce")
    return (f'<link rel="stylesheet" href="/panes.css" nonce="{nonce}">'
            f'<script src="/panes.js" nonce="{nonce}"></script>')


def inject(html, nonce, env=None):
    """html with the tags before its first </head> (before its last </body> when it has none); unchanged while off or already there."""
    if not enabled(env) or 'src="/panes.js"' in html:
        return html
    t = tags(nonce)
    m = _HEAD_END.search(html)
    if m:
        return html[:m.start()] + t + html[m.start():]
    b = list(_BODY_END.finditer(html))
    return html + t if not b else html[:b[-1].start()] + t + html[b[-1].start():]


def inject_bytes(body, nonce, env=None):
    """inject() for an encoded page; the very same bytes object while the flag is off."""
    if not enabled(env):
        return body
    return inject(body.decode("utf-8"), nonce, env).encode("utf-8")


def serve(handler, path, env=None):
    """Answer GET/HEAD for /panes.css and /panes.js on a web.Handler; False (nothing sent) for any other path or while off."""
    a = asset(path, getattr(handler, "span", None), env)
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
