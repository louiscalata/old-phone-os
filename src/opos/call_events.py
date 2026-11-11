# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional modem observer, separate from the Modem protocol's three methods.

An adapter emits started, then connected when answered, then ended, using one
unique id per call. at is Unix time; ended.duration is connected seconds measured
by the adapter's monotonic clock (None when unknown). practice must be explicit.
An ended event may include answered so a lost connected write cannot turn an
answered incoming call into a missed one. Otherwise the saved connected event
determines that outcome.
Incoming calls that end before connected are missed. Duplicate events are safe.
The observer must never prevent the modem from placing or ending a call.
"""
from dataclasses import dataclass
import logging


@dataclass(frozen=True)
class CallEvent:
    call_id: str
    state: str
    number: str
    direction: str
    at: float
    practice: bool
    duration: float | None = None
    answered: bool | None = None


def notify(observer, event):
    if observer is not None:
        try:
            observer(event)
        except Exception:
            # No number, contact name or exception detail in the process log.
            logging.getLogger("opos.calls").warning("The call log could not be updated.")
