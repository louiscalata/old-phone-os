# SPDX-License-Identifier: GPL-3.0-or-later
"""An app route answers only when OPOS_APPS names that app, and the home page gains one tag for the whole set."""
import os
import unittest
from unittest import mock

from opos import web
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
}
ROUTES = ("/apps",) + tuple(path for paths in APPS.values() for path in paths)
NAMED = ",".join(APPS)
DUO = "clock,contacts,notes,calc,calls,convert"


class AppRoutes(unittest.TestCase):
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
        for name, own in APPS.items():
            with self.subTest(app=name), running({"OPOS_APPS": name}) as server:
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


if __name__ == "__main__":
    unittest.main()
