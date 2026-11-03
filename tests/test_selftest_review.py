# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression: non-AssertionError details must not leak paths."""
from __future__ import annotations
import os
import unittest
from pathlib import Path
from unittest import mock

import opos.setup.apply  # noqa: F401  # bind the real config functions before the mock.patch calls below
from opos.setup.selftest import run_checks


def _detail(checks: list[tuple[str, bool, str]], name: str) -> tuple[bool, str]:
    for n, passed, detail in checks:
        if n == name:
            return passed, detail
    raise AssertionError("missing " + name)


class SelftestDetailTest(unittest.TestCase):
    def test_os_error_hides_absolute_path(self) -> None:
        home = str(Path.home())
        probe = os.path.join(home, "probe" + "file")
        with mock.patch("opos.setup.config.save", side_effect=OSError(probe)):
            checks = run_checks()
        passed, detail = _detail(checks, "config")
        self.assertFalse(passed)
        self.assertNotIn(home, detail)
        self.assertNotIn(probe, detail)
        self.assertEqual(detail, "OSError")

    def test_assertion_error_keeps_fixed_wording(self) -> None:
        with mock.patch("opos.setup.config.load", return_value={}):
            checks = run_checks()
        passed, detail = _detail(checks, "config")
        self.assertFalse(passed)
        self.assertIn("config " + "round-trip " + "mismatch", detail)

    def test_app_server_cleanup_after_failed_probe(self) -> None:
        host = "".join(chr(c) for c in [49, 50, 55, 46, 48, 46, 48, 46, 49])
        cases = [
            (40, 200, "AssertionError: " + "app " + "token length 40 < 43"),
            (43, 503, "AssertionError: " + "app " + "status 503 != 200"),
        ]
        for length, status, expected in cases:
            with self.subTest(length=length, status=status):
                token = chr(97) * length
                wizard_server = mock.MagicMock()
                wizard_server.server_address = (host, 18080)
                app_server = mock.MagicMock()
                app_server.server_address = (host, 18081)
                body = b'{"device": {}}'
                with mock.patch("opos.setup.config.ensure_token", return_value=token), mock.patch("opos.setup.server.make_server", return_value=wizard_server), mock.patch("opos.web.make_server", return_value=app_server), mock.patch("opos.setup.selftest._serve") as serve, mock.patch("opos.setup.selftest._get", side_effect=[(200, b""), (200, body), (status, b"")]):
                    checks = run_checks()
                found = any(app_server in c[0] or app_server in c[1].values() for c in serve.call_args_list)
                self.assertTrue(found)
                self.assertEqual(_detail(checks, "app"), (False, expected))
                app_server.shutdown.assert_called_once_with()
                app_server.server_close.assert_called_once_with()

    def test_apply_keeps_real_config_functions(self) -> None:
        from opos.setup import apply as apply_mod, config as config_mod
        self.assertEqual((apply_mod.save is config_mod.save, apply_mod.ensure_token is config_mod.ensure_token), (True, True))


if __name__ == "__main__":
    unittest.main()
