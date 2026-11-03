# SPDX-License-Identifier: GPL-3.0-or-later
"""Modem foundation tests."""
import ast
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch

from opos.essentials import Essentials
from opos.mm_modem import CALL
from opos.mm_modem import FakeModem
from opos.mm_modem import MMModem
from opos.mm_modem import MESSAGING
from opos.mm_modem import MM
from opos.mm_modem import MM_PATH
from opos.mm_modem import OM
from opos.mm_modem import SMS
from opos.mm_modem import VOICE
from opos.mm_modem import _parse_array
from opos.mm_modem import _parse_dict
from opos.mm_modem import _parse_string
from opos.mm_modem import _parse_tuple
from opos.mm_modem import _parse_variant
from opos.mm_modem import _skip_ws
from opos.mm_modem import find_modem
from opos.mm_modem import parse_gvariant
from opos.mm_modem import to_gvariant


class ModemFoundationTests(unittest.TestCase):
    def test_f2029_constant_values(self):
        self.assertEqual(MM, "org.freedesktop.ModemManager1")
        self.assertEqual(MM_PATH, "/org/freedesktop/ModemManager1")
        self.assertEqual(OM, "org.freedesktop.DBus.ObjectManager")
        self.assertEqual(VOICE, "org.freedesktop.ModemManager1.Modem.Voice")
        self.assertEqual(MESSAGING, "org.freedesktop.ModemManager1.Modem.Messaging")
        self.assertEqual(CALL, "org.freedesktop.ModemManager1.Call")
        self.assertEqual(SMS, "org.freedesktop.ModemManager1.Sms")

    def test_f2029_skip_ws_boundaries(self):
        cases = [
            (" \t\nx", 0, 3),
            ("a \t\nb", 1, 4),
            ("x \t\n", 0, 0),
            (" \t\n", 0, 3),
            ("", 0, 0),
            ("x", 1, 1),
            ("x", 2, 2),
        ]
        for s, i, expected in cases:
            with self.subTest(s=s, i=i):
                self.assertEqual(_skip_ws(s, i), expected)


class ParseStringTests(unittest.TestCase):
    def test_f2030_double_quoted_string(self):
        s = '"hello",tail'
        text, end = _parse_string(s, 0)
        self.assertEqual(text, "hello")
        self.assertEqual(end, 7)
        self.assertEqual(s[end:], ",tail")

    def test_f2030_escaped_quote_string(self):
        s = "'it\\'s',tail"
        text, end = _parse_string(s, 0)
        self.assertEqual(text, "it's")
        self.assertEqual(end, 7)
        self.assertEqual(s[end:], ",tail")

    def test_f2030_unescaped_quote_terminates(self):
        s = "'it's',tail"
        text, end = _parse_string(s, 0)
        self.assertEqual(text, "it")
        self.assertEqual(end, 4)
        self.assertEqual(s[end:], "s',tail")

    def test_p074_surrogate_escapes_use_invalid_escape_fallback(self):
        bs = chr(92)
        cases = [
            (bs + "u" + "D8" + "00", "u" + "D8" + "00"),
            (bs + "u" + "DF" + "FF", "u" + "DF" + "FF"),
            (bs + "U" + "0000" + "D8" + "00", "U" + "0000" + "D8" + "00"),
            (bs + "U" + "0000" + "DF" + "FF", "U" + "0000" + "DF" + "FF"),
            (bs + "u" + "D8" + "3D" + bs + "u" + "DE" + "00", "u" + "D8" + "3D" + "u" + "DE" + "00"),
            (bs + "u" + "D7" + "FF", chr(int("D7" + "FF", 16))),
            (bs + "u" + "E0" + "00", chr(int("E0" + "00", 16))),
            (bs + "U" + "0001" + "F6" + "00", chr(int("0001" + "F6" + "00", 16))),
        ]
        for esc, expected in cases:
            with self.subTest(esc=esc):
                s = '"' + esc + '"' + ",tail"
                text, end = _parse_string(s, 0)
                self.assertEqual(text, expected)
                self.assertEqual(s[end:], ",tail")
                text.encode("utf-8", "strict")


class ToGvariantTests(unittest.TestCase):
    def test_str_plain(self):
        self.assertEqual(to_gvariant("hello"), "'hello'")

    def test_str_with_quote(self):
        self.assertEqual(to_gvariant("it's"), "'it\\'s'")

    def test_str_with_backslash(self):
        self.assertEqual(to_gvariant("a\\b"), "'a\\\\b'")

    def test_str_with_quote_and_backslash(self):
        self.assertEqual(to_gvariant("a\\'b"), "'a\\\\\\'b'")

    def test_dict(self):
        value = {}
        value["number"] = "5551234"
        value["text"] = "hi"
        self.assertEqual(to_gvariant(value), "{'number': <'5551234'>, 'text': <'hi'>}")

    def test_dict_single_entry(self):
        self.assertEqual(to_gvariant({"number": "5551"}), "{'number': <'5551'>}")

    def test_p077_empty_dictionaries_have_explicit_type(self):
        cases = [
            ({}, "@a{sv} {}"),
            ({"outer": {}}, "{'outer': <@a{sv} {}>}"),
            ({"outer": {"inner": {}}}, "{'outer': <{'inner': <@a{sv} {}>}>}"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(to_gvariant(value), expected)

    def test_bool(self):
        self.assertEqual(to_gvariant(True), "true")
        self.assertEqual(to_gvariant(False), "false")

    def test_int(self):
        self.assertEqual(to_gvariant(3), "3")
        self.assertEqual(to_gvariant(0), "0")
        self.assertEqual(to_gvariant(-1), "-1")

    def test_p076_integer_width_boundaries(self):
        cases = [
            (-2 ** 31, str(-2 ** 31)),
            (2 ** 31 - 1, str(2 ** 31 - 1)),
            (-2 ** 31 - 1, "int64 " + str(-2 ** 31 - 1)),
            (2 ** 31, "int64 " + str(2 ** 31)),
            (-2 ** 63, "int64 " + str(-2 ** 63)),
            (2 ** 63 - 1, "int64 " + str(2 ** 63 - 1)),
            (2 ** 63, "uint64 " + str(2 ** 63)),
            (2 ** 64 - 1, "uint64 " + str(2 ** 64 - 1)),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(to_gvariant(value), expected)
                self.assertEqual(to_gvariant({"n": value}), "{'n': <" + expected + ">}")
        bad = [-2 ** 63 - 1, 2 ** 64]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    to_gvariant(value)
                with self.assertRaises(ValueError):
                    to_gvariant({"n": value})

    def test_p078_rejects_unsupported_types(self):
        nested_none = {}
        nested_none["value"] = None
        nested_list = {}
        nested_list["value"] = [True]
        none_key = {}
        none_key[None] = "value"
        cases = [
            (None, "NoneType"),
            ([True], "list"),
            (1.5, "float"),
            ((True,), "tuple"),
            (nested_none, "NoneType"),
            (nested_list, "list"),
            (none_key, "NoneType"),
        ]
        for value, name in cases:
            with self.subTest(value=value, name=name):
                with self.assertRaises(TypeError) as ctx:
                    to_gvariant(value)
                self.assertEqual(str(ctx.exception), "Unsupported GVariant type: " + name)


class FindModemTests(unittest.TestCase):
    def test_picks_first_sorted_modem_path(self):
        bus = Mock()
        modem = MM + ".Modem"
        second = MM_PATH + "/Modem/2"
        first = MM_PATH + "/Modem/1"
        bus.call.return_value = {second: {modem: {}}, first: {modem: {}}}
        self.assertEqual(find_modem(bus), first)
        bus.call.assert_called_once_with(MM, MM_PATH, OM, "GetManagedObjects")

    def test_picks_modem_when_other_objects_sort_first(self):
        bus = Mock()
        modem_path = MM_PATH + "/Modem/0"
        bus.call.return_value = {"/aaa": {MESSAGING: {}}, modem_path: {MM + ".Modem": {}}}
        self.assertEqual(find_modem(bus), modem_path)

    def test_raises_when_no_modem(self):
        bus = Mock()
        bus.call.return_value = {MM_PATH + "/Sim/0": {MM + ".Sim": {}}}
        with self.assertRaises(OSError) as ctx:
            find_modem(bus)
        self.assertEqual(str(ctx.exception), "no modem")

    def test_raises_when_no_objects(self):
        bus = Mock()
        bus.call.return_value = {}
        with self.assertRaises(OSError) as ctx:
            find_modem(bus)
        self.assertEqual(str(ctx.exception), "no modem")

    def test_propagates_bus_error(self):
        bus = Mock()
        err = RuntimeError("boom")
        bus.call.side_effect = err
        with self.assertRaises(RuntimeError) as ctx:
            find_modem(bus)
        self.assertIs(ctx.exception, err)


class MMModemCoreTests(unittest.TestCase):
    def test_explicit_modem_path_skips_find(self):
        bus = Mock()
        path = MM_PATH + "/Modem/0"
        with patch("opos.mm_modem.find_modem", side_effect=RuntimeError("boom")) as finder:
            modem = MMModem(bus, modem_path=path)
            first = modem._modem()
            second = modem._modem()
        self.assertEqual(first, path)
        self.assertEqual(second, path)
        finder.assert_not_called()
        bus.call.assert_not_called()
        bus.get.assert_not_called()

    def test_find_failure_wrapped_as_oserror(self):
        bus = Mock()
        err = RuntimeError("boom")
        with patch("opos.mm_modem.find_modem", side_effect=err) as finder:
            modem = MMModem(bus)
            finder.assert_not_called()
            with self.assertRaises(OSError) as ctx:
                modem._modem()
        self.assertEqual(str(ctx.exception), "boom")
        self.assertIs(ctx.exception.__cause__, err)
        finder.assert_called_once_with(bus)
        self.assertIs(finder.call_args[0][0], bus)
        self.assertIsNone(modem._modem_path)

    def test_discovered_modem_path_is_cached(self):
        bus = Mock()
        path = MM_PATH + "/Modem/0"
        with patch("opos.mm_modem.find_modem", return_value=path) as finder:
            modem = MMModem(bus)
            first = modem._modem()
            second = modem._modem()
        self.assertEqual(first, path)
        self.assertEqual(second, path)
        self.assertEqual(modem._modem_path, path)
        finder.assert_called_once_with(bus)
        self.assertIs(finder.call_args[0][0], bus)

    def test_p089_bus_helpers_forward_and_recover(self):
        cases = [
            ("call", True),
            ("call", False),
            ("get", True),
            ("get", False),
        ]
        for helper, discovered in cases:
            with self.subTest(helper=helper, discovered=discovered):
                bus = Mock()
                old_path = MM_PATH + "/Modem/" + "0"
                new_path = MM_PATH + "/Modem/" + "1"
                first = object()
                second = object()
                err = OSError("bus failed")
                with patch("opos.mm_modem.find_modem", side_effect=[old_path, new_path]) as finder:
                    if discovered:
                        modem = MMModem(bus)
                    else:
                        modem = MMModem(bus, modem_path=old_path)
                    if helper == "call":
                        bus.call.side_effect = [first, err, second]
                        self.assertIs(modem._bus_call(VOICE, "Probe", "argument", 7), first)
                        with self.assertRaises(OSError) as ctx:
                            modem._bus_call(VOICE, "Probe", "argument", 7)
                        self.assertIs(ctx.exception, err)
                        if discovered:
                            self.assertIsNone(modem._modem_path)
                        else:
                            self.assertEqual(modem._modem_path, old_path)
                        self.assertIs(modem._bus_call(VOICE, "Probe", "argument", 7), second)
                        expected = new_path if discovered else old_path
                        self.assertEqual(
                            bus.call.call_args_list,
                            [
                                call(MM, old_path, VOICE, "Probe", "argument", 7),
                                call(MM, old_path, VOICE, "Probe", "argument", 7),
                                call(MM, expected, VOICE, "Probe", "argument", 7),
                            ],
                        )
                        self.assertEqual(bus.call.call_count, 3)
                        bus.get.assert_not_called()
                    else:
                        bus.get.side_effect = [first, err, second]
                        self.assertIs(modem._bus_get(VOICE, "Calls"), first)
                        with self.assertRaises(OSError) as ctx:
                            modem._bus_get(VOICE, "Calls")
                        self.assertIs(ctx.exception, err)
                        if discovered:
                            self.assertIsNone(modem._modem_path)
                        else:
                            self.assertEqual(modem._modem_path, old_path)
                        self.assertIs(modem._bus_get(VOICE, "Calls"), second)
                        expected = new_path if discovered else old_path
                        self.assertEqual(
                            bus.get.call_args_list,
                            [
                                call(MM, old_path, VOICE, "Calls"),
                                call(MM, old_path, VOICE, "Calls"),
                                call(MM, expected, VOICE, "Calls"),
                            ],
                        )
                        self.assertEqual(bus.get.call_count, 3)
                        bus.call.assert_not_called()
                    if discovered:
                        self.assertEqual(modem._modem_path, new_path)
                        self.assertEqual(finder.call_args_list, [call(bus), call(bus)])
                        self.assertIs(finder.call_args_list[0][0][0], bus)
                        self.assertIs(finder.call_args_list[1][0][0], bus)
                    else:
                        self.assertEqual(modem._modem_path, old_path)
                        finder.assert_not_called()


class DialTests(unittest.TestCase):
    def test_dial_creates_and_starts_call(self):
        modem_path = MM_PATH + "/Modem/" + "0"
        call_path = MM_PATH + "/Call/" + "0"
        number = "555" + "1234"
        bus = Mock()
        bus.call.side_effect = [{modem_path: {MM + ".Modem": {}}}, call_path, None]
        modem = MMModem(bus)
        modem.dial(number)
        self.assertEqual(modem.call_path, call_path)
        create = bus.call.call_args_list[1]
        self.assertEqual(create.args[0], MM)
        self.assertEqual(create.args[1], modem_path)
        self.assertEqual(create.args[2], VOICE)
        self.assertEqual(create.args[3], "CreateCall")
        self.assertEqual(create.args[4], {"number": number})
        start = bus.call.call_args_list[2]
        self.assertEqual(start.args, (MM, call_path, CALL, "Start"))
        self.assertLess(bus.call.call_args_list.index(create), bus.call.call_args_list.index(start))

    def test_explicit_modem_path_skips_find(self):
        modem_path = MM_PATH + "/Modem/" + "0"
        call_path = MM_PATH + "/Call/" + "0"
        number = "555" + "0001"
        bus = Mock()
        bus.call.side_effect = [call_path, None]
        modem = MMModem(bus, modem_path=modem_path)
        modem.dial(number)
        self.assertEqual(modem.call_path, call_path)
        for recorded in bus.call.call_args_list:
            self.assertNotIn("GetManagedObjects", recorded.args)

    def test_find_failure_wrapped_as_oserror(self):
        number = "555" + "1234"
        bus = Mock()
        err = RuntimeError("boom")
        with patch("opos.mm_modem.find_modem", side_effect=err) as finder:
            modem = MMModem(bus)
            with self.assertRaises(OSError) as ctx:
                modem.dial(number)
        self.assertEqual(str(ctx.exception), "boom")
        self.assertIs(ctx.exception.__cause__, err)
        finder.assert_called_once_with(bus)

    def test_find_no_modem_raises_oserror_on_first_use(self):
        number = "555" + "1234"
        bus = Mock()
        bus.call.return_value = {}
        modem = MMModem(bus)
        with self.assertRaises(OSError) as ctx:
            modem.dial(number)
        self.assertEqual(str(ctx.exception), "no modem")

    def test_f2034_dial_rejects_existing_call(self):
        modem_path = MM_PATH + "/Modem/" + "0"
        call_path = MM_PATH + "/Call/" + "0"
        number = "555" + "1234"
        bus = Mock()
        modem = MMModem(bus, modem_path=modem_path)
        modem.call_path = call_path
        with self.assertRaises(OSError) as ctx:
            modem.dial(number)
        self.assertEqual(str(ctx.exception), "already in a call")
        self.assertEqual(modem.call_path, call_path)
        bus.call.assert_not_called()
        bus.get.assert_not_called()

    def test_f2034_dial_start_failure_cleanup(self):
        modem_path = MM_PATH + "/Modem/" + "0"
        call_path = MM_PATH + "/Call/" + "0"
        number = "555" + "1234"
        cases = [(None, "delete-ok"), (OSError("delete failed"), "delete-oserror")]
        for delete_result, name in cases:
            with self.subTest(name=name):
                start_err = OSError("start failed")
                bus = Mock()
                if delete_result is None:
                    bus.call.side_effect = [call_path, start_err, None]
                else:
                    bus.call.side_effect = [call_path, start_err, delete_result]
                modem = MMModem(bus, modem_path=modem_path)
                with self.assertRaises(OSError) as ctx:
                    modem.dial(number)
                self.assertEqual(str(ctx.exception), "start failed")
                self.assertIs(ctx.exception.__cause__, start_err)
                self.assertIsNone(modem.call_path)
                self.assertEqual(len(bus.call.call_args_list), 3)
                create = bus.call.call_args_list[0]
                self.assertEqual(create.args, (MM, modem_path, VOICE, "CreateCall", {"number": number}))
                start = bus.call.call_args_list[1]
                self.assertEqual(start.args, (MM, call_path, CALL, "Start"))
                delete = bus.call.call_args_list[2]
                self.assertEqual(delete.args, (MM, modem_path, VOICE, "DeleteCall", call_path))


MODEM = MM_PATH + "/Modem/" + "0"
CALL_PATH = MM_PATH + "/Call/" + "0"
SMS_PATH_1 = MM_PATH + "/SMS/" + "1"
SMS_PATH_2 = MM_PATH + "/SMS/" + "2"
SMS_PATH_3 = MM_PATH + "/SMS/" + "3"
SMS_PATH_4 = MM_PATH + "/SMS/" + "4"


class FakeBus:
    def __init__(self):
        self.calls = []
        self.errors = {}
        self.smses = {}

    def sms(self, number, text, pdu_type, state):
        path = MM_PATH + "/SMS/" + number[-1]
        self.smses[path] = {
            "Number": number,
            "Text": text,
            "PduType": pdu_type,
            "State": state,
        }
        return path

    def get(self, bus_name, path, interface, prop):
        return self.smses[path][prop]

    def call(self, bus_name, path, interface, method, *args):
        self.calls.append((path, interface, method, args))
        error = self.errors.get(method)
        if error is not None:
            raise error
        if method == "CreateCall":
            return CALL_PATH
        if method == "Create":
            return SMS_PATH_1
        if method == "List":
            return list(self.smses)
        return None


class HangUpTests(unittest.TestCase):
    def _modem_in_call(self):
        bus = FakeBus()
        modem = MMModem(bus, modem_path=MODEM)
        modem.dial("555" + "1234")
        bus.calls.clear()
        return modem, bus

    def test_hang_up_hangs_up_and_deletes(self):
        modem, bus = self._modem_in_call()
        modem.hang_up()
        self.assertEqual(
            bus.calls,
            [(CALL_PATH, CALL, "Hangup", ()), (MODEM, VOICE, "DeleteCall", (CALL_PATH,))],
        )
        self.assertIsNone(modem.call_path)

    def test_hang_up_raises_but_still_deletes_if_hangup_fails(self):
        modem, bus = self._modem_in_call()
        bus.errors["Hangup"] = OSError("already gone")
        with self.assertRaises(OSError) as ctx:
            modem.hang_up()
        self.assertEqual(str(ctx.exception), "hangup failed")
        self.assertEqual(
            bus.calls,
            [(CALL_PATH, CALL, "Hangup", ()), (MODEM, VOICE, "DeleteCall", (CALL_PATH,))],
        )
        self.assertIsNone(modem.call_path)

    def test_hang_up_propagates_non_oserror(self):
        modem, bus = self._modem_in_call()
        err = RuntimeError("boom")
        bus.errors["Hangup"] = err
        with self.assertRaises(RuntimeError) as ctx:
            modem.hang_up()
        self.assertIs(ctx.exception, err)
        self.assertEqual(bus.calls, [(CALL_PATH, CALL, "Hangup", ())])
        self.assertEqual(modem.call_path, CALL_PATH)

    def test_hang_up_with_no_call_does_nothing(self):
        bus = FakeBus()
        modem = MMModem(bus, modem_path=MODEM)
        modem.hang_up()
        self.assertEqual(bus.calls, [])
        self.assertIsNone(modem.call_path)

    def test_hang_up_delete_failure_state_and_error_precedence(self):
        for hangup_error in (None, OSError("already gone")):
            with self.subTest(hangup_error=hangup_error):
                modem, bus = self._modem_in_call()
                delete_error = OSError("delete failed")
                bus.errors["DeleteCall"] = delete_error
                if hangup_error is not None:
                    bus.errors["Hangup"] = hangup_error
                with self.assertRaises(OSError) as ctx:
                    modem.hang_up()
                self.assertEqual(str(ctx.exception), "delete failed")
                self.assertIs(ctx.exception.__cause__, delete_error)
                self.assertEqual(
                    bus.calls,
                    [(CALL_PATH, CALL, "Hangup", ()), (MODEM, VOICE, "DeleteCall", (CALL_PATH,))],
                )
                if hangup_error is None:
                    self.assertIsNone(modem.call_path)
                else:
                    self.assertEqual(modem.call_path, CALL_PATH)


class SendSmsTests(unittest.TestCase):
    def _modem_and_bus(self):
        bus = FakeBus()
        return MMModem(bus, modem_path=MODEM), bus

    def test_send_sms_create_send_delete(self):
        number = "555" + "1234"
        text = "hello"
        modem, bus = self._modem_and_bus()
        result = modem.send_sms(number, text)
        self.assertIsNone(result)
        self.assertEqual(
            bus.calls,
            [
                (MODEM, MESSAGING, "Create", ({"number": number, "text": text},)),
                (SMS_PATH_1, SMS, "Send", ()),
                (MODEM, MESSAGING, "Delete", (SMS_PATH_1,)),
            ],
        )

    def test_send_failure_raises_oserror(self):
        number = "555" + "1234"
        text = "hello"
        modem, bus = self._modem_and_bus()
        err = RuntimeError("no signal")
        bus.errors["Create"] = err
        with self.assertRaises(OSError) as ctx:
            modem.send_sms(number, text)
        self.assertEqual(str(ctx.exception), "no signal")
        self.assertIs(ctx.exception.__cause__, err)
        self.assertEqual(
            bus.calls,
            [(MODEM, MESSAGING, "Create", ({"number": number, "text": text},))],
        )

    def test_send_step_failure_raises_oserror(self):
        number = "555" + "1234"
        text = "hello"
        cases = [(None, "delete-ok"), (RuntimeError("cleanup failed"), "delete-fails")]
        for delete_error, name in cases:
            with self.subTest(name=name):
                modem, bus = self._modem_and_bus()
                send_err = RuntimeError("send failed")
                bus.errors["Send"] = send_err
                if delete_error is not None:
                    bus.errors["Delete"] = delete_error
                with self.assertRaises(OSError) as ctx:
                    modem.send_sms(number, text)
                self.assertEqual(str(ctx.exception), "send failed")
                self.assertIs(ctx.exception.__cause__, send_err)
                self.assertEqual(
                    bus.calls,
                    [
                        (MODEM, MESSAGING, "Create", ({"number": number, "text": text},)),
                        (SMS_PATH_1, SMS, "Send", ()),
                        (MODEM, MESSAGING, "Delete", (SMS_PATH_1,)),
                    ],
                )

    def test_send_sms_success_cleanup_exception_policy(self):
        number = "555" + "1234"
        text = "hello"
        modem, bus = self._modem_and_bus()
        bus.errors["Delete"] = OSError("gone")
        self.assertIsNone(modem.send_sms(number, text))
        modem, bus = self._modem_and_bus()
        delete_err = RuntimeError("cleanup failed")
        bus.errors["Delete"] = delete_err
        with self.assertRaises(RuntimeError) as ctx:
            modem.send_sms(number, text)
        self.assertIs(ctx.exception, delete_err)

    def test_send_failure_queued_in_outbox_then_retry_sends(self):
        number = "555" + "1234"
        text = "hello"
        names = ["create-fails", "send-fails-delete-ok", "send-fails-delete-fails"]
        for name in names:
            with self.subTest(name=name):
                modem, bus = self._modem_and_bus()
                if name == "create-fails":
                    bus.errors["Create"] = RuntimeError("no signal")
                else:
                    bus.errors["Send"] = RuntimeError("send failed")
                    if name == "send-fails-delete-fails":
                        bus.errors["Delete"] = RuntimeError("cleanup failed")
                app = Essentials(modem)
                app.send_message(number, text)
                self.assertEqual(len(app.outbox), 1)
                queued = app.outbox[0]
                self.assertEqual(queued.number, number)
                self.assertEqual(queued.text, text)
                self.assertFalse(queued.sent)
                if name == "create-fails":
                    self.assertEqual(
                        bus.calls,
                        [(MODEM, MESSAGING, "Create", ({"number": number, "text": text},))],
                    )
                else:
                    self.assertEqual(
                        bus.calls,
                        [
                            (MODEM, MESSAGING, "Create", ({"number": number, "text": text},)),
                            (SMS_PATH_1, SMS, "Send", ()),
                            (MODEM, MESSAGING, "Delete", (SMS_PATH_1,)),
                        ],
                    )
                bus.errors.clear()
                bus.calls.clear()
                self.assertEqual(app.retry_outbox(), 1)
                self.assertEqual(app.outbox, [])
                self.assertTrue(queued.sent)
                self.assertEqual(
                    bus.calls,
                    [
                        (MODEM, MESSAGING, "Create", ({"number": number, "text": text},)),
                        (SMS_PATH_1, SMS, "Send", ()),
                        (MODEM, MESSAGING, "Delete", (SMS_PATH_1,)),
                    ],
                )


class PollIncomingTests(unittest.TestCase):
    def test_delivers_received_incoming_and_deletes(self):
        bus = FakeBus()
        bus.sms("555" + "2222", "second", 1, 3)
        bus.sms("555" + "1111", "first", 1, 3)
        bus.sms("555" + "3333", "outgoing", 2, 3)
        bus.sms("555" + "4444", "pending", 1, 1)
        modem = MMModem(bus, modem_path=MODEM)
        essentials = Essentials(modem)
        self.assertEqual(modem.poll_incoming(essentials), 2)
        self.assertEqual(
            [(m.number, m.text, m.outgoing) for m in essentials.log],
            [("555" + "1111", "first", False), ("555" + "2222", "second", False)],
        )
        deletes = [recorded for recorded in bus.calls if recorded[2] == "Delete"]
        self.assertEqual(
            deletes,
            [
                (MODEM, MESSAGING, "Delete", (SMS_PATH_1,)),
                (MODEM, MESSAGING, "Delete", (SMS_PATH_2,)),
            ],
        )
        self.assertIn((MODEM, MESSAGING, "List", ()), bus.calls)

    def test_delivers_received_cdma_and_deletes(self):
        bus = FakeBus()
        number = "555" + "1111"
        text = "cd" + "ma"
        path = bus.sms(number, text, 32, 3)
        modem = MMModem(bus, modem_path=MODEM)
        essentials = Essentials(modem)
        self.assertEqual(modem.poll_incoming(essentials), 1)
        self.assertEqual(
            [(m.number, m.text, m.outgoing) for m in essentials.log],
            [(number, text, False)],
        )
        deletes = [recorded for recorded in bus.calls if recorded[2] == "Delete"]
        self.assertEqual(deletes, [(MODEM, MESSAGING, "Delete", (path,))])

    def test_skips_outgoing_and_non_received(self):
        bus = FakeBus()
        bus.sms("555" + "1111", "outgoing", 2, 3)
        bus.sms("555" + "2222", "sent", 1, 5)
        bus.sms("555" + "3333", "unknown", 1, 0)
        modem = MMModem(bus, modem_path=MODEM)
        essentials = Essentials(modem)
        self.assertEqual(modem.poll_incoming(essentials), 0)
        self.assertEqual(essentials.log, [])
        deletes = [recorded for recorded in bus.calls if recorded[2] == "Delete"]
        self.assertEqual(deletes, [])

    def test_bad_number_left_on_modem_and_not_counted(self):
        bus = FakeBus()
        bus.smses[SMS_PATH_1] = {
            "Number": "not" + "-a-number!",
            "Text": "bad",
            "PduType": 1,
            "State": 3,
        }
        bus.smses[SMS_PATH_2] = {
            "Number": "555" + "2222",
            "Text": "good",
            "PduType": 1,
            "State": 3,
        }
        modem = MMModem(bus, modem_path=MODEM)
        essentials = Essentials(modem)
        self.assertEqual(modem.poll_incoming(essentials), 1)
        self.assertEqual(
            [(m.number, m.text, m.outgoing) for m in essentials.log],
            [("555" + "2222", "good", False)],
        )
        deletes = [recorded for recorded in bus.calls if recorded[2] == "Delete"]
        self.assertEqual(deletes, [(MODEM, MESSAGING, "Delete", (SMS_PATH_2,))])

    def test_empty_list_returns_zero(self):
        bus = FakeBus()
        modem = MMModem(bus, modem_path=MODEM)
        essentials = Essentials(modem)
        self.assertEqual(modem.poll_incoming(essentials), 0)
        self.assertEqual(essentials.log, [])
        self.assertEqual(bus.calls, [(MODEM, MESSAGING, "List", ())])


class TestPollIncomingNoSilentExceptPass(unittest.TestCase):
    def test_poll_incoming_has_no_pass_only_handler(self):
        path = Path(__file__).resolve().parents[1] / "src" / "opos" / "mm_modem.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func = next(
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "poll_incoming"
        )
        bad = [
            node.lineno
            for node in ast.walk(func)
            if isinstance(node, ast.ExceptHandler)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ]
        self.assertEqual(bad, [], "poll_incoming has pass-only handlers at lines %s" % (bad,))


class GdbusBusTests(unittest.TestCase):
    def _completed(self, stdout):
        completed = Mock()
        completed.returncode = 0
        completed.stdout = stdout
        completed.stderr = ""
        return completed

    def test_call_argv_and_unwrap(self):
        from opos.mm_modem import GdbusBus
        modem_path = MM_PATH + "/Modem/" + "0"
        call_path = MM_PATH + "/Call/" + "1"
        number = "555" + "1234"
        stdout = "(objectpath '" + call_path + "',)\n"
        bus = GdbusBus()
        with patch("opos.mm_modem.subprocess.run", return_value=self._completed(stdout)) as run:
            result = bus.call(MM, modem_path, VOICE, "CreateCall", {"number": number})
        self.assertEqual(result, call_path)
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "gdbus",
                "call",
                "--system",
                "--dest",
                MM,
                "--object-path",
                modem_path,
                "--method",
                VOICE + ".CreateCall",
                "{'number': <'5551234'>}",
            ],
        )
        self.assertEqual(run.call_args.kwargs["capture_output"], True)
        self.assertEqual(run.call_args.kwargs["text"], True)
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(run.call_args.kwargs["check"], False)

    def test_find_modem_supplies_destination(self):
        from opos.mm_modem import GdbusBus
        modem_path = MM_PATH + "/Modem/" + "0"
        modem_iface = MM + ".Modem"
        stdout = "({'" + modem_path + "': {'" + modem_iface + "': {}}},)\n"
        bus = GdbusBus()
        with patch("opos.mm_modem.subprocess.run", return_value=self._completed(stdout)) as run:
            result = find_modem(bus)
        self.assertEqual(result, modem_path)
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "gdbus",
                "call",
                "--system",
                "--dest",
                MM,
                "--object-path",
                MM_PATH,
                "--method",
                OM + ".GetManagedObjects",
            ],
        )
        self.assertEqual(run.call_args.kwargs["capture_output"], True)
        self.assertEqual(run.call_args.kwargs["text"], True)
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(run.call_args.kwargs["check"], False)

    def test_call_string_arg_argv(self):
        from opos.mm_modem import GdbusBus
        call_path = MM_PATH + "/Call/" + "1"
        iface = MM + ".Call"
        bus = GdbusBus()
        with patch("opos.mm_modem.subprocess.run", return_value=self._completed("()\n")) as run:
            bus.call(MM, call_path, iface, "Start")
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "gdbus",
                "call",
                "--system",
                "--dest",
                MM,
                "--object-path",
                call_path,
                "--method",
                iface + ".Start",
            ],
        )
        self.assertEqual(run.call_args.kwargs["capture_output"], True)
        self.assertEqual(run.call_args.kwargs["text"], True)
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(run.call_args.kwargs["check"], False)

    def test_call_nonzero_rc_raises_oserror(self):
        from opos.mm_modem import GdbusBus
        number = "555" + "1234"
        completed = Mock()
        completed.stdout = ""
        completed.stderr = "Error: no modem\n"
        completed.returncode = 1
        bus = GdbusBus()
        with patch("opos.mm_modem.subprocess.run", return_value=completed):
            with self.assertRaises(OSError) as ctx:
                bus.call(MM, MM_PATH + "/Modem/0", VOICE, "CreateCall", {"number": number})
        self.assertEqual(str(ctx.exception), "Error: no modem")

    def test_get_nonzero_rc_raises_oserror(self):
        from opos.mm_modem import GdbusBus
        completed = Mock()
        completed.stdout = ""
        completed.stderr = "denied\n"
        completed.returncode = 1
        bus = GdbusBus()
        with patch("opos.mm_modem.subprocess.run", return_value=completed):
            with self.assertRaises(OSError) as ctx:
                bus.get(MM, MM_PATH + "/SMS/1", SMS, "State")
        self.assertEqual(str(ctx.exception), "denied")

    def test_get_argv_and_unwrap_variant(self):
        from opos.mm_modem import GdbusBus
        stdout = "(<uint32 3>,)\n"
        bus = GdbusBus()
        with patch("opos.mm_modem.subprocess.run", return_value=self._completed(stdout)) as run:
            result = bus.get(MM, SMS_PATH_1, SMS, "PduType")
        self.assertEqual(result, 3)
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "gdbus",
                "call",
                "--system",
                "--dest",
                MM,
                "--object-path",
                SMS_PATH_1,
                "--method",
                "org.freedesktop.DBus.Properties.Get",
                "'" + SMS + "'",
                "'PduType'",
            ],
        )

    def test_get_string_value(self):
        from opos.mm_modem import GdbusBus
        number = "+1" + "5551234"
        stdout = "(<'" + number + "'>,)\n"
        bus = GdbusBus()
        with patch("opos.mm_modem.subprocess.run", return_value=self._completed(stdout)):
            result = bus.get(MM, SMS_PATH_1, SMS, "Number")
        self.assertEqual(result, number)

    def test_r009_missing_decoder_raises_oserror(self):
        from opos.mm_modem import GdbusBus
        bus = GdbusBus()
        stdouts = [
            "(<uint32 3>,)",
            "({'State': <uint32 3>},)",
            "([uint32 1, 2],)",
            "(42,)",
        ]
        path = MM_PATH + "/Modem/" + "0"
        with patch("opos.mm_modem.parse_gvariant", None):
            for stdout in stdouts:
                with self.subTest(stdout=stdout):
                    with patch("opos.mm_modem.subprocess.run", return_value=self._completed(stdout)) as run_call:
                        with self.assertRaises(OSError) as ctx:
                            bus.call(MM, path, VOICE, "CreateCall")
                    self.assertEqual(str(ctx.exception), "GVariant decoder unavailable")
                    run_call.assert_not_called()
                    with patch("opos.mm_modem.subprocess.run", return_value=self._completed(stdout)) as run_get:
                        with self.assertRaises(OSError) as ctx:
                            bus.get(MM, SMS_PATH_1, SMS, "State")
                    self.assertEqual(str(ctx.exception), "GVariant decoder unavailable")
                    run_get.assert_not_called()

    def test_r011_discovery_and_incoming_sms_through_adapter(self):
        from opos.mm_modem import GdbusBus
        number = "555" + "1234"
        text = "hel" + "lo"
        modem_iface = MM + ".Modem"
        managed = "({'" + MODEM + "': {'" + modem_iface + "': {}}},)\n"
        listing = "([objectpath '" + SMS_PATH_1 + "'],)\n"
        num_out = "(<'" + number + "'>,)\n"
        text_out = "(<'" + text + "'>,)\n"
        responses = [managed, listing, num_out, text_out, "(<uint32 1>,)\n", "(<uint32 3>,)\n", "()\n"]
        results = [self._completed(stdout) for stdout in responses]
        modem = MMModem(GdbusBus())
        essentials = Essentials(modem)
        with patch("opos.mm_modem.subprocess.run", side_effect=results) as run:
            delivered = modem.poll_incoming(essentials)
        self.assertEqual(delivered, 1)
        self.assertEqual(modem._modem_path, MODEM)
        self.assertEqual(len(essentials.log), 1)
        self.assertEqual(essentials.log[0].number, number)
        self.assertEqual(essentials.log[0].text, text)
        self.assertFalse(essentials.log[0].outgoing)
        self.assertEqual(run.call_count, 7)
        props = "org.freedesktop.DBus.Properties.Get"
        expected = [
            ["gdbus", "call", "--system", "--dest", MM, "--object-path", MM_PATH, "--method", OM + ".GetManagedObjects"],
            ["gdbus", "call", "--system", "--dest", MM, "--object-path", MODEM, "--method", MESSAGING + ".List"],
            ["gdbus", "call", "--system", "--dest", MM, "--object-path", SMS_PATH_1, "--method", props, "'" + SMS + "'", "'Number'"],
            ["gdbus", "call", "--system", "--dest", MM, "--object-path", SMS_PATH_1, "--method", props, "'" + SMS + "'", "'Text'"],
            ["gdbus", "call", "--system", "--dest", MM, "--object-path", SMS_PATH_1, "--method", props, "'" + SMS + "'", "'PduType'"],
            ["gdbus", "call", "--system", "--dest", MM, "--object-path", SMS_PATH_1, "--method", props, "'" + SMS + "'", "'State'"],
            ["gdbus", "call", "--system", "--dest", MM, "--object-path", MODEM, "--method", MESSAGING + ".Delete", "'" + SMS_PATH_1 + "'"],
        ]
        for idx, argv in enumerate(expected):
            with self.subTest(idx=idx):
                self.assertEqual(run.call_args_list[idx].args[0], argv)

    def test_r011_adapter_timeout_and_nonzero_errors(self):
        import subprocess
        from opos.mm_modem import GdbusBus
        for method in ("call", "get"):
            with self.subTest(method=method):
                failed = Mock()
                failed.returncode = 1
                failed.stdout = ""
                failed.stderr = "den" + "ied" + "\n"
                bus = GdbusBus()
                with patch("opos.mm_modem.subprocess.run", return_value=failed):
                    if method == "call":
                        with self.assertRaises(OSError) as ctx:
                            bus.call(MM, MODEM, VOICE, "CreateCall")
                    else:
                        with self.assertRaises(OSError) as ctx:
                            bus.get(MM, SMS_PATH_1, SMS, "State")
                self.assertEqual(str(ctx.exception), "den" + "ied")
                expired = subprocess.TimeoutExpired(["gdbus"], 10)
                with patch("opos.mm_modem.subprocess.run", side_effect=expired) as run:
                    if method == "call":
                        with self.assertRaises(OSError) as ctx:
                            bus.call(MM, MODEM, VOICE, "CreateCall")
                    else:
                        with self.assertRaises(OSError) as ctx:
                            bus.get(MM, SMS_PATH_1, SMS, "State")
                self.assertEqual(str(ctx.exception), "gdbus timed out")
                self.assertIs(ctx.exception.__cause__, expired)
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs["timeout"], 10)


class FakeModemEssentialsTests(unittest.TestCase):
    def test_call_and_end_call(self):
        modem = FakeModem()
        app = Essentials(modem)
        number = "555" + "1234"
        app.call(number)
        self.assertEqual(modem.calls, [number])
        self.assertTrue(modem.in_call)
        app.end_call()
        self.assertFalse(modem.in_call)
        self.assertEqual(modem.calls, [number])

    def test_dial_records_number(self):
        modem = FakeModem()
        number = "555" + "9999"
        modem.dial(number)
        self.assertEqual(modem.calls, [number])
        self.assertTrue(modem.in_call)
        modem.hang_up()
        self.assertFalse(modem.in_call)

    def test_send(self):
        modem = FakeModem()
        app = Essentials(modem)
        number = "555" + "1234"
        app.send_message(number, "hello")
        self.assertEqual(modem.sent, [(number, "hello")])
        self.assertEqual(app.outbox, [])

    def test_failing_send_to_outbox_then_retry(self):
        modem = FakeModem()
        app = Essentials(modem)
        number = "555" + "1234"
        modem.fail_sms = True
        app.send_message(number, "hello")
        self.assertEqual(modem.sent, [])
        self.assertEqual(len(app.outbox), 1)
        with self.assertRaises(OSError):
            modem.send_sms(number, "direct")
        modem.fail_sms = False
        app.retry_outbox()
        self.assertEqual(modem.sent, [(number, "hello")])
        self.assertEqual(app.outbox, [])

    def test_incoming_duplicates_delivered_once_per_queue_entry(self):
        modem = FakeModem()
        app = Essentials(modem)
        first = "555" + "1234"
        second = "555" + "9999"
        modem.deliver(first, "hello")
        modem.deliver(first, "hello")
        modem.deliver(second, "later")
        self.assertEqual(modem.inbox, [(first, "hello"), (first, "hello"), (second, "later")])
        self.assertEqual(modem.poll_incoming(app), 3)
        self.assertEqual(modem.inbox, [])
        self.assertEqual(
            [(m.number, m.text, m.outgoing) for m in app.log],
            [(first, "hello", False), (first, "hello", False), (second, "later", False)],
        )
        logged = [(m.number, m.text, m.outgoing) for m in app.log]
        self.assertEqual(modem.poll_incoming(app), 0)
        self.assertEqual([(m.number, m.text, m.outgoing) for m in app.log], logged)

    def test_incoming_rejection_retained_then_retried(self):
        modem = FakeModem()
        app = Essentials(modem)
        first = "555" + "1234"
        second = "555" + "9999"
        modem.deliver(first, "hello")
        modem.deliver(second, "later")
        original = app.receive_message

        def flaky(number, text):
            if text == "hello":
                raise ValueError("reject")
            return original(number, text)

        app.receive_message = flaky
        self.assertEqual(modem.poll_incoming(app), 1)
        self.assertEqual(modem.inbox, [(first, "hello")])
        self.assertEqual(
            [(m.number, m.text, m.outgoing) for m in app.log],
            [(second, "later", False)],
        )
        app.receive_message = original
        self.assertEqual(modem.poll_incoming(app), 1)
        self.assertEqual(modem.inbox, [])
        self.assertEqual(
            [(m.number, m.text, m.outgoing) for m in app.log],
            [(second, "later", False), (first, "hello", False)],
        )
        logged = [(m.number, m.text, m.outgoing) for m in app.log]
        self.assertEqual(modem.poll_incoming(app), 0)
        self.assertEqual([(m.number, m.text, m.outgoing) for m in app.log], logged)


class DefaultModemTests(unittest.TestCase):
    def test_f2043_default_modem_selection(self):
        from opos.mm_modem import GdbusBus
        from opos.mm_modem import default_modem
        gdbus = "/usr/bin/gdbus"
        socket = "/run/dbus/system_bus_socket"
        cases = [
            (gdbus, True, "linux", True),
            (gdbus, True, "linux2", True),
            (None, True, "linux", False),
            (gdbus, False, "linux", False),
            (gdbus, True, "darwin", False),
        ]
        for which, exists, platform, available in cases:
            with self.subTest(which=which, exists=exists, platform=platform):
                with patch("opos.mm_modem.shutil.which", return_value=which) as which_mock:
                    with patch("opos.mm_modem.os.path.exists", return_value=exists) as exists_mock:
                        with patch("opos.mm_modem.sys.platform", platform):
                            with patch("opos.mm_modem.subprocess.run") as run:
                                if available:
                                    for modem in (default_modem(), default_modem(False), default_modem(True)):
                                        self.assertIsInstance(modem, MMModem)
                                        self.assertIsInstance(modem.bus, GdbusBus)
                                else:
                                    with self.assertRaises(OSError) as ctx:
                                        default_modem()
                                    self.assertEqual(str(ctx.exception), "no ModemManager available")
                                    with self.assertRaises(OSError) as ctx:
                                        default_modem(False)
                                    self.assertEqual(str(ctx.exception), "no ModemManager available")
                                    modem = default_modem(True)
                                    self.assertIsInstance(modem, FakeModem)
                                run.assert_not_called()
                self.assertEqual(which_mock.call_args_list, [call("gdbus")] * 3)
                if which:
                    self.assertEqual(exists_mock.call_args_list, [call(socket)] * 3)
                else:
                    exists_mock.assert_not_called()


class ParseVariantArrayTests(unittest.TestCase):
    def test_f2044_empty_arrays(self):
        with patch("opos.mm_modem._parse_value") as parsed:
            parsed.side_effect = AssertionError("must not be called")
            self.assertEqual(_parse_array("[]", 0), ([], 2))
            self.assertEqual(_parse_array("[ \t]", 0), ([], 4))
            self.assertEqual(_parse_array("@as []", 4), ([], 6))
            parsed.assert_not_called()

    def test_f2044_variant_delegation_and_progress(self):
        with patch("opos.mm_modem._parse_value", return_value=(7, 2)) as parsed:
            self.assertEqual(_parse_variant("<7>", 0), (7, 3))
            parsed.assert_called_once_with("<7>", 1)
        with patch("opos.mm_modem._parse_value", return_value=(None, 1)) as parsed:
            with self.assertRaises(ValueError) as ctx:
                _parse_variant("<?>", 0)
            self.assertEqual(str(ctx.exception), "no progress in variant")

    def test_f2044_array_delegation_and_progress(self):
        with patch("opos.mm_modem._parse_value", return_value=(7, 2)) as parsed:
            self.assertEqual(_parse_array("[7]", 0), ([7], 3))
            parsed.assert_called_once_with("[7]", 1)
        with patch("opos.mm_modem._parse_value", return_value=(None, 1)) as parsed:
            with self.assertRaises(ValueError) as ctx:
                _parse_array("[?]", 0)
            self.assertEqual(str(ctx.exception), "no progress in array")

    def test_r015_real_variant_array_values_and_offsets(self):
        self.assertEqual(_parse_variant("<7>", 0), (7, 3))
        self.assertEqual(_parse_array("[7, 8]", 0), ([7, 8], 6))
        self.assertEqual(_parse_variant("<[7, <8>]>", 0), ([7, 8], 10))

    def test_r014_variant_requires_immediate_closing_delimiter(self):
        bad = ["<7 junk>", "<7, <8>>", "<7", "<7 \t"]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as ctx:
                    _parse_variant(text, 0)
                self.assertEqual(str(ctx.exception), "expected '>' in variant")
        self.assertEqual(_parse_variant("<7 \t>tail", 0), (7, 5))


class ParseDictTests(unittest.TestCase):
    def test_f2045_empty_dict(self):
        with patch("opos.mm_modem._parse_value", create=True) as parsed:
            parsed.side_effect = AssertionError("must not be called")
            self.assertEqual(_parse_dict("{}", 0), ({}, 2))
            self.assertEqual(_parse_dict("x{ \t}tail", 1), ({}, 5))
            parsed.assert_not_called()

    def test_f2045_dict_separator_and_progress_errors(self):
        with patch("opos.mm_modem._parse_value", create=True) as parsed:
            parsed.side_effect = [("k", 2), ("v", 4)]
            s = "{k:v}"
            self.assertEqual(_parse_dict(s, 0), ({"k": "v"}, 5))
            self.assertEqual(parsed.call_count, 2)
            self.assertEqual(parsed.call_args_list[0].args, (s, 1))
            self.assertEqual(parsed.call_args_list[1].args, (s, 3))
        with patch("opos.mm_modem._parse_value", create=True) as parsed:
            parsed.side_effect = [("k", 2), ("v", 4)]
            s = "{k:v!"
            with self.assertRaises(ValueError) as ctx:
                _parse_dict(s, 0)
            self.assertEqual(str(ctx.exception), "expected ',' or '}' in dict")
            self.assertEqual(parsed.call_count, 2)
            self.assertEqual(parsed.call_args_list[0].args, (s, 1))
            self.assertEqual(parsed.call_args_list[1].args, (s, 3))
        with patch("opos.mm_modem._parse_value", create=True) as parsed:
            parsed.side_effect = [(None, 1)]
            s = "{!"
            with self.assertRaises(ValueError) as ctx:
                _parse_dict(s, 0)
            self.assertEqual(str(ctx.exception), "no progress in dict key")
            self.assertEqual(parsed.call_count, 1)
            self.assertEqual(parsed.call_args_list[0].args, (s, 1))
        with patch("opos.mm_modem._parse_value", create=True) as parsed:
            parsed.side_effect = [("k", 2), (None, 3)]
            s = "{k:!"
            with self.assertRaises(ValueError) as ctx:
                _parse_dict(s, 0)
            self.assertEqual(str(ctx.exception), "no progress in dict value")
            self.assertEqual(parsed.call_count, 2)
            self.assertEqual(parsed.call_args_list[0].args, (s, 1))
            self.assertEqual(parsed.call_args_list[1].args, (s, 3))

    def test_r016_dict_real_values_and_cursor(self):
        payload = "{'name': 'phone', 'count': 2, 'nested': {'enabled': <true>, 'items': [1, 2]}}"
        text = "x" + payload + "tail"
        expected = {"name": "phone", "count": 2, "nested": {"enabled": True, "items": [1, 2]}}
        self.assertEqual(_parse_dict(text, 1), (expected, 1 + len(payload)))


class ParseTupleTests(unittest.TestCase):
    def test_f2046_empty_tuple(self):
        with patch("opos.mm_modem._parse_value", create=True) as parsed:
            parsed.side_effect = AssertionError("must not be called")
            self.assertEqual(_parse_tuple("()", 0), ((), 2))
            self.assertEqual(_parse_tuple("x( \t)tail", 1), ((), 5))
            parsed.assert_not_called()

    def test_f2046_no_progress_raises(self):
        with patch("opos.mm_modem._parse_value", create=True, return_value=(None, 1)) as parsed:
            with self.assertRaises(ValueError) as ctx:
                _parse_tuple("(?)", 0)
            self.assertEqual(str(ctx.exception), "no progress in tuple")
            self.assertEqual(parsed.call_count, 1)
            self.assertEqual(parsed.call_args.args, ("(?)", 1))

    def test_f2046_invalid_separator_raises(self):
        with patch("opos.mm_modem._parse_value", create=True, return_value=(7, 2)) as parsed:
            with self.assertRaises(ValueError) as ctx:
                _parse_tuple("(7;)", 0)
            self.assertEqual(str(ctx.exception), "expected ',' or ')' in tuple")
            self.assertEqual(parsed.call_count, 1)
            self.assertEqual(parsed.call_args.args, ("(7;)", 1))

    def test_f2090_trailing_comma_closes_tuple(self):
        with patch("opos.mm_modem._parse_value", create=True, return_value=(7, 2)) as parsed:
            self.assertEqual(_parse_tuple("(7,)", 0), ((7,), 4))
            self.assertEqual(_parse_tuple("(7, )", 0), ((7,), 5))
            self.assertEqual(parsed.call_count, 2)

    def test_r018_real_parser_nonempty_tuples(self):
        cases = [
            ("(7)", 0, ((7,), 3)),
            ("x(7, 8)tail", 1, ((7, 8), 7)),
            ("(1, 'a', (2, 3))tail", 0, ((1, "a", (2, 3)), 16)),
            ("((7,),)", 0, (((7,),), 7)),
        ]
        for s, i, expected in cases:
            with self.subTest(s=s, i=i):
                self.assertEqual(_parse_tuple(s, i), expected)


class TestNoSilentExceptPass(unittest.TestCase):
    def test_no_except_handler_with_only_pass(self):
        path = Path(__file__).resolve().parents[1] / "src" / "opos" / "mm_modem.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad = sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id == "Exception"
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        )
        self.assertEqual(bad, [], "silent except handlers at lines %s" % (bad,))


class ParseGvariantTests(unittest.TestCase):
    def test_objectpath_tuple(self):
        path = "/org/freedesktop/ModemManager1/" + "Call/1"
        self.assertEqual(parse_gvariant("(objectpath '" + path + "',)"), (path,))

    def test_array_of_objectpaths(self):
        self.assertEqual(parse_gvariant("([objectpath '/a', objectpath '/b'],)"), (["/a", "/b"],))

    def test_variant_uint32(self):
        self.assertEqual(parse_gvariant("(<uint32 3>,)"), (3,))

    def test_managed_objects_dict(self):
        modem = "org.freedesktop.ModemManager1." + "Modem"
        text = "({'/m/0': {'" + modem + "': {'State': <8>}}},)"
        self.assertEqual(parse_gvariant(text), ({"/m/0": {modem: {"State": 8}}},))

    def test_escaped_quote_string(self):
        self.assertEqual(parse_gvariant("('it\\'s',)"), ("it's",))

    def test_typed_empty_container(self):
        self.assertEqual(parse_gvariant("(@a{sv} {},)"), ({},))

    def test_bool(self):
        self.assertEqual(parse_gvariant("(true,)"), (True,))
        self.assertEqual(parse_gvariant("(false,)"), (False,))
        self.assertIs(parse_gvariant("(true,)")[0], True)
        self.assertIs(parse_gvariant("(false,)")[0], False)

    def test_double_quoted_string(self):
        self.assertEqual(parse_gvariant('("hello",)'), ("hello",))

    def test_plain_int(self):
        self.assertEqual(parse_gvariant("(42,)"), (42,))

    def test_r017_singleton_tuple_trailing_comma_whitespace(self):
        cases = ["(5,)", "(5, )", "(5,\t\n)"]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_gvariant(text), (5,))

    def test_f029_numeric_forms_and_full_consumption(self):
        good = [
            ("<1.5>", 1.5, float),
            ("<double -2.5e1>", -25.0, float),
            ("[byte 0x00, 0x7f, 0xff]", [0, 127, 255], int),
            ("  42  ", 42, int),
        ]
        for text, expected, kind in good:
            with self.subTest(text=text):
                value = parse_gvariant(text)
                self.assertEqual(value, expected)
                if isinstance(expected, list):
                    for item in value:
                        self.assertIs(type(item), kind)
                else:
                    self.assertIs(type(value), kind)
        bad = ["1.5junk", "<1junk>", "<1.5 junk>", "1 2", "[byte 0xGG]", "<1e>"]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_gvariant(text)

    def test_f029_nested_numeric_properties_allow_modem_discovery(self):
        modem = "org.freedesktop.ModemManager1." + "Modem"
        path = "/" + "m/0"
        text = (
            "({'" + path + "': {'" + modem + "': "
            "{'State': <uint32 8>, 'Rate': <1.5>, "
            "'Bytes': <@ay [byte 0x01, 0xff]>}}},)"
        )
        expected = ({path: {modem: {"State": 8, "Rate": 1.5, "Bytes": [1, 255]}}},)
        self.assertEqual(parse_gvariant(text), expected)
        bus = Mock()
        bus.call.return_value = expected[0]
        self.assertEqual(find_modem(bus), path)

    def test_r028_hexadecimal_doubles_and_invalid_tokens(self):
        good = [
            ("0x1." + "8p1", 3.0),
            ("<double -0X1." + "8P+1>", -3.0),
            ("0x1" + "p-1", 0.5),
            ("0x." + "8p1", 1.0),
            ("0x1." + "8", 1.5),
        ]
        for text, expected in good:
            with self.subTest(text=text):
                value = parse_gvariant(text)
                self.assertEqual(value, expected)
                self.assertIs(type(value), float)
        value = parse_gvariant("[0x" + "10, -0X" + "f]")
        self.assertEqual(value, [16, -15])
        for item in value:
            self.assertIs(type(item), int)
        bad = [
            "0x",
            "0x." + "p1",
            "0x1" + "p",
            "0x1" + "p+",
            "0x1." + "8p1" + "junk",
            "0x1." + "8p1." + "0",
        ]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_gvariant(text)

    def test_s036_hexadecimal_double_overflow_raises_valueerror(self):
        cases = [
            "0x1p" + "1024",
            "-0x1p" + "1024",
            "<double 0x1p" + "1024>",
        ]
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as ctx:
                    parse_gvariant(text)
                self.assertEqual(str(ctx.exception), "bad numeric token")

    def test_r029_octal_integer_radix(self):
        good = [
            ("010", 8, int),
            ("-010", -8, int),
            ("077", 63, int),
            ("0", 0, int),
            ("-0", 0, int),
            ("10", 10, int),
            ("0x10", 16, int),
            ("<uint32 010>", 8, int),
            ("010.5", 10.5, float),
            ("010e1", 100.0, float),
        ]
        for text, expected, kind in good:
            with self.subTest(text=text):
                value = parse_gvariant(text)
                self.assertEqual(value, expected)
                self.assertIs(type(value), kind)
        for text in ["08", "09", "-08"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_gvariant(text)


if __name__ == "__main__":
    unittest.main()
