# SPDX-License-Identifier: GPL-3.0-or-later
"""Duo volume keys: simulated state only until OPOS has a media/ringer mixer."""
from __future__ import annotations

import os
import select
import signal
import sys
import time
from pathlib import Path

from opos import volume
from opos.powerkey import INPUT_EVENT, EV_KEY, EV_SYN, SYN_DROPPED, SYN_REPORT, parse_event

DEVICES = {"qpnp_pon": 114, "gpio-keys": 115}
STEP = 5
RETRY_SECONDS = 1.0


def journal(status):
    """Fixed operational statuses only: no input bytes, values or exception text."""
    print("opos-volumekey:", status, file=sys.stderr, flush=True)


def find_device(name, root="/"):
    base = Path(root)
    for entry in sorted((base / "sys/class/input").glob("event*")):
        try:
            actual = (entry / "device/name").read_text(encoding="utf-8").removesuffix("\n")
        except (OSError, UnicodeError):
            continue
        if actual == name:
            return base / "dev/input" / entry.name
    raise FileNotFoundError("volume input unavailable")


def open_input(name, root):
    stream = find_device(name, root).open("rb", buffering=0)
    try:
        os.set_blocking(stream.fileno(), False)
    except BaseException:
        stream.close()
        raise
    return stream  # No exclusive grab: qpnp_pon still reaches the power reader.


class VolumeKey:
    def __init__(self, write, log=journal):
        self.write = write
        self.log = log
        self.percent = volume.DEFAULT
        self.pressed = set()
        self.dropped = set()

    def start(self):
        self.write(self.percent)

    def input_lost(self, source):
        self.pressed.discard(source)
        self.dropped.discard(source)

    def event(self, source, event):
        if source not in DEVICES:
            return
        if event.type == EV_SYN and event.code == SYN_DROPPED:
            self.pressed.discard(source)
            if source not in self.dropped:
                self.log("input synchronization lost")
            self.dropped.add(source)
            return
        if source in self.dropped:
            if event.type == EV_SYN and event.code == SYN_REPORT:
                self.dropped.discard(source)
            return
        if event.type != EV_KEY or event.code != DEVICES[source]:
            return
        if event.value == 0:
            self.pressed.discard(source)
        elif event.value == 1 and source not in self.pressed:
            self.pressed.add(source)
            target = max(0, min(100, self.percent + (STEP if event.code == 115 else -STEP)))
            if target != self.percent:
                self.write(target)  # A failed publication exits to systemd; never claim success.
                self.percent = target
        # Autorepeats and unknown values never change volume or renew a press.


def run(root="/", *, state_path=volume.STATE, controller=None, wait=select.select,
        open_device=open_input, clock=time.monotonic, log=journal):
    daemon = controller if controller is not None else VolumeKey(
        lambda percent: volume.write_percent(state_path, percent), log)
    streams, buffers, retry_at = {}, {}, dict.fromkeys(DEVICES, 0.0)
    failed = set()

    def close(source):
        stream = streams.pop(source, None)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        buffers.pop(source, None)
        daemon.input_lost(source)

    def lost(source):
        close(source)
        retry_at[source] = clock() + RETRY_SECONDS
        if source not in failed:
            log("input unavailable; retrying")
        failed.add(source)

    try:
        daemon.start()
        while True:
            for source in DEVICES:
                if source not in streams and clock() >= retry_at[source]:
                    try:
                        streams[source] = open_device(source, root)
                        buffers[source] = b""
                    except (OSError, ValueError):
                        lost(source)
            pending = [retry_at[source] for source in DEVICES if source not in streams]
            timeout = max(0, min(pending) - clock()) if pending else None
            readable, _, _ = wait(list(streams.values()), [], [], timeout)
            for source, stream in list(streams.items()):
                if stream not in readable:
                    continue
                try:
                    chunk = stream.read(INPUT_EVENT.size * 64)
                    if chunk == b"":
                        raise EOFError
                except BlockingIOError:
                    continue
                except (OSError, EOFError, ValueError):
                    lost(source)
                    continue
                if chunk is None:
                    continue
                if source in failed:
                    log("input recovered")
                    failed.discard(source)
                data = buffers[source] + chunk
                end = len(data) - len(data) % INPUT_EVENT.size
                for offset in range(0, end, INPUT_EVENT.size):
                    daemon.event(source, parse_event(data[offset:offset + INPUT_EVENT.size]))
                buffers[source] = data[end:]
    except (OSError, ValueError):
        log("reader stopped; state or input unavailable")
        return 1
    finally:
        for source in list(streams):
            close(source)


def terminate(signum, frame):
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    raise SystemExit(run())
