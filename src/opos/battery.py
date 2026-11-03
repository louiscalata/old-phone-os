# SPDX-License-Identifier: GPL-3.0-or-later
"""Battery drain and reserve helpers."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Sample:
    t_s: float
    level: float


class DrainEstimator:
    """Estimate drain from battery samples."""
    def __init__(self, window=20, alpha=0.3):
        self._window = window
        self._alpha = alpha
        self._samples: list[Sample] = []

    def add(self, sample: Sample) -> None:
        if self._samples and sample.t_s <= self._samples[-1].t_s:
            return
        if self._samples and sample.level > self._samples[-1].level:
            self._samples = [sample]
            return
        self._samples.append(sample)
        del self._samples[:-self._window]

    def rate(self) -> float | None:
        if len(self._samples) < 2:
            return None
        pairs = self._samples[-self._window:]
        ema = max(0.0, (pairs[0].level - pairs[1].level) / (pairs[1].t_s - pairs[0].t_s) * 3600.0)
        for prev, cur in zip(pairs[1:-1], pairs[2:]):
            r = max(0.0, (prev.level - cur.level) / (cur.t_s - prev.t_s) * 3600.0)
            ema = self._alpha * r + (1.0 - self._alpha) * ema
        return max(0.0, ema)


def hours_left(level: float, rate: float | None) -> float:
    """Estimate remaining hours from battery level and drain rate."""
    if rate is None or rate <= 0:
        return math.inf
    return level / rate


def essentials_reserve(rate: float | None, call_minutes: float = 10, messages: int = 20, call_cost: float = 0.05, sms_cost: float = 0.01) -> float:
    """Calculate reserve for calls, messages, and drain."""
    return call_minutes * call_cost + messages * sms_cost + (0.0 if rate is None else rate)
