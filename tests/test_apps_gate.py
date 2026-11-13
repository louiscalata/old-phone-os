# SPDX-License-Identifier: GPL-3.0-or-later
"""An app route answers only when its switch is on, and the home page gains one link for the set."""
import os
import unittest
from unittest import mock

from opos import web
from tests.test_apps_shell import get, running
from tests.test_uxp30_budget import SWITCHES, WAVE_GATE, served_home_page

ROUTES = ("/apps", "/clock", "/contacts", "/notes", "/calc", "/api/clock")
NAMED = "clock,contacts,notes,calc"


class AppRoutes(unittest.TestCase):
    def test_every_route_is_off_when_its_switch_is_off(self):
        with running({}) as server:
            for path in ROUTES:
                status, _body = get(server, path)
                self.assertEqual(status, 404, path)
        with running({"OPOS_APPS": "clock"}) as server:
            self.assertEqual(get(server, "/clock")[0], 200)
            self.assertEqual(get(server, "/apps")[0], 200)
            for path in ("/contacts", "/notes", "/calc"):
                self.assertEqual(get(server, path)[0], 404, path)

    def test_page_grows_by_at_most_one_link_for_the_whole_set(self):
        self.assertEqual(WAVE_GATE, 102000)
        token, nonce = "t" * 43, "n" * 22
        plain = web.page(token, nonce)
        with mock.patch.dict(os.environ, {"OPOS_APPS": NAMED}):
            named = web.page(token, nonce)
        self.assertEqual(plain, named)
        one = served_home_page({"OPOS_APPS": "clock"})
        four = served_home_page({"OPOS_APPS": NAMED})
        self.assertEqual(one.count(b"/apps-home.js"), 1)
        self.assertEqual(four.count(b"/apps-home.js"), 1)
        self.assertEqual(len(four), len(one))
        every = served_home_page(dict(SWITCHES, OPOS_APPS=NAMED))
        self.assertLessEqual(len(every), WAVE_GATE)
        self.assertEqual(every.count(b"/apps-home.js"), 1)
