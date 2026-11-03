# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from tests.web_source import web_text
from tests import web_source

class TestWebSource(unittest.TestCase):
    def test_markers_expanded(self):
        raw = (web_source._OPOS / "web.py").read_text(encoding="utf-8")
        text = web_text()
        self.assertIsNone(re.search(r"@@OPOS:[A-Za-z0-9_.-]+@@", text))
        expected = raw.count("<!doctype html>")
        for name in re.findall(r"@@OPOS:([A-Za-z0-9_.-]+)@@", raw):
            asset_path = web_source._OPOS / "web_static" / name
            expected += asset_path.read_text(encoding="utf-8").count("<!doctype html>")
        self.assertEqual(text.count("<!doctype html>"), expected)

    def test_asset_text_included(self):
        raw = (web_source._OPOS / "web.py").read_text(encoding="utf-8")
        text = web_text()
        for name in re.findall(r"@@OPOS:([A-Za-z0-9_.-]+)@@", raw):
            asset = (web_source._OPOS / "web_static" / name).read_text(encoding="utf-8")
            self.assertIn(asset[:40], text)

    def test_missing_asset_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            (tmp_path / "web.py").write_text('x = "@@OPOS:nope.html@@"', encoding="utf-8")
            with mock.patch.object(web_source, "_OPOS", tmp_path):
                with self.assertRaises(FileNotFoundError):
                    web_text()
