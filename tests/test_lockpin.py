# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock PIN: the hash, the growing wait, and the server refusing writes while locked."""
import json
import shutil
import subprocess
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
        self.assertIn("oposLockHeld", page)
        self.assertNotIn('type="password"', page)

    def test_digit_pad_posts_the_pin_and_does_not_hide_the_screen(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        script = lockpin._JS.read_text(encoding="utf-8")
        harness = r"""
const vm = require('vm');
const script = process.argv[1];
const fs = require('fs');
class Element {
  constructor(tag){ this.tag = tag; this.id = ''; this.hidden = false; this.children = []; this.attrs = {}; this.text = ''; this.style = {}; this.onclick = null; this.listeners = []; }
  setAttribute(k, v){ this.attrs[k] = v; }
  appendChild(c){ this.children.push(c); c.parent = this; return c; }
  insertBefore(c, before){ const i = before ? this.children.indexOf(before) : -1; if (i < 0) return this.appendChild(c); this.children.splice(i, 0, c); c.parent = this; return c; }
  addEventListener(type, fn){ this.listeners.push(fn); }
  get textContent(){ return this.text; }
  set textContent(v){ this.text = String(v); }
}
function make(tag){ return new Element(tag); }
const lock = make('div'); lock.id = 'lock';
const note = make('div'); note.id = 'lock-notice'; note.text = 'Ana called'; lock.appendChild(note);
const unlock = make('button'); unlock.id = 'unlock'; unlock.text = 'Unlock'; lock.appendChild(unlock);
function find(el, id){ if (el.id === id) return el; for (const child of el.children) { const hit = find(child, id); if (hit) return hit; } return null; }
let opened = false;
const c = {
  document: {
    querySelector(){ return { content: 'tok' }; },
    getElementById(id){ return find(lock, id); },
    createElement: make
  },
  fetch(url, opts){
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    c.posts.push(body);
    const unlocked = !!(body && body.pin === '1234');
    const payload = unlocked
      ? { ok: true, locked: false, has_pin: true, enforced: true, lock: { messages: 0, missed: 0 } }
      : { locked: true, has_pin: true, enforced: true, wait_s: 0, lock: { messages: 2, missed: 1, alarm: 'Alarm 07:00' } };
    return Promise.resolve({ ok: !body || unlocked, json: () => Promise.resolve(payload) });
  },
  setInterval(){},
  posts: [],
  oposUI: { unlock(){ opened = true; } }
};
c.window = c;
const context = vm.createContext(c);
vm.runInContext(fs.readFileSync(script, 'utf8'), context, { filename: script });
(async function () {
  await new Promise(r => setTimeout(r, 20));
  const pad = find(lock, 'lock-pad');
  const labels = pad.children.filter(e => e.tag === 'button').map(e => e.text);
  const out = { labels, notice: note.text, held: c.oposLockHeld, opened };
  function press(label){ pad.children.find(e => e.text === label).onclick(); }
  press('1'); press('2'); press('3'); press('4');
  out.entered = find(lock, 'lock-pin').text;
  press('Unlock');
  await new Promise(r => setTimeout(r, 20));
  out.posts = c.posts;
  out.openedAfter = opened;
  out.heldAfter = c.oposLockHeld;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
        result = subprocess.run([node, "-e", harness, str(lockpin._JS)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["labels"], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "Clear", "0", "Unlock"])
        self.assertEqual(out["notice"], "2 new messages. 1 missed call. Alarm 07:00")
        self.assertNotIn("Ana", out["notice"])
        self.assertTrue(out["held"])
        self.assertFalse(out["opened"])
        self.assertEqual(out["entered"], "Entered 4")
        self.assertEqual(out["posts"][0], None)
        self.assertEqual(out["posts"][-1], {"action": "unlock", "pin": "1234"})
        self.assertTrue(out["openedAfter"])
        self.assertFalse(out["heldAfter"])

    def test_the_lock_notice_stays_empty_until_the_lock_response(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        harness = r"""
const vm = require('vm'), fs = require('fs');
class Element {
  constructor(){ this.id=''; this.text=''; this.children=[]; }
  get textContent(){ return this.text; }
  set textContent(v){ this.text = String(v); }
}
const note = new Element(); note.id = 'lock-notice'; note.text = 'Ana called';
function find(id){ return id === 'lock-notice' ? note : null; }
let mode = 'wait';
const c = {
  document: { getElementById: find, querySelector(){ return { content: 'tok' }; } },
  fetch(){
    if (mode === 'fail') return Promise.reject(new Error('down'));
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ enforced: true, locked: true, lock: { messages: 1, missed: 0 } }) });
  },
  setInterval(){}
};
c.window = c;
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), vm.createContext(c), { filename: 'lock.js' });
const before = note.text;
(async function () {
  await new Promise(r => setTimeout(r, 20));
  const after = note.text;
  mode = 'fail';
  note.text = 'Ana called';
  c.window.oposLockCounts = true;
  vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), vm.createContext(c), { filename: 'lock.js' });
  await new Promise(r => setTimeout(r, 20));
  console.log(JSON.stringify({ before, after, failed: note.text, flag: c.oposLockCounts }));
})().catch(e => { console.error(e); process.exit(1); });
"""
        result = subprocess.run([node, "-e", harness, str(lockpin._JS)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["before"], "")
        self.assertNotEqual(out["after"], "Ana called")
        self.assertNotIn("Ana", out["after"])
        self.assertNotEqual(out["failed"], "Ana called")
        self.assertIn(out["failed"], ("", "Try again."))
        self.assertTrue(out["flag"])

    def test_a_failed_lock_read_keeps_the_cover_and_a_hidden_pad_stays_hidden(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        harness = r"""
const vm = require('vm'), fs = require('fs');
class Element {
  constructor(tag){ this.tag = tag; this.id = ''; this.hidden = false; this.children = []; this.text = ''; this.style = {}; this.onclick = null; }
  setAttribute(){}
  appendChild(c){ this.children.push(c); return c; }
  insertBefore(c, before){ const i = before ? this.children.indexOf(before) : -1; if (i < 0) return this.appendChild(c); this.children.splice(i, 0, c); return c; }
  get textContent(){ return this.text; }
  set textContent(v){ this.text = String(v); }
}
function make(tag){ return new Element(tag); }
const lock = make('div'); lock.id = 'lock';
const note = make('div'); note.id = 'lock-notice'; note.text = 'Ana called'; lock.appendChild(note);
const unlock = make('button'); unlock.id = 'unlock'; lock.appendChild(unlock);
function find(el, id){ if (el.id === id) return el; for (const child of el.children) { const hit = find(child, id); if (hit) return hit; } return null; }
let opened = false, mode = 'fail';
const c = {
  document: { querySelector(){ return { content: 'tok' }; }, getElementById(id){ return find(lock, id); }, createElement: make },
  fetch(){
    if (mode === 'fail') return Promise.reject(new Error('down'));
    if (mode === 'bad') return Promise.resolve({ ok: false, json: () => Promise.resolve({ error: 'locked' }) });
    const locked = mode === 'locked';
    const payload = { locked: locked, has_pin: locked, enforced: true, wait_s: 0, lock: { messages: 0, missed: 0 } };
    return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
  },
  setInterval(fn){ c.again = fn; },
  oposUI: { unlock(){ opened = true; } }
};
c.window = c;
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), vm.createContext(c), { filename: 'lock.js' });
(async function () {
  await new Promise(r => setTimeout(r, 20));
  const failed = { held: c.oposLockHeld, opened, text: note.text };
  mode = 'bad';
  c.again();
  await new Promise(r => setTimeout(r, 20));
  const bad = { held: c.oposLockHeld, opened };
  mode = 'locked';
  c.again();
  await new Promise(r => setTimeout(r, 20));
  const pad = find(lock, 'lock-pad');
  const shown = { display: pad.style.display, hidden: pad.hidden };
  mode = 'open';
  c.again();
  await new Promise(r => setTimeout(r, 20));
  console.log(JSON.stringify({ failed, bad, shown, hidden: pad.hidden, display: pad.style.display, held: c.oposLockHeld }));
})().catch(e => { console.error(e); process.exit(1); });
"""
        result = subprocess.run([node, "-e", harness, str(lockpin._JS)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertTrue(out["failed"]["held"])
        self.assertFalse(out["failed"]["opened"])
        self.assertEqual(out["failed"]["text"], "Try again.")
        self.assertNotIn("Ana", out["failed"]["text"])
        self.assertTrue(out["bad"]["held"])
        self.assertFalse(out["bad"]["opened"])
        self.assertEqual(out["shown"]["display"], "grid")
        self.assertFalse(out["shown"]["hidden"])
        self.assertEqual(out["display"], "none")
        self.assertTrue(out["hidden"])
        self.assertFalse(out["held"])


if __name__ == "__main__":
    unittest.main()
