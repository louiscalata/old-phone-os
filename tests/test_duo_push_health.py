# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import unittest
from pathlib import Path


class DuoPushHealthTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "deploy" / "push-to-duo.sh").read_text(encoding="utf-8")
        self.script_text = text
        self.stripped = [line.strip() for line in text.splitlines()]
        self.install_index = self.stripped.index("sh install.sh --no-launch --yes")

    def test_path_note_follows_install(self):
        profile = "~" + "/" + ".pro" + "file"
        quote = chr(34)
        wanted = "note: open a new login shell or run " + quote + ". " + profile + quote + " so opos is on PATH"
        nxt = self.stripped[self.install_index + 2]
        self.assertTrue(nxt.startswith("printf"))
        self.assertIn(wanted, nxt)

    def test_restart_follows_install(self):
        line = self.stripped[self.install_index + 1]
        self.assertTrue(line.startswith("systemctl --user"))
        self.assertIn("try-restart opos-server.service", line)
        self.assertTrue(line.endswith("|| true"))
        # find status line index
        status_idx = None
        for i in range(self.install_index + 2, len(self.stripped)):
            if '/.local/bin/opos"' in self.stripped[i] and 'status' in self.stripped[i]:
                status_idx = i
                break
        self.assertIsNotNone(status_idx)
        self.assertLess(self.install_index + 1, status_idx)

    def test_status_check_follows_note(self):
        dollar = chr(36)
        base = dollar + "HO" + "ME"
        exe = base + "/" + ".lo" + "cal" + "/" + "bin" + "/" + "op" + "os"
        quoted = chr(34) + exe + chr(34) + " st" + "atus"
        nxt = self.stripped[self.install_index + 3]
        self.assertIn(quoted, nxt)
        self.assertTrue(nxt.endswith("|| true"))
        health = "health: if running is no, start it with: opos run --defaults"
        after = self.stripped[self.install_index + 4]
        self.assertTrue(after.startswith("printf"))
        self.assertIn(health, after)

    def test_no_curl_wget_url_or_python_snippet(self):
        a = "c" + "u" + "r" + "l"
        b = "w" + "g" + "e" + "t"
        c = "h" + "t" + "tp:/" + "/"
        d = "h" + "t" + "tps:/" + "/"
        e = "py" + "thon3 " + "-c"
        for bad in (a, b, c, d, e):
            self.assertNotIn(bad, self.script_text)
        cmd = ["sh", "-n", str(Path(__file__).resolve().parents[1] / "deploy" / "push-to-duo.sh")]
        done = subprocess.run(cmd, capture_output=True, check=False)
        self.assertEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()
