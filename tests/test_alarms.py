# SPDX-License-Identifier: GPL-3.0-or-later
"""The alarm engine: repeat rules, daylight-saving time, snooze, a missed fire, and an unknown clock."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from opos.alarms import Book, Event, first_valid, latest_fire, next_fire
from opos.home import is_quiet

try:
    UTC = ZoneInfo("UTC")
    NY = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:  # e.g. Windows without the tzdata package
    raise unittest.SkipTest("no IANA time zone database on this host") from None


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

    def test_scheduled_occurrence_bounds_use_instants(self):
        first = at(NY, 2026, 11, 1, 1, 30)
        second = at(NY, 2026, 11, 1, 1, 15, 1)
        end = at(NY, 2026, 11, 1, 1, 31, 1)
        matched = latest_fire(1, 30, (6,), at(NY, 2026, 11, 1, 1, 29), first)
        self.assertEqual(matched.astimezone(UTC), at(UTC, 2026, 11, 1, 5, 30))
        self.assertEqual(next_fire(1, 30, (6,), first).astimezone(UTC), at(UTC, 2026, 11, 1, 5, 30))
        self.assertIsNone(latest_fire(1, 30, (6,), first, end))
        self.assertIsNone(latest_fire(1, 30, (6,), second, end))
        self.assertEqual(next_fire(1, 30, (6,), second), at(NY, 2026, 11, 8, 1, 30))

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

    def test_snooze_deadline_uses_elapsed_minutes_during_the_repeated_hour(self):
        for minute, fold, expected in ((55, 0, "2026-11-01T06:05:00+00:00"), (20, 1, "2026-11-01T06:30:00+00:00")):
            with self.subTest(fold=fold):
                book = self.book(NY)
                fire = at(NY, 2026, 11, 1, 1, 0)
                row = book.add(1, 0, days=(6,), now=fire - timedelta(minutes=1))
                self.assertEqual(book.tick(fire), [Event("ring", "alarm", row["id"], fire, "Alarm 01:00")])
                now = at(NY, 2026, 11, 1, 1, minute, fold)
                self.assertEqual(book.snooze(now, row["id"]), dict(row, snooze_at=expected))
                reloaded = Book(book.store.root, NY)
                self.assertEqual(reloaded.alarms, [dict(row, snooze_at=expected)])
                self.assertEqual(reloaded.last_seen, now.astimezone(UTC).isoformat())

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

    def test_second_fold_does_not_replay_earlier_alarm(self):
        book = self.book(NY)
        start = at(NY, 2026, 11, 1, 1, 15, 1)
        row = book.add(1, 30, days=(6,), now=start)
        self.assertEqual(book.next_alarm(start)["at"], at(NY, 2026, 11, 8, 1, 30))
        self.assertEqual(book.tick(at(NY, 2026, 11, 1, 1, 31, 1)), [])
        self.assertEqual(Book(book.store.root, NY).alarms, [row])

    def test_alarm_grace_uses_elapsed_time_across_fall_back(self):
        for minute, kind, text in ((5, "ring", "Alarm 01:55"), (15, "missed", "Missed alarm 01:55")):
            with self.subTest(kind=kind):
                book = self.book(NY)
                start = at(NY, 2026, 11, 1, 1, 50)
                fire = at(NY, 2026, 11, 1, 1, 55)
                row = book.add(1, 55, days=(6,), now=start)
                events = book.tick(at(NY, 2026, 11, 1, 1, minute, 1))
                self.assertEqual(events, [Event(kind, "alarm", row["id"], fire, text)])
                self.assertEqual(events[0].at.astimezone(UTC), at(UTC, 2026, 11, 1, 5, 55))

    def test_timer_and_reload(self):
        book = self.book()
        start = at(UTC, 2026, 11, 9, 12, 0)
        timer = book.start_timer(60, start, label="Eggs")
        due = at(UTC, 2026, 11, 9, 12, 1)
        self.assertEqual(timer["fires_at"], due.isoformat())
        self.assertEqual(book.tick(start + timedelta(seconds=59)), [])
        self.assertEqual(book.tick(due), [Event("ring", "timer", timer["id"], due, "Timer Eggs")])
        book.start_timer(60, start)
        path = book.store.root
        again = Book(path, UTC)
        self.assertIsNotNone(again.timer)
        gone = Book(path, UTC)
        gone.cancel_timer()
        self.assertIsNone(Book(path, UTC).timer)

    def test_timer_deadline_uses_elapsed_seconds_during_the_repeated_hour(self):
        for minute, fold, expected in ((55, 0, "2026-11-01T06:05:00+00:00"), (20, 1, "2026-11-01T06:30:00+00:00")):
            with self.subTest(fold=fold):
                book = self.book(NY)
                start = at(NY, 2026, 11, 1, 1, minute, fold)
                timer = book.start_timer(600, start)
                self.assertEqual(timer["fires_at"], expected)
                self.assertEqual(Book(book.store.root, NY).timer, timer)

    def test_saved_deadlines_use_instants_across_fall_back(self):
        start = at(UTC, 2026, 11, 1, 5, 55)
        due = at(UTC, 2026, 11, 1, 6, 5)
        for previous, seconds, fires in ((start, -1, False), (start, 0, True), (start, 60, True), (due, 60, False)):
            with self.subTest(previous=previous, seconds=seconds):
                book = self.book(NY)
                row = book.add(1, 55, days=(6,), now=start)
                timer = {"id": "timer", "fires_at": due.isoformat(), "label": "Eggs"}
                book.store.write_json(
                    "alarms.json",
                    {
                        "alarms": [dict(row, snooze_at=due.isoformat())],
                        "timer": timer,
                        "last_seen": previous.isoformat(),
                        "notices": [],
                    },
                )
                book = Book(book.store.root, NY)
                events = book.tick(due + timedelta(seconds=seconds))
                if fires:
                    local = due.astimezone(NY)
                    self.assertEqual(
                        events,
                        [
                            Event("ring", "timer", "timer", local, "Timer Eggs"),
                            Event("ring", "alarm", row["id"], local, "Alarm 01:55"),
                        ],
                    )
                    self.assertEqual([event.at.astimezone(UTC) for event in events], [due, due])
                    self.assertTrue(all(event.at.tzinfo is NY and event.at.fold == 1 for event in events))
                    self.assertIsNone(book.timer)
                    self.assertNotIn("snooze_at", book.alarms[0])
                else:
                    self.assertEqual(events, [])
                    self.assertEqual(book.timer, timer)
                    self.assertEqual(book.alarms[0]["snooze_at"], due.isoformat())

    def test_one_shot_ring_next_tick_reload_and_stop(self):
        book = self.book()
        fire = at(UTC, 2026, 11, 9, 7, 0)
        row = book.add(7, 0, label="Kitchen", now=fire - timedelta(hours=1))
        self.assertEqual(book.tick(fire), [Event("ring", "alarm", row["id"], fire, "Alarm 07:00")])
        self.assertEqual(book.alarms[0], dict(row, done=True, ringing=True))
        self.assertEqual(
            book.next_alarm(fire + timedelta(seconds=1)),
            {
                "id": row["id"],
                "at": fire + timedelta(days=1),
                "label": "Kitchen",
                "text": "Alarm 07:00",
            },
        )
        book = Book(book.store.root, UTC)
        self.assertEqual(book.tick(fire + timedelta(seconds=30)), [])
        self.assertEqual(book.alarms[0], dict(row, enabled=False, ringing=True))
        self.assertIsNone(book.next_alarm(fire + timedelta(seconds=30)))
        book = Book(book.store.root, UTC)
        self.assertEqual(book.stop(fire + timedelta(minutes=1)), dict(row, enabled=False))
        self.assertEqual(Book(book.store.root, UTC).alarms, [dict(row, enabled=False)])

    def test_one_shot_can_snooze_after_the_next_tick_disabled_it(self):
        book = self.book()
        fire = at(UTC, 2026, 11, 9, 7, 0)
        row = book.add(7, 0, now=fire - timedelta(hours=1))
        book.tick(fire)
        book.tick(fire + timedelta(seconds=30))
        snoozed_at = fire + timedelta(minutes=1)
        snoozed = book.snooze(snoozed_at)
        self.assertEqual(snoozed, dict(row, snooze_at="2026-11-09T07:11:00+00:00"))
        book = Book(book.store.root, UTC)
        self.assertEqual(
            book.next_alarm(snoozed_at),
            {
                "id": row["id"],
                "at": snoozed_at + timedelta(minutes=10),
                "label": "",
                "text": "Alarm 07:00",
            },
        )
        self.assertEqual(
            book.tick(snoozed_at + timedelta(minutes=10)),
            [
                Event("ring", "alarm", row["id"], snoozed_at + timedelta(minutes=10), "Alarm 07:00"),
            ],
        )
        self.assertEqual(book.stop(snoozed_at + timedelta(minutes=10), row["id"]), dict(row, enabled=False, done=True))

    def test_unknown_clock_preserves_the_entire_saved_book(self):
        book = self.book()
        fire = at(UTC, 2026, 11, 9, 7, 0)
        row = book.add(7, 0, days=(0,), now=fire - timedelta(hours=1))
        book.tick(fire)
        book.snooze(fire, row["id"])
        book.start_timer(60, fire, "Eggs")
        path = book.store.root / "alarms.json"
        before = path.read_bytes()
        self.assertEqual(book.tick(fire + timedelta(minutes=30), clock_ok=False), [])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(Book(book.store.root, UTC).last_seen, fire.isoformat())

    def test_late_restart_repeating_alarm_and_timer_keep_event_contract(self):
        book = self.book()
        fire = at(UTC, 2026, 11, 9, 7, 0)
        row = book.add(7, 0, days=(0,), now=fire - timedelta(hours=1))
        timer = book.start_timer(60, fire - timedelta(minutes=1))
        book = Book(book.store.root, UTC)
        self.assertEqual(
            book.tick(fire + timedelta(minutes=11)),
            [
                Event("missed", "timer", timer["id"], fire, "Missed timer"),
                Event("missed", "alarm", row["id"], fire, "Missed alarm 07:00"),
            ],
        )
        self.assertEqual(book.alarms, [row])
        self.assertIsNone(book.timer)
        self.assertEqual(book.notices, ["Missed timer", "Missed alarm 07:00"])
        self.assertEqual(Book(book.store.root, UTC).notices, book.notices)

    def test_grace_boundary_for_alarm_snooze_and_timer(self):
        fire = at(UTC, 2026, 11, 9, 7, 0)
        for kind in ("alarm", "snooze", "timer"):
            for late in (False, True):
                with self.subTest(kind=kind, late=late):
                    book = self.book()
                    if kind == "timer":
                        book.start_timer(60, fire - timedelta(minutes=1))
                    else:
                        row = book.add(7, 0, now=fire - timedelta(hours=1))
                        if kind == "snooze":
                            book.tick(fire)
                            book.snooze(fire, row["id"])
                    due = fire + timedelta(minutes=10 if kind == "snooze" else 0)
                    events = book.tick(due + timedelta(minutes=10, seconds=int(late)))
                    self.assertEqual(len(events), 1)
                    self.assertEqual(events[0].kind, "missed" if late else "ring")
                    self.assertEqual(events[0].what, "timer" if kind == "timer" else "alarm")
                    self.assertEqual(events[0].at, due)

    def test_timer_grace_uses_elapsed_seconds_across_clock_changes(self):
        spring = at(UTC, 2026, 3, 8, 6, 55)
        fall = at(UTC, 2026, 11, 1, 5, 5)
        cases = (
            (spring, 600, "ring", "Timer Eggs"),
            (spring, 601, "missed", "Missed timer"),
            (fall, 3600, "missed", "Missed timer"),
        )
        for due, seconds, kind, text in cases:
            with self.subTest(due=due, seconds=seconds):
                book = self.book(NY)
                book.store.write_json(
                    "alarms.json",
                    {
                        "alarms": [],
                        "timer": {"id": "timer", "fires_at": due.isoformat(), "label": "Eggs"},
                        "last_seen": (due - timedelta(seconds=1)).isoformat(),
                        "notices": [],
                    },
                )
                book = Book(book.store.root, NY)
                events = book.tick(due + timedelta(seconds=seconds))
                self.assertEqual(events, [Event(kind, "timer", "timer", due.astimezone(NY), text)])
                self.assertEqual(events[0].at.astimezone(UTC), due)
                self.assertIs(events[0].at.tzinfo, NY)
                self.assertIsNone(book.timer)
                self.assertEqual(book.notices, [text] if kind == "missed" else [])

    def test_alarm_actions_preserve_timer_deadline_across_reload(self):
        ring = at(UTC, 2026, 11, 9, 7, 0)
        due = ring + timedelta(seconds=15)
        for action in (Book.stop, Book.snooze):
            for offset in (-1, 0, 1):
                with self.subTest(action=action.__name__, offset=offset):
                    book = self.book()
                    alarm = book.add(7, 0, now=ring - timedelta(hours=1))
                    timer = book.start_timer(60, due - timedelta(seconds=60), "Eggs")
                    self.assertEqual(book.tick(ring), [Event("ring", "alarm", alarm["id"], ring, "Alarm 07:00")])
                    action_at = due + timedelta(microseconds=offset)
                    action(book, action_at, alarm["id"])

                    path = book.store.root / "alarms.json"
                    written = path.read_bytes()
                    data = json.loads(written)
                    self.assertEqual(data["schema"], 1)
                    self.assertEqual(data["last_seen"], action_at.isoformat())
                    self.assertEqual(data["timer"], timer)
                    self.assertEqual(set(timer), {"id", "fires_at", "label"})
                    resumed = Book(book.store.root, UTC)
                    self.assertEqual(resumed.tick(due + timedelta(seconds=2), clock_ok=False), [])
                    self.assertEqual(path.read_bytes(), written)
                    self.assertEqual(
                        resumed.tick(due + timedelta(seconds=6)),
                        [Event("ring", "timer", timer["id"], due, "Timer Eggs")],
                    )
                    resumed = Book(book.store.root, UTC)
                    self.assertIsNone(resumed.timer)
                    self.assertEqual(resumed.tick(due + timedelta(seconds=7)), [])

    def test_alarm_action_preserves_the_interval_of_an_unmarked_saved_timer(self):
        book = self.book()
        ring = at(UTC, 2026, 11, 9, 7, 0)
        due = ring + timedelta(seconds=15)
        alarm = book.add(7, 0, now=ring - timedelta(hours=1))
        book.tick(ring)
        timer = {"id": "eggs", "fires_at": due.isoformat(), "label": "Eggs", "timer_seen": "legacy extra"}
        book.store.write_json(
            "alarms.json",
            {"alarms": book.alarms, "timer": timer, "last_seen": ring.isoformat(sep=" "), "notices": ["Earlier"]},
        )
        resumed = Book(book.store.root, UTC)
        resumed.stop(due, alarm["id"])
        resumed = Book(book.store.root, UTC)
        self.assertEqual(resumed.timer, timer)
        self.assertEqual(resumed.last_seen, due.isoformat())
        self.assertEqual(
            resumed.tick(due + timedelta(minutes=10, seconds=1)),
            [Event("missed", "timer", "eggs", due, "Missed timer")],
        )
        resumed = Book(book.store.root, UTC)
        self.assertEqual(resumed.notices, ["Earlier", "Missed timer"])
        self.assertEqual(resumed.tick(due + timedelta(minutes=11)), [])

    def test_alarm_actions_replace_an_unusable_legacy_last_seen(self):
        ring = at(UTC, 2026, 11, 9, 7, 0)
        due = ring + timedelta(seconds=15)
        timer = {"id": "tea", "fires_at": due.isoformat(), "label": "Tea"}
        prior_values = ("2026-02-29T07:00:00+00:00", "0001-01-01T00:00:00+00:01", "9999-12-31T23:59:59-00:01")
        for action in (Book.stop, Book.snooze):
            for prior in prior_values:
                with self.subTest(action=action.__name__, prior=prior):
                    book = self.book()
                    alarm = book.add(7, 0, now=ring - timedelta(hours=1))
                    book.tick(ring)
                    book.store.write_json(
                        "alarms.json",
                        {"alarms": book.alarms, "timer": timer, "last_seen": prior, "notices": ["Earlier"]},
                    )
                    book = Book(book.store.root, UTC)
                    row = action(book, due, alarm["id"])
                    self.assertNotIn("ringing", row)
                    saved, _ = book.store.read_json("alarms.json", {})
                    self.assertEqual(saved["last_seen"], due.isoformat())
                    self.assertEqual(saved["timer"], timer)
                    self.assertNotIn("timer_seen", saved)

                    resumed = Book(book.store.root, UTC)
                    self.assertEqual(resumed.alarms, [row])
                    self.assertEqual(resumed.tick(due + timedelta(seconds=1)), [])
                    self.assertEqual(resumed.timer, timer)
                    self.assertEqual(resumed.notices, ["Earlier"])

    def test_legacy_timer_ignores_unrecognized_cursor_metadata(self):
        due = at(UTC, 2026, 11, 9, 7, 0)
        timer = {"id": "old-timer", "fires_at": due.isoformat(), "label": "Tea"}
        for metadata in (None, True, "earlier", {"cursor": 0}, 10**30):
            with self.subTest(metadata=metadata):
                book = self.book()
                book.store.write_json(
                    "alarms.json",
                    {"timer": timer, "last_seen": due.isoformat(), "timer_seen": metadata},
                )
                resumed = Book(book.store.root, UTC)
                self.assertEqual(resumed.tick(due + timedelta(seconds=1)), [])
                self.assertEqual(resumed.timer, timer)

    def test_replacement_timer_uses_its_start_after_backward_clock_correction(self):
        book = self.book()
        started = at(UTC, 2026, 11, 9, 6, 59)
        before_correction = started + timedelta(hours=1)
        book.tick(before_correction)
        book.start_timer(15, started, "Cancelled")
        book.cancel_timer()
        saved, _ = book.store.read_json("alarms.json", {})
        self.assertIsNone(saved["timer"])
        self.assertNotIn("timer_seen", saved)
        book.start_timer(30, started, "Replaced")
        timer = book.start_timer(60, started, "Eggs")
        resumed = Book(book.store.root, UTC)
        self.assertEqual(resumed.last_seen, before_correction.isoformat())
        due = started + timedelta(seconds=60)
        self.assertEqual(resumed.tick(due), [Event("ring", "timer", timer["id"], due, "Timer Eggs")])
        saved, _ = resumed.store.read_json("alarms.json", {})
        self.assertIsNone(saved["timer"])
        self.assertNotIn("timer_seen", saved)

    def test_edit_toggle_delete_and_saved_field_names(self):
        book = self.book()
        now = at(UTC, 2026, 11, 9, 6, 0)
        row = book.add(7, 0, now=now)
        self.assertEqual(book.toggle(row["id"], False), dict(row, enabled=False))
        edited = dict(row, hour=8, minute=5, days=[0, 2], label="Tea", once=False, enabled=False)
        self.assertEqual(book.edit(row["id"], 8, 5, (2, 0), " Tea "), edited)
        edited["enabled"] = True
        self.assertEqual(book.toggle(row["id"], True), edited)
        data = json.loads((book.store.root / "alarms.json").read_text())
        self.assertEqual(
            data, {"schema": 1, "alarms": [edited], "timer": None, "last_seen": now.isoformat(), "notices": []}
        )
        book.delete(row["id"])
        self.assertEqual(Book(book.store.root, UTC).alarms, [])

    def test_stop_can_select_a_due_alarm_before_tick_or_an_explicit_id(self):
        book = self.book()
        fire = at(UTC, 2026, 11, 9, 7, 0)
        row = book.add(7, 0, now=fire - timedelta(hours=1))
        self.assertEqual(book.stop(fire), dict(row, enabled=False))
        self.assertEqual(book.tick(fire + timedelta(seconds=30)), [])
        book.toggle(row["id"], True)
        self.assertEqual(book.stop(fire + timedelta(days=1), row["id"]), dict(row, enabled=False))

    def test_toggle_off_while_ringing_keeps_stop_and_snooze_available(self):
        fire = at(UTC, 2026, 11, 9, 7, 0)
        for days in ((), (0,)):
            with self.subTest(days=days):
                book = self.book()
                row = book.add(7, 0, days=days, now=fire - timedelta(hours=1))
                book.tick(fire)
                book.toggle(row["id"], False)
                self.assertTrue(book.alarms[0]["ringing"])
                self.assertIsNone(book.next_alarm(fire))
                self.assertTrue(book.snooze(fire, row["id"])["enabled"])
                book.stop(fire, row["id"])
                self.assertNotIn("ringing", book.alarms[0])

    def test_edit_preserves_a_pending_snooze_and_uses_the_new_label_and_time(self):
        book = self.book()
        fire = at(UTC, 2026, 11, 9, 7, 0)
        row = book.add(7, 0, days=(0,), now=fire - timedelta(hours=1))
        book.tick(fire)
        book.snooze(fire, row["id"])
        edited = book.edit(row["id"], 8, 5, (0,), "Tea")
        self.assertEqual(edited["snooze_at"], "2026-11-09T07:10:00+00:00")
        self.assertEqual(book.next_alarm(fire)["text"], "Alarm 08:05")
        self.assertEqual(book.next_alarm(fire)["label"], "Tea")
        self.assertEqual(
            book.tick(fire + timedelta(minutes=10)),
            [
                Event("ring", "alarm", row["id"], fire + timedelta(minutes=10), "Alarm 08:05"),
            ],
        )

    def test_a_repeating_alarm_still_rings_next_week_without_a_stop(self):
        book = self.book()
        fire = at(UTC, 2026, 11, 9, 7, 0)
        row = book.add(7, 0, days=(0,), now=fire - timedelta(hours=1))
        book.tick(fire)
        self.assertEqual(
            book.tick(fire + timedelta(days=7)),
            [
                Event("ring", "alarm", row["id"], fire + timedelta(days=7), "Alarm 07:00"),
            ],
        )

    def test_switching_off_a_snoozed_alarm_cancels_its_ring(self):
        for days in ((), (0,)):
            with self.subTest(days=days):
                book = self.book()
                fire = at(UTC, 2026, 11, 9, 7, 0)
                row = book.add(7, 0, days=days, now=fire - timedelta(hours=1))
                book.tick(fire)
                book.snooze(fire, row["id"])
                book.toggle(row["id"], False)
                self.assertIsNone(book.next_alarm(fire))
                book = Book(book.store.root, UTC)
                self.assertEqual(book.tick(fire + timedelta(minutes=10)), [])
                self.assertNotIn("snooze_at", book.alarms[0])
                self.assertNotIn("ringing", book.alarms[0])

    def test_snooze_requires_a_ring_even_with_an_explicit_id(self):
        fire = at(UTC, 2026, 11, 9, 7, 0)
        for enabled, selected, now in (
            (True, True, fire - timedelta(minutes=1)),
            (False, True, fire),
            (True, False, fire),
        ):
            with self.subTest(enabled=enabled, selected=selected):
                book = self.book()
                row = book.add(7, 0, now=fire - timedelta(hours=1))
                book.toggle(row["id"], enabled)
                path = book.store.root / "alarms.json"
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "^id$"):
                    book.snooze(now, row["id"] if selected else None)
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
