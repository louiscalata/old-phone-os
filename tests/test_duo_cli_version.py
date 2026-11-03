# SPDX-License-Identifier: GPL-3.0-or-later
"""Version flag regression coverage for setup CLI."""
from __future__ import annotations
import contextlib
import io
import unittest
import unittest.mock
from opos.setup import cli


class DuoCliVersionTest(unittest.TestCase):
    def test_version_flag_prints_version(self):
        with unittest.mock.patch.object(cli, "_handle_run") as run_mock:
            with unittest.mock.patch.object(cli, "_handle_status") as status_mock:
                with unittest.mock.patch.object(cli, "_handle_uninstall") as uninstall_mock:
                    with self.subTest("parser"):
                        out = io.StringIO()
                        err = io.StringIO()
                        with self.assertRaises(SystemExit) as ctx:
                            with contextlib.redirect_stdout(out):
                                with contextlib.redirect_stderr(err):
                                    cli._build_parser().parse_args(["--version"])
                        self.assertEqual(ctx.exception.code, 0)
                        self.assertEqual(out.getvalue(), "opos 0.1.0\n")
                        self.assertEqual(err.getvalue(), "")
                        self.assertFalse(run_mock.called)
                        self.assertFalse(status_mock.called)
                        self.assertFalse(uninstall_mock.called)
                    with self.subTest("main"):
                        out = io.StringIO()
                        err = io.StringIO()
                        with self.assertRaises(SystemExit) as ctx:
                            with contextlib.redirect_stdout(out):
                                with contextlib.redirect_stderr(err):
                                    cli.main(["--version"], env={})
                        self.assertEqual(ctx.exception.code, 0)
                        self.assertEqual(out.getvalue(), "opos 0.1.0\n")
                        self.assertEqual(err.getvalue(), "")
                        self.assertFalse(run_mock.called)
                        self.assertFalse(status_mock.called)
                        self.assertFalse(uninstall_mock.called)


if __name__ == "__main__":
    unittest.main()
