# SPDX-License-Identifier: GPL-3.0-or-later
"""The native screens' behaviour, run against a stand-in toolkit: widgets that only remember their text, and a shell that
records what the screens ask the server and answers when the test says so. No display and no GTK are needed, so these
run everywhere. What they pin: a draft, a contact, an alarm or a note changes on screen only when the server confirms,
a late reply never puts a screen back, rows that read the same act on the newest id, and practice is said."""

import importlib
import io
import os
import sys
import threading
import types
import unittest
import urllib.error

from opos.native import logic


class Widget:
    """Any toolkit object: it keeps its label, visibility and sensitivity, and ignores everything else."""

    def __init__(self, *a, **k):
        self.label, self.visible, self.sensitive, self.children, self.css = k.get("label", ""), True, True, [], set()

    def set_label(self, text):
        self.label = text

    def get_label(self):
        return self.label

    def set_visible(self, v):
        self.visible = bool(v)

    def get_visible(self):
        return self.visible

    def set_sensitive(self, v):
        self.sensitive = bool(v)

    def append(self, child):
        self.children.append(child)

    def remove(self, child):
        self.children.remove(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_last_child(self):
        return self.children[-1] if self.children else None

    def get_next_sibling(self):
        return None

    def get_child(self):
        return Widget()

    def set_visible_child_name(self, name):
        self.visible_child_name = name

    def grab_focus(self):
        self.focused = True

    def add_css_class(self, c):
        self.css.add(c)

    def remove_css_class(self, c):
        self.css.discard(c)

    def __getattr__(self, name):
        if name.startswith(
            ("set_", "get_", "add_", "remove_", "connect", "attach", "insert", "reorder", "queue_", "can_")
        ):
            return lambda *a, **k: Widget()
        raise AttributeError(name)  # as a real widget: no attribute until someone sets one


class Kind(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        made = Kind(name, (Widget,), {})
        setattr(cls, name, made)
        return made


class Toolkit(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        made = Kind(name, (Widget,), {})
        setattr(self, name, made)
        return made


class Core:
    def __init__(self):
        self.answers, self.sent = [], []

    def app(self, name, data=None):
        self.sent.append((name, data))
        return self.answers.pop(0) if self.answers else {}


class FakeShell:
    """What the screens need of the shell. ask() records; answer(i, reply) delivers the i-th answer as the shell would."""

    def __init__(self):
        self.snap, self.snap_seq, self.seq, self.hand = {}, 0, logic.Sequence(), "right"
        self.core, self.asked, self.went, self.pages = Core(), [], [], {}
        self.apps = [("phone", "Phone"), ("messages", "Messages")]
        self.known = True
        self.lid_closed = None

    def ask(self, action, done=None, **fields):
        n = self.seq.issue()
        self.asked.append((n, action, fields, done))
        return n

    def drawn(self, n, snap):
        """A state the shell asked for as request n (a poll) arrives and is newer than what is drawn."""
        if self.seq.accept(n):
            self.snap, self.snap_seq = snap, n
            for page in self.pages.values():
                page.show(snap)

    def answer(self, i, reply):
        """The server answered the i-th ask. A state that is newer than what is drawn replaces it, as Shell.answered does."""
        n, _action, _fields, done = self.asked[i]
        if reply is not None and self.seq.accept(n, floor=True):
            self.snap, self.snap_seq = reply, n
            for page in self.pages.values():
                page.show(reply)
        if done:
            done(reply)

    def go(self, name):
        self.went.append(name)

    def leave(self):
        self.went.append("leave")

    def set_hand(self, hand):
        self.hand = hand

    def clock_known(self):
        return self.known

    def time_text(self, now=None):
        return "10:05" if self.known else "--:--"


class Screens(unittest.TestCase):
    def setUp(self):
        names = ("gi", "gi.repository", "opos.native.screens", "opos.native.ui")
        saved = {n: sys.modules.get(n) for n in names}

        def restore():
            for n, m in saved.items():
                sys.modules.pop(n, None)
                if m is not None:
                    sys.modules[n] = m

        self.addCleanup(restore)
        for n in names:
            sys.modules.pop(n, None)
        gi = Toolkit("gi")
        gi.require_version = lambda *a: None
        gi.repository = Toolkit("gi.repository")
        gi.repository.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
        sys.modules["gi"], sys.modules["gi.repository"] = gi, gi.repository
        self.screens = importlib.import_module("opos.native.screens")
        self.work = []  # requests to the apps, held until the test lets the server answer
        self.screens.threading = types.SimpleNamespace(
            Thread=lambda target, daemon=None: types.SimpleNamespace(start=lambda: self.work.append(target))
        )
        self.shell = FakeShell()
        # the converter tests below that use the server stand-in run with the in-process code withheld; the ones for the
        # in-process path hand it in with local_converter()
        self.screens.Converter.source = staticmethod(lambda: None)

    def flush(self):
        """The server answers every request made so far, in order."""
        while self.work:
            self.work.pop(0)()

    def page(self, cls):
        page = cls(self.shell)
        self.shell.pages[page.name] = page
        return page

    def test_battery_uses_polled_state_without_fetching_or_claiming_default_measurements(self):
        snapshot = {
            "battery": {"percent": 51, "time_left": "4 hours", "drain": {"basis": "model", "per_hour": 4.1}},
            "battery_source": {"source": "sysfs", "health_source": "assumed"},
            "temp_c": 30,
        }
        self.shell.snap = snapshot
        page = self.page(self.screens.SetBattery)
        page.enter()
        self.assertFalse(page.tile)
        self.assertEqual(page.readings["Level"].get_label(), "Level: 51% · Device reading")
        self.assertEqual(page.readings["Charging"].get_label(), "Charging: Not reported")
        self.assertEqual(page.readings["Temperature"].get_label(), "Temperature: Not reported")
        self.assertEqual(page.readings["Health"].get_label(), "Health: Not reported")
        self.assertEqual(page.readings["Time left"].get_label(), "Time left: 4 hours · Server estimate")
        self.assertEqual(page.readings["Drain"].get_label(), "Drain: 4.1%/h · Model estimate")

        active = dict(
            snapshot,
            battery_source={
                "source": "sysfs",
                "health_source": "charge-counter",
                "temperature_reported": True,
            },
        )
        self.shell.drawn(self.shell.seq.issue(), active)
        self.assertEqual(page.readings["Temperature"].get_label(), "Temperature: 30.0 °C · Device reading")
        self.assertEqual(page.readings["Health"].get_label(), "Health: Estimated from the charge counter")
        self.shell.drawn(self.shell.seq.issue(), dict(snapshot, battery_source={"source": None}))
        self.assertEqual(page.readings["Level"].get_label(), "Level: Not reported")
        page.leave()
        page.enter()
        self.assertEqual(self.work, [])
        self.assertEqual(self.shell.asked, [])
        self.assertEqual(self.shell.core.sent, [])

    def test_battery_distinguishes_practice_from_device_readings(self):
        page = self.page(self.screens.SetBattery)
        self.assertEqual(page.summary.get_label(), "Waiting for battery status.")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {"percent": 0}, "charging": False, "temp_c": 0})
        self.assertIn("simulated", page.summary.get_label())
        self.assertEqual(page.readings["Level"].get_label(), "Level: 0% · Practice")
        self.assertEqual(page.readings["Charging"].get_label(), "Charging: No · Practice")
        self.assertEqual(page.readings["Temperature"].get_label(), "Temperature: 0.0 °C · Practice")

    def test_sound_reports_only_explicit_practice_volume(self):
        page = self.page(self.screens.SetSound)
        page.enter()
        self.assertEqual(page.level.get_label(), "Practice volume: Not reported")
        page.show({"volume_keys": {"percent": 70}})
        self.assertEqual(page.level.get_label(), "Practice volume: Not reported")
        page.show({"volume_keys": {"simulated": True, "percent": True}})
        self.assertEqual(page.level.get_label(), "Practice volume: Not reported")
        page.show({"volume_keys": {"simulated": True, "percent": 0}})
        self.assertEqual(page.level.get_label(), "Practice volume: 0%")
        self.assertEqual(self.work, [])

    def test_security_reports_the_lid_and_uses_the_existing_native_lock_screen(self):
        page = self.page(self.screens.SetSecurity)
        page.enter()
        self.assertEqual(page.lid.get_label(), "Lid: Not reported")
        self.shell.lid_closed = False
        page.tick()
        self.assertEqual(page.lid.get_label(), "Lid: Open")
        self.shell.lid_closed = True
        page.tick()
        self.assertEqual(page.lid.get_label(), "Lid: Closed")
        page.show_lock()
        self.assertEqual(self.shell.went, ["lock"])
        self.assertEqual(self.shell.asked, [])
        self.assertEqual(self.work, [])

    def test_display_offers_dark_and_paper_and_reports_failed_saves(self):
        shell = self.shell
        shell.theme, shell.theme_fixed, shell.theme_kept = "dark", None, True

        def apply(theme):
            shell.theme, shell.theme_kept = theme, False

        shell.set_theme = apply
        shell.brightness_level = lambda: None
        settings = self.page(self.screens.Settings)
        display = self.page(self.screens.SetDisplay)
        self.assertFalse(display.tile, "Display is a Settings page")
        self.assertIn("set_display", settings.groups)
        self.assertIn("on", display.dark.css)
        display.pick_theme("paper")
        self.assertIn("on", display.paper.css)
        self.assertNotIn("on", display.dark.css)
        self.assertIn("could not be saved", display.theme_source.get_label())
        shell.theme_fixed = "paper"
        display.enter()
        self.assertFalse(display.dark.sensitive)
        self.assertFalse(display.paper.sensitive)
        self.assertIn("OPOS_THEME", display.theme_source.get_label())

    def test_display_disables_unknown_brightness_and_reports_the_lit_level(self):
        shell = self.shell
        shell.theme, shell.theme_fixed, shell.theme_kept = "dark", None, True
        level, picked = [None], []

        def apply(fraction):
            picked.append(fraction)
            level[0] = fraction
            return True

        shell.brightness_level = lambda: level[0]
        shell.set_brightness = apply
        page = self.page(self.screens.SetDisplay)
        page.enter()
        self.assertFalse(page.bright_controls.sensitive)
        page.pick_brightness(6)
        self.assertEqual(picked, [])
        level[0] = 0.4
        page.enter()
        self.assertTrue(page.bright_controls.sensitive)
        self.assertEqual(page.bright_line.get_label(), "Brightness 40%")
        page.pick_brightness(6)
        self.assertEqual(picked, [0.6])
        self.assertEqual(page.bright_line.get_label(), "Brightness 60%")
        self.assertEqual([i for i, b in enumerate(page.steps, 1) if "on" in b.css], list(range(1, 7)))

    def test_updates_reads_and_checks_through_owner_endpoints_and_polls_current_visit(self):
        page = self.page(self.screens.SetUpdates)
        self.assertFalse(page.tile)
        self.shell.core.post = lambda action, **fields: self.shell.core.app(action, fields)
        timers, removed = [], []
        self.screens.GLib.timeout_add = lambda ms, callback: timers.append((ms, callback)) or len(timers)
        self.screens.GLib.source_remove = removed.append
        ready = {"available": True, "source": {"configured": True, "url": "https://updates.example/index?private"}}
        downloading = {**ready, "job": {"phase": "downloading", "bytes": 1000000, "total": 2000000}}
        self.shell.core.answers = [ready, dict(downloading, ok=True)]
        page.enter()
        self.flush()
        self.assertTrue(page.check_b.sensitive)
        page.check()
        page.check()
        self.assertIn("updates.example", page.note.label)
        self.assertNotIn("private", page.note.label)
        self.flush()
        self.assertEqual(self.shell.core.sent, [("update", None), ("update_check", {})])
        self.assertIn("1.0 MB of 2.0 MB", page.status.label)
        self.assertFalse(page.check_b.sensitive)
        self.assertEqual(timers[0][0], 2000)
        self.assertFalse(timers[0][1]())
        page.leave()
        page.enter()
        self.shell.core.answers = [downloading, ready]
        self.flush()
        self.assertTrue(page.check_b.sensitive)
        self.assertNotIn("Downloading", page.status.label)
        self.assertEqual(len(timers), 1)
        self.shell.core.answers = [downloading]
        page.refresh()
        self.flush()
        page.leave()
        self.assertEqual(removed, [2])

    def test_updates_download_requires_a_current_review_and_posts_its_exact_token(self):
        page = self.page(self.screens.SetUpdates)
        self.shell.core.post = lambda action, **fields: self.shell.core.app(action, fields)
        base = {"available": True, "source": {"configured": True, "url": "https://updates.example/latest"}}
        first = {**base, "check": {"newer": True, "ok": True, "version": "1.0", "serial": 1, "review": "old"}}
        newer = {**base, "check": {"newer": True, "ok": True, "version": "2.0", "serial": 2, "review": "new"}}
        self.shell.core.answers = [first, newer]
        page.enter()
        self.flush()
        self.assertTrue(page.download_b.visible)
        page.confirm_review()
        page.review_download()
        self.assertIn("1.0", page.review_text.label)
        page.cancel_review()
        page.confirm_review()
        self.assertEqual(len(self.work), 0)
        page.review_download()
        page.refresh()
        self.flush()
        self.assertEqual(page.actions.visible_child_name, "actions")
        page.confirm_review()
        self.assertEqual(len(self.work), 0)
        page.review_download()
        self.assertIn("2.0", page.review_text.label)
        self.shell.core.answers = [dict(base, ok=True)]
        page.confirm_review()
        page.confirm_review()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("update_download", {"expected": "new"}))
        self.assertEqual(len(self.shell.core.sent), 3)

    def test_updates_distinguishes_drop_from_going_back(self):
        page = self.page(self.screens.SetUpdates)
        self.shell.core.post = lambda action, **fields: self.shell.core.app(action, fields)
        base = {"available": True}
        rollback = {"possible": True, "review": {"kind": "rollback", "serial": 2, "key": "rollback-token"}}
        cancel = {
            "possible": True,
            "staged": 4,
            "review": {"kind": "cancel", "serial": 4, "key": "cancel-token"},
        }
        self.shell.core.answers = [{**base, "rollback": rollback}, {**base, "rollback": cancel}]
        page.enter()
        self.flush()
        page.review_rollback()
        self.assertIn("Go back to update 2?", page.review_text.label)
        page.refresh()
        self.flush()
        page.confirm_review()
        self.assertEqual(len(self.work), 0)
        page.review_rollback()
        self.assertIn("Drop update 4", page.review_text.label)
        self.shell.core.answers = [dict(base, ok=True)]
        page.confirm_review()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("update_rollback", {"expected": "cancel-token"}))

    # --- Date & time ----------------------------------------------------------------------------------------------

    def test_settings_time_zone_uses_the_existing_endpoint_without_a_home_tile(self):
        self.assertIn(self.screens.SetTime, self.screens.enabled_pages({}))
        self.assertFalse(self.screens.SetTime.tile)
        page = self.page(self.screens.SetTime)
        zones = ["UTC", "America/Argentina/Buenos_Aires", "America/New_York", "Europe/Paris"]
        zone = zones[1]
        applied = []
        self.shell.apply_zone = lambda value: not applied.append(value)
        self.shell.core.answers = [
            {"zones": zones, "saved": "Europe/Paris", "clock": {"plausible": True, "text": "10:05"}},
            {"ok": True, "zone": zone, "restart": False},
            {"zones": zones, "saved": zone},
        ]
        page.enter()
        self.flush()
        self.assertEqual(self.shell.core.sent, [("timezone", None)])
        self.assertEqual([row[0] for row in page.cities.rowset.words], ["America", "Europe", "UTC"])
        self.assertIn("10:05", page.clock.get_label())
        page.cities.rowset.click(0)
        self.assertEqual(page.cities.rowset.words[0], ("Argentina / Buenos Aires", zone))
        page.choose("Mars/Olympus")
        self.assertIsNone(page.chosen)
        page.cities.rowset.click(0)
        self.assertEqual(page.chosen, zone)
        self.assertTrue(page.use.sensitive)
        self.assertIn("Alarms ring", page.alarm.get_label())
        page.save()
        page.save()
        self.assertFalse(page.use.sensitive)
        self.flush()
        self.assertEqual(
            self.shell.core.sent,
            [("timezone", None), ("timezone", {"zone": zone, "client_tz": zone}), ("timezone", None)],
        )
        self.assertEqual(applied, [zone])
        self.assertEqual(page.saved_zone, zone)
        self.assertIn("saved", page.note.get_label())

    def test_time_zone_read_after_leaving_does_not_enable_save(self):
        page = self.page(self.screens.SetTime)
        page.enter()
        old = self.work.pop(0)
        page.leave()
        page.enter()
        self.shell.core.answers = [{"zones": ["UTC"]}, {"missing": True}]
        old()
        self.assertEqual(page.zones, ())
        self.flush()
        self.assertIn("unavailable", page.summary.get_label())
        page.choose("UTC")
        page.save()
        self.assertEqual(self.shell.core.sent, [("timezone", None), ("timezone", None)])

    def test_time_zone_refusal_keeps_the_shell_clock_and_allows_retry(self):
        page = self.page(self.screens.SetTime)
        applied = []
        self.shell.apply_zone = applied.append
        choices = {
            "zones": ["UTC", "Europe/Paris"],
            "saved": None,
            "clock": {"plausible": False, "note": "Clock is not set"},
        }
        self.shell.core.answers = [choices, {"error": "unknown time zone"}, choices]
        page.enter()
        self.flush()
        self.assertIn("Clock is not set", page.clock.get_label())
        page.open_region("UTC")
        self.assertEqual(page.chosen, "UTC")
        page.save()
        self.flush()
        self.assertEqual(applied, [])
        self.assertIn("unknown time zone", page.note.get_label())
        self.assertTrue(page.use.sensitive)

    def test_confirmed_time_zone_updates_shell_even_when_settings_was_left(self):
        page = self.page(self.screens.SetTime)
        applied = []
        self.shell.apply_zone = applied.append
        page.enter()
        self.shell.core.answers = [{"zones": ["UTC"]}, {"ok": True, "zone": "UTC"}]
        self.flush()
        page.open_region("UTC")
        page.save()
        page.leave()
        self.flush()
        self.assertEqual(applied, ["UTC"])
        self.assertEqual(self.shell.core.sent[-1], ("timezone", {"zone": "UTC", "client_tz": "UTC"}))
        self.assertFalse(self.work, "no background read after leaving")

    def test_returning_during_time_zone_save_applies_the_zone_and_refreshes_once(self):
        page = self.page(self.screens.SetTime)
        applied = []
        self.shell.apply_zone = lambda value: not applied.append(value)
        self.shell.core.answers = [{"zones": ["UTC"]}, {"ok": True, "zone": "UTC"}, {"zones": ["UTC"], "saved": "UTC"}]
        page.enter()
        self.flush()
        page.open_region("UTC")
        page.save()
        page.leave()
        page.enter()
        self.assertEqual(len(self.work), 1, "the new visit waits for the outstanding write before fetching")
        self.assertFalse(page.use.sensitive)
        self.assertFalse(page.cities.sensitive)
        self.flush()
        self.assertEqual(
            self.shell.core.sent,
            [("timezone", None), ("timezone", {"zone": "UTC", "client_tz": "UTC"}), ("timezone", None)],
        )
        self.assertEqual(applied, ["UTC"])
        self.assertEqual(page.saved_zone, "UTC")
        self.assertTrue(page.cities.sensitive)
        self.assertFalse(page.use.sensitive)

    # --- Messages -------------------------------------------------------------------------------------------------
    def messages(self):
        m = self.page(self.screens.Messages)
        self.shell.pages["messages"] = m
        return m

    def type_into(self, m, text):
        for ch in text:
            m.key(ch)

    def test_a_draft_stays_in_the_box_until_the_server_says_it_went_out(self):
        m = self.messages()
        m.open_thread("+1 Bob")
        self.shell.answer(0, {"battery": {}, "thread": [], "status": "thread open"})
        self.type_into(m, "hi")
        m.send_typed()
        self.assertEqual(m.typed.text, "hi", "still there while the server has not answered")
        self.assertFalse(m.send.sensitive, "and it cannot be sent twice")
        m.send_typed()
        self.assertEqual(len(self.shell.asked), 2, "the open and one send, not two")
        self.shell.answer(
            1,
            {
                "battery": {},
                "status": "sent",
                "notice": "Practice message. Nothing was sent.",
                "thread": ["You: hi"],
                "simulation": {"modem": True},
            },
        )
        self.assertEqual(m.typed.text, "")
        self.assertEqual(m.practice.get_label(), "Practice message. Nothing was sent.")
        self.assertEqual(m.thread.get_label(), "You: hi")

    def test_a_refused_send_keeps_the_words_and_shows_the_servers_reason(self):
        m = self.messages()
        m.open_thread("+1 Bob")
        self.shell.answer(0, {"battery": {}, "thread": []})
        self.type_into(m, "hi")
        m.send_typed()
        self.shell.answer(
            1,
            {
                "battery": {},
                "status": "error: battery empty, charge the phone to send",
                "notice": "Charge the phone to send",
            },
        )
        self.assertEqual(m.typed.text, "hi")
        self.assertEqual(m.practice.get_label(), "Charge the phone to send")
        self.assertTrue(m.send.sensitive, "and the owner can try again")

    def test_a_send_that_never_got_an_answer_keeps_the_words(self):
        m = self.messages()
        m.open_thread("+1 Bob")
        self.shell.answer(0, {"battery": {}, "thread": []})
        self.type_into(m, "hi")
        m.send_typed()
        self.shell.answer(1, None)
        self.assertEqual(m.typed.text, "hi")
        self.assertIn("did not go through", m.practice.get_label())

    def test_the_heading_and_the_lines_always_belong_to_the_same_person(self):
        m = self.messages()
        self.shell.snap = {
            "battery": {},
            "contacts": [{"name": "Bob", "number": "+1 Bob"}],
            "thread": ["Alice: private text"],
        }
        m.open_thread("+1 Bob")
        self.assertEqual(m.head.get_label(), "Bob")
        self.assertNotIn("Alice", m.thread.get_label(), "Alice's thread is still the server's open one")
        self.shell.answer(0, {"battery": {}, "contacts": [{"name": "Bob", "number": "+1 Bob"}], "thread": ["Bob: hi"]})
        self.assertEqual((m.head.get_label(), m.thread.get_label()), ("Bob", "Bob: hi"))

    def test_a_refused_open_says_so_under_the_new_name(self):
        m = self.messages()
        m.open_thread("+1 Bob")
        self.shell.answer(0, None)
        self.assertIn("could not be opened", m.thread.get_label())

    def test_practice_is_said_before_anything_is_sent(self):
        m = self.messages()
        m.show({"battery": {}, "simulation": {"modem": True}})
        self.assertEqual(m.practice.get_label(), logic.PRACTICE_MESSAGES)
        m.show({"battery": {}, "simulation": {"modem": False}})
        self.assertEqual(m.practice.get_label(), "")

    # --- Phone and Home -------------------------------------------------------------------------------------------
    def test_a_call_waits_for_the_server_and_a_second_tap_sends_nothing(self):
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        p.call_or_hang()
        self.assertEqual([(a, f) for _n, a, f, _d in self.shell.asked], [("call", {"number": "+15550100"})])
        self.assertFalse(p.go_call.sensitive)
        self.shell.answer(0, {"battery": {}, "in_call": True, "status": "in call", "simulation": {"modem": True}})
        self.assertEqual(p.go_call.get_label(), "Hang up")
        self.assertIn("In a practice call", p.status.get_label())
        self.assertTrue(p.go_call.sensitive)

    def test_a_refused_call_shows_the_servers_words_and_a_real_call_is_not_called_practice(self):
        p = self.page(self.screens.Phone)
        p.show(
            {
                "battery": {},
                "status": "error: this phone cannot call emergency numbers. Use another phone.",
                "notice": "This phone cannot call emergency numbers. Use another phone.",
                "in_call": False,
            }
        )
        self.assertIn("cannot call emergency numbers", p.status.get_label())
        p.show({"battery": {}, "in_call": True, "simulation": {"modem": False}})
        self.assertEqual(p.status.get_label(), "In call\n" + self.screens.EMERGENCY)

    def test_home_reveals_practice_preview_on_hold_and_clears_it_on_return(self):
        phone = self.page(self.screens.Phone)
        refreshes = []
        phone.refresh = lambda: refreshes.append("phone")
        self.shell.snap = {
            "battery": {},
            "home_note": "Practice mode: calls, texts and tasks are simulated.",
            "in_call": True,
            "simulation": {"modem": True},
        }
        home = self.page(self.screens.Home)

        home.enter()
        self.assertEqual(refreshes, [])
        self.assertIn("home-idle", home.read.css)
        self.assertEqual([tile.get_label() for tile in home.tiles.children], ["Phone", "Messages"])

        home.choose("phone")
        self.assertEqual(refreshes, ["phone"])
        self.assertNotIn("home-idle", home.read.css)
        self.assertEqual(home.v_note.get_label(), self.shell.snap["home_note"])
        self.assertEqual(home.v_text.get_label(), "In a practice call")

        home.enter()
        home.show(self.shell.snap)
        self.assertEqual(refreshes, ["phone"])
        self.assertIn("home-idle", home.read.css)
        self.assertFalse(home.v_title.visible)
        self.assertFalse(home.v_text.visible)
        self.assertFalse(home.v_note.visible)
        self.assertEqual(home.v_rows.children, [])

    def test_a_reply_that_lost_the_race_keeps_call_busy_until_a_newer_state_is_drawn(self):
        """The call is sent (n1); a poll asked after it (n2) is answered first, from before the server acted: not in a
        call. n1's success then arrives and is dropped as older news. Call must not come back for a second tap."""
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Calling", False))
        self.shell.answer(0, {"battery": {}, "in_call": True, "status": "in call"})
        self.assertFalse(self.shell.snap["in_call"], "the late reply was not drawn")
        self.assertEqual(
            (p.go_call.get_label(), p.go_call.sensitive), ("Calling", False), "still waiting for the truth"
        )
        p.call_or_hang()
        self.assertEqual(len(self.shell.asked), 1, "a second tap sends nothing")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": True})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Hang up", True))

    def test_a_poll_sent_before_the_call_reply_was_drawn_cannot_bring_call_back(self):
        """Reverse order: the call is sent (n1), a poll is sent (n2) and the server answers the poll before it acts on
        the call. The call's reply arrives first and is drawn (Hang up); the poll's older state then arrives. Its number is
        newer, but it was asked before the reply was drawn, so it is refused and Call never shows while the call is up."""
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        poll = self.shell.seq.issue()
        self.shell.answer(0, {"battery": {}, "in_call": True, "status": "in call"})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Hang up", True))
        self.shell.drawn(poll, {"battery": {}, "in_call": False})
        self.assertTrue(self.shell.snap["in_call"], "the poll from before the call was refused")
        self.assertEqual(p.go_call.get_label(), "Hang up")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": True})
        self.assertEqual(p.go_call.get_label(), "Hang up", "a poll sent after the reply is drawn as usual")
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Call", True), "and can say the call ended")

    def test_a_call_with_no_answer_frees_the_button_only_once_a_newer_state_is_drawn(self):
        """R9: a failed or timed-out reply says nothing about whether the call went out. Call is offered again only when
        a state asked for after that reply is drawn, never from the old snapshot."""
        p = self.page(self.screens.Phone)
        p.dial("+15550100")
        p.call_or_hang()
        before = self.shell.seq.issue()  # a poll asked before the failure was known
        self.shell.answer(0, None)
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Calling", False), "not from the old snapshot")
        self.shell.drawn(before, {"battery": {}, "in_call": False})
        self.assertEqual(
            (p.go_call.get_label(), p.go_call.sensitive), ("Calling", False), "asked before: still waiting"
        )
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": True})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Hang up", True), "the call did go out")
        p.call_or_hang()  # hang up; that reply times out too, and the next poll says no call
        self.shell.answer(1, None)
        self.assertFalse(p.go_call.sensitive)
        self.shell.drawn(self.shell.seq.issue(), {"battery": {}, "in_call": False})
        self.assertEqual((p.go_call.get_label(), p.go_call.sensitive), ("Call", True))

    def test_a_real_phone_says_emergency_calls_cannot_be_relied_on_and_practice_says_practice(self):
        p = self.page(self.screens.Phone)
        p.show({"battery": {}, "simulation": {"modem": False}})
        self.assertEqual(
            p.status.get_label(), "OPOS cannot place an emergency call; keep another way to call for help."
        )
        self.assertTrue(
            any(self.screens.EMERGENCY in r for r in self.screens.notices.RISKS), "the shared words, not a copy"
        )
        self.assertNotIn("simulated", self.screens.EMERGENCY, "a real call is never called simulated")
        p.show({"battery": {}})
        self.assertIn(
            self.screens.EMERGENCY, p.status.get_label(), "no simulation block: calls are not called practice"
        )
        p.show({"battery": {}, "simulation": {"modem": True}})
        self.assertEqual(p.status.get_label(), "Practice only: this phone cannot make real calls yet.")

    def test_settings_shows_the_shared_notices_as_the_browser_does_under_about(self):
        made = []
        real = self.screens.label

        def recording(text="", *classes, **k):
            made.append(text)
            return real(text, *classes, **k)

        self.screens.label = recording
        try:
            hub = self.page(self.screens.Settings)
            self.page(self.screens.SetAbout)
        finally:
            self.screens.label = real
        self.assertIn("About", made)
        self.assertEqual(hub.risk.get_label(), self.screens.notices.DISCLAIMER + " " + self.screens.EMERGENCY)
        self.assertIn(self.screens.notices.as_text(), made)
        self.assertIn(self.screens.EMERGENCY, self.screens.notices.as_text())

    def test_the_emergency_line_is_the_shared_sentence_and_a_reworded_notice_never_stops_the_shell(self):
        words = self.screens.emergency_words
        self.assertEqual(
            words(("Emergency calling is not guaranteed. Keep another way to call for help.",)),
            "Emergency calling is not guaranteed. Keep another way to call for help.",
        )
        self.assertEqual(
            words(("Calls are simulated. OPOS cannot place an emergency call; keep another way.",)),
            "OPOS cannot place an emergency call; keep another way.",
        )
        self.assertIn("emergency calls", words(("Nothing about it here.",)))
        self.assertIn("emergency calls", words(()))

    def test_only_the_everyday_apps_opos_apps_names_get_pages(self):
        names = lambda env: [c.name for c in self.screens.enabled_pages(env)]  # noqa: E731
        base = [
            "lock",
            "home",
            "phone",
            "messages",
            "calc",
            "settings",
            "set_about",
            "set_hand",
            "set_battery",
            "set_sound",
            "set_security",
            "set_display",
            "set_time",
            "set_updates",
            "network",
        ]
        self.assertEqual(names({}), base, "absent: no everyday apps")
        self.assertEqual(names({"OPOS_APPS": ""}), base, "empty: none")
        self.assertEqual(
            names({"OPOS_APPS": "notes,clock"}),
            [
                "lock",
                "home",
                "phone",
                "messages",
                "clock",
                "notes",
                "calc",
                "settings",
                "set_about",
                "set_hand",
                "set_battery",
                "set_sound",
                "set_security",
                "set_display",
                "set_time",
                "set_updates",
                "network",
            ],
        )
        self.assertEqual(
            names({"OPOS_APPS": "clock,contacts,notes,calc,calls"}),
            [
                "lock",
                "home",
                "phone",
                "messages",
                "clock",
                "contacts",
                "notes",
                "calc",
                "calls",
                "settings",
                "set_about",
                "set_hand",
                "set_battery",
                "set_sound",
                "set_security",
                "set_display",
                "set_time",
                "set_updates",
                "network",
            ],
        )
        self.assertEqual(
            names({"OPOS_APPS": "clock,contacts,notes,calc,calls,convert"}),
            [
                "lock",
                "home",
                "phone",
                "messages",
                "clock",
                "contacts",
                "notes",
                "calc",
                "convert",
                "calls",
                "settings",
                "set_about",
                "set_hand",
                "set_battery",
                "set_sound",
                "set_security",
                "set_display",
                "set_time",
                "set_updates",
                "network",
            ],
            "the Duo list: the converter has a native screen",
        )
        self.assertEqual(
            names({"OPOS_APPS": "convert"}),
            [
                "lock",
                "home",
                "phone",
                "messages",
                "calc",
                "convert",
                "settings",
                "set_about",
                "set_hand",
                "set_battery",
                "set_sound",
                "set_security",
                "set_display",
                "set_time",
                "set_updates",
                "network",
            ],
            "only when OPOS_APPS names it",
        )
        self.assertEqual(names({"OPOS_APPS": "nonsense"}), base)

    # --- Separate delete reviews -----------------------------------------------------------------------------------
    def test_an_alarm_requires_a_separate_review_of_the_current_alarm(self):
        c = self.page(self.screens.Clock)
        first = {"id": "a1", "hour": 7, "minute": 0, "days": [], "enabled": True}
        second = {"id": "a2", "hour": 8, "minute": 0, "days": [], "enabled": True}
        snapshot = {"alarms": [first, second], "clock_ok": True}
        c.got(snapshot)
        c.choose("a1")
        c.remove()
        c.remove()
        self.assertEqual(self.work, [])
        self.assertEqual(c.remove_button.get_label(), "Remove")
        self.assertEqual(c.actions.visible_child_name, "review")
        self.assertIn("07:00", c.delete_prompt.get_label())
        self.assertTrue(c.cancel_delete_button.focused)
        c.got(snapshot)
        self.assertEqual(c.actions.visible_child_name, "review", "an identical refresh keeps the review")
        c.got(dict(snapshot, alarms=[dict(first, minute=30), second]))
        c.confirm_delete()
        self.assertEqual(self.work, [], "a change to the reviewed alarm requires another review")
        c.choose("a2")
        c.remove()
        self.assertIn("08:00", c.delete_prompt.get_label())
        self.shell.core.answers = [dict(snapshot, alarms=[first])]
        c.confirm_delete()
        c.confirm_delete()
        self.assertEqual(len(self.work), 1)
        self.assertEqual(c.chosen, "a2", "still chosen while the server reply waits")
        self.flush()
        self.assertEqual(self.shell.core.sent, [("clock", {"action": "delete", "id": "a2"})])
        self.assertFalse(c.pick_box.visible, "the accepted server snapshot no longer has the deleted alarm")

    def test_contact_review_names_the_target_and_clears_on_cancel_change_or_leave(self):
        c = self.page(self.screens.Contacts)
        first = {"id": "p1", "name": "Ann", "numbers": [{"number": "2125550100"}]}
        second = {"id": "p2", "name": "Bo", "numbers": []}
        c.got({"people": [first, second]})
        c.choose("p1")
        c.remove()
        self.assertIn("Ann (2125550100)", c.delete_prompt.get_label())
        c.cancel_delete()
        c.confirm_delete()
        self.assertTrue(c.pick_box.visible, "Cancel leaves the current contact selected")
        c.remove()
        c.choose("p2")
        c.confirm_delete()
        c.remove()
        c.leave()
        c.confirm_delete()
        self.assertEqual(self.work, [])
        c.remove()
        c.remove()
        self.assertIn("Bo", c.delete_prompt.get_label())
        self.shell.core.answers = [{"people": [first]}]
        c.confirm_delete()
        c.confirm_delete()
        self.flush()
        self.assertEqual(self.shell.core.sent, [("contacts", {"action": "delete", "id": "p2"})])

    def test_a_note_is_deleted_only_by_the_review_confirmation(self):
        n = self.page(self.screens.Notes)
        self.open_note(n)
        n.confirm_delete()
        n.delete()
        n.delete()
        self.assertEqual(self.work, [], "only the review button can delete the note")
        self.assertEqual(n.actions.visible_child_name, "review")
        self.assertIn('"hello"', n.delete_prompt.get_label())
        self.assertTrue(n.cancel_delete_button.focused)
        self.assertEqual(n.delete_button.get_label(), "Delete this note")
        self.shell.core.answers = [{"ok": True, "notes": []}]
        n.confirm_delete()
        n.confirm_delete()
        self.assertEqual(len(self.work), 1)
        self.assertEqual(n.ident, "n1", "the note stays until the reply")
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("notes", {"action": "delete", "id": "n1"}))
        self.assertIsNone(n.ident)
        self.assertEqual(n.actions.visible_child_name, "edit")

    def test_a_note_review_freezes_editing_and_saving_until_cancel(self):
        n = self.page(self.screens.Notes)
        self.open_note(n)
        n.delete()
        for ch in ("x", None, "\n"):
            n.key(ch)
        n.main_action()
        n.close()
        n.open_note("n2")
        self.assertEqual(n.entry.text, "hello")
        self.assertEqual(n.text.get_label(), "hello_")
        self.assertEqual(self.work, [])
        n.cancel_delete()
        self.assertTrue(n.delete_button.focused)
        self.assertEqual(n.actions.visible_child_name, "edit")
        n.key("x")
        n.main_action()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("notes", {"action": "save", "id": "n1", "text": "hellox"}))

    def test_leaving_notes_drops_the_pending_deletion(self):
        n = self.page(self.screens.Notes)
        self.open_note(n)
        n.delete()
        n.leave()
        n.confirm_delete()
        self.assertEqual(self.work, [])
        self.assertEqual((n.ident, n.entry.text), ("n1", "hello"))
        self.assertEqual(n.actions.visible_child_name, "edit")

    def test_a_new_open_reply_disarms_the_old_note_review(self):
        n = self.page(self.screens.Notes)
        self.open_note(n)
        self.shell.core.answers = [{"ok": True, "id": "n2", "text": "another note"}]
        n.open_note("n2")
        n.delete()
        self.flush()
        n.confirm_delete()
        self.assertEqual(self.work, [])
        self.assertEqual((n.ident, n.entry.text), ("n2", "another note"))
        self.assertEqual(n.actions.visible_child_name, "edit")

    def test_failed_note_deletion_keeps_the_draft(self):
        n = self.page(self.screens.Notes)
        self.open_note(n)
        n.key("x")
        self.shell.core.answers = [{"error": "id"}]
        n.delete()
        n.confirm_delete()
        self.flush()
        self.assertEqual((n.ident, n.entry.text), ("n1", "hellox"))
        self.assertEqual(n.actions.visible_child_name, "edit")
        n.key("y")
        self.assertEqual(n.entry.text, "helloxy")

    def test_add_alarm_twice_makes_one_request(self):
        c = self.page(self.screens.Clock)
        for d in "0730":
            c.digit(d)
        self.shell.core.answers = [{"ok": True, "alarms": [], "clock_ok": True}]
        c.add_alarm()
        c.add_alarm()
        self.assertEqual(len(self.work), 1, "one request, not two alarms")
        self.assertEqual((c.add.get_label(), c.add.sensitive), ("Adding", False))
        self.flush()
        self.assertEqual((c.add.get_label(), c.add.sensitive, c.digits), ("Add alarm", True, ""))
        self.assertEqual(
            [d for _n, d in self.shell.core.sent].count({"action": "add", "hour": 7, "minute": 30, "days": []}), 1
        )

    def test_a_refusal_whose_body_cannot_be_read_still_ends_the_add(self):
        # The server refused (an HTTP error) and the connection dropped while its body was read. The answer must still
        # reach the screen, or Add would stay on "Adding" for the shell's life and every later tap would be ignored.
        c = self.page(self.screens.Clock)
        for d in "0730":
            c.digit(d)

        class Dropped(urllib.error.HTTPError):
            def read(self, *a):
                raise ConnectionResetError("reset while reading")

        body = io.BytesIO(b"unreadable")

        def refuse(name, data=None):
            self.shell.core.sent.append((name, data))
            raise Dropped("u", 500, "broken", {}, body)

        self.shell.core.app = refuse
        c.add_alarm()
        while self.work:  # as a real worker thread: an exception ends the thread and nothing else
            try:
                self.work.pop(0)()
            except Exception:
                pass
        self.assertFalse(c.adding, "the add ended")
        self.assertEqual(
            (c.add.get_label(), c.add.sensitive, c.digits), ("Add alarm", True, "0730"), "refused: the time stays"
        )
        self.assertTrue(body.closed, "the handled response is closed even when its body cannot be read")
        self.assertIn("did not go through", c.note.get_label())
        c.add_alarm()
        self.assertEqual(len(self.shell.core.sent) + len(self.work), 2, "a second tap sends again")

    def test_a_refusal_is_delivered_once_before_a_close_failure_escapes_the_worker(self):
        c = self.page(self.screens.Clock)
        for d in "0730":
            c.digit(d)

        class RefusesClose(io.BytesIO):
            close_called = False

            def close(self):
                if not self.close_called:
                    self.close_called = True
                    raise OSError("close failed")
                super().close()

        body = RefusesClose(b'{"error": "refused"}')

        def refuse(name, data=None):
            raise urllib.error.HTTPError("u", 500, "broken", {}, body)

        self.shell.core.app = refuse
        c.add_alarm()
        with self.assertRaisesRegex(OSError, "close failed"):
            self.flush()
        self.assertTrue(body.close_called, "cleanup was attempted and its failure stayed observable")
        self.assertFalse(c.adding, "the established refusal was delivered before cleanup")
        self.assertEqual(c.note.get_label(), "That did not go through.")

    def test_a_scheduling_failure_still_closes_the_refusal_response(self):
        c = self.page(self.screens.Clock)
        for d in "0730":
            c.digit(d)
        body = io.BytesIO(b'{"error": "refused"}')

        def refuse(name, data=None):
            raise urllib.error.HTTPError("u", 500, "broken", {}, body)

        def fail_schedule(*args):
            raise RuntimeError("schedule failed")

        self.shell.core.app = refuse
        self.screens.GLib.idle_add = fail_schedule
        c.add_alarm()
        with self.assertRaisesRegex(RuntimeError, "schedule failed"):
            self.flush()
        self.assertTrue(body.closed, "the owned response closes when callback scheduling fails")

    # --- Contacts and Clock ---------------------------------------------------------------------------------------
    def test_an_unrelated_answer_cannot_clear_an_unsaved_contact_and_a_refusal_keeps_it(self):
        c = self.page(self.screens.Contacts)
        for ch in "Ann":
            c.key(ch)
        c.focus("number")
        for ch in "123":
            c.key(ch)
        self.shell.core.answers = [{"error": "name"}]
        c.save()
        self.assertTrue(c.busy)
        self.assertFalse(c.save_button.sensitive)
        c.deliver(
            c.seq.issue(), {"ok": True, "people": [], "practice": False}
        )  # a refresh answered meanwhile: not this save
        self.assertEqual((c.entries["name"].text, c.entries["number"].text), ("Ann", "123"))
        self.assertTrue(c.busy)
        self.flush()
        self.assertFalse(c.busy, "the save's own answer came back")
        self.assertEqual(
            (c.entries["name"].text, c.entries["number"].text),
            ("Ann", "123"),
            "refused: the person is still there to fix",
        )
        self.assertIn("needs a name", c.note.get_label())

    def test_a_saved_contact_clears_the_boxes_only_when_the_server_saved_it(self):
        c = self.page(self.screens.Contacts)
        for ch in "Ann":
            c.key(ch)
        self.shell.core.answers = [
            {"ok": True, "people": [{"id": "a1", "name": "Ann", "numbers": []}], "practice": False}
        ]
        c.save()
        self.assertEqual(c.entries["name"].text, "Ann", "not until the server has answered")
        self.flush()
        self.assertEqual(c.entries["name"].text, "")
        self.assertEqual(c.save_button.get_label(), "Save")

    def test_rows_that_read_the_same_act_on_the_newest_id(self):
        n = self.page(self.screens.Notes)
        n.paint({"notes": [{"id": "n1", "title": "Empty note"}, {"id": "n2", "title": "Empty note"}]})
        n.paint({"notes": [{"id": "n3", "title": "Empty note"}, {"id": "n4", "title": "Empty note"}]})
        n.list.rowset.click(1)
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("notes", {"action": "open", "id": "n4"}), "not the n2 that is gone")

    def test_a_warning_when_the_clock_was_never_set(self):
        c = self.page(self.screens.Clock)
        c.paint({"alarms": [], "next": {"text": "07:00"}, "clock_ok": False})
        self.assertTrue(c.warn.visible)
        self.assertFalse(c.next.visible, "no next alarm is promised")
        c.paint({"alarms": [], "next": {"text": "07:00"}, "clock_ok": True})
        self.assertFalse(c.warn.visible)
        self.assertEqual(c.next.get_label(), "Next alarm: 07:00")

    def test_a_typed_alarm_time_stays_until_the_server_made_the_alarm(self):
        c = self.page(self.screens.Clock)
        for d in "0730":
            c.digit(d)
        self.shell.core.answers = [{"error": "full"}]
        c.add_alarm()
        self.assertEqual(c.digits, "0730", "not until the server has answered")
        self.flush()
        self.assertEqual(c.digits, "0730")
        self.assertIn("full", c.note.get_label())
        self.shell.core.answers = [{"ok": True, "alarms": [], "clock_ok": True}]
        c.add_alarm()
        self.flush()
        self.assertEqual(c.digits, "")

    def test_a_refused_alarm_removal_keeps_the_alarm_and_allows_a_new_review(self):
        c = self.page(self.screens.Clock)
        alarm = {"id": "a1", "hour": 7, "minute": 0, "days": [], "enabled": True}
        c.got({"alarms": [alarm], "clock_ok": True})
        c.choose("a1")
        self.shell.core.answers = [{"error": "id"}, {"alarms": [], "clock_ok": True}]
        c.remove()
        c.confirm_delete()
        self.assertFalse(c.remove_button.sensitive)
        c.remove()
        c.confirm_delete()
        self.assertEqual(len(self.work), 1)
        self.flush()
        self.assertTrue(c.pick_box.visible, "the refused alarm still exists on the last snapshot")
        self.assertTrue(c.remove_button.sensitive, "a refusal releases the pending state")
        self.assertIn("cannot be changed", c.note.get_label())
        c.remove()
        c.confirm_delete()
        self.flush()
        self.assertFalse(c.pick_box.visible)

    # --- Notes ----------------------------------------------------------------------------------------------------
    def open_note(self, n, ident="n1", text="hello"):
        n.paint({"notes": [{"id": ident, "title": "x"}]})
        self.shell.core.answers = [{"ok": True, "id": ident, "text": text, "notes": [{"id": ident, "title": "x"}]}]
        n.open_note(ident)
        self.flush()

    def test_a_note_closes_only_after_the_server_saved_it(self):
        n = self.page(self.screens.Notes)
        self.open_note(n)
        self.assertEqual(n.ident, "n1")
        self.shell.core.answers = [{"error": "size"}]
        n.close()
        self.assertEqual(n.ident, "n1", "still open while the server has not answered")
        self.assertTrue(n.busy)
        self.flush()
        self.assertEqual((n.ident, n.entry.text), ("n1", "hello"), "refused: the editor and the words are still there")
        self.assertFalse(n.busy)
        self.shell.core.answers = [
            {"ok": True, "id": "n1", "saved": "Saved 10:05", "notes": [{"id": "n1", "title": "hello"}]}
        ]
        n.close()
        self.flush()
        self.assertIsNone(n.ident)

    def test_a_late_answer_for_an_older_note_never_becomes_the_open_note(self):
        n = self.page(self.screens.Notes)
        n.paint({"notes": [{"id": "n1", "title": "a"}, {"id": "n2", "title": "b"}]})
        self.shell.core.answers = [{"ok": True, "id": "n1", "text": "old"}, {"ok": True, "id": "n2", "text": "new"}]
        n.open_note("n1")
        n.open_note("n2")  # the owner tapped n1, then n2
        self.work.pop(0)()  # n1's answer comes first
        self.assertIsNone(n.ident)
        self.flush()
        self.assertEqual((n.ident, n.entry.text), ("n2", "new"))

    def test_new_note_never_adopts_the_answer_for_a_note_tapped_before_it(self):
        """Tap an existing note, then New note before its answer is back. The existing note's answer has an id and a
        text, but it is not the reply to the add: taking it would make Save write the new words into the old note."""
        n = self.page(self.screens.Notes)
        n.paint({"notes": [{"id": "n1", "title": "groceries"}]})
        self.shell.core.answers = [
            {"ok": True, "id": "n1", "text": "milk", "notes": [{"id": "n1", "title": "groceries"}]},
            {"ok": True, "id": "n7", "text": "", "notes": [{"id": "n1"}, {"id": "n7"}]},
        ]
        n.open_note("n1")
        n.main_action()  # New note
        self.work.pop(0)()  # the open answer arrives first
        self.assertIsNone(n.ident, "not the reply to the add")
        self.flush()
        self.assertEqual((n.ident, n.entry.text), ("n7", ""), "the new note is the one that opens")
        for ch in "eggs":
            n.key(ch)
        n.main_action()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1], ("notes", {"action": "save", "id": "n7", "text": "eggs"}))

    def test_a_drawn_list_answer_never_opens_a_note(self):
        n = self.page(self.screens.Notes)
        n.want = "add"
        n.deliver(n.seq.issue(), {"ok": True, "id": "n1", "text": "milk", "notes": []})
        self.assertIsNone(n.ident, "an answer nobody asked to open with")

    def test_a_refresh_sent_while_an_add_was_on_its_way_cannot_hide_the_new_alarm(self):
        """R8: the add is sent (n1), a refresh is sent (n2) and answered from before the add took effect, but arrives
        after the add's reply. The add's reply sets a floor, so the stale list is refused."""
        c = self.page(self.screens.Clock)
        add, poll = c.seq.issue(), c.seq.issue()
        new = {"ok": True, "alarms": [{"id": "a1", "hour": 7, "minute": 30, "days": [], "on": True}]}
        c.deliver(add, new, None, True)
        c.deliver(poll, {"ok": True, "alarms": []})
        self.assertEqual(c.data["alarms"], new["alarms"])
        c.deliver(c.seq.issue(), {"ok": True, "alarms": []})
        self.assertEqual(c.data["alarms"], [], "a refresh asked after the reply was drawn is drawn as usual")

    def test_a_failed_write_reply_sets_no_floor_so_a_newer_refresh_is_drawn(self):
        """Round 6: the add is sent (n2), a refresh is sent (n3); the add's reply is an error. A failed write proves
        nothing about the server's state, so it must not raise the floor: the refresh, newer than the add, is drawn."""
        n = self.page(self.screens.Notes)
        n.paint({"notes": []})
        add, poll = n.seq.issue(), n.seq.issue()
        n.deliver(add, {"error": "timed out"}, None, True)
        listed = {"ok": True, "notes": [{"id": "n1", "title": "milk"}]}
        n.deliver(poll, listed)
        self.assertEqual(n.data["notes"], listed["notes"], "the valid refresh is not hidden by a failed write")

    def test_an_older_answer_is_not_drawn_over_a_newer_one(self):
        n = self.page(self.screens.Notes)
        first, second = n.seq.issue(), n.seq.issue()
        n.deliver(second, {"ok": True, "notes": [{"id": "n9", "title": "new"}]})
        n.deliver(first, {"ok": True, "notes": [{"id": "n1", "title": "old"}]})
        self.assertEqual(n.data["notes"][0]["id"], "n9")

    # --- Converter ------------------------------------------------------------------------------------------------
    KINDS = {
        "ok": True,
        "kinds": [
            {
                "id": "length",
                "title": "Length",
                "src": "km",
                "dst": "mi",
                "units": [{"id": "m", "title": "m"}, {"id": "km", "title": "km"}, {"id": "mi", "title": "mile"}],
            },
            {
                "id": "temperature",
                "title": "Temperature",
                "src": "c",
                "dst": "f",
                "units": [{"id": "c", "title": "C"}, {"id": "f", "title": "F"}, {"id": "k", "title": "K"}],
            },
        ],
    }

    def converter(self, amount="5"):
        c = self.page(self.screens.Converter)
        self.shell.core.answers = [self.KINDS]
        c.enter()
        self.flush()
        for ch in amount:
            c.key(ch)
        return c

    def test_the_converter_reads_its_measures_from_the_server_and_starts_on_the_first(self):
        c = self.converter("")
        self.assertEqual(self.shell.core.sent, [("convert", None)], "one read of the measures, nothing else")
        self.assertEqual((c.kind, c.src, c.dst), ("length", "km", "mi"))
        self.assertEqual(
            (c.measure.get_label(), c.from_b.get_label(), c.to_b.get_label()),
            ("Measure: Length", "From: km", "To: mile"),
        )
        self.assertEqual(c.result.get_label(), "", "nothing is worked out on the phone")

    def test_convert_sends_one_request_and_shows_the_servers_words(self):
        c = self.converter("5")
        self.assertEqual(c.amount.get_label(), "5")
        self.shell.core.answers = [
            {"ok": True, "text": "3.10686", "line": "5 km = 3.10686 mile", "kind": "length", "src": "km", "dst": "mi"}
        ]
        c.convert()
        c.convert()
        self.assertEqual(len(self.work), 1, "one request in flight, not two")
        self.assertEqual((c.go.get_label(), c.go.sensitive), ("Converting", False))
        self.flush()
        self.assertEqual(
            self.shell.core.sent[-1], ("convert", {"kind": "length", "value": "5", "src": "km", "dst": "mi"})
        )
        self.assertEqual((c.result.get_label(), c.line.get_label()), ("3.10686", "5 km = 3.10686 mile"))
        self.assertEqual((c.go.get_label(), c.go.sensitive), ("Convert", True))
        self.assertEqual(len(self.shell.core.sent), 2, "a conversion asks for no refresh after it")

    def test_the_answer_shown_is_the_servers_text_not_one_the_phone_works_out(self):
        c = self.converter("5")
        self.shell.core.answers = [
            {"ok": True, "text": "server says", "line": "server line", "kind": "length", "src": "km", "dst": "mi"}
        ]
        c.convert()
        self.flush()
        self.assertEqual((c.result.get_label(), c.line.get_label()), ("server says", "server line"))

    def test_an_answer_for_other_units_than_were_asked_is_not_drawn(self):
        c = self.converter("5")
        self.shell.core.answers = [
            {"ok": True, "text": "8.04672", "line": "5 mile = 8.04672 km", "kind": "length", "src": "mi", "dst": "km"}
        ]
        c.convert()
        self.flush()
        self.assertEqual(c.result.get_label(), "")
        self.assertEqual(c.note.get_label(), "That conversion is not available.")
        self.assertTrue(c.go.sensitive)

    def test_a_refusal_is_said_in_the_converters_words_and_no_result_stays(self):
        c = self.converter("5")
        self.shell.core.answers = [
            {"ok": True, "text": "3.10686", "line": "5 km = 3.10686 mile", "kind": "length", "src": "km", "dst": "mi"}
        ]
        c.convert()
        self.flush()
        c.key("<")
        for ch in "-1":
            c.key(ch)

        body = io.BytesIO(b'{"error": "sign"}')

        def refuse(name, data=None):
            self.shell.core.sent.append((name, data))
            raise urllib.error.HTTPError("u", 400, "bad", {}, body)

        self.shell.core.app = refuse
        c.convert()
        self.flush()
        self.assertEqual(self.shell.core.sent[-1][1]["value"], "-1")
        self.assertEqual(
            (c.result.get_label(), c.line.get_label()), ("", ""), "the old answer is not left beside new input"
        )
        self.assertEqual(c.note.get_label(), "Length, mass and volume stay at zero or more.")
        self.assertTrue(c.go.sensitive)
        self.assertTrue(body.closed, "the handled refusal response is closed after its message is read")

    def test_a_lost_connection_never_shows_its_raw_text(self):
        c = self.converter("5")

        def fail(name, data=None):
            raise OSError("connection refused to 127.0.0.1:8765 token=abc")

        self.shell.core.app = fail
        c.convert()
        self.flush()
        self.assertEqual(c.note.get_label(), "That conversion is not available.")
        self.assertTrue(c.go.sensitive, "the button comes back")

    def test_a_converter_the_server_does_not_have_says_it_is_missing(self):
        c = self.page(self.screens.Converter)

        body = io.BytesIO(b"{}")

        def gone(name, data=None):
            raise urllib.error.HTTPError("u", 404, "missing", {}, body)

        self.shell.core.app = gone
        c.enter()
        self.flush()
        self.assertEqual(c.note.get_label(), self.screens.MISSING % "Converter")
        self.assertTrue(body.closed, "the handled missing response is closed")
        c.convert()
        self.assertEqual(c.note.get_label(), "Loading the converter.", "no measures: nothing is converted")

    def test_no_thread_means_nothing_was_sent_and_the_button_comes_back(self):
        c = self.converter("5")

        def no_thread(target, daemon=None):
            def start():
                raise RuntimeError("can't start new thread")

            return types.SimpleNamespace(start=start)

        self.screens.threading = types.SimpleNamespace(Thread=no_thread)
        c.convert()
        self.assertFalse(c.busy)
        self.assertEqual((c.go.get_label(), c.go.sensitive), ("Convert", True))
        self.assertEqual(c.note.get_label(), "That conversion is not available.")

    def test_inputs_do_not_change_while_a_conversion_is_on_its_way(self):
        c = self.converter("5")
        self.shell.core.answers = [
            {"ok": True, "text": "3.10686", "line": "5 km = 3.10686 mile", "kind": "length", "src": "km", "dst": "mi"}
        ]
        c.convert()
        c.key("9")
        c.next_unit("src")
        c.next_unit("dst")
        c.swap()
        c.next_kind()
        self.assertEqual(c.inputs(), ("length", "5", "km", "mi"), "the answer will be for what is on screen")
        self.flush()
        self.assertEqual(c.result.get_label(), "3.10686")

    def test_any_change_to_the_input_clears_the_answer(self):
        for change in (
            lambda c: c.key("0"),
            lambda c: c.next_unit("src"),
            lambda c: c.next_unit("dst"),
            lambda c: c.swap(),
            lambda c: c.next_kind(),
        ):
            c = self.converter("5")
            self.shell.core.answers = [
                {
                    "ok": True,
                    "text": "3.10686",
                    "line": "5 km = 3.10686 mile",
                    "kind": "length",
                    "src": "km",
                    "dst": "mi",
                }
            ]
            c.convert()
            self.flush()
            change(c)
            self.assertEqual((c.result.get_label(), c.line.get_label()), ("", ""))

    def test_units_step_and_swap_and_never_meet(self):
        c = self.converter("")
        c.next_unit("src")
        self.assertEqual((c.src, c.dst), ("m", "mi"), "km, then the next one round")
        c.next_unit("src")
        self.assertEqual((c.src, c.dst), ("km", "mi"), "mile is the To unit, so From skips it")
        c.next_unit("dst")
        self.assertEqual((c.src, c.dst), ("km", "m"))
        c.swap()
        self.assertEqual((c.src, c.dst, c.from_b.get_label(), c.to_b.get_label()), ("m", "km", "From: m", "To: km"))
        c.next_kind()
        self.assertEqual(
            (c.kind, c.src, c.dst, c.measure.get_label()), ("temperature", "c", "f", "Measure: Temperature")
        )
        c.next_kind()
        self.assertEqual(c.kind, "length")

    def test_an_older_read_of_the_measures_is_not_drawn_over_a_newer_one(self):
        c = self.page(self.screens.Converter)
        first, second = c.seq.issue(), c.seq.issue()
        c.deliver(second, self.KINDS)
        c.deliver(
            first,
            {
                "ok": True,
                "kinds": [
                    {
                        "id": "mass",
                        "title": "Mass",
                        "src": "kg",
                        "dst": "lb",
                        "units": [{"id": "kg", "title": "kg"}, {"id": "lb", "title": "lb"}],
                    }
                ],
            },
        )
        self.assertEqual(c.kind, "length")

    def test_a_conversion_answer_sets_no_floor_and_is_not_the_pages_data(self):
        """A conversion changes nothing on the server: a read of the measures asked after it is still drawn, and its
        answer never becomes the measures."""
        c = self.converter("5")
        self.shell.core.answers = [
            {"ok": True, "text": "3.10686", "line": "5 km = 3.10686 mile", "kind": "length", "src": "km", "dst": "mi"}
        ]
        c.convert()
        c.refresh()
        self.shell.core.answers.append(self.KINDS)
        self.flush()
        self.assertEqual(c.data, self.KINDS)
        self.assertEqual(c.result.get_label(), "3.10686")
        c.deliver(
            c.seq.issue(),
            {"ok": True, "text": "1", "line": "nobody asked", "kind": "length", "src": "km", "dst": "mi"},
            None,
            True,
        )
        self.assertEqual(c.result.get_label(), "3.10686", "an answer nobody is waiting for is not drawn")

    def test_a_late_answer_to_an_earlier_request_is_not_drawn(self):
        c = self.converter("5")
        c.convert()
        old = self.work.pop(0)
        c.busy = False  # as if that answer had been given up on; a newer request is then sent
        c.set_busy(False)
        self.shell.core.answers = [
            {"ok": True, "text": "new", "line": "new line", "kind": "length", "src": "km", "dst": "mi"},
            {"ok": True, "text": "old", "line": "old line", "kind": "length", "src": "km", "dst": "mi"},
        ]
        c.convert()
        self.flush()
        old()
        self.assertEqual(c.result.get_label(), "new")

    def test_measures_the_converter_cannot_use_are_not_offered(self):
        c = self.page(self.screens.Converter)
        c.got(
            {
                "ok": True,
                "kinds": [
                    "x",
                    {"id": "solo", "title": "Solo", "units": [{"id": "a", "title": "a"}]},
                    {"id": 3, "title": "Bad", "units": []},
                ],
            }
        )
        self.assertEqual(c.kinds, [])
        self.assertEqual(c.note.get_label(), "That conversion is not available.")
        c.convert()
        self.flush()
        self.assertEqual(
            self.shell.core.sent, [("convert", None)], "it reads the measures again, it sends no conversion"
        )

    def test_the_home_preview_shows_the_last_answer(self):
        c = self.page(self.screens.Converter)
        self.assertEqual(c.preview({}), ("Loading the converter.", []))
        c.got(self.KINDS)
        self.assertEqual(c.preview({})[0], "No conversion yet.")
        for ch in "5":
            c.key(ch)
        self.shell.core.answers = [
            {"ok": True, "text": "3.10686", "line": "5 km = 3.10686 mile", "kind": "length", "src": "km", "dst": "mi"}
        ]
        c.convert()
        self.flush()
        self.assertEqual(c.preview({})[0], "5 km = 3.10686 mile")

    # --- Converter, in this process -------------------------------------------------------------------------------
    def local_converter(self, amount="5"):
        """A Converter that works conversions out in this process, with a server that cannot be reached at all."""

        def down(name, data=None):
            raise OSError("connection refused")

        self.shell.core.app = down
        c = self.page(self.screens.Converter)
        c.source = self.screens.converter_source
        c.enter()
        self.flush()
        for ch in amount:
            c.key(ch)
        return c

    def test_the_converter_works_with_the_server_down_and_asks_it_nothing(self):
        from opos import convert

        c = self.local_converter("5")
        self.assertEqual([k["id"] for k in c.kinds], [k["id"] for k in convert.snapshot()["kinds"]])
        self.assertEqual((c.kind, c.src, c.dst), ("length", "km", "mi"))
        self.assertEqual(c.note.get_label(), "", "no 'not available' while the server is down")
        c.convert()
        self.assertEqual((c.go.get_label(), c.go.sensitive), ("Converting", False))
        self.flush()
        want = convert.convert("length", "5", "km", "mi")
        self.assertEqual((c.result.get_label(), c.line.get_label()), (want["text"], want["line"]))
        self.assertEqual((c.go.get_label(), c.go.sensitive), ("Convert", True))
        self.assertEqual(self.shell.core.sent, [], "the server was never asked")

    def test_the_screen_gives_the_servers_answer_for_every_input_and_unit_pair(self):
        """One table, one handler: for every measure, every ordered pair of units and a table of amounts (good, refused,
        edge), what the screen shows equals what the server's /api/convert handler answers, word for word."""
        from opos import convert, convert_page

        amounts = [
            "0",
            "1",
            "5",
            "12.5",
            "0.000001",
            "999999999999",
            "1000000000000",
            "1000000000001",
            "-1",
            "-0",
            "-273.15",
            "-273.16",
            "-300",
            "-459.67",
            "-459.68",
            "00",
            "1.",
            ".5",
            "+3",
            "1e5",
            "abc",
            " 5",
            "5 ",
            "٣",
            "9" * 41,
        ]
        seen = set()
        for kind in convert.snapshot()["kinds"]:
            ids = [u["id"] for u in kind["units"]]
            for src in ids:
                for dst in ids:
                    if src == dst:
                        continue
                    for amount in amounts:
                        c = self.local_converter("")
                        c.kind, c.src, c.dst = kind["id"], src, dst
                        c.entry.text = amount
                        c.changed()
                        c.convert()
                        if not amount:
                            continue
                        self.flush()
                        status, body = convert_page.post({"kind": kind["id"], "value": amount, "src": src, "dst": dst})
                        if status == 200:
                            self.assertEqual(
                                (c.result.get_label(), c.line.get_label(), c.note.get_label()),
                                (body["text"], body["line"], ""),
                                (kind["id"], src, dst, amount),
                            )
                        else:
                            seen.add(body["error"])
                            self.assertEqual(
                                (c.result.get_label(), c.line.get_label(), c.note.get_label()),
                                ("", "", self.screens.Converter.refusal_words(body)),
                                (kind["id"], src, dst, amount),
                            )
                        self.assertTrue(c.go.sensitive)
        self.assertEqual(seen, {"value", "sign", "cold"}, "every refusal a typed amount can cause was in the table")
        self.assertEqual(self.screens.Converter.refusal_words({"error": "value"}), "Enter a number.")
        self.assertEqual(
            self.screens.Converter.refusal_words({"error": "sign"}), "Length, mass and volume stay at zero or more."
        )
        self.assertEqual(self.screens.Converter.refusal_words({"error": "cold"}), "That is below absolute zero.")

    def test_the_in_process_answer_is_the_handlers_answer_for_a_request_the_screen_could_not_send(self):
        """The refusals the buttons cannot cause (an unknown unit or measure, a request that is not an object) come out
        of the same handler with the same codes, so a screen given such a body says what the server's page would."""
        from opos import convert_page

        read, post = self.screens.converter_source()
        for body in (
            {"kind": "length", "value": "1", "src": "km", "dst": "zz"},
            {"kind": "heat", "value": "1", "src": "c", "dst": "f"},
            {},
            {"kind": "length", "value": 5, "src": "km", "dst": "mi"},
            {"kind": "length", "value": True, "src": "km", "dst": "mi"},
        ):
            self.assertEqual(post(body), convert_page.post(body))
        c = self.page(self.screens.Converter)
        c.source = self.screens.converter_source
        self.assertEqual(c.fetch({"kind": "length", "value": "1", "src": "km", "dst": "zz"}), {"error": "unit"})
        self.assertEqual(c.fetch({}), {"error": "kind"})
        self.assertEqual(c.fetch(None), read())

    def test_the_server_is_asked_only_when_the_converters_own_code_is_unavailable(self):
        c = self.page(self.screens.Converter)
        self.assertIsNone(c.source(), "this test class withholds it")
        self.shell.core.answers = [self.KINDS]
        c.enter()
        self.flush()
        self.assertEqual(self.shell.core.sent, [("convert", None)])
        c.source = self.screens.converter_source
        c.refresh()
        self.flush()
        self.assertEqual(self.shell.core.sent, [("convert", None)], "now it is worked out here")

    def test_in_process_an_older_answer_is_still_never_drawn_over_a_newer_one(self):
        c = self.local_converter("5")
        c.convert()
        old = self.work.pop(0)
        c.busy = False  # as if that answer had been given up on; a newer request is then sent
        c.set_busy(False)
        c.key("0")  # 50 km
        c.convert()
        self.flush()
        self.assertEqual(c.line.get_label(), "50 km = 31.0686 mile")
        old()
        self.assertEqual(
            c.line.get_label(), "50 km = 31.0686 mile", "the answer for 5 km arrived late and is not drawn"
        )

    def test_in_process_one_conversion_is_asked_at_a_time_and_the_input_holds_while_it_is_on_its_way(self):
        c = self.local_converter("5")
        c.convert()
        c.convert()
        self.assertEqual(len(self.work), 1, "a second tap sends nothing")
        c.key("9")
        c.next_unit("src")
        c.next_unit("dst")
        c.swap()
        c.next_kind()
        self.assertEqual(c.inputs(), ("length", "5", "km", "mi"))
        self.flush()
        self.assertEqual(c.line.get_label(), "5 km = 3.10686 mile")
        c.key("9")
        self.assertEqual((c.result.get_label(), c.line.get_label()), ("", ""), "a later change clears the answer")

    def test_in_process_a_refusal_is_said_in_the_converters_words_and_no_result_stays(self):
        c = self.local_converter("5")
        c.convert()
        self.flush()
        c.key("<")
        c.key("-")
        c.key("1")
        c.convert()
        self.flush()
        self.assertEqual((c.result.get_label(), c.line.get_label()), ("", ""))
        self.assertEqual(c.note.get_label(), "Length, mass and volume stay at zero or more.")
        self.assertTrue(c.go.sensitive)

    def test_the_home_preview_shows_the_last_answer_in_process(self):
        c = self.local_converter("5")
        self.assertEqual(c.preview({})[0], "No conversion yet.")
        c.convert()
        self.flush()
        self.assertEqual(c.preview({})[0], "5 km = 3.10686 mile")

    # --- Settings hub --------------------------------------------------------------------------------------------

    def test_settings_only_links_registered_sections_and_each_link_opens_that_section(self):
        calls = {}
        real = self.screens.settings_row

        def recording(title, detail, callback):
            calls[title] = callback
            return real(title, detail, callback)

        self.screens.settings_row = recording
        existing = {"settings", "set_about", "set_time", "network", "set_hand"}
        self.screens.PAGES = tuple(page for page in self.screens.PAGES if page.name in existing)
        hub = self.page(self.screens.Settings)
        self.assertEqual(list(hub.groups), ["set_about", "set_time", "network", "set_hand"])
        self.assertEqual(list(calls), ["About", "Date & time", "Network", "Hand & posture"])
        for navigate in calls.values():
            navigate()
        self.assertEqual(self.shell.went, list(hub.groups))
        self.assertEqual(self.shell.core.sent, [], "creating and choosing hub links does not ask the server")
        self.assertEqual(self.work, [])

        class OptionalBattery(self.screens.SettingsSection):
            name = "set_battery"

        self.screens.PAGES += (OptionalBattery,)
        hub = self.screens.Settings(self.shell)
        self.assertEqual(list(hub.groups), ["set_about", "set_battery", "set_time", "network", "set_hand"])
        calls["Battery"]()
        self.assertEqual(self.shell.went[-1], "set_battery")

    def test_settings_sections_have_a_return_link_and_no_home_tile(self):
        calls = {}
        real = self.screens.button

        def recording(title, callback, *classes):
            if title == "◀ Settings":
                calls["back"] = callback
            return real(title, callback, *classes)

        self.screens.button = recording
        for cls in (self.screens.SetAbout, self.screens.SetTime, self.screens.Network, self.screens.SetHand):
            with self.subTest(page=cls.name):
                self.assertFalse(self.page(cls).tile)
                calls.pop("back")()
                self.assertEqual(self.shell.went[-1], "settings")

    def test_hand_section_retains_the_switch_and_the_current_posture(self):
        hub = self.page(self.screens.Settings)
        hand = self.page(self.screens.SetHand)
        self.assertEqual(hand.position.get_label(), "Buttons on the right.")
        self.assertEqual(hand.hand.get_label(), "Switch to left")
        hand.switch_hand()
        hub.paint_hand()
        self.assertEqual(hand.position.get_label(), "Buttons on the left.")
        self.assertEqual(hand.hand.get_label(), "Switch to right")
        self.shell.posture, self.shell.angle, self.shell.back = "back", 351, 1
        hub.paint_posture()
        self.assertEqual(
            hand.hinge_line.get_label(), "Back, hinge at 351 degrees. One screen is lit; the other is dark."
        )
        self.assertEqual(hand.hinge_line.get_label(), hub.hinge_line.get_label())

    # --- Settings: storage ----------------------------------------------------------------------------------------
    def test_storage_is_read_when_settings_is_entered_and_not_before(self):
        reads = []

        def usage(path):
            reads.append(path)
            return types.SimpleNamespace(total=52_100_000_000, used=0, free=12_300_000_000)

        self.screens.shutil = types.SimpleNamespace(disk_usage=usage)
        s = self.page(self.screens.Settings)
        about = self.page(self.screens.SetAbout)
        s.preview({})
        s.show({})
        s.tick()
        self.assertEqual(reads, [], "building, previewing, a new state and the clock read nothing")
        self.assertEqual(s.storage.get_label(), "Storage: unknown")
        s.enter()
        self.assertEqual(s.storage.get_label(), "Storage: 12.3 GB free of 52.1 GB")
        about.enter()
        self.assertEqual(about.storage.get_label(), s.storage.get_label())
        self.assertEqual(len(reads), 1)
        self.assertEqual(
            reads[0],
            os.path.expanduser("~") if os.path.isdir(os.path.expanduser("~")) else os.sep,
            "the owner's home folder is the one measured",
        )
        s.enter()
        self.assertEqual(len(reads), 2, "read again on each visit, once")

    def test_a_failing_storage_read_says_unknown(self):
        def usage(path):
            raise PermissionError("no")

        self.screens.shutil = types.SimpleNamespace(disk_usage=usage)
        s = self.page(self.screens.Settings)
        s.enter()
        self.assertEqual(s.storage.get_label(), "Storage: unknown")


class Recorder:
    """A page as the shell sees it: it notes what it was shown."""

    def __init__(self, name):
        self.name, self.shown, self.entered, self.lost_text, self.data = name, [], 0, None, None
        self.seq = logic.Sequence()
        self.delivered, self.left = [], 0

    def show(self, snap):
        self.shown.append(snap)

    def enter(self):
        self.entered += 1

    def leave(self):
        self.left += 1

    def tick(self):
        pass

    def lost(self, text):
        self.lost_text = text

    def deliver(self, n, data):
        self.delivered.append((n, data))


class ShellRules(unittest.TestCase):
    """Shell.answered, polled, go and the start-up walk, with the shell built without a window."""

    def setUp(self):
        names = ("gi", "gi.repository", "opos.native.shell", "opos.native.screens", "opos.native.ui")
        saved = {n: sys.modules.get(n) for n in names}

        def restore():
            for n, m in saved.items():
                sys.modules.pop(n, None)
                if m is not None:
                    sys.modules[n] = m

        self.addCleanup(restore)
        for n in names:
            sys.modules.pop(n, None)
        gi = Toolkit("gi")
        gi.require_version = lambda *a: None
        gi.repository = Toolkit("gi.repository")
        self.timers = []
        gi.repository.GLib = types.SimpleNamespace(
            idle_add=lambda fn, *a: fn(*a), timeout_add=lambda ms, fn, *a: self.timers.append((fn, a))
        )
        sys.modules["gi"], sys.modules["gi.repository"] = gi, gi.repository
        self.shell_module = importlib.import_module("opos.native.shell")
        sh = self.sh = self.shell_module.Shell.__new__(self.shell_module.Shell)
        sh.seq, sh.link, sh.ring = logic.Sequence(), logic.Link(), logic.RingGate()
        sh.snap, sh.snap_seq, sh.said, sh.said_text, sh.battery_line = {}, 0, 0, "", ""
        sh.wake, sh.warming, sh.wanted, sh.page, sh.leaving = threading.Event(), False, None, "lock", 0
        sh.pages = {n: Recorder(n) for n in ("lock", "home", "phone", "clock")}
        sh.s_right, sh.b_head, sh.b_detail, sh.b_events, sh.strip, sh.sheet, sh.b_link = (Widget() for _ in range(7))
        sh.reads, sh.acts, sh.splash = Widget(), Widget(), Widget()
        sh.quit_calls = 0
        sh.get_application = lambda: types.SimpleNamespace(quit=lambda: setattr(sh, "quit_calls", sh.quit_calls + 1))
        sh.ready = logic.Ready(lambda: setattr(sh, "said_ready", True))
        sh.said_ready = False
        sh.lock_check, sh.held = logic.LockCheck(), False  # the server said "no PIN lock" in every poll below
        sh.get_mapped = lambda: True
        sh.get_frame_clock = lambda: None

    def state(self, **kw):
        return dict({"battery": {"percent": 80}}, **kw)

    def test_a_late_reply_never_puts_the_screen_back(self):
        sh = self.sh
        poll, action = sh.seq.issue(), sh.seq.issue()
        sh.answered(action, self.state(in_call=True), None)
        sh.polled(poll, self.state(in_call=False), False)  # the poll started before the call and landed after it
        self.assertTrue(sh.snap["in_call"])
        self.assertEqual(len(sh.pages["phone"].shown), 1)

    def test_a_poll_sent_before_an_action_reply_was_drawn_is_refused_even_with_a_newer_number(self):
        sh = self.sh
        action = sh.seq.issue()
        poll = sh.seq.issue()  # sent while the call was on its way
        sh.answered(action, self.state(in_call=True), None)
        sh.polled(poll, self.state(in_call=False), False)  # answered by the server before it acted on the call
        self.assertTrue(sh.snap["in_call"])
        self.assertEqual(sh.snap_seq, action)
        sh.polled(sh.seq.issue(), self.state(in_call=True, status="later"), False)
        self.assertEqual(sh.snap["status"], "later", "the next poll is drawn")

    def test_a_reply_that_lost_the_race_still_reaches_the_screen_that_asked_and_asks_for_a_fresh_look(self):
        sh, told = self.sh, []
        a, b = sh.seq.issue(), sh.seq.issue()
        sh.answered(b, self.state(), None)
        sh.wake.clear()
        sh.answered(a, self.state(in_call=True), told.append)
        self.assertEqual(len(told), 1)
        self.assertTrue(sh.wake.is_set(), "the poll looks again at once")
        self.assertNotIn("in_call", sh.snap)

    def test_battery_indicators_agree_with_the_source_and_hide_unavailable_values(self):
        sh = self.sh
        sh.pages["lock"] = lock = self.shell_module.screens.Lock(sh)
        self.assertEqual(lock.batt.get_label(), "Battery not reported")
        battery = dict(percent=63, time_left="3 hours", headline="Normal", events=["Display on"])
        for source, level, detail in (
            ({"source": "sysfs"}, "Battery · 63% · Charging", "3 hours"),
            ({"source": "termux-api"}, "Battery · 63% · Charging", "3 hours"),
            (None, "Battery unavailable", ""),
            ({"source": None}, "Battery unavailable", ""),
        ):
            with self.subTest(source=source):
                sh.polled(sh.seq.issue(), self.state(battery=battery, charging=True, battery_source=source), False)
                self.assertEqual(lock.batt.get_label(), level)
                self.assertEqual(sh.b_head.get_label(), level)
                self.assertTrue(sh.s_right.get_label().startswith(level))
                self.assertEqual(sh.b_detail.get_label().strip(), detail)
                self.assertEqual(sh.b_events.get_label(), "Display on" if detail else "")

        sh.polled(sh.seq.issue(), self.state(battery=dict(percent=True), charging="yes"), False)
        self.assertEqual(sh.b_head.get_label(), "Battery level not reported")
        self.assertEqual(sh.s_right.get_label(), "Battery level not reported")
        self.assertEqual(lock.batt.get_label(), "Battery level not reported")
        sh.polled(sh.seq.issue(), self.state(battery=battery), False)
        self.assertEqual(sh.b_head.get_label(), "Practice battery · 63%")
        self.assertEqual(lock.batt.get_label(), "Practice battery · 63%")

    def test_a_failed_poll_keeps_the_last_good_state_and_says_the_connection_is_lost(self):
        sh = self.sh
        sh.polled(sh.seq.issue(), self.state(status="ready"), False)
        good = sh.snap
        sh.polled(sh.seq.issue(), None, None)
        self.assertIs(sh.snap, good)
        self.assertEqual(sh.s_right.get_label(), logic.LINK_LOST)
        self.assertEqual(sh.b_link.get_label(), logic.LINK_LOST)
        self.assertTrue(sh.b_link.visible)
        self.assertEqual(sh.b_head.get_label(), "Practice battery · 80%")
        self.assertEqual(
            sh.pages["lock"].lost_text, logic.LINK_LOST, "the lock screen has no top line, so it says so itself"
        )
        self.assertEqual(len(sh.pages["phone"].shown), 1, "no page was told anything new")
        sh.polled(sh.seq.issue(), self.state(), False)
        self.assertEqual((sh.s_right.get_label(), sh.pages["lock"].lost_text), ("Practice battery · 80%", ""))
        self.assertFalse(sh.b_link.visible)

    def test_an_error_body_is_not_a_state(self):
        sh = self.sh
        sh.polled(sh.seq.issue(), {"error": "forbidden"}, False)
        self.assertEqual(sh.snap, {})
        self.assertEqual(sh.s_right.get_label(), logic.LINK_WAITING)

    def test_a_locked_server_sends_the_owner_to_the_browser(self):
        sh = self.sh
        sh.polled(sh.seq.issue(), {"locked": True, "battery": {"percent": 80}}, False)
        self.assertEqual((sh.leaving, sh.quit_calls), (self.shell_module.LOCKED, 1))

    def test_a_request_to_change_page_while_the_cover_is_up_waits_and_the_page_stays_with_the_stack(self):
        sh = self.sh
        sh.warming = True
        sh.go("home")
        self.assertEqual((sh.page, sh.wanted, sh.pages["home"].entered), ("lock", "home", 0))
        sh.go("clock")  # an alarm rings while the cover is up: the newest request is the one kept
        self.assertEqual(sh.wanted, "clock")
        self.assertEqual(sh.pages["lock"].left, 0, "nothing is left under the cover")
        sh.warming = False
        sh.show_page = lambda *a: None
        sh.go("clock")
        self.assertEqual(sh.page, "clock")
        self.assertEqual((sh.pages["lock"].left, sh.pages["clock"].left), (1, 0), "the page left is told, once")

    def test_the_start_up_walk_ends_on_the_lock_page_then_makes_the_waiting_request(self):
        sh, shown = self.sh, []
        sh.warming, sh.wanted = True, "home"
        sh.show_page = lambda name, how=None: shown.append(name)
        sh.warm()
        step, _args = self.timers[0]
        while step():
            pass
        self.assertEqual(
            shown[-2:], ["lock", "home"], "the walk ends on the true page, then the waiting request is made"
        )
        self.assertFalse(sh.warming)
        self.assertEqual(sh.page, "home")
        self.assertNotIn("SLIDE", " ".join(map(str, shown)))

    def test_ready_needs_a_frame(self):
        sh = self.sh
        sh.warming, sh.wanted = True, None
        sh.show_page = lambda *a: None
        sh.warm()
        step, _args = self.timers[0]
        while step():
            pass
        self.assertFalse(sh.said_ready, "mapped and warm, but no frame has been drawn")
        sh.ready.mark("painted")
        self.assertTrue(sh.said_ready)

    def test_an_alarm_brings_the_clock_forward_once(self):
        sh = self.sh
        sh.show_page = lambda *a: None
        ringing = {"ringing": {"id": "a1"}, "clock_ok": True}
        sh.clock_seen(1, ringing)
        self.assertEqual(sh.page, "clock")
        sh.go("home")
        sh.clock_seen(2, ringing)
        self.assertEqual(sh.page, "home", "the owner left; the next poll does not drag them back")

    def test_a_shell_with_no_clock_page_still_knows_the_clock(self):
        sh = self.sh
        del sh.pages["clock"]
        self.assertTrue(sh.clock_known())
        sh.snap = {"battery": {"clock_ok": False}}
        self.assertFalse(sh.clock_known())

    def test_the_server_says_whether_it_has_a_pin_lock(self):
        import urllib.error

        core = self.shell_module.Core("http://127.0.0.1:1")
        core.token = "t"
        asked = []

        def opener(result):
            def _open(path, headers=None):
                asked.append((path, headers))
                if isinstance(result, Exception):
                    raise result
                return result

            return _open

        core._open = opener(object())
        self.assertTrue(core.lock_on(), "/api/lock answers only where OPOS_LOCK=1")
        self.assertEqual(asked[0], ("/api/lock", {"X-OPOS-Token": "t"}))
        missing_body = io.BytesIO(b"missing")
        core._open = opener(urllib.error.HTTPError("u", 404, "not found", {}, missing_body))
        self.assertFalse(core.lock_on())
        self.assertTrue(missing_body.closed, "the handled missing-lock response is closed")
        broken_body = io.BytesIO(b"still available")
        core._open = opener(urllib.error.HTTPError("u", 500, "broken", {}, broken_body))
        with self.assertRaises(urllib.error.HTTPError) as raised:
            core.lock_on()  # unknown: the poll asks again rather than guessing
        self.assertFalse(broken_body.closed, "a rethrown response remains owned by the caller")
        with raised.exception:
            self.assertEqual(raised.exception.read(), b"still available")

    def test_a_handled_missing_lock_does_not_hide_a_close_failure(self):
        import urllib.error

        class RefusesClose(io.BytesIO):
            close_called = False

            def close(self):
                if not self.close_called:
                    self.close_called = True
                    raise OSError("close failed")
                super().close()

        core = self.shell_module.Core("http://127.0.0.1:1")
        core.token = "t"
        error = urllib.error.HTTPError("u", 404, "not found", {}, RefusesClose(b"missing"))
        core._open = lambda path, headers=None: (_ for _ in ()).throw(error)
        with self.assertRaisesRegex(OSError, "close failed"):
            core.lock_on()


if __name__ == "__main__":
    unittest.main()
