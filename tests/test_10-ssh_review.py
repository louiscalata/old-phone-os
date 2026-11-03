# SPDX-License-Identifier: GPL-3.0-or-later
"""Review tests for deploy/harden.d/10-ssh.sh step_ssh()."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_step_ssh_body():
    root = Path(__file__).resolve().parent.parent
    text = (root / "deploy" / "harden.d" / "10-ssh.sh").read_text()
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^step_ssh\(\)", line):
            start = i
            break
    assert start is not None, "step_ssh() not found"
    end = None
    for j in range(start, len(lines)):
        if j != start and re.match(r"^}", lines[j]):
            end = j
            break
    assert end is not None, "step_ssh() closing brace not found"
    body = "\n".join(lines[start:end + 1])
    kept = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(kept)


def _step_ssh_preamble():
    return "\n".join([
        "p() {",
        "  printf '%s\\n' \"$ROOT$1\"",
        "}",
        "write_file() {",
        "  mkdir -p \"$(dirname \"$(p \"$1\")\")\"",
        "  cat > \"$(p \"$1\")\"",
        "  chmod \"$2\" \"$(p \"$1\")\"",
        "}",
        "have_cmd() {",
        "  if [ \"${1:-}\" = \"sshd\" ]; then",
        "    return 0",
        "  fi",
        "  if [ \"${1:-}\" = \"systemctl\" ]; then",
        "    return 0",
        "  fi",
        "  return 1",
        "}",
        "run() {",
        "  if [ \"${1:-}\" = \"sshd\" ]; then",
        "    if [ \"${2:-}\" = \"-T\" ]; then",
        "      printf 'probe\\n' >> \"$EVENT_LOG\"",
        "      printf '%s\\n' 'passwordauthentication no'",
        "      printf '%s\\n' 'kbdinteractiveauthentication no'",
        "      printf '%s\\n' 'pubkeyauthentication yes'",
        "      printf '%s\\n' 'authenticationmethods publickey'",
        "      printf '%s\\n' \"$USEDNS_LINES\"",
        "      return 0",
        "    fi",
        "    return 0",
        "  fi",
        "  if [ \"${1:-}\" = \"systemctl\" ]; then",
        "    printf 'reload\\n' >> \"$EVENT_LOG\"",
        "    return 0",
        "  fi",
        "  if [ \"${1:-}\" = \"passwd\" ]; then",
        "    printf 'passwd\\n' >> \"$EVENT_LOG\"",
        "    return 0",
        "  fi",
        "  return 0",
        "}",
        "systemctl() {",
        "  if [ \"${1:-}\" = \"cat\" ]; then",
        "    return 0",
        "  fi",
        "  printf 'reload\\n' >> \"$EVENT_LOG\"",
        "  return 0",
        "}",
        "die() {",
        "  printf '%s\\n' \"$*\" >&2",
        "  exit 1",
        "}",
        "say() {",
        "  return 0",
        "}",
        "warn() {",
        "  return 0",
        "}",
    ])


def _load_ssh_key_ok():
    root = Path(__file__).resolve().parent.parent
    text = (root / "deploy" / "harden.d" / "10-ssh.sh").read_text()
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^ssh_key_ok\(\)", line):
            start = i
            break
    assert start is not None, "ssh_key_ok() not found"
    end = None
    for j in range(start, len(lines)):
        if j != start and re.match(r"^}", lines[j]):
            end = j
            break
    assert end is not None, "ssh_key_ok() closing brace not found"
    body = "\n".join(lines[start:end + 1])
    kept = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(kept)


def _key_line():
    typ = "".join(["ssh", "-", "ed25519"])
    frag = chr(65) * 68
    return typ + " " + frag + " test"


def _conn_str():
    first = ".".join(["192", "0", "2", "10"])
    second = ".".join(["198", "51", "100", "20"])
    return first + " 41000 " + second + " 22"


def _conf_rel():
    return "/etc/ssh/sshd_config.d/10-opos-hardening.conf"


def _bash_prelude():
    return "\n".join([
        "say() {",
        "  printf '[harden] %s\\n' \"$1\"",
        "}",
        "warn() {",
        "  printf '[harden] WARN: %s\\n' \"$1\" >&2",
        "}",
        "die() {",
        "  warn \"$1\"",
        "  exit 1",
        "}",
        "p() {",
        "  printf '%s\\n' \"$ROOT$1\"",
        "}",
        "backup() {",
        "  [ \"$APPLY\" = \"1\" ] || return 0",
        "  if [ -e \"$(p \"$1\")\" ]; then",
        "    mkdir -p \"$(dirname \"$BACKUP_DIR$1\")\"",
        "    cp -p \"$(p \"$1\")\" \"$BACKUP_DIR$1\"",
        "    printf 'restore %s\\n' \"$1\" >> \"$MANIFEST\"",
        "  else",
        "    printf 'created %s\\n' \"$1\" >> \"$MANIFEST\"",
        "  fi",
        "}",
        "write_file() {",
        "  if [ \"$APPLY\" != \"1\" ]; then",
        "    say \"would write $1 ($2)\"",
        "    return 0",
        "  fi",
        "  backup \"$1\"",
        "  mkdir -p \"$(dirname \"$(p \"$1\")\")\"",
        "  tmp=\"$(p \"$1\").tmp.$$\"",
        "  cat > \"$tmp\"",
        "  chmod \"$2\" \"$tmp\"",
        "  mv \"$tmp\" \"$(p \"$1\")\"",
        "}",
        "run() {",
        "  if [ \"$APPLY\" != \"1\" ]; then",
        "    say \"would run: $*\"",
        "    return 0",
        "  fi",
        "  if [ \"$TEST\" = \"1\" ]; then",
        "    say \"skip (test root): $*\"",
        "    return 0",
        "  fi",
        "  \"$@\"",
        "}",
        "have_cmd() {",
        "  return 0",
        "}",
        "sshd() {",
        "  printf 'sshd %s\\n' \"$*\" >> \"$ORDER_LOG\"",
        "  if [ \"${1:-}\" = \"-t\" ]; then",
        "    return 0",
        "  fi",
        "  local has_c=\"0\"",
        "  local a=\"\"",
        "  for a in \"$@\"; do",
        "    if [ \"$a\" = \"-C\" ]; then",
        "      has_c=\"1\"",
        "    fi",
        "  done",
        "  if [ \"$has_c\" = \"1\" ]; then",
        "    if [ \"${CTX_FAIL:-0}\" = \"1\" ]; then",
        "      return 1",
        "    fi",
        "    printf '%s\\n' 'PasswordAuthentication no'",
        "    printf '%s\\n' 'KbdInteractiveAuthentication no'",
        "    printf '%s\\n' 'PubkeyAuthentication yes'",
        "    printf '%s\\n' 'AuthenticationMethods publickey'",
        "    return 0",
        "  fi",
        "  printf '%s\\n' 'UseDNS no'",
        "  return 0",
        "}",
        "systemctl() {",
        "  printf 'systemctl %s\\n' \"$*\" >> \"$ORDER_LOG\"",
        "  return 0",
        "}",
        "passwd() {",
        "  printf 'passwd %s\\n' \"$*\" >> \"$ORDER_LOG\"",
        "  return 0",
        "}",
        "chown() {",
        "  printf 'chown %s\\n' \"$*\" >> \"$ORDER_LOG\"",
        "  return 0",
        "}",
    ])


def _run_once(body, ctx_fail, with_backup):
    key = _key_line()
    conn = _conn_str()
    conf = _conf_rel()
    user = "opostest"
    want = b"backup-before\n"
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bdir, tempfile.TemporaryDirectory() as tdir:
        sep = chr(47)
        home = sep + "home"
        auth_rel = home + sep + user + sep + ".ssh" + sep + "authorized_keys"
        auth_full = root + auth_rel
        os.makedirs(os.path.dirname(auth_full), exist_ok=True)
        with open(auth_full, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        if with_backup:
            live = root + conf
            os.makedirs(os.path.dirname(live), exist_ok=True)
            with open(live, "wb") as f:
                f.write(want)
        manifest = os.path.join(bdir, "MANIFEST")
        with open(manifest, "w", encoding="utf-8") as f:
            f.write("test\n")
        order = os.path.join(tdir, "order.log")
        with open(order, "w", encoding="utf-8") as f:
            f.write("")
        key_ok = _load_ssh_key_ok()
        script = os.path.join(tdir, "run.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write("set -euo pipefail\n")
            f.write(_bash_prelude() + "\n")
            f.write(key_ok + "\n")
            f.write(body + "\n")
            f.write("step_ssh\n")
        env = dict(os.environ)
        env["ROOT"] = root
        env["BACKUP_DIR"] = bdir
        env["MANIFEST"] = manifest
        env["ORDER_LOG"] = order
        env["APPLY"] = "1"
        env["TEST"] = "0"
        env["OPOS_USER"] = user
        env["SSH_CONNECTION"] = conn
        env["CTX_FAIL"] = "1" if ctx_fail else "0"
        env.pop("ADD_KEY", None)
        try:
            proc = subprocess.run(["bash", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError("step_ssh test script timed out after 10 seconds") from exc
        with open(order, "r", encoding="utf-8") as f:
            order_text = f.read()
        rconf = root + conf
        if os.path.exists(rconf):
            with open(rconf, "rb") as f:
                rbytes = f.read()
            exists = True
        else:
            rbytes = None
            exists = False
        return proc.returncode, order_text, exists, rbytes, want


def _check_restore_with_backup(body):
    rc, order, exists, rbytes, want = _run_once(body, True, True)
    assert rc != 0, "contextual failure with backup must abort"
    assert exists, "restored file must exist"
    assert rbytes == want, "backup bytes must be restored exactly"
    assert "reload" not in order, "no reload on abort"
    assert "passwd" not in order, "no passwd on abort"


def _check_remove_without_backup(body):
    rc, order, exists, _, _ = _run_once(body, True, False)
    assert rc != 0, "contextual failure without backup must abort"
    assert not exists, "new drop-in must be removed"
    assert "reload" not in order, "no reload on abort"
    assert "passwd" not in order, "no passwd on abort"


def _check_success(body):
    rc, order, exists, _, _ = _run_once(body, False, False)
    assert rc == 0, "success run must exit 0"
    assert exists, "drop-in must exist on success"
    lines = [ln.strip() for ln in order.splitlines() if ln.strip()]
    reload_idx = [i for i, ln in enumerate(lines) if "reload" in ln]
    passwd_idx = [i for i, ln in enumerate(lines) if ln.startswith("passwd")]
    assert len(reload_idx) >= 1, "reload must run on success"
    assert len(passwd_idx) == 1, "exactly one passwd on success"
    assert passwd_idx[0] > reload_idx[-1], "passwd must follow reload"


def _check_all(body):
    _check_restore_with_backup(body)
    _check_remove_without_backup(body)
    _check_success(body)


class SSHReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = _load_step_ssh_body()

    def test_step_ssh_preamble_defines_stubs(self):
        pre = _step_ssh_preamble()
        for sig in ["p() {", "write_file() {", "have_cmd() {", "run() {", "systemctl() {", "die() {", "say() {", "warn() {"]:
            self.assertIn(sig, pre)
            self.assertEqual(pre.count(sig), 1)
        self.assertIn("probe", pre)
        self.assertIn("USEDNS_LINES", pre)

    def test_p028_contextual_probe_rollback_and_order(self):
        body = self.body
        probe_lines = [
            ln for ln in body.splitlines()
            if "sshd" in ln and "-T" in ln and "-C" in ln and "run" in ln
        ]
        self.assertTrue(probe_lines, "contextual sshd -T -C probe not found")
        probe = "\n".join(probe_lines)
        self.assertIn("host=$caddr", probe)
        self.assertIn("addr=$caddr", probe)
        _check_all(body)

    def test_r019_behavioral_checks_reject_mutations(self):
        _check_all(self.body)
        restore_mut = self.body.replace('cp -p "$BACKUP_DIR$conf" "$(p "$conf")"', ':')
        assert restore_mut != self.body
        with self.assertRaises(AssertionError):
            _check_restore_with_backup(restore_mut)
        remove_mut = self.body.replace('rm -f "$(p "$conf")"', ':')
        assert remove_mut != self.body
        with self.assertRaises(AssertionError):
            _check_remove_without_backup(remove_mut)
        b = self.body
        p_idx = b.rindex("run passwd")
        s_start = b.rindex('if [ "$APPLY" = "1" ]; then', 0, p_idx)
        s_end = b.index("\n  fi", p_idx) + len("\n  fi")
        block = b[s_start:s_end]
        rest = b[:s_start] + b[s_end:]
        ins = rest.index("if have_cmd systemctl")
        order_mut = rest[:ins] + block + "\n" + rest[ins:]
        assert order_mut != self.body
        with self.assertRaises(AssertionError):
            _check_success(order_mut)

    def test_run_once_timeout_is_bounded_failure(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["bash"], 10)) as run_mock:
            with self.assertRaises(AssertionError) as cm:
                _run_once(self.body, False, False)
        self.assertEqual(str(cm.exception), "step_ssh test script timed out after 10 seconds")
        self.assertEqual(run_mock.call_count, 1)
        self.assertEqual(run_mock.call_args.kwargs.get("timeout"), 10)

    def test_s033_restore_requires_backup_of_existing_live_config(self):
        _check_restore_with_backup(self.body)
        pre = _bash_prelude()
        self.assertEqual(pre.count('backup "$1"'), 1)
        mut = pre.replace('backup "$1"', ':')
        with mock.patch.dict(globals(), {"_bash_prelude": lambda: mut}):
            with self.assertRaises(AssertionError):
                _check_restore_with_backup(self.body)

    def test_p028_usedns_guard_acceptance_and_rollback(self):
        body = self.body
        lines = body.splitlines()
        free_idx = next(
            i for i, ln in enumerate(lines) if "if ! dns_eff=" in ln
        )
        end_idx = next(
            i for i, ln in enumerate(lines)
            if i > free_idx and "if ! eff=" in ln
        )
        self.assertLess(free_idx, end_idx)
        guard = "\n".join(lines[free_idx:end_idx])
        self.assertIn("dns_eff", guard)
        ctx_idx = next(
            i for i, ln in enumerate(lines)
            if "sshd" in ln and "-T" in ln and "-C" in ln and "run" in ln
        )
        self.assertLessEqual(end_idx, ctx_idx)
        self.assertLess(free_idx, ctx_idx)
        reload_pos = body.lower().index("reload", body.index(lines[ctx_idx]))
        passwd_pos = body.lower().index("run passwd", body.index(lines[ctx_idx]))
        guard_start_off = body.index(lines[free_idx])
        self.assertGreater(reload_pos, guard_start_off)
        self.assertGreater(passwd_pos, guard_start_off)
        u = chr(117) + chr(115) + chr(101) + chr(68) + chr(78) + chr(83)
        no = chr(110) + chr(111)
        yes = chr(121) + chr(101) + chr(115)
        ok = u.lower() + " " + no
        cases = [
            ("single", ok, "0", True),
            ("missing", "", "0", False),
            ("duplicate", ok + "\n" + ok, "0", False),
            ("malformed", ok + " extra", "0", False),
            ("yes", u.lower() + " " + yes, "0", False),
            ("cmdfail", ok, "1", False),
            ("extra-directives", "port 22" + "\n" + ok + "\n" + "permitrootlogin " + no, "0", True),
            ("unknown", u.lower() + " maybe", "0", False),
            ("mixed-duplicate", ok + "\n" + u.lower() + " " + yes, "0", False),
        ]
        conf = "/etc/ssh/sshd_config.d/10-opos-hardening.conf"
        for name, out, status, want_ok in cases:
            for with_backup in (True, False):
                with self.subTest(case=name, backup=with_backup):
                    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bdir:
                        drop = root + conf
                        os.makedirs(os.path.dirname(drop), exist_ok=True)
                        with open(drop, "wb") as f:
                            f.write(b"dropin-new\n")
                        sent = b"sentinel-backup\n"
                        bpath = bdir + conf
                        if with_backup:
                            os.makedirs(os.path.dirname(bpath), exist_ok=True)
                            with open(bpath, "wb") as f:
                                f.write(sent)
                        pre = "\n".join([
                            "set -u",
                            'ROOT="$ROOT"',
                            'BACKUP_DIR="$BACKUP_DIR"',
                            'conf="$CONF"',
                            "p() {",
                            "  printf '%s\\n' \"$ROOT$1\"",
                            "}",
                            "die() {",
                            "  printf '%s\\n' \"$*\" >&2",
                            "  exit 1",
                            "}",
                            "run() {",
                            '  if [ "$RUN_STATUS" != "0" ]; then',
                            "    return 1",
                            "  fi",
                            '  printf \'%s\\n\' "$USEDNS_OUTPUT"',
                            "  return 0",
                            "}",
                        ])
                        script = pre + "\n" + guard + "\n" + "printf 'CONTINUED\\n'\n"
                        env = dict(os.environ)
                        env["ROOT"] = root
                        env["BACKUP_DIR"] = bdir
                        env["CONF"] = conf
                        env["USEDNS_OUTPUT"] = out
                        env["RUN_STATUS"] = status
                        proc = subprocess.run(
                            ["bash"],
                            input=script,
                            env=env,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        continued = "CONTINUED" in proc.stdout
                        if want_ok:
                            self.assertEqual(proc.returncode, 0, name)
                            self.assertTrue(continued, name)
                            with open(drop, "rb") as f:
                                self.assertEqual(f.read(), b"dropin-new\n", name)
                        else:
                            self.assertNotEqual(proc.returncode, 0, name)
                            self.assertFalse(continued, name)
                            if with_backup:
                                with open(drop, "rb") as f:
                                    self.assertEqual(f.read(), sent, name)
                            else:
                                self.assertFalse(os.path.exists(drop), name)

    def test_step_ssh_accepts_usedns_no(self):
        repo = Path(__file__).resolve().parent.parent
        script_text = (repo / "deploy" / "harden.d" / "10-ssh.sh").read_text()

        def run_case(usedns):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                auth = tmp_path / "home" / "admin" / ".ssh" / "authorized_keys"
                auth.parent.mkdir(parents=True)
                auth.write_text(_key_line() + "\n")
                events = tmp_path / "events.log"
                events.write_text("")
                backup = tmp_path / "backup"
                backup.mkdir()
                env = dict(os.environ)
                env["OPOS_USER"] = "admin"
                env["APPLY"] = "1"
                env["TEST"] = "0"
                env["ADD_KEY"] = ""
                env["ROOT"] = tmp
                env["BACKUP_DIR"] = str(backup)
                env["EVENT_LOG"] = str(events)
                env["USEDNS_LINES"] = usedns
                env["SSH_CONNECTION"] = _conn_str()
                script = _step_ssh_preamble() + "\n" + script_text + "\n" + "step_ssh\n"
                proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
                event_words = events.read_text().split()
                drop_path = tmp + _conf_rel()
                exists = os.path.exists(drop_path)
                return proc, event_words, exists

        proc, event_words, exists = run_case("usedns no")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(event_words, ["probe", "probe", "reload", "passwd"])
        self.assertTrue(exists)


if __name__ == "__main__":
    unittest.main()
