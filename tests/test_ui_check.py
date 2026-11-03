# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import opos.web

has_node = bool(shutil.which("node"))
value = os.environ.get("CHROME")
resolved = shutil.which(value) if value else None
chrome_candidates = [
    resolved,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome"),
    shutil.which("chromium"),
]
has_chrome = any(p and os.access(p, os.X_OK) for p in chrome_candidates)


class TestUICheck(unittest.TestCase):
    @unittest.skipUnless(has_node and has_chrome, "node or chrome missing")
    def test_ui_check(self):
        srv = opos.web.make_server(0, opos.web.Phone())
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        port = srv.server_address[1]
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        ui_path = Path(__file__).parent / "ui_check.mjs"
        proc = subprocess.run(
            ["node", str(ui_path), f"http://127.0.0.1:{port}"],
            capture_output=True,
            text=True,
            timeout=150,
        )
        msg = f"returncode={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        self.assertEqual(proc.returncode, 0, msg)
        self.assertNotIn("FAIL", proc.stdout, msg)


class TestUICheckSource(unittest.TestCase):
    def test_avoids_pathname(self):
        src = Path(__file__).with_name("ui_check.mjs").read_text(encoding="utf-8")
        self.assertIn("fileURLToPath(import.meta.url)", src)
        for line in src.splitlines():
            self.assertNotIn("new URL(import.meta.url).pathname", line)

    @unittest.skipUnless(has_node, "node missing")
    def test_f1108_source_read_with_spaces_and_unicode(self):
        src = Path(__file__).with_name("ui_check.mjs").read_text(encoding="utf-8")
        idx = src.lower().find("global timeout")
        self.assertNotEqual(idx, -1, "global timeout diagnostic check not found")
        open_idx = src.find("{", idx)
        self.assertNotEqual(open_idx, -1, "check declaration body not found")
        close_idx = src.find("});", open_idx)
        self.assertNotEqual(close_idx, -1, "check closing not found")
        body = src[open_idx + 1 : close_idx]
        decl = next(
            (line for line in src.splitlines() if "globalTimeout" in line and "=" in line),
            None,
        )
        self.assertIsNotNone(decl, "globalTimeout declaration not found")
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "F1108 space café"
            sub.mkdir()
            harness = sub / "f1108_harness.mjs"
            harness.write_text(
                "import fs from \"fs\";\n"
                + "import { fileURLToPath } from \"url\";\n"
                + "// " + decl + "\n"
                + body
                + "\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["node", str(harness)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            msg = f"returncode={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
            self.assertEqual(proc.returncode, 0, msg)


if __name__ == "__main__":
    unittest.main()
