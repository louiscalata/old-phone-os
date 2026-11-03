# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


def _read_install_sh() -> str:
    candidates = [
        pathlib.Path(__file__).resolve().parents[1] / "install.sh",
        pathlib.Path("install.sh"),
    ]
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("install.sh not found in expected locations")


def _assert_isolated_shell_paths(text, tmp_path):
    lits = set()
    for pat in (r'"(/[^\s"]*)"', r"'(/[^\s']*)'"):
        for m in re.finditer(pat, text):
            lits.add(m.group(1))
    for m in re.finditer(r"(?<![A-Za-z0-9_/$\"'=!.:%})-])(/[^\s'\";|&<>()`]*)", text):
        lits.add(m.group(1))
    root = os.path.realpath(tmp_path)
    bad = []
    for p in sorted(lits):
        if p in ("/dev/null", "/var/backups/opos-harden"):
            continue
        if p in ("/", "/.", "/..") or "*" in p:
            continue
        if "$" in p or "`" in p:
            bad.append(p)
            continue
        try:
            inside = os.path.commonpath([root, os.path.realpath(p)]) == root
        except ValueError:
            inside = False
        if not inside:
            bad.append(p)
    env_roots = ("HOME", "OPOS_HOME", "OPOS_BIN_DIR", "OPOS_ROOT", "XDG_DATA_HOME")
    exp_re = re.compile(r"(?:\$([A-Za-z_][A-Za-z0-9_]*)|\$\{([A-Za-z_][A-Za-z0-9_]*))([^\s'\";|&<>()`]*)")
    for m in exp_re.finditer(text):
        var = m.group(1) or m.group(2)
        rest = m.group(3)
        if "/" not in rest:
            continue
        if re.search(r"(?:^|/)\.\.(?:/|$)", rest) is not None:
            bad.append(m.group(0))
        elif var not in env_roots and re.search(r"(?<![A-Za-z0-9_])" + re.escape(var) + "=", text) is None:
            bad.append(m.group(0))
    if bad:
        raise AssertionError("shell-path scan must fail closed on unrecognized paths: %s" % sorted(set(bad)))
    probe = "/var/backups/opos-harden"
    assign_re = re.compile(r"^_hard_backups=(\"[^\"]*\"|'[^']*'|[^ \t;#|&$`<>]+)$")
    mutate_re = re.compile(r"(^|[;\s&|`$(\|])(rm|mv|rmdir|dd|shred|mkfs|truncate|tee|chmod|chown|touch|cp|ln|mkdir|mknod|install|rsync|tar|unzip|zip|cpio|curl|wget|git)\b")
    alias_re = re.compile(r"^\s*(?:export\s+|local\s+|declare\s+|typeset\s+)?[A-Za-z_][A-Za-z0-9_]*=.*\$\{?_hard_backups")
    exec_re = re.compile(r"(^|[;\s&|`$(\|])(eval|exec|sudo|sh|bash|dash|xargs|env)\b")
    readonly_re = re.compile(r"(^\s*test\b|^\s*\[|^\s*\[\[|^\s*if\b|\bthen\b|\bcase\b|/dev/null|\btest\b|\bls\b|\bstat\b|\bfind\b|\bcat\b|\bgrep\b|\bhead\b|\btail\b|\bwc\b|\bdiff\b|\bcmp\b|\bbasename\b|\bdirname\b|\bprintf\b|\becho\b|:)")
    found_probe_assign = False
    for line in text.splitlines():
        stripped = line.strip()
        if "_hard_backups" not in line and probe not in line:
            continue
        if stripped.startswith("_hard_backups="):
            if assign_re.match(stripped) is None:
                raise AssertionError("read-only probe must be a standalone _hard_backups assignment: %r" % line)
            if probe not in stripped:
                raise AssertionError("backup assignment must contain a literal path: %r" % line)
            if "$(" in stripped or "`" in stripped or "${" in stripped:
                raise AssertionError("backup assignment must not use shell expansion: %r" % line)
            if probe in line:
                found_probe_assign = True
            if mutate_re.search(stripped) is not None:
                raise AssertionError("backup assignment line must not execute a modifying command: %r" % line)
            continue
        if probe in line:
            raise AssertionError("read-only probe must only appear as _hard_backups assignment: %r" % line)
        if re.search(r"\$\{_hard_backups[^}]*\}|\$_hard_backups", line) is not None:
            if alias_re.match(stripped) is not None and not stripped.startswith("_hard_backups="):
                raise AssertionError("backup path must not be aliased: %r" % line)
            if mutate_re.search(line) is not None or ">" in line or "<" in line:
                raise AssertionError("backup path use must be read-only: %r" % line)
            if exec_re.search(line) is not None:
                raise AssertionError("backup path use must be read-only: %r" % line)
            if readonly_re.search(line) is None:
                raise AssertionError("backup path use must be explicitly recognized read-only: %r" % line)
    if probe in text and not found_probe_assign:
        raise AssertionError("read-only probe must only appear as _hard_backups assignment")


def _assert_execution_confined(patched, tmp_path, stubdir, home, opos_home, bindir):
    root = os.path.realpath(tmp_path)
    for p in (home, opos_home, bindir, stubdir):
        if os.path.commonpath([root, os.path.realpath(p)]) != root:
            raise AssertionError("env root must stay inside isolation: %s" % p)
    for name in ("ufw", "iptables", "ip6tables", "nft", "firewall-cmd", "systemctl", "loginctl", "service"):
        s = pathlib.Path(stubdir) / name
        if not s.is_file() or not os.access(s, os.X_OK):
            raise AssertionError("missing confined service stub: %s" % name)
    _assert_isolated_shell_paths(patched, tmp_path)


def _extract_sshd_fragment():
    text = _read_install_sh()
    start_tok = '_sshd="skipped"'
    end_tok = 'if [ -f "$OPOS_HOME/firewall.state" ]'
    if start_tok not in text:
        raise AssertionError("missing sshd start")
    if end_tok not in text:
        raise AssertionError("missing sshd end")
    start = text.index(start_tok)
    line_start = text.rfind("\n", 0, start) + 1
    end = text.index(end_tok, start)
    end_line = text.rfind("\n", 0, end) + 1
    frag = text[line_start:end_line]
    if start_tok not in frag:
        raise AssertionError("missing sshd start in fragment")
    if end_tok in frag:
        raise AssertionError("fragment must stop before firewall check")
    return frag


def _run_sshd_isolated(tmp_path, dropin_bytes, expected_bytes, extra="", extra_env=None):
    frag = _extract_sshd_fragment()
    m_dropin = re.search(r'_sshd_dropin="([^"]+)"', frag)
    if m_dropin is None:
        raise AssertionError("dropin not found")
    dropin_template = m_dropin.group(1)
    m_expected = re.search(r'_sshd_expected="([^"]+)"', frag)
    if m_expected is None:
        raise AssertionError("expected not found")
    expected_template = m_expected.group(1)
    m_preserved = re.search(r'_sshd_preserved="([^"]+)"', frag)
    if m_preserved is None:
        raise AssertionError("preserved not found")
    preserved_template = m_preserved.group(1)
    home = tmp_path / "home"
    opos_home = tmp_path / "opos_home"
    etc_ssh = tmp_path / "etc_ssh"
    workdir = tmp_path / "work"
    fakebin = tmp_path / "fakebin"
    deny_log = tmp_path / "deny.log"
    for p in (home, opos_home, etc_ssh, workdir, fakebin):
        p.mkdir(parents=True, exist_ok=True)
    deny_log.write_text("", encoding="utf-8")
    etc_real = os.path.realpath(str(etc_ssh))
    opos_real = os.path.realpath(str(opos_home))
    real_cp = shutil.which("cp") or "/bin/cp"
    real_rm = shutil.which("rm") or "/bin/rm"
    real_head = shutil.which("head") or "/usr/bin/head"
    real_cmp = shutil.which("cmp") or "/usr/bin/cmp"
    real_cat = shutil.which("cat") or "/bin/cat"
    real_py = shutil.which("python3") or "/usr/bin/python3"
    real_sh = shutil.which("sh") or "/bin/sh"
    dropin_path = pathlib.Path(dropin_template.replace("/etc/ssh/", str(etc_ssh) + "/"))
    expected_path = pathlib.Path(expected_template.replace("$OPOS_HOME", str(opos_home)))
    preserved_path = pathlib.Path(preserved_template.replace("$OPOS_HOME", str(opos_home)))
    dropin_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    if dropin_bytes is not None:
        dropin_path.write_bytes(dropin_bytes)
    elif dropin_path.exists() or dropin_path.is_symlink():
        dropin_path.unlink()
    if expected_bytes is not None:
        expected_path.write_bytes(expected_bytes)
    elif expected_path.exists() or expected_path.is_symlink():
        expected_path.unlink()
    for name in ("sshd_config.bak", "sshd_dropin"):
        cand = opos_home / name
        if cand.exists() or cand.is_symlink():
            cand.unlink()
    if preserved_path.exists() or preserved_path.is_symlink():
        preserved_path.unlink()
    cp_text = (
        "#!/bin/sh\n"
        "set -eu\n"
        "_deny=\"" + str(deny_log) + "\"\n"
        "_fix=\"" + etc_real + "\"\n"
        "_opos=\"" + opos_real + "\"\n"
        "_real=\"" + real_cp + "\"\n"
        "_py=\"" + real_py + "\"\n"
        "_dashdash=0\n"
        "for _a in \"$@\"; do\n"
        "  if [ \"$_dashdash\" -eq 0 ]; then\n"
        "    case \"$_a\" in\n"
        "      --) _dashdash=1; continue;;\n"
        "      -p|-f) continue;;\n"
        "      -*) printf 'denied: cp %s\\n' \"$*\" >> \"$_deny\"; exit 1;;\n"
        "    esac\n"
        "  fi\n"
        "  if ! _resolved=\"$(\"$_py\" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' \"$_a\")\"; then\n"
        "    printf 'denied: cp %s\\n' \"$*\" >> \"$_deny\"\n"
        "    exit 1\n"
        "  fi\n"
        "  case \"$_resolved\" in\n"
        "    \"$_fix\"|\"$_fix\"/*|\"$_opos\"|\"$_opos\"/*) ;;\n"
        "    *) printf 'denied: cp %s\\n' \"$*\" >> \"$_deny\"; exit 1;;\n"
        "  esac\n"
        "done\n"
        "exec \"$_real\" \"$@\"\n"
    )
    rm_text = (
        "#!/bin/sh\n"
        "set -eu\n"
        "_deny=\"" + str(deny_log) + "\"\n"
        "_fix=\"" + etc_real + "\"\n"
        "_opos=\"" + opos_real + "\"\n"
        "_real=\"" + real_rm + "\"\n"
        "_py=\"" + real_py + "\"\n"
        "_dashdash=0\n"
        "for _a in \"$@\"; do\n"
        "  if [ \"$_dashdash\" -eq 0 ]; then\n"
        "    case \"$_a\" in\n"
        "      --) _dashdash=1; continue;;\n"
        "      -*) continue;;\n"
        "    esac\n"
        "  fi\n"
        "  if ! _resolved=\"$(\"$_py\" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' \"$_a\")\"; then\n"
        "    printf 'denied: rm %s\\n' \"$*\" >> \"$_deny\"\n"
        "    exit 1\n"
        "  fi\n"
        "  case \"$_resolved\" in\n"
        "    \"$_fix\"|\"$_fix\"/*|\"$_opos\"|\"$_opos\"/*) ;;\n"
        "    *) printf 'denied: rm %s\\n' \"$*\" >> \"$_deny\"; exit 1;;\n"
        "  esac\n"
        "done\n"
        "exec \"$_real\" \"$@\"\n"
    )
    (fakebin / "cp").write_text(cp_text, encoding="utf-8")
    (fakebin / "rm").write_text(rm_text, encoding="utf-8")
    (fakebin / "head").write_text("#!/bin/sh\nset -eu\nexec \"" + real_head + "\" \"$@\"\n", encoding="utf-8")
    (fakebin / "cmp").write_text("#!/bin/sh\nset -eu\nexec \"" + real_cmp + "\" \"$@\"\n", encoding="utf-8")
    (fakebin / "cat").write_text("#!/bin/sh\nset -eu\nexec \"" + real_cat + "\" \"$@\"\n", encoding="utf-8")
    for name in ("ufw", "iptables", "nft", "systemctl", "loginctl"):
        (fakebin / name).write_text(
            "#!/bin/sh\nset -eu\n_deny=\"" + str(deny_log) + "\"\nprintf 'denied: " + name + " %s\\n' \"$*\" >> \"$_deny\"\nexit 1\n",
            encoding="utf-8",
        )
    for p in fakebin.iterdir():
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    frag_rm = frag.replace("/etc/ssh/", str(etc_ssh) + "/")
    run_path = tmp_path / "run_sshd.sh"
    run_path.write_text(
        "set -u\n"
        "OPOS_HOME=\"" + str(opos_home) + "\"\n"
        "export OPOS_HOME\n"
        "HOME=\"" + str(home) + "\"\n"
        "export HOME\n"
        + frag_rm + "\n"
        + extra + "\n"
        "printf \"sshd=%s\\n\" \"$_sshd\"\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["OPOS_HOME"] = str(opos_home)
    env["PATH"] = str(fakebin)
    if extra_env is not None:
        for k, v in extra_env.items():
            env[k] = v
    proc = subprocess.run([real_sh, str(run_path)], capture_output=True, text=True, env=env, cwd=str(workdir))
    denied = deny_log.read_text(encoding="utf-8") if deny_log.is_file() else ""
    m = re.search(r"sshd=(\S+)", proc.stdout)
    sshd = m.group(1) if m else ""
    info = {"dropin": dropin_path, "expected": expected_path, "preserved": preserved_path, "etc": etc_ssh, "opos": opos_home}
    return proc, sshd, denied, info


class TestInstallReview(unittest.TestCase):
    def test_unresolved_sshd_preserves_firewall_and_recovery_files(self):
        text = _read_install_sh()
        m_dropin = re.search(r'_sshd_dropin="([^"]+)"', text)
        self.assertIsNotNone(m_dropin, "dropin path not found")
        dropin_template = m_dropin.group(1)
        m_marker = re.search(r'_sshd_marker="([^"]+)"', text)
        self.assertIsNotNone(m_marker, "marker not found")
        marker = m_marker.group(1)
        m_expected = re.search(r'_sshd_expected="([^"]+)"', text)
        self.assertIsNotNone(m_expected, "expected path not found")
        expected_template = m_expected.group(1)
        m_preserved = re.search(r'_sshd_preserved="([^"]+)"', text)
        self.assertIsNotNone(m_preserved, "preserved path not found")
        preserved_template = m_preserved.group(1)
        private_ip = ".".join([str(10), "0", "0", str(9)])
        admin_line = "".join(["AllowUsers admin@", private_ip, "\n"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos_home"
            bindir = tmp_path / "bin"
            etc_fixture = tmp_path / "etc_ssh"
            stubdir = tmp_path / "stubs"
            home.mkdir()
            opos_home.mkdir()
            bindir.mkdir()
            etc_fixture.mkdir()
            stubdir.mkdir()
            expected_content = marker + "\nPort 2222\n"
            expected_path = pathlib.Path(expected_template.replace("$OPOS_HOME", str(opos_home)))
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_text(expected_content, encoding="utf-8")
            fixture_dropin_str = dropin_template.replace("/etc/ssh/", str(etc_fixture) + "/")
            opos_dropin = pathlib.Path(fixture_dropin_str)
            opos_dropin.parent.mkdir(parents=True, exist_ok=True)
            dropin_content = marker + "\nPort 2222\n" + admin_line
            opos_dropin.write_text(dropin_content, encoding="utf-8")
            backup = opos_home / "sshd_config.bak"
            backup.write_text("Port 22\n", encoding="utf-8")
            state = opos_home / "firewall.state"
            state.write_text("enabled\n", encoding="utf-8")
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv = opos_home / "venv"
            venv.mkdir()
            manifest = opos_home / "manifest"
            manifest.write_text("launcher=" + str(launcher) + "\nvenv=" + str(venv) + "\n", encoding="utf-8")
            snapshots = {}
            for p in (opos_dropin, expected_path, backup, state, manifest, launcher):
                snapshots[str(p)] = p.read_bytes()
            log = tmp_path / "calls.log"
            log.write_text("", encoding="utf-8")
            for name in ("ufw", "iptables", "ip6tables", "nft", "firewall-cmd", "systemctl", "loginctl", "service"):
                stub = stubdir / name
                stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> \"" + str(log) + "\"\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            patched = text.replace("/etc/ssh/", str(etc_fixture) + "/")
            _assert_execution_confined(patched, tmp_path, stubdir, home, opos_home, bindir)
            script = tmp_path / "install_copy.sh"
            script.write_text(patched, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(stubdir) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(script), "--uninstall"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            for path_str, before in snapshots.items():
                self.assertEqual(pathlib.Path(path_str).read_bytes(), before)
            self.assertEqual(log.read_text(encoding="utf-8"), "")
            self.assertIn("manual SSH restoration required", proc.stderr)
            self.assertIn(str(backup), proc.stderr)
            preserved_path = pathlib.Path(preserved_template.replace("$OPOS_HOME", str(opos_home)))
            self.assertFalse(preserved_path.exists())

    def test_sshd_snippet_confined_to_isolation_with_allowlist(self):
        text = _read_install_sh()
        m_marker = re.search(r'_sshd_marker="([^"]+)"', text)
        self.assertIsNotNone(m_marker, "marker not found")
        marker = m_marker.group(1)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            good = (marker + "\nPort 2222\n").encode("utf-8")
            proc, sshd, denied, info = _run_sshd_isolated(tmp_path, good, good, extra="")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(sshd, "succeeded", proc.stdout + proc.stderr)
            self.assertFalse(info["dropin"].exists(), "managed drop-in must be removed")
            self.assertEqual(denied, "", "no operation must be denied")
            self.assertFalse((info["opos"] / "sshd_config.bak").exists())
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            dropin_bytes = (marker + "\nPort 2222\n").encode("utf-8")
            other = (marker + "\nPort 2223\n").encode("utf-8")
            proc, sshd, denied, info = _run_sshd_isolated(tmp_path, dropin_bytes, other, extra="")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(sshd, "failed", proc.stdout + proc.stderr)
            self.assertTrue(info["dropin"].is_file(), "drop-in must remain when content differs")
            self.assertEqual(info["dropin"].read_bytes(), dropin_bytes)
            self.assertTrue(info["preserved"].is_file(), "preserved copy must exist")
            self.assertEqual(info["preserved"].read_bytes(), dropin_bytes)
            self.assertEqual(denied, "", "preservation inside isolation must not be denied")

    def test_sshd_isolation_rejects_escape_operands_and_services(self):
        text = _read_install_sh()
        m_marker = re.search(r'_sshd_marker="([^"]+)"', text)
        self.assertIsNotNone(m_marker, "marker not found")
        marker = m_marker.group(1)
        base = (marker + "\nPort 2222\n").encode("utf-8")
        for name in ("unquoted", "env", "dotdot", "symlink", "systemctl"):
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    outside = tmp_path / "outside"
                    outside.mkdir()
                    sentinel = outside / "sentinel"
                    sent_bytes = "".join(["out", "side-", "sent", "inel-", name]).encode("utf-8")
                    sentinel.write_bytes(sent_bytes)
                    etc_pre = tmp_path / "etc_ssh"
                    etc_pre.mkdir(parents=True, exist_ok=True)
                    link = etc_pre / "link"
                    if link.is_symlink() or link.exists():
                        link.unlink()
                    if name == "symlink":
                        os.symlink(str(outside), str(link))
                    extra = ""
                    extra_env = None
                    if name == "unquoted":
                        extra = "cp \"$_sshd_expected\" " + str(sentinel) + " || true"
                    elif name == "env":
                        extra = "cp \"$_sshd_expected\" \"$ESCAPE\" || true"
                        extra_env = {"ESCAPE": str(sentinel)}
                    elif name == "dotdot":
                        extra = "cp \"$_sshd_expected\" \"" + str(etc_pre) + "/../outside/sentinel\" || true"
                    elif name == "symlink":
                        extra = "cp \"$_sshd_expected\" \"" + str(link) + "/sentinel\" || true"
                    elif name == "systemctl":
                        extra = "systemctl restart ssh || true"
                    proc, sshd, denied, info = _run_sshd_isolated(tmp_path, base, base, extra=extra, extra_env=extra_env)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn("denied", denied, "rejected operation must be logged for %s" % name)
                    if name == "systemctl":
                        self.assertIn("denied: systemctl", denied)
                    else:
                        self.assertIn("denied: cp", denied)
                    self.assertEqual(sentinel.read_bytes(), sent_bytes, "outside sentinel must remain unchanged for %s" % name)

    def test_sshd_copy_wrapper_rejects_destination_option(self):
        text = _read_install_sh()
        m_marker = re.search(r'_sshd_marker="([^"]+)"', text)
        self.assertIsNotNone(m_marker, "marker not found")
        marker = m_marker.group(1)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            outside = tmp_path / "outside"
            outside.mkdir()
            sentinel = outside / "sshd_dropin.expected"
            sent_bytes = "".join(["out", "side-", "sent", "inel-", "target"]).encode("utf-8")
            sentinel.write_bytes(sent_bytes)
            base = (marker + "\nPort 2222\n").encode("utf-8")
            extra = 'cp "--target-directory=$ESCAPE" "$_sshd_expected" || true'
            proc, sshd, denied, info = _run_sshd_isolated(tmp_path, base, base, extra=extra, extra_env={"ESCAPE": str(outside)})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("denied: cp", denied)
            self.assertEqual(sentinel.read_bytes(), sent_bytes)


    def test_ssh_writes_stay_inside_isolated_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ssh_dir = tmp_path / "etc_ssh"
            ssh_dir.mkdir()
            fixture = ssh_dir / "sshd_config"
            ok_text = 'touch "' + str(fixture) + '"\ntouch "/dev/null"\n'
            _assert_isolated_shell_paths(ok_text, tmp_path)
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths('touch "/etc/ssh/sshd_config"\n', tmp_path)
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths('touch "/dev/unsafe"\n', tmp_path)
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths('touch "/proc/unsafe"\n', tmp_path)
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths('touch "/sys/unsafe"\n', tmp_path)
            sibling = str(tmp_path) + "-outside"
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths('touch "' + sibling + '/sentinel"\n', tmp_path)
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths('_hard_backups="/var/backups/opos-harden"; rm -rf "$_hard_backups"\n', tmp_path)
            with tempfile.TemporaryDirectory() as other:
                link = tmp_path / "link"
                os.symlink(other, link)
                escape = 'touch "' + str(link / "sentinel") + '"\n'
                with self.assertRaises(AssertionError):
                    _assert_isolated_shell_paths(escape, tmp_path)

    def test_shell_path_scan_rejects_unquoted_normalized_and_expansion_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            sibling = str(tmp_path) + "-outside"
            cases = (
                "touch " + sibling + "/sentinel\n",
                "touch /./etc/ssh/sshd_config\n",
                "touch /../etc/ssh/sshd_config\n",
                'touch "/./etc/ssh/sshd_config"\n',
                "touch '/../etc/ssh/sshd_config'\n",
                'touch "/$ESCAPE/sentinel"\n',
                'touch "${ESCAPE}/sentinel"\n',
                'touch "$ESCAPE/../sentinel"\n',
            )
            for snippet in cases:
                with self.subTest(snippet=snippet):
                    with self.assertRaises(AssertionError):
                        _assert_isolated_shell_paths(snippet, tmp_path)

    def test_backup_assignment_rejects_command_substitution(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            probe = "/" + "var/" + "backups/" + "opos-harden"
            sub = "$" + "("
            evil = "_hard_backups=\"" + sub + "rm -rf " + probe + ")\"\n"
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths(evil, tmp_path)
            tick = chr(96)
            evil_tick = "_hard_backups=\"" + tick + "rm -rf " + probe + tick + "\"\n"
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths(evil_tick, tmp_path)
            brace = "$" + "{"
            evil_brace = "_hard_backups=\"" + brace + "x:-" + probe + "}\"\n"
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths(evil_brace, tmp_path)

    def test_execution_requires_full_confinement(self):
        text = _read_install_sh()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            with self.assertRaises(AssertionError):
                _assert_isolated_shell_paths(text, tmp_path)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos_home"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            for p in (home, opos_home, bindir, stubdir):
                p.mkdir()
            with self.assertRaises(AssertionError):
                _assert_execution_confined(text, tmp_path, stubdir, home, opos_home, bindir)
            for name in ("ufw", "iptables", "ip6tables", "nft", "firewall-cmd", "systemctl", "loginctl", "service"):
                stub = stubdir / name
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            with self.assertRaises(AssertionError):
                _assert_execution_confined(text, tmp_path, stubdir, home, opos_home, bindir)
            etc_ssh = tmp_path / "etc_ssh"
            etc_ssh.mkdir()
            patched = text.replace("/etc/ssh/", str(etc_ssh) + "/")
            _assert_execution_confined(patched, tmp_path, stubdir, home, opos_home, bindir)

    def test_installer_execution_requires_confinement_guard(self):
        with mock.patch(__name__ + "._assert_execution_confined", side_effect=AssertionError("confinement guard reached")) as guard:
            with mock.patch(__name__ + ".subprocess.run", side_effect=AssertionError("subprocess reached before confinement guard")) as run_mock:
                other = TestInstallReview("test_unresolved_sshd_preserves_firewall_and_recovery_files")
                with self.assertRaisesRegex(AssertionError, "^confinement guard reached$"):
                    other.test_unresolved_sshd_preserves_firewall_and_recovery_files()
                self.assertEqual(guard.call_count, 1)
                self.assertEqual(run_mock.call_count, 0)


if __name__ == "__main__":
    unittest.main()
