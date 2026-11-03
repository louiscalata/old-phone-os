# SPDX-License-Identifier: GPL-3.0-or-later
import os
import re
import subprocess
import tempfile
import unittest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args, repo, aports):
    env = dict(os.environ)
    env["OPOS_APORTS_DIR"] = aports
    env.pop("OPOS_QEMU_DEVICE", None)
    return subprocess.run(args, cwd=repo, env=env, text=True, capture_output=True)


class DemoRehearsalTest(unittest.TestCase):
    def test_steps_point_at_real_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            demo = _run(["/bin/sh", "packaging/pmos/qemu-demo.sh"], REPO, tmp)
            self.assertEqual(demo.returncode, 0)
            self.assertEqual(demo.stderr, "")
            lines = [l for l in demo.stdout.splitlines() if re.match(r"^\s*[0-9]+\.", l)]
            self.assertEqual(len(lines), 11)
            self.assertIn("packaging/pmos/stage-aports.sh", lines[0])
            self.assertTrue(os.path.isfile(os.path.join(REPO, "packaging/pmos/stage-aports.sh")))
            self.assertEqual(_run(["sh", "-n", "packaging/pmos/stage-aports.sh"], REPO, tmp).returncode, 0)
            self.assertEqual(_run(["sh", "-n", "packaging/pmos/qemu-demo.sh"], REPO, tmp).returncode, 0)
            self.assertEqual(_run(["sh", "-n", "packaging/pmos/qemu-acceptance.sh"], REPO, tmp).returncode, 0)
            for name in ("opos", "postmarketos-ui-opos"):
                self.assertTrue(os.path.isfile(os.path.join(REPO, "packaging/pmos", name, "APKBUILD")))

    def test_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            demo = _run(["/bin/sh", "packaging/pmos/qemu-demo.sh", "--help"], REPO, tmp)
            self.assertEqual(demo.returncode, 0)
            self.assertEqual(demo.stdout.strip(), "usage: qemu-demo.sh [--help]")


if __name__ == "__main__":
    unittest.main()
