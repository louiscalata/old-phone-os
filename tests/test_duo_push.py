# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import re
import subprocess
import unittest
SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "push-to-duo.sh"
class DuoPushTest(unittest.TestCase):
    def test_missing_target_exits_2(self):
        proc = subprocess.run(["sh", str(SCRIPT), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage: push-to-duo.sh", proc.stderr)
    def test_dry_run_ipv6_link_local_forms(self):
        proc = subprocess.run(["sh", str(SCRIPT), "--dry-run", "admin@fe80::1%8"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        lines = proc.stdout.splitlines()
        archive = [x for x in lines if x.startswith("+ git archive ") and x.endswith(" HEAD")]
        self.assertEqual(len(archive), 1)
        copy = [x for x in lines if x.startswith("+ scp -q ") and x.endswith(" admin@[fe80::1%8]:<remote-tmp>/opos-push.tgz")]
        self.assertEqual(len(copy), 1)
        login = [x for x in lines if x.startswith("+ ssh ") and x.endswith("admin@fe80::1%8 sh -s -- <remote-tmp>")]
        self.assertEqual(len(login), 1)
        self.assertNotIn("[", login[0])
    def test_remote_staging_and_install_lock_static(self):
        text = SCRIPT.read_text()
        self.assertIn("mktemp -d /tmp/opos-push.XXXXXXXX", text)
        self.assertNotIn("/tmp/opos-push.tgz", text.split("<<'REMOTE'")[0])
        self.assertGreaterEqual(text.count("$remote_dir/opos-push.tgz"), 2)
        self.assertIn('stage="$1"', text)
        self.assertIn('tar -xzf "$stage/opos-push.tgz"', text)
        self.assertIn('rm -rf "$stage"', text)
        self.assertIn('rm -rf "$remote_dir"', text)
        self.assertIn('if ! mkdir "$HOME/.opos-push.lock"', text)
        self.assertIn('rmdir "$HOME/.opos-push.lock"', text)
        lock_mkdir = text.index('mkdir "$HOME/.opos-push.lock"')
        lock_trap = text.index('rmdir "$HOME/.opos-push.lock"')
        replace = text.index('mv "$HOME/opos"')
        install = text.index("sh install.sh --no-launch --yes")
        self.assertLess(lock_mkdir, replace)
        self.assertLess(lock_trap, replace)
        self.assertLess(lock_mkdir, install)
        self.assertLess(lock_trap, install)
    def test_script_text_static(self):
        text = SCRIPT.read_text()
        self.assertEqual(text.splitlines()[1], "# SPDX-License-Identifier: GPL-3.0-or-later")
        self.assertIn("opos.bak-", text)
        self.assertIn("sh install.sh --no-launch --yes", text)
        self.assertIsNone(re.search(r"(?<![\d.])(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))(\.\d{1,3}){2,3}(?![\d.])", text))
        self.assertNotIn("http://", text)
        proc = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
    def test_remote_script_prunes_backups_and_checks_space_before_moving_install(self):
        text = SCRIPT.read_text()
        body = text.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
        mv_at = body.index('mv "$HOME/opos" "$HOME/opos.bak-')
        for needle in ['df -Pk "$HOME"', 'du -k "$stage/opos-push.tgz"', "reserve_kb=262144", "keep=2", "not enough free space", 'rm -f "$stage/opos-push.tgz"', "opos.bak-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]", '/install.sh"', "rm -rf"]:
            self.assertIn(needle, body)
            self.assertLess(body.index(needle), mv_at)
        self.assertLess(body.index('df -Pk "$HOME"'), body.index('mkdir "$HOME/opos"'))
    def test_remote_archive_paths_are_stage_scoped_static(self):
        text = SCRIPT.read_text()
        body = text.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
        self.assertNotIn("/tmp/opos-push.tgz", text)
        self.assertIn('du -k "$stage/opos-push.tgz"', body)
        self.assertIn('rm -f "$stage/opos-push.tgz"', body)
        self.assertIn('tar -xzf "$stage/opos-push.tgz"', body)
    def test_deployment_propagates_installer_failure(self):
        import os
        import stat
        import tarfile
        import tempfile
        with tempfile.TemporaryDirectory() as top:
            bin_dir = os.path.join(top, "bin")
            os.mkdir(bin_dir)
            remote_home = os.path.join(top, "remote-home")
            os.mkdir(remote_home)
            stage_dir = os.path.join(top, "stage")
            os.mkdir(stage_dir)
            staging_archive = os.path.join(stage_dir, "opos-push.tgz")
            fixture = os.path.join(top, "fixture.tgz")
            git_log = os.path.join(top, "git-args")
            scp_log = os.path.join(top, "scp-args")
            ssh_log = os.path.join(top, "ssh-args")
            sentinel_dir = os.path.join(remote_home, "opos")
            os.mkdir(sentinel_dir)
            sentinel_body = "sentinel-original\n"
            with open(os.path.join(sentinel_dir, "sentinel"), "w") as handle:
                handle.write(sentinel_body)
            installer_src = os.path.join(top, "install.sh")
            with open(installer_src, "w") as handle:
                handle.write("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$HOME/install-args\"\nexit 37\n")
            with tarfile.open(fixture, "w:gz") as tar:
                tar.add(installer_src, arcname="install.sh")
            with open(os.path.join(bin_dir, "git"), "w") as handle:
                handle.write("#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" > \"$OPOS_GIT_LOG\"\no=\"\"\nprev=\"\"\nfor a in \"$@\"; do\nif [ \"$prev\" = \"-o\" ]; then\no=\"$a\"\nfi\nprev=\"$a\"\ndone\ncp \"$OPOS_FIXTURE\" \"$o\"\n")
            with open(os.path.join(bin_dir, "scp"), "w") as handle:
                handle.write("#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" > \"$OPOS_SCP_LOG\"\nsrc=\"\"\nfor a in \"$@\"; do\ncase \"$a\" in\n-*) ;;\n*)\nif [ -f \"$a\" ]; then\nsrc=\"$a\"\nfi\n;;\nesac\ndone\ncp \"$src\" \"$OPOS_STAGE_ARCHIVE\"\n")
            with open(os.path.join(bin_dir, "ssh"), "w") as handle:
                handle.write("#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" >> \"$OPOS_SSH_LOG\"\ntmp_in=\"$(mktemp)\"\ncat > \"$tmp_in\"\nif [ ! -s \"$tmp_in\" ]; then\ncase \"$*\" in\n*mktemp*)\nprintf '%s\\n' \"$OPOS_STAGE_DIR\"\nrm -f \"$tmp_in\"\nexit 0\n;;\nesac\nfi\ntmp_out=\"$(mktemp)\"\nsed \"s|/tmp/opos-push.tgz|$OPOS_STAGE_ARCHIVE|g\" \"$tmp_in\" > \"$tmp_out\"\nif [ \"$#\" -ne 5 ]; then\nrm -f \"$tmp_in\" \"$tmp_out\"\nexit 64\nfi\nif [ \"$1\" != \"$OPOS_EXPECT_TARGET\" ]; then\nrm -f \"$tmp_in\" \"$tmp_out\"\nexit 64\nfi\nif [ \"$2\" != \"sh\" ]; then\nrm -f \"$tmp_in\" \"$tmp_out\"\nexit 64\nfi\nif [ \"$3\" != \"-s\" ]; then\nrm -f \"$tmp_in\" \"$tmp_out\"\nexit 64\nfi\nif [ \"$4\" != \"--\" ]; then\nrm -f \"$tmp_in\" \"$tmp_out\"\nexit 64\nfi\nif [ \"$5\" != \"$OPOS_STAGE_DIR\" ]; then\nrm -f \"$tmp_in\" \"$tmp_out\"\nexit 64\nfi\nshift\nHOME=\"$OPOS_REMOTE_HOME\" \"$@\" < \"$tmp_out\"\ncode=$?\nrm -f \"$tmp_in\" \"$tmp_out\"\nexit \"$code\"\n")
            for name in ("git", "scp", "ssh"):
                path = os.path.join(bin_dir, name)
                mode = os.stat(path).st_mode
                os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = os.environ.copy()
            env["PATH"] = bin_dir + os.pathsep + env["PATH"]
            env["OPOS_FIXTURE"] = fixture
            env["OPOS_GIT_LOG"] = git_log
            env["OPOS_SCP_LOG"] = scp_log
            env["OPOS_SSH_LOG"] = ssh_log
            env["OPOS_STAGE_DIR"] = stage_dir
            env["OPOS_STAGE_ARCHIVE"] = staging_archive
            env["OPOS_REMOTE_HOME"] = remote_home
            target = "admin" + "@" + "example." + "invalid"
            env["OPOS_EXPECT_TARGET"] = target
            ssh_stub = os.path.join(bin_dir, "ssh")
            for case in ("omitted_stage", "corrupted_stage"):
                with self.subTest(case):
                    marker = os.path.join(top, case + "-marker")
                    body = "touch '" + marker + "'\n"
                    if case == "omitted_stage":
                        args = [ssh_stub, target, "sh", "-s", "--"]
                    else:
                        args = [ssh_stub, target, "sh", "-s", "--", stage_dir + "-bad"]
                    stub_proc = subprocess.run(args, input=body, capture_output=True, text=True, env=env)
                    self.assertEqual(stub_proc.returncode, 64)
                    self.assertFalse(os.path.exists(marker))
            proc = subprocess.run(["sh", str(SCRIPT), "admin@example.invalid", "HEAD"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 37)
            with open(git_log) as handle:
                git_args = handle.read()
            self.assertIn("archive", git_args)
            self.assertIn("-o", git_args)
            self.assertIn("HEAD", git_args)
            with open(scp_log) as handle:
                scp_args = handle.read()
            self.assertIn("-q", scp_args)
            self.assertIn("opos-push.tgz", scp_args)
            self.assertIn("admin@example.invalid", scp_args)
            with open(ssh_log) as handle:
                ssh_args = handle.read()
            self.assertIn("admin@example.invalid", ssh_args)
            self.assertIn(target + " sh -s -- " + stage_dir, ssh_args.splitlines())
            self.assertFalse(any(name.startswith("opos.bak-") for name in os.listdir(remote_home)))
            with open(os.path.join(remote_home, "opos", "sentinel")) as handle:
                self.assertEqual(handle.read(), sentinel_body)
            self.assertFalse(os.path.exists(os.path.join(remote_home, "opos", "install.sh")))
            self.assertIn("push-to-duo: install failed, restored previous deployment", proc.stderr)
            with open(os.path.join(remote_home, "install-args")) as handle:
                self.assertEqual(handle.read().split(), ["--no-launch", "--yes"])
            self.assertFalse(os.path.exists(staging_archive))
            parts = git_args.split()
            local_archive = parts[parts.index("-o") + 1]
            self.assertFalse(os.path.exists(local_archive))
            self.assertFalse(os.path.exists(os.path.dirname(local_archive)))
    def test_failed_install_restores_previous_deployment(self):
        import os
        import tarfile
        import tempfile
        text = SCRIPT.read_text()
        body = text.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
        with tempfile.TemporaryDirectory() as top:
            home = os.path.join(top, "home")
            os.mkdir(home)
            prev = os.path.join(home, "opos")
            os.mkdir(prev)
            with open(os.path.join(prev, "marker.txt"), "w") as handle:
                handle.write("old")
            stage = os.path.join(top, "stage")
            os.mkdir(stage)
            installer = os.path.join(top, "install.sh")
            with open(installer, "w") as handle:
                handle.write("exit 1\n")
            archive = os.path.join(stage, "opos-push.tgz")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(installer, arcname="install.sh")
            env = os.environ.copy()
            env["HOME"] = home
            proc = subprocess.run(["sh", "-s", "--", stage], input=body, capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 1)
            with open(os.path.join(home, "opos", "marker.txt")) as handle:
                self.assertEqual(handle.read(), "old")
            self.assertFalse(os.path.exists(os.path.join(home, "opos", "install.sh")))
            self.assertFalse(any(name.startswith("opos.bak-") for name in os.listdir(home)))
            self.assertFalse(os.path.exists(os.path.join(home, ".opos-push.lock")))
