# SPDX-License-Identifier: GPL-3.0-or-later
"""Panes in a real browser: surfaces, hinge, mirror, targets, contrast, palette, Hand, the OS pages, the keyboard, motion.

One headless Chromium run (tests/panes_browser_driver.mjs, over the DevTools port, node 22+) at 1392 x 900 CSS px against a real
server started with OPOS_PANES=1 and a 675,42 span, in both hands and both themes. The run measures, these tests assert. Skips
without node 22+ or a headless Chromium (OPOS_TEST_CHROME, the Playwright cache, chromium/google-chrome on PATH, or Edge/Chrome
on Windows). docs/jaryo-panes.md sections 2 to 5 say what is asserted and why.
"""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from opos import firstrun, web
from opos.span import Span

try:
    from tests.test_uxr_acceptance import find_chrome
except ImportError:  # that module imports pwd: Windows has none
    def find_chrome():
        return None

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "tests" / "panes_browser_driver.mjs"
NODE = shutil.which("node")
DUO = Span(675, 42)
WHITE, BLACK = "rgb(243, 240, 232)", "rgb(0, 0, 0)"
WHITE_HEX, BLACK_HEX = "#f3f0e8", "#000000"
APPS = ("home", "allapps", "phone", "messages", "helper", "settings", "shade", "lock-start", "lock")
WINDOWS_BROWSERS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")


def browser():
    env = os.environ.get("OPOS_TEST_CHROME")
    if env:
        return env if os.access(env, os.X_OK) else None
    found = find_chrome()
    if found:
        return found
    if os.name == "nt":
        return next((p for p in WINDOWS_BROWSERS if os.path.isfile(p)), None)
    return None


def node_has_websocket():
    if not NODE:
        return False
    try:
        return subprocess.run([NODE, "-e", "process.exit(typeof WebSocket==='function'?0:1)"], timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_env(config_dir, **extra):
    """The environment of a test server: no OPOS_PANES, OPOS_OSK, OPOS_FIRSTRUN, OPOS_NETWORK or OPOS_CONFIG_DIR from outside."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPOS_PANES", "OPOS_OSK", "OPOS_FIRSTRUN", "OPOS_NETWORK", "OPOS_CONFIG_DIR"))}
    env.update(OPOS_CONFIG_DIR=config_dir, **extra)
    return env


class PanesBrowser(unittest.TestCase):
    r = None

    @classmethod
    def setUpClass(cls):
        chrome = browser()
        if not chrome or not node_has_websocket():
            return
        d = tempfile.mkdtemp(prefix="opos-panes-")
        try:
            env = clean_env(d, OPOS_PANES="1", OPOS_OSK="1", OPOS_NETWORK="1", OPOS_FIRSTRUN="1")
            outfile = Path(d) / "out.json"
            with mock.patch.dict(os.environ, env, clear=True):
                undo = firstrun.install(web)  # the /firstrun and /timezone routes, as opos.web.main installs them
                server = web.make_server(0, web.Phone(), span=DUO)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    p = subprocess.run([NODE, str(DRIVER), chrome, "http://127.0.0.1:%d" % server.server_address[1], str(free_port()),
                                        str(Path(d) / "profile"), str(outfile)], capture_output=True, text=True, timeout=400)
                finally:
                    server.shutdown()
                    server.server_close()
                    if undo:
                        undo()
            if p.returncode:
                raise AssertionError("browser driver failed: " + (p.stderr or p.stdout)[-4000:])
            cls.r = json.loads(outfile.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(d, ignore_errors=True)  # the browser may still be letting go of its profile files on Windows

    def setUp(self):
        if self.r is None:
            self.skipTest("no node 22+ or headless chromium")

    # -- helpers ----------------------------------------------------------------------------------------------------------
    def states(self, prefix="states"):
        for k, m in self.r[prefix].items():
            scheme, hand, name = k.split("/", 2)
            yield k, scheme, hand, name, m

    @staticmethod
    def thumb(hand, box):
        """Whether a box sits in the thumb pane: the right pane in Right, the left pane in Left."""
        return box["l"] >= 717 if hand == "right" else box["r"] <= 675

    @staticmethod
    def listing(hand, box):
        return box["r"] <= 675 if hand == "right" else box["l"] >= 717

    # -- the rules for every state of every page ----------------------------------------------------------------------------
    def test_every_state_is_two_panes_in_the_asked_hand(self):
        for k, scheme, hand, name, m in self.states():
            self.assertEqual((m["attrs"]["ff"], m["attrs"]["panes"], m["attrs"]["hand"]), ("foldable", "two", hand), k)
        for k, scheme, hand, name, m in self.states("os"):
            self.assertEqual((m["attrs"]["panes"], m["attrs"]["hand"]), ("two", hand), k)

    def test_the_two_surfaces_are_one_black_and_one_white_and_the_hinge_is_black(self):
        for prefix in ("states", "os"):
            for k, scheme, hand, name, m in self.states(prefix):
                left, right = (WHITE, BLACK) if hand == "right" else (BLACK, WHITE)
                self.assertEqual((m["surface"]["left"], m["surface"]["right"], m["surface"]["hinge"]), (left, right, BLACK), k)
                lx, rx = (WHITE_HEX, BLACK_HEX) if hand == "right" else (BLACK_HEX, WHITE_HEX)
                self.assertEqual((m["pixels"]["left"], m["pixels"]["right"]), (lx, rx), k)
                self.assertEqual((m["pixels"]["hingeTop"], m["pixels"]["hingeMid"], m["pixels"]["hingeBottom"]), (BLACK_HEX,) * 3, k)

    def test_no_text_and_no_control_touches_the_hinge(self):
        for prefix in ("states", "os"):
            for k, scheme, hand, name, m in self.states(prefix):
                self.assertGreater(m["n"], 0, k)
                self.assertEqual(m["hinge"], [], k)

    def test_every_target_is_at_least_48_by_48(self):
        for prefix in ("states", "os"):
            for k, scheme, hand, name, m in self.states(prefix):
                self.assertEqual(m["small"], [], k)

    def test_contrast_is_at_least_15_to_1(self):
        for prefix in ("states", "os"):
            for k, scheme, hand, name, m in self.states(prefix):
                self.assertEqual(m["low"], [], k)
                self.assertGreaterEqual(m["minCr"], 15, k)

    def test_only_the_four_palette_colours_and_no_effects(self):
        for prefix in ("states", "os"):
            for k, scheme, hand, name, m in self.states(prefix):
                self.assertEqual(m["off"], [], k)
                self.assertEqual(m["bad"], [], k)
                self.assertLessEqual(set(m["colors"]), {"rgb(0,0,0)", "rgb(230,226,217)", "rgb(243,240,232)", "rgb(26,26,26)", "transparent"}, k)

    def test_the_pane_colours_replace_the_theme_so_light_and_dark_look_the_same(self):
        for k, scheme, hand, name, m in self.states():
            if scheme == "light":
                twin = self.r["states"]["dark/%s/%s" % (hand, name)]
                self.assertEqual((m["surface"], m["pixels"]), (twin["surface"], twin["pixels"]), k)

    # -- where things sit -------------------------------------------------------------------------------------------------
    def test_the_thumb_items_are_in_the_thumb_pane_and_the_list_items_in_the_list_pane(self):
        for k, scheme, hand, name, m in self.states():
            at = m["at"]
            if name in ("home", "settings", "phone", "messages", "helper", "allapps"):
                self.assertTrue(at["status"] and all(self.thumb(hand, {"l": a, "r": b}) for a, b in at["status"]), (k, at["status"]))
                self.assertEqual(at["masthead"]["l"], 0 if hand == "right" else 717, k)
            if name == "home":
                self.assertEqual(at["home"]["l"], 729 if hand == "right" else 12, k)
            if name == "allapps":
                self.assertTrue(self.thumb(hand, at["appsList"]) and self.thumb(hand, at["appsClose"]), k)
            if name == "phone":
                self.assertTrue(self.thumb(hand, at["back"]) and self.thumb(hand, at["number"]) and self.listing(hand, at["contacts"]), k)
            if name == "messages":
                self.assertTrue(self.thumb(hand, at["back"]) and self.thumb(hand, at["msg"]) and self.listing(hand, at["threads"]), k)
            if name == "helper":
                self.assertTrue(self.thumb(hand, at["back"]) and self.thumb(hand, at["ask"]) and self.listing(hand, at["helper"]), k)
            if name == "settings":
                self.assertTrue(self.thumb(hand, at["settings"]) and self.thumb(hand, at["back"]), k)
            if name in ("lock", "lock-start"):
                self.assertTrue(self.thumb(hand, at["unlock"]) and self.listing(hand, at["lockTime"]), k)

    def test_the_shade_is_the_list_pane_from_its_own_edge(self):
        for k, scheme, hand, name, m in self.states():
            if name == "shade":
                self.assertEqual((m["at"]["sheet"]["l"], m["at"]["sheet"]["w"]), (0, 675) if hand == "right" else (717, 675), k)

    def test_the_os_pages_split_at_the_hinge(self):
        for k, scheme, hand, name, m in self.states("os"):
            self.assertEqual(m["cols"], "675px 42px 675px", k)

    # -- Hand ------------------------------------------------------------------------------------------------------------
    def test_hand_button_taps_flips_persists_and_takes_the_keyboard(self):
        h = self.r["hand"]
        self.assertEqual((h["start"]["hand"], h["start"]["stored"], h["start"]["label"], h["start"]["prev"]), ("right", None, "Hand: Right", "jaryo-theme"))
        self.assertGreaterEqual(h["start"]["btnH"], 48)
        self.assertGreaterEqual(h["start"]["btnW"], 48)
        self.assertEqual(h["start"]["mastheadL"], 0)
        self.assertGreaterEqual(h["start"]["settingsL"], 717)
        self.assertEqual(h["start"]["themeAfter"], '" (one screen only)"')
        self.assertEqual((h["tap"]["hand"], h["tap"]["stored"], h["tap"]["label"]), ("left", "left", "Hand: Left"))
        self.assertEqual((h["tap"]["mastheadL"], h["tap"]["settingsL"] < 675), (717, True))
        self.assertEqual((h["reload"]["hand"], h["reload"]["label"]), ("left", "Hand: Left"))
        self.assertEqual((h["tapAgain"]["hand"], h["tapAgain"]["stored"]), ("right", "right"))
        self.assertEqual((h["enter"]["hand"], h["enter"]["stored"], h["enter"]["label"]), ("left", "left", "Hand: Left"))

    def test_one_pane_windows_are_what_jaryo_paints(self):
        for key, o in self.r["off"].items():
            self.assertEqual((o["before"], o["after"], o["handDisplay"], o["themeAfter"]), ("none", "none", "none", "none"), key)
            self.assertEqual(o["handRect"], 0, key)
        self.assertIsNone(self.r["off"]["one_pane"]["ff"])
        self.assertIsNone(self.r["off"]["below_gate"]["ff"])
        self.assertEqual(self.r["off"]["slab"]["ff"], "slab")
        self.assertIsNone(self.r["off"]["slab"]["panes"])
        self.assertEqual(self.r["off"]["foldable_675"]["ff"], "foldable")  # the sheet is inert here: the media gate is false
        for key in ("one_pane", "below_gate", "slab"):
            self.assertIsNone(self.r["off"][key]["panes"], key)

    # -- the keyboard and motion -------------------------------------------------------------------------------------------
    def test_the_keyboard_halves_take_the_surface_they_sit_on(self):
        for hand in ("right", "left"):
            k = self.r["osk"][hand]
            self.assertIsNotNone(k, hand)
            first, last = k["panels"][0], k["panels"][-1]
            lft = ("rgb(26, 26, 26)", WHITE) if hand == "right" else ("rgb(230, 226, 217)", BLACK)
            rgt = ("rgb(230, 226, 217)", BLACK) if hand == "right" else ("rgb(26, 26, 26)", WHITE)
            self.assertEqual((first["keyFg"], first["keyBg"]), lft, hand)
            self.assertEqual((last["keyFg"], last["keyBg"]), rgt, hand)
            self.assertLess(first["r"], 700, hand)
            self.assertGreater(last["l"], 700, hand)

    def test_opening_and_closing_apps_ends_on_the_thumb_side_with_focus_back_on_the_tile(self):
        for hand, rows in self.r["motion"].items():
            for row in rows:
                self.assertEqual(row["st"]["app"], row["app"], hand)
                self.assertTrue(self.thumb(hand, {"l": row["st"]["backL"], "r": row["st"]["backR"]}), (hand, row))
                self.assertEqual((row["st"]["anims"], row["home"]["anims"]), (0, 0), (hand, row))
                self.assertEqual(row["home"]["app"], "home", (hand, row))
                self.assertEqual(row["home"]["homeL"], 729 if hand == "right" else 12, (hand, row))
                self.assertEqual(row["home"]["focus"], row["app"], (hand, row))

    def test_no_console_error_and_no_csp_violation_anywhere(self):
        for key, logs in self.r["logs"].items():
            self.assertEqual(logs, [], key)


class DriverWaits(unittest.TestCase):
    """The driver waits for the page it loaded, never for a fixed time (a busy machine once made it call oposUI.openApp too early)."""

    @unittest.skipUnless(NODE, "no node")
    def test_the_readiness_conditions_and_the_poll(self):
        p = subprocess.run([NODE, str(ROOT / "tests" / "panes_wait_test.mjs")], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        self.assertIn("assertions passed", p.stdout)

    def test_a_page_that_takes_three_seconds_is_waited_for(self):
        chrome = browser()
        if not chrome or not node_has_websocket():
            self.skipTest("no node 22+ or headless chromium")
        real_page = web.page

        def slow(*args, **kw):
            time.sleep(3)  # longer than the old fixed 1.4 s wait, on the first load and on every reload
            return real_page(*args, **kw)

        d = tempfile.mkdtemp(prefix="opos-panes-slow-")
        try:
            outfile = Path(d) / "out.json"
            with mock.patch.dict(os.environ, clean_env(d, OPOS_PANES="1"), clear=True), mock.patch.object(web, "page", slow):
                server = web.make_server(0, web.Phone(), span=DUO)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    p = subprocess.run([NODE, str(DRIVER), chrome, "http://127.0.0.1:%d" % server.server_address[1], str(free_port()),
                                        str(Path(d) / "profile"), str(outfile), "ready"], capture_output=True, text=True, timeout=120)
                finally:
                    server.shutdown()
                    server.server_close()
            self.assertEqual(p.returncode, 0, "browser driver failed: " + (p.stderr or p.stdout)[-3000:])
            self.assertEqual(json.loads(outfile.read_text(encoding="utf-8"))["ready"], {"app": "settings", "ff": "foldable", "panes": "two"})
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
