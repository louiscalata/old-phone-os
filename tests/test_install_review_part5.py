# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest


def _read_install_sh() -> str:
    candidates = [
        pathlib.Path(__file__).resolve().parents[1] / "install.sh",
        pathlib.Path("install.sh"),
    ]
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("install.sh not found in expected locations")


class TestInstallReview(unittest.TestCase):
    def test_launcher_traversal_symlink_and_relative_rejected(self):
        text = _read_install_sh()
        self.assertRegex(text, r"\*/\./\*", "must reject dot path parts")
        self.assertRegex(text, r"\*/\.\./\*", "must reject dotdot path parts")
        self.assertIn("canon_file", text, "must resolve launcher through symlinks")
        self.assertRegex(text, r'canon_file "\$_manifest_launcher"', "must compare resolved manifest launcher")
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        key = "".join(["launch", "er="])

        def run_case(home, bindir):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
            )

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            trusted = bindir / "opos"
            trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            dotdot = "".join([str(bindir), "/sub/../opos"])
            (home / "manifest").write_text(
                key + dotdot + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            proc = run_case(home, bindir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(dotdot, proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertIn(
                "==> would run " + str(trusted) + " uninstall --dry-run",
                proc.stdout.splitlines(),
            )
            self.assertNotIn("would run " + dotdot, proc.stdout + proc.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            trusted = bindir / "opos"
            trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            outside = pathlib.Path(tmp) / "outside.sh"
            outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            outside.chmod(outside.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            link = bindir / "linked"
            os.symlink(str(outside), str(link))
            (home / "manifest").write_text(
                key + str(link) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            proc = run_case(home, bindir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(str(link), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertIn(
                "==> would run " + str(trusted) + " uninstall --dry-run",
                proc.stdout.splitlines(),
            )
            self.assertNotIn("would run " + str(link), proc.stdout + proc.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            trusted = bindir / "opos"
            trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            rel = "".join(["rel", "ative/opos"])
            (home / "manifest").write_text(
                key + rel + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            proc = run_case(home, bindir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(rel, proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertIn(
                "==> would run " + str(trusted) + " uninstall --dry-run",
                proc.stdout.splitlines(),
            )
            self.assertNotIn("would run " + rel, proc.stdout + proc.stderr)

    def test_untrusted_launcher_not_executed(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        key = "".join(["launch", "er="])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            home.mkdir()
            bindir.mkdir()
            marker = tmp_path / "marker"
            trusted = bindir / "opos"
            trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            rejected = bindir / "rejected"
            rejected.write_text("#!/bin/sh\n: > \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
            rejected.chmod(rejected.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            probe_bin = tmp_path / "probe_bin"
            probe_bin.mkdir()
            (probe_bin / "touch").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            (probe_bin / "touch").chmod(0o755)
            probe_env = dict(os.environ)
            probe_env["PATH"] = str(probe_bin) + os.pathsep + os.environ.get("PATH", "")
            if marker.exists():
                marker.unlink()
            subprocess.run([str(rejected)], env=probe_env, check=True)
            self.assertTrue(marker.is_file())
            marker.unlink()
            (home / "manifest").write_text(key + str(rejected) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n", encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            proc = subprocess.run(["sh", str(install), "--uninstall", "--dry-run"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(marker.exists())
            self.assertIn(str(rejected), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertIn("==> would run " + str(trusted) + " uninstall --dry-run", proc.stdout.splitlines())
            self.assertNotIn("would run " + str(rejected), proc.stdout + proc.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            home.mkdir()
            bindir.mkdir()
            marker = tmp_path / "marker2"
            rejected = bindir / "rejected"
            rejected.write_text("#!/bin/sh\n: > \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
            rejected.chmod(rejected.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            probe_bin = tmp_path / "probe_bin"
            probe_bin.mkdir()
            (probe_bin / "touch").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            (probe_bin / "touch").chmod(0o755)
            probe_env = dict(os.environ)
            probe_env["PATH"] = str(probe_bin) + os.pathsep + os.environ.get("PATH", "")
            if marker.exists():
                marker.unlink()
            subprocess.run([str(rejected)], env=probe_env, check=True)
            self.assertTrue(marker.is_file())
            marker.unlink()
            (home / "manifest").write_text(key + str(rejected) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n", encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            proc = subprocess.run(["sh", str(install), "--uninstall", "--dry-run"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 1)
            self.assertFalse(marker.exists())
            self.assertIn(str(rejected), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertIn("revert by hand", proc.stderr.lower())
            self.assertNotIn("would run " + str(rejected), proc.stdout + proc.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            faked = tmp_path / "fakebin"
            home.mkdir()
            bindir.mkdir()
            faked.mkdir()
            marker = tmp_path / "marker3"
            trusted = bindir / "opos"
            trusted_log = tmp_path / "trusted.log"
            trusted.write_text("#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> \"$TRUSTED_LOG\"\nexit 0\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            rejected = bindir / "rejected"
            rejected.write_text("#!/bin/sh\n: > \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
            rejected.chmod(rejected.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            probe_bin = tmp_path / "probe_bin"
            probe_bin.mkdir()
            (probe_bin / "touch").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            (probe_bin / "touch").chmod(0o755)
            probe_env = dict(os.environ)
            probe_env["PATH"] = str(probe_bin) + os.pathsep + os.environ.get("PATH", "")
            if marker.exists():
                marker.unlink()
            subprocess.run([str(rejected)], env=probe_env, check=True)
            self.assertTrue(marker.is_file())
            marker.unlink()
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(key + str(rejected) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n", encoding="utf-8")
            for name in ("cp", "ufw", "systemctl"):
                stub = faked / name
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["PATH"] = str(faked) + os.pathsep + env.get("PATH", "")
            env["TRUSTED_LOG"] = str(trusted_log)
            proc = subprocess.run(["sh", str(install), "--uninstall", "--force"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(marker.exists())
            self.assertIn(str(rejected), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertNotIn("would run " + str(rejected), proc.stdout + proc.stderr)
            self.assertTrue(trusted_log.is_file(), "trusted launcher must be invoked")
            logged = trusted_log.read_text(encoding="utf-8")
            self.assertIn(str(trusted), logged)
            self.assertIn("uninstall", logged)

    def test_trusted_path_symlink_outside_not_executed(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        key = "".join(["launch", "er="])
        venv_key = "".join(["ve", "nv="])
        repo_key = "".join(["re", "po="])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            home.mkdir()
            bindir.mkdir()
            marker = tmp_path / "marker"
            outside = tmp_path / "outside.sh"
            outside.write_text("#!/bin/sh\n: > \"" + str(marker) + "\"\nexit 42\n", encoding="utf-8")
            outside.chmod(0o755)
            launcher = bindir / "opos"
            os.symlink(str(outside), str(launcher))
            self.assertTrue(os.path.islink(str(launcher)))
            proc0 = subprocess.run([str(outside)], capture_output=True, text=True)
            self.assertEqual(proc0.returncode, 42)
            self.assertTrue(marker.is_file())
            marker.unlink()
            manifest = home / "manifest"
            manifest_text = key + str(launcher) + "\n" + venv_key + str(home / "venv") + "\n" + repo_key + "\n"
            manifest.write_text(manifest_text, encoding="utf-8")
            manifest_bytes = manifest.read_bytes()
            link_target = os.readlink(str(launcher))
            outside_bytes = outside.read_bytes()
            outside_mode = stat.S_IMODE(outside.stat().st_mode)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env)
            self.assertFalse(marker.exists())
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("revert by hand", proc.stderr.lower())
            self.assertEqual(manifest.read_bytes(), manifest_bytes)
            self.assertTrue(os.path.islink(str(launcher)))
            self.assertEqual(os.readlink(str(launcher)), link_target)
            self.assertEqual(outside.read_bytes(), outside_bytes)
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), outside_mode)

    def test_missing_trusted_launcher_refuses_then_force_cleans_safely(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        key = "".join(["launch", "er="])
        venv_key = "".join(["ve", "nv="])
        repo_key = "".join(["re", "po="])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos = tmp_path / "opos"
            bindir = tmp_path / "bin"
            home.mkdir()
            opos.mkdir()
            bindir.mkdir()
            marker = tmp_path / "marker"
            rejected = bindir / "rejected"
            rejected.write_text("#!/bin/sh\n: > \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
            rejected.chmod(0o755)
            subprocess.run([str(rejected)], check=True)
            self.assertTrue(marker.is_file())
            marker.unlink()
            venv = opos / "venv"
            venv.mkdir()
            owned = venv / "owned.dat"
            owned.write_text("owned\n", encoding="utf-8")
            manifest = opos / "manifest"
            manifest_text = key + str(rejected) + "\n" + venv_key + str(venv) + "\n" + repo_key + "\n"
            manifest.write_text(manifest_text, encoding="utf-8")
            opos_keep = opos / "keep.txt"
            opos_keep.write_text("home sentinel\n", encoding="utf-8")
            bin_keep = bindir / "keep.txt"
            bin_keep.write_text("bin sentinel\n", encoding="utf-8")
            rejected_text = rejected.read_text(encoding="utf-8")
            stubs = tmp_path / "stubs"
            stubs.mkdir()
            (stubs / "systemctl").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (stubs / "systemctl").chmod(0o755)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("ignoring untrusted launcher", proc.stderr)
            self.assertIn("revert by hand", proc.stderr.lower())
            self.assertFalse(marker.exists())
            self.assertEqual(manifest.read_text(encoding="utf-8"), manifest_text)
            self.assertEqual(rejected.read_text(encoding="utf-8"), rejected_text)
            self.assertTrue(owned.is_file())
            self.assertEqual(opos_keep.read_text(encoding="utf-8"), "home sentinel\n")
            self.assertEqual(bin_keep.read_text(encoding="utf-8"), "bin sentinel\n")
            proc = subprocess.run(["sh", str(install), "--uninstall", "--force"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("force uninstall without verified recovery entry point", proc.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(venv.exists())
            self.assertFalse(owned.exists())
            self.assertTrue(rejected.is_file())
            self.assertEqual(rejected.read_text(encoding="utf-8"), rejected_text)
            self.assertEqual(opos_keep.read_text(encoding="utf-8"), "home sentinel\n")
            self.assertEqual(bin_keep.read_text(encoding="utf-8"), "bin sentinel\n")
            self.assertTrue(opos.is_dir())

    def test_f929_launcher_tests_defined_only_in_part5(self):
        tests_dir = pathlib.Path(__file__).resolve().parent
        names_by_module = {}
        for path in sorted(tests_dir.glob("test_install_review*.py")):
            text = path.read_text(encoding="utf-8")
            names_by_module[path.name] = re.findall(r"^    def (test_\w+)\(", text, re.M)
        for name in (
            "test_launcher_traversal_symlink_and_relative_rejected",
            "test_untrusted_launcher_not_executed",
        ):
            found = [(mod, n) for mod, names in names_by_module.items() for n in names if n == name]
            self.assertEqual(found, [("test_install_review_part5.py", name)], msg="F929: " + name + " must be defined exactly once and only in part5")
        size = pathlib.Path(__file__).resolve().stat().st_size
        self.assertLess(size, 20000, msg=f"F929: test_install_review_part5.py must stay under 20000 bytes (got {size})")

if __name__ == "__main__":
    unittest.main()
