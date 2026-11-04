# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import importlib.util
import unittest

_root = Path(__file__).resolve().parents[1]
_guard_spec = importlib.util.spec_from_file_location(
    "opos_guard_check_pmos",
    str(_root / "guard" / "check.py")
)
_guard_mod = importlib.util.module_from_spec(_guard_spec)
_guard_spec.loader.exec_module(_guard_mod)

class TestPmosConsistency(unittest.TestCase):
    def test_no_private_addresses(self):
        root = Path(__file__).resolve().parents[1]
        (root / "packaging" / "pmos" / "opos" / "APKBUILD").read_text(encoding="utf-8")
        (root / "packaging" / "pmos" / "postmarketos-ui-opos" / "APKBUILD").read_text(encoding="utf-8")

        pmos_dir = root / "packaging" / "pmos"
        for p in pmos_dir.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                rel = str(p.relative_to(root))
                text = p.read_text(encoding="utf-8", errors="replace")
                with self.subTest(path=rel):
                    found = any(
                        pat.search(text)
                        for pat in _guard_mod.PRIVATE
                    )
                    self.assertFalse(found, f"private data in {rel}")

    def test_version_numbers_not_flagged(self):
        parts = ["10", "1", "2"]
        version = ".".join(parts)
        line = f"pkgver={version}"
        found = any(pat.search(line) for pat in _guard_mod.PRIVATE)
        self.assertFalse(found, "version number should not be flagged as private")
