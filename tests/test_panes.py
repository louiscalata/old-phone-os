# SPDX-License-Identifier: GPL-3.0-or-later
"""Panes: the black and white surfaces for a window that spans both panels (opos.panes, panes_static/).

The flag and its words, the style sheet (tokens, literals, scoping, the span rewrite), injection and routes, "off is byte
identical", the main page's size budget, and the node DOM test for the Hand setting (tests/panes_dom_test.js).
"""
import http.client
import inspect
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from opos import osk, panes, span as spanmod
from opos.span import Span
from opos.web import page
from tests import webclient

_params = inspect.signature(http.client.HTTPConnection.request).parameters
if "skip_host" not in _params and not any(p.kind is p.VAR_KEYWORD for p in _params.values()):
    _orig_request = http.client.HTTPConnection.request

    def _compat(self, method, url, body=None, headers=None, **kw):
        kw.pop("skip_host", None)
        kw.pop("skip_accept_encoding", None)
        return _orig_request(self, method, url, body=body, headers=headers or {}, **kw)
    http.client.HTTPConnection.request = _compat  # type: ignore[method-assign]

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "opos" / "panes_static"
WEB = ROOT / "src" / "opos" / "web_static"
NODE = shutil.which("node")
DUO = Span(675, 42)
TOKEN = "t" * 43
ON = {"OPOS_PANES": "1"}
PAGE_BUDGET = 100 * 1024  # tests/test_uxp30_budget.py
ALLOWED = {"#000", "#e6e2d9", "#f3f0e8", "#1a1a1a"}  # the two token pairs of jaryo.css
PURE_ALLOWED = {"#000", "#fff"}


def env_without(**kw):
    e = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_PANES") and not k.startswith("OPOS_OSK")}
    e.update(kw)
    return mock.patch.dict(os.environ, e, clear=True)


class Flag(unittest.TestCase):
    def test_words(self):
        for v, m in (("1", {"1"}), ("on", {"on"}), (" 1,pure ", {"1", "pure"}), ("INVERT", {"invert"}), ("1,theme", {"1", "theme"})):
            self.assertEqual(panes.modes({"OPOS_PANES": v}), m, v)
        for v in ("", "0", "off", "yes", "1,wat", "true", "pure,"):
            self.assertEqual(panes.modes({"OPOS_PANES": v}), set() if v != "pure," else {"pure"}, v)
        self.assertFalse(panes.enabled({}))
        self.assertTrue(panes.enabled({"OPOS_PANES": "mirror"}))

    def test_touch_is_not_a_word_until_it_is_built(self):
        # docs/jaryo-panes.md section 7 says "touch" is specified, not built: it used to turn the feature on and behave like "1"
        for v in ("touch", "1,touch", "touch,pure", "TOUCH"):
            self.assertEqual(panes.modes({"OPOS_PANES": v}), set(), v)
            self.assertFalse(panes.enabled({"OPOS_PANES": v}), v)
            self.assertIsNone(panes.asset("/panes.css", None, {"OPOS_PANES": v}), v)
            self.assertEqual(panes.inject("<head></head>", "n1", {"OPOS_PANES": v}), "<head></head>", v)
        self.assertEqual(panes._WORDS, {"1", "on", "mirror", "invert", "theme", "pure"})


class Sheet(unittest.TestCase):
    css = staticmethod(lambda **kw: (STATIC / "panes.css").read_text(encoding="utf-8"))

    def served(self, span=None, **env):
        return panes.css_text(span, env or ON)

    def test_tokens_are_the_jaryo_pairs(self):
        j = (WEB / "jaryo.css").read_text(encoding="utf-8")
        self.assertIn("--jaryo-fg:#e6e2d9;--jaryo-bg:#000", j)  # dark
        self.assertIn("--jaryo-fg:#1a1a1a;--jaryo-bg:#f3f0e8", j)  # light
        self.assertEqual(panes.SOFT, {"@K@": "#000", "@KI@": "#e6e2d9", "@KCS@": "dark", "@W@": "#f3f0e8", "@WI@": "#1a1a1a", "@WCS@": "light"})
        out = self.served()
        self.assertIn("--jp-k:#000;--jp-k-ink:#e6e2d9;--jp-k-cs:dark;--jp-w:#f3f0e8;--jp-w-ink:#1a1a1a;--jp-w-cs:light", out)

    def test_only_token_colours_and_no_forbidden_paint(self):
        for env, allowed in ((ON, ALLOWED), ({"OPOS_PANES": "1,pure"}, PURE_ALLOWED | {"#000"})):
            out = self.served(**env)
            self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", out)), allowed)
            for bad in ("@font-face", "url(", "gradient", "box-shadow", "filter:", "text-shadow", "rgb(", "rgba(", "hsl", "@import", "!important",
                        "transition", "animation", "@keyframes", "content:url", "image-set"):
                self.assertNotIn(bad, out, bad)
            self.assertEqual(re.findall(r"opacity:[^;}]*", out), ["opacity:1"])  # ::placeholder only: no dimming
            self.assertNotIn("@@", out)
            self.assertNotRegex(out, r"@[A-Z]+@")

    def test_every_rule_is_scoped_by_the_switch(self):
        out = (STATIC / "panes.css").read_text(encoding="utf-8") + (STATIC / "panes-mirror.css").read_text(encoding="utf-8")
        out = re.sub(r"/\*.*?\*/", "", out, flags=re.S)
        inner = out[out.index("@media (horizontal-viewport-segments:2){") + 40:]
        sels = [m.group(1).strip() for m in re.finditer(r"(?:^|\})\s*([^{}@/][^{}]*)\{", inner)]
        self.assertGreater(len(sels), 50)
        for sel in sels:
            for one in re.split(r",(?![^()]*\))", sel):
                self.assertRegex(one.strip(), r"^html\[data-panes=two\]", one)
        # the one rule outside the gate hides the Hand button
        self.assertTrue(out.split("@media")[0].strip().splitlines()[-1].startswith("#jaryo-hand{display:none}"))

    def test_a_button_is_never_given_a_colour_by_a_role(self):
        # A `color` stated on Home, Close, Unlock or Call by the role rules (two ids) out-specifies `button:active` and leaves ink on ink
        # while the button is pressed. Roles state the two tokens for everything and `color` only for what is not a button.
        out = re.sub(r"/\*.*?\*/", "", (STATIC / "panes.css").read_text(encoding="utf-8"), flags=re.S)
        for m in re.finditer(r"(?:^|\})\s*([^{}@]*#phone>[^{}]*|[^{}@]*#lock>[^{}]*)\{([^}]*)\}", out):
            sel, body = m.group(1), m.group(2)
            if re.search(r"(?:^|;)color:", body):
                self.assertIn(":not(button)", sel, sel)
        self.assertIn(":is(#phone>*,#lock>*):not(button){color:var(--jp-lst-ink)}", out)
        # the OS pages' Home link is an anchor with the .btn pressed rule of the page: it states no colour either
        home = re.search(r"body>header>#home\{([^}]*)\}", out).group(1)
        self.assertNotRegex(home, r"(?:^|;)color:")

    def test_gate_and_geometry_without_a_span(self):
        out = self.served()
        self.assertEqual(out.count("@media (horizontal-viewport-segments:2){"), 1)
        self.assertIn("--jp-x1:env(viewport-segment-width 0 0);--jp-x2:env(viewport-segment-left 1 0)", out)
        self.assertNotIn("--pane", out)
        self.assertNotIn("var(--pane)", out)

    def test_span_rewrites_it_exactly_as_span_py_does(self):
        out = self.served(DUO)
        self.assertTrue(out.startswith(spanmod.variables(DUO)))
        self.assertNotIn("viewport-segment", out)
        self.assertIn("@media (min-width:1055px){", out)
        self.assertIn("--jp-x1:var(--pane);--jp-x2:calc(var(--pane) + var(--hinge))", out)
        self.assertEqual(out.count("--pane:"), 1)  # defined once, not once per rule

    def test_the_root_takes_the_ink_of_the_pane_it_paints(self):
        # html is the right pane's canvas, so its colour (and the caret that follows it) is that pane's ink: with "pure" #fff or #000, not
        # the theme's soft ink. No caret-color is set: it would be inherited into the other pane.
        for env, k_ink, w_ink in ((ON, "#e6e2d9", "#1a1a1a"), ({"OPOS_PANES": "1,pure"}, "#fff", "#000")):
            out = self.served(**env)
            root = [d.strip() for d in re.search(r"html\[data-panes=two\]\{([^}]*)\}", out).group(1).split(";")]
            self.assertIn("background:var(--jp-r)", root)
            self.assertIn("color:var(--jp-r-ink)", root)
            self.assertIn("--jp-r-ink:var(--jp-k-ink)", root)  # Right: the right pane is the black one
            self.assertIn("--jp-k-ink:" + k_ink, root)
            self.assertIn("--jp-w-ink:" + w_ink, root)
            left = [d.strip() for d in re.search(r"\[data-hand=left\]\{([^}]*)\}", out).group(1).split(";")]
            self.assertIn("--jp-r-ink:var(--jp-w-ink)", left)  # Left: the right pane is the white one
            self.assertNotIn("caret-color", out)

    def test_modes_change_one_thing_each(self):
        soft, pure = self.served(), self.served(**{"OPOS_PANES": "1,pure"})
        self.assertIn("--jp-k-ink:#fff;--jp-k-cs:dark;--jp-w:#fff;--jp-w-ink:#000", pure)
        self.assertEqual(soft.replace("#e6e2d9", "#fff").replace("#f3f0e8", "#fff").replace("#1a1a1a", "#000"), pure)
        invert = self.served(**{"OPOS_PANES": "invert"})
        self.assertNotIn("[data-hand=left][data-ff] #phone>", invert)  # no layout mirror
        self.assertIn("[data-hand=left]{--jp-l:var(--jp-k)", invert)   # but the colours still swap
        self.assertIn("[data-hand=left][data-ff] #phone>", soft)
        self.assertIn("var(--jaryo-bg)", self.served(**{"OPOS_PANES": "theme"}))

    def test_the_main_page_is_untouched(self):
        a = page("t" * 43, "n" * 22)
        with env_without(**ON):
            b = page("t" * 43, "n" * 22)
        self.assertEqual(a, b)
        self.assertNotIn("panes", a)
        self.assertNotIn("jp-", a)
        self.assertNotIn("jaryo_hand", a)

    def test_size_budget_is_untouched(self):
        with env_without(**ON):
            self.assertLessEqual(len(page("t" * 43, "n" * 22, span=DUO).encode("utf-8")), PAGE_BUDGET)
            self.assertEqual(page(span=DUO), page(span=DUO))


class Inject(unittest.TestCase):
    def test_off_is_the_same_object(self):
        body = "<html><head></head><body>é</body></html>".encode("utf-8")
        self.assertIs(panes.inject_bytes(body, "n1", {}), body)
        self.assertIs(panes.inject_bytes(body, "n1", {"OPOS_PANES": "nonsense"}), body)
        with env_without():
            self.assertIs(osk.inject_bytes(body, "n1"), body, "and through the keyboard's hook")

    def test_tags_and_place(self):
        t = panes.tags("abc_DEF-123")
        self.assertEqual(t, '<link rel="stylesheet" href="/panes.css" nonce="abc_DEF-123"><script src="/panes.js" nonce="abc_DEF-123"></script>')
        for bad in ("", "a b", 'a"b', "<x>", "a" * 129, None, 5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                panes.tags(bad)
        html = "<html><head><style>x</style></head><body><p>x</p></body></html>"
        got = panes.inject(html, "n1", ON)
        self.assertEqual(got, "<html><head><style>x</style>" + panes.tags("n1") + "</head><body><p>x</p></body></html>")
        self.assertEqual(panes.inject(got, "n1", ON), got, "idempotent")
        self.assertEqual(panes.inject("<p>no head</p></body>", "n1", ON), "<p>no head</p>" + panes.tags("n1") + "</body>")
        self.assertEqual(panes.inject(html, "n1", {}), html)

    def test_after_the_page_s_own_style_so_it_wins_by_source_order(self):
        with env_without(**ON):
            out = panes.inject(page("t" * 43, "n" * 22), "n" * 22, ON)
        self.assertLess(out.index("</style>"), out.index('href="/panes.css"'))
        self.assertLess(out.index('href="/panes.css"'), out.index("</head>"))

    def test_chained_through_osk_both_on_and_either_alone(self):
        body = "<html><head></head><body></body></html>".encode()
        with env_without(OPOS_OSK="1", OPOS_PANES="1"):
            got = osk.inject_bytes(body, "n1").decode()
        self.assertIn('/panes.css"', got)
        self.assertIn('src="/osk.js"', got)
        with env_without(OPOS_OSK="1"):
            got = osk.inject_bytes(body, "n1").decode()
        self.assertNotIn("panes", got)
        self.assertIn("osk.js", got)
        with env_without(OPOS_PANES="1"):
            got = osk.inject_bytes(body, "n1").decode()
        self.assertIn("panes.js", got)
        self.assertNotIn("osk.js", got)


class Routes(unittest.TestCase):
    def get(self, run, path="/", method="GET"):
        return webclient.request(run.port, method, path)

    def test_off_404_and_same_page(self):
        with env_without(), webclient.Running(token=TOKEN) as run:
            for p in ("/panes.css", "/panes.js"):
                s, h, b = self.get(run, p)
                self.assertEqual((s, b), (404, b'{"error": "not found"}'), p)
            s, h, b = self.get(run)
            self.assertNotIn(b"panes", b)

    def test_off_serves_every_full_page_exactly_as_the_pages_are_built(self):
        # Flag unset: the bytes on the wire are what web.page() and net_page.render() return, nothing added before or after.
        from opos import net_page
        with env_without(), webclient.Running(token=TOKEN, span=DUO) as run:
            for path, build in (("/", lambda t, n: page(t, n, span=DUO)), ("/network", net_page.render)):
                for method in ("GET", "HEAD"):
                    s, h, body = self.get(run, path, method)
                    nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", h["content-security-policy"]).group(1)
                    self.assertEqual(s, 200, path)
                    self.assertEqual(body, build(TOKEN, nonce).encode("utf-8") if method == "GET" else b"", (path, method))
                    self.assertEqual(h["content-length"], str(len(build(TOKEN, nonce).encode("utf-8"))), (path, method))

    def test_on_serves_both_with_the_page_hardening_and_the_span(self):
        with env_without(**ON), webclient.Running(token=TOKEN, span=DUO) as run:
            s, h, b = self.get(run, "/panes.css")
            self.assertEqual(s, 200)
            self.assertEqual(h["content-type"], "text/css; charset=utf-8")
            self.assertEqual(h["cache-control"], "no-store")
            self.assertEqual(h["x-content-type-options"], "nosniff")
            self.assertEqual(b.decode(), panes.css_text(DUO, ON))
            s, h, b = self.get(run, "/panes.js")
            self.assertEqual((s, h["content-type"]), (200, "text/javascript; charset=utf-8"))
            self.assertNotIn(b"@MODES@", b)
            self.assertEqual(self.get(run, "/panes.js", "HEAD")[0::2], (200, b""))
            s, h, body = self.get(run)
            nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", h["content-security-policy"]).group(1)
            self.assertEqual(body.decode().count("/panes."), 2)
            self.assertIn(f'<link rel="stylesheet" href="/panes.css" nonce="{nonce}">', body.decode())
            self.assertEqual(body.decode().replace(panes.tags(nonce), ""), page(TOKEN, nonce, span=DUO))

    def test_every_full_page_gets_it(self):
        with env_without(**ON), webclient.Running(token=TOKEN) as run:
            s, h, b = self.get(run, "/network")
            self.assertEqual(s, 200)
            self.assertIn(b"/panes.css", b)

    def test_without_osk_module_nothing_changes(self):
        with env_without(**ON), mock.patch.object(webclient.web, "osk", None), webclient.Running(token=TOKEN) as run:
            s, h, b = self.get(run)
            self.assertEqual(s, 200)
            self.assertNotIn(b"/panes.", b)  # rides on osk.py's hook: documented


@unittest.skipUnless(NODE, "no node")
class HandDom(unittest.TestCase):
    def test_hand_toggle_persistence_and_switch(self):
        p = subprocess.run([NODE, str(ROOT / "tests" / "panes_dom_test.js"), str(STATIC / "panes.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        self.assertIn("assertions passed", p.stdout)


if __name__ == "__main__":
    unittest.main()
