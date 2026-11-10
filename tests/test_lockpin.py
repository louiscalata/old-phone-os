# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock PIN: the hash, the growing wait, and the server refusing writes while locked."""
import json
import tempfile
import unittest

from opos import lockpin, web
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
            ok, status = lockpin.unlock("", 0)
            self.assertFalse(ok)
            self.assertFalse(status["locked"])
            lockpin._store().write_json("lock.json", {"locked": True, "failures": 0, "wait_until": 0})
            self.assertFalse(lockpin.is_locked())
            ok, again = lockpin.unlock("", 0)
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
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234", "now": 50}, token)
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


if __name__ == "__main__":
    unittest.main()
