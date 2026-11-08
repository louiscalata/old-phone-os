# SPDX-License-Identifier: GPL-3.0-or-later
"""The first run and time zone as shipped: the unit's switch, verify-image.sh's checks, the docs."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = os.path.isdir("/usr/share/zoneinfo") and os.path.isfile("/usr/share/zoneinfo/Europe/Berlin")


class Packaging(unittest.TestCase):
    def test_the_server_unit_turns_it_on(self):
        unit = (ROOT / "packaging/duo-os/opos-server.service").read_text(encoding="utf-8")
        self.assertEqual(1, unit.count("Environment=OPOS_FIRSTRUN=1\n"))
        self.assertIn("track firstboot", unit)
        self.assertIn("OPOS_ON_PHONE=1", unit)

    def test_verify_image_checks_it(self):
        text = (ROOT / "packaging/duo-os/verify-image.sh").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("# >>> track firstboot"))
        self.assertEqual(1, text.count("# <<< track firstboot"))
        for needle in ("OPOS_FIRSTRUN=1", "usr/share/zoneinfo", "+0530", "opos.timezone --set", "opos-kiosk"):
            self.assertIn(needle, text)
        r = subprocess.run(["sh", "-n", str(ROOT / "packaging/duo-os/verify-image.sh")], capture_output=True, text=True) if shutil.which("sh") else None
        if r:
            self.assertEqual(0, r.returncode, r.stderr)

    @unittest.skipUnless(shutil.which("sh") and sys.platform != "win32" and REAL, "needs sh and a zone database")
    def test_the_verify_block_passes_against_a_pretend_image(self):
        """Run the track's block from verify-image.sh against a directory laid out like the image, with the host python3 for the image's."""
        text = (ROOT / "packaging/duo-os/verify-image.sh").read_text(encoding="utf-8")
        block = re.search(r"# >>> track firstboot.*?\n(.*?)\n# <<< track firstboot", text, re.S).group(1)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        r = os.path.join(d, "img")
        w = os.path.join(r, "etc/systemd/system")
        os.makedirs(w)
        os.makedirs(os.path.join(r, "usr/libexec/opos"))
        os.makedirs(os.path.join(r, "usr/share"))
        shutil.copy(ROOT / "packaging/duo-os/opos-server.service", w)
        shutil.copy(ROOT / "packaging/duo-os/opos-kiosk", os.path.join(r, "usr/libexec/opos"))
        os.symlink("/usr/share/zoneinfo", os.path.join(r, "usr/share/zoneinfo"))
        script = ("set -u\nfail=0\ncheck() { if eval \"$2\"; then echo \"PASS $1\"; else echo \"FAIL $1\"; fail=1; fi; }\n"
                  f"r={r}\nw={w}\nwork={d}\n"
                  f"py() {{ PYTHONPATH={ROOT / 'src'} PYTHONDONTWRITEBYTECODE=1 {sys.executable} \"$@\"; }}\n" + block + "\nexit $fail\n")
        env = {k: v for k, v in os.environ.items() if k not in ("TZ", "OPOS_CONFIG_DIR")}
        res = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertEqual(5, res.stdout.count("PASS"), res.stdout)
        self.assertNotIn("FAIL", res.stdout)

    @unittest.skipUnless(shutil.which("sh") and sys.platform != "win32", "needs sh")
    def test_the_verify_block_fails_when_the_wiring_is_missing(self):
        text = (ROOT / "packaging/duo-os/verify-image.sh").read_text(encoding="utf-8")
        block = re.search(r"# >>> track firstboot.*?\n(.*?)\n# <<< track firstboot", text, re.S).group(1)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "etc/systemd/system"))
        os.makedirs(os.path.join(d, "usr/libexec/opos"))
        Path(d, "etc/systemd/system/opos-server.service").write_text("[Service]\n")
        Path(d, "usr/libexec/opos/opos-kiosk").write_text("#!/bin/sh\nexec chromium\n")
        script = ("set -u\nfail=0\ncheck() { if eval \"$2\"; then echo \"PASS $1\"; else echo \"FAIL $1\"; fail=1; fi; }\n"
                  f"r={d}\nw={d}/etc/systemd/system\npy() {{ false; }}\n" + block + "\nexit $fail\n")
        res = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(1, res.returncode, res.stdout)
        self.assertEqual(2, res.stdout.count("FAIL"), res.stdout)
        self.assertIn("NOTE: no /usr/share/zoneinfo", res.stdout)


class Docs(unittest.TestCase):
    def test_the_doc_is_linked_and_says_what_it_does_and_what_it_does_not_show(self):
        self.assertIn("(duo-firstrun.md)", (ROOT / "docs/README.md").read_text(encoding="utf-8"))
        doc = (ROOT / "docs/duo-firstrun.md").read_text(encoding="utf-8")
        for needle in ("OPOS_FIRSTRUN=1", "OPOS_KIOSK_RESTART=0", "firstrun.json", "/timezone", "/api/firstrun", "/api/timezone",
                       "clock_plausible", "text/html", "/network", "Not yet run on the Duo", "What only the phone can show"):
            self.assertIn(needle, doc)
        for name in ("firstrun.py", "firstrun_page.py", "timezone.py", "clockcheck.py"):
            self.assertIn(name, doc)
            self.assertTrue((ROOT / "src/opos" / name).is_file(), name)
        self.assertNotRegex(doc, r"(?i)verified on the (phone|duo)|works on the (phone|duo)")

    def test_new_files_carry_the_licence_header_and_unix_line_endings(self):
        for rel in ("src/opos/clockcheck.py", "src/opos/timezone.py", "src/opos/firstrun.py", "src/opos/firstrun_page.py",
                    "tests/test_firstrun.py", "tests/test_firstrun_wiring.py", "tests/test_timezone.py", "docs/duo-firstrun.md"):
            raw = (ROOT / rel).read_bytes()
            self.assertNotIn(b"\r", raw, rel)
            self.assertIn(b"SPDX-License-Identifier: GPL-3.0-or-later", raw[:120], rel)


if __name__ == "__main__":
    unittest.main()
