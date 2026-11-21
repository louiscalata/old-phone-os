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
    anywhere      swipe sideways across the hinge   move the buttons to that side (right- or left-handed); not in back mode
    Home          press and hold a tile             look at that app on the other screen without opening it
    lid closed    the shell locks, and both screens go dark; opening the lid lights them as they were
    folded back   back mode: the page on one screen, the other dark and deaf to touch (see below)

Back mode is the one thing the hinge angle changes. In posture "back" (folded all the way back) with the lid switch
reading open, the active screen (the one last touched, else the thumb side) shows the reading pane above the thumb pane,
and the other screen is darkened through the Blanker (blank(only=...)) and ignores touches that go down on it. Any other
posture, no hinge reading, the lid closing, the lid signal lost or the shell ending leaves it, and both screens are lit
again (the lid closing hands over to its own blank of both). The angle never locks, changes the page or the hand.

A drag that follows the finger is direct manipulation, not animation: each frame draws the surface where the finger
holds it, and the frame after the finger lifts shows the result, complete (no easing, no coasting, no spring-back on a
timer). layout.drag_kind, drag_progress and drag_release decide; a drag is taken only after the finger has moved SLOP
pixels, mostly up or down, so a tap still reaches its button; a quick swipe the drag did not take is still read by
layout.swipe_action, and one touch never acts twice. The lid is read by posture.py; the screens are blanked by
blank.py, also when the port's lid daemon asks through displayconfig.py.

The battery rules, the modem, alarms, notes and storage stay in the OPOS server; this process only draws and asks.
GTK 4 and PyGObject are already part of the Droidian image, so nothing is installed for it.

    python3 -m opos.native.shell [--probe] [--shots FOLDER]

Exit status: 10 when the owner chose the old interface in Settings, 11 when the server has a PIN lock or did not say
whether it has one (this shell has no PIN screen), 3 when the window could not be built; the session's launcher
(packaging/duo-os/opos-native) starts the browser on 10 and 11 and counts every other end.

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

from opos.native import blank, displayconfig, facing, hinge, logic, posture, screens  # noqa: E402
from opos.native.layout import (  # noqa: E402
    SLOP,
    active_screen,
    drag_kind,
    drag_offset,
    drag_progress,
    drag_release,
    drag_velocity,
    hinge_px,
    load_hand,
    pane_order,
    panel_to_darken,
    save_hand,
    screen_at,
    swipe_action,
    touch_ignored,
)
from opos.native.ui import (  # noqa: E402
    ANIMATE,
    CSS,
    MOVE_MS,
    Slide,
    bar,
    button,
    column,
    label,
    one_column,
    reading,
    two_columns,
    two_panes,
)

# The OPOS server, on loopback: OPOS_SERVER_URL when it is exactly http://127.0.0.1:PORT, else the phone's own port
# (logic.server_url; a refused value is said once in the log and never used). Never OPOS_LOCAL_URL: that is the Helper's
# model server, and reading it here sent the shell's token and the owner's actions to whatever that name pointed at.
BASE, BASE_NOTE = logic.server_url(os.environ)
if BASE_NOTE:
    print("opos shell: " + BASE_NOTE, file=sys.stderr, flush=True)
POLL_S = 3.0
LEAVE = 10      # exit status when the owner chose the old interface; the launcher (opos-native) starts the browser
LOCKED = 11     # exit status when the server has a PIN lock, or would not say: no PIN screen here, so the browser shows it
BROKEN = 3      # exit status when the window could not be built
ASKED_TO_END = 128 + signal.SIGTERM   # SIGTERM: the launcher reads 143 as "asked to start again", as before
BLANK_MS = 350  # after the lid closes, the screens go dark this much later, once the lock screen is up
# While the screens may still be dark and the lid is not closed, a restore is tried again this long after the last try:
# 1 s, 2 s, 4 s, then every RESTORE_EVERY_MS, with no upper limit (one timer at a time, never a busy loop).
RESTORE_BACKOFF_MS = (1000, 2000, 4000)
RESTORE_EVERY_MS = 5000
# A lid watcher that stopped is looked for again this many seconds later, then every LID_AGAIN_EVERY_S, with no upper
# limit (one timer at a time, on the seconds clock, which the system may group with other wake-ups).
LID_AGAIN_S = (5, 15, 30)
LID_AGAIN_EVERY_S = 60


class OnlyThisServer(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another page of the same server; one that names another address is an error."""

    def __init__(self, base):
        self.base = base

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not logic.same_server(self.base, newurl):
            err = urllib.error.HTTPError(req.full_url, code, "redirected away from the OPOS server; not followed", headers, fp)
            err.close()     # the refused answer is not read: its connection is closed here, not whenever the error is dropped
            raise err
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Core:
    """The running OPOS server, asked over loopback. One token read, then /api/state, /api and /api/<app>.

    Every request goes through this object's own opener: it uses no proxy, whatever http_proxy says (urllib would send
    a loopback request, token included, through one), and it does not follow a redirect to another address."""

    def __init__(self, base=BASE):
        self.base, self.token = base, None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), OnlyThisServer(base))

    def _open(self, path, headers=None):
        return self.opener.open(urllib.request.Request(self.base + path, headers=headers or {}), timeout=5)

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

    def _send(self, path, data, timeout=10):
        if self.token is None:
            self.state()
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode(), method="POST", headers={
            "X-OPOS-Token": self.token, "Content-Type": "application/json", "Origin": self.base})
        return json.load(self.opener.open(req, timeout=timeout))

    def post(self, action, **fields):
        """One action for the server (call, hang_up, send, open). Returns the new state, as /api does."""
        return self._send("/api", dict(fields, action=action))

    def net(self, action, fields, timeout):
        """One of the server's net_* actions (the Network screen), waiting up to `timeout` seconds: a join can keep the
        core busy far longer than the 10 s the other actions get. Returns the core's own reply, not the state."""
        return self._send("/api", dict(fields, action=action), timeout)

    def app(self, name, data=None):
        """One of the server's apps (clock, contacts, notes, convert, calls): its snapshot, or the result of one action."""
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
        self.angle, self.hinge_watcher = None, None     # the hinge angle in degrees (None: no reading) and its watcher
        # both screens dark while the lid is closed; the lid daemon's request to blank is refused while the lid reads open
        self.blanker = blank.Blanker(may_blank=lambda: self.lid_closed is not False)
        self.restore_tries, self.restore_timer, self.restore_blanks = 0, None, 0
        # the lid watcher: the number of the one listened to (a lost one is retired), how many looks for a new one
        # there have been since the last report, the timer of the next look, and whether the loss has been said
        self.lid_gen, self.lid_tries, self.lid_timer, self.lid_said = 0, 0, None, False
        # the PIN lock: what the server's answers add up to (logic.LockCheck), and whether the shell is held on its lock
        # page because a state came without an answer
        self.lock_check, self.held = logic.LockCheck(), False
        # back mode: None, or the screen in use (0 left, 1 right); the screen touched last; whether this touch is ignored
        self.back, self.last_screen, self.ignored, self.back_failed = None, None, False, False
        self.facing = facing.Facing()       # which screen faces the user, from the accelerometer (only back mode uses it)

        classes = list(screens.enabled_pages())      # Clock, Contacts, Notes, Converter, Call log: only when OPOS_APPS has them
        self.apps = self.tiles(classes)     # Network has no tile: Settings opens it
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

    @staticmethod
    def tiles(classes):
        """(name, title) of the pages with a tile on Home: those with a title, less any marked tile = False."""
        return [(c.name, c.title) for c in classes if c.title and getattr(c, "tile", True)]

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
        if self.held and name != "lock":
            return      # whether the server has a PIN lock is not known: nothing but the lock page (see hold)
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
        was, left = self.page, self.pages.get(self.page)
        self.page = name
        if left is not None:
            try:
                left.leave()    # before the new page's enter(): a page that polls stops, and drops what was typed
            except Exception as err:    # page code never blocks the change: the lid closing goes to "lock" this way
                print("opos shell: leaving %s raised %s; carrying on" % (was, type(err).__name__), file=sys.stderr, flush=True)
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
        for row in self.rows if self.back is None else ():     # in back mode the order is put right when it ends
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
        lost = self.link.text() or (logic.LOCK_UNKNOWN if self.held else "")
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
        try:
            threading.Thread(target=work, daemon=True).start()
        except RuntimeError:        # no thread could be started: nothing was sent, and the screen that asked is told
            GLib.idle_add(self.answered, n, None, done)
        return n

    def answered(self, n, reply, done):
        if not logic.is_snapshot(reply):
            reply = None
            self.say("That did not go through.")
        elif self.held:
            pass        # not drawn while the lock question is open; done() below still tells the screen that asked
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
        if touch_ignored(self.start, self.get_width(), self.hinge, self.back):
            return      # back mode: it went down on the dark screen
        act = swipe_action(self.page_kind(), self.sheet.get_reveal_child(), self.start, self.get_width(), self.get_height(), vx, vy, self.hinge, self.back)
        if self.back is not None and act in ("hand-right", "hand-left"):
            return      # one column: there is no hinge to push the buttons across
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
        self.ignored = touch_ignored((x, y), self.get_width(), self.hinge, self.back)
        if self.ignored:
            # back mode, on the dark screen: claimed here, in the capture phase, so no button under it is pressed, and
            # owned, so neither the drag nor the swipe acts on it. Only where a touch goes down counts.
            self.owned = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        seen = screen_at(x, self.get_width(), self.hinge)
        if seen is not None:
            self.last_screen = seen     # the screen back mode will use, if the phone is folded back next

    def drag_update(self, gesture, dx, dy):
        if self.ignored:
            return
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
        A missing or unreadable lid switch is said once; the shell then never blanks on its own, and looks for the
        switch again (lid_lost)."""
        def say(line):
            print("opos shell: " + line, file=sys.stderr, flush=True)
        # The lid daemon's PowerSaveMode 0 goes through restore() too, so a restore that fails is tried again as any
        # other. GDBus calls set_property in the main context it was registered from, which is this (main) thread, so
        # it runs in order with the daemon's blanks and the lid's own changes.
        self.blanker.on_restore = self.restore
        displayconfig.own(self.blanker, say)
        if not self.lid_closed_now():
            self.restore()
        self.watch_lid()
        try:
            self.hinge_watcher = hinge.HingeWatcher(lambda angle: GLib.idle_add(self.hinge_changed, angle),
                                                    on_tilt=lambda z: GLib.idle_add(self.tilt_changed, z,
                                                                                    time.monotonic())).start()
        except Exception as err:    # no hinge readings: two screens, as without the sensor service
            self.hinge_watcher = None
            print("opos shell: cannot start the hinge watcher (%s); carrying on without it" % type(err).__name__,
                  file=sys.stderr, flush=True)

    def watch_lid(self):
        """Start a lid watcher and listen to it alone. Each watcher has a number; its reports and its loss carry it, and
        anything from a watcher that is no longer the one listened to is dropped, so a late "closed" from a watcher
        that was given up can never lock or darken a phone nobody is watching the opening of. A watcher that cannot
        be started is a lost one."""
        self.lid_gen += 1
        gen = self.lid_gen
        try:
            self.lid_watcher = posture.LidWatcher(lambda closed: GLib.idle_add(self.lid_report, gen, closed),
                                                  lambda reason: GLib.idle_add(self.lid_gone, gen, reason)).start()
        except Exception as err:
            self.lid_gone(gen, "cannot start the lid watcher: %s" % type(err).__name__)

    def lid_report(self, gen, closed):
        """The watcher numbered gen read the lid. Its first report is the switch's state now (EVIOCGSW)."""
        if gen != self.lid_gen:
            return False
        self.lid_tries, self.lid_said = 0, False
        return self.lid_changed(closed)

    def lid_gone(self, gen, reason):
        """The watcher numbered gen ended (no device, or it cannot be read). Said once until a watcher reports again."""
        if gen != self.lid_gen:
            return False
        if not self.lid_said:
            self.lid_said = True
            print("opos shell: no lid signal (%s); carrying on without it and looking for it again" % reason,
                  file=sys.stderr, flush=True)
        return self.lid_lost()

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
        signal no "opened" would come, so the screens are lit now (and tried again until they are), the watcher is
        retired (nothing it says later is listened to), and a new one is started LID_AGAIN_S later, then every
        LID_AGAIN_EVERY_S. The new watcher's first report is the switch's state at that moment and is handled as any
        report: closed locks and darkens, open lights. Looking again never darkens anything by itself."""
        self.lid_closed = None
        self.lid_gen += 1
        watcher = getattr(self, "hinge_watcher", None)
        if watcher is not None and hasattr(watcher, "set_paused"):
            watcher.set_paused(False)       # the lid is unknown: readings carry on, so the posture stays known
        self.set_posture()
        self.restore()
        if self.lid_timer is None:
            n = self.lid_tries
            wait_s = LID_AGAIN_S[n] if n < len(LID_AGAIN_S) else LID_AGAIN_EVERY_S
            self.lid_timer = GLib.timeout_add_seconds(wait_s, self.lid_again)
        return False

    def lid_again(self):
        self.lid_timer = None
        self.lid_tries += 1
        self.watch_lid()
        return False            # a one-shot timer; the next loss puts up the next one

    def restore(self):
        """Light both screens as they were (in back mode: the active one, see relight). While they may still be dark (the saved file is there, or a panel could not
        be written) and the lid is not closed, it is tried again: RESTORE_BACKOFF_MS, then every RESTORE_EVERY_MS, with
        no upper limit. A blank made after this call (the lid closed, the daemon asked while the lid is unknown) ends
        the tries: dark is then what was asked for, and the next open or restore starts them again."""
        self.restore_tries, self.restore_blanks = 0, self.blanker.blanks
        self.relight()
        self.retry_restore()

    def relight(self):
        """Light what should be lit now: both screens, or in back mode the active one (the other is kept dark)."""
        if self.back is not None:
            try:
                self.blanker.blank(only=(panel_to_darken(self.back),))
                return
            except Exception as err:    # never left with one screen dark by a fault: light both instead
                print("opos shell: cannot darken one screen (%s); lighting both" % type(err).__name__, file=sys.stderr, flush=True)
        self.blanker.restore()

    def retry_restore(self):
        """Put up the next try, unless one is already waiting, nothing may be dark, or the lid is closed. In back mode one
        screen is dark on purpose (the saved file is there), so only a screen that could not be lit is tried again."""
        may_be_dark = self.blanker.failed if self.back is not None else self.blanker.pending()
        if self.restore_timer is not None or self.lid_closed or not may_be_dark:
            return
        n = self.restore_tries
        ms = RESTORE_BACKOFF_MS[n] if n < len(RESTORE_BACKOFF_MS) else RESTORE_EVERY_MS
        self.restore_timer = GLib.timeout_add(ms, self.restore_again)

    def restore_again(self):
        self.restore_timer = None
        if self.lid_closed or self.blanker.blanks != self.restore_blanks:
            return False        # closed again, or darkened on purpose since: dark is right
        self.restore_tries += 1
        self.relight()
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
        self.lid_closed = closed
        watcher = getattr(self, "hinge_watcher", None)
        if watcher is not None and hasattr(watcher, "set_paused"):
            watcher.set_paused(closed)       # a closed lid needs no hinge or accelerometer readings
        if closed:
            self.set_posture()      # leaves back mode, without lighting the dark screen: both go dark BLANK_MS later
            kind, self.drag = self.drag, None
            if kind is not None:
                self.owned = True           # the finger still down must not act when it lifts
                self.settle(kind, "cancel")
            self.set_shade(False)
            self.go("lock")
            GLib.timeout_add(BLANK_MS, self.blank_if_closed)
        else:
            self.restore()          # the lid's own restore first: both lit
            self.set_posture()      # then, still folded back, back mode again
        return False

    def set_posture(self):
        """The posture from the lid and the hinge angle now; a change is one line in the log. Back mode follows it."""
        was = self.posture
        self.posture = posture.posture(self.lid_closed, self.angle, was)
        if self.posture != was:
            print("opos shell: posture %s -> %s (hinge %s)" % (was, self.posture, self.angle), file=sys.stderr, flush=True)
        self.fit_back()
        page = self.pages.get("settings")
        if page is not None and hasattr(page, "paint_posture"):
            page.paint_posture()

    def fit_back(self):
        """Enter or leave back mode as the posture and the lid now say (posture.one_screen). One change, one frame."""
        want = posture.one_screen(self.posture, self.lid_closed)
        if not want:
            self.back_failed = False
        if want and self.back is None and not self.back_failed:
            self.enter_back()
        elif not want and self.back is not None:
            self.leave_back()

    def enter_back(self):
        """Folded back with the lid open: the page in one column on the active screen, the other screen dark. If the
        panes cannot be moved, back mode is not entered: both screens stay lit, in two panes, and the shell carries on."""
        side = active_screen(self.last_screen, self.hand, self.facing.current)
        try:
            for row in self.rows:
                one_column(row, side)
        except Exception as err:
            print("opos shell: cannot use one screen (%s); staying on two" % type(err).__name__, file=sys.stderr, flush=True)
            self.back_failed = True     # not tried again until the posture leaves back (every hinge reading would retry)
            self.two_rows()
            self.restore()
            return
        self.back = side
        print("opos shell: back mode on screen %d" % side, file=sys.stderr, flush=True)
        self.restore()              # relight() in back mode: the other panel dark, this one lit, retried if it fails

    def leave_back(self):
        """Back mode ends: the two panes as before, and both screens lit (unless the lid is closed: its blank follows)."""
        self.back = None
        self.two_rows()
        print("opos shell: back mode off", file=sys.stderr, flush=True)
        if not self.lid_closed:
            self.restore()

    def two_rows(self):
        for row in getattr(self, "rows", ()):
            try:
                two_columns(row, self.hand)
            except Exception as err:    # never stops the restore that follows
                print("opos shell: cannot put the panes back (%s)" % type(err).__name__, file=sys.stderr, flush=True)

    def tilt_changed(self, z, sampled_at=None):
        """A new accelerometer reading (None: lost), taken at sampled_at (time.monotonic() in the watcher thread; now
        when not given). It only chooses the screen back mode uses: when it says the other screen faces the user (held
        for a moment, see facing.py), back mode moves to that screen."""
        if z is None:
            self.facing.reset()
            return False
        # the time the watcher thread took the reading, not when this thread got round to it: a stalled main loop
        # must not make readings taken a second apart look simultaneous, or the other way round
        self.facing.update(z, time.monotonic() if sampled_at is None else sampled_at)
        if self.back is not None and self.facing.current:
            want = active_screen(self.last_screen, self.hand, self.facing.current)
            if want != self.back:
                self.back = None
                self.two_rows()
                self.enter_back()
        return False

    def hinge_changed(self, angle):
        """A new hinge reading (None: the sensor service went away). It names the posture, and so back mode; it never
        locks, changes the page or the hand, and never darkens both screens (that is the lid's)."""
        self.angle = angle
        self.set_posture()
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
        lock = None
        while True:
            lock = self.poll_once(lock)
            self.wake.wait(POLL_S)
            self.wake.clear()

    def poll_once(self, lock):
        """One look at the server, from the polling thread. `lock` is what is known of its PIN lock: True, False, or None
        for not known. It is asked beside the first state, again while it has no answer, and again after the server
        went away (a restart may change it). A check that fails or says anything but yes or no stays None: it is never
        read as "no lock". The state and that answer reach the window together. Returns the answer for the next look."""
        n = self.seq.issue()
        try:
            snap = self.core.state()
        except Exception:  # the shell stays up when the server is restarting, and says so
            snap, lock = None, None
        if snap is not None and lock is None:
            try:
                said = self.core.lock_on()
                lock = said if said is True or said is False else None
            except Exception:
                lock = None
        GLib.idle_add(self.polled, n, snap, lock)
        if not self.clock_absent:      # an alarm must be seen from any page
            clock = self.pages["clock"]
            m = clock.seq.issue()
            try:
                GLib.idle_add(self.clock_seen, m, self.core.app("clock"))
            except urllib.error.HTTPError as err:
                self.clock_absent = err.code == 404
            except Exception:
                pass
        return lock

    def polled(self, n, snap, lock):
        """A poll answered (snap) or failed (None), with the server's answer about its PIN lock (True, False, or None
        for none). A failure keeps what is on screen and says the connection is lost. A state is drawn only with the
        answer "no lock"; "yes" ends the shell for the browser, and no answer holds the shell (logic.LockCheck)."""
        if lock is True:
            self.leave(LOCKED)
            return False
        if not logic.is_snapshot(snap):
            self.link.failed()
            self.paint_strip()
            return False
        self.link.good()
        verdict = self.lock_check.answer(lock)
        if verdict == "browser":
            print("opos shell: the server did not say whether it has a PIN lock, %d times in a row; ending so the browser"
                  " interface shows what it enforces" % logic.LockCheck.LIMIT, file=sys.stderr, flush=True)
            self.leave(LOCKED)
        elif verdict == "hold":
            self.hold()
        else:
            self.held = False
            if self.seq.accept(n):
                self.show(snap, n)
            else:
                self.paint_strip()
        return False

    def hold(self):
        """A state came, but the server did not say whether it has a PIN lock. That is never taken as "no lock": the
        state is not drawn, any drag ends where it began, and the shell goes to its own lock page and stays there
        (go refuses every other page) until the answer comes. The lock page says why."""
        if not self.held:
            self.held = True
            print("opos shell: the server did not say whether it has a PIN lock; staying on the lock page until it does",
                  file=sys.stderr, flush=True)
        kind, self.drag = self.drag, None
        if kind is not None:
            self.owned = True           # the finger still down must not act when it lifts
            self.settle(kind, "cancel")
        self.set_shade(False)
        self.go("lock")
        self.paint_strip()

    def clock_seen(self, n, data):
        clock = self.pages["clock"]
        clock.deliver(n, data)
        if self.held:
            return False        # the alarm comes forward once the shell is free: the ring is not marked as seen here
        if isinstance(data, dict) and self.ring.new(data.get("ringing")):
            self.go("clock")        # once for each ringing alarm: the owner can leave the page and the alarm stays on it
        return False

    def clock_known(self):
        clock = self.pages.get("clock")
        return logic.clock_known(self.snap, clock.data if clock else None)

    def apply_zone(self, zone):
        """Use a server-confirmed zone in the native process as well as the server."""
        from opos import timezone

        try:
            timezone.apply(zone)
        except (OSError, ValueError) as err:
            print(f"opos shell: cannot apply the time zone ({type(err).__name__})", file=sys.stderr, flush=True)
            return False
        self.s_time.set_label(self.time_text())
        return True

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
