# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import subprocess
import tempfile
import unittest
_WARNING = "warning: Surface Duo detected. If this kernel was RAM-booted from a PC with fastboot boot, do not run reboot or systemctl reboot: the phone would start the stock kernel and be stranded.\nTo restart safely run reboot bootloader, then re-arm the misc brake and RAM-boot once from the PC. See docs/duo-usb-install.md, section \"Rebooting safely / recovering\".\n"
def _install_path():
    return pathlib.Path(__file__).resolve().parent.parent / "install.sh"
def _func_text():
    lines = _install_path().read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("duo_reboot_warning() {"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1]) + "\n"
def _run_root(root):
    script = "set -eu\n" + _func_text() + "duo_reboot_warning\n"
    env = dict(os.environ)
    env["OPOS_DT_ROOT"] = root
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)
def _check_placement(case):
    lines = _install_path().read_text(encoding="utf-8").splitlines()
    hits = [i for i, line in enumerate(lines) if line == "duo_reboot_warning"]
    case.assertEqual(len(hits), 1)
    case.assertEqual(lines[hits[0] - 1], "esac")
    case.assertEqual(lines[hits[0] + 1], 'if [ "$DRY" -eq 1 ]; then')
class DuoInstallRebootWarnTest(unittest.TestCase):
    def test_duo_model_warns(self):
        cases = [("proc/device-tree/model", "Microsoft Surface Duo"), ("sys/firmware/devicetree/base/model", "Microsoft surface duo")]
        for rel, label in cases:
            with self.subTest(model=rel):
                with tempfile.TemporaryDirectory() as td:
                    p = pathlib.Path(td) / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes((label + chr(0)).encode())
                    r = _run_root(td)
                    self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", _WARNING))
        _check_placement(self)
    def test_other_and_missing_model_silent(self):
        def _prep(td, files):
            for rel, label in files:
                p = pathlib.Path(td) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes((label + chr(0)).encode())
        cases = [[], [["proc/device-tree/model", "Acme Generic Board"]], [["proc/device-tree/model", "Acme Generic Board"], ["sys/firmware/devicetree/base/model", "Microsoft Surface Duo"]]]
        for idx, files in enumerate(cases):
            with self.subTest(case=idx):
                with tempfile.TemporaryDirectory() as td:
                    _prep(td, files)
                    r = _run_root(td)
                    self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
