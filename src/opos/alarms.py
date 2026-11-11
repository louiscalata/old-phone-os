# SPDX-License-Identifier: GPL-3.0-or-later
"""Alarm and timer scheduler. Pure: no sound, no thread, no network.

A saved alarm does not ring while the clock is unknown. A fire more than 10 minutes
late is a missed alarm, not a ring. A wall time that does not exist (the spring-forward
gap) is skipped. A repeated wall time (the fall-back hour) rings once, at the first one.
"""
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from zoneinfo import ZoneInfo

from opos.store import Store

MAX_ALARMS = 20
MAX_LABEL = 40
GRACE = timedelta(minutes=10)
SNOOZE = timedelta(minutes=10)
MAX_TIMER_S = 24 * 60 * 60
_FILE = "alarms.json"


@dataclass(frozen=True)
class Event:
    kind: str  # "ring" or "missed"
    what: str  # "alarm" or "timer"
    id: str
    at: datetime
    text: str


class Occurrence(Enum):
    IDLE = auto()
    ARMED = auto()
    SNOOZED = auto()
    RINGING = auto()
    MISSED = auto()
    COMPLETED = auto()


def occurrence(row):
    """Derive the resting state, including a disabled one-shot still ringing.

    MISSED and COMPLETED are transition outcomes. The unchanged saved fields
    retain no terminal history; those outcomes settle to IDLE or ARMED.
    """
    if row.get("ringing"):
        return Occurrence.RINGING
    if not row.get("enabled"):
        return Occurrence.IDLE
    if isinstance(row.get("snooze_at"), str):
        return Occurrence.SNOOZED
    return Occurrence.ARMED


def _label(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("label")
    text = " ".join(value.split())
    if len(text) > MAX_LABEL:
        raise ValueError("label")
    return text


def _clock(hour, minute):
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("hour")
    if isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute <= 59:
        raise ValueError("minute")
    return hour, minute


def _days(days):
    if days is None:
        return ()
    if isinstance(days, (str, bytes)) or not isinstance(days, (tuple, list)):
        raise ValueError("days")
    found = []
    for day in days:
        if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6 or day in found:
            raise ValueError("days")
        found.append(day)
    return tuple(sorted(found))


def _aware(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now")
    return now


def _hhmm(hour, minute):
    return "%02d:%02d" % (hour, minute)


def first_valid(day, hour, minute, zone):
    """The earlier real instant of that wall time on that date, or None if the minute is skipped."""
    found = []
    for fold in (0, 1):
        made = datetime(day.year, day.month, day.day, hour, minute, fold=fold, tzinfo=zone)
        back = made.astimezone(timezone.utc).astimezone(zone)
        if back.hour == hour and back.minute == minute and back.date() == day and back.fold == fold:
            found.append(made)
    if not found:
        return None
    return min(found, key=lambda item: item.astimezone(timezone.utc))


def _matches(days, day):
    return not days or day.weekday() in days


def latest_fire(hour, minute, days, start, end):
    """The latest valid fire in (start, end], or None."""
    best = None
    day = (start.astimezone(end.tzinfo)).date() - timedelta(days=1)
    last = end.astimezone(end.tzinfo).date()
    zone = end.tzinfo
    while day <= last:
        if _matches(days, day):
            made = first_valid(day, hour, minute, zone)
            if made is not None and start < made <= end and (best is None or made > best):
                best = made
        day += timedelta(days=1)
    return best


def next_fire(hour, minute, days, now):
    """The next valid fire at or after now, looking eight days ahead."""
    now = _aware(now)
    zone = now.tzinfo
    day = now.date()
    for offset in range(8):
        when = day + timedelta(days=offset)
        if not _matches(days, when):
            continue
        made = first_valid(when, hour, minute, zone)
        if made is not None and made >= now:
            return made
    return None


def _iso(when):
    return when.astimezone(timezone.utc).isoformat()


def _parse(text, zone):
    made = datetime.fromisoformat(text)
    if made.tzinfo is None:
        made = made.replace(tzinfo=timezone.utc)
    return made.astimezone(zone)


class Book:
    """The saved alarms and the one timer. tick() is the only thing that rings or misses."""

    def __init__(self, root, zone=None):
        self.store = Store(root)
        self.zone = zone or ZoneInfo("UTC")
        self._lock = threading.Lock()
        self.alarms = []
        self.timer = None
        self.last_seen = None
        self.notices = []
        self._load()

    def _load(self):
        data, self.recovered = self.store.read_json(_FILE, {"alarms": [], "notices": []})
        alarms = data.get("alarms") if isinstance(data.get("alarms"), list) else []
        self.alarms = [row for row in alarms if isinstance(row, dict) and isinstance(row.get("id"), str)]
        timer = data.get("timer")
        self.timer = timer if isinstance(timer, dict) and isinstance(timer.get("fires_at"), str) else None
        seen = data.get("last_seen")
        self.last_seen = seen if isinstance(seen, str) else None
        notes = data.get("notices") if isinstance(data.get("notices"), list) else []
        self.notices = [note for note in notes if isinstance(note, str)][-20:]

    def _save(self):
        self.store.write_json(_FILE, {
            "alarms": self.alarms,
            "timer": self.timer,
            "last_seen": self.last_seen,
            "notices": self.notices[-20:],
        })

    def add(self, hour, minute, days=(), label="", now=None):
        with self._lock:
            if len(self.alarms) >= MAX_ALARMS:
                raise ValueError("full")
            hour, minute = _clock(hour, minute)
            row = {"id": uuid.uuid4().hex[:8]}
            self._transition(row, "edit", changes={
                "hour": hour,
                "minute": minute,
                "days": list(_days(days)),
                "label": _label(label),
                "enabled": True,
                "once": not _days(days),
            })
            self.alarms.append(row)
            if now is not None and self.last_seen is None:
                self.last_seen = _iso(_aware(now))
            self._save()
            return dict(row)

    def edit(self, alarm_id, hour, minute, days=(), label=""):
        with self._lock:
            row = self._row(alarm_id)
            hour, minute = _clock(hour, minute)
            picked = _days(days)
            self._transition(row, "edit", changes={"hour": hour, "minute": minute, "days": list(picked), "label": _label(label), "once": not picked})
            self._save()
            return dict(row)

    def delete(self, alarm_id):
        with self._lock:
            self._transition(self._row(alarm_id), "delete")
            self._save()

    def toggle(self, alarm_id, enabled):
        with self._lock:
            row = self._row(alarm_id)
            if not isinstance(enabled, bool):
                raise ValueError("enabled")
            self._transition(row, "toggle", enabled=enabled)
            self._save()
            return dict(row)

    def start_timer(self, seconds, now, label=""):
        with self._lock:
            now = _aware(now)
            if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= MAX_TIMER_S:
                raise ValueError("seconds")
            self.timer = {"id": uuid.uuid4().hex[:8], "fires_at": _iso(now + timedelta(seconds=seconds)), "label": _label(label)}
            if self.last_seen is None:
                self.last_seen = _iso(now)
            self._save()
            return dict(self.timer)

    def cancel_timer(self):
        with self._lock:
            self.timer = None
            self._save()

    def snooze(self, now, alarm_id=None):
        """Push the ringing alarm 10 minutes from now. Repeating alarms stay on."""
        with self._lock:
            now = _aware(now)
            row = self._ringing(now, alarm_id)
            if row is None:
                raise ValueError("id")
            self._transition(row, "snooze", now=now)
            self.last_seen = _iso(now)
            self._save()
            return dict(row)

    def stop(self, now, alarm_id=None):
        """Stop the ring. A one-time alarm turns off. A repeating alarm stays on."""
        with self._lock:
            now = _aware(now)
            row = self._ringing(now, alarm_id)
            if row is None and alarm_id is not None:
                row = self._row(alarm_id)
            if row is None:
                raise ValueError("id")
            self._transition(row, "stop")
            self.last_seen = _iso(now)
            self._save()
            return dict(row)

    def tick(self, now, clock_ok=True):
        """Events since the last tick. An unknown clock changes nothing, including last_seen."""
        with self._lock:
            now = _aware(now).astimezone(self.zone)
            if not clock_ok:
                return []
            if self.last_seen is None:
                self.last_seen = _iso(now)
                self._save()
                return []
            start = _parse(self.last_seen, self.zone)
            events = []
            events.extend(self._timer_events(start, now))
            for row in list(self.alarms):
                event = self._transition(row, "tick", start=start, now=now)
                if event is not None:
                    events.append(event)
            self.last_seen = _iso(now)
            if events:
                for event in events:
                    if event.kind == "missed":
                        self.notices.append(event.text)
                self.notices = self.notices[-20:]
            self._save()
            return events

    def next_alarm(self, now):
        """The next armed fire at or after now, or None. Snooze counts."""
        now = _aware(now).astimezone(self.zone)
        best = None
        best_row = None
        for row in self.alarms:
            if not row.get("enabled"):
                continue
            snooze = row.get("snooze_at")
            if isinstance(snooze, str):
                when = _parse(snooze, self.zone)
                if when >= now and (best is None or when < best):
                    best, best_row = when, row
                    continue
            when = next_fire(row["hour"], row["minute"], tuple(row.get("days") or ()), now)
            if when is not None and (best is None or when < best):
                best, best_row = when, row
        if best is None:
            return None
        return {"id": best_row["id"], "at": best, "label": best_row.get("label") or "", "text": "Alarm " + _hhmm(best_row["hour"], best_row["minute"])}

    def _row(self, alarm_id):
        for row in self.alarms:
            if row["id"] == alarm_id:
                return row
        raise ValueError("id")

    def _ringing(self, now, alarm_id):
        """Select an alarm; the transition separately checks snooze eligibility."""
        if alarm_id is not None:
            return self._row(alarm_id)
        for row in self.alarms:
            if occurrence(row) is Occurrence.RINGING:
                return row
        for row in self.alarms:
            snooze = row.get("snooze_at")
            if isinstance(snooze, str):
                when = _parse(snooze, self.zone)
                if when <= now <= when + GRACE:
                    return row
        for row in self.alarms:
            if not row.get("enabled"):
                continue
            when = latest_fire(row["hour"], row["minute"], tuple(row.get("days") or ()), now - GRACE, now)
            if when is not None:
                return row
        return None

    def _transition(self, row, event, *, now=None, start=None, enabled=None, changes=None):
        """The only writer of alarm occurrence fields. Terminal states are transient."""
        state = occurrence(row)
        if event == "edit":
            row.update(changes)
            return
        if event == "delete":
            self.alarms = [item for item in self.alarms if item["id"] != row["id"]]
            return
        if event == "toggle":
            row["enabled"] = enabled
            if not enabled:
                row.pop("snooze_at", None)
            return
        if event == "snooze":
            if state is not Occurrence.RINGING:
                raise ValueError("id")
            state = Occurrence.SNOOZED
        elif event == "stop":
            state = Occurrence.COMPLETED
        elif event == "tick":
            # A one-shot keeps its ring through the tick that turns it off.
            if row.get("done") and not row.get("snooze_at"):
                row["enabled"] = False
                row.pop("done", None)
                return
            if state is Occurrence.IDLE:
                return
            when = None
            snooze = row.get("snooze_at")
            if isinstance(snooze, str):
                due = _parse(snooze, self.zone)
                if start < due <= now:
                    when = due
                    row.pop("snooze_at", None)
            if when is None:
                if not row.get("enabled"):
                    return
                when = latest_fire(row["hour"], row["minute"], tuple(row.get("days") or ()), start, now)
            if when is None:
                return
            state = Occurrence.MISSED if now - when > GRACE else Occurrence.RINGING
        else:
            raise ValueError("event")

        if state is Occurrence.SNOOZED:
            row.pop("done", None)
            row.pop("ringing", None)
            row["enabled"] = True
            row["snooze_at"] = _iso(now + SNOOZE)
        elif state is Occurrence.COMPLETED:
            row.pop("snooze_at", None)
            row.pop("ringing", None)
            if row.get("once"):
                row["enabled"] = False
        else:
            missed = state is Occurrence.MISSED
            if row.get("once"):
                if missed:
                    row["enabled"] = False
                    row.pop("done", None)
                else:
                    row["done"] = True
            if missed:
                row.pop("ringing", None)
            else:
                row["ringing"] = True
            return Event("missed" if missed else "ring", "alarm", row["id"], when,
                         ("Missed alarm " if missed else "Alarm ") + _hhmm(row["hour"], row["minute"]))

    def _timer_events(self, start, now):
        if not self.timer:
            return []
        when = _parse(self.timer["fires_at"], self.zone)
        if not start < when <= now:
            return []
        label = self.timer.get("label") or ""
        ident = self.timer.get("id") or "timer"
        self.timer = None
        if now - when <= GRACE:
            text = "Timer" + (" " + label if label else "")
            return [Event("ring", "timer", ident, when, text.strip())]
        return [Event("missed", "timer", ident, when, "Missed timer")]
