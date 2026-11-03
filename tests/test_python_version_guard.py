# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def old_version_message(version):
    return (
        f"Old Phone OS Interface needs Python 3.11 or newer; this is Python {version}. "
        "Install Python 3.11 or newer and run the command again with it.\n"
    )


class TestPythonVersionGuard(unittest.TestCase):
    def run_python(self, code):
        return subprocess.run(
            [sys.executable, "-B", "-c", code],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def assert_old_python_rejected(self, result, version="3.9"):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("3.11", result.stderr)
        self.assertIn(version, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)
        self.assertEqual(result.stderr, old_version_message(version))

    def assert_import_succeeds(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_guard_parses_with_python_3_7_grammar(self):
        ast.parse(
            (ROOT / "src/opos/__init__.py").read_text(),
            feature_version=(3, 7),
        )

    def test_old_python_import_fails_clearly(self):
        result = self.run_python(
            "import sys; sys.version_info = (3, 9, 6); import opos"
        )
        self.assert_old_python_rejected(result)

    def test_old_python_runpy_fails_with_same_message(self):
        result = self.run_python(
            'import sys; sys.version_info = (3, 9, 6); import runpy; '
            'runpy.run_module("opos.web", run_name="__main__")'
        )
        self.assert_old_python_rejected(result)

    def test_old_python_runpy_never_binds_a_port(self):
        result = self.run_python('''
import runpy
import socket
import sys
from unittest.mock import patch

with patch.object(socket.socket, "bind", side_effect=AssertionError("unexpected bind")) as bind:
    sys.version_info = (3, 9, 6)
    try:
        runpy.run_module("opos.web", run_name="__main__")
    finally:
        bind.assert_not_called()
''')
        self.assert_old_python_rejected(result)

    def test_supported_python_import_succeeds(self):
        self.assert_import_succeeds(self.run_python("import sys; import opos"))

    def test_python_3_10_is_rejected_by_name(self):
        result = self.run_python(
            "import sys; sys.version_info = (3, 10, 14); import opos"
        )
        self.assert_old_python_rejected(result, version="3.10")

    def test_python_3_11_boundary_is_accepted(self):
        # The exact minimum, final and pre-release, must import silently.
        for version in ((3, 11, 0, "final", 0), (3, 11, 0, "alpha", 1)):
            with self.subTest(version=version):
                self.assert_import_succeeds(self.run_python(
                    f"import sys; sys.version_info = {version!r}; import opos"
                ))


if __name__ == "__main__":
    unittest.main()
