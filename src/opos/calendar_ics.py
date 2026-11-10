# SPDX-License-Identifier: GPL-3.0-or-later
"""A local calendar file. RFC 5545 only as far as one VEVENT list needs: start, end,
daily and weekly repeat, and a display reminder.

Reminders use the alarm engine's grace and event type. This module does not write alarms.
"""
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from opos import alarms
from opos.store import MAX_JSON, Store

MAX_EVENTS = 200
MAX_TITLE = 80
FILE = "calendar.ics"
REMINDERS = ("none", "10min", "1h", "1d")
_DELTA = {"10min": timedelta(minutes=10), "1h": timedelta(hours=1), "1d": timedelta(days=1)}
_DAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_DAY_NAME = {number: name for name, number in _DAY.items()}
_TRIGGER = {"-PT10M": "10min", "-PT1H": "1h", "-P1D": "1d"}


@dataclass(frozen=True)
class Item:
    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    rule: str
    days: tuple
    reminder: str
    ack: tuple


def _unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        if lines and line[:1] in (" ", "\t"):
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _split(line):
    if ":" not in line:
        return None
    head, value = line.split(":", 1)
    bits = head.split(";")
    name = bits[0].strip().upper()
    params = {}
    for bit in bits[1:]:
        if "=" not in bit:
            continue
        key, raw = bit.split("=", 1)
        params[key.strip().upper()] = raw.strip()
    return name, params, value


def _unescape(value):
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        out.append(value[i])
        i += 1
    return "".join(out)


def _escape(value):
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _fold(line):
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts = []
    first = True
    while raw:
        take = 75 if first else 74
        chunk = raw[:take]
        while chunk:
            try:
                text = chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        if not chunk:
            break
        parts.append(text if first else " " + text)
        raw = raw[len(chunk):]
        first = False
    return "\r\n".join(parts)


def _stamp(value, params, zone):
    """A date, or an aware datetime in zone. Raises ValueError on a shape we do not keep."""
    text = value.strip()
    kind = params.get("VALUE", "").upper()
    if kind == "DATE" or (len(text) == 8 and "T" not in text):
        day = datetime.strptime(text, "%Y%m%d").date()
        return True, datetime(day.year, day.month, day.day, tzinfo=zone)
    if text.endswith("Z"):
        made = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return False, made.astimezone(zone)
    made = datetime.strptime(text, "%Y%m%dT%H%M%S")
    if "TZID" in params:
        made = made.replace(tzinfo=ZoneInfo(params["TZID"]))
        return False, made.astimezone(zone)
    return False, made.replace(tzinfo=zone)


def _rule(value):
    """(rule, days, skipped). An unsupported piece is skipped and the event happens once."""
    parts = {}
    for bit in value.split(";"):
        if "=" not in bit:
            continue
        key, raw = bit.split("=", 1)
        parts[key.strip().upper()] = raw.strip().upper()
    if "INTERVAL" in parts and parts["INTERVAL"] != "1":
        return "", (), 1
    freq = parts.get("FREQ", "")
    if freq == "DAILY":
        return "daily", (), 0
    if freq == "WEEKLY":
        days = []
        for name in parts.get("BYDAY", "").split(","):
            if not name:
                continue
            if name not in _DAY:
                return "", (), 1
            if _DAY[name] not in days:
                days.append(_DAY[name])
        return "weekly", tuple(sorted(days)), 0
    if freq:
        return "", (), 1
    return "", (), 0


def _title(value):
    text = " ".join(_unescape(value).split())
    if not text:
        return ""
    if len(text) > MAX_TITLE:
        return text[:MAX_TITLE]
    return text


def parse(text, zone):
    """(items, notices, skipped). ValueError when the file is not a calendar."""
    if not isinstance(text, str) or "\x00" in text:
        raise ValueError("bad")
    lines = _unfold(text)
    if not any(line.strip() for line in lines):
        return [], [], 0
    if not any(line.strip().upper() == "BEGIN:VCALENDAR" for line in lines):
        raise ValueError("bad")
    items = []
    notices = []
    skipped = 0
    event = None
    alarm = None
    schema = None
    for line in lines:
        if not line.strip():
            continue
        parts = _split(line.strip())
        if parts is None:
            continue
        name, params, value = parts
        upper = value.strip().upper()
        if name == "BEGIN" and upper == "VEVENT":
            event = {"ack": []}
            alarm = None
            continue
        if name == "END" and upper == "VEVENT":
            made, extra = _item(event or {}, zone)
            skipped += extra
            if made is not None:
                items.append(made)
            event = None
            alarm = None
            continue
        if name == "BEGIN" and upper == "VALARM":
            alarm = {}
            continue
        if name == "END" and upper == "VALARM":
            if event is not None and alarm is not None:
                trigger = _TRIGGER.get((alarm.get("TRIGGER") or "").upper())
                action = (alarm.get("ACTION") or "").upper()
                if action and action != "DISPLAY":
                    skipped += 1
                elif alarm.get("TRIGGER") and trigger is None:
                    skipped += 1
                elif trigger:
                    event["reminder"] = trigger
            alarm = None
            continue
        if name == "X-OPOS-SCHEMA" and event is None:
            schema = value.strip()
            continue
        if name == "X-OPOS-NOTICE" and event is None:
            note = " ".join(value.split())
            if note:
                notices.append(note[:80])
            continue
        if alarm is not None:
            alarm[name] = value.strip()
            continue
        if event is None:
            continue
        if name == "UID":
            event["uid"] = value.strip()
        elif name == "SUMMARY":
            event["title"] = value
        elif name == "DTSTART":
            event["start"] = (params, value.strip())
        elif name == "DTEND":
            event["end"] = (params, value.strip())
        elif name == "RRULE":
            event["rule"] = value.strip()
        elif name == "X-OPOS-ACK":
            event["ack"].append(value.strip())
    if schema not in (None, "", "1"):
        raise ValueError("bad")
    return items[:MAX_EVENTS], notices[-20:], skipped


def _item(event, zone):
    title = _title(event.get("title") or "")
    uid = event.get("uid") or ""
    if not title or not uid or len(uid) > 64 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for c in uid):
        return None, 1
    if "start" not in event:
        return None, 1
    skipped = 0
    try:
        all_day, start = _stamp(event["start"][1], event["start"][0], zone)
        if "end" in event:
            end_day, end = _stamp(event["end"][1], event["end"][0], zone)
            if end_day != all_day:
                return None, 1
        elif all_day:
            end = start + timedelta(days=1)
        else:
            end = start + timedelta(hours=1)
    except (ValueError, KeyError, OverflowError):
        return None, 1
    rule, days, extra = _rule(event["rule"]) if event.get("rule") else ("", (), 0)
    skipped += extra
    if rule == "weekly" and not days:
        days = (start.weekday(),)
    reminder = event.get("reminder") if event.get("reminder") in REMINDERS else "none"
    ack = tuple(key for key in event.get("ack") or [] if isinstance(key, str) and 1 <= len(key) <= 64)
    return Item(uid, title, start, end, all_day, rule, days, reminder, ack), skipped


def emit(items, notices=()):
    """The calendar file. Timed instants are UTC. All-day dates stay dates."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OPOS//Calendar//EN", "X-OPOS-SCHEMA:1"]
    for note in list(notices)[-20:]:
        lines.append("X-OPOS-NOTICE:" + " ".join(str(note).split())[:80])
    for item in items:
        lines.append("BEGIN:VEVENT")
        lines.append("UID:" + item.uid)
        lines.append(_fold("SUMMARY:" + _escape(item.title)))
        if item.all_day:
            lines.append("DTSTART;VALUE=DATE:" + item.start.strftime("%Y%m%d"))
            lines.append("DTEND;VALUE=DATE:" + item.end.strftime("%Y%m%d"))
        else:
            lines.append("DTSTART:" + item.start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            lines.append("DTEND:" + item.end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        if item.rule == "daily":
            lines.append("RRULE:FREQ=DAILY")
        elif item.rule == "weekly":
            days = item.days or (item.start.weekday(),)
            names = ",".join(_DAY_NAME[day] for day in days)
            lines.append("RRULE:FREQ=WEEKLY;BYDAY=" + names)
        if item.reminder in _DELTA:
            lines.extend(["BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:" + {v: k for k, v in _TRIGGER.items()}[item.reminder], "END:VALARM"])
        for key in item.ack:
            lines.append("X-OPOS-ACK:" + key)
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _aware(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now")
    return now


def occurrences(item, start, end):
    """Occurrence starts in [start, end], in the zone of start. A gap minute is skipped."""
    start = _aware(start)
    end = _aware(end).astimezone(start.tzinfo)
    first = item.start.astimezone(start.tzinfo)
    if item.rule == "":
        return [first] if start <= first <= end else []
    days = ()
    if item.rule == "weekly":
        days = item.days or (first.weekday(),)
    elif item.rule != "daily":
        return []
    probe = first - timedelta(seconds=1)
    if start - timedelta(seconds=1) > probe:
        probe = start - timedelta(seconds=1)
    found = []
    when = alarms.next_fire(first.hour, first.minute, days, probe)
    guard = 0
    while when is not None and when <= end and guard < 400:
        if when >= first and when >= start:
            found.append(when)
        nxt = alarms.next_fire(first.hour, first.minute, days, when + timedelta(minutes=1))
        if nxt is None or nxt <= when:
            break
        when = nxt
        guard += 1
    return found


def _label(item, when):
    if item.all_day:
        return "All day"
    local = when.astimezone(when.tzinfo)
    return "%02d:%02d" % (local.hour, local.minute)


def handoff(item, when, now, clock_ok=True):
    """The alarm event for this occurrence, or None. The text has no title."""
    if not clock_ok or item.reminder not in _DELTA:
        return None
    now = _aware(now)
    when = _aware(when).astimezone(now.tzinfo)
    fire = when - _DELTA[item.reminder]
    if ack_key(item.uid, fire) in item.ack or now < fire:
        return None
    text = _label(item, when)
    if now - fire > alarms.GRACE:
        return alarms.Event("missed", "calendar", item.uid, fire, "Missed " + text)
    return alarms.Event("ring", "calendar", item.uid, fire, "Reminder: " + text)


def agenda(items, now):
    """Today, tomorrow, and the rest of the next seven days."""
    now = _aware(now)
    today = now.astimezone(now.tzinfo).date()
    start = datetime(today.year, today.month, today.day, tzinfo=now.tzinfo)
    end = start + timedelta(days=7) - timedelta(seconds=1)
    groups = {"today": [], "tomorrow": [], "later": []}
    for item in items:
        for when in occurrences(item, start, end):
            day = when.astimezone(now.tzinfo).date()
            row = {
                "id": item.uid, "title": item.title, "date": day.isoformat(),
                "time": "" if item.all_day else _label(item, when), "all_day": item.all_day,
            }
            if day == today:
                groups["today"].append(row)
            elif day == today + timedelta(days=1):
                groups["tomorrow"].append(row)
            elif day <= today + timedelta(days=6):
                groups["later"].append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: (row["date"], row["all_day"], row["time"], row["title"]))
    return groups


def next_lock(items, now, clock_ok=True):
    """The next timed event as 'Next: 15:00', or ''. No title."""
    if not clock_ok:
        return ""
    now = _aware(now)
    best = None
    for item in items:
        if item.all_day:
            continue
        for when in occurrences(item, now, now + timedelta(days=370)):
            if when >= now and (best is None or when < best):
                best = when
            break
    if best is None:
        return ""
    return "Next: %02d:%02d" % (best.hour, best.minute)


def month_days(year, month):
    """Day numbers for a Monday-first month, plus the blank cells before day 1."""
    if isinstance(year, bool) or isinstance(month, bool):
        raise ValueError("date")
    first = date(int(year), int(month), 1)
    if first.month != month:
        raise ValueError("date")
    lead = first.weekday()
    last = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    days = []
    for number in range(1, last.day + 1):
        days.append({"n": number, "date": "%04d-%02d-%02d" % (year, month, number)})
    return {"year": year, "month": month, "lead": lead, "days": days}


def ack_key(uid, fire):
    """The saved id of one reminder. It is not a title."""
    return uid + "@" + fire.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _clock(hour, minute):
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("time")
    if isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute <= 59:
        raise ValueError("time")
    return hour, minute


def _weekdays(days):
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


class Book:
    """The calendar.ics in one state directory. A file that cannot be read is moved aside."""

    def __init__(self, root, zone=None):
        self.store = Store(root)
        self.zone = zone or ZoneInfo("UTC")
        self._lock = threading.Lock()
        self.events = []
        self.notices = []
        self.recovered = ""
        self.view = None
        self._load()

    def _load(self):
        path = self.store.root / FILE
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self.store._quarantine(path)
            self.recovered = self.store.note_once(FILE)
            return
        if len(raw) > MAX_JSON:
            self.store._quarantine(path)
            self.recovered = self.store.note_once(FILE)
            return
        try:
            self.events, self.notices, _skipped = parse(raw.decode("utf-8"), self.zone)
        except (UnicodeError, ValueError):
            self.store._quarantine(path)
            self.recovered = self.store.note_once(FILE)

    def _save(self):
        raw = emit(self.events, self.notices).encode("utf-8")
        self.store.write_bytes(FILE, raw, MAX_JSON)

    def add(self, title, day, hour, minute, all_day, rule, days, reminder):
        with self._lock:
            if len(self.events) >= MAX_EVENTS:
                raise ValueError("full")
            item = self._make(uuid.uuid4().hex[:8], title, day, hour, minute, all_day, rule, days, reminder, ())
            self.events.append(item)
            self._save()
            return item

    def edit(self, uid, title, day, hour, minute, all_day, rule, days, reminder):
        with self._lock:
            old = self._get(uid)
            item = self._make(uid, title, day, hour, minute, all_day, rule, days, reminder, old.ack)
            self.events = [item if row.uid == uid else row for row in self.events]
            self._save()
            return item

    def delete(self, uid):
        with self._lock:
            self._get(uid)
            self.events = [row for row in self.events if row.uid != uid]
            self._save()

    def dismiss(self, key):
        with self._lock:
            if not isinstance(key, str) or "@" not in key:
                raise ValueError("id")
            uid = key.split("@", 1)[0]
            self._get(uid)
            self.events = [
                Item(row.uid, row.title, row.start, row.end, row.all_day, row.rule, row.days, row.reminder, row.ack + ((key,) if key not in row.ack else ()))
                if row.uid == uid else row
                for row in self.events
            ]
            self._save()

    def show(self, day):
        with self._lock:
            if not isinstance(day, date):
                raise ValueError("date")
            self.view = day

    def import_text(self, text):
        """Merge events by uid. Returns how many fields were skipped."""
        with self._lock:
            found, _notes, skipped = parse(text, self.zone)
            by_id = {item.uid: item for item in self.events}
            for item in found:
                if item.uid not in by_id and len(by_id) >= MAX_EVENTS:
                    break
                by_id[item.uid] = item
            self.events = list(by_id.values())
            self._save()
            return skipped

    def tick(self, now, clock_ok=True):
        """Reminder events since they became due. An unknown clock changes nothing."""
        with self._lock:
            now = _aware(now).astimezone(self.zone)
            if not clock_ok:
                return []
            window_start = now - timedelta(days=1, minutes=20)
            window_end = now + timedelta(days=1, minutes=1)
            found = []
            for item in self.events:
                for when in occurrences(item, window_start, window_end):
                    event = handoff(item, when, now, True)
                    if event is not None:
                        found.append((item, event))
            if not found:
                return []
            found.sort(key=lambda pair: pair[1].at)
            ringing = [pair for pair in found if pair[1].kind == "ring"]
            chosen = ringing[0] if ringing else found[0]
            item, event = chosen
            if event.kind == "missed":
                key = ack_key(item.uid, event.at)
                self.events = [
                    Item(row.uid, row.title, row.start, row.end, row.all_day, row.rule, row.days, row.reminder, row.ack + (key,))
                    if row.uid == item.uid and key not in row.ack else row
                    for row in self.events
                ]
                self.notices = (self.notices + [event.text])[-20:]
                self._save()
            return [event]

    def due(self, now, clock_ok=True):
        """The reminder to show now, if one is inside the grace window."""
        now = _aware(now).astimezone(self.zone)
        if not clock_ok:
            return None
        best = None
        for item in self.events:
            for when in occurrences(item, now - timedelta(days=1, minutes=20), now + timedelta(days=1, minutes=1)):
                event = handoff(item, when, now, True)
                if event is not None and event.kind == "ring" and (best is None or event.at < best.at):
                    best = event
        return best

    def _get(self, uid):
        for item in self.events:
            if item.uid == uid:
                return item
        raise ValueError("id")

    def _make(self, uid, title, day, hour, minute, all_day, rule, days, reminder, ack):
        if not isinstance(title, str):
            raise ValueError("title")
        title = " ".join(title.split())
        if not title or len(title) > MAX_TITLE:
            raise ValueError("title")
        if not isinstance(day, date):
            raise ValueError("date")
        if rule not in ("", "daily", "weekly"):
            raise ValueError("rule")
        if reminder not in REMINDERS:
            raise ValueError("reminder")
        if not isinstance(all_day, bool):
            raise ValueError("all_day")
        picked = _weekdays(days) if rule == "weekly" else ()
        if all_day:
            start = datetime(day.year, day.month, day.day, tzinfo=self.zone)
            end = start + timedelta(days=1)
        else:
            hour, minute = _clock(hour, minute)
            start = alarms.first_valid(day, hour, minute, self.zone)
            if start is None:
                raise ValueError("skipped")
            end = start + timedelta(hours=1)
        if rule == "weekly" and not picked:
            picked = (start.weekday(),)
        return Item(uid, title, start, end, all_day, rule, picked, reminder, tuple(ack))
