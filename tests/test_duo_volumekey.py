# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulated Duo volume-key reader tests; fake input only, no phone or sleeps."""
import configparser
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opos import powerkey, volume, volumekey

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging/duo-os"


def wire(code, value, *, kind=powerkey.EV_KEY):
    return powerkey.INPUT_EVENT.pack(0, 0, kind, code, value)


class FakeStream:
    def __init__(self, *chunks):
        self.chunks = list(chunks)
        self.closed = False

    def read(self, size):
        item = self.chunks.pop(0) if self.chunks else b""
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, event, name):
        path = self.root / "sys/class/input" / event / "device/name"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n", encoding="ascii")
        (self.root / "dev/input" / event).parent.mkdir(parents=True, exist_ok=True)

    def test_named_sources_are_exact_independent_and_renumberable(self):
        self.add("event0", "gpio-keys-extra")
        self.add("event2", "gpio-keys")
        self.add("event3", "qpnp_pon-extra")
        self.add("event7", "qpnp_pon")
        self.assertEqual(volumekey.find_device("gpio-keys", self.root), self.root / "dev/input/event2")
        self.assertEqual(volumekey.find_device("qpnp_pon", self.root), self.root / "dev/input/event7")

        (self.root / "sys/class/input/event2/device/name").unlink()
        self.add("event11", "gpio-keys")
        self.assertEqual(volumekey.find_device("gpio-keys", self.root), self.root / "dev/input/event11")

    def test_missing_or_unreadable_name_is_not_matched(self):
        bad = self.root / "sys/class/input/event1/device/name"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"\xff")
        with self.assertRaises(FileNotFoundError):
            volumekey.find_device("gpio-keys", self.root)

    def test_open_is_nonblocking_and_failure_closes_without_grabbing(self):
        self.add("event2", "gpio-keys")
        path = self.root / "dev/input/event2"
        path.write_bytes(wire(115, 1))
        with volumekey.open_input("gpio-keys", self.root) as stream:
            self.assertFalse(os.get_blocking(stream.fileno()))
            self.assertEqual(stream.read(24), wire(115, 1))
        with mock.patch.object(volumekey, "find_device", return_value=path), \
                mock.patch.object(volumekey.os, "set_blocking", side_effect=OSError("private")), \
                mock.patch.object(Path, "open") as opened:
            stream = opened.return_value
            with self.assertRaises(OSError):
                volumekey.open_input("gpio-keys", self.root)
            stream.close.assert_called_once_with()


class Controller(unittest.TestCase):
    def setUp(self):
        self.values = []
        self.logs = []
        self.reader = volumekey.VolumeKey(self.values.append, self.logs.append)

    def test_only_matching_source_key_down_changes_and_release_rearms(self):
        self.reader.start()
        self.assertEqual(self.values, [volume.DEFAULT])
        self.reader.event("gpio-keys", powerkey.parse_event(wire(114, 1)))
        self.reader.event("qpnp_pon", powerkey.parse_event(wire(115, 1)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 2)))
        for source in volumekey.DEVICES:
            for value in (0, 1, 2, -1, 3):
                self.reader.event(source, powerkey.parse_event(wire(116, value)))
        self.reader.event("other", powerkey.parse_event(wire(115, 1)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1, kind=powerkey.EV_SYN)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 2)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
        self.assertEqual(self.values, [60, 65])
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 0)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
        self.assertEqual(self.values, [60, 65, 70])

    def test_both_directions_clamp_to_zero_and_one_hundred(self):
        self.reader.start()
        for _ in range(20):
            self.reader.event("qpnp_pon", powerkey.parse_event(wire(114, 1)))
            self.reader.event("qpnp_pon", powerkey.parse_event(wire(114, 0)))
        self.assertEqual(self.reader.percent, 0)
        self.assertEqual(self.values[-1], 0)
        for _ in range(20):
            self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
            self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 0)))
        self.assertEqual(self.reader.percent, 100)
        self.assertEqual(self.values[-1], 100)

    def test_syn_dropped_ignores_events_until_report_and_logs_once(self):
        self.reader.start()
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(powerkey.SYN_DROPPED, 0, kind=powerkey.EV_SYN)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 0)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(powerkey.SYN_DROPPED, 0, kind=powerkey.EV_SYN)))
        self.assertEqual(self.values, [60, 65])
        self.assertEqual(self.logs, ["input synchronization lost"])
        self.reader.event("gpio-keys", powerkey.parse_event(wire(powerkey.SYN_REPORT, 0, kind=powerkey.EV_SYN)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
        self.assertEqual(self.values, [60, 65, 70])

    def test_input_lost_clears_held_and_dropped_state(self):
        self.reader.start()
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
        self.reader.input_lost("gpio-keys")
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
        self.reader.event("gpio-keys", powerkey.parse_event(wire(powerkey.SYN_DROPPED, 0, kind=powerkey.EV_SYN)))
        self.reader.input_lost("gpio-keys")
        self.reader.event("gpio-keys", powerkey.parse_event(wire(115, 1)))
        self.assertEqual(self.values, [60, 65, 70, 75])


class ReaderLoop(unittest.TestCase):
    def test_splits_records_and_reads_both_devices_independently(self):
        up = FakeStream(wire(115, 1)[:9], wire(115, 1)[9:] + wire(115, 0) + wire(115, 1))
        down = FakeStream(wire(114, 1) + wire(114, 0))
        opened = {"gpio-keys": up, "qpnp_pon": down}
        values, logs, calls = [], [], []

        def wait(readers, _w, _x, _timeout):
            calls.append(tuple(readers))
            if len(calls) == 1:
                return [up, down], [], []
            if len(calls) == 2:
                return [up], [], []
            raise SystemExit(0)

        with self.assertRaises(SystemExit) as stopped:
            volumekey.run(controller=volumekey.VolumeKey(values.append, logs.append),
                          open_device=lambda name, root: opened[name], wait=wait)
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(values, [60, 55, 60, 65])
        self.assertTrue(up.closed)
        self.assertTrue(down.closed)

    def test_eof_retries_one_source_while_other_keeps_working(self):
        now = [0.0]
        bad_first = FakeStream(b"")
        healthy = FakeStream(wire(115, 1) + wire(115, 0), wire(115, 1))
        replacement = FakeStream(wire(114, 1))
        opened, values, logs = {"qpnp_pon": 0}, [], []

        def open_device(name, _root):
            if name == "gpio-keys":
                return healthy
            opened[name] += 1
            if opened[name] == 1:
                return bad_first
            return replacement

        def clock():
            return now[0]

        sequence = [bad_first, healthy]

        def wait(readers, _w, _x, timeout):
            if sequence:
                stream = sequence.pop(0)
                return [stream], [], []
            self.assertGreaterEqual(timeout, 1.0)
            now[0] += timeout
            # Healthy device is still being serviced after the other reader's EOF.
            if healthy in readers:
                return [healthy], [], []
            raise SystemExit(0)

        # The second healthy read proves the unaffected source remains live; then
        # the next wait advances the deterministic retry clock and reopens qpnp_pon.
        original_wait = wait
        healthy_reads = [0]

        def staged_wait(readers, w, x, timeout):
            if sequence:
                if sequence[-1] is healthy:
                    healthy_reads[0] += 1
                return original_wait(readers, w, x, timeout)
            if opened["qpnp_pon"] >= 2:
                raise SystemExit(0)
            return original_wait(readers, w, x, timeout)

        with self.assertRaises(SystemExit) as stopped:
            volumekey.run(controller=volumekey.VolumeKey(values.append, logs.append),
                          open_device=open_device, wait=staged_wait, clock=clock, log=logs.append)
        self.assertEqual(stopped.exception.code, 0)
        self.assertGreaterEqual(opened["qpnp_pon"], 2)
        self.assertGreaterEqual(healthy_reads[0], 1)
        self.assertTrue(bad_first.closed)
        self.assertIn("input unavailable; retrying", logs)

    def test_open_fault_retries_one_source_without_stopping_other_reader(self):
        now = [0.0]
        healthy = FakeStream(wire(115, 1), wire(115, 0))
        recovered = FakeStream(wire(114, 1))
        attempts = {"qpnp_pon": 0}
        values, logs = [], []

        def open_device(name, _root):
            if name == "gpio-keys":
                return healthy
            attempts[name] += 1
            if attempts[name] == 1:
                raise OSError("private open detail")
            return recovered

        def clock():
            return now[0]

        def wait(readers, _w, _x, timeout):
            if recovered in readers:
                raise SystemExit(0)
            if healthy in readers:
                if timeout:
                    now[0] += timeout
                return [healthy], [], []
            raise SystemExit(0)

        with self.assertRaises(SystemExit) as stopped:
            volumekey.run(controller=volumekey.VolumeKey(values.append, logs.append),
                          open_device=open_device, wait=wait, clock=clock, log=logs.append)
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(attempts["qpnp_pon"], 2)
        self.assertEqual(values, [60, 65])
        self.assertTrue(healthy.closed)
        self.assertTrue(recovered.closed)
        self.assertIn("input unavailable; retrying", logs)
        self.assertNotIn("private open detail", " ".join(logs))

    def test_read_fault_reopens_only_failed_stream_and_closes_everything(self):
        now = [0.0]
        failing = FakeStream(OSError("private read detail"))
        recovered = FakeStream(wire(114, 1))
        healthy = FakeStream(wire(115, 1))
        streams = {"qpnp_pon": [failing, recovered], "gpio-keys": [healthy]}
        values, logs = [], []

        def open_device(name, _root):
            return streams[name].pop(0)

        def wait(readers, _w, _x, timeout):
            if failing in readers:
                return [failing], [], []
            if timeout:
                now[0] += timeout
            if recovered in readers:
                return [recovered], [], []
            if healthy in readers:
                return [healthy], [], []
            raise SystemExit(0)

        # Stop only after the recovered reader has been observed once.
        recovered_seen = [False]

        def staged_wait(readers, w, x, timeout):
            if recovered in readers and recovered_seen[0]:
                raise SystemExit(0)
            if recovered in readers:
                recovered_seen[0] = True
            return wait(readers, w, x, timeout)

        with self.assertRaises(SystemExit) as stopped:
            volumekey.run(controller=volumekey.VolumeKey(values.append, logs.append),
                          open_device=open_device, wait=staged_wait, clock=lambda: now[0], log=logs.append)
        self.assertEqual(stopped.exception.code, 0)
        self.assertTrue(failing.closed)
        self.assertTrue(recovered.closed)
        self.assertTrue(healthy.closed)
        self.assertNotIn("private read detail", " ".join(logs))

    def test_publication_failure_returns_one_fixed_status_and_closes_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_parent = Path(tmp) / "absent" / "state.json"
            logs = []
            result = volumekey.run(state_path=missing_parent, log=logs.append,
                                   open_device=lambda *_: self.fail("must not open before initial publication"))
        self.assertEqual(result, 1)
        self.assertEqual(logs, ["reader stopped; state or input unavailable"])
        self.assertNotIn("Errno", " ".join(logs))
        self.assertNotIn(str(missing_parent), " ".join(logs))


class Packaging(unittest.TestCase):
    def test_reader_unit_and_server_opt_in_are_wired(self):
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.read(DUO / "opos-volumekey.service")
        self.assertEqual(unit.get("Service", "User"), "root")
        self.assertEqual(unit.get("Service", "Restart"), "always")
        self.assertEqual(unit.get("Service", "RuntimeDirectory"), "opos-volume")
        self.assertEqual(unit.get("Service", "RuntimeDirectoryMode"), "0755")
        self.assertEqual(unit.get("Service", "ReadWritePaths"), "/run/opos-volume")
        self.assertIn("Environment=OPOS_VOLUME_KEYS=1\n", (DUO / "opos-server.service").read_text())

    def test_build_installs_and_enables_reader_under_opos_service(self):
        build = (DUO / "build-image.sh").read_text()
        self.assertIn('install -m 0644 "$here/opos-volumekey.service" "$r/etc/systemd/system/opos-volumekey.service"', build)
        self.assertRegex(build, r'ln -sfn /etc/systemd/system/opos-volumekey\.service\s+\\\n\s+"\$r/etc/systemd/system/opos\.service\.wants/opos-volumekey\.service"')

    def test_actual_install_and_verifier_blocks_with_missing_artifacts_and_policy(self):
        def block(name):
            text = (DUO / name).read_text()
            return re.search(r"^# --- volume keys: BEGIN[^\n]*\n(.*?)^# --- volume keys: END",
                             text, re.M | re.S).group(1)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed = root / "etc/systemd/system"
            result = subprocess.run(["sh", "-eu", "-c", 'here=$1; r=$2\n' + block("build-image.sh"),
                                     "volume-test", str(DUO), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            shutil.copyfile(DUO / "opos-server.service", installed / "opos-server.service")
            for name in ("volumekey.py", "volume.py", "web_static/volume.js"):
                destination = root / "opt/opos/src/opos" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "src/opos" / name, destination)

            def verify():
                prelude = ('r=$1; w=$r/etc/systemd/system; fail=0\n'
                           'check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n')
                return subprocess.run(["sh", "-c", prelude + block("verify-image.sh") + '\nexit "$fail"\n',
                                       "volume-test", str(root)], capture_output=True, text=True)

            result = verify()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.count("PASS "), 2)
            self.assertEqual(os.readlink(installed / "opos.service.wants/opos-volumekey.service"),
                             "/etc/systemd/system/opos-volumekey.service")
            for relative in ("opt/opos/src/opos/volumekey.py", "opt/opos/src/opos/volume.py",
                             "opt/opos/src/opos/web_static/volume.js",
                             "etc/systemd/system/opos.service.wants/opos-volumekey.service"):
                path = root / relative
                saved = path.with_name(path.name + ".saved")
                path.rename(saved)
                try:
                    self.assertNotEqual(verify().returncode, 0, relative)
                finally:
                    saved.rename(path)
            path = installed / "opos-volumekey.service"
            original = path.read_text()
            for directive in ("Restart=always", "RestartSec=3", "StartLimitIntervalSec=0",
                              "User=root", "RuntimeDirectory=opos-volume", "RuntimeDirectoryMode=0755",
                              "BindsTo=opos.service", "PartOf=opos.service", "After=opos.service"):
                path.write_text(original.replace(directive + "\n", ""))
                self.assertNotEqual(verify().returncode, 0, directive)
            path.write_text(original)
            server = installed / "opos-server.service"
            server.write_text(server.read_text().replace("Environment=OPOS_VOLUME_KEYS=1\n", ""))
            self.assertNotEqual(verify().returncode, 0)

