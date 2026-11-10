# SPDX-License-Identifier: GPL-3.0-or-later
"""Notes: create, save unicode, refuse a note over 64 KB, and read it back from the file."""
import json
import os
import tempfile
import unittest

from opos import notes_page
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of


class Notes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        notes_page.reset()
        self.env = {"OPOS_APPS": "notes", "OPOS_STATE_DIR": self.tmp.name}

    def tearDown(self):
        notes_page.reset()

    def test_save_reload_and_the_size_cap(self):
        html = notes_page.render("t", "n")
        self.assertIn(">New note<", html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/notes", {"action": "add"}, token)
            ident = json.loads(raw)["id"]
            code, raw = post(server, "/api/notes", {"action": "save", "id": ident, "text": "Café\nsecond"}, token)
            self.assertEqual(code, 200, raw)
            self.assertTrue(json.loads(raw)["saved"].startswith("Saved "))
            notes_page.reset()
            code, raw = post(server, "/api/notes", {"action": "open", "id": ident}, token)
            self.assertEqual(json.loads(raw)["text"], "Café\nsecond")
            code, obj = notes_page.post({"action": "save", "id": ident, "text": "x" * (64 * 1024 + 1)})
            self.assertEqual(code, 400)
            self.assertEqual(obj["error"], "size")
            status, body = get(server, "/notes")
            self.assertEqual(status, 200)
            self.assertIn(b"New note", body)

    def test_delete_confirms_on_the_page_and_removes_the_file(self):
        html = notes_page.render("t", "n")
        self.assertIn(">Delete<", html)
        self.assertIn("Delete this note?", html)
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/notes", {"action": "add"}, token)
            ident = json.loads(raw)["id"]
            post(server, "/api/notes", {"action": "save", "id": ident, "text": "Cafe note"}, token)
            code, raw = post(server, "/api/notes", {"action": "add"}, token)
            other = json.loads(raw)["id"]
            path = os.path.join(self.tmp.name, "note-%s.txt" % ident)
            self.assertTrue(os.path.isfile(path))
            code, raw = post(server, "/api/notes", {"action": "delete", "id": ident}, token)
            self.assertEqual(code, 200, raw)
            self.assertEqual([row["id"] for row in json.loads(raw)["notes"]], [other])
            self.assertFalse(os.path.isfile(path))
            self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "note-%s.txt" % other)))
            code, raw = post(server, "/api/notes", {"action": "delete", "id": ident}, token)
            self.assertEqual(code, 400)
            self.assertEqual(json.loads(raw)["error"], "id")
            code, raw = post(server, "/api/notes", {"action": "delete", "id": "../secret"}, token)
            self.assertEqual(code, 400)
            self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "note-%s.txt" % other)))


if __name__ == "__main__":
    unittest.main()
