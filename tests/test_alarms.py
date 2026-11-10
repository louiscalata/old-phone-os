# SPDX-License-Identifier: GPL-3.0-or-later
"""The alarm engine: repeat rules, daylight-saving time, snooze, a missed fire, and an unknown clock."""
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from opos.alarms import Book, first_valid, next_fire
from opos.home import is_quiet

UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")


def at(zone, year, month, day, hour, minute, fold=0):
    return datetime(year, month, day, hour, minute, fold=fold, tzinfo=zone)


class Rules(unittest.TestCase):
    def test_weekdays_skip_the_weekend_and_a_gap_is_not_a_time(self):
        monday = at(UTC, 2026, 11, 9, 7, 0)  # Monday
        self.assertEqual(next_fire(7, 0, (0, 1, 2, 3, 4), monday), monday)
        saturday = at(UTC, 2026, 11, 7, 8, 0)
        self.assertEqual(next_fire(7, 0, (0, 1, 2, 3, 4), saturday), at(UTC, 2026, 11, 9, 7, 0))
        self.assertIsNone(first_valid(datetime(2026, 3, 8).date(), 2, 30, NY))
        self.assertEqual(first_valid(datetime(2026, 3, 8).date(), 3, 0, NY), at(NY, 2026, 3, 8, 3, 0))
        first = first_valid(datetime(2026, 11, 1).date(), 1, 30, NY)
        self.assertEqual(first, at(NY, 2026, 11, 1, 1, 30, 0))
        self.assertEqual(first.utcoffset(), timedelta(hours=-4))

    def test_labels_and_the_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            book = Book(folder, UTC)
            now = at(UTC, 2026, 11, 9, 6, 0)
            with self.assertRaises(ValueError):
                book.add(7, 0, label="x" * 41, now=now)
            with self.assertRaises(ValueError):
                book.add(24, 0, now=now)
            for n in range(20):
                book.add(7, n % 60, days=(0,), label=str(n), now=now)
            with self.assertRaises(ValueError):
                book.add(8, 0, now=now)


class Engine(unittest.TestCase):
    def book(self, zone=UTC):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        return Book(self._tmp.name, zone)

    def test_ring_snooze_and_stop(self):
        book = self.book()
        armed = at(UTC, 2026, 11, 9, 6, 0)
        row = book.add(7, 0, days=(0, 1, 2, 3, 4), label="Kitchen", now=armed)
        fire = at(UTC, 2026, 11, 9, 7, 0)
        events = book.tick(fire + timedelta(seconds=30))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "ring")
        self.assertEqual(events[0].text, "Alarm 07:00")
        self.assertLessEqual((events[0].at - at(UTC, 2026, 11, 9, 7, 0)).total_seconds(), 1)
        book.snooze(at(UTC, 2026, 11, 9, 7, 0) + timedelta(seconds=30), row["id"])
        again = book.tick(at(UTC, 2026, 11, 9, 7, 10) + timedelta(seconds=30))
        self.assertEqual([event.kind for event in again], ["ring"])
        self.assertEqual(again[0].at, at(UTC, 2026, 11, 9, 7, 10) + timedelta(seconds=30))
        book.stop(again[0].at, row["id"])
        self.assertTrue(book.alarms[0]["enabled"])
        self.assertEqual(book.tick(at(UTC, 2026, 11, 9, 7, 20)), [])

    def test_a_late_restart_is_missed_and_an_unknown_clock_does_not_ring(self):
        book = self.book()
        fire = at(UTC, 2026, 11, 9, 7, 0)
        book.add(7, 0, label="", now=fire - timedelta(hours=1))
        self.assertEqual(book.tick(fire, clock_ok=False), [])
        self.assertEqual(book.tick(fire + timedelta(minutes=11), clock_ok=False), [])
        missed = book.tick(fire + timedelta(minutes=11), clock_ok=True)
        self.assertEqual([event.text for event in missed], ["Missed alarm 07:00"])
        self.assertNotEqual(missed[0].kind, "ring")
        self.assertFalse(book.alarms[0]["enabled"])
        self.assertTrue(is_quiet([event.text for event in missed]))

    def test_spring_forward_is_skipped_and_fall_back_rings_once(self):
        book = self.book(NY)
        book.add(2, 30, days=(6,), now=at(NY, 2026, 3, 8, 1, 0))  # Sunday
        self.assertEqual(book.tick(at(NY, 2026, 3, 8, 4, 0)), [])
        nxt = book.next_alarm(at(NY, 2026, 3, 8, 4, 0))
        self.assertEqual(nxt["at"], at(NY, 2026, 3, 15, 2, 30))
        book.delete(book.alarms[0]["id"])
        book.add(1, 30, days=(6,), now=at(NY, 2026, 11, 1, 1, 0, 0))
        first = book.tick(at(NY, 2026, 11, 1, 1, 31, 0))
        self.assertEqual([event.kind for event in first], ["ring"])
        self.assertEqual(first[0].at.utcoffset(), timedelta(hours=-4))
        second = book.tick(at(NY, 2026, 11, 1, 1, 31, 1))
        self.assertEqual(second, [])

    def test_timer_and_reload(self):
        book = self.book()
        start = at(UTC, 2026, 11, 9, 12, 0)
        book.start_timer(60, start, label="Eggs")
        self.assertEqual(book.tick(start + timedelta(seconds=30)), [])
        rung = book.tick(start + timedelta(seconds=61))
        self.assertEqual(rung[0].text, "Timer Eggs")
        book.start_timer(60, start)
        path = book.store.root
        again = Book(path, UTC)
        self.assertIsNotNone(again.timer)
        gone = Book(path, UTC)
        gone.cancel_timer()
        self.assertIsNone(Book(path, UTC).timer)


if __name__ == "__main__":
    unittest.main()
