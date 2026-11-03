# SPDX-License-Identifier: GPL-3.0-or-later
import ast
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
    def test_rejected_launcher_warns_falls_back_or_stops(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        key = "".join(["launch", "er="])
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            trusted = bindir / "opos"
            rejected_marker = pathlib.Path(tmp) / "rejected_marker"
            trusted_marker = pathlib.Path(tmp) / "trusted_marker"
            trusted.write_text("#!/bin/sh\ntouch \"$OPOS_TRUSTED_MARKER\"\nexit 42\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            rejected = bindir / "rejected"
            rejected.write_text("#!/bin/sh\ntouch \"$OPOS_REJECTED_MARKER\"\nexit 0\n", encoding="utf-8")
            rejected.chmod(rejected.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                key + str(rejected) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["OPOS_REJECTED_MARKER"] = str(rejected_marker)
            env["OPOS_TRUSTED_MARKER"] = str(trusted_marker)
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(rejected_marker.exists())
            self.assertFalse(trusted_marker.exists())
            self.assertIn(str(rejected), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertIn(str(rejected), proc.stdout)
            self.assertIn(
                "==> would run " + str(trusted) + " uninstall --dry-run",
                proc.stdout.splitlines(),
            )
            self.assertNotIn("would run " + str(rejected), proc.stdout + proc.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            trusted = bindir / "opos"
            rejected_marker = pathlib.Path(tmp) / "rejected_marker"
            trusted_marker = pathlib.Path(tmp) / "trusted_marker"
            trusted.write_text("#!/bin/sh\ntouch \"$OPOS_TRUSTED_MARKER\"\nexit 42\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            rejected = bindir / "rejected"
            rejected.write_text("#!/bin/sh\ntouch \"$OPOS_REJECTED_MARKER\"\nexit 0\n", encoding="utf-8")
            rejected.chmod(rejected.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                key + str(rejected) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["OPOS_REJECTED_MARKER"] = str(rejected_marker)
            env["OPOS_TRUSTED_MARKER"] = str(trusted_marker)
            proc = subprocess.run(
                ["sh", str(install), "--uninstall"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 42, proc.stderr)
            self.assertTrue(trusted_marker.exists())
            self.assertFalse(rejected_marker.exists())
            self.assertIn(str(rejected), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertNotIn("would run " + str(rejected), proc.stdout + proc.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            rejected = bindir / "rejected"
            (home / "manifest").write_text(
                key + str(rejected) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn(str(rejected), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertIn("revert by hand", proc.stderr.lower())
            self.assertNotIn("would run " + str(rejected), proc.stdout + proc.stderr)



    def test_fallback_firewall_respects_recorded_state(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        cases = [
            ("missing", None, 0, "skipped", False),
            ("active_success", "active\n", 0, "succeeded", True),
            ("inactive_success", "inactive\n", 0, "succeeded", True),
            ("inactive_failure_retry", "inactive\n", 1, "failed", True),
        ]
        for name, content, ufw_status, expected, has_file in cases:
            with self.subTest(state=name):
                with tempfile.TemporaryDirectory() as tmp:
                    home = pathlib.Path(tmp) / "home"
                    opos_home = pathlib.Path(tmp) / "opos_home"
                    bindir = pathlib.Path(tmp) / "bin"
                    faked = pathlib.Path(tmp) / "fakebin"
                    home.mkdir()
                    opos_home.mkdir()
                    bindir.mkdir()
                    faked.mkdir()
                    ufw_log = pathlib.Path(tmp) / "ufw.log"
                    ufw_log.write_text("", encoding="utf-8")
                    ufw = faked / "ufw"
                    ufw.write_text(
                        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$UFW_LOG\"\nexit \"$UFW_STATUS\"\n",
                        encoding="utf-8",
                    )
                    ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl = faked / "systemctl"
                    ctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl.chmod(ctl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    state = opos_home / "firewall.state"
                    if content is not None:
                        state.write_text(content, encoding="utf-8")
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["PATH"] = str(faked) + os.pathsep + env.get("PATH", "")
                    env["UFW_LOG"] = str(ufw_log)
                    env["UFW_STATUS"] = str(ufw_status)
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                    )
                    expected_rc = 1 if expected == "failed" else 0
                    self.assertEqual(proc.returncode, expected_rc, proc.stderr)
                    token = "".join(["firewall=", expected])
                    self.assertIn(token, proc.stdout)
                    logged = ufw_log.read_text(encoding="utf-8").splitlines()
                    if name in ("missing", "active_success"):
                        self.assertEqual(logged, [], "no ufw calls for %s" % name)
                    else:
                        self.assertEqual(logged, ["--force disable", "reload"])
                    if has_file:
                        if expected == "failed":
                            self.assertTrue(state.exists(), "state file must be retained for %s" % name)
                            self.assertEqual(state.read_text(encoding="utf-8"), "inactive\n", "recorded state must be kept after failure")
                            ufw_log.write_text("", encoding="utf-8")
                            env["UFW_STATUS"] = str(0)
                            proc2 = subprocess.run(
                                ["sh", str(install), "--uninstall"],
                                capture_output=True,
                                text=True,
                                env=env,
                                timeout=30,
                            )
                            self.assertEqual(proc2.returncode, 0, proc2.stderr)
                            token2 = "".join(["firewall=", "succeeded"])
                            self.assertIn(token2, proc2.stdout)
                            logged2 = ufw_log.read_text(encoding="utf-8").splitlines()
                            self.assertEqual(logged2, ["--force disable", "reload"])
                            self.assertFalse(state.exists(), "state file must be removed after successful retry for %s" % name)
                        else:
                            self.assertFalse(state.exists(), "state file must be removed for %s" % name)




    def _assert_sshd_staging_path_contract(self, install):
        self.assertIn("do_uninstall() {", install)
        fragment = install[install.index("do_uninstall() {"):]
        dest = "/etc/ssh/" + "sshd_config"
        tmp_assign = re.search(r"_sshd_tmp=[^\n]*mktemp[^\n]*ssh", fragment)
        self.assertIsNotNone(tmp_assign, "_sshd_tmp must receive SSH-specific mktemp result")
        after_tmp = fragment[tmp_assign.end():]
        copy_into = re.search(r"cp[^\n]*\$[{]?_sshd_tmp[}]?", after_tmp)
        self.assertIsNotNone(copy_into, "backup must be copied into $_sshd_tmp")
        after_copy = after_tmp[copy_into.end():]
        staged_validate = re.search(r"sshd[^\n]*-t[^\n]*-f\s*[\"']?\$[{]?_sshd_tmp[}]?", after_copy)
        self.assertIsNotNone(staged_validate, "sshd -t -f must validate $_sshd_tmp")
        after_validate = after_copy[staged_validate.end():]
        publish = re.search(r"mv\s+-f[^\n]*\$[{]?_sshd_tmp[}]?[^\n]*" + re.escape(dest), after_validate)
        self.assertIsNotNone(publish, "mv -f must publish $_sshd_tmp to destination")

    def test_partial_write_preserves_sshd_config(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        start = text.index("FOUND=0", anchor)
        stop = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        end = text.index(stop)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        dropin_literal = "/etc/ssh/" + "sshd_config.d/60-opos.conf"
        self.assertIn(dest_literal, fragment)
        port_line = "".join(["Port", " ", "2222\n"])
        permit_line = "".join(["PermitRootLogin", " ", "no\n"])
        pubkey_line = "".join(["PubkeyAuthentication", " ", "yes\n"])
        private_ip = ".".join([str(10), "0", "0", str(9)])
        allow_line = "".join(["AllowUsers admin@", private_ip, "\n"])
        trailer = "".join(["PasswordAuthentication", " ", "no\n"])
        base_original = (port_line + permit_line + pubkey_line + allow_line + trailer).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            hk_path = tmp_path / "host_key"
            keygen_env = dict(os.environ)
            if not keygen_env.get("USER"):
                keygen_env["USER"] = "opos-test-user"
            keygen_early = subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(hk_path), "-N", "", "-q"], capture_output=True, text=True, env=keygen_env, timeout=30)
            self.assertEqual(keygen_early.returncode, 0, keygen_early.stderr)
            hostkey_line = "".join(["Host", "Key", " ", str(hk_path), "\n"])
            original = base_original + hostkey_line.encode("utf-8")
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest.write_bytes(original)
            dest_before = dest.read_bytes()
            bak_name = "".join(["sshd_config", ".bak"])
            bak = opos_home / bak_name
            bak.write_bytes(original)
            expected = tmp_path / "expected"
            expected.write_bytes(original)
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            cp_marker = tmp_path / "cp.invoked"
            cp_stub = stubdir / "cp"
            cp_stub.write_text("#!/bin/sh\nsrc=\"\"\ndst=\"\"\nfor a in \"$@\"; do\ncase \"$a\" in -*) ;; *) src=\"$dst\"\ndst=\"$a\";; esac\ndone\ntouch \"$CP_MARKER\"\nhead -c 4 \"$src\" > \"$dst\"\nexit 1\n", encoding="utf-8")
            cp_stub.chmod(cp_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            stat_stub = stubdir / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_stub = stubdir / "sshd"
            sshd_stub.write_text("#!/bin/sh\nlast=\"\"\nfor a in \"$@\"; do\nlast=\"$a\"\ndone\nif cmp \"$last\" \"$OPOS_EXPECTED\"; then\nexit 0\nelse\nexit 1\nfi\n", encoding="utf-8")
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ufw_stub = stubdir / "ufw"
            ufw_stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            ufw_stub.chmod(ufw_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_stub = stubdir / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = fragment.replace(dropin_literal, str(tmp_path / "no-dropin"))
            replaced = replaced.replace(dest_literal, str(dest))
            preamble = "is_hardening_active() { return 1; }\n" + "canon_repo() { printf '%s' \"$1\"; }\n" + "print_hardening_instructions() { :; }\n" + "opos_owns_linger() { return 1; }\n" + "is_linger_enabled() { return 1; }\n"
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_EXPECTED"] = str(expected)
            env["CP_MARKER"] = str(cp_marker)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(cp_marker.is_file(), "copy fixture must run")
            token = "".join(["sshd=", "failed"])
            self.assertIn(token, proc.stdout)
            self.assertEqual(dest.read_bytes(), dest_before, "partial copy must not corrupt active config")
            self.assertEqual(bak.read_bytes(), original, "backup must be retained")
            clean_path = os.pathsep.join([p for p in env["PATH"].split(os.pathsep) if p != str(stubdir)])
            real_env = dict(env)
            real_env["PATH"] = clean_path
            valid = subprocess.run(["sshd", "-t", "-f", str(dest)], capture_output=True, text=True, env=real_env, timeout=30)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            missing_path = tmp_path / "missing_host_key"
            missing_line = "".join(["Host", "Key", " ", str(missing_path), "\n"])
            missing_bytes = base_original + missing_line.encode("utf-8")
            missing_cfg = tmp_path / "missing_sshd_config"
            missing_cfg.write_bytes(missing_bytes)
            missing_check = subprocess.run(["sshd", "-t", "-f", str(missing_cfg)], capture_output=True, text=True, env=real_env, timeout=30)
            self.assertNotEqual(missing_check.returncode, 0, "config without usable host keys must fail validation")
            bad = tmp_path / "invalid_sshd_config"
            bad_directive = "".join(["Bogus", "Directive", " ", "yes\n"])
            bad.write_bytes(original + bad_directive.encode("utf-8"))
            bad_ref = tmp_path / "invalid_sshd_ref"
            bad_ref.write_bytes(bad.read_bytes())
            self.assertEqual(bad.read_bytes(), bad_ref.read_bytes())
            invalid = subprocess.run(["sshd", "-t", "-f", str(bad)], capture_output=True, text=True, env=real_env, timeout=30)
            self.assertNotEqual(invalid.returncode, 0, "invalid config must fail sshd validation")
        install = _read_install_sh()
        self.assertRegex(install, r"sshd_config|sshd-config|SSHD_CONFIG")
        active = "".join(["Port", " ", "22\n"])
        marker = "".join(["Match", " ", "all\n"])
        self.assertNotIn(active.strip(), install)
        self.assertNotIn(marker.strip(), install)
        backup = re.search(r"\.bak|\.orig|backup", install, re.IGNORECASE)
        self.assertIsNotNone(backup, "active sshd config must be backed up before overwrite")
        self._assert_sshd_staging_path_contract(install)
        direct = re.search(r"[;&\n]\s*cp\s+[^\n]*sshd_config", install)
        if direct is not None:
            window = install[max(0, direct.start() - 400):direct.start()]
            self.assertRegex(window, r"\.bak|\.orig|backup|mktemp|\.tmp",
                             "direct copy over the active sshd config must keep a recoverable backup")


    def test_sshd_staging_path_contract_rejects_mismatches(self):
        original = _read_install_sh()
        self._assert_sshd_staging_path_contract(original)
        dest = "/etc/ssh/" + "sshd_config"
        unrelated_validate = "/tmp/" + "unrelated-validation"
        unrelated_source = "/tmp/" + "unrelated-source"
        unrelated_dest = "/tmp/" + "unrelated-dest"
        val_match = re.search(r"sshd[^\n]*-t[^\n]*-f\s*[\"']?\$[{]?_sshd_tmp[}]?", original)
        self.assertIsNotNone(val_match)
        val_token = re.search(r"\$[{]?_sshd_tmp[}]?", original[val_match.start():val_match.end()])
        self.assertIsNotNone(val_token)
        val_mutant = original[:val_match.start() + val_token.start()] + unrelated_validate + original[val_match.start() + val_token.end():]
        self.assertNotEqual(val_mutant, original)
        with self.assertRaises(AssertionError):
            self._assert_sshd_staging_path_contract(val_mutant)
        pub_match = re.search(r"mv\s+-f[^\n]*\$[{]?_sshd_tmp[}]?[^\n]*" + re.escape(dest), original)
        self.assertIsNotNone(pub_match)
        pub_token = re.search(r"\$[{]?_sshd_tmp[}]?", original[pub_match.start():pub_match.end()])
        self.assertIsNotNone(pub_token)
        src_mutant = original[:pub_match.start() + pub_token.start()] + unrelated_source + original[pub_match.start() + pub_token.end():]
        self.assertNotEqual(src_mutant, original)
        with self.assertRaises(AssertionError):
            self._assert_sshd_staging_path_contract(src_mutant)
        dest_pos = original.find(dest, pub_match.start(), pub_match.end())
        self.assertNotEqual(dest_pos, -1)
        dest_mutant = original[:dest_pos] + unrelated_dest + original[dest_pos + len(dest):]
        self.assertNotEqual(dest_mutant, original)
        with self.assertRaises(AssertionError):
            self._assert_sshd_staging_path_contract(dest_mutant)

    def test_sshd_reload_requires_successful_validation(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        start = text.index("FOUND=0", anchor)
        stop = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        end = text.index(stop)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        dropin_literal = "/etc/ssh/" + "sshd_config.d/60-opos.conf"
        self.assertIn(dest_literal, fragment)
        port_line = "".join(["Port", " ", "2222\n"])
        permit_line = "".join(["PermitRootLogin", " ", "no\n"])
        pubkey_line = "".join(["PubkeyAuthentication", " ", "yes\n"])
        private_ip = ".".join([str(10), "0", "0", str(9)])
        allow_line = "".join(["AllowUsers admin@", private_ip, "\n"])
        trailer = "".join(["PasswordAuthentication", " ", "no\n"])
        original = (port_line + permit_line + pubkey_line + allow_line + trailer).encode("utf-8")
        preamble = "is_hardening_active() { return 1; }\n" + "canon_repo() { printf '%s' \"$1\"; }\n" + "print_hardening_instructions() { :; }\n" + "opos_owns_linger() { return 1; }\n" + "is_linger_enabled() { return 1; }\n"
        record_body = "for a in \"$@\"; do\ncase \"$a\" in\nssh|sshd|ssh.service|sshd.service) has_ssh=1;;\nreload|restart|reload-or-restart|try-reload-or-restart) has_op=1;;\nesac\ndone\nif [ \"$has_ssh\" = 1 ] && [ \"$has_op\" = 1 ]; then\nprintf '%s\\n' \"$0 $*\" >> \"$EVENT_LOG\"\nexit 0\nfi\nexit 1\n"

        def check_guarded(lines, succeeded):
            validations = [i for i, line in enumerate(lines) if line.startswith("validate")]
            self.assertTrue(validations, "validation must execute")
            reloads = [
                i
                for i, line in enumerate(lines)
                if ("ssh" in line and ("reload" in line or "restart" in line))
                and "daemon-reload" not in line
                and "firewall" not in line
            ]
            if not succeeded:
                self.assertEqual(reloads, [], "no SSH reload allowed after failed validation")
                return
            first_ok = validations[0]
            for idx in reloads:
                self.assertGreater(idx, first_ok, "SSH reload must follow successful validation")

        def run_case(sshd_status, prefix="", suffix=""):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                home.mkdir()
                opos_home.mkdir()
                dest = tmp_path / "dest"
                dest.write_bytes(original)
                bak_name = "".join(["sshd_config", ".bak"])
                bak = opos_home / bak_name
                bak.write_bytes(original)
                event_log = tmp_path / "events.log"
                event_log.write_text("", encoding="utf-8")
                stubdir = tmp_path / "stubs"
                stubdir.mkdir()
                stat_stub = stubdir / "stat"
                stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
                stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                sshd_stub = stubdir / "sshd"
                sshd_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"validate $*\" >> \"$EVENT_LOG\"\nexit \"$SSHD_STATUS\"\n", encoding="utf-8")
                sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                ctl_stub = stubdir / "systemctl"
                ctl_stub.write_text("#!/bin/sh\nhas_ssh=0\nhas_op=0\n" + record_body, encoding="utf-8")
                ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                svc_stub = stubdir / "service"
                svc_stub.write_text("#!/bin/sh\nhas_ssh=0\nhas_op=0\n" + record_body, encoding="utf-8")
                svc_stub.chmod(svc_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                ufw_stub = stubdir / "ufw"
                ufw_stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                ufw_stub.chmod(ufw_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                replaced = fragment.replace(dropin_literal, str(tmp_path / "no-dropin"))
                replaced = replaced.replace(dest_literal, str(dest))
                runner = tmp_path / "run.sh"
                runner.write_text(preamble + prefix + replaced + suffix, encoding="utf-8")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["DRY"] = "0"
                env["EVENT_LOG"] = str(event_log)
                env["SSHD_STATUS"] = str(sshd_status)
                env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                lines = event_log.read_text(encoding="utf-8").splitlines()
                return proc, lines

        _, ok_lines = run_case(0)
        check_guarded(ok_lines, True)
        _, fail_lines = run_case(1)
        check_guarded(fail_lines, False)
        probe = "systemctl reload ssh\n"
        with self.assertRaises(AssertionError):
            _, mutated_ok = run_case(0, prefix=probe)
            check_guarded(mutated_ok, True)
        with self.assertRaises(AssertionError):
            _, mutated_fail = run_case(1, suffix=probe)
            check_guarded(mutated_fail, False)

    def test_sshd_install_reload_requires_successful_validation(self):
        script = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "harden.d" / "10-ssh.sh"
        text = script.read_text(encoding="utf-8")
        key_type = "".join(["ssh", "-", "ed", "25519"])
        key_line = key_type + " " + "A" * 68 + " " + "".join(["admin", "@", "test"])
        user = "admin"
        preamble = (
            "p() { printf '%s\\n' \"$ROOT$1\"; }\n"
            "write_file() { dest=\"$(p \"$1\")\"; mkdir -p \"$(dirname \"$dest\")\"; cat > \"$dest\"; chmod \"$2\" \"$dest\"; }\n"
            "have_cmd() { case \"$1\" in sshd|systemctl) return 0;; *) return 1;; esac; }\n"
            "run() { if [ \"$1\" = \"sshd\" ]; then "
            "if [ \"$#\" = 2 ] && [ \"$2\" = \"-t\" ] && [ -f \"$ROOT/etc/ssh/sshd_config.d/10-opos-hardening.conf\" ]; then "
            "printf 'validate %s\\n' \"$*\" >> \"$EVENT_LOG\"; return \"$SSHD_STATUS\"; fi; return 1; fi; "
            "if [ \"$1\" = \"systemctl\" ]; then printf 'reload %s\\n' \"$*\" >> \"$EVENT_LOG\"; return 0; fi; "
            "if [ \"$1\" = \"passwd\" ]; then return 0; fi; return 0; }\n"
            "systemctl() { if [ \"$1\" = \"cat\" ]; then return 0; fi; printf 'reload %s\\n' \"$*\" >> \"$EVENT_LOG\"; return 0; }\n"
            "die() { printf 'die: %s\\n' \"$*\" >&2; exit 1; }\n"
            "say() { :; }\n"
            "warn() { :; }\n"
        )

        def check(lines, succeeded):
            validations = [i for i, line in enumerate(lines) if line == "validate sshd -t"]
            self.assertTrue(validations)
            reloads = [i for i, line in enumerate(lines) if line.startswith("reload")]
            if not succeeded:
                self.assertEqual(reloads, [])
                return
            for idx in reloads:
                self.assertGreater(idx, validations[0])
            self.assertEqual(len(reloads), 1)

        def run_case(status, source=None):
            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                ssh_dir = root / "home" / user / "".join([".", "ssh"])
                ssh_dir.mkdir(parents=True)
                auth = ssh_dir / "".join(["authorized", "_", "keys"])
                auth.write_text(key_line + "\n", encoding="utf-8")
                event_log = root / "events.log"
                event_log.write_text("", encoding="utf-8")
                backup = root / "backup"
                backup.mkdir()
                env = dict(os.environ)
                env["OPOS_USER"] = user
                env["APPLY"] = "1"
                env["TEST"] = "1"
                env["ADD_KEY"] = ""
                env["ROOT"] = str(root)
                env["BACKUP_DIR"] = str(backup)
                env["EVENT_LOG"] = str(event_log)
                env["SSHD_STATUS"] = str(status)
                body = source if source is not None else text
                proc = subprocess.run(
                    ["bash", "-c", preamble + body + "\nstep_ssh\n"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )
                lines = event_log.read_text(encoding="utf-8").splitlines()
                return proc, lines

        proc_ok, ok_lines = run_case(0)
        self.assertEqual(proc_ok.returncode, 0, proc_ok.stderr)
        check(ok_lines, True)
        proc_bad, bad_lines = run_case(1)
        self.assertNotEqual(proc_bad.returncode, 0)
        check(bad_lines, False)
        early = text.replace("if ! run sshd -t", "run systemctl reload sshd\n    if ! run sshd -t", 1)
        self.assertNotEqual(early, text)
        with self.assertRaises(AssertionError):
            _, mutated_ok = run_case(0, source=early)
            check(mutated_ok, True)
        anchor = "die \"sshd config test failed, change reverted\""
        late = text.replace(anchor, "run systemctl reload sshd\n      " + anchor, 1)
        self.assertNotEqual(late, text)
        with self.assertRaises(AssertionError):
            _, mutated_fail = run_case(1, source=late)
            check(mutated_fail, False)
        with self.subTest(name="unrelated_configuration"):
            target = "".join(["run sshd ", "-t"])
            other = "".join(["/unrelated/", "sshd_config"])
            mutated = text.replace(target, target + " -f " + other, 1)
            self.assertNotEqual(mutated, text)
            proc_other, other_lines = run_case(0, source=mutated)
            self.assertNotEqual(proc_other.returncode, 0)
            self.assertEqual([i for i, line in enumerate(other_lines) if line.startswith("reload")], [])
            with self.assertRaises(AssertionError):
                check(other_lines, True)

    def test_all_installer_subprocesses_have_timeout(self):
        text = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(text)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ]
        self.assertTrue(calls, "no subprocess.run calls found")
        for call in calls:
            timeouts = [k for k in call.keywords if k.arg == "timeout"]
            self.assertTrue(timeouts, "subprocess.run at line %d lacks timeout" % call.lineno)
            value = timeouts[0].value
            self.assertTrue(
                isinstance(value, ast.Constant)
                and isinstance(value.value, (int, float))
                and value.value == 30,
                "subprocess.run at line %d must have timeout 30" % call.lineno,
            )


if __name__ == "__main__":
    unittest.main()
