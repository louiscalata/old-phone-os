# SPDX-License-Identifier: GPL-3.0-or-later
import socket
import time
import unittest
import threading
import urllib.request
import urllib.error
import json
from unittest import mock
from opos import web
from opos.web import Phone, make_server
from opos.setup import config as setup_config


def _assert_connection_closed(testcase, sock, start, budget):
    while True:
        remaining = budget - (time.monotonic() - start)
        if remaining <= 0:
            raise AssertionError("deadline exceeded before closure")
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(4096)
        except (ConnectionResetError, BrokenPipeError):
            break
        except socket.timeout as exc:
            raise AssertionError("timed out waiting for closure") from exc
        if chunk == b"":
            break
    testcase.assertLess(time.monotonic() - start, budget)

class TestPhone(unittest.TestCase):
    def test_initial_level(self):
        self.assertEqual(Phone().sim.level, 60)

    def test_state_keys(self):
        p = Phone()
        s = p.state()
        for k in ("screen", "brightness", "status", "in_call"):
            self.assertIn(k, s)

    def test_call_and_hangup(self):
        p = Phone()
        p.call("+1 555 0100")
        self.assertTrue(p.state()["in_call"])
        p.hang_up()
        self.assertFalse(p.state()["in_call"])

    def test_send_status(self):
        p = Phone()
        p.send("+15550100", "On my way")
        self.assertIn("sent", p.state()["status"].lower())

    def test_call_invalid_no_raise(self):
        p = Phone()
        p.call("abc")
        self.assertIn("error", p.state()["status"].lower())

    def test_page_content(self):
        from opos.web import page
        html = page()
        self.assertRegex(html, r"(?i)<html")
        self.assertNotIn("<script src", html.lower())

class TestServer(unittest.TestCase):
    def setUp(self):
        self.phone = Phone()
        self.server = make_server(0, self.phone)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_get_state(self):
        url = f"http://127.0.0.1:{self.port}/api/state"
        req = urllib.request.Request(url, headers={"X-OPOS-Token": self.server.RequestHandlerClass.token})
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
        self.assertIn("screen", data)

    def test_post_invalid_action(self):
        url = f"http://127.0.0.1:{self.port}/api"
        payload = json.dumps({"action": "nope"}).encode()
        token = self.server.RequestHandlerClass.token
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "X-OPOS-Token": token})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)

    def test_post_without_token_is_forbidden(self):
        """Reject POST without token."""
        url = f"http://127.0.0.1:{self.port}/api"
        payload = json.dumps({"action": "nope"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)

    def test_oversized_numeric_actions_rejected_without_mutation(self):
        host = ".".join(["127", "0", "0", "1"])
        url = f"http://{host}:{self.port}/api"
        field = "-".join(["X", "OPOS", "Token"])
        token = self.server.RequestHandlerClass.token
        ctype = "-".join(["Content", "Type"])
        err = {"error": " ".join(["bad", "request"])}
        cases = [("battery", "level"), ("battery", "value"), ("tick", "real_s")]
        big_pos = 10 ** 400
        for action, key in cases:
            for big in (big_pos, -big_pos):
                with self.subTest(action=action, key=key, sign=big > 0):
                    snap = (self.phone.sim.level, self.phone.sim.t_s, self.phone.clock_s, self.phone.status, self.phone._last_tick_time)
                    payload = json.dumps({"action": action, key: big}).encode()
                    req = urllib.request.Request(url, data=payload, headers={ctype: "application/json", field: token})
                    with self.assertRaises(urllib.error.HTTPError) as cm:
                        urllib.request.urlopen(req)
                    self.assertEqual(cm.exception.code, 400)
                    self.assertEqual(json.load(cm.exception), err)
                    self.assertEqual((self.phone.sim.level, self.phone.sim.t_s, self.phone.clock_s, self.phone.status, self.phone._last_tick_time), snap)
        payload = json.dumps({"action": "battery", "level": 42}).encode()
        req = urllib.request.Request(url, data=payload, headers={ctype: "application/json", field: token})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(self.phone.sim.level, 42)
        payload = json.dumps({"action": "tick", "real_s": 0}).encode()
        req = urllib.request.Request(url, data=payload, headers={ctype: "application/json", field: token})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.load(resp)
        self.assertIn("screen", data)

    def test_slow_response_survives_deadline(self):
        """Deadline covers read, not response."""
        import time
        from opos import web as webmod
        old = webmod.Handler.DEADLINE
        webmod.Handler.DEADLINE = 0.2
        orig = self.phone.state
        def slow():
            time.sleep(1.0)
            return orig()
        self.phone.state = slow
        try:
            srv = make_server(0, self.phone)
            port = srv.server_address[1]
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            try:
                host = ".".join(["127", "0", "0", "1"])
                url = f"http://{host}:{port}/api/state"
                req = urllib.request.Request(url, headers={"X-OPOS-Token": srv.RequestHandlerClass.token})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.load(resp)
                self.assertIn("screen", data)
            finally:
                srv.shutdown()
                srv.server_close()
        finally:
            self.phone.state = orig
            webmod.Handler.DEADLINE = old

    def test_deadline_cuts_stalled_headers(self):
        """Incomplete headers are cut off by the deadline."""
        import socket
        import time
        from opos import web as webmod
        old = webmod.Handler.DEADLINE
        webmod.Handler.DEADLINE = 0.2
        srv = make_server(0, self.phone)
        port = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            host = ".".join(["127", "0", "0", "1"])
            s = socket.create_connection((host, port), timeout=5)
            try:
                s.settimeout(5)
                s.sendall(b"GET /api/state HTTP/1.1\r\nHost: x\r\n")
                start = time.monotonic()
                _assert_connection_closed(self, s, start, 0.2 * 10)
            finally:
                s.close()
        finally:
            srv.shutdown()
            srv.server_close()
            webmod.Handler.DEADLINE = old

    def test_deadline_cuts_stalled_body(self):
        """POST body that never finishes is cut off by the deadline."""
        import socket
        import time
        from opos import web as webmod
        old = webmod.Handler.DEADLINE
        webmod.Handler.DEADLINE = 0.2
        srv = make_server(0, self.phone)
        port = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            host = ".".join(["127", "0", "0", "1"])
            field = "-".join(["X", "OPOS", "Token"])
            token = srv.RequestHandlerClass.token
            s = socket.create_connection((host, port), timeout=5)
            try:
                s.settimeout(5)
                head = (
                    "POST /api HTTP/1.1\r\nHost: x\r\n"
                    + field + ": " + token + "\r\n"
                    + "Content-Type: application/json\r\n"
                    + "Content-Length: 100\r\n\r\n{}"
                ).encode()
                s.sendall(head)
                start = time.monotonic()
                _assert_connection_closed(self, s, start, 0.2 * 10)
            finally:
                s.close()
        finally:
            srv.shutdown()
            srv.server_close()
            webmod.Handler.DEADLINE = old

    def test_deadline_cuts_trickling_headers(self):
        import socket
        import time
        from opos import web as webmod
        old_deadline = webmod.Handler.DEADLINE
        old_timeout = webmod.Handler.timeout
        webmod.Handler.DEADLINE = 0.6
        webmod.Handler.timeout = 0.2
        srv = make_server(0, self.phone)
        port = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            host = ".".join(["127", "0", "0", "1"])
            s = socket.create_connection((host, port), timeout=5)
            try:
                s.settimeout(0.05)
                hip = ".".join(["127", "0", "0", "1"])
                host_val = hip + ":" + str(port)
                hdr = "-".join(["X", "Slow"])
                start = time.monotonic()
                s.sendall(("GET / HTTP/1.1\r\nHost: " + host_val + "\r\n").encode() + hdr.encode() + b": ")
                closed = None
                while True:
                    elapsed = time.monotonic() - start
                    if elapsed > 1.5:
                        break
                    try:
                        s.sendall(b" ")
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        closed = time.monotonic() - start
                        break
                    try:
                        data = s.recv(4096)
                        if data == b"":
                            closed = time.monotonic() - start
                            break
                    except socket.timeout:
                        pass
                    except (ConnectionResetError, BrokenPipeError, OSError):
                        closed = time.monotonic() - start
                        break
                self.assertIsNotNone(closed)
                self.assertGreaterEqual(closed, 0.4)
                self.assertLessEqual(closed, 1.5)
            finally:
                s.close()
        finally:
            srv.shutdown()
            srv.server_close()
            webmod.Handler.DEADLINE = old_deadline
            webmod.Handler.timeout = old_timeout

    def test_deadline_cuts_trickling_body(self):
        import socket
        import time
        from opos import web as webmod
        old_deadline = webmod.Handler.DEADLINE
        old_timeout = webmod.Handler.timeout
        webmod.Handler.DEADLINE = 0.6
        webmod.Handler.timeout = 0.2
        srv = make_server(0, self.phone)
        port = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            host = ".".join(["127", "0", "0", "1"])
            field = "-".join(["X", "OPOS", "Token"])
            token = srv.RequestHandlerClass.token
            s = socket.create_connection((host, port), timeout=5)
            try:
                s.settimeout(0.05)
                hip = ".".join(["127", "0", "0", "1"])
                host_val = hip + ":" + str(port)
                start = time.monotonic()
                head = ("POST /api HTTP/1.1\r\nHost: " + host_val + "\r\n" + field + ": " + token + "\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{").encode()
                s.sendall(head)
                closed = None
                while True:
                    elapsed = time.monotonic() - start
                    if elapsed > 1.5:
                        break
                    try:
                        s.sendall(b" ")
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        closed = time.monotonic() - start
                        break
                    try:
                        data = s.recv(4096)
                        if data == b"":
                            closed = time.monotonic() - start
                            break
                    except socket.timeout:
                        pass
                    except (ConnectionResetError, BrokenPipeError, OSError):
                        closed = time.monotonic() - start
                        break
                self.assertIsNotNone(closed)
                self.assertGreaterEqual(closed, 0.4)
                self.assertLessEqual(closed, 1.5)
            finally:
                s.close()
        finally:
            srv.shutdown()
            srv.server_close()
            webmod.Handler.DEADLINE = old_deadline
            webmod.Handler.timeout = old_timeout

class TestMain(unittest.TestCase):
    def test_saved_goal_reaches_runtime(self):
        cases = [
            ("enabled", "18:30", True, 6.5, "18:30"),
            ("disabled", "18:30", False, None, None),
            ("missing", None, None, 4.0, "16:00"),  # no setup run: the quickstart demo's 16:00 goal
            ("corrupt", None, None, 11.0, "23:00"),
        ]
        for name, cfg_time, enabled, exp_hours, exp_goal in cases:
            with self.subTest(name=name):
                if name in ("enabled", "disabled"):
                    cfg = setup_config.default_config()
                    cfg["goal"]["time"] = cfg_time
                    cfg["goal"]["enabled"] = enabled
                    load_side = cfg
                elif name == "missing":
                    load_side = None
                else:
                    load_side = setup_config.ConfigError("bad")
                captured = {}
                def fake_make_server(port, phone, host="127.0.0.1", token=None):
                    captured["phone"] = phone
                    srv = mock.Mock()
                    srv.serve_forever = mock.Mock()
                    return srv
                with mock.patch.object(setup_config, "load") as m_load:
                    with mock.patch.object(web, "_route_with_glimmer") as m_route:
                        with mock.patch.object(web, "make_server", side_effect=fake_make_server):
                            if isinstance(load_side, Exception):
                                m_load.side_effect = load_side
                            else:
                                m_load.side_effect = None
                                m_load.return_value = load_side
                            web.main([])
                phone = captured["phone"]
                self.assertEqual(phone.clock_s, 43200)
                seen = {}
                orig_step = phone.runtime.step
                def spy_step(*a, **k):
                    seen.update(k)
                    return orig_step(*a, **k)
                with mock.patch.object(phone.runtime, "step", side_effect=spy_step):
                    phone._tick()
                self.assertEqual(seen.get("goal_hours"), exp_hours)
                self.assertEqual(phone.dash.goal, exp_goal)


class TestDeadlineClosure(unittest.TestCase):
    def test_expired_headers_do_not_dispatch(self):
        import io
        hip = ".".join(["127", "0", "0", "1"])
        raw = ("GET / HTTP/1.1\r\n" + "Host: " + hip + ":" + str(12345) + "\r\n").encode()
        h = web.Handler.__new__(web.Handler)
        h.connection = mock.Mock()
        h._deadline_lock = threading.Lock()
        h._expired = False
        h._deadline_at = time.monotonic() + web.Handler.DEADLINE
        h._timer = mock.Mock()
        h.wfile = io.BytesIO()
        h.close_connection = False
        h.client_address = (hip, 12345)
        h.do_GET = mock.Mock()
        stream = io.BytesIO(raw)
        orig_readline = stream.readline
        fired = {}
        def readline(n=-1):
            line = orig_readline(n)
            if line == b"" and not fired.get("done"):
                fired["done"] = True
                h._expire()
            return line
        h.rfile = mock.Mock()
        h.rfile.readline.side_effect = readline
        h.handle_one_request()
        self.assertTrue(h.connection.shutdown.called)
        h.do_GET.assert_not_called()
        self.assertTrue(h.close_connection)
        self.assertEqual(h.wfile.getvalue(), b"")
    def test_expiry_between_parse_and_dispatch_blocks_execution(self):
        import io
        hip = ".".join(["127", "0", "0", "1"])
        port = 12345
        host = hip + ":" + str(port)
        def make_handler(method, target):
            if method == "GET":
                raw = ("GET " + target + " HTTP/1.1\r\nHost: " + host + "\r\n\r\n").encode()
            else:
                raw = ("POST " + target + " HTTP/1.1\r\nHost: " + host + "\r\nContent-Length: 0\r\n\r\n").encode()
            h = web.Handler.__new__(web.Handler)
            h.connection = mock.Mock()
            h._deadline_lock = threading.Lock()
            h._expired = False
            h._deadline_at = 200.0
            h._timer = mock.Mock()
            h.rfile = io.BytesIO(raw)
            h.wfile = io.BytesIO()
            h.close_connection = False
            h.client_address = (hip, port)
            return h
        orig_parse = web.Handler.parse_request
        cases = [("GET", "/", "_get", "expire"), ("GET", "/", "_get", "deadline"), ("POST", "/api", "_post", "expire"), ("POST", "/api", "_post", "deadline")]
        for method, target, attr, mode in cases:
            with self.subTest(method=method, mode=mode):
                h = make_handler(method, target)
                app = mock.Mock()
                setattr(h, attr, app)
                clock = {"now": 100.0}
                with mock.patch.object(web.time, "monotonic", side_effect=lambda: clock["now"]):
                    def hooked():
                        ok = orig_parse(h)
                        if ok:
                            if mode == "expire":
                                h._expire()
                            else:
                                clock["now"] = 200.0
                        return ok
                    h.parse_request = hooked
                    h.handle_one_request()
                app.assert_not_called()
                self.assertTrue(h.close_connection)
                self.assertEqual(h.wfile.getvalue(), b"")
                if mode == "expire":
                    self.assertTrue(h._expired)
                else:
                    self.assertFalse(h._expired)
        for method, target, attr in [("GET", "/", "_get"), ("POST", "/api", "_post")]:
            with self.subTest(method=method, mode="valid"):
                h = make_handler(method, target)
                app = mock.Mock()
                setattr(h, attr, app)
                with mock.patch.object(web.time, "monotonic", return_value=100.0):
                    h.handle_one_request()
                self.assertEqual(app.call_count, 1)
                self.assertFalse(h._expired)
    def test_response_bytes_do_not_prove_closure(self):
        first = ("re" + "sp").encode()
        closings = [b"", ConnectionResetError(), BrokenPipeError(), socket.timeout()]
        for index, closing in enumerate(closings):
            with self.subTest(index=index):
                sock = mock.Mock()
                sock.recv.side_effect = [first, closing]
                with mock.patch("tests.test_web.time.monotonic", return_value=100.1):
                    if index < 3:
                        _assert_connection_closed(self, sock, 100.0, 2.0)
                    else:
                        with self.assertRaises(AssertionError):
                            _assert_connection_closed(self, sock, 100.0, 2.0)
                self.assertEqual(sock.recv.call_count, 2)
                for call in sock.recv.call_args_list:
                    self.assertEqual(call.args, (4096,))


if __name__ == "__main__":
    unittest.main()
