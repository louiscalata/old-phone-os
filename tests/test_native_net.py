# SPDX-License-Identifier: GPL-3.0-or-later
"""The native Network screen: its toolkit-free rules (netlogic) always, and the page itself on a stand-in toolkit.

Every network name and password here is invented and obviously fake. No test talks to a radio, a device, the network or
the core: the core is a stand-in that records what it was asked and answers when the test lets its thread run.
"""
import ast
import copy
import importlib
import io
import json
import pickle
import re
import sys
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from opos.native import logic, netlogic
from opos.native.netlogic import Secret

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "src" / "opos" / "native"
PW = "fake-pass-0000"           # an invented password, never a real one
URL = "http://opos.invalid/api"  # a reserved name: nothing is ever fetched from it


def net(ssid, signal=50, security="WPA2", active=False, known=False, **k):
    n = {"ssid": ssid, "signal": signal, "security": security, "active": active, "secure": security != "open",
         "known": known, "supported": "802.1X" not in security and "WEP" not in security}
    n.update(k)
    return n


def stand_in_toolkit(test, extra=()):
    """Import the native modules on the stand-in widgets of test_native_screens, and put them back afterwards."""
    from tests.test_native_screens import Toolkit
    names = ("gi", "gi.repository", "opos.native.screens", "opos.native.ui") + tuple(extra)
    saved = {n: sys.modules.get(n) for n in names}

    def restore():
        for n, m in saved.items():
            sys.modules.pop(n, None)
            if m is not None:
                sys.modules[n] = m
    test.addCleanup(restore)
    for n in names:
        sys.modules.pop(n, None)
    gi = Toolkit("gi")
    gi.require_version = lambda *a: None
    gi.repository = Toolkit("gi.repository")
    sys.modules["gi"], sys.modules["gi.repository"] = gi, gi.repository
    return gi


# --- the rules, with no toolkit --------------------------------------------------------------------------------------
class Rows(unittest.TestCase):
    def test_connected_first_then_by_signal(self):
        got = netlogic.rows({"networks": [net("Cafe-Test", 40), net("Home-Test", 30, active=True, known=True),
                                          net("Library-Test", 90)]})
        self.assertEqual([r["ssid"] for r in got], ["Home-Test", "Library-Test", "Cafe-Test"])
        self.assertTrue(all(r["visible"] for r in got))

    def test_one_row_per_name_keeping_the_connected_or_strongest(self):
        got = netlogic.rows({"networks": [net("Cafe-Test", 20), net("Cafe-Test", 70), net("Home-Test", 10, active=True),
                                          net("Home-Test", 95)]})
        self.assertEqual([(r["ssid"], r["signal"], r["active"]) for r in got], [("Home-Test", 10, True), ("Cafe-Test", 70, False)])

    def test_flags_are_carried_through(self):
        r, = netlogic.rows({"networks": [net("Office-Test", 60, "WPA2 802.1X", label="company or school network, not supported yet, signal 60%")]})
        self.assertEqual((r["secure"], r["known"], r["supported"]), (True, False, False))

    def test_entries_without_a_string_name_are_skipped(self):
        got = netlogic.rows({"networks": [{"ssid": None}, {"ssid": 5}, {"ssid": ""}, {"ssid": "x" * 65}, "Cafe-Test", None, {},
                                          net("Home-Test")]})
        self.assertEqual([r["ssid"] for r in got], ["Home-Test"])

    def test_saved_names_not_in_range_come_last(self):
        got = netlogic.rows({"networks": [net("Cafe-Test")], "saved": ["Cabin-Test", "Cafe-Test", 3, ""]})
        self.assertEqual([r["ssid"] for r in got], ["Cafe-Test", "Cabin-Test"])
        last = got[-1]
        self.assertEqual((last["known"], last["visible"], netlogic.detail(last)), (True, False, "saved, not in range"))

    def test_malformed_input_is_an_empty_list(self):
        for bad in (None, [], "x", 3, {"networks": "x"}, {"networks": {}}, {"networks": [], "saved": "Cafe-Test"}):
            with self.subTest(bad=bad):
                self.assertEqual(netlogic.rows(bad), [])
        self.assertEqual(netlogic.rows({}), [])
        self.assertEqual([r["ssid"] for r in netlogic.rows({"saved": ["Cabin-Test"]})], ["Cabin-Test"])

    def test_odd_signals(self):
        got = netlogic.rows({"networks": [net("A-Test", "90"), net("B-Test", True), net("C-Test", 250), net("D-Test", -5)]})
        self.assertEqual({r["ssid"]: r["signal"] for r in got}, {"A-Test": None, "B-Test": None, "C-Test": 100, "D-Test": 0})


class Detail(unittest.TestCase):
    def test_the_cores_label_is_used(self):
        self.assertEqual(netlogic.detail({"label": "saved, signal 40%", "known": False}), "saved, signal 40%")

    def test_built_when_there_is_no_label(self):
        self.assertEqual(netlogic.detail({"active": True, "signal": 70}), "connected, signal 70%")
        self.assertEqual(netlogic.detail({"known": True, "signal": 40}), "saved, signal 40%")
        self.assertEqual(netlogic.detail({"secure": False, "signal": 20}), "open network, signal 20%")
        self.assertEqual(netlogic.detail({"secure": True, "signal": 5}), "needs a password, signal 5%")
        self.assertEqual(netlogic.detail({"supported": False}), "not supported yet")
        self.assertEqual(netlogic.detail({"visible": False}), "saved, not in range")
        self.assertEqual(netlogic.detail(None), "")


class Choosing(unittest.TestCase):
    def test_known_and_open_networks_join_without_typing(self):
        self.assertTrue(netlogic.needs_password({"secure": True, "known": False}))
        self.assertFalse(netlogic.needs_password({"secure": True, "known": True}))
        self.assertFalse(netlogic.needs_password({"secure": False, "known": False}))
        self.assertTrue(netlogic.needs_password({}), "unknown security: ask")
        self.assertTrue(netlogic.needs_password(None))

    def test_a_typed_hidden_network_needs_one_unless_open_was_chosen(self):
        self.assertTrue(netlogic.needs_password({}, typed_hidden=True))
        self.assertTrue(netlogic.needs_password({"secure": True, "known": True}, typed_hidden=True))
        self.assertFalse(netlogic.needs_password({"secure": False}, typed_hidden=True))

    def test_unsupported(self):
        office = {"supported": False, "security": "WPA2 802.1X"}
        self.assertFalse(netlogic.can_join(office))
        self.assertIn("Company", netlogic.why_not(office))
        self.assertIn("WEP", netlogic.why_not({"supported": False, "security": "WEP"}))
        self.assertTrue(netlogic.why_not({"supported": False}))
        self.assertTrue(netlogic.why_not(None))
        self.assertTrue(netlogic.can_join({}))
        self.assertEqual(netlogic.why_not({}), "")
        self.assertFalse(netlogic.can_join(None))

    def test_choice(self):
        self.assertEqual(netlogic.choice(None), "idle")
        self.assertEqual(netlogic.choice({"active": True, "known": True}), "connected")
        self.assertEqual(netlogic.choice({"supported": False}), "unsupported")
        self.assertEqual(netlogic.choice({"secure": True}), "password")
        self.assertEqual(netlogic.choice({"secure": True, "known": True}), "known")
        self.assertEqual(netlogic.choice({"secure": False}), "open")


class TheTypedPassword(unittest.TestCase):
    def typed(self, text=PW):
        s = Secret()
        for ch in text:
            self.assertTrue(s.key(ch))
        return s

    def test_a_bullet_per_character(self):
        self.assertEqual(netlogic.mask("abc"), "•" * 3)
        self.assertEqual(netlogic.mask(""), "")
        self.assertEqual(netlogic.mask(None), "")
        s = self.typed()
        self.assertEqual(netlogic.mask(s), "•" * len(PW))

    def test_it_never_shows_itself(self):
        s = self.typed()
        shown = [repr(s), str(s), format(s), format(s, ">40"), "%s" % s, "%r" % (s,), f"{s}", f"{s!r}", f"{s!s}",
                 "{}".format(s), "{!r}".format(s), str([s]), str({"p": s})]
        for text in shown:
            self.assertNotIn(PW, text)
            self.assertIn(netlogic.HIDDEN, text)
        self.assertFalse(hasattr(s, "__dict__"), "no attribute holds a copy")
        self.assertNotIn(PW, " ".join(str(getattr(s, a, "")) for a in dir(s) if not a.startswith("_Secret")))
        with self.assertRaises(TypeError):
            json.dumps(s)
        self.assertEqual(s.reveal(), PW)

    def test_it_cannot_be_copied_saved_or_guessed_by_comparing(self):
        s = self.typed()
        for how in (pickle.dumps, copy.copy, copy.deepcopy):
            with self.subTest(how=how.__name__), self.assertRaises(TypeError):
                how(s)
        self.assertFalse(s == PW, "comparing with a guess says nothing")
        self.assertTrue(s == s)

    def test_keys_delete_clear_and_the_limit(self):
        s = Secret()
        self.assertFalse(s.key(None) or s.delete(), "nothing to delete")
        self.assertFalse(s.key("\n"), "Return is not part of a password")
        self.assertFalse(s.key(3) or s.key(""))
        s.key("a")
        s.key("b")
        self.assertTrue(s.delete())
        self.assertEqual((len(s), s.reveal()), (1, "a"))
        self.assertTrue(s.key(None))
        self.assertEqual(len(s), 0)
        for _ in range(200):
            s.key("x")
        self.assertEqual(len(s), netlogic.LIMIT)
        self.assertEqual(netlogic.LIMIT, 64, "the longest Wi-Fi password: 64 hex digits")
        self.assertFalse(s.key("y"))
        s.clear()
        self.assertEqual((len(s), s.reveal(), bool(s)), (0, "", False))

    def test_a_reply_that_quotes_it_is_hidden(self):
        got = netlogic.scrub({"ok": False, "error": "nmcli said %s was wrong" % PW, "message": "kept", "n": 3}, PW)
        self.assertEqual(got, {"ok": False, "error": netlogic.HIDDEN, "message": "kept", "n": 3})
        same = {"ok": True, "message": "Connected to Cafe-Test."}
        self.assertIs(netlogic.scrub(same, None), same)
        self.assertIs(netlogic.scrub(same, ""), same)
        self.assertEqual(netlogic.scrub(None, PW), None)


class Sentences(unittest.TestCase):
    def test_connected_to(self):
        def w(**k):
            return {"ok": True, "wifi": k}
        self.assertEqual(netlogic.connected_to(w(enabled=True, state="connected", ssid="Home-Test")), (True, "Home-Test"))
        for done in ("disconnected", "off", "blocked", "absent"):
            self.assertEqual(netlogic.connected_to(w(enabled=True, state=done)), (True, None), done)
        self.assertEqual(netlogic.connected_to(w(enabled=False)), (True, None), "Wi-Fi off: nothing is connected")
        for unsure in (None, {}, {"ok": False}, w(enabled=True), w(enabled=True, state="connecting", ssid="Home-Test"),
                       w(enabled=True, state="connected"), w(enabled=True, state="connected", ssid=""),
                       w(enabled=True, state="connected", ssid=5), {"wifi": "x"}):
            self.assertEqual(netlogic.connected_to(unsure), (False, None), unsure)

    def test_status_lines_in_order(self):
        status = {"ok": True, "data": {"summary": "Mobile data: off"}, "bluetooth": {"summary": "Bluetooth: off"},
                  "wifi": {"summary": "Wi-Fi: connected to Home-Test (signal 70%)", "enabled": True},
                  "airplane": {"summary": "Airplane mode: off"}, "modem": {"summary": "Modem: absent"}}
        self.assertEqual(netlogic.status_lines(status), ["Wi-Fi: connected to Home-Test (signal 70%)", "Airplane mode: off",
                                                        "Bluetooth: off", "Mobile data: off"])
        self.assertTrue(netlogic.wifi_on(status))

    def test_status_lines_with_missing_keys(self):
        self.assertEqual(netlogic.status_lines({"wifi": {}, "airplane": "x", "data": {"summary": 3}}), [])
        self.assertEqual(netlogic.status_lines({"bluetooth": {"summary": "Bluetooth: off"}}), ["Bluetooth: off"])
        self.assertEqual(netlogic.status_lines({"ok": False, "error": "OPOS could not read the network state."}),
                         ["OPOS could not read the network state."])
        for bad in (None, [], "x"):
            self.assertEqual(netlogic.status_lines(bad), [])
            self.assertIsNone(netlogic.wifi_on(bad))
        self.assertFalse(netlogic.wifi_on({"wifi": {"enabled": False}}))
        self.assertIsNone(netlogic.wifi_on({"wifi": {"enabled": "no"}}))
        self.assertIsNone(netlogic.wifi_on({"wifi": {"enabled": None}}), "unknown stays unknown")

    def test_friendly(self):
        self.assertEqual(netlogic.friendly({"ok": True, "message": "Connected to Cafe-Test."}), (True, "Connected to Cafe-Test."))
        self.assertEqual(netlogic.friendly({"ok": False, "error": "The password was not accepted."}), (False, "The password was not accepted."))
        self.assertEqual(netlogic.friendly({"ok": False, "message": "Wi-Fi is off."}), (False, "Wi-Fi is off."))
        self.assertEqual(netlogic.friendly({"ok": False}), (False, netlogic.FALLBACK))
        self.assertEqual(netlogic.friendly({"error": ""}), (False, netlogic.FALLBACK))
        self.assertEqual(netlogic.friendly({"ok": True}), (True, "Done."))
        self.assertEqual(netlogic.friendly({"ok": "yes", "message": "x"}), (False, "x"), "only true is ok")
        for bad in (None, [], "ok"):
            self.assertEqual(netlogic.friendly(bad), (False, netlogic.FALLBACK))
        self.assertEqual(netlogic.refused(429), netlogic.TOO_MANY)
        self.assertEqual(netlogic.refused(400), netlogic.FALLBACK)
        self.assertEqual(netlogic.refused(403), netlogic.FALLBACK)

    def test_a_change_that_timed_out_is_not_called_failed(self):
        late = TimeoutError("timed out")
        self.assertTrue(netlogic.timed_out(late))
        self.assertTrue(netlogic.timed_out(urllib.error.URLError(TimeoutError("timed out"))))
        self.assertFalse(netlogic.timed_out(urllib.error.URLError(ConnectionRefusedError())))
        self.assertFalse(netlogic.timed_out(ValueError()))
        self.assertEqual(netlogic.failed(late, True), {"ok": False, "error": netlogic.NO_ANSWER})
        self.assertEqual(netlogic.failed(late, False), {"ok": False, "error": netlogic.SLOW})
        self.assertEqual(netlogic.failed(OSError("connection refused " + PW), True), {"ok": False, "error": netlogic.FALLBACK},
                         "an error's own words (which may quote the request) are never shown")

    def test_no_toolkit_no_files_no_logs(self):
        text = (NATIVE / "netlogic.py").read_text(encoding="utf-8")
        tree = ast.parse(text)
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(mods - {"__future__"}, mods)
        for pattern in (r"\bopen\(", r"\bprint\(", r"(?m)^\s*(import|from)\s+logging\b|\blogging\.", r"\bwrite\("):
            self.assertIsNone(re.search(pattern, text), pattern)


# --- the page, on stand-in widgets -----------------------------------------------------------------------------------
class Core:
    """Stands in for shell.Core: records each net_* request (a copy of its fields, and the wait it was given) and answers
    from a script: a reply, an exception to raise, or a list of them taken in turn."""

    def __init__(self):
        self.posts, self.answers, self.handed = [], {}, []

    def net(self, action, fields, timeout):
        self.posts.append((action, dict(fields)))
        self.handed.append((action, fields, timeout))
        got = self.answers.get(action, {"ok": True})
        if isinstance(got, list):
            got = got.pop(0) if got else {"ok": True}
        if isinstance(got, BaseException):
            raise got
        return got


class NetworkPage(unittest.TestCase):
    """screens.Network on a stand-in toolkit, with threads that run only when the test says, in the order it says."""

    def setUp(self):
        stand_in_toolkit(self)
        screens = self.screens = importlib.import_module("opos.native.screens")
        self.threads, self.timers, self.removed = [], [], []

        class Thread:
            def __init__(s, target, daemon=None):
                s.target = target

            def start(s):
                self.threads.append(s.target)
        screens.threading = types.SimpleNamespace(Thread=Thread)
        screens.GLib = types.SimpleNamespace(
            idle_add=lambda fn, *a: fn(*a),
            timeout_add=lambda ms, fn, *a: self.timers.append((ms, fn)) or len(self.timers),
            source_remove=lambda i: self.removed.append(i))
        self.core = Core()
        self.shell = types.SimpleNamespace(page="network", core=self.core, gone=[], hand="right")
        self.shell.go = lambda n: self.shell.gone.append(n)
        self.page = screens.Network(self.shell)
        self.clock = [100.0]                        # the page's clock stands still unless a test moves it (look())
        self.page.now = lambda: self.clock[0]
        self.said = []
        real_say = self.page.say

        def say(text):
            self.said.append(text)
            real_say(text)
        self.page.say = say

    def look(self):
        """The owner has had the page in front of them for HOLD_S: what the buttons say now is what a tap acts on."""
        self.clock[0] += self.screens.Network.HOLD_S

    def run_threads(self):
        while self.threads:
            self.threads.pop(0)()

    def run_one(self, index):
        """The index-th request still waiting gets its answer now (the others keep waiting)."""
        self.threads.pop(index)()

    def type_in(self, text):
        for ch in text:
            self.page.key(ch)

    def chosen(self, row):
        self.page.select(netlogic.rows({"networks": [row]})[0])

    def changes(self):
        return [p for p in self.core.posts if p[0] in ("net_join", "net_forget", "net_wifi")]

    def status_on(self, enabled=True):
        self.page.statused(self.page.radios.issue(), {"ok": True, "wifi": {"enabled": enabled, "summary": "Wi-Fi: fake"}})

    def status_connected(self, ssid):
        """A status reply that says the Wi-Fi is connected to ssid (None: on, not connected)."""
        w = ({"enabled": True, "state": "connected", "ssid": ssid, "summary": "Wi-Fi: connected to %s" % ssid} if ssid else
             {"enabled": True, "state": "disconnected", "summary": "Wi-Fi: on, not connected"})
        self.page.statused(self.page.radios.issue(), {"ok": True, "wifi": w})

    def scan_shows(self, *nets):
        self.page.scanned(self.page.found.issue(), {"ok": True, "networks": list(nets), "saved": []}, quiet=True)

    def assert_nothing_holds_the_password(self):
        for name, value in vars(self.page).items():
            self.assertNotIn(PW, repr(value), name)
        self.assertTrue(all(PW not in str(s) for s in self.said))
        for _action, fields, _t in self.core.handed:
            self.assertEqual(fields, {}, "the sending thread drops its fields once the request has gone")

    # --- where it is ------------------------------------------------------------------------------------------------
    def test_no_tile_no_preview_and_settings_opens_it(self):
        self.assertFalse(self.screens.Network.tile)
        self.assertTrue(self.screens.Settings.tile)
        self.assertIs(self.screens.PAGES[-1], self.screens.Network)
        self.assertIn(self.screens.Network, self.screens.enabled_pages({}), "it needs no OPOS_APPS entry")
        self.assertEqual(self.page.preview({}), ("", []))
        self.assertEqual(self.page.title, "Network")
        self.shell.leave, self.shell.set_hand = (lambda: None), (lambda h: None)
        settings = self.screens.Settings(self.shell)
        _text, rows = settings.preview({})
        rows[-1][2]()
        self.assertEqual((rows[-1][0], self.shell.gone), ("Network", ["network"]))
        self.assertEqual(self.core.posts, [], "building the pages asks nothing")

    # --- join -----------------------------------------------------------------------------------------------------
    def test_join_sends_the_password_once_and_drops_it_on_sending(self):
        self.chosen(net("Cafe-Test", 60))
        self.assertEqual(self.page.mode(), "password")
        self.type_in(PW)
        self.assertEqual(len(self.page.secret), len(PW))
        self.assertEqual(self.page.typed.get_label(), "•" * len(PW), "shown as bullets only")
        self.core.answers["net_join"] = {"ok": True, "message": "Connected to Cafe-Test."}
        self.page.main_action()
        self.assertEqual(len(self.page.secret), 0, "emptied once handed to the sender, before any answer")
        self.assertEqual(self.core.posts, [], "nothing has gone out yet: the thread has not run")
        self.assertTrue(self.page.busy)
        self.assertFalse(self.page.main.sensitive)
        self.assertIn("Joining...", self.said)
        self.page.main_action()                     # a second tap while joining sends nothing
        self.page.key("\n")
        self.assertEqual(len(self.threads), 1)
        self.shell.page = "home"                    # left meanwhile: no refresh after the answer
        self.run_threads()
        self.assertEqual(self.core.posts, [("net_join", {"ssid": "Cafe-Test", "password": PW})])
        self.assertEqual(self.core.handed[0][2], 100, "a join gets as long as the core may take")
        self.assertEqual(self.said[-1], "Connected to Cafe-Test.")
        self.assertFalse(self.page.busy)
        self.assertIsNone(self.page.chosen)
        self.assert_nothing_holds_the_password()

    def test_a_join_answered_while_showing_reads_the_radios_and_list_again(self):
        self.chosen(net("Cafe-Test", 60))
        self.type_in(PW)
        self.page.main_action()
        self.run_threads()
        self.assertEqual([p[0] for p in self.core.posts], ["net_join", "net_status", "net_scan"])
        self.assertEqual(self.core.posts[1:], [("net_status", {"fresh": True}), ("net_scan", {"rescan": False})])

    def test_a_known_network_joins_without_a_password(self):
        self.chosen(net("Home-Test", 40, known=True))
        self.assertEqual(self.page.mode(), "known")
        self.type_in("x")                           # no keyboard for it: keys do nothing
        self.assertEqual(len(self.page.secret), 0)
        self.page.main_action()
        self.run_threads()
        self.assertEqual(self.core.posts[0], ("net_join", {"ssid": "Home-Test"}))

    def test_an_open_network_joins_without_a_password_and_says_it_is_open(self):
        self.chosen(net("Park-Test", 40, security="open"))
        self.assertEqual(self.page.mode(), "open")
        self.assertEqual(self.page.why.get_label(), netlogic.OPEN_NOTE)
        self.page.main_action()
        self.run_threads()
        self.assertEqual(self.core.posts[0], ("net_join", {"ssid": "Park-Test"}))

    def test_no_password_typed_sends_nothing(self):
        self.chosen(net("Cafe-Test"))
        self.page.main_action()
        self.assertEqual((self.threads, self.said[-1]), ([], netlogic.NEED_PW))

    def test_return_on_the_keyboard_while_joining_sends_no_second_join(self):
        self.chosen(net("Cafe-Test", 60))
        self.type_in(PW)
        self.page.main_action()
        self.assertTrue(self.page.busy)
        self.type_in("fake-again-1111")             # the keyboard still takes keys while the first join waits
        self.page.key("\n")                         # Return calls join() directly, not through the greyed-out button
        self.assertEqual(len(self.threads), 1, "one join in flight: Return while busy sends nothing")
        self.assertEqual(self.said[-1], netlogic.KEYS_WAIT, "the keys typed were said to be not kept; Return adds nothing")
        self.run_one(0)
        self.assertEqual(self.changes(), [("net_join", {"ssid": "Cafe-Test", "password": PW})])

    def test_cancel_choosing_another_row_and_leaving_drop_the_password_and_send_nothing(self):
        for drop in (lambda: self.page.cancel(), lambda: self.chosen(net("Other-Test")), lambda: self.page.other_network(),
                     lambda: self.page.leave()):
            self.chosen(net("Cafe-Test"))
            self.type_in(PW)
            drop()
            self.assertEqual((len(self.page.secret), self.threads, self.core.posts), (0, [], []))
        self.assert_nothing_holds_the_password()

    def test_a_hidden_network(self):
        self.page.other_network()
        self.assertEqual(self.page.mode(), "other")
        self.type_in("Cabin-Test\n")
        self.assertEqual(self.page.field, "password")
        self.type_in(PW)
        self.assertNotIn(PW, self.page.boxes["password"].get_label())
        self.page.key("\n")                         # Return in the password joins
        self.assertEqual(len(self.page.secret), 0)
        self.run_threads()
        self.assertEqual(self.core.posts, [("net_join", {"ssid": "Cabin-Test", "hidden": True, "password": PW}),
                                           ("net_status", {"fresh": True}), ("net_scan", {"rescan": False})])
        self.assert_nothing_holds_the_password()

    def test_a_hidden_network_needs_its_name_and_an_open_one_no_password(self):
        self.page.other_network()
        self.page.main_action()
        self.assertEqual((self.threads, self.said[-1]), ([], netlogic.NEED_NAME))
        self.type_in("Barn-Test")
        self.page.set_open(True)
        self.page.main_action()
        self.shell.page = "home"
        self.run_threads()
        self.assertEqual(self.core.posts, [("net_join", {"ssid": "Barn-Test", "hidden": True})])

    # --- error replies --------------------------------------------------------------------------------------------
    def test_the_cores_error_sentence_is_shown_and_the_choice_kept(self):
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.core.answers["net_join"] = {"ok": False, "error": "The password was not accepted. Check it and try again."}
        self.page.main_action()
        self.run_threads()
        self.assertIn("The password was not accepted. Check it and try again.", self.said)
        self.assertEqual(self.page.chosen["ssid"], "Cafe-Test", "a failed join keeps the choice to try again")
        self.assertEqual((len(self.page.secret), self.page.busy), (0, False), "the password is typed again, never kept")
        self.assert_nothing_holds_the_password()

    def test_a_reply_that_echoes_the_password_never_shows_it(self):
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.core.answers["net_join"] = {"ok": False, "error": "nmcli: secret %s refused" % PW}
        self.page.main_action()
        self.run_threads()
        self.assertEqual(self.said[-1], netlogic.HIDDEN)
        self.assert_nothing_holds_the_password()

    def test_an_exception_that_quotes_the_request_is_never_shown(self):
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.core.answers["net_join"] = OSError("connection reset while sending %s" % PW)
        self.page.main_action()
        self.run_threads()
        self.assertEqual(self.said[-1], netlogic.FALLBACK)
        self.assert_nothing_holds_the_password()

    def test_http_errors_are_plain_sentences(self):
        for code, words in ((400, netlogic.FALLBACK), (403, netlogic.FALLBACK), (429, netlogic.TOO_MANY)):
            self.core.answers["net_join"] = urllib.error.HTTPError(URL, code, "refused", {}, io.BytesIO(b"{}"))
            self.chosen(net("Home-Test", known=True))
            self.page.main_action()
            self.run_threads()
            self.assertEqual(self.said[-1], words, code)
            self.assertFalse(self.page.busy)

    def test_a_join_with_no_answer_in_time_is_not_called_failed_and_the_radios_are_read_again(self):
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.core.answers["net_join"] = TimeoutError("timed out")
        self.page.main_action()
        self.run_threads()
        self.assertEqual(self.said[-1], netlogic.NO_ANSWER)
        self.assertEqual([p[0] for p in self.core.posts], ["net_join", "net_status", "net_scan"])
        self.assertFalse(self.page.busy)

    def test_a_status_that_fails_leaves_wifi_unknown_and_unswitchable(self):
        self.page.statused(self.page.radios.issue(), {"ok": False, "error": "OPOS could not read the network state."})
        self.assertEqual(self.page.lines.get_label(), "OPOS could not read the network state.")
        self.assertFalse(self.page.wifi.sensitive)
        self.page.statused(self.page.radios.issue(), {"ok": False})
        self.assertEqual(self.page.lines.get_label(), "The radios could not be read.")

    # --- forget, disconnect and Wi-Fi off: two taps each ------------------------------------------------------------
    def test_forget_takes_two_taps_on_the_same_network(self):
        self.chosen(net("Home-Test", known=True))
        self.page.forget()
        self.assertEqual((self.threads, self.page.forget_b.get_label()), ([], "Forget this network?"), "the first tap only asks")
        self.chosen(net("Work-Test", known=True))
        self.assertEqual(self.page.forget_b.get_label(), "Forget", "another row disarms it")
        self.page.forget()
        self.page.forget()
        self.run_threads()
        self.assertEqual(self.changes(), [("net_forget", {"ssid": "Work-Test"})])
        self.assertEqual(self.core.handed[0][2], 45)

    def test_disconnect_takes_two_taps(self):
        self.status_connected("Home-Test")
        self.chosen(net("Home-Test", active=True, known=True))
        self.assertEqual(self.page.mode(), "connected")
        self.page.main_action()
        self.assertEqual((self.threads, self.page.main.get_label()), ([], "Disconnect now?"))
        self.page.cancel()
        self.chosen(net("Home-Test", active=True, known=True))
        self.page.main_action()
        self.assertEqual(self.threads, [], "Cancel disarmed it")
        self.page.main_action()
        self.shell.page = "home"
        self.run_threads()
        self.assertEqual(self.changes(), [("net_wifi", {"mode": "disconnect"})])

    def test_wifi_off_takes_two_taps_and_on_takes_one(self):
        self.status_on(True)
        self.assertEqual(self.page.wifi.get_label(), "Turn Wi-Fi off")
        self.look()
        self.page.toggle_wifi()
        self.assertEqual((self.threads, self.page.wifi.get_label()), ([], "Turn Wi-Fi off now?"))
        self.page.toggle_wifi()
        self.shell.page = "home"
        self.run_threads()
        self.assertEqual(self.changes(), [("net_wifi", {"mode": "off"})])
        self.status_on(False)
        self.assertEqual(self.page.wifi.get_label(), "Turn Wi-Fi on")
        self.look()
        self.page.toggle_wifi()
        self.run_threads()
        self.assertEqual(self.changes()[-1], ("net_wifi", {"mode": "on"}))

    def test_wifi_off_armed_is_dropped_when_the_radio_reads_off(self):
        self.status_on(True)
        self.page.toggle_wifi()
        self.status_on(False)
        self.status_on(True)
        self.page.toggle_wifi()
        self.assertEqual(self.threads, [], "the earlier first tap no longer counts")

    def test_wifi_is_never_switched_while_its_state_is_not_known(self):
        self.assertEqual(self.page.wifi.get_label(), "Wi-Fi: not known yet")
        self.assertFalse(self.page.wifi.sensitive)
        for _ in range(3):
            self.page.toggle_wifi()
        self.assertEqual((self.threads, self.said[-1]), ([], netlogic.WIFI_UNKNOWN))
        self.page.statused(self.page.radios.issue(), {"ok": True, "wifi": {"enabled": None, "summary": "Wi-Fi: unknown"}})
        self.page.toggle_wifi()
        self.page.toggle_wifi()
        self.assertEqual((self.threads, self.changes()), ([], []))

    def test_an_unsupported_network_offers_nothing(self):
        self.chosen(net("Office-Test", security="WPA2 802.1X"))
        self.assertEqual(self.page.mode(), "unsupported")
        self.assertFalse(self.page.main.visible)
        self.page.main_action()
        self.page.join()
        self.assertEqual(self.threads, [])

    # --- coming and going -------------------------------------------------------------------------------------------
    def test_entering_reads_the_radios_then_one_rescan_then_polls_the_radios_only(self):
        self.page.enter()
        self.run_threads()
        self.run_threads()
        self.assertEqual(self.core.posts, [("net_status", {}), ("net_scan", {"rescan": True})])
        (ms, poll), = self.timers
        self.assertEqual(ms, 10000)
        for _ in range(3):
            self.assertTrue(poll())
            self.run_threads()
        self.assertEqual([p[0] for p in self.core.posts[2:]], ["net_status"] * 3, "the poll never rescans")
        self.assertEqual(self.changes(), [], "and nothing on entering or polling changes the Wi-Fi")

    def test_a_poll_does_not_stack_on_a_read_still_waiting(self):
        self.page.enter()
        (_ms, poll), = self.timers
        poll()
        poll()
        self.assertEqual(len(self.threads), 1, "the first read has not answered: no second one")

    def test_nothing_is_asked_while_not_showing(self):
        self.shell.page = "home"
        self.page.enter()
        self.assertEqual((self.threads, self.timers), ([], []))
        self.shell.page = "network"
        self.page.enter()
        self.run_threads()
        self.run_threads()
        (_ms, poll), = self.timers
        self.core.posts.clear()
        self.shell.page = "settings"
        self.assertFalse(poll(), "the timer stops itself")
        self.assertEqual((self.threads, self.core.posts), ([], []))

    def test_a_first_status_answered_after_leaving_starts_no_scan(self):
        self.page.enter()
        self.assertEqual(len(self.threads), 1, "the first status read is on its way")
        self.shell.page = "home"                    # left before it answered
        self.page.leave()
        self.run_threads()
        self.assertEqual((self.core.posts, self.threads), ([("net_status", {})], []),
                         "the rescan that follows entering is not started for a screen that is not showing")

    def test_leaving_stops_the_timer_and_drops_the_password(self):
        self.page.enter()
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.shell.page = "home"
        self.page.leave()
        self.assertEqual((self.removed, self.page.timer, len(self.page.secret), self.page.chosen), ([1], None, 0, None))

    def test_scan_rows_are_shown_and_can_be_chosen(self):
        self.core.answers["net_scan"] = {"ok": True, "message": "2 networks found.", "networks": [net("Cafe-Test", 30), net("Home-Test", 80)], "saved": []}
        self.page.scan(True)
        self.run_threads()
        self.assertEqual(self.page.nets.rowset.words, [("Home-Test", "needs a password, signal 80%"), ("Cafe-Test", "needs a password, signal 30%")])
        self.page.nets.rowset.click(1)
        self.assertEqual(self.page.chosen["ssid"], "Cafe-Test")
        self.assertEqual(self.changes(), [], "choosing a row sends nothing")

    def test_a_failed_scan_keeps_the_list_and_says_why(self):
        self.core.answers["net_scan"] = [{"ok": True, "networks": [net("Cafe-Test")]}, {"ok": False, "error": "Wi-Fi is switched off. Turn it on first."}]
        self.page.scan(True)
        self.run_threads()
        self.page.scan(True)
        self.run_threads()
        self.assertEqual(self.said[-1], "Wi-Fi is switched off. Turn it on first.")
        self.assertEqual([w[0] for w in self.page.nets.rowset.words], ["Cafe-Test"])

    # --- stale replies (logic.Sequence and its floor) ---------------------------------------------------------------
    def test_an_older_status_never_replaces_a_newer_one(self):
        self.core.answers["net_status"] = [{"ok": True, "wifi": {"enabled": True, "summary": "Wi-Fi: older"}},
                                           {"ok": True, "wifi": {"enabled": False, "summary": "Wi-Fi: newer"}}]
        self.page.status()
        self.page.status()
        first, second = self.threads
        self.threads.clear()
        # the core answers in order (older first), but the window thread is handed the newer reply first
        self.screens.GLib.idle_add, held = (lambda fn, *a: held.append((fn, a))), []
        first()
        second()
        held[1][0](*held[1][1])
        held[0][0](*held[0][1])
        self.assertEqual(self.page.lines.get_label(), "Wi-Fi: newer")
        self.assertEqual(self.page.wifi.get_label(), "Turn Wi-Fi on")

    def test_an_older_scan_never_replaces_a_newer_one(self):
        self.core.answers["net_scan"] = [{"ok": True, "networks": [net("Old-Test")]}, {"ok": True, "networks": [net("New-Test")]}]
        self.page.scan(False, quiet=True)
        self.page.scan(False, quiet=True)
        self.screens.GLib.idle_add, held = (lambda fn, *a: held.append((fn, a))), []
        self.run_threads()
        held[1][0](*held[1][1])
        held[0][0](*held[0][1])
        self.assertEqual([w[0] for w in self.page.nets.rowset.words], ["New-Test"])

    def test_a_read_sent_before_a_confirmed_change_is_refused_after_it(self):
        self.status_on(True)
        self.look()
        self.page.toggle_wifi()
        self.page.toggle_wifi()                     # Wi-Fi off asked for
        self.page.status()                          # a poll that the core may answer before it acts
        self.core.answers["net_status"] = [{"ok": True, "wifi": {"enabled": True, "summary": "Wi-Fi: before the change"}},
                                           {"ok": True, "wifi": {"enabled": False, "summary": "Wi-Fi: off"}}]
        change, poll = self.threads
        self.threads.clear()
        self.screens.GLib.idle_add, held = (lambda fn, *a: held.append((fn, a))), []
        poll()                                      # answered with the old state, but not delivered yet
        change()
        held[1][0](*held[1][1])                     # the change's answer: Wi-Fi off, confirmed
        self.screens.GLib.idle_add = lambda fn, *a: fn(*a)
        held[0][0](*held[0][1])                     # the old poll arrives late: refused
        self.assertNotIn("before the change", self.page.lines.get_label())
        self.run_threads()                          # the read made after the change is drawn
        self.assertEqual(self.page.lines.get_label(), "Wi-Fi: off")
        self.assertEqual(self.core.posts[-2], ("net_status", {"fresh": True}), "and it skips the core's few-second cache")

    def test_a_failed_change_sets_no_floor(self):
        """Only a refusal before anything ran (an HTTP 4xx such as 429) leaves the radios as they were: no floor."""
        self.status_on(True)
        self.core.answers["net_wifi"] = urllib.error.HTTPError(URL, 429, "refused", {}, io.BytesIO(b"{}"))
        self.core.answers["net_status"] = {"ok": True, "wifi": {"enabled": True, "summary": "Wi-Fi: a newer read"}}
        self.look()
        self.page.toggle_wifi()
        self.page.toggle_wifi()
        self.page.status()
        change, poll = self.threads
        self.threads.clear()
        self.screens.GLib.idle_add, held = (lambda fn, *a: held.append((fn, a))), []
        change()
        poll()
        held[0][0](*held[0][1])                     # the refusal says nothing about the radios
        self.assertEqual(self.said[-1], netlogic.TOO_MANY)
        self.assertTrue(netlogic.wifi_on(self.page.status_data), "the radios read is still trusted")
        held[1][0](*held[1][1])                     # so the read after it is still drawn
        self.assertEqual(self.page.lines.get_label(), "Wi-Fi: a newer read")

    def timed_out_join_then_a_stale_poll(self, earlier_visit):
        """Home-Test is connected; a join to Cafe-Test gets no answer in time, but the core does connect Cafe-Test. A poll
        sent while the join waited arrives last, after the owner has chosen the Home-Test row."""
        home = {"ok": True, "wifi": {"enabled": True, "state": "connected", "ssid": "Home-Test", "summary": "Wi-Fi: Home-Test"}}
        cafe = {"ok": True, "wifi": {"enabled": True, "state": "connected", "ssid": "Cafe-Test", "summary": "Wi-Fi: Cafe-Test"}}
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.page.nets.rowset.click(1)
        self.core.answers["net_join"] = {"ok": False, "error": netlogic.NO_ANSWER}
        self.page.main_action()                     # the join to Cafe-Test waits (thread 0)
        if earlier_visit:
            self.come_back()                        # this visit's first status (thread 1)
            self.core.answers["net_status"] = home
            self.run_one(1)                         # answered before the join took effect; its scan is put off
        self.page.poll()                            # a poll sent while the join still runs
        self.run_one(0)                             # the join answers: no answer in time
        self.assertIn(netlogic.NO_ANSWER, self.said)
        self.core.answers["net_scan"] = {"ok": True, "networks": [net("Home-Test", known=True),
                                                                 net("Cafe-Test", active=True, known=True)], "saved": []}
        self.run_one(1 if earlier_visit else len(self.threads) - 1)    # the scan sent after the join: Cafe-Test connected
        rows = [w[0] for w in self.page.nets.rowset.words]
        self.look()                                 # the newer list has been in front of the owner for HOLD_S
        self.page.nets.rowset.click(rows.index("Home-Test"))
        self.core.answers["net_status"] = home
        self.run_one(0)                             # the poll sent before the join's answer arrives now: refused
        self.assertEqual((self.page.mode(), self.page.main.get_label()), ("known", "Join"), "the old poll is not drawn")
        self.look()
        self.page.main_action()
        self.page.main_action()
        self.core.answers["net_status"] = cafe
        self.run_threads()
        self.assertNotIn(("net_wifi", {"mode": "disconnect"}), self.changes(), "Cafe-Test is never dropped")

    def test_a_timed_out_join_sets_the_floor(self):
        self.timed_out_join_then_a_stale_poll(earlier_visit=False)

    def test_a_timed_out_join_from_an_earlier_visit_sets_the_floor(self):
        self.timed_out_join_then_a_stale_poll(earlier_visit=True)

    # --- after a change, the radios read from before it is not trusted --------------------------------------------
    HOME = {"ok": True, "wifi": {"enabled": True, "state": "connected", "ssid": "Home-Test", "summary": "Wi-Fi: Home-Test"}}
    CAFE = {"ok": True, "wifi": {"enabled": True, "state": "connected", "ssid": "Cafe-Test", "summary": "Wi-Fi: Cafe-Test"}}
    CORE_SLOW = "The network service did not answer in time. Try again."   # opos.net's own timeout sentence

    def disconnect_from_the_old_list(self, answer):
        """Home-Test is connected; the owner joins saved Cafe-Test and the join is answered. Before the fresh radios read
        and list come back, the owner taps the Home-Test row (the old list still says connected), then Disconnect twice."""
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.page.nets.rowset.click(1)
        self.core.answers["net_join"] = answer
        self.page.main_action()
        self.run_one(0)                             # the join's answer; the fresh status and the quiet scan wait
        self.assertEqual(len(self.threads), 2)
        shown = (self.page.lines.get_label(), self.page.wifi.get_label())
        rows = [w[0] for w in self.page.nets.rowset.words]
        self.page.nets.rowset.click(rows.index("Home-Test"))
        self.assertEqual(self.page.main.get_label(), "Disconnect")
        self.page.main_action()
        self.page.main_action()                     # Disconnect now?: confirmed on a read from before the change
        said = self.said[-1]
        self.page.toggle_wifi()                     # the Wi-Fi button cannot switch either
        self.core.answers["net_status"] = self.CAFE
        self.run_threads()
        self.assertEqual([c for c in self.changes() if c[0] == "net_wifi"], [], "Cafe-Test is never dropped")
        self.assertEqual(said, netlogic.DISCONNECT_UNKNOWN)
        self.assertNotIn("Home-Test", shown[0], "what the radios were before the change is not shown as now")
        self.assertEqual(shown, (netlogic.REREAD, "Wi-Fi: not known yet"))
        self.assertEqual(netlogic.connected_to(self.page.status_data), (True, "Cafe-Test"), "the read after it is drawn")

    def test_a_confirmed_join_then_disconnect_on_the_old_list_sends_nothing(self):
        self.disconnect_from_the_old_list({"ok": True, "message": "Connected to Cafe-Test."})

    def test_a_join_with_no_answer_then_disconnect_on_the_old_list_sends_nothing(self):
        self.disconnect_from_the_old_list({"ok": False, "error": netlogic.NO_ANSWER})

    def test_a_failed_join_then_disconnect_on_the_old_list_sends_nothing(self):
        for error in (self.CORE_SLOW, "The password was not accepted.", "Another network change is still running."):
            with self.subTest(error=error):
                self.setUp()
                self.disconnect_from_the_old_list({"ok": False, "error": error})

    def stale_poll_after(self, answer):
        """A poll answered with Home-Test before the join took effect is delivered after the join's answer and the scan."""
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.page.nets.rowset.click(1)
        self.core.answers["net_join"] = answer
        self.page.main_action()                     # thread 0: the join
        self.page.poll()                            # thread 1: a poll sent while the join waits
        poll = self.threads.pop(1)
        self.core.answers["net_status"] = self.HOME
        self.screens.GLib.idle_add, late = (lambda fn, *a: late.append((fn, a))), []
        poll()                                      # answered with Home-Test, not delivered yet
        self.screens.GLib.idle_add = lambda fn, *a: fn(*a)
        self.run_one(0)                             # the join's answer
        self.core.answers["net_status"] = self.CAFE
        self.core.answers["net_scan"] = {"ok": True, "networks": [net("Home-Test", known=True),
                                                                 net("Cafe-Test", active=True, known=True)], "saved": []}
        self.run_one(len(self.threads) - 1)         # the quiet scan answers; the fresh status still waits
        self.look()
        rows = [w[0] for w in self.page.nets.rowset.words]
        self.page.nets.rowset.click(rows.index("Home-Test"))
        late[0][0](*late[0][1])                     # the poll from before the join arrives last: refused
        self.assertEqual((self.page.mode(), self.page.main.get_label()), ("known", "Join"))
        self.assertIsNone(self.page.status_data)
        self.page.main_action()
        self.run_threads()
        self.assertEqual([c for c in self.changes() if c[0] == "net_wifi"], [])

    def test_the_cores_own_timeout_sets_the_floor(self):
        self.stale_poll_after({"ok": False, "error": self.CORE_SLOW})

    def test_any_other_answer_from_the_core_to_a_change_sets_the_floor(self):
        for error in ("The password was not accepted.", "Another network change is still running.", None):
            with self.subTest(error=error):
                self.setUp()
                self.stale_poll_after({"ok": False, "error": error} if error else {"ok": False})

    def test_a_server_error_sets_the_floor_and_a_refusal_before_anything_ran_does_not(self):
        for code, floor in ((500, True), (502, True), (400, False), (403, False), (429, False)):
            with self.subTest(code=code):
                self.assertEqual(netlogic.nothing_ran(netlogic.refusal(code)), not floor)
        self.assertTrue(netlogic.nothing_ran(netlogic.failed(urllib.error.URLError(ConnectionRefusedError()), True)))
        for err in (TimeoutError("timed out"), ValueError("bad reply"), ConnectionResetError()):
            self.assertFalse(netlogic.nothing_ran(netlogic.failed(err, True)), err)
        for got in (None, {}, {"ok": True}, {"ok": False, "error": netlogic.TOO_MANY}, {netlogic.NOT_SENT: "yes"}):
            self.assertFalse(netlogic.nothing_ran(got), got)

    # --- the Wi-Fi button and the rows hold too ---------------------------------------------------------------------
    def test_the_wifi_button_that_appears_by_itself_holds_the_first_tap(self):
        self.page.statused(self.page.radios.issue(), {"ok": True, "wifi": {"enabled": False, "state": "off", "summary": "Wi-Fi: off"}})
        self.scan_shows()
        self.page.scanned(self.page.found.issue(), {"ok": True, "networks": [], "saved": ["Cabin-Test"]}, quiet=True)
        self.look()
        self.page.nets.rowset.click(0)              # a saved network not in range: the Wi-Fi button is hidden
        self.assertFalse(self.page.idle.visible)
        self.page.scanned(self.page.found.issue(), {"ok": True, "networks": [], "saved": []}, quiet=True)
        self.assertEqual((self.page.idle.visible, self.page.wifi.get_label()), (True, "Turn Wi-Fi on"))
        self.page.toggle_wifi()                     # a tap meant for what was there before
        self.assertEqual((self.threads, self.said[-1]), ([], netlogic.CHANGED))
        self.look()
        self.page.toggle_wifi()
        self.run_threads()
        self.assertEqual(self.changes(), [("net_wifi", {"mode": "on"})])

    def test_a_row_moved_under_the_finger_chooses_nothing(self):
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", 60, known=True))
        self.look()
        # a newer quiet scan puts an open network in place 1 just before the owner's tap meant for Cafe-Test
        self.scan_shows(net("Home-Test", active=True, known=True), net("Open-Test", 90, security="open"),
                        net("Cafe-Test", 40, known=True))
        self.page.nets.rowset.click(1)
        self.page.main_action()                     # the quick Join: the pane is idle, so it only scans
        self.run_threads()
        self.assertEqual(self.changes(), [])
        self.assertIn(netlogic.LIST_CHANGED, self.said)
        self.look()
        self.page.nets.rowset.click(1)              # once the owner has seen the new list, the tap is theirs
        self.assertEqual(self.page.ssid(), "Open-Test")

    def test_a_row_whose_offer_changed_in_place_chooses_nothing(self):
        self.scan_shows(net("Cafe-Test"))
        self.look()
        self.scan_shows(net("Cafe-Test", known=True))   # same place, now saved: a tap would join at once
        self.page.nets.rowset.click(0)
        self.assertIsNone(self.page.chosen)
        self.assertEqual(self.said[-1], netlogic.LIST_CHANGED)

    def test_control_a_newer_list_with_only_new_signals_holds_nothing(self):
        self.scan_shows(net("Home-Test", 80, known=True), net("Cafe-Test", 40, known=True))
        self.look()
        self.scan_shows(net("Home-Test", 70, known=True), net("Cafe-Test", 45, known=True))
        self.page.nets.rowset.click(1)
        self.assertEqual(self.page.ssid(), "Cafe-Test")
        self.page.main_action()
        self.run_threads()
        self.assertEqual(self.changes(), [("net_join", {"ssid": "Cafe-Test"})])
        self.assertFalse([t for t in self.said if "just changed" in t], "nothing was held")

    def test_an_answer_after_leaving_says_the_outcome_and_asks_nothing_more(self):
        self.chosen(net("Home-Test", known=True))
        self.page.main_action()
        self.shell.page = "home"
        self.page.leave()
        self.run_threads()
        self.assertEqual([p[0] for p in self.core.posts], ["net_join"])
        self.assertFalse(self.page.busy)

    # --- a prompt never acts on a network other than the one it asked about -----------------------------------------
    def armed_on_home(self):
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.page.nets.rowset.click(0)
        self.assertEqual(self.page.ssid(), "Home-Test")
        self.page.main_action()
        self.assertEqual(self.page.main.get_label(), "Disconnect now?")

    def test_a_newer_status_with_another_network_connected_drops_the_disconnect_prompt(self):
        self.armed_on_home()
        self.status_connected("Cafe-Test")
        self.assertIsNone(self.page.sure.armed)
        self.assertFalse(self.page.chosen["active"], "the chosen row follows the radios")
        self.assertEqual((self.page.mode(), self.page.main.get_label()), ("known", "Join"))
        self.assertEqual((self.threads, self.changes()), ([], []))

    def test_a_newer_scan_with_another_network_connected_drops_the_disconnect_prompt(self):
        self.armed_on_home()
        self.scan_shows(net("Home-Test", known=True), net("Cafe-Test", active=True, known=True))
        self.assertIsNone(self.page.sure.armed)
        self.assertEqual((self.page.ssid(), self.page.chosen["active"]), ("Home-Test", False), "the row is found again by name")
        self.assertEqual(self.page.mode(), "known")
        self.assertNotIn(("net_wifi", {"mode": "disconnect"}), self.changes())

    def test_the_second_disconnect_tap_is_refused_when_the_radios_name_another_network(self):
        self.armed_on_home()
        # the radios now name another network, without any reply having redrawn the pane (the last guard)
        self.page.status_data = {"ok": True, "wifi": {"enabled": True, "state": "connected", "ssid": "Cafe-Test"}}
        self.page.main_action()
        self.assertEqual((self.threads, self.changes()), ([], []))
        self.assertEqual(self.said[-1], netlogic.DISCONNECT_MOVED)
        self.assertIsNone(self.page.sure.armed)

    def test_the_second_disconnect_tap_is_refused_while_the_connected_network_is_not_known(self):
        for status in (None, {"ok": False}, {"ok": True, "wifi": {"enabled": True, "state": "connecting"}},
                       {"ok": True, "wifi": {"enabled": True, "state": "connected"}}):
            with self.subTest(status=status):
                self.armed_on_home()
                self.page.status_data = status
                self.page.main_action()
                self.assertEqual((self.threads, self.changes()), ([], []))
                self.assertEqual(self.said[-1], netlogic.DISCONNECT_UNKNOWN)

    def quick_tap_after(self, newer):
        clock = [100.0]
        self.page.now = lambda: clock[0]
        self.armed_on_home()
        newer()                                     # a newer read: Home-Test is now only saved, the button says Join
        self.assertEqual((self.page.sure.armed, self.page.mode(), self.page.main.get_label()), (None, "known", "Join"))
        clock[0] += self.screens.Network.HOLD_S / 2
        self.page.main_action()                     # the quick second tap that was meant for "Disconnect now?"
        self.assertEqual((self.threads, self.changes()), ([], []))
        self.assertEqual(self.said[-1], netlogic.CHANGED)
        clock[0] += self.screens.Network.HOLD_S
        self.page.main_action()                     # a tap after the pause is the owner's own Join
        self.run_threads()
        self.assertEqual(self.changes(), [("net_join", {"ssid": "Home-Test"})])

    def test_a_quick_tap_after_a_newer_scan_turned_disconnect_into_join_joins_nothing(self):
        self.quick_tap_after(lambda: self.scan_shows(net("Home-Test", known=True), net("Cafe-Test", active=True, known=True)))

    def test_a_quick_tap_after_a_newer_status_turned_disconnect_into_join_joins_nothing(self):
        self.quick_tap_after(lambda: self.status_connected("Cafe-Test"))

    def test_a_quick_tap_after_the_wifi_off_prompt_turned_into_turn_on_sends_nothing(self):
        clock = [100.0]
        self.page.now = lambda: clock[0]
        self.status_on(True)
        self.page.toggle_wifi()
        self.status_on(False)                       # the radio already reads off: the button says Turn Wi-Fi on
        self.assertEqual(self.page.wifi.get_label(), "Turn Wi-Fi on")
        self.page.toggle_wifi()
        self.assertEqual((self.threads, self.said[-1]), ([], netlogic.CHANGED))
        clock[0] += self.screens.Network.HOLD_S
        self.page.toggle_wifi()
        self.run_threads()
        self.assertEqual(self.changes(), [("net_wifi", {"mode": "on"})])

    def test_a_dropped_prompt_whose_button_still_only_arms_is_held_too(self):
        self.armed_on_home()
        self.scan_shows(net("Home-Test", active=True, known=True, security="open"))    # its row changed: dropped
        self.assertEqual((self.page.sure.armed, self.page.main.get_label()), (None, "Disconnect"))
        self.page.main_action()                     # the quick tap meant for "Disconnect now?"
        self.assertEqual((self.threads, self.page.main.get_label(), self.said[-1]), ([], "Disconnect", netlogic.CHANGED))
        self.look()
        self.page.main_action()                     # after the pause: a first tap that only asks
        self.assertEqual((self.threads, self.page.main.get_label()), ([], "Disconnect now?"))

    def unarmed_quick_tap_after(self, newer):
        """The button says Disconnect (not armed) and a newer read turns it into Join: the tap meant to arm Disconnect
        joins nothing, and says the button changed."""
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.page.nets.rowset.click(0)
        self.assertEqual((self.page.sure.armed, self.page.main.get_label()), (None, "Disconnect"))
        self.look()
        newer()
        self.assertEqual((self.page.mode(), self.page.main.get_label()), ("known", "Join"))
        self.clock[0] += self.screens.Network.HOLD_S / 2
        self.page.main_action()
        self.assertEqual((self.threads, self.changes(), self.said[-1]), ([], [], netlogic.CHANGED))
        self.clock[0] += self.screens.Network.HOLD_S / 2
        self.page.main_action()                     # once HOLD_S has passed, Join is the owner's own
        self.run_threads()
        self.assertEqual(self.changes(), [("net_join", {"ssid": "Home-Test"})])

    def test_an_unarmed_disconnect_turned_into_join_by_a_newer_status_joins_nothing_on_a_quick_tap(self):
        self.unarmed_quick_tap_after(lambda: self.status_connected("Cafe-Test"))

    def test_an_unarmed_disconnect_turned_into_join_by_a_newer_scan_joins_nothing_on_a_quick_tap(self):
        self.unarmed_quick_tap_after(lambda: self.scan_shows(net("Home-Test", known=True), net("Cafe-Test", active=True, known=True)))

    def test_an_unarmed_wifi_off_turned_into_turn_on_sends_nothing_on_a_quick_tap(self):
        self.status_connected(None)
        self.look()
        self.assertEqual(self.page.wifi.get_label(), "Turn Wi-Fi off")
        self.page.statused(self.page.radios.issue(), {"ok": True, "wifi": {"enabled": False, "summary": "Wi-Fi: off"}})
        self.assertEqual(self.page.wifi.get_label(), "Turn Wi-Fi on")
        self.page.toggle_wifi()                     # the tap meant to arm Turn Wi-Fi off
        self.assertEqual((self.threads, self.said[-1]), ([], netlogic.CHANGED))
        self.look()
        self.page.toggle_wifi()
        self.run_threads()
        self.assertEqual(self.changes(), [("net_wifi", {"mode": "on"})])

    def test_a_scan_that_closes_the_choice_holds_scan_again(self):
        self.scan_shows(net("Cafe-Test", known=True))
        self.page.nets.rowset.click(0)
        self.look()
        self.scan_shows(net("Park-Test"))           # the chosen row is gone: the button says Scan again
        self.assertEqual(self.page.main.get_label(), "Scan again")
        self.page.main_action()                     # the tap meant for Join
        self.assertEqual((self.threads, self.said[-1]), ([], netlogic.CHANGED))

    def test_a_join_that_now_needs_no_password_is_held_though_it_still_says_join(self):
        self.scan_shows(net("Cafe-Test"))
        self.page.nets.rowset.click(0)
        self.look()
        self.assertEqual((self.page.mode(), self.page.main.get_label()), ("password", "Join"))
        self.scan_shows(net("Cafe-Test", known=True))   # now saved: the same Join would join in one tap
        self.assertEqual((self.page.mode(), self.page.main.get_label()), ("known", "Join"))
        self.page.main_action()
        self.assertEqual((self.threads, self.said[-1]), ([], netlogic.CHANGED))
        self.look()
        self.page.main_action()
        self.run_threads()
        self.assertEqual(self.changes(), [("net_join", {"ssid": "Cafe-Test"})])

    def test_control_the_owners_own_taps_are_never_held(self):
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.look()                                 # from here the clock stands still: no time passes between taps
        self.page.nets.rowset.click(0)              # the owner's tap changed the button: no hold
        self.page.main_action()                     # arms at once
        self.assertEqual((self.threads, self.page.main.get_label()), ([], "Disconnect now?"))
        self.page.main_action()                     # and the confirm right after it is not held either
        self.assertEqual(len(self.threads), 1)
        self.shell.page = "home"
        self.run_threads()
        self.assertEqual(self.changes(), [("net_wifi", {"mode": "disconnect"})])
        self.shell.page = "network"
        self.status_connected(None)                 # a read sent after the change: the radios are known again
        self.look()
        self.page.toggle_wifi()                     # Turn Wi-Fi off: arms, then confirms, with no pause
        self.page.toggle_wifi()
        self.run_threads()
        self.assertEqual(self.changes()[-1], ("net_wifi", {"mode": "off"}))
        self.page.nets.rowset.click(1)              # Cafe-Test: Join right after choosing it
        self.page.main_action()
        self.run_threads()
        self.assertEqual(self.changes()[-1], ("net_join", {"ssid": "Cafe-Test"}))
        self.assertNotIn(netlogic.CHANGED, self.said)

    def test_a_held_button_is_freed_by_the_owners_own_tap_that_changes_it(self):
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.page.nets.rowset.click(0)
        self.look()
        self.status_connected("Cafe-Test")          # Join, held
        self.page.cancel()                          # the owner's own Cancel: Scan again is what they asked for
        self.assertEqual(self.page.main.get_label(), "Scan again")
        self.page.main_action()
        self.assertEqual((len(self.threads), self.said[-1]), (1, "Scanning..."))
        self.assertNotIn(netlogic.CHANGED, self.said)

    def test_a_forget_prompt_is_dropped_when_the_row_changes_or_goes(self):
        self.status_connected(None)
        self.scan_shows(net("Home-Test", known=True))
        self.page.nets.rowset.click(0)
        self.page.forget()
        self.assertEqual(self.page.forget_b.get_label(), "Forget this network?")
        self.scan_shows(net("Home-Test", known=True, active=True))
        self.assertEqual((self.page.sure.armed, self.page.forget_b.get_label()), (None, "Forget"), "its state changed")
        self.page.forget()
        self.status_connected("Cafe-Test")
        self.assertIsNone(self.page.sure.armed, "the connected network changed")
        self.page.forget()
        self.scan_shows(net("Cafe-Test", known=True, active=True))
        self.assertEqual((self.page.chosen, self.page.sure.armed), (None, None), "the row is gone: the choice is closed")
        self.page.forget()
        self.assertEqual((self.threads, self.changes()), ([], []))

    def test_a_forget_prompt_is_dropped_when_the_connected_network_changes_elsewhere(self):
        self.status_connected("Cafe-Test")
        self.scan_shows(net("Cafe-Test", active=True, known=True), net("Home-Test", known=True))
        self.page.nets.rowset.click(1)
        self.page.forget()
        self.assertEqual(self.page.sure.armed, ("forget", "Home-Test"))
        self.status_connected("Park-Test")          # Home-Test's own row is unchanged, but the phone moved
        self.assertEqual((self.page.sure.armed, self.page.chosen["active"]), (None, False))
        self.page.forget()
        self.assertEqual(self.threads, [], "the next tap only asks again")

    def test_a_scan_that_drops_the_chosen_row_also_drops_a_typed_password(self):
        self.scan_shows(net("Cafe-Test"))
        self.page.nets.rowset.click(0)
        self.type_in(PW)
        self.scan_shows(net("Park-Test"))
        self.assertEqual((self.page.chosen, len(self.page.secret)), (None, 0))
        self.assert_nothing_holds_the_password()

    # --- the password while a join waits -----------------------------------------------------------------------------
    def test_keys_typed_while_a_join_waits_are_not_kept(self):
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.page.main_action()
        self.type_in("fake-again-1111")
        self.assertEqual(len(self.page.secret), 0, "the keyboard types nothing while a join waits")
        self.assertEqual(self.page.typed.get_label(), " ")

    def test_keys_not_kept_while_busy_are_said_to_be_not_kept(self):
        for busy_with in ("join", "scan"):
            with self.subTest(busy_with=busy_with):
                self.chosen(net("Cafe-Test"))
                if busy_with == "join":
                    self.type_in(PW)
                    self.page.main_action()
                else:
                    self.page.scan(True)
                self.said.clear()
                self.type_in("ab")
                self.assertEqual((len(self.page.secret), self.said), (0, [netlogic.KEYS_WAIT, netlogic.KEYS_WAIT]))
                self.shell.page = "home"
                self.run_threads()
                self.shell.page = "network"
                self.chosen(net("Cafe-Test"))
                self.said.clear()
                self.type_in("ab")
                self.assertEqual((len(self.page.secret), self.said), (2, []), "free again: kept, and nothing said")
                self.page.cancel()

    def test_every_answer_to_a_join_empties_the_password(self):
        for answer in ({"ok": True, "message": "Connected."}, {"ok": False, "error": "Not accepted."}, TimeoutError("late")):
            with self.subTest(answer=answer):
                self.chosen(net("Cafe-Test"))
                self.type_in(PW)
                self.core.answers["net_join"] = answer
                self.page.main_action()
                self.page.secret.key("x")            # whatever is in the buffer when the answer comes
                self.shell.page = "home"
                self.run_threads()
                self.shell.page = "network"
                self.assertEqual(len(self.page.secret), 0)

    # --- replies from an earlier visit ------------------------------------------------------------------------------
    def come_back(self):
        self.shell.page = "home"
        self.page.leave()
        self.shell.page = "network"
        self.page.enter()

    def test_a_join_from_an_earlier_visit_keeps_the_choice_made_since(self):
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.core.answers["net_join"] = {"ok": True, "message": "Connected to Cafe-Test."}
        self.page.main_action()
        self.come_back()
        self.chosen(net("Park-Test", security="open"))
        self.page.other_network()
        self.type_in("Barn")
        self.run_one(0)                             # the earlier visit's join answers now
        self.assertEqual((self.page.mode(), self.page.ssid_entry.text), ("other", "Barn"), "the new editor is kept")
        self.assertFalse(self.page.busy, "the join is over, so the page is free again")
        self.assertEqual(self.said[-1], "Connected to Cafe-Test.", "its outcome is still said")
        self.assertEqual(len(self.threads), 2, "this visit's first status, and a fresh read after the confirmed join")
        self.run_threads()
        self.assertIn(("net_status", {"fresh": True}), self.core.posts)
        self.assertEqual((self.page.mode(), self.page.ssid_entry.text), ("other", "Barn"))

    def test_a_status_sent_before_an_earlier_visits_confirmed_join_is_refused(self):
        home = {"ok": True, "wifi": {"enabled": True, "state": "connected", "ssid": "Home-Test", "summary": "Wi-Fi: Home-Test"}}
        cafe = {"ok": True, "wifi": {"enabled": True, "state": "connected", "ssid": "Cafe-Test", "summary": "Wi-Fi: Cafe-Test"}}
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.page.nets.rowset.click(1)
        self.core.answers["net_join"] = {"ok": True, "message": "Connected to Cafe-Test."}
        self.page.main_action()                     # the join to Cafe-Test waits (thread 0)
        self.come_back()                            # this visit's first status (thread 1)
        self.core.answers["net_status"] = home
        self.run_one(1)                             # answered before the join took effect; its scan is put off
        self.page.poll()                            # a poll sent while the join still runs
        self.run_one(0)                             # the earlier visit's join is confirmed: the put-off scan starts
        self.core.answers["net_scan"] = {"ok": True, "networks": [net("Home-Test", known=True),
                                                                 net("Cafe-Test", active=True, known=True)], "saved": []}
        self.run_one(1)                             # the scan, sent after the join, shows Cafe-Test connected
        rows = [w[0] for w in self.page.nets.rowset.words]
        self.look()
        self.page.nets.rowset.click(rows.index("Home-Test"))
        self.run_one(0)                             # the poll sent before the join arrives last: refused
        self.assertEqual((self.page.mode(), self.page.main.get_label()), ("known", "Join"))
        self.core.answers["net_status"] = cafe
        self.run_threads()                          # the fresh read started after the join is drawn
        self.assertEqual(netlogic.connected_to(self.page.status_data), (True, "Cafe-Test"))
        self.page.main_action()
        self.page.main_action()
        self.run_threads()
        self.assertNotIn(("net_wifi", {"mode": "disconnect"}), self.changes(), "Cafe-Test is never dropped")

    def test_an_earlier_visits_answer_leaves_this_visits_typing_alone(self):
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.page.main_action()
        self.come_back()
        self.chosen(net("Park-Test"))
        self.type_in("x")                           # the page is still busy with the earlier join
        self.assertEqual((len(self.page.secret), self.said[-1]), (0, netlogic.KEYS_WAIT))
        self.page.secret.key("y")                   # whatever this visit's buffer holds when the answer comes
        self.run_one(0)                             # the earlier visit's join answers
        self.assertEqual((self.page.ssid(), len(self.page.secret)), ("Park-Test", 1), "this visit's typing is kept")
        self.type_in("z")
        self.assertEqual(len(self.page.secret), 2, "and the page takes keys again")

    def test_a_first_status_from_an_earlier_visit_starts_no_scan(self):
        self.page.enter()
        self.come_back()
        self.assertEqual(len(self.threads), 2)
        self.run_one(0)                             # the earlier visit's first status
        self.assertEqual(len(self.threads), 1, "it started nothing: only this visit's status is waiting")
        self.run_one(0)
        self.assertEqual([p for p in self.core.posts if p[0] == "net_scan"], [], "the scan has not run yet")
        self.assertEqual(len(self.threads), 1, "this visit's own status starts this visit's scan")

    def test_a_scan_from_an_earlier_visit_does_not_touch_the_choice_and_this_visit_still_scans(self):
        self.core.answers["net_scan"] = [{"ok": True, "networks": [net("Old-Test")]}, {"ok": True, "networks": [net("Park-Test")]}]
        self.page.scan(True)                        # Scan again, then the screen is left before it answers
        self.come_back()
        self.chosen(net("Park-Test"))
        self.run_one(1)                             # this visit's status: its scan waits for the page to be free
        self.assertEqual(len(self.threads), 1)
        self.run_one(0)                             # the earlier visit's scan answers
        self.assertEqual(self.page.ssid(), "Park-Test", "the choice is not dropped by an earlier visit's list")
        self.assertIsNone(self.page.scan_data)
        self.assertEqual(len(self.threads), 1, "the page is free, so this visit's own scan has started")
        self.run_threads()
        self.assertEqual([w[0] for w in self.page.nets.rowset.words], ["Park-Test"], "this visit's own scan ran")
        self.assertEqual(self.page.ssid(), "Park-Test")

    # --- what an earlier visit read is not trusted in the next one ----------------------------------------------------
    def moved_while_away(self):
        """Visit 1: Home-Test is connected and listed first. The owner leaves; while the page is not showing, the phone
        moves to Cafe-Test (the browser's /network page, or NetworkManager roaming). The owner comes back; this visit's
        first radios read is still waiting (thread 0)."""
        self.status_connected("Home-Test")
        self.scan_shows(net("Home-Test", active=True, known=True), net("Cafe-Test", known=True))
        self.run_threads()
        self.look()
        self.come_back()
        self.look()
        self.core.answers["net_status"] = self.CAFE

    def test_coming_back_starts_at_the_floor(self):
        self.moved_while_away()
        self.assertIsNone(self.page.status_data, "the earlier visit's radios read is forgotten")
        self.assertEqual(self.page.lines.get_label(), netlogic.REREAD)
        self.assertEqual(self.page.wifi.get_label(), "Wi-Fi: not known yet")
        self.assertFalse(self.page.wifi.sensitive)
        self.assertIsNone(self.page.scan_data)
        self.assertEqual(self.page.nets.rowset.words, [(netlogic.LIST_REREAD, "")], "no row of the earlier list is offered")

    def test_coming_back_then_tapping_where_the_connected_row_was_sends_nothing(self):
        self.moved_while_away()
        self.page.nets.rowset.click(0)              # where Home-Test was drawn in the earlier visit
        tapped = (self.page.mode(), self.said[-1] if self.said else None)
        self.page.main_action()
        self.page.main_action()
        self.run_threads()
        self.assertNotIn(("net_wifi", {"mode": "disconnect"}), self.changes(), "Cafe-Test is never dropped")
        self.assertEqual(tapped, ("idle", netlogic.LIST_WAIT), "the waiting row chooses nothing")

    def test_coming_back_then_disconnect_before_the_first_read_sends_nothing(self):
        self.moved_while_away()
        self.chosen(net("Home-Test", active=True, known=True))     # a row that says connected, before any read of this visit
        self.assertEqual(self.page.main.get_label(), "Disconnect")
        self.page.main_action()
        self.page.main_action()                     # Disconnect now?
        self.assertEqual(self.said[-1], netlogic.DISCONNECT_UNKNOWN)
        self.run_threads()
        self.assertEqual([c for c in self.changes() if c[0] == "net_wifi"], [], "Cafe-Test is never dropped")
        self.assertEqual(netlogic.connected_to(self.page.status_data), (True, "Cafe-Test"), "this visit's read is drawn")

    def test_coming_back_then_turning_wifi_off_twice_sends_nothing(self):
        self.moved_while_away()
        self.page.toggle_wifi()
        self.page.toggle_wifi()
        self.assertEqual(self.said[-1], netlogic.WIFI_UNKNOWN)
        self.run_threads()
        self.assertNotIn(("net_wifi", {"mode": "off"}), self.changes(), "the Wi-Fi is not switched on an earlier visit's read")

    def test_coming_back_the_first_list_holds_row_taps(self):
        self.moved_while_away()
        self.core.answers["net_scan"] = {"ok": True, "networks": [net("Home-Test", known=True),
                                                                 net("Cafe-Test", active=True, known=True)], "saved": []}
        self.run_threads()                          # this visit's radios read, then its scan
        rows = [w[0] for w in self.page.nets.rowset.words]
        self.assertEqual(sorted(rows), ["Cafe-Test", "Home-Test"])
        self.page.nets.rowset.click(0)              # the finger was on the waiting row when the list came
        self.assertEqual((self.page.chosen, self.said[-1]), (None, netlogic.LIST_CHANGED))
        self.look()
        self.page.nets.rowset.click(rows.index("Cafe-Test"))
        self.assertEqual((self.page.ssid(), self.page.main.get_label()), ("Cafe-Test", "Disconnect"))

    # --- a sending thread that cannot start -----------------------------------------------------------------------
    def test_a_sending_thread_that_cannot_start_drops_the_password_and_frees_the_page(self):
        class Refuses:
            def __init__(s, target, daemon=None):
                pass

            def start(s):
                raise RuntimeError("can't start new thread")
        self.screens.threading = types.SimpleNamespace(Thread=Refuses)
        self.chosen(net("Cafe-Test"))
        self.type_in(PW)
        self.page.main_action()                     # nothing escapes
        self.assertEqual((len(self.page.secret), self.page.busy, self.page.main.sensitive), (0, False, True))
        self.assertIn(netlogic.FALLBACK, self.said)
        self.assertEqual(self.core.posts, [])
        self.assert_nothing_holds_the_password()
        self.page.forget()
        self.page.toggle_wifi()
        self.page.scan(True)
        self.assertFalse(self.page.busy, "every action can be tried again")


class ShellParts(unittest.TestCase):
    """The shell's side: Network has no Home tile, go() tells the page it is left, and Core gives net_* their own wait."""

    def setUp(self):
        from tests.test_duo_native import Toolkit
        names = ("gi", "gi.repository", "opos.native.shell", "opos.native.screens", "opos.native.ui",
                 "opos.native.blank", "opos.native.displayconfig", "opos.native.posture", "opos.native.layout")
        saved = {n: sys.modules.get(n) for n in names}

        def restore():
            for n, m in saved.items():
                sys.modules.pop(n, None)
                if m is not None:
                    sys.modules[n] = m
        self.addCleanup(restore)
        for n in names:
            sys.modules.pop(n, None)
        gi = Toolkit("gi")
        gi.require_version = lambda *a: None
        gi.repository = Toolkit("gi.repository")
        sys.modules["gi"], sys.modules["gi.repository"] = gi, gi.repository
        self.shell = importlib.import_module("opos.native.shell")

    def test_pages_without_a_tile_are_not_on_home(self):
        pages = self.shell.screens.PAGES
        names = [n for n, _t in self.shell.Shell.tiles(pages)]
        self.assertNotIn("network", names)
        self.assertIn("settings", names)
        self.assertNotIn("lock", names)
        self.assertEqual(len(names), len(pages) - 3, "lock and home have no title; network no tile")

    def test_go_tells_the_page_left_before_the_new_one_enters(self):
        events = []

        def page(name):
            return types.SimpleNamespace(enter=lambda: events.append(("enter", name, win.page)),
                                         leave=lambda: events.append(("leave", name, win.page)), tick=lambda: None)
        win = types.SimpleNamespace(page="settings", hand="right", warming=False, wanted=None,
                                    pages={n: page(n) for n in ("settings", "network", "home", "lock")},
                                    strip=types.SimpleNamespace(set_opacity=lambda v: None),
                                    set_shade=lambda v: None, show_page=lambda *a: None)
        self.shell.Shell.go(win, "network")
        self.shell.Shell.go(win, "network")         # the same page: nothing left, nothing entered
        self.shell.Shell.go(win, "lock")            # the lid closing goes to the lock screen this way
        self.assertEqual(events, [("leave", "settings", "network"), ("enter", "network", "network"),
                                  ("leave", "network", "lock"), ("enter", "lock", "lock")])
        win.warming = True
        self.shell.Shell.go(win, "home")
        self.assertEqual(len(events), 4, "under the start-up cover nothing is left or entered")

    def test_a_page_whose_leave_raises_still_lets_the_lid_close_to_the_lock_screen(self):
        events = []

        def broken():
            raise RuntimeError("page code went wrong")
        pages = {"network": types.SimpleNamespace(enter=lambda: None, leave=broken, tick=lambda: None),
                 "lock": types.SimpleNamespace(enter=lambda: events.append("enter lock"), leave=lambda: None,
                                               tick=lambda: events.append("tick lock"))}
        win = types.SimpleNamespace(page="network", hand="right", warming=False, wanted=None, pages=pages, drag=None,
                                    strip=types.SimpleNamespace(set_opacity=lambda v: None), set_shade=lambda v: None,
                                    show_page=lambda name, how: events.append("show " + name), blank_if_closed=lambda: None)
        win.go = lambda n: self.shell.Shell.go(win, n)
        timers = []
        err = io.StringIO()
        with mock.patch.object(self.shell, "GLib", types.SimpleNamespace(timeout_add=lambda ms, fn: timers.append(fn))), \
                mock.patch.object(sys, "stderr", err):
            self.assertIs(self.shell.Shell.lid_changed(win, True), False)
        self.assertEqual((win.page, events, win.lid_closed), ("lock", ["enter lock", "tick lock", "show lock"], True))
        self.assertEqual(len(timers), 1, "the blank is still scheduled")
        self.assertIn("RuntimeError", err.getvalue())
        self.assertNotIn("page code went wrong", err.getvalue(), "the error's own words are not printed")

    def test_core_net_gives_the_action_its_own_wait_and_returns_the_cores_reply(self):
        seen = []

        class Answer(io.BytesIO):
            pass

        def urlopen(req, timeout=None):
            seen.append((req.full_url, json.loads(req.data.decode()), timeout))
            return Answer(b'{"ok": true, "message": "Wi-Fi is on."}')
        core = self.shell.Core(base="http://opos.invalid")
        core.token = "fake-token"
        with mock.patch.object(self.shell.urllib.request, "urlopen", urlopen):
            got = core.net("net_wifi", {"mode": "on"}, 35)
            core.post("call", number="+1 555 0100")
        self.assertEqual(got, {"ok": True, "message": "Wi-Fi is on."})
        self.assertEqual(seen, [("http://opos.invalid/api", {"mode": "on", "action": "net_wifi"}, 35),
                                ("http://opos.invalid/api", {"number": "+1 555 0100", "action": "call"}, 10)])

    def test_the_waits_cover_the_cores_own_budgets(self):
        from opos import net
        wait = self.shell.screens.Network.WAIT
        self.assertGreater(wait["net_join"], net.JOIN_WAIT_S + 60, "the core's join budget")
        self.assertGreater(wait["net_forget"], net.BUDGET_S + 20)
        self.assertGreater(wait["net_wifi"], net.BUDGET_S + 10)
        self.assertGreater(wait["net_scan"], net.BUDGET_S + net.SCAN_WAIT_S)
        self.assertGreater(wait["net_status"], net.BUDGET_S)
        self.assertEqual(set(wait), set(self.shell.screens.Network.CHANGES) | {"net_status", "net_scan"})
        from opos import net_page
        self.assertEqual(set(wait), set(net_page.ACTIONS), "only the server's own net_* actions")


# --- the source: nothing prints, logs, shows or saves the password ---------------------------------------------------
class NoPasswordShown(unittest.TestCase):
    SINK = re.compile(r"\b(print|say|label|set_label|set_text|write)\s*\(|\blog\w*\s*\(|\blogging\.|"
                      r"\.(debug|info|warning|error|exception|critical)\s*\(", re.I)
    NAMES = ("password", "secret", "reveal")

    def sources(self):
        screens = (NATIVE / "screens.py").read_text(encoding="utf-8")
        tree = ast.parse(screens)
        cls, = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Network"]
        yield "Network", ast.get_source_segment(screens, cls), cls
        text = (NATIVE / "netlogic.py").read_text(encoding="utf-8")
        yield "netlogic", text, ast.parse(text)

    def test_no_line_has_a_password_next_to_a_sink(self):
        for where, text, _tree in self.sources():
            for i, line in enumerate(text.splitlines(), 1):
                code = line.split("#")[0]
                if "password" in code.lower() or "secret" in code.lower():
                    with self.subTest(where=where, line=i):
                        self.assertIsNone(self.SINK.search(code), line)

    def test_no_sink_call_is_given_the_password(self):
        sinks = {"print", "say", "label", "set_label", "set_text", "write", "debug", "info", "warning", "error",
                 "exception", "critical", "log", "format", "dumps", "dump", "open"}

        def bad_names(node):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "mask":
                return []
            found = []
            if isinstance(node, ast.Name) and any(w in node.id.lower() for w in self.NAMES):
                found.append(node.id)
            if isinstance(node, ast.Attribute) and any(w in node.attr.lower() for w in self.NAMES):
                found.append(node.attr)
            for child in ast.iter_child_nodes(node):
                found += bad_names(child)
            return found
        checked = 0
        for where, _text, tree in self.sources():
            for node in ast.walk(tree):
                name = None
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", getattr(node.func, "id", None))
                if name in sinks or isinstance(node, ast.JoinedStr) or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)):
                    checked += 1
                    with self.subTest(where=where, line=node.lineno):
                        self.assertEqual(bad_names(node), [], ast.unparse(node))
        self.assertGreater(checked, 10)

    def test_reveal_is_read_only_where_the_join_is_sent(self):
        screens = (NATIVE / "screens.py").read_text(encoding="utf-8")
        self.assertEqual(screens.count(".reveal()"), 1)
        self.assertIn('fields["password"] = self.secret.reveal()', screens)
        join = screens[screens.index("    def join(self):"):screens.index("    def reply_to(self, then):")]
        # read into the request, then the page's copy is emptied, and only then is the request handed over (a thread
        # that cannot start answers at once, so nothing may be left to clear after send())
        self.assertLess(join.index("self.secret.reveal()"), join.index("self.secret.clear()"))
        self.assertLess(join.index("self.secret.clear()"), join.index('self.send("net_join", fields, self.reply_to(self.done))'))

    def test_the_page_writes_no_file_and_logs_nothing(self):
        for where, text, _tree in self.sources():
            for pattern in (r"\bopen\(", r"\bprint\(", r"(?m)^\s*(import|from)\s+logging\b|\blogging\.", r"\bstderr\b", r"\btraceback\b", r"\bshelve\b",
                            r"\bpickle\.", r"\bjson\."):
                self.assertIsNone(re.search(pattern, text), (where, pattern))


class Docs(unittest.TestCase):
    def test_native_md_describes_the_network_screen_and_what_was_not_run(self):
        doc = (ROOT / "packaging" / "duo-os" / "NATIVE.md").read_text(encoding="utf-8")
        self.assertIn("src/opos/native/netlogic.py", doc)
        section = doc[doc.index("## The Network screen"):]
        section = section[:section.index("\n## ", 3)]
        for words in ("net_status", "net_scan", "net_join", "net_forget", "net_wifi", "two taps", "NOT_RUN"):
            self.assertIn(words, section)

    def test_native_md_quotes_the_fixed_sentences_and_the_hold(self):
        section = " ".join((ROOT / "packaging" / "duo-os" / "NATIVE.md").read_text(encoding="utf-8").split())
        for sentence in (netlogic.KEYS_WAIT, netlogic.CHANGED, netlogic.LIST_CHANGED, netlogic.REREAD,
                         netlogic.LIST_REREAD, netlogic.LIST_WAIT):
            self.assertIn('"%s"' % sentence, section)
        hold = re.search(r"^    HOLD_S = ([0-9.]+)", (NATIVE / "screens.py").read_text(encoding="utf-8"), re.M).group(1)
        self.assertIn("For %s s after that" % hold, section)

    def test_the_final_doc_names_every_switch_the_native_shell_reads(self):
        doc = (ROOT / "docs" / "duo-os-final.md").read_text(encoding="utf-8")
        used = set()
        for path in NATIVE.glob("*.py"):
            used |= set(re.findall(r"\bOPOS_[A-Z_]+\b", path.read_text(encoding="utf-8")))
        self.assertIn("OPOS_LOCAL_URL", used)
        self.assertEqual(sorted(u for u in used if u not in doc), [])

    def test_the_roadmap_keeps_the_device_checks_not_run(self):
        roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
        line = next(par for par in roadmap.split("\n- ") if "native Network screen" in par)
        self.assertIn("NOT_RUN", line)
        for words in ("join", "forget", "disconnect", "Wi-Fi off"):
            self.assertIn(words, line)


if __name__ == "__main__":
    unittest.main()
