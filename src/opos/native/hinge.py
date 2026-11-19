# SPDX-License-Identifier: GPL-3.0-or-later
"""The hinge angle of the Surface Duo 1, read from the phone's sensor service (sensorfw, D-Bus name
com.nokia.SensorService on the system bus). Linux has no input device for it; the service reads Android's hinge sensor
through the HAL. This module only ever reads: it asks the service for a hinge session, starts it, and asks for the angle
a few times a second. It never starts, stops or configures the service process. When the service is not running the watcher
says so once and looks again every RETRY_S seconds; nothing else in the shell depends on it.

The watcher can also read the accelerometer (which screen faces the user, see facing.py) through a second session on
the same bus, with its own data socket. That part is optional: when it cannot be had the hinge angle is unaffected and
only on_tilt(None) is said.

SensorBus is the one part that needs the toolkit (Gio); HingeWatcher takes any object with the same five methods (and,
for the accelerometer, open_accel, tilt and close_accel), so it is tested with a fake.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time

NAME = "com.nokia.SensorService"
MANAGER = "/SensorManager"
SENSOR = "hingesensor"
SOCKET = "/var/run/sensord.sock"  # every session must also connect here, or the service drops it after about 10 s
POLL_S = 0.25
RETRY_S = 5.0
SETTLE_S = 0.8  # a started sensor reads 0 until its first real report; the first reading waits this long
CALL_TIMEOUT_MS = 2000
MIN_STEP = 2  # degrees: a smaller change is not reported
ACCEL = "accelerometersensor"  # in the RIGHT half; z is out of that half's screen, in 1/1024 g
TILT_STEP = 8  # 1/1024 g: a smaller change of z is not reported
TILT_MIN_S = 0.2  # at most five tilt reports a second
TILT_HEARTBEAT_S = (
    0.5  # but an unchanged reading is still passed on this often: facing.py needs a verdict repeated over time
)
# Battery: a shell that polls two sensors four times a second costs about six times the idle CPU of one that does not
# (peer-measured by the PC session on one phone, 2026-11-15, not reproduced from the Mac: 84 against 13 ticks per
# 30 s). So the watcher polls fast only while it matters: the hinge is
# moving, or it is at FAST_FROM degrees or more (tent, back: where the accelerometer chooses the screen). Otherwise it
# looks once every SLOW_POLL_S, and reads the accelerometer once every SLOW_TILT_S, enough to have a verdict ready.
FAST_FROM = 300
CALM_S = 3.0  # the hinge counts as moving until it has not changed by MIN_STEP for this long
SLOW_POLL_S = 1.0
SLOW_TILT_S = 2.0
PAUSED_WAKE_S = 5.0  # while paused it looks up only to see whether it was stopped


class SensorBus:
    """The sensor service over the system bus (Gio). Every call is synchronous with a timeout and raises on failure."""

    accel_session = None  # class defaults, so a bus made without __init__ (the tests) closes cleanly
    accel_sock = None

    def __init__(self):
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.session = None
        self.sock = None
        self.accel_session = None
        self.accel_sock = None

    def _call(self, path, iface, method, args, reply, name=NAME):
        # NO_AUTO_START: no call may D-Bus-activate the sensor service; it runs only when the phone itself started it
        return self.conn.call_sync(
            name,
            path,
            iface,
            method,
            args,
            self.GLib.VariantType(reply),
            self.Gio.DBusCallFlags.NO_AUTO_START,
            CALL_TIMEOUT_MS,
            None,
        ).unpack()

    def owned(self):
        return bool(
            self._call(
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "NameHasOwner",
                self.GLib.Variant("(s)", (NAME,)),
                "(b)",
                "org.freedesktop.DBus",
            )[0]
        )

    def open(self):
        """Load the hinge sensor, ask for a session for this process and start it."""
        if not self._call(MANAGER, "local.SensorManager", "loadPlugin", self.GLib.Variant("(s)", (SENSOR,)), "(b)")[0]:
            raise RuntimeError("the service has no hinge sensor")
        sid = self._call(
            MANAGER, "local.SensorManager", "requestSensor", self.GLib.Variant("(sx)", (SENSOR, os.getpid())), "(i)"
        )[0]
        if sid < 0:
            raise RuntimeError("the service refused a hinge session")
        self.session = sid
        self._connect(sid)
        self._call(MANAGER + "/" + SENSOR, "local.HingeSensor", "start", self.GLib.Variant("(i)", (sid,)), "()")

    def _connect(self, sid):
        self.sock = self._dial(sid)

    def _dial(self, sid):
        """Open the session's data socket the way sensorfw's own clients do: connect, send the session id as a native
        int, read the one-byte acknowledgement. The readings themselves are read from the sensor object (angle()); the
        socket is held open, and what arrives on it is thrown away, so the service keeps the session alive."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(2.0)
            sock.connect(SOCKET)
            sock.sendall(struct.pack("@i", sid))
            if not sock.recv(1):
                raise ConnectionError("the sensor service closed the data socket")
            sock.setblocking(False)
        except BaseException:
            sock.close()
            raise
        return sock

    def _drain(self, sock=None):
        """Throw away what the service sent on a data socket; raise when it has closed it (the session is gone)."""
        sock = self.sock if sock is None else sock
        while True:
            try:
                data = sock.recv(4096)
            except BlockingIOError:
                return
            if not data:
                raise ConnectionError("the sensor service closed the data socket")

    def drain_accel(self):
        """Throw away what the service sent on the accelerometer's data socket, if one is open (it raises when closed)."""
        if getattr(self, "accel_sock", None) is not None:
            self._drain(self.accel_sock)

    def angle(self):
        """The hinge angle in degrees now (the service's reading is a (timestamp, degrees) pair)."""
        if self.sock is not None:
            self._drain()
        return self._call(MANAGER + "/" + SENSOR, "local.HingeSensor", "hinge", None, "((tu))")[0][1]

    def open_accel(self):
        """The accelerometer's own session, socket and start, like open(); on failure nothing of it is left behind."""
        try:
            if not self._call(MANAGER, "local.SensorManager", "loadPlugin", self.GLib.Variant("(s)", (ACCEL,)), "(b)")[
                0
            ]:
                raise RuntimeError("the service has no accelerometer")
            sid = self._call(
                MANAGER, "local.SensorManager", "requestSensor", self.GLib.Variant("(sx)", (ACCEL, os.getpid())), "(i)"
            )[0]
            if sid < 0:
                raise RuntimeError("the service refused an accelerometer session")
            self.accel_session = sid
            self.accel_sock = self._dial(sid)
            self._call(
                MANAGER + "/" + ACCEL, "local.AccelerometerSensor", "start", self.GLib.Variant("(i)", (sid,)), "()"
            )
        except BaseException:
            self.close_accel()
            raise

    def tilt(self):
        """The accelerometer's z now, in 1/1024 g (the reading is (timestamp, x, y, z)); None while the sensor has not
        reported yet (a started sensor reads all zeros until then)."""
        if self.accel_sock is not None:
            self._drain(self.accel_sock)
        ts, x, y, z = self._call(MANAGER + "/" + ACCEL, "local.AccelerometerSensor", "xyz", None, "((tddd))")[0]
        if not (ts or x or y or z):
            return None
        return z

    def close_accel(self):
        sid, self.accel_session = self.accel_session, None
        sock, self.accel_sock = self.accel_sock, None
        if sock is not None:
            sock.close()
        if sid is None:
            return
        try:
            self._call(
                MANAGER + "/" + ACCEL, "local.AccelerometerSensor", "stop", self.GLib.Variant("(i)", (sid,)), "()"
            )
            self._call(
                MANAGER,
                "local.SensorManager",
                "releaseSensor",
                self.GLib.Variant("(six)", (ACCEL, sid, os.getpid())),
                "(b)",
            )
        except Exception:
            pass  # best-effort release on stop: a gone service or bus (GLib.Error, OSError) ends its sessions anyway

    def close(self):
        self.close_accel()
        sid, self.session = self.session, None
        sock, self.sock = self.sock, None
        if sock is not None:
            sock.close()
        if sid is None:
            return
        try:
            self._call(MANAGER + "/" + SENSOR, "local.HingeSensor", "stop", self.GLib.Variant("(i)", (sid,)), "()")
            self._call(
                MANAGER,
                "local.SensorManager",
                "releaseSensor",
                self.GLib.Variant("(six)", (SENSOR, sid, os.getpid())),
                "(b)",
            )
        except Exception:
            pass  # the service may be gone already; its sessions go with it


def _say(line):
    print("opos hinge: " + line, file=sys.stderr, flush=True)


_UNSAID = object()  # no tilt has been said yet, not even None


class HingeWatcher:
    """Reads the hinge angle in a daemon thread. on_angle(degrees) is called when a reading is first had and after that
    whenever it moves by MIN_STEP degrees or more; on_angle(None) when the service goes away (it is then looked for
    again every retry_s). make_bus() gives a SensorBus (or a stand-in); it is made inside the thread, so a missing bus
    is only a "none" here. Both callbacks run in the watcher's thread.

    With on_tilt, the same thread also reads the accelerometer's z: on_tilt(z) when a reading is first had and after
    that when z moves by TILT_STEP or more, at most once every tilt_min_s; on_tilt(None) when the accelerometer cannot
    be had or is lost. The accelerometer is optional: its failure never touches the hinge angle, and it is tried again
    every retry_s while the hinge is read. Without on_tilt the accelerometer is never opened."""

    def __init__(
        self,
        on_angle,
        make_bus=SensorBus,
        poll_s=POLL_S,
        retry_s=RETRY_S,
        settle_s=SETTLE_S,
        on_tilt=None,
        tilt_min_s=TILT_MIN_S,
        clock=time.monotonic,
        slow_poll_s=SLOW_POLL_S,
        slow_tilt_s=SLOW_TILT_S,
        calm_s=CALM_S,
    ):
        self.on_angle, self.make_bus, self.poll_s, self.retry_s = on_angle, make_bus, poll_s, retry_s
        self.settle_s = settle_s
        self.slow_poll_s, self.slow_tilt_s, self.calm_s = slow_poll_s, slow_tilt_s, calm_s
        self._moved_at, self._tilt_due = float("-inf"), 0.0
        self._paused, self._wake = False, threading.Event()
        self.on_tilt, self.tilt_min_s, self.clock = on_tilt, tilt_min_s, clock
        self.last = None  # the last angle reported; None when none is in force
        self.tilt_last = _UNSAID  # the last z reported (None: the accelerometer was said to be lost)
        self._tilt_at = None  # when the last z was reported
        self._accel_open = False
        self._accel_retry_at = 0.0
        self._stop = threading.Event()
        self._told = None  # the last reason said in the log, so a long outage is one line
        self._told_tilt = None  # the same, for the accelerometer
        self.thread = None

    def start(self):
        """Start the thread; a watcher that is already running is left alone, and one that was stopped starts afresh."""
        if self.thread is not None and self.thread.is_alive():
            return self
        self._stop = threading.Event()
        self.last = None
        self.tilt_last, self._tilt_at, self._accel_open, self._accel_retry_at = _UNSAID, None, False, 0.0
        self.thread = threading.Thread(target=self._run, name="opos-hinge", daemon=True)
        self.thread.start()
        return self

    def stop(self, wait=True):
        self._stop.set()
        self._wake.set()  # a paused watcher waits on _wake, not _stop: wake it so it sees the stop at once
        if wait and self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(self.poll_s + self.retry_s + 3.0)

    def _tell(self, reason):
        if reason != self._told:
            self._told = reason
            _say(reason)

    def _tell_tilt(self, reason):
        if reason != self._told_tilt:
            self._told_tilt = reason
            _say(reason)

    def set_paused(self, paused):
        """Pause (True) or resume the readings. While the lid is closed nothing needs the hinge or the accelerometer, so
        the accelerometer session is closed, the sensors are not asked, and the sensor service has less to do; on resume
        the accelerometer is opened again and the readings carry on. Safe from any thread."""
        paused = bool(paused)
        if paused != self._paused:
            self._paused = paused
            self._wake.set()

    def _lost(self):
        if self.last is not None:
            self.last = None
            self._deliver(None)
        self._accel_open = False  # the bus is closed (or about to be) with the hinge session; the accelerometer too
        self._accel_retry_at = 0.0
        self._tilt_lost()

    def _deliver(self, angle):
        try:
            self.on_angle(angle)
        except Exception as err:  # a callback's failure must not end the watcher
            _say("a callback failed: %r" % (err,))

    def _tilt_lost(self):
        if self.on_tilt is not None and self.tilt_last is not None:
            self.tilt_last, self._tilt_at = None, None
            self._deliver_tilt(None)

    def _deliver_tilt(self, z):
        try:
            self.on_tilt(z)
        except Exception as err:
            _say("a tilt callback failed: %r" % (err,))

    def _open_accel(self, bus):
        """Try the accelerometer once; a failure is said once and reported as on_tilt(None), never raised."""
        if self.on_tilt is None or self._accel_open:
            return
        try:
            opener = getattr(bus, "open_accel", None)
            if opener is None:
                raise RuntimeError("this bus has no accelerometer")
            opener()
            self._accel_open = True
            self._tell_tilt("reading the accelerometer")
        except Exception as err:
            self._accel_failed(bus, "the accelerometer cannot be had (the hinge angle is unaffected): %r" % (err,))

    def _accel_failed(self, bus, reason):
        self._accel_open = False
        self._accel_retry_at = self.clock() + self.retry_s
        try:
            closer = getattr(bus, "close_accel", None)
            if closer is not None:
                closer()
        except Exception:
            pass  # best-effort close of a failed accelerometer; the failure itself was said by the caller
        self._tell_tilt(reason)
        self._tilt_lost()

    def _read_tilt(self, bus, fast=True):
        """One accelerometer reading, reported when it is the first, or moved TILT_STEP or more and tilt_min_s has
        passed since the last report. A failure only ends the accelerometer session; it is tried again after retry_s."""
        if self.on_tilt is None:
            return
        if not fast and self.clock() < self._tilt_due:
            return  # slow mode: one reading every slow_tilt_s is enough
        if not self._accel_open:
            if self.clock() >= self._accel_retry_at:
                self._open_accel(bus)
            return
        self._tilt_due = self.clock() + self.slow_tilt_s
        try:
            z = bus.tilt()
        except Exception as err:
            self._accel_failed(bus, "the accelerometer was lost (the hinge angle is unaffected): %r" % (err,))
            return
        if z is None:
            return  # no report yet since it was started
        now = self.clock()
        if self.tilt_last is _UNSAID or self.tilt_last is None:
            pass
        elif now - self._tilt_at < self.tilt_min_s:
            return
        elif abs(z - self.tilt_last) < TILT_STEP and now - self._tilt_at < TILT_HEARTBEAT_S:
            return  # barely moved, and reported a moment ago
        self.tilt_last, self._tilt_at = z, now
        self._deliver_tilt(z)

    def _run(self):
        bus = None
        while not self._stop.is_set():
            try:
                if bus is None:
                    bus = self.make_bus()
                if not bus.owned():
                    raise LookupError("the sensor service is not running; no hinge angle")
                bus.open()
                self._tell("reading the hinge angle")
                self._open_accel(bus)
                self._read(bus)
            except Exception as err:
                self._lost()
                if isinstance(err, LookupError):
                    self._tell(str(err))
                else:
                    self._tell("the hinge sensor cannot be read: %r" % (err,))
                    try:
                        if bus is not None:
                            bus.close()
                    except Exception:
                        pass  # best-effort close of a lost bus before reconnecting; the loss was already handled above
                    bus = None
            if self._stop.wait(self.retry_s):
                break
        try:
            if bus is not None:
                bus.close()
        except Exception:
            pass  # best-effort close on watcher exit

    def _drain_accel(self, bus):
        try:
            drain = getattr(bus, "drain_accel", None)
            if drain is not None and self._accel_open:
                drain()
        except Exception as err:
            self._accel_failed(bus, "the accelerometer was lost (the hinge angle is unaffected): %r" % (err,))

    def _rest(self, bus):
        """While paused: close the accelerometer session once and wait to be woken (or stopped)."""
        if self._accel_open:
            closer = getattr(bus, "close_accel", None)
            try:
                if closer is not None:
                    closer()
            except Exception:
                pass  # best-effort close; the accelerometer is marked closed either way
            self._accel_open = False
            self._accel_retry_at = 0.0  # on resume it is opened at once
        self._wake.clear()
        if not self._paused or self._stop.is_set():  # resumed or stopped between the check and the clear
            return
        self._wake.wait(PAUSED_WAKE_S)
        if not self._paused:
            self._moved_at = self.clock()  # fast reads for the first moments after waking

    def _fast(self, now, angle):
        """Whether to poll fast: the hinge moved by MIN_STEP within calm_s of `now` (a time from the injected clock), or
        it is at FAST_FROM degrees or more."""
        return now - self._moved_at < self.calm_s or angle >= FAST_FROM

    def _read(self, bus):
        """Poll until stopped; raises when a call fails (the service went away, or its session ended)."""
        if self._stop.wait(self.settle_s):
            return
        while not self._stop.is_set():
            if self._paused:
                self._rest(bus)
                continue
            angle = bus.angle()
            now = self.clock()
            if self.last is None and not angle:
                # still the 0 a started sensor gives before its first report (the timestamp is always 0, so it cannot be
                # told from a real reading); a closed hinge is the lid switch's to say
                self._drain_accel(bus)  # the accelerometer is open already: its data socket must not fill up meanwhile
                if self._stop.wait(self.poll_s):
                    return
                continue
            if self.last is None or abs(angle - self.last) >= MIN_STEP:
                self.last = angle
                self._moved_at = now
                self._deliver(angle)
            fast = self._fast(now, angle)
            self._read_tilt(bus, fast)
            if self._stop.wait(self.poll_s if fast else self.slow_poll_s):
                return
