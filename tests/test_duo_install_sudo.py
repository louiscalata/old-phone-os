# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import subprocess
import tempfile
import unittest


class TestDuoInstallSudo(unittest.TestCase):
    def test_sudo_password_notice_precedes_the_prompt(self):
        repo = pathlib.Path(__file__).resolve().parent.parent
        text = (repo / "install.sh").read_text(encoding="utf-8")
        func = text[text.index("ensure_venv()"):text.index("install_requirements()")]
        notice = "==> sudo may ask for your password; to skip, run: sudo apt install python3-venv, then re-run install.sh"
        prompt = "Install python3-venv with sudo apt? [y/N] "
        self.assertIn(notice, func)
        self.assertIn(prompt, func)
        self.assertLess(func.index(notice), func.index(prompt))

    def _expected_kiosk_bytes(self, browser):
        host = ".".join(["127", "0", "0", "1"])
        scheme = "".join(["http", "://"])
        url = scheme + host + ":" + "".join(["8765"]) + "/"
        text = "[Desktop Entry]\nType=Application\nName=OPOS\nExec=" + browser + " --kiosk " + url + "\nX-OPOS-Kiosk=true\n"
        return text.encode()

    def _run_install_prefix(self, args, extra_env=None, base_tmp=None):
        repo = pathlib.Path(__file__).resolve().parent.parent
        text = (repo / "install.sh").read_text(encoding="utf-8")
        prefix = text[: text.index("ROOT=$(")]
        owned = None
        if base_tmp is None:
            owned = tempfile.TemporaryDirectory()
            self.addCleanup(owned.cleanup)
            base = pathlib.Path(owned.name)
        else:
            base = pathlib.Path(base_tmp)
        home = base / "home"
        home.mkdir(parents=True, exist_ok=True)
        opos_home = home / "opos-home"
        bin_dir = home / "bin-dir"
        data_home = home / "data-home"
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["OPOS_HOME"] = str(opos_home)
        env["OPOS_BIN_DIR"] = str(bin_dir)
        env["XDG_DATA_HOME"] = str(data_home)
        if extra_env:
            env.update(extra_env)
        script = prefix + '\nparse_args "$@"\n'
        proc = subprocess.run(["sh", "-c", script, "_"] + list(args), capture_output=True, text=True, env=env, cwd=str(repo))
        adir = home / ".config" / "autostart"
        target = adir / "opos-kiosk.desktop"
        return proc, adir, target

    def test_f2182_kiosk_write_and_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            proc, adir, target = self._run_install_prefix(["--kiosk"], extra_env={"OPOS_KIOSK_BROWSER": "sh"}, base_tmp=base)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(target.read_bytes(), self._expected_kiosk_bytes("sh"))
            proc, adir, target = self._run_install_prefix(["--uninstall"], base_tmp=base)
            self.assertEqual(proc.returncode, 0)
            self.assertFalse(target.exists())
            adir.mkdir(parents=True, exist_ok=True)
            unmarked = "".join(["un", "marked\n"]).encode()
            target.write_bytes(unmarked)
            proc, adir, target = self._run_install_prefix(["--uninstall", "--kiosk"], base_tmp=base)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(target.read_bytes(), unmarked)

    def test_f2182_kiosk_dry_run_help_and_missing_browser(self):
        with self.subTest("dry-run"):
            with tempfile.TemporaryDirectory() as tmp:
                proc, adir, target = self._run_install_prefix(["--dry-run", "--kiosk"], extra_env={"OPOS_KIOSK_BROWSER": "sh"}, base_tmp=tmp)
                self.assertEqual(proc.returncode, 0)
                self.assertFalse(adir.exists())
                self.assertIn("==> would write kiosk autostart " + str(target), proc.stdout)
        with self.subTest("help"):
            with tempfile.TemporaryDirectory() as tmp:
                proc, adir, target = self._run_install_prefix(["--kiosk", "--help"], base_tmp=tmp)
                self.assertEqual(proc.returncode, 0)
                self.assertFalse(adir.exists())
                self.assertIn("[--no-launch] [--kiosk]", proc.stdout)
        with self.subTest("missing-browser"):
            with tempfile.TemporaryDirectory() as tmp:
                proc, adir, target = self._run_install_prefix(["--kiosk"], extra_env={"OPOS_KIOSK_BROWSER": "no-such-browser-xyz"}, base_tmp=tmp)
                self.assertEqual(proc.returncode, 0)
                self.assertFalse(adir.exists())
                self.assertEqual(proc.stderr.strip(), "warning: kiosk browser not found: no-such-browser-xyz; autostart not written")
        with self.subTest("dry-run-uninstall"):
            with tempfile.TemporaryDirectory() as tmp:
                base = pathlib.Path(tmp)
                proc, adir, target = self._run_install_prefix(["--kiosk"], extra_env={"OPOS_KIOSK_BROWSER": "sh"}, base_tmp=base)
                self.assertEqual(proc.returncode, 0)
                before = target.read_bytes()
                proc, adir, target = self._run_install_prefix(["--dry-run", "--uninstall"], base_tmp=base)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(target.read_bytes(), before)
                self.assertIn("==> would remove kiosk autostart " + str(target), proc.stdout)
        with self.subTest("unknown"):
            with tempfile.TemporaryDirectory() as tmp:
                proc, adir, target = self._run_install_prefix(["--kiosk", "--unknown"], base_tmp=tmp)
                self.assertEqual(proc.returncode, 2)
                self.assertFalse(adir.exists())


if __name__ == "__main__":
    unittest.main()
