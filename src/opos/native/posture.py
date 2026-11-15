# SPDX-License-Identifier: GPL-3.0-or-later
"""How the phone is held, as far as Linux can tell. No toolkit: the shell posts what this reports to its own thread.

On the Surface Duo 1 running Droidian the kernel shows one posture signal: a switch input device named exactly
"Surface Duo Lid" (SW_LID), whose node /dev/input/eventN the user may read through the android_input group. The
"Surface Duo Lid Wakeup" device next to it is a key device, not the lid. No hinge angle reaches Linux as an input device:
it comes from the phone's sensor service (hinge.py). With an angle posture() tells book, flat, tent and back apart;
without one (the service is not running) it says only "closed" or "open".

What the device dossier records about the hardware (stock Android 12L read on 2026-10-31, read-only; Notion "Surface Duo
Dossier for OPOS", section 04): the hinge turns 360 degrees and has no angle encoder. A sensor core (SLPI) computes the
angle from an accelerometer, gyroscope and magnetometer in each half, and two hall switches mark "closed" and "fully
folded back"; Android reports the result as hinge-angle sensor type 36, 0 to 360 degrees, sent on change. The two panels
are 1350 x 1800 pixels each with an 84-pixel hinge column between them (2784 x 1800), which is the 42 px gap of
OPOS_SPAN=675,42 at scale 2. Android's own folding state has only two values, HALF_OPENED and FLAT (folding_state()).
Two things in the dossier are still hypotheses: how the angle is measured, and that a hall switch is the lid input. The
lid switch here does work; the angle was read on one phone on 2026-11-14 (peer-measured by the PC session, not
reproduced from the Mac) and followed a fold from 0 to about 180.

The device is only ever opened read-only and non-blocking; it is never grabbed and nothing is ever written to it.
"""
from __future__ import annotations

import os
import select
import struct
import sys
import threading

EV_SYN, SYN_REPORT, SYN_DROPPED = 0, 0, 3
EV_SW = 5
SW_LID = 0
DROPPED = "dropped"                         # in lid_items: events were lost; read the switch again with EVIOCGSW
LID_NAME = "Surface Duo Lid"
EVENT_FORMAT = "@llHHi"                     # struct input_event: time (two longs), type, code, value
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)  # 24 on aarch64
SW_BYTES = 8                                # the switch bitmap read by EVIOCGSW; SW_MAX is 0x10, so 8 bytes is plenty
EVIOCGSW_8 = (2 << 30) | (SW_BYTES << 16) | (ord("E") << 8) | 0x1b
POLL_S = 1.0


def find_lid(sys_input="/sys/class/input"):
    """The /dev/input/eventN node of the input device named exactly "Surface Duo Lid", or None when there is none."""
    try:
        entries = os.listdir(sys_input)
    except OSError:
        return None

    def number(name, prefix):
        tail = name[len(prefix):]
        return int(tail) if name.startswith(prefix) and tail.isdigit() else None

    for entry in sorted((e for e in entries if number(e, "input") is not None), key=lambda e: number(e, "input")):
        folder = os.path.join(sys_input, entry)
        try:
            with open(os.path.join(folder, "name"), encoding="utf-8", errors="replace") as f:
                name = f.read().rstrip("\n")
        except OSError:
            continue
        if name != LID_NAME:
            continue
        try:
            events = sorted((c for c in os.listdir(folder) if number(c, "event") is not None), key=lambda c: number(c, "event"))
        except OSError:
            continue
        if events:
            return "/dev/input/" + events[0]
    return None


def split_events(data):
    """(the whole input_event records in data, the bytes of a partial record left at its end)."""
    cut = len(data) - len(data) % EVENT_SIZE
    return bytes(data[:cut]), bytes(data[cut:])


def lid_changes(data):
    """Every lid event in data, in order: True when the lid closed, False when it opened. Other events are skipped,
    and so is a partial record at the end (split_events keeps it for the next read)."""
    whole, _rest = split_events(data)
    return [value != 0 for _s, _us, kind, code, value in struct.iter_unpack(EVENT_FORMAT, whole)
            if kind == EV_SW and code == SW_LID]


def lid_items(data):
    """lid_changes, plus DROPPED where the kernel says its queue overflowed (SYN_DROPPED). The events after a
    SYN_DROPPED, up to and including the next SYN_REPORT, are incomplete and skipped (as the evdev documentation says);
    DROPPED stands at that SYN_REPORT, or at the end of data when none came yet, so the reader asks the kernel for the
    switch's state there instead."""
    whole, _rest = split_events(data)
    out, dropping = [], False
    for _s, _us, kind, code, value in struct.iter_unpack(EVENT_FORMAT, whole):
        if kind == EV_SYN and code == SYN_DROPPED:
            dropping = True
        elif dropping:
            if kind == EV_SYN and code == SYN_REPORT:
                dropping = False
                out.append(DROPPED)
        elif kind == EV_SW and code == SW_LID:
            out.append(value != 0)
    if dropping:
        out.append(DROPPED)
    return out


def lid_state(fd):
    """Whether the lid is closed now, read with the EVIOCGSW ioctl (which does not grab the device); None if that fails."""
    try:
        import fcntl     # here, not at the top: the module must import on Windows, which has no fcntl
        buf = bytearray(SW_BYTES)
        fcntl.ioctl(fd, EVIOCGSW_8, buf, True)
    except (ImportError, OSError, ValueError, TypeError):
        return None
    return bool(buf[SW_LID // 8] & (1 << (SW_LID % 8)))


PANEL_WIDTH_PX, PANEL_HEIGHT_PX, HINGE_COLUMN_PX = 1350, 1800, 84      # from the dossier, section 05
HINGE_RANGE = (0, 360)      # degrees the hinge angle sensor reports (Android sensor type 36)
# Hinge angle in degrees, as the phone's sensor service reports it: 0 closed, about 180 flat, 360 folded back on itself.
# Each zone is held until the angle leaves it by HYST degrees, so a hand that wobbles on a border never flickers.
ZONES = (("closed", 0, 15), ("book", 15, 170), ("flat", 170, 190), ("tent", 190, 345), ("back", 345, 361))
HYST = 6


def classify(angle, previous=None):
    """The posture a hinge angle means: closed, book (the two screens like an open book), flat, tent (folded the other
    way, standing on its edges) or back (one screen behind the other). previous, a name this returned, is kept while the
    angle is within HYST degrees of its zone. A reading outside 0..360 or not a number says nothing: None."""
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or angle != angle or angle < 0 or angle > 360:
        return None
    for name, low, high in ZONES:
        if name == previous:
            if low - HYST <= angle < high + HYST:
                return name
            break
    for name, low, high in ZONES:
        if low <= angle < high:
            return name
    return None


FOLDING_STATE = {"book": "HALF_OPENED", "tent": "HALF_OPENED", "flat": "FLAT"}


def folding_state(name):
    """The Android window-layout folding state a posture name stands for: "FLAT" or "HALF_OPENED" (the only two Android
    has, per the dossier), or None for closed, back and open: Android reports no hinge feature for those."""
    return FOLDING_STATE.get(name)


def describe(name, angle, one=True):
    """One line for Settings: the posture and the hinge angle, or that the sensor is not giving one. Never raises.
    one says whether the shell is in back mode now; folded back without it (the lid unknown, or the panes could not
    move) both screens are lit, and the line says so."""
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or angle != angle or not 0 <= angle <= 360:
        return "No hinge reading: the sensor service is not running, so the posture is not known."
    shown = "%s, hinge at %d degrees." % ((name or "open").capitalize(), round(angle))
    if name == "back":
        return shown + (" One screen is lit; the other is dark." if one else " Both screens are lit.")
    return shown


def one_screen(name, lid_closed):
    """Whether the shell uses one screen only (back mode): the posture is "back" and the lid switch reads open. An
    unknown lid (None: no watcher, or it was lost) is not open, so the phone then always uses both screens."""
    return name == "back" and lid_closed is False


def posture(lid_closed, angle=None, previous=None):
    """The posture name: "closed" when the lid switch says so, otherwise what the hinge angle says (classify) or "open" when
    there is no angle, or the angle alone says closed (the sensor service is not running, or it gave a reading that makes no sense). previous is the
    name this returned last time, for classify's hysteresis."""
    if lid_closed:
        return "closed"
    name = classify(angle, previous)
    # "closed" is the lid switch's to say: a hinge reading near 0 with the lid open (a sensor that has not reported yet,
    # a lid that has not tripped its switch) is only "open"
    return "open" if name in (None, "closed") else name


def _readable(fd, timeout):
    """Whether fd has something to read within timeout seconds."""
    ready, _w, _x = select.select([fd], [], [], timeout)
    return bool(ready)


def _say(line):
    print("opos lid: " + line, file=sys.stderr, flush=True)


class LidWatcher:
    """Watches the lid in a daemon thread. on_change(closed) is called once at the start with the state then (read with
    EVIOCGSW, so a watcher started again always reports the state now), and after that on every change (never for a
    repeat of the same state); when the kernel says events were dropped (SYN_DROPPED) the lid items at and after the drop
    in that read are ignored and the state is read again with EVIOCGSW (see _take), so a lost "opened" cannot leave the
    screens dark. on_missing(reason) is called once if the device is absent or cannot be read (also when that re-read
    fails), and the thread then ends. Both are called from the watcher's thread."""

    def __init__(self, on_change, on_missing, path=None, sys_input="/sys/class/input", poll_s=POLL_S):
        self.on_change, self.on_missing = on_change, on_missing
        self.path, self.sys_input, self.poll_s = path, sys_input, poll_s
        self.closed = None          # the last state reported; None until one is known
        self._stop = threading.Event()
        self._missing_told = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="opos-lid", daemon=True)
        self.thread.start()
        return self

    def stop(self, wait=True):
        """End the thread; it notices within poll_s seconds."""
        self._stop.set()
        if wait and self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(self.poll_s + 1.0)

    def _call(self, fn, *args):
        try:
            fn(*args)
        except Exception as err:   # a callback's failure must not end the watcher with a traceback
            _say("a lid callback failed: %r" % (err,))

    def _missing(self, reason):
        if not self._missing_told:
            self._missing_told = True
            self._call(self.on_missing, reason)

    def _report(self, closed):
        if closed is None or closed == self.closed:
            return
        self.closed = closed
        self._call(self.on_change, closed)

    def _run(self):
        try:
            path = self.path or find_lid(self.sys_input)
            if not path:
                self._missing('no input device named "%s"' % LID_NAME)
                return
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                fd = os.open(path, flags)
            except OSError as err:
                self._missing("cannot open %s: %s" % (path, err.strerror or err))
                return
            try:
                self._watch(fd, path)
            finally:
                os.close(fd)
        except Exception as err:    # never raise out of the thread
            self._missing("the lid watcher stopped: %r" % (err,))

    def _watch(self, fd, path):
        # A failed first read is a lost switch, as a failed re-read after a drop is (_take): said once, and the watcher
        # ends; the shell then treats the lid as unknown and lights the screens. It never waits silently.
        state = lid_state(fd)
        if state is None:
            self._missing("cannot read the lid switch")
            return
        self._report(state)
        rest = b""
        while not self._stop.is_set():
            try:
                if not _readable(fd, self.poll_s):
                    continue
                data = os.read(fd, EVENT_SIZE * 16)
            except BlockingIOError:
                continue
            except OSError as err:
                self._missing("cannot read %s: %s" % (path, err.strerror or err))
                return
            if not data:            # nothing came (an input device does not do this; a plain file at its end does)
                self._stop.wait(self.poll_s)
                continue
            whole, rest = split_events(rest + data)
            if not self._take(fd, lid_items(whole)):
                return

    def _take(self, fd, items):
        """Report one read's lid items. Returns False when the watcher must end (the switch can no longer be read).

        Without a DROPPED every item is reported in order. With one, the items before the first DROPPED are reported,
        and every item at or after it is ignored: the switch is read once with EVIOCGSW instead, after the whole read.
        That read is newer than anything in the batch, and Linux's EVIOCGSW also flushes the client's queued switch
        events, so replaying an item read before it (a "closed" from an earlier SYN_REPORT, say) would put back a state
        the kernel has already moved past and could blank an open phone. If that read fails, the switch is lost: the
        watcher says so (on_missing; the shell then treats the lid as unknown and lights the screens) and ends, so a
        stale "closed" can never stay in force with no way to learn of the opening."""
        if DROPPED not in items:
            for item in items:
                self._report(item)
            return True
        for item in items[:items.index(DROPPED)]:
            self._report(item)
        state = lid_state(fd)
        if state is None:
            self._missing("cannot read the lid switch again after the kernel dropped its events")
            return False
        self._report(state)
        return True
