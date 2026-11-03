# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import subprocess
import tempfile
import unittest


class TestImagePrep(unittest.TestCase):
    def run_image(self, payload):
        script = pathlib.Path(__file__).resolve().parent.parent / "packaging" / "pmos" / "easy" / "image-prep.sh"
        text = script.read_text()
        with tempfile.TemporaryDirectory() as d:
            img = pathlib.Path(d) / "image"
            if payload is not None:
                img.write_bytes(payload)
            p = subprocess.run(["/bin/sh", "-s", "--", "--check", str(img)], input=text, capture_output=True, text=True)
            return (p.returncode, p.stdout, p.stderr)
    def test_sparse_header(self):
        self.assertEqual(self.run_image(bytes.fromhex("3aff26ed")), (0, "format: sparse\n", ""))
    def test_raw_header(self):
        self.assertEqual(self.run_image(b"\x00\x00\x00\x00"), (0, "format: raw\n", ""))
    def test_missing_image(self):
        self.assertEqual(self.run_image(None), (2, "", "no such image\n"))
