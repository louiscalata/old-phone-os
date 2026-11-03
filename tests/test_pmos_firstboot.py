# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestFirstboot(unittest.TestCase):
    def setUp(self):
        self.script = str(Path(__file__).resolve().parent.parent / "packaging" / "pmos" / "easy" / "opos-firstboot.sh")
        Path(self.script).read_text()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.state = os.path.join(self.tmp, "state")
        self.fake = os.path.join(self.tmp, "fake-tdm")
        self.log = os.path.join(self.tmp, "log")
        with open(self.fake, "w") as f:
            f.write("#!/bin/sh\n")
            f.write('printf \'%s\\n\' "$*" >> "$FAKE_LOG"\n')
            f.write('exit "${FAKE_EXIT:-0}"\n')
        os.chmod(self.fake, 0o755)
        self.env = os.environ.copy()
        for key in ("OPOS_STATE_DIR", "OPOS_TDM", "OPOS_SESSION", "FAKE_EXIT"):
            self.env.pop(key, None)
        self.env["OPOS_STATE_DIR"] = self.state
        self.env["OPOS_TDM"] = self.fake
        self.env["FAKE_LOG"] = self.log

    def run_script(self, **extra):
        env = dict(self.env)
        env.update(extra)
        return subprocess.run(["/bin/sh", self.script], env=env, capture_output=True, text=True)

    def test_selects_once(self):
        first = self.run_script()
        marker_after_first = os.path.exists(os.path.join(self.state, "firstboot-done"))
        second = self.run_script()
        log_text = Path(self.log).read_text()
        self.assertEqual((first.returncode, first.stdout, marker_after_first, second.returncode, second.stdout, log_text), (0, "kiosk: ON\n", True, 0, "first boot: already done\n", "-s /usr/share/wayland-sessions/cage-ui.desktop\n"))

    def test_selection_failure(self):
        p = self.run_script(FAKE_EXIT="1")
        self.assertEqual((p.returncode, os.path.exists(os.path.join(self.state, "firstboot-done"))), (1, False))

    def test_state_dir_uncreatable(self):
        Path(os.path.join(self.tmp, "blocker")).write_text("x")
        p = self.run_script(OPOS_STATE_DIR=str(os.path.join(self.tmp, "blocker", "state")))
        self.assertEqual((p.returncode, p.stdout, os.path.exists(self.log)), (1, "", False))


if __name__ == "__main__":
    unittest.main()
