# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock PIN: the hash, the growing wait, and the server refusing writes while locked."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from opos import lockpin, web
from tests.test_apps_shell import get, post, running


def lock_request(payload=None):
    """Run the real /api/lock dispatcher without opening a socket."""
    handler = web.Handler.__new__(web.Handler)
    handler.path = "/api/lock"
    handler.port = 8765
    handler.token = "t" * 32
    body = json.dumps(payload).encode()
    handler.headers = {"Host": "127.0.0.1:8765", "Content-Type": "application/json",
                       "X-OPOS-Token": handler.token, "Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.limiter = mock.Mock()
    handler.limiter.allow.return_value = True
    handler._json = mock.Mock()
    if payload is None:
        handler._get()
    else:
        handler._post()
    handler._json.assert_called_once()
    args = handler._json.call_args.args
    return args[1] if len(args) > 1 else 200, args[0]


class Hash(unittest.TestCase):
    def test_verify_and_the_iteration_count(self):
        record = lockpin.hash_pin("1234")
        self.assertGreaterEqual(record["iterations"], 200000)
        self.assertTrue(lockpin.verify("1234", record))
        self.assertFalse(lockpin.verify("9999", record))
        with self.assertRaises(ValueError):
            lockpin.hash_pin("12")

    def test_five_wrong_tries_then_a_wait_that_grows(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        with unittest.mock.patch.dict(__import__("os").environ, {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name, "OPOS_LOCK_TEST_CLOCK": "1"}):
            lockpin.set_pin("1234")
            for n in range(4):
                ok, status = lockpin.unlock("0000", 1000)
                self.assertFalse(ok)
                self.assertEqual(status["wait_s"], 0)
            ok, status = lockpin.unlock("0000", 1000)
            self.assertFalse(ok)
            self.assertGreaterEqual(status["wait_s"], 30)
            ok, early = lockpin.unlock("1234", 1000)
            self.assertFalse(ok)
            self.assertGreater(early["wait_s"], 0)
            ok, later = lockpin.unlock("1234", 1031)
            self.assertTrue(ok)
            self.assertFalse(later["locked"])


class LockCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name, "OPOS_LOCK_TEST_CLOCK": "1"}
        self.enterContext(mock.patch.dict(os.environ, self.env))
        lockpin.reset()
        self.addCleanup(lockpin.reset)

    def saved(self):
        return json.loads((self.root / "lock.json").read_text())


class LockStateCharacterization(LockCase):
    def test_status_shape_and_types(self):
        status = lockpin.status(1000)
        expected = {"ok": False, "locked": True, "has_pin": False,
                    "wait_s": 0, "failures": 0, "enforced": True}
        self.assertEqual(status, expected)
        for key, value in expected.items():
            self.assertIs(type(status[key]), type(value), key)

    def test_off_is_not_enforced_or_blocked(self):
        with mock.patch.dict(os.environ, {"OPOS_LOCK": "0"}):
            self.assertEqual(lockpin.status(), {"ok": True, "locked": False,
                             "has_pin": False, "wait_s": 0, "enforced": False})
            self.assertFalse(lockpin.is_locked())
            self.assertFalse(lockpin.blocks("call", "/api"))

    def test_first_run_set_preserves_file_format_and_hash_parameters(self):
        lockpin.set_pin("1234")
        data = self.saved()
        self.assertEqual(set(data), {"schema", "record", "locked", "failures", "wait_until"})
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["record"]["iterations"], 200000)
        self.assertEqual(set(data["record"]), {"salt", "hash", "iterations"})
        self.assertEqual(len(bytes.fromhex(data["record"]["salt"])), 16)
        self.assertEqual(len(bytes.fromhex(data["record"]["hash"])), 32)
        self.assertTrue(lockpin.verify("1234", data["record"]))
        self.assertTrue(lockpin.is_locked())

    def test_first_run_skip_then_lock_and_digitless_unlock(self):
        lockpin.skip()
        self.assertNotIn("record", self.saved())
        self.assertFalse(lockpin.is_locked())
        lockpin.lock()
        self.assertTrue(lockpin.is_locked())
        ok, status = lockpin.unlock("", 1000)
        self.assertTrue(ok)
        self.assertEqual(status, {"ok": True, "locked": False, "has_pin": False,
                                  "wait_s": 0, "failures": 0})

    def test_unlocked_owner_can_replace_or_remove_pin(self):
        lockpin.set_pin("1234")
        self.assertTrue(lockpin.unlock("1234", 1000)[0])
        lockpin.set_pin("5678")
        self.assertTrue(lockpin.is_locked())
        self.assertTrue(lockpin.unlock("5678", 1000)[0])
        lockpin.skip()
        self.assertFalse(lockpin.has_pin())
        self.assertFalse(lockpin.is_locked())

    def test_wrong_then_correct_pin_and_explicit_lock(self):
        lockpin.set_pin("1234")
        ok, status = lockpin.unlock("9999", 1000)
        self.assertFalse(ok)
        self.assertEqual(status, {"ok": False, "locked": True, "has_pin": True,
                                  "wait_s": 0, "failures": 1})
        ok, status = lockpin.unlock("1234", 1000)
        self.assertTrue(ok)
        self.assertEqual(status["failures"], 0)
        self.assertEqual(self.saved()["wait_until"], 0)
        lockpin.lock()
        self.assertTrue(lockpin.is_locked())
        self.assertTrue(lockpin.has_pin())

    def test_five_tries_wait_boundary_and_doubling(self):
        lockpin.set_pin("1234")
        for failure in range(1, 6):
            ok, status = lockpin.unlock("9999", 1000)
            self.assertFalse(ok)
            self.assertEqual(status["failures"], failure)
            self.assertEqual(status["wait_s"], 30 if failure == 5 else 0)
        before = self.saved()
        lockpin.lock()
        self.assertEqual(self.saved(), before)
        for pin in ("1234", "9999"):
            ok, status = lockpin.unlock(pin, 1029)
            self.assertFalse(ok)
            self.assertEqual(status["wait_s"], 1)
            self.assertEqual(self.saved(), before)
        ok, status = lockpin.unlock("9999", 1030)
        self.assertFalse(ok)
        self.assertEqual((status["failures"], status["wait_s"]), (6, 60))
        ok, status = lockpin.unlock("9999", 1090)
        self.assertFalse(ok)
        self.assertEqual((status["failures"], status["wait_s"]), (7, 120))
        ok, status = lockpin.unlock("1234", 1210)
        self.assertTrue(ok)
        self.assertEqual((status["failures"], status["wait_s"]), (0, 0))

    def test_blocks_exemptions(self):
        for action, path in (("stop", "/api/clock"), ("snooze", "/api/clock"),
                             ("hang_up", "/api"), ("unlock", "/api"),
                             ("set", "/api/lock"), ("skip", "/api/lock")):
            with self.subTest(action=action, path=path):
                self.assertFalse(lockpin.blocks(action, path))
        for action, path in (("call", "/api"), ("send", "/api"),
                             ("stop", "/api/notes"), ("save", "/api/clock")):
            with self.subTest(action=action, path=path):
                self.assertTrue(lockpin.blocks(action, path))
        lockpin.skip()
        self.assertFalse(lockpin.blocks("save", "/api/notes"))

    def test_redaction_and_counts(self):
        with mock.patch("opos.clock_page.snapshot", return_value={"next": {"text": "07:30"}}):
            self.assertEqual(lockpin.counts(), {"messages": 0, "missed": 0, "alarm": "07:30"})
            snap = {"clock": "07:00", "battery": {"percent": 50, "private": "Ana"},
                    "notice": "Ana called", "threads": [{"name": "Ana"}]}
            self.assertEqual(lockpin.redact(snap), {"locked": True, "clock": "07:00",
                             "battery": {"percent": 50},
                             "lock": {"messages": 0, "missed": 0, "alarm": "07:30"}})
        with mock.patch("opos.clock_page.snapshot", side_effect=ValueError):
            self.assertEqual(lockpin.counts(), {"messages": 0, "missed": 0, "alarm": ""})

    def test_http_first_run_skip_set_unlock_lock_and_errors(self):
        for payload, expected_code, locked in (
                ({"action": "skip"}, 200, False),
                ({"action": "lock"}, 200, True),
                ({"action": "set", "pin": "1234"}, 200, True),
                ({"action": "unlock", "pin": "9999", "now": 1000}, 403, True),
                ({"action": "unlock", "pin": "1234", "now": 1000}, 200, False),
                ({"action": "set", "pin": "5678"}, 200, True),
        ):
            with self.subTest(payload=payload):
                code, body = lock_request(payload)
                self.assertEqual(code, expected_code, body)
                keys = {"ok", "locked", "has_pin", "wait_s", "failures"}
                if payload["action"] != "unlock":
                    keys.add("enforced")
                self.assertEqual(set(body), keys)
                self.assertEqual(body["locked"], locked)
        code, body = lock_request()
        self.assertEqual(code, 200, body)
        self.assertEqual(set(body), {"ok", "locked", "has_pin", "wait_s",
                         "failures", "enforced", "lock"})
        code, body = lock_request({"action": "unknown"})
        self.assertEqual(code, 400, body)
        self.assertEqual(body, {"error": "bad request"})


class LockRegressions(LockCase):
    def assert_pin_change_refused(self, action):
        for waiting in (False, True):
            with self.subTest(waiting=waiting), mock.patch("time.time", return_value=1000):
                (self.root / "lock.json").unlink(missing_ok=True)
                lockpin.set_pin("1234")
                if waiting:
                    for _ in range(5):
                        lockpin.unlock("9999", 1000)
                before = (self.root / "lock.json").read_bytes()
                for pin in ("5678", "bad"):
                    code, body = lock_request({"action": action, "pin": pin})
                    self.assertEqual(code, 403, body)
                    self.assertEqual(body, lockpin.status(1000))
                    self.assertEqual((self.root / "lock.json").read_bytes(), before)
                # Direct callers must obey the same policy as the HTTP dispatcher.
                if action == "set":
                    lockpin.set_pin("5678")
                else:
                    lockpin.skip()
                self.assertEqual((self.root / "lock.json").read_bytes(), before)

    def test_locked_pin_skip_is_refused(self):
        self.assert_pin_change_refused("skip")

    def test_locked_pin_set_is_refused(self):
        self.assert_pin_change_refused("set")

    def test_request_clock_is_ignored_without_explicit_test_switch(self):
        lockpin.set_pin("1234")
        for flag in ("", "0", "true"):
            with self.subTest(flag=flag), mock.patch.dict(os.environ, {"OPOS_LOCK_TEST_CLOCK": flag}), \
                    mock.patch("time.time", return_value=1_700_000_000):
                for _ in range(5):
                    lock_request({"action": "unlock", "pin": "9999"})
                before = self.saved()
                code, body = lock_request({"action": "unlock", "pin": "1234", "now": 9_999_999_999})
                self.assertEqual(code, 403, body)
                self.assertEqual(body["wait_s"], 30)
                self.assertEqual(self.saved(), before)

    def test_explicit_test_switch_allows_request_clock(self):
        lockpin.set_pin("1234")
        with mock.patch("time.time", return_value=1_700_000_000):
            for _ in range(5):
                code, body = lock_request({"action": "unlock", "pin": "9999", "now": 1000})
            self.assertEqual((code, body["wait_s"]), (403, 30))
            code, body = lock_request({"action": "unlock", "pin": "1234", "now": 1030})
            self.assertEqual(code, 200, body)
            self.assertFalse(body["locked"])

    def test_status_and_get_report_remaining_whole_seconds(self):
        lockpin.set_pin("1234")
        for _ in range(5):
            lockpin.unlock("9999", 1_700_000_000)
        for now, remaining in ((1_700_000_000, 30), (1_700_000_012.75, 17),
                               (1_700_000_030, 0), (1_700_000_040, 0)):
            with self.subTest(now=now), mock.patch("time.time", return_value=now):
                self.assertEqual(lockpin.status()["wait_s"], remaining)
                code, body = lock_request()
                self.assertEqual(code, 200, body)
                self.assertEqual(body["wait_s"], remaining)

    def test_malformed_saved_state_falls_back_to_locked(self):
        record = lockpin.hash_pin("1234")
        for fields in ({"locked": None}, {"locked": 0}, {"locked": ""},
                       {"locked": False, "failures": "bad"},
                       {"locked": False, "failures": -1},
                       {"locked": False, "wait_until": "bad"},
                       {"locked": False, "wait_until": float("inf")},
                       {"locked": False, "wait_until": 10**400},
                       {"locked": False, "record": "bad"},
                       {"locked": False, "record": {"iterations": []}}):
            with self.subTest(fields=fields):
                data = dict(schema=1, record=record, failures=0, wait_until=0)
                data.update(fields)
                (self.root / "lock.json").write_text(json.dumps(data))
                status = lockpin.status()
                self.assertTrue(status["locked"])
                self.assertFalse(status["ok"])
                self.assertTrue(lockpin.is_locked())
                self.assertTrue(lockpin.blocks("call", "/api"))
                if "record" in fields:
                    code, body = lock_request({"action": "unlock", "pin": ""})
                    self.assertEqual(code, 403, body)
                    for action in ("set", "skip"):
                        code, body = lock_request({"action": action, "pin": "5678"})
                        self.assertEqual(code, 403, body)
        for raw in ("not json", "[]", '{"schema":0}'):
            with self.subTest(raw=raw):
                (self.root / "lock.json").write_text(raw)
                self.assertTrue(lockpin.status()["locked"])


class Server(unittest.TestCase):
    def test_locked_state_has_no_names_and_writes_are_refused(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        env = {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name}
        snap = {"notice": "Ana called", "contacts": [{"name": "Ana"}], "threads": [{"name": "Ana"}], "clock": "07:00", "battery": {"percent": 50}}
        red = lockpin.redact(snap)
        self.assertNotIn("Ana", json.dumps(red))
        self.assertEqual(red["battery"]["percent"], 50)
        self.assertNotIn("notice", red)
        with running(env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/lock", {"action": "set", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            self.assertTrue(lockpin.blocks("call", "/api"))
            self.assertFalse(lockpin.blocks("stop", "/api/clock"))
            self.assertFalse(lockpin.blocks("hang_up", "/api"))
            code, raw = post(server, "/api", {"action": "call", "number": "+15550100"}, token)
            self.assertEqual(code, 403)
            self.assertEqual(json.loads(raw)["error"], "locked")
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234", "now": 50}, token)
            self.assertEqual(code, 200, raw)
            self.assertFalse(json.loads(raw)["locked"])
            status, body = get(server, "/api/state", token)
            self.assertEqual(status, 200)
            self.assertNotIn("locked", json.loads(body))
        page = web.page()
        self.assertNotIn(">Emergency<", page)


if __name__ == "__main__":
    unittest.main()
