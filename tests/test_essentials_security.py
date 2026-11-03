# SPDX-License-Identifier: GPL-3.0-or-later
"""Security checks for Essentials messaging."""
import unittest
from unittest import mock
import opos.essentials as ess_mod
from opos.essentials import Essentials


class FakeModem:
    """Record modem calls for assertions."""
    def __init__(self) -> None:
        """Init empty records."""
        self.dialed: list[str] = []
        self.hangups: int = 0
        self.sent: list[tuple[str, str]] = []
        self.fail_send: bool = False

    def dial(self, number: str) -> None:
        """Record a dial request."""
        self.dialed.append(number)

    def hang_up(self) -> None:
        """Record a hangup."""
        self.hangups += 1

    def send_sms(self, number: str, text: str) -> None:
        """Record or fail an SMS send."""
        if self.fail_send:
            raise OSError("fail")
        self.sent.append((number, text))


class TestEssentialsSecurity(unittest.TestCase):
    """Security behavior of receive and send paths."""
    def setUp(self) -> None:
        """Build Essentials with a fake modem."""
        self.modem = FakeModem()
        self.e = Essentials(self.modem)

    def test_receive_strips_controls(self) -> None:
        """Stripped text keeps only visible chars."""
        msg = self.e.receive_message("123456", "x\u202ey\x00z")
        self.assertEqual(msg.text, "xyz")

    def test_receive_rejects_oversized_text(self) -> None:
        """Oversized text raises without side effects."""
        for size in (1601, 5000):
            modem = FakeModem()
            ess = Essentials(modem)
            with self.assertRaises(ValueError):
                ess.receive_message("123456", "a" * size)
            self.assertEqual(ess.log, [])
            self.assertEqual(ess.outbox, [])
            self.assertEqual(modem.sent, [])
        modem = FakeModem()
        ess = Essentials(modem)
        msg = ess.receive_message("123456", "a" * 1600)
        self.assertEqual(msg.text, "a" * 1600)

    def test_messaging_validation_contract(self) -> None:
        """Named senders, normalization and rejection contract."""
        modem = FakeModem()
        ess = Essentials(modem)
        msg = ess.receive_message("Bank123", "x․y\x00z".replace("․", "‮"))
        self.assertEqual(msg.number, "Bank123")
        self.assertEqual(msg.text, "xyz")
        self.assertEqual(ess.log, [msg])
        self.assertEqual(modem.sent, [])
        self.assertEqual(ess.outbox, [])
        named64 = "B" + "1" * 63
        modem = FakeModem()
        ess = Essentials(modem)
        msg = ess.receive_message(named64, "x․y\x00z".replace("․", "‮"))
        self.assertEqual(msg.number, named64)
        self.assertEqual(msg.text, "xyz")
        self.assertEqual(ess.log, [msg])
        self.assertEqual(modem.sent, [])
        self.assertEqual(ess.outbox, [])
        modem = FakeModem()
        ess = Essentials(modem)
        msg = ess.receive_message("+1 (234) 567", "hi")
        self.assertEqual(msg.number, "+1234567")
        self.assertEqual(ess.log, [msg])
        self.assertEqual(modem.sent, [])
        self.assertEqual(ess.outbox, [])
        bad_senders = (None, 123, "", "A" * 65, "1" * 65, "Bank!", "B‮nk".replace("‮", "ä"))
        for sender in bad_senders:
            with self.subTest(sender=repr(sender)[:20]):
                modem = FakeModem()
                ess = Essentials(modem)
                with self.assertRaises(ValueError):
                    ess.receive_message(sender, "hi")  # type: ignore[arg-type]
                self.assertEqual(ess.log, [])
                self.assertEqual(ess.outbox, [])
                self.assertEqual(modem.sent, [])
        bad_bodies = (None, 123, "", "‬".replace("‬", "\x00‮"), "x" * 1601)
        for body in bad_bodies:
            with self.subTest(body=repr(body)[:20]):
                modem = FakeModem()
                ess = Essentials(modem)
                with self.assertRaises(ValueError):
                    ess.receive_message("123456", body)  # type: ignore[arg-type]
                self.assertEqual(ess.log, [])
                self.assertEqual(ess.outbox, [])
                self.assertEqual(modem.sent, [])
        bad_numbers = (None, 123, "1" * 65)
        for number in bad_numbers:
            with self.subTest(number=repr(number)[:20]):
                modem = FakeModem()
                ess = Essentials(modem)
                with self.assertRaises(ValueError):
                    ess.send_message(number, "hi")  # type: ignore[arg-type]
                self.assertEqual(ess.log, [])
                self.assertEqual(ess.outbox, [])
                self.assertEqual(modem.sent, [])
        bad_texts = (None, 123, "", "\x00", "x" * 161)
        for body in bad_texts:
            with self.subTest(body=repr(body)[:20]):
                modem = FakeModem()
                ess = Essentials(modem)
                with self.assertRaises(ValueError):
                    ess.send_message("123456", body)  # type: ignore[arg-type]
                self.assertEqual(ess.log, [])
                self.assertEqual(ess.outbox, [])
                self.assertEqual(modem.sent, [])
        modem = FakeModem()
        ess = Essentials(modem)
        msg = ess.send_message("123456", "x" * 160)
        self.assertEqual(msg.text, "x" * 160)
        self.assertEqual(modem.sent, [("123456", "x" * 160)])

    def test_receive_empty_after_clean(self) -> None:
        """Only control chars raises ValueError."""
        with self.assertRaises(ValueError):
            self.e.receive_message("123456", "\x00\x07")

    def test_receive_non_str(self) -> None:
        """Non-str text raises ValueError."""
        with self.assertRaises(ValueError):
            self.e.receive_message("123456", 123)  # type: ignore[arg-type]

    def test_log_bounded_newest_last(self) -> None:
        """600 receives keep 500 with newest last."""
        last = None
        for i in range(600):
            last = self.e.receive_message(str(100000 + i), "m")
        self.assertEqual(len(self.e.log), 500)
        self.assertIs(self.e.log[-1], last)

    def test_unsent_survives_trim(self) -> None:
        """Unsent outgoing survives later trimming."""
        self.modem.fail_send = True
        kept = self.e.send_message("123456", "keep")
        self.modem.fail_send = False
        for i in range(600):
            self.e.receive_message(str(200000 + i), "m")
        self.assertIn(kept, self.e.log)
        self.assertEqual(len(self.e.log), 500)

    def test_send_strips_bidi(self) -> None:
        """Send delivers cleaned text to modem."""
        msg = self.e.send_message("123456", "hi\u202e")
        self.assertEqual(msg.text, "hi")
        self.assertEqual(self.modem.sent, [("123456", "hi")])

    def test_send_too_long(self) -> None:
        """161 chars raises ValueError."""
        with self.assertRaises(ValueError):
            self.e.send_message("123456", "x" * 161)

    def test_send_null_byte(self) -> None:
        """Null byte text raises ValueError."""
        with self.assertRaises(ValueError):
            self.e.send_message("123456", "\x00")

    def test_non_str_number(self) -> None:
        """Int number raises ValueError."""
        with self.assertRaises(ValueError):
            self.e.receive_message(123, "hi")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.e.send_message(123, "hi")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.e.call(123)  # type: ignore[arg-type]

    def test_send_bounds_log(self) -> None:
        """600 sends keep log at 500."""
        for i in range(600):
            self.e.send_message(str(100000 + i), "m")
        self.assertEqual(len(self.e.log), 500)

    def test_long_number_rejected(self) -> None:
        """Overlong number raises ValueError."""
        with self.assertRaises(ValueError):
            self.e.receive_message("1" * 65, "hi")
        with self.assertRaises(ValueError):
            self.e.send_message("1" * 65, "hi")

    def test_p010_pending_history_does_not_block_messaging(self) -> None:
        """Pending-only history evicts oldest entry without blocking."""
        base = 299999 + 1
        first_num = str(base)
        second_num = str(base + 1)
        third_num = str(base + 2)
        fourth_num = str(base + 3)
        with mock.patch.object(ess_mod, "MAX_LOG", 2):
            with self.subTest("receive"):
                modem = FakeModem()
                ess = Essentials(modem)
                modem.fail_send = True
                first = ess.send_message(first_num, "alpha")
                second = ess.send_message(second_num, "beta")
                new = ess.receive_message(third_num, "gamma")
                self.assertEqual(len(ess.log), 2)
                self.assertIs(ess.log[0], second)
                self.assertIs(ess.log[1], new)
                self.assertEqual([id(m) for m in ess.outbox], [id(first), id(second)])
                self.assertEqual(modem.sent, [])
                modem.fail_send = False
                self.assertEqual(ess.retry_outbox(), 2)
                self.assertEqual(modem.sent, [(first_num, "alpha"), (second_num, "beta")])
                self.assertTrue(first.sent)
                self.assertTrue(second.sent)
                self.assertEqual(ess.outbox, [])
            with self.subTest("send-recovered"):
                modem = FakeModem()
                ess = Essentials(modem)
                modem.fail_send = True
                first = ess.send_message(first_num, "alpha")
                second = ess.send_message(second_num, "beta")
                modem.fail_send = False
                new = ess.send_message(fourth_num, "delta")
                self.assertTrue(new.sent)
                self.assertEqual(len(ess.log), 2)
                self.assertIs(ess.log[0], second)
                self.assertIs(ess.log[1], new)
                self.assertEqual([id(m) for m in ess.outbox], [id(first), id(second)])
                self.assertEqual(modem.sent, [(fourth_num, "delta")])
                self.assertEqual(ess.retry_outbox(), 2)
                self.assertEqual(
                    modem.sent,
                    [(fourth_num, "delta"), (first_num, "alpha"), (second_num, "beta")],
                )
                self.assertTrue(first.sent)
                self.assertTrue(second.sent)
                self.assertEqual(ess.outbox, [])
            with self.subTest("send-failed"):
                modem = FakeModem()
                ess = Essentials(modem)
                modem.fail_send = True
                first = ess.send_message(first_num, "alpha")
                second = ess.send_message(second_num, "beta")
                new = ess.send_message(fourth_num, "delta")
                self.assertFalse(new.sent)
                self.assertEqual(len(ess.log), 2)
                self.assertIs(ess.log[0], second)
                self.assertIs(ess.log[1], new)
                self.assertEqual(
                    [id(m) for m in ess.outbox], [id(first), id(second), id(new)]
                )
                modem.fail_send = False
                self.assertEqual(ess.retry_outbox(), 3)
                self.assertEqual(
                    modem.sent,
                    [(first_num, "alpha"), (second_num, "beta"), (fourth_num, "delta")],
                )
                self.assertTrue(first.sent)
                self.assertTrue(second.sent)
                self.assertTrue(new.sent)
                self.assertEqual(ess.outbox, [])

    def test_history_evicts_oldest_nonpending(self) -> None:
        """Send and receive evict only the oldest nonpending entry."""
        base = 400000
        with mock.patch.object(ess_mod, "MAX_LOG", 2):
            with self.subTest("send"):
                self.setUp()
                self.modem.fail_send = True
                pending = self.e.send_message(str(base), "keep")
                old = self.e.receive_message(str(base + 1), "old")
                self.assertEqual(self.e.log, [pending, old])
                self.modem.fail_send = False
                sent_before = list(self.modem.sent)
                new = self.e.send_message(str(base + 2), "new")
                self.assertEqual(self.e.log, [pending, new])
                self.assertIs(self.e.log[0], pending)
                self.assertIs(self.e.log[1], new)
                self.assertNotIn(old, self.e.log)
                self.assertEqual(self.e.outbox, [pending])
                self.assertEqual(
                    self.modem.sent, sent_before + [(str(base + 2), "new")]
                )
            with self.subTest("receive"):
                self.setUp()
                self.modem.fail_send = True
                pending = self.e.send_message(str(base), "keep")
                old = self.e.receive_message(str(base + 1), "old")
                self.assertEqual(self.e.log, [pending, old])
                sent_before = list(self.modem.sent)
                new = self.e.receive_message(str(base + 2), "new")
                self.assertEqual(self.e.log, [pending, new])
                self.assertIs(self.e.log[0], pending)
                self.assertIs(self.e.log[1], new)
                self.assertNotIn(old, self.e.log)
                self.assertEqual(self.e.outbox, [pending])
                self.assertEqual(self.modem.sent, sent_before)

    def test_bad_number_receive_and_send(self) -> None:
        """Shell metachars raise in both paths."""
        with self.assertRaises(ValueError):
            self.e.receive_message("+1; rm -rf /", "hi")
        with self.assertRaises(ValueError):
            self.e.send_message("+1; rm -rf /", "hi")

    def test_call_rejects_hostile(self) -> None:
        """Call rejects metachars and overlong input."""
        with self.assertRaises(ValueError):
            self.e.call("+1; rm -rf /")
        with self.assertRaises(ValueError):
            self.e.call("1" * 65)
        self.assertEqual(self.modem.dialed, [])

    def test_call_double_blocked(self) -> None:
        """Second call raises until end_call runs."""
        self.e.call("123456")
        with self.assertRaises(RuntimeError):
            self.e.call("123456")
        self.e.end_call()
        self.e.call("123456")
        self.assertEqual(self.modem.dialed, ["123456", "123456"])
        self.assertEqual(self.modem.hangups, 1)

    def test_send_failure_queues_and_retries(self) -> None:
        """Failed send queues then retry marks sent."""
        self.modem.fail_send = True
        msg = self.e.send_message("123456", "hi")
        self.assertFalse(msg.sent)
        self.assertEqual(len(self.e.outbox), 1)
        self.modem.fail_send = False
        self.assertEqual(self.e.retry_outbox(), 1)
        self.assertTrue(msg.sent)
        self.assertEqual(self.e.outbox, [])
        self.assertEqual(self.modem.sent, [("123456", "hi")])

    def test_retry_keeps_failed(self) -> None:
        """Retry keeps messages when modem still down."""
        self.modem.fail_send = True
        self.e.send_message("123456", "one")
        self.e.send_message("123457", "two")
        self.assertEqual(self.e.retry_outbox(), 0)
        self.assertEqual(len(self.e.outbox), 2)

    def test_f016_message_operations_do_not_write_files(self) -> None:
        """Send, retry, and receive write no files."""
        import builtins
        import io
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = os.path.join(tmp, "sentinel.bin")
            link = os.path.join(tmp, "messages.json.tmp")
            with open(sentinel, "wb") as handle:
                handle.write(b"F016 sentinel")
            os.chmod(sentinel, 0o640)
            os.symlink(sentinel, link)
            calls: list[str] = []

            def guard(name: str):  # type: ignore[no-untyped-def]
                def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
                    calls.append(name)
                    raise AssertionError(name)
                return fail

            with mock.patch.object(builtins, "open", side_effect=guard("builtins.open")), \
                    mock.patch.object(io, "open", side_effect=guard("io.open")), \
                    mock.patch.object(os, "open", side_effect=guard("os.open")), \
                    mock.patch.object(os, "chmod", side_effect=guard("os.chmod")), \
                    mock.patch.object(os, "replace", side_effect=guard("os.replace")):
                self.modem.fail_send = True
                queued = self.e.send_message("123456", "queued")
                self.modem.fail_send = False
                retried = self.e.retry_outbox()
                incoming = self.e.receive_message("123457", "incoming")
                self.assertEqual(calls, [])
            with open(sentinel, "rb") as handle:
                self.assertEqual(handle.read(), b"F016 sentinel")
            self.assertEqual(os.stat(sentinel).st_mode & 0o777, 0o640)
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.readlink(link), sentinel)
            self.assertEqual(retried, 1)
            self.assertTrue(queued.sent)
            self.assertEqual(self.e.outbox, [])
            self.assertEqual(self.e.log, [queued, incoming])
            self.assertEqual(self.modem.sent, [("123456", "queued")])


if __name__ == "__main__":
    unittest.main()
