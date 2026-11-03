# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression test for setup semaphore leak on failure."""
import unittest
from pathlib import Path
from unittest import mock

from opos.setup.server import make_server, SetupContext


class TestSetupFailureCleanup(unittest.TestCase):
    def test_setup_exception_releases_semaphore_and_closes_connection(self):
        repo_root = Path('.')
        home = Path.home()
        ctx = SetupContext(repo_root=repo_root, python='python3', home=home, platform='linux')
        server = make_server(0, ctx)
        HandlerCls = server.RequestHandlerClass
        sem = HandlerCls._slots
        initial_value = sem._value

        mock_conn = mock.Mock()
        mock_conn.close = mock.Mock()
        mock_conn.settimeout = mock.Mock()
        mock_conn.shutdown = mock.Mock()

        handler = HandlerCls.__new__(HandlerCls)
        handler.connection = mock_conn

        with mock.patch('http.server.BaseHTTPRequestHandler.setup'):
            with mock.patch('threading.Timer.start', side_effect=RuntimeError('timer start failure')):
                with self.assertRaises(RuntimeError):
                    handler.setup()

        self.assertEqual(sem._value, initial_value)
        mock_conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
