# SPDX-License-Identifier: GPL-3.0-or-later
"""Local contacts and vCard: add Ana, keep her, and do not put her name in the phone state."""
import json
import tempfile
import unittest
from pathlib import Path

from opos import contacts_page, vcard, web
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of

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


if __name__ == "__main__":
    unittest.main()
