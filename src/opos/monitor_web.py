# SPDX-License-Identifier: GPL-3.0-or-later
"""The web side of the Inference page: what opos.web needs to serve GET /monitor and GET /api/monitor.

Everything here is off unless the server runs with OPOS_MONITOR=1 (exactly that value; the Duo OS image's
opos-server.service sets it). With it unset, or anything else, opos.web serves the pages it always served and answers
/monitor and /api/monitor like any other path it does not know: 404.

The data (opos.monitor) and the document (web_static/monitor.html, .css, .js and monitor-theme.js) arrive together as one
drop-in and are replaced as a whole, so nothing here reaches into them beyond three things their README names: the
@@OPOS:name@@ placeholders in monitor.html, the exact title it carries, and Monitor.snapshot(local_state). The data module
is imported by opos.web, not here, so a runtime that ships without it serves what it always served.

The main page is at its size budget, so the Settings link is added to the finished page at serve time (link_bytes),
the way opos.osk adds its keyboard: the very same bytes while the switch is off.
"""
import math
import os
import re
from html import escape

from opos import secure
from opos.span import apply as apply_span

TITLE = "<title>Inference - OPOS</title>"  # the anchor the drop-in README names; the token meta goes in front of it
ANCHOR = '<section aria-labelledby="set-about">'  # Settings > About: the link goes ahead of it, as the Network and Time zone links do
# 48 px tall like every other touch target on the page; the color and border follow the page's theme.
LINK = ('<section aria-labelledby="set-inference"><h3 id="set-inference">Inference</h3><a id="mon-link" href="/monitor" '
        'style="display:inline-block;min-height:48px;line-height:48px;padding:0 16px;border:1px solid;color:inherit">'
        'Inference page</a></section>')
LIMIT_KEY = "monitor"  # the rate-limit bucket web._Limits keeps for GET /api/monitor (an absent key is refused, never allowed)
# Where the battery's health came from (opos.hw.live_battery health_source). Only "sysfs" is a reading: the gauge's own full-charge
# over design capacity. "charge-counter" is an estimate (counter / level / a design capacity from a model table or
# OPOS_BATTERY_DESIGN_MAH, clamped to 0.1..1.0), "assumed" is a placeholder and "OPOS_BATTERY_HEALTH" is a number the owner typed.
# local_state() has no way to label an estimate, and the page prints a bare "Battery health NN%", so none of those is shown.
MEASURED_HEALTH = frozenset({"sysfs"})
_HEAD_END = re.compile(r"</style>\s*</head>")


def enabled(env=None):
    """Whether the server serves the Inference page: OPOS_MONITOR is exactly 1."""
    return (os.environ if env is None else env).get("OPOS_MONITOR") == "1"


def buckets():
    """Fresh per-server rate limit for GET /api/monitor: the page asks every 2 s; a burst of 10, then 2 a second."""
    return {LIMIT_KEY: secure.RateLimiter(2.0, 10)}


def add_settings_link(html, env=None):
    """The main page with an Inference link in Settings, only where OPOS_MONITOR=1; unchanged (byte for byte) otherwise."""
    if not enabled(env) or ANCHOR not in html:
        return html
    return html.replace(ANCHOR, LINK + ANCHOR, 1)


def link_bytes(body, env=None):
    """add_settings_link() for the encoded home page; the very same bytes object while the switch is off."""
    if not enabled(env):
        return body
    return add_settings_link(body.decode("utf-8"), env).encode("utf-8")


def document(template, token, nonce, span=None):
    """The Inference document for one request.

    template is monitor.html with its @@OPOS:name@@ parts already inlined (web does that with the inliner page() uses).
    With a span the document goes through the same two-panel rewrite as the main page, because Chromium on Linux cannot
    report viewport segments and the two-pane layout depends on them; that comes first, then the token meta, then the
    nonce, the order page() uses. Raises ValueError, sending nothing half-built, if the title anchor is not there.
    """
    html = template
    if span is not None:
        # span.apply looks for the style sheet's end and the head's end with nothing between them (the main page has none);
        # without that its --hinge and --pane would be left out and a hinge other than 42 px would not be honoured.
        html = apply_span(_HEAD_END.sub("</style></head>", html, count=1), span)
    if TITLE not in html:
        raise ValueError("the title anchor is missing")
    meta = '<meta name="opos-token" content="' + escape(token, quote=True) + '">'
    return secure.add_nonce(html.replace(TITLE, meta + TITLE, 1), nonce)


def _number(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        return None
    return v


def local_state(snap, health=None):
    """The local_state dict Monitor.snapshot() takes, from one Phone.state() snapshot and the live battery's health (0 to 1).

    {"percent", "healthPercent", "source"}, each only when it is the phone's own reading: a phone running on the
    simulation (no battery_source in its state) has no battery to report, a health that is estimated (from the charge
    counter), assumed or typed in by the owner is left out, and anything that is not a plain number in range is left out.
    The page says "Not reported"
    for a missing key; it never shows 0 for one.
    """
    out = {}
    if not isinstance(snap, dict):
        return out
    src, battery = snap.get("battery_source"), snap.get("battery")
    if not isinstance(src, dict) or not isinstance(battery, dict):
        return out
    source = src.get("source")
    if not isinstance(source, str) or not source:
        return out  # the battery cannot be read just now
    out["source"] = source
    percent = _number(battery.get("percent"), 0, 100)
    if percent is not None:
        out["percent"] = percent
    where = src.get("health_source")
    if isinstance(where, str) and where in MEASURED_HEALTH:
        h = _number(health, 0.0, 1.0)
        if h is not None and h > 0:
            out["healthPercent"] = round(h * 100, 1)
    return out
