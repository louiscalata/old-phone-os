# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import tempfile
import unittest
from tests.pmos_lint import apkbuild_vars, function_body


class TestPmosLint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.apkbuild = pathlib.Path(self.tmp.name) / "APKBUILD"
        sample_lines = [
            "# Maintainer: none",
            'pkgname="opos-sample"',
            "pkgrel=0",
            'depends="',
            "\tpostmarketos-base-ui-cage",
            "\tcage",
            '\t"',
            'sha512sums="SKIP"',
            "package() {",
            "\tinner=1",
            "}",
        ]
        self.apkbuild.write_text("\n".join(sample_lines) + "\n", encoding="utf-8")
        self.vars = apkbuild_vars(self.apkbuild)

    # opos-sample pkgname arrives as a double-quoted one-line value
    def test_quoted_value(self):
        self.assertEqual(self.vars["pkgname"], "opos-sample")

    # opos-sample pkgrel arrives as a bare unquoted numeric value
    def test_unquoted_value(self):
        self.assertEqual(self.vars["pkgrel"], "0")

    # opos-sample checksum marker uses a quoted all-caps word here
    def test_digit_key(self):
        self.assertEqual(self.vars["sha512sums"], "SKIP")

    # opos-sample folded cage depends collapse into one spaced line
    def test_multiline_value(self):
        self.assertEqual(self.vars["depends"], "postmarketos-base-ui-cage cage")

    # opos-sample package body assignment stays out of top-level keys
    def test_indented_ignored(self):
        self.assertNotIn("inner", self.vars)

    # opos-sample package stanza returns opener body and closer intact
    def test_function_body(self):
        self.assertEqual(function_body(self.apkbuild, "package"), "package() {\n\tinner=1\n}")

    # opos-sample missing build stanza signals a ValueError failure
    def test_missing_function(self):
        with self.assertRaises(ValueError):
            function_body(self.apkbuild, "build")

    # opos-sample absent APKBUILD path surfaces FileNotFoundError signal
    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            apkbuild_vars(pathlib.Path(self.tmp.name) / "missing")
