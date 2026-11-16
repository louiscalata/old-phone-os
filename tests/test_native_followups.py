# SPDX-License-Identifier: GPL-3.0-or-later
"""Four follow-ups to the native shell, run with the stand-in toolkit (no display, no GTK, no phone):

- the shell asks the phone's own OPOS server and nothing else: `OPOS_SERVER_URL` is taken only as
  http://127.0.0.1:PORT, the Helper's `OPOS_LOCAL_URL` is never read for it, no proxy from the environment is used and
  a redirect to another address is not followed;
- Call says what is true while its answer is missing, and a request that could not be sent never leaves a button busy;
- a state is drawn only together with the server's answer to "is there a PIN lock?": no answer is never read as "no";
- a lid watcher that stopped is looked for again, and nothing in that can leave a screen dark.
"""

import contextlib
import gc
import http.server
import importlib
import io
import os
import random
import re
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
import warnings
from pathlib import Path
from unittest import mock

from opos.native import blank, logic
from tests import test_native_screens as base
from tests.test_native_shell import Fake, StandInShell

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "src" / "opos" / "native"
DEFAULT = "http://127.0.0.1:8765"
DOC_HOST = "192.0.2.7"  # a documentation address (RFC 5737): never a real machine


def stand_in_shell(case, env=None):
    """opos.native.shell imported over the stand-in toolkit, with `env` as the environment it is imported in."""
    names = ("gi", "gi.repository", "opos.native.shell", "opos.native.screens", "opos.native.ui")
    saved = {n: sys.modules.get(n) for n in names}

    def restore():
        for n, m in saved.items():
            sys.modules.pop(n, None)
            if m is not None:
                sys.modules[n] = m

    case.addCleanup(restore)
    for n in names:
        sys.modules.pop(n, None)
    gi = base.Toolkit("gi")
    gi.require_version = lambda *a: None
    gi.repository = base.Toolkit("gi.repository")
    case.timers = []
    gi.repository.GLib = types.SimpleNamespace(
        idle_add=lambda fn, *a: fn(*a), timeout_add=lambda ms, fn, *a: case.timers.append((fn, a))
    )
    sys.modules["gi"], sys.modules["gi.repository"] = gi, gi.repository
    with mock.patch.dict(os.environ, env or {}, clear=env is not None):
        return importlib.import_module("opos.native.shell")


# --- the server's address ---------------------------------------------------------------------------------------------
class TheServersAddress(unittest.TestCase):
    BAD = (
        "https://127.0.0.1:8765",
        "http://localhost:8765",
        "http://127.0.0.1",
        "http://127.0.0.1:",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:08765",
        "http://127.0.0.1:8765/v1",
        "http://127.0.0.1:8765/?x=1",
        "http://127.0.0.1:8765#x",
        "http://user@127.0.0.1:8765",
        "http://user:word@127.0.0.1:8765",
        "http://127.0.0.1:8765@example.org",
        "http://127.0.0.1.example.org:8765",
        "http://127.0.0.2:8765",
        "http://[::1]:8765",
        "http://0.0.0.0:8765",
        "http://2130706433:8765",
        "http://0x7f.0.0.1:8765",
        "http://127.1:8765",
        "http://%s:8765" % DOC_HOST,
        "http://example.org:8765",
        "HTTP://127.0.0.1:8765",
        " http://127.0.0.1:8765",
        "http://127.0.0.1:8765 ",
        "http://127.0.0.1:8765\n",
        "http://127.0.0.1:8765\r\nHost: x",
        "http://127.0.0.1:８７６５",
        "http://127.0.0.1:87٦٥",
        "//127.0.0.1:8765",
        "127.0.0.1:8765",
        "file:///etc/passwd",
        "unix:/run/opos.sock",
        "8765",
        "0",
        "off",
        8765,
        b"http://127.0.0.1:8765",
        ["http://127.0.0.1:8765"],
    )

    def test_unset_or_empty_is_the_phones_own_server(self):
        self.assertEqual(logic.SERVER_URL, DEFAULT)
        for env in ({}, {"OPOS_SERVER_URL": ""}, {"OPOS_NETWORK": "1"}):
            self.assertEqual(logic.server_url(env), (DEFAULT, ""), env)

    def test_another_port_on_127_0_0_1_is_taken_and_nothing_else_of_it(self):
        self.assertEqual(logic.server_url({"OPOS_SERVER_URL": "http://127.0.0.1:9000"}), ("http://127.0.0.1:9000", ""))
        self.assertEqual(logic.server_url({"OPOS_SERVER_URL": "http://127.0.0.1:9000/"}), ("http://127.0.0.1:9000", ""))
        self.assertEqual(logic.server_url({"OPOS_SERVER_URL": "http://127.0.0.1:1"}), ("http://127.0.0.1:1", ""))
        self.assertEqual(
            logic.server_url({"OPOS_SERVER_URL": "http://127.0.0.1:65535"}), ("http://127.0.0.1:65535", "")
        )

    def test_anything_else_is_refused_and_said_and_the_phones_own_server_is_asked(self):
        for value in self.BAD:
            with self.subTest(value=value):
                url, note = logic.server_url({"OPOS_SERVER_URL": value})
                self.assertEqual(url, DEFAULT, "never another server")
                self.assertEqual(note, logic.SERVER_URL_REFUSED)
        self.assertIn("OPOS_SERVER_URL", logic.SERVER_URL_REFUSED)
        self.assertIn(DEFAULT, logic.SERVER_URL_REFUSED)
        self.assertNotIn("%", logic.SERVER_URL_REFUSED, "the refused value is not repeated: it may hold a password")

    def test_the_helpers_variable_is_never_the_shells_address(self):
        """OPOS_LOCAL_URL is the Helper's model server (assist.local_route). The shell read that name for the OPOS
        server, so setting it for the Helper sent the shell's token and every action to the model server."""
        helper = {"OPOS_LOCAL_URL": "http://127.0.0.1:9911/v1"}
        self.assertEqual(logic.server_url(helper), (DEFAULT, ""))
        shell = stand_in_shell(self, helper)
        self.assertEqual((shell.BASE, shell.Core().base), (DEFAULT, DEFAULT))
        for path in NATIVE.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"""(get|environ)\s*[\[(]\s*["']OPOS_LOCAL_URL""", text), path.name)

    def test_the_shell_takes_the_checked_address_and_says_a_refusal_once_in_its_log(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            shell = stand_in_shell(self, {"OPOS_SERVER_URL": "http://127.0.0.1:9000"})
        self.assertEqual(
            (shell.BASE, shell.Core().base, err.getvalue()), ("http://127.0.0.1:9000", "http://127.0.0.1:9000", "")
        )
        with contextlib.redirect_stderr(io.StringIO()) as err:
            shell = stand_in_shell(self, {"OPOS_SERVER_URL": "http://%s:8765" % DOC_HOST})
        self.assertEqual(shell.BASE, DEFAULT)
        self.assertEqual(err.getvalue(), "opos shell: %s\n" % logic.SERVER_URL_REFUSED)
        self.assertNotIn(DOC_HOST, err.getvalue())

    def test_same_server_is_scheme_host_and_port_exactly(self):
        for url in (DEFAULT + "/", DEFAULT + "/api/state", DEFAULT + "/firstrun?x=1", DEFAULT):
            self.assertTrue(logic.same_server(DEFAULT, url), url)
        for url in (
            "http://127.0.0.1:8766/",
            "https://127.0.0.1:8765/",
            "http://localhost:8765/",
            "http://127.0.0.1/",
            "http://%s:8765/" % DOC_HOST,
            "http://127.0.0.1:8765@example.org/",
            "http://x@127.0.0.1:8765/",
            "//example.org/",
            "/api/state",
            "",
            None,
            "http://[::1",
            "http://127.0.0.1:x/",
        ):
            self.assertFalse(logic.same_server(DEFAULT, url), url)


class TheShellAsksOnlyThatServer(unittest.TestCase):
    def setUp(self):
        self.shell = stand_in_shell(self, {})

    def test_no_proxy_from_the_environment_is_used(self):
        """urllib sends a loopback request through http_proxy when no_proxy does not name it: the token would leave."""
        proxy = "http://%s:3128" % DOC_HOST
        with mock.patch.dict(os.environ, {"http_proxy": proxy, "HTTP_PROXY": proxy, "all_proxy": proxy}):
            core = self.shell.Core()
        proxies = [h for h in core.opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual([h.proxies for h in proxies if h.proxies], [], "no handler holds a proxy from the environment")
        with mock.patch.dict(os.environ, {"http_proxy": proxy}):
            usual = urllib.request.build_opener()
        self.assertTrue(
            [h for h in usual.handlers if isinstance(h, urllib.request.ProxyHandler) and h.proxies],
            "the usual opener would have taken it: this check means something",
        )

    def test_a_redirect_to_another_address_is_not_followed(self):
        core = self.shell.Core()
        handler = next(h for h in core.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
        req = urllib.request.Request(DEFAULT + "/", headers={"X-OPOS-Token": "t"})
        for target in (
            "http://%s:8765/" % DOC_HOST,
            "http://127.0.0.1:8766/",
            "https://127.0.0.1:8765/",
            "http://localhost:8765/",
        ):
            with self.subTest(target=target):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    handler.redirect_request(req, io.BytesIO(b""), 302, "Found", {}, target)
                self.assertEqual(caught.exception.code, 302)
        again = handler.redirect_request(req, io.BytesIO(b""), 302, "Found", {}, DEFAULT + "/firstrun")
        self.assertEqual(again.full_url, DEFAULT + "/firstrun", "the server's own pages are still followed")

    def test_the_answer_that_redirected_away_is_closed_when_it_is_refused(self):
        """The refused answer holds the connection: left open, it is closed only when the error is dropped, with a
        ResourceWarning at each refusal."""
        core = self.shell.Core()
        handler = next(h for h in core.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
        req = urllib.request.Request(DEFAULT + "/")
        answer = io.BytesIO(b"not read")
        with warnings.catch_warnings(record=True) as said:
            warnings.simplefilter("always")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                handler.redirect_request(req, answer, 302, "Found", {}, "http://127.0.0.1:8766/")
            self.assertTrue(answer.closed, "closed where it is refused")
            self.assertEqual(
                (caught.exception.code, caught.exception.headers), (302, {}), "what a caller reads is still there"
            )
            del caught
            gc.collect()
        self.assertEqual([str(w.message) for w in said if issubclass(w.category, ResourceWarning)], [])
        kept = io.BytesIO(b"")
        handler.redirect_request(req, kept, 302, "Found", {}, DEFAULT + "/firstrun")
        self.assertFalse(kept.closed, "an answer that is followed is left to urllib, as before")

    def test_every_request_goes_through_that_opener(self):
        seen = []

        def open_(req, timeout=None):
            seen.append((req.full_url, req.get_method(), timeout))
            return io.BytesIO(b'{"battery": {}}')

        core = self.shell.Core()
        core.token = "fake-token"
        core.opener = types.SimpleNamespace(open=open_)
        core.state()
        core.post("call", number="+1 555 0100")
        core.app("clock")
        with mock.patch.object(self.shell.urllib.request, "urlopen", side_effect=AssertionError("the default opener")):
            core.net("net_status", {}, 20)
        self.assertEqual(
            seen,
            [
                (DEFAULT + "/api/state", "GET", 5),
                (DEFAULT + "/api", "POST", 10),
                (DEFAULT + "/api/clock", "GET", 5),
                (DEFAULT + "/api", "POST", 20),
            ],
        )
        source = (NATIVE / "shell.py").read_text(encoding="utf-8")
        self.assertNotIn("urllib.request.urlopen(", source)

    def test_on_loopback_the_token_never_reaches_a_server_it_was_redirected_to(self):
        got = []

        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                got.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        class Ours(Other):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/api/state" % other.server_address[1])
                self.send_header("Content-Length", "0")
                self.end_headers()

        try:
            other = http.server.HTTPServer(("127.0.0.1", 0), Other)
            ours = http.server.HTTPServer(("127.0.0.1", 0), Ours)
        except OSError as err:
            self.skipTest("cannot listen on loopback here: %s" % err)
        for server in (other, ours):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        proxy = "http://127.0.0.1:%d" % other.server_address[1]
        with mock.patch.dict(os.environ, {"http_proxy": proxy, "HTTP_PROXY": proxy, "no_proxy": "", "NO_PROXY": ""}):
            core = self.shell.Core("http://127.0.0.1:%d" % ours.server_address[1])
            core.token = "fake-token"
            with warnings.catch_warnings(record=True) as said:
                warnings.simplefilter("always")
                with self.assertRaises(urllib.error.HTTPError):
                    core.state()
                gc.collect()
        self.assertEqual(got, [], "neither as a proxy nor as a redirect target")
        self.assertEqual(
            [str(w.message) for w in said if issubclass(w.category, ResourceWarning)],
            [],
            "and the refused answer's connection is closed, not left to be cleaned up",
        )


# --- Phone: busy ------------------------------------------------------------------------------------------------------
class PendingSaysWhy(unittest.TestCase):
    def test_a_missing_answer_is_remembered_until_a_newer_state_is_drawn(self):
        p = logic.Pending()
        self.assertTrue(p.start("call"))
        self.assertFalse(p.lost)
        p.answered(3, 2, 5, False)
        self.assertEqual((p.what, p.lost), ("call", True))
        p.seen(5)
        self.assertEqual((p.what, p.lost), ("call", True), "asked before the reply came: says nothing")
        p.seen(6)
        self.assertEqual((p.what, p.lost), (None, False))

    def test_a_reply_that_only_lost_the_race_is_not_called_missing(self):
        p = logic.Pending()
        p.start("hang_up")
        p.answered(3, 4, 5, True)
        self.assertEqual((p.what, p.lost), ("hang_up", False))
        p.start("call")
        self.assertEqual(p.what, "hang_up", "one at a time")
        p.seen(6)
        self.assertTrue(p.start("call"))
        self.assertFalse(p.lost, "a new request starts clean")

    def test_the_words(self):
        self.assertEqual(logic.pending_words("call", False), "Asking the phone to call.")
        self.assertEqual(logic.pending_words("hang_up", False), "Asking the phone to hang up.")
        self.assertEqual(logic.pending_words("call", True), logic.CALL_UNSURE)
        self.assertEqual(logic.pending_words("hang_up", True), logic.HANG_UP_UNSURE)
        self.assertEqual(logic.pending_words(None, True), "")
        for words in (logic.CALL_UNSURE, logic.HANG_UP_UNSURE):
            self.assertNotIn("Asking", words)


class PhoneBusy(base.Screens):
    """Only the Phone cases; the base class's own tests are not run again here."""

    def test_a_call_with_no_answer_says_so_and_never_says_it_is_still_asking(self):
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        self.assertIn("Asking the phone to call.", p.status.get_label())
        self.shell.answer(0, None)
        self.assertEqual(
            (p.go_call.get_label(), p.go_call.sensitive), ("Calling", False), "Call is not offered from old news"
        )
        self.assertIn(logic.CALL_UNSURE, p.status.get_label())
        self.assertNotIn("Asking the phone", p.status.get_label(), "nothing is being asked any more")
        p.call_or_hang()
        self.assertEqual(len(self.shell.asked), 1, "and a tap still sends no second call")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": True})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Hang up", True))
        self.assertNotIn(logic.CALL_UNSURE, p.status.get_label())

    def test_a_hang_up_with_no_answer_says_the_call_may_still_be_up(self):
        p = self.page(self.screens.Phone)
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": True})
        p.call_or_hang()
        self.shell.answer(0, None)
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Hanging up", False))
        self.assertIn(logic.HANG_UP_UNSURE, p.status.get_label())
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Call", True))

    def test_an_answer_that_comes_before_ask_returns_does_not_break_the_tap(self):
        """A request that could not be sent is answered at once (None), before ask() has returned its number."""
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        shell = self.shell

        def ask(action, done=None, **fields):
            n = shell.seq.issue()
            done(None)
            return n

        shell.ask = ask
        p.call_or_hang()
        self.assertEqual((p.pending.what, p.pending.lost, p.go_call.sensitive), ("call", True, False))
        shell.drawn(shell.seq.issue(), {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Call", True), "free again with the next state")

    def test_an_app_request_that_could_not_be_sent_is_answered_so_no_button_stays_busy(self):
        c = self.page(self.screens.Contacts)

        def no_thread(target=None, daemon=None):
            def start():
                raise RuntimeError("can't start new thread")

            return types.SimpleNamespace(start=start)

        c.entries["name"].text, c.entries["number"].text = "Ada", "+15550100"
        self.screens.threading = types.SimpleNamespace(Thread=no_thread)
        c.save()
        self.assertFalse(c.busy, "the save was answered: not sent")
        self.assertEqual(c.note.get_label(), "That did not go through.")
        self.assertEqual(self.shell.core.sent, [], "nothing reached the server")
        self.assertEqual(c.entries["name"].text, "Ada", "and what was typed is kept")


for _name in [n for n in vars(base.Screens) if n.startswith("test_")]:
    setattr(PhoneBusy, _name, None)  # unittest skips a None attribute: the base tests run once, in their own module


def bare_shell(case):
    """Shell without a window, as tests.test_native_screens.ShellRules builds it, with what these rules need."""
    module = stand_in_shell(case, {})
    sh = module.Shell.__new__(module.Shell)
    sh.seq, sh.link, sh.ring = logic.Sequence(), logic.Link(), logic.RingGate()
    sh.snap, sh.snap_seq, sh.said, sh.said_text, sh.battery_line = {}, 0, 0, "", ""
    sh.wake, sh.warming, sh.wanted, sh.page, sh.leaving = threading.Event(), False, None, "lock", 0
    sh.pages = {n: base.Recorder(n) for n in ("lock", "home", "phone", "clock")}
    sh.s_right, sh.b_head, sh.b_detail, sh.b_events, sh.strip, sh.sheet = (base.Widget() for _ in range(6))
    sh.reads, sh.acts, sh.splash = base.Widget(), base.Widget(), base.Widget()
    sh.quit_calls, sh.clock_absent = 0, True
    sh.get_application = lambda: types.SimpleNamespace(quit=lambda: setattr(sh, "quit_calls", sh.quit_calls + 1))
    sh.show_page = lambda *a: None
    sh.drag, sh.owned = None, False
    sh.page_slide, sh.sheet_slide = Fake(offset=lambda dy: None), Fake(offset=lambda dy: None)
    sh.lock_check, sh.held = logic.LockCheck(), False
    return module, sh


class ARequestThatCouldNotBeSent(unittest.TestCase):
    def test_ask_answers_with_nothing_so_the_screen_that_asked_is_told(self):
        module, sh = bare_shell(self)

        class NoThread:
            def __init__(self, *a, **k):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        told = []
        with mock.patch.object(module.threading, "Thread", NoThread):
            n = sh.ask("call", done=told.append, number="+15550100")
        self.assertEqual((n, told), (1, [None]))
        self.assertEqual(sh.said_text, "That did not go through.")
        self.assertTrue(sh.wake.is_set(), "and the poll looks again at once")


# --- the PIN lock: no answer is never "no lock" -----------------------------------------------------------------------
class LockCheckRules(unittest.TestCase):
    def test_only_a_plain_no_opens_and_a_yes_goes_to_the_browser(self):
        check = logic.LockCheck()
        self.assertEqual(check.answer(False), "open")
        self.assertEqual(check.answer(True), "browser")
        for unknown in (None, 0, "", "no", [], {}, 1, "yes"):
            self.assertEqual(logic.LockCheck().answer(unknown), "hold", repr(unknown))

    def test_no_answer_holds_and_after_the_limit_the_browser_decides(self):
        check = logic.LockCheck()
        self.assertEqual(
            [check.answer(None) for _ in range(logic.LockCheck.LIMIT)],
            ["hold"] * (logic.LockCheck.LIMIT - 1) + ["browser"],
        )
        self.assertGreaterEqual(logic.LockCheck.LIMIT, 3)

    def test_an_answer_starts_the_count_again(self):
        check = logic.LockCheck()
        for _ in range(logic.LockCheck.LIMIT - 1):
            check.answer(None)
        self.assertEqual(check.answer(False), "open")
        self.assertEqual(check.answer(None), "hold")


class Server:
    """core.state() and core.lock_on() from a script: each entry is a value to return or an exception to raise."""

    def __init__(self, states, locks):
        self.states, self.locks, self.asked = list(states), list(locks), []

    def _next(self, name, script):
        self.asked.append(name)
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def state(self):
        return self._next("state", self.states)

    def lock_on(self):
        return self._next("lock", self.locks)


GOOD = {"battery": {"percent": 80}}


class TheLockQuestion(unittest.TestCase):
    def setUp(self):
        self.module, self.sh = bare_shell(self)
        self.seen = []
        self.sh.polled = lambda n, snap, lock: self.seen.append((snap, lock))

    def look(self, states, locks, lock=None):
        self.sh.core = Server(states, locks)
        out = []
        for _ in range(len(states)):
            lock = self.sh.poll_once(lock)
            out.append(lock)
        return out

    def test_no_lock_is_asked_once_and_kept(self):
        self.assertEqual(self.look([GOOD, GOOD, GOOD], [False]), [False, False, False])
        self.assertEqual(self.sh.core.asked, ["state", "lock", "state", "state"])
        self.assertEqual(self.seen, [(GOOD, False)] * 3)

    def test_a_lock_check_that_fails_is_unknown_and_asked_again_never_read_as_no_lock(self):
        broken = urllib.error.HTTPError("u", 500, "broken", {}, None)
        self.addCleanup(broken.close)
        self.assertEqual(self.look([GOOD, GOOD, GOOD], [broken, OSError("timed out"), True]), [None, None, True])
        self.assertEqual(self.seen, [(GOOD, None), (GOOD, None), (GOOD, True)])

    def test_after_the_server_went_away_the_question_is_asked_again(self):
        self.assertEqual(self.look([GOOD, OSError("refused"), GOOD], [False, True]), [False, None, True])
        self.assertEqual(
            self.seen, [(GOOD, False), (None, None), (GOOD, True)], "a restart may have switched the lock on"
        )

    def test_only_true_and_false_are_answers(self):
        self.assertEqual(self.look([GOOD, GOOD], ["yes", 0]), [None, None])


class NoAnswerAboutTheLockHoldsTheShell(unittest.TestCase):
    def setUp(self):
        self.module, self.sh = bare_shell(self)

    def state(self, **kw):
        return dict(GOOD, **kw)

    def test_a_state_without_the_locks_answer_is_not_drawn_and_the_shell_stays_on_its_lock_page(self):
        sh = self.sh
        sh.polled(sh.seq.issue(), self.state(contacts=[{"name": "Ada"}]), False)
        sh.go("home")
        self.assertEqual(sh.page, "home")
        shown = len(sh.pages["home"].shown)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            sh.polled(sh.seq.issue(), self.state(status="after a restart"), None)
            sh.polled(sh.seq.issue(), self.state(status="after a restart"), None)
        self.assertEqual((sh.page, sh.held), ("lock", True))
        self.assertNotEqual(sh.snap.get("status"), "after a restart", "not drawn")
        self.assertEqual(len(sh.pages["home"].shown), shown)
        self.assertEqual(sh.pages["lock"].lost_text, logic.LOCK_UNKNOWN)
        self.assertEqual(err.getvalue().count("PIN lock"), 1, "said once in the log, not at every poll")
        self.assertEqual(sh.quit_calls, 0)

    def test_nothing_leaves_the_lock_page_while_held(self):
        sh = self.sh
        with contextlib.redirect_stderr(io.StringIO()):
            sh.polled(sh.seq.issue(), self.state(), None)
        for name in ("home", "phone", "clock"):
            sh.go(name)
            self.assertEqual(sh.page, "lock", name)
        sh.warming = True
        sh.go("home")
        self.assertIsNone(sh.wanted, "nor is it kept for after the start-up cover")
        sh.warming = False
        sh.clock_seen(1, {"ringing": {"id": "a1"}})
        self.assertEqual(sh.page, "lock")
        sh.settle("lock", "complete")
        self.assertEqual(sh.page, "lock", "the unlock drag does nothing")

    def test_the_converter_page_is_not_reachable_while_held_so_working_it_out_here_opens_nothing(self):
        """The converter runs in the shell's own process, so the server's refusal while locked (403) is not what keeps a
        locked phone out of it: the shell is. With a PIN lock, or without a verdict on one, the shell shows its lock page
        and nothing else, and a server that enforces a lock sends the shell to the browser (status 11) before any page."""
        sh = self.sh
        sh.pages["convert"] = base.Recorder("convert")
        with contextlib.redirect_stderr(io.StringIO()):
            sh.polled(sh.seq.issue(), self.state(), None)
        sh.go("convert")
        self.assertEqual((sh.page, sh.held), ("lock", True))
        self.assertEqual(
            (sh.pages["convert"].entered, sh.pages["convert"].shown), (0, []), "the converter was not opened"
        )

    def test_a_reply_to_an_action_is_not_drawn_while_held_and_the_screen_that_asked_is_still_told(self):
        sh, told = self.sh, []
        sh.polled(sh.seq.issue(), self.state(), False)
        action = sh.seq.issue()
        with contextlib.redirect_stderr(io.StringIO()):
            sh.polled(sh.seq.issue(), self.state(), None)
        sh.answered(action, self.state(in_call=True), told.append)
        self.assertNotIn("in_call", sh.snap)
        self.assertEqual(len(told), 1)

    def test_the_answer_no_lock_frees_the_shell_and_draws_the_state(self):
        sh = self.sh
        with contextlib.redirect_stderr(io.StringIO()):
            sh.polled(sh.seq.issue(), self.state(), None)
        sh.polled(sh.seq.issue(), self.state(status="ready"), False)
        self.assertEqual((sh.held, sh.snap.get("status"), sh.pages["lock"].lost_text), (False, "ready", ""))
        sh.go("home")
        self.assertEqual(sh.page, "home")
        sh.clock_seen(2, {"ringing": {"id": "a1"}})
        self.assertEqual(sh.page, "clock", "an alarm that rang while held still comes forward once")

    def test_an_alarm_that_rings_while_held_is_not_marked_as_seen_and_comes_forward_when_the_shell_is_free(self):
        """Marked as seen while held, the ring would be used up on a page change that is refused: the alarm would then
        ring behind whatever page the owner opens, and never come forward."""
        sh = self.sh
        sh.polled(sh.seq.issue(), self.state(), False)
        with contextlib.redirect_stderr(io.StringIO()):
            sh.polled(sh.seq.issue(), self.state(), None)
        ringing = {"ringing": {"id": "a1"}}
        for n in (1, 2):
            sh.clock_seen(n, ringing)
        self.assertEqual(
            (sh.page, sh.held, sh.ring.seen), ("lock", True, None), "not brought forward, and not marked as seen"
        )
        self.assertEqual(
            sh.pages["clock"].delivered, [(1, ringing), (2, ringing)], "the Clock page still gets what was read"
        )
        sh.polled(sh.seq.issue(), self.state(), False)
        self.assertEqual((sh.page, sh.held), ("lock", False))
        sh.clock_seen(3, ringing)
        self.assertEqual((sh.page, sh.ring.seen), ("clock", "a1"), "the same alarm, first seen now")
        sh.go("home")
        sh.clock_seen(4, ringing)
        self.assertEqual(sh.page, "home", "once for each alarm, as before")

    def test_the_answer_locked_goes_to_the_browser_whatever_the_state_is(self):
        for snap in (self.state(), None, {"error": "forbidden"}):
            module, sh = bare_shell(self)
            sh.polled(sh.seq.issue(), snap, True)
            self.assertEqual((sh.leaving, sh.quit_calls), (module.LOCKED, 1), snap)

    def test_after_the_limit_the_browser_takes_over_and_the_log_says_why(self):
        sh = self.sh
        with contextlib.redirect_stderr(io.StringIO()) as err:
            for i in range(logic.LockCheck.LIMIT):
                self.assertEqual(sh.quit_calls, 0, i)
                sh.polled(sh.seq.issue(), self.state(), None)
        self.assertEqual((sh.leaving, sh.quit_calls), (self.module.LOCKED, 1))
        self.assertIn("did not say whether it has a PIN lock", err.getvalue())

    def test_a_lost_connection_is_not_a_missing_answer(self):
        sh = self.sh
        sh.polled(sh.seq.issue(), self.state(), False)
        sh.go("home")
        for _ in range(logic.LockCheck.LIMIT * 3):
            sh.polled(sh.seq.issue(), None, None)
        self.assertEqual(
            (sh.page, sh.held, sh.quit_calls, sh.lock_check.misses),
            ("home", False, 0, 0),
            "the last good state stays, as before; the question is asked when the server is back",
        )
        self.assertEqual(sh.pages["lock"].lost_text, logic.LINK_LOST)

    def test_the_source_has_no_default_for_the_locks_answer(self):
        shell = (NATIVE / "shell.py").read_text(encoding="utf-8")
        self.assertIn("    def polled(self, n, snap, lock):", shell)
        self.assertIn("GLib.idle_add(self.polled, n, snap, lock)", shell)
        self.assertNotIn("lock=False", shell)


# --- the lid watcher is looked for again ------------------------------------------------------------------------------
class Watcher:
    """Stands in for posture.LidWatcher: the test plays the thread."""

    made, refuse = [], False

    def __init__(self, on_change, on_missing):
        self.on_change, self.on_missing = on_change, on_missing

    def start(self):
        if Watcher.refuse:
            raise RuntimeError("can't start new thread")
        Watcher.made.append(self)
        return self


class LidAgain(StandInShell):
    def setUp(self):
        super().setUp()
        Watcher.made, Watcher.refuse = [], False
        self.slow = []  # (seconds, callback) handed to GLib.timeout_add_seconds
        self.shell.GLib.timeout_add_seconds = lambda s, fn, *a: self.slow.append((s, fn, a)) or len(self.slow)
        patch = mock.patch.object(self.shell.posture, "LidWatcher", Watcher)
        patch.start()
        self.addCleanup(patch.stop)
        self.err = io.StringIO()
        redirect = contextlib.redirect_stderr(self.err)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def again(self):
        """Run the one waiting look for the lid; its delay in seconds."""
        self.assertEqual(len(self.slow), 1, "one look waits at a time")
        s, fn, a = self.slow.pop(0)
        self.assertIs(fn(*a), False, "a one-shot timer")
        return s

    def test_a_lost_watcher_is_looked_for_again_and_the_screens_are_lit_meanwhile(self):
        w = self.win
        w.watch_lid()
        first = Watcher.made[-1]
        first.on_change(True)
        self.fire_timers()
        self.assertEqual((w.lid_closed, self.calls), (True, ["blank"]))
        self.calls.clear()
        first.on_missing("cannot read /dev/input/event5: No such device")
        self.assertEqual((w.lid_closed, self.calls), (None, ["restore"]), "lit at once, as before")
        self.assertEqual(self.again(), self.shell.LID_AGAIN_S[0])
        self.assertEqual(len(Watcher.made), 2)
        self.assertIs(w.lid_watcher, Watcher.made[-1])

    def test_the_new_watcher_reports_as_the_first_one_did(self):
        w = self.win
        w.watch_lid()
        Watcher.made[-1].on_missing("gone")
        self.again()
        self.calls.clear()
        w.page = "home"
        Watcher.made[-1].on_change(True)
        self.assertEqual((w.lid_closed, w.page), (True, "lock"))
        self.fire_timers()
        self.assertEqual(self.calls, ["blank"])
        Watcher.made[-1].on_change(False)
        self.assertEqual((w.lid_closed, self.calls[-1], w.blanker.dark), (False, "restore", False))

    def test_the_wait_grows_and_never_ends_and_a_report_starts_it_over(self):
        w = self.win
        w.watch_lid()
        waits = []
        for _ in range(12):
            Watcher.made[-1].on_missing("gone")
            waits.append(self.again())
        S = self.shell
        self.assertEqual(waits, list(S.LID_AGAIN_S) + [S.LID_AGAIN_EVERY_S] * (12 - len(S.LID_AGAIN_S)))
        self.assertGreaterEqual(min(waits), 5, "never a busy loop")
        Watcher.made[-1].on_change(False)
        Watcher.made[-1].on_missing("gone")
        self.assertEqual(self.again(), S.LID_AGAIN_S[0])

    def test_a_watcher_that_was_lost_is_never_listened_to_again(self):
        w = self.win
        w.watch_lid()
        old = Watcher.made[-1]
        old.on_change(False)
        old.on_missing("gone")
        self.calls.clear()
        w.page = "home"
        old.on_change(True)  # a late report from the watcher that was given up
        old.on_missing("gone again")
        self.assertEqual(
            (w.lid_closed, w.page, self.calls, self.timers, len(self.slow)),
            (None, "home", [], [], 1),
            "no lock, no blank, no second look",
        )
        self.again()
        old.on_change(True)
        self.assertEqual((w.lid_closed, self.timers), (None, []), "nor once a new watcher runs")
        Watcher.made[-1].on_change(False)
        self.assertIs(w.lid_closed, False)

    def test_a_watcher_that_cannot_be_started_is_a_lost_one(self):
        w = self.win
        Watcher.refuse = True
        w.watch_lid()
        self.assertEqual((w.lid_closed, self.calls), (None, ["restore"]))
        self.assertEqual(self.again(), self.shell.LID_AGAIN_S[0])
        self.assertEqual(self.calls, ["restore", "restore"])
        Watcher.refuse = False
        self.again()
        self.assertEqual(len(Watcher.made), 1)

    def test_the_loss_is_said_once_until_a_watcher_reports_again(self):
        w = self.win
        w.watch_lid()
        for _ in range(6):
            Watcher.made[-1].on_missing('no input device named "Surface Duo Lid"')
            self.again()
        self.assertEqual(self.err.getvalue().count("no lid signal"), 1)
        Watcher.made[-1].on_change(False)
        Watcher.made[-1].on_missing("gone")
        self.assertEqual(self.err.getvalue().count("no lid signal"), 2)

    def test_a_flapping_switch_lights_the_screens_at_each_loss_and_is_said_once_for_each_loss(self):
        """Found, reports, lost, found again, with no end: a closed phone alternates dark and lit, an open one stays lit,
        each loss is one log line, and each next look is the shortest wait (a report starts the waits over)."""
        w = self.win
        w.watch_lid()
        for n in range(1, 9):
            self.calls.clear()
            Watcher.made[-1].on_change(True)
            self.fire_timers()
            self.assertEqual((w.lid_closed, self.calls, w.blanker.dark), (True, ["blank"], True), n)
            Watcher.made[-1].on_missing("gone")
            self.assertEqual(
                (w.lid_closed, self.calls, w.blanker.dark), (None, ["blank", "restore"], False), "lit at loss %d" % n
            )
            self.fire_timers()
            self.assertFalse(w.blanker.dark, "and it stays lit until a watcher says closed")
            self.assertEqual(self.err.getvalue().count("no lid signal"), n, "one line for each loss")
            self.assertEqual(self.again(), self.shell.LID_AGAIN_S[0])
            self.assertEqual(len(Watcher.made), n + 1)
        for n in range(8):
            self.calls.clear()
            Watcher.made[-1].on_change(False)
            Watcher.made[-1].on_missing("gone")
            self.fire_timers()
            self.assertEqual(
                (self.calls, w.blanker.dark), (["restore", "restore"], False), "an open phone is never darkened"
            )
            self.again()

    def test_start_lid_uses_the_same_watcher_start(self):
        with (
            mock.patch.object(self.shell.posture, "find_lid", lambda *a: None),
            mock.patch.object(self.shell.displayconfig, "own", lambda b, say: None),
        ):
            self.win.start_lid()
        self.assertEqual((len(Watcher.made), self.win.lid_gen), (1, 1))
        self.assertIs(self.win.lid_watcher, Watcher.made[0])

    def test_the_source_keeps_the_rules(self):
        text = (NATIVE / "shell.py").read_text(encoding="utf-8")
        self.assertIn("GLib.idle_add(self.lid_report, gen, closed)", text)
        self.assertIn("GLib.idle_add(self.lid_gone, gen, reason)", text)
        lost = text[text.index("    def lid_lost(") : text.index("    def lid_again(")]
        self.assertLess(lost.index("self.restore()"), lost.index("timeout_add_seconds"), "light first, then look again")
        for name in ("watch_lid", "lid_report", "lid_gone", "lid_again"):
            body = text[text.index("    def %s(" % name) :]
            body = body[: body.index("\n    def ", 5)]
            self.assertNotIn("blank(", body, name + " never darkens anything itself")

    def real_tree(self, d):
        root = Path(d)
        run = root / "run"
        run.mkdir(mode=0o700)
        for p_, v in zip(blank.PANELS, (90, 200)):
            (root / "sys/class/backlight" / p_).mkdir(parents=True)
            (root / "sys/class/backlight" / p_ / "brightness").write_text("%d\n" % v)
        w = self.win
        w.blanker = blank.Blanker(
            root=str(root), run_dir=str(run), say=lambda t: None, may_blank=lambda: w.lid_closed is not False
        )
        w.blanker.on_restore = w.restore
        return [root / "sys/class/backlight" / p_ / "brightness" for p_ in blank.PANELS]

    def test_random_lid_histories_with_lost_watchers_never_leave_an_open_or_unknown_phone_dark(self):
        """The real Blanker on a made-up sysfs tree. After every step, with the waiting timers run: the screens may be
        dark only while the lid is known closed, or while the daemon asked for dark and has not been answered since
        (by an opening, a lost watcher or its own 0). Seeded, so a failure names its history."""
        for seed in range(60):
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as d:
                rnd = random.Random(seed)
                self.timers[:], self.slow[:], Watcher.made = [], [], []
                w = self.win
                w.lid_closed, w.lid_gen, w.lid_tries, w.lid_timer, w.lid_said = None, 0, 0, None, False
                w.restore_tries, w.restore_timer, w.restore_blanks = 0, None, 0
                panels = self.real_tree(d)
                w.watch_lid()
                daemon_dark, alive, history = False, True, []
                for _ in range(80):
                    step = rnd.choice(("closed", "open", "lost", "again", "daemon3", "daemon0", "stale"))
                    history.append(step)
                    if step in ("closed", "open") and alive:
                        Watcher.made[-1].on_change(step == "closed")
                        if step == "open":
                            daemon_dark = False
                    elif step == "lost" and alive:
                        Watcher.made[-1].on_missing("gone")
                        alive, daemon_dark = False, False
                    elif step == "again" and self.slow:
                        self.again()
                        alive = True
                    elif step == "daemon3":
                        w.blanker.set_mode(3)
                        daemon_dark = daemon_dark or w.lid_closed is not False
                    elif step == "daemon0":
                        w.blanker.set_mode(0)
                        daemon_dark = False
                    elif step == "stale" and len(Watcher.made) > 1:
                        Watcher.made[0].on_change(True)
                    for _round in range(3):  # the blank after BLANK_MS, and a restore's next try
                        self.fire_timers()
                    lit = [int(p.read_text()) > 0 for p in panels]
                    if w.lid_closed is not True and not daemon_dark:
                        self.assertEqual(lit, [True, True], "dark with the lid %r after %s" % (w.lid_closed, history))
                    if w.lid_closed is False:
                        self.assertEqual(lit, [True, True], "an open phone is never dark: %s" % history)
                    self.assertLessEqual(len(self.slow), 1, history)
                    self.assertEqual(
                        alive or bool(self.slow), True, "a lost watcher always has a next look waiting: %s" % history
                    )


# --- the docs name what the code does ---------------------------------------------------------------------------------
class Docs(unittest.TestCase):
    def flat(self, *parts):
        return " ".join(ROOT.joinpath(*parts).read_text(encoding="utf-8").split())

    def test_the_final_doc_names_the_shells_address_switch_and_what_it_refuses(self):
        doc = self.flat("docs", "duo-os-final.md")
        row = next(
            line
            for line in ROOT.joinpath("docs", "duo-os-final.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("| `OPOS_SERVER_URL`")
        )
        for words in ("http://127.0.0.1:PORT", "never reads `OPOS_LOCAL_URL`", "proxy", "redirect"):
            self.assertIn(words, row)
        self.assertNotIn("the shell reads `OPOS_LOCAL_URL` for it", doc)
        used = set()
        for path in NATIVE.glob("*.py"):
            used |= set(re.findall(r"\bOPOS_[A-Z_]+\b", path.read_text(encoding="utf-8")))
        self.assertIn("OPOS_SERVER_URL", used)
        self.assertEqual(sorted(u for u in used if u not in doc), [])

    def test_native_md_says_what_changed_and_what_was_not_run(self):
        doc = self.flat("packaging", "duo-os", "NATIVE.md")
        for words in (
            "`OPOS_SERVER_URL`",
            '"%s"' % logic.LOCK_UNKNOWN,
            '"%s"' % logic.CALL_UNSURE,
            '"%s"' % logic.HANG_UP_UNSURE,
            "looked for again",
            "has no PIN screen",
        ):
            self.assertIn(words, doc)
        self.assertNotIn("a lid watcher that stops is not started again", doc)
        self.assertIn("%d times in a row" % logic.LockCheck.LIMIT, doc)
        shell = (NATIVE / "shell.py").read_text(encoding="utf-8")
        waits = re.search(r"^LID_AGAIN_S = \(([0-9, ]+)\)", shell, re.M).group(1).split(", ")
        every = re.search(r"^LID_AGAIN_EVERY_S = ([0-9]+)", shell, re.M).group(1)
        self.assertIn("%s s later, then every %s s" % (" s, ".join(waits[:-1]) + " s and " + waits[-1], every), doc)
        lid = doc[doc.index("## The lid") : doc.index("## Follow-ups of November 14")]
        for words in (
            "A lid switch that flaps",
            "The screens are lit at each loss",
            "one line for each loss that follows a report",
            "A closed phone with a flapping switch therefore alternates",
            "An open phone stays lit throughout",
            "This is the safe side",
            "the next look is %s s after each loss" % waits[0],
        ):
            self.assertIn(words, lid)
        follow = doc[doc.index("## Follow-ups of November 14") :]
        self.assertIn("A lid switch that flaps", follow)
        self.assertIn("NOT_RUN", follow)


if __name__ == "__main__":
    unittest.main()
