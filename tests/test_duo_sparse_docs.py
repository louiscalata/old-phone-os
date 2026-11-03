# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest

_DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "duo-usb-install.md"
_PUSH = "## Push a build with push-to-duo.sh"
_SPARSE = "## Convert and verify a sparse image before flashing"

def _section():
    lines = _DOC.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(_SPARSE)
    except ValueError:
        return ""
    end = len(lines)
    for pos in range(start + 1, len(lines)):
        if lines[pos].startswith("## "):
            end = pos
            break
    return "\n".join(lines[start:end])

class DuoSparseDocsTest(unittest.TestCase):
    def test_sparse_section_content(self):
        section = _section()
        self.assertTrue(section)
        self.assertIn("python3 deploy/sparse_image.py convert <raw-image> <sparse-image>", section)
        self.assertIn("python3 deploy/sparse_image.py check <raw-image> <sparse-image>", section)
        for literal in ("sha256", "fastboot 37.0.1", "Windows", "bad_alloc", "5.7 GB", "ext4", "do not flash"):
            self.assertIn(literal, section)
    def test_sparse_heading_once(self):
        lines = _DOC.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines.count(_SPARSE), 1)
        self.assertGreater(lines.index(_SPARSE), lines.index(_PUSH))

if __name__ == "__main__":
    unittest.main()
