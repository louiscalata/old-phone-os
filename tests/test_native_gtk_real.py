# SPDX-License-Identifier: GPL-3.0-or-later
"""The real shell under real GTK 4: every page in two panes and in back mode (either screen, either hand) must fit the
1392x900 screen at its minimum size, and the bottom bar must be on screen. It needs a Wayland display of that size, which
tools/gtk-headless.sh starts (a headless Weston). Without that script, without GTK or without a display, the real test
skips: nothing here imports GTK, and the worker that does runs in its own process (tests/gtk_real_worker.py).

How to run it, on a Linux machine with python3-gi, gir1.2-gtk-4.0, weston and dbus-daemon installed:
  dbus-run-session -- sh tools/gtk-headless.sh . python3 -B -m unittest tests.test_native_gtk_real
It is not part of CI: the runner has no GTK. See tools/README.md. Set OPOS_REQUIRE_GTK=1 to turn "no GTK here" into a failure.

class Rules tests the plain parts (what fits, when to skip) and the script's safety on every machine."""

import ast
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from opos import apps
from opos.native import logic
from tests import gtk_real_rules as rules

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "gtk-headless.sh"
GTK_NAMES = {"gi", "Gtk", "Gdk", "GLib", "Gio"}


class RealGtk(unittest.TestCase):
    def test_every_page_fits_the_screen_in_every_layout(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
        why = rules.not_headless(env)
        if why:
            self.skipTest(why)
        r = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests" / "gtk_real_worker.py")],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        why = rules.skip_after_run(r.returncode, r.stderr, env)
        if why:
            self.skipTest(why)
        self.assertEqual(r.returncode, 0, rules.failure_lines(r.stdout + r.stderr))


class Rules(unittest.TestCase):
    def test_a_row_may_be_as_tall_as_the_screen_and_no_taller(self):
        self.assertIsNone(rules.row_problem("x", 0, 900))
        self.assertIn("needs at least 901 px", rules.row_problem("x", 0, 901))

    def test_a_pane_inside_the_screen_has_no_problem(self):
        self.assertEqual(rules.pane_problems("x", "act", (696, 100, 696, 800), True), [])
        self.assertEqual(rules.pane_problems("x", "act", (-0.4, -0.4, 1392.8, 900.8), True), [])  # rounding only

    def test_a_thumb_pane_made_too_tall_or_pushed_off_is_a_problem(self):
        # the failure the harness exists for: a pane taller than the screen puts the bottom bar off it
        self.assertEqual(len(rules.pane_problems("x", "act", (696, 0, 696, 901.6), True)), 1)
        self.assertEqual(len(rules.pane_problems("x", "act", (696, 40, 696, 900), True)), 1)
        self.assertEqual(len(rules.pane_problems("x", "act", (-2, 0, 696, 900), True)), 1)
        self.assertEqual(len(rules.pane_problems("x", "act", (900, 0, 696, 900), True)), 1)
        self.assertEqual(len(rules.pane_problems("x", "act", (900, -50, 696, 1000), True)), 2)

    def test_a_pane_without_bounds_is_a_problem_and_one_not_on_screen_is_not_measured(self):
        self.assertEqual(rules.pane_problems("x", "read", None, True), ["x: read pane has no bounds"])
        self.assertEqual(rules.pane_problems("x", "read", (0, 0, 9999, 9999), False), [])

    def test_the_real_check_skips_unless_it_is_under_the_script(self):
        self.assertIn("tools/gtk-headless.sh", rules.not_headless({}))
        self.assertIn("tools/gtk-headless.sh", rules.not_headless({"OPOS_GTK_HEADLESS": ""}))
        self.assertIsNone(rules.not_headless({"OPOS_GTK_HEADLESS": "1"}))

    def test_no_gtk_skips_and_everything_else_is_a_result(self):
        self.assertEqual(rules.skip_after_run(77, "no GTK 4: x\n", {}), "no GTK 4: x")
        self.assertEqual(rules.skip_after_run(77, "", {}), "no GTK 4 here")
        self.assertIsNone(rules.skip_after_run(77, "no GTK 4: x", {"OPOS_REQUIRE_GTK": "1"}))
        for code in (0, 1, 2, -9):
            self.assertIsNone(rules.skip_after_run(code, "", {}))

    def test_a_failure_shows_the_failed_checks_and_not_the_chatter(self):
        out = "pages: lock, home\nFAIL: one\nsome warning\nchecked 5 things, 1 failed\n"
        self.assertEqual(rules.failure_lines(out), "FAIL: one\nchecked 5 things, 1 failed")
        self.assertEqual(rules.failure_lines("a traceback"), "a traceback")

    def test_every_server_app_is_on_so_every_page_is_measured(self):
        self.assertEqual(set(rules.ALL_APPS.split(",")), set(apps.MODULES))
        self.assertEqual({a.id for a in apps.enabled({"OPOS_APPS": rules.ALL_APPS})}, set(apps.MODULES))
        self.assertIn("convert", rules.ALL_APPS.split(","))  # main's server-backed Converter page

    def test_nothing_imports_gtk_when_the_real_test_is_collected(self):
        for name in ("test_native_gtk_real.py", "gtk_real_rules.py"):
            tree = ast.parse((ROOT / "tests" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                    self.assertFalse({m.split(".")[0] for m in mods} & GTK_NAMES, f"{name} imports GTK at import time")

    def test_the_worker_exits_77_when_gtk_is_missing(self):
        # PyGObject is hidden from the worker (None in sys.modules makes `import gi` fail), so it says so and stops before
        # it reads the shell, whether or not this machine has GTK; with GTK it would need a display, which is not started here
        code = "import runpy, sys; sys.modules['gi'] = None; runpy.run_path(sys.argv[1], run_name='__main__')"
        r = subprocess.run(
            [sys.executable, "-B", "-c", code, str(ROOT / "tests" / "gtk_real_worker.py")],
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT / "tests")),
        )
        self.assertEqual(r.returncode, rules.NO_GTK, r.stdout + r.stderr)
        self.assertIn("no GTK 4", r.stderr)

    def worker_server_url(self, **given):
        """OPOS_SERVER_URL as the worker leaves it, read after it stops at the hidden `gi` (so no GTK is needed)."""
        code = (
            "import os, runpy, sys\n"
            "sys.modules['gi'] = None\n"
            "try:\n"
            "    runpy.run_path(sys.argv[1], run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "print(os.environ.get('OPOS_SERVER_URL', 'UNSET'))\n"
        )
        env = {k: v for k, v in os.environ.items() if k != "OPOS_SERVER_URL"}
        env.update(PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT / "tests"), **given)
        r = subprocess.run(
            [sys.executable, "-B", "-c", code, str(ROOT / "tests" / "gtk_real_worker.py")],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def test_the_worker_asks_no_real_server_when_none_is_named(self):
        self.assertEqual(self.worker_server_url(), rules.NO_SERVER)
        self.assertEqual(self.worker_server_url(OPOS_SERVER_URL=""), rules.NO_SERVER)  # empty means 8765 to the shell

    def test_the_worker_keeps_a_server_the_caller_named(self):
        self.assertEqual(self.worker_server_url(OPOS_SERVER_URL="http://127.0.0.1:9000"), "http://127.0.0.1:9000")

    def test_the_no_server_address_is_one_the_shell_accepts_and_not_its_default(self):
        self.assertEqual(logic.server_url({"OPOS_SERVER_URL": rules.NO_SERVER}), (rules.NO_SERVER, ""))
        self.assertNotEqual(rules.NO_SERVER, logic.SERVER_URL)


class Script(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.code = "\n".join(line for line in cls.text.splitlines() if not line.lstrip().startswith("#"))

    def test_it_is_a_safe_posix_script(self):
        lines = self.text.splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertIn("# SPDX-License-Identifier: GPL-3.0-or-later", lines[:3])
        self.assertIn("\nset -eu\n", self.text)
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR, "tools/gtk-headless.sh is not executable")

    def test_it_does_nothing_but_start_weston_and_clean_up_after_itself(self):
        for word in ("eval", "sudo", "curl", "wget", "apt", "pip", "ssh", "nc", "scp", "git", "chmod 777"):
            self.assertNotRegex(self.code, rf"(?<![\w-]){word}(?![\w-])", word)
        self.assertNotRegex(self.code, r"\brm\s+-[a-zA-Z]*[rR]")  # no recursive delete, of any path
        self.assertNotRegex(self.code, r"\brm\s+-[a-zA-Z]*f")
        self.assertIn("mktemp -d", self.code)
        self.assertIn("rmdir", self.code)
        self.assertIn("trap cleanup EXIT", self.code)
        self.assertIn("*/opos-gtk.*)", self.code)  # the folder is removed only while it still looks like ours
        self.assertEqual(self.code.count("weston --backend=headless"), 1)
        self.assertIn("--width=1392 --height=900", self.code)
        self.assertEqual((rules.W, rules.H), (1392, 900))

    def test_it_exports_what_the_real_test_needs(self):
        for name in (
            "XDG_RUNTIME_DIR",
            "PYTHONPATH",
            "GDK_BACKEND",
            "WAYLAND_DISPLAY",
            "OPOS_GTK_HEADLESS",
            "OPOS_SPAN",
        ):
            self.assertRegex(self.code, rf"(?m)^{name}=")
            self.assertIn(name, self.code.split("export ", 1)[1].split("\n", 1)[0])
        self.assertIn(f"OPOS_SPAN={rules.SPAN}", self.code)

    def test_it_keeps_gtk_settings_in_memory_so_no_dconf_folder_is_left(self):
        self.assertRegex(self.code, r"(?m)^GSETTINGS_BACKEND=memory$")
        exported = self.code.split("export ", 1)[1].split("\n", 1)[0].split()
        self.assertIn("GSETTINGS_BACKEND", exported)

    @unittest.skipUnless(os.path.exists("/bin/sh"), "no /bin/sh")
    def test_it_refuses_a_bad_call_and_a_missing_weston_before_it_makes_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "path"
            empty.mkdir()
            env = {"PATH": str(empty), "TMPDIR": tmp}
            for args, words in (
                ([], "usage"),
                (["x"], "usage"),
                ([tmp, "true"], "not an OPOS checkout"),
                ([str(ROOT), "true"], "weston is not installed"),
            ):
                with self.subTest(args=args):
                    r = subprocess.run(
                        ["/bin/sh", str(SCRIPT)] + args, capture_output=True, text=True, env=env, timeout=30
                    )
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn(words, r.stderr)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["path"])  # no runtime folder was made

    @unittest.skipUnless(os.path.exists("/bin/sh") and shutil.which("shellcheck"), "no ShellCheck here")
    def test_shellcheck_is_clean(self):
        r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
