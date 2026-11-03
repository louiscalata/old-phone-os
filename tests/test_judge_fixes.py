# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for the fresh-checkout judge fixes."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opos.setup import cli, device


class TestJudgeSetupDryRun(unittest.TestCase):
    def test_configured_dry_run_does_not_advertise_a_live_url(self):
        out = io.StringIO()
        with (
            patch.object(cli, "reboot_warning", return_value=None),
            patch("opos.setup.config.load", return_value={"applied": True}),
            patch("opos.setup.server.main") as server,
            patch("opos.setup.apply.apply") as apply,
        ):
            rc = cli.main(["run", "--dry-run"], env={}, out=out)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.getvalue(), "Dry run finished. Nothing was configured or started.\n",
        )
        server.assert_not_called()
        apply.assert_not_called()

    def test_defaults_dry_run_reports_preview_without_writes_or_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            config_dir = home / "config"
            out = io.StringIO()
            with (
                patch.object(cli, "reboot_warning", return_value=None),
                patch.object(device, "detect_device", return_value=device.stub_device()),
                patch("opos.setup.services.default_runner") as runner,
                patch("opos.setup.apply.http_health") as health,
            ):
                rc = cli.main(
                    ["run", "--defaults", "--dry-run", "--config-dir", str(config_dir)],
                    env={}, home=home, platform="linux", out=out,
                )
            self.assertEqual(rc, 0, out.getvalue())
            self.assertEqual(
                out.getvalue().splitlines()[-1],
                "Dry run finished. Nothing was configured or started.",
            )
            self.assertIn("would write", out.getvalue())
            self.assertNotIn("Old Phone OS Interface is set up", out.getvalue())
            self.assertNotIn("Open it at", out.getvalue())
            self.assertNotIn("http://", out.getvalue())
            self.assertEqual(list(home.iterdir()), [])
            runner.assert_not_called()
            health.assert_not_called()


if __name__ == "__main__":
    unittest.main()
