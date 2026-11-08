# SPDX-License-Identifier: GPL-3.0-or-later
"""Span: one browser window covering both panels of a two-screen phone and the hinge between them.

Chromium on Linux cannot report viewport segments, so the page's two-screen layout (the
(horizontal-viewport-segments:2) rules, and Auto picking Foldable) never applies there. The owner tells the
server the geometry (OPOS_SPAN or --span) and only then the page is served as if the browser had reported two
segments: that query becomes a width test and the segment env() values become --pane and --hinge. Unset, the
page is served exactly as before.
"""
import math
import re
from typing import NamedTuple

_PX = r"(\d+(?:\.\d+)?)(?:px)?"
_RE = re.compile(_PX + r"\s*,\s*" + _PX)


class Span(NamedTuple):
    pane: float
    hinge: float

    @property
    def width(self):
        return 2 * self.pane + self.hinge


def parse(text):
    """Span from 'PANE,HINGE' in CSS px (units optional); None for empty or 'off'; ValueError if unusable."""
    t = (text or "").strip().lower()
    if t in ("", "0", "off", "none"):
        return None
    m = _RE.fullmatch(t)
    if m is None:
        raise ValueError(f"expected PANE,HINGE in CSS px, e.g. 675,42, not {text!r}")
    s = Span(float(m[1]), float(m[2]))
    if not 0 < s.hinge < s.pane or not 840 <= s.width <= 8192:
        raise ValueError(f"{text.strip()!r} does not describe two panes and a hinge (the whole span must be 840 to 8192 CSS px)")
    return s


def _n(v):
    return format(v, "g")


def gate(span):
    """Narrowest window, in CSS px, that counts as spanning: the first pane, the hinge and half a second pane.

    Panes and hinge are anchored at the left edge, so a window a little narrower than the whole span (rounding of the
    output scale, say) only narrows the second pane; a window of one pane stays well below."""
    return max(840, math.ceil(span.pane + span.hinge + span.pane / 2))


def variables(span):
    """The two custom properties every span rule is written in."""
    return f":root{{--pane:{_n(span.pane)}px;--hinge:{_n(span.hinge)}px}}"


# What the segment-aware rules leave on the hinge or out of place. The status line's middle text is centred
# across the whole window and the shade's grab handle sits at 50%: both move into the first pane. The segment
# rules also top-align the lock screen; the wide Foldable layout centres it.
EXTRA = ('html[data-ff="foldable"] #lock:not([hidden]){align-content:center}'
         "#sb-engine{flex:0 1 auto;max-width:calc(var(--pane) - 220px);text-align:left}"
         "#meter-hit::after{left:calc(var(--pane)/2)}")


def readout(span):
    """Settings, Device type also states the window the page found, so the numbers can be checked on the phone.

    phone-b.js rewrites that line from scratch on every resize, so the readout is appended again on each one; its
    script is the last in the page, which puts its listener after phone-b.js's. A resize delivered before load
    would otherwise append it twice, so it is skipped when the line already carries it."""
    return ("<script>['load','resize'].forEach(function(t){addEventListener(t,function(){"
            "var e=document.getElementById('ff-detected');"
            "if(e&&e.textContent.indexOf('. Window ')<0)e.textContent+='. Window '+innerWidth+' x '+innerHeight+' CSS px at '+devicePixelRatio+'x, "
            f"span {_n(span.pane)} + {_n(span.hinge)} + {_n(span.pane)}.'}})}})</script>")


def rewrites(span):
    """(old, new) text pairs applied to the served page, in order."""
    return (
        ("(horizontal-viewport-segments:2)", f"(min-width:{gate(span)}px)"),
        ("env(viewport-segment-width 0 0)", "var(--pane)"),
        ("env(viewport-segment-left 1 0)", "calc(var(--pane) + var(--hinge))"),
        # Auto must keep the Foldable that phone-b.js just picked when the first state arrives.
        ("{v='auto';delete d.dataset.ff}", "{v='auto'}"),
    )


def squeeze(html):
    """Drop the style sheet's comments and the line breaks between its rules, and the two markup comments.

    The span's own rules and readout add a few hundred bytes and the page has a hard size budget
    (tests/test_uxp30_budget.py); this takes out about 500 without changing a rule, so a spanned page stays inside it."""
    head, sep, rest = html.partition("</style></head>")
    if not sep:
        return html
    head = re.sub(r"(?<=[,;{}])\n", "", re.sub(r"/\*.*?\*/", "", head, flags=re.S))
    return re.sub(r"^<!--.*?-->\n", "", head + sep + rest, flags=re.M)


def apply(html, span):
    """The page for a window spanning two panes; unchanged when span is None."""
    if span is None:
        return html
    for old, new in rewrites(span):
        html = html.replace(old, new)
    media = f"@media (min-width:{gate(span)}px){{{EXTRA}}}"
    html = squeeze(html).replace("</style></head>", variables(span) + media + "</style></head>", 1)
    return html.replace("</body>", readout(span) + "</body>", 1)
