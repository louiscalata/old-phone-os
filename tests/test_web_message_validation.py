# SPDX-License-Identifier: GPL-3.0-or-later
"""Message validation names recipient errors without exposing internal exceptions."""
import copy
import unittest
from unittest import mock

from opos.web import Phone, notice_for


def message_state(phone):
    return copy.deepcopy((phone.essentials.log, phone.essentials.outbox, phone.modem.events,
                          phone.essentials.in_call, phone._in_call))


class TestMessageValidation(unittest.TestCase):
    def seeded_phone(self):
        phone = Phone()
        phone.send("+15550100", "Kept outgoing")
        phone.receive("Clinic", "Kept incoming")
        phone.call("+15550142")
        return phone

    def test_invalid_recipient_has_number_error_without_mutation(self):
        for action, recipients in (
            ("send", ("", "12", "abc", "++15550100")),
            ("receive", ("", "12", "<invalid>", "bad\nname")),
        ):
            for recipient in recipients:
                with self.subTest(action=action, recipient=recipient):
                    phone = self.seeded_phone()
                    before = message_state(phone)
                    getattr(phone, action)(recipient, "Valid message")
                    self.assertEqual(phone.status, "error: number not valid")
                    self.assertEqual(notice_for(phone.status, phone._in_call), "Number not valid")
                    self.assertEqual(message_state(phone), before)

    def test_empty_or_long_message_keeps_message_error_without_mutation(self):
        for action, limit in (("send", 160), ("receive", 1600)):
            for text in ("", "x" * (limit + 1)):
                with self.subTest(action=action, length=len(text)):
                    phone = self.seeded_phone()
                    before = message_state(phone)
                    getattr(phone, action)("+15550100", text)
                    self.assertEqual(phone.status, "error: message empty or too long")
                    self.assertEqual(message_state(phone), before)

    def test_normalized_recipient_and_sender_alias_still_work(self):
        phone = Phone()
        phone.send("+1 (555) 0100", "Outgoing")
        phone.receive("Clinic", "Incoming")
        self.assertEqual(phone.modem.events, [("sms", "+15550100", "Outgoing")])
        self.assertEqual([(m.number, m.text, m.outgoing) for m in phone.essentials.log],
                         [("+15550100", "Outgoing", True), ("Clinic", "Incoming", False)])
        self.assertEqual(phone.status, "message received")

    def test_arbitrary_modem_exception_text_is_not_exposed(self):
        private_detail = "internal transport path and credential marker"
        for error in (ValueError(private_detail), RuntimeError(private_detail)):
            with self.subTest(error=type(error).__name__):
                phone = self.seeded_phone()
                before = message_state(phone)
                with mock.patch.object(phone.modem, "send_sms", side_effect=error):
                    phone.send("+15550100", "Valid message")
                self.assertEqual(phone.status, "error: message empty or too long")
                self.assertNotIn(private_detail, phone.status)
                self.assertNotIn(private_detail, notice_for(phone.status, phone._in_call))
                self.assertEqual(message_state(phone), before)


if __name__ == "__main__":
    unittest.main()
