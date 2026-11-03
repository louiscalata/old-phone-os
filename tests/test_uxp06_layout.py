# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
from pathlib import Path

from opos.web import page

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "opos" / "web_static"

WIDE = "@media (min-width:600px) and (min-aspect-ratio:3/4) and (max-aspect-ratio:4/3){"
SHORT = "@media (orientation:landscape) and (max-height:520px){"


class UXP06Layout(unittest.TestCase):
    def test_viewport_cover(self):
        out = page("t" * 32, "n")
        self.assertIn('content="width=device-width,initial-scale=1,viewport-fit=cover"', out)
        self.assertIn("viewport-fit=cover", out)
        self.assertIn("min-height:100vh;min-height:100dvh", out)

    def test_media_queries_present(self):
        out = page("t" * 32, "n")
        self.assertIn(WIDE, out)
        self.assertIn(SHORT, out)
        for ff in ("foldable", "slide", "slab"):
            self.assertIn('html[data-ff="%s"]' % ff, out)
        self.assertIn("padding-left:env(safe-area-inset-left,0px)", out)
        self.assertIn("padding-bottom:env(safe-area-inset-bottom,0px)", out)
        self.assertIn('html[data-ff="slab"] #phone{max-width:420px}', out)
        # Base column is unchanged.
        self.assertIn("#phone{max-width:420px;margin:auto;", out)

    def test_fixed_surfaces_use_safe_area(self):
        out = page("t" * 32, "n")
        css = out[:out.index("</style>")]
        # Each fixed surface gets its inset rule after its base rule, so it wins the cascade.
        for base, inset in (
            ("#sb{position:fixed;top:0", "#sb{height:calc(28px + env(safe-area-inset-top,0px));padding-top:env(safe-area-inset-top,0px)"),
            # UXP15 reserves 48px for the touch target, plus the safe-area inset.
            ("body{padding-top:48px}", "body{padding-top:calc(48px + env(safe-area-inset-top,0px))}"),
            ("#sheet{position:fixed;", "#sheet{padding-top:calc(16px + env(safe-area-inset-top,0px))"),
        ):
            self.assertIn(base, css)
            self.assertIn(inset, css)
            self.assertLess(css.index(base), css.index(inset), inset)
        self.assertIn("#lock{padding:env(safe-area-inset-top,0px) env(safe-area-inset-right,0px) env(safe-area-inset-bottom,0px) env(safe-area-inset-left,0px)}", css)
        self.assertIn("padding-left:max(12px,env(safe-area-inset-left,0px))", css)

    def test_form_factors_do_not_change_dom(self):
        normal = page("t" * 32, "n")
        demo = page("t" * 32, "n", demo=True)
        ids = re.findall(r'\bid="([^"]+)"', normal)
        self.assertEqual(set(ids), set(re.findall(r'\bid="([^"]+)"', demo)))
        for i in ("number", "msg", "q", "answer"):
            self.assertEqual(ids.count(i), 1, i)

    def test_no_reload_on_resize(self):
        base = (ROOT / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        for f in sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("body-*.html")):
            base += f.read_text(encoding="utf-8")
        baseline = base.count("location.reload")
        # UXP10 adds one explicit Reload-shell action; resize still must not reload.
        self.assertEqual(baseline, 3)
        out = page("t" * 32, "n")
        self.assertEqual(out.count("location.reload"), baseline)
        for m in re.finditer(r"resize|orientationchange", out):
            near = out[max(0, m.start() - 200):m.end() + 200]
            self.assertNotIn("reload", near)


if __name__ == "__main__":
    unittest.main()
