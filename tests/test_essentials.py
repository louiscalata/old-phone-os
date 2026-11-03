# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.essentials import Essentials, Message, normalize_number, normalize_sender


class FakeModem:
    def __init__(self, fail_send=False):
        self.calls = []
        self.hangups = 0
        self.sent = []
        self.fail_send = fail_send

    def dial(self, number):
        self.calls.append(number)

    def hang_up(self):
        self.hangups += 1

    def send_sms(self, number, text):
        if self.fail_send:
            raise OSError("fail")
        self.sent.append((number, text))
        return True


class TestNormalizeNumber(unittest.TestCase):
    def test_normalizes_basic(self):
        self.assertEqual(normalize_number("123 456"), "123456")

    def test_normalizes_plus_and_symbols(self):
        self.assertEqual(normalize_number("+1 (234) 567-890."), "+1234567890")

    def test_too_short(self):
        with self.assertRaises(ValueError):
            normalize_number("12")

    def test_invalid_chars(self):
        with self.assertRaises(ValueError):
            normalize_number("12a34")


class TestEssentials(unittest.TestCase):
    def setUp(self):
        self.modem = FakeModem()
        self.e = Essentials(self.modem)

    def test_call_and_end(self):
        num = self.e.call(" +1 (800) 123-4567 ")
        self.assertEqual(num, "+18001234567")
        self.assertTrue(self.e.in_call)
        self.assertEqual(self.modem.calls, ["+18001234567"])
        self.e.end_call()
        self.assertFalse(self.e.in_call)
        self.assertEqual(self.modem.hangups, 1)

    def test_call_runtime_error(self):
        self.e.call("1234567")
        with self.assertRaises(RuntimeError):
            self.e.call("7654321")

    def test_f054_reconcile_remote_call_state(self):
        first = "123" + "4567"
        second = "765" + "4321"

        class StatefulFakeModem(FakeModem):
            def __init__(self):
                super().__init__()
                self.active = False

            def dial(self, number):
                super().dial(number)
                self.active = True

            def is_call_active(self):
                return self.active

        class IndeterminateModem(StatefulFakeModem):
            def is_call_active(self):
                return None

        class FailingModem(StatefulFakeModem):
            def is_call_active(self):
                raise OSError("query failed")

        for mode in ("remote-hangup", "restart"):
            with self.subTest(mode=mode):
                modem = StatefulFakeModem()
                e = Essentials(modem)
                e.call(first)
                modem.active = False
                num = e.call(second)
                self.assertEqual(num, second)
                self.assertEqual(modem.calls, [first, second])
                self.assertEqual(modem.hangups, 0)
                self.assertTrue(e.in_call)

        with self.subTest(mode="still-active"):
            modem = StatefulFakeModem()
            e = Essentials(modem)
            e.call(first)
            with self.assertRaises(RuntimeError):
                e.call(second)
            self.assertTrue(e.in_call)
            self.assertEqual(modem.calls, [first])

        with self.subTest(mode="indeterminate"):
            modem = IndeterminateModem()
            e = Essentials(modem)
            e.call(first)
            modem.active = False
            with self.assertRaises(RuntimeError):
                e.call(second)
            self.assertTrue(e.in_call)
            self.assertEqual(modem.calls, [first])

        with self.subTest(mode="query-exception"):
            modem = FailingModem()
            e = Essentials(modem)
            e.call(first)
            modem.active = False
            with self.assertRaises(RuntimeError):
                e.call(second)
            self.assertTrue(e.in_call)
            self.assertEqual(modem.calls, [first])

    def test_end_call_no_hangup_if_idle(self):
        self.e.end_call()
        self.assertEqual(self.modem.hangups, 0)

    def test_send_message_success(self):
        msg = self.e.send_message("123 456", "hi")
        self.assertIsInstance(msg, Message)
        self.assertEqual(msg.number, "123456")
        self.assertTrue(msg.outgoing)
        self.assertTrue(msg.sent)
        self.assertIn(msg, self.e.log)
        self.assertEqual(self.modem.sent, [("123456", "hi")])

    def test_send_message_validation(self):
        with self.assertRaises(ValueError):
            self.e.send_message("123456", "")
        with self.assertRaises(ValueError):
            self.e.send_message("123456", "x" * 161)

    def test_send_message_oserror_keeps_outbox(self):
        fail_modem = FakeModem(fail_send=True)
        e = Essentials(fail_modem)
        msg = e.send_message("123456", "hi")
        self.assertFalse(msg.sent)
        self.assertEqual(len(e.outbox), 1)
        self.assertIn(msg, e.log)

    def test_retry_outbox(self):
        fail_modem = FakeModem(fail_send=True)
        e = Essentials(fail_modem)
        e.send_message("111", "a")
        e.send_message("222", "b")
        self.assertEqual(len(e.outbox), 2)
        fail_modem.fail_send = False
        count = e.retry_outbox()
        self.assertEqual(count, 2)
        self.assertEqual(len(e.outbox), 0)
        self.assertEqual(len(fail_modem.sent), 2)

    def test_receive_message(self):
        msg = self.e.receive_message("999888", "incoming")
        self.assertIsInstance(msg, Message)
        self.assertEqual(msg.number, "999888")
        self.assertFalse(msg.outgoing)
        self.assertIn(msg, self.e.log)

    def test_f2037_normalize_sender_contract(self):
        accepted = [
            ("AMAZON", "AMAZON"),
            ("ABCDEFGHIJK", "ABCDEFGHIJK"),
            ("ABCDEFGHIJKL", "ABCDEFGHIJKL"),
            ("Bank123", "Bank123"),
            ("B" + "1" * 63, "B" + "1" * 63),
            ("A B-C_D.E1", "A B-C_D.E1"),
            (" A ", " A "),
            ("A" + "-" * 10, "A" + "-" * 10),
            ("+1 (234) 567-890.", "+1234567890"),
            ("+1 (234) 567", "+1234567"),
        ]
        for sender, expected in accepted:
            with self.subTest(sender=sender):
                modem = FakeModem()
                e = Essentials(modem)
                self.assertEqual(normalize_sender(sender), expected)
                msg = e.receive_message(sender, "incoming")
                self.assertEqual(msg.number, expected)
                self.assertEqual(msg.text, "incoming")
                self.assertFalse(msg.outgoing)
                self.assertEqual(e.log, [msg])
                self.assertEqual(modem.sent, [])
                self.assertEqual(e.outbox, [])
        rejected = [
            None,
            123,
            "",
            "A" * 65,
            "1" * 65,
            "AMAZON!",
            "Bank!",
            "ÉCOLE",
            "Bänk",
            "---",
            "12",
            "A" + "-" * 11,
        ]
        for sender in rejected:
            with self.subTest(sender=sender):
                modem = FakeModem()
                e = Essentials(modem)
                with self.assertRaises(ValueError):
                    normalize_sender(sender)
                with self.assertRaises(ValueError):
                    e.receive_message(sender, "incoming")
                self.assertEqual(e.log, [])
                self.assertEqual(modem.sent, [])
                self.assertEqual(e.outbox, [])


if __name__ == "__main__":
    unittest.main()
