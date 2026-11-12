# SPDX-License-Identifier: GPL-3.0-or-later
"""Lease/input tests and production fake-tree wiring; no phone or sleep."""
import configparser
import io
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opos import powerkey as pk

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging/duo-os"


def event(stamp, value, kind=pk.EV_KEY, code=pk.KEY_POWER):
    return pk.InputEvent(*divmod(stamp, 1_000_000), kind, code, value)


def wire(stamp, value):
    return pk.INPUT_EVENT.pack(*event(stamp, value))


class Parsing(unittest.TestCase):
    def test_64_bit_time_fields_and_signed_value(self):
        fields = (2**34 + 7, 654321, 1, 116, -1)
        raw = struct.pack("=qqHHi", *fields)
        self.assertEqual(len(raw), 24)
        self.assertEqual(pk.INPUT_EVENT.size, 24)
        self.assertEqual(pk.parse_event(raw), fields)

    def test_short_or_extra_bytes_are_rejected(self):
        for size in (0, 16, 23, 25, 48):
            with self.subTest(size=size), self.assertRaises(struct.error):
                pk.parse_event(bytes(size))


class Presses(unittest.TestCase):
    def press(self, state, start, end):
        state, action = pk.press_step(state, event(start, 1))
        self.assertIsNone(action)
        return pk.press_step(state, event(end, 0))

    def test_short_press_toggles_on_release_only(self):
        state, action = self.press(pk.PressState(), 0, 100_000)
        self.assertEqual(action, "short")
        self.assertEqual(state, pk.PressState(None, 100_000))
        self.assertEqual(self.press(state, 500_000, 600_000)[1], "short")

    def test_exact_short_limit_and_long_hold(self):
        for duration, expected in ((0, "short"), (1_500_000, "short"),
                                   (1_500_001, "light"), (10_000_000, "light")):
            with self.subTest(duration=duration):
                state, action = self.press(pk.PressState(), 0, duration)
                self.assertEqual(action, expected)
                self.assertIsNone(state.pressed_at)

    def test_repeat_does_not_shorten_or_renew_hold(self):
        state = pk.PressState(0)
        self.assertEqual(pk.press_step(state, event(1_400_000, 2)), (state, None))
        self.assertEqual(pk.press_step(state, event(1_600_000, 0))[1], "light")

    def test_duplicate_down_cancels_sequence_until_release(self):
        state, action = pk.press_step(pk.PressState(0), event(100_000, 1))
        self.assertEqual(action, "light")
        self.assertEqual(state, pk.PressState(0, None, True))
        self.assertIsNone(pk.press_step(state, event(200_000, 0))[1])

    def test_repeat_without_press_and_stray_release_do_nothing(self):
        state = pk.PressState()
        for value in (2, 0, 0):
            state, action = pk.press_step(state, event(100_000, value))
            self.assertIsNone(action)
            self.assertEqual(state, pk.PressState())

    def test_repeat_during_short_press_still_allows_one_toggle(self):
        state = pk.PressState(0)
        state, action = pk.press_step(state, event(100_000, 2))
        self.assertIsNone(action)
        state, action = pk.press_step(state, event(200_000, 0))
        self.assertEqual(action, "short")
        self.assertIsNone(pk.press_step(state, event(200_000, 0))[1])

    def test_debounce_uses_previous_toggle_not_ignored_release(self):
        state, _ = self.press(pk.PressState(), 0, 100_000)
        state, action = self.press(state, 200_000, 399_999)
        self.assertIsNone(action)
        self.assertEqual(state.last_toggle, 100_000)
        self.assertEqual(self.press(state, 400_000, 400_000)[1], "short")

    def test_all_other_key_codes_including_114_are_ignored(self):
        held = pk.PressState(0)
        for code in range(0x10000):
            if code != pk.KEY_POWER:
                for value in (0, 1, 2):
                    self.assertEqual(pk.press_step(held, event(10, value, code=code)), (held, None))
        self.assertEqual(pk.press_step(held, event(20, -1)), (held, None))
        self.assertEqual(pk.press_step(held, event(20, 0, kind=7)), (held, None))

    def test_backward_timestamp_lights_instead_of_toggle(self):
        self.assertEqual(self.press(pk.PressState(), 1_000_000, 900_000)[1], "light")

    def test_expiry_is_strict_and_only_happens_once(self):
        state = pk.PressState(0)
        self.assertEqual(pk.expire_press(state, pk.SHORT_PRESS_US), (state, False))
        state, light = pk.expire_press(state, pk.SHORT_PRESS_US + 1)
        self.assertTrue(light)
        self.assertTrue(state.cancelled)
        self.assertEqual(pk.expire_press(state, 100_000_000), (state, False))


class FakePanels:
    def __init__(self, values=(0, 0)):
        self.values = list(values)
        self.present = [True, True]
        self.writable = [True, True]
        self.readable = [True, True]
        self.writes = []
        self.passes = []
        self.reads = 0
        self.before_write = lambda index, value: None
        self.during_write = lambda index, value: None
        self.after_write = lambda index, value: None
        self.after_read = lambda: None

    def read(self):
        self.reads += 1
        result = tuple(value if present and readable else None
                       for value, present, readable in zip(self.values, self.present, self.readable))
        self.after_read()
        return result

    def write(self, value):
        self.passes.append(value)
        success = True
        for index in range(2):
            self.before_write(index, value)
            self.writes.append((index, value))
            if self.present[index] and self.writable[index]:
                self.values[index] = value
            else:
                success = False
            self.during_write(index, value)
            self.after_write(index, value)
        return success


class Lease(unittest.TestCase):
    def setUp(self):
        self.panels = FakePanels()
        self.now = 0
        self.probe = mock.Mock(return_value=True)
        self.log = mock.Mock()
        self.daemon = pk.PowerKey(self.panels.read, self.panels.write, self.probe,
                                  lambda: self.now, self.log)
        self.daemon.start()
        self.panels.writes.clear()
        self.panels.passes.clear()

    def short(self, start=0):
        self.now = start / 1_000_000
        self.daemon.event(event(start, 1))
        self.now += 0.1
        self.daemon.event(event(start + 100_000, 0))

    def test_clean_short_press_blanks_once_then_lights_without_probe(self):
        self.short()
        self.assertEqual(self.panels.values, [4, 4])
        self.assertEqual(self.panels.passes, [4])
        self.probe.assert_called_once_with()
        self.probe.reset_mock()
        self.short(500_000)
        self.assertEqual(self.panels.values, [0, 0])
        self.assertEqual(self.panels.passes, [4, 0])
        self.probe.assert_not_called()

    def test_shell_inactive_between_probe_and_write_lights_by_next_tick(self):
        def probe():
            self.probe.return_value = False  # A successful answer is already stale.
            return True

        self.probe.side_effect = probe
        self.short()
        self.assertEqual(self.panels.values, [4, 4])
        self.probe.side_effect = None
        self.now += 1
        self.daemon.tick()
        self.assertEqual(self.panels.values, [0, 0])
        self.assertEqual(self.panels.passes, [4, 0])

    def test_inactive_or_failing_probe_denies_blank(self):
        for failure in (False, None, RuntimeError("probe"), subprocess.TimeoutExpired("probe", .5)):
            with self.subTest(failure=failure):
                self.panels.values[:] = [0, 0]
                self.probe.side_effect = failure if isinstance(failure, Exception) else None
                self.probe.return_value = failure
                self.short(int(self.now) * 1_000_000 + 1_000_000)
                self.assertEqual(self.panels.values, [0, 0])
                self.assertNotIn(4, self.panels.passes)

    def test_tick_rechecks_shell_while_blank_but_not_while_lit(self):
        self.short()
        self.probe.reset_mock()
        self.assertEqual(self.daemon.tick(), 1)
        self.probe.assert_called_once_with()
        self.probe.return_value = False
        self.daemon.tick()
        self.assertEqual(self.panels.values, [0, 0])
        self.probe.reset_mock()
        self.daemon.tick()
        self.probe.assert_not_called()

    def test_unfold_during_and_after_each_blank_write_never_reblanks(self):
        for position in ("during", "after"):
            for index in range(2):
                with self.subTest(position=position, index=index):
                    self.setUp()

                    def unfold(current, value):
                        if current == index and value == 4:
                            self.panels.values[:] = [0, 0]

                    setattr(self.panels, f"{position}_write", unfold)
                    self.short()
                    self.assertEqual(self.panels.values, [0, 0])
                    self.now += 1
                    self.daemon.tick()
                    self.assertEqual(self.panels.values, [0, 0])
                    self.assertEqual(self.panels.passes.count(4), 1)
                    self.assertNotIn(4, self.panels.passes[1:])

    def test_unfold_after_successful_readback_is_observed_on_next_tick(self):
        self.short()
        for index in (0, 1):
            with self.subTest(index=index):
                self.panels.values[:] = [4, 4]
                self.panels.values[index] = 0
                self.daemon.tick()
                self.assertEqual(self.panels.values, [0, 0])
        self.assertEqual(self.panels.passes.count(4), 1)

    def test_lid_lighting_both_is_accepted_without_writes_next_tick(self):
        self.short()
        self.panels.values[:] = [0, 0]
        writes = self.panels.writes.copy()
        self.assertIsNone(self.daemon.tick())
        self.assertEqual(self.panels.writes, writes)
        self.short(500_000)  # Only a new clean press may blank again.
        self.assertEqual(self.panels.values, [4, 4])

    def test_any_uncertain_blank_readback_lights_instead_of_reapplying_blank(self):
        for result in ((0, 4), (4, 0), (0, 0), (None, 4), (4, None), (9, 4)):
            with self.subTest(result=result):
                self.setUp()
                read = self.daemon.read
                self.daemon.read = mock.Mock(side_effect=[pk.LIT, result, pk.LIT])
                self.short()
                self.assertEqual(self.panels.passes, [4, 0])
                self.daemon.read = read
                self.assertEqual(self.panels.values, [0, 0])

    def test_partial_blank_write_error_lights_both_even_if_readback_looks_blank(self):
        write = self.panels.write
        self.daemon.write = lambda value: write(value) and value != 4
        self.short()
        self.assertEqual(self.panels.passes, [4, 0])
        self.assertEqual(self.panels.values, [0, 0])

    def test_tick_mixed_invalid_missing_and_failed_read_light_both(self):
        for values in ((0, 4), (4, 0), (1, 1), (None, 0), (4, None)):
            with self.subTest(values=values):
                self.setUp()
                self.panels.values[:] = values
                self.daemon.tick()
                self.assertEqual(self.panels.passes, [0])
                self.assertEqual(self.panels.values, [0, 0])
                self.probe.assert_not_called()

    def test_recovery_pending_blocks_blank_even_when_panels_now_look_lit(self):
        self.daemon.recovery_pending = True
        self.short()
        self.assertEqual(self.panels.passes, [0])
        self.assertFalse(self.daemon.recovery_pending)
        self.probe.assert_not_called()

    def test_missing_node_retries_until_both_expected_nodes_present_and_lit(self):
        self.panels.present[0] = False
        self.panels.values[:] = [4, 4]
        self.daemon.tick()
        self.assertTrue(self.daemon.recovery_pending)
        self.assertEqual(self.panels.values[1], 0)
        for _ in range(3):
            self.assertEqual(self.daemon.tick(), 1)
        self.log.assert_called_once_with("unblank recovery pending")
        self.panels.present[0] = True
        self.assertIsNone(self.daemon.tick())
        self.assertEqual(self.panels.values, [0, 0])
        self.assertFalse(self.daemon.recovery_pending)
        self.assertEqual(self.log.call_args_list, [mock.call("unblank recovery pending"),
                                                 mock.call("unblank recovery confirmed")])

    def test_failed_light_readback_keeps_retrying_and_logs_only_transitions(self):
        self.panels.writable[0] = False
        self.panels.values[:] = [4, 4]
        self.daemon.light()
        for _ in range(4):
            self.assertEqual(self.daemon.tick(), 1)
        self.log.assert_called_once_with("unblank recovery pending")
        self.panels.writable[0] = True
        self.daemon.tick()
        self.assertEqual(self.panels.values, [0, 0])
        self.assertEqual(self.log.call_count, 2)
        writes = self.panels.writes.copy()
        self.assertIsNone(self.daemon.tick())
        self.assertEqual(self.panels.writes, writes)

    def test_lost_release_then_new_press_lights_and_does_not_toggle_on_release(self):
        self.panels.values[:] = [4, 4]
        self.daemon.event(event(0, 1))
        self.daemon.event(event(500_000, 1))
        self.assertEqual(self.panels.values, [0, 0])
        self.daemon.event(event(600_000, 0))
        self.assertEqual(self.panels.passes, [0])
        self.short(1_000_000)
        self.assertEqual(self.panels.passes, [0, 4])

    def test_real_long_hold_lights_once_then_no_toggle_or_light_storm(self):
        for initial in ((0, 0), (4, 4)):
            with self.subTest(initial=initial):
                self.setUp()
                self.panels.values[:] = initial
                self.daemon.event(event(0, 1))
                self.now = 1
                self.assertEqual(self.daemon.tick(), 1)
                self.daemon.event(event(1_000_000, 2))
                self.now = 2
                self.daemon.tick()
                self.assertEqual(self.panels.passes, [0])
                for stamp in (3, 4, 5, 20):
                    self.now = stamp
                    self.daemon.event(event(stamp * 1_000_000, 2))
                    self.assertIsNone(self.daemon.tick())
                self.daemon.event(event(21_000_000, 0))
                self.assertEqual(self.panels.passes, [0])
                self.assertEqual(self.panels.values, [0, 0])

    def test_expired_lost_release_then_new_down_also_lights_without_toggle(self):
        self.daemon.event(event(0, 1))
        self.now = 2
        self.daemon.tick()
        self.panels.values[:] = [4, 4]
        self.daemon.event(event(3_000_000, 1))
        self.assertEqual(self.panels.values, [0, 0])
        self.daemon.event(event(3_100_000, 0))
        self.assertNotIn(4, self.panels.passes)

    def test_syn_dropped_lights_and_discards_through_report(self):
        self.short()
        self.daemon.event(event(500_000, 1))
        self.daemon.event(event(510_000, 0, kind=0, code=3))
        self.assertEqual(self.panels.values, [0, 0])
        self.assertEqual(self.daemon.tick(), 1)  # Waiting for SYN_REPORT is pending input.
        for item in (event(520_000, 1), event(530_000, 0), event(540_000, 0, kind=0, code=0),
                     event(600_000, 0)):
            self.daemon.event(item)
        self.assertEqual(self.panels.passes, [4, 0])
        self.short(1_000_000)
        self.assertEqual(self.panels.passes, [4, 0, 4])

    def test_start_stop_and_input_loss_light_without_probe(self):
        for action in (self.daemon.start, self.daemon.stop, self.daemon.input_lost):
            self.panels.values[:] = [4, 4]
            action()
            self.assertEqual(self.panels.values, [0, 0])
        self.probe.assert_not_called()

    def test_idle_tick_only_reads_and_chooses_infinite_select_timeout(self):
        reads = self.panels.reads
        self.assertIsNone(self.daemon.tick())
        self.assertGreater(self.panels.reads, reads)
        self.assertEqual(self.panels.writes, [])
        self.probe.assert_not_called()
        self.daemon.event(event(0, 1))
        self.assertEqual(self.daemon.tick(), 1)

    def test_code_114_does_not_light_blank_or_affect_power_press(self):
        self.panels.values[:] = [4, 4]
        for value in (1, 2, 0):
            self.daemon.event(event(0, value, code=114))
        self.assertEqual(self.panels.writes, [])
        self.assertEqual(self.daemon.press, pk.PressState())
        self.daemon.event(event(0, 1))
        self.daemon.event(event(10, 1, code=114))
        self.daemon.event(event(20, 0, code=114))
        self.daemon.event(event(100_000, 0))
        self.assertEqual(self.panels.passes, [0])


class FakeTree(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="ascii")
        return path

    def panels(self):
        paths = []
        for name in pk.PANELS:
            paths.append(self.put(f"sys/class/backlight/{name}/bl_power", "0\n"))
            self.put(f"sys/class/backlight/{name}/brightness", "90\n")
        return paths


class DeviceLookup(FakeTree):
    def test_exact_name_and_dev_path_on_fake_sysfs(self):
        for number, name in ((0, "gpio-keys"), (1, "qpnp_pon-extra"), (2, " qpnp_pon"), (7, "qpnp_pon")):
            self.put(f"sys/class/input/event{number}/device/name", name + "\n")
        self.assertEqual(pk.find_device(self.root), self.root / "dev/input/event7")

    def test_missing_name_and_near_matches_are_skipped(self):
        (self.root / "sys/class/input/event0/device").mkdir(parents=True)
        self.put("sys/class/input/event8/device/name", "qpnp_pon \n")
        with self.assertRaises(FileNotFoundError):
            pk.find_device(self.root)

    def test_reopen_does_fresh_lookup_nonblocking_and_monotonic_clock(self):
        paths = [mock.Mock(), mock.Mock()]
        streams = [mock.Mock(), mock.Mock()]
        for path, stream in zip(paths, streams):
            path.open.return_value = stream
        with mock.patch.object(pk, "find_device", side_effect=paths) as lookup, \
                mock.patch.object(pk.os, "set_blocking") as nonblocking, \
                mock.patch.object(pk.fcntl, "ioctl") as ioctl:
            for stream in streams:
                self.assertIs(pk.open_input(self.root), stream)
        self.assertEqual(lookup.call_args_list, [mock.call(self.root)] * 2)
        for stream in streams:
            self.assertIn(mock.call(stream.fileno(), False), nonblocking.call_args_list)
            self.assertIn(mock.call(stream, pk.EVIOCSCLOCKID, struct.pack("=i", pk.time.CLOCK_MONOTONIC)),
                          ioctl.call_args_list)

    def test_clock_failure_closes_device_and_does_not_use_wall_clock(self):
        path = mock.Mock()
        with mock.patch.object(pk, "find_device", return_value=path), \
                mock.patch.object(pk.os, "set_blocking"), \
                mock.patch.object(pk.fcntl, "ioctl", side_effect=OSError("clock")), \
                self.assertRaises(OSError):
            pk.open_input(self.root)
        path.open.return_value.close.assert_called_once_with()


class PanelFiles(FakeTree):
    def test_one_write_pass_never_changes_brightness(self):
        paths = self.panels()
        for value in (4, 0):
            self.assertTrue(pk.write_panel_power(self.root, value))
            self.assertEqual(pk.read_panels(self.root), (value, value))
        self.assertEqual([p.with_name("brightness").read_text() for p in paths], ["90\n"] * 2)

    def test_missing_nodes_are_unknown_not_created(self):
        self.assertEqual(pk.read_panels(self.root), (None, None))
        self.assertFalse(pk.write_panel_power(self.root, 0))
        self.assertFalse((self.root / "sys").exists())

    def test_read_and_write_failure_still_attempt_other_panel(self):
        paths = self.panels()
        paths[0].unlink()
        paths[0].mkdir()
        paths[1].write_text("4\n")
        self.assertEqual(pk.read_panels(self.root), (None, 4))
        self.assertFalse(pk.write_panel_power(self.root, 0))
        self.assertEqual(paths[1].read_text(), "0\n")
        paths[0].rmdir()
        paths[0].write_text("garbage\n")
        self.assertEqual(pk.read_panels(self.root), (None, 0))


class ProductionLoop(FakeTree):
    class StopLoop(Exception):
        pass

    def test_default_controller_short_press_and_recovery_write_panel_files(self):
        paths = self.panels()
        probe = mock.Mock(return_value=True)
        stream = mock.Mock()
        open_device = mock.Mock(return_value=stream)

        def press(size):
            stamp = int(pk.time.monotonic() * 1_000_000)
            return wire(stamp, 1) + wire(stamp + 100_000, 0)

        stream.read.side_effect = press
        calls = 0

        def wait(read, write, error, timeout):
            nonlocal calls
            calls += 1
            self.assertEqual(read, [stream])
            if calls == 1:
                self.assertEqual([p.read_text() for p in paths], ["0\n"] * 2)
                self.assertIsNone(timeout)
                return read, [], []  # A complete short press reaches the real controller.
            if calls == 2:
                self.assertEqual([p.read_text() for p in paths], ["4\n"] * 2)
                self.assertEqual(timeout, 1)
                probe.return_value = False  # Revoke the blank lease without another key.
                return [], [], []
            self.assertEqual(calls, 3)
            self.assertEqual([p.read_text() for p in paths], ["0\n"] * 2)
            self.assertEqual([p.with_name("brightness").read_text() for p in paths], ["90\n"] * 2)
            self.assertIsNone(timeout)
            raise self.StopLoop  # Assert recovery before finally-stop can light panels.

        with self.assertRaises(self.StopLoop):
            pk.run(self.root, is_active=probe, wait=wait, open_device=open_device)
        self.assertEqual(calls, 3)
        open_device.assert_called_once_with(self.root)
        stream.read.assert_called_once_with(pk.INPUT_EVENT.size * 64)
        stream.close.assert_called_once_with()

    def test_default_controller_start_retries_unavailable_panels_until_lit(self):
        for failure in ("unwritable", "missing"):
            with self.subTest(failure=failure):
                paths = self.panels()
                for path in paths:
                    path.write_text("4\n")
                    if failure == "missing":
                        path.unlink()
                blocked = True
                original_open = Path.open

                def open_panel(path, mode="r", *args, **kwargs):
                    # Readable blank files reject writes independently of host/root permissions.
                    if blocked and failure == "unwritable" and path in paths and mode == "r+":
                        raise PermissionError("panel temporarily unwritable")
                    return original_open(path, mode, *args, **kwargs)

                stream = mock.Mock()
                open_device = mock.Mock(return_value=stream)
                probe = mock.Mock(return_value=True)
                log = io.StringIO()
                calls = 0

                def wait(read, write, error, timeout):
                    nonlocal calls, blocked
                    calls += 1
                    self.assertEqual(read, [stream])
                    if calls <= 2:
                        self.assertEqual(timeout, 1)
                        self.assertEqual(pk.read_panels(self.root),
                                         pk.BLANK if failure == "unwritable" else (None, None))
                        self.assertIn("unblank recovery pending", log.getvalue())
                        if calls == 2:
                            blocked = False
                            if failure == "missing":
                                for path in paths:
                                    path.write_text("4\n")
                        return [], [], []  # Retry through select with no key event.
                    self.assertEqual(calls, 3)
                    self.assertEqual([p.read_text() for p in paths], ["0\n"] * 2)
                    self.assertIn("unblank recovery confirmed", log.getvalue())
                    self.assertIsNone(timeout)
                    raise self.StopLoop

                with mock.patch.object(Path, "open", open_panel), \
                        mock.patch.object(pk.sys, "stderr", log), self.assertRaises(self.StopLoop):
                    pk.run(self.root, is_active=probe, wait=wait, open_device=open_device)
                self.assertEqual(calls, 3)
                open_device.assert_called_once_with(self.root)
                stream.read.assert_not_called()
                stream.close.assert_called_once_with()
                probe.assert_not_called()  # Pending recovery never needs a shell probe.


class ShellProbe(unittest.TestCase):
    def test_bounded_exact_systemctl_command(self):
        with mock.patch.object(pk.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(pk.shell_active())
        run.assert_called_once_with(
            ["systemctl", "is-active", "--quiet", "opos.service"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=0.5, check=False)

    def test_nonzero_error_and_timeout_are_inactive_and_recover_blank(self):
        for failure in (mock.Mock(returncode=1), mock.Mock(returncode=3), FileNotFoundError("systemctl"),
                        subprocess.TimeoutExpired("systemctl", 0.5)):
            with self.subTest(failure=failure):
                panels = FakePanels((4, 4))
                daemon = pk.PowerKey(panels.read, panels.write, pk.shell_active)
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with mock.patch.object(pk.subprocess, "run", **kwargs):
                    daemon.tick()
                self.assertEqual(panels.values, [0, 0])
                self.assertEqual(panels.passes, [0])


class InputLoop(unittest.TestCase):
    class StopLoop(Exception):
        pass

    def setUp(self):
        self.panels = FakePanels()
        self.now = 0
        self.probe = mock.Mock(return_value=True)
        self.daemon = pk.PowerKey(self.panels.read, self.panels.write, self.probe, lambda: self.now, mock.Mock())
        self.stream = mock.Mock()
        self.open = mock.Mock(return_value=self.stream)
        self.enterContext(mock.patch.object(pk.sys, "stderr", io.StringIO()))

    def run_loop(self, wait):
        with self.assertRaises(self.StopLoop):
            pk.run("injected-root", controller=self.daemon, wait=wait, open_device=self.open)

    def test_idle_select_is_infinite_and_no_write_or_probe_on_idle_tick(self):
        def wait(read, write, error, timeout):
            self.assertEqual(read, [self.stream])
            self.assertIsNone(timeout)
            self.assertEqual(self.panels.passes, [0])  # Startup only.
            self.probe.assert_not_called()
            raise self.StopLoop
        self.run_loop(wait)
        self.assertEqual(self.panels.passes, [0, 0])  # Finally-stop lights too.

    def test_late_node_after_input_open_is_lit_by_tick_without_key_event(self):
        self.panels.present[0] = False
        self.panels.values[:] = [4, 4]
        calls = 0
        def wait(read, write, error, timeout):
            nonlocal calls
            self.assertTrue(self.open.called)
            calls += 1
            if calls == 1:
                self.assertEqual(timeout, 1)
                self.assertTrue(self.daemon.recovery_pending)
                self.panels.present[0] = True
                self.now += 1
                return [], [], []
            self.assertEqual(self.panels.values, [0, 0])
            self.assertFalse(self.daemon.recovery_pending)
            self.assertIsNone(timeout)
            raise self.StopLoop
        self.run_loop(wait)
        self.assertEqual(self.panels.values, [0, 0])

    def test_write_becomes_possible_after_input_open_then_tick_recovers(self):
        self.panels.values[:] = [4, 4]
        self.panels.writable[0] = False
        calls = 0
        def wait(read, write, error, timeout):
            nonlocal calls
            self.assertTrue(self.open.called)
            calls += 1
            if calls == 1:
                self.assertEqual(timeout, 1)
                self.assertTrue(self.daemon.recovery_pending)
                self.panels.writable[0] = True
                self.now += 1
                return [], [], []
            self.assertIsNone(timeout)
            self.assertEqual(self.panels.values, [0, 0])
            self.assertFalse(self.daemon.recovery_pending)
            raise self.StopLoop
        self.run_loop(wait)

    def test_partial_event_never_blocks_tick_and_assembles_later(self):
        pieces = [wire(0, 1)[:5], wire(0, 1)[5:], wire(100_000, 0)]
        self.stream.read.side_effect = pieces
        calls = 0
        def wait(read, write, error, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertIsNone(timeout)
            if calls == 3:
                self.assertEqual(timeout, 1)  # Pending down, even though both lit.
            if calls == 4:
                self.assertEqual(self.panels.values, [4, 4])
                self.assertEqual(timeout, 1)
                raise self.StopLoop
            self.now = .1 if calls == 3 else 0
            return read, [], []
        self.run_loop(wait)
        self.assertEqual(self.stream.read.call_count, 3)  # One read per readiness, no assembly loop.

    def test_repeat_flood_cannot_postpone_tick_or_expiry(self):
        self.stream.read.side_effect = [wire(0, 1), wire(1_000_000, 2), wire(2_000_000, 2)]
        calls = 0
        def wait(read, write, error, timeout):
            nonlocal calls
            calls += 1
            if calls == 4:
                self.assertTrue(self.daemon.press.cancelled)
                self.assertEqual(self.panels.passes, [0, 0])  # Start and one expiry.
                self.assertIsNone(timeout)
                raise self.StopLoop
            self.now = calls - 1
            return read, [], []
        self.run_loop(wait)

    def test_tick_runs_between_buffered_events_before_processing_more_input(self):
        def stale_probe():
            self.probe.side_effect = None
            self.probe.return_value = False
            return True

        self.probe.side_effect = stale_probe
        packed = wire(0, 1) + wire(100_000, 0) + pk.INPUT_EVENT.pack(*event(200_000, 1, code=114))
        self.stream.read.return_value = packed
        handle = self.daemon.event
        def check_event(item):
            if item.code == 114:
                # The shell became inactive at the blank probe. Recover before
                # another buffered event, without waiting for another select.
                self.assertEqual(self.panels.values, [0, 0])
                self.assertEqual(self.panels.passes, [0, 4, 0])
            handle(item)
        self.daemon.event = check_event
        calls = 0
        def wait(read, write, error, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return read, [], []
            self.assertEqual(self.probe.call_count, 2)
            raise self.StopLoop
        self.run_loop(wait)

    def test_would_block_or_incomplete_read_returns_through_tick(self):
        for chunk in (None, BlockingIOError("try again")):
            with self.subTest(chunk=chunk):
                self.setUp()
                self.stream.read.side_effect = [chunk, wire(0, 1)]
                calls = 0
                def wait(read, write, error, timeout):
                    nonlocal calls
                    calls += 1
                    if calls == 3:
                        self.assertEqual(timeout, 1)
                        self.assertEqual(self.daemon.press.pressed_at, 0)
                        raise self.StopLoop
                    return read, [], []
                self.run_loop(wait)

    def test_open_would_block_is_input_loss_with_finite_retry(self):
        self.open.side_effect = BlockingIOError("open unavailable")
        def wait(read, write, error, timeout):
            self.assertEqual(read, [])
            self.assertEqual(timeout, 1)
            self.assertEqual(self.panels.values, [0, 0])
            raise self.StopLoop
        with mock.patch.object(self.daemon, "input_lost", wraps=self.daemon.input_lost) as lost:
            self.run_loop(wait)
        lost.assert_called_once_with()

    def test_device_vanishes_while_blank_lights_before_one_second_retry(self):
        for failure in (b"", OSError("disconnected")):
            with self.subTest(failure=failure):
                self.setUp()
                self.stream.read.side_effect = [wire(0, 1) + wire(100_000, 0),
                                                failure if isinstance(failure, Exception) else failure]
                calls = 0
                def wait(read, write, error, timeout):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return read, [], []
                    if calls == 2:
                        self.assertEqual(self.panels.values, [4, 4])
                        self.assertEqual(timeout, 1)
                        return read, [], []
                    self.assertEqual(read, [])
                    self.assertEqual(timeout, 1)
                    self.assertEqual(self.panels.values, [0, 0])
                    self.assertIsNone(self.daemon.press.pressed_at)
                    raise self.StopLoop
                self.run_loop(wait)

    def test_lookup_open_or_ioctl_failure_lights_and_retries_without_sleep(self):
        for error in (FileNotFoundError("lookup"), OSError("open"), OSError("clock ioctl")):
            with self.subTest(error=error):
                self.open.side_effect = error
                def wait(read, write, exceptional, timeout):
                    self.assertEqual(read, [])
                    self.assertEqual(timeout, 1)
                    self.assertEqual(self.panels.values, [0, 0])
                    self.assertIn(0, self.panels.passes)
                    raise self.StopLoop
                with mock.patch.object(self.daemon, "input_lost", wraps=self.daemon.input_lost) as lost:
                    self.run_loop(wait)
                lost.assert_called_once_with()
                self.open.reset_mock(side_effect=True)

    def test_reopen_discards_partial_bytes_and_old_down(self):
        streams = [mock.Mock(), mock.Mock()]
        streams[0].read.side_effect = [wire(0, 1) + wire(100, 0)[:5], b""]
        streams[1].read.return_value = wire(100_000, 0)
        self.open.side_effect = streams
        calls = 0
        def wait(read, write, error, timeout):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.assertEqual(read, [])
                return [], [], []
            if calls == 5:
                self.assertEqual(self.open.call_count, 2)
                self.assertNotIn(4, self.panels.passes)
                self.assertIsNone(self.daemon.press.pressed_at)
                raise self.StopLoop
            return read, [], []
        self.run_loop(wait)

    def test_sigterm_unwinds_into_final_light_attempt(self):
        def wait(*args):
            self.panels.values[:] = [4, 4]
            pk.terminate(pk.signal.SIGTERM, None)
        with self.assertRaises(SystemExit):
            pk.run(controller=self.daemon, wait=wait, open_device=self.open)
        self.assertEqual(self.panels.values, [0, 0])
        self.assertEqual(self.panels.passes, [0, 0])


class Helpers(FakeTree):
    def helper(self, env=None):
        no_sleep = self.root / "no-sleep"
        no_sleep.mkdir(exist_ok=True)
        (no_sleep / "sleep").write_text("#!/bin/sh\nexit 0\n")
        (no_sleep / "sleep").chmod(0o755)
        helper_env = {**os.environ, "OPOS_SYS_ROOT": str(self.root), **(env or {})}
        # A scenario's injected sleep wins; otherwise helper retries never sleep.
        first = helper_env["PATH"].split(":")[0] if env and "PATH" in env else str(no_sleep)
        helper_env["PATH"] = f"{first}:{no_sleep}:{helper_env['PATH']}"
        return subprocess.run(["sh", str(DUO / "opos-unblank")],
                              env=helper_env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)

    def test_unblank_helper_writes_zero_to_both_and_leaves_brightness(self):
        paths = self.panels()
        for path in paths:
            path.write_text("4\n")
        result = self.helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([path.read_text() for path in paths], ["0\n", "0\n"])
        self.assertEqual([p.with_name("brightness").read_text() for p in paths], ["90\n", "90\n"])

    def test_unblank_helper_skips_missing_panels_and_keeps_going_after_error(self):
        self.assertEqual(self.helper().returncode, 0)
        paths = self.panels()
        paths[0].unlink()
        paths[0].mkdir()
        paths[1].write_text("4\n")
        result = self.helper()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("panel0-backlight", result.stderr)
        self.assertEqual(paths[1].read_text(), "0\n")

    def test_helper_logs_absent_nodes_and_accepts_delayed_appearance(self):
        paths = self.panels()
        paths[0].unlink()
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        sleep = fake_bin / "sleep"
        sleep.write_text('#!/bin/sh\nprintf "4\\n" > "$OPOS_SYS_ROOT/sys/class/backlight/panel0-backlight/bl_power"\n')
        sleep.chmod(0o755)
        result = self.helper({"PATH": f"{fake_bin}:/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("absent", result.stderr)
        self.assertEqual([p.read_text() for p in paths], ["0\n"] * 2)

    def test_helper_retries_transient_write_failure(self):
        paths = self.panels()
        paths[0].unlink()
        paths[0].mkdir()
        paths[1].write_text("4\n")
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        sleep = fake_bin / "sleep"
        sleep.write_text('#!/bin/sh\nnode="$OPOS_SYS_ROOT/sys/class/backlight/panel0-backlight/bl_power"\n'
                         'rmdir "$node"\nprintf "4\\n" > "$node"\n')
        sleep.chmod(0o755)
        result = self.helper({"PATH": f"{fake_bin}:/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("write failed", result.stderr)
        self.assertEqual([p.read_text() for p in paths], ["0\n"] * 2)

    def test_helper_reconciles_after_lid_interleaves_with_writes(self):
        paths = self.panels()
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        cat = fake_bin / "cat"
        cat.write_text('#!/bin/sh\nif [ ! -e "$OPOS_SYS_ROOT/interleaved" ]; then\n'
                       '  touch "$OPOS_SYS_ROOT/interleaved"\n  printf "4\\n" > "$1"\nfi\n/bin/cat "$@"\n')
        cat.chmod(0o755)
        result = self.helper({"PATH": f"{fake_bin}:/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "interleaved").exists())
        self.assertEqual([p.read_text() for p in paths], ["0\n"] * 2)



class Packaging(FakeTree):
    def block(self, name):
        text = (DUO / name).read_text()
        return re.search(r"^# --- power key: BEGIN[^\n]*\n(.*?)^# --- power key: END", text, re.M | re.S).group(1)

    def install(self):
        self.put("opt/opos/src/opos/powerkey.py", (ROOT / "src/opos/powerkey.py").read_text())
        result = subprocess.run(["sh", "-eu", "-c", 'here=$1; r=$2\n' + self.block("build-image.sh"),
                                 "powerkey-test", str(DUO), str(self.root)], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("opos.service", "opos-fallback.service"):
            shutil.copyfile(DUO / name, self.root / "etc/systemd/system" / name)

    def verify(self):
        prelude = 'r=$1; w=$r/etc/systemd/system; fail=0\ncheck() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n'
        return subprocess.run(["sh", "-c", prelude + self.block("verify-image.sh") + '\nexit "$fail"\n',
                               "powerkey-test", str(self.root)], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=10)

    def test_actual_install_and_verification_blocks(self):
        self.install()
        helper = self.root / "usr/libexec/opos/opos-unblank"
        self.assertEqual(helper.stat().st_mode & 0o777, 0o755)
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("PASS "), 2)
        link = self.root / "etc/systemd/system/opos.service.wants/opos-powerkey.service"
        self.assertEqual(os.readlink(link), "/etc/systemd/system/opos-powerkey.service")
        self.assertFalse((self.root / "etc/systemd/system/multi-user.target.wants/opos-powerkey.service").exists())

    def test_verification_rejects_missing_module_helper_or_enable_link(self):
        self.install()
        for name in ("opt/opos/src/opos/powerkey.py", "usr/libexec/opos/opos-unblank",
                     "etc/systemd/system/opos.service.wants/opos-powerkey.service"):
            with self.subTest(name=name):
                path = self.root / name
                saved = path.with_name(path.name + ".saved")
                path.rename(saved)
                try:
                    self.assertNotEqual(self.verify().returncode, 0)
                finally:
                    saved.rename(path)

    def test_unblank_is_first_shell_prestart_and_root_in_all_units(self):
        for name, expected in (("opos.service", "-+"), ("opos-fallback.service", "-"), ("opos-powerkey.service", "-")):
            lines = (DUO / name).read_text().splitlines()
            prestart = next(line for line in lines if line.startswith("ExecStartPre="))
            self.assertEqual(prestart, f"ExecStartPre={expected}/usr/libexec/opos/opos-unblank")
        service = (DUO / "opos-powerkey.service").read_text()
        self.assertIn("WantedBy=opos.service\n", service)
        self.assertIn("ExecStopPost=-/usr/libexec/opos/opos-unblank\n", service)

    def test_daemon_restart_and_hardening_are_present(self):
        text = (DUO / "opos-powerkey.service").read_text()
        for directive in ("NoNewPrivileges=yes", "ProtectHome=yes", "PrivateTmp=yes", "ProtectSystem=full"):
            self.assertIn(directive + "\n", text)
        self.assertIn("After=opos-powerkey.service\n", (DUO / "opos-fallback.service").read_text())

    def test_daemon_restart_policy_last_effective_values(self):
        # Repeated sections/options use the last assignment, as systemd does for these scalars.
        unit = configparser.ConfigParser(strict=False, interpolation=None)
        unit.optionxform = str
        unit.read_string((DUO / "opos-powerkey.service").read_text())
        for section, key, expected in (("Service", "Restart", "always"),
                                       ("Service", "RestartSec", "3"),
                                       ("Unit", "StartLimitIntervalSec", "0")):
            with self.subTest(section=section, key=key):
                self.assertEqual(unit.get(section, key), expected)

    def test_verification_rejects_later_effective_command_resets_and_replacements(self):
        self.install()
        for unit, key in (("opos-powerkey.service", "ExecStopPost"),
                          ("opos-powerkey.service", "ExecStartPre"),
                          ("opos.service", "ExecStartPre"), ("opos-fallback.service", "ExecStartPre")):
            path = self.root / "etc/systemd/system" / unit
            original = path.read_text()
            for replacement in ("", "/bin/true"):
                with self.subTest(unit=unit, key=key, replacement=replacement):
                    path.write_text(original + f"\n[Service]\n{key}=\n{key}={replacement}\n")
                    self.assertNotEqual(self.verify().returncode, 0)
            path.write_text(original)
        # A reset that restores the effective safety hook remains valid.
        path.write_text(original + "\n[Service]\nExecStartPre=\nExecStartPre=-/usr/libexec/opos/opos-unblank\n")
        self.assertEqual(self.verify().returncode, 0)

    def test_verification_rejects_later_resets_with_systemd_whitespace(self):
        self.install()
        for unit, key in (("opos-powerkey.service", "ExecStopPost"),
                          ("opos-powerkey.service", "ExecStartPre"),
                          ("opos.service", "ExecStartPre"), ("opos-fallback.service", "ExecStartPre")):
            path = self.root / "etc/systemd/system" / unit
            original = path.read_text()
            with self.subTest(unit=unit, key=key):
                path.write_text(original + f"\n[Service]\n  {key} =  \n  {key} = /bin/true  \n")
                self.assertNotEqual(self.verify().returncode, 0)
            path.write_text(original)

    def test_verification_rejects_removed_restart_always(self):
        self.install()
        path = self.root / "etc/systemd/system/opos-powerkey.service"
        path.write_text(path.read_text().replace("Restart=always\n", ""))
        self.assertNotEqual(self.verify().returncode, 0)


if __name__ == "__main__":
    unittest.main()
