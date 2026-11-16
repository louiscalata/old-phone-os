# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo's hardware facts: one declared profile (src/opos/hw/device.py), the hand-written copies checked against it,
and the read-only reader (src/opos/hw/facts.py) that compares the profile with what the kernel reports."""
import ast
import contextlib
import dataclasses
import io
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from opos.hw import device, facts
from opos.hw.store import builtin_profile

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging" / "duo-os"
SRC = ROOT / "src" / "opos"


def assigned(path: Path, name: str):
    """The literal a Python file assigns to a top-level name, read with ast (the file is not imported)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name) and t.id == name]
    if len(found) != 1:
        raise AssertionError("%s assigns %s %d times" % (path, name, len(found)))
    return found[0]


def unit_value(path: Path, pattern: str):
    found = re.findall(pattern, path.read_text(encoding="utf-8"), re.M)
    if len(found) != 1:
        raise AssertionError("%s: %d lines match %s" % (path, len(found), pattern))
    return found[0]


class ContractTest(unittest.TestCase):
    """Each hand-written copy of a Duo fact is parsed and compared with the profile, so changing one end fails."""

    def setUp(self):
        self.duo = device.SURFACE_DUO

    def test_the_server_unit_span_is_the_profile_span(self):
        pane, hinge = unit_value(DUO / "opos-server.service", r"^Environment=OPOS_SPAN=(\d+),(\d+)$")
        self.assertEqual((int(pane), int(hinge)), (self.duo.span.value, self.duo.hinge.value))

    def test_an_unmeasured_span_is_still_called_unmeasured_in_the_unit(self):
        self.assertFalse(self.duo.span.measured)
        self.assertFalse(self.duo.hinge.measured)
        self.assertIn("Not yet measured on the phone.", (DUO / "opos-server.service").read_text(encoding="utf-8"))

    def test_the_selftest_output_is_the_profile_output(self):
        self.assertEqual(assigned(DUO / "opos-selftest", "OUTPUT_PX"), self.duo.output_px.value)
        self.assertEqual(assigned(DUO / "opos-selftest", "OUTPUT_SCALE"), self.duo.scale.value)

    def test_the_lid_name_is_the_profile_lid_name(self):
        self.assertEqual(assigned(SRC / "native" / "posture.py", "LID_NAME"), self.duo.lid_name.value)

    def test_the_brightness_unit_uid_is_the_profile_uid(self):
        uid = unit_value(DUO / "opos-brightness.service", r"^Environment=OPOS_BRIGHTNESS_DIR=/run/user/(\d+)$")
        self.assertEqual(int(uid), self.duo.session_uid.value)

    def test_the_battery_reader_names_the_profile_gauge_driver(self):
        drivers = re.findall(r'entry\.name\.startswith\("([^"]+)"\)', (SRC / "hw" / "battery.py").read_text(encoding="utf-8"))
        self.assertEqual(drivers, [self.duo.fuel_gauge_driver.value])


# Duo-only tokens and the files that may hold them. A new file in src/opos or packaging/duo-os that repeats one fails:
# it should read the fact from opos.hw.device, or be added here on purpose with a contract test above.
LEAK_ALLOWED = {
    "2784": {"src/opos/hw/device.py", "packaging/duo-os/opos-selftest", "packaging/duo-os/opos-server.service",
             "packaging/duo-os/README.md"},
    "675,42": {"src/opos/web.py", "src/opos/span.py", "packaging/duo-os/verify-image.sh", "packaging/duo-os/opos-server.service",
               "packaging/duo-os/README.md", "packaging/duo-os/build-image.sh", "packaging/duo-os/opos-native"},
    "Surface Duo Lid": {"src/opos/hw/device.py", "src/opos/native/posture.py", "packaging/duo-os/NATIVE.md"},
    "bq27742": {"src/opos/hw/device.py", "src/opos/hw/battery.py", "packaging/duo-os/README.md"},
    "32011": {"src/opos/hw/device.py", "packaging/duo-os/POWER.md", "packaging/duo-os/opos-brightness.service"},
}


def holders(token: str) -> set[str]:
    out = set()
    for top in (SRC, DUO):
        for p in top.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and token.encode() in p.read_bytes():
                out.add(p.relative_to(ROOT).as_posix())
    return out


class LeakTest(unittest.TestCase):
    def test_duo_tokens_appear_only_in_listed_files(self):
        for token, allowed in LEAK_ALLOWED.items():
            with self.subTest(token=token):
                self.assertEqual(holders(token) - allowed, set())

    def test_the_profile_holds_each_token_it_declares(self):
        text = (SRC / "hw" / "device.py").read_text(encoding="utf-8")
        for token in ("2784", "Surface Duo Lid", "bq27742", "32011"):
            self.assertIn(token, text)

    def test_the_reader_and_the_diag_hold_none(self):
        for p in (SRC / "hw" / "facts.py", DUO / "opos-diag"):
            for token in LEAK_ALLOWED:
                self.assertNotIn(token, p.read_text(encoding="utf-8"), p)


def duo_tree(root: Path, panel=b"1350x1800\n") -> None:
    def put(rel, data):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    put("proc/device-tree/model", b"Microsoft Surface Duo\x00")
    put("proc/device-tree/compatible", b"microsoft,surface-duo\x00qcom,sm8150\x00")
    put("proc/meminfo", b"MemTotal:        5765432 kB\nMemFree:          123456 kB\n")
    (root / "sys/class/drm/card0").mkdir(parents=True)
    put("sys/class/drm/card0-DSI-1/modes", panel)
    put("sys/class/drm/card0-DSI-2/modes", panel)
    put("sys/class/drm/card0-Virtual-1/modes", b"")
    put("proc/bus/input/devices",
        b'I: Bus=0000 Vendor=0000 Product=0000 Version=0000\nN: Name="qpnp_pon"\nB: EV=3\n\n'
        b'I: Bus=0000 Vendor=0000 Product=0000 Version=0000\nN: Name="Surface Duo Lid"\nB: EV=21\nB: SW=1\n\n'
        b'I: Bus=0000 Vendor=0000 Product=0000 Version=0000\nN: Name="Surface Duo Lid Wakeup"\nB: EV=3\n\n')
    for name, kind in (("bq27742-0", b"Battery\n"), ("bq27742-1", b"Battery\n"), ("usb", b"USB\n")):
        put("sys/class/power_supply/%s/type" % name, kind)
    for name in ("panel0-backlight", "panel1-backlight"):
        put("sys/class/backlight/%s/max_brightness" % name, b"255\n")


def run(argv) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = facts.main(argv)
    return rc, out.getvalue()


def statuses(text: str) -> dict[str, str]:
    lines = [ln.split() for ln in text.splitlines() if ln and ln.split()[0] in facts.STATUSES]
    return {parts[1]: parts[0] for parts in lines}


def snapshot(root: Path) -> list:
    return sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*"))


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_duo_shaped_tree_matches_every_declared_fact(self):
        duo_tree(self.root)
        before = snapshot(self.root)
        rc, text = run(["--root", str(self.root), "--profile", "Surface Duo"])
        self.assertEqual(rc, 0, text)
        got = statuses(text)
        self.assertEqual(set(got), set(facts.FACT_NAMES))
        self.assertEqual({n for n, s in got.items() if s == "MATCH"}, set(facts.FACT_NAMES) - {"memory"})
        self.assertEqual(got["memory"], "UNDECLARED")  # read, but the profile declares no memory size
        self.assertIn("5765432 kB", text)
        self.assertEqual(snapshot(self.root), before)  # writes nothing

    def test_another_panel_size_is_a_diff_and_exit_1(self):
        duo_tree(self.root, panel=b"1080x1920\n")
        rc, text = run(["--root", str(self.root), "--profile", "Surface Duo"])
        self.assertEqual(rc, 1, text)
        self.assertEqual(statuses(text)["panel_px"], "DIFF")
        self.assertIn("1080x1920", text)

    def test_an_empty_tree_is_unreadable_for_every_fact_and_never_a_match(self):
        rc, text = run(["--root", str(self.root), "--profile", "Surface Duo"])
        self.assertEqual(rc, 0, text)
        got = statuses(text)
        self.assertEqual(set(got), set(facts.FACT_NAMES))
        self.assertEqual(set(got.values()), {"UNREADABLE"})
        self.assertNotRegex(text, r"(?m)^MATCH ")
        self.assertIn("facts: 0 MATCH, 0 DIFF, %d UNREADABLE, 0 UNDECLARED" % len(facts.FACT_NAMES), text)

    def test_unreadable_is_never_match_at_the_comparison(self):
        for name, read in facts.read_kernel(str(self.root)).items():
            self.assertIsNone(read, name)
        for r in facts.compare(device.SURFACE_DUO, str(self.root)):
            self.assertEqual(r.status, "UNREADABLE", r)

    def test_a_fact_the_profile_does_not_declare_is_undeclared(self):
        duo_tree(self.root)
        rc, text = run(["--root", str(self.root), "--profile", "Generic phone"])
        self.assertEqual(rc, 0, text)
        self.assertEqual(set(statuses(text).values()), {"UNDECLARED"})
        partial = facts.compare(dataclasses.replace(device.SURFACE_DUO, lid_name=None), str(self.root))
        self.assertEqual({r.name: r.status for r in partial}["lid"], "UNDECLARED")

    def test_a_missing_lid_or_too_few_gauges_and_backlights_are_diffs(self):
        duo_tree(self.root)
        (self.root / "proc/bus/input/devices").write_bytes(b'N: Name="Surface Duo Lid Wakeup"\n\n')
        (self.root / "sys/class/power_supply/bq27742-1/type").unlink()
        (self.root / "sys/class/power_supply/bq27742-1").rmdir()
        (self.root / "sys/class/backlight/panel1-backlight/max_brightness").unlink()
        (self.root / "sys/class/backlight/panel1-backlight").rmdir()
        rc, text = run(["--root", str(self.root), "--profile", "Surface Duo"])
        self.assertEqual(rc, 1, text)
        got = statuses(text)
        self.assertEqual((got["lid"], got["fuel_gauges"], got["backlights"]), ("DIFF", "DIFF", "DIFF"))
        self.assertEqual(got["model"], "MATCH")

    def diffs(self) -> dict[str, str]:
        rc, text = run(["--root", str(self.root), "--profile", "Surface Duo"])
        self.assertEqual(rc, 1, text)
        return statuses(text)

    def test_another_model_or_compatible_is_a_diff(self):
        duo_tree(self.root)
        (self.root / "proc/device-tree/model").write_bytes(b"Google Pixel 4\x00")
        (self.root / "proc/device-tree/compatible").write_bytes(b"google,flame\x00qcom,sm8150-mtp\x00qcom,sm8250\x00")
        got = self.diffs()
        self.assertEqual((got["model"], got["compatible"]), ("DIFF", "DIFF"))  # an entry must equal, not contain
        self.assertEqual(got["panel_px"], "MATCH")

    def test_one_panel_of_another_size_is_a_diff(self):
        duo_tree(self.root)
        (self.root / "sys/class/drm/card0-DSI-2/modes").write_bytes(b"1080x1920\n")
        got = self.diffs()
        self.assertEqual((got["panels"], got["panel_px"]), ("MATCH", "DIFF"))

    def test_more_gauges_or_backlights_than_declared_are_diffs(self):
        duo_tree(self.root)
        (self.root / "sys/class/power_supply/bq27742-2").mkdir()
        (self.root / "sys/class/backlight/panel2-backlight").mkdir()
        got = self.diffs()
        self.assertEqual((got["fuel_gauges"], got["backlights"]), ("DIFF", "DIFF"))
        self.assertEqual(got["lid"], "MATCH")

    def test_a_drm_tree_without_modes_files_is_unreadable_not_zero_panels(self):
        duo_tree(self.root)
        for n in ("card0-DSI-1", "card0-DSI-2", "card0-Virtual-1"):
            (self.root / "sys/class/drm" / n / "modes").unlink()
        rc, text = run(["--root", str(self.root), "--profile", "Surface Duo"])
        self.assertEqual(rc, 0, text)
        got = statuses(text)
        self.assertEqual((got["panels"], got["panel_px"]), ("UNREADABLE", "UNREADABLE"))

    def test_each_line_says_whether_the_declared_value_was_measured(self):
        duo_tree(self.root)
        _, text = run(["--root", str(self.root), "--profile", "Surface Duo"])
        lines = {ln.split()[1]: ln for ln in text.splitlines() if ln.split() and ln.split()[0] in facts.STATUSES}
        self.assertIn("(measured)", lines["backlights"])
        self.assertIn("(not yet measured)", lines["panel_px"])

    def test_an_unknown_profile_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            run(["--root", str(self.root), "--profile", "nope"])
        self.assertEqual(cm.exception.code, 2)

    def test_the_reader_has_no_write_calls(self):
        text = (SRC / "hw" / "facts.py").read_text(encoding="utf-8")
        for call in ("write_", ".write(", '"w"', "'w'", '"a"', "mkdir", "unlink", "rmdir", "rename", "chmod", "subprocess"):
            self.assertNotIn(call, text)


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class DiagTest(unittest.TestCase):
    START, END = "# --- track: device facts", "# --- end track: device facts ---"

    def block(self) -> str:
        d = (DUO / "opos-diag").read_text(encoding="utf-8")
        self.assertEqual(d.count(self.START), 1)
        self.assertEqual(d.count(self.END), 1)
        return d[d.index(self.START):d.index(self.END) + len(self.END)]

    def test_the_diag_runs_the_reader_once_read_only(self):
        blk = self.block()
        self.assertEqual(blk.count("-m opos.hw.facts"), 1)
        self.assertIn('python3 -B -m opos.hw.facts --root / --profile "Surface Duo"', blk)
        self.assertIn("PYTHONPATH=/opt/opos/src", blk)
        self.assertTrue((SRC / "hw" / "facts.py").is_file())  # shipped by build-image.sh's git archive of src

    def test_the_block_runs_the_real_reader(self):
        with tempfile.TemporaryDirectory() as d:
            bin_ = Path(d, "bin")
            bin_.mkdir()
            shim = bin_ / "python3"
            shim.write_text('#!/bin/sh\nPYTHONPATH="%s" exec "%s" "$@"\n' % (ROOT / "src", sys.executable))
            tmo = bin_ / "timeout"
            tmo.write_text('#!/bin/sh\nshift\nexec "$@"\n')
            for p in (shim, tmo):
                p.chmod(p.stat().st_mode | stat.S_IXUSR)
            script = Path(d, "facts.sh")
            script.write_text("#!/bin/sh\n" + self.block() + "\n", newline="\n")
            env = {"PATH": "%s:/usr/bin:/bin" % bin_}
            p = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("== device facts", p.stdout)
        self.assertRegex(p.stdout, r"(?m)^(MATCH|DIFF|UNREADABLE|UNDECLARED) model ")
        self.assertRegex(p.stdout, r"(?m)^facts: exit [0-9]+$")


if __name__ == "__main__":
    unittest.main()
