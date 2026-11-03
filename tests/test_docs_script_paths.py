# SPDX-License-Identifier: GPL-3.0-or-later
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "docs/pmos.md", "docs/pmos-easy-setup.md"]
PAT = re.compile(r"packaging/pmos/[A-Za-z0-9_./-]+|install\.sh|bootstrap\.sh")


def find_paths():
    found = set()
    for name in DOCS:
        doc = ROOT / name
        if not doc.is_file():
            continue
        found.update(PAT.findall(doc.read_text(encoding="utf-8")))
    return sorted(found)


class DocsScriptPathsTest(unittest.TestCase):
    def test_paths_exist(self):
        missing = [p for p in find_paths() if not (ROOT / p).exists()]
        self.assertEqual([], missing, "missing scripts: " + str(missing))

    def test_minimum_paths_found(self):
        self.assertGreaterEqual(len(find_paths()), 3)

    def test_sh_syntax_ok(self):
        for p in find_paths():
            if p.endswith(".sh") and (ROOT / p).is_file():
                proc = subprocess.run(["sh", "-n", str(ROOT / p)], capture_output=True, text=True)
                self.assertEqual(0, proc.returncode, p + ": " + proc.stderr.strip())


if __name__ == "__main__":
    unittest.main()
