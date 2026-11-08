# SPDX-License-Identifier: GPL-3.0-or-later
import inspect
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import opos.web


# These belong to the separate /monitor document (assembled by web._monitor_serve, only where OPOS_MONITOR=1), not to the
# main page, so web.page() never inlines them. tests/test_monitor_wiring.py and the drop-in's tests/test_monitor_page.py cover them.
SEPARATE_DOCUMENT = {"monitor.html", "monitor.css", "monitor.js", "monitor-theme.js"}


class TestWebStaticLoader(unittest.TestCase):
    def test_reads_asset(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.css"
            p.write_text("hi", encoding="utf-8")
            with mock.patch.object(opos.web, "_STATIC", Path(d)):
                self.assertEqual(opos.web._asset("x.css"), "hi")

    def test_missing_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(opos.web, "_STATIC", Path(d)):
                with self.assertRaises(FileNotFoundError):
                    opos.web._asset("nope.css")

    def test_inline_marker(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x.css").write_text("hi", encoding="utf-8")
            with mock.patch.object(opos.web, "_STATIC", Path(d)):
                self.assertEqual(opos.web._inline_assets("a @@OPOS:x.css@@ b"), "a hi b")

    def test_page_has_no_marker(self):
        self.assertNotIn("@@OPOS:", opos.web.page())

    def test_every_asset_inlined(self):
        body = opos.web.page()
        base = opos.web._STATIC
        if not base.is_dir():
            return
        for p in sorted(base.iterdir()):
            if p.is_file() and p.name not in SEPARATE_DOCUMENT:
                self.assertIn(p.read_text(encoding="utf-8"), body)

    def test_markers_resolve(self):
        src = inspect.getsource(opos.web)
        names = set()
        for i, line in enumerate(src.splitlines()):
            if "re.sub" in line:
                continue
            if "_inline_assets" in line and "def " in line:
                continue
            for m in re.finditer(r"@@OPOS:([A-Za-z0-9_.-]+)@@", line):
                names.add(m.group(1))
        base = opos.web._STATIC
        for name in names:
            self.assertTrue((base / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
