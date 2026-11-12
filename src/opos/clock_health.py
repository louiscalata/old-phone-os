# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared clock trust rule for the server and the image self-test."""
import time


def clock_status(now, guess, synced, floor):
    if time.gmtime(now).tm_year < 2026:
        return "year"
    floor = (floor or "").strip()
    if len(floor) <= 12 and floor.isascii() and floor.isdigit() and now <= int(floor):
        return "floor"
    if guess and not synced:
        return "guess"
    return "ok"
