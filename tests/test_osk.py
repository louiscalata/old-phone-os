# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo OS on-screen keyboard: the server side (injection, routes, unset = unchanged), the shipped files, and the logic under node."""
import http.client
import inspect
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from opos import osk
from tests import webclient

# tests/webclient.py passes skip_host to HTTPConnection.request, which newer Pythons no longer take; the other web tests
# strip it with a module-level shim, so a run of this module alone does the same (unless a shim is already there).
_params = inspect.signature(http.client.HTTPConnection.request).parameters
if "skip_host" not in _params and not any(p.kind is p.VAR_KEYWORD for p in _params.values()):
    _orig_request = http.client.HTTPConnection.request

    def _compat(self, method, url, body=None, headers=None, **kw):
        kw.pop("skip_host", None)
        kw.pop("skip_accept_encoding", None)
        return _orig_request(self, method, url, body=body, headers=headers or {}, **kw)
    http.client.HTTPConnection.request = _compat  # type: ignore[method-assign]

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "opos" / "osk_static"
JS = (STATIC / "osk.js").read_text(encoding="utf-8")
CSS = (STATIC / "osk.css").read_text(encoding="utf-8")
PAGE_BUDGET = 100 * 1024  # tests/test_uxp30_budget.py
TOKEN = "t" * 43


def env_without(**kw):
    """os.environ with OPOS_OSK* cleared, then kw set."""
    e = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_OSK")}
    e.update(kw)
    return mock.patch.dict(os.environ, e, clear=True)


class OskEnvTest(unittest.TestCase):
    def test_mode(self):
        for v, m in (("1", "web"), ("web", "web"), (" WEB ", "web"), ("stevia", "stevia"), ("Stevia", "stevia"),
                     ("", ""), ("0", ""), ("off", ""), ("true", ""), ("2", ""), ("web2", "")):
            self.assertEqual(osk.mode({"OPOS_OSK": v}), m, v)
        self.assertEqual(osk.mode({}), "")
        self.assertTrue(osk.enabled({"OPOS_OSK": "1"}))
        self.assertFalse(osk.enabled({"OPOS_OSK": "stevia"}))
        self.assertFalse(osk.enabled({}))

    def test_hinge(self):
        self.assertEqual(osk.hinge({}), 42)
        self.assertEqual(osk.hinge({"OPOS_OSK_HINGE": "0"}), 0)
        self.assertEqual(osk.hinge({"OPOS_OSK_HINGE": "off"}), 0)
        self.assertEqual(osk.hinge({"OPOS_OSK_HINGE": "36"}), 36)
        for bad in ("-1", "201", "x", "4.5", "9999999999"):
            self.assertEqual(osk.hinge({"OPOS_OSK_HINGE": bad}), 42, bad)

    def test_tags(self):
        t = osk.tags("abc_DEF-123", {"OPOS_OSK_HINGE": "40"})
        self.assertEqual(t, '<link rel="stylesheet" href="/osk.css" nonce="abc_DEF-123">'
                            '<script src="/osk.js" nonce="abc_DEF-123" data-hinge="40" defer></script>')
        for bad in ("", "a b", 'a"b', "<x>", "a" * 129, None, 5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                osk.tags(bad)

    def test_inject(self):
        on, off = {"OPOS_OSK": "1"}, {}
        html = "<html><body><p>x</p></body></html>"
        got = osk.inject(html, "n1", on)
        self.assertEqual(got, "<html><body><p>x</p>" + osk.tags("n1", on) + "</body></html>")
        self.assertEqual(osk.inject(got, "n1", on), got, "idempotent")
        self.assertEqual(osk.inject(html, "n1", off), html)
        self.assertEqual(osk.inject("<p>no body end", "n1", on), "<p>no body end" + osk.tags("n1", on))
        two = "<body></body><script>'</body>'</script></BODY>"
        self.assertTrue(osk.inject(two, "n1", on).endswith(osk.tags("n1", on) + "</BODY>"), "before the last </body>, any case")

    def test_inject_survives_characters_whose_lower_case_is_longer(self):
        on = {"OPOS_OSK": "1"}  # "İ".lower() is two characters, so an index into lower() is wrong for the original
        html = "<html><body><p>İlker İİ</p></body></html>"
        got = osk.inject(html, "n1", on)
        self.assertEqual(got, "<html><body><p>İlker İİ</p>" + osk.tags("n1", on) + "</body></html>")
        self.assertEqual(osk.inject("<BODY>İ</BODY >", "n1", on), "<BODY>İ" + osk.tags("n1", on) + "</BODY >")

    def test_inject_bytes_is_the_same_object_while_off(self):
        body = "<html><body>é</body></html>".encode("utf-8")
        self.assertIs(osk.inject_bytes(body, "n1", {}), body)
        self.assertIs(osk.inject_bytes(body, "n1", {"OPOS_OSK": "stevia"}), body)
        got = osk.inject_bytes(body, "n1", {"OPOS_OSK": "1"})
        self.assertIsInstance(got, bytes)
        self.assertIn("é".encode("utf-8"), got)
        self.assertIn(b'src="/osk.js"', got)

    def test_asset(self):
        on = {"OPOS_OSK": "1"}
        js, css = osk.asset("/osk.js", on), osk.asset("/osk.css", on)
        self.assertEqual(js, ((STATIC / "osk.js").read_bytes(), "text/javascript; charset=utf-8"))
        self.assertEqual(css, ((STATIC / "osk.css").read_bytes(), "text/css; charset=utf-8"))
        for p in ("/osk.js", "/osk.css"):
            self.assertIsNone(osk.asset(p, {}))
            self.assertIsNone(osk.asset(p, {"OPOS_OSK": "stevia"}))
        for p in ("/osk", "/osk.js/", "/osk.json", "/OSK.JS", "/../osk.js", "/web_static/osk.js", "/"):
            self.assertIsNone(osk.asset(p, on), p)


class OskServerTest(unittest.TestCase):
    def get(self, run, path="/", method="GET", **kw):
        return webclient.request(run.port, method, path, **kw)

    def expected_page(self, run, headers):
        nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", headers["content-security-policy"]).group(1)
        return nonce, webclient.web.page(TOKEN, nonce)

    def test_unset_the_page_and_every_route_are_unchanged(self):
        for value in (None, "", "0", "off", "stevia", "nonsense"):
            with env_without(**({} if value is None else {"OPOS_OSK": value})), webclient.Running(token=TOKEN) as run:
                status, headers, body = self.get(run)
                nonce, page = self.expected_page(run, headers)
                self.assertEqual(status, 200)
                self.assertEqual(body, page.encode("utf-8"), "byte-identical page, OPOS_OSK=%r" % value)
                self.assertNotIn(b"/osk.", body)
                for p in ("/osk.js", "/osk.css", "/osk.js?x=1"):
                    s, h, b = self.get(run, p)
                    self.assertEqual(s, 404, (value, p))
                    self.assertEqual(b, b'{"error": "not found"}')
                s, h, b = self.get(run, "/osk.js", "HEAD")
                self.assertEqual(s, 404)

    def test_on_the_page_gets_two_tags_and_nothing_else(self):
        with env_without(OPOS_OSK="1"), webclient.Running(token=TOKEN) as run:
            status, headers, body = self.get(run)
            nonce, page = self.expected_page(run, headers)
            tags = osk.tags(nonce, {"OPOS_OSK": "1"})
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
            self.assertEqual(body.decode("utf-8"), page[:page.rindex("</body>")] + tags + "</body></html>")
            self.assertEqual(body.decode("utf-8").replace(tags, ""), page, "removing the tags gives the unchanged page")
            self.assertEqual(int(headers["content-length"]), len(body))
            self.assertIn(f"script-src 'nonce-{nonce}'", headers["content-security-policy"])
            self.assertIn(f"style-src 'nonce-{nonce}'", headers["content-security-policy"])
            self.assertEqual(body.count(b"/osk.js"), 1)

    def test_without_the_module_the_server_serves_what_it_always_served(self):
        # a runtime that ships web.py without src/opos/osk.py (the pmos inventory once did): no 500, no keyboard, even with the env set
        with env_without(OPOS_OSK="1"), mock.patch.object(webclient.web, "osk", None), webclient.Running(token=TOKEN) as run:
            status, headers, body = self.get(run)
            nonce, page = self.expected_page(run, headers)
            self.assertEqual((status, body), (200, page.encode("utf-8")))
            for p in ("/osk.js", "/osk.css"):
                self.assertEqual(self.get(run, p)[0], 404, p)
            self.assertEqual(self.get(run, "/favicon.ico")[0], 200)

    def test_the_pmos_runtime_inventory_lists_the_keyboard_files(self):
        listed = (ROOT / "packaging/pmos/runtime-files.txt").read_text(encoding="utf-8").splitlines()
        for f in ("src/opos/osk.py", "src/opos/osk_static/osk.css", "src/opos/osk_static/osk.js"):
            self.assertIn(f, listed)
        paths = [x for x in listed if x and not x.startswith("#")]
        self.assertEqual(paths, sorted(paths), "make-runtime-source.py wants the list sorted")

    def test_on_the_two_files_are_served_with_the_page_hardening(self):
        with env_without(OPOS_OSK="1"), webclient.Running(token=TOKEN) as run:
            for path, name, ctype in (("/osk.js", "osk.js", "text/javascript; charset=utf-8"), ("/osk.css", "osk.css", "text/css; charset=utf-8")):
                for q in ("", "?v=2"):
                    status, h, body = self.get(run, path + q)
                    self.assertEqual(status, 200, path + q)
                    self.assertEqual(body, (STATIC / name).read_bytes())
                    self.assertEqual(h["content-type"], ctype)
                    self.assertEqual(h["x-content-type-options"], "nosniff")
                    self.assertEqual(h["cache-control"], "no-store")
                    self.assertEqual(h["cross-origin-resource-policy"], "same-origin")
                    self.assertEqual(int(h["content-length"]), len(body))
                status, h, body = self.get(run, path, "HEAD")
                self.assertEqual((status, body), (200, b""))
                self.assertEqual(int(h["content-length"]), len((STATIC / name).read_bytes()))

    def test_on_foreign_hosts_are_refused_before_the_files_are_served(self):
        with env_without(OPOS_OSK="1"), webclient.Running(token=TOKEN) as run:
            for host in ("evil.example", "127.0.0.1:1", "localhost"):
                s, h, b = self.get(run, "/osk.js", headers={"Host": host})
                self.assertEqual(s, 403, host)
                self.assertNotIn(b"OPOS on-screen keyboard", b)
            s, h, b = self.get(run, "/osk.js", headers={"Host": f"localhost:{run.port}"})
            self.assertEqual(s, 200)

    def test_on_other_methods_and_paths_are_as_before(self):
        with env_without(OPOS_OSK="1"), webclient.Running(token=TOKEN) as run:
            s, h, b = self.get(run, "/osk.js", "POST", body=b"{}", headers={"Content-Type": "application/json"})
            self.assertEqual(s, 404)
            s, h, b = self.get(run, "/osk.js", "PUT")
            self.assertEqual(s, 405)
            s, h, b = self.get(run, "/favicon.ico")
            self.assertEqual((s, h["content-type"]), (200, "image/png"))
            self.assertEqual(b, webclient.web.FAVICON)
            s, h, b = self.get(run, "/osk.txt")
            self.assertEqual(s, 404)

    def test_the_page_itself_never_knows_about_the_keyboard(self):
        with env_without():
            off = webclient.web.page(TOKEN, "n" * 22)
        with env_without(OPOS_OSK="1"):
            on = webclient.web.page(TOKEN, "n" * 22)
        self.assertEqual(on, off, "web.page() ignores OPOS_OSK: the size budget is untouched")
        self.assertNotIn("/osk.", off)
        self.assertNotIn("opos-osk", off)
        self.assertLessEqual(len(off.encode("utf-8")), PAGE_BUDGET)
        self.assertLessEqual(len(osk.inject(off, "n" * 22, {"OPOS_OSK": "1"}).encode("utf-8")) - len(off.encode("utf-8")), 300,
                             "the two tags are small")

    def test_the_keyboard_files_are_not_inlined_in_the_page(self):
        page = webclient.web.page(TOKEN, "n" * 22)
        self.assertNotIn("opos-osk", page)
        self.assertNotIn("osk-panel", page)
        self.assertTrue(STATIC.is_dir() and not (ROOT / "src/opos/web_static/osk.js").exists())


class OskFilesTest(unittest.TestCase):
    def test_headers_and_line_endings(self):
        for name in ("osk.js", "osk.css"):
            raw = (STATIC / name).read_bytes()
            self.assertNotIn(b"\r", raw, name)
            text = raw.decode("utf-8")
            self.assertTrue(text.isascii(), name + " is ASCII (other characters are written as escapes)")
            self.assertTrue(text.startswith("/* SPDX-License-Identifier: GPL-3.0-or-later */\n"), name)
            self.assertTrue(text.endswith("\n"), name)
        self.assertTrue((ROOT / "src/opos/osk.py").read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: GPL-3.0-or-later\n"))

    def test_sizes(self):
        self.assertLess(len(JS.encode("utf-8")), 40 * 1024)
        self.assertLess(len(CSS.encode("utf-8")), 8 * 1024)

    def test_nothing_external_and_no_dynamic_code(self):
        for name, text in (("osk.js", JS), ("osk.css", CSS)):
            self.assertNotRegex(text, r"https?://", name)
            self.assertNotRegex(text, r"//[a-z0-9.-]+\.[a-z]{2,}/", name)
        self.assertNotIn("@import", CSS)
        self.assertNotRegex(CSS, r"url\(")
        self.assertNotRegex(CSS, r"@font-face")
        for bad in ("eval(", "new Function", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "XMLHttpRequest", "fetch(",
                    "WebSocket", "sendBeacon", "importScripts", "setAttribute('style'"):
            self.assertNotIn(bad, JS, bad)
        self.assertNotRegex(JS, r"setTimeout\(\s*['\"]")

    def test_storage_is_guarded(self):
        uses = [m.start() for m in re.finditer(r"localStorage", JS)]
        self.assertTrue(uses)
        for i in uses:
            self.assertRegex(JS[max(0, i - 120):i], r"try \{", "every localStorage use sits in a try")

    def test_the_page_part_only_runs_in_a_page_and_the_core_is_exported_for_tests(self):
        self.assertIn("if (typeof module === 'object' && module && module.exports) module.exports = core;", JS)
        self.assertIn("typeof document !== 'undefined' && document && document.documentElement", JS)
        self.assertIn("root.oposOsk = core", JS)
        self.assertTrue(JS.rstrip().endswith("})(typeof window !== 'undefined' ? window : globalThis);"))
        self.assertIn("(function (root) {\n'use strict';", JS)

    def test_sizes_in_the_script_and_the_style_sheet_agree(self):
        m = re.search(r"var M = \{([^}]*)\}", JS)
        const = {k: float(v) for k, v in re.findall(r"(\w+): ([0-9.]+)", m.group(1))}
        self.assertGreaterEqual(const["key"], 56)
        self.assertEqual(const["key"], float(re.search(r"--osk-key:(\d+)px", CSS).group(1)))
        self.assertEqual(const["bar"], float(re.search(r"--osk-bar:(\d+)px", CSS).group(1)))
        self.assertEqual(const["gap"], float(re.search(r"--osk-gap:(\d+)px", CSS).group(1)))
        panel = re.search(r"\.osk-panel\{([^}]*)\}", CSS).group(1)
        self.assertIn("gap:var(--osk-gap)", panel)
        self.assertEqual(const["pad"], float(re.search(r"padding:(\d+)px", panel).group(1)))
        self.assertIn("border:1px solid", panel)
        self.assertEqual(const["border"], 1)
        self.assertRegex(CSS, r"\.osk-key\{[^}]*min-height:var\(--osk-key\)")
        self.assertRegex(CSS, r"\.osk-row\{[^}]*min-height:var\(--osk-key\)")

    def test_the_style_sheet_follows_the_page_theme(self):
        self.assertIn("var(--jaryo-bg,#f3f0e8)", CSS)
        self.assertIn("var(--jaryo-fg,#1a1a1a)", CSS)
        self.assertRegex(CSS, r"@media \(prefers-color-scheme:dark\)\{#opos-osk\{--osk-bg:var\(--jaryo-bg,#000\);--osk-fg:var\(--jaryo-fg,#e6e2d9\)")
        self.assertIn("#opos-osk[hidden]{display:none}", CSS)
        self.assertIn(":focus-visible", CSS)
        self.assertIn("forced-colors", CSS)
        self.assertRegex(CSS, r"#opos-osk\{[^}]*pointer-events:none")
        self.assertRegex(CSS, r"\.osk-panel\{[^}]*pointer-events:auto")
        self.assertRegex(CSS, r"\.osk-panel\{[^}]*touch-action:none")
        self.assertRegex(CSS, r"#opos-osk\{[^}]*z-index:2147483000")
        self.assertRegex(CSS, r"touch-action:none", "a swipe over the keys must not scroll the page or cancel a key")

    def test_the_keys_are_labelled_buttons_in_a_named_group(self):
        for s in ("setAttribute('role', 'group')", "setAttribute('aria-label', 'On-screen keyboard')", "b.type = 'button'",
                  "'aria-pressed'", "b.tabIndex = -1", "e.detail === 0", "e.preventDefault();"):
            self.assertIn(s, JS, s)
        for label in ("Delete", "Enter", "Shift", "space", "Hide the keyboard"):
            self.assertIn(label, JS)

    def test_keys_do_not_take_the_focus(self):
        self.assertIn("r.addEventListener('pointerdown'", JS)
        self.assertIn("r.addEventListener('mousedown', function (e) { e.preventDefault(); });", JS)

    def test_every_page_handler_event_the_page_relies_on_is_sent(self):
        for s in ("'keydown'", "'keyup'", "'beforeinput'", "'input'", "'change'", "key: key", "bubbles: true"):
            self.assertIn(s, JS, s)
        # js-8.js / phone-b.js read these on the Enter handlers of #number, #msg and #q
        page = "\n".join((ROOT / "src/opos/web_static" / n).read_text(encoding="utf-8") for n in ("js-8.js", "phone-b.js"))
        self.assertIn("e.key==='Enter'&&!e.isComposing&&!e.repeat", page)
        self.assertNotIn("isComposing: true", JS)

    def test_the_text_fields_the_page_has_are_all_covered(self):
        body = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "src/opos/web_static").glob("*.html")))
        fields = re.findall(r"<(?:input|textarea)\b[^>]*>", body)
        text_like = []
        for f in fields:
            m = re.search(r'type="?([a-z-]+)', f)
            if (m.group(1) if m else "text") not in ("checkbox", "radio", "range"):
                text_like.append(f)
        ids = sorted(re.search(r'id="([^"]+)"', f).group(1) for f in text_like)
        self.assertEqual(ids, ["goal", "msg", "number", "q"], "a new text field needs a look at the keyboard's kinds")
        self.assertIn('type="time" id="goal"', body)
        self.assertIn('inputmode="tel"', body)


NODE = os.environ.get("OPOS_TEST_NODE") or shutil.which("node") or shutil.which("nodejs")


@unittest.skipUnless(NODE, "node is not installed (the keyboard's logic is also checked at string level above)")
class OskLogicNodeTest(unittest.TestCase):
    def run_node(self, *args):
        return subprocess.run([NODE, *args], capture_output=True, text=True, timeout=120, cwd=str(ROOT))

    def test_syntax(self):
        r = self.run_node("--check", "src/opos/osk_static/osk.js")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_logic(self):
        r = self.run_node("tests/osk_core_test.js")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r"ok \d+ tests")
        self.assertNotIn("FAIL", r.stderr)


if __name__ == "__main__":
    unittest.main()
