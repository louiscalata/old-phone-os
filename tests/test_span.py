# SPDX-License-Identifier: GPL-3.0-or-later
"""Span: the page served for one window covering both panels of a two-screen phone (opos.span).

Option parsing, the served page with and without a span, the CSS variables and the geometry they give, the
server and command line wiring, and one real-browser run over the DevTools pipe (headless Chromium found like
tests/test_uxr_acceptance.py; skips without it or without node).
"""

import contextlib
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opos import span as spanmod, web
from opos.span import Span, apply, parse, rewrites, squeeze, variables
from opos.web import Phone, page
from tests import test_uxp21_profiles as profiles
from tests.test_uxp21_profiles import CDP, blocks
from tests.test_uxp30_budget import PAGE_BUDGET, rules
from tests.test_uxr_acceptance import find_chrome

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "opos" / "web_static"
NODE = shutil.which("node")
DUO = Span(675, 42)
SEGMENTS = "(horizontal-viewport-segments:2)"


def style(html):
    return re.search(r"<style[^>]*>(.*?)</style>", html, re.S)[1]


class Parse(unittest.TestCase):
    def test_duo_values(self):
        s = parse("675,42")
        self.assertEqual(s, DUO)
        self.assertEqual(s.width, 1392)

    def test_spaces_units_and_fractions(self):
        self.assertEqual(parse(" 675px , 42px "), DUO)
        self.assertEqual(parse("540,33.6"), Span(540, 33.6))  # the Android density: 2.5
        self.assertAlmostEqual(parse("540,33.6").width, 1113.6)

    def test_off(self):
        for text in (None, "", "  ", "0", "off", "OFF", "none"):
            self.assertIsNone(parse(text), text)

    def test_rejects_what_is_not_two_panes_and_a_hinge(self):
        for text in ("675", "675,", ",42", "675,42,1", "675x42", "675;42", "abc", "-675,42", "675,-42", "6e2,42",
                     "675,0", "675,675", "675,700",  # no hinge, or a hinge as wide as a pane
                     "100,42", "419,1",  # narrower than the page's 840 px two-column layouts
                     "5000,42"):  # wider than any phone
            with self.assertRaises(ValueError, msg=text):
                parse(text)

    def test_error_names_the_expected_form(self):
        with self.assertRaises(ValueError) as ctx:
            parse("wide")
        self.assertIn("PANE,HINGE", str(ctx.exception))
        self.assertIn("675,42", str(ctx.exception))


class Variables(unittest.TestCase):
    def test_custom_properties(self):
        self.assertEqual(variables(DUO), ":root{--pane:675px;--hinge:42px}")
        self.assertEqual(variables(Span(540, 33.6)), ":root{--pane:540px;--hinge:33.6px}")

    def test_served_in_the_page_and_nowhere_else(self):
        out = page(span=DUO)
        self.assertEqual(out.count(variables(DUO)), 1)
        self.assertEqual(out.count("--pane:"), 1)
        self.assertEqual(out.count("--hinge:"), 1)
        self.assertNotIn("--pane", page())
        self.assertNotIn("--hinge", page())


def calc(text, **env):
    """Evaluate a px length made of numbers, + - * / and calc(); only after every name is substituted."""
    for name, value in env.items():
        text = text.replace(name, f"{value}px")
    expr = re.sub(r"(?<=\d)px", "", text).replace("calc", "")
    assert re.fullmatch(r"[\d.+\-*/() ]+", expr), text
    return eval(expr)


class Geometry(unittest.TestCase):
    """The segment rules, with the variables substituted, put the panes and the hinge where the span says."""

    def grid(self, span, inset=0):
        css = style(page(span=span))
        gate = f"@media (min-width:{spanmod.gate(span)}px){{"
        # The rule that makes #phone and #lock two columns: first pane, hinge as the gap, second pane takes the rest.
        m = re.search(r'html\[data-ff="foldable"\] #phone,html\[data-ff="foldable"\] #lock:not\(\[hidden\]\)\{([^}]*)\}', css[css.index(gate):])
        self.assertIsNotNone(m)
        decl = dict(d.split(":", 1) for d in m[1].split(";") if d)
        cols = decl["grid-template-columns"]
        first, rest = re.fullmatch(r"(calc\(.*\)) (1fr)", cols).groups()
        env = {"var(--pane)": span.pane, "var(--hinge)": span.hinge, "env(safe-area-inset-left,0px)": inset}
        return calc(first, **env), rest, calc(decl["column-gap"], **env)

    def test_duo(self):
        first, rest, gap = self.grid(DUO)
        self.assertEqual((first, rest, gap), (675, "1fr", 42))
        self.assertEqual(first + gap, 717)  # where the second pane begins: 1434 device px at scale 2

    def test_the_first_pane_ends_where_the_hinge_begins_whatever_the_left_inset(self):
        first, _, gap = self.grid(DUO, inset=40)
        self.assertEqual((first + 40, gap), (675, 42))

    def test_other_densities(self):
        for span in (Span(540, 33.6), Span(450, 28), Span(960, 60)):
            first, rest, gap = self.grid(span)
            self.assertAlmostEqual(first, span.pane)
            self.assertAlmostEqual(gap, span.hinge)

    def test_the_segment_env_values_are_the_only_things_swapped_for_variables(self):
        css = (STATIC / "phone-b.css").read_text(encoding="utf-8")
        body = blocks(css, "@media " + SEGMENTS + "{")[0]
        expected = body.replace("env(viewport-segment-width 0 0)", "var(--pane)") \
                       .replace("env(viewport-segment-left 1 0)", "calc(var(--pane) + var(--hinge))")
        self.assertIn("@media (min-width:1055px){" + expected.replace("\n", ""), style(page(span=DUO)))


class ServedPage(unittest.TestCase):
    def test_unset_is_byte_identical(self):
        base = page("t" * 43, "n" * 22)
        self.assertEqual(page("t" * 43, "n" * 22, span=None), base)
        self.assertEqual(page(demo=True, span=None), page(demo=True))
        self.assertIs(apply(base, None), base)
        self.assertNotIn("min-width:1055px", base)
        self.assertGreaterEqual(base.count(SEGMENTS), 3)  # phone-b.css, jaryo-layout.css and phone-b.js's TWO

    def test_every_rewrite_target_is_in_the_page(self):
        # A reworded asset would silently turn a rewrite into a no-op; this names the one that went stale.
        base = page()
        for old, new in rewrites(DUO):
            self.assertIn(old, base, old)
            self.assertNotIn(old, page(span=DUO), old)

    def test_segment_query_becomes_a_width_test_everywhere(self):
        out = page(span=DUO)
        self.assertNotIn("viewport-segment", out)
        # the three places the query lived plus the one block of rules the span adds
        self.assertEqual(out.count("(min-width:1055px)"), page().count(SEGMENTS) + 1)
        self.assertIn("var TWO='(min-width:1055px)'", out)
        self.assertIn("@media (min-width:1055px){html[data-ff=\"foldable\"] #phone,", out)

    def test_gate_is_the_first_pane_the_hinge_and_half_a_second_pane(self):
        self.assertEqual(spanmod.gate(DUO), 1055)  # 675 + 42 + 337.5, rounded up; the whole span is 1392
        self.assertEqual(spanmod.gate(Span(540, 33.6)), 844)
        self.assertEqual(spanmod.gate(Span(450, 28)), 840)  # never below the page's own 840 px two-column layouts
        self.assertIn("(min-width:1055px)", page(span=DUO))
        self.assertIn("(min-width:844px)", page(span=Span(540, 33.6)))
        for span in (DUO, Span(540, 33.6), Span(450, 28)):
            self.assertLess(span.pane, spanmod.gate(span))  # one pane never counts as spanning
            self.assertLessEqual(spanmod.gate(span), span.width)

    def test_auto_keeps_foldable_when_the_first_state_arrives(self):
        base, out = page(), page(span=DUO)
        self.assertIn("{v='auto';delete d.dataset.ff}", base)
        self.assertNotIn("delete d.dataset.ff}else", out)
        self.assertIn("{v='auto'}else d.dataset.ff=v;", out)

    def test_extra_rules_keep_the_hinge_clear(self):
        css = style(page(span=DUO))
        tail = css[css.rindex("@media (min-width:1055px){"):]
        self.assertIn("#sb-engine{flex:0 1 auto;max-width:calc(var(--pane) - 220px);text-align:left}", tail)
        self.assertIn("#meter-hit::after{left:calc(var(--pane)/2)}", tail)
        self.assertIn('html[data-ff="foldable"] #lock:not([hidden]){align-content:center}', tail)

    def test_single_pane_gets_no_new_rules(self):
        # Below the gate (one pane, 675 px) only the two custom properties are added; the phone layout is untouched.
        top = lambda h: [(sel, d) for context, sel, d in rules(style(h)) if not context]
        self.assertEqual(top(page(span=DUO)), top(page()) + [(":root", {"--pane": "675px", "--hinge": "42px"})])
        gated = {c for c, sel, d in rules(style(page(span=DUO)))} - {c for c, sel, d in rules(style(page()))}
        self.assertEqual(gated, {("@media (min-width:1055px)",)})

    def test_squeeze_changes_no_rule(self):
        base = page()
        squeezed = squeeze(base)
        self.assertLess(len(squeezed), len(base) - 300)
        self.assertEqual(list(rules(style(squeezed))), list(rules(style(base))))
        # Outside the style sheet only the two comment lines at the top of the markup went.
        tail = lambda h: h.partition("</style></head>")[2]
        self.assertEqual(tail(squeezed), re.sub(r"^<!--.*?-->\n", "", tail(base), flags=re.M))
        self.assertEqual(len(re.findall(r"^<!--.*?-->\n", tail(base), flags=re.M)), 2)
        self.assertEqual(tail(squeezed).count("<script>"), tail(base).count("<script>"))

    def test_fits_the_page_budget_with_the_release_metadata(self):
        # The same recipe as test_uxp30_budget: 43-character token, 22-character nonce, 12-character build.
        with mock.patch("opos.notices.BUILD", "f" * 12):
            served = page("t" * 43, "n" * 22, demo=True, span=DUO)
            plain = page("t" * 43, "n" * 22, demo=True)
        self.assertLessEqual(len(served.encode("utf-8")), PAGE_BUDGET)
        self.assertLessEqual(len(plain.encode("utf-8")), PAGE_BUDGET)

    def test_nonce_reaches_every_inline_tag(self):
        out = page("t" * 43, "n" * 22, span=DUO)
        self.assertNotIn("<style>", out)
        self.assertNotIn("<script>", out)
        self.assertEqual(out.count('<style nonce="' + "n" * 22 + '">'), 1)
        self.assertEqual(out.count("<script nonce="), page("t" * 43, "n" * 22).count("<script nonce=") + 1)

    def test_readout_states_the_window_for_the_device_check(self):
        out = page(span=DUO)
        self.assertEqual(out.count(spanmod.readout(DUO)), 1)
        self.assertTrue(out.endswith(spanmod.readout(DUO) + "</body></html>"))
        self.assertIn("document.getElementById('ff-detected')", spanmod.readout(DUO))
        self.assertIn("span 675 + 42 + 675.", spanmod.readout(DUO))
        self.assertIn("span 540 + 33.6 + 540.", spanmod.readout(Span(540, 33.6)))
        self.assertNotIn("innerWidth", page())

    def test_readout_is_added_again_after_every_resize(self):
        # phone-b.js rewrites the whole line on each resize (the on-screen keyboard, a compositor configure), which
        # took the readout away when it was appended on load only.
        out, readout = page(span=DUO), spanmod.readout(DUO)
        self.assertIn("['load','resize'].forEach(function(t){addEventListener(t,function(){", readout)
        self.assertEqual(readout.count("textContent+="), 1)
        # Appended after phone-b.js has rewritten the line, so it cannot be added twice: that script's resize
        # listener is registered before this one.
        self.assertEqual(out.count("window.addEventListener('resize',guess)"), 1)
        self.assertLess(out.index("window.addEventListener('resize',guess)"), out.index(readout))

    def test_readout_is_added_once_when_a_resize_comes_before_load(self):
        # A compositor configure during start-up can deliver resize before load; nothing rewrites the line in between,
        # so the second call must see the readout already there.
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        body = spanmod.readout(DUO)[len("<script>"):-len("</script>")]
        driver = ("var h={},e={textContent:'Auto looks like: two screens'};"
                  "var addEventListener=function(t,f){h[t]=f};"
                  "var document={getElementById:function(){return e}};"
                  "var innerWidth=1392,innerHeight=900,devicePixelRatio=1;"
                  + body + ";h.resize();h.load();console.log(e.textContent)")
        out = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=30).stdout.strip()
        self.assertEqual(out.count(". Window "), 1, out)
        self.assertIn("span 675 + 42 + 675.", out)


@contextlib.contextmanager
def serving(**kw):
    """The real server on an ephemeral loopback port; yields (server, fetch)."""
    server = web.make_server(0, Phone(), **kw)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def fetch(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
            res = conn.getresponse()
            return res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
        finally:
            conn.close()
    try:
        yield server, fetch
    finally:
        server.shutdown()
        server.server_close()


class Wiring(unittest.TestCase):
    def test_server_serves_the_span_page_and_only_when_asked(self):
        for span in (None, DUO):
            with serving(span=span) as (server, fetch):
                status, headers, body = fetch("/")
                token = server.RequestHandlerClass.token
            self.assertEqual(status, 200)
            nonce = re.search(rb'<style nonce="([^"]+)">', body)[1].decode()
            self.assertIn("nonce-" + nonce, headers["content-security-policy"])
            self.assertEqual(body, page(token, nonce, span=span).encode())
            self.assertEqual(b"--pane:675px" in body, span is not None)

    def test_make_server_without_a_span_takes_the_old_arguments(self):
        with serving() as (server, fetch):
            self.assertIsNone(server.RequestHandlerClass.span)
            self.assertNotIn(b"--pane", fetch("/")[2])

    def main(self, argv, env=None):
        captured = {}

        def fake_make_server(port, phone, host="127.0.0.1", token=None, **kw):
            captured["kw"] = kw
            return mock.Mock(serve_forever=mock.Mock())

        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, dict(env or {}, OPOS_CONFIG_DIR=directory)), \
                mock.patch.object(web, "_route_with_glimmer"), \
                mock.patch.object(web, "make_server", side_effect=fake_make_server), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if not env or "OPOS_SPAN" not in env:
                os.environ.pop("OPOS_SPAN", None)
            web.main(argv)
        return captured["kw"], out.getvalue(), err.getvalue()

    def test_option(self):
        kw, out, err = self.main(["--span", "675,42"])
        self.assertEqual(kw, {"span": DUO})
        self.assertIn("675 + 42 + 675 CSS px", out)
        self.assertEqual(err, "")

    def test_environment(self):
        kw, out, _ = self.main([], {"OPOS_SPAN": "675,42"})
        self.assertEqual(kw, {"span": DUO})
        self.assertIn("span", out)

    def test_option_beats_environment_and_off_turns_it_off(self):
        self.assertEqual(self.main(["--span", "540,33.6"], {"OPOS_SPAN": "675,42"})[0], {"span": Span(540, 33.6)})
        self.assertEqual(self.main(["--span", "off"], {"OPOS_SPAN": "675,42"})[0], {})

    def test_unset_calls_make_server_as_before(self):
        kw, out, err = self.main([])
        self.assertEqual(kw, {})  # no span keyword at all: older make_server signatures still work
        self.assertNotIn("span", out)
        self.assertEqual(err, "")

    def test_bad_environment_is_reported_and_ignored(self):
        kw, _, err = self.main([], {"OPOS_SPAN": "wide"})
        self.assertEqual(kw, {})
        self.assertIn("ignoring OPOS_SPAN", err)

    def test_bad_option_stops_the_start(self):
        err = io.StringIO()
        with mock.patch.object(web, "make_server") as make, contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            web.main(["--span", "wide"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--span", err.getvalue())
        make.assert_not_called()

    def test_service_file_carries_the_value_commented_out(self):
        lines = (ROOT / "packaging" / "duo-os" / "opos-server.service").read_text(encoding="utf-8").splitlines()
        self.assertIn("#Environment=OPOS_SPAN=675,42", lines)
        self.assertFalse([l for l in lines if l.startswith("Environment=OPOS_SPAN")])
        self.assertEqual(parse("675,42"), DUO)


class Docs(unittest.TestCase):
    def test_readme_says_the_window_readout_is_shown_whatever_the_width(self):
        text = " ".join((ROOT / "packaging" / "duo-os" / "README.md").read_text(encoding="utf-8").split())
        self.assertIn("whatever the window's width it adds the window's size, the output scale and the span it was given", text)
        self.assertIn("again after every resize", text)
        self.assertNotIn("while the window spans both panels. Unset", text)  # the old sentence tied the size to it


# Runs in the browser page: every visible piece of text, button and field against the hinge, plus the numbers
# that show where the layout put things.
MEASURE = r"""(function(){
var lo=675,hi=717,bad=[],n=0;
function shown(e){for(var p=e;p&&p.nodeType===1;p=p.parentElement){if(p.hidden)return false;var c=getComputedStyle(p);if(c.display==='none'||c.visibility==='hidden')return false}return true}
function cross(r){return r.width>0&&r.height>0&&r.left<hi-0.5&&r.right>lo+0.5}
var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
while(w.nextNode()){var t=w.currentNode,e=t.parentElement;if(!t.nodeValue.trim()||!e||!shown(e)||/^(SCRIPT|STYLE)$/.test(e.tagName))continue;
 var rg=document.createRange();rg.selectNodeContents(t);Array.prototype.forEach.call(rg.getClientRects(),function(r){n++;if(cross(r))bad.push('text '+t.nodeValue.trim().slice(0,24)+' '+Math.round(r.left)+'-'+Math.round(r.right))})}
Array.prototype.forEach.call(document.querySelectorAll('button,input,select,textarea,summary'),function(b){if(b.id==='meter'||!shown(b))return;var r=b.getBoundingClientRect();n++;if(cross(r))bad.push((b.id||b.tagName)+' '+Math.round(r.left)+'-'+Math.round(r.right))});
var p=document.getElementById('phone'),cs=getComputedStyle(p),root=getComputedStyle(document.documentElement);
return {ff:document.documentElement.dataset.ff||null,w:innerWidth,scroll:document.documentElement.scrollWidth,n:n,bad:bad,
 display:cs.display,cols:cs.gridTemplateColumns,gap:cs.columnGap,pane:root.getPropertyValue('--pane').trim(),hinge:root.getPropertyValue('--hinge').trim(),
 auto:(document.getElementById('ff-detected')||{}).textContent||''}})()"""

# The browser start-up, DevTools plumbing and canned state are the UXP21 driver's; only its cases are replaced.
MAIN = r"""
const MEASURE2 = %s;
const plainUrl = process.argv[5];  // argv: chrome, span page, state file, plain page
async function run(url, w, h, q, after) {
  await send('Emulation.setDeviceMetricsOverride', {width: w, height: h, deviceScaleFactor: 1, mobile: true,
    screenOrientation: w > h ? {type: 'landscapePrimary', angle: 90} : {type: 'portraitPrimary', angle: 0}});
  await send('Page.navigate', {url: url + q});
  await sleep(50);
  if (!await waitFor(`document.readyState==='complete'&&location.protocol==='file:'`)) throw new Error('page did not load ' + q);
  if (/stub=1/.test(q) && !await waitFor(`window.oposUI&&document.getElementById('phone').dataset.app`)) throw new Error('shell did not render ' + q);
  if (after) await ev(after);
  await sleep(250);
  return ev(MEASURE2);
}
(async () => {
  const timer = setTimeout(() => {
    process.stderr.write('timed out\n');
    closeBrowser().catch(e => process.stderr.write(String(e) + '\n')).finally(() => process.exit(3));
  }, 60000);
  const out = {};
  try {
    const {targetId} = await sendTo(null, 'Target.createTarget', {url: 'about:blank'});
    ({sessionId: session} = await sendTo(null, 'Target.attachToTarget', {targetId, flatten: true}));
    await send('Page.enable'); await send('Runtime.enable');
    // Where things rest, not where they pass: the Jaryo zoom from a tile to its app (240 ms) travels across the hinge.
    await send('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'reduce'}]});
    await send('Page.addScriptToEvaluateOnNewDocument', {source: PRE});
    const q = '?lock=0&stub=1';
    for (const app of ['home', 'phone', 'messages', 'helper', 'settings'])
      out['span_' + app] = await run(fileUrl, 1392, 900, q, `oposUI.openApp('${app}')`);
    out.span_allapps = await run(fileUrl, 1392, 900, q, `oposUI.openApp('home');document.getElementById('allapps').click()`);
    out.span_lock = await run(fileUrl, 1392, 900, '?stub=1', `oposUI.lock&&oposUI.lock()`);
    out.span_shade = await run(fileUrl, 1392, 900, q, `oposUI.openApp('home');opos.open()`);
    // Largest text with the helper's status line showing: its text must stay in the first pane.
    out.span_xl = await run(fileUrl, 1392, 900, q, `document.documentElement.dataset.text='xl';oposUI.openApp('home');api({action:'visible',visible:true}).then(render)`);
    out.narrow = await run(fileUrl, 1100, 900, q, `oposUI.openApp('phone')`);
    out.below = await run(fileUrl, 1000, 900, q, `oposUI.openApp('home')`);
    out.one_pane = await run(fileUrl, 675, 900, q, `oposUI.openApp('home')`);
    out.one_pane_phone = await run(fileUrl, 675, 900, q, `oposUI.openApp('phone')`);
    out.slab = await run(fileUrl, 1392, 900, q + '&ff=slab', `oposUI.openApp('home')`);
    out.plain = await run(plainUrl, 1392, 900, q, `oposUI.openApp('home')`);
    // phone-b.js writes the Device type line again on every resize: the readout has to come back, once, with the new size.
    out.resized = await run(fileUrl, 1392, 900, q, `oposUI.openApp('settings');dispatchEvent(new Event('resize'));dispatchEvent(new Event('resize'))`);
    await send('Emulation.setDeviceMetricsOverride', {width: 1392, height: 800, deviceScaleFactor: 1, mobile: true,
      screenOrientation: {type: 'landscapePrimary', angle: 90}});
    await sleep(250);
    out.resized_real = await ev(MEASURE2);
  } finally {
    clearTimeout(timer);
    await closeBrowser();
  }
  process.stdout.write(JSON.stringify(out) + '\n', () => process.exit(0));
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(2); });
""" % json.dumps(MEASURE)


def driver():
    """The UXP21 driver up to its cases, then this file's.

    Another track may change that driver. Looked up when the browser tests start, not when this file is imported, so
    the loss costs those six tests (an error naming the cause, whether or not a browser is installed) and not the
    parse, served-page and wiring tests next to them."""
    head, sep, _ = CDP.partition("async function run(")
    if not sep:
        raise AssertionError("tests/test_uxp21_profiles.py's driver changed: it has no 'async function run(' for "
                             "tests/test_span.py to replace; update MAIN here to match")
    return head + MAIN


class DriverDrift(unittest.TestCase):
    def test_a_changed_shared_driver_costs_only_the_browser_tests(self):
        # The same file loaded again while the UXP21 driver no longer has the part this file replaces: the import must
        # still work (it used to fail on an assert, taking every test in the module with it).
        spec = importlib.util.spec_from_file_location("tests.test_span_changed_driver", __file__)
        copy = importlib.util.module_from_spec(spec)
        with mock.patch.object(profiles, "CDP", "// rewritten: nothing named run any more"):
            spec.loader.exec_module(copy)
        with self.assertRaises(AssertionError) as ctx:
            copy.Browser.setUpClass()
        self.assertIn("async function run(", str(ctx.exception))
        result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromTestCase(copy.Parse))
        self.assertGreater(result.testsRun, 0)
        self.assertTrue(result.wasSuccessful())

    def test_the_driver_is_the_shared_start_up_then_these_cases(self):
        script = driver()
        self.assertTrue(script.endswith(MAIN))
        head = script[:-len(MAIN)]
        self.assertTrue(head and CDP.startswith(head))
        self.assertNotIn("async function run(", head)
        self.assertEqual(script.count("async function run("), 1)


class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = None
        script = driver()
        chrome = find_chrome()
        if not chrome or not NODE:
            return
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            files = {}
            for name, span in (("span", DUO), ("plain", None)):
                files[name] = Path(d) / (name + ".html")
                files[name].write_text(page(span=span), encoding="utf-8")
            state = Path(d) / "state.json"
            state.write_text(json.dumps({"hidden": Phone().state(), "visible": Phone(visible=True).state()}, default=str), encoding="utf-8")
            drv = Path(d) / "cdp.js"
            drv.write_text(script, encoding="utf-8")
            p = subprocess.run([NODE, str(drv), chrome, files["span"].as_uri(), str(state), files["plain"].as_uri()],
                               capture_output=True, text=True, timeout=90, env=dict(os.environ, TMPDIR=d))
            if p.returncode:
                raise AssertionError("CDP driver failed: " + p.stderr[-5000:])
            cls.r = json.loads(p.stdout.strip().splitlines()[-1])

    def setUp(self):
        if self.r is None:
            self.skipTest("no headless chromium or node")

    def test_nothing_lands_on_the_hinge(self):
        for k in ("span_home", "span_phone", "span_messages", "span_helper", "span_settings", "span_allapps", "span_lock", "span_shade", "span_xl"):
            m = self.r[k]
            self.assertEqual(m["ff"], "foldable", k)
            self.assertGreater(m["n"], 0, k)
            self.assertEqual(m["bad"], [], k)
            self.assertLessEqual(m["scroll"], m["w"], k)
            self.assertIn("two screens", m["auto"], k)
            self.assertIn("Window 1392 x 900 CSS px at 1x, span 675 + 42 + 675.", m["auto"], k)

    def test_variables_drive_the_grid(self):
        m = self.r["span_home"]
        self.assertEqual((m["pane"], m["hinge"]), ("675px", "42px"))
        self.assertEqual(m["display"], "grid")
        self.assertEqual(m["cols"], "675px 675px")
        self.assertEqual(m["gap"], "42px")

    def test_a_window_a_little_narrower_than_the_span_still_spans(self):
        # The panes and the hinge are anchored at the left edge, so only the second pane gets narrower.
        m = self.r["narrow"]
        self.assertEqual((m["ff"], m["bad"], m["cols"], m["gap"]), ("foldable", [], "675px 383px", "42px"))
        self.assertIsNone(self.r["below"]["ff"])  # under 1055 px it is not taken for two panels

    def test_one_pane_is_the_plain_phone_layout(self):
        for k in ("one_pane", "one_pane_phone"):
            m = self.r[k]
            self.assertIsNone(m["ff"], k)
            self.assertEqual(m["display"], "flex", k)
            self.assertLessEqual(m["scroll"], 675, k)
            # The readout is not tied to the window spanning: it is also what shows a window that does not.
            self.assertNotIn("two screens", m["auto"], k)
            self.assertTrue(m["auto"].endswith(". Window 675 x 900 CSS px at 1x, span 675 + 42 + 675."), m["auto"])

    def test_the_readout_follows_the_window_when_it_resizes(self):
        # A resize event, then a real change of the window's height: the line is written again each time and the
        # readout comes back once, with the size the window has now.
        for k, height in (("resized", 900), ("resized_real", 800)):
            self.assertEqual(self.r[k]["auto"],
                             "Auto looks like: two screens. Window 1392 x %d CSS px at 1x, span 675 + 42 + 675." % height, k)

    def test_an_explicit_device_type_is_still_honoured(self):
        self.assertEqual(self.r["slab"]["ff"], "slab")

    def test_without_the_option_the_same_window_is_not_foldable(self):
        # Why the option exists: Chromium on Linux cannot report segments, so Auto never picks Foldable.
        m = self.r["plain"]
        self.assertIsNone(m["ff"])
        self.assertEqual(m["pane"], "")


if __name__ == "__main__":
    unittest.main()
