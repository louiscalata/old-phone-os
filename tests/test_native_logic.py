# SPDX-License-Identifier: GPL-3.0-or-later
"""What the native screens work out without a toolkit: typing, sums, and the lines for alarms, timers and calls."""
import ast
import unittest
from pathlib import Path

from opos.native import logic

NATIVE = Path(__file__).resolve().parents[1] / "src" / "opos" / "native"


class Typing(unittest.TestCase):
    def test_keys_add_and_delete(self):
        e = logic.Entry()
        for ch in ("H", "i", " ", None):
            e.key(ch)
        self.assertEqual(e.text, "Hi")
        self.assertFalse(logic.Entry().key(None), "deleting nothing changes nothing")

    def test_a_new_line_only_where_it_is_allowed(self):
        one, many = logic.Entry(), logic.Entry(multiline=True)
        self.assertFalse(one.key("\n"))
        self.assertTrue(many.key("\n"))
        self.assertEqual((one.text, many.text), ("", "\n"))

    def test_the_limit_is_in_bytes_and_is_never_passed(self):
        e = logic.Entry(limit=3)
        for ch in "abé":
            e.key(ch)
        self.assertEqual(e.text, "ab", "é is two bytes and does not fit")


class Sums(unittest.TestCase):
    def test_the_four_operations_and_their_order(self):
        for expr, answer in (("2+3", "5"), ("2+3×4", "14"), ("10-4÷2", "8"), ("7÷2", "3.5"), ("0.1+0.2", "0.3"),
                             ("-3+5", "2"), ("6×-2", "-12"), ("1÷3", "0.333333333333"), ("5-5", "0"), ("1000000×1000000", "1000000000000")):
            with self.subTest(expr=expr):
                self.assertEqual(logic.evaluate(expr), answer)

    def test_nonsense_and_dividing_by_zero_are_said_plainly(self):
        for expr in ("", "+", "2+", "2++3", "1.2.3", "abc", "--2"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    logic.evaluate(expr)
        with self.assertRaisesRegex(ValueError, "divide by zero"):
            logic.evaluate("4÷0")

    def test_the_keys_build_a_sum_and_the_tape_keeps_it(self):
        c = logic.Calc()
        for key in "12+3=":
            c.press(key)
        self.assertEqual((c.expr, c.tape), ("15", ["12+3 = 15"]))
        for key in ("×", "+", "2", "="):       # a second sign replaces the first; the answer carries on
            c.press(key)
        self.assertEqual(c.expr, "17")

    def test_one_point_per_number_delete_and_clear(self):
        c = logic.Calc()
        for key in ("1", ".", ".", "5", "+", "2", ".", "5", "<", "<"):
            c.press(key)
        self.assertEqual(c.expr, "1.5+2")
        c.press("C")
        self.assertEqual(c.expr, "")

    def test_a_bad_sum_leaves_a_note_and_the_sum_as_typed(self):
        c = logic.Calc()
        for key in ("4", "÷", "0", "="):
            c.press(key)
        self.assertEqual((c.expr, c.tape), ("4÷0", []))
        self.assertIn("zero", c.note)


class Lines(unittest.TestCase):
    def test_typed_digits_are_read_as_a_time(self):
        self.assertEqual(logic.clock_digits("0730"), (7, 30))
        self.assertEqual(logic.clock_digits("730"), (7, 30))
        self.assertEqual(logic.clock_digits("2359"), (23, 59))
        for bad in ("", "7", "73", "2460", "1299", "ab30"):
            self.assertIsNone(logic.clock_digits(bad), bad)
        self.assertEqual([logic.digits_shown(d) for d in ("", "7", "73", "730", "0730")], ["", "7", "73", "7:30", "07:30"])

    def test_an_alarm_reads_as_time_days_and_state(self):
        self.assertEqual(logic.alarm_line({"hour": 7, "minute": 5, "days": [], "enabled": True}), ("07:05", "Once  On"))
        self.assertEqual(logic.alarm_line({"hour": 22, "minute": 0, "days": list(range(7)), "label": "Pills", "enabled": False}),
                         ("22:00", "Every day  Pills  Off"))

    def test_a_timer_counts_down_and_ends_quietly(self):
        self.assertEqual(logic.timer_left("2026-11-13T10:05:00+00:00", "2026-11-13T10:00:55+00:00"), "4 min 05 s left")
        self.assertEqual(logic.timer_left("2026-11-13T10:00:30+00:00", "2026-11-13T10:00:00+00:00"), "30 s left")
        self.assertEqual(logic.timer_left("2026-11-13T10:00:00+00:00", "2026-11-13T10:00:00+00:00"), "")
        self.assertEqual(logic.timer_left(None, "nonsense"), "")

    def test_a_call_reads_as_who_then_what_happened(self):
        title, detail = logic.call_line({"name": "Mom", "number": "+15550100", "direction": "missed", "start_time": "2026-11-13T10:00:00+00:00",
                                         "duration": None, "practice": True})
        self.assertEqual(title, "Mom")
        self.assertTrue(detail.startswith("Missed"))
        self.assertTrue(detail.endswith("Practice"))
        self.assertEqual(logic.call_line({"number": "+15550142", "direction": "outgoing", "start_time": "bad", "duration": 125}),
                         ("+15550142", "Outgoing  2 min"))


class Ordering(unittest.TestCase):
    """A late reply never puts the screen back, and a failed poll never empties it."""

    def test_an_older_reply_is_dropped_once_a_newer_one_was_drawn(self):
        seq = logic.Sequence()
        poll, action = seq.issue(), seq.issue()     # the poll was sent first and the action after it
        self.assertTrue(seq.accept(action))
        self.assertFalse(seq.accept(poll), "the poll's reply is older than the action's: it must not put the screen back")
        self.assertTrue(seq.accept(seq.issue()))

    def test_a_drawn_action_reply_sets_a_floor_under_everything_issued_before_it(self):
        seq = logic.Sequence()
        action, poll = seq.issue(), seq.issue()
        self.assertTrue(seq.accept(action, floor=True))
        self.assertFalse(seq.accept(poll), "issued before the action's reply was drawn: maybe older than it")
        later = seq.issue()
        self.assertTrue(seq.accept(later))
        a, b = seq.issue(), seq.issue()
        self.assertTrue(seq.accept(b, floor=True))
        self.assertFalse(seq.accept(a, floor=True), "an older reply is still older news")
        p1, p2 = seq.issue(), seq.issue()
        self.assertTrue(seq.accept(p1))
        self.assertTrue(seq.accept(p2), "a poll reply sets no floor")

    def test_replies_in_order_are_all_drawn_and_one_is_never_drawn_twice(self):
        seq = logic.Sequence()
        a, b = seq.issue(), seq.issue()
        self.assertEqual([seq.accept(a), seq.accept(b), seq.accept(b), seq.accept(a)], [True, True, False, False])

    def test_a_failed_poll_says_so_and_keeps_the_screen(self):
        link = logic.Link()
        self.assertEqual(link.text(), "")
        link.failed()
        self.assertEqual(link.text(), logic.LINK_WAITING, "nothing was ever drawn")
        link.shown()
        self.assertEqual(link.text(), logic.LINK_LOST)
        self.assertIn("last state", link.text())
        link.good()
        self.assertEqual(link.text(), "")

    def test_only_a_server_state_counts_as_one(self):
        self.assertTrue(logic.is_snapshot({"battery": {}, "status": "ready"}))
        for bad in (None, {}, {"error": "x", "battery": {}}, [], "battery"):
            with self.subTest(bad=bad):
                self.assertFalse(logic.is_snapshot(bad))

    def test_a_pin_lock_sends_the_owner_to_the_browser(self):
        self.assertTrue(logic.needs_browser({"locked": True, "battery": {}}))
        self.assertFalse(logic.needs_browser({"battery": {}}))
        self.assertFalse(logic.needs_browser(None))


class Rows(unittest.TestCase):
    def test_a_tap_goes_to_the_newest_handler_when_the_words_did_not_change(self):
        rows, hits = logic.RowSet(), []
        self.assertTrue(rows.update([("Empty note", "", lambda: hits.append("old a")), ("Empty note", "", lambda: hits.append("old b"))]))
        rows.click(1)
        self.assertFalse(rows.update([("Empty note", "", lambda: hits.append("new a")), ("Empty note", "", lambda: hits.append("new b"))]),
                         "the same words need no new buttons")
        rows.click(1)
        rows.click(0)
        self.assertEqual(hits, ["old b", "new b", "new a"], "the tap must not reach a row that no longer exists")

    def test_other_words_rebuild_and_a_tap_beyond_the_rows_is_nothing(self):
        rows = logic.RowSet()
        rows.update([("a", "", lambda: None)])
        self.assertTrue(rows.update([("b", "", lambda: None)]))
        rows.click(5)
        rows.click(-1)


class Messages(unittest.TestCase):
    def test_the_lines_show_only_after_the_server_opened_that_person(self):
        c = logic.Conversation()
        c.open("+1 Bob")
        self.assertEqual(c.body(), "Opening the conversation.", "no stale Alice lines under Bob")
        c.snapshot(["Alice: private text"], seq=9)
        self.assertNotIn("Alice", c.body(), "a snapshot before the open answer cannot say whose thread it is")
        self.assertTrue(c.opened("+1 Bob", ["Bob: hi"], issued=10))
        self.assertEqual(c.body(), "Bob: hi")

    def test_a_snapshot_asked_for_before_the_open_answer_cannot_bring_back_the_old_thread(self):
        c = logic.Conversation()
        c.open("bob")
        c.opened("bob", ["Bob: hi"], issued=10)
        c.snapshot(["Alice: private text"], seq=10)     # sent before the server answered the open
        self.assertEqual(c.body(), "Bob: hi")
        c.snapshot(["Bob: hi", "Bob: there?"], seq=11)  # sent after: the server had Bob open by then
        self.assertEqual(c.body(), "Bob: hi\nBob: there?")

    def test_an_open_answer_for_someone_the_owner_left_is_ignored(self):
        c = logic.Conversation()
        c.open("alice")
        c.open("bob")
        self.assertFalse(c.opened("alice", ["Alice: x"], issued=3))
        self.assertEqual(c.body(), "Opening the conversation.")
        c.close()
        self.assertFalse(c.opened("bob", ["Bob: x"], issued=4))
        self.assertEqual(c.body(), "")

    def test_a_refused_open_says_so_instead_of_showing_an_empty_thread(self):
        c = logic.Conversation()
        c.open("bob")
        c.open_failed("bob")
        self.assertEqual(c.body(), "That conversation could not be opened.")
        c.open("carol")
        self.assertEqual(c.body(), "Opening the conversation.")

    def test_a_person_with_no_messages_reads_so_only_once_confirmed(self):
        c = logic.Conversation()
        c.open("bob")
        c.opened("bob", [], issued=1)
        self.assertEqual(c.body(), "No messages with this person yet.")

    def test_a_draft_stays_until_the_server_says_it_went_out(self):
        d = logic.Draft()
        self.assertFalse(d.start("  ", True), "nothing to send")
        self.assertTrue(d.start("hello", True))
        self.assertFalse(d.start("hello", True), "one send at a time: a second tap sends nothing")
        self.assertEqual(d.done(False, "hello"), "hello", "a refusal (battery empty) keeps the words")
        self.assertTrue(d.start("hello", True))
        self.assertEqual(d.done(True, "hello"), "", "confirmed: the box empties")

    def test_a_quick_reply_never_clears_what_was_typed(self):
        d = logic.Draft()
        d.start("OK", False)
        self.assertEqual(d.done(True, "OK"), "OK", "the owner typed OK too: that is still theirs to send")

    def test_the_servers_own_words_are_what_the_owner_reads(self):
        self.assertEqual(logic.refusal({"status": "error: battery empty, charge the phone to send", "notice": "Charge the phone to send"}),
                         "Charge the phone to send")
        self.assertEqual(logic.refusal({"status": "error: weird", "notice": ""}), "That did not go through.")
        self.assertEqual(logic.refusal({"status": "sent"}), "")
        self.assertTrue(logic.sent({"status": "sent"}))
        self.assertFalse(logic.sent({"status": "error: number not valid"}))

    def test_practice_is_named_where_the_modem_is_a_practice_one(self):
        practice, real = {"simulation": {"modem": True}, "in_call": True}, {"simulation": {"modem": False}, "in_call": True}
        self.assertEqual(logic.call_words(practice), "In a practice call")
        self.assertEqual(logic.call_words(real), "In call")
        self.assertEqual(logic.call_words({"in_call": False}), "")
        self.assertEqual(logic.messages_line(practice), logic.PRACTICE_MESSAGES)
        self.assertEqual(logic.messages_line(practice, "Practice message. Nothing was sent."), "Practice message. Nothing was sent.")
        self.assertEqual(logic.messages_line(real), "")


class Clocks(unittest.TestCase):
    def test_a_clock_nobody_set_shows_no_time_and_warns(self):
        self.assertFalse(logic.clock_known({"clock_ok": False}))
        self.assertFalse(logic.clock_known({"battery": {"clock_ok": False}}, {}))
        self.assertTrue(logic.clock_known({"battery": {}}, None, {"clock_ok": True}))
        self.assertEqual(logic.time_text(False, (2026, 11, 13, 10, 5, 0, 4, 317, 0)), "--:--")
        self.assertEqual(logic.time_text(True, (2026, 11, 13, 10, 5, 0, 4, 317, 0)), "10:05")
        self.assertIn("alarms will not ring", logic.CLOCK_UNSET)

    def test_a_ringing_alarm_brings_the_clock_forward_once(self):
        gate = logic.RingGate()
        self.assertFalse(gate.new(None))
        self.assertTrue(gate.new({"id": "a1"}))
        self.assertFalse(gate.new({"id": "a1"}), "the owner left the page; the next poll must not drag them back")
        self.assertFalse(gate.new(None))
        self.assertTrue(gate.new({"id": "a1"}), "it rings again later")
        self.assertTrue(gate.new({"id": "a2"}))


class Readiness(unittest.TestCase):
    def test_ready_is_said_once_and_only_after_all_three(self):
        said = []
        r = logic.Ready(lambda: said.append(1))
        r.mark("warmed")
        r.mark("mapped")
        self.assertEqual(said, [], "no frame has been drawn yet")
        r.mark("painted")
        r.mark("painted")
        self.assertEqual(said, [1])

    def test_a_frame_before_the_window_is_up_does_not_count_as_ready(self):
        said = []
        r = logic.Ready(lambda: said.append(1))
        r.mark("painted")
        r.mark("warmed")
        self.assertEqual(said, [])
        r.mark("mapped")
        self.assertEqual(said, [1])


class Sources(unittest.TestCase):
    def test_every_native_module_parses_and_the_rules_need_no_toolkit(self):
        for path in NATIVE.glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in ("logic.py", "layout.py"):
            self.assertNotIn("gi.repository", (NATIVE / name).read_text(encoding="utf-8"), name)


class Confirm(unittest.TestCase):
    def test_two_taps_on_the_same_thing(self):
        c = logic.Confirm()
        self.assertFalse(c.tap("a"))
        self.assertTrue(c.tap("a"))
        self.assertFalse(c.tap("a"), "a third tap starts again")

    def test_another_thing_or_nothing_disarms(self):
        c = logic.Confirm()
        c.tap("a")
        self.assertFalse(c.tap("b"), "a tap on another row only arms that one")
        self.assertTrue(c.tap("b"))
        c.tap("a")
        c.keep_only("b")
        self.assertIsNone(c.armed)
        c.tap("a")
        c.keep_only("a")
        self.assertEqual(c.armed, "a")
        self.assertFalse(c.tap(None))
        self.assertIsNone(c.armed)


class Pending(unittest.TestCase):
    def test_a_drawn_reply_ends_it(self):
        p = logic.Pending()
        self.assertTrue(p.start("call"))
        self.assertFalse(p.start("call"), "one at a time")
        p.answered(5, 5, 5, True)
        self.assertIsNone(p.what)

    def test_no_answer_waits_for_a_state_asked_after_it(self):
        """R9: a failed or timed-out reply may still have reached the server; Call comes back only with a newer state."""
        p = logic.Pending()
        p.start("call")
        p.answered(5, 3, 6, False)
        self.assertEqual((p.what, p.after), ("call", 6))
        p.seen(6)
        self.assertEqual(p.what, "call")
        p.seen(7)
        self.assertIsNone(p.what)
        p.start("hang_up")
        p.answered(8, 8, 8, False)
        self.assertEqual(p.what, "hang_up", "even when its own number is the one drawn: a failure draws nothing")

    def test_a_reply_that_lost_the_race_waits_for_a_state_asked_after_it(self):
        p = logic.Pending()
        p.start("call")
        p.seen(6)
        self.assertEqual(p.what, "call", "before the reply, any state is older news")
        p.answered(5, 6, 7, True)       # 6 was drawn instead of 5; 7 was issued already
        self.assertEqual(p.what, "call")
        p.seen(7)
        self.assertEqual(p.what, "call", "7 may have been asked before the reply came")
        p.seen(8)
        self.assertIsNone(p.what)


if __name__ == "__main__":
    unittest.main()
