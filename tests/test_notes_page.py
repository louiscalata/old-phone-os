# SPDX-License-Identifier: GPL-3.0-or-later
"""Notes: create, save unicode, refuse a note over 64 KB, and read it back from the file."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

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


class NotesDeleteFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"OPOS_STATE_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        notes_page.reset()
        self.addCleanup(notes_page.reset)
        _, note = notes_page.post({"action": "add"})
        self.ident = note["id"]
        notes_page.post({"action": "save", "id": self.ident, "text": "Keep this text"})
        self.path = Path(self.tmp.name) / ("note-%s.txt" % self.ident)

    def test_unlink_error_keeps_note_available_and_retryable(self):
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                notes_page.post({"action": "delete", "id": self.ident})
        self.assertEqual(notes_page.snapshot()["notes"][0]["id"], self.ident)
        self.assertEqual(notes_page.post({"action": "open", "id": self.ident})[1]["text"], "Keep this text")
        self.assertEqual(notes_page.post({"action": "delete", "id": self.ident})[0], 200)
        self.assertFalse(self.path.exists())

    def test_index_write_error_is_not_success_and_can_be_reconciled(self):
        with mock.patch.object(notes_page, "_write_index", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                notes_page.post({"action": "delete", "id": self.ident})
        self.assertEqual(notes_page.snapshot()["notes"][0]["id"], self.ident)
        # A missing file is already deleted; retry must still remove the index row.
        self.assertEqual(notes_page.post({"action": "delete", "id": self.ident})[0], 200)
        self.assertEqual(notes_page.snapshot()["notes"], [])
        self.assertFalse(self.path.exists())

    def test_missing_file_delete_reconciles_index(self):
        self.path.unlink()
        self.assertEqual(notes_page.post({"action": "delete", "id": self.ident})[0], 200)
        self.assertEqual(notes_page.snapshot()["notes"], [])

    def test_successful_delete_preserves_other_note_and_refuses_unknown_id(self):
        _, other = notes_page.post({"action": "add"})
        status, body = notes_page.post({"action": "delete", "id": self.ident})
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in body["notes"]], [other["id"]])
        self.assertFalse(self.path.exists())
        for ident in (self.ident, "../secret"):
            self.assertEqual(notes_page.post({"action": "delete", "id": ident}), (400, {"error": "id"}))
        self.assertTrue((Path(self.tmp.name) / ("note-%s.txt" % other["id"])).is_file())

    def test_save_read_and_size_cap_survive_delete_changes(self):
        payload = {"action": "save", "id": self.ident, "text": "Café\nsecond"}
        self.assertEqual(notes_page.post(payload)[0], 200)
        notes_page.reset()
        self.assertEqual(notes_page.post({"action": "open", "id": self.ident})[1]["text"], payload["text"])
        self.assertEqual(notes_page.post(dict(payload, text="x" * (64 * 1024 + 1))), (400, {"error": "size"}))

    def test_actual_script_deferred_delete_and_save_ordering(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        result = subprocess.run([node, "--test", str(Path(__file__).with_name("notes_delete_test.js"))],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
