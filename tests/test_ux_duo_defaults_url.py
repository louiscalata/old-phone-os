# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Duo defaults URL output."""
from __future__ import annotations
import io
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import opos.setup.cli


class TestDefaultsUrl(unittest.TestCase):
    def test_defaults_prints_url(self):
        buf = io.StringIO()
        with patch('opos.setup.cli.reboot_warning', return_value=None):
            with patch('opos.setup.config.default_config', return_value={}):
                with patch('opos.setup.config.validate', return_value=[]):
                    with patch('opos.setup.device.detect_device', return_value=object()):
                        with patch('opos.setup.device.to_config_device', return_value={"source": "stub", "id": "unknown"}):
                            mock_apply = MagicMock()
                            mock_result = MagicMock()
                            mock_result.snapshot.return_value = []
                            mock_apply.return_value = mock_result
                            with patch('opos.setup.apply.apply', mock_apply):
                                with patch('opos.setup.config.config_dir', return_value=Path.cwd()):
                                    rc = opos.setup.cli.main(
                                        ["run", "--defaults"],
                                        env={},
                                        home=Path.cwd(),
                                        platform="linux",
                                        out=buf,
                                    )
        output = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Old Phone OS is set up with the default settings.", output)
        lines = output.splitlines()
        self.assertTrue(lines)
        self.assertEqual(lines[-1], "Open it at http://127.0.0.1:8765/")
