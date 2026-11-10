# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock PIN: the hash, the growing wait, and the server refusing writes while locked."""
import json
import tempfile
import unittest
from types import SimpleNamespace

from opos import lockpin, web
from opos.essentials import Message
from tests.test_apps_shell import get, post, running


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
        with unittest.mock.patch.dict(__import__("os").environ, {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name}):
            lockpin.set_clock(lambda: 1000)
            lockpin.set_pin("1234")
            for n in range(4):
                ok, status = lockpin.unlock("0000")
                self.assertFalse(ok)
                self.assertEqual(status["wait_s"], 0)
            ok, status = lockpin.unlock("0000")
            self.assertFalse(ok)
            self.assertGreaterEqual(status["wait_s"], 30)
            ok, early = lockpin.unlock("1234")
            self.assertFalse(ok)
            self.assertGreater(early["wait_s"], 0)
            lockpin.set_clock(lambda: 1031)
            ok, later = lockpin.unlock("1234")
            self.assertTrue(ok)
            self.assertFalse(later["locked"])

    def test_a_client_clock_cannot_skip_the_wait(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        env = {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name}
        lockpin.set_clock(lambda: 1000)
        with running(env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/lock", {"action": "set", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            for _ in range(5):
                code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "0000", "now": 10 ** 12}, token)
                self.assertEqual(code, 403, raw)
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234", "now": 10 ** 12}, token)
            self.assertEqual(code, 403, raw)
            self.assertGreater(json.loads(raw)["wait_s"], 0)
            lockpin.set_clock(lambda: 1031)
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234", "now": 0}, token)
            self.assertEqual(code, 200, raw)
            self.assertFalse(json.loads(raw)["locked"])

    def test_a_poisoned_wait_is_clamped_to_the_server_backoff(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        with unittest.mock.patch.dict(__import__("os").environ, {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name}):
            lockpin.set_clock(lambda: 1000)
            lockpin.set_pin("1234")
            data = lockpin._read()
            data["failures"] = 5
            data["locked"] = True
            data["wait_until"] = 10 ** 12
            lockpin._write(data)
            status = lockpin.status()
            self.assertLessEqual(status["wait_s"], 30)
            self.assertGreater(status["wait_s"], 0)
            self.assertLessEqual(lockpin._read()["wait_until"], 1030)
            ok, early = lockpin.unlock("1234")
            self.assertFalse(ok)
            lockpin.set_clock(lambda: 1031)
            ok, later = lockpin.unlock("1234")
            self.assertTrue(ok)
            self.assertFalse(later["locked"])
            lockpin.set_pin("1234")
            data = lockpin._read()
            data["failures"] = 0
            data["locked"] = True
            data["wait_until"] = 10 ** 12
            lockpin._write(data)
            ok, opened = lockpin.unlock("1234")
            self.assertTrue(ok)
            self.assertFalse(opened["locked"])


class Server(unittest.TestCase):
    def test_no_saved_pin_is_unlocked_and_empty_unlock_fails(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        env = {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name}
        with unittest.mock.patch.dict(__import__("os").environ, env):
            self.assertFalse(lockpin.is_locked())
            self.assertFalse(lockpin.status()["locked"])
            self.assertFalse(lockpin.status()["has_pin"])
            lockpin.lock()
            self.assertFalse(lockpin.is_locked())
            ok, status = lockpin.unlock("")
            self.assertFalse(ok)
            self.assertFalse(status["locked"])
            lockpin._store().write_json("lock.json", {"locked": True, "failures": 0, "wait_until": 0})
            self.assertFalse(lockpin.is_locked())
            ok, again = lockpin.unlock("")
            self.assertFalse(ok)
            self.assertFalse(again["locked"])
        with running(env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": ""}, token)
            self.assertEqual(code, 403, raw)
            self.assertFalse(json.loads(raw)["locked"])
            code, raw = post(server, "/api/lock", {"action": "lock"}, token)
            self.assertEqual(code, 200, raw)
            self.assertFalse(json.loads(raw)["locked"])
            code, raw = post(server, "/api", {"action": "call", "number": "+15550100"}, token)
            self.assertNotEqual(code, 403, raw)

    def test_set_and_skip_are_refused_while_locked(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        env = {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name}
        with running(env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/lock", {"action": "set", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            saved = lockpin._read()["record"]["hash"]
            self.assertTrue(lockpin.blocks("set", "/api/lock"))
            self.assertTrue(lockpin.blocks("skip", "/api/lock"))
            self.assertFalse(lockpin.blocks("unlock", "/api/lock"))
            self.assertFalse(lockpin.blocks("lock", "/api/lock"))
            code, raw = post(server, "/api/lock", {"action": "skip"}, token)
            self.assertEqual(code, 403, raw)
            self.assertEqual(json.loads(raw)["error"], "locked")
            self.assertEqual(lockpin._read()["record"]["hash"], saved)
            self.assertTrue(lockpin.is_locked())
            code, raw = post(server, "/api/lock", {"action": "set", "pin": "5678"}, token)
            self.assertEqual(code, 403, raw)
            self.assertTrue(lockpin.verify("1234", lockpin._read()["record"]))
            self.assertFalse(lockpin.verify("5678", lockpin._read()["record"]))
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234", "now": 10 ** 12}, token)
            self.assertEqual(code, 200, raw)
            self.assertFalse(json.loads(raw)["locked"])
            code, raw = post(server, "/api/lock", {"action": "skip"}, token)
            self.assertEqual(code, 200, raw)
            self.assertFalse(lockpin.has_pin())
            self.assertFalse(lockpin.is_locked())

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

    def test_counts_follow_the_phone_log(self):
        log = [
            Message("+15550100", "hello from ana", False),
            Message("+15550100", "sent", True),
            SimpleNamespace(kind="missed"),
        ]
        got = lockpin.counts(log)
        self.assertEqual(got["messages"], 1)
        self.assertEqual(got["missed"], 1)
        dumped = json.dumps(got)
        self.assertNotIn("hello", dumped)
        self.assertNotIn("15550100", dumped)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        env = {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name}
        with running(env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api", {"action": "receive", "number": "+15550100", "text": "hello"}, token)
            self.assertEqual(code, 200, raw)
            code, raw = post(server, "/api/lock", {"action": "set", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            status, body = get(server, "/api/lock", token)
            self.assertEqual(status, 200, body)
            parsed = json.loads(body)
            self.assertEqual(parsed["lock"]["messages"], 1)
            self.assertEqual(parsed["lock"]["missed"], 0)
            self.assertNotIn(b"hello", body)
            self.assertNotIn(b"Mom", body)
            status, body = get(server, "/api/state", token)
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)["lock"]["messages"], 1)
            self.assertNotIn(b"hello", body)
            self.assertNotIn(b"Mom", body)

    def test_locked_app_gets_are_redacted(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        env = {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp.name, "OPOS_APPS": "contacts,notes,clock,calc"}
        with running(env) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/api/contacts", token)
            self.assertEqual(status, 200, body)
            self.assertIn(b"Mom", body)
            code, raw = post(server, "/api/lock", {"action": "set", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            for path in ("/api/contacts", "/api/notes", "/api/clock", "/api/calc"):
                status, body = get(server, path, token)
                self.assertEqual(status, 200, body)
                parsed = json.loads(body)
                self.assertTrue(parsed["locked"], path)
                self.assertNotIn("people", parsed)
                self.assertNotIn("notes", parsed)
                self.assertNotIn("alarms", parsed)
                self.assertNotIn(b"Mom", body)
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            status, body = get(server, "/api/contacts", token)
            self.assertEqual(status, 200, body)
            self.assertIn(b"Mom", body)

    def test_lock_screen_script_shows_counts_and_page_stays_under_the_gate(self):
        page = web.page()
        self.assertIn("oposLockCounts", page)
        self.assertIn('script[src="/lock.js"]', page)
        self.assertEqual(page.count('src="/lock.js"'), 1)
        script = lockpin._JS.read_text(encoding="utf-8")
        self.assertLess(script.index("window.oposLockCounts = true"), script.index("fetch("))
        self.assertLessEqual(len(page.encode("utf-8")), 102000)
        self.assertIn("new messages", script)
        self.assertNotIn("notices", script)
        same = b"<html><body></body></html>"
        self.assertIs(lockpin.inject_bytes(same, "n"), same)
        with unittest.mock.patch.dict(__import__("os").environ, {"OPOS_LOCK": "1"}):
            injected = lockpin.inject_bytes(same, "nonce")
        self.assertIn(b'src="/lock.js"', injected)
        self.assertIn(b'nonce="nonce"', injected)


if __name__ == "__main__":
    unittest.main()
