# SPDX-License-Identifier: GPL-3.0-or-later
"""The native screens' behaviour, run against a stand-in toolkit: widgets that only remember their text, and a shell that
records what the screens ask the server and answers when the test says so. No display and no GTK are needed, so these
run everywhere. What they pin: a draft, a contact, an alarm or a note changes on screen only when the server confirms,
a late reply never puts a screen back, rows that read the same act on the newest id, and practice is said."""
import importlib
import sys
import threading
import types
import unittest
import urllib.error

from opos.native import logic


class Widget:
    """Any toolkit object: it keeps its label, visibility and sensitivity, and ignores everything else."""

    def __init__(self, *a, **k):
        self.label, self.visible, self.sensitive, self.children, self.css = k.get("label", ""), True, True, [], set()

    def set_label(self, text):
        self.label = text

    def get_label(self):
        return self.label

    def set_visible(self, v):
        self.visible = bool(v)

    def get_visible(self):
        return self.visible

    def set_sensitive(self, v):
        self.sensitive = bool(v)

    def append(self, child):
        self.children.append(child)

    def remove(self, child):
        self.children.remove(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_last_child(self):
        return self.children[-1] if self.children else None

    def get_next_sibling(self):
        return None

    def get_child(self):
        return Widget()

    def add_css_class(self, c):
        self.css.add(c)

    def remove_css_class(self, c):
        self.css.discard(c)

    def __getattr__(self, name):
        if name.startswith(("set_", "get_", "add_", "remove_", "connect", "attach", "insert", "reorder", "queue_", "can_")):
            return lambda *a, **k: Widget()
        raise AttributeError(name)      # as a real widget: no attribute until someone sets one


class Kind(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        made = Kind(name, (Widget,), {})
        setattr(cls, name, made)
        return made


class Toolkit(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        made = Kind(name, (Widget,), {})
        setattr(self, name, made)
        return made


class Core:
    def __init__(self):
        self.answers, self.sent = [], []

    def app(self, name, data=None):
        self.sent.append((name, data))
        return self.answers.pop(0) if self.answers else {}


class FakeShell:
    """What the screens need of the shell. ask() records; answer(i, reply) delivers the i-th answer as the shell would."""

    def __init__(self):
        self.snap, self.snap_seq, self.seq, self.hand = {}, 0, logic.Sequence(), "right"
        self.core, self.asked, self.went, self.pages = Core(), [], [], {}
        self.apps = [("phone", "Phone"), ("messages", "Messages")]
        self.known = True

    def ask(self, action, done=None, **fields):
        n = self.seq.issue()
        self.asked.append((n, action, fields, done))
        return n

    def drawn(self, n, snap):
        """A state the shell asked for as request n (a poll) arrives and is newer than what is drawn."""
        if self.seq.accept(n):
            self.snap, self.snap_seq = snap, n
            for page in self.pages.values():
                page.show(snap)

    def answer(self, i, reply):
        """The server answered the i-th ask. A state that is newer than what is drawn replaces it, as Shell.answered does."""
        n, _action, _fields, done = self.asked[i]
        if reply is not None and self.seq.accept(n, floor=True):
            self.snap, self.snap_seq = reply, n
            for page in self.pages.values():
                page.show(reply)
        if done:
            done(reply)

    def go(self, name):
        self.went.append(name)

    def leave(self):
        self.went.append("leave")

    def set_hand(self, hand):
        self.hand = hand

    def clock_known(self):
        return self.known

    def time_text(self, now=None):
        return "10:05" if self.known else "--:--"


class Screens(unittest.TestCase):
    def setUp(self):
        names = ("gi", "gi.repository", "opos.native.screens", "opos.native.ui")
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
        gi.repository.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
        sys.modules["gi"], sys.modules["gi.repository"] = gi, gi.repository
        self.screens = importlib.import_module("opos.native.screens")
        self.work = []       # requests to the apps, held until the test lets the server answer
        self.screens.threading = types.SimpleNamespace(Thread=lambda target, daemon=None: types.SimpleNamespace(start=lambda: self.work.append(target)))
        self.shell = FakeShell()

    def flush(self):
        """The server answers every request made so far, in order."""
        while self.work:
            self.work.pop(0)()

    def page(self, cls):
        page = cls(self.shell)
        self.shell.pages[page.name] = page
        return page

    # --- Messages -------------------------------------------------------------------------------------------------
    def messages(self):
        m = self.page(self.screens.Messages)
        self.shell.pages["messages"] = m
        return m

    def type_into(self, m, text):
        for ch in text:
            m.key(ch)

    def test_a_draft_stays_in_the_box_until_the_server_says_it_went_out(self):
        m = self.messages()
        m.open_thread("+1 Bob")
        self.shell.answer(0, {"battery": {}, "thread": [], "status": "thread open"})
        self.type_into(m, "hi")
        m.send_typed()
        self.assertEqual(m.typed.text, "hi", "still there while the server has not answered")
        self.assertFalse(m.send.sensitive, "and it cannot be sent twice")
        m.send_typed()
        self.assertEqual(len(self.shell.asked), 2, "the open and one send, not two")
        self.shell.answer(1, {"battery": {}, "status": "sent", "notice": "Practice message. Nothing was sent.", "thread": ["You: hi"],
                              "simulation": {"modem": True}})
        self.assertEqual(m.typed.text, "")
        self.assertEqual(m.practice.get_label(), "Practice message. Nothing was sent.")
        self.assertEqual(m.thread.get_label(), "You: hi")

    def test_a_refused_send_keeps_the_words_and_shows_the_servers_reason(self):
        m = self.messages()
        m.open_thread("+1 Bob")
        self.shell.answer(0, {"battery": {}, "thread": []})
        self.type_into(m, "hi")
        m.send_typed()
        self.shell.answer(1, {"battery": {}, "status": "error: battery empty, charge the phone to send", "notice": "Charge the phone to send"})
        self.assertEqual(m.typed.text, "hi")
        self.assertEqual(m.practice.get_label(), "Charge the phone to send")
        self.assertTrue(m.send.sensitive, "and the owner can try again")

    def test_a_send_that_never_got_an_answer_keeps_the_words(self):
        m = self.messages()
        m.open_thread("+1 Bob")
        self.shell.answer(0, {"battery": {}, "thread": []})
        self.type_into(m, "hi")
        m.send_typed()
        self.shell.answer(1, None)
        self.assertEqual(m.typed.text, "hi")
        self.assertIn("did not go through", m.practice.get_label())

    def test_the_heading_and_the_lines_always_belong_to_the_same_person(self):
        m = self.messages()
        self.shell.snap = {"battery": {}, "contacts": [{"name": "Bob", "number": "+1 Bob"}], "thread": ["Alice: private text"]}
        m.open_thread("+1 Bob")
        self.assertEqual(m.head.get_label(), "Bob")
        self.assertNotIn("Alice", m.thread.get_label(), "Alice's thread is still the server's open one")
        self.shell.answer(0, {"battery": {}, "contacts": [{"name": "Bob", "number": "+1 Bob"}], "thread": ["Bob: hi"]})
        self.assertEqual((m.head.get_label(), m.thread.get_label()), ("Bob", "Bob: hi"))

    def test_a_refused_open_says_so_under_the_new_name(self):
        m = self.messages()
        m.open_thread("+1 Bob")
        self.shell.answer(0, None)
        self.assertIn("could not be opened", m.thread.get_label())

    def test_practice_is_said_before_anything_is_sent(self):
        m = self.messages()
        m.show({"battery": {}, "simulation": {"modem": True}})
        self.assertEqual(m.practice.get_label(), logic.PRACTICE_MESSAGES)
        m.show({"battery": {}, "simulation": {"modem": False}})
        self.assertEqual(m.practice.get_label(), "")

    # --- Phone and Home -------------------------------------------------------------------------------------------
    def test_a_call_waits_for_the_server_and_a_second_tap_sends_nothing(self):
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        p.call_or_hang()
        self.assertEqual([(a, f) for _n, a, f, _d in self.shell.asked], [("call", {"number": "+15550100"})])
        self.assertFalse(p.go_call.sensitive)
        self.shell.answer(0, {"battery": {}, "in_call": True, "status": "in call", "simulation": {"modem": True}})
        self.assertEqual(p.go_call.get_label(), "Hang up")
        self.assertIn("In a practice call", p.status.get_label())
        self.assertTrue(p.go_call.sensitive)

    def test_a_refused_call_shows_the_servers_words_and_a_real_call_is_not_called_practice(self):
        p = self.page(self.screens.Phone)
        p.show({"battery": {}, "status": "error: this phone cannot call emergency numbers. Use another phone.",
                "notice": "This phone cannot call emergency numbers. Use another phone.", "in_call": False})
        self.assertIn("cannot call emergency numbers", p.status.get_label())
        p.show({"battery": {}, "in_call": True, "simulation": {"modem": False}})
        self.assertEqual(p.status.get_label(), "In call\n" + self.screens.EMERGENCY)

    def test_home_says_practice_mode_and_previews_a_practice_call(self):
        self.page(self.screens.Phone)
        h = self.page(self.screens.Home)
        h.show({"battery": {}, "home_note": "Practice mode: calls, texts and tasks are simulated.", "in_call": True,
                "simulation": {"modem": True}})
        self.assertEqual(h.v_note.get_label(), "Practice mode: calls, texts and tasks are simulated.")
        self.assertEqual(h.v_text.get_label(), "In a practice call", "not the raw word 'in call'")

    def test_a_reply_that_lost_the_race_keeps_call_busy_until_a_newer_state_is_drawn(self):
        """The call is sent (n1); a poll asked after it (n2) is answered first, from before the server acted: not in a
        call. n1's success then arrives and is dropped as older news. Call must not come back for a second tap."""
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Calling", False))
        self.shell.answer(0, {"battery": {}, "in_call": True, "status": "in call"})
        self.assertFalse(self.shell.snap["in_call"], "the late reply was not drawn")
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Calling", False), "still waiting for the truth")
        p.call_or_hang()
        self.assertEqual(len(self.shell.asked), 1, "a second tap sends nothing")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": True})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Hang up", True))

    def test_a_poll_sent_before_the_call_reply_was_drawn_cannot_bring_call_back(self):
        """Reverse order: the call is sent (n1), a poll is sent (n2) and the server answers the poll before it acts on
        the call. The call's reply arrives first and is drawn (Hang up); the poll's older state then arrives. Its number is
        newer, but it was asked before the reply was drawn, so it is refused and Call never shows while the call is up."""
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        poll = self.shell.seq.issue()
        self.shell.answer(0, {"battery": {}, "in_call": True, "status": "in call"})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Hang up", True))
        self.shell.drawn(poll, {"battery": {}, "in_call": False})
        self.assertTrue(self.shell.snap["in_call"], "the poll from before the call was refused")
        self.assertEqual(p.go_call.get_label(), "Hang up")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": True})
        self.assertEqual(p.go_call.get_label(), "Hang up", "a poll sent after the reply is drawn as usual")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Call", True), "and can say the call ended")

    def test_a_call_with_no_answer_frees_the_button_only_once_a_newer_state_is_drawn(self):
        """R9: a failed or timed-out reply says nothing about whether the call went out. Call is offered again only when
        a state asked for after that reply is drawn, never from the old snapshot."""
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        before = self.shell.seq.issue()         # a poll asked before the failure was known
        self.shell.answer(0, None)
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Calling", False), "not from the old snapshot")
        self.shell.drawn(before, {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Calling", False), "asked before: still waiting")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": True})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Hang up", True), "the call did go out")
        p.call_or_hang()                        # hang up; that reply times out too, and the next poll says no call
        self.shell.answer(1, None)
        self.assertFalse(p.go_call.sensitive)
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Call", True))

    def test_a_real_phone_says_emergency_calls_cannot_be_relied_on_and_practice_says_practice(self):
        p = self.page(self.screens.Phone)
        p.show({"battery": {}, "simulation": {"modem": False}})
        self.assertEqual(p.status.get_label(), "OPOS cannot place an emergency call; keep another way to call for help.")
        self.assertTrue(any(self.screens.EMERGENCY in r for r in self.screens.notices.RISKS), "the shared words, not a copy")
        self.assertNotIn("simulated", self.screens.EMERGENCY, "a real call is never called simulated")
        p.show({"battery": {}})
        self.assertIn(self.screens.EMERGENCY, p.status.get_label(), "no simulation block: calls are not called practice")
        p.show({"battery": {}, "simulation": {"modem": True}})
        self.assertEqual(p.status.get_label(), "Practice only: this phone cannot make real calls yet.")

    def test_settings_shows_the_shared_notices_as_the_browser_does_under_about(self):
        made = []
        real = self.screens.label

        def recording(text="", *classes, **k):
            made.append(text)
            return real(text, *classes, **k)
        self.screens.label = recording
        try:
            self.page(self.screens.Settings)
        finally:
            self.screens.label = real
        self.assertIn("About", made)
        self.assertIn(self.screens.notices.as_text(), made)
        self.assertIn(self.screens.EMERGENCY, self.screens.notices.as_text())

    def test_the_emergency_line_is_the_shared_sentence_and_a_reworded_notice_never_stops_the_shell(self):
        words = self.screens.emergency_words
        self.assertEqual(words(("Emergency calling is not guaranteed. Keep another way to call for help.",)),
                         "Emergency calling is not guaranteed. Keep another way to call for help.")
        self.assertEqual(words(("Calls are simulated. OPOS cannot place an emergency call; keep another way.",)),
                         "OPOS cannot place an emergency call; keep another way.")
        self.assertIn("emergency calls", words(("Nothing about it here.",)))
        self.assertIn("emergency calls", words(()))

    def test_only_the_everyday_apps_opos_apps_names_get_pages(self):
        names = lambda env: [c.name for c in self.screens.enabled_pages(env)]  # noqa: E731
        base = ["lock", "home", "phone", "messages", "calc", "settings"]
        self.assertEqual(names({}), base, "absent: no everyday apps")
        self.assertEqual(names({"OPOS_APPS": ""}), base, "empty: none")
        self.assertEqual(names({"OPOS_APPS": "notes,clock"}), ["lock", "home", "phone", "messages", "clock", "notes", "calc", "settings"])
        self.assertEqual(names({"OPOS_APPS": "clock,contacts,notes,calc,calls"}),
                         ["lock", "home", "phone", "messages", "clock", "contacts", "notes", "calc", "calls", "settings"])
        self.assertEqual(names({"OPOS_APPS": "nonsense"}), base)

    # --- two taps to delete, as the browser asks --------------------------------------------------------------------
    def test_an_alarm_is_removed_only_on_the_second_tap(self):
        c = self.page(self.screens.Clock)
        two = [{"id": "a1", "hour": 7, "minute": 0, "days": [], "enabled": True},
               {"id": "a2", "hour": 8, "minute": 0, "days": [], "enabled": True}]
        c.got({"alarms": two, "clock_ok": True})
        c.choose("a1")
        c.remove()
        self.assertEqual((self.work, c.remove_button.get_label()), ([], "Delete this alarm?"), "the first tap only asks")
        c.got({"alarms": two, "clock_ok": True})
        self.assertEqual(c.remove_button.get_label(), "Delete this alarm?", "a refresh keeps the question")
        c.got({"alarms": two[1:], "clock_ok": True})
        self.assertEqual(c.remove_button.get_label(), "Remove", "the alarm went away: nothing is left armed")
        c.got({"alarms": two, "clock_ok": True})
        c.choose("a2")
        self.assertEqual(c.remove_button.get_label(), "Remove", "another alarm: the question is gone")
        c.remove()
        self.assertEqual(self.work, [])
        c.remove()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("clock", {"action": "delete", "id": "a2"}))
        self.assertEqual(c.remove_button.get_label(), "Remove")

    def test_a_person_is_removed_only_on_the_second_tap(self):
        c = self.page(self.screens.Contacts)
        c.got({"people": [{"id": "p1", "name": "Ann", "numbers": []}, {"id": "p2", "name": "Bo", "numbers": []}]})
        c.choose("p1")
        c.remove()
        self.assertEqual((self.work, c.remove_button.get_label()), ([], "Delete this person?"))
        c.choose(None)
        c.choose("p1")
        c.remove()
        self.assertEqual(self.work, [], "Done in between: the first tap asks again")
        c.remove()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("contacts", {"action": "delete", "id": "p1"}))

    def test_a_note_is_deleted_only_on_the_second_tap(self):
        n = self.page(self.screens.Notes)
        self.open_note(n)
        sent = len(self.shell.core.sent)
        n.delete()
        self.assertEqual((len(self.shell.core.sent), self.work, n.delete_button.get_label()), (sent, [], "Delete this note?"))
        n.delete()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("notes", {"action": "delete", "id": "n1"}))

    def test_add_alarm_twice_makes_one_request(self):
        c = self.page(self.screens.Clock)
        for d in "0730":
            c.digit(d)
        self.shell.core.answers = [{"ok": True, "alarms": [], "clock_ok": True}]
        c.add_alarm()
        c.add_alarm()
        self.assertEqual(len(self.work), 1, "one request, not two alarms")
        self.assertEqual((c.add.get_label(), c.add.sensitive), ("Adding", False))
        self.flush()
        self.assertEqual((c.add.get_label(), c.add.sensitive, c.digits), ("Add alarm", True, ""))
        self.assertEqual([d for _n, d in self.shell.core.sent].count(
            {"action": "add", "hour": 7, "minute": 30, "days": []}), 1)

    def test_a_refusal_whose_body_cannot_be_read_still_ends_the_add(self):
        # The server refused (an HTTP error) and the connection dropped while its body was read. The answer must still
        # reach the screen, or Add would stay on "Adding" for the shell's life and every later tap would be ignored.
        c = self.page(self.screens.Clock)
        for d in "0730":
            c.digit(d)

        class Dropped(urllib.error.HTTPError):
            def read(self, *a):
                raise ConnectionResetError("reset while reading")

        def refuse(name, data=None):
            self.shell.core.sent.append((name, data))
            raise Dropped("u", 500, "broken", {}, None)
        self.shell.core.app = refuse
        c.add_alarm()
        while self.work:            # as a real worker thread: an exception ends the thread and nothing else
            try:
                self.work.pop(0)()
            except Exception:
                pass
        self.assertFalse(c.adding, "the add ended")
        self.assertEqual((c.add.get_label(), c.add.sensitive, c.digits), ("Add alarm", True, "0730"), "refused: the time stays")
        self.assertIn("did not go through", c.note.get_label())
        c.add_alarm()
        self.assertEqual(len(self.shell.core.sent) + len(self.work), 2, "a second tap sends again")

    # --- Contacts and Clock ---------------------------------------------------------------------------------------
    def test_an_unrelated_answer_cannot_clear_an_unsaved_contact_and_a_refusal_keeps_it(self):
        c = self.page(self.screens.Contacts)
        for ch in "Ann":
            c.key(ch)
        c.focus("number")
        for ch in "123":
            c.key(ch)
        self.shell.core.answers = [{"error": "name"}]
        c.save()
        self.assertTrue(c.busy)
        self.assertFalse(c.save_button.sensitive)
        c.deliver(c.seq.issue(), {"ok": True, "people": [], "practice": False})      # a refresh answered meanwhile: not this save
        self.assertEqual((c.entries["name"].text, c.entries["number"].text), ("Ann", "123"))
        self.assertTrue(c.busy)
        self.flush()
        self.assertFalse(c.busy, "the save's own answer came back")
        self.assertEqual((c.entries["name"].text, c.entries["number"].text), ("Ann", "123"), "refused: the person is still there to fix")
        self.assertIn("needs a name", c.note.get_label())

    def test_a_saved_contact_clears_the_boxes_only_when_the_server_saved_it(self):
        c = self.page(self.screens.Contacts)
        for ch in "Ann":
            c.key(ch)
        self.shell.core.answers = [{"ok": True, "people": [{"id": "a1", "name": "Ann", "numbers": []}], "practice": False}]
        c.save()
        self.assertEqual(c.entries["name"].text, "Ann", "not until the server has answered")
        self.flush()
        self.assertEqual(c.entries["name"].text, "")
        self.assertEqual(c.save_button.get_label(), "Save")

    def test_rows_that_read_the_same_act_on_the_newest_id(self):
        n = self.page(self.screens.Notes)
        n.paint({"notes": [{"id": "n1", "title": "Empty note"}, {"id": "n2", "title": "Empty note"}]})
        n.paint({"notes": [{"id": "n3", "title": "Empty note"}, {"id": "n4", "title": "Empty note"}]})
        n.list.rowset.click(1)
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("notes", {"action": "open", "id": "n4"}), "not the n2 that is gone")

    def test_a_warning_when_the_clock_was_never_set(self):
        c = self.page(self.screens.Clock)
        c.paint({"alarms": [], "next": {"text": "07:00"}, "clock_ok": False})
        self.assertTrue(c.warn.visible)
        self.assertFalse(c.next.visible, "no next alarm is promised")
        c.paint({"alarms": [], "next": {"text": "07:00"}, "clock_ok": True})
        self.assertFalse(c.warn.visible)
        self.assertEqual(c.next.get_label(), "Next alarm: 07:00")

    def test_a_typed_alarm_time_stays_until_the_server_made_the_alarm(self):
        c = self.page(self.screens.Clock)
        for d in "0730":
            c.digit(d)
        self.shell.core.answers = [{"error": "full"}]
        c.add_alarm()
        self.assertEqual(c.digits, "0730", "not until the server has answered")
        self.flush()
        self.assertEqual(c.digits, "0730")
        self.assertIn("full", c.note.get_label())
        self.shell.core.answers = [{"ok": True, "alarms": [], "clock_ok": True}]
        c.add_alarm()
        self.flush()
        self.assertEqual(c.digits, "")

    def test_a_removed_alarm_stays_on_screen_until_the_server_removed_it(self):
        c = self.page(self.screens.Clock)
        c.paint({"alarms": [{"id": "a1", "hour": 7, "minute": 0, "days": [], "enabled": True}], "clock_ok": True})
        c.choose("a1")
        self.shell.core.answers = [{"error": "id"}]
        c.remove()
        self.flush()
        self.assertEqual(c.chosen, "a1", "refused: still chosen and still there")

    # --- Notes ----------------------------------------------------------------------------------------------------
    def open_note(self, n, ident="n1", text="hello"):
        n.paint({"notes": [{"id": ident, "title": "x"}]})
        self.shell.core.answers = [{"ok": True, "id": ident, "text": text, "notes": [{"id": ident, "title": "x"}]}]
        n.open_note(ident)
        self.flush()

    def test_a_note_closes_only_after_the_server_saved_it(self):
        n = self.page(self.screens.Notes)
        self.open_note(n)
        self.assertEqual(n.ident, "n1")
        self.shell.core.answers = [{"error": "size"}]
        n.close()
        self.assertEqual(n.ident, "n1", "still open while the server has not answered")
        self.assertTrue(n.busy)
        self.flush()
        self.assertEqual((n.ident, n.entry.text), ("n1", "hello"), "refused: the editor and the words are still there")
        self.assertFalse(n.busy)
        self.shell.core.answers = [{"ok": True, "id": "n1", "saved": "Saved 10:05", "notes": [{"id": "n1", "title": "hello"}]}]
        n.close()
        self.flush()
        self.assertIsNone(n.ident)

    def test_a_late_answer_for_an_older_note_never_becomes_the_open_note(self):
        n = self.page(self.screens.Notes)
        n.paint({"notes": [{"id": "n1", "title": "a"}, {"id": "n2", "title": "b"}]})
        self.shell.core.answers = [{"ok": True, "id": "n1", "text": "old"}, {"ok": True, "id": "n2", "text": "new"}]
        n.open_note("n1")
        n.open_note("n2")       # the owner tapped n1, then n2
        self.work.pop(0)()      # n1's answer comes first
        self.assertIsNone(n.ident)
        self.flush()
        self.assertEqual((n.ident, n.entry.text), ("n2", "new"))

    def test_new_note_never_adopts_the_answer_for_a_note_tapped_before_it(self):
        """Tap an existing note, then New note before its answer is back. The existing note's answer has an id and a
        text, but it is not the reply to the add: taking it would make Save write the new words into the old note."""
        n = self.page(self.screens.Notes)
        n.paint({"notes": [{"id": "n1", "title": "groceries"}]})
        self.shell.core.answers = [{"ok": True, "id": "n1", "text": "milk", "notes": [{"id": "n1", "title": "groceries"}]},
                                   {"ok": True, "id": "n7", "text": "", "notes": [{"id": "n1"}, {"id": "n7"}]}]
        n.open_note("n1")
        n.main_action()         # New note
        self.work.pop(0)()      # the open answer arrives first
        self.assertIsNone(n.ident, "not the reply to the add")
        self.flush()
        self.assertEqual((n.ident, n.entry.text), ("n7", ""), "the new note is the one that opens")
        for ch in "eggs":
            n.key(ch)
        n.main_action()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("notes", {"action": "save", "id": "n7", "text": "eggs"}))

    def test_a_drawn_list_answer_never_opens_a_note(self):
        n = self.page(self.screens.Notes)
        n.want = "add"
        n.deliver(n.seq.issue(), {"ok": True, "id": "n1", "text": "milk", "notes": []})
        self.assertIsNone(n.ident, "an answer nobody asked to open with")

    def test_a_refresh_sent_while_an_add_was_on_its_way_cannot_hide_the_new_alarm(self):
        """R8: the add is sent (n1), a refresh is sent (n2) and answered from before the add took effect, but arrives
        after the add's reply. The add's reply sets a floor, so the stale list is refused."""
        c = self.page(self.screens.Clock)
        add, poll = c.seq.issue(), c.seq.issue()
        new = {"ok": True, "alarms": [{"id": "a1", "hour": 7, "minute": 30, "days": [], "on": True}]}
        c.deliver(add, new, None, True)
        c.deliver(poll, {"ok": True, "alarms": []})
        self.assertEqual(c.data["alarms"], new["alarms"])
        c.deliver(c.seq.issue(), {"ok": True, "alarms": []})
        self.assertEqual(c.data["alarms"], [], "a refresh asked after the reply was drawn is drawn as usual")

    def test_a_failed_write_reply_sets_no_floor_so_a_newer_refresh_is_drawn(self):
        """Round 6: the add is sent (n2), a refresh is sent (n3); the add's reply is an error. A failed write proves
        nothing about the server's state, so it must not raise the floor: the refresh, newer than the add, is drawn."""
        n = self.page(self.screens.Notes)
        n.paint({"notes": []})
        add, poll = n.seq.issue(), n.seq.issue()
        n.deliver(add, {"error": "timed out"}, None, True)
        listed = {"ok": True, "notes": [{"id": "n1", "title": "milk"}]}
        n.deliver(poll, listed)
        self.assertEqual(n.data["notes"], listed["notes"], "the valid refresh is not hidden by a failed write")

    def test_an_older_answer_is_not_drawn_over_a_newer_one(self):
        n = self.page(self.screens.Notes)
        first, second = n.seq.issue(), n.seq.issue()
        n.deliver(second, {"ok": True, "notes": [{"id": "n9", "title": "new"}]})
        n.deliver(first, {"ok": True, "notes": [{"id": "n1", "title": "old"}]})
        self.assertEqual(n.data["notes"][0]["id"], "n9")


class Recorder:
    """A page as the shell sees it: it notes what it was shown."""

    def __init__(self, name):
        self.name, self.shown, self.entered, self.lost_text, self.data = name, [], 0, None, None
        self.seq = logic.Sequence()
        self.delivered = []

    def show(self, snap):
        self.shown.append(snap)

    def enter(self):
        self.entered += 1

    def tick(self):
        pass

    def lost(self, text):
        self.lost_text = text

    def deliver(self, n, data):
        self.delivered.append((n, data))


class ShellRules(unittest.TestCase):
    """Shell.answered, polled, go and the start-up walk, with the shell built without a window."""

    def setUp(self):
        names = ("gi", "gi.repository", "opos.native.shell", "opos.native.screens", "opos.native.ui")
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
        self.timers = []
        gi.repository.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a), timeout_add=lambda ms, fn, *a: self.timers.append((fn, a)))
        sys.modules["gi"], sys.modules["gi.repository"] = gi, gi.repository
        self.shell_module = importlib.import_module("opos.native.shell")
        sh = self.sh = self.shell_module.Shell.__new__(self.shell_module.Shell)
        sh.seq, sh.link, sh.ring = logic.Sequence(), logic.Link(), logic.RingGate()
        sh.snap, sh.snap_seq, sh.said, sh.said_text, sh.battery_line = {}, 0, 0, "", ""
        sh.wake, sh.warming, sh.wanted, sh.page, sh.leaving = threading.Event(), False, None, "lock", 0
        sh.pages = {n: Recorder(n) for n in ("lock", "home", "phone", "clock")}
        sh.s_right, sh.b_head, sh.b_detail, sh.b_events, sh.strip, sh.sheet = (Widget() for _ in range(6))
        sh.reads, sh.acts, sh.splash = Widget(), Widget(), Widget()
        sh.quit_calls = 0
        sh.get_application = lambda: types.SimpleNamespace(quit=lambda: setattr(sh, "quit_calls", sh.quit_calls + 1))
        sh.ready = logic.Ready(lambda: setattr(sh, "said_ready", True))
        sh.said_ready = False
        sh.get_mapped = lambda: True
        sh.get_frame_clock = lambda: None

    def state(self, **kw):
        return dict({"battery": {"percent": 80}}, **kw)

    def test_a_late_reply_never_puts_the_screen_back(self):
        sh = self.sh
        poll, action = sh.seq.issue(), sh.seq.issue()
        sh.answered(action, self.state(in_call=True), None)
        sh.polled(poll, self.state(in_call=False))      # the poll started before the call and landed after it
        self.assertTrue(sh.snap["in_call"])
        self.assertEqual(len(sh.pages["phone"].shown), 1)

    def test_a_poll_sent_before_an_action_reply_was_drawn_is_refused_even_with_a_newer_number(self):
        sh = self.sh
        action = sh.seq.issue()
        poll = sh.seq.issue()                            # sent while the call was on its way
        sh.answered(action, self.state(in_call=True), None)
        sh.polled(poll, self.state(in_call=False))      # answered by the server before it acted on the call
        self.assertTrue(sh.snap["in_call"])
        self.assertEqual(sh.snap_seq, action)
        sh.polled(sh.seq.issue(), self.state(in_call=True, status="later"))
        self.assertEqual(sh.snap["status"], "later", "the next poll is drawn")

    def test_a_reply_that_lost_the_race_still_reaches_the_screen_that_asked_and_asks_for_a_fresh_look(self):
        sh, told = self.sh, []
        a, b = sh.seq.issue(), sh.seq.issue()
        sh.answered(b, self.state(), None)
        sh.wake.clear()
        sh.answered(a, self.state(in_call=True), told.append)
        self.assertEqual(len(told), 1)
        self.assertTrue(sh.wake.is_set(), "the poll looks again at once")
        self.assertNotIn("in_call", sh.snap)

    def test_a_failed_poll_keeps_the_last_good_state_and_says_the_connection_is_lost(self):
        sh = self.sh
        sh.polled(sh.seq.issue(), self.state(status="ready"))
        good = sh.snap
        sh.polled(sh.seq.issue(), None)
        self.assertIs(sh.snap, good)
        self.assertEqual(sh.s_right.get_label(), logic.LINK_LOST)
        self.assertEqual(sh.pages["lock"].lost_text, logic.LINK_LOST, "the lock screen has no top line, so it says so itself")
        self.assertEqual(len(sh.pages["phone"].shown), 1, "no page was told anything new")
        sh.polled(sh.seq.issue(), self.state())
        self.assertEqual((sh.s_right.get_label(), sh.pages["lock"].lost_text), ("80%  ", ""))

    def test_an_error_body_is_not_a_state(self):
        sh = self.sh
        sh.polled(sh.seq.issue(), {"error": "forbidden"})
        self.assertEqual(sh.snap, {})
        self.assertEqual(sh.s_right.get_label(), logic.LINK_WAITING)

    def test_a_locked_server_sends_the_owner_to_the_browser(self):
        sh = self.sh
        sh.polled(sh.seq.issue(), {"locked": True, "battery": {"percent": 80}})
        self.assertEqual((sh.leaving, sh.quit_calls), (self.shell_module.LOCKED, 1))

    def test_a_request_to_change_page_while_the_cover_is_up_waits_and_the_page_stays_with_the_stack(self):
        sh = self.sh
        sh.warming = True
        sh.go("home")
        self.assertEqual((sh.page, sh.wanted, sh.pages["home"].entered), ("lock", "home", 0))
        sh.go("clock")      # an alarm rings while the cover is up: the newest request is the one kept
        self.assertEqual(sh.wanted, "clock")
        sh.warming = False
        sh.show_page = lambda *a: None
        sh.go("clock")
        self.assertEqual(sh.page, "clock")

    def test_the_start_up_walk_ends_on_the_lock_page_then_makes_the_waiting_request(self):
        sh, shown = self.sh, []
        sh.warming, sh.wanted = True, "home"
        sh.show_page = lambda name, how=None: shown.append(name)
        sh.warm()
        step, _args = self.timers[0]
        while step():
            pass
        self.assertEqual(shown[-2:], ["lock", "home"], "the walk ends on the true page, then the waiting request is made")
        self.assertFalse(sh.warming)
        self.assertEqual(sh.page, "home")
        self.assertNotIn("SLIDE", " ".join(map(str, shown)))

    def test_ready_needs_a_frame(self):
        sh = self.sh
        sh.warming, sh.wanted = True, None
        sh.show_page = lambda *a: None
        sh.warm()
        step, _args = self.timers[0]
        while step():
            pass
        self.assertFalse(sh.said_ready, "mapped and warm, but no frame has been drawn")
        sh.ready.mark("painted")
        self.assertTrue(sh.said_ready)

    def test_an_alarm_brings_the_clock_forward_once(self):
        sh = self.sh
        sh.show_page = lambda *a: None
        ringing = {"ringing": {"id": "a1"}, "clock_ok": True}
        sh.clock_seen(1, ringing)
        self.assertEqual(sh.page, "clock")
        sh.go("home")
        sh.clock_seen(2, ringing)
        self.assertEqual(sh.page, "home", "the owner left; the next poll does not drag them back")

    def test_a_shell_with_no_clock_page_still_knows_the_clock(self):
        sh = self.sh
        del sh.pages["clock"]
        self.assertTrue(sh.clock_known())
        sh.snap = {"battery": {"clock_ok": False}}
        self.assertFalse(sh.clock_known())

    def test_the_server_says_whether_it_has_a_pin_lock(self):
        import urllib.error
        core = self.shell_module.Core("http://127.0.0.1:1")
        core.token = "t"
        asked = []

        def opener(result):
            def _open(path, headers=None):
                asked.append((path, headers))
                if isinstance(result, Exception):
                    raise result
                return result
            return _open
        core._open = opener(object())
        self.assertTrue(core.lock_on(), "/api/lock answers only where OPOS_LOCK=1")
        self.assertEqual(asked[0], ("/api/lock", {"X-OPOS-Token": "t"}))
        core._open = opener(urllib.error.HTTPError("u", 404, "not found", {}, None))
        self.assertFalse(core.lock_on())
        core._open = opener(urllib.error.HTTPError("u", 500, "broken", {}, None))
        with self.assertRaises(urllib.error.HTTPError):
            core.lock_on()      # unknown: the poll asks again rather than guessing


if __name__ == "__main__":
    unittest.main()
