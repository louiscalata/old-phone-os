# SPDX-License-Identifier: GPL-3.0-or-later
"""Calendar file: ICS round trip, weekly and daily repeat across a clock change, and reminder hand-off."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from opos import alarms, calendar_ics
from opos.home import is_quiet

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

ROUND = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:kitchen1\r
SUMMARY:Tea\\, cake and a fol\r
 ded line\r
DTSTART:20261110T150000Z\r
DTEND:20261110T160000Z\r
RRULE:FREQ=WEEKLY;BYDAY=MO,WE\r
BEGIN:VALARM\r
ACTION:DISPLAY\r
TRIGGER:-PT10M\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:cafe1\r
SUMMARY:Café\r
DTSTART;VALUE=DATE:20261112\r
DTEND;VALUE=DATE:20261113\r
RRULE:FREQ=DAILY\r
END:VEVENT\r
END:VCALENDAR\r
"""


class CalendarFile(unittest.TestCase):
    def test_round_trip_keeps_the_fields_and_joins_a_folded_line(self):
        items, notices, skipped = calendar_ics.parse(ROUND, UTC)
        self.assertEqual(skipped, 0)
        self.assertEqual(notices, [])
        self.assertEqual(items[0].title, "Tea, cake and a folded line")
        self.assertEqual(items[0].rule, "weekly")
        self.assertEqual(items[0].days, (0, 2))
        self.assertEqual(items[0].reminder, "10min")
        self.assertEqual(items[0].start, datetime(2026, 11, 10, 15, 0, tzinfo=UTC))
        self.assertEqual(items[1].title, "Café")
        self.assertTrue(items[1].all_day)
        self.assertEqual(items[1].rule, "daily")
        again, _notes, extra = calendar_ics.parse(calendar_ics.emit(items), UTC)
        self.assertEqual(extra, 0)
        self.assertEqual([(row.uid, row.title, row.start, row.rule, row.days, row.reminder, row.all_day) for row in again],
                         [(row.uid, row.title, row.start, row.rule, row.days, row.reminder, row.all_day) for row in items])

    def test_a_monthly_rule_is_skipped_and_the_event_happens_once(self):
        text = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:once1\nSUMMARY:Once\nDTSTART:20261110T150000Z\nRRULE:FREQ=MONTHLY\nEND:VEVENT\nEND:VCALENDAR\n"
        items, _notes, skipped = calendar_ics.parse(text, UTC)
        self.assertEqual(skipped, 1)
        self.assertEqual(items[0].rule, "")

    def test_daily_skips_the_spring_gap_and_rings_the_fall_back_hour_once(self):
        start = datetime(2026, 3, 6, 2, 30, tzinfo=NY)
        item = calendar_ics.Item("gap", "Gap", start, start + timedelta(hours=1), False, "daily", (), "none", ())
        window_start = datetime(2026, 3, 6, 0, 0, tzinfo=NY)
        window_end = datetime(2026, 3, 10, 12, 0, tzinfo=NY)
        days = [when.date().isoformat() for when in calendar_ics.occurrences(item, window_start, window_end)]
        self.assertEqual(days, ["2026-03-06", "2026-03-07", "2026-03-09", "2026-03-10"])
        fall = calendar_ics.Item("fall", "Fall", datetime(2026, 10, 31, 1, 30, tzinfo=NY), datetime(2026, 10, 31, 2, 30, tzinfo=NY), False, "daily", (), "none", ())
        found = calendar_ics.occurrences(fall, datetime(2026, 10, 31, 0, 0, tzinfo=NY), datetime(2026, 11, 2, 12, 0, tzinfo=NY))
        november = [when for when in found if when.date().isoformat() == "2026-11-01"]
        self.assertEqual(len(november), 1)
        self.assertEqual(november[0].fold, 0)
        self.assertEqual(november[0].hour, 1)

    def test_handoff_uses_the_alarm_grace_and_writes_nothing(self):
        start = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
        item = calendar_ics.Item("kitchen", "Kitchen", start, start + timedelta(hours=1), False, "", (), "10min", ())
        fire = start - timedelta(minutes=10)
        ring = calendar_ics.handoff(item, start, fire + timedelta(seconds=30), True)
        self.assertIsInstance(ring, alarms.Event)
        self.assertEqual(ring.kind, "ring")
        self.assertEqual(ring.text, "Reminder: 15:00")
        self.assertNotIn("Kitchen", ring.text)
        self.assertIsNone(calendar_ics.handoff(item, start, fire - timedelta(minutes=1), True))
        self.assertIsNone(calendar_ics.handoff(item, start, fire + timedelta(seconds=30), False))
        missed = calendar_ics.handoff(item, start, fire + timedelta(minutes=11), True)
        self.assertEqual(missed.kind, "missed")
        self.assertEqual(missed.text, "Missed 15:00")
        self.assertLessEqual(alarms.GRACE, timedelta(minutes=10))
        self.assertFalse(Path("alarms.json").exists())

    def test_a_bad_file_is_moved_aside_and_a_missed_reminder_is_kept_once(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "calendar.ics").write_text("this is not a calendar", encoding="utf-8")
        book = calendar_ics.Book(root, UTC)
        self.assertEqual(book.events, [])
        self.assertIn("could not be read", book.recovered)
        self.assertFalse((root / "calendar.ics").exists())
        self.assertTrue(any(name.startswith("calendar.ics.bad-") for name in (path.name for path in root.iterdir())))
        day = datetime(2026, 11, 10, tzinfo=UTC).date()
        book.add("Kitchen", day, 15, 0, False, "", (), "10min")
        now = datetime(2026, 11, 10, 15, 1, tzinfo=UTC)
        missed = book.tick(now, True)
        self.assertEqual([event.text for event in missed], ["Missed 15:00"])
        self.assertEqual(book.tick(now, True), [])
        self.assertTrue(is_quiet([event.text for event in missed]))
        self.assertFalse((root / "alarms.json").exists())
        text = (root / "calendar.ics").read_text(encoding="utf-8")
        self.assertNotIn("Kitchen", "\n".join(line for line in text.splitlines() if line.startswith("X-OPOS-NOTICE")))

    def test_the_lock_line_has_the_time_and_not_the_title(self):
        start = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
        item = calendar_ics.Item("secret", "Secret Lunch", start, start + timedelta(hours=1), False, "", (), "none", ())
        now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
        self.assertEqual(calendar_ics.next_lock([item], now, True), "Next: 15:00")
        self.assertNotIn("Secret", calendar_ics.next_lock([item], now, True))
        self.assertEqual(calendar_ics.next_lock([item], now, False), "")
        groups = calendar_ics.agenda([item], now)
        self.assertEqual(groups["today"][0]["title"], "Secret Lunch")
        self.assertEqual(groups["tomorrow"], [])

    def test_add_refuses_a_full_book_and_a_minute_that_does_not_exist(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        book = calendar_ics.Book(tmp.name, NY)
        day = datetime(2026, 3, 8, tzinfo=NY).date()
        with self.assertRaises(ValueError):
            book.add("Gap", day, 2, 30, False, "", (), "none")
        plain = datetime(2026, 11, 10, tzinfo=NY).date()
        for number in range(calendar_ics.MAX_EVENTS):
            book.add("Item %d" % number, plain, 9, 0, False, "", (), "none")
        with self.assertRaises(ValueError):
            book.add("One more", plain, 9, 0, False, "", (), "none")


if __name__ == "__main__":
    unittest.main()
