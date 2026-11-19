# SPDX-License-Identifier: GPL-3.0-or-later
"""An app route answers only when OPOS_APPS names that app, and the home page gains one tag for the whole set."""

import json
import os
import re
import tempfile
import unittest
from unittest import mock

from opos import apps, apps_page, inbox, lockpin, web
from tests.test_apps_shell import get, post, running
from tests.test_uxp30_budget import SWITCHES, WAVE_GATE, served_home_page

# Each app's page and the token-protected reads it has. Calculator has no API.
APPS = {
    "clock": ("/clock", "/api/clock"),
    "contacts": ("/contacts", "/api/contacts"),
    "notes": ("/notes", "/api/notes"),
    "calc": ("/calc",),
    "calls": ("/calls", "/api/calls"),
    "convert": ("/convert", "/api/convert"),
    "notices": ("/notices", "/api/notices"),
}
ROUTES = ("/apps",) + tuple(path for paths in APPS.values() for path in paths)
NAMED = ",".join(APPS)
DUO = "clock,contacts,notes,calc,calls,convert,notices"


class AppRoutes(unittest.TestCase):
    def test_every_registered_module_is_in_the_gate_table(self):
        self.assertEqual(set(APPS), set(apps.MODULES), "a new module needs a row in APPS")

    def test_each_module_registers_the_expected_path(self):
        with mock.patch.dict(os.environ, {"OPOS_APPS": NAMED}):
            enabled = apps.enabled()
        by_id = {app.id: app for app in enabled}
        for name in apps.MODULES:
            with self.subTest(app=name):
                self.assertIn(name, APPS)
                self.assertIn(name, by_id, "the module did not register its app")
                self.assertEqual(by_id[name].path, APPS[name][0])

    def test_the_index_has_one_link_for_each_named_app(self):
        app_paths = {paths[0] for paths in APPS.values()}

        def app_links(html):
            return [link for link in re.findall(r'href="([^"]+)"', html) if link in app_paths]

        token, nonce = "t" * 43, "n" * 22
        with mock.patch.dict(os.environ, {"OPOS_APPS": NAMED}):
            every = apps_page.index_html(token, nonce)
        links = app_links(every)
        for name, paths in APPS.items():
            with self.subTest(app=name):
                self.assertEqual(links.count(paths[0]), 1)
        self.assertEqual(len(links), len(APPS))
        with mock.patch.dict(os.environ, {"OPOS_APPS": "clock"}):
            one = apps_page.index_html(token, nonce)
        self.assertEqual(app_links(one), [APPS["clock"][0]])

    def test_the_served_home_page_has_no_app_path(self):
        """Each app reaches home through the one /apps-home.js tag, never through its own path."""
        every = served_home_page({"OPOS_APPS": NAMED})
        self.assertEqual(every.count(b"/apps-home.js"), 1)
        for name, paths in APPS.items():
            for path in paths:
                with self.subTest(app=name, path=path):
                    # The served page has no app path today. The paths live in the script and on /apps.
                    self.assertEqual(every.count(path.encode()), 0)

    def test_every_route_is_off_when_no_app_is_named(self):
        for env in ({}, {"OPOS_APPS": ""}):
            with self.subTest(env=env), running(env) as server:
                token = server.RequestHandlerClass.token
                for path in ROUTES:
                    self.assertEqual(get(server, path)[0], 404, path)
                    self.assertEqual(get(server, path, token)[0], 404, path)
                for path in ROUTES[1:]:
                    if path.startswith("/api/"):
                        self.assertEqual(post(server, path, {}, token)[0], 404, path)

    def test_each_app_answers_alone_and_the_others_stay_off(self):
        inbox.reset()
        self.addCleanup(inbox.reset)
        with tempfile.TemporaryDirectory() as state:
            for name, own in APPS.items():
                with self.subTest(app=name), running({"OPOS_APPS": name, "OPOS_STATE_DIR": state}) as server:
                    token = server.RequestHandlerClass.token
                    self.assertEqual(get(server, "/apps")[0], 200)
                    for path in own:
                        self.assertEqual(get(server, path, token)[0], 200, path)
                    for other, paths in APPS.items():
                        if other == name:
                            continue
                        for path in paths:
                            self.assertEqual(get(server, path, token)[0], 404, path)

    def test_the_page_grows_by_one_tag_for_the_whole_set(self):
        self.assertEqual(WAVE_GATE, 102000)
        token, nonce = "t" * 43, "n" * 22
        plain = web.page(token, nonce)
        with mock.patch.dict(os.environ, {"OPOS_APPS": NAMED}):
            named = web.page(token, nonce)
        self.assertEqual(plain, named, "page() does not change when apps are named")
        one = served_home_page({"OPOS_APPS": "clock"})
        duo = served_home_page({"OPOS_APPS": DUO})
        every = served_home_page({"OPOS_APPS": NAMED})
        for body in (one, duo, every):
            self.assertEqual(body.count(b"/apps-home.js"), 1)
        self.assertEqual(len(duo), len(one))
        self.assertEqual(len(every), len(one))
        switched = served_home_page(dict(SWITCHES, OPOS_APPS=NAMED))
        self.assertLessEqual(len(switched), WAVE_GATE)
        self.assertEqual(switched.count(b"/apps-home.js"), 1)

    def test_the_lock_and_every_app_stay_under_the_gate(self):
        """The PIN pad's tag and the apps' tag together: naming every app adds nothing over naming one."""
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        one = served_home_page(dict(SWITCHES, OPOS_LOCK="1", OPOS_APPS="clock"))
        every = served_home_page(dict(SWITCHES, OPOS_LOCK="1", OPOS_APPS=NAMED))
        self.assertEqual(len(every), len(one))
        self.assertLessEqual(len(every), WAVE_GATE)
        self.assertEqual(every.count(b"/apps-home.js"), 1)
        self.assertEqual(every.count(b"/pin.js"), 1)


class LockedConverter(unittest.TestCase):
    """With the PIN lock on and the phone locked, the converter gives the locked body and converts nothing."""

    def test_locked_converter_reads_the_locked_body_and_refuses_a_conversion(self):
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        with (
            tempfile.TemporaryDirectory() as tmp,
            running(
                {"OPOS_LOCK": "1", "OPOS_STATE_DIR": tmp, "OPOS_CONFIG_DIR": tmp, "OPOS_APPS": "convert"}
            ) as server,
        ):
            token = server.RequestHandlerClass.token
            ask = {"kind": "length", "value": "1", "src": "km", "dst": "m"}
            for action in ({"action": "set", "pin": "1234"}, {"action": "unlock", "pin": "1234"}):
                code, raw = post(server, "/api/lock", action, token)
                self.assertEqual(code, 200, raw)
            code, raw = post(server, "/api/convert", ask, token)
            self.assertEqual(code, 200, raw)
            self.assertIn("text", json.loads(raw))
            code, raw = post(server, "/api/lock", {"action": "lock"}, token)
            self.assertEqual(code, 200, raw)
            status, raw = get(server, "/api/convert", token)
            self.assertEqual(status, 200, raw)
            body = json.loads(raw)
            self.assertIs(body.get("locked"), True, raw)
            self.assertNotIn("kinds", body)
            code, raw = post(server, "/api/convert", ask, token)
            self.assertEqual(code, 403, raw)
            self.assertEqual(json.loads(raw)["error"], "locked")


if __name__ == "__main__":
    unittest.main()
