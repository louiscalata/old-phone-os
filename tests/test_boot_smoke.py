# SPDX-License-Identifier: GPL-3.0-or-later
"""The boot smoke test: a judge that reads a serial log and the PC-only QEMU harness that runs a boot and asks the judge.

The judge must fail closed: every recorded bad boot and silence are failures. It takes its positive line from one of two
sources: systemd's own "Started opos-server.service" console line (--accept server-started), which the shipped image
already prints, or the documented marker "OPOS_BOOT_OK <identity> server=ok" (--expect), which names the exact image and
which no shipped unit prints yet: the unit that would print it is a proposal awaiting Louis's yes, so it must not be in
the tree or installed by any build script. The harness must refuse the Mac, must never write the image, and must not
report a pass when QEMU is missing, when the guest panics, or when it hangs. Nothing here boots anything: the harness is
run with stand-ins for qemu-system-aarch64 and uname.
The fixtures under tests/fixtures/boot-logs are written by hand, in the shape of a systemd serial console (the ones with a
marker show where the proposed unit would print it); they are to be replaced by real logs after the first run on the PC."""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging" / "duo-os"
VERDICT = PKG / "boot-smoke-verdict.py"
HARNESS = PKG / "qemu-boot-smoke.sh"
SERVER_UNIT = PKG / "opos-server.service"
LOGS = ROOT / "tests" / "fixtures" / "boot-logs"
SH = shutil.which("sh")
# The identity in the fixtures: the SHA-256 of a made-up /etc/opos/image.json.
GOOD = "bdb339e56673771e01e232646a3185abfeebf9cf4f61b584cece7d8f3d436377"
OTHER = "349626652bbf0bcc6a14cead3b1b1940a9dc5d58c9b36b2309c1e9dbc67093e7"
NEGATIVE = ("Kernel panic", "Failed to start", "emergency mode", "Dependency failed", "opos-fallback")
BAD_FIXTURES = {"panic.log": 1, "timeout.log": 3, "failed-unit.log": 1, "wrong-identity.log": 1,
                "marker-before-panic.log": 1, "marker-in-kernel-echo.log": 3}


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def verdict(log, expect=GOOD, extra=(), judge=VERDICT):
    args = [sys.executable, "-I", "-B", str(judge)]
    if log is not None:
        args += ["--log", str(log)]
    if expect is not None:
        args += ["--expect", expect]
    p = subprocess.run(args + list(extra), capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    return p


def evidence(p):
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, p.stdout + p.stderr
    return json.loads(lines[0])


class Fixtures(unittest.TestCase):
    def test_every_fixture_is_there_and_has_no_private_details(self):
        for name in list(BAD_FIXTURES) + ["ok.log", "server-started.log"]:
            with self.subTest(name=name):
                text = (LOGS / name).read_text(encoding="utf-8")
                self.assertTrue(text.strip())
                self.assertIsNone(re.search(r"(?i)\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", text), "a MAC address")
                self.assertIsNone(re.search(r"(?<!\d)\d{15}(?!\d)", text), "an IMEI-length number")
                self.assertIsNone(re.search(r"/Users/|/home/[a-z]", text), "a user path")


class VerdictFailsClosed(unittest.TestCase):
    """Written first: nothing but the one good log may exit 0."""

    def test_every_bad_fixture_exits_non_zero_with_its_code(self):
        for name, code in BAD_FIXTURES.items():
            with self.subTest(name=name):
                p = verdict(LOGS / name)
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertEqual(p.returncode, code, p.stdout + p.stderr)
                self.assertEqual(evidence(p)["exit"], code)

    def test_an_empty_log_is_a_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "serial.log"
            log.write_bytes(b"")
            p = verdict(log)
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
            self.assertEqual(evidence(p)["verdict"], "timeout")

    def test_a_wrong_identity_fails_and_names_both(self):
        p = verdict(LOGS / "wrong-identity.log")
        self.assertEqual(p.returncode, 1)
        ev = evidence(p)
        self.assertEqual(ev["expected"], GOOD)
        self.assertIn(OTHER, [m["identity"] for m in ev["markers"]])
        # the good log, asked for another image, fails too: the identity is compared, not only matched by shape
        p = verdict(LOGS / "ok.log", expect=OTHER)
        self.assertEqual(p.returncode, 1, p.stdout)

    def test_each_negative_marker_fails_a_log_that_has_the_marker(self):
        ok = (LOGS / "ok.log").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            for neg in NEGATIVE + ("You are in EMERGENCY MODE", "OPOS_BOOT_FAIL %s server=down" % GOOD):
                for where in ("before", "after"):
                    with self.subTest(neg=neg, where=where):
                        log = Path(d) / "serial.log"
                        line = "[   12.345678] systemd[1]: %s for something.\n" % neg
                        log.write_text(line + ok if where == "before" else ok + line, encoding="utf-8")
                        p = verdict(log)
                        self.assertEqual(p.returncode, 1, p.stdout)
                        self.assertTrue(evidence(p)["negative"])

    def test_two_markers_or_a_malformed_marker_fail(self):
        ok = (LOGS / "ok.log").read_text(encoding="utf-8")
        line = "OPOS_BOOT_OK %s server=ok\n" % GOOD
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "serial.log"
            for text in (ok + line, ok.replace(line, "OPOS_BOOT_OK %s server=ok extra\n" % GOOD),
                         ok.replace(line, "OPOS_BOOT_OK %s server=down\n" % GOOD), ok.replace(line, "OPOS_BOOT_OK %s\n" % GOOD),
                         ok.replace(line, "OPOS_BOOT_OK %s server=ok\n" % GOOD.upper())):
                with self.subTest(text=text[-90:]):
                    log.write_text(text, encoding="utf-8")
                    self.assertEqual(verdict(log).returncode, 1)

    def test_a_marker_that_does_not_start_its_line_is_not_a_marker(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "serial.log"
            for text in ("[    5.000000] OPOS_BOOT_OK %s server=ok\n", "echo OPOS_BOOT_OK %s server=ok\n",
                         "Kernel command line: console=ttyAMA0 x=\"OPOS_BOOT_OK %s server=ok\"\n"):
                with self.subTest(text=text):
                    log.write_text(text % GOOD, encoding="utf-8")
                    self.assertEqual(verdict(log).returncode, 3)

    def test_bad_arguments_exit_2_with_one_json_line(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "nothing.log"
            for log, expect in ((None, GOOD), (LOGS / "ok.log", None), (missing, GOOD), (LOGS / "ok.log", ""),
                                (LOGS / "ok.log", "abc"), (LOGS / "ok.log", GOOD[:-1]), (LOGS / "ok.log", ".*"),
                                (LOGS / "ok.log", GOOD.upper()), (Path(d), GOOD)):
                with self.subTest(log=log, expect=expect):
                    p = verdict(log, expect)
                    self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                    self.assertEqual(evidence(p)["verdict"], "bad-arguments")

    def test_the_evidence_line_names_the_log_and_its_hash(self):
        p = verdict(LOGS / "panic.log")
        ev = evidence(p)
        self.assertEqual(ev["log_sha256"], hashlib.sha256((LOGS / "panic.log").read_bytes()).hexdigest())
        self.assertEqual(ev["verdict"], "fail")
        self.assertTrue(any(n["marker"] == "Kernel panic" for n in ev["negative"]))


class VerdictPass(unittest.TestCase):
    def test_the_good_log_passes_and_names_the_image(self):
        p = verdict(LOGS / "ok.log")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ev = evidence(p)
        self.assertEqual((ev["verdict"], ev["exit"], ev["expected"]), ("pass", 0, GOOD))
        self.assertEqual([m["identity"] for m in ev["markers"]], [GOOD])
        self.assertEqual(ev["negative"], [])

    def test_carriage_returns_and_console_colours_do_not_hide_or_fake_anything(self):
        ok = (LOGS / "ok.log").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "serial.log"
            log.write_bytes(ok.replace("\n", "\r\n").encode())
            self.assertEqual(verdict(log).returncode, 0)
            log.write_text(ok + "[\x1b[0;1;31mFAILED\x1b[0m] Failed\x1b[0m to start opos-server.service\n", encoding="utf-8")
            self.assertEqual(verdict(log).returncode, 1)
            log.write_bytes(ok.encode() + b"\xff\xfe garbage\n")
            self.assertEqual(verdict(log).returncode, 0)

    def test_a_marker_with_console_whitespace_around_it_still_counts(self):
        ok = (LOGS / "ok.log").read_text(encoding="utf-8")
        line = "OPOS_BOOT_OK %s server=ok\n" % GOOD
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "serial.log"
            for padded in ("OPOS_BOOT_OK %s server=ok \t\n" % GOOD, "  OPOS_BOOT_OK %s server=ok  \n" % GOOD):
                with self.subTest(padded=padded):
                    log.write_text(ok.replace(line, padded), encoding="utf-8")
                    self.assertEqual(verdict(log).returncode, 0, verdict(log).stdout)


def accept(log, judge=VERDICT, extra=()):
    return verdict(log, expect=None, extra=("--accept", "server-started") + tuple(extra), judge=judge)


class ServerStarted(unittest.TestCase):
    """--accept server-started: the positive line is systemd's own console line for the shipped opos-server.service."""

    def started(self):
        desc = [ln.split("=", 1)[1] for ln in SERVER_UNIT.read_text(encoding="utf-8").splitlines() if ln.startswith("Description=")]
        self.assertEqual(len(desc), 1)
        return "[  OK  ] Started opos-server.service - %s.\n" % desc[0], "[  OK  ] Started %s.\n" % desc[0]

    def test_the_shipped_server_line_passes_in_both_systemd_formats(self):
        p = accept(LOGS / "server-started.log")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ev = evidence(p)
        self.assertEqual((ev["verdict"], ev["source"], ev["expected"]), ("pass", "server-started", None))
        self.assertEqual(len(ev["server_started"]), 1)
        self.assertIn("not checked", ev["reason"])
        combined, plain = self.started()
        text = (LOGS / "server-started.log").read_text(encoding="utf-8")
        self.assertIn(combined, text)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "serial.log"
            log.write_text(text.replace(combined, plain), encoding="utf-8")
            self.assertEqual(accept(log).returncode, 0, "systemd 252 prints the description alone")
            log.write_text(text.replace(combined, "[\x1b[0;32m  OK  \x1b[0m] Started \x1b[0;1;39mopos-server.service\x1b[0m - "
                                        + combined.split(" - ", 1)[1]).replace("\n", "\r\n"), encoding="utf-8")
            self.assertEqual(accept(log).returncode, 0, "console colours and CRLF")

    def test_every_bad_fixture_still_fails_and_silence_is_a_timeout(self):
        for name, code in (("panic.log", 1), ("timeout.log", 3), ("failed-unit.log", 1), ("marker-before-panic.log", 1),
                           ("marker-in-kernel-echo.log", 3)):
            with self.subTest(name=name):
                p = accept(LOGS / name)
                self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "serial.log"
            log.write_bytes(b"")
            self.assertEqual(accept(log).returncode, 3)

    def test_a_restart_a_lookalike_or_a_line_that_is_not_alone_does_not_pass(self):
        combined, plain = self.started()
        text = (LOGS / "server-started.log").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "serial.log"
            cases = {1: (text + combined, text + plain, text.replace(combined, combined + "[FAILED] Failed to start x.\n")),
                     3: (text.replace(combined, combined.replace("opos-server.service", "opos-serverx.service")),
                         text.replace(combined, combined.replace("battery.", "battery")),
                         text.replace(combined, "[    5.000000] systemd[1]: " + combined.split("] ", 1)[1]),
                         text.replace(combined, "echo " + combined),
                         text.replace(combined, combined.replace("[  OK  ]", "[ OK ]")),
                         text.replace(combined, "[  OK  ] Started opos-diag.service - OPOS server.\n"))}
            for code, texts in cases.items():
                for t in texts:
                    with self.subTest(code=code, tail=t[-100:]):
                        log.write_text(t, encoding="utf-8")
                        p = accept(log)
                        self.assertEqual(p.returncode, code, p.stdout)

    def test_the_description_is_read_from_the_shipped_unit(self):
        combined, _ = self.started()
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            judge = d / "boot-smoke-verdict.py"
            shutil.copyfile(VERDICT, judge)
            # no unit next to the judge: a setup problem, never a pass or a timeout
            p = accept(LOGS / "server-started.log", judge=judge)
            self.assertEqual(p.returncode, 2, p.stdout)
            self.assertEqual(evidence(p)["verdict"], "bad-arguments")
            unit = SERVER_UNIT.read_text(encoding="utf-8")
            (d / "opos-server.service").write_text(unit.replace("Description=", "Description=Renamed "), encoding="utf-8")
            self.assertEqual(accept(LOGS / "server-started.log", judge=judge).returncode, 3, "the old line is no longer the server's")
            log = d / "serial.log"
            log.write_text((LOGS / "server-started.log").read_text(encoding="utf-8").replace(" - OPOS server", " - Renamed OPOS server"))
            self.assertEqual(accept(log, judge=judge).returncode, 0)
            for broken in (unit + "Description=Second\n", unit.replace("Description=", "Descr=")):
                with self.subTest(broken=broken[-30:]):
                    (d / "opos-server.service").write_text(broken, encoding="utf-8")
                    self.assertEqual(accept(LOGS / "server-started.log", judge=judge).returncode, 2)

    def test_the_two_sources_are_exclusive_and_one_is_required(self):
        for extra in ((), ("--accept", "server-started", "--expect", GOOD), ("--accept", "anything")):
            with self.subTest(extra=extra):
                p = verdict(LOGS / "ok.log", expect=None, extra=extra)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertEqual(evidence(p)["verdict"], "bad-arguments")
        # the marker source ignores the server line: a log with only that line is a timeout there
        self.assertEqual(verdict(LOGS / "server-started.log").returncode, 3)


FAKE_QEMU = r'''#!/bin/sh
# a stand-in for qemu-system-aarch64: records its arguments, then plays one fixture into the -serial file
printf '%s\n' "$@" > "$FAKE_ARGS"
log=''
prev=''
for a in "$@"; do
    if [ "$prev" = -serial ]; then log=${a#file:}; fi
    prev=$a
done
case $FAKE_MODE in
    hang) exec sleep 1000 ;;
    silent) exit 0 ;;
    *) cat "$FAKE_LOG" > "$log"; exit "${FAKE_STATUS:-0}" ;;
esac
'''


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class Harness(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.kernel = self.tmp / "vmlinuz"
        self.kernel.write_bytes(b"k")
        self.rootfs = self.tmp / "rootfs.img"
        self.rootfs.write_bytes(b"r")
        self.args = self.tmp / "qemu-args"

    def fake(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)

    def run_harness(self, args, uname="Linux", qemu=True, mode="play", fixture="ok.log", path=None, status="0"):
        if uname:
            self.fake("uname", "#!/bin/sh\necho %s\n" % uname)
        if qemu:
            self.fake("qemu-system-aarch64", FAKE_QEMU)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OPOS_", "FAKE_"))}
        env.update(FAKE_ARGS=str(self.args), FAKE_MODE=mode, FAKE_LOG=str(LOGS / fixture), FAKE_STATUS=status,
                   TMPDIR=str(self.tmp))
        env["PATH"] = path if path is not None else "%s:%s:/usr/bin:/bin" % (self.bin, os.path.dirname(sys.executable))
        t0 = time.monotonic()
        # Its own session, so a harness that never ends its guest is ended here with everything it started (a red test, not a hang).
        proc = subprocess.Popen([SH, str(HARNESS)] + args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            self.fail("the harness did not end within 60 s: " + out + err)
        p = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
        p.seconds = time.monotonic() - t0
        return p

    def kernel_args(self, *more):
        return ["--kernel", str(self.kernel), "--rootfs", str(self.rootfs), "--expect", GOOD, "--timeout", "30"] + list(more)

    def test_it_refuses_to_run_on_macos(self):
        uname = None if sys.platform == "darwin" else "Darwin"
        p = self.run_harness(self.kernel_args(), uname=uname)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("refuses to run on macOS", p.stderr)
        self.assertFalse(self.args.exists(), "qemu was started")

    def test_without_qemu_it_exits_2_naming_it(self):
        p = self.run_harness(self.kernel_args(), qemu=False, path=str(self.bin))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("qemu-system-aarch64", p.stderr)

    def test_without_firmware_the_image_mode_exits_2_naming_it(self):
        disk = self.tmp / "disk.img"
        disk.write_bytes(b"d")
        p = self.run_harness(["--image", str(disk), "--firmware", str(self.tmp / "no-AAVMF_CODE.fd"), "--expect", GOOD])
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("AAVMF", p.stderr)
        self.assertFalse(self.args.exists(), "qemu was started")

    def test_bad_arguments_exit_2(self):
        for args in ([], ["--kernel", str(self.kernel), "--expect", GOOD], ["--rootfs", str(self.rootfs), "--expect", GOOD],
                     ["--kernel", str(self.kernel), "--rootfs", str(self.rootfs)],
                     self.kernel_args("--image", str(self.rootfs)), self.kernel_args("--timeout", "0"),
                     self.kernel_args("--timeout", "x"), ["--kernel", str(self.tmp / "none"), "--rootfs", str(self.rootfs), "--expect", GOOD],
                     self.kernel_args("--bogus"), self.kernel_args("--accept", "server-started"),
                     ["--kernel", str(self.kernel), "--rootfs", str(self.rootfs), "--accept", "anything"],
                     ["--kernel", str(self.kernel), "--rootfs", str(self.rootfs), "--expect", GOOD.upper()]):
            with self.subTest(args=args):
                p = self.run_harness(args)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertFalse(self.args.exists(), "qemu was started")

    def test_a_qemu_that_plays_a_panic_and_exits_0_still_fails(self):
        p = self.run_harness(self.kernel_args(), fixture="panic.log")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("{")][-1])["verdict"], "fail")

    def test_every_bad_fixture_played_by_qemu_fails(self):
        for name, code in BAD_FIXTURES.items():
            with self.subTest(name=name):
                p = self.run_harness(self.kernel_args(), fixture=name)
                self.assertEqual(p.returncode, code, p.stdout + p.stderr)

    def test_a_qemu_that_hangs_is_ended_by_the_timeout_and_gives_3(self):
        p = self.run_harness(self.kernel_args("--timeout", "2"), mode="hang")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertLess(p.seconds, 30)
        self.assertIn("timeout", p.stdout + p.stderr)

    def test_a_qemu_that_writes_nothing_gives_3_and_a_stale_log_is_refused(self):
        p = self.run_harness(self.kernel_args(), mode="silent")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.args.unlink()
        stale = self.tmp / "stale.log"
        stale.write_text((LOGS / "ok.log").read_text(encoding="utf-8"))
        p = self.run_harness(self.kernel_args("--log", str(stale)), mode="silent")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertFalse(self.args.exists(), "qemu was started")

    def test_the_good_boot_passes_with_the_fixed_qemu_arguments(self):
        log = self.tmp / "serial.log"
        p = self.run_harness(self.kernel_args("--log", str(log)))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        args = self.args.read_text().splitlines()
        for flag, value in (("-display", "none"), ("-serial", "file:" + str(log)), ("-kernel", str(self.kernel)), ("-nic", "none")):
            with self.subTest(flag=flag):
                self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("-no-reboot", args)
        drive = args[args.index("-drive") + 1]
        self.assertIn("snapshot=on", drive, "the image file is never written")
        self.assertIn("file=" + str(self.rootfs), drive)
        append = args[args.index("-append") + 1].split()
        for word in ("panic=-1", "opos.smoke=1", "console=ttyAMA0", "root=/dev/vda"):
            self.assertIn(word, append)
        # exactly the three units QEMU virt cannot run are masked, and nothing else
        self.assertEqual(sorted(w for w in append if w.startswith("systemd.mask=")),
                         ["systemd.mask=android-service@hwcomposer.service", "systemd.mask=lxc@android.service", "systemd.mask=opos.service"])
        self.assertEqual(json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("{")][-1])["verdict"], "pass")
        # a qemu that exits non-zero after a good log does not turn the verdict into a pass or a fail it did not see
        p = self.run_harness(self.kernel_args(), status="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_image_mode_boots_the_firmware_and_never_writes_the_disk(self):
        disk = self.tmp / "disk.img"
        disk.write_bytes(b"d")
        firmware = self.tmp / "AAVMF_CODE.fd"
        firmware.write_bytes(b"f")
        p = self.run_harness(["--image", str(disk), "--firmware", str(firmware), "--expect", GOOD, "--timeout", "30"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        args = self.args.read_text().splitlines()
        self.assertEqual(args[args.index("-bios") + 1], str(firmware))
        drives = [args[i + 1] for i, a in enumerate(args) if a == "-drive"]
        self.assertEqual(len(drives), 1, args)
        self.assertEqual(drives[0].split(","), ["file=" + str(disk), "format=raw", "if=virtio", "snapshot=on"])
        for flag in ("-kernel", "-initrd", "-append"):
            self.assertNotIn(flag, args)
        for flag, value in (("-display", "none"), ("-nic", "none")):
            self.assertEqual(args[args.index(flag) + 1], value)

    def test_the_kernel_mode_drive_is_a_snapshot_too(self):
        p = self.run_harness(self.kernel_args())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        args = self.args.read_text().splitlines()
        drives = [args[i + 1] for i, a in enumerate(args) if a == "-drive"]
        self.assertEqual([d.split(",") for d in drives], [["file=" + str(self.rootfs), "format=raw", "if=virtio", "snapshot=on"]])

    def test_the_server_started_source_passes_on_the_shipped_line_and_still_fails_closed(self):
        args = ["--kernel", str(self.kernel), "--rootfs", str(self.rootfs), "--accept", "server-started", "--timeout", "30"]
        p = self.run_harness(args, fixture="server-started.log")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ev = json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("{")][-1])
        self.assertEqual((ev["verdict"], ev["source"]), ("pass", "server-started"))
        for name, code in (("panic.log", 1), ("timeout.log", 3), ("failed-unit.log", 1)):
            with self.subTest(name=name):
                self.assertEqual(self.run_harness(args, fixture=name).returncode, code)
        self.assertEqual(self.run_harness(args[:-2] + ["--timeout", "2"], mode="hang").returncode, 3)

    def test_the_verdict_is_the_last_word(self):
        text = HARNESS.read_text(encoding="utf-8")
        self.assertNotIn("|| true", text)
        self.assertNotRegex(text, r"boot-smoke-verdict\.py[^\n]*\|\|")
        self.assertTrue(text.startswith("#!/bin/sh\n# SPDX-License-Identifier: GPL-3.0-or-later\n"))
        self.assertEqual(HARNESS.stat().st_mode & 0o111, 0o111)


class Docs(unittest.TestCase):
    def test_the_readme_states_the_scope_and_that_nothing_was_run(self):
        readme = " ".join(read("packaging/duo-os/README.md").split())
        section = readme[readme.index("## Boot smoke test (QEMU, PC only)"):]
        section = section[:section.index(" ## ", 5)]
        for phrase in ("--accept server-started", "--expect", "OPOS_BOOT_OK", "opos.smoke=1", "no hwcomposer", "qemu-boot-smoke.sh",
                       "boot-smoke-verdict.py", "NOT_RUN", "exit 3", "PROPOSAL", "Louis's yes", "snapshot=on"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_the_readme_section_keeps_the_file_wrapping(self):
        text = read("packaging/duo-os/README.md")
        section = text[text.index("## Boot smoke test (QEMU, PC only)"):]
        section = section[:section.index("\n## ", 5)]
        self.assertEqual([ln for ln in section.splitlines() if len(ln) > 120], [])

    def test_the_proposed_marker_unit_is_not_shipped_or_installed(self):
        # AGENTS.md: a new service needs Louis's yes before it is added. Until it is recorded, the unit stays a proposal.
        self.assertFalse((PKG / "opos-boot-ok.service").exists())
        self.assertFalse((PKG / "opos-boot-ok").exists())
        for name in ("build-image.sh", "verify-image.sh", "image-policy.py"):
            with self.subTest(name=name):
                text = (PKG / name).read_text(encoding="utf-8")
                self.assertNotIn("opos-boot-ok", text)
                self.assertNotIn("OPOS_SMOKE", text)

    def test_the_runbook_has_the_pc_session_row(self):
        text = read("docs/duo-device-checks.md")
        self.assertRegex(text, r"(?m)^### C33\. .*QEMU")
        self.assertRegex(text, r"(?m)^\| C33 \| .* \| \| NOT_RUN \|")


if __name__ == "__main__":
    unittest.main()
