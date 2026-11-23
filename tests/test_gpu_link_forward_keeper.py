# SPDX-License-Identifier: GPL-3.0-or-later
"""Fake-transport checks only. Real phones, Windows and cable pulls are NOT_RUN."""

from __future__ import annotations

import contextlib
import errno
import faulthandler
import importlib.util
import io
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/gpu-link-forward-keeper.py"
SPEC = importlib.util.spec_from_file_location("gpu_keeper", TOOL)
keeper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keeper)


@contextlib.contextmanager
def capture_descriptor(descriptor, output=None):
    """Capture descriptor writes, draining reporters before restoring the sink."""
    output = output if output is not None else io.StringIO()
    reports = []
    enqueue = keeper.Reporter.enqueue
    original_write = keeper.os.write
    original_init = keeper.Reporter.__init__
    sources = {}

    def init(report, quiet=False, fd=1):
        original_init(report, quiet, fd)
        sources[report] = fd

    def track(report, data):
        if sources.get(report, 1) == descriptor and report not in reports:
            reports.append(report)
        enqueue(report, data)

    def write(fd, data):
        if any(report.fd == fd for report in reports):
            output.write(data.decode("utf-8"))
            return len(data)
        return original_write(fd, data)

    with (
        patch.object(keeper.Reporter, "__init__", init),
        patch.object(keeper.Reporter, "enqueue", track),
        patch.object(keeper.os, "write", side_effect=write),
    ):
        try:
            yield output
        finally:
            for report in reports:
                report.close()


def capture_reports(output=None):
    return capture_descriptor(1, output)


def capture_errors(output=None):
    return capture_descriptor(2, output)


@contextlib.contextmanager
def hang_guard():
    faulthandler.dump_traceback_later(5, file=sys.__stderr__)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def bounded_read_adb_output(test, stream, output, *events):
    """A broken readiness loop must fail this test, rather than hang the suite."""
    reader = threading.Thread(target=keeper.read_adb_output, args=(stream, output, *events), daemon=True)
    reader.start()
    try:
        reader.join(timeout=0.5)
        test.assertFalse(reader.is_alive(), "reader did not finish within the test budget")
    finally:
        if reader.is_alive():
            events[-1].set()
            reader.join(timeout=0.1)


class ArgumentTests(unittest.TestCase):
    def setUp(self):
        # The older mock children use poll(); native waitid is tested separately.
        self.fallback = patch.object(keeper, "can_waitid", return_value=False)
        self.fallback.start()
        self.addCleanup(self.fallback.stop)

    def test_refused_arguments(self):
        with TemporaryDirectory() as folder:
            missing = str(Path(folder) / "private-identity")
            bad = [
                [],
                ["--mode", "other"],
                ["--mode", "ssh"],
                ["--unknown"],
                ["--status", "--once"],
                ["--mode", "ssh", "--ssh-target", "u@h", "--serial", "phone"],
                ["--mode", "adb", "--ssh-target", "u@h"],
                ["--mode", "adb", "--ssh-port", "22"],
                ["--mode", "adb", "--ssh-identity", folder],
            ]
            for target in ("u@host name", "-u@h", "u@-h", "u@h;echo", "u@@h", "@h", "u@", "u/h@h", "u@h\n", "u@h[1]"):
                bad.append(["--mode", "ssh", "--ssh-target=" + target])
            for option, values in (
                ("--local-port", ("1023", "65536", "0", "no")),
                ("--remote-port", ("0", "65536", "-1", "no")),
                ("--ssh-port", ("0", "65536", "-1", "no")),
                ("--interval", ("0.09", "30.1", "nan", "inf", "-inf", "no")),
                ("--serial", ("", "-phone", "a b", "a;echo", "a\n", "a" * 65)),
                ("--ssh", ("", "ssh -v", "ssh\n")),
                ("--adb", ("", "adb shell", "adb\n")),
                ("--ssh-identity", (missing, folder)),
            ):
                for value in values:
                    bad.append(["--status", option + "=" + value])
            for argv in bad:
                with self.subTest(argv=argv), capture_errors() as output:
                    with self.assertRaises(SystemExit) as caught:
                        keeper.arguments(argv)
                    self.assertEqual(caught.exception.code, 2)
                    self.assertNotIn(missing, output.getvalue())

    def test_ssh_once_refuses_with_reason(self):
        for target in ([], ["--ssh-target", "u@h"]):
            with capture_errors() as output, self.assertRaises(SystemExit) as caught:
                keeper.arguments(["--mode", "ssh", "--once", *target])
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("terminate the owned forward", output.getvalue())

    def test_batch_transport_wrappers_refused_before_launch(self):
        with TemporaryDirectory(prefix="keeper space & ") as folder:
            for suffix in (".cmd", ".bat", ".CMD", ".BAT"):
                wrapper = Path(folder) / ("transport" + suffix)
                wrapper.touch()
                for option in ("--ssh", "--adb"):
                    for executable in (str(wrapper), wrapper.name):
                        with (
                            self.subTest(option=option, executable=executable),
                            capture_errors() as output,
                            capture_reports(),
                            patch.object(subprocess, "Popen", side_effect=AssertionError("must not launch wrappers")),
                        ):
                            with self.assertRaises(SystemExit) as caught:
                                keeper.main(["--mode", "ssh", "--ssh-target", "u@h", option, executable])
                            self.assertEqual(caught.exception.code, 2)
                            self.assertIn(".cmd/.bat transport wrappers are refused", output.getvalue())
                            self.assertIn("native executable", output.getvalue())
                            self.assertNotIn(folder, output.getvalue())

    def test_valid_limits_defaults_and_help(self):
        args = keeper.arguments(["--status"])
        self.assertEqual((args.local_port, args.remote_port, args.interval), (8767, 8767, 0.5))
        for local, remote, interval in ((1024, 1, 0.1), (65535, 65535, 30)):
            args = keeper.arguments(
                [
                    "--mode",
                    "ssh",
                    "--ssh-target",
                    "user_name@host-name.example:12",
                    "--local-port",
                    str(local),
                    "--remote-port",
                    str(remote),
                    "--interval",
                    str(interval),
                    "--ssh-port",
                    "65535",
                ]
            )
            self.assertEqual((args.local_port, args.remote_port, args.interval), (local, remote, interval))
        result = subprocess.run([sys.executable, "-B", str(TOOL), "--help"], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--status", result.stdout)

    def test_identity_metadata_only_and_executable_path_with_spaces(self):
        with TemporaryDirectory(prefix="keeper space ") as folder:
            identity = Path(folder) / "identity"
            identity.touch()
            identity.chmod(0)
            with patch.object(Path, "open", side_effect=AssertionError("must not read identity")):
                args = keeper.arguments(
                    [
                        "--mode",
                        "ssh",
                        "--ssh-target",
                        "u@h",
                        "--ssh-identity",
                        str(identity),
                        "--ssh",
                        str(identity),
                    ]
                )
            self.assertEqual(args.ssh_identity, str(identity))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO metadata fixture requires POSIX")
    def test_identity_fifo_refused_without_opening(self):
        with TemporaryDirectory() as folder:
            identity = Path(folder) / "fifo"
            os.mkfifo(identity)
            with capture_errors(), self.assertRaises(SystemExit) as caught:
                keeper.arguments(["--status", "--ssh-identity", str(identity)])
            self.assertEqual(caught.exception.code, 2)

    def test_status_up_and_down_read_only(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch.object(subprocess, "Popen", side_effect=AssertionError("status must not start transports")):
                with capture_reports() as output:
                    self.assertEqual(keeper.main(["--status", "--local-port", str(port)]), 0)
                self.assertIn(f"forward answers on 127.0.0.1:{port} (listener identity unverified)", output.getvalue())
            connection, _ = listener.accept()
            connection.close()
        with capture_reports() as output:
            self.assertEqual(keeper.main(["--status", "--local-port", str(port)]), 1)
        self.assertIn(f"nothing answers on 127.0.0.1:{port}", output.getvalue())
        with patch.object(socket, "create_connection", side_effect=OSError) as probe:
            with capture_reports():
                self.assertEqual(keeper.main(["--status"]), 1)
            probe.assert_called_once_with(("127.0.0.1", 8767), timeout=0.5)

    def test_reporting_transitions_controls_quiet_and_rate_limit(self):
        with patch.object(keeper.time, "monotonic", side_effect=[10, 14, 23, 24]):
            with capture_reports() as output:
                report = keeper.Reporter()
                report.change("observed")
                report.change("observed")
                report.change("waiting", "cable absent")
                report.error("failed\x1b\r\n\t\u202e")
                report.error("duplicate")
                report.error("failed again")
                report.error("duplicate")
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[-2].endswith("failed"))
        self.assertTrue(lines[-1].endswith("failed again"))
        for line in lines:
            self.assertRegex(line, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z ")
            self.assertEqual(line, keeper.clean(line))
        with capture_reports() as output:
            report = keeper.Reporter(quiet=True)
            report.change("observed")
            report.change("waiting", "absent")
            report.change("conflict", "occupied")
            report.change("conflict", "occupied")
            report.error("error")
        self.assertEqual(len(output.getvalue().splitlines()), 2)

    def test_backoff_cap_and_reset(self):
        args = keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h", "--interval", "0.1"])
        instance = keeper.Keeper(args)
        waits = []

        class Child:
            stdout = None
            returncode = None
            pid = 99999999

            def __init__(self, *a, **kw):
                self.polls = 0

            def poll(self):
                self.polls += 1
                # Eighth child survives long enough to reset backoff.
                return None if len(waits) == 7 and self.polls < 3 else 1

            def wait(self, **kw):
                return 1

        def wait(delay):
            if delay != 0.05:
                waits.append(round(delay, 3))
            if len(waits) == 10:
                instance.stop.set()

        with (
            patch.object(subprocess, "Popen", Child),
            patch.object(keeper.os, "killpg", create=True),
            patch.object(instance.stop, "wait", side_effect=wait),
        ):
            with patch.object(keeper, "STARTUP", 0), capture_reports():
                self.assertEqual(instance.run_ssh(), 0)
        self.assertEqual(waits, [0.1, 0.2, 0.4, 0.8, 1, 1, 1, 0.1, 0.1, 0.2])

    def test_long_interval_is_not_shortened_by_backoff(self):
        args = keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h", "--interval", "5"])
        instance = keeper.Keeper(args)
        waits = []

        class Child:
            stdout = None
            returncode = None
            pid = 99999999

            def poll(self):
                return 1

            def wait(self, **kw):
                return 1

        def wait(delay):
            waits.append(delay)
            if len(waits) == 3:
                instance.stop.set()

        with (
            patch.object(subprocess, "Popen", return_value=Child()),
            patch.object(keeper.os, "killpg", create=True),
            patch.object(instance.stop, "wait", side_effect=wait),
        ):
            with capture_reports():
                self.assertEqual(instance.run_ssh(), 0)
        self.assertEqual(waits, [5, 5, 5])

    def test_resource_errors_are_sanitised(self):
        for option in ("--ssh", "--adb", "--ssh-identity", "--ssh-config"):
            for value in ("PRIVATE-PATH " + "x" * 10000, "PRIVATE-PATH" + "x" * 10000, "PRIVATE-PATH\0hidden"):
                with self.subTest(option=option, kind=value[:12]):
                    with (
                        capture_errors() as error,
                        capture_reports() as out,
                    ):
                        with self.assertRaises(SystemExit) as caught:
                            keeper.arguments(["--status", option, value])
                    self.assertEqual(caught.exception.code, 2)
                    self.assertNotIn("PRIVATE-PATH", error.getvalue() + out.getvalue())
                    self.assertNotIn("Traceback", error.getvalue() + out.getvalue())
        with TemporaryDirectory() as folder:
            identity = Path(folder) / "identity"
            identity.touch()
            for value in ("PRIVATE-PATH " + "x" * 10000, "PRIVATE-PATH\0hidden", str(Path(folder) / "missing"), folder):
                with capture_errors() as error, self.assertRaises(SystemExit) as caught:
                    keeper.arguments(["--status", "--ssh-identity", str(identity), "--ssh-config", value])
                self.assertEqual(caught.exception.code, 2)
                self.assertNotIn("PRIVATE-PATH", error.getvalue())
        # Newer pathlib may swallow metadata errors; exercise the guards directly too.
        for option, method in (
            ("--ssh", "stat"),
            ("--adb", "stat"),
            ("--ssh-identity", "is_file"),
            ("--ssh-config", "is_file"),
        ):
            with patch.object(Path, method, side_effect=OSError("PRIVATE-PATH")):
                with capture_errors() as error, self.assertRaises(SystemExit) as caught:
                    keeper.arguments(["--status", option, "PRIVATE-PATH"])
            self.assertEqual(caught.exception.code, 2)
            self.assertNotIn("PRIVATE-PATH", error.getvalue())

    def test_config_metadata_requirement_and_exact_position(self):
        with TemporaryDirectory() as folder:
            config = Path(folder) / "private config"
            config.touch()
            with patch.object(Path, "open", side_effect=AssertionError("metadata only")):
                with capture_errors(), self.assertRaises(SystemExit):
                    keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h", "--ssh-config", str(config)])
                args = keeper.arguments(
                    ["--mode", "ssh", "--ssh-target", "u@h", "--ssh-config", str(config), "--ssh-identity", str(config)]
                )
            command = keeper.ssh_command(args)
            self.assertEqual(command[:4], ["ssh", "-F", str(config), "-N"])
            self.assertEqual(command[-3:], ["-i", str(config), "u@h"])
            with capture_errors(), self.assertRaises(SystemExit):
                keeper.arguments(["--mode", "adb", "--ssh-config", str(config), "--ssh-identity", str(config)])
        self.assertNotIn("-F", keeper.ssh_command(keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h"])))
        help_text = " ".join(keeper.__doc__.split())
        for phrase in (
            "not isolated",
            "--ssh-identity is required",
            "SIGKILL",
            "--status before starting",
            "Windows descendants are not tracked",
            "NOT_RUN",
        ):
            self.assertIn(phrase, help_text)
        with capture_reports() as output, self.assertRaises(SystemExit):
            keeper.arguments(["--help"])
        self.assertIn("not isolated", " ".join(output.getvalue().split()))

    def test_terminate_precedes_wait_and_kill(self):
        trace = []

        class Child:
            stdout = None
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                trace.append("terminate")

            def kill(self):
                trace.append("kill")

            def wait(self, **kw):
                trace.append("wait")
                if len(trace) == 1:
                    raise subprocess.TimeoutExpired("fake", 0.3)

        instance = keeper.Keeper(keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h"]))
        instance.child = Child()
        instance.terminate()
        self.assertEqual(trace, ["terminate", "wait"])

    def test_direct_child_escalation_kills_then_reaps(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "adb", "--once"]))
        trace = []

        class Child:
            stdout = None
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                trace.append("terminate")

            def kill(self):
                trace.append("kill")

            def wait(self, **kw):
                trace.append("wait")
                if len(trace) == 2:
                    raise subprocess.TimeoutExpired("fake", 0.3)

        instance.child = Child()
        instance.terminate()
        self.assertEqual(trace, ["terminate", "wait", "kill", "wait"])
        self.assertIsNone(instance.child)

    def test_posix_group_cleanup_even_after_leader_exits(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h"]))

        class Child:
            stdout = None
            returncode = None
            pid = 99999999

            def poll(self):
                self.returncode = 0
                return 0

            def wait(self, **kw):
                return 0

        instance.child = Child()
        instance.child_group = True
        with patch.object(keeper.os, "killpg", create=True) as group_signal:
            instance.terminate()
        self.assertEqual(
            [call.args for call in group_signal.call_args_list],
            [(99999999, signal.SIGTERM)],
        )
        self.assertIsNone(instance.child)

    def test_startup_threshold_at_point_eight_seconds(self):
        clock = [0.0]
        instance = keeper.Keeper(keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h"]))

        class Child:
            stdout = None
            returncode = None
            pid = 99999999

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, **kw):
                return 0

        def wait(delay):
            clock[0] += delay
            if clock[0] >= 0.8:
                instance.stop.set()

        with (
            patch.object(keeper.subprocess, "Popen", return_value=Child()) as spawn,
            patch.object(keeper.os, "killpg", create=True) as group_signal,
            patch.object(keeper.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(instance.stop, "wait", side_effect=wait),
            capture_reports() as output,
        ):
            instance.run_ssh()
        self.assertNotIn("ssh running", output.getvalue())

        self.assertTrue(spawn.call_args.kwargs.get("start_new_session"))
        self.assertEqual(
            [call.args for call in group_signal.call_args_list],
            [(99999999, signal.SIGTERM), (99999999, signal.SIGKILL)],
        )

    def test_sigbreak_installed_and_restored(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "adb", "--once"]))
        sentinel = object()
        with (
            patch.object(keeper.signal, "SIGBREAK", 999, create=True),
            patch.object(keeper.signal, "signal", return_value=sentinel) as handler,
            patch.object(instance, "ensure_adb", return_value=True),
            capture_reports(),
        ):
            instance.run()
        calls = [call.args for call in handler.call_args_list if call.args[0] == 999]
        self.assertEqual(len(calls), 2)
        self.assertTrue(callable(calls[0][1]))
        self.assertEqual(calls[1], (999, sentinel))

    def test_signal_handler_never_acquires_condition_lock(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h"]))
        handlers = {}

        def register(signum, handler):
            handlers[signum] = handler

        def run():
            with patch.object(threading.Condition, "__enter__", side_effect=AssertionError("signal handler took lock")):
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            self.assertTrue(instance.stop.is_set())
            return 0

        with (
            patch.object(keeper.signal, "signal", side_effect=register),
            patch.object(instance, "run_ssh", side_effect=run),
            capture_reports(),
        ):
            self.assertEqual(instance.run(), 0)

    def test_windows_process_group_request_direct_cleanup(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h"]))

        class Child:
            stdout = None
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, **kw):
                return 0

        with (
            patch.object(keeper.os, "name", "nt"),
            patch.object(keeper.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True),
            patch.object(keeper.subprocess, "Popen", return_value=Child()) as spawn,
            patch.object(instance.stop, "wait", side_effect=lambda _: instance.stop.set()),
            patch.object(keeper.os, "killpg", create=True) as killpg,
            capture_reports(),
        ):
            instance.run_ssh()
        self.assertEqual(spawn.call_args.kwargs.get("creationflags"), 512)
        self.assertNotIn("start_new_session", spawn.call_args.kwargs)
        killpg.assert_not_called()

    def test_immediate_exit_hint_throttled_without_extra_command(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h", "--interval", ".1"]))
        clock, waits = [0.0], []

        class Child:
            stdout = None
            returncode = None
            pid = 99999999

            def poll(self):
                return 1

            def wait(self, **kw):
                return 1

        def wait(delay):
            waits.append(delay)
            clock[0] += delay
            if len(waits) == 40:
                instance.stop.set()

        with (
            patch.object(keeper.subprocess, "Popen", return_value=Child()) as spawn,
            patch.object(keeper.os, "killpg", create=True),
            patch.object(instance.stop, "wait", side_effect=wait),
            patch.object(keeper.time, "monotonic", side_effect=lambda: clock[0]),
            capture_reports() as output,
        ):
            instance.run_ssh()
        hints = [line for line in output.getvalue().splitlines() if "hint:" in line]
        self.assertEqual(len(hints), 2)
        self.assertIn("stale ssh may be holding the local port; run --status", hints[0])
        self.assertEqual(spawn.call_count, 40)
        self.assertTrue(all(call.args[0][0] == "ssh" for call in spawn.call_args_list))

    def test_strict_adb_records(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "adb", "--once"]))
        for command, good, bad in (
            (
                ["devices"],
                b"List of devices attached\r\n\nfixture\tdevice\r\n",
                [
                    b"fixture\tdevice\n",
                    b"List of devices attached\nfixture device\n",
                    b"List of devices attached\nfixture\tdevice extra\n",
                    b"List of devices attached\n-a\tdevice\n",
                ],
            ),
            (
                ["forward", "--list"],
                b"fixture tcp:18767 tcp:8767\r\n\n",
                [
                    b"garbage\n",
                    b"fixture tcp:18767 tcp:87\x0067\n",
                    b"fixture\ttcp:18767\ttcp:8767\n",
                    b"fixture  tcp:18767 tcp:8767\n",
                    b"fixture tcp:0 tcp:8767\n",
                    b"fixture tcp:18767 tcp:65536\n",
                    b"fixture tcp:18767 tcp:8767 extra\n",
                    b"x" * 513,
                    b"\t",
                    b"\x1b",
                    b"\xff",
                    b"\rgarbage",
                    b"fixture tcp:18767 tcp:8767\r",
                ],
            ),
        ):
            self.assertEqual(
                instance.adb_records(good, command),
                [("fixture", "device")] if command == ["devices"] else [("fixture", "tcp:18767", "tcp:8767")],
            )
            for raw in bad:
                with self.subTest(raw=raw[:40]), capture_reports():
                    self.assertIsNone(instance.adb_records(raw, command))

    def test_adb_line_length_boundary_independent_of_grammar(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "adb", "--once"]))
        prefix = b"List of devices attached\nfixture\t"
        self.assertEqual(instance.adb_records(prefix + b"x" * 504, ["devices"]), [("fixture", "x" * 504)])
        with capture_reports() as output:
            self.assertIsNone(instance.adb_records(prefix + b"x" * 505, ["devices"]))
        self.assertIn("adb output not understood; waiting", output.getvalue())


FAKE = r"""
import json, os, pathlib, signal, sys, time
root = pathlib.Path(__file__).parent
config = json.loads((root / 'config.json').read_text())
def publish(path, text):
    pending = path.with_name(path.name + '.' + str(os.getpid()) + '.pending')
    pending.write_text(text)
    os.replace(pending, path)
log = root / 'commands.jsonl'
old = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
name = pathlib.Path(__file__).name
entry = dict(name=name, argv=sys.argv[1:], pid=os.getpid(), at=time.monotonic())
publish(log, ''.join(json.dumps(row) + '\n' for row in [*old, entry]))
if name == 'ssh':
    if config.get('noise'):
        print(config['noise'], flush=True)
        print(config['noise'], file=sys.stderr, flush=True)
    if config.get('ignore_term'):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if config.get('fork'):
        pid = os.fork()
        if pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            publish(root / 'descendant', str(os.getpid()))
            time.sleep(60)
            os._exit(0)
        def reap(*_):
            try:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            except ProcessLookupError:
                pass
            sys.exit(0)
        # Remain waiting so escalation must kill the whole group.
        signal.signal(signal.SIGTERM, reap)
    index = sum(row['name'] == 'ssh' for row in old)
    lifetimes = config.get('lifetimes', [60])
    time.sleep(lifetimes[min(index, len(lifetimes) - 1)])
    sys.exit(1)
argv = sys.argv[1:]
if argv[:1] == ['-s']:
    argv = argv[2:]
if config.get('inherit_stdout'):
    if os.fork() == 0:
        publish(root / 'descendant', str(os.getpid()))
        time.sleep(60)
        os._exit(0)
    os._exit(0)
if config.get('flood') == argv:
    while True:
        os.write(sys.stdout.fileno(), b'x' * 4096)
if config.get('hang') == argv:
    time.sleep(60)
if config.get('fail') == argv:
    print('failure\x1b\r\n' + config.get('noise', ''), file=sys.stderr)
    sys.exit(7)
if argv == ['devices']:
    print(config.get('devices', 'List of devices attached\nfixture\tdevice\n'), end='')
elif argv == ['forward', '--list']:
    saved = root / 'forward'
    print(saved.read_text() if saved.exists() else config.get('forwards', ''), end='', flush=True)
    if config.get('race'):
        publish(saved, 'fixture tcp:18767 tcp:9999\n')
elif argv == ['forward', '--no-rebind', 'tcp:18767', 'tcp:8767']:
    saved = root / 'forward'
    if saved.exists():
        sys.exit(8)
    publish(saved, 'fixture tcp:18767 tcp:8767\n')
else:
    sys.exit(91)
"""


@unittest.skipIf(os.name == "nt", "POSIX fake executable and signal fixtures; Windows run is NOT_RUN")
class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="gpu-keeper-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.log = self.root / "commands.jsonl"
        self.settings = {}
        self.configure()
        for name in ("ssh", "adb"):
            executable = self.root / name
            executable.write_text(f"#!{sys.executable}\n" + FAKE, encoding="utf-8")
            executable.chmod(0o700)
        self.identity = self.root / "private identity"
        self.identity.write_text("PRIVATE KEY MATERIAL MUST NOT APPEAR", encoding="utf-8")
        self.children = []
        self.addCleanup(self.cleanup_processes)

    def configure(self, **values):
        self.settings.update(values)
        pending = self.root / "pending.json"
        pending.write_text(json.dumps(self.settings), encoding="utf-8")
        pending.replace(self.root / "config.json")

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def command(self, mode, *extra):
        return [
            sys.executable,
            "-B",
            str(TOOL),
            "--mode",
            mode,
            "--" + mode,
            str(self.root / mode),
            "--interval",
            "0.1",
            "--local-port",
            "18767",
            *(["--ssh-target", "fixture@phone"] if mode == "ssh" else []),
            *extra,
        ]

    def start(self, mode, *extra):
        child = subprocess.Popen(self.command(mode, *extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def finish(self, child, signum=signal.SIGTERM):
        if child.poll() is None:
            child.send_signal(signum)
        output, error = child.communicate(timeout=4)
        self.assertEqual(child.returncode, 0, output + error)
        self.assertEqual(error, "")
        self.assertNotIn(str(self.identity), output)
        self.assertNotIn("PRIVATE KEY MATERIAL", output)
        for line in output.splitlines():
            self.assertEqual(line, keeper.clean(line))
            self.assertRegex(line, r"^\d{4}-\d\d-\d\dT.*Z ")
        return output

    def until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("fake transport did not reach expected state; log=" + repr(self.calls()))

    def assert_allowed(self):
        # Applied to EVERY fixture scenario, including failing/ambiguous ones.
        for row in self.calls():
            if row["name"] == "adb":
                argv = row["argv"]
                if "forward" in argv:
                    self.assertEqual(argv[:2], ["-s", "fixture"])
                if argv[:1] == ["-s"]:
                    self.assertEqual(argv[1], "fixture")
                    argv = argv[2:]
                self.assertIn(
                    argv,
                    [
                        ["devices"],
                        ["forward", "--list"],
                        ["forward", "--no-rebind", "tcp:18767", "tcp:8767"],
                    ],
                )
            else:
                self.assertEqual(row["name"], "ssh")

    def cleanup_processes(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            try:
                child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
        orphans = []
        for row in self.calls():
            try:
                os.kill(row["pid"], 0)
            except ProcessLookupError:
                continue
            orphans.append(row["pid"])
            os.kill(row["pid"], signal.SIGKILL)
        self.assert_allowed()
        self.assertEqual(orphans, [], "keeper left a fake transport orphan")

    def once(self, *extra):
        child = self.start("adb", "--once", *extra)
        output, error = child.communicate(timeout=4)
        self.assertEqual(error, "", error)
        self.assertNotIn(str(self.identity), output)
        return child.returncode, output

    def adb_argv(self):
        return [row["argv"][2:] if row["argv"][:1] == ["-s"] else row["argv"] for row in self.calls()]

    def test_ssh_exact_argv_and_no_duplicate_child(self):
        child = self.start("ssh")
        self.until(lambda: len(self.calls()) == 1)
        time.sleep(0.35)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(
            self.calls()[0]["argv"],
            [
                "-N",
                "-T",
                "-L",
                "127.0.0.1:18767:127.0.0.1:8767",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "ServerAliveInterval=1",
                "-o",
                "ServerAliveCountMax=2",
                "-o",
                "ConnectTimeout=3",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ForwardAgent=no",
                "-o",
                "ForwardX11=no",
                "-o",
                "PermitLocalCommand=no",
                "fixture@phone",
            ],
        )
        self.finish(child)

    def test_ssh_optional_identity_and_port_never_printed(self):
        self.configure(noise=str(self.identity) + "\nPRIVATE KEY MATERIAL\x1b\r\u202e resolved-phone-address")
        child = self.start("ssh", "--ssh-identity", str(self.identity), "--ssh-port", "2222")
        self.until(lambda: bool(self.calls()))
        expected = keeper.ssh_command(
            keeper.arguments(
                [
                    "--mode",
                    "ssh",
                    "--ssh-target",
                    "fixture@phone",
                    "--local-port",
                    "18767",
                ]
            )
        )[1:-1]
        self.assertEqual(self.calls()[0]["argv"], expected + ["-i", str(self.identity), "-p", "2222", "fixture@phone"])
        time.sleep(1.1)
        output = self.finish(child, signal.SIGINT)
        self.assertIn("ssh running (child alive for 1.0s; forward readiness not verified)", output)
        self.assertIn("startup observation", output)
        self.assertIn("ssh exited (was running", output)
        self.assertNotIn("resolved-phone-address", output)

    def test_ssh_identity_only_and_port_only(self):
        for options, suffix in (
            (["--ssh-identity", str(self.identity)], ["-i", str(self.identity)]),
            (["--ssh-port", "1"], ["-p", "1"]),
        ):
            before = len(self.calls())
            child = self.start("ssh", *options)
            self.until(lambda before=before: len(self.calls()) > before)
            argv = self.calls()[-1]["argv"]
            self.assertEqual(argv[-len(suffix) - 1 :], suffix + ["fixture@phone"])
            self.assertEqual(argv.count("-i"), int("-i" in suffix))
            self.assertEqual(argv.count("-p"), int("-p" in suffix))
            self.finish(child)

    def test_ssh_restart_within_a_few_intervals_and_recovery(self):
        self.configure(lifetimes=[0, 0, 1.15, 60])
        child = self.start("ssh")
        self.until(lambda: len(self.calls()) >= 4, timeout=4)
        rows = self.calls()
        self.assertGreaterEqual(rows[1]["at"] - rows[0]["at"], 0.1)
        self.assertLess(rows[1]["at"] - rows[0]["at"], 0.5)
        self.assertGreaterEqual(rows[2]["at"] - rows[1]["at"], 0.2)
        self.assertLess(rows[3]["at"] - rows[2]["at"], 1.6)
        output = self.finish(child)
        self.assertIn("ssh running (child alive for 1.0s; forward readiness not verified)", output)
        self.assertIn("ssh exited (was running", output)

    def test_ssh_sigterm_escalates_and_no_orphan(self):
        self.configure(ignore_term=True)
        child = self.start("ssh")
        self.until(lambda: bool(self.calls()))
        time.sleep(0.05)  # fake has installed its SIGTERM handler
        started = time.monotonic()
        self.finish(child)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_ssh_missing_retries_quiet_and_error_throttle(self):
        child = self.start("ssh", "--ssh", str(self.root / "missing"), "--quiet")
        time.sleep(0.85)
        output = self.finish(child)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("SSH executable unavailable", output)
        self.assertEqual(self.calls(), [])

    def test_adb_missing_forward_created_once_and_existing_untouched(self):
        child = self.start("adb")
        self.until(lambda: len(self.calls()) >= 7)
        self.finish(child)
        calls = self.adb_argv()
        self.assertEqual(
            calls[:3], [["devices"], ["forward", "--list"], ["forward", "--no-rebind", "tcp:18767", "tcp:8767"]]
        )
        self.assertEqual(calls.count(["forward", "--no-rebind", "tcp:18767", "tcp:8767"]), 1)
        self.assertEqual(calls[3:5], [["devices"], ["forward", "--list"]])

    def test_adb_existing_once_and_serial(self):
        self.configure(
            devices="List of devices attached\nother\tdevice\nfixture\tdevice\n",
            forwards="fixture tcp:18767 tcp:8767\n",
        )
        code, output = self.once("--serial", "fixture")
        self.assertEqual(code, 0)
        self.assertIn("adb forward listed (not verified end to end)", output)
        self.assertEqual(
            [row["argv"] for row in self.calls()],
            [["-s", "fixture", "devices"], ["-s", "fixture", "forward", "--list"]],
        )

    def test_adb_multiple_devices_wait_once_without_any_forward_command(self):
        self.configure(devices="List of devices attached\nfixture\tdevice\nother\toffline\n")
        child = self.start("adb")
        self.until(lambda: len(self.calls()) >= 3)
        output = self.finish(child)
        self.assertEqual(output.count("several devices"), 1)
        self.assertTrue(all(argv == ["devices"] for argv in self.adb_argv()))

    def test_adb_ambiguous_once_fails(self):
        self.configure(devices="List of devices attached\na\tdevice\nb\tdevice\n")
        self.assertEqual(self.once()[0], 1)
        self.assertEqual(self.adb_argv(), [["devices"]])

    def test_adb_conflicting_remote_untouched_and_quiet_reports_conflict(self):
        self.configure(forwards="fixture tcp:18767 tcp:9999\n")
        code, output = self.once("--quiet")
        self.assertEqual(code, 1)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("conflict", output)
        self.assertEqual(self.adb_argv(), [["devices"], ["forward", "--list"]])

    def test_adb_other_serial_and_malformed_local_row_conflict(self):
        for forwards in ("other tcp:18767 tcp:8767\n", "fixture tcp:18767\n"):
            with self.subTest(forwards=forwards):
                self.configure(forwards=forwards)
                self.assertEqual(self.once()[0], 1)
        self.assertEqual(self.adb_argv(), [["devices"], ["forward", "--list"]] * 2)

    def test_adb_unrelated_forward_preserved(self):
        self.configure(forwards="fixture tcp:19999 tcp:1234\n")
        self.assertEqual(self.once()[0], 0)
        self.assertEqual(
            self.adb_argv(), [["devices"], ["forward", "--list"], ["forward", "--no-rebind", "tcp:18767", "tcp:8767"]]
        )

    def test_adb_not_ready_and_selected_absent(self):
        for devices, options in (
            ("List of devices attached\nfixture\toffline\n", []),
            ("List of devices attached\nother\tdevice\n", ["--serial", "fixture"]),
            ("", []),
            ("bad data extra\n", []),
        ):
            with self.subTest(devices=devices):
                self.configure(devices=devices)
                self.assertEqual(self.once(*options)[0], 1)
        self.assertEqual(self.adb_argv(), [["devices"]] * 4)

    def test_adb_replug_and_forward_loss_restored(self):
        self.configure(devices="")
        child = self.start("adb")
        self.until(lambda: len(self.calls()) >= 2)
        started = time.monotonic()
        self.configure(devices="List of devices attached\nfixture\tdevice\n")
        self.until(lambda: (self.root / "forward").exists())
        count = len(self.calls())
        self.until(lambda: len(self.calls()) > count)
        self.assertLess(time.monotonic() - started, 1)
        self.configure(devices="")
        self.until(lambda: len(self.calls()) >= 8)
        (self.root / "forward").unlink()
        started = time.monotonic()
        self.configure(devices="List of devices attached\nfixture\tdevice\n")
        self.until(lambda: (self.root / "forward").exists())
        count = len(self.calls())
        self.until(lambda: len(self.calls()) > count)
        self.assertLess(time.monotonic() - started, 1)
        output = self.finish(child)
        self.assertEqual(self.adb_argv().count(["forward", "--no-rebind", "tcp:18767", "tcp:8767"]), 2)
        self.assertIn("adb forward state unknown (adb unavailable)", output)
        self.assertEqual(output.count("adb forward created (not verified end to end)"), 2)

    def test_adb_missing_continues_rate_limited_and_once_fails(self):
        missing = str(self.root / "missing-adb")
        child = self.start("adb", "--adb", missing)
        time.sleep(0.35)
        output = self.finish(child)
        self.assertEqual(output.count("adb executable unavailable"), 1)
        self.assertEqual(self.once("--adb", missing)[0], 1)
        self.assertEqual(self.calls(), [])

    def test_adb_failures_and_child_controls_never_echoed(self):
        for command in (["devices"], ["forward", "--list"], ["forward", "--no-rebind", "tcp:18767", "tcp:8767"]):
            with self.subTest(command=command):
                self.configure(fail=command, noise=str(self.identity) + "\x1b\u202e resolved-address")
                code, output = self.once()
                self.assertEqual(code, 1)
                self.assertIn("adb command failed", output)
                self.assertNotIn("resolved-address", output)
                self.assertNotIn("\x1b", output)
                self.assertNotIn("\u202e", output)
        self.assertEqual(
            self.adb_argv(),
            [
                ["devices"],
                ["devices"],
                ["forward", "--list"],
                ["devices"],
                ["forward", "--list"],
                ["forward", "--no-rebind", "tcp:18767", "tcp:8767"],
            ],
        )

    def test_adb_failed_command_recovers_without_ending_loop(self):
        self.configure(fail=["devices"])
        child = self.start("adb")
        self.until(lambda: len(self.calls()) >= 2)
        self.configure(fail=None)
        self.until(lambda: (self.root / "forward").exists())
        count = len(self.calls())
        self.until(lambda: len(self.calls()) > count)
        output = self.finish(child)
        self.assertIn("adb command failed", output)
        self.assertIn("adb forward created (not verified end to end)", output)

    def test_adb_hung_command_times_out_and_retries(self):
        self.configure(hang=["devices"])
        child = self.start("adb")
        self.until(lambda: len(self.calls()) >= 2, timeout=3)
        self.assertGreaterEqual(self.calls()[1]["at"] - self.calls()[0]["at"], 1)
        self.finish(child, signal.SIGINT)

    def test_adb_signal_stops_hung_child_promptly(self):
        self.configure(hang=["devices"])
        child = self.start("adb")
        self.until(lambda: bool(self.calls()))
        started = time.monotonic()
        self.finish(child)
        self.assertLess(time.monotonic() - started, 0.8)

    def test_adb_once_never_probes_tcp_and_does_not_remove_forward(self):
        args = keeper.arguments(self.command("adb", "--once")[3:])
        with patch.object(socket, "create_connection", side_effect=AssertionError("only status probes TCP")):
            with capture_reports():
                self.assertEqual(keeper.Keeper(args).run(), 0)
        self.assertTrue((self.root / "forward").exists())
        self.assertEqual(
            self.adb_argv(), [["devices"], ["forward", "--list"], ["forward", "--no-rebind", "tcp:18767", "tcp:8767"]]
        )

    def test_adb_race_refused_without_overwrite(self):
        self.configure(race=True)
        code, output = self.once()
        self.assertEqual(code, 1)
        self.assertIn("adb create failed or conflicted", output)
        self.assertNotIn("adb forward created", output)
        self.assertEqual((self.root / "forward").read_text(), "fixture tcp:18767 tcp:9999\n")
        self.assertEqual(
            [row["argv"] for row in self.calls()],
            [
                ["devices"],
                ["-s", "fixture", "forward", "--list"],
                ["-s", "fixture", "forward", "--no-rebind", "tcp:18767", "tcp:8767"],
            ],
        )

    def test_adb_serial_stays_pinned_after_selection(self):
        child = self.start("adb")
        self.until(lambda: len(self.calls()) >= 4)
        self.configure(devices="List of devices attached\nother\tdevice\n")
        count = len(self.calls())
        self.until(lambda: len(self.calls()) >= count + 2)
        output = self.finish(child)
        self.assertIn("adb forward state unknown (adb unavailable)", output)
        self.assertTrue(all(row["argv"][:2] == ["-s", "fixture"] for row in self.calls()[1:]))

    def test_adb_output_cap_and_malformed_never_create(self):
        for forwards, phrase in (
            ("x" * 65537, "adb output too large; waiting"),
            ("garbage\n", "adb output not understood; waiting"),
            ("fixture tcp:18767 tcp:87\x0067\n", "adb output not understood; waiting"),
        ):
            self.configure(forwards=forwards)
            before = len(self.calls())
            code, output = self.once()
            self.assertEqual(code, 1)
            self.assertIn(phrase, output)
            self.assertEqual(len(self.calls()) - before, 2)
            self.assertFalse((self.root / "forward").exists())
        # Exactly 64 KiB is allowed. Many blank records exercise aggregate size.
        self.configure(forwards="\n" * 65536)
        self.assertEqual(self.once()[0], 0)

    def test_adb_streaming_output_killed_at_cap_and_bad_devices_never_list(self):
        self.configure(flood=["forward", "--list"])
        started = time.monotonic()
        code, output = self.once()
        self.assertEqual(code, 1)
        self.assertIn("adb output too large; waiting", output)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(self.calls()), 2)
        for devices in (
            "x" * 65537,
            "List of devices attached\nfixture\tde\x00vice\n",
            "List of devices attached\nfixture device\n",
        ):
            self.configure(flood=None, devices=devices)
            before = len(self.calls())
            code, output = self.once()
            self.assertEqual(code, 1)
            self.assertIn("adb output", output)
            self.assertEqual(len(self.calls()) - before, 1)
        self.assertFalse((self.root / "forward").exists())

    def test_adb_loss_of_observation_is_unknown(self):
        child = self.start("adb")
        self.until(lambda: len(self.calls()) >= 4)
        self.configure(fail=["devices"])
        count = len(self.calls())
        self.until(lambda: len(self.calls()) >= count + 2)
        output = self.finish(child)
        self.assertIn("adb forward created (not verified end to end)", output)
        self.assertIn("adb forward state unknown (adb unavailable)", output)
        self.assertIn("adb operation", output)
        for obsolete in ("forward up", "forward down", "connect took"):
            self.assertNotIn(obsolete, output)

    def test_missing_ssh_never_echoes_numeric_target_or_identity(self):
        child = self.start(
            "ssh",
            "--ssh-target",
            "u@192.0.2.1",
            "--ssh-identity",
            str(self.identity),
            "--ssh",
            str(self.root / "missing"),
            "--quiet",
        )
        time.sleep(0.2)
        output = self.finish(child)
        self.assertIn("SSH executable unavailable", output)
        self.assertNotIn("192.0.2.1", output)
        self.assertNotIn(str(self.identity), output)

    def test_ssh_descendants_removed_after_sigterm_and_sigint(self):
        self.configure(fork=True)
        for signum in (signal.SIGTERM, signal.SIGINT):
            child = self.start("ssh")
            descendant = self.root / "descendant"
            self.until(descendant.exists)
            pid = int(descendant.read_text())

            def gone(pid=pid):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                return False

            try:
                self.assertEqual(os.getpgid(pid), self.calls()[-1]["pid"])
                self.finish(child, signum)
                self.until(gone)
            finally:
                if not gone():
                    os.kill(pid, signal.SIGKILL)
                descendant.unlink(missing_ok=True)

    def test_replug_at_max_backoff_starts_within_one_point_five_seconds(self):
        self.configure(lifetimes=[0])
        child = self.start("ssh")
        self.until(lambda: len(self.calls()) >= 6, timeout=5)
        rows = self.calls()
        self.assertGreaterEqual(rows[-1]["at"] - rows[-2]["at"], 0.9)
        # Let the sixth immediate exit settle into the capped wait.
        time.sleep(0.1)
        self.configure(lifetimes=[60])
        switched = time.monotonic()
        count = len(self.calls())
        self.until(lambda: len(self.calls()) > count, timeout=1.5)
        self.assertLess(self.calls()[count]["at"] - switched, 1.5)
        output = self.finish(child)
        self.assertIn("hint: a stale ssh", output)

    def audited_runner(self, body=""):
        runner = self.root / "runner.py"
        runner.write_text(
            "import faulthandler,importlib.util,pathlib,sys,threading,os,time\n"
            "faulthandler.dump_traceback_later(3)\n"
            f"s=importlib.util.spec_from_file_location('keeper',{str(TOOL)!r})\n"
            "k=importlib.util.module_from_spec(s);s.loader.exec_module(k)\n"
            + body
            + "code=k.main(sys.argv[1:])\n"
            + f"pathlib.Path({str(self.root / 'threads')!r}).write_text(str([t.name for t in threading.enumerate() if t.name=='keeper-adb']))\n"
            + "raise SystemExit(code)\n"
        )
        return runner

    def test_inherited_adb_stdout_signal_and_deadline_leave_no_reader(self):
        self.configure(inherit_stdout=True)
        for send_stop in (True, False):
            with self.subTest(send_stop=send_stop), hang_guard():
                runner = self.audited_runner()
                child = subprocess.Popen(
                    [sys.executable, "-B", str(runner), *self.command("adb", "--once")[3:]],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.children.append(child)
                descendant = self.root / "descendant"
                self.until(descendant.exists)
                pid = int(descendant.read_text())
                started = time.monotonic()
                try:
                    if send_stop:
                        child.send_signal(signal.SIGTERM)
                    output, error = child.communicate(timeout=1.5)
                    self.assertEqual(child.returncode, 0 if send_stop else 1, output + error)
                    self.assertEqual(error, "")
                    self.assertLess(time.monotonic() - started, 1.5)
                    self.assertEqual((self.root / "threads").read_text(), "[]")
                    if not send_stop:
                        self.assertIn("adb command failed or timed out; waiting", output)
                    self.until(lambda pid=pid: not self.pid_exists(pid))
                finally:
                    if self.pid_exists(pid):
                        os.kill(pid, signal.SIGKILL)
                    descendant.unlink(missing_ok=True)

    @staticmethod
    def pid_exists(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_full_stdout_at_final_report_exits_without_draining(self):
        marker = self.root / "full"
        runner = self.audited_runner(
            "original=k.Reporter.change\n"
            "def change(self,state,detail=''):\n"
            " if detail=='stopped; owned SSH child terminated':\n"
            "  with self.condition:\n"
            "   os.set_blocking(1,False)\n"
            "   try:\n"
            "    while True: os.write(1,b'x'*4096)\n"
            "   except BlockingIOError: pass\n"
            "   finally: os.set_blocking(1,True)\n"
            f"  pathlib.Path({str(marker)!r}).touch()\n"
            " original(self,state,detail)\n"
            "k.Reporter.change=change\n"
        )
        with hang_guard():
            child = subprocess.Popen(
                [sys.executable, "-B", str(runner), *self.command("ssh")[3:]],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.children.append(child)
            self.until(lambda: bool(self.calls()))
            child.send_signal(signal.SIGTERM)
            self.until(marker.exists)
            started = time.monotonic()
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=1)
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(child.returncode, 0)
            self.assertEqual(child.stderr.read(), b"")
            # Keep the pipe open, but never read it, until after keeper exit.
            child.stdout.close()
            child.stderr.close()
            self.assertEqual((self.root / "threads").read_text(), "[]")


class RoundTwoTests(unittest.TestCase):
    def instance(self, mode="adb"):
        return keeper.Keeper(keeper.arguments(["--mode", mode, *(["--ssh-target", "u@h"] if mode == "ssh" else [])]))

    def reader_command(self, read, thread_factory=None, wait=None, stream=None):
        """A fake descriptor plus a reaping direct child; no transport is run."""
        instance = self.instance()
        stream = stream or unittest.mock.Mock()
        stream.fileno.return_value = 99999999

        class Child:
            stdout = stream
            returncode = None
            pid = 99999999

            def poll(self):
                self.returncode = 0
                return 0

            def wait(self, **kwargs):
                self.returncode = 0
                return 0

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(keeper, "can_waitid", return_value=False))
        stack.enter_context(patch.object(keeper.subprocess, "Popen", return_value=Child()))
        stack.enter_context(patch.object(keeper.os, "killpg", create=True))
        stack.enter_context(patch.object(keeper.os, "read", side_effect=read))
        stack.enter_context(patch.object(keeper.os, "set_blocking"))
        stack.enter_context(patch.object(keeper, "wait_descriptor", return_value=True))
        if thread_factory:
            stack.enter_context(patch.object(keeper.threading, "Thread", side_effect=thread_factory))
        if wait:
            stack.enter_context(patch.object(instance.stop, "wait", side_effect=lambda delay: wait(instance, delay)))
        return instance, stream

    def test_partial_records_then_read_error_are_rejected(self):
        instance, stream = self.reader_command([b"List of devices attached\nfixture\tdevice\n", OSError("read failed")])
        with capture_reports(), hang_guard():
            self.assertIsNone(instance.adb_command(["devices"]))
        stream.close.assert_called_once()

    def test_exited_child_still_waits_for_complete_reader(self):
        chunks = iter([b"List of devices attached\nfixture\tdevice\n", b""])

        def read(*_):
            time.sleep(0.025)
            return next(chunks)

        instance, _ = self.reader_command(read)
        with capture_reports(), hang_guard():
            self.assertEqual(instance.adb_command(["devices"]), [("fixture", "device")])

    def test_stop_during_list_prevents_create(self):
        instance = self.instance()
        calls = []

        def command(cmd):
            calls.append(cmd)
            if cmd == ["devices"]:
                return [("fixture", "device")]
            instance.stop.set()
            return []

        with patch.object(instance, "adb_command", side_effect=command), capture_reports():
            instance.ensure_adb()
        self.assertEqual(calls, [["devices"], ["forward", "--list"]])

    def test_startup_observation_retains_three_decimal_places(self):
        instance = self.instance("ssh")
        clock = [0.0]

        def alive():
            clock[0] = 1.125
            return True

        with (
            patch.object(keeper.subprocess, "Popen"),
            patch.object(instance, "leader_alive", side_effect=alive),
            patch.object(instance, "terminate"),
            patch.object(keeper.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(instance.stop, "wait", side_effect=lambda _: instance.stop.set()),
            capture_reports() as output,
        ):
            instance.run_ssh()
        self.assertIn("startup observation 1.125s", output.getvalue())

    def test_success_then_two_immediate_exits_has_no_hint(self):
        instance = self.instance("ssh")
        clock, spawns, waits = [0.0], [0], [0]

        def spawn(*_, **kwargs):
            spawns[0] += 1
            return object()

        def alive():
            if spawns[0] == 1 and clock[0] == 0:
                clock[0] = 1.125
                return True
            return False

        def wait(delay):
            if delay != 0.05:
                waits[0] += 1
                if waits[0] == 3:
                    instance.stop.set()
            clock[0] += delay

        with (
            patch.object(keeper.subprocess, "Popen", side_effect=spawn),
            patch.object(instance, "leader_alive", side_effect=alive),
            patch.object(instance, "terminate"),
            patch.object(keeper.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(instance.stop, "wait", side_effect=wait),
            capture_reports() as output,
        ):
            instance.run_ssh()
        self.assertEqual(spawns[0], 3)
        self.assertNotIn("hint:", output.getvalue())

    def test_zero_exit_create_error_stdout_is_rejected(self):
        instance = self.instance()
        with capture_reports() as output:
            self.assertIsNone(
                instance.adb_records(b"error: cannot bind\n", ["forward", "--no-rebind", "tcp:1", "tcp:2"])
            )
        self.assertIn("adb output not understood", output.getvalue())

    def test_empty_devices_reports_missing_header(self):
        instance = self.instance()
        with capture_reports() as output:
            self.assertIsNone(instance.adb_records(b"", ["devices"]))
        self.assertIn("adb output not understood", output.getvalue())

    def test_stop_interrupts_long_wait(self):
        flag = keeper.StopFlag()
        thread = threading.Thread(target=flag.wait, args=(5,), daemon=True)
        with hang_guard():
            thread.start()
            time.sleep(0.02)
            started = time.monotonic()
            flag.set()
            thread.join(0.2)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - started, 0.2)

    def test_permission_denied_group_cleanup_is_not_silent(self):
        instance = self.instance("ssh")
        instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
        instance.child.poll.return_value = None
        instance.child.wait.side_effect = subprocess.TimeoutExpired("fake", 0)
        instance.child_group = True
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", create=True, side_effect=PermissionError("denied")),
            capture_reports(),
        ):
            with self.assertRaises(PermissionError):
                instance.terminate()

    def test_reader_is_joined_before_command_returns(self):
        threads = []
        original = threading.Thread

        class DelayedThread(original):
            def run(self):
                super().run()
                time.sleep(0.12)

        def factory(*args, **kwargs):
            thread = DelayedThread(*args, **kwargs)
            threads.append(thread)
            return thread

        instance, _ = self.reader_command([b"List of devices attached\n", b""], thread_factory=factory)
        with capture_reports(), hang_guard():
            self.assertEqual(instance.adb_command(["devices"]), [])
        self.assertTrue(threads)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertTrue(all(thread.daemon for thread in threads))

    def test_delayed_reader_after_cancel_closes_only_in_reader(self):
        entered, release = threading.Event(), threading.Event()
        original = threading.Thread
        threads, owners = [], []

        class DelayedThread(original):
            def run(self):
                entered.set()
                release.wait(1)
                super().run()

        def factory(*args, **kwargs):
            thread = DelayedThread(*args, **kwargs)
            threads.append(thread)
            return thread

        def stop(instance, _):
            entered.wait(1)
            instance.stop.set()
            # The reader starts only after finally has set cancel.
            timer = original(target=lambda: (time.sleep(0.03), release.set()), daemon=True)
            timer.start()

        instance, stream = self.reader_command(AssertionError("read after cancel"), factory, stop)
        stream.close.side_effect = lambda: owners.append(threading.current_thread())
        with capture_reports(), contextlib.redirect_stderr(io.StringIO()) as error, hang_guard():
            self.assertIsNone(instance.adb_command(["devices"]))
        self.assertEqual(error.getvalue(), "")
        self.assertEqual(owners, threads)
        self.assertFalse(threads[0].is_alive())
        keeper.os.read.assert_not_called()

    def test_read_errors_and_close_errors_never_escape_reader(self):
        for failure in (OSError("read"), ValueError("closed"), RuntimeError("unexpected")):
            with self.subTest(failure=type(failure)):
                instance, stream = self.reader_command(failure)
                stream.close.side_effect = ValueError("close")
                with capture_reports(), contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertIsNone(instance.adb_command(["devices"]))
                self.assertEqual(error.getvalue(), "")

    def test_cancel_drops_late_bytes_and_stops_reading(self):
        reads = []
        blocked = threading.Event()
        instance = None

        def read(*_):
            reads.append(1)
            blocked.set()
            end = time.monotonic() + 1
            while not instance.stop.is_set() and time.monotonic() < end:
                time.sleep(0.001)
            self.assertTrue(instance.stop.is_set(), "test reader was never stopped")
            time.sleep(0.03)
            return b"List of devices attached\n"

        def stop(current, _):
            blocked.wait(1)
            current.stop.set()

        instance, _ = self.reader_command(read, wait=stop)
        retained = bytearray()
        with (
            patch.object(instance, "adb_records") as parse,
            patch.object(keeper, "bytearray", return_value=retained, create=True),
            capture_reports(),
            hang_guard(),
        ):
            self.assertIsNone(instance.adb_command(["devices"]))
        self.assertEqual(retained, b"")
        self.assertEqual(len(reads), 1)
        parse.assert_not_called()

    def test_reader_deadline_requires_eof_even_after_direct_exit(self):
        calls = []

        def read(*_):
            calls.append(1)
            if len(calls) == 1:
                return b"List of devices attached\nfixture\tdevice\n"
            time.sleep(0.08)
            return b""

        instance, _ = self.reader_command(read)
        with patch.object(keeper, "COMMAND_TIMEOUT", 0.025), capture_reports() as output, hang_guard():
            self.assertIsNone(instance.adb_command(["devices"]))
        self.assertIn("adb command failed or timed out; waiting", output.getvalue())
        self.assertFalse(any(t.name == "keeper-adb" for t in threading.enumerate()))

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX wait constants unavailable")
    def test_waitid_adb_success_requires_normal_zero_exit(self):
        instance = self.instance()
        instance.child_group = True
        instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
        for code, status, success in ((os.CLD_EXITED, 0, True), (os.CLD_EXITED, 1, False), (os.CLD_KILLED, 0, False)):
            result = unittest.mock.Mock(si_code=code, si_status=status)
            with (
                patch.object(keeper, "can_waitid", return_value=True),
                patch.object(keeper.os, "waitid", return_value=result, create=True),
            ):
                self.assertEqual(instance.child_succeeded(), success)

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    @unittest.skipUnless(hasattr(os, "WNOWAIT"), "non-reaping wait constants unavailable")
    def test_waitid_observes_without_poll_and_kills_group_before_reap(self):
        instance = self.instance("ssh")
        trace = []

        class Child:
            pid = 99999999
            returncode = None

            def poll(self):
                raise AssertionError("poll reaps on waitid branch")

            def wait(self, **kwargs):
                trace.append("reap")
                self.returncode = 0
                return 0

        def waitid(kind, pid, flags):
            self.assertEqual((kind, pid), (os.P_PID, 99999999))
            self.assertEqual(flags, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            trace.append("observe")
            return object()

        def kill(pid, signum):
            self.assertNotIn("reap", trace)
            trace.append(signum)

        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", side_effect=waitid, create=True),
            patch.object(keeper.os, "killpg", create=True, side_effect=kill),
            patch.object(keeper.subprocess, "Popen", return_value=Child()),
            patch.object(instance.stop, "wait", side_effect=lambda _: instance.stop.set()),
            capture_reports(),
        ):
            instance.run_ssh()
        self.assertIn(signal.SIGKILL, trace)
        self.assertLess(trace.index(signal.SIGKILL), trace.index("reap"))
        self.assertEqual(trace[0], "observe")

    def test_fallback_missing_waitid_or_wnowait_never_signals_reaped_group(self):
        actual_hasattr = hasattr
        for missing in ("waitid", "WNOWAIT"):
            trace = []
            instance = self.instance("ssh")

            class Child:
                pid = 99999999
                returncode = None

                def poll(self, trace=trace):
                    trace.append("reap")
                    self.returncode = 0
                    return 0

                def wait(self, **kwargs):
                    return 0

            with (
                patch.object(
                    keeper,
                    "hasattr",
                    side_effect=lambda obj, name, missing=missing: name != missing and actual_hasattr(obj, name),
                    create=True,
                ),
                patch.object(
                    keeper.os, "killpg", create=True, side_effect=lambda *args, trace=trace: trace.append("signal")
                ),
                patch.object(keeper.subprocess, "Popen", return_value=Child()),
                patch.object(instance.stop, "wait", side_effect=lambda _, instance=instance: instance.stop.set()),
                capture_reports(),
            ):
                self.assertFalse(keeper.can_waitid())
                instance.run_ssh()
            self.assertEqual(trace, ["reap"])

    def test_queue_bound_drop_oldest_notice_once_and_partial_writes(self):
        blocked, release = threading.Event(), threading.Event()
        writes = bytearray()
        report = keeper.Reporter()

        def write(fd, data):
            self.assertEqual(fd, report.fd)
            self.assertNotEqual(fd, 1)
            blocked.set()
            release.wait(2)
            count = min(7, len(data))
            writes.extend(data[:count])
            return count

        with patch.object(keeper.os, "write", side_effect=write), hang_guard():
            try:
                report.emit("first")
                self.assertTrue(blocked.wait(1))
                for index in range(70):
                    report.emit(f"line-{index}")
                self.assertEqual(len(report.queue), 64)
                self.assertEqual(report.dropped, 6)
                self.assertTrue(report.writer.daemon)
                release.set()
                report.close()
            finally:
                release.set()
                report.close()
        lines = writes.decode().splitlines()
        self.assertEqual(len(lines), 65)
        self.assertTrue(lines[0].endswith("first"))
        self.assertTrue(lines[1].startswith("(6 lines not shown) "))
        self.assertTrue(lines[1].endswith("line-6"))
        self.assertTrue(lines[-1].endswith("line-69"))
        self.assertEqual(writes.count(b"lines not shown"), 1)

    def test_broken_closed_and_zero_length_output_disable_reporting(self):
        for error in (BrokenPipeError(), OSError(), ValueError(), None):
            report = keeper.Reporter()
            with patch.object(keeper.os, "write", side_effect=error, return_value=0) as write:
                report.emit("first")
                report.close()
                self.assertTrue(report.failed)
                self.assertEqual(len(report.queue), 0)
                report.emit("after failure")
                self.assertEqual(write.call_count, 1)

    def test_emit_and_shutdown_do_not_wait_for_blocked_sink(self):
        blocked, release = threading.Event(), threading.Event()
        report = keeper.Reporter()

        def write(fd, data):
            blocked.set()
            release.wait(2)
            return len(data)

        with patch.object(keeper.os, "write", side_effect=write), hang_guard():
            try:
                report.emit("first")
                self.assertTrue(blocked.wait(1))
                started = time.monotonic()
                report.emit("last")
                self.assertLess(time.monotonic() - started, 0.05)
                started = time.monotonic()
                report.close()
                self.assertLess(time.monotonic() - started, 0.4)
                self.assertTrue(report.writer.is_alive())
            finally:
                release.set()
                report.writer.join(1)
        self.assertFalse(report.writer.is_alive())

    def test_close_wakes_idle_writer_and_drains_all_ordered_lines(self):
        data = bytearray()
        report = keeper.Reporter()
        wrote, idle = threading.Event(), threading.Event()
        original_wait = report.condition.wait

        def wait(*args, **kwargs):
            idle.set()
            return original_wait(*args, **kwargs)

        def write(fd, chunk):
            data.extend(chunk)
            wrote.set()
            return len(chunk)

        with (
            patch.object(keeper.os, "write", side_effect=write),
            patch.object(report.condition, "wait", side_effect=wait),
            hang_guard(),
        ):
            try:
                report.emit("one")
                self.assertTrue(wrote.wait(1))
                self.assertTrue(idle.wait(1))
                # Ensure the writer has released the condition inside wait().
                with report.condition:
                    pass
                report.close()
                self.assertFalse(report.writer.is_alive())
            finally:
                with report.condition:
                    report.closed = True
                    report.condition.notify_all()
                report.writer.join(1)
        second = keeper.Reporter()
        with patch.object(keeper.os, "write", side_effect=write):
            second.emit("two")
            second.close()
        self.assertFalse(second.writer.is_alive())
        self.assertTrue(data.decode().splitlines()[0].endswith("one"))
        self.assertTrue(data.decode().splitlines()[1].endswith("two"))

    def test_run_drains_final_report_before_restoring_signals(self):
        instance = self.instance("ssh")
        order = []
        with (
            patch.object(instance, "run_ssh", return_value=0),
            patch.object(instance.report, "change", side_effect=lambda *args: order.append("report")),
            patch.object(instance.report, "close", side_effect=lambda **kwargs: order.append("drain")),
            patch.object(keeper.signal, "signal", side_effect=lambda *args: order.append("signal")),
        ):
            self.assertEqual(instance.run(), 0)
        self.assertEqual(order, ["signal", "signal", "report", "drain", "signal", "signal"])

    def test_help_complete_ordered_and_status_have_bounded_drain(self):
        with patch.object(keeper.Reporter, "close", autospec=True) as close, capture_reports() as output:
            with self.assertRaises(SystemExit) as caught:
                keeper.arguments(["--help"])
            self.assertEqual(caught.exception.code, 0)
            # Explicitly join while close is mocked, so content assertions are deterministic.
            for call in close.call_args_list:
                report = call.args[0]
                with report.condition:
                    report.closed = True
                    report.condition.notify()
                report.writer.join(1)
            self.assertTrue(all(call.kwargs.get("timeout") == 1.0 for call in close.call_args_list))
        help_text = output.getvalue()
        self.assertTrue(help_text.startswith("usage:"))
        self.assertLess(help_text.index("usage:"), help_text.index("options:"))
        self.assertIn("--once", help_text)
        with patch.object(keeper.socket, "create_connection", side_effect=OSError), capture_reports() as output:
            self.assertEqual(keeper.main(["--status"]), 1)
        self.assertIn("nothing answers on 127.0.0.1:8767", output.getvalue())


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX wait constants; Windows NOT_RUN")
class RoundThreeTests(unittest.TestCase):
    def mock_child(self, trace, stream, waitid, external_reap, exited, exit_status):
        class Child:
            pid = 99999999
            returncode = None
            stdout = stream

            def poll(self):
                if waitid and not external_reap:
                    raise AssertionError("poll would reap before the group signal")
                trace.append("poll")
                if exited:
                    self.returncode = exit_status
                return self.returncode

            def wait(self, **kwargs):
                if (
                    not waitid
                    and "timeout" in kwargs
                    and (
                        not exited
                        or kwargs["timeout"] <= keeper.READER_JOIN
                        and signal.SIGTERM not in trace
                        and signal.SIGKILL not in trace
                    )
                ):
                    raise subprocess.TimeoutExpired("fake", kwargs["timeout"])
                if self.returncode is None:
                    trace.append("reap")
                self.returncode = exit_status
                return exit_status

        return Child()

    def exercise_permission(
        self, place, exited, waitid=True, denied=signal.SIGTERM, error=None, external_reap=False, exit_status=0
    ):
        instance = keeper.Keeper(
            keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h"] if place == "ssh" else ["--mode", "adb"])
        )
        trace = []
        stream = unittest.mock.Mock()
        stream.fileno.return_value = 99999999

        child = self.mock_child(trace, stream, waitid, external_reap, exited, exit_status)

        def observe(*_):
            self.assertNotIn("reap", trace)
            trace.append("observe-exit" if exited else "observe-live")
            if external_reap:
                raise ChildProcessError
            return unittest.mock.Mock(si_code=os.CLD_EXITED, si_status=exit_status) if exited else None

        def group_signal(pid, signum):
            self.assertEqual(pid, child.pid)
            self.assertNotIn("reap", trace)
            trace.append(signum)
            if signum == denied:
                raise error if error is not None else PermissionError(1, "denied")

        def spawn(*_, **kwargs):
            # Exercise cleanup directly when a stop arrives during creation.
            if not exited or not waitid or external_reap:
                instance.stop.set()
            return child

        with (
            patch.object(keeper, "can_waitid", return_value=waitid),
            patch.object(keeper.os, "waitid", side_effect=observe, create=True),
            patch.object(keeper.os, "killpg", create=True, side_effect=group_signal),
            patch.object(keeper.subprocess, "Popen", side_effect=spawn),
            patch.object(keeper.os, "read", side_effect=[b"List of devices attached\n", b""]),
            patch.object(keeper.os, "set_blocking"),
            patch.object(keeper, "wait_descriptor", return_value=True),
            patch.object(keeper, "STOP_TIMEOUT", 0),
            patch.object(instance.stop, "wait", side_effect=lambda _: instance.stop.set()),
            capture_reports(),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            if place == "stop":
                instance.child, instance.child_group = child, True
                operation = instance.terminate
            elif place == "ssh":
                operation = instance.run_ssh
            else:

                def operation():
                    return instance.adb_command(["devices"])

            if (
                error is not None
                and not isinstance(error, ProcessLookupError)
                or (error is None and not external_reap and not exited)
            ):
                with self.assertRaises(type(error) if error is not None else PermissionError):
                    operation()
            else:
                result = operation()
                self.assertEqual(
                    result, {"stop": None, "ssh": 0, "adb": [] if exit_status == 0 and waitid else None}[place]
                )
                self.assertEqual(child.returncode, exit_status)
                self.assertEqual(trace.count("reap"), 1)
                if not external_reap:
                    self.assertLess(trace.index(denied), trace.index("reap"))
                else:
                    self.assertNotIn(signal.SIGTERM, trace)
                    self.assertNotIn(signal.SIGKILL, trace)
                if error is None and waitid:
                    self.assertIn("observe-exit", trace)
            self.assertEqual(stderr.getvalue(), "")
        if exited:
            self.assertIsNone(instance.child)
        else:
            self.assertIs(instance.child, child)
        self.assertFalse(any(t.name == "keeper-adb" for t in threading.enumerate()))
        return trace

    def test_stop_eperm_requires_unreaped_exited_leader_for_both_signals(self):
        for signum in (signal.SIGTERM, signal.SIGKILL):
            for exited, waitid in ((True, True), (False, True), (True, False), (False, False)):
                if signum == signal.SIGKILL and exited and not waitid:
                    continue  # Fallback already reaped this leader, so must never send SIGKILL.
                with self.subTest(signum=signum, exited=exited, waitid=waitid):
                    self.exercise_permission("stop", exited, waitid, signum)

    def test_ssh_clear_eperm_requires_unreaped_exited_leader(self):
        for exited, waitid in ((True, True), (False, True), (True, False), (False, False)):
            with self.subTest(exited=exited, waitid=waitid):
                self.exercise_permission("ssh", exited, waitid)

    def test_adb_clear_eperm_requires_unreaped_exited_leader(self):
        for exited, waitid in ((True, True), (False, True), (True, False), (False, False)):
            with self.subTest(exited=exited, waitid=waitid):
                self.exercise_permission("adb", exited, waitid)

    def test_fallback_never_reaps_while_reader_holds_pipe(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "adb"]))
        instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
        reader = unittest.mock.Mock()
        with patch.object(keeper, "can_waitid", return_value=False):
            instance.reap_completed_adb(reader, False)
        reader.join.assert_not_called()
        instance.child.wait.assert_not_called()

    def test_completed_oversized_adb_fallback_reaps_before_empty_group_signal(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "adb"]))
        stream = unittest.mock.Mock()
        stream.fileno.return_value = 99999999
        child = unittest.mock.Mock(pid=99999999, returncode=None, stdout=stream)
        child.poll.side_effect = AssertionError("output cap must precede polling")

        def wait(**kwargs):
            child.returncode = 7
            return 7

        child.wait.side_effect = wait
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper, "OUTPUT_LIMIT", 0),
            patch.object(keeper.os, "read", return_value=b"x"),
            patch.object(keeper.os, "set_blocking"),
            patch.object(keeper, "wait_descriptor", return_value=True),
            patch.object(keeper.os, "killpg", create=True) as group_signal,
            patch.object(keeper.subprocess, "Popen", return_value=child),
            capture_reports(),
        ):
            self.assertIsNone(instance.adb_command(["devices"]))
        self.assertEqual(len(child.wait.call_args_list), 2)
        for call in child.wait.call_args_list:
            self.assertGreaterEqual(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], keeper.SHUTDOWN_TIMEOUT)
        self.assertLessEqual(child.wait.call_args_list[0].kwargs["timeout"], keeper.READER_JOIN)
        self.assertEqual(child.returncode, 7)
        stream.close.assert_called_once()
        group_signal.assert_not_called()
        self.assertFalse(any(t.name == "keeper-adb" for t in threading.enumerate()))

    def test_group_signal_esrch_is_success_without_observing_or_reaping(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "adb"]))
        instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
        instance.child_group = True
        with (
            patch.object(keeper.os, "killpg", create=True, side_effect=ProcessLookupError(3, "gone")),
            patch.object(instance, "leader_alive") as observe,
        ):
            instance.group_signal(signal.SIGKILL)
        observe.assert_not_called()
        instance.child.wait.assert_not_called()

    def test_eperm_waits_for_delayed_nonreaping_exit_notification(self):
        instance = keeper.Keeper(keeper.arguments(["--mode", "ssh", "--ssh-target", "u@h"]))
        instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
        instance.child_group = True
        observed = unittest.mock.Mock(si_code=os.CLD_EXITED, si_status=7)
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", side_effect=[None, None, observed], create=True) as waitid,
            patch.object(keeper.os, "killpg", create=True, side_effect=PermissionError(1, "denied")),
        ):
            instance.group_signal(signal.SIGTERM)
        self.assertEqual(waitid.call_count, 3)
        instance.child.poll.assert_not_called()
        instance.child.wait.assert_not_called()

    def test_eperm_cleanup_preserves_nonzero_leader_exit_status(self):
        for place in ("stop", "ssh", "adb"):
            with self.subTest(place=place):
                self.exercise_permission(place, True, exit_status=7)

    def test_group_signal_other_errors_and_external_reap_keep_existing_handling(self):
        for place in ("stop", "ssh", "adb"):
            for error in (ProcessLookupError(3, "gone"), OSError(5, "I/O")):
                with self.subTest(place=place, error=type(error)):
                    self.exercise_permission(place, True, error=error)
        self.exercise_permission("stop", True, external_reap=True)

    @unittest.skipUnless(os.name == "posix", "native POSIX process groups; Windows NOT_RUN")
    def test_native_immediate_exit_has_empty_interpreter_stderr(self):
        self.native_exit(False)

    @unittest.skipUnless(os.name == "posix", "native POSIX process groups; Windows NOT_RUN")
    def test_native_exited_leader_live_descendant_is_killed(self):
        self.native_exit(True)

    def native_exit(self, descendant):
        with TemporaryDirectory(prefix="keeper-native-exit-") as folder:
            root = Path(folder)
            fake, runner, pidfile = root / "fake", root / "runner.py", root / "descendant"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, time\n"
                + (
                    "parent = os.getpid()\n"
                    "pid = os.fork()\n"
                    "if pid == 0:\n"
                    " while os.getppid() == parent: time.sleep(.001)\n"
                    f" pending = pathlib.Path({str(pidfile.with_suffix('.pending'))!r})\n"
                    " pending.write_text(str(os.getpid()))\n"
                    f" pending.replace({str(pidfile)!r})\n"
                    " time.sleep(60)\n"
                    " os._exit(0)\n"
                    if descendant
                    else ""
                )
                + "os._exit(7)\n"
            )
            fake.chmod(0o700)
            runner.write_text(
                "import importlib.util, pathlib, sys, threading, time\n"
                f"s = importlib.util.spec_from_file_location('k', {str(TOOL)!r})\n"
                "k = importlib.util.module_from_spec(s); s.loader.exec_module(k)\n"
                "instance = k.Keeper(k.arguments(sys.argv[1:]))\n"
                + (
                    "spawn = k.subprocess.Popen\n"
                    "def popen(*args, **kwargs):\n"
                    " child = spawn(*args, **kwargs)\n"
                    " deadline = time.monotonic() + 2\n"
                    f" while not pathlib.Path({str(pidfile)!r}).exists():\n"
                    "  assert time.monotonic() < deadline\n"
                    "  time.sleep(.002)\n"
                    # No poll: keep the exited leader owning the group number.
                    " instance.stop.set()\n"
                    " return child\n"
                    "k.subprocess.Popen = popen\n"
                    if descendant
                    else ""
                )
                + "wait = instance.stop.wait\n"
                "def stop_after_exit(delay):\n"
                " if delay != .05: instance.stop.set()\n"
                " return wait(delay)\n"
                "instance.stop.wait = stop_after_exit\n"
                "code = instance.run()\n"
                "assert not [t for t in threading.enumerate() if t.name == 'keeper-adb']\n"
                "raise SystemExit(code)\n"
            )
            try:
                result = subprocess.run(
                    [sys.executable, "-B", str(runner), "--mode", "ssh", "--ssh-target", "u@h", "--ssh", str(fake)],
                    capture_output=True,
                    timeout=4,
                )
                self.assertEqual(result.stderr, b"")
                self.assertEqual(result.returncode, 0, result.stdout)
                if descendant:
                    pid = int(pidfile.read_text())
                    deadline = time.monotonic() + 1
                    while time.monotonic() < deadline:
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(0.005)
                    else:
                        self.fail("native same-group descendant survived keeper exit")
            finally:
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass


class RoundFourTests(unittest.TestCase):
    reader_command = RoundTwoTests.reader_command

    def instance(self, mode="adb"):
        instance = RoundTwoTests.instance(self, mode)
        self.addCleanup(instance.report.close)
        return instance

    def test_closed_rejects_new_emit_and_unused_duplicate_is_closed_by_writer(self):
        report = keeper.Reporter()
        owned = report.fd
        closes = []
        close = os.close

        def record(fd):
            closes.append((fd, threading.current_thread().name))
            close(fd)

        with patch.object(keeper.os, "close", side_effect=record), patch.object(keeper.os, "write") as write:
            report.close()
            report.emit("after closed")
            self.assertEqual(list(report.queue), [])
            report.close()
        write.assert_not_called()
        self.assertEqual(closes, [(owned, "keeper-report")])
        with self.assertRaises(OSError):
            os.fstat(owned)

    def test_real_closed_and_reused_descriptor_is_never_written(self):
        with TemporaryDirectory() as folder:
            original, unrelated = Path(folder) / "original", Path(folder) / "unrelated"
            fd = os.open(original, os.O_WRONLY | os.O_CREAT, 0o600)
            report = keeper.Reporter(fd=fd)
            owned = report.fd
            self.assertNotEqual(owned, fd)
            self.assertFalse(os.get_inheritable(owned))
            os.close(fd)
            reused = os.open(unrelated, os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                self.assertEqual(reused, fd)
                report.emit("ONLY ORIGINAL café 漢字")
                report.close(timeout=1)
                self.assertIn("ONLY ORIGINAL café 漢字", original.read_text())
                self.assertEqual(unrelated.read_bytes(), b"")
                os.fstat(reused)
                with self.assertRaises(OSError):
                    os.fstat(owned)
            finally:
                report.close()
                os.close(reused)

    def test_dup_failure_silently_disables_reporting(self):
        with patch.object(keeper.os, "dup", side_effect=OSError("closed")):
            report = keeper.Reporter()
        with patch.object(keeper.os, "write") as write:
            report.emit("lost")
            report.close()
        self.assertTrue(report.failed)
        self.assertIsNone(report.writer)
        write.assert_not_called()

    def test_requested_drain_and_nonascii_are_preserved(self):
        report = keeper.Reporter()
        entered, done = threading.Event(), threading.Event()
        data = bytearray()

        def write(fd, chunk):
            entered.set()
            time.sleep(0.45)
            data.extend(chunk)
            done.set()
            return len(chunk)

        with patch.object(keeper.os, "write", side_effect=write):
            report.emit("café 漢字")
            self.assertTrue(entered.wait(1))
            try:
                report.close(timeout=1)
                self.assertTrue(done.is_set())
            finally:
                report.writer.join(timeout=1)
        self.assertIn("café 漢字", data.decode())

    def test_only_writer_closes_duplicate_after_blocked_write(self):
        report = keeper.Reporter()
        owned = report.fd
        entered, release = threading.Event(), threading.Event()
        closes = []
        close = os.close

        def write(fd, chunk):
            entered.set()
            release.wait(2)
            os.fstat(owned)
            return len(chunk)

        def record(fd):
            closes.append((fd, threading.current_thread().name))
            close(fd)

        with patch.object(keeper.os, "write", side_effect=write), patch.object(keeper.os, "close", side_effect=record):
            try:
                report.emit("pending")
                self.assertTrue(entered.wait(1))
                report.close(timeout=0.01)
                os.fstat(owned)
                self.assertEqual(closes, [])
            finally:
                release.set()
                report.writer.join(timeout=1)
        self.assertEqual(closes, [(owned, "keeper-report")])

    def test_eagain_retries_until_writable_even_after_close(self):
        for platform in ("posix", "nt"):
            with self.subTest(platform=platform):
                report = keeper.Reporter()
                writes = []

                def write(fd, data, writes=writes):
                    writes.append(data)
                    if len(writes) <= 3:
                        raise BlockingIOError(errno.EAGAIN, "retry")
                    return len(data)

                with (
                    patch.object(keeper.os, "name", platform),
                    patch.object(keeper.os, "write", side_effect=write),
                    patch.object(keeper, "wait_descriptor", return_value=True) as ready,
                    patch.object(keeper.time, "sleep") as sleep,
                ):
                    report.emit("eventually")
                    report.close(timeout=1)
                self.assertFalse(report.failed)
                self.assertEqual(len(writes), 4)
                self.assertTrue(all(chunk == writes[0] for chunk in writes))
                if platform == "posix":
                    self.assertEqual(ready.call_count, 3)
                    self.assertEqual(ready.call_args.kwargs, {"writable": True, "timeout": 0.05})
                    sleep.assert_not_called()
                else:
                    ready.assert_not_called()
                    self.assertEqual(sleep.call_count, 3)
                    sleep.assert_called_with(0.05)

    @unittest.skipUnless(os.name == "posix", "POSIX pipes; Windows NOT_RUN")
    def test_real_nonblocking_sink_recovers_after_eagain(self):
        read, write = os.pipe()
        os.set_blocking(write, False)
        try:
            while True:
                os.write(write, b"x" * 4096)
        except BlockingIOError:
            pass
        report = keeper.Reporter(fd=write)
        try:
            report.emit("SINK RECOVERED")
            report.close(timeout=0.02)
            self.assertTrue(report.writer.is_alive())
            os.set_blocking(read, False)
            data = bytearray()
            end = time.monotonic() + 1
            while b"SINK RECOVERED" not in data and time.monotonic() < end:
                try:
                    data.extend(os.read(read, 65536))
                except BlockingIOError:
                    time.sleep(0.002)
            report.writer.join(timeout=1)
            self.assertFalse(report.failed)
            self.assertFalse(report.writer.is_alive())
            self.assertIn(b"SINK RECOVERED", data)
        finally:
            os.close(read)
            os.close(write)

    def test_reader_cap_retains_only_one_sentinel(self):
        retained = bytearray()
        remaining = [keeper.OUTPUT_LIMIT + 4096]

        def read(fd, count):
            count = min(count, remaining[0])
            remaining[0] -= count
            return b"\n" * count

        instance, _ = self.reader_command(read)
        with patch.object(keeper, "bytearray", return_value=retained, create=True), capture_reports():
            self.assertIsNone(instance.adb_command(["forward", "--list"]))
        self.assertEqual(len(retained), keeper.OUTPUT_LIMIT + 1)

    def test_fragmented_record_order(self):
        instance, _ = self.reader_command([b"List of devices att", b"ached\nfixture\tdevice\n", b""])
        self.assertEqual(instance.adb_command(["devices"]), [("fixture", "device")])

    def test_close_failure_invalidates_output_and_completion_waits_for_close(self):
        for delay in (0, 0.06):
            with self.subTest(delay=delay):

                def close(delay=delay):
                    time.sleep(delay)
                    raise OSError("late close failure")

                instance, stream = self.reader_command([b"List of devices attached\nfixture\tdevice\n", b""])
                stream.close.side_effect = close
                with capture_reports():
                    self.assertIsNone(instance.adb_command(["devices"]))

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    @unittest.skipUnless(hasattr(os, "WNOWAIT"), "non-reaping wait constants unavailable")
    def test_eperm_short_lag_and_configured_deadline(self):
        for allowance, lag, bound in ((0.3, 0.006, 0.065), (0.01, 1, 0.04)):
            with self.subTest(allowance=allowance):
                instance = self.instance()
                instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
                instance.child_group = True
                start = time.perf_counter()

                def observe(*_, start=start, lag=lag):
                    return None if time.perf_counter() - start < lag else unittest.mock.Mock()

                with (
                    patch.object(keeper, "STOP_TIMEOUT", allowance),
                    patch.object(keeper, "can_waitid", return_value=True),
                    patch.object(keeper.os, "killpg", create=True, side_effect=PermissionError(errno.EPERM, "denied")),
                    patch.object(keeper.os, "waitid", side_effect=observe, create=True),
                ):
                    if lag > allowance:
                        with self.assertRaises(PermissionError):
                            instance.group_signal(signal.SIGTERM)
                    else:
                        instance.group_signal(signal.SIGTERM)
                self.assertLess(time.perf_counter() - start, bound)

    def test_stalled_reader_and_fallback_wait_allowances(self):
        release, entered = threading.Event(), threading.Event()

        def read(*_):
            entered.set()
            release.wait(2)
            return b""

        instance, _ = self.reader_command(read)
        with patch.object(keeper, "COMMAND_TIMEOUT", 0.001), capture_reports():
            start = time.perf_counter()
            try:
                instance.adb_command(["devices"])
                self.assertLess(time.perf_counter() - start, 0.3)
            finally:
                release.set()
                for reader in instance.abandoned_readers:
                    reader.join(timeout=1)
        instance.child = unittest.mock.Mock()

        def wait(timeout):
            time.sleep(timeout)
            raise subprocess.TimeoutExpired("fake", timeout)

        instance.child.wait.side_effect = wait
        start = time.perf_counter()
        instance.reap_completed_adb(unittest.mock.Mock(), True)
        self.assertLess(time.perf_counter() - start, 0.3)

    def test_abandoned_reader_cap_throttles_and_recovers(self):
        instance, _ = self.reader_command([b""])
        never = unittest.mock.Mock()
        never.is_alive.return_value = True
        now = [0.0]
        real_thread = threading.Thread
        # Model Windows-style readers which remain stuck after cancellation.
        with (
            patch.object(
                keeper.threading,
                "Thread",
                side_effect=lambda *a, **kw: never if kw.get("name") == "keeper-adb" else real_thread(*a, **kw),
            ),
            patch.object(keeper, "COMMAND_TIMEOUT", 0),
            patch.object(keeper.time, "monotonic", side_effect=lambda: now[0]),
            capture_reports() as output,
        ):
            for _ in range(4):
                self.assertIsNone(instance.adb_command(["devices"]))
            spawn = keeper.subprocess.Popen
            self.assertEqual(spawn.call_count, 4)
            for clock in (0, 1, 9, 10, 11):
                now[0] = clock
                self.assertIsNone(instance.adb_command(["devices"]))
            self.assertEqual(spawn.call_count, 4)
            self.assertEqual(len(instance.abandoned_readers), 4)
            self.assertEqual(instance.last_reader_warning, 10)
            never.is_alive.return_value = False
            instance.adb_command(["devices"])
            self.assertEqual(spawn.call_count, 5)
        self.assertEqual(output.getvalue().count("adb output reader limit reached; waiting"), 2)

    @unittest.skipUnless(os.name == "posix", "POSIX escaped descendant; Windows NOT_RUN")
    def test_twenty_escaped_adb_writers_leave_no_readers_or_descriptors(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            fake = root / "fake-adb"
            pidfile = root / "escaped"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, sys, time\n"
                "if os.fork() == 0:\n"
                " os.setsid()\n"
                " path = pathlib.Path(sys.argv[1])\n"
                " pending = path.with_suffix('.pending')\n"
                " pending.write_text(str(os.getpid()))\n"
                " os.replace(pending, path)\n"
                " time.sleep(60)\n"
                " os._exit(0)\n"
                "time.sleep(60)\n"
            )
            fake.chmod(0o700)
            instance = self.instance()
            instance.args.adb = str(fake)
            descriptors = []
            popen = subprocess.Popen

            def spawn(*args, **kwargs):
                child = popen(*args, **kwargs)
                descriptors.append(child.stdout.fileno())
                # Start the cancellation probe after the escaped writer is ready,
                # rather than racing interpreter startup against the 180 ms budget.
                end = time.monotonic() + 2
                while not pidfile.exists() and time.monotonic() < end:
                    time.sleep(0.002)
                if not pidfile.exists():
                    child.kill()
                    child.wait(timeout=1)
                    self.fail("fixture never published escaped PID before the probe")
                return child

            baseline = len(os.listdir("/dev/fd"))
            with (
                patch.object(keeper.subprocess, "Popen", side_effect=spawn),
                patch.object(keeper, "COMMAND_TIMEOUT", 0.18),
                capture_reports(),
            ):
                for _ in range(20):
                    try:
                        self.assertIsNone(instance.adb_command([str(pidfile)]))
                        self.assertTrue(pidfile.exists(), "fixture never published escaped PID")
                        end = time.monotonic() + 0.5
                        while any(t.name == "keeper-adb" for t in threading.enumerate()) and time.monotonic() < end:
                            time.sleep(0.005)
                        self.assertFalse(any(t.name == "keeper-adb" for t in threading.enumerate()))
                        with self.assertRaises(OSError):
                            os.fstat(descriptors[-1])
                        self.assertEqual(len(os.listdir("/dev/fd")), baseline)
                    finally:
                        if pidfile.exists():
                            pid = int(pidfile.read_text())
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            pidfile.unlink()

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    @unittest.skipUnless(hasattr(os, "WNOWAIT"), "non-reaping wait constants unavailable")
    def test_waitid_retry_once_and_permanent_capability_switch(self):
        for failure in (
            OSError(errno.ENOSYS, "unsupported"),
            OSError(errno.EINVAL, "unsupported"),
            InterruptedError(),
            OSError(errno.EIO, "temporary"),
        ):
            with self.subTest(error=repr(failure)):
                instance = self.instance()
                instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
                instance.child.poll.return_value = None
                instance.child_group = True
                with (
                    patch.object(keeper, "_WAITID_SUPPORTED", True),
                    patch.object(keeper.os, "waitid", side_effect=failure, create=True) as observe,
                    patch.object(keeper.time, "sleep") as sleep,
                ):
                    # Actual can_waitid is feature-detected on this interpreter.
                    with patch.object(keeper, "can_waitid", side_effect=lambda: keeper._WAITID_SUPPORTED):
                        self.assertTrue(instance.leader_alive())
                        permanent = failure.errno in (errno.ENOSYS, errno.EINVAL)
                        self.assertEqual(keeper.can_waitid(), not permanent)
                        self.assertEqual(observe.call_count, 1 if permanent else 2)
                        self.assertEqual(sleep.call_count, 0 if permanent else 1)
                        if not permanent:
                            sleep.assert_called_with(0.01)
                        self.assertIsNotNone(instance.child)

    @unittest.skipUnless(os.name == "posix", "POSIX fake child; Windows NOT_RUN")
    @unittest.skipUnless(hasattr(os, "WNOWAIT"), "non-reaping wait constants unavailable")
    def test_runtime_waitid_errors_still_kill_and_reap_real_child(self):
        with TemporaryDirectory() as folder:
            ready = Path(folder) / "ready"
            for failure in (
                OSError(errno.ENOSYS, "unsupported"),
                OSError(errno.EINVAL, "unsupported"),
                InterruptedError(),
            ):
                with self.subTest(error=repr(failure)):
                    instance = self.instance("ssh")
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            "import os,pathlib,signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                            "p=sys.argv[1];pathlib.Path(p+'.pending').write_text('ready');os.replace(p+'.pending',p);time.sleep(60)",
                            str(ready),
                        ],
                        start_new_session=True,
                    )
                    instance.child, instance.child_group = child, True
                    try:
                        end = time.monotonic() + 2
                        while not ready.exists() and time.monotonic() < end:
                            time.sleep(0.002)
                        self.assertTrue(ready.exists())
                        with (
                            patch.object(keeper, "_WAITID_SUPPORTED", True),
                            patch.object(keeper, "can_waitid", side_effect=lambda: keeper._WAITID_SUPPORTED),
                            patch.object(keeper.os, "waitid", side_effect=failure, create=True),
                        ):
                            instance.terminate()
                        self.assertIsNone(instance.child)
                        self.assertIsNotNone(child.returncode)
                        with self.assertRaises(ChildProcessError):
                            os.waitpid(child.pid, os.WNOHANG)
                        with self.assertRaises(ProcessLookupError):
                            os.kill(child.pid, 0)
                    finally:
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=1)
                        ready.unlink(missing_ok=True)

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    @unittest.skipUnless(hasattr(os, "WNOWAIT"), "non-reaping wait constants unavailable")
    def test_external_reaper_precheck_never_signals_lost_group(self):
        instance = self.instance("ssh")
        child = unittest.mock.Mock(pid=99999999, returncode=None)
        instance.child, instance.child_group = child, True
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", side_effect=ChildProcessError, create=True),
            patch.object(keeper.os, "killpg", create=True) as kill,
        ):
            instance.terminate()
        kill.assert_not_called()
        child.poll.assert_not_called()  # Bounded wait handles an externally reaped child.
        self.assertIn("timeout", child.wait.call_args.kwargs)
        self.assertIsNone(instance.child)
        # Ownership may also be lost while interpreting an EPERM refusal.
        instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
        instance.child_group = True
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", side_effect=ChildProcessError, create=True),
            patch.object(keeper.os, "killpg", create=True, side_effect=PermissionError(errno.EPERM, "denied")),
        ):
            with self.assertRaises(PermissionError):
                instance.group_signal(signal.SIGTERM)
        self.assertFalse(instance.child_group)

    def test_deadline_arithmetic_stop_observation_and_no_reset(self):
        clock = [10.0]
        with patch.object(keeper.time, "perf_counter", side_effect=lambda: clock[0]):
            stop = keeper.StopFlag()
            stop.set()
            self.assertIsNone(stop.deadline)
            self.assertTrue(stop.is_set())
            deadline = stop.deadline
            self.assertEqual(deadline.end, 11)
            clock[0] = 10.8
            self.assertTrue(stop.is_set())
            self.assertIs(stop.deadline, deadline)
            self.assertAlmostEqual(deadline.remaining(0.3), 0.2)
            clock[0] = 12
            self.assertEqual(deadline.remaining(), 0)
            stop2 = keeper.StopFlag()
            earlier = keeper.Deadline(stop=stop2)
            stop2.set()
            clock[0] = 12.2
            earlier.remaining()
            self.assertEqual(earlier.end, 13)
            self.assertEqual(stop2.deadline.end, 13.2)
            self.assertAlmostEqual(earlier.remaining(), 0.8)

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    def test_every_cleanup_wait_is_bounded_by_remaining_budget(self):
        for grouped in (True, False):
            with self.subTest(grouped=grouped), capture_reports() as output:
                instance = self.instance("ssh")
                child = unittest.mock.Mock(pid=99999999, returncode=None)
                child.poll.return_value = None
                child.wait.side_effect = subprocess.TimeoutExpired("fake", 0)
                instance.child, instance.child_group = child, grouped
                budget = keeper.Deadline(0)
                with (
                    patch.object(keeper, "can_waitid", return_value=False),
                    patch.object(keeper.os, "killpg", create=True) as kill,
                ):
                    instance.terminate(budget)
                self.assertIs(instance.child, child)
                for call in child.wait.call_args_list:
                    self.assertEqual(call.kwargs, {"timeout": 0})
                if grouped:
                    self.assertEqual(kill.call_args.args, (child.pid, signal.SIGKILL))
                    self.assertGreaterEqual(kill.call_count, 3)
                else:
                    self.assertEqual(child.kill.call_count, 2)
                instance.report.close()
                self.assertIn("cleanup incomplete: the SSH child may still be running", output.getvalue())

    def test_fallback_reader_wait_and_reporter_join_use_shared_budget(self):
        instance = self.instance()
        instance.child = unittest.mock.Mock()
        reader = unittest.mock.Mock()
        budget = keeper.Deadline(0.015)
        time.sleep(0.02)
        with patch.object(keeper, "can_waitid", return_value=False):
            instance.reap_completed_adb(reader, True, budget)
        reader.join.assert_called_once_with(timeout=0)
        instance.child.wait.assert_called_once_with(timeout=0)
        report = keeper.Reporter()
        report.writer = unittest.mock.Mock()
        # Close through a real writer afterward; the stub here owns no duplicate.
        try:
            report.close(timeout=1, deadline=budget)
            report.writer.join.assert_called_once_with(timeout=0)
        finally:
            report.writer = None
            report.close()

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    @unittest.skipUnless(hasattr(os, "WNOWAIT"), "non-reaping wait constants unavailable")
    def test_composed_shutdown_has_one_second_total_budget(self):
        instance = self.instance("ssh")
        child = unittest.mock.Mock(pid=99999999, returncode=None)
        instance.child, instance.child_group = child, True
        reader = unittest.mock.Mock()
        release, entered = threading.Event(), threading.Event()
        start = time.perf_counter()

        def observe(*_):
            # Grace .3s, then a denied kill whose exit notification lags .27s.
            return None if time.perf_counter() - start < 0.57 else unittest.mock.Mock()

        def kill(pid, signum):
            if signum == signal.SIGKILL:
                raise PermissionError(errno.EPERM, "notification delayed")

        def write(fd, data):
            entered.set()
            release.wait(2)
            return len(data)

        def wait(timeout):
            self.assertIsNotNone(timeout)
            return 0

        child.wait.side_effect = wait
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", side_effect=observe, create=True),
            patch.object(keeper.os, "killpg", create=True, side_effect=kill),
            patch.object(reader, "join", side_effect=lambda timeout: time.sleep(timeout)),
            patch.object(keeper.os, "write", side_effect=write),
        ):
            instance.report.emit("blocked")
            self.assertTrue(entered.wait(1))
            start = time.perf_counter()
            instance.stop.set()
            budget = instance.cleanup_deadline()
            try:
                instance.terminate(budget)
                reader.join(timeout=budget.remaining(keeper.READER_JOIN))
                instance.report.close(deadline=budget)
                self.assertLessEqual(time.perf_counter() - start, 1.1)
                self.assertGreater(time.perf_counter() - start, 0.9)
            finally:
                release.set()
                instance.report.writer.join(timeout=1)

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    def test_posix_reader_readiness_nonblocking_retry_and_cancel(self):
        stream = unittest.mock.Mock()
        stream.fileno.return_value = 99999999
        output = bytearray()
        complete, oversized, failed, cancel = (threading.Event() for _ in range(4))
        polls = []

        def readiness(descriptor, *, timeout):
            self.assertEqual((descriptor, timeout), (99999999, 0.05))
            polls.append(len(polls))
            return len(polls) != 1

        def read(fd, count):
            self.assertGreater(len(polls), 1, "read without readiness")
            if len(polls) == 2:
                raise BlockingIOError(errno.EAGAIN, "spurious readiness")
            return b"record" if len(polls) == 3 else b""

        with (
            patch.object(keeper.os, "name", "posix"),
            patch.object(keeper.os, "set_blocking") as blocking,
            patch.object(keeper, "wait_descriptor", side_effect=readiness),
            patch.object(keeper.os, "read", side_effect=read),
        ):
            bounded_read_adb_output(self, stream, output, complete, oversized, failed, cancel)
            blocking.assert_called_once_with(99999999, False)
        self.assertFalse(failed.is_set())
        self.assertTrue(complete.is_set())
        self.assertEqual(output, b"record")
        stream.close.assert_called_once()
        complete.clear()
        with (
            patch.object(keeper.os, "name", "posix"),
            patch.object(keeper.os, "set_blocking"),
            patch.object(keeper, "wait_descriptor", side_effect=lambda *args, **kw: (cancel.set(), True)[1]),
            patch.object(keeper.os, "read") as read,
        ):
            bounded_read_adb_output(self, stream, bytearray(), complete, oversized, failed, cancel)
        read.assert_not_called()
        self.assertTrue(complete.is_set())

    def test_windows_reader_keeps_blocking_pipe_path(self):
        stream = unittest.mock.Mock()
        stream.fileno.return_value = 99999999
        output = bytearray()
        events = [threading.Event() for _ in range(4)]
        with (
            patch.object(keeper.os, "name", "nt"),
            patch.object(keeper.os, "set_blocking") as blocking,
            patch.object(keeper, "wait_descriptor") as select,
            patch.object(keeper.os, "read", side_effect=[b"record", b""]),
        ):
            bounded_read_adb_output(self, stream, output, *events)
        self.assertEqual(output, b"record")
        self.assertTrue(events[0].is_set())
        blocking.assert_not_called()
        select.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    @unittest.skipUnless(hasattr(os, "WNOWAIT"), "non-reaping wait constants unavailable")
    def test_expired_shutdown_bounds_eperm_notification_and_final_reader_join(self):
        instance = self.instance()
        instance.child = unittest.mock.Mock(pid=99999999, returncode=None)
        instance.child_group = True
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", return_value=None, create=True),
            patch.object(keeper.os, "killpg", create=True, side_effect=PermissionError(errno.EPERM, "denied")),
        ):
            start = time.perf_counter()
            with self.assertRaises(PermissionError):
                instance.group_signal(signal.SIGKILL, keeper.Deadline(0))
            self.assertLess(time.perf_counter() - start, 0.05)
        instance.child = None
        reader = unittest.mock.Mock()
        reader.is_alive.return_value = True
        child = unittest.mock.Mock(stdout=unittest.mock.Mock(), pid=99999999, returncode=None)
        instance.stop.set()
        instance.stop.is_set()
        instance.stop.deadline.end = 0
        real_thread = threading.Thread
        with (
            patch.object(keeper.subprocess, "Popen", return_value=child),
            patch.object(
                keeper.threading,
                "Thread",
                side_effect=lambda *a, **kw: reader if kw.get("name") == "keeper-adb" else real_thread(*a, **kw),
            ),
            patch.object(instance, "terminate"),
        ):
            instance.adb_command(["devices"])
        reader.join.assert_called_once_with(timeout=0)
        self.assertEqual(instance.abandoned_readers, [reader])

    @unittest.skipUnless(os.name == "posix", "POSIX capability path; Windows NOT_RUN")
    def test_expired_shutdown_grace_and_error_wait_are_bounded(self):
        instance = self.instance("ssh")
        child = unittest.mock.Mock(pid=99999999, returncode=None)
        instance.child, instance.child_group = child, True
        child.poll.return_value = None
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", create=True),
            patch.object(keeper.time, "sleep", side_effect=AssertionError("waited beyond shutdown deadline")),
        ):
            instance.terminate(keeper.Deadline(0))
        self.assertEqual(child.wait.call_args.kwargs, {"timeout": 0})
        child.returncode = None
        instance.child, instance.child_group = child, True
        child.poll.return_value = 0
        child.wait.side_effect = subprocess.TimeoutExpired("fake", 0)
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", create=True, side_effect=PermissionError(errno.EPERM, "denied")),
            capture_reports(),
        ):
            with self.assertRaises(PermissionError):
                instance.terminate(keeper.Deadline(0))
        child.wait.assert_called_with(timeout=0)
        self.assertIs(instance.child, child)
        self.assertTrue(instance.cleanup_incomplete)

    def test_precancelled_reader_never_waits_or_reads(self):
        for platform in ("posix", "nt"):
            with self.subTest(platform=platform):
                stream = unittest.mock.Mock()
                stream.fileno.return_value = 99999999
                events = [threading.Event() for _ in range(4)]
                events[-1].set()
                with (
                    patch.object(keeper.os, "name", platform),
                    patch.object(keeper.os, "set_blocking"),
                    patch.object(keeper, "wait_descriptor", return_value=False) as ready,
                    patch.object(keeper.os, "read", return_value=b"") as read,
                ):
                    bounded_read_adb_output(self, stream, bytearray(), *events)
                ready.assert_not_called()
                read.assert_not_called()
                stream.close.assert_called_once()
                self.assertTrue(events[0].is_set())
                self.assertFalse(events[2].is_set())

    def test_incomplete_cleanup_never_claims_ssh_exited_or_terminated(self):
        instance = self.instance("ssh")

        def incomplete(*args, **kwargs):
            if not instance.cleanup_incomplete:
                instance.incomplete_cleanup()

        with (
            patch.object(keeper, "STARTUP", 0),
            patch.object(keeper.subprocess, "Popen"),
            patch.object(instance, "leader_alive", return_value=True),
            patch.object(instance.stop, "wait", side_effect=lambda delay: instance.stop.set()),
            patch.object(instance, "terminate", side_effect=incomplete),
            capture_reports() as output,
        ):
            self.assertEqual(instance.run(), 0)
        self.assertIn("cleanup incomplete: the SSH child may still be running", output.getvalue())
        self.assertNotIn("ssh exited", output.getvalue())
        self.assertNotIn("owned SSH child terminated", output.getvalue())
        self.assertIsNotNone(instance.child)


class RoundFiveTests(unittest.TestCase):
    def instance(self, quiet=False):
        obj = keeper.Keeper(keeper.arguments(["--mode", "adb"] + (["--quiet"] if quiet else [])))
        self.addCleanup(obj.report.close)
        return obj

    @unittest.skipUnless(os.name == "posix" and Path("/dev/fd").is_dir(), "descriptor inventory unavailable")
    def test_no_intermediate_descriptor_leak(self):
        before = len(os.listdir("/dev/fd"))
        report = keeper.Reporter()
        report.close(timeout=1)
        self.assertEqual(len(os.listdir("/dev/fd")), before)

    def test_preserve_callers_inheritance(self):
        fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.set_inheritable(fd, True)
            report = keeper.Reporter(fd=fd)
            try:
                self.assertTrue(os.get_inheritable(fd))
                self.assertFalse(os.get_inheritable(report.fd))
            finally:
                report.close(timeout=1)
        finally:
            os.close(fd)

    @unittest.skipUnless(os.name == "posix", "POSIX readiness path; Windows NOT_RUN")
    def test_robustness_injected_eintr_invalidates_records(self):
        # Injected EINTR is a robustness check, not ordinary CPython CLI behaviour.
        stream = unittest.mock.Mock()
        stream.fileno.return_value = 99
        events = [threading.Event() for _ in range(4)]
        data = bytearray()
        with (
            patch.object(keeper.os, "set_blocking"),
            patch.object(keeper, "wait_descriptor", return_value=True),
            patch.object(keeper.os, "read", side_effect=[InterruptedError(errno.EINTR, "injected"), b"record", b""]),
        ):
            bounded_read_adb_output(self, stream, data, *events)
        self.assertTrue(events[2].is_set())
        self.assertTrue(events[0].is_set())
        self.assertEqual(data, b"")
        stream.close.assert_called_once()

    def test_cap_warning_survives_quiet(self):
        obj = self.instance(True)
        obj.abandoned_readers = [unittest.mock.Mock(is_alive=lambda: True) for _ in range(4)]
        with capture_reports() as output:
            obj.adb_command(["devices"])
        self.assertIn("adb output reader limit reached; waiting", output.getvalue())

    def test_warning_rate_across_recovery(self):
        obj = self.instance()
        live = unittest.mock.Mock(is_alive=lambda: True)
        now = [0.0]
        with (
            patch.object(obj.report, "emit") as emit,
            patch.object(keeper.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(keeper.subprocess, "Popen", side_effect=OSError("fake unavailable")),
        ):
            obj.abandoned_readers = [live] * 4
            obj.adb_command(["devices"])
            now[0] = 1
            obj.abandoned_readers = [live] * 3
            obj.adb_command(["devices"])
            now[0] = 2
            obj.abandoned_readers = [live] * 4
            obj.adb_command(["devices"])
        warnings = [call for call in emit.call_args_list if call.args[0].startswith("adb output reader limit")]
        self.assertEqual(len(warnings), 1)

    def test_incomplete_child_not_overwritten(self):
        obj = self.instance()
        obj.child = unittest.mock.Mock()
        obj.cleanup_incomplete = True
        with patch.object(keeper.subprocess, "Popen", side_effect=AssertionError("new command")) as spawn:
            obj.adb_command(["devices"])
        spawn.assert_not_called()

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX wait constants unavailable")
    def test_capability_stays_disabled_across_instances(self):
        with patch.object(keeper, "_WAITID_SUPPORTED", True):
            obj = self.instance()
            obj.child = unittest.mock.Mock(pid=99999999, returncode=None)
            obj.child_group = True
            with patch.object(keeper.os, "waitid", side_effect=OSError(errno.ENOSYS, "unsupported"), create=True):
                obj.observe_exit()
            self.assertFalse(keeper._WAITID_SUPPORTED)
            self.instance()
            self.assertFalse(keeper._WAITID_SUPPORTED)
            with patch.object(keeper.os, "waitid", create=True) as waitid:
                obj.observe_exit()
            waitid.assert_not_called()

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX wait constants unavailable")
    def test_observation_retry_respects_expired_budget(self):
        obj = self.instance()
        obj.child = unittest.mock.Mock(pid=99999999, returncode=None)
        obj.child_group = True
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", side_effect=InterruptedError(), create=True),
            patch.object(keeper.time, "sleep") as sleep,
        ):
            obj.observe_exit(keeper.Deadline(0))
        sleep.assert_called_once_with(0)

    def test_latched_cleanup_does_not_escalate_again(self):
        obj = self.instance()
        obj.child = unittest.mock.Mock(returncode=None, pid=99999999)
        obj.child_group = True
        obj.cleanup_incomplete = True
        with (
            patch.object(keeper.os, "killpg", create=True) as kill,
            patch.object(obj, "terminate_group") as group,
        ):
            obj.terminate(keeper.Deadline(0))
        group.assert_not_called()
        kill.assert_not_called()

    def test_incomplete_warning_survives_quiet_and_is_transport_specific_once(self):
        for mode, label in (("adb", "adb"), ("ssh", "SSH")):
            with self.subTest(mode=mode), capture_reports() as output:
                obj = self.instance(True)
                obj.args.mode = mode
                obj.incomplete_cleanup()
                obj.incomplete_cleanup()
                obj.report.close(timeout=1)
            text = output.getvalue()
            self.assertEqual(text.count("cleanup incomplete:"), 1)
            self.assertIn(f"cleanup incomplete: the {label} child may still be running", text)

    def test_adb_terminal_state_stops_before_and_after_ensure_including_once(self):
        for already_incomplete in (False, True):
            for once in (False, True):
                with self.subTest(already_incomplete=already_incomplete, once=once):
                    obj = self.instance(True)
                    obj.args.once = once
                    obj.cleanup_incomplete = already_incomplete
                    calls = []

                    def ensure(obj=obj, calls=calls):
                        calls.append(True)
                        obj.cleanup_incomplete = True
                        return False

                    def wait(_, obj=obj, calls=calls):
                        # Keep a broken loop finite so the assertion diagnoses it.
                        if len(calls) >= 3:
                            obj.stop.set()

                    with patch.object(obj, "ensure_adb", side_effect=ensure), patch.object(obj.stop, "wait", wait):
                        self.assertEqual(obj.run(), 0)
                    self.assertEqual(len(calls), 0 if already_incomplete else 1)

    @unittest.skipUnless(os.name == "posix", "real fake executable uses POSIX process groups; Windows NOT_RUN")
    def test_exhausted_adb_cleanup_is_terminal_with_real_fake_child(self):
        with TemporaryDirectory() as folder, capture_reports() as output:
            script = Path(folder) / "fake_adb.py"
            ready = Path(folder) / "ready"
            script.write_text(
                "import os,pathlib,signal,time\n"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                f"p=pathlib.Path({str(ready)!r});p.with_suffix('.pending').write_text('ready');"
                "p.with_suffix('.pending').replace(p)\n"
                "time.sleep(60)\n"
            )
            obj = self.instance(True)
            obj.args.adb = sys.executable
            children = []
            original_spawn = subprocess.Popen

            def spawn(*args, **kwargs):
                child = original_spawn(*args, **kwargs)
                children.append(child)
                end = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < end:
                    time.sleep(0.002)
                self.assertTrue(ready.exists())
                original_wait = child.wait

                def exhausted_wait(timeout=None):
                    # Reproduce unconfirmed reaping at an injected exhausted deadline,
                    # independent of whether the OS schedules SIGKILL before wait(0).
                    if timeout == 0:
                        raise subprocess.TimeoutExpired("fake adb", timeout)
                    return original_wait(timeout=timeout)

                child.wait = exhausted_wait
                return child

            calls = []

            def ensure():
                calls.append(True)
                obj.adb_command([str(script)])
                return False

            def wait(_):
                if len(calls) >= 3:
                    obj.stop.set()

            try:
                with (
                    patch.object(keeper.subprocess, "Popen", side_effect=spawn) as spawns,
                    patch.object(keeper, "COMMAND_TIMEOUT", 0.01),
                    patch.object(obj, "cleanup_deadline", side_effect=lambda: keeper.Deadline(0)),
                    patch.object(obj, "ensure_adb", side_effect=ensure),
                    patch.object(obj.stop, "wait", side_effect=wait),
                ):
                    started = time.perf_counter()
                    self.assertEqual(obj.run(), 0)  # Same incomplete-cleanup status as SSH.
                    self.assertLess(time.perf_counter() - started, 1)
                    self.assertTrue(obj.cleanup_incomplete)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(spawns.call_count, 1)
                    for _ in range(100):
                        self.assertIsNone(obj.adb_command(["devices"]))
                    obj.terminate(keeper.Deadline(0))
                    self.assertEqual(spawns.call_count, 1)
                    self.assertIs(obj.child, children[0])
                obj.report.close(timeout=1)
                self.assertEqual(output.getvalue().count("cleanup incomplete: the adb child may still be running"), 1)
            finally:
                for child in children:
                    child.kill()
                    child.wait(timeout=2)
                for reader in obj.abandoned_readers:
                    reader.join(timeout=1)
                    self.assertFalse(reader.is_alive())

    @contextlib.contextmanager
    def high_duplicate(self, descriptor):
        import fcntl
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        high = None
        try:
            target = 1200 if hard == resource.RLIM_INFINITY else min(1200, hard)
            if soft < target:
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            try:
                high = fcntl.fcntl(descriptor, fcntl.F_DUPFD, 1100)
            except OSError as error:
                self.skipTest(f"cannot allocate descriptor >=1100: {error}")
            yield high
        except (OSError, ValueError) as error:
            if high is None:
                self.skipTest(f"cannot raise descriptor limit within hard limit: {error}")
            raise
        finally:
            if high is not None:
                os.close(high)
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    @unittest.skipUnless(os.name == "posix", "fcntl/resource high descriptors; Windows NOT_RUN")
    def test_high_descriptor_reader_receives_response_and_eof(self):
        read, write = os.pipe()
        try:
            with self.high_duplicate(read) as high:
                payload = b"List of devices attached\nfixture\tdevice\n"
                os.write(write, payload)
                os.close(write)
                write = None
                # Own a separate high duplicate: the reader alone closes its pipe.
                import fcntl

                owned = fcntl.fcntl(high, fcntl.F_DUPFD, 1101)
                stream = os.fdopen(owned, "rb", buffering=0)
                data = bytearray()
                events = [threading.Event() for _ in range(4)]
                reader = threading.Thread(target=keeper.read_adb_output, args=(stream, data, *events), daemon=True)
                reader.start()
                try:
                    self.assertTrue(events[0].wait(1), "high descriptor did not produce a complete response and EOF")
                finally:
                    events[3].set()
                    reader.join(timeout=1)
                self.assertFalse(reader.is_alive())
                self.assertEqual(data, payload)
                self.assertTrue(events[0].is_set())
                self.assertFalse(events[2].is_set())
                self.assertTrue(stream.closed)
                with self.assertRaises(OSError):
                    os.fstat(owned)
        finally:
            os.close(read)
            if write is not None:
                os.close(write)

    @unittest.skipUnless(os.name == "posix", "fcntl/resource high descriptors; Windows NOT_RUN")
    def test_high_descriptor_reporter_recovers_after_full_pipe_drains(self):
        read, write = os.pipe()
        report = None
        try:
            with self.high_duplicate(write) as high:
                import fcntl

                os.set_blocking(write, False)
                try:
                    while True:
                        os.write(write, b"x" * 4096)
                except BlockingIOError:
                    pass
                with patch.object(keeper.os, "dup", side_effect=lambda fd: fcntl.fcntl(fd, fcntl.F_DUPFD, high + 1)):
                    report = keeper.Reporter(fd=write)
                self.assertGreaterEqual(report.fd, 1100)
                report.emit("HIGH SINK RECOVERED")
                report.close(timeout=0.02)
                self.assertFalse(report.failed)
                self.assertTrue(report.writer.is_alive())
                os.set_blocking(read, False)
                data = bytearray()
                end = time.monotonic() + 2
                while b"HIGH SINK RECOVERED" not in data and time.monotonic() < end:
                    try:
                        data.extend(os.read(read, 65536))
                    except BlockingIOError:
                        time.sleep(0.002)
                report.writer.join(timeout=1)
                self.assertFalse(report.writer.is_alive())
                self.assertFalse(report.failed)
                self.assertIn(b"HIGH SINK RECOVERED", data)
                self.assertIsNone(report.fd)
        finally:
            os.close(read)
            os.close(write)
            if report is not None:
                report.close(timeout=1)

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_poll_masks_timeouts_and_empty_readiness(self):
        for writable in (False, True):
            for flags, expected in (
                (0, False),
                (keeper.select.POLLIN, not writable),
                (keeper.select.POLLOUT, writable),
                (keeper.select.POLLHUP, not writable),
                (keeper.select.POLLIN | keeper.select.POLLHUP, not writable),
            ):
                with self.subTest(writable=writable, flags=flags):
                    poller = unittest.mock.Mock()
                    poller.poll.return_value = [(1100, flags)] if flags else []
                    with patch.object(keeper.select, "poll", return_value=poller):
                        self.assertEqual(keeper.wait_descriptor(1100, writable=writable, timeout=0.05), expected)
                    poller.register.assert_called_once_with(
                        1100, keeper.select.POLLOUT if writable else keeper.select.POLLIN
                    )
                    poller.poll.assert_called_once_with(50)

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_poll_errors_fail_reader_and_close_only_in_reader(self):
        for flags in (keeper.select.POLLERR, keeper.select.POLLNVAL, keeper.select.POLLERR | keeper.select.POLLIN):
            with self.subTest(flags=flags):
                poller = unittest.mock.Mock()
                poller.poll.return_value = [(1100, flags)]
                stream = unittest.mock.Mock()
                stream.fileno.return_value = 1100
                events = [threading.Event() for _ in range(4)]
                with (
                    patch.object(keeper.select, "poll", return_value=poller),
                    patch.object(keeper.os, "set_blocking"),
                    patch.object(keeper.os, "read", side_effect=AssertionError("read after readiness failure")) as read,
                    patch.object(keeper.os, "fstat") as fstat,
                ):
                    with self.assertRaises((OSError, ValueError)):
                        keeper.wait_descriptor(1100)
                    bounded_read_adb_output(self, stream, bytearray(), *events)
                read.assert_not_called()
                fstat.assert_not_called()
                stream.close.assert_called_once()
                self.assertTrue(events[0].is_set())
                self.assertTrue(events[2].is_set())

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_poll_errors_disable_reporting_but_valid_nval_retries(self):
        for flags, valid, success in (
            (keeper.select.POLLERR, True, False),
            (keeper.select.POLLERR | keeper.select.POLLOUT, True, False),
            (keeper.select.POLLERR | keeper.select.POLLNVAL, True, False),
            (keeper.select.POLLNVAL, False, False),
            (keeper.select.POLLNVAL, True, True),
        ):
            with self.subTest(flags=flags, valid=valid):
                report = keeper.Reporter()
                owned = report.fd
                poller = unittest.mock.Mock()
                poller.poll.return_value = [(owned, flags)]
                with (
                    patch.object(keeper.select, "poll", return_value=poller),
                    patch.object(
                        keeper.os, "write", side_effect=[BlockingIOError(errno.EAGAIN, "retry"), 10000]
                    ) as write,
                    patch.object(keeper.os, "fstat", side_effect=None if valid else OSError("closed")) as fstat,
                    patch.object(keeper.time, "sleep") as sleep,
                ):
                    report.emit("retry record")
                    report.close(timeout=1)
                self.assertEqual(report.failed, not success)
                self.assertEqual(write.call_count, 2 if success else 1)
                if flags == keeper.select.POLLNVAL:
                    fstat.assert_called_once_with(owned)
                else:
                    fstat.assert_not_called()
                if success:
                    sleep.assert_called_once_with(0.05)
                else:
                    sleep.assert_not_called()
                self.assertIsNone(report.fd)
                with self.assertRaises(OSError):
                    os.fstat(owned)

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_readiness_exceptions_follow_existing_failure_paths(self):
        for failure in (OSError("poll failed"), ValueError("invalid"), InterruptedError(errno.EINTR, "injected")):
            with self.subTest(failure=failure):
                stream = unittest.mock.Mock()
                stream.fileno.return_value = 1100
                events = [threading.Event() for _ in range(4)]
                with (
                    patch.object(keeper.select, "poll", side_effect=failure),
                    patch.object(keeper.os, "set_blocking"),
                ):
                    bounded_read_adb_output(self, stream, bytearray(), *events)
                self.assertTrue(events[2].is_set())
                self.assertTrue(events[0].is_set())
                stream.close.assert_called_once()
                report = keeper.Reporter()
                with (
                    patch.object(keeper.select, "poll", side_effect=failure),
                    patch.object(keeper.os, "write", side_effect=BlockingIOError(errno.EAGAIN, "retry")),
                ):
                    report.emit("failed wait")
                    report.close(timeout=1)
                self.assertTrue(report.failed)
                self.assertIsNone(report.fd)

    def test_readiness_helper_uses_select_without_poll_on_windows(self):
        with (
            patch.object(keeper.os, "name", "nt"),
            patch.object(keeper.select, "poll", create=True) as poll,
            patch.object(keeper.select, "select", return_value=([99], [], [])) as select,
        ):
            self.assertTrue(keeper.wait_descriptor(99))
        poll.assert_not_called()
        select.assert_called_once_with([99], [], [], 0.05)

    def test_select_fallback_below_limit_and_sleep_retry_at_limit(self):
        for writable in (False, True):
            for descriptor in (99, 1023, 1024, 1100):
                with self.subTest(writable=writable, descriptor=descriptor):
                    with (
                        patch.object(keeper.select, "poll", create=True),
                        patch.object(keeper, "hasattr", return_value=False, create=True),
                        patch.object(
                            keeper.select,
                            "select",
                            return_value=([] if writable else [descriptor], [descriptor] if writable else [], []),
                        ) as select,
                        patch.object(keeper.time, "sleep") as sleep,
                    ):
                        self.assertTrue(keeper.wait_descriptor(descriptor, writable=writable, timeout=0.05))
                    if descriptor >= 1024:
                        select.assert_not_called()
                        sleep.assert_called_once_with(0.05)
                    else:
                        select.assert_called_once_with(
                            [] if writable else [descriptor], [descriptor] if writable else [], [], 0.05
                        )
                        sleep.assert_not_called()
        with (
            patch.object(keeper, "hasattr", return_value=False, create=True),
            patch.object(keeper.select, "select", return_value=([], [], [])),
        ):
            self.assertFalse(keeper.wait_descriptor(99))
            self.assertFalse(keeper.wait_descriptor(99, writable=True))

    @unittest.skipUnless(os.name == "posix", "nonblocking POSIX pipe; Windows NOT_RUN")
    def test_high_descriptor_without_poll_retries_read_and_eof(self):
        read, write = os.pipe()
        try:
            with self.high_duplicate(read) as high:
                import fcntl

                owned = fcntl.fcntl(high, fcntl.F_DUPFD, 1101)
                os.write(write, b"record\n")
                os.close(write)
                write = None
                events = [threading.Event() for _ in range(4)]
                data = bytearray()
                with patch.object(keeper, "hasattr", return_value=False, create=True):
                    reader = threading.Thread(
                        target=keeper.read_adb_output,
                        args=(os.fdopen(owned, "rb", buffering=0), data, *events),
                        daemon=True,
                    )
                    reader.start()
                    try:
                        self.assertTrue(events[0].wait(1), "high-descriptor retry failed to consume data and EOF")
                    finally:
                        events[3].set()
                        reader.join(timeout=1)
                    self.assertFalse(reader.is_alive())
                self.assertEqual(data, b"record\n")
                self.assertFalse(events[2].is_set())
                self.assertTrue(events[0].is_set())
        finally:
            os.close(read)
            if write is not None:
                os.close(write)


class RoundSixGapTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_poll_at_exact_boundary(self):
        poller = unittest.mock.Mock()
        poller.poll.return_value = [(1024, keeper.select.POLLIN)]
        with patch.object(keeper.select, "poll", return_value=poller), patch.object(keeper.time, "sleep"):
            self.assertTrue(keeper.wait_descriptor(1024))
        poller.register.assert_called_once_with(1024, keeper.select.POLLIN)
        poller.poll.assert_called_once_with(50)

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_helper_honors_nondefault_timeout(self):
        poller = unittest.mock.Mock()
        poller.poll.return_value = []
        with patch.object(keeper.select, "poll", return_value=poller):
            self.assertFalse(keeper.wait_descriptor(1100, timeout=0.007))
        poller.poll.assert_called_once_with(7)

    def instance(self):
        with patch.object(keeper, "Reporter"):
            obj = keeper.Keeper(keeper.arguments(["--mode", "adb"]))
        return obj

    def test_preterminal_retains_child(self):
        obj = self.instance()
        child = unittest.mock.Mock(returncode=None)
        obj.child = child
        obj.cleanup_incomplete = True
        with (
            patch.object(obj, "ensure_adb", side_effect=AssertionError("unexpected ensure")),
            patch.object(keeper.signal, "signal"),
        ):
            self.assertEqual(obj.run(), 0)
        self.assertIs(obj.child, child)

    def test_preterminal_never_polls_or_reaps_late_exit(self):
        obj = self.instance()
        child = unittest.mock.Mock(returncode=None)
        obj.child = child
        obj.cleanup_incomplete = True
        with patch.object(keeper.signal, "signal"):
            self.assertEqual(obj.run(), 0)
        child.poll.assert_not_called()
        child.wait.assert_not_called()

    def test_postterminal_never_waits_retry_interval(self):
        obj = self.instance()
        obj.args.interval = 30

        def ensure():
            obj.cleanup_incomplete = True
            return False

        with (
            patch.object(obj, "ensure_adb", side_effect=ensure),
            patch.object(obj.stop, "wait", side_effect=AssertionError("terminal retry wait")),
            patch.object(keeper.signal, "signal"),
        ):
            self.assertEqual(obj.run(), 0)

    def test_postterminal_at_reader_cap_precedes_once(self):
        obj = self.instance()
        obj.args.once = True
        obj.abandoned_readers = [unittest.mock.Mock() for _ in range(4)]

        def ensure():
            obj.cleanup_incomplete = True
            return False

        with patch.object(obj, "ensure_adb", side_effect=ensure), patch.object(keeper.signal, "signal"):
            self.assertEqual(obj.run(), 0)

    def syscall_error(self, stage, error):
        poller = unittest.mock.Mock()
        getattr(poller, stage).side_effect = error
        for writable in (False, True):
            with (
                self.subTest(stage=stage, error=error, writable=writable),
                patch.object(keeper.select, "poll", return_value=poller),
            ):
                with self.assertRaises(OSError):
                    keeper.wait_descriptor(1100, writable=writable)

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_poll_method_eagain(self):
        self.syscall_error("poll", BlockingIOError(35, "injected"))

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_poll_method_ebadf(self):
        self.syscall_error("poll", OSError(9, "injected"))

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_poll_method_eintr(self):
        self.syscall_error("poll", InterruptedError(4, "injected"))

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_registration_error(self):
        self.syscall_error("register", OSError(9, "injected"))

    @unittest.skipUnless(os.name == "posix" and hasattr(keeper.select, "poll"), "POSIX poll required")
    def test_empty_poll_consumes_only_its_own_wait(self):
        poller = unittest.mock.Mock()
        poller.poll.return_value = []
        with patch.object(keeper.select, "poll", return_value=poller), patch.object(keeper.time, "sleep") as sleep:
            self.assertFalse(keeper.wait_descriptor(1100))
        sleep.assert_not_called()


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX wait constants; Windows NOT_RUN")
class RoundSixCleanupTests(unittest.TestCase):
    def setUp(self):
        # Virtual sleeps advance the budget; a broken loop still fails fast.
        clock = [0.0, 0]

        def now():
            clock[1] += 1
            self.assertLess(clock[1], 1000, "cleanup exceeded the test's clock-read bound")
            return clock[0]

        def sleep(delay):
            clock[0] += max(0.0, delay)

        for name, function in (("perf_counter", now), ("sleep", sleep)):
            override = patch.object(keeper.time, name, side_effect=function)
            override.start()
            self.addCleanup(override.stop)

    def instance(self):
        with patch.object(keeper, "Reporter"):
            obj = keeper.Keeper(keeper.arguments(["--mode", "adb"]))
        obj.child = unittest.mock.Mock(pid=99999999, returncode=None)
        obj.child_group = True
        return obj

    def reap(self, child, status=0):
        confirmed = False

        def wait(**kwargs):
            nonlocal confirmed
            self.assertGreaterEqual(kwargs["timeout"], 0)
            if not confirmed and child.returncode is None:
                child.returncode = status
            confirmed = True
            return child.returncode

        return wait

    def test_reap_fixture_preserves_cached_status(self):
        child = unittest.mock.Mock(returncode=None)
        wait = self.reap(child, 7)
        self.assertEqual(wait(timeout=0), 7)
        self.assertEqual(child.returncode, 7)
        child.returncode = -signal.SIGTERM
        self.assertEqual(wait(timeout=0), -signal.SIGTERM)
        self.assertEqual(child.returncode, -signal.SIGTERM)
        child.returncode = None
        self.assertIsNone(wait(timeout=0))
        self.assertIsNone(child.returncode)

    def test_receipt_eperm_then_exit_observation_reaps_child(self):
        obj = self.instance()
        child = obj.child
        exited = unittest.mock.Mock(si_code=os.CLD_EXITED, si_status=0)
        child.wait.side_effect = self.reap(child)
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", side_effect=[None, exited, exited, exited], create=True) as observe,
            patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "zombie"), create=True) as kill,
            patch.object(keeper.time, "sleep") as sleep,
        ):
            obj.terminate()
        self.assertIsNone(obj.child)
        self.assertEqual(child.returncode, 0)
        self.assertFalse(obj.cleanup_incomplete)
        self.assertEqual(observe.call_count, 4)
        self.assertEqual([call.args[1] for call in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])
        child.wait.assert_called_once()
        child.poll.assert_not_called()
        sleep.assert_not_called()

    def test_fallback_eperm_confirms_exit_and_never_signals_reaped_group(self):
        for status in (0, 7, -signal.SIGTERM):
            with self.subTest(status=status):
                obj = self.instance()
                child = obj.child
                child.wait.side_effect = self.reap(child, status)

                def refuse(pid, signum, child=child):
                    self.assertIsNone(child.returncode, "group signal after reaping")
                    raise PermissionError(errno.EPERM, "zombie")

                with (
                    patch.object(keeper, "can_waitid", return_value=False),
                    patch.object(keeper.os, "waitid", create=True) as observe,
                    patch.object(keeper.os, "killpg", side_effect=refuse, create=True) as kill,
                ):
                    obj.terminate()
                self.assertIsNone(obj.child)
                self.assertEqual(child.returncode, status)
                self.assertFalse(obj.cleanup_incomplete)
                kill.assert_called_once_with(child.pid, signal.SIGTERM)
                observe.assert_not_called()
                self.assertGreater(child.wait.call_args_list[0].kwargs["timeout"], 0)
                self.assertLessEqual(child.wait.call_args_list[0].kwargs["timeout"], keeper.STOP_TIMEOUT)

    def test_live_fallback_denial_propagates_original_and_retains_child(self):
        obj = self.instance()
        child = obj.child
        child.wait.side_effect = subprocess.TimeoutExpired("fake", 0)
        child.poll.return_value = None
        denied = PermissionError(errno.EPERM, "live child")
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", side_effect=denied, create=True) as kill,
        ):
            with self.assertRaises(PermissionError) as caught:
                obj.terminate()
        self.assertIs(caught.exception, denied)
        self.assertIs(obj.child, child)
        self.assertTrue(obj.cleanup_incomplete)
        kill.assert_called_once()
        obj.report.emit.assert_called_once_with("cleanup incomplete: the adb child may still be running", error=True)

    def test_fallback_wait_uses_remaining_deadline_including_zero(self):
        for remaining in (0, 0.007):
            with self.subTest(remaining=remaining):
                obj = self.instance()
                child = obj.child
                child.wait.side_effect = self.reap(child)
                deadline = unittest.mock.Mock()
                deadline.remaining.return_value = remaining
                with (
                    patch.object(keeper, "can_waitid", return_value=False),
                    patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "zombie"), create=True),
                    patch.object(keeper.time, "sleep") as sleep,
                ):
                    obj.group_signal(signal.SIGTERM, deadline)
                child.wait.assert_called_once_with(timeout=remaining)
                self.assertEqual(child.returncode, 0)
                sleep.assert_not_called()

    def test_expired_fallback_live_child_still_propagates(self):
        obj = self.instance()
        obj.child.wait.side_effect = subprocess.TimeoutExpired("fake", 0)
        deadline = unittest.mock.Mock()
        deadline.remaining.return_value = 0
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "live"), create=True),
        ):
            with self.assertRaises(PermissionError):
                obj.group_signal(signal.SIGKILL, deadline)
        obj.child.wait.assert_called_once_with(timeout=0)

    def test_transient_observation_failure_uses_wait_without_resetting_grace(self):
        obj = self.instance()
        child = obj.child
        child.wait.side_effect = self.reap(child)
        deadline = unittest.mock.Mock()
        deadline.remaining.return_value = 0.009
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper, "_WAITID_SUPPORTED", True),
            patch.object(keeper.os, "waitid", side_effect=OSError(errno.EIO, "transient"), create=True) as observe,
            patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "zombie"), create=True),
            patch.object(keeper.time, "perf_counter", side_effect=[10, 10.298]),
            patch.object(keeper.time, "sleep") as sleep,
        ):
            obj.group_signal(signal.SIGTERM, deadline)
            self.assertTrue(keeper._WAITID_SUPPORTED)
        self.assertEqual(observe.call_count, 2)
        sleep.assert_called_once_with(0.009)
        self.assertAlmostEqual(child.wait.call_args.kwargs["timeout"], 0.002)
        self.assertTrue(obj.child_group)
        self.assertEqual(child.returncode, 0)

    def test_observation_capability_loss_can_still_confirm_exit(self):
        for code in (errno.ENOSYS, errno.EINVAL):
            with self.subTest(code=code):
                obj = self.instance()
                child = obj.child
                child.wait.side_effect = self.reap(child)
                with (
                    patch.object(keeper, "_WAITID_SUPPORTED", True),
                    patch.object(keeper.os, "waitid", side_effect=OSError(code, "unsupported"), create=True) as observe,
                    patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "zombie"), create=True),
                    patch.object(keeper.time, "sleep") as sleep,
                ):
                    obj.group_signal(signal.SIGKILL)
                    self.assertFalse(keeper.can_waitid())
                observe.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(child.returncode, 0)

    def test_external_reap_during_refusal_does_not_wait_or_claim_exit(self):
        obj = self.instance()
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(keeper.os, "waitid", side_effect=ChildProcessError, create=True),
            patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "lost"), create=True),
        ):
            with self.assertRaises(PermissionError):
                obj.group_signal(signal.SIGTERM)
        self.assertFalse(obj.child_group)
        obj.child.wait.assert_not_called()

    def test_refusal_requires_owned_unreaped_group(self):
        for group, returncode in ((False, None), (True, 0), (True, 7)):
            with self.subTest(group=group, returncode=returncode):
                obj = self.instance()
                obj.child_group = group
                obj.child.returncode = returncode
                with (
                    patch.object(keeper.os, "waitid", create=True) as observe,
                    patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "lost"), create=True),
                ):
                    with self.assertRaises(PermissionError):
                        obj.group_signal(signal.SIGTERM)
                observe.assert_not_called()
                obj.child.wait.assert_not_called()

    def test_late_observation_failure_gets_zero_wait_not_a_fresh_budget(self):
        obj = self.instance()
        child = obj.child
        child.wait.side_effect = self.reap(child)
        with (
            patch.object(keeper, "can_waitid", return_value=True),
            patch.object(
                keeper.os,
                "waitid",
                side_effect=[None, OSError(errno.EIO, "late"), OSError(errno.EIO, "late")],
                create=True,
            ),
            patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "zombie"), create=True),
            patch.object(keeper.time, "perf_counter", side_effect=[10, 10.29, 10.299, 10.31]),
            patch.object(keeper.time, "sleep") as sleep,
        ):
            obj.group_signal(signal.SIGTERM)
        child.wait.assert_called_once_with(timeout=0)
        self.assertEqual(sleep.call_count, 2)
        self.assertAlmostEqual(sleep.call_args_list[0].args[0], 0.001)

    def test_wait_errors_other_than_timeout_propagate(self):
        obj = self.instance()
        failure = OSError(errno.EIO, "wait failed")
        obj.child.wait.side_effect = failure
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", side_effect=PermissionError(errno.EPERM, "denied"), create=True),
        ):
            with self.assertRaises(OSError) as caught:
                obj.group_signal(signal.SIGTERM)
        self.assertIs(caught.exception, failure)

    def test_final_fallback_reap_finishes_cleanup(self):
        for status in (0, 7, -signal.SIGTERM):
            with self.subTest(status=status):
                with patch.object(keeper, "Reporter"):
                    obj = keeper.Keeper(keeper.arguments(["--mode", "adb"]))
                child = unittest.mock.Mock(pid=99999999, returncode=None)
                obj.child = child
                obj.child_group = True
                child.poll.return_value = None
                trace = []

                def wait(timeout, child=child, status=status, trace=trace):
                    trace.append(("wait", timeout))
                    if len([r for r in trace if r[0] == "wait"]) == 1:
                        raise subprocess.TimeoutExpired("fake", timeout)
                    if child.returncode is None:
                        child.returncode = status
                    return child.returncode

                def kill(pid, sig, child=child, trace=trace):
                    self.assertIsNone(child.returncode)
                    trace.append(("kill", int(sig)))
                    if len([r for r in trace if r[0] == "kill"]) == 3:
                        raise PermissionError(1, "exited between final wait and signal")

                child.wait.side_effect = wait
                deadline = unittest.mock.Mock()
                deadline.remaining.return_value = 0
                with (
                    patch.object(keeper, "can_waitid", return_value=False),
                    patch.object(keeper.os, "killpg", side_effect=kill, create=True),
                ):
                    obj.terminate(deadline)
                self.assertEqual(
                    trace,
                    [
                        ("kill", int(signal.SIGTERM)),
                        ("kill", int(signal.SIGKILL)),
                        ("wait", 0),
                        ("kill", int(signal.SIGKILL)),
                        ("wait", 0),
                    ],
                )
                self.assertEqual(child.returncode, status)
                self.assertIsNone(obj.child)
                self.assertFalse(obj.child_group)
                self.assertFalse(obj.cleanup_incomplete)
                obj.report.emit.assert_not_called()


@unittest.skipUnless(os.name == "posix", "POSIX fake children and signals; Windows NOT_RUN")
class RoundSevenCleanupTests(unittest.TestCase):
    def instance(self):
        with patch.object(keeper, "Reporter"):
            return keeper.Keeper(keeper.arguments(["--mode", "adb"]))

    def test_real_cached_exit_status_survives_fallback(self):
        for status in (7, -signal.SIGTERM):
            with self.subTest(status=status):
                code = (
                    "import sys;sys.exit(7)" if status == 7 else "import os,signal;os.kill(os.getpid(),signal.SIGTERM)"
                )
                child = subprocess.Popen([sys.executable, "-c", code])
                obj = self.instance()
                obj.child = child
                obj.child_group = True
                try:
                    with (
                        patch.object(keeper, "can_waitid", return_value=False),
                        patch.object(keeper.os, "killpg", side_effect=PermissionError(1, "denied"), create=True),
                    ):
                        obj.terminate()
                    self.assertIsNone(obj.child)
                    self.assertEqual(child.returncode, status)
                finally:
                    if child.returncode is None:
                        try:
                            os.kill(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        child.wait(timeout=2)

    def test_every_other_wait_exception_preserves_identity(self):
        for error in (ValueError("bad wait"), RuntimeError("bad wait"), ChildProcessError("bad wait")):
            with self.subTest(error=type(error).__name__):
                obj = self.instance()
                obj.child = unittest.mock.Mock(pid=99999999, returncode=None)
                obj.child_group = True
                obj.child.wait.side_effect = error
                with (
                    patch.object(keeper, "can_waitid", return_value=False),
                    patch.object(keeper.os, "killpg", side_effect=PermissionError(1, "denied"), create=True),
                ):
                    with self.assertRaises(type(error)) as caught:
                        obj.group_signal(signal.SIGTERM)
                self.assertIs(caught.exception, error)

    def test_native_live_timeout_retains_unreaped_handle(self):
        child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(10)"])
        obj = self.instance()
        obj.child = child
        obj.child_group = True
        try:
            deadline = unittest.mock.Mock()
            deadline.remaining.return_value = 0
            with (
                patch.object(keeper, "can_waitid", return_value=False),
                patch.object(keeper.os, "killpg", side_effect=PermissionError(1, "denied"), create=True),
            ):
                with self.assertRaises(PermissionError):
                    obj.terminate(deadline)
            self.assertIs(obj.child, child)
            self.assertIsNone(child.returncode)
            self.assertTrue(obj.cleanup_incomplete)
        finally:
            os.kill(child.pid, signal.SIGKILL)
            child.returncode = None
            child.wait(timeout=2)

    def test_fractional_millisecond_deadline_is_not_truncated(self):
        obj = self.instance()
        obj.child = unittest.mock.Mock(pid=99999999, returncode=None)
        obj.child_group = True

        def reap(timeout):
            if obj.child.returncode is None:
                obj.child.returncode = 0
            return obj.child.returncode

        obj.child.wait.side_effect = reap
        deadline = unittest.mock.Mock()
        deadline.remaining.return_value = 0.0007
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", side_effect=PermissionError(1, "denied"), create=True),
        ):
            obj.group_signal(signal.SIGTERM, deadline)
        obj.child.wait.assert_called_once_with(timeout=0.0007)

    def test_timeout_does_not_leak_command_in_exception_context(self):
        obj = self.instance()
        obj.child = unittest.mock.Mock(pid=99999999, returncode=None)
        obj.child_group = True
        obj.child.wait.side_effect = subprocess.TimeoutExpired("PRIVATE-COMMAND-MARKER", 0)
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", side_effect=PermissionError(1, "denied"), create=True),
        ):
            with self.assertRaises(PermissionError) as caught:
                obj.group_signal(signal.SIGTERM)
            formatted = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn("PRIVATE-COMMAND-MARKER", formatted)

    def test_exit_after_timeout_does_not_emit_false_incomplete(self):
        obj = self.instance()
        child = unittest.mock.Mock(pid=99999999, returncode=None)
        obj.child = child
        obj.child_group = True

        def wait(timeout):
            if child.returncode is None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return child.returncode

        def poll():
            child.returncode = 0
            return 0

        child.wait.side_effect = wait
        child.poll.side_effect = poll
        deadline = unittest.mock.Mock()
        deadline.remaining.return_value = 0
        with (
            patch.object(keeper, "can_waitid", return_value=False),
            patch.object(keeper.os, "killpg", side_effect=PermissionError(1, "denied"), create=True),
        ):
            with self.assertRaises(PermissionError):
                obj.terminate(deadline)
        self.assertIsNone(obj.child)
        obj.report.emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
