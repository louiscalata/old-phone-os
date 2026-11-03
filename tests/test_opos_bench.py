# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixture-only coverage for the portable guest benchmark."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
KEYS = ["bench_version", "boot_to_login_s", "boot_to_login_src", "idle_ram_mb",
        "idle_cpu_pct", "pkg_count", "pkg_manager", "enabled_services", "init"]
USAGE = "usage: opos-bench.sh [--help]\n"


class OposBenchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        for tool in "awk sed ls sort wc cat sleep getconf".split():
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.env = {**os.environ, "PATH": str(self.bin),
                    "OPOS_BENCH_ROOT": str(self.root),
                    "OPOS_BENCH_SAMPLE": "0", "OPOS_BENCH_HZ": "100"}
        self.env.pop("OPOS_BENCH_STAT2", None)
        self.shell = shutil.which("sh")

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def apk(self):
        path = self.bin / "apk"
        path.write_text("#!/bin/sh\nprintf 'one\\ntwo\\nthree\\n'\n")
        path.chmod(0o755)

    def run_bench(self, *args):
        return subprocess.run([self.shell, "tools/opos-bench.sh", *args],
                              cwd=ROOT, env=self.env, capture_output=True,
                              text=True, timeout=10)

    def test_help(self):
        result = self.run_bench("--help")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, USAGE, ""))

    def test_bad_arg(self):
        for args in [("bad",), ("--help", "extra")]:
            with self.subTest(args=args):
                result = self.run_bench(*args)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, "", USAGE))

    def test_bad_sample(self):
        for sample in ("abc", "", "-1", "61", "1.5"):
            with self.subTest(sample=sample):
                self.env["OPOS_BENCH_SAMPLE"] = sample
                result = self.run_bench()
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, "", USAGE))

    def test_full_fixture(self):
        self.apk()
        self.write("proc/meminfo", "MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\n")
        self.write("proc/stat", "cpu  100 0 100 700 100 0 0 0\n")
        stat2 = self.write("stat2", "cpu  150 0 150 1500 100 0 0 0\n")
        self.env["OPOS_BENCH_STAT2"] = str(stat2)
        self.write("proc/42/comm", "cage\n")
        self.write("proc/42/stat", "42 (cage) S " + "0 " * 18 + "1234 0\n")
        for level, names in {"default": ("opos-server", "sshd"),
                             "boot": ("sshd", "hwclock")}.items():
            for name in names:
                self.write(f"etc/runlevels/{level}/{name}", "")
        result = self.run_bench()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        expected = ["1", "12.3", "cage", "1000", "11.1", "3", "apk", "3", "openrc"]
        self.assertEqual(result.stdout, "".join(f"{k}={v}\n" for k, v in zip(KEYS, expected)))

    def test_empty_root_all_na(self):
        result = self.run_bench()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        expected = ["1", "NA", "none", "NA", "NA", "NA", "none", "NA", "none"]
        self.assertEqual(result.stdout, "".join(f"{k}={v}\n" for k, v in zip(KEYS, expected)))

    def values(self):
        result = self.run_bench()
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        lines = result.stdout.splitlines()
        self.assertEqual([line.split("=", 1)[0] for line in lines], KEYS)
        return dict(line.split("=", 1) for line in lines)

    def process(self, pid, name, ticks, stat_name=None):
        self.write(f"proc/{pid}/comm", name + "\n")
        self.write(f"proc/{pid}/stat",
                   f"{pid} ({stat_name or name}) S " + "0 " * 18 + f"{ticks} 0\n")

    def test_boot_priority_and_smallest_starttime(self):
        self.process(1, "agetty", 100)
        self.process(42, "cage", 2400)
        self.process(43, "cage", 1234, "nested (name) with spaces")
        self.process(44, "cage-helper", 1)
        self.process("45extra", "cage", 1)
        values = self.values()
        self.assertEqual(values["boot_to_login_src"], "cage")
        self.assertEqual(values["boot_to_login_s"], "12.3")

    def test_boot_match_without_stat_preserves_priority(self):
        self.write("proc/42/comm", "cage\n")
        self.process(43, "agetty", 100)
        values = self.values()
        self.assertEqual(values["boot_to_login_src"], "cage")
        self.assertEqual(values["boot_to_login_s"], "NA")

    def test_hz_fallback(self):
        self.process(42, "login", 1234)
        self.env["OPOS_BENCH_HZ"] = "invalid"
        self.assertEqual(self.values()["boot_to_login_s"], "12.3")

    def test_cpu_unavailable_or_nonincreasing(self):
        self.write("proc/stat", "cpu  100 0 100 700 100 0 0 0\n")
        for content in (None, "cpu  100 0 100 700 100 0 0 0\n",
                        "cpu  0 0 0 1 0 0 0 0\n", "intr 123\n"):
            with self.subTest(content=content):
                path = self.root / "second"
                if content is not None:
                    path.write_text(content)
                self.env["OPOS_BENCH_STAT2"] = str(path)
                self.assertEqual(self.values()["idle_cpu_pct"], "NA")

    def test_meminfo_requires_both_fields(self):
        for content in ("MemTotal: 2048000 kB\n", "MemAvailable: 1024000 kB\n"):
            with self.subTest(content=content):
                self.write("proc/meminfo", content)
                self.assertEqual(self.values()["idle_ram_mb"], "NA")

    def test_dpkg_fallback_and_apk_priority(self):
        path = self.bin / "dpkg-query"
        path.write_text("#!/bin/sh\nprintf '.\\n.\\n'\n")
        path.chmod(0o755)
        values = self.values()
        self.assertEqual((values["pkg_manager"], values["pkg_count"]), ("dpkg", "2"))
        self.apk()
        values = self.values()
        self.assertEqual((values["pkg_manager"], values["pkg_count"]), ("apk", "3"))

    def failing(self, tool):
        path = self.bin / tool
        path.write_text("#!/bin/sh\nprintf 'partial\\n'\nexit 1\n")
        path.chmod(0o755)

    def test_failed_apk_reports_na(self):
        self.failing("apk")
        values = self.values()
        self.assertEqual((values["pkg_manager"], values["pkg_count"]), ("apk", "NA"))

    def systemctl(self):
        path = self.bin / "systemctl"
        path.write_text("#!/bin/sh\nprintf 'a.service enabled\\nb.service enabled\\n'\n")
        path.chmod(0o755)

    @unittest.skipIf(os.path.isdir("/etc/runlevels"), "host uses OpenRC runlevels")
    def test_failed_systemctl_reports_na(self):
        self.failing("systemctl")
        self.env["OPOS_BENCH_ROOT"] = ""
        values = self.values()
        self.assertEqual((values["init"], values["enabled_services"]), ("systemd", "NA"))

    def test_systemd_only_without_fake_root(self):
        self.systemctl()
        values = self.values()
        self.assertEqual((values["init"], values["enabled_services"]), ("none", "NA"))

    @unittest.skipIf(os.path.isdir("/etc/runlevels"), "host uses OpenRC runlevels")
    def test_systemd_fallback_on_live_root(self):
        self.systemctl()
        self.env["OPOS_BENCH_ROOT"] = ""
        values = self.values()
        self.assertEqual((values["init"], values["enabled_services"]), ("systemd", "2"))

    def test_empty_runlevels(self):
        (self.root / "etc/runlevels").mkdir(parents=True)
        values = self.values()
        self.assertEqual((values["init"], values["enabled_services"]), ("openrc", "0"))


if __name__ == "__main__":
    unittest.main()
