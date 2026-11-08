# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's time zone: the zone list, validation, the saved setting, TZ in a fresh process, the kiosk's export."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from opos import timezone as tzmod

ROOT = Path(__file__).resolve().parents[1]
REAL = os.path.isdir("/usr/share/zoneinfo") and os.path.isfile("/usr/share/zoneinfo/Europe/Berlin")
TZIF = b"TZif2" + bytes(40)
KIOSK = ROOT / "packaging/duo-os/opos-kiosk"


def make_tree(root, names, junk=()):
    for n in names:
        p = Path(root, n)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(TZIF)
    for n in junk:
        p = Path(root, n)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not zone data\n")


def py(code, *args, env=None, **kw):
    e = {k: v for k, v in os.environ.items() if k not in ("TZ", "OPOS_CONFIG_DIR", "OPOS_FIRSTRUN")}
    e.update(PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1", **(env or {}))
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, timeout=60, env=e, **kw)


class ZoneList(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_lists_zone_files_with_up_to_three_parts(self):
        make_tree(self.d, ["UTC", "Europe/Berlin", "Asia/Kolkata", "America/Argentina/Buenos_Aires"])
        self.assertEqual((["America/Argentina/Buenos_Aires", "Asia/Kolkata", "Europe/Berlin", "UTC"], "system"), tzmod.zones(self.d))

    def test_skips_copies_old_names_fixed_offsets_and_files_that_are_not_zone_data(self):
        make_tree(self.d, ["Europe/Berlin", "posix/Europe/Berlin", "right/Europe/Berlin", "Etc/GMT+5", "US/Pacific", "Brazil/East", "CET",
                           "localtime", "posixrules", "A/B/C/D"],
                  junk=["Europe/notes", "zone.tab", "tzdata.zi", "Europe/has space", "Europe/semi;colon", "Europe/$(id)"])
        self.assertEqual((["Europe/Berlin"], "system"), tzmod.zones(self.d))

    def test_a_symlinked_directory_is_not_followed(self):
        make_tree(self.d, ["Europe/Berlin"])
        try:
            os.symlink(self.d, os.path.join(self.d, "Europe", "loop"), target_is_directory=True)
            os.symlink("/etc/passwd", os.path.join(self.d, "Europe", "Passwd"))
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        self.assertEqual((["Europe/Berlin"], "system"), tzmod.zones(self.d))

    def test_the_scan_is_remembered_until_the_database_changes(self):
        make_tree(self.d, ["Europe/Berlin", "Asia/Kolkata"])
        with mock.patch.object(tzmod, "_scan", wraps=tzmod._scan) as scan:
            first = tzmod.zones(self.d)
            self.assertEqual((["Asia/Kolkata", "Europe/Berlin"], "system"), first)
            for _ in range(5):
                self.assertEqual(first, tzmod.zones(self.d))
                self.assertTrue(tzmod.valid_zone("Europe/Berlin", self.d))
            self.assertEqual(1, scan.call_count, "six asks, one scan")
            first[0].append("Mars/Olympus")  # a caller's changes to its copy are not remembered
            self.assertEqual((["Asia/Kolkata", "Europe/Berlin"], "system"), tzmod.zones(self.d))
            make_tree(self.d, ["Europe/Paris"])  # a new file in a region directory changes that directory's time
            os.utime(os.path.join(self.d, "Europe"), ns=(1, 2 * 10**18))
            self.assertEqual((["Asia/Kolkata", "Europe/Berlin", "Europe/Paris"], "system"), tzmod.zones(self.d))
            self.assertEqual(2, scan.call_count)
        self.assertEqual("builtin", tzmod.zones(os.path.join(self.d, "nope"))[1], "another root is its own entry")

    def test_a_missing_or_empty_database_falls_back_to_the_built_in_list(self):
        for root in (os.path.join(self.d, "nope"), self.d):
            names, source = tzmod.zones(root)
            self.assertEqual("builtin", source)
            self.assertEqual(sorted(tzmod.BUILTIN), names)
        self.assertIn("Europe/Berlin", names)
        self.assertIn("UTC", names)

    @unittest.skipUnless(REAL, "no system zone database")
    def test_the_system_database_gives_hundreds_of_zones(self):
        names, source = tzmod.zones()
        self.assertEqual("system", source)
        self.assertGreater(len(names), 300)
        for n in ("Europe/Berlin", "Asia/Kolkata", "America/Argentina/Buenos_Aires", "Pacific/Auckland", "UTC"):
            self.assertIn(n, names)
        self.assertEqual(names, sorted(set(names)))
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}", n) for n in names))


class ValidZone(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        make_tree(self.d, ["UTC", "Europe/Berlin", "America/Argentina/Buenos_Aires"])

    def test_a_listed_name_is_accepted_exactly(self):
        for n in ("UTC", "Europe/Berlin", "America/Argentina/Buenos_Aires"):
            self.assertEqual(n, tzmod.valid_zone(n, self.d))

    def test_everything_else_is_refused(self):
        bad = ["", " ", None, 7, b"UTC", ["UTC"], "europe/berlin", "EUROPE/BERLIN", "Europe/Berlin\n", " Europe/Berlin", "Europe/Berlin ",
               "Europe/Berlin\x00", "../etc/passwd", "Europe/../Berlin", "/etc/passwd", "/usr/share/zoneinfo/UTC", "Europe//Berlin",
               "Europe/Berlin/", "./Europe/Berlin", "Europe/Berlin;touch /tmp/x", "Europe/Berlin && id", "$(id)", "`id`", "Europe/$(id)",
               "Europe/Berlin|cat", "Europe/Berlın", "Europe/Berl١n", "UTC0", "CET-1CEST,M3.5.0,M10.5.0/3", "<+03>-3", ":Europe/Berlin",
               "Etc/UTC", "posix/UTC", "Asia/Kolkata", "A" * 65, "Europe/" + "B" * 100, "-h", "--help", "*", "Europe/*", "Europe/B[e]rlin"]
        for n in bad:
            with self.subTest(name=n):
                self.assertIsNone(tzmod.valid_zone(n, self.d))

    def test_the_built_in_names_are_valid_when_the_database_is_missing(self):
        nowhere = os.path.join(self.d, "nope")
        self.assertEqual("Europe/Berlin", tzmod.valid_zone("Europe/Berlin", nowhere))
        self.assertIsNone(tzmod.valid_zone("Mars/Olympus_Mons", nowhere))
        self.assertIsNone(tzmod.valid_zone("America/Argentina/Cordoba", nowhere))  # a real zone, but not in the short list

    def test_tz_value_is_the_name_from_the_database_and_the_rule_without_one(self):
        self.assertEqual("Europe/Berlin", tzmod.tz_value("Europe/Berlin", self.d))
        nowhere = os.path.join(self.d, "nope")
        self.assertEqual("CET-1CEST,M3.5.0,M10.5.0/3", tzmod.tz_value("Europe/Berlin", nowhere))
        with self.assertRaises(ValueError):
            tzmod.tz_value("../x", self.d)


@unittest.skipUnless(REAL and hasattr(time, "tzset"), "needs the system zone database and time.tzset")
class BuiltinRules(unittest.TestCase):
    def test_every_built_in_rule_gives_the_database_offsets_in_2026(self):
        import zoneinfo
        stamps = [int(dt.datetime(2026, m, 15, 12, tzinfo=dt.timezone.utc).timestamp()) for m in range(1, 13)]
        for y, m, d in ((2026, 3, 8), (2026, 3, 29), (2026, 4, 5), (2026, 9, 27), (2026, 10, 4), (2026, 10, 25), (2026, 11, 1)):  # around each change
            for k in (-1, 0, 1, 2):
                stamps.append(int((dt.datetime(y, m, d, 12, tzinfo=dt.timezone.utc) + dt.timedelta(days=k)).timestamp()))
        code = ("import json, os, sys, time\nfrom opos import timezone as t\nst = json.loads(sys.argv[1])\nout = {}\n"
                "for n, rule in t.BUILTIN.items():\n    os.environ['TZ'] = rule\n    time.tzset()\n    out[n] = [time.localtime(s).tm_gmtoff for s in st]\nprint(json.dumps(out))")
        r = py(code, json.dumps(stamps))
        self.assertEqual(0, r.returncode, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(sorted(tzmod.BUILTIN), sorted(got))
        for name, offs in got.items():
            zi = zoneinfo.ZoneInfo(name)
            want = [int(dt.datetime.fromtimestamp(s, zi).utcoffset().total_seconds()) for s in stamps]
            with self.subTest(zone=name):
                self.assertEqual(want, offs)


class Setting(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.env = {"OPOS_CONFIG_DIR": os.path.join(self.d, "cfg")}
        self.zi = os.path.join(self.d, "zi")
        make_tree(self.zi, ["UTC", "Europe/Berlin", "Asia/Kolkata"])
        self.path = tzmod.setting_path(self.env)

    def test_the_setting_lives_in_the_config_directory(self):
        self.assertEqual(Path(self.env["OPOS_CONFIG_DIR"]) / "timezone", self.path)

    def test_save_then_load(self):
        self.assertIsNone(tzmod.load_setting(self.env, self.zi))
        self.assertEqual("Asia/Kolkata", tzmod.save_setting("Asia/Kolkata", self.env, self.zi))
        self.assertEqual("Asia/Kolkata\n", self.path.read_text())
        self.assertEqual("Asia/Kolkata", tzmod.load_setting(self.env, self.zi))
        tzmod.save_setting("UTC", self.env, self.zi)
        self.assertEqual("UTC", tzmod.load_setting(self.env, self.zi))
        self.assertEqual(["timezone"], os.listdir(self.path.parent))  # no temp file left behind

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_the_file_is_private(self):
        tzmod.save_setting("UTC", self.env, self.zi)
        self.assertEqual(0o600, self.path.stat().st_mode & 0o777)

    def test_an_unknown_name_is_refused_and_nothing_is_written(self):
        for bad in ("../../etc/passwd", "Mars/Base", "", None, "UTC; id", "Europe/Berlin\nUTC"):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                tzmod.save_setting(bad, self.env, self.zi)
        self.assertFalse(self.path.exists())

    def test_a_bad_saved_value_is_ignored(self):
        self.path.parent.mkdir(parents=True)
        for raw in (b"", b"\n", b"Mars/Base\n", b"$(touch x)\n", b"`id`", b"../etc/passwd", b"Europe/Berlin; id", b"\xff\xfeEurope/Berlin",
                    b"Europe/B\xc3\xa9rlin", b"UTC" + b"A" * 500, b"CET-1CEST,M3.5.0,M10.5.0/3", b"\x00"):
            self.path.write_bytes(raw)
            with self.subTest(raw=raw[:20]):
                self.assertIsNone(tzmod.load_setting(self.env, self.zi))
        self.path.write_bytes(b"Europe/Berlin\nrm -rf /\n")  # only the first line counts
        self.assertEqual("Europe/Berlin", tzmod.load_setting(self.env, self.zi))
        self.path.unlink()
        self.path.mkdir()  # a directory where the file should be
        self.assertIsNone(tzmod.load_setting(self.env, self.zi))

    def test_apply_saved_with_nothing_saved_changes_nothing(self):
        before = os.environ.get("TZ")
        self.assertIsNone(tzmod.apply_saved(self.env, self.zi))
        self.assertEqual(before, os.environ.get("TZ"))

    def test_apply_saved_never_raises(self):
        blocker = Path(self.d, "file")
        blocker.write_text("x")
        self.assertIsNone(tzmod.apply_saved({"OPOS_CONFIG_DIR": str(blocker / "sub")}, self.zi))
        with self.assertRaises(ValueError):
            tzmod.apply("../x", self.zi)

    def test_save_into_an_unwritable_place_raises_oserror(self):
        blocker = Path(self.d, "file")
        blocker.write_text("x")
        with self.assertRaises(OSError):
            tzmod.save_setting("UTC", {"OPOS_CONFIG_DIR": str(blocker / "sub")}, self.zi)


@unittest.skipUnless(REAL and hasattr(time, "tzset"), "needs the system zone database and time.tzset")
class AppliedInAFreshProcess(unittest.TestCase):
    """What the server does at start: read the saved zone and put it in TZ, so time.localtime follows it."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.env = {"OPOS_CONFIG_DIR": os.path.join(self.d, "cfg")}

    OFFSET = ("import time\nfrom opos import timezone as t\nprint(t.apply_saved(%s))\n"
              "print(time.strftime('%%z', time.localtime(%d)), __import__('os').environ.get('TZ'))")

    def run_start(self, ts, root=None, own=None):
        r = py(self.OFFSET % (("root=%r" % root) if root else "", ts), env=dict(self.env, **({"TZ": own} if own else {})))
        self.assertEqual(0, r.returncode, r.stderr)
        return r.stdout.split("\n")[:2]

    def test_the_saved_zone_sets_the_local_time(self):
        jan, jul = 1768478400, 1784116800  # 2026-01-15 and 2026-07-15, 12:00 UTC
        tzmod.save_setting("Asia/Kolkata", self.env)
        self.assertEqual(["Asia/Kolkata", "+0530 Asia/Kolkata"], self.run_start(jan))
        tzmod.save_setting("Pacific/Auckland", self.env)
        self.assertEqual(["Pacific/Auckland", "+1300 Pacific/Auckland"], self.run_start(jan))
        self.assertEqual(["Pacific/Auckland", "+1200 Pacific/Auckland"], self.run_start(jul))
        tzmod.save_setting("America/New_York", self.env)
        self.assertEqual("-0500 America/New_York", self.run_start(jan)[1])
        self.assertEqual("-0400 America/New_York", self.run_start(jul)[1])

    def test_with_nothing_saved_the_process_keeps_its_own_zone(self):
        self.assertEqual(["None", "+0900 Asia/Tokyo"], self.run_start(1768478400, own="Asia/Tokyo"))

    def test_with_a_garbage_setting_the_process_keeps_its_own_zone(self):
        p = tzmod.setting_path(self.env)
        p.parent.mkdir(parents=True)
        p.write_text("../../etc/localtime\n")
        self.assertEqual(["None", "+0900 Asia/Tokyo"], self.run_start(1768478400, own="Asia/Tokyo"))

    def test_without_a_zone_database_the_built_in_rule_is_used(self):
        nowhere = os.path.join(self.d, "no-zoneinfo")
        tzmod.save_setting("Europe/Berlin", self.env, nowhere)
        jan, jul = 1768478400, 1784116800
        self.assertEqual(["Europe/Berlin", "+0100 CET-1CEST,M3.5.0,M10.5.0/3"], self.run_start(jan, nowhere))
        self.assertEqual(["Europe/Berlin", "+0200 CET-1CEST,M3.5.0,M10.5.0/3"], self.run_start(jul, nowhere))
        tzmod.save_setting("Asia/Kolkata", self.env, nowhere)
        self.assertEqual("+0530 IST-5:30", self.run_start(jan, nowhere)[1])

    def test_the_command_line(self):
        def cli(*a):
            e = {k: v for k, v in os.environ.items() if k != "TZ"}
            e.update(PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1", **self.env)
            return subprocess.run([sys.executable, "-m", "opos.timezone", *a], capture_output=True, text=True, timeout=60, env=e)
        r = cli("--set", "Europe/Berlin")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual("Europe/Berlin", tzmod.load_setting(self.env))
        self.assertIn("saved zone: Europe/Berlin", cli().stdout)
        listed = cli("--list")
        self.assertEqual(0, listed.returncode)
        self.assertIn("Asia/Kolkata", listed.stdout.split("\n"))
        for bad in ("../../etc/passwd", "Europe/Berlin;id", "$(id)"):
            r = cli("--set", bad)
            self.assertEqual(2, r.returncode, bad)
        self.assertEqual("Europe/Berlin", tzmod.load_setting(self.env))
        self.assertEqual(2, cli("--bogus").returncode)


@unittest.skipUnless(shutil.which("sh") and KIOSK.is_file() and sys.platform != "win32", "needs a POSIX sh")
class KioskExport(unittest.TestCase):
    """The opos-kiosk block that turns the saved line into TZ for Chromium: read, check, export; never run."""

    @classmethod
    def setUpClass(cls):
        text = KIOSK.read_text(encoding="utf-8")
        m = re.search(r"# >>> track firstboot.*?\n(.*?)\n\s*# <<< track firstboot", text, re.S)
        cls.block = m.group(1)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def tz(self, raw, **extra):
        cfg = os.path.join(self.d, "cfg")
        os.makedirs(cfg, exist_ok=True)
        if raw is not None:
            Path(cfg, "timezone").write_bytes(raw)
        env = {"PATH": os.environ["PATH"], "OPOS_CONFIG_DIR": cfg, "HOME": self.d}
        env.update(extra)
        r = subprocess.run(["sh", "-c", self.block + '\nprintf "%s" "${TZ-unset}"'], capture_output=True, text=True, timeout=20, env=env, cwd=self.d)
        self.assertEqual(0, r.returncode, r.stderr)
        return r.stdout

    def test_a_good_zone_is_exported(self):
        for z in ("Europe/Berlin", "America/Argentina/Buenos_Aires", "UTC", "Etc/GMT+5"):
            self.assertEqual(z, self.tz((z + "\n").encode()), z)
        self.assertEqual("Asia/Kolkata", self.tz(b"Asia/Kolkata"))  # no trailing newline

    def test_only_the_first_line_counts(self):
        self.assertEqual("Europe/Berlin", self.tz(b"Europe/Berlin\ntouch evil\n"))

    def test_nothing_unsafe_is_exported_or_run(self):
        for raw in (b"", b"\n", b"Europe/Berlin;touch evil", b"$(touch evil)", b"`touch evil`", b"Europe/Berlin touch evil", b"../etc/passwd",
                    b"Europe/../Berlin", b"/etc/localtime", b"Europe//Berlin", b"-h", b"+3", b"A" * 65 + b"/B", b"Europe/B\xc3\xa9rlin",
                    b"'; touch evil; '", b'" ; touch evil ; "', b"Europe/*", b"Europe/B?rlin", b"CET-1CEST,M3.5.0,M10.5.0/3\n", b"\n\nEurope/Berlin"):
            with self.subTest(raw=raw[:30]):
                self.assertEqual("unset", self.tz(raw))
                self.assertFalse(os.path.exists(os.path.join(self.d, "evil")))

    def test_no_file_means_no_tz(self):
        self.assertEqual("unset", self.tz(None))

    def test_the_default_location_is_the_one_the_server_writes(self):
        home = os.path.join(self.d, "h")
        os.makedirs(os.path.join(home, ".config", "opos"))
        Path(home, ".config", "opos", "timezone").write_text("Asia/Tokyo\n")
        env = {"PATH": os.environ["PATH"], "HOME": home}
        r = subprocess.run(["sh", "-c", self.block + '\nprintf "%s" "${TZ-unset}"'], capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual("Asia/Tokyo", r.stdout)
        self.assertEqual(Path(home, ".config", "opos", "timezone"), tzmod.setting_path({"HOME": home, "OPOS_CONFIG_DIR": ""}))
        env["XDG_CONFIG_HOME"] = os.path.join(self.d, "x")
        os.makedirs(os.path.join(self.d, "x", "opos"))
        Path(self.d, "x", "opos", "timezone").write_text("Europe/Paris\n")
        r = subprocess.run(["sh", "-c", self.block + '\nprintf "%s" "${TZ-unset}"'], capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual("Europe/Paris", r.stdout)
        self.assertEqual(Path(self.d, "x", "opos", "timezone"), tzmod.setting_path({"HOME": home, "XDG_CONFIG_HOME": env["XDG_CONFIG_HOME"]}))

    def test_the_kiosk_script_is_still_valid_shell_and_reads_the_zone_before_each_start(self):
        r = subprocess.run(["sh", "-n", str(KIOSK)], capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr)
        text = KIOSK.read_text(encoding="utf-8")
        self.assertLess(text.index("# >>> track firstboot"), text.index("chromium --kiosk"))
        self.assertLess(text.index("while :; do"), text.index("# >>> track firstboot"))  # inside the restart loop


if __name__ == "__main__":
    unittest.main()
