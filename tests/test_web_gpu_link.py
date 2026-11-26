# SPDX-License-Identifier: GPL-3.0-or-later
"""The page-server entry point owns the optional loopback GPU Link listener."""

import io
import os
import socket
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from opos import ai_answers, firstrun, gpu_link_server, web
from tests.ai_page_support import request

ROOT = Path(__file__).resolve().parents[1]


class WebGpuLink(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(dir=ROOT))
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {"OPOS_STATE_DIR": tmp, "OPOS_CONFIG_DIR": tmp, "XDG_STATE_HOME": tmp},
                clear=True,
            )
        )
        ai_answers.reset()
        self.addCleanup(ai_answers.reset)
        self.addCleanup(gpu_link_server.stop)
        self.enterContext(mock.patch.object(firstrun, "install"))
        self.enterContext(mock.patch.object(web, "_route_with_glimmer"))
        self.enterContext(mock.patch.object(web, "_install_helper_visible", return_value=False))

    def run_main(self, check, ending=None):
        make_server = web.make_server
        servers = []

        def make(*args, **kwargs):
            server = make_server(*args, **kwargs)
            servers.append(server)
            serve = server.serve_forever

            def probe():
                thread = threading.Thread(target=serve, kwargs={"poll_interval": 0.01})
                thread.start()
                try:
                    self.assertEqual(request(server, "/", token=False)[0], 200)
                    check()
                finally:
                    server.shutdown()
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                if ending is not None:
                    raise ending

            server.serve_forever = probe
            server.RequestHandlerClass.updater.start_auto_check = mock.Mock()
            return server

        error = io.StringIO()
        try:
            with (
                mock.patch.object(web, "make_server", side_effect=make),
                redirect_stdout(io.StringIO()),
                redirect_stderr(error),
            ):
                web.main(["--port", "0"])
        finally:
            for server in servers:
                server.server_close()
        return error.getvalue()

    def test_unset_switch_starts_no_link(self):
        # Only the exact value 1 turns the listener on; anything else, set or not, starts nothing.
        for value in (None, "", "0", "true", " 1", "yes"):
            with self.subTest(switch=value), mock.patch.dict(os.environ):
                os.environ.pop("OPOS_GPU_LINK", None)
                if value is not None:
                    os.environ["OPOS_GPU_LINK"] = value
                with (
                    mock.patch.object(gpu_link_server, "start") as start,
                    mock.patch.object(gpu_link_server, "stop") as stop,
                ):
                    self.assertEqual(self.run_main(lambda: self.assertIsNone(gpu_link_server.service())), "")
                start.assert_not_called()
                stop.assert_not_called()

    def test_enabled_listener_answers_on_loopback_and_stops_on_every_exit(self):
        for ending in (None, KeyboardInterrupt(), RuntimeError("page server ended")):
            with self.subTest(ending=type(ending).__name__):
                os.environ.update(OPOS_GPU_LINK="1", OPOS_GPU_LINK_PORT="0")
                addresses = []

                def check(addresses=addresses):
                    self.assertIsNotNone(gpu_link_server.service())
                    listener = gpu_link_server._Running.listener
                    addresses.append(listener.server_address)
                    self.assertEqual(addresses[-1][0], "127.0.0.1")
                    self.assertEqual(request(listener, "/v1/hello", token=False)[0], 200)

                if isinstance(ending, RuntimeError):
                    with self.assertRaisesRegex(RuntimeError, "page server ended"):
                        self.run_main(check, ending)
                else:
                    self.assertEqual(self.run_main(check, ending), "")
                self.assertIsNone(gpu_link_server.service())
                with closing(socket.socket()) as client:
                    client.settimeout(1)
                    self.assertNotEqual(client.connect_ex(addresses[0]), 0)

    def test_occupied_port_reports_one_line_and_the_page_still_answers(self):
        with closing(socket.socket()) as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            os.environ.update(OPOS_GPU_LINK="1", OPOS_GPU_LINK_PORT=str(busy.getsockname()[1]))
            error = self.run_main(lambda: self.assertIsNone(gpu_link_server.service()))
        self.assertEqual(error, "opos: GPU Link could not start: OSError\n")

    def test_invalid_setting_reports_no_setting_value_and_keeps_serving(self):
        os.environ.update(OPOS_GPU_LINK="1", OPOS_GPU_LINK_PORT="private-setting-value")
        error = self.run_main(lambda: self.assertIsNone(gpu_link_server.service()))
        self.assertEqual(error, "opos: GPU Link could not start: ValueError\n")

    def test_missing_optional_modules_keep_the_page_serving(self):
        os.environ["OPOS_GPU_LINK"] = "1"
        with mock.patch.object(web, "gpu_link_server", None):
            error = self.run_main(lambda: self.assertIsNone(gpu_link_server.service()))
        self.assertEqual(error, "opos: GPU Link could not start: ImportError\n")

    def test_default_port_is_8767_without_binding_that_port(self):
        os.environ["OPOS_GPU_LINK"] = "1"
        listener_type = gpu_link_server.Listener
        requested = []

        def ephemeral_listener(link, host="127.0.0.1", port=8767):
            requested.append(port)
            return listener_type(link, host, 0)

        with mock.patch.object(gpu_link_server, "Listener", side_effect=ephemeral_listener):
            self.assertEqual(self.run_main(lambda: self.assertIsNotNone(gpu_link_server.service())), "")
        self.assertEqual(requested, [8767])
        self.assertEqual(listener_type.__init__.__defaults__, ("127.0.0.1", 8767))
