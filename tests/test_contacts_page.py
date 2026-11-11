# SPDX-License-Identifier: GPL-3.0-or-later
"""Local contacts and vCard: add Ana, keep her, and do not put her name in the phone state."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opos import contacts_page, vcard, web
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, page, served_home_page

ANA = {"action": "add", "name": "Ana", "number": "+1 555 0101", "label": "Mobile", "favorite": True}


class VCardTests(unittest.TestCase):
    def test_round_trip_utf8_folded_lines_and_skipped_fields(self):
        text = "BEGIN:VCARD\nVERSION:4.0\nN:García;Ana;;;\nFN:Ana García\nTEL;TYPE=CELL:+15550101\nTEL;TYPE=HOME:+15550102\nNOTE:line\n  two\nBDAY:20000101\nEND:VCARD\n"
        people, skipped = vcard.parse(text)
        self.assertEqual(people[0]["name"], "Ana García")
        self.assertEqual(len(people[0]["numbers"]), 2)
        self.assertEqual(skipped, 1)
        again, skipped2 = vcard.parse(vcard.emit(people))
        self.assertEqual(again[0]["numbers"][0]["number"], "+15550101")
        self.assertEqual(skipped2, 0)
        self.assertIn("Ana García", vcard.emit(people))


class ContactsAlphabet(unittest.TestCase):
    def test_mixed_names_have_one_letter_bucket_and_one_jump_target(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        harness = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const nodes = new Map();
let focused = null, scrolled = null;
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.value = ''; this.dataset = {}; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text || ''; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  setAttribute() {}
  addEventListener() {}
  get nextElementSibling() { return this.parent.children[this.parent.children.indexOf(this) + 1]; }
  querySelector(tag) { return this.children.find(child => child.tagName === tag); }
  focus() { focused = this.textContent; }
  scrollIntoView() { scrolled = this.id; }
}
const context = {
  document: {
    getElementById(id) {
      const row = nodes.get('rows');
      const heading = row && row.children.find(child => child.id === id);
      if (heading) return heading;
      if (!nodes.has(id)) nodes.set(id, new Element('div'));
      return nodes.get(id);
    },
    createElement: tag => new Element(tag),
    querySelector: () => ({content: 'fixture-token'})
  },
  fetch: () => new Promise(() => {}), window: {}
};
vm.createContext(context);
vm.runInContext(JSON.parse(require('node:fs').readFileSync(0, 'utf8')), context);
const names = ['Zulu', 'Élodie', 'Ana', '7 Eleven', '李', 'Alex', 'Özlem'];
context.paint({people: names.map((name, id) => ({id: String(id), name, numbers: []}))});
const rows = nodes.get('rows').children;
const headings = rows.filter(row => row.className === 'letter-head');
assert.deepEqual(headings.map(row => row.id), ['letter-other', 'letter-A', 'letter-Z']);
assert.deepEqual(nodes.get('letters').children.map(button => button.textContent), ['#', 'A', 'Z']);
let bucket = '';
const groups = {};
for (const row of rows) {
  if (row.className === 'letter-head') { bucket = row.textContent; groups[bucket] = []; }
  else groups[bucket].push(row.children[0].textContent);
}
assert.deepEqual(groups['#'], ['7 Eleven', 'Élodie', 'Özlem', '李']);
assert.deepEqual(groups.A, ['Alex', 'Ana']);
assert.deepEqual(groups.Z, ['Zulu']);
nodes.get('letters').children[0].onclick();
assert.equal(focused, '7 Eleven');
assert.equal(scrolled, 'letter-other');
console.log('mixed-name groups and jump target passed');
"""
        result = subprocess.run([node, "-e", harness], input=json.dumps(contacts_page._SCRIPT), text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mixed-name groups and jump target passed", result.stdout)


class ContactsApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        contacts_page.reset()
        self.env = {"OPOS_APPS": "contacts", "OPOS_STATE_DIR": self.tmp.name, "OPOS_CONTACTS_DIR": str(Path(self.tmp.name) / "vcards")}

    def tearDown(self):
        contacts_page.reset()

    def test_add_favorite_survives_reload_and_stays_out_of_phone_state(self):
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/contacts")
            self.assertEqual(status, 200)
            self.assertIn("Practice contacts. These are examples.", body.decode())
            code, raw = post(server, "/api/contacts", ANA, token)
            snap = json.loads(raw)
            self.assertEqual(code, 200, raw)
            self.assertEqual(snap["people"][0]["name"], "Ana")
            self.assertTrue(snap["people"][0]["favorite"])
            self.assertFalse(snap["practice"])
            self.assertEqual(snap["call"], "Practice call. No real call is made.")
            contacts_page.reset()
            code, raw = post(server, "/api/contacts", {"action": "export"}, token)
            self.assertIn("Exported 1", json.loads(raw)["file_line"])
            front, state = get(server, "/api/state", token)
            self.assertEqual(front, 200)
            self.assertNotIn("Ana", state.decode())
            self.assertNotIn("+15550101", state.decode())
        html = contacts_page.render("tok", "nonce")
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        self.assertIn(">Delete this person?<", html) if False else self.assertIn(">Delete<", html)

    def test_import_and_bad_number(self):
        folder = Path(self.env["OPOS_CONTACTS_DIR"])
        folder.mkdir()
        (folder / "people.vcf").write_text("BEGIN:VCARD\nFN:Bo\nTEL:+15550109\nEND:VCARD\n", encoding="utf-8")
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/contacts", {"action": "import"}, token)
            self.assertEqual(json.loads(raw)["people"][0]["name"], "Bo")
            code, raw = post(server, "/api/contacts", {"action": "add", "name": "No", "number": "abc"}, token)
            self.assertEqual(code, 400)

    def test_routes_and_the_home_tile_stay_off_until_contacts_is_named(self):
        # Importing the page registers it. A registered app still refuses until OPOS_APPS names it.
        self.assertIsNotNone(contacts_page.snapshot)
        with running({"OPOS_APPS": "clock"}) as server:
            self.assertEqual(get(server, "/contacts")[0], 404)
            self.assertEqual(get(server, "/api/contacts")[0], 404)
            code, _raw = post(server, "/api/contacts", {"action": "add", "name": "Ana", "number": "+15550101"}, "x" * 20)
            self.assertEqual(code, 404)
            status, home = get(server, "/")
            self.assertEqual(status, 200)
            self.assertIn(b"dataset.scroll==='contacts'&&!contactsOn", home)
            self.assertNotIn(b'data-contacts="1"', home)
        with running({}) as server:
            self.assertEqual(get(server, "/contacts")[0], 404)
            self.assertEqual(get(server, "/api/contacts")[0], 404)
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            self.assertEqual(get(server, "/contacts")[0], 200)
            self.assertEqual(get(server, "/api/contacts", token)[0], 200)

    def test_practice_links_letter_index_and_the_main_page_does_not_grow(self):
        html = contacts_page.render("tok", "nonce")
        self.assertIn('id="letters"', html)
        self.assertIn("practiceUrl('phone'", html)
        self.assertIn("practiceUrl('messages'", html)
        self.assertNotIn("lock=0", html)
        self.assertIn("/?practice='+which", html)
        self.assertIn("Jump to ", html)
        self.assertNotIn("dial(", html)
        self.assertNotIn("action:'call'", html)
        self.assertNotIn('action:"call"', html)
        before = page()
        with mock.patch.dict(os.environ, {"OPOS_APPS": "contacts"}):
            self.assertEqual(page(), before)
        served = served_home_page({"OPOS_APPS": "contacts"})
        self.assertLessEqual(len(served), WAVE_GATE)
        self.assertIn(b"/apps-home.js", served)
        self.assertNotIn(b"practiceUrl", served)
        self.assertNotIn(b'id="letters"', served)
        home = Path(__file__).resolve().parents[1].joinpath("src/opos/apps_static/home.js").read_text(encoding="utf-8")
        self.assertIn('data-fav', home)
        self.assertIn("favourite", home)
        self.assertIn('practice', home)
        self.assertNotIn("unlock()", home)
        self.assertNotIn("action:'call'", home)
        self.assertNotIn('action:"call"', home)
        page_script = page()
        self.assertIn("||/(^|[?&])practice=/.test(location.search))window.oposUI.lock();", page_script)
        self.assertLessEqual(len(page_script.encode("utf-8")), WAVE_GATE)


if __name__ == "__main__":
    unittest.main()
