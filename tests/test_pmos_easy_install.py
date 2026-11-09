# SPDX-License-Identifier: GPL-3.0-or-later
import os, pathlib, subprocess, tempfile, unittest
class TestEasyInstall(unittest.TestCase):
    def run_case(self, args, confirm=0):
        text = (pathlib.Path(__file__).resolve().parent.parent / "packaging" / "pmos" / "easy" / "easy-install.sh").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            dst = pathlib.Path(tmp) / "easy-install.sh"; dst.write_text(text)
            log = pathlib.Path(tmp) / "log"
            (pathlib.Path(tmp) / "image-prep.sh").write_text(f'echo "prep:$#:$1:$2" >> "{log}"\n')
            (pathlib.Path(tmp) / "confirm-reboot.sh").write_text(f'echo "confirm:$*" >> "{log}"\nexit {confirm}\n')
            pm = pathlib.Path(tmp) / "pmbootstrap"; pm.write_text(f'echo "pmbootstrap:$*" >> "{log}"\n'); pm.chmod(0o755)
            env = dict(os.environ); env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["/bin/sh", str(dst)] + args, capture_output=True, text=True, env=env)
            lines = log.read_text().splitlines() if log.exists() else []; return proc.returncode, proc.stdout, lines, tmp
    def test_dry_run(self):
        code, out, log, here = self.run_case(["image file.img"])
        self.assertEqual((code, out.splitlines(), log), (0, ["step 1: sh " + here + "/image-prep.sh --check image file.img", "step 2: sh " + here + "/confirm-reboot.sh", "step 3: skipped (no --flash)"], []))
    def test_refusal(self):
        code, out, log, here = self.run_case(["--flash", "image file.img"], 1)
        self.assertEqual((code, log), (1, ["prep:2:--check:image file.img", "confirm:"]))
    def test_yes_forwarding(self):
        code, out, log, here = self.run_case(["--flash", "--yes", "image file.img"], 0)
        self.assertEqual((code, out, log), (0, "step 3: flashing (physical-device flashing is untested; pmbootstrap uses its installed rootfs, not the checked image path)\n", ["prep:2:--check:image file.img", "confirm:--yes", "pmbootstrap:flasher flash_rootfs"]))
    def test_check_mode(self):
        code, out, log, here = self.run_case(["--flash", "image file.img"], 0)
        self.assertEqual((code, out, log), (0, "step 3: flashing (physical-device flashing is untested; pmbootstrap uses its installed rootfs, not the checked image path)\n", ["prep:2:--check:image file.img", "confirm:", "pmbootstrap:flasher flash_rootfs"]))
