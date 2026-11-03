# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest
class TestPmosDocs(unittest.TestCase):
    def setUp(self):
        p = pathlib.Path(__file__).parent.parent / "docs" / "pmos.md"
        self.text = p.read_text(encoding="utf-8")
    def test_headings_in_order(self):
        self.assertEqual([l for l in self.text.splitlines() if l.startswith("#")], ["# OPOS on postmarketOS", "## What this is", "## Build", "## Try it in QEMU", "## Limits"])
    def test_required_terms(self):
        self.assertTrue(all(t in self.text for t in ("stage-aports.sh", "pmbootstrap checksum", "pmbootstrap qemu", "qemu-amd64", "postmarketos-ui-opos", "Droidian")))
    def test_no_private_locations(self):
        self.assertTrue(all(t not in self.text for t in ("/" + "Users/", "/" + "home/", "192" + ".168.", "10" + ".0.0.")))
    def test_renderer_hook_and_its_verified_scope(self):
        # The renderer default only reaches cage through tinydm's env-wayland.d hook (cage-ui's opos.sh runs too late).
        self.assertIn("/usr/share/tinydm/env-wayland.d/50-opos-renderer.sh", self.text)
        self.assertIn("sets `WLR_RENDERER=pixman` unless a value is already set", self.text)
        self.assertIn("booted to the OPOS lock screen", self.text)
        self.assertIn("not a physical-phone or accelerated-rendering result", self.text)
if __name__ == "__main__":
    unittest.main()
