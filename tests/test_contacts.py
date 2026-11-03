# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.contacts import Contact, Book, threads, thread, DEMO_CONTACTS
from opos.essentials import Message

class TestContacts(unittest.TestCase):
    def test_contact_creation(self):
        c = Contact('A', '123')
        self.assertEqual(c.name, 'A')
        self.assertEqual(c.number, '123')

    def test_book_add_strips_name(self):
        b = Book()
        c = b.add('  Alice ', '1111111')
        self.assertEqual(c.name, 'Alice')

    def test_book_add_empty_name_error(self):
        b = Book()
        with self.assertRaises(ValueError):
            b.add('', '1111111')

    def test_book_add_bad_number_error(self):
        b = Book()
        with self.assertRaises(ValueError):
            b.add('Bob', 'abc')

    def test_book_readd_replaces_name(self):
        b = Book()
        b.add('A', '1111111')
        b.add('B', '1111111')
        self.assertEqual(b.name_for('1111111'), 'B')

    def test_book_name_for_contact(self):
        b = Book()
        b.add('Mom', '+15550100')
        self.assertEqual(b.name_for('+15550100'), 'Mom')

    def test_book_name_for_normalized(self):
        b = Book()
        self.assertEqual(b.name_for('+1999888777'), '+1999888777')

    def test_book_name_for_raw(self):
        b = Book()
        self.assertEqual(b.name_for('abc!@#'), 'abc!@#')

    def test_book_all(self):
        b = Book(contacts=(Contact('X','1'), Contact('Y','2')))
        self.assertEqual(len(b.all()), 2)

    def test_demo_contacts_contains_mom(self):
        found = any(c.name == 'Mom' and c.number == '+15550100' for c in DEMO_CONTACTS)
        self.assertTrue(found)

    def test_threads_newest_first(self):
        b = Book()
        msgs = [Message('111','a',False), Message('222','b',False), Message('111','c',False)]
        t = threads(b, msgs)
        self.assertEqual(t[0]['number'], '111')
        self.assertEqual(t[1]['number'], '222')

    def test_threads_fields(self):
        b = Book()
        msgs = [Message('111','a',True,sent=False), Message('111','b',True,sent=True)]
        t = threads(b, msgs)[0]
        self.assertEqual(t['count'], 2)
        self.assertEqual(t['unsent'], 1)
        self.assertEqual(t['last'], 'b')

    def test_thread_formatting_incoming(self):
        b = Book()
        b.add('Mom', '+15550100')
        msgs = [Message('+15550100','hi',False)]
        self.assertEqual(thread(b, msgs, '+15550100'), ['Mom: hi'])

    def test_thread_formatting_outgoing_sent(self):
        b = Book()
        msgs = [Message('111','ok',True,sent=True)]
        self.assertEqual(thread(b, msgs, '111'), ['Me: ok'])

    def test_thread_formatting_outgoing_unsent(self):
        b = Book()
        msgs = [Message('111','later',True,sent=False)]
        self.assertEqual(thread(b, msgs, '111'), ['Me: later (not sent)'])

    def test_thread_name_fallback(self):
        b = Book()
        msgs = [Message('999','hey',False)]
        self.assertEqual(thread(b, msgs, '999'), ['999: hey'])

if __name__ == '__main__':
    unittest.main()
