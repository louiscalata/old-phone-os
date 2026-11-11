# SPDX-License-Identifier: GPL-3.0-or-later
"""Local call storage and modem events, without sockets, models or a phone."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from opos import calllog, contacts_page, web
from opos.call_events import CallEvent
from opos.essentials import Essentials, Modem
from opos.ui import ScreenModem

START = 1794391200.0
NUMBER = "+15550101"


def event(ident=1, state="started", **kwargs):
    return replace(CallEvent(f"{ident:032x}", state, NUMBER, "outgoing", START, True), **kwargs)


class LocalCalls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {"OPOS_STATE_DIR": self.tmp.name, "OPOS_APPS": "calls", "OPOS_CONFIG_DIR": self.tmp.name}))
        calllog.reset()
        contacts_page.reset()
        self.addCleanup(calllog.reset)
        self.addCleanup(contacts_page.reset)
        self.path = Path(self.tmp.name) / "calls.json"

    def finish(self, ident=1, **kwargs):
        calllog.record(event(ident, **kwargs))
        calllog.record(event(ident, "connected", **kwargs))
        calllog.record(event(ident, "ended", duration=63.8, **kwargs))

    def test_name_is_saved_at_start_and_survives_edit_and_reload(self):
        status, saved = contacts_page.post({"action": "add", "name": "Ana", "number": "+1 555 0101"})
        self.assertEqual(status, 200)
        calllog.record(event())
        ident = saved["people"][0]["id"]
        contacts_page.post({"action": "edit", "id": ident, "name": "Changed", "number": NUMBER})
        calllog.record(event(state="connected"))
        calllog.record(event(state="ended", duration=63.8))
        calllog.reset()
        row = calllog.snapshot()["calls"][0]
        self.assertEqual((row["number"], row["name"], row["direction"], row["duration"], row["practice"]), (NUMBER, "Ana", "outgoing", 63.8, True))
        self.assertEqual(row["start_time"], "2026-11-11T10:00:00+00:00")
        self.assertEqual(row["state"], "ended")
        contacts_page.post({"action": "delete", "id": ident})
        self.assertEqual(calllog.snapshot()["calls"][0]["name"], "Ana")

    def test_unknown_name_and_real_adapter_event_are_not_inferred_as_practice(self):
        self.finish(practice=False)
        self.finish(2, number="+15550100", practice=False)
        for row in calllog.snapshot()["calls"]:
            self.assertEqual(row["name"], "")
            self.assertIs(row["practice"], False)
        self.finish(3, number="+15550100")
        self.assertEqual(calllog.snapshot()["calls"][0]["name"], "Mom")

    def test_answered_incoming_and_missed_count_survive_reload_and_clear(self):
        self.finish(direction="incoming")
        calllog.record(event(2, direction="incoming"))
        calllog.record(event(2, "ended", direction="incoming", duration=99))
        calllog.reset()
        snap = calllog.snapshot()
        self.assertEqual(snap["missed_count"], 1)
        self.assertEqual([(r["direction"], r["duration"]) for r in snap["calls"]], [("missed", 0), ("incoming", 63.8)])
        self.assertEqual(calllog.snapshot()["missed_count"], 1)
        self.assertEqual(calllog.clear()["missed_count"], 0)
        calllog.reset()
        self.assertEqual(calllog.snapshot()["calls"], [])

    def test_starts_are_durable_and_unfinished_incoming_is_not_called_missed(self):
        calllog.record(event(direction="incoming"))
        calllog.reset()
        snap = calllog.snapshot()
        self.assertEqual(snap["missed_count"], 0)
        self.assertEqual(snap["calls"][0]["state"], "started")
        self.assertIsNone(snap["calls"][0]["duration"])

    def test_oldest_start_is_pruned_and_file_has_a_size_bound(self):
        for n in range(calllog.MAX_CALLS + 3):
            calllog.record(event(n, at=START + n))
        # A delayed old event cannot evict a newer call.
        calllog.record(event(999, at=START - 1))
        rows = calllog.snapshot()["calls"]
        self.assertEqual(len(rows), calllog.MAX_CALLS)
        self.assertEqual(rows[0]["id"], f"{calllog.MAX_CALLS + 2:032x}")
        self.assertEqual(rows[-1]["id"], f"{3:032x}")
        self.assertLessEqual(self.path.stat().st_size, calllog.MAX_JSON)
        calllog.reset()
        self.assertEqual(calllog.snapshot()["calls"], rows)

    def test_pruning_a_missed_row_decreases_the_retained_missed_count(self):
        calllog.record(event(direction="incoming"))
        calllog.record(event(state="ended", direction="incoming"))
        with mock.patch.object(calllog, "MAX_CALLS", 2):
            self.finish(2, at=START + 1)
            self.assertEqual(calllog.snapshot()["missed_count"], 1)
            self.finish(3, at=START + 2)
            self.assertEqual(calllog.snapshot()["missed_count"], 0)

    def test_private_permissions_and_atomic_replacement_order(self):
        import opos.store as store
        original_replace = os.replace
        original_fsync = os.fsync
        order = []
        def synced(fd):
            original_fsync(fd)
            order.append("fsync")
        def replaced(src, dest):
            self.assertEqual(Path(src).name, ".calls.json.tmp")
            self.assertEqual(stat.S_IMODE(Path(src).stat().st_mode), 0o600)
            json.loads(Path(src).read_text())
            self.assertEqual(order[-1], "fsync")
            order.append("replace")
            return original_replace(src, dest)
        with mock.patch.object(store.os, "replace", side_effect=replaced), mock.patch.object(store.os, "fsync", side_effect=synced):
            self.finish()
        self.assertEqual(order, ["fsync", "replace"] * 3)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertFalse((self.path.parent / ".calls.json.tmp").exists())

    def test_failed_atomic_write_preserves_previous_complete_file(self):
        self.finish()
        before = self.path.read_bytes()
        with mock.patch("opos.store.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                calllog.record(event(2))
        self.assertEqual(self.path.read_bytes(), before)
        calllog.record(event(2))
        self.assertEqual(len(calllog.snapshot()["calls"]), 2)

    def test_corrupt_files_are_kept_private_and_recovery_notice_is_visible(self):
        invalids = [b"not json", b"\xff", b'{"schema":0}', b'{"schema":1,"calls":{}}',
                    b'{"schema":1,"calls":[{"number":"abc"}]}', b"x" * (calllog.MAX_JSON + 1)]
        for raw in invalids:
            with self.subTest(raw=raw[:40]):
                self.path.write_bytes(raw)
                snap = calllog.snapshot()
                self.assertEqual(snap["calls"], [])
                self.assertIn("Started empty", snap["recovered"])
                bad = [p for p in self.path.parent.glob("calls.json.bad-*") if p.read_bytes() == raw]
                self.assertEqual(len(bad), 1)
                self.assertEqual(stat.S_IMODE(bad[0].stat().st_mode), 0o600)
                self.finish()
                self.assertEqual(len(calllog.snapshot()["calls"]), 1)
                self.assertTrue(bad[0].exists())

    def test_invalid_row_and_duplicate_ids_recover_instead_of_silently_dropping_data(self):
        self.finish()
        valid = json.loads(self.path.read_text())
        for field, value in (("practice", "false"), ("duration", -1), ("duration", 10**400), ("direction", "other"), ("start_time", "bad"), ("id", "../oops")):
            data = json.loads(json.dumps(valid))
            data["calls"][0][field] = value
            self.path.write_text(json.dumps(data))
            self.assertEqual(calllog.snapshot()["calls"], [], field)
        valid["calls"].append(valid["calls"][0])
        self.path.write_text(json.dumps(valid))
        self.assertEqual(calllog.snapshot()["calls"], [])

    def test_unreadable_file_and_failed_quarantine_do_not_destroy_the_original(self):
        self.finish()
        raw = self.path.read_bytes()
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            snap = calllog.snapshot()
        self.assertIn("Started empty", snap["recovered"])
        self.assertEqual(next(self.path.parent.glob("calls.json.bad-*")).read_bytes(), raw)
        self.path.write_bytes(b"broken")
        with mock.patch("opos.store.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(OSError):
                calllog.clear()
        self.assertEqual(self.path.read_bytes(), b"broken")

    def test_duplicate_out_of_order_and_changed_identity_events(self):
        calllog.record(event(state="ended"))
        self.assertFalse(self.path.exists())
        calllog.record(event())
        calllog.record(event())
        with self.assertRaises(ValueError):
            calllog.record(event(state="connected", practice=False))
        calllog.record(event(state="connected"))
        calllog.record(event(state="connected"))
        calllog.record(event(state="ended", duration=5))
        calllog.record(event(state="ended", duration=99))
        self.assertEqual(len(calllog.snapshot()["calls"]), 1)
        self.assertEqual(calllog.snapshot()["calls"][0]["duration"], 5)

    def test_bad_events_are_rejected_and_no_files_are_written_while_switched_off(self):
        for bad in (None, event(call_id="bad"), event(number="bad"), event(direction="missed"), event(practice="real"), event(at=float("nan")), event(at=True), event(duration=-1), event(answered=1)):
            with self.assertRaises(ValueError):
                calllog.record(bad)
        for env in ({"OPOS_APPS": ""}, {"OPOS_APPS": "contacts"}):
            with mock.patch.dict(os.environ, env):
                calllog.record(event())
        with mock.patch.dict(os.environ):
            os.environ.pop("OPOS_APPS", None)
            calllog.record(event())
        self.assertFalse(self.path.exists())

    def test_parallel_events_keep_all_rows(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: self.finish(n, at=START + n), range(16)))
        snap = calllog.snapshot()
        self.assertEqual(len(snap["calls"]), 16)
        self.assertTrue(all(row["state"] == "ended" for row in snap["calls"]))

    def test_simulated_modem_boundary_records_duration_and_does_not_log_sms(self):
        modem = ScreenModem(call_observer=calllog.record)
        essentials = Essentials(modem)
        with mock.patch("opos.ui.time.monotonic", side_effect=[10, 75.25]), mock.patch("opos.ui.time.time", side_effect=[START, START + 1, START - 3600]):
            essentials.call(NUMBER)
            essentials.end_call()
        essentials.send_message(NUMBER, "Example")
        row = calllog.snapshot()["calls"][0]
        self.assertIs(row["practice"], True)
        self.assertEqual(row["duration"], 65.25)
        self.assertEqual(row["start_time"], "2026-11-11T10:00:00+00:00")
        self.assertEqual(len(calllog.snapshot()["calls"]), 1)
        self.assertEqual(set(Modem.__dict__) & {"dial", "hang_up", "send_sms", "call_observer"}, {"dial", "hang_up", "send_sms"})

    def test_simulated_incoming_answered_and_missed(self):
        modem = ScreenModem(call_observer=calllog.record)
        modem.receive_call(NUMBER)
        modem.hang_up()
        modem.receive_call(NUMBER)
        modem.answer_call()
        modem.hang_up()
        snap = calllog.snapshot()
        self.assertEqual([r["direction"] for r in snap["calls"]], ["incoming", "missed"])
        self.assertEqual(snap["missed_count"], 1)

    def test_lost_connected_write_does_not_turn_answered_incoming_into_missed(self):
        def observer(event):
            if event.state == "connected":
                raise OSError("disk error")
            calllog.record(event)
        modem = ScreenModem(call_observer=observer)
        modem.receive_call(NUMBER)
        with self.assertLogs("opos.calls", level="WARNING"):
            modem.answer_call()
        modem.hang_up()
        snap = calllog.snapshot()
        self.assertEqual(snap["calls"][0]["direction"], "incoming")
        self.assertEqual(snap["missed_count"], 0)
        self.assertEqual(snap["calls"][0]["state"], "ended")

    def test_observer_failure_does_not_block_call_or_reveal_number_in_warning(self):
        modem = ScreenModem(call_observer=mock.Mock(side_effect=OSError(NUMBER)))
        essentials = Essentials(modem)
        with self.assertLogs("opos.calls", level="WARNING") as logs:
            essentials.call(NUMBER)
            self.assertTrue(essentials.in_call)
            essentials.end_call()
        self.assertFalse(essentials.in_call)
        self.assertNotIn(NUMBER, " ".join(logs.output))

    def test_phone_calls_use_the_observer_and_refused_calls_are_not_logged(self):
        phone = web.Phone()
        phone.call("911")
        phone.call("bad")
        self.assertFalse(self.path.exists())
        phone.call(NUMBER)
        phone.call("+15550102")  # Refused while the first call is active.
        phone.hang_up()
        self.assertEqual(len(calllog.snapshot()["calls"]), 1)
        phone.call(NUMBER)
        phone.set_battery(0)  # The modem still supplies the ending event.
        self.assertEqual(calllog.snapshot()["calls"][0]["state"], "ended")

    def test_clear_during_call_does_not_restore_deleted_history_at_hangup(self):
        modem = ScreenModem(call_observer=calllog.record)
        modem.dial(NUMBER)
        calllog.clear()
        modem.hang_up()
        self.assertEqual(calllog.snapshot()["calls"], [])


if __name__ == "__main__":
    unittest.main()
