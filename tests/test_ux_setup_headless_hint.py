# SPDX-License-Identifier: GPL-3.0-or-later
import io
import contextlib
import tempfile
import unittest
from unittest import mock

from opos.setup import server


class TestHeadlessHint(unittest.TestCase):
    def test_hint_printed(self):
        fake = mock.Mock()
        fake.server_address = ("127.0.0.1", 8766)
        fake.serve_forever.side_effect = KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('opos.setup.server.make_server', return_value=fake):
                with mock.patch('opos.setup.server.webbrowser.open') as mock_open:
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        ret = server.main(['--no-browser', '--config-dir', tmpdir])
                    out = buf.getvalue()

        self.assertEqual(ret, 0)
        self.assertIn('Setup wizard: http://127.0.0.1:8766/', out)
        self.assertIn('No browser on this device? Run: opos run --defaults', out)
        self.assertIn('Or from your computer: ssh -L 8766:127.0.0.1:8766 <user>@<phone>, then open http://127.0.0.1:8766/ there.', out)

        self.assertLess(
            out.index('Setup wizard: http://127.0.0.1:8766/'),
            out.index('No browser on this device? Run: opos run --defaults')
        )

        self.assertFalse(mock_open.called)
        self.assertEqual(fake.server_close.call_count, 1)
