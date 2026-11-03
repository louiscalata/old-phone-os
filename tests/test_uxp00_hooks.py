# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import importlib.util
from pathlib import Path

from opos.web import page

ROOT = Path(__file__).resolve().parents[1]
POST = ROOT / "packaging" / "pmos" / "opos" / "opos-openrc.post-install"
WEB = ROOT / "src" / "opos" / "web.py"
GUARD = ROOT / "guard" / "check.py"
HOOKS = ["phone-a.css", "phone-b.css", "phone-a.html", "phone-b.html", "phone-a.js", "phone-b.js"]
JARYO = ["jaryo.css", "jaryo-layout.css", "jaryo.js", "jaryo-layout.js"]


class UXP00Hooks(unittest.TestCase):
    def test_openrc_enables_server(self):
        text = POST.read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertIn("rc-update add opos-server default", lines)
        self.assertIn("rc-update add opos-firstboot default", lines)
        self.assertEqual(text.rstrip().splitlines()[-1], "exit 0")

    def test_hook_markers_present_in_order(self):
        src = WEB.read_text(encoding="utf-8")
        for name in HOOKS:
            self.assertEqual(src.count("@@OPOS:%s@@" % name), 1, name)
        style_end = src.index("</style>")
        self.assertLess(src.index("@@OPOS:phone-a.css@@"), src.index("@@OPOS:phone-b.css@@"))
        self.assertLess(src.index("@@OPOS:phone-b.css@@"), style_end)
        self.assertGreater(src.index("@@OPOS:phone-a.css@@"), src.index("<style>"))
        body1 = src.index("@@OPOS:body-1.html@@")
        self.assertLess(src.index("<body>"), src.index("@@OPOS:phone-a.html@@"))
        self.assertLess(src.index("@@OPOS:phone-a.html@@"), src.index('<div id="phone">'))
        self.assertLess(src.index('<div id="phone">'), src.index("@@OPOS:phone-b.html@@"))
        self.assertLess(src.index("@@OPOS:phone-b.html@@"), body1)
        end = src.index("</script></body></html>")
        self.assertLess(src.index("@@OPOS:js-7.js@@"), src.index("@@OPOS:phone-a.js@@"))
        self.assertLess(src.index("@@OPOS:phone-a.js@@"), src.index("@@OPOS:phone-b.js@@"))
        self.assertLess(src.index("@@OPOS:phone-b.js@@"), end)

    def test_jaryo_markers_load_after_phone_assets(self):
        src = WEB.read_text(encoding="utf-8")
        for name in JARYO:
            self.assertEqual(src.count("@@OPOS:%s@@" % name), 1, name)
        self.assertLess(src.index("@@OPOS:phone-b.css@@"), src.index("@@OPOS:jaryo.css@@"))
        self.assertLess(src.index("@@OPOS:jaryo.css@@"), src.index("@@OPOS:jaryo-layout.css@@"))
        self.assertLess(src.index("@@OPOS:jaryo-layout.css@@"), src.index("</style>"))
        self.assertLess(src.index("@@OPOS:phone-b.js@@"), src.index("@@OPOS:jaryo.js@@"))
        self.assertLess(src.index("@@OPOS:jaryo.js@@"), src.index("@@OPOS:jaryo-layout.js@@"))

    def test_page_renders_all_hooks(self):
        out = page("t" * 32, "n")
        self.assertIn("oposHooks", out)
        self.assertNotIn("@@OPOS:", out)
        self.assertIn("window.oposHooks=window.oposHooks||[];", out)
        for name in HOOKS:
            self.assertIn("%s: phone-shell hook point" % name, out)

    def test_hook_errors_are_isolated(self):
        src = WEB.read_text(encoding="utf-8")
        self.assertIn("try{(window.oposHooks||[]).forEach(function(f){try{f(s)}catch(e){}})}catch(e){}", src)
        self.assertIn("if(s&&!s.error){try{(window.oposHooks", src)

    def test_guard_allows_uiux_phone(self):
        spec = importlib.util.spec_from_file_location("guard_check", GUARD)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.assertTrue(guard.UIUX_ID.fullmatch("uiux-UXP01"))
        self.assertTrue(guard.UIUX_PHONE_ID.fullmatch("uiux-UXP01"))
        self.assertIsNone(guard.UIUX_PHONE_ID.match("uiux-UXP01x"))
        self.assertTrue(guard.uiux_ok("uiux-UXP01", ["- uiux-UXP01: phone redesign; written by Fable"]))
        self.assertFalse(guard.uiux_ok("uiux-UXP01", ["- uiux-UXP01: phone redesign; written by an unknown tool"]))
        self.assertTrue(guard.jry_ok("uiux-JRY02", ["- uiux-JRY02: motion; written by Fable"]))
        self.assertFalse(guard.jry_ok("uiux-JRY02", ["- uiux-JRY02: motion; written by an unknown tool"]))


class UXP00FailOpenScripts(unittest.TestCase):
    """Each extension file gets its own <script>, so a syntax error in one cannot stop core init."""

    def _scripts(self):
        import re
        return re.findall(r"<script[^>]*>(.*?)</script>", page("t" * 32, "n"), re.S)

    def test_phone_js_in_separate_script_elements(self):
        scripts = self._scripts()
        self.assertEqual(len(scripts), 5, "core + phone-a + phone-b + jaryo + jaryo-layout")
        self.assertIn("jaryo.js", scripts[3])
        self.assertIn("jaryo-layout.js", scripts[4])
        self.assertIn("phone-a.js", scripts[1])
        self.assertIn("phone-b.js", scripts[2])
        self.assertNotIn("phone-a.js", scripts[0])
        self.assertNotIn("phone-b.js", scripts[0])
        self.assertIn("window.oposHooks", scripts[0])

    def test_core_script_still_parses_when_extension_is_broken(self):
        import json
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        core = self._scripts()[0]
        probe = (
            "const vm=require('vm');const core=%s;new vm.Script(core);"
            "let broke=false;try{new vm.Script('const a=;')}catch(e){broke=true}"
            "if(!broke)process.exit(2);console.log('ok')" % json.dumps(core)
        )
        res = subprocess.run([node, "-e", probe], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
