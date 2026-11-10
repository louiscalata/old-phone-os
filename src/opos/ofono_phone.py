# SPDX-License-Identifier: GPL-3.0-or-later
"""oFono call and message calls, off unless OPOS_CALLS=1.

Practice mode is the default. With the flag on, dial still refuses a short
number with no leading plus, the same rule as the practice phone, including
911, 112 and 999. This module is not installed as Phone.modem. The phone page
is not wired to it. Not measured on a phone, and not used without a SIM.
"""
from __future__ import annotations

from opos import practice


def calls_enabled(env) -> bool:
    return str((env or {}).get("OPOS_CALLS", "")) == "1"


class OfonoPhone:
    """Same method names as the practice modem: dial, hang_up, send_sms."""

    def __init__(self, bus, env=None, path: str = "/ril_0"):
        self.bus = bus
        self.env = {} if env is None else env
        self.path = path
        self.calls: list[str] = []
        self.sent: list[tuple[str, str]] = []

    def dial(self, number: str) -> None:
        self._require()
        if practice.refuses(number):
            raise ValueError(practice.REFUSED)
        self.bus.call("org.ofono", self.path, "org.ofono.VoiceCallManager", "Dial", number, "default")
        self.calls.append(number)

    def hang_up(self) -> None:
        self._require()
        self.bus.call("org.ofono", self.path, "org.ofono.VoiceCallManager", "HangupAll")

    def send_sms(self, number: str, text: str) -> None:
        self._require()
        self.bus.call("org.ofono", self.path, "org.ofono.MessageManager", "SendMessage", number, text)
        self.sent.append((number, text))

    def _require(self) -> None:
        if not calls_enabled(self.env):
            raise RuntimeError("practice mode")
