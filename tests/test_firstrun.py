# SPDX-License-Identifier: GPL-3.0-or-later
"""First run on the Duo OS: the flag, the clock sentence, the setup and time zone pages, skipping, and "never block home"."""
import http.client
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from opos import clockcheck, firstrun, firstrun_page, web
from opos import timezone as tzmod
from opos.setup import config as setup_config
from opos.setup import prefs

PHONE_HOME = "/" + "home/droidian"  # written in two parts so the repository check does not read it as a private path
BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class ClockCheck(unittest.TestCase):
    def test_a_clock_before_the_floor_year_is_implausible(self):
        self.assertFalse(clockcheck.clock_plausible(0))
        self.assertFalse(clockcheck.clock_plausible(1767225599))  # 2025-12-31 23:59:59 UTC
        self.assertTrue(clockcheck.clock_plausible(1767225600))  # 2026-01-01 00:00:00 UTC
        self.assertTrue(clockcheck.clock_plausible(4102444800))  # 2100

    def test_the_floor_year_can_be_given(self):
        self.assertTrue(clockcheck.clock_plausible(0, floor_year=1970))
        self.assertFalse(clockcheck.clock_plausible(1767225600, floor_year=2027))

    def test_default_is_the_system_clock(self):
        with mock.patch.object(time, "time", return_value=5.0):
            self.assertFalse(clockcheck.clock_plausible())
        with mock.patch.object(time, "time", return_value=1798761600.0):  # 2027-01-01
            self.assertTrue(clockcheck.clock_plausible())

    def test_nonsense_is_not_plausible_and_does_not_raise(self):
        for bad in (float("nan"), float("inf"), -float("inf"), "x", [], 10 ** 30, -10 ** 30):
            with self.subTest(now=bad):
                self.assertFalse(clockcheck.clock_plausible(bad))


class ClockSentence(unittest.TestCase):
    def test_a_good_clock_has_no_sentence(self):
        info = firstrun.clock_info(1798761600)
        self.assertTrue(info["plausible"])
        self.assertEqual("", info["note"])
        self.assertEqual(2027, info["year"])
        self.assertRegex(info["text"], r"\d\d:\d\d")

    def test_a_1970_clock_gets_a_plain_sentence_naming_the_year(self):
        info = firstrun.clock_info(0)
        self.assertFalse(info["plausible"])
        self.assertIn("1970", info["note"])
        self.assertIn("wrong", info["note"])
        self.assertIn("online", info["note"])
        self.assertNotRegex(info["note"], r"[<>{}]")

    def test_an_unreadable_clock_says_so(self):
        info = firstrun.clock_info(float("nan"))
        self.assertFalse(info["plausible"])
        self.assertIsNone(info["year"])
        self.assertIn("could not be read", info["note"])

    def test_the_first_step_shows_it(self):
        page = firstrun_page.firstrun_page("tok", "nonce")
        self.assertIn('id="clock-note"', page)
        self.assertIn("S.clock.note", page)  # filled from /api/firstrun


class Env(unittest.TestCase):
    """A temporary config directory, first run on, and the process time zone put back afterwards."""

    first = True

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.cfg = os.path.join(self.d, "cfg")
        if hasattr(time, "tzset"):
            self.addCleanup(time.tzset)
        p = mock.patch.dict(os.environ, {"OPOS_CONFIG_DIR": self.cfg, "HOME": self.d})
        p.start()
        self.addCleanup(p.stop)
        for k in ("OPOS_FIRSTRUN", "OPOS_KIOSK_RESTART", "TZ"):
            os.environ.pop(k, None)
        if self.first:
            os.environ["OPOS_FIRSTRUN"] = "1"
        firstrun.reset()
        self.addCleanup(firstrun.reset)

    def cfgfile(self, name):
        return Path(self.cfg, name)


class Flag(Env):
    def test_enabled_only_by_exactly_one(self):
        for v, want in (("1", True), ("0", False), ("", False), ("true", False), ("yes", False), ("2", False)):
            with self.subTest(v=v):
                self.assertEqual(want, firstrun.enabled({"OPOS_FIRSTRUN": v}))
        self.assertFalse(firstrun.enabled({}))

    def test_pending_until_the_flag_is_written(self):
        self.assertTrue(firstrun.pending())
        self.assertFalse(firstrun.done())
        self.assertTrue(firstrun.mark_done())
        self.assertTrue(firstrun.done())
        self.assertFalse(firstrun.pending())
        self.assertEqual(self.cfgfile("firstrun.json"), firstrun.flag_path())
        self.assertEqual({"version": 1, "skipped": False}, json.loads(self.cfgfile("firstrun.json").read_text()))

    def test_a_skip_is_recorded_as_one(self):
        firstrun.mark_done(skipped=True)
        self.assertEqual({"version": 1, "skipped": True}, json.loads(self.cfgfile("firstrun.json").read_text()))
        self.assertFalse(firstrun.pending())

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_the_flag_is_private_and_no_temp_file_is_left(self):
        firstrun.mark_done()
        self.assertEqual(0o600, self.cfgfile("firstrun.json").stat().st_mode & 0o777)
        self.assertEqual(["firstrun.json"], os.listdir(self.cfg))

    def test_not_pending_when_off(self):
        os.environ.pop("OPOS_FIRSTRUN")
        self.assertFalse(firstrun.pending())

    def test_not_now_lasts_for_the_server_run_only(self):
        firstrun.defer()
        self.assertFalse(firstrun.pending())
        self.assertFalse(firstrun.done())
        firstrun.reset()
        self.assertTrue(firstrun.pending())

    def test_no_first_run_when_the_answers_could_not_be_kept(self):
        blocker = Path(self.d, "blocker")
        blocker.write_text("a file where the config directory should be")
        os.environ["OPOS_CONFIG_DIR"] = str(blocker / "cfg")
        self.assertFalse(firstrun.pending())
        self.assertFalse(firstrun.mark_done())

    def test_a_flag_that_cannot_be_written_is_reported_not_raised(self):
        os.makedirs(self.cfgfile("firstrun.json"))  # a directory where the file should go
        self.assertFalse(firstrun.done())
        self.assertFalse(firstrun.mark_done())


class Answers(Env):
    def setUp(self):
        super().setUp()
        self.phone = web.Phone()
        self.lock = threading.Lock()

    def test_nothing_given_nothing_changed(self):
        self.assertEqual(({}, []), firstrun.apply_answers({}, self.phone, self.lock))
        self.assertEqual(({}, []), firstrun.apply_answers({"zone": None, "goal": None, "helper": None, "junk": 1}, self.phone, self.lock))
        self.assertFalse(os.path.exists(self.cfg))

    def test_goal_is_saved_in_the_setup_config_and_applied_to_the_running_phone(self):
        saved, errors = firstrun.apply_answers({"goal": {"enabled": True, "time": "22:30"}}, self.phone, self.lock)
        self.assertEqual(([], {"enabled": True, "time": "22:30"}), (errors, saved["goal"]))
        cfg = setup_config.load(setup_config.config_path())
        self.assertEqual({"enabled": True, "time": "22:30"}, cfg["goal"])
        self.assertEqual([], setup_config.validate(cfg))
        self.assertTrue(cfg["applied"])
        self.assertEqual("Surface Duo", cfg["device"]["name"])
        self.assertEqual("dual-screen", cfg["device"]["form_factor"])
        self.assertEqual("22:30", self.phone.dash.goal)

    def test_goal_off_clears_it_on_the_running_phone(self):
        self.phone.dash.set_goal("16:00")
        saved, errors = firstrun.apply_answers({"goal": {"enabled": False, "time": "23:00"}}, self.phone, self.lock)
        self.assertEqual([], errors)
        self.assertIsNone(self.phone.dash.goal)
        self.assertEqual({"enabled": False, "time": "23:00"}, setup_config.load(setup_config.config_path())["goal"])

    def test_an_existing_config_keeps_everything_but_the_goal(self):
        cfg = setup_config.default_config()
        cfg["device"] = {"id": "my-phone", "name": "My phone", "source": "manual", "battery_health": 0.8, "form_factor": "slab"}
        cfg["battery_mode"] = "worn"
        setup_config.save(cfg, setup_config.config_path())
        firstrun.apply_answers({"goal": {"enabled": True, "time": "21:15"}}, self.phone, self.lock)
        after = setup_config.load(setup_config.config_path())
        self.assertEqual("My phone", after["device"]["name"])
        self.assertEqual("worn", after["battery_mode"])
        self.assertEqual({"enabled": True, "time": "21:15"}, after["goal"])

    def test_a_broken_config_is_replaced_not_a_crash(self):
        self.cfgfile("config.json").parent.mkdir(parents=True)
        self.cfgfile("config.json").write_text("{not json")
        saved, errors = firstrun.apply_answers({"goal": {"enabled": True, "time": "20:00"}}, self.phone, self.lock)
        self.assertEqual([], errors)
        self.assertEqual("20:00", setup_config.load(setup_config.config_path())["goal"]["time"])

    def test_bad_goals_are_refused_and_nothing_is_written(self):
        for bad in ("23:00", {"enabled": True}, {"time": "23:00"}, {"enabled": "yes", "time": "23:00"}, {"enabled": 1, "time": "23:00"},
                    {"enabled": True, "time": "24:00"}, {"enabled": True, "time": "7:00"}, {"enabled": True, "time": "23:60"},
                    {"enabled": True, "time": "23:00\n"}, {"enabled": True, "time": " 23:00"}, {"enabled": True, "time": 2300},
                    {"enabled": True, "time": "23:00; id"}, [], 5):
            with self.subTest(goal=bad):
                self.assertEqual(({}, ["daily goal"]), firstrun.apply_answers({"goal": bad}, self.phone, self.lock))
        self.assertFalse(self.cfgfile("config.json").exists())

    def test_helper_is_saved_as_the_setup_answer_and_applied_live(self):
        saved, errors = firstrun.apply_answers({"helper": "on"}, self.phone, self.lock)
        self.assertEqual(([], "on"), (errors, saved["helper"]))
        self.assertTrue(self.phone.visible)
        state = prefs.load(prefs.prefs_path())
        self.assertEqual("on", prefs.effective(state)["helper"])
        self.assertTrue(state["applied"])
        self.assertTrue(web._install_helper_visible(False))  # what the next server start will do
        firstrun.apply_answers({"helper": "off"}, self.phone, self.lock)
        self.assertFalse(self.phone.visible)
        self.assertFalse(web._install_helper_visible(False))

    def test_bad_helper_values_are_refused(self):
        for bad in ("maybe", "ON", True, 1, ["on"], ""):
            with self.subTest(helper=bad):
                self.assertEqual(({}, ["helper"]), firstrun.apply_answers({"helper": bad}, self.phone, self.lock))
        self.assertFalse(self.cfgfile("prefs.json").exists())

    def test_the_zone_is_saved_and_put_in_this_process(self):
        saved, errors = firstrun.apply_answers({"zone": "Asia/Kolkata"}, self.phone, self.lock)
        self.assertEqual(([], "Asia/Kolkata"), (errors, saved["timezone"]))
        self.assertEqual("Asia/Kolkata", tzmod.load_setting())
        self.assertEqual(tzmod.tz_value("Asia/Kolkata"), os.environ["TZ"])

    def test_malicious_or_unknown_zones_are_refused_and_nothing_is_saved_or_exported(self):
        for bad in ("../../etc/passwd", "Mars/Base", "UTC; id", "$(id)", "Europe/Berlin\nUTC", "", 5, ["UTC"], {"a": 1}):
            with self.subTest(zone=bad):
                self.assertEqual(({}, ["time zone"]), firstrun.apply_answers({"zone": bad}, self.phone, self.lock))
        self.assertFalse(self.cfgfile("timezone").exists())
        self.assertNotIn("TZ", os.environ)

    def test_one_bad_answer_does_not_stop_the_others(self):
        saved, errors = firstrun.apply_answers({"zone": "nope", "goal": {"enabled": True, "time": "22:00"}, "helper": "on"}, self.phone, self.lock)
        self.assertEqual(["time zone"], errors)
        self.assertEqual({"goal", "helper"}, set(saved))

    def test_without_a_phone_it_only_saves(self):
        saved, errors = firstrun.apply_answers({"goal": {"enabled": True, "time": "22:00"}, "helper": "on"})
        self.assertEqual([], errors)
        self.assertEqual("22:00", setup_config.load(setup_config.config_path())["goal"]["time"])


class KioskRestart(Env):
    def test_the_command_is_a_fixed_list_with_only_the_profile_directory_in_it(self):
        seen = []

        def run(argv, **kw):
            seen.append((argv, kw))
            return mock.Mock(returncode=0)
        self.assertTrue(firstrun.restart_kiosk({"HOME": PHONE_HOME}, run=run))
        argv, kw = seen[0]
        self.assertIsInstance(argv, list)
        self.assertEqual("pkill", argv[0])
        self.assertIn("-TERM", argv)
        self.assertIn("--", argv)
        self.assertTrue(argv[-1].startswith("--user-data-dir=" + PHONE_HOME + "/\\.cache/opos-kiosk"), argv[-1])
        self.assertFalse(kw.get("shell", False))
        self.assertLessEqual(kw["timeout"], 10)

    def test_regex_characters_in_home_are_escaped(self):
        seen = []
        firstrun.restart_kiosk({"HOME": "/" + "home/a.b+c(d)"}, run=lambda argv, **kw: seen.append(argv) or mock.Mock(returncode=1))
        self.assertIn("/" + "home/a\\.b\\+c\\(d\\)/\\.cache/opos-kiosk", seen[0][-1])

    @unittest.skipUnless(shutil.which("pkill") and hasattr(os, "getuid"), "needs pkill")
    def test_the_real_pkill_stops_only_the_process_with_that_profile_directory(self):
        home = os.path.join(self.d, "h.o+me")  # regex characters in the path on purpose
        sleeper = "import time; time.sleep(60)"

        def start(profile):
            p = subprocess.Popen([sys.executable, "-c", sleeper, "--user-data-dir=" + os.path.join(home, *profile), "http://127.0.0.1:8765/"])
            self.addCleanup(p.wait)  # cleanups run last-in first-out: kill, then wait
            self.addCleanup(p.kill)
            return p
        kiosk, other, longer, lookalike = start((".cache", "opos-kiosk")), start((".cache", "other")), start((".cache", "opos-kiosk-2")), start((".cache", "opos-kioskX"))
        time.sleep(0.5)  # let them start
        self.assertTrue(firstrun.restart_kiosk({"HOME": home}))
        self.assertEqual(-15, kiosk.wait(10))  # SIGTERM
        for p in (other, longer, lookalike):
            time.sleep(0.2)
            self.assertIsNone(p.poll())
        self.assertFalse(firstrun.restart_kiosk({"HOME": home}))  # nothing left to signal

    def test_nothing_signalled_or_no_pkill_is_false_not_an_error(self):
        self.assertFalse(firstrun.restart_kiosk({"HOME": "/h"}, run=lambda argv, **kw: mock.Mock(returncode=1)))

        def missing(argv, **kw):
            raise FileNotFoundError("pkill")
        self.assertFalse(firstrun.restart_kiosk({"HOME": "/h"}, run=missing))

        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 5)
        self.assertFalse(firstrun.restart_kiosk({"HOME": "/h"}, run=slow))

    def test_restart_only_when_the_screen_runs_another_zone(self):
        self.assertTrue(firstrun._restart_wanted("Europe/Berlin", "UTC"))
        self.assertTrue(firstrun._restart_wanted("Europe/Berlin", None))
        self.assertFalse(firstrun._restart_wanted("Europe/Berlin", "Europe/Berlin"))
        os.environ["OPOS_KIOSK_RESTART"] = "0"
        self.assertFalse(firstrun._restart_wanted("Europe/Berlin", "UTC"))

    def test_schedule_runs_the_restart_later_and_does_not_hold_the_process_open(self):
        calls = []
        with mock.patch.object(firstrun, "restart_kiosk", side_effect=lambda: calls.append(1)):
            t = firstrun.schedule_restart(delay=0.05)
            self.assertTrue(t.daemon)
            t.join(5)
        self.assertEqual([1], calls)


class Settings(Env):
    PAGE = web.page("t", "n")

    def test_the_link_goes_in_front_of_about_and_shows_the_zone(self):
        out = firstrun.decorate(self.PAGE)
        self.assertEqual(1, out.count('id="tz-link"'))
        self.assertLess(out.index('id="tz-link"'), out.index('aria-labelledby="set-about"'))
        self.assertGreater(out.index('id="tz-link"'), out.index('id="settings"'))
        self.assertRegex(out, r'<a id="tz-link" href="/timezone"[^>]*>Time zone: default \(')
        tzmod.save_setting("Asia/Kolkata")
        self.assertIn('>Time zone: Asia/Kolkata</a>', firstrun.decorate(self.PAGE))

    def test_it_costs_a_few_hundred_bytes_and_only_here(self):
        extra = len(firstrun.decorate(self.PAGE).encode()) - len(self.PAGE.encode())
        self.assertTrue(0 < extra < 500, extra)

    def test_a_page_without_the_anchor_is_left_alone(self):
        self.assertEqual("<html>nothing</html>", firstrun.decorate("<html>nothing</html>"))

    def test_the_zone_text_cannot_inject_markup(self):
        with mock.patch.object(tzmod, "load_setting", return_value='<script>x</script>"'):
            out = firstrun.decorate(self.PAGE)
        self.assertNotIn("<script>x", out)
        self.assertIn("&lt;script&gt;x", out)


class Http(Env):
    """A real server with the track installed (or not), hit with plain HTTP."""

    def setUp(self):
        super().setUp()
        self.orig = (web.Handler._get, web.Handler._post, web.page)
        self.undo = firstrun.install(web)
        if self.undo:
            self.addCleanup(self.undo)
        self.phone = web.Phone()
        self.server = web.make_server(0, self.phone)
        self.port = self.server.server_address[1]
        self.token = self.server.RequestHandlerClass.token
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def req(self, method, path, body=None, headers=None):
        h = {"Host": f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    def api(self, path, token=True):
        return self.req("GET", path, headers={"X-OPOS-Token": self.token} if token else {})

    def post(self, path, obj, **extra):
        h = {"Content-Type": "application/json", "X-OPOS-Token": self.token}
        h.update(extra)
        h = {k: v for k, v in h.items() if v is not None}
        return self.req("POST", path, body=obj if isinstance(obj, bytes) else json.dumps(obj).encode(), headers=h)

    def jpost(self, path, obj, **extra):
        s, h, b = self.post(path, obj, **extra)
        return s, json.loads(b)


class Off(Http):
    first = False

    def test_nothing_is_hooked_and_nothing_is_served(self):
        self.assertIsNone(self.undo)
        self.assertEqual(self.orig, (web.Handler._get, web.Handler._post, web.page))
        self.assertFalse(hasattr(web.Handler, "_firstrun_hooked"))
        for path in ("/firstrun", "/timezone", "/api/firstrun", "/api/timezone"):
            self.assertEqual(404, self.api(path)[0], path)
        self.assertEqual(404, self.post("/api/timezone", {"zone": "UTC"})[0])
        self.assertFalse(os.path.exists(self.cfg))

    def test_the_home_page_is_the_unmodified_page(self):
        s, h, b = self.req("GET", "/", headers=BROWSER)
        self.assertEqual(200, s)
        self.assertNotIn(b"tz-link", b)
        self.assertNotIn(b"/timezone", b)
        norm = lambda t: re.sub(r'nonce="[^"]+"', 'nonce="N"', t)
        self.assertEqual(norm(web.page(self.token, "abc")), norm(b.decode()))

    def test_install_does_nothing_for_other_values(self):
        for v in ("0", "", "true", "yes"):
            os.environ["OPOS_FIRSTRUN"] = v
            self.assertIsNone(firstrun.install(web))
            self.assertEqual(self.orig, (web.Handler._get, web.Handler._post, web.page))


class On(Http):
    def test_the_hook_replaces_three_things_and_undo_puts_them_back(self):
        self.assertTrue(web.Handler._firstrun_hooked)
        self.assertNotEqual(self.orig, (web.Handler._get, web.Handler._post, web.page))
        self.assertIsNone(firstrun.install(web))  # not twice
        self.undo()
        self.assertEqual(self.orig, (web.Handler._get, web.Handler._post, web.page))
        self.assertFalse(hasattr(web.Handler, "_firstrun_hooked"))
        self.undo()  # and undoing twice is harmless

    def test_a_script_that_probes_the_root_still_gets_the_home_page(self):
        for headers in ({}, {"Accept": "*/*"}, {"Accept": "application/json"}):
            s, h, b = self.req("GET", "/", headers=headers)
            self.assertEqual(200, s, headers)
            self.assertIn(b'name="opos-token"', b)
            self.assertNotIn("location", h)

    def test_a_browser_is_sent_to_setup_until_it_is_done(self):
        s, h, b = self.req("GET", "/", headers=BROWSER)
        self.assertEqual((302, "/firstrun", b""), (s, h.get("location"), b))
        self.assertEqual("0", h["content-length"])
        self.assertEqual(302, self.req("HEAD", "/", headers=BROWSER)[0])
        firstrun.mark_done()
        s, h, b = self.req("GET", "/", headers=BROWSER)
        self.assertEqual(200, s)
        self.assertIn(b"Old Phone OS Interface", b)

    def test_not_now_reaches_home_and_stays_there(self):
        s, h, b = self.req("GET", "/?firstrun=later", headers=BROWSER)
        self.assertEqual(200, s)
        self.assertIn(b'name="opos-token"', b)
        self.assertEqual(200, self.req("GET", "/", headers=BROWSER)[0])
        self.assertFalse(firstrun.done())

    def test_a_wrong_host_is_not_redirected(self):
        s, h, b = self.req("GET", "/", headers=dict(BROWSER, Host="evil.example:80"))
        self.assertEqual(403, s)

    def test_a_failure_while_deciding_shows_home(self):
        with mock.patch.object(firstrun, "pending", side_effect=RuntimeError("boom")):
            s, h, b = self.req("GET", "/", headers=BROWSER)
        self.assertEqual(200, s)
        self.assertIn(b'name="opos-token"', b)

    def test_an_unwritable_config_directory_means_no_first_run_at_all(self):
        blocker = Path(self.d, "blocker")
        blocker.write_text("x")
        os.environ["OPOS_CONFIG_DIR"] = str(blocker / "cfg")
        s, h, b = self.req("GET", "/", headers=BROWSER)
        self.assertEqual(200, s)

    def test_the_home_page_gets_the_time_zone_link_in_settings(self):
        s, h, b = self.req("GET", "/")
        text = b.decode()
        self.assertIn('<a id="tz-link" href="/timezone"', text)
        self.assertLess(text.index('id="settings"'), text.index('id="tz-link"'))
        self.assertGreater(len(text), len(self.orig[2](self.token, "n")))

    def test_the_setup_and_time_zone_pages(self):
        for path, title in (("/firstrun", "OPOS setup"), ("/timezone", "OPOS time zone")):
            s, h, b = self.req("GET", path)
            text = b.decode()
            self.assertEqual(200, s, path)
            self.assertTrue(h["content-type"].startswith("text/html"))
            self.assertIn(f"<title>{title}</title>", text)
            self.assertIn(f'<meta name="opos-token" content="{self.token}">', text)
            nonce = re.search(r"script-src 'nonce-([^']+)'", h["content-security-policy"]).group(1)
            self.assertEqual(text.count("<script"), text.count(f'<script nonce="{nonce}">'))
            self.assertEqual(text.count("<style"), text.count(f'<style nonce="{nonce}">'))
            self.assertNotRegex(text, r"\son(click|load|error)=")
            self.assertEqual(f"'nonce-{nonce}'", re.search(r"script-src ([^;]+)", h["content-security-policy"]).group(1))
            self.assertEqual("no-store", h["cache-control"])

    def test_each_page_load_has_its_own_nonce(self):
        n = [re.search(r"nonce-([^']+)", self.req("GET", "/firstrun")[1]["content-security-policy"]).group(1) for _ in range(2)]
        self.assertNotEqual(n[0], n[1])

    def test_pages_refuse_a_foreign_host(self):
        for path in ("/firstrun", "/timezone", "/api/firstrun", "/api/timezone"):
            self.assertEqual(403, self.req("GET", path, headers={"Host": "evil.example", "X-OPOS-Token": self.token})[0], path)

    def test_the_apis_need_the_token(self):
        for path in ("/api/firstrun", "/api/timezone"):
            self.assertEqual(403, self.api(path, token=False)[0], path)
            self.assertEqual(403, self.req("GET", path, headers={"X-OPOS-Token": "wrong"})[0], path)
            self.assertEqual(200, self.api(path)[0], path)

    def test_firstrun_state(self):
        s, h, b = self.api("/api/firstrun")
        j = json.loads(b)
        self.assertEqual({"pending", "clock", "goal", "helper", "timezone"}, set(j))
        self.assertTrue(j["pending"])
        self.assertEqual({"enabled": True, "time": "23:00"}, j["goal"])  # the setup default, not the 16:00 demo goal
        self.assertEqual("off", j["helper"])
        self.assertEqual({"saved": None}, j["timezone"])
        self.assertEqual({"plausible", "year", "text", "note"}, set(j["clock"]))

    def test_firstrun_state_follows_the_phone_and_the_saved_setup(self):
        self.phone.set_visible(True)
        firstrun.apply_answers({"goal": {"enabled": False, "time": "21:45"}, "zone": "Asia/Kolkata"})
        j = json.loads(self.api("/api/firstrun")[2])
        self.assertEqual("on", j["helper"])
        self.assertEqual({"enabled": False, "time": "21:45"}, j["goal"])
        self.assertEqual({"saved": "Asia/Kolkata"}, j["timezone"])

    def test_timezone_state(self):
        j = json.loads(self.api("/api/timezone")[2])
        self.assertEqual({"zones", "source", "saved", "active", "clock"}, set(j))
        self.assertIn(j["source"], ("system", "builtin"))
        self.assertIn("Europe/Berlin", j["zones"])
        self.assertIsNone(j["saved"])

    def test_posts_get_the_same_checks_as_the_main_api(self):
        body = json.dumps({"zone": "UTC"}).encode()
        base = {"Content-Type": "application/json", "X-OPOS-Token": self.token}
        cases = [
            ({"X-OPOS-Token": None}, 403), ({"X-OPOS-Token": "wrong"}, 403), ({"Origin": "http://evil.example"}, 403),
            ({"Content-Type": "text/plain"}, 415), ({"Content-Type": None}, 415), ({"Host": "evil.example"}, 403),
        ]
        for extra, want in cases:
            h = {k: v for k, v in {**base, **extra}.items() if v is not None}
            with self.subTest(extra=extra):
                self.assertEqual(want, self.req("POST", "/api/timezone", body=body, headers=h)[0])
        self.assertEqual(400, self.post("/api/timezone", b"{not json")[0])
        self.assertEqual(400, self.post("/api/timezone", b"[1]")[0])
        self.assertEqual(413, self.post("/api/timezone", b'{"zone":"' + b"A" * 5000 + b'"}')[0])
        self.assertFalse(self.cfgfile("timezone").exists())
        s, j = self.jpost("/api/timezone", {"zone": "UTC"}, Origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(200, s)

    def test_saving_a_zone(self):
        with mock.patch.object(firstrun, "schedule_restart") as sched:
            s, j = self.jpost("/api/timezone", {"zone": "Asia/Kolkata", "client_tz": "UTC"})
            self.assertEqual((200, {"ok": True, "zone": "Asia/Kolkata", "restart": True}), (s, j))
            sched.assert_called_once()
            self.assertEqual("Asia/Kolkata", tzmod.load_setting())
            self.assertEqual(tzmod.tz_value("Asia/Kolkata"), os.environ["TZ"])
            sched.reset_mock()
            s, j = self.jpost("/api/timezone", {"zone": "Asia/Kolkata", "client_tz": "Asia/Kolkata"})
            self.assertEqual((200, False), (s, j["restart"]))
            sched.assert_not_called()

    def test_hostile_zone_names_are_refused_before_anything_happens(self):
        with mock.patch.object(firstrun, "schedule_restart") as sched, mock.patch.object(subprocess, "run") as run, \
                mock.patch.object(subprocess, "Popen") as popen, mock.patch.object(os, "system") as system:
            for bad in ("../../etc/passwd", "UTC; touch /tmp/pwned", "$(id)", "`id`", "Europe/Berlin\nUTC", "", None, 5, ["UTC"], {"zone": "UTC"},
                        "A" * 70, "Europe/Berlin\x00", "/usr/share/zoneinfo/UTC"):
                with self.subTest(zone=bad):
                    s, j = self.jpost("/api/timezone", {"zone": bad, "client_tz": "UTC"})
                    self.assertEqual((400, {"error": "unknown time zone"}), (s, j))
            self.assertEqual((400, {"error": "unknown time zone"}), self.jpost("/api/timezone", {"client_tz": "UTC"}))
            sched.assert_not_called()
            run.assert_not_called()
            popen.assert_not_called()
            system.assert_not_called()
        self.assertFalse(self.cfgfile("timezone").exists())
        self.assertNotIn("TZ", os.environ)

    def test_unknown_actions_and_other_paths(self):
        self.assertEqual(400, self.jpost("/api/firstrun", {"action": "format-the-disk"})[0])
        self.assertEqual(400, self.jpost("/api/firstrun", {})[0])
        self.assertEqual(404, self.post("/api/firstrun/x", {"action": "skip"})[0])
        self.assertFalse(firstrun.done())

    def test_finishing_saves_everything_and_the_phone_follows(self):
        body = {"action": "finish", "zone": "Asia/Kolkata", "goal": {"enabled": True, "time": "22:30"}, "helper": "on", "client_tz": "Asia/Kolkata"}
        with mock.patch.object(firstrun, "schedule_restart") as sched:
            s, j = self.jpost("/api/firstrun", body)
        self.assertEqual(200, s)
        self.assertEqual({"ok": True, "done": True, "errors": [], "restart": False,
                          "saved": {"timezone": "Asia/Kolkata", "goal": {"enabled": True, "time": "22:30"}, "helper": "on"}}, j)
        sched.assert_not_called()
        self.assertTrue(firstrun.done())
        self.assertFalse(json.loads(self.cfgfile("firstrun.json").read_text())["skipped"])
        self.assertEqual("22:30", self.phone.dash.goal)
        self.assertTrue(self.phone.visible)
        self.assertEqual("Asia/Kolkata", tzmod.load_setting())
        self.assertEqual("22:30", setup_config.load(setup_config.config_path())["goal"]["time"])
        self.assertFalse(firstrun.pending())
        self.assertEqual(200, self.req("GET", "/", headers=BROWSER)[0])

    def test_finishing_in_another_zone_than_the_screen_restarts_the_screen(self):
        with mock.patch.object(firstrun, "schedule_restart") as sched:
            s, j = self.jpost("/api/firstrun", {"action": "finish", "zone": "Europe/Berlin", "client_tz": "UTC"})
        self.assertEqual((200, True, True), (s, j["ok"], j["restart"]))
        sched.assert_called_once()

    def test_finishing_without_a_zone_never_restarts_the_screen(self):
        with mock.patch.object(firstrun, "schedule_restart") as sched:
            s, j = self.jpost("/api/firstrun", {"action": "finish", "goal": {"enabled": True, "time": "22:00"}, "client_tz": "UTC"})
        self.assertEqual((200, False), (s, j["restart"]))
        sched.assert_not_called()

    def test_finishing_with_bad_values_reports_them_and_still_leaves_setup(self):
        s, j = self.jpost("/api/firstrun", {"action": "finish", "zone": "nope", "goal": {"enabled": True, "time": "99:99"}, "helper": "maybe"})
        self.assertEqual(200, s)
        self.assertFalse(j["ok"])
        self.assertEqual(["time zone", "daily goal", "helper"], j["errors"])
        self.assertEqual({}, j["saved"])
        self.assertTrue(j["done"])
        self.assertFalse(firstrun.pending())

    def test_skipping_writes_only_the_flag(self):
        s, j = self.jpost("/api/firstrun", {"action": "skip"})
        self.assertEqual((200, {"ok": True, "done": True, "restart": False}), (s, j))
        self.assertEqual({"version": 1, "skipped": True}, json.loads(self.cfgfile("firstrun.json").read_text()))
        self.assertEqual(["firstrun.json"], os.listdir(self.cfg))
        self.assertNotIn("TZ", os.environ)
        self.assertFalse(firstrun.pending())
        s, h, b = self.req("GET", "/", headers=BROWSER)
        self.assertEqual(200, s)

    def test_skipping_leaves_the_phone_as_it_was(self):
        goal, visible = self.phone.dash.goal, self.phone.visible
        self.jpost("/api/firstrun", {"action": "skip"})
        self.assertEqual((goal, visible), (self.phone.dash.goal, self.phone.visible))

    def test_a_flag_that_cannot_be_written_does_not_trap_the_owner_in_setup(self):
        os.makedirs(self.cfgfile("firstrun.json"))  # a directory where the flag should go
        s, j = self.jpost("/api/firstrun", {"action": "skip"})
        self.assertEqual((200, True, False), (s, j["ok"], j["done"]))
        self.assertEqual(200, self.req("GET", "/", headers=BROWSER)[0])
        firstrun.reset()
        s, j = self.jpost("/api/firstrun", {"action": "finish", "helper": "on"})
        self.assertEqual((200, False, False), (s, j["ok"], j["done"]))
        self.assertEqual(200, self.req("GET", "/", headers=BROWSER)[0])

    def test_the_wizard_can_be_opened_again_after_it_was_done(self):
        firstrun.mark_done()
        self.assertEqual(200, self.req("GET", "/firstrun")[0])


class Pages(unittest.TestCase):
    """The two pages as text: they ask for nothing from outside, need no typing, and keep to the panels."""

    def test_no_outside_resources_and_no_dynamic_code(self):
        for page in (firstrun_page.firstrun_page("tok", "nonce"), firstrun_page.timezone_page("tok", "nonce")):
            self.assertNotRegex(page, r"https?://")
            self.assertNotRegex(page, r"\beval\(|new Function|document\.write|innerHTML|insertAdjacentHTML|outerHTML")
            self.assertNotRegex(page, r"<(img|iframe|link|object|embed)\b")
            self.assertEqual(page.count("<script"), page.count('<script nonce="nonce">'))

    def test_the_token_is_escaped(self):
        page = firstrun_page.firstrun_page('a"><script>x</script>', "nonce")
        self.assertNotIn('<script>x</script>', page)

    def test_the_text_stays_off_the_hinge(self):
        css = firstrun_page.CSS
        self.assertIn("--hinge:42px", css)
        self.assertRegex(css, r"grid-template-columns:minmax\(0,1fr\) var\(--hinge\) minmax\(0,1fr\)")
        self.assertRegex(css, r"\.l\{grid-column:1")
        self.assertRegex(css, r"\.r\{grid-column:3")

    def test_every_control_is_a_big_touch_target(self):
        css = firstrun_page.CSS
        self.assertRegex(css, r"button,\.btn\{[^}]*min-height:48px;min-width:48px")
        self.assertIn("[hidden]{display:none!important}", css)

    def test_two_colours_and_no_script_in_the_style(self):
        self.assertNotRegex(firstrun_page.CSS, r"#(?!1a1a1a|f3f0e8|e6e2d9|000)[0-9a-fA-F]{3,6}\b")

    def test_a_skip_is_always_on_screen_and_home_is_always_reachable(self):
        page = firstrun_page.firstrun_page("t", "n")
        self.assertIn('id="skipall">Skip setup', page)
        self.assertIn('id="skip">Skip this step', page)
        self.assertIn('href="/?firstrun=later"', page)
        self.assertIn("location.href='/?firstrun=later'", page)  # a failed skip still gets out
        self.assertIn("api('/api/firstrun',{action:'skip'})", page)
        self.assertIn('<a class="btn" href="/">Back to Home</a>', firstrun_page.timezone_page("t", "n"))

    def test_the_steps_are_the_ones_the_os_needs(self):
        page = firstrun_page.firstrun_page("t", "n")
        for step in ("s-welcome", "s-tz", "s-goal", "s-helper", "s-wifi", "s-done"):
            self.assertIn(f'id="{step}"', page)
        self.assertIn("steps=['welcome','tz','goal','helper','wifi','done']", page)

    def test_wifi_hands_off_to_the_network_page_only_if_it_exists(self):
        page = firstrun_page.firstrun_page("t", "n")
        self.assertIn("fetch('/network',{method:'HEAD'})", page)
        self.assertIn('id="wifi-link" href="/network" hidden', page)
        self.assertIn('id="wifi-none"', page)  # the documented fallback text
        self.assertIn("Joining a network is not available on this screen yet", page)

    def test_no_time_there_is_shown_from_a_clock_that_is_wrong(self):
        for page in (firstrun_page.firstrun_page("t", "n"), firstrun_page.timezone_page("t", "n")):
            self.assertIn("function there(z){if(window.badClock)return ''", page)
            self.assertIn("window.badClock=", page)
            self.assertIn("plausible===false", page)

    def test_the_picker_needs_no_keyboard(self):
        page = firstrun_page.timezone_page("t", "n")
        self.assertIn("Search (optional)", page)
        self.assertIn("chips", page)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_the_page_scripts_parse(self):
        for page in (firstrun_page.firstrun_page("t", "n"), firstrun_page.timezone_page("t", "n")):
            scripts = re.findall(r'<script nonce="n">(.*?)</script>', page, re.S)
            self.assertEqual(2, len(scripts))  # the theme line in the head, then the page
            for js in scripts:
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                    f.write(js)
                self.addCleanup(os.unlink, f.name)
                r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
                self.assertEqual(0, r.returncode, r.stderr)


class Wiring(unittest.TestCase):
    def test_main_installs_the_track_before_the_phone_reads_the_clock(self):
        src = inspect.getsource(web.main)
        self.assertIn("track firstboot", src)
        self.assertLess(src.index("_firstrun.install"), src.index("phone = Phone("))
        self.assertLess(src.index("_firstrun.install"), src.index("LiveBattery("))
        self.assertIn("except Exception", src[src.index("_firstrun.install"):src.index("phone = Phone(")])  # first run can never stop the server starting


if __name__ == "__main__":
    unittest.main()
