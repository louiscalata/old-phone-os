# SPDX-License-Identifier: GPL-3.0-or-later
"""The screens of the native shell. Each one is two panes: what you read, and what is under your thumb.

A screen draws and asks; the rules stay in the OPOS server. Phone and Messages use the server's main state.
What a screen shows is always the newest answer the server gave (an older reply never puts the screen back), and a
change the owner asks for shows only when the server has confirmed it: a typed message, a saved note or a new alarm
stays where it is until then, and a refusal is shown in the server's words.
Clock, Contacts, Notes and Call log use the server's own app endpoints (GET and POST /api/<app>), so the native
screens and the old interface show the same alarms, people, notes and calls. The calculator needs no server.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
from datetime import datetime, timezone

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from opos import apps, notices  # noqa: E402
from opos.native import logic  # noqa: E402
from opos.native.ui import Keys, bar, button, column, fill, hbox, label, reading, vbox  # noqa: E402

MISSING = "%s is not part of the OPOS installed on this phone yet. It arrives with the next update."
ERRORS = {"full": "That list is full.", "id": "That one cannot be changed.", "name": "It needs a name.",
          "hour": "That is not a time.", "minute": "That is not a time.", "favorites": "Six favourites is the most.",
          "size": "That note is too long.", "seconds": "That timer is too long."}
def emergency_words(risks):
    """The shared risk line about emergency calls (notices.RISKS, shown by the browser under About), from its
    sentence that names emergency calls on. A real call is never called simulated, and a reworded notice never
    stops the shell from loading."""
    for line in risks:
        sentences = line.split(". ")
        for i, sentence in enumerate(sentences):
            if "emergency" in sentence.lower():
                words = ". ".join(sentences[i:])
                return words[:1].upper() + words[1:]
    return "Do not rely on this phone for emergency calls. Keep another way to call for help."


# Said on Phone whenever calls are real.
EMERGENCY = emergency_words(notices.RISKS)


class Page:
    """One screen. build() returns (reading pane, thumb pane); show() gets every state the server sends."""

    name, title, api = "", "", None

    def __init__(self, shell):
        self.shell, self.data = shell, None
        self.seq = logic.Sequence()
        self.note = label("", "dim")
        self.note.set_visible(False)
        self.read, self.act = self.build()

    def build(self):
        raise NotImplementedError

    def show(self, snap):
        pass

    def enter(self):
        self.refresh()

    def refresh(self):
        """Ask this screen's app for its newest state (the apps' own pages, not the main state)."""
        if self.api:
            self.call()

    def tick(self):
        pass

    def preview(self, snap):
        """(text, rows) for the reading screen while this app's tile is chosen on Home."""
        return "", []

    def home(self):
        return button("Home", lambda: self.shell.go("home"))

    def say(self, text):
        self.note.set_label(text)
        self.note.set_visible(bool(text))

    def call(self, data=None, done=None):
        """Ask this screen's app endpoint without blocking the window. The answer is drawn only if no newer answer has
        been; done(answer, fresh) is called for every answer, so the screen that asked can tell it was confirmed.
        Returns the request's number."""
        n = self.seq.issue()

        def work():
            try:
                got = self.shell.core.app(self.api, data)
            except urllib.error.HTTPError as err:
                try:
                    got = {"error": json.loads(err.read().decode("utf-8", "replace")).get("error") or "refused"}
                except Exception:       # a body that is not JSON, or a connection lost while reading it: still an answer
                    got = {"error": "refused"}
                if err.code == 404:
                    got = {"missing": True}
            except Exception as err:
                got = {"error": str(err)[:60]}
            GLib.idle_add(self.deliver, n, got, done, data is not None)
        threading.Thread(target=work, daemon=True).start()
        return n

    def deliver(self, n, data, done=None, wrote=False):
        # The reply to a write (an alarm added, a note saved) is accepted with a floor, as Shell.answered does: a refresh
        # issued while the write was on its way may have been answered before the server acted, so it is refused even
        # though its number is newer; otherwise it could put back a list without the new alarm. Only a confirmed write
        # sets the floor: a failed one (an error, or the app missing) says nothing about the server's state, so a newer
        # refresh must still be drawn.
        fresh = self.seq.accept(n, floor=wrote and self.confirmed(data))
        if fresh:
            self.got(data)
        if done:
            done(data, fresh)
        if wrote and not fresh and self.confirmed(data):
            self.refresh()      # the change went through, but a newer answer was drawn first: draw what is true now
        return False

    @staticmethod
    def confirmed(data):
        """True when an app's answer is a real answer: not an error and not "this app is not installed"."""
        return isinstance(data, dict) and "error" not in data and not data.get("missing")

    @staticmethod
    def refusal_words(data):
        if isinstance(data, dict) and data.get("missing"):
            return ""
        return ERRORS.get((data or {}).get("error"), "That did not go through.")

    def got(self, data):
        if data.get("missing"):
            self.say(MISSING % self.title)
        elif "error" in data:
            self.say(self.refusal_words(data))
        else:
            self.say(data.get("recovered") or data.get("saved") or "")
            self.data = data
            self.paint(data)
        return False

    def paint(self, data):
        pass


def people_of(snap):
    return [(c.get("name", ""), c.get("number", "")) for c in snap.get("contacts") or [] if isinstance(c, dict)]


class Lock(Page):
    name = "lock"

    def build(self):
        self.time, self.date = label("--:--", "clock"), label("", "big")
        self.batt, self.story = label("", "big"), label("", "dim")
        self.link = label("", "big")
        self.link.set_visible(False)
        read = reading(self.time, self.date, self.batt, self.story, self.link)
        act = column(bottom=(label("Swipe up to unlock", "dim", x=0.5),
                             bar(button("Call", lambda: self.shell.go("phone")),
                                 button("Unlock", lambda: self.shell.go("home"), "main"))))
        return read, act

    def show(self, snap):
        b = snap.get("battery") or {}
        self.batt.set_label("%s%%%s" % (b.get("percent", "--"), "  charging" if snap.get("charging") else ""))
        self.story.set_label(snap.get("lock_story") or "")

    def lost(self, text):
        """The connection line: shown while the server does not answer, hidden when it does."""
        self.link.set_label(text)
        self.link.set_visible(bool(text))

    def tick(self):
        now = time.localtime()
        self.time.set_label(self.shell.time_text(now))
        self.date.set_label(time.strftime("%A, %B %d", now).replace(" 0", " ") if self.shell.clock_known() else "")


class Home(Page):
    """The tiles under the thumb; the other screen previews the chosen app, live, and its rows can be tapped."""
    name = "home"

    def build(self):
        self.chosen = "phone"
        self.v_title, self.v_text, self.v_note = label("", "head"), label("", "big"), label("", "dim")
        self.v_rows = vbox(spacing=0)
        read = reading(self.v_title, self.v_text, self.v_rows, self.v_note)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        for i, (app_id, name) in enumerate(self.shell.apps):
            tile = button(name, lambda a=app_id: self.shell.go(a), "tile")
            hold = Gtk.GestureLongPress()
            hold.connect("pressed", lambda g, *_xy, a=app_id: (g.set_state(Gtk.EventSequenceState.CLAIMED), self.choose(a)))
            tile.add_controller(hold)
            grid.attach(tile, i % 2, i // 2, 1, 1)
        self.open = button("Open", lambda: self.shell.go(self.chosen), "main")
        act = column(label("Tap to open. Press and hold to look first.", "dim"),
                     bottom=(grid, bar(button("Lock", lambda: self.shell.go("lock")), self.open)))
        return read, act

    def enter(self):
        self.shell.pages[self.chosen].refresh()     # the preview must not be what the app showed at its last visit

    def choose(self, app_id):
        self.chosen = app_id
        self.shell.pages[app_id].refresh()
        self.show(self.shell.snap)

    def show(self, snap):
        page = self.shell.pages[self.chosen]
        text, rows = page.preview(snap)
        self.v_title.set_label(page.title)
        self.v_text.set_label(text)
        self.v_text.set_visible(bool(text))
        self.v_note.set_label(snap.get("home_note") or "")     # the server words it: practice mode, or calls and messages on
        self.open.set_label("Open %s" % page.title)
        fill(self.v_rows, rows)


class Phone(Page):
    name, title = "phone", "Phone"

    def build(self):
        self.pending = logic.Pending()      # "call" or "hang_up" until a drawn state includes it
        self.status, self.people = label("", "big"), vbox(spacing=0)
        read = reading(label("Phone", "head"), self.status, self.people)
        self.number = label("", "head", wrap=False, x=0.5)
        self.number.add_css_class("rule")
        pad = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        for i, key in enumerate("123456789+0<"):
            pad.attach(button("Delete" if key == "<" else key, lambda k=key: self.dial_key(k), "pad"), i % 3, i // 3, 1, 1)
        self.go_call = button("Call", self.call_or_hang, "main")
        act = column(bottom=(self.number, pad, bar(self.home(), self.go_call)))
        return read, act

    def dial(self, number):
        self.number.set_label(number)

    def dial_key(self, key):
        cur = self.number.get_label()
        self.number.set_label(cur[:-1] if key == "<" else (cur + key)[:20])

    def call_or_hang(self):
        """One request at a time: a second tap would send a second call. The button stays busy until a state that
        includes the request is drawn (logic.Pending), so a late or stale reply can never re-enable Call."""
        if self.pending.what:
            return
        if self.shell.snap.get("in_call"):
            what, fields = "hang_up", {}
        elif self.number.get_label():
            what, fields = "call", {"number": self.number.get_label()}
        else:
            return
        self.pending.start(what)
        sent = []
        sent.append(self.shell.ask(what, done=lambda reply: self.answered(sent[0], reply), **fields))
        self.show(self.shell.snap)

    def answered(self, n, reply):
        self.pending.answered(n, self.shell.snap_seq, self.shell.seq.issued, reply is not None)
        self.show(self.shell.snap)

    def show(self, snap):
        self.pending.seen(self.shell.snap_seq)
        busy = self.pending.what
        lines = [x for x in (logic.call_words(snap), logic.refusal(snap)) if x]
        lines.append("Practice only: this phone cannot make real calls yet." if logic.simulated(snap) else EMERGENCY)
        if busy:
            lines.append("Asking the phone to %s." % ("call" if busy == "call" else "hang up"))
        self.status.set_label("\n".join(lines))
        self.status.set_visible(bool(lines))
        self.go_call.set_label({"call": "Calling", "hang_up": "Hanging up"}.get(busy) or ("Hang up" if snap.get("in_call") else "Call"))
        self.go_call.set_sensitive(not busy)
        fill(self.people, [(n or num, num, lambda v=num: self.dial(v)) for n, num in people_of(snap)])

    def preview(self, snap):
        def ring(number):
            self.dial(number)
            self.shell.go("phone")
        return logic.call_words(snap), \
            [("Call %s" % (n or num), num, lambda v=num: ring(v)) for n, num in people_of(snap)]


class Messages(Page):
    name, title = "messages", "Messages"
    QUICK = ("OK", "Call me", "On my way")

    def build(self):
        self.convo, self.draft, self.typed = logic.Conversation(), logic.Draft(), logic.Entry(limit=480)
        self.outcome = ""        # what the last send did, in the server's words
        self.head, self.list, self.thread = label("Messages", "head"), vbox(spacing=0), label("", "big")
        self.practice = label("", "dim")
        read = reading(self.head, self.practice, self.list, self.thread)
        self.line = label("", "typed")
        self.send = button("Send", self.send_typed, "main")
        self.keys = Keys(self.key)
        self.quick = hbox(*[button(t, lambda t=t: self.say_quick(t), "chip") for t in self.QUICK], spacing=8)
        self.hint = label("Choose a person to write to.", "dim")
        act = column(self.hint, bottom=(self.quick, self.line, self.keys, bar(self.home(), self.send)))
        self.paint_line()
        return read, act

    @property
    def to(self):
        return self.convo.to

    def key(self, ch):
        if self.draft.pending is None and self.typed.key(ch):
            self.outcome = ""
            self.paint_line()

    def paint_line(self):
        self.line.set_label(self.typed.text or " ")
        for w in (self.quick, self.line, self.keys, self.send):
            w.set_sensitive(self.to is not None and self.draft.pending is None)
        self.send.set_label("Sending" if self.draft.pending is not None else "Send")
        self.hint.set_visible(self.to is None)

    def open_thread(self, number):
        self.convo.open(number)
        self.outcome = ""
        self.paint_line()
        self.show(self.shell.snap)
        self.shell.ask("open", number=number, done=lambda reply: self.opened(number, reply))

    def opened(self, number, reply):
        if reply is not None and "error" not in reply:
            self.convo.opened(number, reply.get("thread"), self.shell.seq.issued)
        else:
            self.convo.open_failed(number)
        self.show(self.shell.snap)

    def close_thread(self):
        self.convo.close()
        self.outcome = ""
        self.paint_line()
        self.show(self.shell.snap)

    def say_quick(self, text):
        if self.to:
            self.start_send(text, False)

    def send_typed(self):
        if self.to:
            self.start_send(self.typed.text, True)

    def start_send(self, text, boxed):
        """Send text to the open person. A typed draft stays in the box until the server says it went out."""
        number = self.to
        if not self.draft.start(text, boxed):
            return
        self.outcome = ""
        self.paint_line()
        self.shell.ask("send", number=number, text=text.strip(), done=lambda reply: self.sent(number, reply))

    def sent(self, number, reply):
        ok = reply is not None and logic.sent(reply)
        if ok:
            self.outcome = reply.get("notice") or ""
            if number == self.to:
                self.convo.sent(reply.get("thread"), self.shell.seq.issued)
        else:
            self.outcome = logic.refusal(reply or {}) or "That did not go through. Nothing was sent."
        self.typed.text = self.draft.done(ok, self.typed.text)
        self.paint_line()
        self.show(self.shell.snap)

    def rows(self, snap, opener):
        seen, rows = set(), []
        for t in snap.get("threads") or []:
            if isinstance(t, dict) and t.get("number"):
                seen.add(t["number"])
                rows.append((t.get("name") or t["number"], (t.get("last") or "")[:60], lambda v=t["number"]: opener(v)))
        return rows + [(n or num, "No messages yet", lambda v=num: opener(v)) for n, num in people_of(snap) if num not in seen]

    def show(self, snap):
        self.practice.set_label(logic.messages_line(snap, self.outcome))
        self.practice.set_visible(bool(self.practice.get_label()))
        if self.to is None:
            self.head.set_label("Messages")
            fill(self.list, self.rows(snap, self.open_thread))
            self.thread.set_label("")
            return
        self.convo.snapshot(snap.get("thread"), self.shell.snap_seq)
        names = dict((num, n) for n, num in people_of(snap))
        self.head.set_label(names.get(self.to) or self.to)
        fill(self.list, [("All messages", "", self.close_thread)])
        self.thread.set_label(self.convo.body())

    def preview(self, snap):
        def opener(number):
            self.shell.go("messages")
            self.open_thread(number)
        return "", self.rows(snap, opener)


class Clock(Page):
    name, title, api = "clock", "Clock", "clock"

    def build(self):
        self.digits, self.daily, self.chosen = "", False, None
        self.now, self.next, self.timer = label("", "clock"), label("", "big"), label("", "big")
        self.warn = label(logic.CLOCK_UNSET, "big")
        self.warn.set_visible(False)
        self.alarms = vbox(spacing=0)
        read = reading(label("Clock", "head"), self.note, self.warn, self.now, self.next, self.timer, self.alarms)
        self.ring = hbox(button("Snooze", lambda: self.call({"action": "snooze", "id": self.ringing}), "pad"),
                         button("Stop", lambda: self.call({"action": "stop", "id": self.ringing}), "main"))
        self.ringing = None
        self.pick = label("", "big")
        self.sure, self.adding = logic.Confirm(), False      # Remove asks twice; Add waits for its answer
        self.remove_button = button("Remove", self.remove, "chip")
        self.pick_box = vbox(self.pick, hbox(button("On or off", self.toggle, "chip"), self.remove_button,
                                             button("Done", lambda: self.choose(None), "chip"), spacing=8))
        self.typed = label("", "head", wrap=False, x=0.5)
        self.typed.add_css_class("rule")
        self.once = button("Once", lambda: self.set_daily(False), "chip")
        self.every = button("Every day", lambda: self.set_daily(True), "chip")
        pad = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        for i, key in enumerate("123456789 0<"):
            if key != " ":
                pad.attach(button("Delete" if key == "<" else key, lambda k=key: self.digit(k), "key"), i % 3, i // 3, 1, 1)
        self.timers = hbox(*[button("%d min" % m, lambda m=m: self.call({"action": "timer_start", "seconds": m * 60}), "chip")
                             for m in (5, 10, 30)], spacing=8)
        self.cancel = button("Cancel the timer", lambda: self.call({"action": "timer_cancel"}), "chip")
        self.add = button("Add alarm", self.add_alarm, "main")
        act = column(self.ring, self.pick_box, bottom=(label("Timer", "dim"), self.timers, self.cancel,
                                                        label("New alarm: type the time", "dim"), self.typed,
                                                        hbox(self.once, self.every, spacing=8), pad, bar(self.home(), self.add)))
        self.set_daily(False)
        self.choose(None)
        self.ring.set_visible(False)
        self.cancel.set_visible(False)
        return read, act

    def set_daily(self, daily):
        self.daily = daily
        (self.every.add_css_class if daily else self.every.remove_css_class)("on")
        (self.once.remove_css_class if daily else self.once.add_css_class)("on")

    def digit(self, key):
        self.digits = self.digits[:-1] if key == "<" else (self.digits + key)[:4]
        self.typed.set_label(logic.digits_shown(self.digits) or " ")

    def add_alarm(self):
        if self.adding:          # one add at a time: a second tap would make a second alarm
            return
        when = logic.clock_digits(self.digits)
        if when is None:
            self.say("Type the time as four numbers, like 0730.")
            return
        sent = self.digits
        self.set_adding(True)

        def done(data, _fresh):
            self.set_adding(False)
            # the typed time is cleared only when the server has made the alarm; a refusal leaves it to be corrected
            if not self.confirmed(data):
                self.say(self.refusal_words(data) or MISSING % self.title)
            elif self.digits == sent:
                self.digits = ""
                self.typed.set_label(" ")
        self.call({"action": "add", "hour": when[0], "minute": when[1], "days": list(range(7)) if self.daily else []}, done=done)

    def set_adding(self, on):
        self.adding = on
        self.add.set_label("Adding" if on else "Add alarm")
        self.add.set_sensitive(not on)

    def choose(self, alarm_id):
        self.chosen = alarm_id
        row = next((r for r in (self.data or {}).get("alarms", []) if r.get("id") == alarm_id), None)
        self.sure.keep_only(alarm_id if row else None)
        self.remove_button.set_label("Delete this alarm?" if self.sure.armed else "Remove")
        self.pick_box.set_visible(row is not None)
        if row:
            self.pick.set_label("%s  %s" % logic.alarm_line(row))

    def toggle(self):
        row = next((r for r in (self.data or {}).get("alarms", []) if r.get("id") == self.chosen), None)
        if row:
            self.call({"action": "toggle", "id": row["id"], "enabled": not row.get("enabled")})

    def remove(self):
        """The first tap asks "Delete this alarm?", the second deletes, as in the browser. The alarm stays on screen
        until the server has removed it."""
        if not self.chosen:
            return
        go = self.sure.tap(self.chosen)
        self.remove_button.set_label("Remove" if go else "Delete this alarm?")
        if go:
            self.call({"action": "delete", "id": self.chosen})

    def paint(self, data):
        rows = []
        for r in data.get("alarms") or []:
            title, detail = logic.alarm_line(r)
            rows.append((title, detail, lambda v=r.get("id"): self.choose(v)))
        fill(self.alarms, rows)
        known = data.get("clock_ok") is not False
        self.warn.set_visible(not known)
        nxt = data.get("next")
        self.next.set_label("" if not known else (("Next alarm: %s" % nxt.get("text")) if nxt else "No alarm is set."))
        self.next.set_visible(known)
        ringing = data.get("ringing")
        self.ringing = ringing.get("id") if ringing else None
        self.ring.set_visible(bool(ringing))
        if ringing:
            self.chosen = None
            self.say("%s is ringing. %s" % (ringing.get("text") or "An alarm", ringing.get("label") or ""))
        self.cancel.set_visible(bool(data.get("timer")))
        self.timers.set_visible(not data.get("timer"))
        self.choose(self.chosen)
        self.tick()

    def tick(self):
        self.now.set_label(self.shell.time_text())
        timer = (self.data or {}).get("timer")
        left = logic.timer_left(timer.get("fires_at"), datetime.now(timezone.utc).isoformat()) if timer else ""
        self.timer.set_label("Timer: %s" % left if left else "")
        self.timer.set_visible(bool(left))

    def preview(self, snap):
        data = self.data or {}
        nxt = data.get("next") if self.shell.clock_known() else None
        text = self.shell.time_text() + ("\nNext alarm: %s" % nxt.get("text") if nxt else "")
        if not self.shell.clock_known():
            text += "\n" + logic.CLOCK_UNSET
        return text, [("%s  %s" % logic.alarm_line(r), "", lambda: self.shell.go("clock")) for r in data.get("alarms") or []]


class Contacts(Page):
    name, title, api = "contacts", "Contacts", "contacts"

    def build(self):
        self.chosen, self.field, self.busy = None, "name", False
        self.entries = {"name": logic.Entry(limit=80), "number": logic.Entry(limit=24)}
        self.people = vbox(spacing=0)
        read = reading(label("Contacts", "head"), self.note, self.people)
        self.pick = label("", "big")
        self.sure = logic.Confirm()
        self.remove_button = button("Remove", self.remove, "chip")
        self.pick_box = vbox(self.pick, hbox(button("Call", self.ring, "chip"), button("Message", self.write, "chip"),
                                             self.remove_button, button("Done", lambda: self.choose(None), "chip"), spacing=8))
        self.boxes = {f: button("", lambda f=f: self.focus(f), "row") for f in self.entries}
        self.keys = Keys(self.key)
        self.save_button = button("Save", self.save, "main")
        act = column(self.pick_box, bottom=(label("New contact", "dim"), self.boxes["name"], self.boxes["number"], self.keys,
                                            bar(self.home(), self.save_button)))
        self.focus("name")
        self.choose(None)
        return read, act

    def focus(self, field):
        self.field = field
        self.keys.numbers(field == "number")
        self.paint_fields()

    def paint_fields(self):
        for f, b in self.boxes.items():
            b.set_label("%s%s: %s" % ("> " if f == self.field else "", f.capitalize(), self.entries[f].text))
            b.get_child().set_xalign(0.0)

    def key(self, ch):
        if self.busy:
            return
        if ch == "\n":
            self.focus("number" if self.field == "name" else "name")
        elif self.entries[self.field].key(ch):
            self.paint_fields()

    def save(self):
        """The new contact stays in the two boxes until the server has saved it; this save's own answer decides."""
        if self.busy:
            return
        sent = {f: e.text for f, e in self.entries.items()}
        self.busy = True
        self.save_button.set_label("Saving")
        self.save_button.set_sensitive(False)

        def done(data, _fresh):
            self.busy = False
            self.save_button.set_label("Save")
            self.save_button.set_sensitive(True)
            if self.confirmed(data):
                if {f: e.text for f, e in self.entries.items()} == sent:
                    for e in self.entries.values():
                        e.text = ""
                    self.focus("name")
            else:
                self.say(self.refusal_words(data) or MISSING % self.title)
        self.call({"action": "add", "name": sent["name"], "number": sent["number"]}, done=done)

    def person(self):
        return next((p for p in (self.data or {}).get("people", []) if p.get("id") == self.chosen), None)

    @staticmethod
    def number_of(person):
        numbers = person.get("numbers") or []
        return (numbers[0].get("number") or "") if numbers and isinstance(numbers[0], dict) else ""

    def choose(self, ident):
        self.chosen = ident
        person = self.person()
        self.sure.keep_only(ident if person else None)
        self.remove_button.set_label("Delete this person?" if self.sure.armed else "Remove")
        self.pick_box.set_visible(person is not None)
        if person:
            self.pick.set_label("%s\n%s" % (person.get("name") or "", self.number_of(person)))

    def ring(self):
        person = self.person()
        if person:
            self.shell.pages["phone"].dial(self.number_of(person))
            self.shell.go("phone")

    def write(self):
        person = self.person()
        if person and self.number_of(person):
            self.shell.go("messages")
            self.shell.pages["messages"].open_thread(self.number_of(person))

    def remove(self):
        """The first tap asks "Delete this person?", the second deletes, as in the browser. The person stays on screen
        until the server has removed them."""
        if not self.chosen:
            return
        go = self.sure.tap(self.chosen)
        self.remove_button.set_label("Remove" if go else "Delete this person?")
        if go:
            self.call({"action": "delete", "id": self.chosen})

    def paint(self, data):
        fill(self.people, [((p.get("name") or "") + ("  (favourite)" if p.get("favorite") else ""), self.number_of(p),
                            lambda v=p.get("id"): self.choose(v)) for p in data.get("people") or []])
        if data.get("practice"):
            self.say("These are practice contacts. Save one of your own and they go away.")
        self.choose(self.chosen)

    def preview(self, snap):
        def ring(number):
            self.shell.pages["phone"].dial(number)
            self.shell.go("phone")
        return "", [(n or num, num, lambda v=num: ring(v)) for n, num in people_of(snap)]


class Notes(Page):
    name, title, api = "notes", "Notes", "notes"

    def build(self):
        self.ident, self.entry = None, logic.Entry(limit=20000, multiline=True)
        self.want, self.busy = None, False     # the note being opened ("add" for a new one); a save or delete in flight
        self.want_n = None                     # the request number of the last open or add: only its reply is adopted
        self.sure = logic.Confirm()            # Delete asks twice, as in the browser
        self.head, self.list, self.text = label("Notes", "head"), vbox(spacing=0), label("", "big")
        read = reading(self.head, self.note, self.list, self.text)
        self.keys = Keys(self.key)
        self.close_button = button("All notes", self.close, "chip")
        self.delete_button = button("Delete this note", self.delete, "chip")
        self.tools = hbox(self.close_button, self.delete_button, spacing=8)
        self.main = button("New note", self.main_action, "main")
        act = column(bottom=(self.tools, self.keys, bar(self.home(), self.main)))
        self.mode()
        return read, act

    def mode(self):
        on = self.ident is not None
        for w in (self.keys, self.tools, self.text):
            w.set_visible(on)
        self.list.set_visible(not on)
        self.main.set_label("Save" if on else "New note")
        self.head.set_label("Note" if on else "Notes")
        self.sure.keep_only(self.ident)
        self.delete_button.set_label("Delete this note?" if self.sure.armed else "Delete this note")
        for w in (self.close_button, self.delete_button, self.main):
            w.set_sensitive(not self.busy)

    def key(self, ch):
        if not self.busy and self.entry.key(ch):
            self.text.set_label(self.entry.text + "_")

    def open_note(self, ident):
        self.want = ident
        self.ask_note({"action": "open", "id": ident})

    def ask_note(self, data):
        """Open a note or start a new one; only this request's own reply can become the open note."""
        sent = []
        self.want_n = self.call(data, done=lambda answer, _fresh: self.adopt(answer, sent[0]))
        sent.append(self.want_n)

    def adopt(self, data, n):
        """An opened or new note arrives as the reply to request n. Only the reply to the last open or add request is
        taken, by its request number, so a late answer for an older tap can never become the open note (a Save would
        then write into the wrong one): not even an existing note's reply arriving after New note was tapped."""
        if n is None or n != self.want_n or self.want is None:
            return
        if self.confirmed(data) and isinstance(data.get("text"), str) and data.get("id") and self.want in ("add", data.get("id")):
            self.want, self.want_n = None, None
            self.ident, self.entry.text = data["id"], data["text"]
            self.text.set_label(self.entry.text + "_")
            self.mode()

    def main_action(self):
        if self.busy:
            return
        if self.ident is None:
            self.want = "add"
            self.ask_note({"action": "add"})
        else:
            self.change({"action": "save", "id": self.ident, "text": self.entry.text})

    def change(self, data, leave=False):
        """Save or delete the open note. The editor stays, with its text, until the server confirms; with leave it
        closes only then, and only if it still shows that note."""
        ident = self.ident
        self.busy = True
        self.mode()

        def done(answer, _fresh):
            self.busy = False
            if self.confirmed(answer):
                if leave and self.ident == ident:
                    self.ident = None
            else:
                self.say(self.refusal_words(answer) or MISSING % self.title)
            self.mode()
        self.call(data, done=done)

    def close(self):
        if self.ident is not None and not self.busy:
            self.change({"action": "save", "id": self.ident, "text": self.entry.text}, leave=True)

    def delete(self):
        """The first tap asks "Delete this note?", the second deletes."""
        if self.ident is None or self.busy:
            return
        if self.sure.tap(self.ident):
            self.change({"action": "delete", "id": self.ident}, leave=True)
        else:
            self.mode()

    def paint(self, data):
        fill(self.list, [(n.get("title") or "Empty note", "", lambda v=n.get("id"): self.open_note(v))
                         for n in data.get("notes") or []])
        if not data.get("notes") and self.ident is None:
            self.say("No notes yet. New note starts one.")

    def preview(self, snap):
        if self.data is None:
            return "Loading the notes.", []
        return "", [(n.get("title") or "Empty note", "", lambda: self.shell.go("notes")) for n in self.data.get("notes") or []]


class Calculator(Page):
    name, title = "calc", "Calculator"
    KEYS = ("C", "÷", "×", "<", "7", "8", "9", "-", "4", "5", "6", "+", "1", "2", "3", "=", "0", ".")

    def build(self):
        self.calc = logic.Calc()
        self.tape = label("", "big")
        read = reading(label("Calculator", "head"), self.note, self.tape)
        self.sum = label("0", "head", wrap=False, x=1.0)
        self.sum.add_css_class("rule")
        pad = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True, row_homogeneous=True)
        for i, key in enumerate(self.KEYS[:15]):
            pad.attach(button("Delete" if key == "<" else key, lambda k=key: self.press(k), "pad"), i % 4, i // 4, 1, 1)
        pad.attach(button("=", lambda: self.press("="), "pad", "on"), 3, 3, 1, 2)
        pad.attach(button("0", lambda: self.press("0"), "pad"), 0, 4, 2, 1)
        pad.attach(button(".", lambda: self.press("."), "pad"), 2, 4, 1, 1)
        act = column(bottom=(self.sum, pad, bar(self.home())))
        return read, act

    def press(self, key):
        self.calc.press(key)
        self.sum.set_label(self.calc.expr or "0")
        self.tape.set_label("\n".join(self.calc.tape))
        self.say(self.calc.note)

    def preview(self, snap):
        return "\n".join(self.calc.tape[-6:]) or "No sums yet.", []


class Calls(Page):
    name, title, api = "calls", "Call log", "calls"

    def build(self):
        self.rows = vbox(spacing=0)
        read = reading(label("Call log", "head"), self.note, self.rows)
        self.missed = label("", "big")
        self.sure = hbox(button("Keep it", lambda: self.sure.set_visible(False)),
                         button("Yes, clear", self.clear_log, "main"))
        self.sure.set_visible(False)
        act = column(self.missed, bottom=(self.sure, bar(self.home(), button("Clear the log", lambda: self.sure.set_visible(True)))))
        return read, act

    def clear_log(self):
        self.sure.set_visible(False)
        self.call({"action": "clear", "confirm": True})

    def back(self, number):
        self.shell.pages["phone"].dial(number)
        self.shell.go("phone")

    def lines(self, data):
        return [logic.call_line(r) + ((lambda v=r.get("number") or "": self.back(v)),) for r in data.get("calls") or []]

    def paint(self, data):
        fill(self.rows, self.lines(data))
        self.missed.set_label("Missed calls: %d" % (data.get("missed_count") or 0))
        if not data.get("calls"):
            self.say("No calls recorded. Tap a call here to call back.")

    def preview(self, snap):
        if self.data is None:
            return "Loading the call log.", []
        return "" if self.data.get("calls") else "No calls recorded.", self.lines(self.data)[:8]


class Settings(Page):
    name, title = "settings", "Settings"

    def build(self):
        self.hand = button("", lambda: self.shell.set_hand("left" if self.shell.hand == "right" else "right"), "main")
        read = reading(label("Settings", "head"),
                       label("Hand", "big"),
                       label("The buttons sit on the screen under that hand; the other screen is for reading. "
                             "You can also swipe sideways across the hinge: the buttons follow your finger.", "mid", "dim"),
                       label("Gestures", "big"),
                       label("Down from the top edge: battery. Up from the bottom edge: Home. Up on the lock screen: unlock.", "mid", "dim"),
                       label("About", "big"),
                       label(notices.as_text(), "mid", "dim"))
        act = column(bottom=(self.hand, button("Use the old interface", self.shell.leave),
                             bar(self.home())))
        self.paint_hand()
        return read, act

    def paint_hand(self):
        self.hand.set_label("Buttons on the %s. Tap to switch." % self.shell.hand)

    def preview(self, snap):
        b = snap.get("battery") or {}
        return "%s%%  %s\n%s" % (b.get("percent", "--"), b.get("time_left") or "", b.get("headline") or ""), \
            [("Buttons on the %s" % self.shell.hand, "Tap to switch", lambda: self.shell.set_hand("left" if self.shell.hand == "right" else "right"))]


PAGES = (Lock, Home, Phone, Messages, Clock, Contacts, Notes, Calculator, Calls, Settings)


def enabled_pages(env=None):
    """The pages this phone has. Clock, Contacts, Notes and Call log are the server's everyday apps: each is shown only
    when OPOS_APPS switches it on (opos.apps, which loads it as the server does); absent or empty means none of them.
    Every other page needs nothing from that list."""
    on = {a.id for a in apps.enabled(env)}
    return tuple(c for c in PAGES if c.api is None or c.api in on)
