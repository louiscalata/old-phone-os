# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "packaging" / "pmos" / "postmarketos-ui-opos"
HOOK = UI / "opos-cage.sh"
# tinydm sources this before it starts cage; only here does the renderer reach the compositor.
ENV_HOOK = UI / "opos-tinydm-env.sh"


def _source_hook(extra_env, child_script='printf %s "$CAGE_UI_COMMAND"', hook=HOOK):
    env = os.environ.copy()
    env.pop("CAGE_UI_COMMAND", None)
    env.pop("WLR_RENDERER", None)
    env.update(extra_env)
    return subprocess.run(
        ["sh", "-c", '. "$1" && sh -c "$2"', "sh", str(hook), child_script],
        env=env, capture_output=True, text=True, timeout=10,
    )


class TestCageHook(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HOOK.is_file(), HOOK)

    def test_sets_default_with_override(self):
        lines = [line.strip() for line in HOOK.read_text().splitlines()]
        self.assertIn("CAGE_UI_COMMAND=${CAGE_UI_COMMAND:-/usr/bin/opos-kiosk}", lines)
        self.assertIn("export CAGE_UI_COMMAND", lines)
        self.assertTrue(lines[0].startswith("# SPDX-License-Identifier: GPL-3.0-or-later"))

    def test_sourcing_defaults_to_opos_kiosk(self):
        r = _source_hook({})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "/usr/bin/opos-kiosk")

    def test_existing_value_wins(self):
        r = _source_hook({"CAGE_UI_COMMAND": "/bin/true"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "/bin/true")

    def test_renderer_defaults_to_pixman_and_is_exported(self):
        for extra in ({}, {"WLR_RENDERER": ""}):
            with self.subTest(env=extra):
                result = _source_hook(extra, 'printf %s "$WLR_RENDERER"', ENV_HOOK)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "pixman")

    def test_explicit_renderer_override_is_exported(self):
        result = _source_hook(
            {"WLR_RENDERER": "gles2"}, 'printf %s "$WLR_RENDERER"', ENV_HOOK,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "gles2")

    def test_cage_child_hook_does_not_pretend_to_pick_the_renderer(self):
        self.assertNotIn("WLR_RENDERER", HOOK.read_text())
        self.assertTrue(ENV_HOOK.read_text().splitlines()[1].startswith("# SPDX-License-Identifier: GPL-3.0-or-later"))

    def test_no_exec_sudo_or_etc_paths(self):
        text = HOOK.read_text()
        for s in ["exec ", "sudo", "/etc/"]:
            with self.subTest(s=s):
                self.assertNotIn(s, text)


if __name__ == "__main__":
    unittest.main()
