# SPDX-License-Identifier: GPL-3.0-or-later
"""The page server, the AI page API, the real link listener and one shared answer store, end to end.

`web.main` starts the page server with `OPOS_APPS=ai` and `OPOS_GPU_LINK=1`. A test PC enrols through the real
listener, the owner allows it through `/api/ai`, a question is asked on the page, the PC answers through the listener,
and the page shows the answer. Every link action is also tried without the page token and on a locked phone.
"""

import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from opos import ai_answers, ai_page, apps, assist, firstrun, gpu_link_server, lockpin, web
from tests.ai_page_support import request
from tests.gpu_link_fake_pc import FakePC

GPU = {"name": "Test GPU", "memory_mb": 1024, "driver": "1.0"}
ACTIONS = (
    {"action": "allow", "code": "000000"},
    {"action": "deny", "code": "000000"},
    {"action": "forget"},
    {"action": "reopen"},
    {"action": "cancel", "id": "x"},
)
ANSWER = "Shared answer from the test PC."


class WebAiLinkEndToEnd(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(dir=Path(__file__).parent))
        model = Path(tmp) / "practice.bin"
        model.write_bytes(b"fixture, not a model")
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {
                    "OPOS_STATE_DIR": tmp,
                    "OPOS_CONFIG_DIR": tmp,
                    "XDG_STATE_HOME": tmp,
                    "OPOS_APPS": "ai",
                    "OPOS_GPU_LINK": "1",
                    "OPOS_GPU_LINK_PORT": "0",
                    "OPOS_GPU_LINK_MODEL": str(model),
                },
                clear=True,
            )
        )
        ai_answers.reset()
        self.addCleanup(ai_answers.reset)
        self.addCleanup(gpu_link_server.stop)
        self.state = ai_page.PageState()
        self.enterContext(mock.patch.object(ai_page, "_STATE", self.state))
        self.addCleanup(self.state.close)
        # The page registers its app when it is first imported and does not register it again, so it stays registered.
        apps.register(ai_page.APP)
        self.enterContext(mock.patch.object(assist, "local_route", return_value=None))
        self.enterContext(mock.patch.object(firstrun, "install"))
        self.enterContext(mock.patch.object(web, "_route_with_glimmer"))
        self.enterContext(mock.patch.object(web, "_install_helper_visible", return_value=False))

    def run_main(self, check):
        """Start `web.main` and run `check(server)` in place of serving, then stop everything it started."""
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
                    check(server)
                finally:
                    server.shutdown()
                    thread.join(5)

            server.serve_forever = probe
            server.RequestHandlerClass.updater.start_auto_check = mock.Mock()
            return server

        try:
            with (
                mock.patch.object(web, "make_server", side_effect=make),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                web.main(["--port", "0"])
        finally:
            for server in servers:
                server.server_close()

    def test_page_and_listener_share_one_store_and_the_gates_hold(self):
        seen = {}

        def check(server):
            link = gpu_link_server.service()
            self.assertIsNotNone(link)
            host, port = gpu_link_server._Running.listener.server_address
            self.assertEqual(host, "127.0.0.1")
            pc = FakePC(address=(host, port))
            self.assertEqual(pc.hello().status, 200)
            self.assertEqual(pc.enrol().status, 202)

            code = json.loads(request(server, "/api/ai")[1])["gpu"]["link"]["pending"]["code"]
            self.assertEqual(code, pc.code)
            good = {"action": "allow", "code": code}
            # No page token: refused, and the request is still waiting.
            self.assertEqual(request(server, "/api/ai", good, token=False)[0], 403)
            # A locked phone refuses every link action and Stop, with or without the token.
            with mock.patch.object(lockpin, "is_locked", return_value=True):
                for body in (good, *ACTIONS):
                    self.assertEqual(request(server, "/api/ai", body)[0], 403, body)
                locked = json.loads(request(server, "/api/ai")[1])
                self.assertTrue(locked["locked"])
                self.assertNotIn("gpu", locked)
            for body in ACTIONS:
                self.assertEqual(request(server, "/api/ai", body, token=False)[0], 403, body)
            self.assertIsNotNone(link.snapshot()["pending"])
            self.assertIsNone(link.snapshot()["pc"])
            self.assertEqual(request(server, "/api/ai", good)[0], 200)

            # Allow starts a new epoch, so the PC says hello again before its first signed request.
            self.assertEqual(pc.hello().status, 200)
            deadline = time.monotonic() + 10
            while link.snapshot()["model"]["state"] != "ready" and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(pc.status("idle", gpu=GPU).status, 200)

            status, raw = request(server, "/api/ai", {"action": "ask", "question": "Shared?", "panes": ["gpu"]})
            self.assertEqual(status, 200, raw)
            deadline = time.monotonic() + 20
            job = None
            while job is None and time.monotonic() < deadline:
                reply = pc.work(wait=1)
                if reply.status == 200:
                    job = pc.data(reply)
            self.assertIsNotNone(job)
            self.assertEqual(job["question"], "Shared?")
            self.assertEqual(pc.text(job["lease"], 0, ANSWER, done=True, finish="stop").status, 200)
            seen["answer"] = json.loads(request(server, "/api/ai")[1])["gpu"]["answer"]
            self.assertEqual(request(server, "/api/ai", {"action": "forget"})[0], 200)
            self.assertIsNone(link.snapshot()["pc"])

        self.run_main(check)
        self.assertIsNone(gpu_link_server.service())
        self.assertEqual(seen["answer"]["state"], "done")
        self.assertEqual(seen["answer"]["text"], ANSWER)


if __name__ == "__main__":
    unittest.main()
