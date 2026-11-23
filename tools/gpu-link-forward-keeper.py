# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep one GPU Link forward for the target and options supplied.

SSH reports child liveness after a 1.0s startup observation, not forward readiness.
ADB reports a listed/created mapping and adb operation duration, not end-to-end
reachability. Only --status probes TCP; listener identity remains unverified.
Inherited ssh configuration (e.g. ProxyCommand or extra forwards) is not isolated
unless --ssh-config points to a minimal file; then --ssh-identity is required.
Identity/config files are checked by metadata only, never read by the keeper.
Handled shutdown shares one 1.0s waiting budget from the first observed stop across
child cleanup, reader joins and output drain (at most 0.3s for output). This is
not proof of completed termination or a bound on arbitrary scheduling delays.
A cleanup wait timeout causes one final escalation attempt. Signalling or
observation errors may instead propagate after incomplete cleanup is recorded.
Unconfirmed cleanup is terminal in both modes: it retains the child handle,
starts no further command and reports once "cleanup incomplete: the SSH child
may still be running" (SSH) or "cleanup incomplete: the adb child may still be
running" (adb). The non-exceptional terminal-cleanup path returns 0 in both modes;
signalling errors may propagate.
A further signal that arrives after cleanup has restored the original handlers
ends the keeper by that signal's default action.
Cleanup targets the child and, on POSIX, its process group. Descendants that
change process group or session, for example with setpgid() or setsid(), are not
tracked. The keeper requires exclusive ownership of reaping its child: no
SIGCHLD handler that reaps and no other wait. When non-reaping waitid observation
is available, the ownership precheck can detect an already externally reaped
child. It does not protect fallback operation or prevent concurrent reaping.
EPERM from a group signal is accepted only after confirming leader exit. When
non-reaping observation is unavailable or fails transiently, a bounded child wait
may confirm exit by reaping; no later group signal is sent after reaping. If leader exit
cannot be confirmed, `group_signal()` raises the original signalling error.
Normal cleanup propagates that error; the final best-effort escalation suppresses
`OSError` and records incomplete cleanup. A leader confirmed exited by the final
escalation's fallback wait is treated as cleaned up.
Leader exit does not prove all descendants are gone. Without waitid/WNOWAIT, group signals stop once the
leader is reaped; same-group descendants may then survive. Runtime ENOSYS/EINVAL
disables waitid process-wide, including later Keeper objects; other observation
errors retry once after up to 10 ms, clamped by any supplied cleanup deadline,
then fall back for that call. On Windows the direct child is
terminated; Windows descendants are not tracked (NOT_RUN). A SIGKILLed keeper
cannot clean up: run --status before starting and stop a stale ssh by hand.
SSH stdout/stderr and adb stderr are discarded; adb stdout is parsed as records
and never echoed. Create must exit successfully and produce no nonblank stdout
records accepted by the parser; blank lines and ordinary spaces are permitted.
The mapping is not re-listed after the no-overwrite create.
The daemon reporter owns a non-inheritable duplicate of the sink. If duplication
or inheritance protection fails, reporting is silently disabled. It holds at
most 64 queued report entries plus one being written (help is one multiline
entry). Overflow drops the oldest entries; a later successful write reports
accumulated overflow losses once as "(N lines not shown)". Shutdown and sink
failure may prevent that notice or final status delivery. EAGAIN retries while
readiness waiting succeeds. A readiness-wait error also disables reporting, as
do other write errors. Only the writer closes its duplicate, even if shutdown
abandons it.
POSIX adb readers use cancellable nonblocking readiness waits at any descriptor
number. Readiness uses poll where available; without poll, descriptors below
1024 use select and higher descriptors sleep then retry nonblocking I/O. A
reporter POLLNVAL on a still-valid sink also sleeps then retries the write.
Blocking pipe readers on Windows may be abandoned. At most four abandoned readers are admitted;
no new adb command starts at the cap, and "adb output reader limit reached;
waiting" is reported once per ten seconds while polling for a reader to finish.
Escaped descendants may keep stdout open but do not accumulate POSIX readers.
Real SSH, adb, Windows and cable recovery remain NOT_RUN.
"""

from __future__ import annotations

import argparse
import errno
import math
import os
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

STARTUP = 1.0
COMMAND_TIMEOUT = 1.0
STOP_TIMEOUT = 0.3
OUTPUT_LIMIT = 64 * 1024
REPORT_LIMIT = 64
READER_JOIN = 0.2
READER_LIMIT = 4
SHUTDOWN_TIMEOUT = 1.0
_WAITID_SUPPORTED = True


def clean(value):
    return "".join(c for c in value if unicodedata.category(c) not in ("Cc", "Cf", "Cs"))


class Parser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            report = Reporter(fd=2 if file is sys.stderr else 1)
            report.enqueue(message.encode("utf-8"))
            report.close(timeout=1.0)

    def error(self, message):
        # argparse's default diagnostics can echo user values (including paths).
        super().error("invalid arguments; use --help for syntax and limits")


def arguments(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--mode", choices=("ssh", "adb"))
    parser.add_argument("--ssh-target", help="USER@HOST using letters, digits, dot, dash, underscore and colon")
    parser.add_argument("--ssh-identity", help="existing regular file; metadata checked, contents never read")
    parser.add_argument("--ssh-config", help="existing regular minimal config file; requires --ssh-identity")
    parser.add_argument("--ssh-port", type=int, help="SSH port, 1..65535")
    parser.add_argument("--local-port", type=int, default=8767, help="loopback port, 1024..65535")
    parser.add_argument("--remote-port", type=int, default=8767, help="phone loopback port, 1..65535")
    parser.add_argument("--interval", type=float, default=0.5, help="poll/retry seconds, 0.1..30")
    for name in ("ssh", "adb"):
        parser.add_argument(
            "--" + name,
            default=name,
            help="trusted executable code: one path or command name; existing paths may have spaces",
        )
    parser.add_argument(
        "--serial", help="one device serial, letters/digits/dot/dash/underscore/colon, at most 64 characters"
    )
    parser.add_argument("--quiet", action="store_true", help="print only errors")
    parser.add_argument(
        "--status", action="store_true", help="read-only TCP probe, at most 0.5 seconds; no mode needed"
    )
    parser.add_argument("--once", action="store_true", help="adb only: ensure once and exit")
    args = parser.parse_args(argv)
    if not 1024 <= args.local_port <= 65535 or not 1 <= args.remote_port <= 65535:
        parser.error("invalid port")
    if args.ssh_port is not None and not 1 <= args.ssh_port <= 65535:
        parser.error("invalid SSH port")
    if not math.isfinite(args.interval) or not 0.1 <= args.interval <= 30:
        parser.error("invalid interval")
    if (
        args.ssh_target is not None
        and re.fullmatch(r"[A-Za-z0-9._:][A-Za-z0-9._:-]*@[A-Za-z0-9._:][A-Za-z0-9._:-]*", args.ssh_target) is None
    ):
        parser.error("invalid target")
    if args.serial is not None and re.fullmatch(r"[A-Za-z0-9._:][A-Za-z0-9._:-]{0,63}", args.serial) is None:
        parser.error("invalid serial")
    validate_resources(args, parser)
    if args.status and args.once:
        parser.error("status and once are exclusive")
    if not args.status and args.mode is None:
        parser.error("mode required")
    if args.once and args.mode == "ssh":
        parser.exit(2, "--once is refused in ssh mode: exiting would terminate the owned forward.\n")
    if args.mode == "ssh" and not args.status and not args.ssh_target:
        parser.error("SSH target required")
    if args.mode == "adb" and any(
        x is not None for x in (args.ssh_target, args.ssh_identity, args.ssh_port, args.ssh_config)
    ):
        parser.error("SSH options require ssh mode")
    if args.mode == "ssh" and args.serial is not None:
        parser.error("serial requires adb mode")
    return args


def validate_resources(args, parser):
    for value in (args.ssh_identity, args.ssh_config):
        if value is not None:
            try:
                regular = "\0" not in value and Path(value).is_file()
            except OSError:
                regular = False
            if not regular:
                parser.error("identity/config must be a regular file")
    if args.ssh_config is not None and args.ssh_identity is None:
        parser.error("config requires identity")
    for executable in (args.ssh, args.adb):
        try:
            valid = bool(executable) and clean(executable) == executable
            if valid:
                try:
                    regular = stat.S_ISREG(Path(executable).stat().st_mode)
                except FileNotFoundError:
                    regular = False
                valid = not any(c.isspace() for c in executable) or regular
        except OSError:
            valid = False
        if not valid:
            parser.error("invalid executable")


class Reporter:
    def __init__(self, quiet=False, fd=1):
        self.quiet = quiet
        self.state = None
        self.detail = None
        self.last_error = -math.inf
        self.fd = None
        disabled = False
        try:
            self.fd = os.dup(fd)
            os.set_inheritable(self.fd, False)
        except OSError:
            disabled = True
        self.queue = deque()
        self.condition = threading.Condition()
        self.dropped = 0
        self.closed = False
        self.failed = disabled
        self.writer = None

    def enqueue(self, data):
        # No sink I/O under this lock, and the signal handler never takes it.
        with self.condition:
            if self.closed or self.failed:
                return
            if len(self.queue) >= REPORT_LIMIT:
                self.queue.popleft()
                self.dropped += 1
            self.queue.append(data)
            if self.writer is None:
                self.writer = threading.Thread(target=self.write_output, daemon=True, name="keeper-report")
                self.writer.start()
            self.condition.notify()

    def write_output(self):
        try:
            while True:
                with self.condition:
                    while not self.queue and not self.closed:
                        self.condition.wait()
                    if not self.queue:
                        return
                    data = self.queue.popleft()
                    dropped, self.dropped = self.dropped, 0
                if dropped:
                    data = f"({dropped} lines not shown) ".encode("ascii") + data
                while data:
                    try:
                        written = os.write(self.fd, data)
                    except BlockingIOError:
                        if os.name == "posix":
                            wait_descriptor(self.fd, writable=True, timeout=0.05)
                        else:
                            time.sleep(0.05)
                        continue
                    if written <= 0:
                        raise OSError("output closed")
                    data = data[written:]
        except (OSError, ValueError):
            with self.condition:
                self.failed = True
                self.queue.clear()
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def close(self, timeout=0.3, deadline=None):
        with self.condition:
            self.closed = True
            # Even an unused duplicate is closed only by its writer.
            if self.writer is None and self.fd is not None:
                self.writer = threading.Thread(target=self.write_output, daemon=True, name="keeper-report")
                self.writer.start()
            self.condition.notify()
        if self.writer is not None:
            # A blocked os.write owns no Python stream lock; abandon the daemon.
            self.writer.join(timeout=deadline.remaining(timeout) if deadline else timeout)

    def emit(self, message, error=False):
        if error or not self.quiet:
            stamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            self.enqueue(f"{stamp} {clean(message)}\n".encode())

    def error(self, message):
        now = time.monotonic()
        if now - self.last_error >= 10:
            self.emit(message, error=True)
            self.last_error = now

    def change(self, state, detail=""):
        if self.state == state and self.detail == detail:
            return
        self.state, self.detail = state, detail
        self.emit(f"{state}: {detail}" if detail else state, error=state == "conflict")


def ssh_command(args):
    command = [args.ssh]
    if args.ssh_config is not None:
        command.extend(("-F", args.ssh_config))
    command.extend(("-N", "-T", "-L", f"127.0.0.1:{args.local_port}:127.0.0.1:{args.remote_port}"))
    for option in (
        "ExitOnForwardFailure=yes",
        "BatchMode=yes",
        "ServerAliveInterval=1",
        "ServerAliveCountMax=2",
        "ConnectTimeout=3",
        "StrictHostKeyChecking=yes",
        "ForwardAgent=no",
        "ForwardX11=no",
        "PermitLocalCommand=no",
    ):
        command.extend(("-o", option))
    if args.ssh_identity is not None:
        command.extend(("-i", args.ssh_identity))
    if args.ssh_port is not None:
        command.extend(("-p", str(args.ssh_port)))
    return [*command, args.ssh_target]


class Deadline:
    """One finite budget, shared by all cleanup phases and shortened by a stop."""

    def __init__(self, allowance=SHUTDOWN_TIMEOUT, stop=None):
        self.end = time.perf_counter() + allowance
        self.stop = stop

    def remaining(self, allowance=SHUTDOWN_TIMEOUT):
        end = self.end
        if self.stop is not None and self.stop.is_set():
            end = min(end, self.stop.deadline.end)
        return max(0.0, min(allowance, end - time.perf_counter()))


class StopFlag:
    """A signal handler may set this flag even while wait() is running.

    threading.Event.set() takes a non-reentrant lock; interrupting Event.wait()
    in the same thread with a Python signal handler can deadlock on that lock.
    """

    def __init__(self):
        self.requested = False
        self.deadline = None

    def set(self):
        self.requested = True

    def is_set(self):
        if self.requested and self.deadline is None:
            self.deadline = Deadline()
        return self.requested

    def wait(self, delay):
        deadline = time.monotonic() + delay
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        return self.requested


def can_waitid():
    return (
        _WAITID_SUPPORTED
        and os.name == "posix"
        and all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "P_PID", "WEXITED", "WNOHANG"))
    )


def wait_descriptor(descriptor, *, writable=False, timeout=0.05):
    """Wait at most timeout; unknown readiness permits a nonblocking I/O retry."""
    if os.name == "posix" and hasattr(select, "poll"):
        poller = select.poll()
        interest = select.POLLOUT if writable else select.POLLIN
        poller.register(descriptor, interest)
        events = poller.poll(int(timeout * 1000))
        for _, flags in events:
            if flags & select.POLLERR:
                raise OSError("descriptor readiness failed")
            if flags & select.POLLNVAL:
                if writable:
                    # macOS poll can reject a valid device such as a terminal.
                    os.fstat(descriptor)
                    time.sleep(timeout)
                    return True
                raise OSError("invalid read descriptor")
            if flags & (interest | (0 if writable else select.POLLHUP)):
                return True
        return False
    if descriptor >= 1024:
        time.sleep(timeout)
        return True
    reads, writes, _ = select.select([] if writable else [descriptor], [descriptor] if writable else [], [], timeout)
    return bool(writes if writable else reads)


def read_adb_output(stream, output, complete, oversized, read_failed, cancel):
    # This thread alone owns and closes the unbuffered pipe descriptor.
    try:
        descriptor = stream.fileno()
        if os.name == "posix":
            os.set_blocking(descriptor, False)
        while not cancel.is_set():
            if os.name == "posix":
                ready = wait_descriptor(descriptor, timeout=0.05)
                if cancel.is_set():
                    break
                if not ready:
                    continue
            try:
                chunk = os.read(descriptor, min(4096, OUTPUT_LIMIT + 1 - len(output)))
            except BlockingIOError:
                continue
            if cancel.is_set() or not chunk:
                break
            output.extend(chunk)
            if len(output) > OUTPUT_LIMIT:
                oversized.set()
                break
    except Exception:
        read_failed.set()
    finally:
        try:
            stream.close()
        except Exception:
            read_failed.set()
        complete.set()


class Keeper:
    def __init__(self, args):
        self.args = args
        self.stop = StopFlag()
        self.report = Reporter(args.quiet)
        self.child = None
        self.child_group = False
        self.selected = args.serial
        self.adb_observed = False
        self.abandoned_readers = []
        self.last_reader_warning = -math.inf
        self.cleanup_incomplete = False

    def cleanup_deadline(self):
        return self.stop.deadline if self.stop.is_set() else Deadline(stop=self.stop)

    def observe_exit(self, deadline=None):
        """Return (observed, status), falling back without discarding ownership."""
        global _WAITID_SUPPORTED
        if not self.child_group or not can_waitid() or self.child.returncode is not None:
            return False, None
        for attempt in range(2):
            try:
                return True, os.waitid(os.P_PID, self.child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                self.child_group = False
                return False, None
            except OSError as error:
                if error.errno in (errno.ENOSYS, errno.EINVAL):
                    _WAITID_SUPPORTED = False
                    break
                if attempt == 0:
                    time.sleep(deadline.remaining(0.01) if deadline else 0.01)
        return False, None

    def leader_alive(self, deadline=None):
        observed, result = self.observe_exit(deadline)
        if observed:
            return result is None
        return self.child.poll() is None

    def group_signal(self, signum, deadline=None):
        try:
            os.killpg(self.child.pid, signum)
        except ProcessLookupError:
            pass
        except PermissionError as denied:
            # macOS may refuse zombie-only groups, including on builds without
            # waitid. Confirm exit within the existing grace/deadline, never
            # interpreting a refused signal alone as evidence of termination.
            if not self.child_group or self.child.returncode is not None:
                raise
            end = time.perf_counter() + STOP_TIMEOUT
            observed, result = self.observe_exit(deadline)
            while observed and result is None and time.perf_counter() < end:
                remaining = end - time.perf_counter()
                if deadline is not None:
                    remaining = min(remaining, deadline.remaining())
                if remaining <= 0:
                    break
                time.sleep(min(0.001, remaining))
                observed, result = self.observe_exit(deadline)
            if not observed and self.child_group:
                # Observation failure is not exit evidence. A bounded wait can
                # confirm and reap an owned child; returncode then prevents all
                # subsequent group signals. Do not restart the grace period.
                remaining = max(0.0, end - time.perf_counter())
                if deadline is not None:
                    remaining = min(remaining, deadline.remaining())
                try:
                    self.child.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    raise denied from None
                return
            if not observed or result is None:
                raise

    def incomplete_cleanup(self):
        if self.cleanup_incomplete:
            return
        self.cleanup_incomplete = True
        transport = "adb" if self.args.mode == "adb" else "SSH"
        self.report.emit(f"cleanup incomplete: the {transport} child may still be running", error=True)

    def terminate_group(self, deadline):
        # Check non-reaping ownership before the first group signal.
        # Exclusive reaping is still required: this is no concurrent-
        # reaper containment mechanism. Fallback polling can reap.
        self.observe_exit(deadline)
        if self.child_group and self.child.returncode is None:
            self.group_signal(signal.SIGTERM, deadline)
            alive = self.leader_alive(deadline)
            end = time.perf_counter() + STOP_TIMEOUT
            while alive and time.perf_counter() < end and deadline.remaining() > 0:
                time.sleep(max(0.0, min(0.01, end - time.perf_counter(), deadline.remaining())))
                alive = self.leader_alive(deadline)
            if self.child_group and self.child.returncode is None and (can_waitid() or alive):
                self.group_signal(signal.SIGKILL, deadline)
        self.child.wait(timeout=deadline.remaining())

    def terminate_direct(self, deadline):
        if self.child.poll() is None:
            self.child.terminate()
        try:
            self.child.wait(timeout=deadline.remaining(STOP_TIMEOUT))
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=deadline.remaining())

    def terminate(self, deadline=None):  # noqa: C901 - Keep the cleanup ownership/error paths together.
        if self.child is None or self.cleanup_incomplete:
            return
        deadline = deadline if deadline is not None else self.cleanup_deadline()
        child = self.child
        reaped = False
        try:
            if self.child_group:
                self.terminate_group(deadline)
            else:
                self.terminate_direct(deadline)
            reaped = True
        except ProcessLookupError:
            try:
                child.wait(timeout=deadline.remaining())
                reaped = True
            except subprocess.TimeoutExpired:
                pass
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            # Preserve the error/exit rules, but retain a live child's handle.
            if not self.leader_alive(deadline):
                try:
                    child.wait(timeout=deadline.remaining())
                    reaped = True
                except subprocess.TimeoutExpired:
                    pass
            if not reaped:
                self.incomplete_cleanup()
            raise
        finally:
            if reaped:
                self.child = None
                self.child_group = False
        if not reaped:
            # One last escalation at budget exhaustion; never signal a reaped id.
            try:
                if child.returncode is None:
                    if self.child_group:
                        self.group_signal(signal.SIGKILL, deadline)
                    else:
                        child.kill()
            except OSError:
                pass
            if child.returncode is not None:
                self.child = None
                self.child_group = False
                return
            self.incomplete_cleanup()

    def run_ssh(self):
        delay = self.args.interval
        immediate_exits = 0
        last_hint = -math.inf
        self.report.change("waiting", "starting SSH forward")
        while not self.stop.is_set():
            started = time.monotonic()
            running = False
            try:
                isolation = (
                    {"start_new_session": True}
                    if os.name == "posix"
                    else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                )
                self.child = subprocess.Popen(
                    ssh_command(self.args),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **isolation,
                )
                self.child_group = os.name == "posix"
                while not self.stop.is_set() and self.leader_alive():
                    elapsed = time.monotonic() - started
                    if not running and elapsed >= STARTUP:
                        running = True
                        immediate_exits = 0
                        delay = self.args.interval
                        self.report.change("ssh running (child alive for 1.0s; forward readiness not verified)")
                        self.report.emit(f"startup observation {elapsed:.3f}s")
                    self.stop.wait(0.05)
                if not self.stop.is_set():
                    if not running:
                        immediate_exits += 1
                    self.report.error("SSH child exited; retrying")
                    now = time.monotonic()
                    if immediate_exits >= 3 and now - last_hint >= 30:
                        self.report.emit("hint: a stale ssh may be holding the local port; run --status", error=True)
                        last_hint = now
            except OSError:
                self.report.error("SSH executable unavailable or could not start; retrying")
            finally:
                self.terminate()
                if running and self.child is None:
                    self.report.change(f"ssh exited (was running {time.monotonic() - started:.1f}s)")
            if self.stop.is_set() or self.cleanup_incomplete:
                break
            self.report.change("waiting", "SSH forward unavailable; retrying")
            self.stop.wait(delay)
            delay = max(self.args.interval, min(1.0, delay * 2)) if not running else self.args.interval
        return 0

    def child_succeeded(self):
        observed, result = self.observe_exit()
        if observed:
            return result is not None and result.si_code == os.CLD_EXITED and result.si_status == 0
        return self.child.poll() == 0

    def reap_completed_adb(self, reader, finished, deadline=None):
        if not finished or self.child is None or can_waitid():
            return
        deadline = deadline if deadline is not None else self.cleanup_deadline()
        # A finished reader permits fallback reaping before group cleanup.
        reader.join(timeout=deadline.remaining(READER_JOIN))
        try:
            self.child.wait(timeout=deadline.remaining(READER_JOIN))
        except subprocess.TimeoutExpired:
            pass

    def adb_command(self, command):
        self.abandoned_readers = [reader for reader in self.abandoned_readers if reader.is_alive()]
        if len(self.abandoned_readers) >= READER_LIMIT:
            now = time.monotonic()
            if now - self.last_reader_warning >= 10:
                self.report.emit("adb output reader limit reached; waiting", error=True)
                self.last_reader_warning = now
            return None
        if self.child is not None:
            return None  # Never overwrite responsibility for incomplete cleanup.
        prefix = [self.args.adb] + (["-s", self.selected] if self.selected else [])
        reader = None
        output = bytearray()
        complete = threading.Event()
        oversized = threading.Event()
        read_failed = threading.Event()
        cancel = threading.Event()

        try:
            self.child = subprocess.Popen(
                prefix + command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                **({"start_new_session": True} if os.name == "posix" else {}),
            )
            self.child_group = os.name == "posix"
            stream, self.child.stdout = self.child.stdout, None
            reader = threading.Thread(
                target=read_adb_output,
                args=(stream, output, complete, oversized, read_failed, cancel),
                daemon=True,
                name="keeper-adb",
            )
            reader.start()
            deadline = time.monotonic() + COMMAND_TIMEOUT
            while not self.stop.is_set():
                if oversized.is_set():
                    self.report.error("adb output too large; waiting")
                    return None
                if time.monotonic() >= deadline:
                    break
                if complete.is_set() and not self.leader_alive():
                    if self.child_succeeded() and not read_failed.is_set():
                        return self.adb_records(bytes(output), command)
                    break
                self.stop.wait(0.01)
            if not self.stop.is_set():
                self.report.error("adb command failed or timed out; waiting")
        except OSError:
            self.report.error("adb executable unavailable or could not start; waiting")
        finally:
            cancel.set()
            deadline = self.cleanup_deadline()
            try:
                self.reap_completed_adb(reader, complete.is_set() or oversized.is_set(), deadline)
                self.terminate(deadline)
            finally:
                if reader is not None:
                    reader.join(timeout=deadline.remaining(READER_JOIN))
                    if reader.is_alive():
                        self.abandoned_readers.append(reader)
        return None

    def adb_records(self, output, command):
        try:
            text = output.decode("utf-8")
            lines = text.replace("\r\n", "\n").split("\n")
            rows = []
            header = command == ["devices"]
            for line in lines:
                if len(line) > 512:
                    raise ValueError
                if not line or line.isspace() and clean(line) == line:
                    continue
                if header:
                    if line != "List of devices attached":
                        raise ValueError
                    header = False
                    continue
                if command == ["devices"]:
                    match = re.fullmatch(r"([A-Za-z0-9._:][A-Za-z0-9._:-]{0,63})\t([A-Za-z0-9_]+)", line)
                elif command == ["forward", "--list"]:
                    match = re.fullmatch(r"([A-Za-z0-9._:][A-Za-z0-9._:-]{0,63}) (tcp:[0-9]+) (tcp:[0-9]+)", line)
                    if match and any(not 1 <= int(port[4:]) <= 65535 for port in match.groups()[1:]):
                        raise ValueError
                else:
                    match = None  # A successful create has no records.
                if match is None:
                    raise ValueError
                rows.append(match.groups())
            if header:
                raise ValueError
            return rows
        except (UnicodeError, ValueError):
            self.report.error("adb output not understood; waiting")
            return None

    def adb_unknown(self):
        if self.adb_observed:
            self.report.change("adb forward state unknown (adb unavailable)")
        else:
            self.report.change("waiting", "adb unavailable")

    def ensure_adb(self):
        started = time.monotonic()
        rows = self.adb_command(["devices"])
        if rows is None:
            self.adb_unknown()
            return False
        # Count offline/unauthorized attachments too: never guess the first phone.
        if self.selected is None and len(rows) > 1:
            self.report.change("waiting", "several devices attached; select --serial")
            return False
        ready = [row[0] for row in rows if row[1] == "device"]
        if self.selected is None and len(ready) == 1:
            self.selected = ready[0]
        if self.selected not in ready or self.stop.is_set():
            self.adb_unknown()
            return False
        forwards = self.adb_command(["forward", "--list"])
        if forwards is None:
            self.adb_unknown()
            return False
        local, remote = f"tcp:{self.args.local_port}", f"tcp:{self.args.remote_port}"
        matching = [row for row in forwards if row[1] == local]
        if any(row[0] != self.selected or row[2] != remote for row in matching):
            self.report.change("conflict", "local port already has a different adb forward; left untouched")
            return False
        observation = "listed"
        if not matching:
            if self.stop.is_set() or self.adb_command(["forward", "--no-rebind", local, remote]) is None:
                self.adb_unknown()
                self.report.change("conflict", "adb create failed or conflicted; no overwrite attempted")
                return False
            observation = "created"
        self.adb_observed = True
        self.report.change(f"adb forward {observation} (not verified end to end)")
        self.report.emit(f"adb operation {time.monotonic() - started:.3f}s")
        return True

    def run(self):
        previous = {}
        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            signals.append(signal.SIGBREAK)
        try:
            for signum in signals:
                previous[signum] = signal.signal(signum, lambda *_: self.stop.set())
            if self.args.mode == "ssh":
                return self.run_ssh()
            self.report.change("waiting", "checking adb forward")
            while not self.stop.is_set():
                if self.cleanup_incomplete:
                    break
                success = self.ensure_adb()
                if self.cleanup_incomplete:
                    break
                if self.args.once:
                    return 0 if success or self.stop.is_set() else 1
                self.stop.wait(self.args.interval)
            return 0
        finally:
            deadline = self.cleanup_deadline()
            try:
                self.terminate(deadline)
            finally:
                if self.args.mode == "ssh" and self.child is None:
                    self.report.change("waiting", "stopped; owned SSH child terminated")
                try:
                    self.report.close(deadline=deadline)
                finally:
                    for signum, handler in previous.items():
                        signal.signal(signum, handler)


def main(argv=None):
    args = arguments(argv)
    if args.status:
        report = Reporter(args.quiet)
        try:
            with socket.create_connection(("127.0.0.1", args.local_port), timeout=0.5):
                pass
        except OSError:
            report.emit(f"nothing answers on 127.0.0.1:{args.local_port}", error=True)
            report.close(timeout=1.0)
            return 1
        report.emit(f"forward answers on 127.0.0.1:{args.local_port} (listener identity unverified)")
        report.close(timeout=1.0)
        return 0
    return Keeper(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
