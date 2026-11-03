# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the OPOS tinydm post-install hook."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "packaging" / "pmos" / "postmarketos-ui-opos" / "postmarketos-ui-opos.post-install"


def _lines():
    return HOOK.read_text().splitlines()


class TestPostInstall(unittest.TestCase):
    def test_shebang(self):
        self.assertEqual(_lines()[0], "#!/bin/sh")

    def test_sets_cage_session(self):
        matches = [l for l in _lines() if l.strip().startswith("tinydm-set-session -s ")]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].strip(), "tinydm-set-session -s /usr/share/wayland-sessions/cage-ui.desktop")

    def test_exits_zero(self):
        nonempty = [l for l in _lines() if l.strip() != ""]
        self.assertEqual(nonempty[-1].strip(), "exit 0")

    def test_session_selection_status_is_preserved(self):
        for status in (0, 7):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                log = base / "calls"
                stub = base / "tinydm-set-session"
                stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nexit {status}\n')
                stub.chmod(0o755)
                result = subprocess.run(["/bin/sh", str(HOOK)],
                                        env=dict(os.environ, PATH=str(base), CALL_LOG=str(log)),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(log.read_text(), "-s /usr/share/wayland-sessions/cage-ui.desktop\n")

    def test_posix_syntax(self):
        self.assertTrue(HOOK.is_file())
        result = subprocess.run(["sh", "-n", str(HOOK)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)

    def test_no_privileged_or_service_calls(self):
        text = HOOK.read_text()
        for word in ("sudo", "rc-update", "systemctl"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
