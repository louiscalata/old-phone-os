# SPDX-License-Identifier: GPL-3.0-or-later
"""The native OPOS shell: a GTK 4 window, drawn by the phone's GPU, in place of the browser kiosk.

One window covers both screens. Each screen has its own stack of panes, so nothing ever crosses the hinge. Every frame
is a complete, accurate snapshot and each tap is answered in one frame: nothing slides or fades (ui.ANIMATE is off; any
movement is a skin to add later). Gestures:

    lock screen   drag up anywhere                  the page follows the finger; unlocks past 40% or on a flick
    anywhere      drag down from the top edge       the battery sheet follows the finger; opens past 35% or on a flick
    battery sheet drag up anywhere                  the sheet follows the finger; closes past 35% or on a flick
    any app page  drag up from the bottom edge      the page follows the finger; Home past 30% or on a flick
    any app page  swipe in from an outer side edge  Home
    anywhere      swipe sideways across the hinge   move the buttons to that side (right- or left-handed)
    Home          press and hold a tile             look at that app on the other screen without opening it
    lid closed    the shell locks, and both screens go dark; opening the lid lights them as they were

A drag that follows the finger is direct manipulation, not animation: each frame draws the surface where the finger
holds it, and the frame after the finger lifts shows the result, complete (no easing, no coasting, no spring-back on a
timer). layout.drag_kind, drag_progress and drag_release decide; a drag is taken only after the finger has moved SLOP
pixels, mostly up or down, so a tap still reaches its button; a quick swipe the drag did not take is still read by
layout.swipe_action, and one touch never acts twice. The lid is read by posture.py; the screens are blanked by
blank.py, also when the port's lid daemon asks through displayconfig.py.

The battery rules, the modem, alarms, notes and storage stay in the OPOS server; this process only draws and asks.
GTK 4 and PyGObject are already part of the Droidian image, so nothing is installed for it.

    python3 -m opos.native.shell [--probe] [--shots FOLDER]

Exit status: 10 when the owner chose the old interface in Settings, 11 when the server has a PIN lock (this shell has no
PIN screen), 3 when the window could not be built; the session's launcher (packaging/duo-os/opos-native) starts the
browser on 10 and 11 and counts every other end.

--probe walks the main changes twice, prints the frame pacing of each and quits. --shots saves a picture of every
screen into FOLDER and quits. Both are for checking the shell, not for use.
"""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from opos.native import blank, displayconfig, logic, posture, screens  # noqa: E402
from opos.native.layout import (SLOP, drag_kind, drag_offset, drag_progress, drag_release, drag_velocity,  # noqa: E402
                                hinge_px, load_hand, pane_order, save_hand, swipe_action)
from opos.native.ui import ANIMATE, CSS, MOVE_MS, Slide, bar, button, column, label, reading, two_panes  # noqa: E402

BASE = os.environ.get("OPOS_LOCAL_URL", "http://127.0.0.1:8765")
POLL_S = 3.0
LEAVE = 10      # exit status when the owner chose the old interface; the launcher (opos-native) starts the browser
LOCKED = 11     # exit status when the server has a PIN lock: this shell has no PIN screen, so the browser must show it
BROKEN = 3      # exit status when the window could not be built
ASKED_TO_END = 128 + signal.SIGTERM   # SIGTERM: the launcher reads 143 as "asked to start again", as before
BLANK_MS = 350  # after the lid closes, the screens go dark this much later, once the lock screen is up
# While the screens may still be dark and the lid is not closed, a restore is tried again this long after the last try:
# 1 s, 2 s, 4 s, then every RESTORE_EVERY_MS, with no upper limit (one timer at a time, never a busy loop).
RESTORE_BACKOFF_MS = (1000, 2000, 4000)
RESTORE_EVERY_MS = 5000


class Core:
    """The running OPOS server, asked over loopback. One token read, then /api/state, /api and /api/<app>."""

    def __init__(self, base=BASE):
        self.base, self.token = base, None

    def _open(self, path, headers=None):
        return urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers or {}), timeout=5)

    def state(self):
        if self.token is None:
            page = self._open("/").read().decode("utf-8", "replace")
            self.token = re.search(r'name="opos-token" content="([^"]+)"', page).group(1)
        try:
            return json.load(self._open("/api/state", {"X-OPOS-Token": self.token}))
        except Exception:
            self.token = None   # a restarted server has a new token; read it again next time
            raise

    def lock_on(self):
        """True when the server enforces a PIN lock (its /api/lock answers); False when it has none (404 there)."""
        if self.token is None:
            self.state()
        try:
            self._open("/api/lock", {"X-OPOS-Token": self.token})
            return True
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return False
            raise

    def _send(self, path, data):
        if self.token is None:
            self.state()
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode(), method="POST", headers={
            "X-OPOS-Token": self.token, "Content-Type": "application/json", "Origin": self.base})
        return json.load(urllib.request.urlopen(req, timeout=10))

    def post(self, action, **fields):
        """One action for the server (call, hang_up, send, open). Returns the new state, as /api does."""
        return self._send("/api", dict(fields, action=action))

    def app(self, name, data=None):
        """One of the server's apps (clock, contacts, notes, calls): its snapshot, or the result of one action."""
        if data is not None:
            return self._send("/api/" + name, data)
        if self.token is None:
            self.state()
        return json.load(self._open("/api/" + name, {"X-OPOS-Token": self.token}))


class Shell(Gtk.ApplicationWindow):
    def __init__(self, app, probe=False):
        super().__init__(application=app, title="OPOS")
        self.core, self.snap, self.snap_seq, self.probe = Core(), {}, 0, probe
        self.hinge, self.hand = hinge_px(), load_hand()
        self.gaps, self.last_frame, self.start = [], None, None
        self.said, self.said_text, self.battery_line = 0, "", ""
        self.page, self.clock_absent, self.leaving = "lock", False, 0
        self.seq, self.link, self.ring = logic.Sequence(), logic.Link(), logic.RingGate()
        self.wake = threading.Event()       # set after an action: the poll then looks again at once
        self.warming, self.wanted = True, None
        self.ready = logic.Ready(self.say_ready)
        # the drag being followed: its kind (None when the touch is not one), whether it has been decided yet, where it
        # began, its (time, dy) samples, and `owned`, set once a drag takes the touch so the swipe does not act on it too
        self.drag, self.drag_decided, self.drag_start, self.samples, self.owned = None, False, None, [], False
        # the lid: None until the watcher reports (or after it stopped); posture is posture.posture() of it
        self.lid_closed, self.lid_watcher, self.posture = None, None, posture.posture(False)
        # both screens dark while the lid is closed; the lid daemon's request to blank is refused while the lid reads open
        self.blanker = blank.Blanker(may_blank=lambda: self.lid_closed is not False)
        self.restore_tries, self.restore_timer, self.restore_blanks = 0, None, 0

        classes = list(screens.enabled_pages())      # Clock, Contacts, Notes and Call log only when OPOS_APPS has them
        self.apps = [(c.name, c.title) for c in classes if c.title]
        T = Gtk.StackTransitionType
        self.reads = Gtk.Stack(transition_type=T.SLIDE_LEFT_RIGHT if ANIMATE else T.NONE,
                               transition_duration=MOVE_MS if ANIMATE else 0, vexpand=True)
        self.acts = Gtk.Stack(transition_type=T.SLIDE_LEFT_RIGHT if ANIMATE else T.NONE,
                              transition_duration=MOVE_MS if ANIMATE else 0)
        self.pages = {}
        for cls in classes:
            page = cls(self)
            self.pages[page.name] = page
            self.reads.add_named(page.read, page.name)
            self.acts.add_named(page.act, page.name)
        self.clock_absent = "clock" not in self.pages       # no Clock page: the poll does not ask for alarms

        # one quiet line above the reading pane, the same on every page: the time, and the battery or a short message
        self.s_time, self.s_right = label("", "strip", wrap=False), label("", "strip", wrap=False, x=1.0)
        self.s_right.set_hexpand(True)
        self.strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for w in (self.s_time, self.s_right):
            self.strip.append(w)
        for m, v in (("top", 10), ("start", 28), ("end", 28)):
            getattr(self.strip, "set_margin_" + m)(v)
        self.strip.set_opacity(0)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.append(self.strip)
        side.append(self.reads)
        self.rows = [two_panes(side, self.acts, self.hinge, self.hand), self.build_sheet()]
        # each draws its child moved by the finger during a drag; at rest the offset is 0 and nothing is different
        self.page_slide, self.sheet_slide = Slide(self.rows[0]), Slide(self.rows[1])

        self.sheet = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN if ANIMATE else Gtk.RevealerTransitionType.NONE,
                                  transition_duration=MOVE_MS if ANIMATE else 0)
        self.sheet.set_child(self.sheet_slide)
        self.sheet.set_can_target(False)
        overlay = Gtk.Overlay(child=self.page_slide)
        overlay.add_overlay(self.sheet)
        self.splash = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.splash.add_css_class("sheet")
        name = label("OPOS", "clock", x=0.5)
        name.set_vexpand(True)
        self.splash.append(name)
        overlay.add_overlay(self.splash)
        self.set_child(overlay)

        swipe = Gtk.GestureSwipe()
        swipe.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        swipe.connect("begin", self.swipe_begin)
        swipe.connect("swipe", self.swipe_end)
        self.add_controller(swipe)
        drag = Gtk.GestureDrag()    # follows the finger; it claims the touch only once drag_kind names a drag
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self.drag_begin)
        drag.connect("drag-update", self.drag_update)
        drag.connect("drag-end", self.drag_end)
        drag.connect("cancel", self.drag_cancel)
        self.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.key)
        self.add_controller(keys)
        self.connect("map", lambda *_: self.ready.mark("mapped"))

        if probe:   # the frame clock runs only while measuring; an idle shell must draw nothing
            self.add_tick_callback(self.tick)
        self.second()
        GLib.timeout_add(1000, self.second)
        threading.Thread(target=self.poll, daemon=True).start()

    def build_sheet(self):
        """The battery sheet: it covers both screens, so nothing under it shows through."""
        self.b_head, self.b_detail, self.b_events = label("", "head"), label("", "big"), label("", "dim")
        read = reading(self.b_head, self.b_detail, self.b_events)
        act = column(bottom=(label("Swipe up to close", "dim", x=0.5), bar(button("Close", lambda: self.set_shade(False), "main"))))
        sheet = two_panes(read, act, self.hinge, self.hand)
        sheet.add_css_class("sheet")
        return sheet

    # --- moving around -----------------------------------------------------------------------------------------
    def show_page(self, name, how=None):
        """Put both screens on the named page. Without ANIMATE it is one change, drawn in the next frame."""
        how = Gtk.StackTransitionType.NONE if how is None or not ANIMATE else how
        self.reads.set_visible_child_full(name, how)
        self.acts.set_visible_child_full(name, how)

    def go(self, name):
        """Change page, completely, now. While the start-up cover is still up the stacks are being walked, so the
        request waits and is made the moment the cover goes (self.page and the stacks never disagree)."""
        if name not in self.pages:
            return
        if self.warming:
            self.wanted = name
            return
        self.set_shade(False)
        if name == self.page:
            return
        how = None
        if ANIMATE:
            T = Gtk.StackTransitionType
            toward, back = (T.SLIDE_RIGHT, T.SLIDE_LEFT) if self.hand == "left" else (T.SLIDE_LEFT, T.SLIDE_RIGHT)
            how = T.SLIDE_UP if self.page == "lock" else T.SLIDE_DOWN if name == "lock" else back if name == "home" else toward
        self.page = name
        self.strip.set_opacity(0 if name == "lock" else 1)
        self.pages[name].enter()
        self.pages[name].tick()
        self.show_page(name, how)

    def warm(self):
        """Behind the start-up name, show every page once, so its buttons are built and its text is drawn before the
        owner ever opens it. Without this the first visit to each app stalls for about a tenth of a second. Nothing
        moves under the cover: every step is one change of page. self.page stays "lock" throughout."""
        names = [n for n in self.pages if n != "lock"] + ["home", "lock"]
        self.splash.set_opacity(0.99)   # the renderer skips whatever lies under a fully opaque cover; this keeps it drawing

        def step():
            if names:
                self.show_page(names.pop(0))
                return True
            self.show_page(self.page)
            self.warming = False
            self.splash.set_visible(False)
            self.ready.mark("warmed")
            if self.get_mapped():
                self.ready.mark("mapped")
            clock = self.get_frame_clock()
            if clock is not None:       # no frame clock, no frame: then the shell never says "ready" and the launcher acts
                handler = []

                def painted(c):
                    c.disconnect(handler[0])
                    self.ready.mark("painted")
                handler.append(clock.connect("after-paint", painted))
                self.queue_draw()
            if self.wanted:
                wanted, self.wanted = self.wanted, None
                self.go(wanted)
            return False
        GLib.timeout_add(220, step)     # long enough for each page's first drawing to finish
        return False

    def leave(self, status=LEAVE):
        """End with a status the session's launcher answers with the browser: LEAVE (the owner chose it) or LOCKED."""
        self.leaving = status
        self.get_application().quit()

    @staticmethod
    def say_ready(env=None):
        """Tell the launcher the window is up and usable: it ends a shell that has not said so in time (opos-native).
        Called only once the window is on screen, has drawn a frame and has finished warming up (logic.Ready)."""
        path = (os.environ if env is None else env).get("OPOS_NATIVE_READY")
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("ready" + chr(10))
        except OSError as err:
            print("opos-native: cannot write the ready file: %s" % err, file=sys.stderr, flush=True)
            return False
        return True

    def set_shade(self, open_):
        self.sheet.set_reveal_child(bool(open_))
        self.sheet.set_can_target(bool(open_))   # a closed sheet still spans the window; it must not take the taps

    def set_hand(self, hand, save=True):
        """Put the buttons under the named hand, now, and remember it."""
        if hand not in ("right", "left") or hand == self.hand:
            return
        self.hand = hand
        for row in self.rows:
            row.reorder_child_after(getattr(row, pane_order(hand)[0]), None)   # None = first; the other pane follows
        self.pages["settings"].paint_hand()
        self.pages["home"].show(self.snap)
        if save:
            save_hand(hand)

    def say(self, text):
        """A short message in the top line for a few seconds; then the battery line comes back."""
        self.said, self.said_text = time.monotonic() + 4, text
        self.paint_strip()

    def paint_strip(self):
        """The top line: the lost connection first, then a short message, then the battery."""
        lost = self.link.text()
        text = lost or (self.said_text if self.said and time.monotonic() < self.said else self.battery_line)
        self.s_right.set_label(text)
        self.pages["lock"].lost(lost)

    def ask(self, action, done=None, **fields):
        """Send one action to the server without blocking the window; its reply is the new state. The reply changes
        the screen only if no newer one has; done(reply) is called either way, with None when nothing came back.
        Returns the request's number (compare it with snap_seq to tell whether its reply was the one drawn)."""
        n = self.seq.issue()

        def work():
            try:
                reply = self.core.post(action, **fields)
            except Exception:
                reply = None
            GLib.idle_add(self.answered, n, reply, done)
        threading.Thread(target=work, daemon=True).start()
        return n

    def answered(self, n, reply, done):
        if not logic.is_snapshot(reply):
            reply = None
            self.say("That did not go through.")
        elif self.seq.accept(n, floor=True):     # a poll sent before this reply came may be older than it: refused
            self.show(reply, n)
        if done:
            done(reply)
        self.wake.set()     # whatever this changed, look at the server again now rather than in up to POLL_S seconds
        return False

    def page_kind(self):
        return self.page if self.page in ("lock", "home") else "app"

    def swipe_begin(self, gesture, _sequence):
        self.owned = False      # a new touch; cleared here and in drag_begin, whichever runs first
        ok, x, y = gesture.get_point(None)
        self.start = (x, y) if ok else None

    def swipe_end(self, _gesture, vx, vy):
        if self.warming or self.owned:
            return      # the start-up cover is up, or a drag followed this touch and has already decided what it does
        act = swipe_action(self.page_kind(), self.sheet.get_reveal_child(), self.start, self.get_width(), self.get_height(), vx, vy, self.hinge)
        if act == "shade-open":
            self.set_shade(True)
        elif act == "shade-close":
            self.set_shade(False)
        elif act == "home":
            self.go("home")
        elif act in ("hand-right", "hand-left"):
            self.set_hand(act[5:])

    # --- drags that follow the finger (the rules are in layout.py) ----------------------------------------------
    @staticmethod
    def event_ms(gesture):
        return gesture.get_current_event_time() or GLib.get_monotonic_time() // 1000

    def drag_begin(self, gesture, x, y):
        self.owned, self.drag, self.drag_decided, self.drag_start = False, None, False, (x, y)
        self.samples = [(self.event_ms(gesture), 0.0)]

    def drag_update(self, gesture, dx, dy):
        if not self.drag_decided:
            if self.warming or (abs(dx) <= SLOP and abs(dy) <= SLOP):
                return          # not yet: this may still be a tap (or the start-up cover is up)
            self.drag_decided = True
            self.drag = drag_kind(self.page_kind(), self.sheet.get_reveal_child(), self.drag_start,
                                  self.get_width(), self.get_height(), dx, dy)
            if self.drag is None:
                return          # not a drag this shell follows: leave the touch to the buttons and the swipe
            self.owned = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)   # no button under the finger is pressed now
            if self.drag == "sheet-open":
                self.sheet_slide.offset(-self.get_height())
                self.sheet.set_reveal_child(True)
        if self.drag is None:
            return
        self.samples.append((self.event_ms(gesture), dy))
        if len(self.samples) > 64:
            del self.samples[:-32]
        self.dragged().offset(drag_offset(self.drag, dy, self.get_height()))

    def drag_end(self, gesture, _dx, dy):
        kind, self.drag = self.drag, None
        if kind is None:
            return
        self.samples.append((self.event_ms(gesture), dy))
        self.settle(kind, drag_release(kind, drag_progress(kind, dy, self.get_height()), drag_velocity(self.samples)))

    def drag_cancel(self, _gesture, _sequence):
        kind, self.drag = self.drag, None
        if kind is not None:
            self.settle(kind, "cancel")

    def dragged(self, kind=None):
        return self.sheet_slide if (kind or self.drag or "").startswith("sheet") else self.page_slide

    def settle(self, kind, outcome):
        """The finger let go (or the touch was taken away): the surface goes back to rest and the result is made, all
        in this one change, so the next frame is complete. Nothing moves on a timer afterwards."""
        self.dragged(kind).offset(0)
        if kind == "sheet-open":
            self.set_shade(outcome == "complete")
        elif kind == "sheet-close":
            self.set_shade(outcome != "complete")
        elif kind in ("lock", "home") and outcome == "complete":
            self.go("home")

    # --- the lid (posture.py reads it; blank.py darkens the screens) --------------------------------------------
    def start_lid(self):
        """Watch the lid; its reports come from the watcher's thread and are handled on this one. Also answer the lid
        daemon's PowerSaveMode on the session bus, and light screens a crash left dark, unless the lid is closed now.
        A missing or unreadable lid switch is said once; the shell then never blanks on its own."""
        def missing(reason):
            print("opos shell: no lid signal (%s); carrying on without it" % reason, file=sys.stderr, flush=True)
            GLib.idle_add(self.lid_lost)

        def say(line):
            print("opos shell: " + line, file=sys.stderr, flush=True)
        # The lid daemon's PowerSaveMode 0 goes through restore() too, so a restore that fails is tried again as any
        # other. GDBus calls set_property in the main context it was registered from, which is this (main) thread, so
        # it runs in order with the daemon's blanks and the lid's own changes.
        self.blanker.on_restore = self.restore
        displayconfig.own(self.blanker, say)
        if not self.lid_closed_now():
            self.restore()
        self.lid_watcher = posture.LidWatcher(lambda closed: GLib.idle_add(self.lid_changed, closed), missing).start()

    @staticmethod
    def lid_closed_now():
        """Whether the lid is known to be closed this moment, read once, read-only, as the watcher does. False when it
        cannot be read: a phone that cannot tell is treated as open, so it is never left dark."""
        path = posture.find_lid()
        if not path:
            return False
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            return False
        try:
            return posture.lid_state(fd) is True
        finally:
            os.close(fd)

    def lid_lost(self):
        """The watcher stopped: the lid is unknown again, so the shell no longer refuses the daemon's blank. Without a lid
        signal no "opened" will ever come, so the screens are lit now (and tried again until they are)."""
        self.lid_closed = None
        self.restore()
        return False

    def restore(self):
        """Light both screens as they were. While they may still be dark (the saved file is there, or a panel could not
        be written) and the lid is not closed, it is tried again: RESTORE_BACKOFF_MS, then every RESTORE_EVERY_MS, with
        no upper limit. A blank made after this call (the lid closed, the daemon asked while the lid is unknown) ends
        the tries: dark is then what was asked for, and the next open or restore starts them again."""
        self.restore_tries, self.restore_blanks = 0, self.blanker.blanks
        self.blanker.restore()
        self.retry_restore()

    def retry_restore(self):
        """Put up the next try, unless one is already waiting, nothing may be dark, or the lid is closed."""
        if self.restore_timer is not None or self.lid_closed or not self.blanker.pending():
            return
        n = self.restore_tries
        ms = RESTORE_BACKOFF_MS[n] if n < len(RESTORE_BACKOFF_MS) else RESTORE_EVERY_MS
        self.restore_timer = GLib.timeout_add(ms, self.restore_again)

    def restore_again(self):
        self.restore_timer = None
        if self.lid_closed or self.blanker.blanks != self.restore_blanks:
            return False        # closed again, or darkened on purpose since: dark is right
        self.restore_tries += 1
        self.blanker.restore()
        self.retry_restore()
        return False            # each try is its own one-shot timer, so the wait can grow

    def blank_if_closed(self):
        """BLANK_MS after the lid closed: blank both screens, unless it has opened again meanwhile."""
        if self.lid_closed:
            self.blanker.blank()
        return False

    def lid_changed(self, closed):
        """The lid closed: end any drag where it began, close the battery sheet, lock, and BLANK_MS later blank both
        screens. It opened: the lock screen is already showing, and both screens are lit again as they were (the first
        report, open, does that too: it is harmless when nothing was dark). Every change is one frame."""
        closed = bool(closed)
        self.lid_closed, self.posture = closed, posture.posture(closed)
        if closed:
            kind, self.drag = self.drag, None
            if kind is not None:
                self.owned = True           # the finger still down must not act when it lifts
                self.settle(kind, "cancel")
            self.set_shade(False)
            self.go("lock")
            GLib.timeout_add(BLANK_MS, self.blank_if_closed)
        else:
            self.restore()
        return False

    def end(self, status=ASKED_TO_END):
        """SIGTERM: light the screens and end, with the status the launcher reads as "asked to start again"."""
        self.leaving = status
        self.blanker.restore()
        app = self.get_application()
        if app is not None:
            app.quit()
        return False

    def key(self, _c, keyval, *_):
        if keyval == Gdk.KEY_Escape and not self.warming:
            self.go("home")
            return True
        return False

    # --- data --------------------------------------------------------------------------------------------------
    def poll(self):
        checked = False
        while True:
            n = self.seq.issue()
            try:
                snap = self.core.state()
            except Exception:  # the shell stays up when the server is restarting, and says so
                snap, checked = None, False
            if snap is not None and not checked:     # asked once, and again after the server went away: a restart may change it
                try:
                    checked = True
                    if self.core.lock_on():
                        GLib.idle_add(self.leave, LOCKED)
                except Exception:
                    checked = False
            GLib.idle_add(self.polled, n, snap)
            if not self.clock_absent:      # an alarm must be seen from any page
                clock = self.pages["clock"]
                m = clock.seq.issue()
                try:
                    GLib.idle_add(self.clock_seen, m, self.core.app("clock"))
                except urllib.error.HTTPError as err:
                    self.clock_absent = err.code == 404
                except Exception:
                    pass
            self.wake.wait(POLL_S)
            self.wake.clear()

    def polled(self, n, snap):
        """A poll answered (snap) or failed (None). A failure keeps what is on screen and says the connection is lost."""
        if not logic.is_snapshot(snap):
            self.link.failed()
            self.paint_strip()
        else:
            self.link.good()
            if self.seq.accept(n):
                self.show(snap, n)
            else:
                self.paint_strip()
        return False

    def clock_seen(self, n, data):
        clock = self.pages["clock"]
        clock.deliver(n, data)
        if isinstance(data, dict) and self.ring.new(data.get("ringing")):
            self.go("clock")        # once for each ringing alarm: the owner can leave the page and the alarm stays on it
        return False

    def clock_known(self):
        clock = self.pages.get("clock")
        return logic.clock_known(self.snap, clock.data if clock else None)

    def time_text(self, now=None):
        return logic.time_text(self.clock_known(), time.localtime() if now is None else now)

    def show(self, snap, n=0):
        if logic.needs_browser(snap):
            self.leave(LOCKED)
            return False
        self.snap, self.snap_seq = snap, n
        self.link.shown()
        b = snap.get("battery") or {}
        pct = "%s%%%s" % (b.get("percent", "--"), "  charging" if snap.get("charging") else "")
        self.b_head.set_label(pct)
        until, aim = b.get("calls_until"), b.get("goal")     # no "(goal )" when the server has none to say
        goal = "" if not until else "Calls and messages until %s%s." % (until, " (goal %s)" % aim if aim else "")
        self.b_detail.set_label("%s\n%s" % (b.get("time_left") or "", goal))
        self.b_events.set_label("\n".join((b.get("events") or [])[:6]))
        self.battery_line = "%s  %s" % (pct, b.get("headline") or "")
        self.paint_strip()
        for page in self.pages.values():
            page.show(snap)
        return False

    def second(self):
        self.s_time.set_label(self.time_text())
        if self.said and time.monotonic() >= self.said:
            self.said = 0
        self.paint_strip()
        self.pages[self.page].tick()
        return True

    # --- checking the shell ------------------------------------------------------------------------------------
    def tick(self, _w, clock):
        now = clock.get_frame_time() / 1000.0
        if self.last_frame is not None:
            self.gaps.append(now - self.last_frame)
        self.last_frame = now
        self.queue_draw()   # keep the frame clock running so every frame of a change is counted
        return True

    def measure(self, name, action, then, ms=500):
        self.gaps = []
        action()

        def done():
            g = sorted(self.gaps)
            n = len(g)
            if n:
                print("%-24s %3d frames in %d ms; median %.1f ms; worst %.1f ms; over 33 ms: %d"
                      % (name, n, ms, g[n // 2], g[-1], sum(1 for v in g if v > 33)), flush=True)
            then()
            return False
        GLib.timeout_add(ms, done)

    def run_probe(self):
        native = self.get_native()
        print("renderer: %s; window %dx%d; hinge %d" % (type(native.get_renderer()).__name__, self.get_width(), self.get_height(), self.hinge), flush=True)
        once = [("lock -> home", lambda: self.go("home")), ("sheet open", lambda: self.set_shade(True)),
                ("sheet close", lambda: self.set_shade(False)), ("home -> phone", lambda: self.go("phone")),
                ("phone -> home", lambda: self.go("home")), ("home -> messages", lambda: self.go("messages")),
                ("messages -> home", lambda: self.go("home")), ("home -> lock", lambda: self.go("lock"))]
        steps = once + [("again: " + n, a) for n, a in once]     # the second pass is what daily use feels like

        def nxt():
            if steps:
                name, action = steps.pop(0)
                GLib.timeout_add(300, lambda: self.measure(name, action, nxt) and False)
            else:
                self.get_application().quit()
        nxt()
        return False

    def touched(self, x, y):
        """What a tap at (x, y) lands on: the words of the button there, or the widget's type. For checking the shell."""
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and not isinstance(w, Gtk.Button):
            w = w.get_parent()
        if w is None:
            return None
        words, todo = [], [w]
        while todo:
            c = todo.pop()
            if isinstance(c, Gtk.Label):
                words.append(c.get_label())
            k = c.get_first_child()
            while k:
                todo.append(k)
                k = k.get_next_sibling()
        return " / ".join(reversed(words))

    def shot(self, path):
        """Save what the window shows now as a PNG, drawn by the same renderer."""
        paintable, snap = Gtk.WidgetPaintable.new(self), Gtk.Snapshot()
        paintable.snapshot(snap, self.get_width(), self.get_height())
        node = snap.to_node()
        if node is not None:
            self.get_native().get_renderer().render_texture(node, None).save_to_png(path)

    def run_shots(self, folder):
        steps = [("lock", lambda: None), ("home", lambda: self.go("home")), ("sheet", lambda: self.set_shade(True))]
        steps += [(a, lambda a=a: self.go(a)) for a, _n in self.apps]

        def nxt(prev=None):
            if prev:
                self.shot(os.path.join(folder, prev + ".png"))
            if not steps:
                self.get_application().quit()
                return False
            name, action = steps.pop(0)
            action()
            GLib.timeout_add(900, nxt, name)
            return False
        nxt()
        return False


def main(argv=None):
    argv = sys.argv if argv is None else argv
    probe = "--probe" in argv
    shots = argv[argv.index("--shots") + 1] if "--shots" in argv[:-1] else None
    # NON_UNIQUE: the shell must start with no session bus, and a second start must never just wake the first and end.
    app = Gtk.Application(application_id="org.opos.shell", flags=Gio.ApplicationFlags.NON_UNIQUE)
    made = []

    def activate(a):
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        try:
            win = Shell(a, probe)
        except Exception:
            # A half-built window still belongs to the application, which would then run for ever with nothing on the
            # screen. Take it down and end, so the launcher sees BROKEN and can start the browser.
            traceback.print_exc()
            for w in a.get_windows():
                w.destroy()
            a.quit()
            return
        made.append(win)
        # SIGTERM (an update, a new time zone, the session ending): light the screens first, then end as 143
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, win.end)
        win.fullscreen()
        win.present()
        GLib.timeout_add(200, win.warm)
        if probe:
            GLib.timeout_add(5000, win.run_probe)
        if shots:
            GLib.timeout_add(5000, win.run_shots, shots)
        if not probe and not shots:     # the checking modes must not depend on the lid or touch the screens
            win.start_lid()
    app.connect("activate", activate)
    try:
        status = app.run([])
    finally:
        for win in made:        # however the shell ends (the old interface, the PIN lock, an error), never leave it dark
            try:
                win.blanker.restore()
            except Exception:
                traceback.print_exc()
    if not made:
        return BROKEN       # activate raised before there was a window: say so, do not look like a clean end
    return made[0].leaving or status


if __name__ == "__main__":
    raise SystemExit(main())
