# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock PIN: the hash, the growing wait, and the server refusing writes while locked."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from opos import calllog, lockpin, web
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


class LockedAnswers(unittest.TestCase):
    """What the server says while locked: counts and the clock, never names, labels or text."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name, "OPOS_CONFIG_DIR": tmp.name,
                    "OPOS_APPS": "clock,contacts,notes,calls"}
        self.addCleanup(lockpin.reset)
        self.addCleanup(calllog.reset)
        lockpin.reset()
        calllog.reset()

    def open_phone(self, server):
        """Save a PIN, then unlock, so the test can write as the owner would."""
        token = server.RequestHandlerClass.token
        code, raw = post(server, "/api/lock", {"action": "set", "pin": "1234"}, token)
        self.assertEqual(code, 200, raw)
        code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234"}, token)
        self.assertEqual(code, 200, raw)
        return token

    def lock_phone(self, server, token):
        code, raw = post(server, "/api/lock", {"action": "lock"}, token)
        self.assertEqual(code, 200, raw)
        self.assertTrue(json.loads(raw)["locked"])

    def assert_locked_body(self, raw, *private):
        body = json.loads(raw)
        self.assertIs(body.get("locked"), True, raw)
        self.assertEqual(set(body["lock"]), {"messages", "missed", "alarm"})
        text = raw.decode("utf-8")
        for word in private:
            self.assertNotIn(word, text)
        return body

    def test_locked_app_reads_have_no_names_notes_or_labels(self):
        with running(self.env) as server:
            token = self.open_phone(server)
            code, raw = post(server, "/api/notes", {"action": "add"}, token)
            self.assertEqual(code, 200, raw)
            code, raw = post(server, "/api/notes", {"action": "save", "id": json.loads(raw)["id"], "text": "Buy plums for Ana"}, token)
            self.assertEqual(code, 200, raw)
            code, raw = post(server, "/api/clock", {"action": "add", "hour": 7, "minute": 5, "days": [], "label": "Pills for Ana"}, token)
            self.assertEqual(code, 200, raw)
            for path, word in (("/api/contacts", b"Mom"), ("/api/notes", b"plums"), ("/api/clock", b"Pills")):
                status, body = get(server, path, token)
                self.assertEqual(status, 200, body)
                self.assertIn(word, body, path)  # unlocked, the owner reads their own data
            self.lock_phone(server, token)
            for path in ("/api/contacts", "/api/notes", "/api/clock", "/api/calls"):
                with self.subTest(path=path):
                    status, body = get(server, path, token)
                    self.assertEqual(status, 200, body)
                    parsed = self.assert_locked_body(body, "Mom", "+15550100", "plums", "Pills", "Ana")
                    for key in ("people", "notes", "calls"):
                        self.assertNotIn(key, parsed)
                    self.assertFalse(parsed.get("alarms"))
            # The token is still required, locked or not.
            status, body = get(server, "/api/contacts")
            self.assertEqual(status, 403, body)
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            status, body = get(server, "/api/contacts", token)
            self.assertIn(b"Mom", body)

    def test_the_answer_to_an_allowed_locked_write_is_redacted(self):
        with running(self.env) as server:
            token = self.open_phone(server)
            code, raw = post(server, "/api", {"action": "send", "number": "+15550123", "text": "meet at the pier"}, token)
            self.assertEqual(code, 200, raw)
            self.assertIn(b"pier", raw)
            code, raw = post(server, "/api/clock", {"action": "add", "hour": 7, "minute": 5, "days": [], "label": "Pills for Ana"}, token)
            self.assertEqual(code, 200, raw)
            alarm = json.loads(raw)["alarms"][0]["id"]
            self.lock_phone(server, token)
            code, raw = post(server, "/api", {"action": "hang_up"}, token)
            self.assertEqual(code, 200, raw)
            self.assert_locked_body(raw, "pier", "+15550123")
            code, raw = post(server, "/api/clock", {"action": "stop", "id": alarm}, token)
            self.assertEqual(code, 200, raw)
            self.assert_locked_body(raw, "Pills", "Ana")
            # An allowed request the app turns down answers with its error word and nothing else.
            code, raw = post(server, "/api/clock", {"action": "stop", "id": "no-such-alarm"}, token)
            self.assertEqual(code, 400, raw)
            self.assertEqual(set(json.loads(raw)), {"error"})
            # A refused write keeps its plain refusal.
            code, raw = post(server, "/api/clock", {"action": "delete", "id": alarm}, token)
            self.assertEqual((code, json.loads(raw)), (403, {"error": "locked"}))

    def test_a_ringing_alarm_shows_its_time_but_not_its_label_while_locked(self):
        from opos import clock_page
        ringing = {"ok": True, "clock_ok": True, "now": "2026-11-14T07:05:10+00:00", "alarms": [{"id": "a", "label": "Pills for Ana"}],
                   "ringing": {"id": "a", "label": "Pills for Ana", "text": "Alarm 07:05"}, "timer": {"label": "Ana"},
                   "notices": ["Pills for Ana"], "recovered": "", "next": {"id": "a", "text": "Alarm 07:05", "label": "Pills for Ana", "at": "x"}}
        with running(self.env) as server, mock.patch.object(clock_page, "snapshot", return_value=ringing):
            token = self.open_phone(server)
            self.lock_phone(server, token)
            status, raw = get(server, "/api/clock", token)
            self.assertEqual(status, 200, raw)
            body = self.assert_locked_body(raw, "Pills", "Ana")
            self.assertEqual(body["ringing"], {"text": "Alarm 07:05"})
            self.assertEqual(body["alarms"], [])
            self.assertIsNone(body["timer"])

    def test_an_app_cannot_replace_the_lock_fields_or_send_more_than_an_error_word(self):
        from opos import apps_page

        class Phone:
            def state(self):
                return {"clock": "07:05", "battery": {"percent": 50}, "contacts": [{"name": "Ana"}]}

        handler = mock.Mock()
        handler.phone = Phone()
        handler.lock = mock.MagicMock()
        handler.limiter.allow.return_value = True
        app = mock.Mock()
        with mock.patch.dict(os.environ, self.env):
            for extra in ({"locked": False, "clock": "x", "battery": {"percent": 1}, "lock": "x", "ringing": {"text": "Alarm 07:05"}},
                          ["Ana"], "Ana", None):
                app.locked_json = mock.Mock(return_value=extra)
                body = apps_page.locked_json(handler, app)
                self.assertEqual((body["locked"], body["clock"], body["battery"]), (True, "07:05", {"percent": 50}))
                self.assertEqual(set(body["lock"]), {"messages", "missed", "alarm"})
                self.assertEqual(set(body) - {"locked", "clock", "battery", "lock"}, {"ringing"} if isinstance(extra, dict) else set())
                self.assertNotIn("Ana", json.dumps(body))
            app.locked_json = mock.Mock(side_effect=RuntimeError("Ana"))
            self.assertEqual(set(apps_page.locked_json(handler, app)), {"locked", "clock", "battery", "lock"})
            app.locked_json = None
            handler.phone = None
            self.assertEqual(apps_page.locked_json(handler, app)["locked"], True)
            # Locked, an app's refusal is its error word and nothing else it may have put beside it.
            for answer, sent in (((400, {"error": "id", "people": ["Ana"]}), {"error": "id"}),
                                 ((409, {"error": ["Ana"], "note": "Ana"}), {"error": "bad request"}),
                                 ((400, {"people": ["Ana"]}), {"error": "bad request"})):
                app.post = mock.Mock(return_value=answer)
                handler._json.reset_mock()
                with mock.patch.object(apps_page, "_api_app", return_value=app):
                    apps_page.post(handler, "/api/sample", {"action": "stop"})
                handler._json.assert_called_once_with(sent, answer[0])
            # Unlocked, the app's answer is sent as it is.
            with mock.patch.object(lockpin, "is_locked", return_value=False), mock.patch.object(apps_page, "_api_app", return_value=app):
                handler._json.reset_mock()
                apps_page.post(handler, "/api/sample", {"action": "stop"})
                handler._json.assert_called_once_with({"people": ["Ana"]}, 400)

    def test_missed_calls_are_counted_from_the_call_log(self):
        row = {"id": "0" * 32, "number": "+15550177", "name": "Ana", "direction": "missed",
               "start_time": "2026-11-14T07:00:00+00:00", "duration": 0, "practice": True, "state": "ended"}
        rows = [dict(row, id="%032x" % n, start_time="2026-11-14T07:0%d:00+00:00" % (n + 1)) for n in range(2)]
        rows.append(dict(row, id="f" * 32, direction="outgoing", start_time="2026-11-14T07:09:00+00:00"))
        with mock.patch.dict(os.environ, self.env):
            self.assertEqual(lockpin.counts()["missed"], 0)
            (self.root / "calls.json").write_text(json.dumps({"schema": 1, "calls": rows}))
            calllog.reset()
            got = lockpin.counts()
            self.assertEqual(got["missed"], 2)
            self.assertEqual(set(got), {"messages", "missed", "alarm"})
            self.assertNotIn("Ana", json.dumps(got))
            self.assertNotIn("+15550177", json.dumps(got))
            self.assertEqual(lockpin.redact({})["lock"]["missed"], 2)
            # The log belongs to the Call log app: with that app off, nothing is counted.
            with mock.patch.dict(os.environ, {"OPOS_APPS": "clock"}):
                self.assertEqual(lockpin.counts()["missed"], 0)
            # A damaged or unreadable log is no count, not an error on the lock screen.
            with mock.patch.object(calllog, "snapshot", side_effect=OSError("disk")):
                self.assertEqual(lockpin.counts()["missed"], 0)
            for bad in (True, -1, "2", None, 2.0):
                with mock.patch.object(calllog, "snapshot", return_value={"missed_count": bad}):
                    self.assertEqual(lockpin.counts()["missed"], 0, bad)


class PinPad(unittest.TestCase):
    def test_the_pad_is_served_only_while_the_lock_is_on(self):
        self.assertNotIn("pin.js", web.page("t" * 43, "n" * 22))
        for off in ({}, {"OPOS_LOCK": "0"}, {"OPOS_LOCK": ""}, {"OPOS_LOCK": "true"}):
            with self.subTest(off=off), mock.patch.dict(os.environ, {"OPOS_LOCK": ""}), running(off) as server:
                status, body = get(server, "/")
                self.assertEqual(status, 200)
                self.assertNotIn(b"pin.js", body)
                self.assertEqual(lockpin.inject_bytes(body, "n" * 22), body)
                missing, _ = get(server, "/pin.js")
                self.assertEqual(missing, 404)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        with running({"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name}) as server:
            status, body = get(server, "/")
            self.assertEqual(status, 200)
            text = body.decode("utf-8")
            self.assertEqual(text.count('src=/pin.js'), 1)
            tag = text[text.index('src=/pin.js') - 60:text.index('src=/pin.js')]
            self.assertRegex(tag, r'<script nonce="[A-Za-z0-9_-]{16,}" $')
            self.assertLess(text.index('src=/pin.js'), text.rindex("</body>"))
            status, script = get(server, "/pin.js")
            self.assertEqual(status, 200)
            self.assertEqual(script, (Path(web.__file__).with_name("web_static") / "pin.js").read_bytes())
            self.assertIn(b"SPDX-License-Identifier: GPL-3.0-or-later", script)

    def test_the_tag_is_added_once_and_only_with_a_usable_nonce(self):
        body = b"<html><body><p>x</p></body></html>"
        with mock.patch.dict(os.environ, {"OPOS_LOCK": "1"}):
            once = lockpin.inject_bytes(body, "n" * 22)
            self.assertEqual(once, b'<html><body><p>x</p><script nonce="' + b"n" * 22 + b'" src=/pin.js></script></body></html>')
            self.assertEqual(lockpin.inject_bytes(once, "n" * 22), once)
            for nonce in ("", None, '"><script>', "n n", "n" * 129):
                self.assertEqual(lockpin.inject_bytes(body, nonce), body, nonce)
            self.assertEqual(lockpin.inject_bytes("text", "n" * 22), "text")


if __name__ == "__main__":
    unittest.main()
