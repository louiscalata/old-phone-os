# SPDX-License-Identifier: GPL-3.0-or-later
"""posture.py: finding the lid in a fake sysfs tree, reading its events, the posture name, and the watcher thread with a
fake device (a temp file, or a pipe where select works on one). No toolkit and no real device."""
import ast
import contextlib
import io
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from opos.native import posture

POSTURE = Path(__file__).resolve().parents[1] / "src" / "opos" / "native" / "posture.py"


def ev(kind, code, value, sec=1, usec=0):
    return struct.pack("@llHHi", sec, usec, kind, code, value)


def lid(closed):
    return ev(posture.EV_SW, posture.SW_LID, 1 if closed else 0)


SYN = ev(0, 0, 0)


class FakeSysfs:
    def __init__(self, devices):
        """devices: {"input3": ("name file text", ["event3", ...])}"""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for folder, (name, children) in devices.items():
            d = self.root / folder
            d.mkdir()
            if name is not None:
                (d / "name").write_text(name, encoding="utf-8")
            for c in children:
                (d / c).mkdir()

    def close(self):
        self._tmp.cleanup()


class FindLid(unittest.TestCase):
    def tree(self, devices):
        t = FakeSysfs(devices)
        self.addCleanup(t.close)
        return str(t.root)

    def test_the_exact_name_with_its_newline(self):
        root = self.tree({"input0": ("gpio-keys\n", ["event0"]), "input5": ("Surface Duo Lid\n", ["event5"])})
        self.assertEqual(posture.find_lid(root), "/dev/input/event5")

    def test_a_name_file_without_a_newline_also_matches(self):
        self.assertEqual(posture.find_lid(self.tree({"input2": ("Surface Duo Lid", ["event2"])})), "/dev/input/event2")

    def test_the_wakeup_key_device_is_never_the_lid(self):
        root = self.tree({"input1": ("Surface Duo Lid Wakeup\n", ["event1"]), "input7": ("Surface Duo Lid\n", ["event7"])})
        self.assertEqual(posture.find_lid(root), "/dev/input/event7")
        self.assertIsNone(posture.find_lid(self.tree({"input1": ("Surface Duo Lid Wakeup\n", ["event1"])})))

    def test_near_names_do_not_match(self):
        root = self.tree({"input1": ("surface duo lid\n", ["event1"]), "input2": (" Surface Duo Lid\n", ["event2"]),
                          "input3": ("Surface Duo\n", ["event3"])})
        self.assertIsNone(posture.find_lid(root))

    def test_missing(self):
        self.assertIsNone(posture.find_lid(self.tree({})))
        self.assertIsNone(posture.find_lid(self.tree({"input0": ("gpio-keys\n", ["event0"])})))
        self.assertIsNone(posture.find_lid(self.tree({"input4": (None, ["event4"])})), "no name file")
        self.assertIsNone(posture.find_lid(self.tree({"input4": ("Surface Duo Lid\n", ["mouse0"])})), "no event node")
        self.assertIsNone(posture.find_lid(os.path.join(tempfile.gettempdir(), "no-such-opos-sysfs-root")))

    def test_only_input_folders_are_searched(self):
        root = self.tree({"event9": ("Surface Duo Lid\n", ["event9"]), "input10": ("Surface Duo Lid\n", ["event11"])})
        self.assertEqual(posture.find_lid(root), "/dev/input/event11")


class Events(unittest.TestCase):
    def test_the_record_is_the_native_input_event(self):
        self.assertEqual(posture.EVENT_SIZE, struct.calcsize("@llHHi"))
        if sys.platform.startswith("linux") and struct.calcsize("P") == 8:
            self.assertEqual(posture.EVENT_SIZE, 24)
        self.assertEqual((posture.EV_SW, posture.SW_LID), (5, 0))

    def test_closed_and_open(self):
        self.assertEqual(posture.lid_changes(lid(True) + SYN), [True])
        self.assertEqual(posture.lid_changes(lid(False) + SYN), [False])
        self.assertEqual(posture.lid_changes(ev(5, 0, 7)), [True], "any non-zero value is closed")

    def test_other_events_are_skipped(self):
        other = ev(1, 116, 1) + ev(5, 1, 1) + ev(0, 0, 0) + ev(3, 0, 5) + ev(4, 4, 0)
        self.assertEqual(posture.lid_changes(other), [])

    def test_several_events_in_one_read_in_order(self):
        data = lid(True) + SYN + ev(1, 116, 1) + lid(False) + SYN + lid(True) + SYN
        self.assertEqual(posture.lid_changes(data), [True, False, True])

    def test_a_partial_record_at_the_end(self):
        data = lid(True) + SYN + lid(False)[:10]
        whole, rest = posture.split_events(data)
        self.assertEqual((whole, rest), (lid(True) + SYN, lid(False)[:10]))
        self.assertEqual(posture.lid_changes(data), [True], "the partial one is not read")
        self.assertEqual(posture.lid_changes(rest + lid(False)[10:]), [False], "the remainder and the next read make it whole")
        self.assertEqual(posture.split_events(b""), (b"", b""))
        self.assertEqual(posture.split_events(b"abc"), (b"", b"abc"))
        self.assertEqual(posture.lid_changes(bytearray(lid(True))), [True])

    def test_a_dropped_queue_asks_for_the_state_again(self):
        dropped = ev(0, 3, 0)
        D = posture.DROPPED
        self.assertEqual((posture.EV_SYN, posture.SYN_REPORT, posture.SYN_DROPPED), (0, 0, 3))
        self.assertEqual(posture.lid_items(lid(True) + SYN + lid(False) + SYN), [True, False], "as lid_changes without a drop")
        self.assertEqual(posture.lid_items(lid(True) + SYN + dropped + lid(False) + SYN + lid(True) + SYN), [True, D, True],
                         "the events after the drop up to its SYN_REPORT are incomplete: skipped, and the state is read there")
        self.assertEqual(posture.lid_items(lid(True) + SYN + dropped + lid(False)), [True, D], "no SYN_REPORT yet: read it now")
        self.assertEqual(posture.lid_changes(dropped + lid(False) + SYN), [False], "lid_changes itself is unchanged")

    def test_the_ioctl_number_and_a_failing_ioctl(self):
        self.assertEqual(posture.EVIOCGSW_8, (2 << 30) | (8 << 16) | (ord("E") << 8) | 0x1b)
        with tempfile.TemporaryFile() as f:      # a plain file is no input device: the ioctl fails, or Windows has none
            self.assertIsNone(posture.lid_state(f.fileno()))


class Posture(unittest.TestCase):
    def test_closed_or_open_and_the_angle_changes_nothing_yet(self):
        self.assertEqual(posture.posture(True), "closed")
        self.assertEqual(posture.posture(False), "open")
        self.assertEqual(posture.posture(None), "open")
        for angle in (0, 90, 180, 300, None):
            self.assertEqual(posture.posture(False, angle), "open")
            self.assertEqual(posture.posture(True, angle=angle), "closed")


class Watcher(unittest.TestCase):
    def setUp(self):
        self.changes, self.missing = [], []
        self.lock = threading.Lock()
        self.saved_state, self.saved_readable = posture.lid_state, posture._readable
        self.addCleanup(self.restore)
        self.initial = False
        posture.lid_state = lambda fd: self.initial
        if sys.platform == "win32":     # select takes only sockets there; a file is always readable anyway
            posture._readable = lambda fd, timeout: True

    def restore(self):
        posture.lid_state, posture._readable = self.saved_state, self.saved_readable

    def changed(self, closed):
        with self.lock:
            self.changes.append(closed)

    def watcher(self, path, **k):
        w = posture.LidWatcher(self.changed, self.missing.append, path=path, poll_s=0.05, **k)
        self.addCleanup(w.stop)
        return w

    def wait_for(self, what, n, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if len(what) >= n:
                    return
            time.sleep(0.01)
        self.fail("waited for %d, have %r" % (n, what))

    def device(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.unlink, path)
        return path

    def feed(self, path, data):
        with open(path, "ab") as f:
            f.write(data)

    def test_the_start_state_once_then_only_changes(self):
        path = self.device()
        w = self.watcher(path).start()
        self.wait_for(self.changes, 1)
        self.assertEqual(self.changes, [False], "the start state, reported once")
        self.feed(path, lid(False) + SYN + lid(False) + SYN)    # the same state again: not reported
        self.feed(path, lid(True) + SYN + lid(True) + SYN)
        self.wait_for(self.changes, 2)
        self.feed(path, lid(True)[:7])                          # half a record, then the rest in a later read
        time.sleep(0.2)
        self.feed(path, lid(True)[7:] + SYN + ev(1, 116, 1) + lid(False)[:5])
        time.sleep(0.2)
        self.feed(path, lid(False)[5:] + SYN)
        self.wait_for(self.changes, 3)
        time.sleep(0.2)
        w.stop()
        self.assertEqual(self.changes, [False, True, False])
        self.assertEqual(self.missing, [])

    def test_a_closed_start(self):
        self.initial = True
        self.watcher(self.device()).start()
        self.wait_for(self.changes, 1)
        time.sleep(0.15)
        self.assertEqual(self.changes, [True])

    def test_an_unreadable_start_state_is_told_as_missing_and_the_thread_ends(self):
        """Round 6: a failed first EVIOCGSW is treated like a failed re-read (R2): the switch is lost, said once through
        on_missing (the shell then treats the lid as unknown and lights the screens), and the watcher ends. It never
        waits silently, and a later event on the device is not reported."""
        self.initial = None
        path = self.device()
        w = self.watcher(path).start()
        w.thread.join(2.0)
        self.assertFalse(w.thread.is_alive())
        self.assertEqual(self.missing, ["cannot read the lid switch"])
        self.feed(path, lid(False) + SYN)
        time.sleep(0.15)
        self.assertEqual(self.changes, [])

    def test_a_dropped_queue_reports_the_state_read_again(self):
        """The kernel's queue overflowed and the "opened" event was lost: the watcher reads the switch again (EVIOCGSW)
        and reports open, so the screens are lit; without this the remembered state stays closed and dark."""
        self.initial = True
        path = self.device()
        self.watcher(path).start()
        self.wait_for(self.changes, 1)
        self.initial = False                    # the lid is open now; the event that said so was dropped
        self.feed(path, ev(0, 3, 0) + SYN)
        self.wait_for(self.changes, 2)
        self.assertEqual(self.changes, [True, False])

    def test_items_read_before_the_re_read_are_never_replayed_after_it(self):
        """R1: the batch SYN_DROPPED, SYN_REPORT, SW_LID=1, SYN_REPORT, with EVIOCGSW reading open. EVIOCGSW flushes the
        client's queued switch events, so the "closed" already read is older than the re-read: replaying it would leave
        the shell believing the lid is closed (and blank an open phone)."""
        self.initial = True
        path = self.device()
        w = self.watcher(path).start()
        self.wait_for(self.changes, 1)
        self.initial = False                    # open now; the "opened" was lost in the overflow
        self.feed(path, ev(0, 3, 0) + SYN + lid(True) + SYN)
        self.wait_for(self.changes, 2)
        time.sleep(0.2)
        self.assertEqual((self.changes, w.closed), ([True, False], False))
        self.assertEqual(self.missing, [])

    def test_the_batch_rule_directly(self):
        D, reads = posture.DROPPED, []
        posture.lid_state = lambda fd: reads.append(fd) or self.initial
        w = posture.LidWatcher(self.changed, self.missing.append)
        w.closed = True
        self.initial = False
        self.assertTrue(w._take(7, [D, True]))
        self.assertEqual((self.changes, reads, w.closed), ([False], [7], False), "one re-read, reported; the True ignored")
        self.changes.clear()
        self.assertTrue(w._take(7, [True, D, False, True]))
        self.assertEqual((self.changes, w.closed), ([True, False], False), "items before the drop still count, in order")
        self.changes.clear()
        self.assertTrue(w._take(7, [True, False]))
        self.assertEqual((self.changes, len(reads)), ([True, False], 2), "no drop: no re-read")

    def test_a_failed_re_read_after_a_drop_counts_as_a_lost_switch(self):
        """R2: the re-read fails while the last state was closed. The watcher must not keep "closed" in force with no way
        to learn of an opening: it says the switch is lost (the shell then lights the screens) and ends."""
        self.initial = True
        path = self.device()
        w = self.watcher(path).start()
        self.wait_for(self.changes, 1)
        self.initial = None
        self.feed(path, ev(0, 3, 0) + SYN)
        self.wait_for(self.missing, 1)
        w.thread.join(2.0)
        self.assertFalse(w.thread.is_alive())
        self.assertEqual(self.changes, [True])
        self.assertIn("dropped", self.missing[0])

    def test_a_watcher_started_again_reports_the_state_now(self):
        path = self.device()
        first = self.watcher(path).start()
        self.wait_for(self.changes, 1)
        first.stop()
        self.initial = True                     # it closed while nothing watched
        self.watcher(path).start()
        self.wait_for(self.changes, 2)
        self.assertEqual(self.changes, [False, True], "read with EVIOCGSW at the start, not remembered")

    def test_a_missing_device_is_told_once_and_the_thread_ends(self):
        t = FakeSysfs({"input1": ("Surface Duo Lid Wakeup\n", ["event1"])})
        self.addCleanup(t.close)
        w = self.watcher(None, sys_input=str(t.root)).start()
        w.thread.join(2.0)
        self.assertFalse(w.thread.is_alive())
        self.assertEqual(len(self.missing), 1, self.missing)
        self.assertIn("Surface Duo Lid", self.missing[0])
        self.assertEqual(self.changes, [])

    def test_a_device_that_cannot_be_opened_is_told_once(self):
        w = self.watcher(os.path.join(tempfile.gettempdir(), "no-such-opos-device", "event5")).start()
        w.thread.join(2.0)
        self.assertFalse(w.thread.is_alive())
        self.assertEqual(len(self.missing), 1)
        self.assertIn("cannot open", self.missing[0])

    def test_stop_ends_the_thread_quickly(self):
        w = posture.LidWatcher(self.changed, self.missing.append, path=self.device()).start()   # the real 1 s poll
        self.wait_for(self.changes, 1)
        began = time.monotonic()
        w.stop()
        self.assertFalse(w.thread.is_alive())
        self.assertLess(time.monotonic() - began, 1.6)

    def test_a_failing_callback_does_not_end_the_watcher(self):
        path = self.device()
        calls = []

        def bad(closed):
            calls.append(closed)
            raise RuntimeError("boom")
        w = posture.LidWatcher(bad, self.missing.append, path=path, poll_s=0.05)
        self.addCleanup(w.stop)
        with contextlib.redirect_stderr(io.StringIO()):
            w.start()
            self.feed(path, lid(True) + SYN)
            end = time.monotonic() + 3
            while len(calls) < 2 and time.monotonic() < end:
                time.sleep(0.01)
            self.assertTrue(w.thread.is_alive())
        self.assertEqual(calls, [False, True])

    @unittest.skipIf(sys.platform == "win32", "select works only on sockets on Windows")
    def test_a_pipe_with_the_real_select(self):
        self.restore()
        posture.lid_state = lambda fd: False
        r, wfd = os.pipe()
        self.addCleanup(os.close, wfd)
        path = "/dev/fd/%d" % r
        if not os.path.exists(path):
            os.close(r)
            self.skipTest("no /dev/fd here")
        w = self.watcher(path).start()
        self.wait_for(self.changes, 1)
        os.write(wfd, lid(True) + SYN)
        self.wait_for(self.changes, 2)
        os.write(wfd, lid(True) + SYN + lid(False)[:3])
        time.sleep(0.15)
        os.write(wfd, lid(False)[3:] + SYN)
        self.wait_for(self.changes, 3)
        w.stop()
        os.close(r)
        self.assertEqual(self.changes, [False, True, False])


class Source(unittest.TestCase):
    def test_no_toolkit_and_fcntl_only_inside_lid_state(self):
        tree = ast.parse(POSTURE.read_text(encoding="utf-8"))
        top = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
        top |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(top & {"gi", "gtk", "fcntl"}, top)
        everywhere = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertNotIn("gi", everywhere)

    def test_the_device_is_opened_read_only_and_never_written(self):
        text = POSTURE.read_text(encoding="utf-8")
        self.assertIn("os.O_RDONLY", text)
        self.assertIn('"O_NONBLOCK"', text)
        for bad in ("O_RDWR", "O_WRONLY", "os.write", "/sys/kernel/debug", "EVIOCGRAB"):
            self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
