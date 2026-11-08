# SPDX-License-Identifier: GPL-3.0-or-later
"""Is the system clock plausible? A phone that has never had its time set reports 1970."""
import time


def clock_plausible(now=None, floor_year=2026):
    """True when the clock reads floor_year or later; now is epoch seconds (default: the system clock)."""
    try:
        return time.gmtime(time.time() if now is None else float(now)).tm_year >= floor_year
    except (OverflowError, OSError, ValueError, TypeError):
        return False
