# SPDX-License-Identifier: GPL-3.0-or-later
"""Update-job contracts and regressions, with in-memory transport and temporary files."""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from opos import update_client as uc, web
from tests.test_update_client import Env, no_network, tar_bundle


class LocalJob(Env):
    """Reuse the install fixtures without starting their loopback source."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = uc.Paths(config=os.path.join(self.tmp.name, "update.json"),
                              root=os.path.join(self.tmp.name, "lib"),
                              revision=os.path.join(self.tmp.name, "REVISION"))
        os.makedirs(self.paths.inbox)
        self.url = "https://updates.example.org/latest.json"
        self.put(self.paths.config, {"url": self.url})
        self.state(2)
        self.bundle = tar_bundle(7)
        self.index = {"serial": 7, "version": "0.7.0", "url": "opos-7.opos-update",
                      "size": len(self.bundle), "sha256": hashlib.sha256(self.bundle).hexdigest()}
        self.opened = []
        self.c = uc.UpdateClient(self.paths, clock=lambda: 1700000000)
        for patch in no_network() + [mock.patch.object(self.c, "_open", side_effect=self.open)]:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.join)

    def join(self):
        with contextlib.suppress(RuntimeError):  # a failed Thread.start leaves an unjoinable thread before the fix
            self.c.wait(5)

    def open(self, url, cfg):
        self.opened.append(url)
        body = json.dumps(self.index).encode() if url == self.url else self.bundle
        response = io.BytesIO(body)
        response.headers = {"Content-Length": str(len(body))}
        response.geturl = lambda: url
        return response

    def post(self, action):
        """Run the real request handler and JSON writer without binding a socket."""
        body = json.dumps({"action": action}).encode()
        h = object.__new__(web.Handler)
        h.path, h.command, h.request_version = "/api", "POST", "HTTP/1.1"
        h.requestline = "POST /api HTTP/1.1"
        h.port, h.token, h.updater = 8765, "a" * 43, self.c
        h.headers = {"Host": "127.0.0.1:8765", "Content-Type": "application/json",
                     "Content-Length": str(len(body)), "X-OPOS-Token": h.token}
        h.limiter = web._Limits()
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h._admit_request = lambda: True
        with mock.patch.object(web.lockpin, "blocks", return_value=False):
            h.do_POST()
        headers, payload = h.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), payload


class Characterization(LocalJob):
    idle = {"kind": None, "phase": "idle", "bytes": 0, "total": None, "serial": None, "message": ""}

    def test_initial_snapshot_fields_values_and_json_bytes(self):
        expected = {
            "available": True, "message": None,
            "running": {"version": "0.1.0", "revision": "a" * 40, "serial": 2, "release": "2-aaaaaaaaaaaa"},
            "source": {"configured": True, "url": self.url, "auto_check": False, "allow_lan_http": False, "error": None},
            "check": None, "job": self.idle, "pending": [], "stalled": False,
            "rollback": {"possible": False, "requested": False}, "status": None,
        }
        self.assertEqual(json.dumps(self.c.snapshot()).encode(), json.dumps(expected).encode())
        self.assertEqual(self.opened, [])

    def test_check_payload_and_legacy_verdict_words(self):
        for current, failed, newer, message in (
            (2, [], True, "Version 0.7.0 (update 7) is available."),
            (7, [], False, "You have the latest update."),
            (9, [], False, "The source offers update 7, older than the installed update 9."),
            (2, [7], False, "Update 7 was tried on this phone and rolled back, so OPOS will not fetch it again."),
        ):
            with self.subTest(current=current, failed=failed):
                self.state(current, failed=failed)
                expected = {"time": "2023-11-14 22:13:20Z", "ok": True, "newer": newer, "serial": 7,
                            "version": "0.7.0", "size": len(self.bundle), "message": message}
                self.assertEqual(json.dumps(self.c.check()), json.dumps(expected))
                self.assertEqual(self.c.job(), self.idle)
        self.assertEqual(self.inbox(), [])
        self.assertEqual(self.opened, [self.url] * 4)

    def test_checking_keeps_the_job_projection_and_rejects_overlap(self):
        fetch = self.c._fetch_index

        def checking(cfg):
            self.assertEqual(self.c.snapshot()["job"], self.idle)
            for action in (self.c.check, self.c.start_download):
                with self.assertRaises(uc.UpdateError) as cm:
                    action()
                self.assertEqual((cm.exception.code, cm.exception.status, str(cm.exception)),
                                 ("busy", 409, "An update check or download is already running."))
            return fetch(cfg)

        with mock.patch.object(self.c, "_fetch_index", side_effect=checking):
            self.c.check()
        self.assertEqual(self.c.job(), self.idle)

    def test_check_failure_payload_and_recovery_leave_job_unchanged(self):
        with mock.patch.object(self.c, "_fetch_index", side_effect=uc.UpdateError("fetch", "The update server answered 404.", 502)):
            status, body = self.post("update_check")
        self.assertEqual((status, body), (502, b'{"error": "The update server answered 404.", "code": "fetch"}'))
        self.assertEqual(self.c.snapshot()["check"], {"time": "2023-11-14 22:13:20Z", "ok": False,
                         "newer": None, "serial": None, "version": None, "size": None,
                         "message": "The update server answered 404."})
        self.assertEqual(self.c.job(), self.idle)
        self.assertTrue(self.c.check()["newer"])

    def test_download_job_and_atomic_bundle_handoff(self):
        replace = os.replace

        def handoff(part, final):
            self.assertEqual((os.path.basename(part), os.path.basename(final)),
                             ("opos-7.opos-update.partial", "opos-7.opos-update"))
            self.assertEqual(self.c.pending(), [])
            self.assertEqual(self.c.job(), {"kind": "download", "phase": "downloading", "bytes": len(self.bundle),
                             "total": len(self.bundle), "serial": 7, "message": "Downloading."})
            for action in (self.c.check, self.c.start_download):
                with self.assertRaises(uc.UpdateError) as cm:
                    action()
                self.assertEqual((cm.exception.code, cm.exception.status), ("busy", 409))
            replace(part, final)

        with mock.patch.object(uc.os, "replace", side_effect=handoff):
            self.c.start_download()
            self.assertTrue(self.c.wait(5))
        self.assertEqual(self.c.job(), {"kind": "download", "phase": "done", "bytes": len(self.bundle),
                         "total": len(self.bundle), "serial": 7, "message": "Downloaded. The updater applies it next."})
        self.assertEqual(self.inbox(), ["opos-7.opos-update"])
        with open(os.path.join(self.paths.inbox, "opos-7.opos-update"), "rb") as f:
            self.assertEqual(f.read(), self.bundle)
        delivered = self.c.job()
        self.c.check()
        self.assertEqual(self.c.job(), delivered)

    def test_delivery_is_separate_from_root_failure_and_stalling(self):
        self.c.start_download()
        self.assertTrue(self.c.wait(5))
        self.status(phase="failed", serial=7, message="Signature refused.")
        snap = self.c.snapshot()
        self.assertEqual((snap["job"]["phase"], snap["status"]["phase"], snap["pending"], snap["stalled"]),
                         ("done", "failed", ["opos-7.opos-update"], False))
        st = os.stat(os.path.join(self.paths.inbox, "opos-7.opos-update"))
        self.c._clock = lambda: max(st.st_mtime, st.st_ctime) + 300
        self.assertFalse(self.c.snapshot()["stalled"])
        self.c._clock = lambda: max(st.st_mtime, st.st_ctime) + 301
        self.assertTrue(self.c.snapshot()["stalled"])
        os.unlink(os.path.join(self.paths.inbox, "opos-7.opos-update"))
        self.assertEqual(self.c.snapshot()["job"]["phase"], "done")

    def test_failed_download_fields_and_retry(self):
        with mock.patch.object(self.c, "download", side_effect=uc.UpdateError("space", "There is not enough free space for the update.")):
            self.c.start_download()
            self.assertTrue(self.c.wait(5))
        failed = {"kind": "download", "phase": "failed", "bytes": 0, "total": len(self.bundle),
                  "serial": 7, "message": "There is not enough free space for the update."}
        self.assertEqual(self.c.job(), failed)
        self.c.check()
        self.assertEqual(self.c.job(), failed)
        self.c.start_download()
        self.assertTrue(self.c.wait(5))
        self.assertEqual(self.c.job()["phase"], "done")

    def test_root_status_words_and_unknown_adapter(self):
        for phase in ("idle", "verifying", "applying", "checking", "done", "rolled-back", "failed", "unexpected"):
            with self.subTest(phase=phase):
                self.status(phase=phase, serial=7, message="Root status.")
                expected = {"phase": phase if phase != "unexpected" else "unknown", "message": "Root status.",
                            "serial": 7, "time": "2026-11-08T12:00:00Z"}
                self.assertEqual(json.dumps(self.c.snapshot()["status"]), json.dumps(expected))

    def test_rollback_request_bytes_and_root_files_are_unchanged(self):
        for staged, expected in ((None, b""), ({"serial": 8}, b"cancel-staged")):
            with self.subTest(staged=staged):
                self.state(2, previous=["1-aaaaaaaaaaaa"], pending_boot=staged)
                self.status()
                originals = {}
                for path in (self.paths.config, self.paths.state, self.paths.status):
                    with open(path, "rb") as f:
                        originals[path] = f.read()
                request = self.c.request_rollback()
                self.assertEqual(request, {"requested": True, "already": False, **({"cancel": True} if staged else {})})
                self.assertEqual(self.inbox(), ["rollback.request"])
                path = os.path.join(self.paths.inbox, "rollback.request")
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), expected)
                for path, data in originals.items():
                    with open(path, "rb") as f:
                        self.assertEqual(f.read(), data)
                os.unlink(os.path.join(self.paths.inbox, "rollback.request"))

    def test_api_success_and_error_bytes(self):
        status, body = self.post("update_check")
        self.assertEqual(status, 200)
        self.assertEqual(body, json.dumps(dict(self.c.snapshot(), ok=True)).encode())
        self.state(7)
        self.assertEqual(self.post("update_download"),
                         (409, b'{"error": "You have the latest update.", "code": "no_update"}'))
        self.assertEqual(self.post("update_rollback"),
                         (409, b'{"error": "There is no earlier version to go back to.", "code": "no_rollback"}'))
        os.unlink(self.paths.config)
        self.assertEqual(self.post("update_check"),
                         (409, json.dumps({"error": uc.NOT_CONFIGURED, "code": "not_configured"}).encode()))
        os.rmdir(self.paths.inbox)
        os.unlink(self.paths.state)
        os.rmdir(self.paths.root)
        self.assertEqual(self.post("update_download"),
                         (404, b'{"error": "Updates are not available on this install.", "code": "not_available"}'))

    def test_cli_startup_output_with_stubbed_server(self):
        server, out = mock.Mock(), io.StringIO()
        server.server_address = ("127.0.0.1", 43210)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(web, "make_server", return_value=server), \
                mock.patch.object(web, "Phone"), mock.patch.object(web, "_arm_configured_goal"), \
                mock.patch.object(web, "_route_with_glimmer"), mock.patch.object(web.setup_config, "load", return_value=None), \
                mock.patch("sys.stdout", out):
            web.main(["--port", "0"])
        self.assertEqual(out.getvalue(), "OPOS desktop demo: http://127.0.0.1:43210/?demo=1 (Ctrl-C to stop)\n")
        server.RequestHandlerClass.updater.start_auto_check.assert_called_once_with()


class Regressions(LocalJob):
    def launch_failure(self, patch):
        with patch, self.assertRaisesRegex(RuntimeError, "cannot start worker"):
            self.c.start_download()
        self.assertEqual(self.c.job(), {"kind": "download", "phase": "failed", "bytes": 0,
                         "total": len(self.bundle), "serial": 7, "message": "The download failed."})
        self.assertTrue(self.c.wait(0))
        self.assertEqual(self.inbox(), [])
        self.assertTrue(self.c.check()["newer"])
        self.c.start_download()
        self.assertTrue(self.c.wait(5))
        self.assertEqual(self.c.job()["phase"], "done")

    def test_thread_constructor_failure_is_terminal_and_retryable(self):
        self.launch_failure(mock.patch.object(uc.threading, "Thread", side_effect=RuntimeError("cannot start worker")))

    def test_thread_start_failure_is_terminal_and_retryable(self):
        self.launch_failure(mock.patch.object(uc.threading.Thread, "start", side_effect=RuntimeError("cannot start worker")))

    def test_launch_failure_keeps_http_500_but_clears_downloading(self):
        with mock.patch.object(uc.threading.Thread, "start", side_effect=RuntimeError("cannot start worker")), \
                self.assertLogs("opos.security", level="WARNING"):
            self.assertEqual(self.post("update_download"), (500, b'{"error": "internal error"}'))
        self.assertEqual(self.c.snapshot()["job"]["phase"], "failed")

    def test_floor_refuses_trimmed_serials_without_fetching_a_bundle(self):
        for floor in (7, 9):
            with self.subTest(floor=floor):
                self.state(2, floor=floor, failed=[])
                result = self.c.check()
                self.assertFalse(result["newer"])
                message = f"Update 7 is not above {floor}, the highest update that failed on this phone, so OPOS will not fetch it."
                self.assertEqual(result["message"], message)
                with self.assertRaises(uc.UpdateError) as cm:
                    self.c.start_download()
                self.assertEqual((cm.exception.code, cm.exception.status, cm.exception.message), ("no_update", 409, message))
        self.assertEqual(self.opened, [self.url] * 4)
        self.assertEqual(self.inbox(), [])

    def test_stale_check_is_reworked_when_the_floor_advances(self):
        self.assertTrue(self.c.check()["newer"])
        self.state(2, floor=8, failed=[])
        self.assertFalse(self.c.snapshot()["check"]["newer"])
        self.assertEqual(self.opened, [self.url])
        with self.assertRaises(uc.UpdateError) as cm:
            self.c.start_download()
        self.assertEqual((cm.exception.code, cm.exception.status), ("no_update", 409))
        self.assertEqual(self.opened, [self.url, self.url])

    def test_legacy_state_uses_failed_high_water_mark_like_root(self):
        self.state(2, floor=None, failed=[9])
        self.assertFalse(self.c.check()["newer"])
        self.assertFalse(self.c.snapshot()["rollback"]["possible"])


class Boundaries(LocalJob):
    def test_floor_validation_and_legacy_fallback_do_not_enable_cancel(self):
        for floor in (True, -1, "8", 8.0, uc.MAX_SERIAL + 1):
            with self.subTest(floor=floor):
                self.state(2, floor=floor)
                self.assertFalse(self.c.check()["newer"])
                with self.assertRaises(uc.UpdateError) as cm:
                    self.c.start_download()
                self.assertEqual((cm.exception.code, cm.exception.status), ("no_update", 409))
                self.state(7, floor=floor)
                self.assertEqual(self.c.check()["message"], "You have the latest update.")
        for floor in (None, 0, 6):
            with self.subTest(floor=floor):
                self.state(2, floor=floor)
                self.assertTrue(self.c.check()["newer"])
        self.state(2, floor=None, failed=[9], pending_boot={"serial": 10})
        self.assertFalse(self.c.snapshot()["rollback"]["possible"])
        self.assertNotIn("staged", self.c.snapshot()["rollback"])
        self.state(2, floor=6, failed=[9])
        self.assertFalse(self.c.check()["newer"])
        self.assertEqual(self.opened, [self.url] * 19)

    def test_floor_http_error_and_above_floor_delivery(self):
        self.state(2, floor=7)
        self.assertEqual(self.post("update_download"), (409, json.dumps({
            "error": "Update 7 is not above 7, the highest update that failed on this phone, so OPOS will not fetch it.",
            "code": "no_update"}).encode()))
        self.state(2, floor=6)
        self.c.start_download()
        self.assertTrue(self.c.wait(5))
        self.assertEqual(self.c.job()["phase"], "done")

    def test_a_blocked_worker_excludes_other_jobs_and_wait_times_out(self):
        entered, release = threading.Event(), threading.Event()
        download = self.c.download

        def blocked(cfg, idx):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release worker")
            return download(cfg, idx)

        with mock.patch.object(self.c, "download", side_effect=blocked):
            try:
                self.c.start_download()
                self.assertTrue(entered.wait(5))
                self.assertFalse(self.c.wait(0))
                self.assertEqual(self.c.job()["phase"], "downloading")
                for call in (self.c.check, self.c.start_download):
                    with self.assertRaises(uc.UpdateError) as cm:
                        call()
                    self.assertEqual((cm.exception.code, cm.exception.status), ("busy", 409))
            finally:
                release.set()
                self.assertTrue(self.c.wait(5))
        self.assertEqual(self.c.job()["phase"], "done")

    def test_generic_worker_failure_is_terminal_and_retryable(self):
        with mock.patch.object(self.c, "download", side_effect=RuntimeError("private diagnostic")), \
                self.assertLogs("opos.update", level="WARNING"):
            self.c.start_download()
            self.assertTrue(self.c.wait(5))
        self.assertEqual((self.c.job()["phase"], self.c.job()["message"]), ("failed", "The download failed."))
        self.c.start_download()
        self.assertTrue(self.c.wait(5))
        self.assertEqual(self.c.job()["phase"], "done")

    def test_auto_check_never_starts_a_download(self):
        self.put(self.paths.config, {"url": self.url, "auto_check": True})
        with mock.patch.object(self.c._stop, "wait", side_effect=[False, True]) as wait:
            self.c._auto_loop(120, 21600)
        self.assertEqual(wait.call_args_list, [mock.call(120), mock.call(21600)])
        self.assertTrue(self.c.snapshot()["check"]["newer"])
        self.assertEqual(self.c.job()["phase"], "idle")
        self.assertEqual((self.opened, self.inbox()), ([self.url], []))

    def test_worker_base_exception_cleans_up_before_it_propagates(self):
        class Interrupted(BaseException):
            pass

        with mock.patch.object(self.c, "download", side_effect=Interrupted), \
                mock.patch.object(threading, "excepthook") as hook:
            self.c.start_download()
            self.assertTrue(self.c.wait(5))
        self.assertIs(hook.call_args.args[0].exc_type, Interrupted)
        self.assertEqual((self.c.job()["phase"], self.c.job()["message"]), ("failed", "The download failed."))
        self.assertTrue(self.c.check()["newer"])


if __name__ == "__main__":
    unittest.main()
