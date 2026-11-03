# SPDX-License-Identifier: GPL-3.0-or-later
import json
import socket
import sys
import time
import threading
import tempfile
import unittest
import urllib.request
import urllib.error
from pathlib import Path

import opos.secure
import opos.setup.config as cfgmod
from opos.setup.server import SetupContext, make_server


def _write_catalog(path: Path) -> None:
    data = {
        "version": 1,
        "tools": [
            {
                "id": "fake-tool",
                "name": "Fake Tool",
                "binary": "fake-tool",
                "installer_url": "https://example.com/install.sh",
                "interpreter": "sh",
                "installer_args": [],
                "installer_env": {},
                "archs": ["x86_64", "aarch64"],
                "min_free_mb": 5,
                "state_subdir": ".fake-tool",
                "run_env": {},
                "lite_env": {},
                "signin": "Run fake-tool signin",
                "docs_url": "https://example.com/docs",
            }
        ],
    }
    path.write_text(json.dumps(data))


class FakeRunner:
    def __init__(self):
        self.commands = []

    def run(self, *args, **kwargs):
        self.commands.append((args, kwargs))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    __call__ = run


class SetupServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.repo_root = self.tmp_path / "repo"
        self.repo_root.mkdir()
        self.home = self.tmp_path / "home"
        self.home.mkdir()
        self.cfg_dir = self.tmp_path / "cfg"
        self.cfg_dir.mkdir()
        self.tools_prefix = self.tmp_path / "tools"
        self.tools_prefix.mkdir()
        self.catalog_path = self.tmp_path / "ai-tools.json"
        _write_catalog(self.catalog_path)
        self.runner = FakeRunner()
        self.ctx = SetupContext(
            repo_root=self.repo_root,
            python=sys.executable,
            home=self.home,
            platform=sys.platform,
            env={"OPOS_CONFIG_DIR": str(self.cfg_dir), "OPOS_TOOLS_PREFIX": str(self.tools_prefix), "OPOS_AI_TOOLS_TABLE": str(self.catalog_path)},
            dry_run=True,
            runner=self.runner,
            health_check=lambda: True,
            app_port=8765,
            root=str(self.tmp_path),
        )
        self.server = make_server(0, self.ctx, host="127.0.0.1")
        self.port = self.server.server_address[1]
        self.token = self.server.token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.tmp.cleanup()

    def _raw(self, method, path, headers=None, data=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        hdrs = {}
        if headers:
            hdrs.update(headers)
        if "Host" not in hdrs:
            hdrs["Host"] = f"127.0.0.1:{self.port}"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _raw_for(self, port, method, path, headers=None, data=None):
        url = f"http://127.0.0.1:{port}{path}"
        hdrs = {}
        if headers:
            hdrs.update(headers)
        if "Host" not in hdrs:
            hdrs["Host"] = f"127.0.0.1:{port}"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _get(self, path, token=True, host=None, origin=None, extra=None):
        h = {}
        if token is True:
            h["X-OPOS-Token"] = self.token
        elif isinstance(token, str):
            h["X-OPOS-Token"] = token
        if host is not None:
            h["Host"] = host
        if origin is not None:
            h["Origin"] = origin
        if extra:
            h.update(extra)
        return self._raw("GET", path, headers=h)

    def _post(self, path, payload, token=True, host=None, origin=None, ctype="application/json", extra=None):
        body = json.dumps(payload).encode() if payload is not None else b"{}"
        h = {}
        if token is True:
            h["X-OPOS-Token"] = self.token
        elif isinstance(token, str):
            h["X-OPOS-Token"] = token
        if host is not None:
            h["Host"] = host
        if origin is not None:
            h["Origin"] = origin
        else:
            h["Origin"] = f"http://127.0.0.1:{self.port}"
        if ctype is not None:
            h["Content-Type"] = ctype
        h["Content-Length"] = str(len(body))
        if extra:
            h.update(extra)
        return self._raw("POST", path, headers=h, data=body)

    def _get_for(self, port, token, path, origin=None):
        h = {"Host": f"127.0.0.1:{port}", "X-OPOS-Token": token}
        if origin is not None:
            h["Origin"] = origin
        return self._raw_for(port, "GET", path, headers=h)

    def _post_for(self, port, token, path, payload, ctype="application/json", origin=None):
        body = json.dumps(payload).encode() if payload is not None else b"{}"
        h = {"Host": f"127.0.0.1:{port}", "X-OPOS-Token": token, "Content-Type": ctype, "Content-Length": str(len(body))}
        if origin is None:
            h["Origin"] = f"http://127.0.0.1:{port}"
        else:
            h["Origin"] = origin
        if ctype is None:
            h.pop("Content-Type", None)
        return self._raw_for(port, "POST", path, headers=h, data=body)

    def test_root_returns_html_without_token(self):
        status, hdrs, body = self._raw("GET", "/", headers={"Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        self.assertIn("text/html", hdrs.get("Content-Type", ""))
        self.assertIn(self.token.encode(), body)

    def test_security_headers_present(self):
        _, hdrs, _ = self._raw("GET", "/", headers={"Host": f"127.0.0.1:{self.port}"})
        exp = opos.secure.security_headers()
        for k in exp:
            self.assertIn(k, hdrs)
        status, hdrs2, _ = self._get("/setup/api/state")
        for k in exp:
            self.assertIn(k, hdrs2)

    def test_host_check_forbidden(self):
        status, _, body = self._get("/", host="evil.example")
        self.assertEqual(status, 403)
        self.assertIn(b"forbidden", body)
        status, _, body = self._get("/setup/api/state", host="evil.example")
        self.assertEqual(status, 403)

    def test_token_required_for_api(self):
        status, _, body = self._get("/setup/api/state", token=None)
        self.assertEqual(status, 403)
        bogus = "bad-" + "token-" + "value"
        status, _, body = self._get("/setup/api/state", token=bogus)
        self.assertEqual(status, 403)
        status, _, _ = self._post("/setup/api/validate", {"config": cfgmod.default_config()}, token=None)
        self.assertEqual(status, 403)

    def test_origin_required_for_post(self):
        status, _, _ = self._post("/setup/api/validate", {"config": cfgmod.default_config()}, origin="http://evil.example")
        self.assertEqual(status, 403)
        hdrs = {
            "Host": f"127.0.0.1:{self.port}",
            "X-OPOS-Token": self.token,
            "Content-Type": "application/json",
            "Content-Length": "2",
        }
        status, _, _ = self._raw("POST", "/setup/api/validate", headers=hdrs, data=b"{}")
        self.assertEqual(status, 200)

    def test_content_type_must_be_json(self):
        status, _, body = self._post("/setup/api/validate", {"config": cfgmod.default_config()}, ctype="text/plain")
        self.assertEqual(status, 415)
        self.assertIn(b"error", body.lower())
        status, _, _ = self._post("/setup/api/validate", {"config": cfgmod.default_config()}, ctype=None)
        self.assertEqual(status, 415)

    def test_state_shape(self):
        status, _, body = self._get("/setup/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["first_run"])
        self.assertIn("config", data)
        self.assertIn("device", data)
        self.assertIn("profiles", data)
        self.assertIn("services", data)
        self.assertIn("battery_modes", data)
        self.assertEqual(data["dry_run"], True)
        self.assertEqual(data["platform"], sys.platform)
        self.assertEqual(data["app_port"], 8765)
        self.assertEqual(self.ctx.env["OPOS_CONFIG_DIR"], str(self.cfg_dir))

    def test_device_known_and_unknown(self):
        status, _, body = self._get("/setup/api/state")
        profiles = json.loads(body)["profiles"]
        if profiles:
            pid = profiles[0].get("id") or profiles[0].get("profile_id") or list(profiles[0].values())[0]
            status, _, body = self._post("/setup/api/device", {"profile_id": pid})
            self.assertEqual(status, 200)
            self.assertIsInstance(json.loads(body), dict)
        status, _, body = self._post("/setup/api/device", {"profile_id": "__no_such_profile__"})
        self.assertEqual(status, 400)
        self.assertIn("unknown profile", json.loads(body)["error"])

    def test_validate_ok_and_errors(self):
        good = cfgmod.default_config()
        status, _, body = self._post("/setup/api/validate", {"config": good})
        self.assertEqual(status, 200)
        j = json.loads(body)
        self.assertTrue(j["ok"])
        self.assertEqual(j["errors"], [])
        bad = {"services": ["__bad_service_name__"]}
        status, _, body = self._post("/setup/api/validate", {"config": bad})
        self.assertEqual(status, 200)
        j = json.loads(body)
        self.assertFalse(j["ok"])
        self.assertTrue(len(j["errors"]) > 0)

    def test_apply_start_and_progress_to_done(self):
        cfg = cfgmod.default_config()
        status, _, body = self._post("/setup/api/apply", {"config": cfg})
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)["started"])
        deadline = time.time() + 3
        done = False
        while time.time() < deadline:
            status, _, body = self._get("/setup/api/progress")
            self.assertEqual(status, 200)
            p = json.loads(body)
            self.assertIn("running", p)
            self.assertIn("done", p)
            self.assertIn("ok", p)
            self.assertIn("steps", p)
            if p["done"]:
                done = True
                break
            time.sleep(0.05)
        self.assertTrue(done)

    def test_apply_invalid_returns_400(self):
        bad = {"services": ["__bad__"]}
        status, _, body = self._post("/setup/api/apply", {"config": bad})
        self.assertEqual(status, 400)
        j = json.loads(body)
        self.assertFalse(j["ok"])
        self.assertTrue(j["errors"])

    def test_409_while_running(self):
        import opos.setup.server as srv
        target_mod = None
        orig = None
        is_func = False
        if hasattr(srv, "apply"):
            a = getattr(srv, "apply")
            if callable(a):
                orig = a
                is_func = True
            elif hasattr(a, "apply"):
                target_mod = a
                orig = a.apply
        if orig is None:
            import opos.setup.apply as am
            target_mod = am
            orig = am.apply

        def slow(cfg, *a, **kw):
            time.sleep(0.8)
            return orig(cfg, *a, **kw)

        if is_func:
            srv.apply = slow
        else:
            target_mod.apply = slow
        try:
            cfg = cfgmod.default_config()
            status, _, _ = self._post("/setup/api/apply", {"config": cfg})
            self.assertEqual(status, 202)
            status, _, body = self._post("/setup/api/apply", {"config": cfg})
            self.assertEqual(status, 409)
            self.assertIn("already running", json.loads(body)["error"])
            time.sleep(1.0)
            status, _, body = self._get("/setup/api/progress")
            self.assertTrue(json.loads(body)["done"])
        finally:
            if is_func:
                srv.apply = orig
            else:
                target_mod.apply = orig

    def test_405_other_methods(self):
        status, _, body = self._raw("PUT", "/setup/api/state", headers={"Host": f"127.0.0.1:{self.port}", "X-OPOS-Token": self.token})
        self.assertEqual(status, 405)
        status, _, body = self._raw("DELETE", "/setup/api/state", headers={"Host": f"127.0.0.1:{self.port}", "X-OPOS-Token": self.token})
        self.assertEqual(status, 405)
        status, _, _ = self._raw("PUT", "/setup/api/validate", headers={"Host": f"127.0.0.1:{self.port}", "X-OPOS-Token": self.token, "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"})
        self.assertEqual(status, 405)

    def test_404_unknown_path(self):
        status, _, body = self._get("/unknown/path")
        self.assertEqual(status, 404)
        self.assertIn("not found", json.loads(body)["error"])
        status, _, body = self._raw("GET", "/setup/api/doesnotexist", headers={"Host": f"127.0.0.1:{self.port}", "X-OPOS-Token": self.token})
        self.assertEqual(status, 404)

    def test_oversized_body(self):
        big = "x" * 6000
        payload = json.dumps({"config": {"note": big}}).encode()
        hdrs = {
            "Host": f"127.0.0.1:{self.port}",
            "X-OPOS-Token": self.token,
            "Origin": f"http://127.0.0.1:{self.port}",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        }
        status, _, body = self._raw("POST", "/setup/api/validate", headers=hdrs, data=payload)
        self.assertGreaterEqual(status, 400)
        j = json.loads(body)
        self.assertIn("error", j)

    def test_check_bind_rejects_non_loopback(self):
        with self.assertRaises(ValueError):
            make_server(0, self.ctx, host="0.0.0.0")

    def test_progress_initial_state(self):
        status, _, body = self._get("/setup/api/progress")
        self.assertEqual(status, 200)
        p = json.loads(body)
        self.assertFalse(p["running"])
        self.assertFalse(p["done"])
        self.assertEqual(p["steps"], [])

    @unittest.skipIf(sys.platform == "win32", "needs a POSIX host")
    def test_state_ai_tools_linux(self):
        status, _, body = self._get("/setup/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("ai_tools", data)
        ai = data["ai_tools"]
        self.assertIn("supported", ai)
        self.assertIn("tools", ai)
        self.assertIn("modes", ai)
        self.assertIn("default_mode", ai)
        self.assertTrue(ai["supported"])
        self.assertEqual(ai["modes"], ["lite", "full"])
        self.assertEqual(ai["default_mode"], "lite")
        self.assertIsInstance(ai["tools"], list)
        self.assertEqual(len(ai["tools"]), 1)
        t = ai["tools"][0]
        self.assertEqual(t["id"], "fake-tool")
        self.assertIn("name", t)
        self.assertIn("signin", t)

    def test_state_ai_tools_linux_explicit(self):
        prefix = self.tmp_path / "prefix_linux_explicit"
        prefix.mkdir(exist_ok=True)
        cat = self.tmp_path / "cat_linux_explicit.json"
        _write_catalog(cat)
        env = {"OPOS_CONFIG_DIR": str(self.cfg_dir), "OPOS_TOOLS_PREFIX": str(prefix), "OPOS_AI_TOOLS_TABLE": str(cat)}
        ctx = SetupContext(repo_root=self.repo_root, python=sys.executable, home=self.home, platform="linux", env=env, dry_run=True, runner=self.runner, health_check=lambda: True, app_port=8765, root=str(self.tmp_path))
        srv = make_server(0, ctx, host="127.0.0.1")
        thr = threading.Thread(target=srv.serve_forever, daemon=True)
        thr.start()
        time.sleep(0.05)
        try:
            port = srv.server_address[1]
            token = srv.token
            status, _, body = self._get_for(port, token, "/setup/api/state")
            self.assertEqual(status, 200)
            data = json.loads(body)
            ai = data["ai_tools"]
            self.assertTrue(ai["supported"])
            self.assertEqual(ai["modes"], ["lite", "full"])
            self.assertEqual(ai["default_mode"], "lite")
            self.assertEqual(len(ai["tools"]), 1)
            self.assertEqual(ai["tools"][0]["id"], "fake-tool")
        finally:
            srv.shutdown()
            srv.server_close()
            thr.join(timeout=1)

    def test_state_ai_tools_win32_unsupported(self):
        prefix = self.tmp_path / "prefix_win32"
        prefix.mkdir(exist_ok=True)
        cat = self.tmp_path / "cat_win32.json"
        _write_catalog(cat)
        env = {"OPOS_CONFIG_DIR": str(self.cfg_dir), "OPOS_TOOLS_PREFIX": str(prefix), "OPOS_AI_TOOLS_TABLE": str(cat)}
        ctx = SetupContext(repo_root=self.repo_root, python=sys.executable, home=self.home, platform="win32", env=env, dry_run=True, runner=self.runner, health_check=lambda: True, app_port=8765, root=str(self.tmp_path))
        srv = make_server(0, ctx, host="127.0.0.1")
        thr = threading.Thread(target=srv.serve_forever, daemon=True)
        thr.start()
        time.sleep(0.05)
        try:
            port = srv.server_address[1]
            token = srv.token
            status, _, body = self._get_for(port, token, "/setup/api/state")
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertIn("ai_tools", data)
            ai = data["ai_tools"]
            self.assertFalse(ai["supported"])
            self.assertEqual(ai["tools"], [])
            self.assertEqual(ai["modes"], ["lite", "full"])
            self.assertEqual(ai["default_mode"], "lite")
        finally:
            srv.shutdown()
            srv.server_close()
            thr.join(timeout=1)

    def test_validate_ai_tools_and_apply_invalid_mode(self):
        prefix = self.tmp_path / "prefix_ai_validate"
        prefix.mkdir(exist_ok=True)
        cat = self.tmp_path / "cat_ai_validate.json"
        _write_catalog(cat)
        env = {"OPOS_CONFIG_DIR": str(self.cfg_dir), "OPOS_TOOLS_PREFIX": str(prefix), "OPOS_AI_TOOLS_TABLE": str(cat)}
        ctx = SetupContext(repo_root=self.repo_root, python=sys.executable, home=self.home, platform="linux", env=env, dry_run=True, runner=self.runner, health_check=lambda: True, app_port=8765, root=str(self.tmp_path))
        srv = make_server(0, ctx, host="127.0.0.1")
        thr = threading.Thread(target=srv.serve_forever, daemon=True)
        thr.start()
        time.sleep(0.05)
        try:
            port = srv.server_address[1]
            token = srv.token
            good = cfgmod.default_config()
            good["ai_tools"] = {"mode": "lite", "tools": ["fake-tool"]}
            status, _, body = self._post_for(port, token, "/setup/api/validate", {"config": good})
            self.assertEqual(status, 200)
            j = json.loads(body)
            self.assertTrue(j["ok"], j)
            self.assertEqual(j["errors"], [])
            bad = cfgmod.default_config()
            bad["ai_tools"] = {"mode": "badmode", "tools": ["fake-tool"]}
            status, _, body = self._post_for(port, token, "/setup/api/apply", {"config": bad})
            self.assertEqual(status, 400)
            j = json.loads(body)
            self.assertFalse(j["ok"])
            self.assertTrue(j["errors"])
        finally:
            srv.shutdown()
            srv.server_close()
            thr.join(timeout=1)

    def test_tools_runner_passed_to_apply(self):
        import opos.setup.apply as am
        recorded = {}

        orig = am.apply

        def fake_apply(cfg, *a, **kw):
            recorded.update(kw)
            recorded["cfg"] = cfg
            recorded["args"] = a
            prog = kw.get("progress")
            if prog is not None:
                try:
                    prog._done = True
                except Exception:
                    pass
                try:
                    prog._ok = True
                except Exception:
                    pass
            return []

        am.apply = fake_apply
        try:
            stub = lambda *args, **kwargs: []
            prefix = self.tmp_path / "prefix_runner"
            prefix.mkdir(exist_ok=True)
            cat = self.tmp_path / "cat_runner.json"
            _write_catalog(cat)
            env = {"OPOS_CONFIG_DIR": str(self.cfg_dir), "OPOS_TOOLS_PREFIX": str(prefix), "OPOS_AI_TOOLS_TABLE": str(cat)}
            ctx = SetupContext(repo_root=self.repo_root, python=sys.executable, home=self.home, platform="linux", env=env, dry_run=True, runner=self.runner, health_check=lambda: True, tools_runner=stub, app_port=8765, root=str(self.tmp_path))
            srv = make_server(0, ctx, host="127.0.0.1")
            thr = threading.Thread(target=srv.serve_forever, daemon=True)
            thr.start()
            time.sleep(0.05)
            try:
                port = srv.server_address[1]
                token = srv.token
                cfg = cfgmod.default_config()
                status, _, body = self._post_for(port, token, "/setup/api/apply", {"config": cfg})
                self.assertEqual(status, 202)
                deadline = time.time() + 2
                while time.time() < deadline and "tools_runner" not in recorded:
                    time.sleep(0.05)
                self.assertIn("tools_runner", recorded)
                self.assertIs(recorded["tools_runner"], stub)
            finally:
                srv.shutdown()
                srv.server_close()
                thr.join(timeout=1)
        finally:
            am.apply = orig


    def test_idle_connection_times_out_and_normal_request_ok(self):
        handler_cls = self.server.RequestHandlerClass
        original = getattr(handler_cls, "timeout", None)
        self.assertIsNotNone(original)
        self.assertGreater(original, 0)
        self.assertLessEqual(original, 10)
        handler_cls.timeout = 0.3
        try:
            idle = socket.create_connection(("127.0.0.1", self.port), timeout=2)
            try:
                idle.settimeout(5)
                start = time.time()
                data = idle.recv(1)
                elapsed = time.time() - start
                self.assertEqual(data, b"")
                self.assertLess(elapsed, 5)
            finally:
                idle.close()
            status, _, body = self._get("/setup/api/state")
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["first_run"])
        finally:
            handler_cls.timeout = original

    def test_trickled_request_line_and_headers_exceed_total_budget(self):
        orig = self.server.RequestHandlerClass.request_budget
        self.server.RequestHandlerClass.request_budget = 0.3
        try:
            cases = [
                ("request_line", b"GET /"),
                ("headers", ("GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Test: a" % self.port).encode()),
            ]
            for label, initial in cases:
                with self.subTest(label):
                    sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                    sock.settimeout(1)
                    stop = threading.Event()
                    def _sender(sock=sock, stop=stop):
                        end = time.monotonic() + 2
                        while time.monotonic() < end and not stop.is_set():
                            time.sleep(0.05)
                            try:
                                sock.sendall(b"a")
                            except Exception:
                                break
                    thr = threading.Thread(target=_sender, daemon=True)
                    try:
                        sock.sendall(initial)
                        thr.start()
                        start = time.monotonic()
                        try:
                            data = sock.recv(4096)
                        except socket.timeout:
                            self.fail("receive timeout must fail")
                        except (ConnectionResetError, BrokenPipeError, OSError):
                            elapsed = time.monotonic() - start
                            self.assertLess(elapsed, 1)
                        else:
                            elapsed = time.monotonic() - start
                            self.assertLess(elapsed, 1)
                            self.assertEqual(data, b"")
                    finally:
                        stop.set()
                        thr.join(timeout=1)
                        try:
                            sock.close()
                        except Exception:
                            pass
            status, _, _ = self._get("/setup/api/state")
            self.assertEqual(status, 200)
        finally:
            self.server.RequestHandlerClass.request_budget = orig

    def test_setup_rejects_exhausted_slots_closes_request(self):
        from unittest import mock
        handler_cls = self.server.RequestHandlerClass
        slots = mock.Mock()
        slots.acquire.return_value = False
        original = handler_cls._slots
        handler_cls._slots = slots
        try:
            request = mock.Mock()
            loop = ".".join([str(127), str(0), str(0), str(1)])
            with self.assertRaisesRegex(ConnectionAbortedError, "too many connections"):
                handler_cls(request, (loop, 12345), self.server)
            request.close.assert_called_once_with()
            slots.acquire.assert_called_once_with(blocking=False)
            slots.release.assert_not_called()
        finally:
            handler_cls._slots = original

    def test_expired_budget_closes_before_base_handler(self):
        from unittest import mock
        handler_cls = self.server.RequestHandlerClass
        inst = object.__new__(handler_cls)
        inst.connection = mock.Mock()
        inst.close_connection = False
        inst._deadline = 0
        with mock.patch("opos.setup.server.time.monotonic", return_value=1):
            with mock.patch("opos.setup.server.BaseHTTPRequestHandler.handle_one_request") as base:
                result = inst.handle_one_request()
        self.assertIsNone(result)
        self.assertTrue(inst.close_connection)
        inst.connection.close.assert_called_once_with()
        base.assert_not_called()

if __name__ == "__main__":
    unittest.main()
