# SPDX-License-Identifier: GPL-3.0-or-later
"""hinge.py: the watcher with a stand-in for the sensor service (no bus, no toolkit), and the shell's use of it."""
import ast
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from opos.native import hinge

SHELL = Path(__file__).resolve().parents[1] / "src" / "opos" / "native" / "shell.py"


class FakeBus:
    """Stands in for SensorBus: `angles` is what angle() returns in turn (an Exception instance is raised)."""

    def __init__(self, angles, owned=True, open_error=None):
        self.angles, self._owned, self.open_error = list(angles), owned, open_error
        self.opened = self.closed = 0
        self.done = threading.Event()
        self.opened_event = threading.Event()

    def owned(self):
        if callable(self._owned):
            return self._owned()
        return self._owned

    def open(self):
        self.opened += 1
        self.opened_event.set()
        if self.open_error:
            raise self.open_error

    def angle(self):
        if not self.angles:
            self.done.set()
            raise RuntimeError("out of readings")
        v = self.angles.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    def close(self):
        self.closed += 1


class Watch(unittest.TestCase):
    def run_watcher(self, bus, wait_for=None, **kw):
        got, lock = [], threading.Lock()

        def on_angle(a):
            with lock:
                got.append(a)
        w = hinge.HingeWatcher(on_angle, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0, **kw).start()
        (wait_for or bus.done).wait(5)
        w.stop()
        with lock:
            return list(got)

    def test_the_first_reading_and_each_move_of_two_degrees_or_more_is_reported(self):
        bus = FakeBus([160, 161, 162, 150, 150, 151, 90])
        self.assertEqual(self.run_watcher(bus)[:4], [160, 162, 150, 90][:4])

    def test_a_service_that_is_not_running_gives_no_angle_and_is_never_started(self):
        bus = FakeBus([100], owned=False)
        done = threading.Timer(0.2, bus.done.set)
        done.start()
        self.assertEqual(self.run_watcher(bus), [])
        self.assertEqual(bus.opened, 0)

    def test_a_service_that_goes_away_reports_none_and_is_looked_for_again(self):
        bus = FakeBus([120, RuntimeError("gone")])
        got = self.run_watcher(bus)
        self.assertEqual(got[:2], [120, None])

    def test_a_failed_open_is_no_crash_and_reports_nothing(self):
        bus = FakeBus([], open_error=RuntimeError("refused"))
        threading.Timer(0.2, bus.done.set).start()
        self.assertEqual(self.run_watcher(bus), [])
        self.assertGreaterEqual(bus.closed, 1)

    def test_a_callback_that_fails_does_not_end_the_watcher(self):
        bus = FakeBus([10, 50, 90])
        calls = []

        def boom(a):
            calls.append(a)
            raise ValueError("no")
        w = hinge.HingeWatcher(boom, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0).start()
        bus.done.wait(5)
        w.stop()
        self.assertEqual(calls[:3], [10, 50, 90])

    def test_the_first_reading_waits_for_the_sensor_to_settle(self):
        import time
        bus = FakeBus([120, 120])
        t0 = time.monotonic()
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0.3).start()
        while len(bus.angles) == 2 and time.monotonic() - t0 < 5:
            time.sleep(0.005)
        waited = time.monotonic() - t0
        w.stop()
        self.assertGreaterEqual(waited, 0.28)

    def test_a_zero_before_the_first_real_report_is_not_an_angle(self):
        bus = FakeBus([0, 0, 0, 120, 121, 0])
        got = self.run_watcher(bus)
        self.assertEqual(got[:2], [120, 0], "the leading zeros say nothing; a later zero (a closing hinge) is a reading")

    def test_a_stopped_watcher_can_be_started_again_and_a_running_one_is_not_started_twice(self):
        bus = FakeBus([1] * 100000)
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0).start()
        first = w.thread
        self.assertIs(w.start().thread, first, "already running: left alone")
        bus.opened_event.wait(5)
        w.stop()
        self.assertFalse(first.is_alive())
        bus.opened_event.clear()
        w.start()
        self.assertTrue(bus.opened_event.wait(5), "it reads again")
        self.assertTrue(w.thread.is_alive())
        w.stop()

    def test_a_service_that_starts_later_is_picked_up(self):
        flips = iter([False, False, True])
        bus = FakeBus([100, 100], owned=lambda: next(flips, True))
        got = self.run_watcher(bus)
        self.assertEqual(got[:1], [100])
        self.assertEqual(bus.opened, 1, "opened once, after the service appeared")

    def test_stop_ends_the_thread_and_closes_the_session(self):
        bus = FakeBus([1] * 100000)
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0).start()
        bus.opened_event.wait(5)
        w.stop()
        self.assertFalse(w.thread.is_alive())
        self.assertEqual(bus.closed, 1)


class AccelBus(FakeBus):
    """FakeBus with an accelerometer channel: `tilts` is what tilt() returns in turn (an Exception instance is raised;
    once they run out the last value repeats). open_accel raises accel_error when it is set (a callable is called)."""

    def __init__(self, angles, tilts, accel_error=None, **kw):
        super().__init__(angles, **kw)
        self.tilts, self.accel_error = list(tilts), accel_error
        self.accel_opened = self.accel_closed = 0
        self.tilt_reads = 0
        self.last_tilt = None

    def open_accel(self):
        self.accel_opened += 1
        err = self.accel_error() if callable(self.accel_error) else self.accel_error
        if err:
            raise err

    def tilt(self):
        self.tilt_reads += 1
        if self.tilts:
            v = self.tilts.pop(0)
            if isinstance(v, Exception):
                raise v
            self.last_tilt = v
        return self.last_tilt

    def close_accel(self):
        self.accel_closed += 1


class Tilt(unittest.TestCase):
    def run_watcher(self, bus, wait_for=None, **kw):
        angles, tilts, lock = [], [], threading.Lock()

        def on_angle(a):
            with lock:
                angles.append(a)

        def on_tilt(z):
            with lock:
                tilts.append(z)
        kw.setdefault("tilt_min_s", 0)
        w = hinge.HingeWatcher(on_angle, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0,
                               on_tilt=on_tilt, **kw).start()
        (wait_for or bus.done).wait(5)
        w.stop()
        with lock:
            return list(angles), list(tilts)

    def test_z_is_reported_first_and_then_on_moves_of_eight_or_more(self):
        bus = AccelBus([150] * 8, [1000, 1003, 1007, 1010, -900, -905, -1020])
        angles, tilts = self.run_watcher(bus)
        self.assertEqual(angles[:1], [150])
        self.assertEqual(tilts[:4], [1000, 1010, -900, -1020])

    def test_a_reading_before_the_first_report_is_not_a_tilt(self):
        bus = AccelBus([150] * 5, [None, None, 512])
        _, tilts = self.run_watcher(bus)
        self.assertEqual(tilts[:1], [512])

    def test_at_most_five_reports_a_second(self):
        now = [0.0]
        seq = list(range(0, 100000, 50))        # every reading moves by 50
        bus = AccelBus([150] * 40, seq)

        def clock():
            now[0] += 0.05                      # each reading is 50 ms after the last
            return now[0]
        _, tilts = self.run_watcher(bus, tilt_min_s=0.2, clock=clock)
        self.assertGreater(len(tilts), 2)
        self.assertLess(len(tilts), 40 / 3, "about one reading in four or five is said")
        self.assertEqual(hinge.TILT_MIN_S, 0.2)

    def test_an_accelerometer_that_cannot_be_had_leaves_the_hinge_working_and_says_none_once(self):
        bus = AccelBus([160, 161, 162, 150, 150, 151, 90], [1000], accel_error=RuntimeError("no plugin"))
        said = []
        old = hinge._say
        hinge._say = said.append
        try:
            angles, tilts = self.run_watcher(bus)
        finally:
            hinge._say = old
        self.assertEqual(angles[:4], [160, 162, 150, 90])
        self.assertEqual(tilts, [None])
        self.assertEqual(len([s for s in said if "accelerometer cannot be had" in s]), 1)
        self.assertGreaterEqual(bus.accel_closed, 1)

    def test_an_accelerometer_lost_while_reading_says_none_and_is_opened_again(self):
        bus = AccelBus([150] * 400, [700, RuntimeError("socket closed"), 300])
        angles, tilts = self.run_watcher(bus)
        self.assertEqual(angles[:1], [150])
        self.assertEqual(tilts[:3], [700, None, 300])
        self.assertGreaterEqual(bus.accel_opened, 2)
        self.assertIn(angles, ([150], [150, None]), "the hinge angle only goes when the fake runs out of readings")

    def test_a_lost_hinge_closes_both_and_says_none_for_both(self):
        bus = AccelBus([120, RuntimeError("gone")], [800])
        angles, tilts = self.run_watcher(bus)
        self.assertEqual(angles[:2], [120, None])
        self.assertEqual(tilts[:2], [800, None])
        self.assertGreaterEqual(bus.closed, 1)

    def test_a_failing_tilt_callback_does_not_end_the_watcher(self):
        bus = AccelBus([150] * 6, [100, 200, 300])
        calls = []

        def boom(z):
            calls.append(z)
            raise ValueError("no")
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0,
                               on_tilt=boom, tilt_min_s=0).start()
        bus.done.wait(5)
        w.stop()
        self.assertEqual(calls[:3], [100, 200, 300])

    def test_without_on_tilt_the_accelerometer_is_never_opened(self):
        bus = AccelBus([150] * 5, [100])
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0).start()
        bus.done.wait(5)
        w.stop()
        self.assertEqual((bus.accel_opened, bus.tilt_reads), (0, 0))

    def test_a_bus_without_an_accelerometer_is_only_a_none(self):
        bus = FakeBus([160, 170])
        angles, tilts = self.run_watcher(bus)
        self.assertEqual(angles[:2], [160, 170])
        self.assertEqual(tilts, [None])


class Rate(unittest.TestCase):
    """How often the sensors are asked: fast while the hinge moves or is at 300 degrees or more, slow otherwise."""

    def run_for(self, angles, wait=0.35, **kw):
        class Bus(FakeBus):
            calls = []
            def angle(self):
                self.calls.append(("angle", time.monotonic()))
                return self.angles[0] if len(self.angles) == 1 else self.angles.pop(0)

            def tilt(self):
                self.calls.append(("tilt", time.monotonic()))
                return 500
            def open_accel(self):
                pass
        bus = Bus(list(angles))
        bus.calls = []
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, on_tilt=lambda z: None, settle_s=0, retry_s=0.05, **kw).start()
        time.sleep(wait)
        w.stop()
        return [k for k, _t in bus.calls]

    def test_a_still_hinge_below_300_is_asked_slowly(self):
        calls = self.run_for([170], poll_s=0.01, slow_poll_s=0.2, slow_tilt_s=0.3, calm_s=0.05)
        self.assertLess(calls.count("angle"), 10, calls)       # fast would be about 35 in this window
        self.assertLessEqual(calls.count("tilt"), 5, calls)

    def test_a_hinge_at_300_or_more_is_asked_fast_even_when_still(self):
        calls = self.run_for([350], poll_s=0.01, slow_poll_s=0.5, slow_tilt_s=0.5, calm_s=0.05)
        self.assertGreater(calls.count("angle"), 12, calls)
        self.assertGreater(calls.count("tilt"), 12, calls)

    def test_nothing_is_asked_while_paused_and_it_carries_on_after(self):
        class Bus(FakeBus):
            def __init__(self):
                super().__init__([100] * 100000)
                self.asked, self.accel_closed, self.accel_opened = 0, 0, 0

            def angle(self):
                self.asked += 1
                return 100

            def tilt(self):
                return 500

            def open_accel(self):
                self.accel_opened += 1

            def close_accel(self):
                self.accel_closed += 1
        bus = Bus()
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, on_tilt=lambda z: None, settle_s=0, poll_s=0.01,
                               retry_s=0.05).start()
        time.sleep(0.15)
        w.set_paused(True)
        time.sleep(0.15)
        frozen = bus.asked
        time.sleep(0.3)
        self.assertEqual(bus.asked, frozen, "paused: the sensors are not asked")
        self.assertGreaterEqual(bus.accel_closed, 1, "and the accelerometer session is closed")
        w.set_paused(False)
        time.sleep(0.3)
        self.assertGreater(bus.asked, frozen, "resumed: asked again")
        self.assertGreaterEqual(bus.accel_opened, 2, "and the accelerometer is opened again")
        w.stop()

    def test_a_moving_hinge_is_asked_fast(self):
        calls = self.run_for([100 + 5 * i for i in range(40)], poll_s=0.01, slow_poll_s=0.5, slow_tilt_s=0.5, calm_s=5.0)
        self.assertGreater(calls.count("angle"), 12, calls)

    def test_fast_or_slow_is_decided_on_the_injected_clock(self):
        """No thread and no real time: the decision only reads the clock it is given."""
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: None, calm_s=3.0, clock=lambda: 1000.0)
        self.assertFalse(w._fast(0.0, 170), "never moved and below FAST_FROM: slow")
        self.assertTrue(w._fast(0.0, hinge.FAST_FROM), "at FAST_FROM: fast even when still")
        self.assertFalse(w._fast(0.0, hinge.FAST_FROM - 1))
        w._moved_at = 100.0
        self.assertTrue(w._fast(102.9, 170), "moved within calm_s: fast")
        self.assertFalse(w._fast(103.0, 170), "calm for calm_s: slow")

    def test_a_paused_watcher_is_woken_by_stop(self):
        """Paused, the thread waits up to PAUSED_WAKE_S (5 s) for a resume; stop() must end it at once, and close."""
        bus = FakeBus([100] * 100000)
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, settle_s=0, poll_s=0.01, retry_s=0.05).start()
        self.assertTrue(bus.opened_event.wait(2))
        w.set_paused(True)
        time.sleep(0.1)                         # the thread is now in its paused wait
        t0 = time.monotonic()
        w.stop()
        self.assertFalse(w.thread.is_alive(), "stop() returned with the thread still waiting to be resumed")
        self.assertLess(time.monotonic() - t0, 1.0, "stop is not held up by the paused wait")
        self.assertEqual(bus.closed, 1, "the session is closed on the way out")

    def test_a_stop_between_the_pause_check_and_the_clear_is_not_waited_out(self):
        """No thread: stop() lands just as _rest clears _wake, so the wake it set is lost; _rest must see _stop and not
        wait PAUSED_WAKE_S for a wake that will not come."""
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: None)
        w.set_paused(True)
        waited = []

        class Wake:
            def clear(self):
                w.stop(wait=False)          # sets _stop and _wake; the set below is the one the clear wipes out

            def set(self):
                pass

            def wait(self, timeout=None):
                waited.append(timeout)
                return False

        w._wake = Wake()
        w._rest(None)
        self.assertEqual(waited, [], "_rest waited for a wake after it was stopped")


class NeverStartsTheService(unittest.TestCase):
    def test_every_bus_call_asks_the_bus_not_to_start_the_service(self):
        """sensorfwd runs only when the phone starts it: no call may D-Bus-activate it (a sensor-chip stall once reset
        the phone)."""
        seen = []

        class Conn:
            def call_sync(self, *args):
                seen.append(args)
                return type("R", (), {"unpack": lambda self: (True,)})()
        bus = hinge.SensorBus.__new__(hinge.SensorBus)
        bus.conn = Conn()
        bus.Gio = type("Gio", (), {"DBusCallFlags": type("F", (), {"NONE": "none", "NO_AUTO_START": "no-auto-start"})})
        bus.GLib = type("G", (), {"Variant": staticmethod(lambda sig, args: (sig, args)), "VariantType": staticmethod(lambda s: s)})
        self.assertTrue(bus.owned())
        bus._call(hinge.MANAGER, "local.SensorManager", "loadPlugin", None, "(b)")
        self.assertEqual([a[6] for a in seen], ["no-auto-start", "no-auto-start"])


class StillPhone(unittest.TestCase):
    """The review's finding: a phone lying still after a flip must still get its new facing verdict."""

    def test_an_unchanged_reading_is_passed_on_now_and_then(self):
        got = []

        class Bus(FakeBus):
            def tilt(self):
                return -1000                # lying still, the same reading every time

            def open_accel(self):
                pass
        bus = Bus([100] * 100000)
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, on_tilt=got.append, settle_s=0, poll_s=0.01, retry_s=0.05,
                               tilt_min_s=0.01).start()
        time.sleep(1.4)
        w.stop()
        self.assertGreaterEqual(len(got), 3, got)
        self.assertEqual(set(got), {-1000})

    def test_the_tilt_rate_and_heartbeat_follow_the_injected_clock(self):
        """The same rule with no thread and no real time: one reading at a time, on a clock the test sets."""
        got, t, z = [], [0.0], [-1000]

        class Bus:
            def tilt(self):
                return z[0]
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: None, on_tilt=got.append, clock=lambda: t[0], tilt_min_s=0.2)
        w._accel_open = True
        for now, value, said in ((0.0, -1000, 1), (0.3, -1000, 1), (0.5, -1000, 2), (0.6, -1000, 2),
                                 (0.65, -900, 2), (0.75, -900, 3), (0.8, -1000, 3)):
            t[0], z[0] = now, value
            w._read_tilt(Bus(), fast=True)
            self.assertEqual(len(got), said, "at %.2f s" % now)
        self.assertEqual(got, [-1000, -1000, -900])

    def test_a_flip_is_confirmed_by_facing_on_a_phone_that_then_lies_still(self):
        from opos.native import facing
        f = facing.Facing()
        clock = 0.0
        for z in [1000] * 3:
            f.update(z, clock)
            clock += 0.5
        self.assertEqual(f.current, "right")
        for _ in range(4):                  # the heartbeat: a reading every 0.5 s, all the same
            f.update(-1000, clock)
            clock += 0.5
        self.assertEqual(f.current, "left")

    def test_the_accelerometer_socket_is_drained_while_the_hinge_still_reads_its_first_zero(self):
        drained = []

        class Bus(FakeBus):
            def angle(self):
                return 0                    # a sensor that has not reported yet

            def open_accel(self):
                pass

            def tilt(self):
                return None

            def drain_accel(self):
                drained.append(1)
        bus = Bus([0] * 100000)
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, on_tilt=lambda z: None, settle_s=0, poll_s=0.01,
                               retry_s=0.05).start()
        time.sleep(0.3)
        w.stop()
        self.assertGreaterEqual(len(drained), 5)


class ShellUse(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(SHELL.read_text(encoding="utf-8"))

    def method(self, name):
        shell = next(n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "Shell")
        return next(n for n in shell.body if isinstance(n, ast.FunctionDef) and n.name == name)

    def calls(self, node):
        return [ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)]

    def test_only_start_lid_starts_the_watcher_and_start_lid_is_not_run_by_the_checking_modes(self):
        self.assertIn("hinge.HingeWatcher", self.calls(self.method("start_lid")))
        started = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)
                   and any(c == "hinge.HingeWatcher" for c in self.calls(n))]
        self.assertEqual(started, ["start_lid"])
        main = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        gate = next(n for n in ast.walk(main) if isinstance(n, ast.If) and "win.start_lid" in [ast.unparse(c.func) for c in ast.walk(n) if isinstance(c, ast.Call)])
        self.assertEqual(ast.unparse(gate.test).replace("(", "").replace(")", ""), "not probe and not shots")

    def test_the_lid_pauses_the_sensors_and_losing_the_lid_resumes_them(self):
        self.assertIn("watcher.set_paused", self.calls(self.method("lid_changed")))
        self.assertIn("watcher.set_paused", self.calls(self.method("lid_lost")))

    def test_every_change_of_lid_or_angle_goes_through_set_posture(self):
        for name in ("lid_changed", "lid_lost", "hinge_changed"):
            self.assertIn("self.set_posture", self.calls(self.method(name)), name)

    def test_the_settings_line_is_repainted_by_set_posture_only_as_a_label(self):
        self.assertIn("page.paint_posture", self.calls(self.method("set_posture")))
        screens = ast.parse((SHELL.parent / "screens.py").read_text(encoding="utf-8"))
        settings = next(n for n in screens.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
        paint = next(n for n in settings.body if isinstance(n, ast.FunctionDef) and n.name == "paint_posture")
        called = [ast.unparse(c.func) for c in ast.walk(paint) if isinstance(c, ast.Call)]
        self.assertEqual(sorted(set(called)), ["getattr", "posture.describe", "self.hinge_line.set_label"])

    def test_the_angle_is_never_what_blanks_or_locks(self):
        for name in ("hinge_changed", "set_posture"):
            called = self.calls(self.method(name))
            for bad in ("self.blanker.blank", "self.go", "self.set_shade", "self.restore", "self.go"):
                self.assertNotIn(bad, called, name)


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "needs unix sockets (the phone and WSL have them)")
class BusTalk(unittest.TestCase):
    """SensorBus with the D-Bus calls stubbed and a real local socket standing in for /var/run/sensord.sock."""

    def make(self, ack=b"\n", close_after_ack=False):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        path = os.path.join(self.dir.name, "sensord.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(1)
        self.addCleanup(self.server.close)
        self.received = []

        def serve():
            conn, _ = self.server.accept()
            self.conn = conn
            self.received.append(conn.recv(4))
            if ack:
                conn.sendall(ack)
            if close_after_ack:
                conn.close()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        old = hinge.SOCKET
        hinge.SOCKET = path
        self.addCleanup(setattr, hinge, "SOCKET", old)
        bus = hinge.SensorBus.__new__(hinge.SensorBus)
        bus.session, bus.sock, bus.calls = None, None, []
        bus.GLib = type("G", (), {"Variant": staticmethod(lambda sig, args: (sig, args)), "VariantType": staticmethod(lambda s: s)})
        bus._call = lambda path_, iface, method, args, reply, name=hinge.NAME: bus.calls.append(method) or {
            "loadPlugin": (True,), "requestSensor": (7,), "hinge": ((0, 123),)}.get(method, ())
        return bus

    def test_open_sends_the_session_id_and_reads_the_acknowledgement_before_starting(self):
        bus = self.make()
        bus.open()
        self.thread.join(5)
        self.assertEqual(struct.unpack("@i", self.received[0])[0], 7)
        self.assertEqual(bus.calls, ["loadPlugin", "requestSensor", "start"])
        self.assertEqual(bus.angle(), 123)
        bus.close()
        self.assertEqual(bus.calls[-2:], ["stop", "releaseSensor"])
        self.assertIsNone(bus.sock)

    def test_a_service_that_closes_the_socket_is_noticed_by_the_next_reading(self):
        bus = self.make(close_after_ack=True)
        bus.open()
        self.thread.join(5)
        with self.assertRaises(ConnectionError):
            for _ in range(50):
                bus.angle()
        bus.close()

    def test_no_acknowledgement_is_an_error_and_leaves_no_socket_behind(self):
        bus = self.make(ack=b"", close_after_ack=True)
        with self.assertRaises(Exception):
            bus.open()
        self.assertIsNone(bus.sock)


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "needs unix sockets (the phone and WSL have them)")
class AccelTalk(unittest.TestCase):
    """The accelerometer's session on SensorBus: its own session id and its own data socket beside the hinge's."""

    def make(self, accel_ack=b"\n", accel_plugin=True, xyz=(5, 10, -20, 1001)):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        path = os.path.join(self.dir.name, "sensord.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(2)
        self.addCleanup(self.server.close)
        self.received, self.conns = [], []
        acks = [b"\n", accel_ack]

        def serve():
            for ack in acks:
                try:
                    conn, _ = self.server.accept()
                except OSError:
                    return
                self.conns.append(conn)
                self.received.append(conn.recv(4))
                if ack:
                    conn.sendall(ack)
                else:
                    conn.close()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        old = hinge.SOCKET
        hinge.SOCKET = path
        self.addCleanup(setattr, hinge, "SOCKET", old)
        bus = hinge.SensorBus.__new__(hinge.SensorBus)
        bus.session, bus.sock, bus.calls = None, None, []
        bus.GLib = type("G", (), {"Variant": staticmethod(lambda sig, args: (sig, args)), "VariantType": staticmethod(lambda s: s)})

        def call(path_, iface, method, args, reply, name=hinge.NAME):
            sensor = args[1][0] if args and method in ("loadPlugin", "requestSensor", "releaseSensor") else path_.rsplit("/", 1)[-1]
            bus.calls.append((sensor, method))
            if method == "loadPlugin":
                return (accel_plugin if sensor == hinge.ACCEL else True,)
            if method == "requestSensor":
                return (9 if sensor == hinge.ACCEL else 7,)
            if method == "hinge":
                return ((0, 123),)
            if method == "xyz":
                return (xyz,)
            return ()
        bus._call = call
        return bus

    def test_the_accelerometer_has_its_own_session_and_socket_and_is_closed_with_the_hinge(self):
        bus = self.make()
        bus.open()
        bus.open_accel()
        self.thread.join(5)
        self.assertEqual([struct.unpack("@i", r)[0] for r in self.received], [7, 9])
        self.assertEqual(bus.calls, [("hingesensor", "loadPlugin"), ("hingesensor", "requestSensor"),
                                     ("hingesensor", "start"), (hinge.ACCEL, "loadPlugin"),
                                     (hinge.ACCEL, "requestSensor"), (hinge.ACCEL, "start")])
        self.assertIsNot(bus.accel_sock, bus.sock)
        self.assertEqual(bus.tilt(), 1001)
        self.assertEqual(bus.angle(), 123)
        bus.close()
        self.assertIn((hinge.ACCEL, "stop"), bus.calls)
        self.assertIn((hinge.ACCEL, "releaseSensor"), bus.calls)
        self.assertEqual(bus.calls[-2:], [("hingesensor", "stop"), ("hingesensor", "releaseSensor")])
        self.assertIsNone(bus.sock)
        self.assertIsNone(bus.accel_sock)
        self.assertIsNone(bus.accel_session)

    def test_an_all_zero_reading_is_no_report_yet(self):
        bus = self.make(xyz=(0, 0, 0, 0))
        bus.open()
        bus.open_accel()
        self.thread.join(5)
        self.assertIsNone(bus.tilt())
        bus.close()

    def test_no_accelerometer_plugin_is_an_error_that_leaves_the_hinge_session_alone(self):
        bus = self.make(accel_plugin=False)
        bus.open()
        with self.assertRaises(RuntimeError):
            bus.open_accel()
        self.assertIsNone(bus.accel_sock)
        self.assertIsNone(bus.accel_session)
        self.assertEqual(bus.session, 7)
        self.assertIsNotNone(bus.sock)
        self.assertEqual(bus.angle(), 123)
        bus.close()

    def test_no_acknowledgement_on_the_accelerometer_socket_releases_its_session(self):
        bus = self.make(accel_ack=b"")
        bus.open()
        with self.assertRaises(Exception):
            bus.open_accel()
        self.assertIsNone(bus.accel_sock)
        self.assertIsNone(bus.accel_session)
        self.assertIn((hinge.ACCEL, "releaseSensor"), bus.calls)
        self.assertNotIn((hinge.ACCEL, "start"), bus.calls)
        self.assertEqual(bus.angle(), 123)
        bus.close()

    def test_a_closed_accelerometer_socket_is_noticed_by_the_next_tilt(self):
        bus = self.make()
        bus.open()
        bus.open_accel()
        self.thread.join(5)
        self.conns[1].close()
        with self.assertRaises(ConnectionError):
            for _ in range(50):
                bus.tilt()
        self.assertEqual(bus.angle(), 123, "the hinge socket is still fine")
        bus.close()

if __name__ == "__main__":
    unittest.main()
