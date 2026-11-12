# SPDX-License-Identifier: GPL-3.0-or-later
"""A Duo blank is a lease, justified again on each nonblocking input-loop tick."""
from __future__ import annotations

import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from opos.display import PANELS

# Linux's 64-bit input_event: two signed 8-byte timeval fields, u16, u16, s32.
INPUT_EVENT = struct.Struct("=qqHHi")
EV_SYN = 0
SYN_REPORT = 0
SYN_DROPPED = 3
EV_KEY = 1
KEY_POWER = 116
SHORT_PRESS_US = 1_500_000
DEBOUNCE_US = 300_000
TICK_SECONDS = 1.0
LIT = (0, 0)
BLANK = (4, 4)
# Linux _IOW('E', 0xa0, int): select the clock used for evdev timestamps.
EVIOCSCLOCKID = 0x400445A0


class InputEvent(NamedTuple):
    seconds: int
    microseconds: int
    type: int
    code: int
    value: int


class PressState(NamedTuple):
    pressed_at: int | None = None
    last_toggle: int | None = None
    cancelled: bool = False


def parse_event(data: bytes) -> InputEvent:
    """Parse exactly one 64-bit input_event; struct.error rejects other sizes."""
    return InputEvent(*INPUT_EVENT.unpack(data))


def expire_press(state: PressState, now_us: int) -> tuple[PressState, bool]:
    """Recover once for a lost release/long hold; suppress its eventual release."""
    if (state.pressed_at is not None and not state.cancelled
            and now_us - state.pressed_at > SHORT_PRESS_US):
        return PressState(state.pressed_at, state.last_toggle, True), True
    return state, False


def press_step(state: PressState, event: InputEvent) -> tuple[PressState, str | None]:
    """Pure key transition: only a clean, debounced short release requests a toggle."""
    if event.type != EV_KEY or event.code != KEY_POWER:
        return state, None
    stamp = event.seconds * 1_000_000 + event.microseconds
    if event.value == 1:
        if state.pressed_at is not None:
            return PressState(state.pressed_at, state.last_toggle, True), "light"
        return PressState(stamp, state.last_toggle), None
    if event.value != 0:  # Repeats (2) and unknown values never renew a press.
        return state, None
    released = PressState(None, state.last_toggle)
    if state.pressed_at is None or state.cancelled:
        return released, None
    if not 0 <= stamp - state.pressed_at <= SHORT_PRESS_US:
        return released, "light"
    if state.last_toggle is not None and stamp - state.last_toggle < DEBOUNCE_US:
        return released, None
    return PressState(None, stamp), "short"


def find_device(root="/") -> Path:
    """Find qpnp_pon by its exact sysfs name on every reopen."""
    base = Path(root)
    for entry in sorted((base / "sys/class/input").glob("event*")):
        try:
            name = (entry / "device/name").read_text(encoding="utf-8").removesuffix("\n")
        except OSError:
            continue
        if name == "qpnp_pon":
            return base / "dev/input" / entry.name
    raise FileNotFoundError("no qpnp_pon input device")


def panel_paths(root):
    return [Path(root) / "sys/class/backlight" / name / "bl_power" for name in PANELS]


def read_panels(root) -> tuple[int | None, ...]:
    """Always sample both; missing, invalid and unreadable attributes are unknown."""
    powers = []
    for path in panel_paths(root):
        try:
            powers.append(int(path.read_text(encoding="ascii")))
        except (OSError, ValueError):
            powers.append(None)
    return tuple(powers)


def write_panel_power(root, value: int) -> bool:
    """One pass only. Attempt the other attribute even after a failure."""
    succeeded = True
    for path in panel_paths(root):
        try:
            # r+ requires an existing attribute, including on a fake sysfs tree.
            with path.open("r+", encoding="ascii") as stream:
                stream.write(f"{value}\n")
        except OSError:
            succeeded = False
    return succeeded


def shell_active() -> bool:
    """Unit-active is a fallible lease probe, not compositor readiness."""
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", "opos.service"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=0.5, check=False,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def journal(status):
    """Write a power-key operational status to the service journal."""
    print("opos-powerkey:", status, file=sys.stderr, flush=True)


class PowerKey:
    """Injected panel I/O, clock, shell probe and events; no cached panel state."""

    def __init__(self, read, write, is_active=shell_active, clock=time.monotonic, log=journal):
        self.read = read
        self.write = write
        self.is_active = is_active
        self.clock = clock
        self.log = log
        self.press = PressState()
        self.dropped = False
        self.recovery_pending = False

    def active(self):
        try:
            return self.is_active() is True
        except Exception:  # An injected probe must fail safe too.
            return False

    def light(self):
        written = self.write(0)
        powers = self.read()
        pending = not written or powers != LIT
        if pending != self.recovery_pending:
            self.log("unblank recovery pending" if pending else "unblank recovery confirmed")
        self.recovery_pending = pending
        return powers

    def start(self):
        # Missing/unwritable panels leave recovery pending for the loop to retry;
        # temporary inability to unblank must never make startup fatal.
        self.light()

    def stop(self):
        self.light()

    def input_lost(self):
        self.press = PressState(None, self.press.last_toggle)
        self.dropped = False
        self.light()

    def short_press(self):
        powers = self.read()
        if self.recovery_pending or powers != LIT or not self.active():
            self.light()
            return
        # Blank exactly once. A stale probe or later lid write is caught on a tick.
        written = self.write(4)
        powers = self.read()
        if not written or powers != BLANK:
            self.light()  # Never repair uncertainty by applying blank again.

    def event(self, event):
        if event.type == EV_SYN and event.code == SYN_DROPPED:
            self.press = PressState(None, self.press.last_toggle)
            self.dropped = True
            self.light()
            return
        if self.dropped:
            if event.type == EV_SYN and event.code == SYN_REPORT:
                self.dropped = False
            return
        self.press, action = press_step(self.press, event)
        if action == "light":
            self.light()
        elif action == "short":
            self.short_press()

    def tick(self) -> float | None:
        """Rejustify a blank, retry recovery, and choose the next select timeout."""
        self.press, expired = expire_press(self.press, int(self.clock() * 1_000_000))
        powers = self.read()
        if (expired or self.recovery_pending
                or (powers != LIT and (powers != BLANK or not self.active()))):
            powers = self.light()
        pending_press = self.press.pressed_at is not None and not self.press.cancelled
        if powers != LIT or self.recovery_pending or pending_press or self.dropped:
            return TICK_SECONDS
        return None


def open_input(root):
    stream = find_device(root).open("rb", buffering=0)
    try:
        os.set_blocking(stream.fileno(), False)
        fcntl.ioctl(stream, EVIOCSCLOCKID, struct.pack("=i", time.CLOCK_MONOTONIC))
    except BaseException:
        stream.close()
        raise
    return stream


def run(root="/", is_active=shell_active, *, controller=None, wait=select.select, open_device=open_input):
    daemon = controller if controller is not None else PowerKey(
        lambda: read_panels(root), lambda value: write_panel_power(root, value), is_active)
    stream = None
    data = b""
    input_error = None
    try:
        daemon.start()
        while True:
            # Tick before every select, also under continuous input. Events cannot
            # postpone the lease check by repeatedly restarting its timeout.
            timeout = daemon.tick()
            try:
                if stream is None:
                    stream = open_device(root)  # Fresh exact-name lookup and clock ioctl.
                if len(data) < INPUT_EVENT.size:
                    readable, _, _ = wait([stream], [], [], timeout)
                    if not readable:
                        continue
                    try:
                        # One nonblocking read, even when a record is incomplete.
                        chunk = stream.read(INPUT_EVENT.size * 64)
                    except BlockingIOError:
                        continue
                    if chunk is None:
                        continue
                    if not chunk:
                        raise EOFError("power-key input ended")
                    if input_error is not None:
                        journal("input recovered")
                    input_error = None
                    data += chunk
                if len(data) >= INPUT_EVENT.size:
                    daemon.event(parse_event(data[:INPUT_EVENT.size]))
                    data = data[INPUT_EVENT.size:]
                    # Return through tick even between events already buffered.
            except (OSError, EOFError, ValueError) as exc:
                error = str(exc)
                if error != input_error:
                    journal(f"{error}; reopening in 1 s")
                input_error = error
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass  # A failed close must not prevent lighting/reopening.
                    stream = None
                data = b""
                daemon.input_lost()
                wait([], [], [], TICK_SECONDS)  # Keep recovery ticking without a device.
    finally:
        try:
            if stream is not None:
                stream.close()
        finally:
            daemon.stop()


def terminate(signum, frame):
    raise SystemExit(0)  # Unwind select through the final lighting attempt.


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    run()
