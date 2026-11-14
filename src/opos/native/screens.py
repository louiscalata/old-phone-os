# SPDX-License-Identifier: GPL-3.0-or-later
"""The screens of the native shell. Each one is two panes: what you read, and what is under your thumb.

A screen draws and asks; the rules stay in the OPOS server. Phone and Messages use the server's main state.
What a screen shows is always the newest answer the server gave (an older reply never puts the screen back), and a
change the owner asks for shows only when the server has confirmed it: a typed message, a saved note or a new alarm
stays where it is until then, and a refusal is shown in the server's words.
Clock, Contacts, Notes and Call log use the server's own app endpoints (GET and POST /api/<app>), so the native
screens and the old interface show the same alarms, people, notes and calls. The calculator needs no server.
Network (opened from Settings) uses the server's net_* actions, the same ones the browser's /network page uses.
"""
from __future__ import annotations

import functools
import json
import threading
import time
import urllib.error
from datetime import datetime, timezone

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from opos import apps, notices  # noqa: E402
from opos.native import logic, netlogic  # noqa: E402
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
    tile = True         # False: the page has no tile on Home and is opened from another page

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

    def leave(self):
        """Shell.go calls this on the page being left, before the next page's enter()."""

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
        act = column(bottom=(self.hand, button("Network", lambda: self.shell.go("network")),
                             button("Use the old interface", self.shell.leave), bar(self.home())))
        self.paint_hand()
        return read, act

    def paint_hand(self):
        self.hand.set_label("Buttons on the %s. Tap to switch." % self.shell.hand)

    def preview(self, snap):
        b = snap.get("battery") or {}
        return "%s%%  %s\n%s" % (b.get("percent", "--"), b.get("time_left") or "", b.get("headline") or ""), \
            [("Buttons on the %s" % self.shell.hand, "Tap to switch", lambda: self.shell.set_hand("left" if self.shell.hand == "right" else "right")),
             ("Network", "Wi-Fi and radios", lambda: self.shell.go("network"))]


def owners_tap(fn):
    """Marks a handler as the owner's own tap: whatever it changes on a button is what the owner asked for, so it starts
    no hold and ends one (Network.shown). Every other change to what a button says (a newer read, an answer) starts one."""
    @functools.wraps(fn)
    def tap(self, *args):
        outer, self.by_owner = self.by_owner, True
        try:
            return fn(self, *args)
        finally:
            self.by_owner = outer
    return tap


class Network(Page):
    """Wi-Fi and the radios, through the server's net_* actions (opos.net does every change). No Home tile: Settings
    opens it.

    Nothing is asked while the page is not the one showing. On entering: the radios once, then one scan; while it shows,
    the radios again every POLL_MS (never a rescan). Nothing changes the Wi-Fi unless the owner taps for it: Forget,
    Disconnect and Turn Wi-Fi off take two taps, as deleting does elsewhere, and Wi-Fi is never switched while its state
    is not known. Each drawn radios read or list finds the chosen row again by name (rechoose); a Forget or Disconnect
    prompt is dropped when that row changes or the connected network does. Disconnect names no network to the core, so
    its confirming tap sends nothing by itself: if the radios read drawn names the prompt's network as the connected
    one, the radios are read again (skipping the core's cache), the page is busy meanwhile, and the disconnect is sent
    only when that read, asked after the tap, still names it (checked()); the screen names the network it checked. A
    read that names another network or none, a choice closed or changed by the owner meanwhile (drop_check()), or the
    page being left sends nothing. What is left is the moment between the core's read and its disconnect: the core's
    action would have to name the network to close that. A tap on the main button or
    the Wi-Fi button acts only on what the owner has seen it say for at least HOLD_S: whenever what one of them says or
    does changes for any reason other than the owner's own tap (a newer radios read, a newer list, a change's answer),
    a tap on it in the next HOLD_S seconds sends nothing and says the button changed (netlogic.CHANGED). So an armed or
    unarmed Disconnect that turns into Join, or Turn Wi-Fi off that turns into Turn Wi-Fi on, never acts on a tap meant
    for what it said before. The Wi-Fi button is held as well when it appears by itself (the pane goes back to idle
    because a newer list dropped the chosen row, or a change's answer closed the choice). The rows follow the same rule:
    when a newer list puts another network in a place, or changes what a tap on a place would offer, a tap on a row in
    the next HOLD_S seconds chooses nothing and says the list changed (netlogic.LIST_CHANGED). The owner's own taps start
    no hold, so a prompt they armed is confirmed by the very next tap and a row they chose is joined at once. The radios
    and the scan each keep their own order (logic.Sequence): an older answer never replaces a newer one. Their request
    numbers come from one count (ask()), so between a radios read and a list it is known which was asked later: whether
    the chosen row is the connected one follows the later-asked of the two drawn (current()), so a list asked before
    the radios read drawn cannot put "connected" back or take it away, nor a radios read asked before the list drawn,
    whichever answer arrives last. Every answer
    from the core to a change (confirmed, failed, or with no answer in time on either side: it may still be going on)
    sets a floor (set_floor): every answer to a request sent before it is refused, the radios read drawn before it is
    treated as unknown until a read sent after it is drawn (so Disconnect and the Wi-Fi button do nothing until then),
    then both are read again. Only a refusal before anything ran (an HTTP 4xx such as 429, netlogic.NOT_SENT) sets
    none. Each visit has a number (leave() starts the
    next); a radios read or a list from an earlier visit is not drawn and starts nothing, and leaving forgets the radios
    read and the list drawn in the visit (the network may change while the page is not showing), so each visit starts
    at the floor until its own first reads are drawn. A change's answer from an
    earlier visit says its outcome, sets the same floor in the same cases, frees the page and, while it shows, reads
    the radios again (the list too, when this visit's scan was put off), and leaves this visit's choice and typing as
    they are. A typed password lives only in self.secret (netlogic.Secret), is shown as bullets, and belongs to the one
    network name its keys were typed for: any edit of a hidden network's name empties it and says so
    (netlogic.NAME_CHANGED), and a join sends it only with that same name (Secret.is_for), else nothing is sent and it
    is asked for again. It is sent once in the join, and is emptied before the join is handed to the sending thread, on every answer to a change sent in this
    visit, on Cancel, on choosing another row, when the chosen row goes or needs no password, and when the page is left;
    while the page is busy, password keys are not kept and the page says so (netlogic.KEYS_WAIT). The page stays busy
    until a change's answer comes, even across visits, so nothing can be typed in a later visit before an earlier
    visit's answer. The sending thread drops its copy once the request has gone. Nothing here prints, logs or saves it.
    """
    name, title, tile = "network", "Network", False
    POLL_MS = 10000
    TITLES = {"name": "Name", "password": "Password"}
    # how long the shell waits for each action: the core may take up to about 95 s for a join (opos.net JOIN_WAIT_S and
    # its own budget), 35 s for a forget; a read is quicker
    WAIT = {"net_status": 20, "net_scan": 30, "net_join": 100, "net_forget": 45, "net_wifi": 35}
    CHANGES = ("net_join", "net_forget", "net_wifi")
    HOLD_S = 1.5    # after a button changes by itself (not by the owner's tap), taps on it are ignored this long

    def build(self):
        self.scan_data, self.status_data, self.chosen, self.other = None, None, None, False
        self.busy, self.polling, self.timer, self.open_net, self.field = False, None, None, False, "name"
        self.visit, self.scan_owed = 0, False   # which visit this is (leave() starts a new one); a scan put off by busy
        self.held = {}                      # button -> until when a tap on it is ignored (hold())
        self.seen, self.by_owner = {}, False    # what each button last said (shown()); inside an owners_tap handler
        self.radios, self.found = logic.Sequence(), logic.Sequence()    # the order of the status and of the scan answers
        self.sure = logic.Confirm()         # Forget, Disconnect and Turn Wi-Fi off ask twice
        self.secret, self.ssid_entry = netlogic.Secret(), logic.Entry(limit=32)
        self.status_n, self.scan_n = 0, 0   # the request numbers (ask()) of the radios read and of the list now drawn
        self.checking = None                # (network, request number) while a confirming Disconnect tap's read waits
        self.lines, self.nets = label("Reading the radios...", "mid"), vbox(spacing=0)
        read = reading(label("Network", "head"), self.lines, self.note, self.nets)
        self.pick, self.why = label("", "big"), label("", "mid", "dim")
        self.typed = label(" ", "typed")
        self.boxes = {f: button("", lambda f=f: self.focus(f), "row") for f in self.TITLES}
        self.secured = button("Has a password", lambda: self.set_open(False), "chip")
        self.unsecured = button("Open network", lambda: self.set_open(True), "chip")
        self.kinds = hbox(self.secured, self.unsecured, spacing=8)
        self.keys = Keys(self.key)
        self.wifi = button("Wi-Fi: not read yet", self.toggle_wifi)
        self.idle = vbox(self.wifi, button("Other network", self.other_network))
        self.forget_b, self.cancel_b = button("Forget", self.forget, "chip"), button("Cancel", self.cancel, "chip")
        self.chips = hbox(self.forget_b, self.cancel_b, spacing=8)
        self.main = button("Scan again", self.main_action, "main")
        act = column(self.pick, self.why, bottom=(self.idle, self.boxes["name"], self.boxes["password"], self.kinds,
                                                  self.typed, self.keys, self.chips, bar(self.home(), self.main)))
        self.paint_act()
        return read, act

    # --- what shows ---------------------------------------------------------------------------------------------
    def visible(self):
        return self.shell.page == self.name

    def mode(self):
        return "other" if self.other else netlogic.choice(self.chosen)

    def ssid(self):
        return (self.chosen or {}).get("ssid")

    def paint_act(self):
        m, row = self.mode(), self.chosen or {}
        self.pick.set_label("Other network" if m == "other" else row.get("ssid") or "")
        self.pick.set_visible(m != "idle")
        why = (netlogic.why_not(row) if m == "unsupported" else netlogic.OPEN_NOTE if m == "open" else
               "Saved on this phone, not in range now." if row.get("visible") is False else "")
        self.why.set_label(why)
        self.why.set_visible(bool(why))
        self.idle.set_visible(m == "idle")
        for w in (self.boxes["name"], self.boxes["password"], self.kinds):
            w.set_visible(m == "other")
        self.typed.set_visible(m == "password")
        self.keys.set_visible(m in ("password", "other"))
        self.chips.set_visible(m != "idle")
        self.forget_b.set_visible(m in ("known", "connected") and bool(row.get("known") or row.get("active")))
        self.forget_b.set_label("Forget this network?" if self.sure.armed == ("forget", self.ssid()) else "Forget")
        main = {"idle": "Scan again", "connected": "Disconnect"}.get(m, "Join")
        if m == "connected" and self.sure.armed == ("disconnect", self.ssid()):
            main = "Disconnect now?"
        self.main.set_label(main)
        self.shown("main", (main, m, self.ssid()))
        self.main.set_visible(m != "unsupported")
        self.main.set_sensitive(not self.busy)
        self.paint_wifi()
        self.paint_typed()

    def paint_wifi(self):
        on = netlogic.wifi_on(self.status_data)
        says = ("Wi-Fi: not known yet" if on is None else "Turn Wi-Fi on" if on is False else
                "Turn Wi-Fi off now?" if self.sure.armed == ("wifi-off",) else "Turn Wi-Fi off")
        self.wifi.set_label(says)
        self.shown("wifi", (says, self.mode() == "idle"))     # what it says, and whether it is there at all
        self.wifi.set_sensitive(on is not None and not self.busy)

    def paint_typed(self):
        shown = netlogic.mask(self.secret)
        self.typed.set_label(shown or " ")
        words = {"name": self.ssid_entry.text, "password": "not needed" if self.open_net else shown}
        for f, b in self.boxes.items():
            b.set_label("%s%s: %s" % ("> " if f == self.field else "", self.TITLES[f], words[f]))
        (self.unsecured.add_css_class if self.open_net else self.unsecured.remove_css_class)("on")
        (self.secured.remove_css_class if self.open_net else self.secured.add_css_class)("on")

    def paint_list(self):
        found = netlogic.rows(self.scan_data)
        fill(self.nets, [(r["ssid"], netlogic.detail(r), lambda r=r: self.select(r)) for r in found] or
             [("No networks found", "Tap to scan again", self.scan_row)])
        # which network is in each place and what a tap there would offer: a newer list that changes either holds row taps
        self.shown("rows", tuple((r["ssid"], netlogic.choice(r), r["visible"]) for r in found))

    # --- choosing and typing ------------------------------------------------------------------------------------
    @owners_tap
    def select(self, row):
        if self.holding("rows", netlogic.LIST_CHANGED):
            return      # the list changed under the finger: the tap was meant for what was in its place before
        self.drop_check()
        self.secret.clear()
        self.sure.keep_only(None)
        self.other, self.chosen = False, self.current(row)
        self.keys.numbers(False)
        self.say("")
        self.paint_act()

    @owners_tap
    def scan_row(self):
        """The only row of an empty list: scan again."""
        if not self.holding("rows", netlogic.LIST_CHANGED):
            self.scan(True)

    @owners_tap
    def other_network(self):
        self.drop_check()
        self.secret.clear()
        self.sure.keep_only(None)
        self.ssid_entry.text, self.open_net, self.other, self.chosen = "", False, True, None
        self.focus("name")
        self.paint_act()

    @owners_tap
    def set_open(self, open_net):
        self.open_net = bool(open_net)
        if self.open_net:
            self.secret.clear()
        self.focus("name" if self.open_net else self.field)

    @owners_tap
    def focus(self, field):
        self.field = "name" if self.open_net else field
        self.keys.numbers(False)
        self.paint_typed()

    @owners_tap
    def key(self, ch):
        m = self.mode()
        if m == "other" and self.field == "name":
            if ch == "\n":
                self.focus("password")
            elif self.ssid_entry.key(ch):
                if len(self.secret):
                    self.say(netlogic.NAME_CHANGED)     # it was typed for the name as it was before this key
                self.untype()
        elif m in ("other", "password"):
            if ch == "\n":
                self.join()
            elif self.busy:
                self.say(netlogic.KEYS_WAIT)    # while a request waits, password keys are not kept, and it says so
            elif self.secret.key(ch, self.typing_for()):
                self.paint_typed()

    def typing_for(self):
        """The network name a password key typed now is for: the name typed for a hidden network, else the chosen row's."""
        return self.ssid_entry.text if self.mode() == "other" else self.ssid()

    def untype(self):
        """Empty what was typed as a password, and show it empty."""
        self.secret.clear()
        self.paint_typed()

    @owners_tap
    def cancel(self):
        """Close the choice. Nothing is sent; the typed password is dropped."""
        self.drop_check()
        self.secret.clear()
        self.sure.keep_only(None)
        self.ssid_entry.text, self.other, self.chosen, self.open_net = "", False, None, False
        self.paint_act()

    # --- asking the core ----------------------------------------------------------------------------------------
    def send(self, action, fields, then):
        """One net_* action on its own thread; then(reply) runs on the window's thread. `fields` is emptied once the
        request has gone, and any text in the reply that holds what was typed is hidden (netlogic.scrub)."""
        core, wait, change = self.shell.core, self.WAIT.get(action, 20), action in self.CHANGES

        def work():
            typed = fields.get("password")
            try:
                got = core.net(action, fields, wait)
            except urllib.error.HTTPError as err:
                got = netlogic.refusal(err.code)    # a 4xx: refused before anything ran (netlogic.NOT_SENT)
            except Exception as err:        # never shown or logged as it is: it may quote the request
                got = netlogic.failed(err, change)
            finally:
                fields.clear()
            got = netlogic.scrub(got, typed)
            typed = None
            GLib.idle_add(self.answer, then, got)
        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception as err:            # no thread: nothing was sent; the answer is a plain failure, given now
            fields.clear()
            self.answer(then, dict(netlogic.failed(err, False), **{netlogic.NOT_SENT: True}))

    @staticmethod
    def answer(then, got):
        then(got if isinstance(got, dict) else {"ok": False})
        return False

    def now(self):
        return time.monotonic()

    def shown(self, which, what):
        """Button `which` now says and does `what` (its label, and for the main button the pane and the network it acts
        on). A change the owner's own tap made is theirs, and ends any hold; any other change starts one (hold())."""
        if self.seen.get(which, what) != what:
            if self.by_owner:
                self.held.pop(which, None)
            else:
                self.hold(which)
        self.seen[which] = what

    def hold(self, which):
        """Button `which` changed by itself, so a tap on it in the next HOLD_S seconds was meant for what it said before:
        it sends nothing and only says the button changed."""
        self.held[which] = self.now() + self.HOLD_S

    def holding(self, which, why=netlogic.CHANGED):
        if self.now() < self.held.get(which, float("-inf")):
            self.say(why)
            return True
        return False

    @owners_tap
    def main_action(self):
        if self.holding("main"):
            return
        m = self.mode()
        if m == "idle":
            self.scan(True)
        elif m == "connected":
            if not self.sure.tap(("disconnect", self.ssid())):
                self.paint_act()
                return
            # The core's disconnect names no network: it drops whatever is connected. So this tap sends nothing yet.
            # If the radios read drawn already names another network (or none is known), it stops here. Otherwise the
            # radios are read again, and the disconnect is sent only when that read, asked after this tap, still names
            # the network the prompt asked about (checked()): the read drawn may be up to POLL_MS old, and the phone may
            # have moved to another network between the two taps.
            known, now = netlogic.connected_to(self.status_data)
            if not known or now != self.ssid():
                self.say(netlogic.DISCONNECT_MOVED if known else netlogic.DISCONNECT_UNKNOWN)
                self.paint_act()
                return
            if self.busy:
                self.paint_act()
                return
            self.busy = True
            self.say(netlogic.checking(self.ssid()))
            self.paint_act()
            self.status(fresh=True, check=self.ssid())
        elif m != "unsupported":
            self.join()

    def join(self):
        if self.busy:
            return
        m = self.mode()
        if m == "other":
            if not self.ssid_entry.text:
                self.say(netlogic.NEED_NAME)
                return
            need = netlogic.needs_password({"secure": not self.open_net}, typed_hidden=True)
            fields = {"ssid": self.ssid_entry.text, "hidden": True}
        elif m in ("password", "known", "open"):
            need = netlogic.needs_password(self.chosen)
            fields = {"ssid": self.chosen["ssid"]}
        else:
            return
        if need and not self.secret.is_for(fields["ssid"]):
            self.untype()       # none typed, or typed for another name than the one this join would send: not sent
            self.say(netlogic.NEED_PW)
            return
        if need:
            fields["password"] = self.secret.reveal()
        self.busy = True
        self.secret.clear()     # handed to the sending thread below: from here on this page holds no typed password
        self.say("Joining...")
        self.paint_act()
        self.send("net_join", fields, self.reply_to(self.done))

    def reply_to(self, then):
        """then(got, visit): the visit is the one the request was sent in, so a reply can tell it is from an earlier one."""
        visit = self.visit
        return lambda got: then(got, visit)

    def stale(self, visit):
        return visit is not None and visit != self.visit

    def free(self):
        """The page is no longer busy. A scan this visit put off because it was busy runs now."""
        self.busy = False
        if self.scan_owed and self.visible():
            self.scan_owed = False
            self.scan(True)
            return True
        return False

    def done(self, got, visit=None):
        """The answer to a join, a forget or a Wi-Fi change. Every answer except a refusal before anything ran
        (netlogic.nothing_ran) sets the floor (set_floor): every older read still on its way is refused, and the radios
        read drawn before it is no longer trusted until a newer one is drawn. Whatever the answer, the radios and the list
        are read again (a failed join may still have changed them).
        For a change sent in this visit, the typed password is emptied whatever the answer. An answer to a change sent
        in an earlier visit says its outcome, sets the same floor in the same cases, frees the page (which runs a scan this
        visit put off) and, while it shows, reads the radios again; it leaves this visit's choice and typed password as they are."""
        ok, text = netlogic.friendly(got)
        # Any answer from the core to a change may follow a change of the radios: a confirmed one, one with no answer in
        # time on either side (NetworkManager keeps going after nmcli stops waiting), a failed join that dropped the old
        # network. Only a refusal before anything ran (netlogic.NOT_SENT: an HTTP 4xx, or nothing sent) leaves them as
        # they were, so only that sets no floor.
        floor = not netlogic.nothing_ran(got)
        self.sure.keep_only(None)
        self.say(text)
        if self.stale(visit):
            if floor:
                self.set_floor()
            self.paint_act()
            self.free()
            if self.visible():
                self.status(fresh=True)
            return False
        self.secret.clear()
        if ok:
            self.ssid_entry.text, self.other, self.chosen, self.open_net = "", False, None, False
        if floor:
            self.set_floor()
        self.paint_act()
        rescanned = self.free()
        if self.visible():
            self.status(fresh=True)
            if not rescanned:
                self.scan(False, quiet=True)
        return False

    def set_floor(self):
        """After a change: every read sent before now is refused when it comes, and the radios read drawn before now is no
        longer trusted. Until a read sent after this is drawn, the Wi-Fi state and the connected network are unknown, so
        the Wi-Fi button cannot switch and Disconnect sends nothing (connected_to(None) is unknown); the list stays drawn
        so the owner can see it, and each row is checked again when the newer reads come."""
        for seq in (self.radios, self.found):
            seq.accept(self.ask(seq), floor=True)
        self.status_data, self.status_n = None, 0
        self.lines.set_label(netlogic.REREAD)

    @owners_tap
    def forget(self):
        ssid = self.ssid()
        if not ssid:
            return
        if self.sure.tap(("forget", ssid)):
            self.act_on("net_forget", {"ssid": ssid}, "Forgetting...")
        else:
            self.paint_act()

    @owners_tap
    def toggle_wifi(self):
        if self.holding("wifi"):
            return
        on = netlogic.wifi_on(self.status_data)
        if on is None:
            self.say(netlogic.WIFI_UNKNOWN)     # never switch a radio whose state is not known
        elif on is False:
            self.act_on("net_wifi", {"mode": "on"}, "Turning Wi-Fi on...")
        elif self.sure.tap(("wifi-off",)):
            self.act_on("net_wifi", {"mode": "off"}, "Turning Wi-Fi off...")
        else:
            self.paint_act()

    def act_on(self, action, fields, doing):
        if self.busy:
            return
        self.busy = True
        self.say(doing)
        self.paint_act()
        self.send(action, fields, self.reply_to(self.done))

    def ask(self, seq):
        """The number of the next request in `seq` (the radios reads or the lists). The two share one count, so of a
        radios read and a list it is known which was asked later, whichever answer comes first."""
        seq.issued = max(self.radios.issued, self.found.issued)
        return seq.issue()

    def current(self, row):
        """`row` as the radios read drawn says it is, when that read was asked after the list drawn and says which
        network is connected: connected or not follows the one asked later. Otherwise `row` itself."""
        known, now = netlogic.connected_to(self.status_data)
        if row is None or not known or self.status_n <= self.scan_n or bool(row.get("active")) == (now == row.get("ssid")):
            return row
        return dict(row, active=now == row.get("ssid"))

    def status(self, fresh=False, then_scan=False, check=None):
        """Read the radios. `check` is the network a confirming Disconnect tap asked about: this read decides it."""
        n = self.ask(self.radios)
        self.polling = n
        if check:
            self.checking = (check, n)
        self.send("net_status", {"fresh": True} if fresh else {},
                  self.reply_to(lambda got, visit: self.statused(n, got, then_scan, visit)))

    def drop_check(self):
        """The choice is closed or changed by the owner while a confirming Disconnect tap's read waits: that tap's
        confirmation ends with it, so the read, when it comes, sends nothing."""
        if self.checking:
            self.checking = None
            self.say("")
            self.free()

    def checked(self, ssid, drawn):
        """The radios read a confirming Disconnect tap asked for has come (`drawn`: it was the newest and is the one now
        drawn). The disconnect is sent only if that read says `ssid` is the connected network, and the page still shows
        that network as the chosen, connected one. Whatever else it says, nothing is sent and the screen says why."""
        self.checking = None
        known, now = netlogic.connected_to(self.status_data) if drawn else (False, None)
        if known and now == ssid:
            if self.visible() and not self.other and self.ssid() == ssid and self.mode() == "connected":
                self.say(netlogic.disconnecting(ssid))
                self.send("net_wifi", {"mode": "disconnect"}, self.reply_to(self.done))     # the page stays busy
                return
            self.say(netlogic.DISCONNECT_CLOSED)
        else:
            self.say(netlogic.DISCONNECT_MOVED if known else netlogic.DISCONNECT_UNKNOWN)
        self.paint_act()
        self.free()

    def disarm_row(self):
        """Drop a Forget or Disconnect prompt: what it asked about is no longer what it was."""
        if self.sure.armed and self.sure.armed[0] in ("forget", "disconnect"):
            self.sure.keep_only(None)

    def rechoose(self, row):
        """The chosen row is now `row` (a newer reading of the same network, or None when it is gone). A prompt about
        it is dropped if anything about its state changed, and a typed password if the pane no longer asks for one."""
        old = self.chosen
        keys = ("active", "known", "visible", "supported", "secure")
        if row is None or any(row.get(k) != old.get(k) for k in keys):
            self.disarm_row()
        self.chosen = row
        if self.mode() != "password":
            self.secret.clear()

    def statused(self, n, got, then_scan=False, visit=None):
        if self.polling == n:
            self.polling = None
        if self.stale(visit):
            return False        # read for an earlier visit: this visit reads the radios itself
        drawn = self.radios.accept(n)
        if drawn:
            before = netlogic.connected_to(self.status_data)
            self.status_data, self.status_n = got, n
            self.lines.set_label("\n".join(netlogic.status_lines(got)) or "The radios could not be read.")
            if self.sure.armed == ("wifi-off",) and netlogic.wifi_on(got) is not True:
                self.sure.keep_only(None)
            known, now = netlogic.connected_to(got)
            if (known, now) != before:
                self.disarm_row()
            if self.chosen and not self.other:
                row = self.current(self.chosen)     # only when this read was asked after the list drawn
                if row is not self.chosen:
                    self.rechoose(row)
            self.paint_act()
        if self.checking and self.checking[1] == n:
            self.checked(self.checking[0], drawn)
        if then_scan and self.visible():
            if self.busy:
                self.scan_owed = True       # run when the page is free (free())
            else:
                self.scan(True)
        return False

    def scan(self, rescan, quiet=False):
        if not quiet:
            if self.busy:
                return
            self.busy = True
            self.say("Scanning..." if rescan else "")
            self.paint_act()
        n = self.ask(self.found)
        self.send("net_scan", {"rescan": bool(rescan)}, self.reply_to(lambda got, visit: self.scanned(n, got, quiet, visit)))

    def scanned(self, n, got, quiet=False, visit=None):
        if self.stale(visit):
            if not quiet:
                self.paint_act()
                self.free()     # the list of an earlier visit is not drawn; this visit scans for itself
            return False
        if not quiet:
            self.busy = False
        fresh = self.found.accept(n)
        if fresh and isinstance(got.get("networks"), list):
            self.scan_data, self.scan_n = got, n
            self.paint_list()
            if self.chosen and not self.other:
                # found again by name; connected or not as the radios read drawn says, when that was asked after this list
                self.rechoose(self.current(next((r for r in netlogic.rows(got) if r["ssid"] == self.ssid()), None)))
            if not quiet:
                self.say(got.get("message") if isinstance(got.get("message"), str) else "")
        elif not quiet and not isinstance(got.get("networks"), list):
            self.say(netlogic.friendly(got)[1])
        self.paint_act()
        if not quiet:
            self.free()
        return False

    # --- coming and going ---------------------------------------------------------------------------------------
    def enter(self):
        if not self.visible():
            return
        self.status(then_scan=True)
        if self.timer is None:
            self.timer = GLib.timeout_add(self.POLL_MS, self.poll)

    def poll(self):
        if not self.visible():
            self.timer = None
            return False
        if self.polling is None:
            self.status()
        return True

    @owners_tap
    def list_row(self):
        """The only row while this visit's first list has not come: it chooses nothing."""
        self.say(netlogic.LIST_WAIT)

    def leave(self):
        """Every reply still on its way now belongs to an earlier visit (see done, statused and scanned). What this visit
        read is forgotten as well: while the page is not showing, the phone can move to another network or the Wi-Fi be
        switched (the browser's /network page, NetworkManager roaming). So the next visit starts at the floor: the radios
        are unknown (Disconnect and the Wi-Fi button do nothing) until its own first radios read is drawn, and the list
        has one row that chooses nothing until its own first list is drawn (that list starts a row hold, as any newer
        list that changes a place does)."""
        self.visit += 1
        self.scan_owed = False
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.polling = None
        self.status_data, self.scan_data, self.status_n, self.scan_n = None, None, 0, 0
        self.lines.set_label(netlogic.REREAD)
        fill(self.nets, [(netlogic.LIST_REREAD, "", self.list_row)])
        self.shown("rows", ("reading",))
        self.cancel()


PAGES = (Lock, Home, Phone, Messages, Clock, Contacts, Notes, Calculator, Calls, Settings, Network)


def enabled_pages(env=None):
    """The pages this phone has. Clock, Contacts, Notes and Call log are the server's everyday apps: each is shown only
    when OPOS_APPS switches it on (opos.apps, which loads it as the server does); absent or empty means none of them.
    Every other page needs nothing from that list."""
    on = {a.id for a in apps.enabled(env)}
    return tuple(c for c in PAGES if c.api is None or c.api in on)
