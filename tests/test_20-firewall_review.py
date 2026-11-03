# SPDX-License-Identifier: GPL-3.0-or-later
"""Review tests for deploy/harden.d/20-firewall.sh backend selection."""
import ast
import os
import re
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


_OPOS_DELETE_HOOKS = {"INPUT": "OPOS_INPUT", "FORWARD": "OPOS_FORWARD"}
_OPOS_OWNED_CHAINS = frozenset(["OPOS_INPUT", "OPOS_FORWARD"])


def _stop_mutation_permitted(line):
    text = line.strip()
    if not text:
        return False
    try:
        args = shlex.split(text)
    except ValueError:
        return False
    pos = 0
    while pos < len(args):
        tok = args[pos]
        if tok == "-w" or tok == "--wait":
            pos += 1
            if pos < len(args) and args[pos].isdigit():
                pos += 1
        elif tok == "-t" or tok == "--table":
            if pos + 1 >= len(args) or args[pos + 1] != "filter":
                return False
            pos += 2
        else:
            break
    args = args[pos:]
    if len(args) == 2 and args[0] in ("-F", "--flush", "-X", "--delete-chain"):
        return args[1] in _OPOS_OWNED_CHAINS
    if len(args) == 4 and args[0] in ("-D", "--delete"):
        return args[1] in _OPOS_DELETE_HOOKS and args[2] in ("-j", "--jump") and args[3] == _OPOS_DELETE_HOOKS[args[1]]
    return False


def _nft_table_cleanup_exact(line, family, table):
    text = line.strip()
    if not text:
        return False
    try:
        args = shlex.split(text)
    except ValueError:
        return False
    if args and os.path.basename(args[0]) == "nft":
        args = args[1:]
    return args == ["delete", "table", family, table]


def _is_zero_token(tok):
    if tok.startswith("--zero"):
        return True
    if tok.startswith("--"):
        base = tok.split("=", 1)[0]
        if len(base) >= 3 and "--zero".startswith(base):
            return True
        return False
    if len(tok) > 1 and tok[0] == "-" and tok[1] != "-" and "Z" in tok:
        return True
    return False


def _stop_is_inspection(line):
    text = line.strip()
    if not text:
        return False
    try:
        args = shlex.split(text)
    except ValueError:
        return False
    pos = 0
    while pos < len(args):
        tok = args[pos]
        if tok == "-w" or tok == "--wait":
            pos += 1
            if pos < len(args) and args[pos].isdigit():
                pos += 1
        elif tok == "-t" or tok == "--table":
            if pos + 1 >= len(args) or args[pos + 1] != "filter":
                return False
            pos += 2
        else:
            break
    if pos >= len(args):
        return False
    op = args[pos]
    rest = args[pos + 1:]
    for tok in rest:
        if _is_zero_token(tok):
            return False
    if op in ("-S", "--list"):
        if len(rest) == 0:
            return True
        if len(rest) == 1 and rest[0] in ("INPUT", "FORWARD", "OPOS_INPUT", "OPOS_FORWARD"):
            return True
        return False
    if op in ("-C", "--check"):
        if len(rest) == 3 and rest[0] in ("INPUT", "FORWARD") and rest[1] in ("-j", "--jump"):
            if rest[2] == "OPOS_" + rest[0]:
                return True
        return False
    return False


def _chr(code):
    return "".join(chr(c) for c in [code])


def _iface():
    return "".join(chr(c) for c in [117, 115, 98, 48])


def _write_exe(path, body):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR)


def _probe_env(**paths):
    env = dict(os.environ)
    env.update(paths)
    return env


def _mock_string(node, hook_in, hook_fwd):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id == "hook_in":
            return hook_in
        if node.id == "hook_fwd":
            return hook_fwd
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _mock_string(node.left, hook_in, hook_fwd) + _mock_string(node.right, hook_in, hook_fwd)
    raise ValueError("unsupported mock string expression: " + type(node).__name__)


def _shared_state_mock(state):
    return (
        "#!/bin/bash\n"
        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
        "state=\"" + state + "\"\n"
        "argv=(\"$@\")\n"
        "count=\"$#\"\n"
        "idx=0\n"
        "while [ \"$idx\" -lt \"$count\" ]; do\n"
        "  tok=\"${argv[$idx]}\"\n"
        "  if [ \"$tok\" = \"-w\" ] || [ \"$tok\" = \"--wait\" ]; then\n"
        "    idx=$((idx + 1))\n"
        "    if [ \"$idx\" -lt \"$count\" ]; then\n"
        "      case \"${argv[$idx]}\" in\n"
        "        *[!0-9]*) ;;\n"
        "        *) idx=$((idx + 1)) ;;\n"
        "      esac\n"
        "    fi\n"
        "  elif [ \"$tok\" = \"-t\" ] || [ \"$tok\" = \"--table\" ]; then\n"
        "    idx=$((idx + 1))\n"
        "    if [ \"$idx\" -ge \"$count\" ] || [ \"${argv[$idx]}\" != \"filter\" ]; then\n"
        "      exit 1\n"
        "    fi\n"
        "    idx=$((idx + 1))\n"
        "  else\n"
        "    break\n"
        "  fi\n"
        "done\n"
        "op=\"\"\n"
        "if [ \"$idx\" -lt \"$count\" ]; then op=\"${argv[$idx]}\"; fi\n"
        "second=\"\"\n"
        "if [ \"$((idx + 1))\" -lt \"$count\" ]; then second=\"${argv[$((idx + 1))]}\"; fi\n"
        "third=\"\"\n"
        "if [ \"$((idx + 2))\" -lt \"$count\" ]; then third=\"${argv[$((idx + 2))]}\"; fi\n"
        "fourth=\"\"\n"
        "if [ \"$((idx + 3))\" -lt \"$count\" ]; then fourth=\"${argv[$((idx + 3))]}\"; fi\n"
        "rest=$((count - idx))\n"
        "if [ \"$rest\" = \"2\" ]; then\n"
        "  if [ \"$op\" = \"-F\" ] || [ \"$op\" = \"--flush\" ]; then\n"
        "    grep -vF -- \"-A $second \" \"$state\" > \"$state.tmp\"; rc=$?; if [ \"$rc\" = \"0\" ] || [ \"$rc\" = \"1\" ]; then mv \"$state.tmp\" \"$state\" || exit $?; else exit \"$rc\"; fi\n"
        "    exit 0\n"
        "  fi\n"
        "  if [ \"$op\" = \"-X\" ] || [ \"$op\" = \"--delete-chain\" ]; then\n"
        "    if grep -qF -- \"-A $second \" \"$state\"; then exit 1; fi\n"
        "    if grep -qE -- \"-j $second([[:space:]]|$)\" \"$state\"; then exit 1; fi\n"
        "    grep -vFx -- \"-N $second\" \"$state\" > \"$state.tmp\"; rc=$?; if [ \"$rc\" = \"0\" ] || [ \"$rc\" = \"1\" ]; then mv \"$state.tmp\" \"$state\" || exit $?; else exit \"$rc\"; fi\n"
        "    exit 0\n"
        "  fi\n"
        "fi\n"
        "if [ \"$rest\" = \"4\" ]; then\n"
        "  if [ \"$op\" = \"-C\" ] || [ \"$op\" = \"--check\" ]; then\n"
        "    if [ \"$third\" = \"-j\" ] || [ \"$third\" = \"--jump\" ]; then\n"
        "      grep -qFx -- \"-A $second -j $fourth\" \"$state\"\n"
        "      exit \"$?\"\n"
        "    fi\n"
        "    exit 1\n"
        "  fi\n"
        "  if [ \"$op\" = \"-D\" ] || [ \"$op\" = \"--delete\" ]; then\n"
        "    if [ \"$third\" = \"-j\" ] || [ \"$third\" = \"--jump\" ]; then\n"
        "      awk -v want=\"-A $second -j $fourth\" '!removed && $0 == want {removed=1; next} {print} END {if (!removed) exit 1}' \"$state\" > \"$state.tmp\" || exit \"$?\"\n"
        "      mv \"$state.tmp\" \"$state\" || exit \"$?\"\n"
        "      exit 0\n"
        "    fi\n"
        "    exit 1\n"
        "  fi\n"
        "fi\n"
        "if [ \"$op\" = \"-S\" ] || [ \"$op\" = \"--list\" ]; then cat \"$state\"; exit 0; fi\n"
        "exit 1\n"
    )


def _without_comment(text):
    quote = None
    escaped = False
    cut = len(text)
    for pos, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ("\"", "'"):
            quote = char
        elif char == "#":
            cut = pos
            break
    if quote is not None:
        return None
    code = text[:cut]
    arg = r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\S+)"
    code = re.sub(r"(?:^|\s)--comment\s+" + arg, " ", code, flags=re.IGNORECASE)
    code = re.sub(r"(?:^|\s)(?:-m\s+comment|--match\s+comment)\b", " ", code, flags=re.IGNORECASE)
    code = re.sub(r"(?<![\w-])comment\s+" + arg, " ", code, flags=re.IGNORECASE)
    def _keep_simple(match):
        inner = match.group(0)[1:-1]
        if re.search(r"\s", inner) is not None:
            return " __quoted_argument__ "
        return " " + inner + " "
    return re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", _keep_simple, code)


def _nft_dhcpv6_ok(line, sport6, dport6, iface, ll):
    code = _without_comment(line)
    if code is None:
        return False
    low = code.lower()
    try:
        parts = shlex.split(code)
    except ValueError:
        return False
    _opts = frozenset(["-A", "-I", "-d", "-i", "-m", "-o", "-p", "-s", "-t", "--comment", "--dport", "--match", "--sport", "--string", "--table", "comment"])
    _wait = frozenset(["-w", "--wait"])
    targets = []
    idx = 0
    while idx < len(parts):
        if parts[idx] == "-j" or parts[idx] == "--jump":
            if idx + 1 < len(parts):
                targets.append(parts[idx + 1])
                idx += 2
                continue
        elif parts[idx] in _wait:
            idx += 1
            if idx < len(parts) and parts[idx].isdigit():
                idx += 1
            continue
        elif parts[idx] in _opts:
            idx += 2
            continue
        idx += 1
    iptables = re.search(r"(?:^|\s)-(?:A|I|p)\b|(?:^|\s)--(?:sport|dport)\b", code) is not None
    return (
        "udp" in low
        and re.search(r"sport\s+" + re.escape(sport6) + r"(?![\w:-])(?!\s*:)(?!\s*-(?!-|[A-Za-z]\b))", low) is not None
        and re.search(r"dport\s+" + re.escape(dport6) + r"(?![\w:-])(?!\s*:)(?!\s*-(?!-|[A-Za-z]\b))", low) is not None
        and iface in code
        and ll in code
        and re.search(r"\baccept\b", low) is not None
        and re.search(r"\bdrop\b", low) is None
        and re.search(r"\breject\b", low) is None
        and (not iptables or (len(targets) > 0 and all(target.lower() == "accept" for target in targets)))
    )


def _surviving_snapshot(lines, owned):
    owned_set = frozenset(owned)
    policies = []
    by_chain = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        code = _without_comment(line)
        if code is None:
            owned_hit = False
        else:
            try:
                tokens = shlex.split(code)
            except ValueError:
                owned_hit = False
            else:
                owned_hit = False
                if len(tokens) >= 2 and tokens[0] == "-N":
                    if tokens[1] in owned_set:
                        owned_hit = True
                elif len(tokens) >= 2 and tokens[0] == "-A":
                    if tokens[1] in owned_set:
                        owned_hit = True
                    elif len(tokens) == 4 and tokens[1] in _OPOS_DELETE_HOOKS:
                        if tokens[2] in ("-j", "--jump"):
                            want = _OPOS_DELETE_HOOKS[tokens[1]]
                            if tokens[3] == want and tokens[3] in owned_set:
                                owned_hit = True
        if owned_hit:
            continue
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "-P":
            policies.append(line)
        elif len(parts) >= 2 and parts[0] in ("-A", "-N"):
            by_chain.setdefault(parts[1], []).append(line)
        else:
            by_chain.setdefault("", []).append(line)
    return (policies, by_chain)


def _extract_legacy_firewall(text):
    marker = "write_file /etc/opos/firewall-legacy.sh 0755 <<EOF"
    idx = text.find(marker)
    if idx == -1:
        raise AssertionError("legacy marker missing")
    start = text.find("\n", idx)
    if start == -1:
        raise AssertionError("legacy body missing")
    start += 1
    end = text.find("\nEOF\n", start)
    if end == -1:
        raise AssertionError("legacy terminator missing")
    return text[start:end + 1]


class FirewallBackendReviewTest(unittest.TestCase):
    def setUp(self):
        self._route_fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self._route_fixture.cleanup)
        root = self._route_fixture.name
        sysnet = os.path.join(root, "sys", "class", "net")
        os.makedirs(os.path.join(sysnet, _iface()))
        os.makedirs(os.path.join(root, "etc", "opos"))
        bin_dir = os.path.join(root, "bin")
        os.makedirs(bin_dir)
        ss_bin = os.path.join(bin_dir, "ss")
        ssh_bin = os.path.join(bin_dir, "ssh")
        _write_exe(
            ss_bin,
            "#!/bin/sh\n"
            "printf '%s\\n' 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:*'\n",
        )
        _write_exe(ssh_bin, "#!/bin/sh\nexit 0\n")
        self._saved_test_env = {
            name: os.environ.get(name)
            for name in ("OPOS_TEST_ROOT", "OPOS_SYS_CLASS_NET", "OPOS_SS", "OPOS_SSH")
        }
        os.environ.update(
            {
                "OPOS_TEST_ROOT": root,
                "OPOS_SYS_CLASS_NET": sysnet,
                "OPOS_SS": ss_bin,
                "OPOS_SSH": ssh_bin,
            }
        )
        self.addCleanup(self._restore_test_env)

    def _restore_test_env(self):
        for name, value in self._saved_test_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _make_legacy_start_capture(self, tmp):
        bin_dir = os.path.join(tmp, "bin")
        cap_dir = os.path.join(tmp, "cap")
        os.makedirs(bin_dir, exist_ok=True)
        os.makedirs(cap_dir, exist_ok=True)
        state4 = os.path.join(tmp, "state4")
        state6 = os.path.join(tmp, "state6")
        cmd4 = os.path.join(bin_dir, "iptables-legacy")
        cmd6 = os.path.join(bin_dir, "ip6tables-legacy")
        for p in [cmd4, cmd6]:
            state = state4 if p == cmd4 else state6
            body = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "state_file=\"" + state + "\"\n"
                "if [ \"$#\" = \"4\" ]; then\n"
                "if [ \"$1\" = \"-C\" ] || [ \"$1\" = \"-D\" ]; then\n"
                "if [ \"$3\" = \"-j\" ]; then\n"
                "want=\"-A $2 -j $4\"\n"
                "if [ \"$1\" = \"-C\" ]; then grep -qFx -- \"$want\" \"$state_file\"; exit \"$?\"; fi\n"
                "awk -v want=\"$want\" '!removed && $0 == want {removed=1; next} {print} END {if (!removed) exit 1}' \"$state_file\" > \"$state_file.tmp\" || exit \"$?\"\n"
                "mv \"$state_file.tmp\" \"$state_file\" || exit \"$?\"\n"
                "exit 0\n"
                "fi\n"
                "fi\n"
                "fi\n"
                "if [ \"$#\" = \"5\" ] && [ \"$1\" = \"-I\" ] && [ \"$2\" = \"INPUT\" ] && [ \"$3\" = \"1\" ] && [ \"$4\" = \"-j\" ] && [ \"$5\" = \"OPOS_INPUT\" ]; then\n"
                "echo \"-A INPUT -j OPOS_INPUT\" >> \"$state_file\"; exit 0\n"
                "fi\n"
                "if [ \"$#\" = \"2\" ] && [ \"$1\" = \"-S\" ] && [ \"$2\" = \"INPUT\" ]; then\n"
                "echo \"*filter\"; echo \":INPUT ACCEPT [0:0]\"; cat \"$state_file\" 2>/dev/null || true; exit 0\n"
                "fi\n"
                "exit 1\n"
            )
            _write_exe(p, body)
        for name in ("iptables-legacy-restore","ip6tables-legacy-restore"):
            _write_exe(os.path.join(bin_dir, name), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
        env = _probe_env(
            OPOS_IPTABLES_LEGACY=cmd4,
            OPOS_IP6TABLES_LEGACY=cmd6,
            OPOS_IPTABLES_LEGACY_RESTORE=os.path.join(bin_dir,"iptables-legacy-restore"),
            OPOS_IP6TABLES_LEGACY_RESTORE=os.path.join(bin_dir,"ip6tables-legacy-restore"),
            CAPTURE_DIR=cap_dir,
        )
        return os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh"), state4, state6, cmd4, cmd6, env

    def _run_generated_legacy_stop(self, cap_file, bin_dir):
        return subprocess.run(
            ["bash", cap_file, "stop"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env=_probe_env(PATH=bin_dir + ":" + os.environ.get("PATH", "")),
        )

    def test_start_capture_delete_removes_first_exact_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, state4, state6, cmd4, cmd6, _ = self._make_legacy_start_capture(tmp)
            hook = "OPOS" + "_" + "INPUT"
            exact = "-A INPUT -j " + hook
            extra = exact + "_EXTRA"
            fwd = "-A FORWARD -j " + hook
            accept = "-A INPUT -j ACCEPT"
            seed = exact + "\n" + extra + "\n" + fwd + "\n" + exact + "\n" + accept + "\n"
            after_first = extra + "\n" + fwd + "\n" + exact + "\n" + accept + "\n"
            after_second = extra + "\n" + fwd + "\n" + accept + "\n"
            cases = (("ipv4", cmd4, state4), ("ipv6", cmd6, state6))
            for family, cmd, state in cases:
                with self.subTest(family=family):
                    with open(state, "w", encoding="utf-8") as handle:
                        handle.write(seed)
                    proc = subprocess.run([cmd, "-D", "INPUT", "-j", hook], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                    with open(state, encoding="utf-8") as handle:
                        self.assertEqual(handle.read(), after_first)
                    proc = subprocess.run([cmd, "-D", "INPUT", "-j", hook], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                    with open(state, encoding="utf-8") as handle:
                        self.assertEqual(handle.read(), after_second)

    def test_start_capture_honors_exact_command_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, state4, state6, cmd4, cmd6, _ = self._make_legacy_start_capture(tmp)
            hook = "OPOS" + "_" + "INPUT"
            allow = "AC" + "CEPT"
            fwd = "FOR" + "WARD"
            row_in = "-A INPUT -j " + hook
            row_allow = "-A INPUT -j " + allow
            row_fwd = "-A " + fwd + " -j " + hook
            seed = row_in + "\n" + row_in + "\n" + row_allow + "\n" + row_fwd + "\n"
            miss = hook + _chr(95) + "MISSING"
            bad_flag = "-" + "i"

            def _read(state):
                with open(state, encoding="utf-8") as handle:
                    return handle.read()

            def _seed(state):
                with open(state, "w", encoding="utf-8") as handle:
                    handle.write(seed)

            def _run(cmd, args):
                return subprocess.run([cmd] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            cases = (("ipv4", cmd4, state4), ("ipv6", cmd6, state6))
            for family, cmd, state in cases:
                with self.subTest(family=family):
                    _seed(state)
                    for args in (["-C", "INPUT", "-j", hook], ["-C", "INPUT", "-j", allow], ["-C", fwd, "-j", hook]):
                        proc = _run(cmd, args)
                        self.assertEqual(proc.returncode, 0, msg=" ".join(args))
                    self.assertEqual(_read(state), seed)
                    for args in (["-C", "INPUT", "-j", miss], ["-C", fwd, "-j", allow]):
                        proc = _run(cmd, args)
                        self.assertNotEqual(proc.returncode, 0, msg=" ".join(args))
                    self.assertEqual(_read(state), seed)
                    _seed(state)
                    proc = _run(cmd, ["-D", "INPUT", "-j", allow])
                    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                    self.assertEqual(_read(state), row_in + "\n" + row_in + "\n" + row_fwd + "\n")
                    _seed(state)
                    proc = _run(cmd, ["-D", fwd, "-j", hook])
                    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                    self.assertEqual(_read(state), row_in + "\n" + row_in + "\n" + row_allow + "\n")
                    _seed(state)
                    proc = _run(cmd, ["-D", "INPUT", "-j", hook])
                    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                    self.assertEqual(_read(state), row_in + "\n" + row_allow + "\n" + row_fwd + "\n")
                    bad = [
                        ["-C", "INPUT", "-j", miss],
                        ["-D", "INPUT", "-j", miss],
                        ["-C", "INPUT"],
                        ["-D", "INPUT", "-j"],
                        ["-C", "INPUT", bad_flag, hook],
                        ["-D", "INPUT", bad_flag, hook],
                        ["-C", "INPUT", "-j", hook, "extra"],
                        ["-D", "INPUT", "-j", hook, "extra"],
                    ]
                    for args in bad:
                        _seed(state)
                        proc = _run(cmd, args)
                        self.assertNotEqual(proc.returncode, 0, msg=" ".join(args))
                        self.assertEqual(_read(state), seed, msg=" ".join(args))

    def _run_pick(self, bin_dir, want="auto"):
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "deploy",
            "harden.d",
            "20-firewall.sh",
        )
        probe = (
            "set -u\n"
            "APPLY=0\n"
            "TEST=0\n"
            "OPOS_USB_IFACES=" + _iface() + "\n"
            "OPOS_FW_BACKEND=" + want + "\n"
            "export APPLY TEST OPOS_USB_IFACES OPOS_FW_BACKEND\n"
            "export PATH=\"$FWT_BIN:$PATH\"\n"
            "export OPOS_NFT=\"$FWT_BIN/nft\"\n"
            "export OPOS_IPTABLES_LEGACY=\"$FWT_BIN/iptables-legacy\"\n"
            "export OPOS_IP6TABLES_LEGACY=\"$FWT_BIN/ip6tables-legacy\"\n"
            "export OPOS_IPTABLES_LEGACY_RESTORE=\"$FWT_BIN/iptables-legacy-restore\"\n"
            "export OPOS_IP6TABLES_LEGACY_RESTORE=\"$FWT_BIN/ip6tables-legacy-restore\"\n"
            "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
            "say() { :; }\n"
            "warn() { :; }\n"
            ". \"$FWT_SCRIPT\"\n"
            "fw_pick_backend\n"
            "printf " + chr(37) + "s \"$FW_BACKEND\"\n"
        )
        proc = subprocess.run(
            ["bash", "-c", probe],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            cwd=os.path.dirname(os.path.abspath(bin_dir)),
            env=_probe_env(FWT_BIN=bin_dir, FWT_SCRIPT=script),
        )
        return proc

    def test_start_capture_check_detects_missing_input_jump(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, cmd4, _, _ = self._make_legacy_start_capture(tmp)
            hook = "OPOS" + "_" + "INPUT"
            proc = subprocess.run(
                [cmd4, "-C", "INPUT", "-j", hook],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)
            proc = subprocess.run(
                [cmd4, "-I", "INPUT", "1", "-j", hook],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            proc = subprocess.run(
                [cmd4, "-C", "INPUT", "-j", hook],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_probe_paths_are_not_shell_interpreted(self):
        with tempfile.TemporaryDirectory() as tmp:
            inner = "touch" + _chr(36) + "{IFS}canary"
            leaf = "bin" + _chr(36) + "(" + inner + ")"
            bin_dir = os.path.join(tmp, leaf)
            os.mkdir(bin_dir)
            _write_exe(
                os.path.join(bin_dir, "nft"),
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                'if [ "${1:-}" = "list" ]; then exit 0; fi\n'
                'if [ "${1:-}" = "-c" ]; then echo "operation not supported" >&2; exit 1; fi\n'
                "exit 0\n",
            )
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                if name.endswith("restore"):
                    _write_exe(
                        os.path.join(bin_dir, name),
                        "#!/bin/bash\n"
                        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                        "exit 0\n",
                    )
                else:
                    _write_exe(
                        os.path.join(bin_dir, name),
                        "#!/bin/bash\n"
                        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                        'if [ "${1:-}" = "-S" ]; then echo "-P INPUT ACCEPT"; exit 0; fi\n'
                        "exit 0\n",
                    )
            proc = self._run_pick(bin_dir, want="auto")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout.strip(), "iptables-legacy")
            self.assertFalse(os.path.exists(os.path.join(tmp, "canary")))

    def test_auto_falls_back_when_ruleset_check_rejects(self):
        with tempfile.TemporaryDirectory() as bin_dir:
            _write_exe(
                os.path.join(bin_dir, "nft"),
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                'if [ "${1:-}" = "list" ]; then exit 0; fi\n'
                'if [ "${1:-}" = "-c" ]; then echo "operation not supported" >&2; exit 1; fi\n'
                "exit 0\n",
            )
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                if name.endswith("restore"):
                    _write_exe(
                        os.path.join(bin_dir, name),
                        "#!/bin/bash\n"
                        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                        "exit 0\n",
                    )
                else:
                    _write_exe(
                        os.path.join(bin_dir, name),
                        "#!/bin/bash\n"
                        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                        'if [ "${1:-}" = "-S" ]; then echo "-P INPUT ACCEPT"; exit 0; fi\n'
                        "exit 0\n",
                    )
            proc = self._run_pick(bin_dir, want="auto")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout.strip(), "iptables-legacy")

    def test_legacy_stop_reports_removal_failure_xyz_placeholder_never_used(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            absent_body = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ -z \"${2:-}\" ]; then echo \"-P INPUT ACCEPT\"; echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            failing_body = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 0; fi\n"
                "if [ \"${1:-}\" = \"-D\" ]; then echo delete-failed >&2; exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then exit 1; fi\n"
                "exit 0\n"
            )
            stubborn_body = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 0; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then echo \"-A OPOS\"; exit 0; fi\n"
                "exit 0\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), absent_body)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), absent_body)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), failing_body)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), absent_body)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertNotEqual(proc.returncode, 0)
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), stubborn_body)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), stubborn_body)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertNotEqual(proc.returncode, 0)

    def test_legacy_apply_verifies_first_input_rule_after_policy(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        jump = "-A INPUT -j OPOS_INPUT"
        lo_if = "".join(chr(c) for c in [108, 111])
        lo_rule = "-A INPUT -i " + lo_if + " -j ACCEPT"
        old_suffix = "".join(chr(c) for c in [95, 79, 76, 68])
        old_rule = "-A INPUT -j OPOS_INPUT" + old_suffix
        policy = "-P INPUT ACCEPT"
        chain_head = "-N OPOS_INPUT"
        chain_tail = "-A OPOS_INPUT -j DROP"

        def _bin_text(input_lines):
            lines = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-S\" ] && [ \"${2:-}\" = \"OPOS_INPUT\" ]; then\n"
                "  echo \"" + chain_head + "\"\n"
                "  echo \"" + chain_tail + "\"\n"
                "  exit 0\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-S\" ] && [ \"${2:-}\" = \"INPUT\" ]; then\n"
            )
            for line in input_lines:
                lines += "  echo \"" + line + "\"\n"
            lines += "  exit 0\nfi\nexit 0\n"
            return lines

        def _run_case(v4_lines, v6_lines):
            with tempfile.TemporaryDirectory() as tmp:
                bin_dir = os.path.join(tmp, "bin")
                cap_dir = os.path.join(tmp, "cap")
                os.mkdir(bin_dir)
                os.mkdir(cap_dir)
                _write_exe(os.path.join(bin_dir, "iptables-legacy"), _bin_text(v4_lines))
                _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), _bin_text(v6_lines))
                for name in ("iptables-legacy-restore", "ip6tables-legacy-restore"):
                    _write_exe(
                        os.path.join(bin_dir, name),
                        "#!/bin/bash\n"
                        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                        "exit 0\n",
                    )
                manifest = os.path.join(tmp, "manifest")
                with open(manifest, "w", encoding="utf-8"):
                    pass
                probe = (
                    "set -u\n"
                    "APPLY=1\n"
                    "TEST=0\n"
                    "OPOS_USB_IFACES=" + _iface() + "\n"
                    "MANIFEST=" + manifest + "\n"
                    "CAPTURE_DIR=" + cap_dir + "\n"
                    "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                    "export PATH=" + bin_dir + ":$PATH\n"
                    "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                    "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                    "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                    "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                    "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                    "say() { :; }\n"
                    "warn() { :; }\n"
                    "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                    "run() { return 0; }\n"
                    "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                    ". " + script + "\n"
                    "step_firewall_legacy\n"
                )
                return subprocess.run(
                    ["bash", "-c", probe],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )

        proc = _run_case([policy, jump], [policy, jump])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        proc = _run_case([policy, jump], [policy, lo_rule, jump])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("INPUT jump missing", proc.stderr)
        proc = _run_case([policy, lo_rule, jump], [policy, jump])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("INPUT jump missing", proc.stderr)
        proc = _run_case([policy], [policy])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("INPUT jump missing", proc.stderr)
        proc = _run_case([policy, old_rule], [policy, jump])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("INPUT jump missing", proc.stderr)

    def test_legacy_stop_fails_when_inspection_unreliable(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            reliable_absent = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ -z \"${2:-}\" ]; then echo \"-P INPUT ACCEPT\"; echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            broken_body = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ]; then echo \"cannot inspect rules\" >&2; exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then echo \"cannot inspect rules\" >&2; exit 1; fi\n"
                "exit 0\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), broken_body)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), reliable_absent)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("cannot inspect", proc.stderr)
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), reliable_absent)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), broken_body)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("cannot inspect", proc.stderr)

    def test_stop_proves_opos_absence_from_complete_listing(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            misleading_body = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ -z \"${2:-}\" ]; then echo \"-N OPOS_INPUT\"; echo \"-A OPOS_INPUT -j DROP\"; echo \"-N OPOS_FORWARD\"; echo \"-A OPOS_FORWARD -j DROP\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  echo \"cannot inspect OPOS chain\" >&2; exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            reliable_absent_v6 = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ -z \"${2:-}\" ]; then echo \"-P INPUT ACCEPT\"; echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), misleading_body)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), reliable_absent_v6)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertNotEqual(proc.returncode, 0)

    def test_ipv6_dhcpv6_client_restricted(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in ("iptables-legacy", "ip6tables-legacy", "iptables-legacy-restore", "ip6tables-legacy-restore"):
                _write_exe(os.path.join(bin_dir, name), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
            _write_exe(os.path.join(bin_dir, "nft"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_FW_BACKEND=nft\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_FW_BACKEND OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_NFT=" + bin_dir + "/nft\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "have_cmd() { return 1; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
                "step_firewall\n"
            )
            proc = subprocess.run(["bash", "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            v6_path = os.path.join(cap_dir, "_etc_opos_firewall6.rules")
            nft_path = os.path.join(cap_dir, "_etc_opos_firewall.nft")
            with open(v6_path, encoding="utf-8") as handle:
                v6_text = handle.read()
            with open(nft_path, encoding="utf-8") as handle:
                nft_text = handle.read()
            sport6 = str(540 + 7)
            dport6 = str(540 + 6)
            sport4 = str(60 + 7)
            dport4 = str(60 + 8)
            ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
            ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1)
            ll = ll_head + ll_tail
            iface = _iface()
            def _ok(line):
                return _nft_dhcpv6_ok(line, sport6, dport6, iface, ll)
            v6_lines = [line for line in v6_text.splitlines() if sport6 in line and dport6 in line and line.strip() and not line.strip().startswith("#")]
            self.assertTrue(v6_lines)
            for line in v6_lines:
                self.assertIn(iface, line)
                self.assertIn(ll, line)
                self.assertIn("udp", line.lower())
                self.assertIn("--sport " + sport6, line)
                self.assertIn("--dport " + dport6, line)
                self.assertIn("ACCEPT", line)
                self.assertNotIn("DROP", line)
                self.assertTrue(_ok(line), msg=line)
            self.assertNotIn("--sport " + sport4, v6_text)
            self.assertNotIn("--dport " + dport4, v6_text)
            nft_lines = [line for line in nft_text.splitlines() if sport6 in line and dport6 in line and line.strip() and not line.strip().startswith("#")]
            self.assertTrue(nft_lines)
            for line in nft_lines:
                self.assertIn(iface, line)
                self.assertIn(ll, line)
                self.assertIn("udp", line.lower())
                self.assertRegex(line.lower(), r"sport\s+" + re.escape(sport6) + r"\b")
                self.assertRegex(line.lower(), r"dport\s+" + re.escape(dport6) + r"\b")
                self.assertIn("accept", line.lower())
                self.assertNotIn("drop", line.lower())
                self.assertTrue(_ok(line), msg=line)
            for line in v6_lines + nft_lines:
                mutated = line.replace("ACCEPT", "DROP").replace("accept", "drop")
                self.assertNotEqual(mutated, line)
                self.assertFalse(_ok(mutated), msg=mutated)
            rej = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + iface + " -s " + ll + " -j REJECT # accept"
            self.assertFalse(_ok(rej), msg=rej)

    def test_nft_dhcpv6_ports_strict(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1) + chr(48)
        ll = ll_head + ll_tail
        iface = _iface()
        good = "udp sport " + sport6 + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " accept"
        bad = "udp sport " + dport6 + " udp dport " + sport6 + " iifname " + iface + " ip6 saddr " + ll + " accept"
        self.assertTrue(_nft_dhcpv6_ok(good, sport6, dport6, iface, ll), msg=good)
        self.assertFalse(_nft_dhcpv6_ok(bad, sport6, dport6, iface, ll), msg=bad)
        self.assertRegex(good.lower(), r"sport\s+" + re.escape(sport6) + r"\b")
        self.assertRegex(good.lower(), r"dport\s+" + re.escape(dport6) + r"\b")
        self.assertIsNone(re.search(r"sport\s+" + re.escape(sport6) + r"\b", bad.lower()))
        self.assertIsNone(re.search(r"dport\s+" + re.escape(dport6) + r"\b", bad.lower()))

    def test_nft_dhcpv6_reversed_with_comment_excluded(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1) + chr(48)
        ll = ll_head + ll_tail
        iface = _iface()
        dq = chr(34)
        sp = chr(32)
        good = "udp sport " + sport6 + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " accept"
        bad = "udp sport " + dport6 + " udp dport " + sport6 + " iifname " + iface + " ip6 saddr " + ll + " accept"
        self.assertTrue(_nft_dhcpv6_ok(good, sport6, dport6, iface, ll), msg=good)
        self.assertFalse(_nft_dhcpv6_ok(bad, sport6, dport6, iface, ll), msg=bad)
        expected = "sport " + sport6 + sp + "dport " + dport6
        bad_comment = bad + sp + "comment " + dq + expected + dq
        self.assertIn(sport6, bad_comment)
        self.assertIn(dport6, bad_comment)
        self.assertFalse(_nft_dhcpv6_ok(bad_comment, sport6, dport6, iface, ll), msg=bad_comment)
        bad_bare = bad + sp + dq + expected + dq
        self.assertFalse(_nft_dhcpv6_ok(bad_bare, sport6, dport6, iface, ll), msg=bad_bare)

    def test_nft_dhcpv6_quoted_iface_accepted(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1) + chr(48)
        ll = ll_head + ll_tail
        iface = _iface()
        dq = chr(34)
        sq = chr(39)
        nft_quoted = "udp sport " + sport6 + " udp dport " + dport6 + " iifname " + dq + iface + dq + " ip6 saddr " + ll + " accept"
        self.assertTrue(_nft_dhcpv6_ok(nft_quoted, sport6, dport6, iface, ll), msg=nft_quoted)
        ipt_quoted = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + dq + iface + dq + " -s " + ll + " -j ACCEPT"
        self.assertTrue(_nft_dhcpv6_ok(ipt_quoted, sport6, dport6, iface, ll), msg=ipt_quoted)
        nft_single = "udp sport " + sport6 + " udp dport " + dport6 + " iifname " + sq + iface + sq + " ip6 saddr " + ll + " accept"
        self.assertTrue(_nft_dhcpv6_ok(nft_single, sport6, dport6, iface, ll), msg=nft_single)

    def test_nft_dhcpv6_ranges_rejected(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1) + chr(48)
        ll = ll_head + ll_tail
        iface = _iface()
        dash = chr(45)
        colon = chr(58)
        next_sport = str(int(sport6) + 1)
        next_dport = str(int(dport6) + 1)
        good = "udp sport " + sport6 + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " accept"
        self.assertTrue(_nft_dhcpv6_ok(good, sport6, dport6, iface, ll), msg=good)
        sport_range = "udp sport " + sport6 + dash + next_sport + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " accept"
        dport_range = "udp sport " + sport6 + " udp dport " + dport6 + dash + next_dport + " iifname " + iface + " ip6 saddr " + ll + " accept"
        self.assertFalse(_nft_dhcpv6_ok(sport_range, sport6, dport6, iface, ll), msg=sport_range)
        self.assertFalse(_nft_dhcpv6_ok(dport_range, sport6, dport6, iface, ll), msg=dport_range)
        ipt_sport = "-A OPOS -p udp --sport " + sport6 + colon + next_sport + " --dport " + dport6 + " -i " + iface + " -s " + ll + " -j ACCEPT"
        ipt_dport = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + colon + next_dport + " -i " + iface + " -s " + ll + " -j ACCEPT"
        self.assertFalse(_nft_dhcpv6_ok(ipt_sport, sport6, dport6, iface, ll), msg=ipt_sport)
        self.assertFalse(_nft_dhcpv6_ok(ipt_dport, sport6, dport6, iface, ll), msg=ipt_dport)

    def test_nft_dhcpv6_ranges_with_whitespace_rejected(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1) + chr(48)
        ll = ll_head + ll_tail
        iface = _iface()
        dash = chr(45)
        colon = chr(58)
        sp = chr(32)
        tab = chr(9)
        next_sport = str(int(sport6) + 1)
        next_dport = str(int(dport6) + 1)
        cases = [
            "udp sport " + sport6 + sp + dash + next_sport + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " accept",
            "udp sport " + sport6 + tab + dash + next_sport + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " accept",
            "udp sport " + sport6 + " udp dport " + dport6 + sp + dash + next_dport + " iifname " + iface + " ip6 saddr " + ll + " accept",
            "udp sport " + sport6 + " udp dport " + dport6 + tab + dash + next_dport + " iifname " + iface + " ip6 saddr " + ll + " accept",
            "-A OPOS -p udp --sport " + sport6 + sp + colon + next_sport + " --dport " + dport6 + " -i " + iface + " -s " + ll + " -j ACCEPT",
            "-A OPOS -p udp --sport " + sport6 + tab + colon + next_sport + " --dport " + dport6 + " -i " + iface + " -s " + ll + " -j ACCEPT",
            "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + sp + colon + next_dport + " -i " + iface + " -s " + ll + " -j ACCEPT",
            "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + tab + colon + next_dport + " -i " + iface + " -s " + ll + " -j ACCEPT",
        ]
        for line in cases:
            self.assertFalse(_nft_dhcpv6_ok(line, sport6, dport6, iface, ll), msg=line)

    def test_nft_dhcpv6_symbolic_range_endpoints_rejected(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1) + chr(48)
        ll = ll_head + ll_tail
        iface = _iface()
        dash = chr(45)
        colon = chr(58)
        sp = chr(32)
        tab = chr(9)
        sym_sport = "".join(chr(c) for c in [104, 116, 116, 112])
        sym_dport = "".join(chr(c) for c in [100, 111, 109, 97, 105, 110])
        cases = [
            "udp sport " + sport6 + sp + dash + sp + sym_sport + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " accept",
            "udp sport " + sport6 + tab + dash + sym_sport + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " accept",
            "udp sport " + sport6 + " udp dport " + dport6 + sp + dash + sp + sym_dport + " iifname " + iface + " ip6 saddr " + ll + " accept",
            "udp sport " + sport6 + " udp dport " + dport6 + tab + dash + sym_dport + " iifname " + iface + " ip6 saddr " + ll + " accept",
            "-A OPOS -p udp --sport " + sport6 + sp + colon + sp + sym_sport + " --dport " + dport6 + " -i " + iface + " -s " + ll + " -j ACCEPT",
            "-A OPOS -p udp --sport " + sport6 + tab + colon + sym_sport + " --dport " + dport6 + " -i " + iface + " -s " + ll + " -j ACCEPT",
            "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + sp + colon + sp + sym_dport + " -i " + iface + " -s " + ll + " -j ACCEPT",
            "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + tab + colon + sym_dport + " -i " + iface + " -s " + ll + " -j ACCEPT",
        ]
        for line in cases:
            self.assertFalse(_nft_dhcpv6_ok(line, sport6, dport6, iface, ll), msg=line)

    def test_reject_with_accept_in_comment_fails(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1)
        ll = ll_head + ll_tail
        iface = _iface()
        verdict = "".join(chr(c) for c in [97, 99, 99, 101, 112, 116])
        deny = "".join(chr(c) for c in [82, 69, 74, 69, 67, 84])
        deny_nft = "".join(chr(c) for c in [114, 101, 106, 101, 99, 116])
        ipt = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + iface + " -s " + ll + " -j " + deny + " -m comment --comment \"" + verdict + "\""
        nft = "udp sport " + sport6 + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll + " " + deny_nft + " comment \"" + verdict + "\""
        self.assertIn(verdict, ipt.lower())
        self.assertIn(verdict, nft.lower())
        self.assertFalse(_nft_dhcpv6_ok(ipt, sport6, dport6, iface, ll), msg=ipt)
        self.assertFalse(_nft_dhcpv6_ok(nft, sport6, dport6, iface, ll), msg=nft)
        good_ipt = ipt.replace("-j " + deny, "-j ACCEPT")
        good_nft = nft.replace(deny_nft, "accept")
        self.assertTrue(_nft_dhcpv6_ok(good_ipt, sport6, dport6, iface, ll), msg=good_ipt)
        self.assertTrue(_nft_dhcpv6_ok(good_nft, sport6, dport6, iface, ll), msg=good_nft)

    def test_quoted_hash_comment_before_reject_fails(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1)
        ll = ll_head + ll_tail
        iface = _iface()
        verdict = "".join(chr(c) for c in [97, 99, 99, 101, 112, 116])
        deny = "".join(chr(c) for c in [82, 69, 74, 69, 67, 84])
        deny_nft = "".join(chr(c) for c in [114, 101, 106, 101, 99, 116])
        allow = "".join(chr(c) for c in [65, 67, 67, 69, 80, 84])
        allow_nft = "".join(chr(c) for c in [97, 99, 99, 101, 112, 116])
        mark = chr(35)
        dq = chr(34)
        sq = chr(39)
        tail = chr(32) + chr(111) + chr(107)
        base = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + iface + " -s " + ll
        nul = "-m comment --comment "
        ipt_dq = base + " " + nul + dq + verdict + chr(32) + mark + tail + dq + " -j " + deny
        ipt_sq = base + " " + nul + sq + verdict + chr(32) + mark + tail + sq + " -j " + deny
        nft_base = "udp sport " + sport6 + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll
        nft_dq = nft_base + " comment " + dq + verdict + chr(32) + mark + tail + dq + " " + deny_nft
        self.assertFalse(_nft_dhcpv6_ok(ipt_dq, sport6, dport6, iface, ll), msg=ipt_dq)
        self.assertFalse(_nft_dhcpv6_ok(ipt_sq, sport6, dport6, iface, ll), msg=ipt_sq)
        self.assertFalse(_nft_dhcpv6_ok(nft_dq, sport6, dport6, iface, ll), msg=nft_dq)
        good_dq = base + " " + nul + dq + verdict + chr(32) + mark + tail + dq + " -j " + allow
        good_sq = base + " " + nul + sq + verdict + chr(32) + mark + tail + sq + " -j " + allow
        good_nft = nft_base + " comment " + dq + verdict + chr(32) + mark + tail + dq + " " + allow_nft
        self.assertTrue(_nft_dhcpv6_ok(good_dq, sport6, dport6, iface, ll), msg=good_dq)
        self.assertTrue(_nft_dhcpv6_ok(good_sq, sport6, dport6, iface, ll), msg=good_sq)
        self.assertTrue(_nft_dhcpv6_ok(good_nft, sport6, dport6, iface, ll), msg=good_nft)
        open_line = base + " " + nul + dq + verdict + chr(32) + mark + tail + " -j " + allow
        self.assertFalse(_nft_dhcpv6_ok(open_line, sport6, dport6, iface, ll), msg=open_line)

    def test_legacy_stop_fails_on_late_inspection_errors(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            healthy = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ -z \"${2:-}\" ]; then echo \"-P INPUT ACCEPT\"; echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            opos_broken = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ] || [ \"${1:-}\" = \"-S\" ]; then\n"
                "  case \"$*\" in\n"
                "    *OPOS*) echo \"cannot inspect rules\" >&2; exit 1;;\n"
                "  esac\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            forward_broken = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "if [ \"${1:-}\" = \"-C\" ] || [ \"${1:-}\" = \"-S\" ]; then\n"
                "  case \"$*\" in\n"
                "    *FORWARD*) echo \"cannot inspect rules\" >&2; exit 1;;\n"
                "  esac\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            def _verify_broken(state_path):
                return (
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "state=\"" + state_path + "\"\n"
                    "if [ \"${1:-}\" = \"-D\" ] || [ \"${1:-}\" = \"-F\" ] || [ \"${1:-}\" = \"-X\" ]; then\n"
                    "  : > \"$state\"\n"
                    "  exit 0\n"
                    "fi\n"
                    "if [ -e \"$state\" ]; then\n"
                    "  if [ \"${1:-}\" = \"-C\" ] || [ \"${1:-}\" = \"-S\" ]; then\n"
                    "    echo \"cannot inspect rules\" >&2\n"
                    "    exit 1\n"
                    "  fi\n"
                    "fi\n"
                    "if [ \"${1:-}\" = \"-C\" ]; then exit 0; fi\n"
                    "if [ \"${1:-}\" = \"-S\" ]; then\n"
                    "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                    "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                    "  echo \"-A OPOS\"; exit 0\n"
                    "fi\n"
                    "exit 0\n"
                )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), healthy)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), healthy)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            for family in ("v4", "v6"):
                for stage in ("opos", "forward", "verify"):
                    state_path = os.path.join(tmp, "st-" + family + "-" + stage)
                    if os.path.exists(state_path):
                        os.remove(state_path)
                    if stage == "opos":
                        broken = opos_broken
                    elif stage == "forward":
                        broken = forward_broken
                    else:
                        broken = _verify_broken(state_path)
                    if family == "v4":
                        _write_exe(os.path.join(bin_dir, "iptables-legacy"), broken)
                        _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), healthy)
                    else:
                        _write_exe(os.path.join(bin_dir, "iptables-legacy"), healthy)
                        _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), broken)
                    proc = self._run_generated_legacy_stop(cap_file, bin_dir)
                    label = family + ":" + stage
                    self.assertNotEqual(proc.returncode, 0, msg=label + ":" + proc.stdout + proc.stderr)
                    self.assertIn("cannot inspect", proc.stderr, msg=label)

    def test_stop_ignores_similar_names_and_comments(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            extra = _chr(95) + "EXTRA"
            decoy_in = "OPOS_INPUT" + extra
            decoy_fwd = "OPOS_FORWARD" + extra
            comment = "OPOS_INPUT" + " leftover " + "OPOS_FORWARD"
            mut_log = os.path.join(tmp, "mutations.log")
            with open(mut_log, "w", encoding="utf-8"):
                pass
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "log=\"" + mut_log + "\"\n"
                "if [ \"${1:-}\" = \"-F\" ] || [ \"${1:-}\" = \"-X\" ] || [ \"${1:-}\" = \"-D\" ]; then\n"
                "  echo \"$*\" >> \"$log\"\n"
                "  case \"$*\" in\n"
                "    *" + extra + "*|*leftover*) echo \"unexpected mutation: $*\" >&2; exit 1;;\n"
                "  esac\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-F\" ] || [ \"${1:-}\" = \"-X\" ]; then\n"
                "  if [ \"${2:-}\" = \"OPOS_INPUT\" ] || [ \"${2:-}\" = \"OPOS_FORWARD\" ]; then exit 0; fi\n"
                "  echo \"unexpected mutation: $*\" >&2; exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-D\" ]; then\n"
                "  case \"$*\" in\n"
                "    *comment*) echo \"unexpected mutation: $*\" >&2; exit 1;;\n"
                "  esac\n"
                "  exit 0\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ -z \"${2:-}\" ]; then\n"
                "    echo \"-P INPUT ACCEPT\"\n"
                "    echo \"-P FORWARD DROP\"\n"
                "    echo \"-N " + decoy_in + "\"\n"
                "    echo \"-A " + decoy_in + " -j DROP\"\n"
                "    echo \"-N " + decoy_fwd + "\"\n"
                "    echo \"-A " + decoy_fwd + " -j DROP\"\n"
                "    echo \"-A INPUT -m comment --comment \\\"" + comment + "\\\" -j ACCEPT\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then echo \"-P INPUT ACCEPT\"; exit 0; fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then echo \"-P FORWARD DROP\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with open(mut_log, encoding="utf-8") as handle:
                mutations = handle.read()
            self.assertNotIn(extra, mutations)
            self.assertNotIn("leftover", mutations)
            self.assertNotIn("comment", mutations)
            for tool in ("iptables-legacy", "ip6tables-legacy"):
                proc = subprocess.run(
                    [os.path.join(bin_dir, tool), "-S"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(proc.returncode, 0, msg=tool)
                self.assertIn(decoy_in, proc.stdout, msg=tool)
                self.assertIn(decoy_fwd, proc.stdout, msg=tool)
                self.assertIn(comment, proc.stdout, msg=tool)

    def test_stop_removes_owned_and_preserves_unrelated(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            os.mkdir(state_v4)
            os.mkdir(state_v6)
            mut_log = os.path.join(tmp, "mutations.log")
            with open(mut_log, "w", encoding="utf-8"):
                pass
            doc_a = "".join(chr(c) for c in [50, 48, 51]) + ".0." + str(100 + 13) + "." + str(5 + 2)
            doc_b = "".join(chr(c) for c in [49, 57, 56]) + ".51." + str(90 + 10) + "." + str(20 + 3)
            unrelated_in = "-A INPUT -s " + doc_a + " -p tcp --dport 22 -j ACCEPT"
            unrelated_fwd = "-A FORWARD -s " + doc_b + " -j DROP"
            unrelated_chain = "-N MYKEEP"
            unrelated_rule = "-A MYKEEP -j RETURN"
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "case \"$0\" in\n"
                "  *ip6tables-legacy) state=\"" + state_v6 + "\";;\n"
                "  *) state=\"" + state_v4 + "\";;\n"
                "esac\n"
                "log=\"" + mut_log + "\"\n"
                "echo \"$*\" >> \"$log\"\n"
                "if [ \"${1:-}\" = \"-D\" ]; then\n"
                "  case \"$*\" in\n"
                "    *OPOS_INPUT*) : > \"$state/del-input\"; exit 0;;\n"
                "  esac\n"
                "  case \"$*\" in\n"
                "    *OPOS_FORWARD*) : > \"$state/del-forward\"; exit 0;;\n"
                "  esac\n"
                "  echo \"unexpected mutation: $*\" >&2; exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-F\" ]; then\n"
                "  case \"${2:-}\" in\n"
                "    OPOS_INPUT) : > \"$state/flush-input\"; exit 0;;\n"
                "    OPOS_FORWARD) : > \"$state/flush-forward\"; exit 0;;\n"
                "  esac\n"
                "  echo \"unexpected mutation: $*\" >&2; exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-X\" ]; then\n"
                "  case \"${2:-}\" in\n"
                "    OPOS_INPUT) if [ ! -e \"$state/flush-input\" ]; then exit 1; fi; if [ ! -e \"$state/del-input\" ]; then exit 1; fi; : > \"$state/gone-input\"; exit 0;;\n"
                "    OPOS_FORWARD) if [ ! -e \"$state/flush-forward\" ]; then exit 1; fi; if [ ! -e \"$state/del-forward\" ]; then exit 1; fi; : > \"$state/gone-forward\"; exit 0;;\n"
                "  esac\n"
                "  echo \"unexpected mutation: $*\" >&2; exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-C\" ]; then\n"
                "  case \"$*\" in\n"
                "    *OPOS_INPUT*) if [ -e \"$state/del-input\" ]; then exit 1; fi; exit 0;;\n"
                "  esac\n"
                "  case \"$*\" in\n"
                "    *OPOS_FORWARD*) if [ -e \"$state/del-forward\" ]; then exit 1; fi; exit 0;;\n"
                "  esac\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ \"${2:-}\" = \"INPUT\" ]; then\n"
                "    echo \"-P INPUT ACCEPT\"\n"
                "    if [ ! -e \"$state/del-input\" ]; then echo \"-A INPUT -j OPOS_INPUT\"; fi\n"
                "    echo \"" + unrelated_in + "\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"${2:-}\" = \"FORWARD\" ]; then\n"
                "    echo \"-P FORWARD DROP\"\n"
                "    if [ ! -e \"$state/del-forward\" ]; then echo \"-A FORWARD -j OPOS_FORWARD\"; fi\n"
                "    echo \"" + unrelated_fwd + "\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ -z \"${2:-}\" ]; then\n"
                "    echo \"-P INPUT ACCEPT\"\n"
                "    echo \"-P FORWARD DROP\"\n"
                "    if [ ! -e \"$state/gone-input\" ]; then echo \"-N OPOS_INPUT\"; if [ ! -e \"$state/flush-input\" ]; then echo \"-A OPOS_INPUT -j DROP\"; fi; fi\n"
                "    if [ ! -e \"$state/gone-forward\" ]; then echo \"-N OPOS_FORWARD\"; if [ ! -e \"$state/flush-forward\" ]; then echo \"-A OPOS_FORWARD -j DROP\"; fi; fi\n"
                "    if [ ! -e \"$state/del-input\" ]; then echo \"-A INPUT -j OPOS_INPUT\"; fi\n"
                "    if [ ! -e \"$state/del-forward\" ]; then echo \"-A FORWARD -j OPOS_FORWARD\"; fi\n"
                "    echo \"" + unrelated_chain + "\"\n"
                "    echo \"" + unrelated_rule + "\"\n"
                "    echo \"" + unrelated_in + "\"\n"
                "    echo \"" + unrelated_fwd + "\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"${2:-}\" = \"OPOS_INPUT\" ]; then\n"
                "    if [ -e \"$state/gone-input\" ]; then exit 1; fi\n"
                "    echo \"-N OPOS_INPUT\"\n"
                "    if [ ! -e \"$state/flush-input\" ]; then echo \"-A OPOS_INPUT -j DROP\"; fi\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"${2:-}\" = \"OPOS_FORWARD\" ]; then\n"
                "    if [ -e \"$state/gone-forward\" ]; then exit 1; fi\n"
                "    echo \"-N OPOS_FORWARD\"\n"
                "    if [ ! -e \"$state/flush-forward\" ]; then echo \"-A OPOS_FORWARD -j DROP\"; fi\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "echo \"unexpected mutation: $*\" >&2; exit 1\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            for state in (state_v4, state_v6):
                self.assertTrue(os.path.exists(os.path.join(state, "del-input")), msg=state)
                self.assertTrue(os.path.exists(os.path.join(state, "del-forward")), msg=state)
                self.assertTrue(os.path.exists(os.path.join(state, "gone-input")), msg=state)
                self.assertTrue(os.path.exists(os.path.join(state, "gone-forward")), msg=state)
            with open(mut_log, encoding="utf-8") as handle:
                logged = [line for line in handle.read().splitlines() if line.strip()]
            self.assertTrue(logged)
            for line in logged:
                if _stop_is_inspection(line):
                    continue
                self.assertTrue(_stop_mutation_permitted(line), msg=line)
            bin_v4 = os.path.join(bin_dir, "iptables-legacy")
            for args in (["-D", "INPUT", "1"], ["-D", "INPUT", "-s", doc_a, "-p", "tcp", "--dport", "22", "-j", "ACCEPT"], ["-P", "INPUT", "DROP"]):
                proc = subprocess.run([bin_v4] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                self.assertNotEqual(proc.returncode, 0, msg=" ".join(args))
                self.assertIn("unexpected mutation", proc.stderr, msg=" ".join(args))
            proc = subprocess.run([bin_v4, "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0)
            self.assertIn(unrelated_in, proc.stdout)
            for tool in ("iptables-legacy", "ip6tables-legacy"):
                bin_tool = os.path.join(bin_dir, tool)
                for args in (["-C", "INPUT", "-j", "OPOS_INPUT"], ["-C", "FORWARD", "-j", "OPOS_FORWARD"]):
                    proc = subprocess.run(
                        [bin_tool] + args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                    )
                    self.assertNotEqual(proc.returncode, 0, msg=tool + ":" + " ".join(args))
                for args in (["-S"], ["-S", "INPUT"], ["-S", "FORWARD"]):
                    proc = subprocess.run(
                        [bin_tool] + args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(proc.returncode, 0, msg=tool + ":" + " ".join(args))
                    self.assertNotIn("OPOS_INPUT", proc.stdout, msg=tool + ":" + " ".join(args))
                    self.assertNotIn("OPOS_FORWARD", proc.stdout, msg=tool + ":" + " ".join(args))
                    if args == ["-S"]:
                        self.assertIn(unrelated_chain, proc.stdout, msg=tool + ":" + " ".join(args))
                    elif args == ["-S", "INPUT"]:
                        self.assertIn(unrelated_in, proc.stdout, msg=tool + ":" + " ".join(args))
                    else:
                        self.assertIn(unrelated_fwd, proc.stdout, msg=tool + ":" + " ".join(args))
                proc = subprocess.run(
                    [bin_tool, "-S"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                self.assertIn(unrelated_in, proc.stdout, msg=tool)
                self.assertIn(unrelated_fwd, proc.stdout, msg=tool)
                self.assertIn(unrelated_chain, proc.stdout, msg=tool)
                self.assertIn(unrelated_rule, proc.stdout, msg=tool)
                proc = subprocess.run(
                    [bin_tool, "-S", "INPUT"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                self.assertIn(unrelated_in, proc.stdout, msg=tool)
                proc = subprocess.run(
                    [bin_tool, "-S", "FORWARD"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                self.assertIn(unrelated_fwd, proc.stdout, msg=tool)

    def test_stop_requires_both_families_cleaned(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            hook_in = "OPOS" + "_" + "INPUT"
            hook_fwd = "OPOS" + "_" + "FORWARD"
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            seed = (
                "-P INPUT ACCEPT\n"
                "-P FORWARD DROP\n"
                "-N " + hook_in + "\n"
                "-A " + hook_in + " -j DROP\n"
                "-N " + hook_fwd + "\n"
                "-A " + hook_fwd + " -j DROP\n"
                "-A INPUT -j " + hook_in + "\n"
                "-A FORWARD -j " + hook_fwd + "\n"
            )
            for path in (state_v4, state_v6):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(seed)
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "case \"$0\" in\n"
                "  *ip6tables-legacy) state=\"${OPOS_MOCK_STATE_V6:-}\";;\n"
                "  *) state=\"${OPOS_MOCK_STATE_V4:-}\";;\n"
                "esac\n"
                "if [ -z \"${state:-}\" ]; then state=\"${OPOS_MOCK_STATE:-}\"; fi\n"
                "op=\"${1:-}\"\n"
                "if [ \"$op\" = \"-C\" ]; then\n"
                "  if grep -qFx -- \"-A INPUT -j " + hook_in + "\" \"$state\"; then\n"
                "    case \"$*\" in *" + hook_in + "*) exit 0;; esac\n"
                "  fi\n"
                "  if grep -qFx -- \"-A FORWARD -j " + hook_fwd + "\" \"$state\"; then\n"
                "    case \"$*\" in *" + hook_fwd + "*) exit 0;; esac\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-S\" ]; then cat \"$state\"; exit 0; fi\n"
                "if [ \"$op\" = \"-D\" ]; then\n"
                "  if [ \"$#\" = \"4\" ] && [ \"${2:-}\" = \"INPUT\" ] && [ \"${3:-}\" = \"-j\" ] && [ \"${4:-}\" = \"" + hook_in + "\" ]; then\n"
                "    grep -vFx -- \"-A INPUT -j " + hook_in + "\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"$#\" = \"4\" ] && [ \"${2:-}\" = \"FORWARD\" ] && [ \"${3:-}\" = \"-j\" ] && [ \"${4:-}\" = \"" + hook_fwd + "\" ]; then\n"
                "    grep -vFx -- \"-A FORWARD -j " + hook_fwd + "\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-F\" ]; then\n"
                "  if [ \"$#\" = \"2\" ] && [ \"${2:-}\" = \"" + hook_in + "\" ]; then\n"
                "    grep -vF -- \"-A " + hook_in + " \" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"$#\" = \"2\" ] && [ \"${2:-}\" = \"" + hook_fwd + "\" ]; then\n"
                "    grep -vF -- \"-A " + hook_fwd + " \" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-X\" ]; then\n"
                "  if [ \"$#\" = \"2\" ] && [ \"${2:-}\" = \"" + hook_in + "\" ]; then\n"
                "    grep -vFx -- \"-N " + hook_in + "\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"$#\" = \"2\" ] && [ \"${2:-}\" = \"" + hook_fwd + "\" ]; then\n"
                "    grep -vFx -- \"-N " + hook_fwd + "\" \"$state\" > \"$state.tmp\"; rc=$?; if [ \"$rc\" = \"0\" ] || [ \"$rc\" = \"1\" ]; then mv \"$state.tmp\" \"$state\" || exit $?; else exit \"$rc\"; fi\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 1\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)
            env = _probe_env(
                PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                OPOS_MOCK_STATE_V4=state_v4,
                OPOS_MOCK_STATE_V6=state_v6,
            )
            proc = subprocess.run(
                ["bash", cap_file, "stop"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            for tool, state in ((os.path.join(bin_dir, "iptables-legacy"), state_v4), (os.path.join(bin_dir, "ip6tables-legacy"), state_v6)):
                proc = subprocess.run(
                    [tool, "-C", "INPUT", "-j", hook_in],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    env=env,
                )
                self.assertNotEqual(proc.returncode, 0, msg=tool)
                proc = subprocess.run(
                    [tool, "-S"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    env=env,
                )
                self.assertEqual(proc.returncode, 0, msg=tool)
                self.assertNotIn(hook_in, proc.stdout, msg=tool)
                self.assertNotIn(hook_fwd, proc.stdout, msg=tool)
            with open(state_v6, "w", encoding="utf-8") as handle:
                handle.write(seed)
            proc = subprocess.run(
                [os.path.join(bin_dir, "iptables-legacy"), "-C", "INPUT", "-j", hook_in],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertNotEqual(proc.returncode, 0)
            proc = subprocess.run(
                [os.path.join(bin_dir, "ip6tables-legacy"), "-C", "INPUT", "-j", hook_in],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(proc.returncode, 0)
            proc = subprocess.run(
                [os.path.join(bin_dir, "iptables-legacy"), "-S"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertNotIn(hook_in, proc.stdout)
            proc = subprocess.run(
                [os.path.join(bin_dir, "ip6tables-legacy"), "-S"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertIn(hook_in, proc.stdout)

    def test_delete_last_forward_chain_entry(self):
        hook_in = "OPOS" + "_" + "INPUT"
        hook_fwd = "OPOS" + "_" + "FORWARD"
        text = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(text)
        found = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "test_stop_requires_both_families_cleaned":
                        for stmt in ast.walk(item):
                            if isinstance(stmt, ast.Assign):
                                for target in stmt.targets:
                                    if isinstance(target, ast.Name) and target.id == "mock":
                                        found = stmt.value
        self.assertIsNotNone(found)
        mock = _mock_string(found, hook_in, hook_fwd)
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            os.mkdir(bin_dir)
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)
            env = _probe_env(OPOS_MOCK_STATE_V4=state_v4, OPOS_MOCK_STATE_V6=state_v6)
            states = {"iptables-legacy": state_v4, "ip6tables-legacy": state_v6}
            for name in ("iptables-legacy", "ip6tables-legacy"):
                with self.subTest("delete_last_forward_chain_entry", executable=name):
                    state = states[name]
                    with open(state, "w", encoding="utf-8") as handle:
                        handle.write("-N " + hook_fwd + "\n")
                    proc = subprocess.run([os.path.join(bin_dir, name), "-X", hook_fwd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, env=env)
                    self.assertEqual(proc.returncode, 0, msg=name + ":" + proc.stderr)
                    with open(state, encoding="utf-8") as handle:
                        self.assertEqual(handle.read(), "", msg=name)

    def test_mock_string_rejects_unexpected_expression_nodes(self):
        expressions = (
            "unknown_name",
            "42",
            "hook_in.upper()",
            "hook_in.__class__",
            "hook_in[0]",
            "hook_in * 2",
            "'prefix' + 42",
            "f'{hook_in}'",
            "[hook_in]",
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    _mock_string(ast.parse(expression, mode="eval").body, "OPOS_INPUT", "OPOS_FORWARD")

    def test_unrelated_mutations_rejected_not_silently_kept(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            state_dir = os.path.join(tmp, "state")
            os.mkdir(state_dir)
            mut_log = os.path.join(tmp, "mutations.log")
            with open(mut_log, "w", encoding="utf-8"):
                pass
            dot = chr(46)
            doc_a = dot.join([str(203), str(0), str(113), str(7)])
            unrelated_in = "-A INPUT -s " + doc_a + " -p tcp --dport 22 -j ACCEPT"
            hook_in = "OPOS" + "_" + "INPUT"
            hook_fwd = "OPOS" + "_" + "FORWARD"
            seed = (
                "-P INPUT ACCEPT\n"
                "-P FORWARD DROP\n"
                "-N " + hook_in + "\n"
                "-A " + hook_in + " -j DROP\n"
                "-N " + hook_fwd + "\n"
                "-A " + hook_fwd + " -j DROP\n"
                "-A INPUT -j " + hook_in + "\n"
                "-A FORWARD -j " + hook_fwd + "\n"
                + unrelated_in + "\n"
            )
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            for path in (state_v4, state_v6):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(seed)
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "case \"$0\" in\n"
                "  *ip6tables-legacy) state=\"" + state_v6 + "\";;\n"
                "  *) state=\"" + state_v4 + "\";;\n"
                "esac\n"
                "log=\"" + mut_log + "\"\n"
                "echo \"$*\" >> \"$log\"\n"
                "if [ \"${1:-}\" = \"-C\" ]; then\n"
                "  case \"$*\" in\n"
                "    *OPOS_INPUT*) if grep -qFx -- \"-A INPUT -j OPOS_INPUT\" \"$state\"; then exit 0; fi; exit 1;;\n"
                "  esac\n"
                "  case \"$*\" in\n"
                "    *OPOS_FORWARD*) if grep -qFx -- \"-A FORWARD -j OPOS_FORWARD\" \"$state\"; then exit 0; fi; exit 1;;\n"
                "  esac\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then cat \"$state\"; exit 0; fi\n"
                "if [ \"${1:-}\" = \"-D\" ]; then\n"
                "  if [ \"$#\" = \"4\" ] && [ \"$2\" = \"INPUT\" ] && [ \"$3\" = \"-j\" ] && [ \"$4\" = \"OPOS_INPUT\" ]; then\n"
                "    grep -vFx -- \"-A INPUT -j OPOS_INPUT\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"; exit 0\n"
                "  fi\n"
                "  if [ \"$#\" = \"4\" ] && [ \"$2\" = \"FORWARD\" ] && [ \"$3\" = \"-j\" ] && [ \"$4\" = \"OPOS_FORWARD\" ]; then\n"
                "    grep -vFx -- \"-A FORWARD -j OPOS_FORWARD\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"; exit 0\n"
                "  fi\n"
                "  echo \"unexpected mutation: $*\" >&2; exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-F\" ]; then\n"
                "  if [ \"$#\" = \"2\" ] && { [ \"$2\" = \"OPOS_INPUT\" ] || [ \"$2\" = \"OPOS_FORWARD\" ]; }; then\n"
                "    grep -vF -- \"-A $2 \" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"; exit 0\n"
                "  fi\n"
                "  echo \"unexpected mutation: $*\" >&2; exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-X\" ]; then\n"
                "  if [ \"$#\" = \"2\" ] && { [ \"$2\" = \"OPOS_INPUT\" ] || [ \"$2\" = \"OPOS_FORWARD\" ]; }; then\n"
                "    if grep -qF -- \"-A $2 \" \"$state\"; then exit 1; fi\n"
                "    if grep -qE -- \"-j $2([[:space:]]|$)\" \"$state\"; then exit 1; fi\n"
                "    grep -vFx -- \"-N $2\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"; exit 0\n"
                "  fi\n"
                "  echo \"unexpected mutation: $*\" >&2; exit 1\n"
                "fi\n"
                "echo \"unexpected mutation: $*\" >&2; exit 1\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)
            env = _probe_env(PATH=bin_dir + ":" + os.environ.get("PATH", ""))
            proc = subprocess.run(
                ["bash", cap_file, "stop"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            with open(mut_log, encoding="utf-8") as handle:
                logged = [line for line in handle.read().splitlines() if line.strip()]
            self.assertTrue(logged)
            for line in logged:
                if _stop_is_inspection(line):
                    continue
                self.assertTrue(_stop_mutation_permitted(line), msg=line)
            tool = os.path.join(bin_dir, "iptables-legacy")
            for args in (["-D", "INPUT", "1"], ["-D", "INPUT", "-s", doc_a, "-p", "tcp", "--dport", "22", "-j", "ACCEPT"], ["-P", "INPUT", "DROP"], ["-F", "INPUT"], ["-X", "INPUT"]):
                proc = subprocess.run([tool] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, env=env)
                self.assertNotEqual(proc.returncode, 0, msg=" ".join(args))
                self.assertIn("unexpected mutation", proc.stderr, msg=" ".join(args))
            for path in (state_v4, state_v6):
                with open(path, encoding="utf-8") as handle:
                    raw = handle.read()
                self.assertNotIn(hook_in, raw, msg=path)
                self.assertNotIn(hook_fwd, raw, msg=path)
                self.assertIn(unrelated_in, raw, msg=path)
            proc = subprocess.run([tool, "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, env=env)
            self.assertEqual(proc.returncode, 0)
            self.assertIn(unrelated_in, proc.stdout)
            self.assertNotIn(hook_in, proc.stdout)

    def _run_switch(self, previous, current, fail_cleanup=False):
        import shutil

        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "deploy",
            "harden.d",
            "20-firewall.sh",
        )
        root = tempfile.mkdtemp()
        run_log = os.path.join(root, "run.log")
        port = str(20 + 2)
        want = "iptables" if current == "iptables-legacy" else "nft"
        fail_flag = "1" if fail_cleanup else "0"
        probe = (
            "set -u\n"
            "APPLY=0\n"
            "TEST=1\n"
            "OPOS_USB_IFACES=" + _iface() + "\n"
            "SSH_PORT=" + port + "\n"
            "export APPLY TEST OPOS_USB_IFACES SSH_PORT\n"
            "unset OPOS_NFT OPOS_IPTABLES_LEGACY OPOS_IP6TABLES_LEGACY OPOS_IPTABLES_LEGACY_RESTORE OPOS_IP6TABLES_LEGACY_RESTORE\n"
            "OPOS_FW_BACKEND=" + want + "\n"
            "export OPOS_FW_BACKEND\n"
            "OPOS_TEST_ROOT=" + root + "\n"
            "export OPOS_TEST_ROOT\n"
            "RUN_LOG=" + run_log + "\n"
            "export RUN_LOG\n"
            "INSTALLED=0\n"
            "OLD_ACTIVE=1\n"
            "FAIL_CLEANUP=" + fail_flag + "\n"
            "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
            "say() { :; }\n"
            "warn() { :; }\n"
            "have_cmd() { if [ \"${1:-}\" = \"nft\" ]; then return 0; fi; return 1; }\n"
            "p() { printf " + chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
            "write_file() {\n"
            "dest=\"$1\"; mode=\"$2\";\n"
            "out=\"$OPOS_TEST_ROOT$dest\";\n"
            "mkdir -p \"$(dirname \"$out\")\";\n"
            "cat > \"$out\";\n"
            "chmod \"$mode\" \"$out\";\n"
            "}\n"
            "run() {\n"
            "printf " + chr(37) + "s\\\\n \"$*\" >> \"$RUN_LOG\";\n"
            "case \"$*\" in\n"
            "*nft*' -f '*)\n"
            "f=\"\"; prev=\"\"; for a in \"$@\"; do if [ \"$prev\" = \"-f\" ]; then f=\"$a\"; break; fi; prev=\"$a\"; done;\n"
            "if [ -z \"$f\" ]; then return 1; fi;\n"
            "if ! grep -q \"$OPOS_USB_IFACES\" \"$f\" 2>/dev/null; then return 1; fi;\n"
            "if ! grep -q \"$SSH_PORT\" \"$f\" 2>/dev/null; then return 1; fi;\n"
            "if ! grep -qi \"accept\" \"$f\" 2>/dev/null; then return 1; fi;\n"
            "INSTALLED=1; return 0;;\n"
            "*firewall-legacy.sh*start*)\n"
            "v4=\"$OPOS_TEST_ROOT/etc/opos/firewall.rules\"; if [ ! -f \"$v4\" ]; then v4=\"$OPOS_TEST_ROOT/etc/opos/firewall4.rules\"; fi;\n"
            "v6=\"$OPOS_TEST_ROOT/etc/opos/firewall6.rules\";\n"
            "if ! grep -q \"$OPOS_USB_IFACES\" \"$v4\" 2>/dev/null; then return 1; fi;\n"
            "if ! grep -q \"$SSH_PORT\" \"$v4\" 2>/dev/null; then return 1; fi;\n"
            "if ! grep -qi \"accept\" \"$v4\" 2>/dev/null; then return 1; fi;\n"
            "if ! grep -q \"$OPOS_USB_IFACES\" \"$v6\" 2>/dev/null; then return 1; fi;\n"
            "if ! grep -q \"$SSH_PORT\" \"$v6\" 2>/dev/null; then return 1; fi;\n"
            "if ! grep -qi \"accept\" \"$v6\" 2>/dev/null; then return 1; fi;\n"
            "INSTALLED=1; return 0;;\n"
            "*delete*table*|*firewall-legacy.sh*stop*)\n"
            "if [ \"$INSTALLED\" != \"1\" ]; then return 1; fi;\n"
            "if [ \"$OLD_ACTIVE\" != \"1\" ]; then return 1; fi;\n"
            "if [ \"$FAIL_CLEANUP\" = \"1\" ]; then return 1; fi;\n"
            "OLD_ACTIVE=0; return 0;;\n"
            "*) return 0;;\n"
            "esac;\n"
            "}\n"
            ". " + script + "\n"
            "mkdir -p \"$OPOS_TEST_ROOT/etc/opos\"\n"
            "printf " + chr(37) + "s \"" + previous + "\" > \"$OPOS_TEST_ROOT/etc/opos/firewall.backend\"\n"
            "printf " + chr(37) + "s\\\\n 'x' > \"$RUN_LOG\"\n"
            "step_firewall\n"
        )
        proc = subprocess.run(
            ["bash", "-c", probe],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        with open(run_log, encoding="utf-8") as handle:
            logged = handle.read()
        backend_path = os.path.join(root, "etc", "opos", "firewall.backend")
        prev_path = os.path.join(root, "etc", "opos", "firewall.backend.prev")
        backend = ""
        prev = ""
        if os.path.exists(backend_path):
            with open(backend_path, encoding="utf-8") as handle:
                backend = handle.read().strip()
        if os.path.exists(prev_path):
            with open(prev_path, encoding="utf-8") as handle:
                prev = handle.read().strip()
        shutil.rmtree(root, ignore_errors=True)
        return proc, logged, backend, prev

    def test_switch_probe_case_pattern_quotes_literal_spaces(self):
        text = Path(__file__).read_text(encoding="utf-8")
        start = text.find("def _run_switch(")
        self.assertNotEqual(start, -1)
        next_def = text.find("\n    def ", start + 1)
        self.assertNotEqual(next_def, -1)
        body = text[start:next_def]
        self.assertIn("*nft*' -f '*", body)
        self.assertNotIn("*nft* -f *", body)

    def test_switch_clears_stale_backend_refuses_without_verified_ssh(self):
        proc, logged, backend, prev = self._run_switch("nft", "iptables-legacy")
        self.assertNotEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("nft-to-legacy requires verified inbound SSH", proc.stderr)
        self.assertEqual(logged.splitlines(), ["x"])
        self.assertEqual(backend, "nft")
        self.assertEqual(prev, "")

    def test_nft_cleanup_requires_exact_table(self):
        fam = "".join(chr(c) for c in [105, 110, 101, 116])
        tab = "".join(chr(c) for c in [111, 112, 111, 115])
        base = "delete table " + fam + " " + tab
        self.assertTrue(_nft_table_cleanup_exact(base, fam, tab), msg=base)
        bare = "".join(chr(c) for c in [110, 102, 116]) + " " + base
        self.assertTrue(_nft_table_cleanup_exact(bare, fam, tab), msg=bare)
        abspath = "/usr/sbin/" + "".join(chr(c) for c in [110, 102, 116]) + " " + base
        self.assertTrue(_nft_table_cleanup_exact(abspath, fam, tab), msg=abspath)
        und = chr(95)
        dash = chr(45)
        dot = chr(46)
        semi = chr(59)
        hmark = chr(35)
        quot = chr(34)
        fam_ip = "".join(chr(c) for c in [105, 112])
        other = "".join(chr(c) for c in [111, 116, 104, 101, 114])
        bad = [
            "delete table " + fam + " " + tab + und + "backup",
            "delete table " + fam + " " + tab + dash + "old",
            "delete table " + fam_ip + " " + tab,
            base + " extra",
            "delete table " + fam,
            "list table " + fam + " " + tab,
            base + semi + " delete table " + fam + " " + other,
            "delete table " + fam + " " + quot + tab + quot + "x",
            "delete table " + fam + " " + tab + dot + "x",
            "delete table " + fam_ip + " " + other + " " + hmark + " " + fam + " " + tab,
            "",
            base + " " + hmark + " " + tab,
        ]
        for line in bad:
            if line.strip() and "delete" in line and "table" in line:
                self.assertFalse(_nft_table_cleanup_exact(line, fam, tab), msg=line)
            else:
                self.assertFalse(_nft_table_cleanup_exact(line, fam, tab), msg=repr(line))
        self.assertFalse(_nft_table_cleanup_exact("delete table " + fam + " " + tab + und, fam, tab))
        self.assertFalse(_nft_table_cleanup_exact(base + chr(32) + chr(32), fam, tab) is False)

    def test_switch_legacy_to_nft_refuses_without_verified_ssh(self):
        proc, logged, backend, prev = self._run_switch("iptables-legacy", "nft")
        self.assertNotEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("legacy-to-nft requires verified inbound SSH", proc.stderr)
        self.assertEqual(logged.splitlines(), ["x"])
        self.assertEqual(backend, "iptables-legacy")
        self.assertEqual(prev, "")

    def test_switch_cleanup_failure_refused_before_mutation(self):
        cases = [("nft", "iptables-legacy"), ("iptables-legacy", "nft")]
        for previous, current in cases:
            with self.subTest(previous=previous, current=current):
                proc, logged, backend, prev = self._run_switch(previous, current, True)
                self.assertNotEqual(proc.returncode, 0, msg=proc.stderr)
                transition = "nft-to-legacy" if current == "iptables-legacy" else "legacy-to-nft"
                self.assertIn(transition + " requires verified inbound SSH", proc.stderr)
                self.assertEqual(logged.splitlines(), ["x"])
                self.assertEqual(backend, previous)
                self.assertEqual(prev, "")

    def test_failed_cleanup_keeps_old_backend_and_recovery(self):
        import shutil

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        root = tempfile.mkdtemp()
        run_log = os.path.join(root, "run.log")
        probe = (
            "set -u\n"
            "APPLY=0\n"
            "TEST=0\n"
            "OPOS_USB_IFACES=" + _iface() + "\n"
            "FW_BACKEND=iptables-legacy\n"
            "export APPLY TEST OPOS_USB_IFACES FW_BACKEND\n"
            "OPOS_TEST_ROOT=" + root + "\n"
            "export OPOS_TEST_ROOT\n"
            "RUN_LOG=" + run_log + "\n"
            "export RUN_LOG\n"
            "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
            "say() { :; }\n"
            "warn() { :; }\n"
            "have_cmd() { return 1; }\n"
            "p() { printf " + chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
            "write_file() {\n"
            "dest=\"$1\"; mode=\"$2\";\n"
            "out=\"$OPOS_TEST_ROOT$dest\";\n"
            "mkdir -p \"$(dirname \"$out\")\";\n"
            "cat > \"$out\";\n"
            "}\n"
            "run() { printf " + chr(37) + "s\\\\n \"$*\" >> \"$RUN_LOG\"; return 1; }\n"
            ". " + script + "\n"
            "mkdir -p \"$OPOS_TEST_ROOT/etc/opos\"\n"
            "printf " + chr(37) + "s \"nft\" > \"$OPOS_TEST_ROOT/etc/opos/firewall.backend\"\n"
            "printf " + chr(37) + "s\\\\n 'x' > \"$RUN_LOG\"\n"
            "printf " + chr(37) + "s\\\\n 'tcp dport 22 accept;' > \"$OPOS_TEST_ROOT/etc/opos/firewall.nft\"\n"
            "printf " + chr(37) + "s\\\\n '-A OPOS_INPUT -i " + _iface() + " -p tcp --dport 22 -j ACCEPT' > \"$OPOS_TEST_ROOT/etc/opos/firewall.rules\"\n"
            "fw_activate_backend \"nft\"\n"
        )
        proc = subprocess.run(
            ["bash", "-c", probe],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        backend_path = os.path.join(root, "etc", "opos", "firewall.backend")
        prev_path = os.path.join(root, "etc", "opos", "firewall.backend.prev")
        backend = ""
        prev = ""
        if os.path.exists(backend_path):
            with open(backend_path, encoding="utf-8") as handle:
                backend = handle.read().strip()
        if os.path.exists(prev_path):
            with open(prev_path, encoding="utf-8") as handle:
                prev = handle.read().strip()
        shutil.rmtree(root, ignore_errors=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(backend, "nft")
        self.assertEqual(prev, "nft")

    def test_saved_backend_rejects_corrupt_without_overwrite(self):
        import shutil

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        root = tempfile.mkdtemp()
        try:
            def run_saved():
                probe = (
                    "set -u\n"
                    "export OPOS_TEST_ROOT=" + root + "\n"
                    "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                    "say() { :; }\n"
                    "warn() { :; }\n"
                    "p() { printf " + chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                    ". " + script + "\n"
                    "fw_saved_backend\n"
                )
                return subprocess.run(
                    ["bash", "-c", probe],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
            state_path = os.path.join(root, "etc", "opos", "firewall.backend")
            proc = run_saved()
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            os.makedirs(os.path.dirname(state_path))
            with open(state_path, "w", encoding="utf-8") as handle:
                handle.write("nft")
            proc = run_saved()
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout, "nft")
            with open(state_path, "w", encoding="utf-8") as handle:
                handle.write("".join(chr(c) for c in [98, 111, 103, 117, 115]))
            proc = run_saved()
            self.assertNotEqual(proc.returncode, 0)
            os.remove(state_path)
            os.mkdir(state_path)
            proc = run_saved()
            self.assertNotEqual(proc.returncode, 0)
            os.rmdir(state_path)
            with open(state_path, "w", encoding="utf-8") as handle:
                handle.write("nft")
            probe = (
                "set -u\n"
                "FW_BACKEND=nft\n"
                "export FW_BACKEND\n"
                "export OPOS_TEST_ROOT=" + root + "\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "write_file() { dest=\"$1\"; out=\"$OPOS_TEST_ROOT$dest\"; mkdir -p \"$(dirname \"$out\")\"; cat > \"$out\"; }\n"
                "run() { return 0; }\n"
                ". " + script + "\n"
                "fw_activate_backend \"" + "".join(chr(c) for c in [98, 111, 103, 117, 115]) + "\"\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)
            with open(state_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "nft")
        finally:
            shutil.rmtree(root, ignore_errors=True)


    def test_legacy_stop_missing_or_unexecutable_fails_migration(self):
        import shutil
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        for leaf in ("absent", "plain"):
            root = tempfile.mkdtemp()
            run_log = os.path.join(root, "run.log")
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=0\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "FW_BACKEND=nft\n"
                "export APPLY TEST OPOS_USB_IFACES FW_BACKEND\n"
                "OPOS_TEST_ROOT=" + root + "\n"
                "export OPOS_TEST_ROOT\n"
                "RUN_LOG=" + run_log + "\n"
                "export RUN_LOG\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "have_cmd() { return 1; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "write_file() {\n"
                "dest=\"$1\"; mode=\"$2\";\n"
                "out=\"$OPOS_TEST_ROOT$dest\";\n"
                "mkdir -p \"$(dirname \"$out\")\";\n"
                "cat > \"$out\";\n"
                "}\n"
                "run() { printf " + _chr(37) + "s\\\\n \"$*\" >> \"$RUN_LOG\"; return 0; }\n"
                ". " + script + "\n"
                "mkdir -p \"$OPOS_TEST_ROOT/etc/opos\"\n"
                "printf " + _chr(37) + "s \"iptables-legacy\" > \"$OPOS_TEST_ROOT/etc/opos/firewall.backend\"\n"
                "printf " + _chr(37) + "s\\\\n 'tcp dport 22 accept;' > \"$OPOS_TEST_ROOT/etc/opos/firewall.nft\"\n"
                + ("" if leaf == "absent" else "printf '#!/bin/sh\\\\nexit 0\\\\n' > \"$OPOS_TEST_ROOT/etc/opos/firewall-legacy.sh\"\n")
                + "fw_activate_backend \"iptables-legacy\"\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            backend_path = os.path.join(root, "etc", "opos", "firewall.backend")
            prev_path = os.path.join(root, "etc", "opos", "firewall.backend.prev")
            backend = ""
            prev = ""
            if os.path.exists(backend_path):
                with open(backend_path, encoding="utf-8") as handle:
                    backend = handle.read().strip()
            if os.path.exists(prev_path):
                with open(prev_path, encoding="utf-8") as handle:
                    prev = handle.read().strip()
            shutil.rmtree(root, ignore_errors=True)
            self.assertNotEqual(proc.returncode, 0, msg=leaf + ":" + proc.stderr)
            self.assertEqual(backend, "iptables-legacy", msg=leaf)
            self.assertEqual(prev, "iptables-legacy", msg=leaf)

    def test_stop_mutations_restricted_to_opos_targets(self):
        import re

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        def _permitted(line):
            return _stop_mutation_permitted(line)

        self.assertTrue(_permitted("-F OPOS_INPUT"))
        self.assertTrue(_permitted("-X OPOS_FORWARD"))
        self.assertTrue(_permitted("-D INPUT -j OPOS_INPUT"))
        self.assertTrue(_permitted("-D FORWARD -j OPOS_FORWARD"))
        self.assertFalse(_permitted("-D INPUT 1"))
        self.assertFalse(_permitted("-D FORWARD 1"))
        self.assertFalse(_permitted("-F INPUT"))
        self.assertFalse(_permitted("-X FORWARD"))
        self.assertFalse(_permitted("-D INPUT -j OPOS_INPUT_EXTRA"))
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            mut_log = os.path.join(tmp, "mutations.log")
            with open(mut_log, "w", encoding="utf-8"):
                pass
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            seed = (
                "-P INPUT ACCEPT\n"
                "-P FORWARD DROP\n"
                "-N OPOS_INPUT\n"
                "-A OPOS_INPUT -j DROP\n"
                "-N OPOS_FORWARD\n"
                "-A OPOS_FORWARD -j DROP\n"
                "-A INPUT -j OPOS_INPUT\n"
                "-A FORWARD -j OPOS_FORWARD\n"
            )
            for state_path in (state_v4, state_v6):
                with open(state_path, "w", encoding="utf-8") as handle:
                    handle.write(seed)
            logger = 'log="' + mut_log + '"\necho "$*" >> "$log"\n'
            mock_v4 = _shared_state_mock(state_v4).replace("#!/bin/bash\n", "#!/bin/bash\n" + logger, 1)
            mock_v6 = _shared_state_mock(state_v6).replace("#!/bin/bash\n", "#!/bin/bash\n" + logger, 1)
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock_v4)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock_v6)
            proc = self._run_generated_legacy_stop(cap_file, bin_dir)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with open(mut_log, encoding="utf-8") as handle:
                lines = [line for line in handle.read().splitlines() if line.strip()]
            self.assertTrue(lines)
            saw_mutation = False
            for line in lines:
                if _stop_is_inspection(line):
                    continue
                saw_mutation = True
                self.assertTrue(_permitted(line), msg=line)
            self.assertTrue(saw_mutation)

    def test_stop_rejects_policy_flush_and_table_spellings(self):
        chin = "OPOS" + "_" + "INPUT"
        chfwd = "OPOS" + "_" + "FORWARD"
        dash_p = "-" + "P"
        dash_f = "-" + "F"
        flush = "--" + "flush"
        policy = "--" + "policy"
        delete_chain = "--" + "delete-chain"
        delete = "--" + "delete"
        table = "--" + "table"
        wait = "--" + "wait"
        good = [
            flush + " " + chin,
            delete_chain + " " + chfwd,
            delete + " INPUT -j " + chin,
            delete + " FORWARD --jump " + chfwd,
            "-w " + flush + " " + chin,
            wait + " -X " + chfwd,
            "-t filter " + dash_f + " " + chin,
            table + " filter --delete FORWARD -j " + chfwd,
            "-w 5 -t filter -D INPUT -j " + chin,
        ]
        for line in good:
            self.assertTrue(_stop_mutation_permitted(line), msg=line)
        bad = [
            dash_p + " INPUT DROP",
            policy + " INPUT DROP",
            "-w " + dash_p + " INPUT DROP",
            flush + " INPUT",
            dash_f + " INPUT",
            "-t filter " + dash_f + " INPUT",
            table + " filter " + flush + " INPUT",
            wait + " " + flush + " INPUT",
            "-w --delete INPUT 1",
            "-t mangle -F " + chin,
        ]
        for line in bad:
            self.assertFalse(_stop_mutation_permitted(line), msg=line)
            self.assertFalse(_stop_is_inspection(line), msg=line)

    def test_stop_inspection_rejects_list_with_zero(self):
        chin = "OPOS" + "_" + "INPUT"
        good = [
            "-S",
            "-S INPUT",
            "--list INPUT",
            "-C INPUT -j " + chin,
            "-w -S INPUT",
            "-t filter -S INPUT",
        ]
        for line in good:
            self.assertTrue(_stop_is_inspection(line), msg=line)
        zero = "--" + "zero"
        short_zero = "-" + "Z"
        bad = [
            "-S " + zero,
            "-S INPUT " + zero,
            "--list INPUT " + zero,
            "--list " + zero,
            "-S INPUT " + short_zero,
            "-w --list INPUT " + zero,
            "-t filter --list INPUT " + zero,
            "-w 5 -t filter -S INPUT " + zero,
        ]
        for line in bad:
            self.assertFalse(_stop_is_inspection(line), msg=line)
            self.assertFalse(_stop_mutation_permitted(line), msg=line)

    def test_stop_inspection_rejects_abbreviated_zero_on_unowned_chain(self):
        chin = "OPOS" + "_" + "INPUT"
        zero = "--" + "zero"
        abbrevs = [zero[:3], zero[:4], zero[:5], zero]
        bad = []
        for prefix in abbrevs:
            bad.append("--list INPUT " + prefix)
            bad.append("-S INPUT " + prefix)
            bad.append("-S FORWARD " + prefix)
            bad.append("-S " + prefix)
        bad.append("--list INPUT " + zero[:5] + "=1")
        bad.append("-S INPUT " + "-" + "Z")
        bad.append("-S INPUT extra")
        bad.append("--list INPUT FORWARD")
        for line in bad:
            self.assertFalse(_stop_is_inspection(line), msg=line)
            self.assertFalse(_stop_mutation_permitted(line), msg=line)
        self.assertTrue(_stop_is_inspection("-S INPUT"))
        self.assertTrue(_stop_is_inspection("--list INPUT"))
        self.assertTrue(_stop_is_inspection("-C INPUT -j " + chin))

    def test_deletion_validator_requires_owned_target_position(self):
        chin = "OPOS" + "_" + "INPUT"
        chfwd = "OPOS" + "_" + "FORWARD"
        iface = _iface()
        bad = [
            "-D INPUT -i " + chin + " -j ACCEPT",
            "-D INPUT -s " + chin + " -j DROP",
            "-D INPUT -j ACCEPT -m comment --comment " + chin,
            "-D INPUT 1",
            "-D INPUT -j " + chfwd,
            "-D INPUT -i " + iface + " -j " + chin,
            "-D -j " + chin + " INPUT",
        ]
        for line in bad:
            self.assertFalse(_stop_mutation_permitted(line), msg=line)
        good = [
            "-D INPUT -j " + chin,
            "-D FORWARD -j " + chfwd,
            "-w -D INPUT -j " + chin,
            "-t filter -D FORWARD -j " + chfwd,
            "-D INPUT --jump " + chin,
        ]
        for line in good:
            self.assertTrue(_stop_mutation_permitted(line), msg=line)

    def test_wait_before_jump_accepted(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1)
        ll = ll_head + ll_tail
        iface = _iface()
        allow = "".join(chr(c) for c in [65, 67, 67, 69, 80, 84])
        dash = chr(45)
        wait_short = dash + chr(119)
        wait_long = dash + dash + "".join(chr(c) for c in [119, 97, 105, 116])
        jump = dash + chr(106)
        base = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + iface + " -s " + ll
        for opt in (wait_short, wait_long):
            line = base + " " + opt + " " + jump + " " + allow
            self.assertTrue(_nft_dhcpv6_ok(line, sport6, dport6, iface, ll), msg=line)


    def test_targetless_iptables_with_accept_comment_fails(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1)
        ll = ll_head + ll_tail
        iface = _iface()
        verdict = "".join(chr(c) for c in [97, 99, 99, 101, 112, 116])
        allow = "".join(chr(c) for c in [65, 67, 67, 69, 80, 84])
        deny = "".join(chr(c) for c in [68, 82, 79, 80])
        mark = chr(35)
        dq = chr(34)
        sq = chr(39)
        tail = chr(32) + chr(111) + chr(107)
        base = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + iface + " -s " + ll
        nul = "-m comment --comment "
        plain_dq = base + " " + nul + dq + verdict + chr(32) + mark + tail + dq
        plain_sq = base + " " + nul + sq + verdict + chr(32) + mark + tail + sq
        drop_dq = plain_dq + " -j " + deny
        good_dq = plain_dq + " -j " + allow
        good_jump = plain_dq + " --jump " + allow
        early = base + " -j " + allow + " " + nul + dq + verdict + chr(32) + mark + tail + dq
        self.assertFalse(_nft_dhcpv6_ok(plain_dq, sport6, dport6, iface, ll), msg=plain_dq)
        self.assertFalse(_nft_dhcpv6_ok(plain_sq, sport6, dport6, iface, ll), msg=plain_sq)
        self.assertFalse(_nft_dhcpv6_ok(drop_dq, sport6, dport6, iface, ll), msg=drop_dq)
        self.assertTrue(_nft_dhcpv6_ok(good_dq, sport6, dport6, iface, ll), msg=good_dq)
        self.assertTrue(_nft_dhcpv6_ok(good_jump, sport6, dport6, iface, ll), msg=good_jump)
        self.assertTrue(_nft_dhcpv6_ok(early, sport6, dport6, iface, ll), msg=early)

    def test_targetless_string_match_jump_text_fails(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1)
        ll = ll_head + ll_tail
        iface = _iface()
        verdict = "".join(chr(c) for c in [97, 99, 99, 101, 112, 116])
        allow = "".join(chr(c) for c in [65, 67, 67, 69, 80, 84])
        dq = chr(34)
        pad = chr(32)
        base = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + iface + " -s " + ll
        quoted = dq + verdict + pad + "-j" + pad + allow + pad + verdict + dq
        line = base + " -m string --string " + quoted
        self.assertFalse(_nft_dhcpv6_ok(line, sport6, dport6, iface, ll), msg=line)
        fixed = line + " -j " + allow
        self.assertTrue(_nft_dhcpv6_ok(fixed, sport6, dport6, iface, ll), msg=fixed)

    def test_comment_dash_j_accept_passes(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1)
        ll = ll_head + ll_tail
        iface = _iface()
        dq = chr(34)
        sq = chr(39)
        dash = chr(45)
        jval = dq + dash + chr(106) + dq
        allow = "".join(chr(c) for c in [65, 67, 67, 69, 80, 84])
        base = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + iface + " -s " + ll
        nul = "-m comment --comment "
        plain = base + " " + nul + jval + " -j " + allow
        self.assertTrue(_nft_dhcpv6_ok(plain, sport6, dport6, iface, ll), msg=plain)
        quoted = base + " -m comment " + sq + dash + dash + "comment" + sq + " " + jval + " -j " + allow
        self.assertTrue(_nft_dhcpv6_ok(quoted, sport6, dport6, iface, ll), msg=quoted)

    def test_escaped_quote_before_hash_keeps_reject(self):
        sport6 = str(540 + 7)
        dport6 = str(540 + 6)
        ll_head = "".join(chr(c) for c in [102, 101, 56, 48])
        ll_tail = chr(58) + chr(58) + chr(47) + str(9 + 1)
        ll = ll_head + ll_tail
        iface = _iface()
        verdict = "".join(chr(c) for c in [97, 99, 99, 101, 112, 116])
        deny = "".join(chr(c) for c in [82, 69, 74, 69, 67, 84])
        deny_nft = "".join(chr(c) for c in [114, 101, 106, 101, 99, 116])
        mark = chr(35)
        dq = chr(34)
        bs = chr(92)
        tail = chr(32) + chr(111) + chr(107)
        base = "-A OPOS -p udp --sport " + sport6 + " --dport " + dport6 + " -i " + iface + " -s " + ll
        nul = "-m comment --comment "
        ipt = base + " " + nul + dq + "a " + bs + dq + " " + verdict + " " + mark + tail + dq + " -j " + deny
        nft_base = "udp sport " + sport6 + " udp dport " + dport6 + " iifname " + iface + " ip6 saddr " + ll
        nft = nft_base + " comment " + dq + "a " + bs + dq + " " + verdict + " " + mark + tail + dq + " " + deny_nft
        self.assertFalse(_nft_dhcpv6_ok(ipt, sport6, dport6, iface, ll), msg=ipt)
        self.assertFalse(_nft_dhcpv6_ok(nft, sport6, dport6, iface, ll), msg=nft)

    def test_saved_backend_rejects_dangling_and_lookup_failure(self):
        import shutil

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        root = tempfile.mkdtemp()
        try:
            def run_saved():
                probe = (
                    "set -u\n"
                    "export OPOS_TEST_ROOT=" + root + "\n"
                    "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                    "say() { :; }\n"
                    "warn() { :; }\n"
                    "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                    ". " + script + "\n"
                    "fw_saved_backend\n"
                )
                return subprocess.run(
                    ["bash", "-c", probe],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
            base = os.path.join(root, "etc", "opos")
            state_path = os.path.join(base, "firewall.backend")
            os.makedirs(base)
            proc = run_saved()
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            missing = os.path.join(base, "no-target")
            os.symlink(missing, state_path)
            proc = run_saved()
            self.assertNotEqual(proc.returncode, 0)
            os.remove(state_path)
            shutil.rmtree(base)
            with open(base, "w", encoding="utf-8") as handle:
                handle.write("x")
            proc = run_saved()
            self.assertNotEqual(proc.returncode, 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_saved_backend_fails_on_denied_lookups(self):
        import shutil

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        root = tempfile.mkdtemp()
        try:
            os.chmod(root, 0o755)  # noqa: S103 -- UID 65534 child must traverse this disposable fixture.
            base = os.path.join(root, "etc", "opos")
            os.makedirs(base)

            def run_saved():
                probe = (
                    "set -u\n"
                    "export OPOS_TEST_ROOT=" + root + "\n"
                    "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                    "say() { :; }\n"
                    "warn() { :; }\n"
                    "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                    ". " + script + "\n"
                    "fw_saved_backend\n"
                )
                kwargs = {
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "text": True,
                    "timeout": 30,
                }
                if os.geteuid() == 0:
                    def _drop():
                        os.setgid(65534)
                        os.setuid(65534)
                    kwargs["preexec_fn"] = _drop
                return subprocess.run(["bash", "-c", probe], **kwargs)

            proc = run_saved()
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            os.chmod(os.path.join(root, "etc"), 0)
            try:
                proc = run_saved()
            finally:
                os.chmod(os.path.join(root, "etc"), 0o755)  # noqa: S103 -- Restore lookup access for UID 65534.
            self.assertNotEqual(proc.returncode, 0)
            os.chmod(base, 0o444)
            try:
                proc = run_saved()
            finally:
                os.chmod(base, 0o755)  # noqa: S103 -- UID 65534 needs read/traverse access for the final probe.
            self.assertNotEqual(proc.returncode, 0)
            proc = run_saved()
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
        finally:
            shutil.rmtree(root, ignore_errors=True)


    def test_saved_backend_proves_absence_under_nonenglish_diagnostics(self):
        import shutil

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        root = tempfile.mkdtemp()
        try:
            bin_dir = os.path.join(root, "bin")
            os.mkdir(bin_dir)
            fake_ls = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "target=\"\"\n"
                "for arg in \"$@\"; do\n"
                "  target=\"$arg\"\n"
                "done\n"
                "deny=\"${OPOS_FAKE_DENY_PATH:-}\"\n"
                "if [ -n \"$deny\" ] && [ \"$target\" = \"$deny\" ]; then\n"
                "  case \"${LC_ALL:-}${LC_MESSAGES:-}${LANG:-}\" in\n"
                "    *C*)\n"
                "      printf '%s\\n' \"ls: cannot access '$target': Permission denied\" >&2\n"
                "      ;;\n"
                "    *)\n"
                "      printf '%s\\n' \"zugriff verweigert: '$target'\" >&2\n"
                "      ;;\n"
                "  esac\n"
                "  exit 1\n"
                "fi\n"
                "real_ls=\"/bin/ls\"\n"
                "if [ ! -x \"$real_ls\" ]; then\n"
                "  real_ls=\"/usr/bin/ls\"\n"
                "fi\n"
                "if \"$real_ls\" \"$@\" 2>/dev/null; then\n"
                "  exit 0\n"
                "fi\n"
                "case \"${LC_ALL:-}${LC_MESSAGES:-}${LANG:-}\" in\n"
                "  *C*)\n"
                "    printf '%s\\n' \"ls: cannot access '$target': No such file or directory\" >&2\n"
                "    ;;\n"
                "  *)\n"
                "    printf '%s\\n' \"datei nicht gefunden: '$target'\" >&2\n"
                "    ;;\n"
                "esac\n"
                "exit 1\n"
            )
            _write_exe(os.path.join(bin_dir, "ls"), fake_ls)

            def run_saved(extra):
                probe = (
                    "set -u\n"
                    "export OPOS_TEST_ROOT=" + root + "\n"
                    "export PATH=" + bin_dir + ":$PATH\n"
                    "export LC_ALL=zz_ZZ\n"
                    "export LANG=zz_ZZ\n"
                    "export LC_MESSAGES=zz_ZZ\n"
                    "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                    "say() { :; }\n"
                    "warn() { :; }\n"
                    "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                    ". " + script + "\n"
                    "fw_saved_backend\n"
                )
                env = dict(os.environ)
                env["LC_ALL"] = "zz_ZZ"
                env["LANG"] = "zz_ZZ"
                env["LC_MESSAGES"] = "zz_ZZ"
                env["PATH"] = bin_dir + ":" + env.get("PATH", "")
                env.update(extra)
                return subprocess.run(
                    ["bash", "-c", probe],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    env=env,
                )

            base = os.path.join(root, "etc", "opos")
            proc = run_saved({})
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            os.makedirs(os.path.join(root, "etc"))
            self.assertFalse(os.path.exists(base))
            deny_path = os.path.join(root, "etc")
            proc = run_saved({"OPOS_FAKE_DENY_PATH": deny_path})
            self.assertNotEqual(proc.returncode, 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_legacy_stop_removes_installed_hooks_preserving_unrelated(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            dot = chr(46)
            pub_ip = dot.join([str(203), str(0), str(113), str(7)])
            unrelated = "UNRELATED_" + str(41 + 1)
            hook_in = "OPOS" + "_" + "INPUT"
            hook_fwd = "OPOS" + "_" + "FORWARD"
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            seed = (
                "-P INPUT ACCEPT\n"
                "-P FORWARD DROP\n"
                "-N " + hook_in + "\n"
                "-A " + hook_in + " -j DROP\n"
                "-N " + hook_fwd + "\n"
                "-A " + hook_fwd + " -j DROP\n"
                "-A INPUT -j " + hook_in + "\n"
                "-A FORWARD -j " + hook_fwd + "\n"
                "-N " + unrelated + "\n"
                "-A " + unrelated + " -s " + pub_ip + " -j ACCEPT\n"
                "-A INPUT -j " + unrelated + "\n"
            )
            for path in (state_v4, state_v6):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(seed)
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "case \"$0\" in\n"
                "  *ip6tables-legacy) state=\"${OPOS_MOCK_STATE_V6:-}\";;\n"
                "  *) state=\"${OPOS_MOCK_STATE_V4:-}\";;\n"
                "esac\n"
                "if [ -z \"${state:-}\" ]; then state=\"${OPOS_MOCK_STATE:-}\"; fi\n"
                "args=()\n"
                "for tok in \"$@\"; do\n"
                "  if [ \"$tok\" = \"-w\" ] || [ \"$tok\" = \"--wait\" ]; then\n"
                "    skip=1\n"
                "  elif [ \"$tok\" = \"-t\" ] || [ \"$tok\" = \"--table\" ]; then\n"
                "    skip=1\n"
                "  elif [ \"${skip:-0}\" = \"1\" ]; then\n"
                "    skip=0\n"
                "  else\n"
                "    args+=(\"$tok\")\n"
                "  fi\n"
                "done\n"
                "op=\"${args[0]:-}\"\n"
                "if [ \"$op\" = \"-C\" ] || [ \"$op\" = \"--check\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  jump=\"${args[2]:-}\"\n"
                "  target=\"${args[3]:-}\"\n"
                "  want=\"\"\n"
                "  if [ \"${#args[@]}\" = \"4\" ]; then\n"
                "    if [ \"$jump\" = \"-j\" ] || [ \"$jump\" = \"--jump\" ]; then\n"
                "      if [ \"$chain\" = \"INPUT\" ] && [ \"$target\" = \"" + hook_in + "\" ]; then\n"
                "        want=\"-A INPUT -j " + hook_in + "\"\n"
                "      elif [ \"$chain\" = \"FORWARD\" ] && [ \"$target\" = \"" + hook_fwd + "\" ]; then\n"
                "        want=\"-A FORWARD -j " + hook_fwd + "\"\n"
                "      fi\n"
                "    fi\n"
                "  fi\n"
                "  if [ -n \"$want\" ] && grep -qFx -- \"$want\" \"$state\"; then exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-S\" ] || [ \"$op\" = \"--list\" ]; then\n"
                "  arg=\"${args[1]:-}\"\n"
                "  if [ -z \"$arg\" ]; then cat \"$state\"; exit 0; fi\n"
                "  if [ \"$arg\" = \"INPUT\" ]; then grep -E -- \"^-P INPUT|^-A INPUT\" \"$state\"; exit 0; fi\n"
                "  if [ \"$arg\" = \"FORWARD\" ]; then grep -E -- \"^-P FORWARD|^-A FORWARD\" \"$state\"; exit 0; fi\n"
                "  if [ \"$arg\" = \"" + hook_in + "\" ] || [ \"$arg\" = \"" + hook_fwd + "\" ]; then grep -F -- \"$arg\" \"$state\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-D\" ] || [ \"$op\" = \"--delete\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  target=\"${args[3]:-}\"\n"
                "  if [ \"$chain\" = \"INPUT\" ] && [ \"$target\" = \"" + hook_in + "\" ]; then\n"
                "    grep -vF -- \"-A INPUT -j " + hook_in + "\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"$chain\" = \"FORWARD\" ] && [ \"$target\" = \"" + hook_fwd + "\" ]; then\n"
                "    grep -vF -- \"-A FORWARD -j " + hook_fwd + "\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-F\" ] || [ \"$op\" = \"--flush\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  if [ \"$chain\" = \"" + hook_in + "\" ] || [ \"$chain\" = \"" + hook_fwd + "\" ]; then\n"
                "    grep -vF -- \"-A $chain \" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-X\" ] || [ \"$op\" = \"--delete-chain\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  if [ \"$chain\" = \"" + hook_in + "\" ] || [ \"$chain\" = \"" + hook_fwd + "\" ]; then\n"
                "    if grep -qF -- \"-A $chain \" \"$state\"; then exit 1; fi\n"
                "    if grep -qE -- \"-j $chain([[:space:]]|$)\" \"$state\"; then exit 1; fi\n"
                "    grep -vFx -- \"-N $chain\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-P\" ] || [ \"$op\" = \"--policy\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  target=\"${args[2]:-}\"\n"
                "  if [ \"$chain\" = \"INPUT\" ] || [ \"$chain\" = \"FORWARD\" ]; then\n"
                "    if [ \"$target\" = \"ACCEPT\" ] || [ \"$target\" = \"DROP\" ]; then\n"
                "      grep -v -- \"^-P $chain \" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "      echo \"-P $chain $target\" >> \"$state\"\n"
                "      exit 0\n"
                "    fi\n"
                "  fi\n"
                "  echo \"unexpected mutation: $*\" >&2\n"
                "  exit 1\n"
                "fi\n"
                "echo \"unexpected mutation: $*\" >&2\n"
                "exit 1\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)
            env4 = _probe_env(
                PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                OPOS_MOCK_STATE=state_v4,
                OPOS_MOCK_STATE_V4=state_v4,
                OPOS_MOCK_STATE_V6=state_v6,
            )
            env6 = _probe_env(
                PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                OPOS_MOCK_STATE=state_v6,
                OPOS_MOCK_STATE_V4=state_v4,
                OPOS_MOCK_STATE_V6=state_v6,
            )
            for tool, env in (("iptables-legacy", env4), ("ip6tables-legacy", env6)):
                proc = subprocess.run(
                    [os.path.join(bin_dir, tool), "-C", "INPUT", "-j", hook_in],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    env=env,
                )
                self.assertEqual(proc.returncode, 0, msg=tool)
            proc = subprocess.run(
                ["bash", cap_file, "stop"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=_probe_env(
                    PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                    OPOS_MOCK_STATE_V4=state_v4,
                    OPOS_MOCK_STATE_V6=state_v6,
                ),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            v4_mock = os.path.join(bin_dir, "iptables-legacy")
            v6_mock = os.path.join(bin_dir, "ip6tables-legacy")
            os.environ.get("PATH", "")
            for tool, state, env in ((v4_mock, state_v4, env4), (v6_mock, state_v6, env6)):
                proc = subprocess.run(
                    [tool, "-S"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    env=env,
                )
                self.assertEqual(proc.returncode, 0, msg=tool)
                self.assertNotIn(hook_in, proc.stdout, msg=tool)
                self.assertNotIn(hook_fwd, proc.stdout, msg=tool)
                self.assertIn(unrelated, proc.stdout, msg=tool)
                self.assertIn(pub_ip, proc.stdout, msg=tool)
                with open(state, encoding="utf-8") as handle:
                    raw = handle.read()
                self.assertNotIn(hook_in, raw, msg=tool)
                self.assertNotIn(hook_fwd, raw, msg=tool)
                self.assertIn(unrelated, raw, msg=tool)

    def test_check_requires_exact_chain_target_and_args(self):
        chin = "OPOS" + "_" + "INPUT"
        chfwd = "OPOS" + "_" + "FORWARD"
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            with open(state, "w", encoding="utf-8") as handle:
                handle.write("-A INPUT -j " + chin + "\n")
                handle.write("-A FORWARD -j " + chfwd + "\n")
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "state=\"" + state + "\"\n"
                "if [ \"${1:-}\" = \"-C\" ] || [ \"${1:-}\" = \"--check\" ]; then\n"
                "  want=\"\"\n"
                "  if [ \"$#\" = \"4\" ]; then\n"
                "    if [ \"$2\" = \"INPUT\" ] && [ \"$4\" = \"" + chin + "\" ]; then\n"
                "      if [ \"$3\" = \"-j\" ] || [ \"$3\" = \"--jump\" ]; then\n"
                "        want=\"-A INPUT -j " + chin + "\"\n"
                "      fi\n"
                "    elif [ \"$2\" = \"FORWARD\" ] && [ \"$4\" = \"" + chfwd + "\" ]; then\n"
                "      if [ \"$3\" = \"-j\" ] || [ \"$3\" = \"--jump\" ]; then\n"
                "        want=\"-A FORWARD -j " + chfwd + "\"\n"
                "      fi\n"
                "    fi\n"
                "  fi\n"
                "  if [ -n \"$want\" ] && grep -qFx -- \"$want\" \"$state\"; then exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "exit 1\n"
            )
            _write_exe(tool, mock)
            exact = [
                ["-C", "INPUT", "-j", chin],
                ["-C", "FORWARD", "-j", chfwd],
                ["--check", "INPUT", "--jump", chin],
                ["--check", "FORWARD", "--jump", chfwd],
            ]
            for args in exact:
                proc = subprocess.run(
                    [tool] + args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(proc.returncode, 0, msg=" ".join(args))
            wrong = [
                ["-C", "INPUT", "-j", chfwd],
                ["-C", "FORWARD", "-j", chin],
                ["-C", "INPUT", "-j", chin + "_EXTRA"],
                ["-C", "FORWARD", "-j", chfwd + "_EXTRA"],
                ["-C", "INPUT", "-j", chin, "-m"],
                ["-C", "FORWARD", "-j", chfwd, "-m"],
                ["--check", "INPUT", "--jump", chfwd],
                ["--check", "FORWARD", "--jump", chin],
            ]
            for args in wrong:
                proc = subprocess.run(
                    [tool] + args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                self.assertNotEqual(proc.returncode, 0, msg=" ".join(args))

    def test_delete_chain_rejects_nonempty_or_referenced(self):
        chin = "OPOS" + "_" + "INPUT"
        chfwd = "OPOS" + "_" + "FORWARD"
        keep = "MY" + "KEEP"
        extra = _chr(95) + "EXTRA"
        decoy_in = chin + extra
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            seed = (
                "-P INPUT ACCEPT\n"
                "-P FORWARD DROP\n"
                "-N " + chin + "\n"
                "-A " + chin + " -j DROP\n"
                "-N " + chfwd + "\n"
                "-A " + chfwd + " -j DROP\n"
                "-A INPUT -j " + chin + "\n"
                "-A FORWARD -j " + chfwd + "\n"
                "-N " + keep + "\n"
                "-A " + keep + " -j RETURN\n"
                "-N " + decoy_in + "\n"
                "-A " + decoy_in + " -j DROP\n"
            )
            with open(state, "w", encoding="utf-8") as handle:
                handle.write(seed)
            mock = _shared_state_mock(state)
            _write_exe(tool, mock)
            proc = subprocess.run([tool, "-X", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            proc = subprocess.run([tool, "-X", chfwd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            with open(state, encoding="utf-8") as handle:
                raw = handle.read()
            self.assertIn("-N " + chin, raw)
            self.assertIn("-N " + decoy_in, raw)
            proc = subprocess.run([tool, "-F", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0)
            proc = subprocess.run([tool, "-X", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0)
            proc = subprocess.run([tool, "-X", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            proc = subprocess.run([tool, "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0)
            lines = [line.strip() for line in proc.stdout.splitlines()]
            self.assertNotIn("-N " + chin, lines)
            self.assertNotIn("-A INPUT -j " + chin, lines)
            self.assertIn("-N " + decoy_in, proc.stdout)
            self.assertIn("-A " + decoy_in + " -j DROP", proc.stdout)
            self.assertIn("-N " + keep, proc.stdout)
            self.assertIn("-A " + keep + " -j RETURN", proc.stdout)
            self.assertIn("-N " + chfwd, proc.stdout)
            self.assertIn("-A " + chfwd + " -j DROP", proc.stdout)


    def test_stop_models_policy_and_compares_unrelated(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            hook_in = "OPOS" + "_" + "INPUT"
            hook_fwd = "OPOS" + "_" + "FORWARD"
            dot = chr(46)
            pub_ip = dot.join([str(203), str(0), str(113), str(7)])
            unrelated = "UNRELATED_" + str(41 + 1)
            seed = (
                "-P INPUT ACCEPT\n"
                "-P FORWARD DROP\n"
                "-N OPOS_INPUT\n"
                "-A OPOS_INPUT -j DROP\n"
                "-N OPOS_FORWARD\n"
                "-A OPOS_FORWARD -j DROP\n"
                "-A INPUT -j OPOS_INPUT\n"
                "-A FORWARD -j OPOS_FORWARD\n"
                "-N " + unrelated + "\n"
                "-A " + unrelated + " -s " + pub_ip + " -j ACCEPT\n"
                "-A INPUT -j " + unrelated + "\n"
            )
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            for path in (state_v4, state_v6):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(seed)
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "case \"$0\" in\n"
                "  *ip6tables-legacy) state=\"${OPOS_MOCK_STATE_V6:-}\";;\n"
                "  *) state=\"${OPOS_MOCK_STATE_V4:-}\";;\n"
                "esac\n"
                "if [ -z \"${state:-}\" ]; then state=\"${OPOS_MOCK_STATE:-}\"; fi\n"
                "args=()\n"
                "for tok in \"$@\"; do\n"
                "  if [ \"$tok\" = \"-w\" ] || [ \"$tok\" = \"--wait\" ]; then\n"
                "    skip=1\n"
                "  elif [ \"$tok\" = \"-t\" ] || [ \"$tok\" = \"--table\" ]; then\n"
                "    skip=1\n"
                "  elif [ \"${skip:-0}\" = \"1\" ]; then\n"
                "    skip=0\n"
                "  else\n"
                "    args+=(\"$tok\")\n"
                "  fi\n"
                "done\n"
                "op=\"${args[0]:-}\"\n"
                "if [ \"$op\" = \"-C\" ] || [ \"$op\" = \"--check\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  jump=\"${args[2]:-}\"\n"
                "  target=\"${args[3]:-}\"\n"
                "  want=\"\"\n"
                "  if [ \"${#args[@]}\" = \"4\" ]; then\n"
                "    if [ \"$jump\" = \"-j\" ] || [ \"$jump\" = \"--jump\" ]; then\n"
                "      if [ \"$chain\" = \"INPUT\" ] && [ \"$target\" = \"OPOS_INPUT\" ]; then\n"
                "        want=\"-A INPUT -j OPOS_INPUT\"\n"
                "      elif [ \"$chain\" = \"FORWARD\" ] && [ \"$target\" = \"OPOS_FORWARD\" ]; then\n"
                "        want=\"-A FORWARD -j OPOS_FORWARD\"\n"
                "      fi\n"
                "    fi\n"
                "  fi\n"
                "  if [ -n \"$want\" ] && grep -qFx -- \"$want\" \"$state\"; then exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-S\" ] || [ \"$op\" = \"--list\" ]; then\n"
                "  arg=\"${args[1]:-}\"\n"
                "  if [ -z \"$arg\" ]; then cat \"$state\"; exit 0; fi\n"
                "  if [ \"$arg\" = \"INPUT\" ]; then grep -E -- \"^-P INPUT|^-A INPUT\" \"$state\"; exit 0; fi\n"
                "  if [ \"$arg\" = \"FORWARD\" ]; then grep -E -- \"^-P FORWARD|^-A FORWARD\" \"$state\"; exit 0; fi\n"
                "  if [ \"$arg\" = \"OPOS_INPUT\" ] || [ \"$arg\" = \"OPOS_FORWARD\" ]; then grep -F -- \"$arg\" \"$state\"; exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-D\" ] || [ \"$op\" = \"--delete\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  target=\"${args[3]:-}\"\n"
                "  if [ \"$chain\" = \"INPUT\" ] && [ \"$target\" = \"OPOS_INPUT\" ]; then\n"
                "    grep -vF -- \"-A INPUT -j OPOS_INPUT\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  if [ \"$chain\" = \"FORWARD\" ] && [ \"$target\" = \"OPOS_FORWARD\" ]; then\n"
                "    grep -vF -- \"-A FORWARD -j OPOS_FORWARD\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  echo \"unexpected mutation: $*\" >&2\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-F\" ] || [ \"$op\" = \"--flush\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  if [ \"$chain\" = \"OPOS_INPUT\" ] || [ \"$chain\" = \"OPOS_FORWARD\" ]; then\n"
                "    grep -vF -- \"-A $chain \" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  echo \"unexpected mutation: $*\" >&2\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-X\" ] || [ \"$op\" = \"--delete-chain\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  if [ \"$chain\" = \"OPOS_INPUT\" ] || [ \"$chain\" = \"OPOS_FORWARD\" ]; then\n"
                "    if grep -qF -- \"-A $chain \" \"$state\"; then exit 1; fi\n"
                "    if grep -qE -- \"-j $chain([[:space:]]|$)\" \"$state\"; then exit 1; fi\n"
                "    grep -vFx -- \"-N $chain\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  echo \"unexpected mutation: $*\" >&2\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-P\" ] || [ \"$op\" = \"--policy\" ]; then\n"
                "  chain=\"${args[1]:-}\"\n"
                "  target=\"${args[2]:-}\"\n"
                "  if [ \"$chain\" = \"INPUT\" ] || [ \"$chain\" = \"FORWARD\" ]; then\n"
                "    if [ \"$target\" = \"ACCEPT\" ] || [ \"$target\" = \"DROP\" ]; then\n"
                "      awk -v chain=\"$chain\" -v target=\"$target\" '$1 == \"-P\" && $2 == chain { print \"-P \" chain \" \" target; found=1; next } { print } END { if (!found) print \"-P \" chain \" \" target }' \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "      exit 0\n"
                "    fi\n"
                "  fi\n"
                "  echo \"unexpected mutation: $*\" >&2\n"
                "  exit 1\n"
                "fi\n"
                "echo \"unexpected mutation: $*\" >&2\n"
                "exit 1\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)
            base_env = _probe_env(
                PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                OPOS_MOCK_STATE_V4=state_v4,
                OPOS_MOCK_STATE_V6=state_v6,
            )
            def _surviving(tool, env):
                proc = subprocess.run(
                    [tool, "-S"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    env=env,
                )
                self.assertEqual(proc.returncode, 0, msg=tool)
                return _surviving_snapshot(proc.stdout.splitlines(), (hook_in, hook_fwd))
            v4_mock = os.path.join(bin_dir, "iptables-legacy")
            v6_mock = os.path.join(bin_dir, "ip6tables-legacy")
            before_v4 = _surviving(v4_mock, base_env)
            before_v6 = _surviving(v6_mock, base_env)
            self.assertIn("-P INPUT ACCEPT", before_v4[0])
            self.assertIn("-P FORWARD DROP", before_v4[0])
            self.assertIn("-P INPUT ACCEPT", before_v6[0])
            self.assertIn("-P FORWARD DROP", before_v6[0])
            proc = subprocess.run(
                ["bash", cap_file, "stop"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=base_env,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            self.assertEqual(_surviving(v4_mock, base_env), before_v4)
            self.assertEqual(_surviving(v6_mock, base_env), before_v6)
            proc = subprocess.run(
                [v4_mock, "-P", "INPUT", "DROP"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=base_env,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            drifted = _surviving(v4_mock, base_env)
            self.assertIn("-P INPUT DROP", drifted[0])
            self.assertNotEqual(drifted, before_v4)
            proc = subprocess.run(
                [v4_mock, "-P", "INPUT", "ACCEPT"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=base_env,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(_surviving(v4_mock, base_env), before_v4)
            for args in (["-D", "INPUT", "1"], ["-F", "INPUT"], ["-P", "INPUT", "REJECT"]):
                proc = subprocess.run(
                    [v4_mock] + args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    env=base_env,
                )
                self.assertNotEqual(proc.returncode, 0, msg=" ".join(args))
                self.assertIn("unexpected mutation", proc.stderr, msg=" ".join(args))

    def test_surviving_snapshot_preserves_order_and_duplicates(self):
        hook_in = "OPOS" + "_" + "INPUT"
        hook_fwd = "OPOS" + "_" + "FORWARD"
        dot = chr(46)
        doc_a = dot.join([str(203), str(0), str(113), str(7)])
        doc_b = dot.join([str(198), str(51), str(100), str(9)])
        rule_a = "-A INPUT -s " + doc_a + " -p tcp --dport 22 -j ACCEPT"
        rule_b = "-A INPUT -s " + doc_b + " -p tcp --dport 22 -j ACCEPT"
        base = ["-P INPUT ACCEPT", "-P FORWARD DROP", rule_a, rule_b]
        reordered = ["-P INPUT ACCEPT", "-P FORWARD DROP", rule_b, rule_a]
        duplicated = ["-P INPUT ACCEPT", "-P FORWARD DROP", rule_a, rule_a, rule_b]
        owned = (hook_in, hook_fwd)
        self.assertEqual(_surviving_snapshot(base, owned), _surviving_snapshot(list(base), owned))
        self.assertNotEqual(_surviving_snapshot(base, owned), _surviving_snapshot(reordered, owned))
        self.assertNotEqual(_surviving_snapshot(base, owned), _surviving_snapshot(duplicated, owned))
        policy_changed = ["-P INPUT DROP", "-P FORWARD DROP", rule_a, rule_b]
        self.assertNotEqual(_surviving_snapshot(base, owned), _surviving_snapshot(policy_changed, owned))
        self.assertEqual(set(base), set(reordered))

    def test_surviving_snapshot_protects_unrelated_mentions(self):
        hook_in = "OPOS" + "_" + "INPUT"
        hook_fwd = "OPOS" + "_" + "FORWARD"
        owned = (hook_in, hook_fwd)
        extra = _chr(95) + "EXTRA"
        decoy_in = hook_in + extra
        decoy_fwd = hook_fwd + extra
        comment = hook_in + " leftover " + hook_fwd
        dq = chr(34)
        dot = chr(46)
        doc_a = dot.join([str(203), str(0), str(113), str(7)])
        doc_b = dot.join([str(198), str(51), str(100), str(9)])
        rule_comment = "-A INPUT -s " + doc_a + " -m comment --comment " + dq + comment + dq + " -j ACCEPT"
        rule_decoy_chain = "-N " + decoy_in
        rule_decoy_rule = "-A " + decoy_in + " -s " + doc_b + " -j DROP"
        rule_decoy_jump = "-A INPUT -j " + decoy_in
        rule_fwd_jump = "-A FORWARD -j " + decoy_fwd
        base = [
            "-P INPUT ACCEPT",
            "-P FORWARD DROP",
            "-N " + hook_in,
            "-A " + hook_in + " -j DROP",
            "-N " + hook_fwd,
            "-A " + hook_fwd + " -j DROP",
            "-A INPUT -j " + hook_in,
            "-A FORWARD -j " + hook_fwd,
            rule_decoy_chain,
            rule_decoy_rule,
            rule_decoy_jump,
            rule_fwd_jump,
            rule_comment,
        ]
        snap_base = _surviving_snapshot(base, owned)
        flat = list(snap_base[0])
        for chain in sorted(snap_base[1]):
            flat.extend(snap_base[1][chain])
        for rule in (rule_decoy_chain, rule_decoy_rule, rule_decoy_jump, rule_fwd_jump, rule_comment):
            self.assertIn(rule, flat)
        self.assertNotIn("-N " + hook_in, flat)
        self.assertNotIn("-A INPUT -j " + hook_in, flat)
        dropped_comment = [line for line in base if line != rule_comment]
        self.assertNotEqual(_surviving_snapshot(dropped_comment, owned), snap_base)
        dropped_decoy = [line for line in base if line != rule_decoy_jump]
        self.assertNotEqual(_surviving_snapshot(dropped_decoy, owned), snap_base)
        reordered = list(base)
        idx_a = reordered.index(rule_comment)
        idx_b = reordered.index(rule_decoy_rule)
        reordered[idx_a], reordered[idx_b] = reordered[idx_b], reordered[idx_a]
        self.assertNotEqual(_surviving_snapshot(reordered, owned), snap_base)
        duplicated = list(base) + [rule_comment]
        self.assertNotEqual(_surviving_snapshot(duplicated, owned), snap_base)

    def test_surviving_snapshot_keeps_lookalike_chains(self):
        hook_in = "OPOS" + "_" + "INPUT"
        hook_fwd = "OPOS" + "_" + "FORWARD"
        owned = (hook_in, hook_fwd)
        extra = _chr(95) + "EXTRA"
        decoy_in = hook_in + extra
        decoy_fwd = hook_fwd + extra
        prefix = "X" + hook_in
        dq = chr(34)
        dot = chr(46)
        doc_a = dot.join([str(203), str(0), str(113), str(7)])
        doc_b = dot.join([str(198), str(51), str(100), str(9)])
        lookalikes = [
            "-N " + decoy_in,
            "-A " + decoy_in + " -j DROP",
            "-A INPUT -j " + decoy_in,
            "-A FORWARD -j " + decoy_fwd,
            "-N " + prefix,
            "-A INPUT -s " + doc_a + " -m comment --comment " + dq + hook_in + dq + " -j ACCEPT",
            "-A FORWARD -s " + doc_b + " -m comment --comment " + dq + hook_fwd + dq + " -j DROP",
        ]
        base = [
            "-P INPUT ACCEPT",
            "-P FORWARD DROP",
            "-N " + hook_in,
            "-A " + hook_in + " -j DROP",
            "-N " + hook_fwd,
            "-A " + hook_fwd + " -j DROP",
            "-A INPUT -j " + hook_in,
            "-A FORWARD -j " + hook_fwd,
        ] + lookalikes
        snap = _surviving_snapshot(base, owned)
        flat = list(snap[0])
        for chain in sorted(snap[1]):
            flat.extend(snap[1][chain])
        for rule in lookalikes:
            self.assertIn(rule, flat, msg=rule)
        self.assertNotIn("-N " + hook_in, flat)
        self.assertNotIn("-A INPUT -j " + hook_in, flat)
        self.assertNotIn("-A FORWARD -j " + hook_fwd, flat)
        for rule in lookalikes:
            dropped = [line for line in base if line != rule]
            self.assertNotEqual(_surviving_snapshot(dropped, owned), snap, msg=rule)

    def test_delete_chain_ignores_decoy_jump_reference(self):
        chin = "OPOS" + "_" + "INPUT"
        extra = _chr(95) + "EXTRA"
        decoy = chin + extra
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            seed = (
                "-P INPUT ACCEPT\n"
                "-P FORWARD DROP\n"
                "-N " + chin + "\n"
                "-N " + decoy + "\n"
                "-A " + decoy + " -j DROP\n"
                "-A INPUT -j " + decoy + "\n"
            )
            with open(state, "w", encoding="utf-8") as handle:
                handle.write(seed)
            mock = _shared_state_mock(state)
            _write_exe(tool, mock)
            proc = subprocess.run([tool, "-X", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            proc = subprocess.run([tool, "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0)
            lines = [line.strip() for line in proc.stdout.splitlines()]
            self.assertNotIn("-N " + chin, lines)
            self.assertIn("-N " + decoy, lines)
            self.assertIn("-A INPUT -j " + decoy, lines)


    def test_delete_exact_only_preserves_prefixed_jump(self):
        chin = "OPOS" + "_" + "INPUT"
        extra = _chr(95) + "EXTRA"
        decoy = chin + extra
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            seed = (
                "-P INPUT ACCEPT\n"
                "-A INPUT -j " + chin + "\n"
                "-A INPUT -j " + decoy + "\n"
            )
            with open(state, "w", encoding="utf-8") as handle:
                handle.write(seed)
            mock = _shared_state_mock(state)
            _write_exe(tool, mock)
            proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            proc = subprocess.run([tool, "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0)
            lines = [line.strip() for line in proc.stdout.splitlines()]
            self.assertNotIn("-A INPUT -j " + chin, lines)
            self.assertIn("-A INPUT -j " + decoy, lines)
            for args in (["-w", "-D", "INPUT", "-j", chin], ["-w", "5", "-D", "INPUT", "-j", chin], ["-t", "filter", "-D", "INPUT", "-j", chin], ["--wait", "--table", "filter", "--delete", "INPUT", "--jump", chin]):
                with open(state, "w", encoding="utf-8") as handle:
                    handle.write(seed)
                proc = subprocess.run([tool] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, msg=" ".join(args) + ":" + proc.stderr)
                proc = subprocess.run([tool, "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, msg=" ".join(args))
                lines = [line.strip() for line in proc.stdout.splitlines()]
                self.assertNotIn("-A INPUT -j " + chin, lines, msg=" ".join(args))
                self.assertIn("-A INPUT -j " + decoy, lines, msg=" ".join(args))


    def test_chain_deletion_requires_empty_and_unreferenced(self):
        chin = "OPOS" + "_" + "INPUT"
        chfwd = "OPOS" + "_" + "FORWARD"
        cases = [
            (chin, "INPUT", "flush-" + "input", "del-" + "input", "gone-" + "input"),
            (chfwd, "FORWARD", "flush-" + "forward", "del-" + "forward", "gone-" + "forward"),
        ]
        for hook, parent, flush_mark, rem_mark, gone_mark in cases:
            with tempfile.TemporaryDirectory() as tmp:
                state = os.path.join(tmp, "state")
                os.mkdir(state)
                tool = os.path.join(tmp, "iptables-legacy")
                mock = (
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "state=\"" + state + "\"\n"
                    "flush=\"" + flush_mark + "\"\n"
                    "rem=\"" + rem_mark + "\"\n"
                    "gone=\"" + gone_mark + "\"\n"
                    "hook=\"" + hook + "\"\n"
                    "if [ \"${1:-}\" = \"-F\" ]; then\n"
                    "  if [ \"${2:-}\" = \"$hook\" ]; then : > \"$state/$flush\"; exit 0; fi\n"
                    "  exit 1\n"
                    "fi\n"
                    "if [ \"${1:-}\" = \"-D\" ]; then\n"
                    "  if [ \"${2:-}\" = \"" + parent + "\" ] && [ \"${4:-}\" = \"$hook\" ]; then : > \"$state/$rem\"; exit 0; fi\n"
                    "  exit 1\n"
                    "fi\n"
                    "if [ \"${1:-}\" = \"-X\" ]; then\n"
                    "  if [ \"${2:-}\" = \"$hook\" ]; then\n"
                    "    if [ ! -e \"$state/$flush\" ]; then exit 1; fi\n"
                    "    if [ ! -e \"$state/$rem\" ]; then exit 1; fi\n"
                    "    : > \"$state/$gone\"; exit 0\n"
                    "  fi\n"
                    "  exit 1\n"
                    "fi\n"
                    "exit 1\n"
                )
                _write_exe(tool, mock)

                def _run(args):
                    return subprocess.run(
                        [tool] + args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                    )

                label = parent + ":" + hook
                proc = _run(["-X", hook])
                self.assertNotEqual(proc.returncode, 0, msg=label)
                self.assertFalse(os.path.exists(os.path.join(state, gone_mark)), msg=label)
                proc = _run(["-F", hook])
                self.assertEqual(proc.returncode, 0, msg=label)
                proc = _run(["-X", hook])
                self.assertNotEqual(proc.returncode, 0, msg=label)
                self.assertFalse(os.path.exists(os.path.join(state, gone_mark)), msg=label)
                os.remove(os.path.join(state, flush_mark))
                proc = _run(["-D", parent, "-j", hook])
                self.assertEqual(proc.returncode, 0, msg=label)
                proc = _run(["-X", hook])
                self.assertNotEqual(proc.returncode, 0, msg=label)
                self.assertFalse(os.path.exists(os.path.join(state, gone_mark)), msg=label)
                proc = _run(["-F", hook])
                self.assertEqual(proc.returncode, 0, msg=label)
                proc = _run(["-X", hook])
                self.assertEqual(proc.returncode, 0, msg=label + ":" + proc.stderr)
                self.assertTrue(os.path.exists(os.path.join(state, gone_mark)), msg=label)

    def test_delete_chain_fixtures_distinguish_prefix_decoy(self):
        chin = "OPOS" + "_" + "INPUT"
        chfwd = "OPOS" + "_" + "FORWARD"
        extra = _chr(95) + "EXTRA"
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            decoy_in = chin + extra
            decoy_fwd = chfwd + extra
            fixtures = [
                ("INPUT", chin, decoy_in),
                ("FORWARD", chfwd, decoy_fwd),
                ("CHAIN", chin, decoy_in),
            ]
            for parent, hook, decoy in fixtures:
                for leaf in ("exact", "decoy"):
                    state_v4 = os.path.join(tmp, "state-v4")
                    state_v6 = os.path.join(tmp, "state-v6")
                    if parent == "CHAIN":
                        if leaf == "exact":
                            seed = (
                                "-P INPUT ACCEPT\n"
                                "-P FORWARD DROP\n"
                                "-N " + hook + "\n"
                                "-A " + hook + " -j DROP\n"
                                "-N " + decoy + "\n"
                                "-A " + decoy + " -j DROP\n"
                            )
                        else:
                            seed = (
                                "-P INPUT ACCEPT\n"
                                "-P FORWARD DROP\n"
                                "-N " + hook + "\n"
                                "-N " + decoy + "\n"
                                "-A " + decoy + " -j DROP\n"
                            )
                    elif leaf == "exact":
                        seed = (
                            "-P INPUT ACCEPT\n"
                            "-P FORWARD DROP\n"
                            "-N " + hook + "\n"
                            "-N " + decoy + "\n"
                            "-A INPUT -j " + hook + "\n"
                            "-A INPUT -j " + decoy + "\n"
                            if parent == "INPUT"
                            else "-P INPUT ACCEPT\n"
                            "-P FORWARD DROP\n"
                            "-N " + hook + "\n"
                            "-N " + decoy + "\n"
                            "-A FORWARD -j " + hook + "\n"
                            "-A FORWARD -j " + decoy + "\n"
                        )
                    else:
                        seed = (
                            "-P INPUT ACCEPT\n"
                            "-P FORWARD DROP\n"
                            "-N " + hook + "\n"
                            "-N " + decoy + "\n"
                            "-A INPUT -j " + decoy + "\n"
                            if parent == "INPUT"
                            else "-P INPUT ACCEPT\n"
                            "-P FORWARD DROP\n"
                            "-N " + hook + "\n"
                            "-N " + decoy + "\n"
                            "-A FORWARD -j " + decoy + "\n"
                        )
                    for path in (state_v4, state_v6):
                        with open(path, "w", encoding="utf-8") as handle:
                            handle.write(seed)
                    for tool in ("iptables-legacy", "ip6tables-legacy"):
                        _write_exe(
                            os.path.join(bin_dir, tool),
                            _shared_state_mock(state_v4 if "iptables-legacy" == tool or tool == "iptables-legacy" else state_v6),
                        )
                    _write_exe(os.path.join(bin_dir, "iptables-legacy"), _shared_state_mock(state_v4))
                    _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), _shared_state_mock(state_v6))
                    env = _probe_env(PATH=bin_dir + ":" + os.environ.get("PATH", ""))
                    label = parent + ":" + hook + ":" + leaf
                    proc = subprocess.run(
                        ["bash", cap_file, "stop"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                        env=env,
                    )
                    self.assertEqual(proc.returncode, 0, msg=label + ":" + proc.stdout + proc.stderr)
                    for path in (state_v4, state_v6):
                        with open(path, encoding="utf-8") as handle:
                            lines = [line.strip() for line in handle.read().splitlines() if line.strip()]
                        self.assertNotIn("-N " + hook, lines, msg=label + ":" + path)
                        self.assertNotIn("-A " + parent + " -j " + hook if parent != "CHAIN" else "-A " + hook + " -j DROP", lines, msg=label + ":" + path)
                        self.assertIn("-N " + decoy, lines, msg=label + ":" + path)
                        self.assertIn("-A INPUT -j " + decoy if parent == "INPUT" else "-A " + decoy + " -j DROP" if parent == "CHAIN" else "-A FORWARD -j " + decoy, lines, msg=label + ":" + path)

    def test_decoy_rule_preservation_assertion_is_unconditional(self):
        text = Path(__file__).read_text(encoding="utf-8")
        start = text.find("def test_delete_chain_fixtures_distinguish_prefix_decoy(self):")
        end = text.find("\n    def ", start + 1)
        lines = [line for line in text[start:end].splitlines() if line.strip()]
        decl = "self.assertIn(\"-N \" + decoy, lines, msg=label + \":\" + path)"
        rule = "self.assertIn(\"-A INPUT -j \" + decoy if parent == \"INPUT\" else \"-A \" + decoy + \" -j DROP\" if parent == \"CHAIN\" else \"-A FORWARD -j \" + decoy, lines, msg=label + \":\" + path)"
        stripped = [line.strip() for line in lines]
        self.assertIn(decl, stripped)
        self.assertIn(rule, stripped)
        self.assertEqual(stripped.index(rule), stripped.index(decl) + 1)
        self.assertEqual(lines[stripped.index(decl)][:len(lines[stripped.index(decl)]) - len(lines[stripped.index(decl)].lstrip())], lines[stripped.index(rule)][:len(lines[stripped.index(rule)]) - len(lines[stripped.index(rule)].lstrip())])

    def test_legacy_dry_run_skips_live_verification(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            log_path = os.path.join(tmp, "probe.log")
            stub = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "log=\"" + log_path + "\"\n"
                "echo \"$*\" >> \"$log\"\n"
                "if [ \"${1:-}\" = \"-S\" ] && [ \"${2:-}\" = \"INPUT\" ]; then\n"
                "  echo \"-P INPUT ACCEPT\"\n"
                "  exit 0\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), stub)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), stub)
            for name in ("iptables-legacy-restore", "ip6tables-legacy-restore"):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=0\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertNotIn("firewall verification failed", proc.stderr)
            if os.path.exists(log_path):
                with open(log_path, encoding="utf-8") as handle:
                    lines = [line for line in handle.read().splitlines() if line.strip()]
                for line in lines:
                    self.assertFalse(line.startswith("-S"), msg=line)


    def test_cold_boot_ipv6_failure_retains_safe_filtering_and_blocks_network(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            svc_file = os.path.join(cap_dir, "_etc_systemd_system_opos-firewall.service")
            self.assertTrue(os.path.exists(cap_file))
            self.assertTrue(os.path.exists(svc_file))
            with open(cap_file, encoding="utf-8") as handle:
                body = handle.read()
            with open(svc_file, encoding="utf-8") as handle:
                svc = handle.read()
            ssh_port = str(20 + 2)
            self.assertIn("opos_failsafe", body)
            self.assertIn("--dport " + ssh_port, body)
            self.assertIn("-j DROP", body)
            self.assertIn("RequiredBy=network-pre.target", svc)
            self.assertIn("Before=network-pre.target", svc)
            log_path = os.path.join(tmp, "calls.log")
            with open(log_path, "w", encoding="utf-8"):
                pass
            stub_ok = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "echo \"$0 $*\" >> \"" + log_path + "\"\n"
                "exit 0\n"
            )
            stub_fail_v6 = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "echo \"$0 $*\" >> \"" + log_path + "\"\n"
                "if [ -r \"${2:-}\" ]; then cat \"${2:-}\" >> \"" + log_path + "\"; fi\n"
                "exit 1\n"
            )
            stub_restore_ok = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "echo \"$0 $*\" >> \"" + log_path + "\"\n"
                "if [ -r \"${2:-}\" ]; then cat \"${2:-}\" >> \"" + log_path + "\"; fi\n"
                "exit 0\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), stub_ok)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), stub_ok)
            _write_exe(os.path.join(bin_dir, "iptables-legacy-restore"), stub_restore_ok)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy-restore"), stub_fail_v6)
            env = _probe_env(
                PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                OPOS_IPTABLES_LEGACY=os.path.join(bin_dir, "iptables-legacy"),
                OPOS_IP6TABLES_LEGACY=os.path.join(bin_dir, "ip6tables-legacy"),
                OPOS_IPTABLES_LEGACY_RESTORE=os.path.join(bin_dir, "iptables-legacy-restore"),
                OPOS_IP6TABLES_LEGACY_RESTORE=os.path.join(bin_dir, "ip6tables-legacy-restore"),
                OPOS_USB_IFACES=_iface(),
            )
            proc = subprocess.run(
                ["bash", cap_file, "start"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertNotEqual(proc.returncode, 0)
            with open(log_path, encoding="utf-8") as handle:
                calls = handle.read()
            self.assertIn("-j OPOS_INPUT", calls)
            self.assertIn("-j DROP", calls)
            self.assertIn("--dport " + ssh_port, calls)
            self.assertIn(_iface(), calls)

    def test_failsafe_has_no_chain_flush(self):
        txt = (Path(__file__).resolve().parents[1]/"deploy"/"harden.d"/"20-firewall.sh").read_text()
        m = re.search(r'opos_failsafe\(\) \{.*?^\}', txt, re.MULTILINE|re.DOTALL)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn('OPOS_IPTABLES_LEGACY_RESTORE', body)
        self.assertIn('OPOS_IP6TABLES_LEGACY_RESTORE', body)
        self.assertEqual(body.count('--noflush'), 2)

    def test_failsafe_ipv4_restored_without_chain_flush(self):
        import pathlib
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        text = pathlib.Path(script).read_text(encoding="utf-8")
        lines = text.splitlines()
        start = None
        for idx, line in enumerate(lines):
            if line.strip() == "opos_failsafe() {":
                start = idx
                break
        self.assertIsNotNone(start)
        end = None
        for idx in range(start + 1, len(lines)):
            if lines[idx] == "}":
                end = idx
                break
        self.assertIsNotNone(end)
        body = "\n".join(lines[start:end + 1])
        self.assertIn("OPOS_IPTABLES_LEGACY_RESTORE", body)
        self.assertIn("--noflush", body)
        for line in body.splitlines():
            if "$ipt" in line:
                self.assertNotIn("-F OPOS_INPUT", line)
                self.assertNotIn("-F OPOS_FORWARD", line)

    def test_failsafe_ipv4_transaction_contains_complete_fallback(self):
        import pathlib
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        text = pathlib.Path(script_path).read_text(encoding="utf-8")
        ssh_port = str(20 + 2)
        sport = str(60 + 7)
        dport = str(60 + 8)
        chain_in = "OPOS" + "_" + "INPUT"
        chain_fwd = "OPOS" + "_" + "FORWARD"
        def _check(inner):
            legacy = _extract_legacy_firewall(inner)
            fstart = legacy.find("opos_failsafe() {")
            self.assertNotEqual(fstart, -1)
            fend = legacy.find("\n}\n", fstart)
            self.assertNotEqual(fend, -1)
            failsafe = legacy[fstart:fend + 2]
            unesc = failsafe.replace("\\$", "$")
            loop_head = unesc.find("for iface in $OPOS_USB_IFACES; do")
            self.assertNotEqual(loop_head, -1)
            loop_end = unesc.find("done", loop_head)
            self.assertNotEqual(loop_end, -1)
            loop_body = unesc[loop_head:loop_end]
            self.assertIn("-p tcp --dport " + ssh_port + " -j ACCEPT", loop_body)
            self.assertIn(chain_in, loop_body)
            vopen = unesc.find('tmp="$(mktemp)"')
            self.assertNotEqual(vopen, -1)
            vrem = unesc.find('} > "$tmp"', vopen)
            self.assertNotEqual(vrem, -1)
            txn = unesc[vopen:vrem]
            self.assertEqual(txn.count("*filter"), 1)
            self.assertEqual(txn.count("COMMIT"), 1)
            decls = re.findall(r":([A-Za-z0-9_]+) - \[0:0\]", txn)
            self.assertEqual(sorted(decls), sorted([chain_in, chain_fwd]))
            self.assertNotIn("-F ", txn)
            self.assertNotIn("-X ", txn)
            self.assertNotIn("-N ", txn)
            self.assertNotIn("-P ", txn)
            ssh_payload = "printf '%s' \"$ssh4\""
            dhcp_line = "-A " + chain_in + " -p udp --sport " + sport + " --dport " + dport + " -j ACCEPT"
            needles = [
                "*filter",
                ":" + chain_in + " - [0:0]",
                ":" + chain_fwd + " - [0:0]",
                "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
                "-A " + chain_in + " -i lo -j ACCEPT",
                ssh_payload,
                dhcp_line,
                "-p icmp --icmp-type echo-request",
                "-p icmp --icmp-type destination-unreachable",
                "-p icmp --icmp-type time-exceeded",
                "-A " + chain_in + " -j DROP",
                "-A " + chain_fwd + " -j DROP",
                "COMMIT",
            ]
            pos = 0
            for needle in needles:
                nxt = txn.find(needle, pos)
                self.assertNotEqual(nxt, -1, needle)
                pos = nxt + len(needle)
            self.assertEqual(unesc.count("OPOS_IPTABLES_LEGACY_RESTORE"), 1)
            rpos = unesc.find("OPOS_IPTABLES_LEGACY_RESTORE")
            rstart = unesc.rfind("\n", 0, rpos) + 1
            rend = unesc.find("\n", rpos)
            rline = unesc[rstart:rend]
            self.assertIn("--noflush", rline)
            self.assertIn('"$tmp"', rline)
            self.assertGreater(rpos, vrem)
            cpos = unesc.find("rm -f", rend)
            self.assertNotEqual(cpos, -1)
            cstart = unesc.rfind("\n", 0, cpos) + 1
            cend = unesc.find("\n", cpos)
            cline = unesc[cstart:cend]
            self.assertIn('"$tmp"', cline)
        _check(text)
        with self.assertRaises(AssertionError):
            _check(text.replace("OPOS_IPTABLES_LEGACY_RESTORE", "OPOS_REMOVED_RESTORE"))
        with self.assertRaises(AssertionError):
            _check(text.replace("COMMIT", "REMOVED"))
        with self.assertRaises(AssertionError):
            _check(text.replace("$ssh4", "$gone"))

    def test_legacy_heredoc_extraction_uses_complete_lines(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        body = text.replace("#!/bin/sh\n", "#!/bin/sh\n# embedded EOF marker\n", 1)
        legacy = _extract_legacy_firewall(body)
        self.assertTrue(legacy.startswith("#!/bin/sh\n# embedded EOF marker\n"))
        self.assertIn("opos_failsafe() {", legacy)
        self.assertTrue(legacy.endswith("esac\n"))
        self.assertNotIn("Description=Old Phone OS firewall", legacy)

    def test_failsafe_ipv6_restored_without_chain_flush(self):
        import pathlib
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        text = pathlib.Path(script).read_text(encoding="utf-8")
        lines = text.splitlines()
        start = None
        for idx, line in enumerate(lines):
            if line.strip() == "opos_failsafe() {":
                start = idx
                break
        self.assertIsNotNone(start)
        end = None
        for idx in range(start + 1, len(lines)):
            if lines[idx] == "}":
                end = idx
                break
        self.assertIsNotNone(end)
        body = "\n".join(lines[start:end + 1])
        self.assertIn("OPOS_IP6TABLES_LEGACY_RESTORE", body)
        self.assertIn("--noflush", body)
        for line in body.splitlines():
            if "$ip6t" in line:
                self.assertNotIn("-F OPOS_INPUT", line)
                self.assertNotIn("-F OPOS_FORWARD", line)

    def test_legacy_requires_live_ssh_and_rolls_back(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        dot = chr(46)
        colon = chr(58)
        port = str(20 + 2)
        other_port = str(79 + 1)
        any_ip = dot.join([str(0), str(0), str(0), str(0)])
        good_hit = any_ip + colon + port
        bad_hit = any_ip + colon + other_port

        def _run_case(has_iface, ss_good, ssh_ok):
            with tempfile.TemporaryDirectory() as tmp:
                bin_dir = os.path.join(tmp, "bin")
                cap_dir = os.path.join(tmp, "cap")
                os.mkdir(bin_dir)
                os.mkdir(cap_dir)
                sysnet = os.path.join(tmp, "sysnet")
                os.mkdir(sysnet)
                if has_iface:
                    os.mkdir(os.path.join(sysnet, _iface()))
                for name in ("iptables-legacy", "ip6tables-legacy", "iptables-legacy-restore", "ip6tables-legacy-restore"):
                    _write_exe(os.path.join(bin_dir, name), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
                ss_body = "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\n"
                if ss_good:
                    ss_body += "echo \"LISTEN " + good_hit + "\"\n"
                else:
                    ss_body += "echo \"LISTEN " + bad_hit + "\"\n"
                ss_body += "exit 0\n"
                _write_exe(os.path.join(bin_dir, "ss"), ss_body)
                ssh_code = "0" if ssh_ok else "1"
                _write_exe(os.path.join(bin_dir, "ssh"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit " + ssh_code + "\n")
                manifest = os.path.join(tmp, "manifest")
                with open(manifest, "w", encoding="utf-8"):
                    pass
                run_log = os.path.join(tmp, "run.log")
                with open(run_log, "w", encoding="utf-8"):
                    pass
                probe = (
                    "set -u\n"
                    "APPLY=0\n"
                    "TEST=1\n"
                    "OPOS_USB_IFACES=" + _iface() + "\n"
                    "MANIFEST=" + manifest + "\n"
                    "CAPTURE_DIR=" + cap_dir + "\n"
                    "RUN_LOG=" + run_log + "\n"
                    "SYSNET=" + sysnet + "\n"
                    "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR RUN_LOG\n"
                    "export OPOS_SYS_CLASS_NET=\"$SYSNET\"\n"
                    "export OPOS_SS=\"" + bin_dir + "/ss\"\n"
                    "export OPOS_SSH=\"" + bin_dir + "/ssh\"\n"
                    "export PATH=" + bin_dir + ":$PATH\n"
                    "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                    "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                    "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                    "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                    "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                    "say() { :; }\n"
                    "warn() { :; }\n"
                    "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                    "run() { printf " + _chr(37) + "s\\\\n \"$*\" >> \"$RUN_LOG\"; return 0; }\n"
                    "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                    ". " + script + "\n"
                    "step_firewall_legacy\n"
                )
                proc = subprocess.run(["bash", "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                with open(run_log, encoding="utf-8") as handle:
                    logged = handle.read()
                return proc, logged
        proc, _ = _run_case(False, True, True)
        self.assertNotEqual(proc.returncode, 0)
        proc, _ = _run_case(True, False, True)
        self.assertNotEqual(proc.returncode, 0)
        proc, logged = _run_case(True, True, False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("stop", logged)
        proc, logged = _run_case(True, True, True)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertNotIn("stop", logged)


    def test_delete_removes_one_duplicate_jump_per_invocation(self):
        chin = "OPOS" + "_" + "INPUT"
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            seed = "-A INPUT -j " + chin + "\n-A INPUT -j " + chin + "\n"
            with open(state, "w", encoding="utf-8") as handle:
                handle.write(seed)
            _write_exe(tool, _shared_state_mock(state))
            proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with open(state, encoding="utf-8") as handle:
                remaining = [line.strip() for line in handle.read().splitlines() if line.strip()]
            self.assertEqual(remaining, ["-A INPUT -j " + chin])
            proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with open(state, encoding="utf-8") as handle:
                remaining = [line.strip() for line in handle.read().splitlines() if line.strip()]
            self.assertEqual(remaining, [])

    def test_delete_last_jump_empties_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, state4, _, cmd4, _, _ = self._make_legacy_start_capture(tmp)
            hook = "OPOS" + "_" + "INPUT"
            want = "-A INPUT -j " + hook + "\n"
            with open(state4, "w", encoding="utf-8") as handle:
                handle.write(want)
            proc = subprocess.run(
                [cmd4, "-D", "INPUT", "-j", hook],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with open(state4, encoding="utf-8") as handle:
                raw = handle.read()
            self.assertNotIn(hook, raw)
            self.assertEqual(raw.strip(), "")
            proc = subprocess.run(
                [cmd4, "-C", "INPUT", "-j", hook],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)


    def test_delete_rejects_invalid_jump_option_and_preserves_state(self):
        chin = "OPOS" + "_" + "INPUT"
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            seed = "-A INPUT -j " + chin + "\n"
            _write_exe(tool, _shared_state_mock(state))
            bad_i = "-" + "i"
            bad_g = "-" + "g"
            bad_cases = [
                ["-D", "INPUT", bad_i, chin],
                ["-D", "INPUT", bad_g, chin],
                ["--delete", "INPUT", bad_i, chin],
            ]
            for argv in bad_cases:
                with open(state, "w", encoding="utf-8") as handle:
                    handle.write(seed)
                proc = subprocess.run([tool] + argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                self.assertNotEqual(proc.returncode, 0, msg=" ".join(argv))
                with open(state, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), seed, msg=" ".join(argv))
            with open(state, "w", encoding="utf-8") as handle:
                handle.write(seed)
            proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with open(state, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "")
            with open(state, "w", encoding="utf-8") as handle:
                handle.write(seed)
            proc = subprocess.run([tool, "--delete", "INPUT", "--jump", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with open(state, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "")

    def test_legacy_rejects_unowned_and_preserves_owned_for_rollback(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        hook_in = "OPOS" + "_" + "INPUT"
        hook_fwd = "OPOS" + "_" + "FORWARD"
        dot = chr(46)
        colon = chr(58)
        doc_a = dot.join([str(203), str(0), str(113), str(7)])
        doc_b = dot.join([str(198), str(51), str(100), str(23)])
        any_ip = dot.join([str(0), str(0), str(0), str(0)])
        port = str(20 + 2)
        hit = any_ip + colon + port
        iface = _iface()
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            os.mkdir(bin_dir)
            sysnet = os.path.join(tmp, "sysnet")
            os.mkdir(sysnet)
            os.mkdir(os.path.join(sysnet, iface))
            _write_exe(os.path.join(bin_dir, "ss"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\necho \"LISTEN " + hit + "\"\nexit 0\n")
            _write_exe(os.path.join(bin_dir, "ssh"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
            foreign = "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nif [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\nif [ \"${1:-}\" = \"-S\" ] && [ \"${2:-}\" = \"" + hook_in + "\" ]; then echo \"-N " + hook_in + "\"\necho \"-A " + hook_in + " -s " + doc_a + " -j ACCEPT\"\nexit 0\nfi\nif [ \"${1:-}\" = \"-S\" ]; then echo \"-P INPUT ACCEPT\"\necho \"-P FORWARD DROP\"\nexit 0\nfi\nexit 0\n"
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), foreign)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), foreign)
            for name in ("iptables-legacy-restore", "ip6tables-legacy-restore"):
                _write_exe(os.path.join(bin_dir, name), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
            root = os.path.join(tmp, "root")
            os.mkdir(root)
            run_log = os.path.join(tmp, "run.log")
            with open(run_log, "w", encoding="utf-8"):
                pass
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = ("set -u\n" "APPLY=0\n" "TEST=1\n" "OPOS_USB_IFACES=" + iface + "\n" "MANIFEST=" + manifest + "\n" "export APPLY TEST OPOS_USB_IFACES MANIFEST\n" "export OPOS_TEST_ROOT=" + root + "\n" "export RUN_LOG=" + run_log + "\n" "export OPOS_SYS_CLASS_NET=" + sysnet + "\n" "export OPOS_SS=" + bin_dir + "/ss\n" "export OPOS_SSH=" + bin_dir + "/ssh\n" "export PATH=" + bin_dir + ":$PATH\n" "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n" "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n" "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n" "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n" "die() { echo die: $* >&2; exit 1; }\n" "say() { :; }\n" "warn() { :; }\n" "p() { printf \"%s\" \"$OPOS_TEST_ROOT$1\"; }\n" "write_file() { dest=\"$1\"; out=\"$OPOS_TEST_ROOT$dest\"; mkdir -p \"$(dirname \"$out\")\"; cat > \"$out\"; }\n" "run() { printf \"%s\\n\" \"$*\" >> \"$RUN_LOG\"; return 0; }\n" ". " + script + "\n" "step_firewall_legacy\n")
            proc = subprocess.run(["bash", "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("unowned", proc.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            os.mkdir(bin_dir)
            sysnet = os.path.join(tmp, "sysnet")
            os.mkdir(sysnet)
            os.mkdir(os.path.join(sysnet, iface))
            _write_exe(os.path.join(bin_dir, "ss"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\necho \"LISTEN " + hit + "\"\nexit 0\n")
            _write_exe(os.path.join(bin_dir, "ssh"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 1\n")
            owned = "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nif [ \"${1:-}\" = \"-C\" ]; then exit 1; fi\nif [ \"${1:-}\" = \"-S\" ] && [ \"${2:-}\" = \"" + hook_in + "\" ]; then echo \"-N " + hook_in + "\"\necho \"-A " + hook_in + " -s " + doc_b + " -j ACCEPT\"\necho \"-A " + hook_in + " -j DROP\"\nexit 0\nfi\nif [ \"${1:-}\" = \"-S\" ]; then echo \"-P INPUT ACCEPT\"\necho \"-P FORWARD DROP\"\nexit 0\nfi\nexit 0\n"
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), owned)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), owned)
            for name in ("iptables-legacy-restore", "ip6tables-legacy-restore"):
                _write_exe(os.path.join(bin_dir, name), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
            root = os.path.join(tmp, "root")
            os.mkdir(root)
            run_log = os.path.join(tmp, "run.log")
            with open(run_log, "w", encoding="utf-8"):
                pass
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = ("set -u\n" "APPLY=0\n" "TEST=1\n" "OPOS_USB_IFACES=" + iface + "\n" "MANIFEST=" + manifest + "\n" "export APPLY TEST OPOS_USB_IFACES MANIFEST\n" "export OPOS_TEST_ROOT=" + root + "\n" "export RUN_LOG=" + run_log + "\n" "export OPOS_SYS_CLASS_NET=" + sysnet + "\n" "export OPOS_SS=" + bin_dir + "/ss\n" "export OPOS_SSH=" + bin_dir + "/ssh\n" "export PATH=" + bin_dir + ":$PATH\n" "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n" "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n" "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n" "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n" "die() { echo die: $* >&2; exit 1; }\n" "say() { :; }\n" "warn() { :; }\n" "p() { printf \"%s\" \"$OPOS_TEST_ROOT$1\"; }\n" "write_file() { dest=\"$1\"; out=\"$OPOS_TEST_ROOT$dest\"; mkdir -p \"$(dirname \"$out\")\"; cat > \"$out\"; }\n" "run() { printf \"%s\\n\" \"$*\" >> \"$RUN_LOG\"; return 0; }\n" ". " + script + "\n" "step_firewall_legacy\n")
            proc = subprocess.run(["bash", "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("rolled back", proc.stderr)
            prev = os.path.join(root, "etc", "opos", "fw-prev-v4-" + hook_in)
            self.assertTrue(os.path.exists(prev))
            with open(prev, encoding="utf-8") as handle:
                saved = handle.read()
            self.assertIn(doc_b, saved)
            self.assertIn("-A " + hook_in + " -j DROP", saved)


    def test_stop_reports_jump_flush_chain_failure_per_family(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            for name in (
                "iptables-legacy",
                "ip6tables-legacy",
                "iptables-legacy-restore",
                "ip6tables-legacy-restore",
            ):
                _write_exe(
                    os.path.join(bin_dir, name),
                    "#!/bin/bash\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "exit 0\n",
                )
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))
            hook_in = "OPOS" + "_" + "INPUT"
            hook_fwd = "OPOS" + "_" + "FORWARD"
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            seed = (
                "-P INPUT ACCEPT\n"
                "-P FORWARD DROP\n"
                "-N " + hook_in + "\n"
                "-A " + hook_in + " -j DROP\n"
                "-N " + hook_fwd + "\n"
                "-A " + hook_fwd + " -j DROP\n"
                "-A INPUT -j " + hook_in + "\n"
                "-A FORWARD -j " + hook_fwd + "\n"
            )
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "case \"$0\" in\n"
                "  *ip6tables-legacy) state=\"" + state_v6 + "\"; fail=\"${OPOS_FAIL_V6:-}\";;\n"
                "  *) state=\"" + state_v4 + "\"; fail=\"${OPOS_FAIL_V4:-}\";;\n"
                "esac\n"
                "argv=(\"$@\")\n"
                "count=\"$#\"\n"
                "idx=0\n"
                "while [ \"$idx\" -lt \"$count\" ]; do\n"
                "  tok=\"${argv[$idx]}\"\n"
                "  if [ \"$tok\" = \"-w\" ] || [ \"$tok\" = \"--wait\" ]; then\n"
                "    idx=$((idx + 1))\n"
                "    if [ \"$idx\" -lt \"$count\" ]; then\n"
                "      case \"${argv[$idx]}\" in\n"
                "        *[!0-9]*) ;;\n"
                "        *) idx=$((idx + 1)) ;;\n"
                "      esac\n"
                "    fi\n"
                "  elif [ \"$tok\" = \"-t\" ] || [ \"$tok\" = \"--table\" ]; then\n"
                "    idx=$((idx + 1))\n"
                "    if [ \"$idx\" -ge \"$count\" ] || [ \"${argv[$idx]}\" != \"filter\" ]; then\n"
                "      exit 1\n"
                "    fi\n"
                "    idx=$((idx + 1))\n"
                "  else\n"
                "    break\n"
                "  fi\n"
                "done\n"
                "op=\"\"\n"
                "if [ \"$idx\" -lt \"$count\" ]; then op=\"${argv[$idx]}\"; fi\n"
                "second=\"\"\n"
                "if [ \"$((idx + 1))\" -lt \"$count\" ]; then second=\"${argv[$((idx + 1))]}\"; fi\n"
                "third=\"\"\n"
                "if [ \"$((idx + 2))\" -lt \"$count\" ]; then third=\"${argv[$((idx + 2))]}\"; fi\n"
                "fourth=\"\"\n"
                "if [ \"$((idx + 3))\" -lt \"$count\" ]; then fourth=\"${argv[$((idx + 3))]}\"; fi\n"
                "rest=$((count - idx))\n"
                "if [ \"$op\" = \"-C\" ] || [ \"$op\" = \"--check\" ]; then\n"
                "  want=\"\"\n"
                "  if [ \"$rest\" = \"4\" ]; then\n"
                "    if [ \"$second\" = \"INPUT\" ] && [ \"$fourth\" = \"" + hook_in + "\" ]; then want=\"-A INPUT -j " + hook_in + "\"; fi\n"
                "    if [ \"$second\" = \"FORWARD\" ] && [ \"$fourth\" = \"" + hook_fwd + "\" ]; then want=\"-A FORWARD -j " + hook_fwd + "\"; fi\n"
                "  fi\n"
                "  if [ -n \"$want\" ] && grep -qFx -- \"$want\" \"$state\"; then exit 0; fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$op\" = \"-S\" ] || [ \"$op\" = \"--list\" ]; then\n"
                "  if [ \"$rest\" = \"1\" ]; then cat \"$state\"; exit 0; fi\n"
                "  if [ \"$rest\" = \"2\" ]; then\n"
                "    if [ \"$second\" = \"INPUT\" ]; then grep -E -- \"^-P INPUT|^-A INPUT\" \"$state\" || true; exit 0; fi\n"
                "    if [ \"$second\" = \"FORWARD\" ]; then grep -E -- \"^-P FORWARD|^-A FORWARD\" \"$state\" || true; exit 0; fi\n"
                "    if [ \"$second\" = \"" + hook_in + "\" ] || [ \"$second\" = \"" + hook_fwd + "\" ]; then grep -F -- \"$second\" \"$state\"; exit $?; fi\n"
                "  fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$rest\" = \"2\" ]; then\n"
                "  if [ \"$op\" = \"-F\" ] || [ \"$op\" = \"--flush\" ]; then\n"
                "    if [ \"$second\" = \"" + hook_in + "\" ] || [ \"$second\" = \"" + hook_fwd + "\" ]; then\n"
                "      if [ \"$fail\" = \"F\" ]; then echo \"flush-failed\" >&2; exit 1; fi\n"
                "      grep -vF -- \"-A $second \" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "      exit 0\n"
                "    fi\n"
                "    exit 1\n"
                "  fi\n"
                "  if [ \"$op\" = \"-X\" ] || [ \"$op\" = \"--delete-chain\" ]; then\n"
                "    if [ \"$second\" = \"" + hook_in + "\" ] || [ \"$second\" = \"" + hook_fwd + "\" ]; then\n"
                "      if [ \"$fail\" = \"X\" ]; then echo \"chain-failed\" >&2; exit 1; fi\n"
                "      if grep -qF -- \"-A $second \" \"$state\"; then exit 1; fi\n"
                "      if grep -qE -- \"-j $second([[:space:]]|$)\" \"$state\"; then exit 1; fi\n"
                "      grep -vFx -- \"-N $second\" \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "      exit 0\n"
                "    fi\n"
                "    exit 1\n"
                "  fi\n"
                "fi\n"
                "if [ \"$rest\" = \"4\" ]; then\n"
                "  if [ \"$op\" = \"-D\" ] || [ \"$op\" = \"--delete\" ]; then\n"
                "    if [ \"$fail\" = \"D\" ]; then echo \"delete-failed\" >&2; exit 1; fi\n"
                "    if [ \"$third\" != \"-j\" ] && [ \"$third\" != \"--jump\" ]; then exit 1; fi\n"
                "    if [ \"$second\" = \"INPUT\" ] && [ \"$fourth\" = \"" + hook_in + "\" ]; then\n"
                "      awk -v want=\"-A $second -j $fourth\" '!removed && $0 == want {removed=1; next} {print}' \"$state\" > \"$state.tmp\" || exit \"$?\"\n"
                "      mv \"$state.tmp\" \"$state\" || exit \"$?\"\n"
                "      exit 0\n"
                "    fi\n"
                "    if [ \"$second\" = \"FORWARD\" ] && [ \"$fourth\" = \"" + hook_fwd + "\" ]; then\n"
                "      awk -v want=\"-A $second -j $fourth\" '!removed && $0 == want {removed=1; next} {print}' \"$state\" > \"$state.tmp\" || exit \"$?\"\n"
                "      mv \"$state.tmp\" \"$state\" || exit \"$?\"\n"
                "      exit 0\n"
                "    fi\n"
                "    exit 1\n"
                "  fi\n"
                "fi\n"
                "exit 1\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)

            def _reset():
                for path in (state_v4, state_v6):
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(seed)

            def _stop(fail_v4, fail_v6):
                _reset()
                env = _probe_env(
                    PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                    OPOS_FAIL_V4=fail_v4,
                    OPOS_FAIL_V6=fail_v6,
                )
                return subprocess.run(
                    ["bash", cap_file, "stop"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    env=env,
                )

            proc = _stop("", "")
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            for path in (state_v4, state_v6):
                with open(path, encoding="utf-8") as handle:
                    raw = handle.read()
                self.assertNotIn(hook_in, raw, msg=path)
                self.assertNotIn(hook_fwd, raw, msg=path)
            env_ok = _probe_env(
                PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                OPOS_FAIL_V4="",
                OPOS_FAIL_V6="",
            )
            for tool in ("iptables-legacy", "ip6tables-legacy"):
                bin_tool = os.path.join(bin_dir, tool)
                for args in (["-C", "INPUT", "-j", hook_in], ["-C", "FORWARD", "-j", hook_fwd]):
                    proc = subprocess.run(
                        [bin_tool] + args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                        env=env_ok,
                    )
                    self.assertNotEqual(proc.returncode, 0, msg=tool + ":" + " ".join(args))
                for args in (["-S", hook_in], ["-S", hook_fwd]):
                    proc = subprocess.run(
                        [bin_tool] + args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                        env=env_ok,
                    )
                    self.assertNotEqual(proc.returncode, 0, msg=tool + ":" + " ".join(args))
                for args in (["-S"], ["-S", "INPUT"], ["-S", "FORWARD"]):
                    proc = subprocess.run(
                        [bin_tool] + args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                        env=env_ok,
                    )
                    self.assertEqual(proc.returncode, 0, msg=tool + ":" + " ".join(args))
                    self.assertNotIn(hook_in, proc.stdout, msg=tool + ":" + " ".join(args))
                    self.assertNotIn(hook_fwd, proc.stdout, msg=tool + ":" + " ".join(args))
            cases = [
                ("D", "", "v4:jump-delete"),
                ("F", "", "v4:flush"),
                ("X", "", "v4:chain-delete"),
                ("", "D", "v6:jump-delete"),
                ("", "F", "v6:flush"),
                ("", "X", "v6:chain-delete"),
            ]
            for fail_v4, fail_v6, label in cases:
                proc = _stop(fail_v4, fail_v6)
                self.assertNotEqual(proc.returncode, 0, msg=label + ":" + proc.stdout + proc.stderr)


    def test_shared_mock_delete_preserves_prefixed_decoy(self):
        chin = "OPOS" + "_" + "INPUT"
        extra = _chr(95) + "EXTRA"
        decoy = chin + extra
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            target_line = "-A INPUT -j " + chin
            decoy_line = "-A INPUT -j " + decoy
            with open(state, "w", encoding="utf-8") as handle:
                handle.write("-P INPUT ACCEPT\n")
                handle.write(target_line + "\n")
                handle.write(decoy_line + "\n")
            mock = _shared_state_mock(state)
            _write_exe(tool, mock)
            proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            proc = subprocess.run([tool, "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            lines = [line.strip() for line in proc.stdout.splitlines()]
            self.assertNotIn(target_line, lines)
            self.assertIn(decoy_line, lines)

    def test_shared_mock_delete_requires_match_and_successful_commit(self):
        chin = "OPOS" + "_" + "INPUT"
        unrelated = "UNREL" + "ATED"
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            _write_exe(tool, _shared_state_mock(state))
            with self.subTest("missing-match"):
                seed = "-A INPUT -j " + unrelated + "\n"
                with open(state, "w", encoding="utf-8") as handle:
                    handle.write(seed)
                proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                self.assertNotEqual(proc.returncode, 0)
                with open(state, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), seed)
            with self.subTest("failed-commit"):
                seed = "-A INPUT -j " + chin + "\n"
                with open(state, "w", encoding="utf-8") as handle:
                    handle.write(seed)
                stub_dir = os.path.join(tmp, "stub")
                os.mkdir(stub_dir)
                _write_exe(os.path.join(stub_dir, "mv"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 23\n")
                env = _probe_env(PATH=stub_dir + ":" + os.environ.get("PATH", ""))
                proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, env=env)
                self.assertEqual(proc.returncode, 23)
                with open(state, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), seed)

    def test_shared_mock_delete_rejects_existing_empty_state(self):
        hook = "OPOS" + "_" + "INPUT"
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            with open(state, "w", encoding="utf-8"):
                pass
            _write_exe(tool, _shared_state_mock(state))
            proc = subprocess.run([tool, "-D", "INPUT", "-j", hook], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(os.path.exists(state))
            with open(state, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "")

    def test_legacy_start_moves_input_jump_first(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        hook_in = "OPOS" + "_" + "INPUT"
        jump = "-A INPUT -j " + hook_in
        accept = "-A INPUT -j ACCEPT"
        policy = "-P INPUT ACCEPT"
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            cap_dir = os.path.join(tmp, "cap")
            os.mkdir(bin_dir)
            os.mkdir(cap_dir)
            sysnet = os.path.join(tmp, "sysnet")
            os.mkdir(sysnet)
            os.mkdir(os.path.join(sysnet, _iface()))
            dot = chr(46)
            colon = chr(58)
            any_ip = dot.join([str(0), str(0), str(0), str(0)])
            port = str(20 + 2)
            hit = any_ip + colon + port
            _write_exe(os.path.join(bin_dir, "ss"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\necho \"LISTEN " + hit + "\"\nexit 0\n")
            _write_exe(os.path.join(bin_dir, "ssh"), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
            state_v4 = os.path.join(tmp, "state-v4")
            state_v6 = os.path.join(tmp, "state-v6")
            mock = (
                "#!/bin/bash\n"
                "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                "case \"$0\" in\n"
                "  *ip6tables-legacy) state=\"${OPOS_MOCK_STATE_V6:-}\";;\n"
                "  *) state=\"${OPOS_MOCK_STATE_V4:-}\";;\n"
                "esac\n"
                "if [ -z \"${state:-}\" ]; then state=\"${OPOS_MOCK_STATE:-}\"; fi\n"
                "if [ \"${1:-}\" = \"-C\" ]; then\n"
                "  if [ \"$#\" = \"4\" ] && [ \"${2:-}\" = \"INPUT\" ] && [ \"${4:-}\" = \"" + hook_in + "\" ]; then\n"
                "    if grep -qFx -- \"" + jump + "\" \"$state\"; then exit 0; fi\n"
                "  fi\n"
                "  if [ \"$#\" = \"4\" ] && [ \"${2:-}\" = \"FORWARD\" ]; then exit 1; fi\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-D\" ]; then\n"
                "  if [ \"$#\" = \"4\" ] && [ \"${2:-}\" = \"INPUT\" ] && [ \"${3:-}\" = \"-j\" ] && [ \"${4:-}\" = \"" + hook_in + "\" ]; then\n"
                "    if ! grep -qFx -- \"" + jump + "\" \"$state\"; then exit 1; fi\n"
                "    awk -v want=\"" + jump + "\" '!removed && $0 == want {removed=1; next} {print}' \"$state\" > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-I\" ]; then\n"
                "  if [ \"${2:-}\" = \"INPUT\" ] && [ \"${3:-}\" = \"1\" ]; then\n"
                "    if [ \"${OPOS_MOCK_NOOP_INSERT:-}\" = \"1\" ]; then exit 0; fi\n"
                "    if [ -n \"${OPOS_MOCK_NOOP_V4:-}\" ] && case \"$0\" in *ip6tables-legacy*) false;; *) true;; esac; then exit 0; fi\n"
                "    if [ -n \"${OPOS_MOCK_NOOP_V6:-}\" ] && case \"$0\" in *ip6tables-legacy*) true;; *) false;; esac; then exit 0; fi\n"
                "    { echo \"" + jump + "\"; cat \"$state\"; } > \"$state.tmp\" && mv \"$state.tmp\" \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                "if [ \"${1:-}\" = \"-S\" ]; then\n"
                "  if [ \"${2:-}\" = \"INPUT\" ] || [ -z \"${2:-}\" ]; then\n"
                "    echo \"" + policy + "\"\n"
                "    cat \"$state\"\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n"
            )
            _write_exe(os.path.join(bin_dir, "iptables-legacy"), mock)
            _write_exe(os.path.join(bin_dir, "ip6tables-legacy"), mock)
            for name in ("iptables-legacy-restore", "ip6tables-legacy-restore"):
                _write_exe(os.path.join(bin_dir, name), "#!/bin/bash\n# SPDX-License-Identifier: GPL-3.0-or-later\nexit 0\n")
            manifest = os.path.join(tmp, "manifest")
            with open(manifest, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "APPLY=0\n"
                "TEST=1\n"
                "OPOS_USB_IFACES=" + _iface() + "\n"
                "MANIFEST=" + manifest + "\n"
                "CAPTURE_DIR=" + cap_dir + "\n"
                "SYSNET=" + sysnet + "\n"
                "export APPLY TEST OPOS_USB_IFACES MANIFEST CAPTURE_DIR\n"
                "export OPOS_SYS_CLASS_NET=\"$SYSNET\"\n"
                "export OPOS_SS=\"" + bin_dir + "/ss\"\n"
                "export OPOS_SSH=\"" + bin_dir + "/ssh\"\n"
                "export PATH=" + bin_dir + ":$PATH\n"
                "export OPOS_IPTABLES_LEGACY=" + bin_dir + "/iptables-legacy\n"
                "export OPOS_IP6TABLES_LEGACY=" + bin_dir + "/ip6tables-legacy\n"
                "export OPOS_IPTABLES_LEGACY_RESTORE=" + bin_dir + "/iptables-legacy-restore\n"
                "export OPOS_IP6TABLES_LEGACY_RESTORE=" + bin_dir + "/ip6tables-legacy-restore\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + _chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "run() { return 0; }\n"
                "write_file() { dest=\"$1\"; out=\"$CAPTURE_DIR/$(printf " + _chr(37) + "s \"$dest\" | tr '/' '_')\"; cat > \"$out\"; }\n"
                ". " + script + "\n"
                "step_firewall_legacy\n"
            )
            proc = subprocess.run(["bash", "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            cap_file = os.path.join(cap_dir, "_etc_opos_firewall-legacy.sh")
            self.assertTrue(os.path.exists(cap_file))

            def _base_env():
                return _probe_env(
                    PATH=bin_dir + ":" + os.environ.get("PATH", ""),
                    OPOS_MOCK_STATE_V4=state_v4,
                    OPOS_MOCK_STATE_V6=state_v6,
                    OPOS_IPTABLES_LEGACY=os.path.join(bin_dir, "iptables-legacy"),
                    OPOS_IP6TABLES_LEGACY=os.path.join(bin_dir, "ip6tables-legacy"),
                    OPOS_IPTABLES_LEGACY_RESTORE=os.path.join(bin_dir, "iptables-legacy-restore"),
                    OPOS_IP6TABLES_LEGACY_RESTORE=os.path.join(bin_dir, "ip6tables-legacy-restore"),
                )

            def _input_rules(tool, env):
                proc = subprocess.run([tool, "-S", "INPUT"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, env=env)
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                return [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("-A INPUT")]

            v4_mock = os.path.join(bin_dir, "iptables-legacy")
            v6_mock = os.path.join(bin_dir, "ip6tables-legacy")
            for path in (state_v4, state_v6):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(accept + "\n" + jump + "\n")
            env = _base_env()
            for _ in range(2):
                proc = subprocess.run(["bash", cap_file, "start"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, env=env)
                self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            for tool in (v4_mock, v6_mock):
                rules = _input_rules(tool, env)
                self.assertEqual(rules, [jump, accept], msg=tool)
            for flag in ("OPOS_MOCK_NOOP_V4", "OPOS_MOCK_NOOP_V6"):
                for path in (state_v4, state_v6):
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(accept + "\n" + jump + "\n")
                bad = _base_env()
                bad[flag] = "1"
                proc = subprocess.run(["bash", cap_file, "start"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, env=bad)
                self.assertNotEqual(proc.returncode, 0, msg=flag)


    def test_migration_guard_requires_ssh_accept(self):
        import shutil

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "deploy", "harden.d", "20-firewall.sh")
        port = str(20 + 2)
        bad_port = str(2222)
        dash = chr(45)
        sp = chr(32)

        def _activate(previous, current, nft_text=None, rules_text=None):
            root = tempfile.mkdtemp()
            run_log = os.path.join(root, "run.log")
            with open(run_log, "w", encoding="utf-8"):
                pass
            probe = (
                "set -u\n"
                "FW_BACKEND=" + current + "\n"
                "export FW_BACKEND\n"
                "export OPOS_TEST_ROOT=" + root + "\n"
                "die() { echo die:" + _chr(32) + "$* >&2; exit 1; }\n"
                "say() { :; }\n"
                "warn() { :; }\n"
                "p() { printf " + chr(37) + "s \"$OPOS_TEST_ROOT$1\"; }\n"
                "write_file() { dest=\"$1\"; out=\"$OPOS_TEST_ROOT$dest\"; mkdir -p \"$(dirname \"$out\")\"; cat > \"$out\"; }\n"
                "run() { return 0; }\n"
                ". " + script + "\n"
                "fw_activate_backend \"" + previous + "\"\n"
            )
            opos = os.path.join(root, "etc", "opos")
            os.makedirs(opos)
            with open(os.path.join(opos, "firewall.backend"), "w", encoding="utf-8") as handle:
                handle.write(previous)
            legacy = os.path.join(opos, "firewall-legacy.sh")
            with open(legacy, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(legacy, 0o700)
            if nft_text is not None:
                with open(os.path.join(opos, "firewall.nft"), "w", encoding="utf-8") as handle:
                    handle.write(nft_text)
            if rules_text is not None:
                with open(os.path.join(opos, "firewall.rules"), "w", encoding="utf-8") as handle:
                    handle.write(rules_text)
            proc = subprocess.run(
                ["bash", "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            shutil.rmtree(root, ignore_errors=True)
            return proc

        good_nft = "table inet x {\n\ttcp dport " + sp + port + " accept;\n}\n"
        self.assertEqual(_activate("iptables-legacy", "nft", nft_text=good_nft).returncode, 0)
        bad_nfts = [
            "table inet x {\n\ttcp dport " + sp + bad_port + " accept;\n}\n",
            "table inet x {\n\ttcp dport " + sp + port + " drop;\n}\n",
            "table inet x {\n\t# tcp dport " + sp + port + " accept;\n}\n",
            "table inet x {\n\ttcp dport " + sp + port + " drop; # accept\n}\n",
        ]
        for text in bad_nfts:
            proc = _activate("iptables-legacy", "nft", nft_text=text)
            self.assertNotEqual(proc.returncode, 0, msg=text)
        dport = dash + dash + "dport" + sp + port
        dport_bad = dash + dash + "dport" + sp + bad_port
        jump_ok = dash + "j" + sp + "ACCEPT"
        jump_drop = dash + "j" + sp + "DROP"
        good_rules = "-A OPOS_INPUT " + dash + "p tcp " + dport + " " + jump_ok + "\n"
        self.assertEqual(_activate("nft", "iptables-legacy", rules_text=good_rules).returncode, 0)
        bad_rules = [
            "-A OPOS_INPUT " + dash + "p tcp " + dport_bad + " " + jump_ok + "\n",
            "-A OPOS_INPUT " + dash + "p tcp " + dport + " " + jump_drop + "\n",
            "# -A OPOS_INPUT " + dash + "p tcp " + dport + " " + jump_ok + "\n",
            "-A OPOS_INPUT " + dash + "p tcp " + dport + " " + jump_drop + " -m comment " + dash + dash + "comment \"ACCEPT\"\n",
        ]
        for text in bad_rules:
            proc = _activate("nft", "iptables-legacy", rules_text=text)
            self.assertNotEqual(proc.returncode, 0, msg=text)

    def test_migration_iptables_guard_preserves_tcp_matches_static(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        start = text.find("fw_activate_backend() {")
        self.assertNotEqual(start, -1)
        end = text.find("step_firewall() {", start)
        self.assertNotEqual(end, -1)
        body = text[start:end]
        lines = [line for line in body.splitlines() if "/etc/opos/firewall.rules" in line]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        port = "grep -E -- '--dport[[:space:]]+22([^0-9]|$)'"
        tcp = "grep -E -- '-p[[:space:]]+tcp([[:space:]]|$)'"
        accept = "grep -Eq -- '-j[[:space:]]+ACCEPT([[:space:]]|$)'"
        self.assertIn(port, line)
        self.assertIn(tcp, line)
        self.assertIn(accept, line)
        self.assertIn(port + " | " + tcp, line)
        self.assertIn(tcp + " | " + accept, line)
        self.assertLess(line.find(port), line.find(tcp))
        self.assertLess(line.find(tcp), line.find(accept))
        self.assertNotIn("grep -Eq -- '-p[[:space:]]+tcp", line)

    def test_migration_iptables_guard_rejects_negation_static(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        start = text.find("fw_activate_backend() {")
        self.assertNotEqual(start, -1)
        end = text.find("\nstep_firewall() {", start)
        self.assertNotEqual(end, -1)
        body = text[start:end]
        lines = [line for line in body.splitlines() if "/etc/opos/firewall.rules" in line]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        sed_stage = "sed -e 's/#.*//'"
        neg = "grep -Ev -- '(^|[[:space:]])!([[:space:]]|$)'"
        port = "grep -E -- '--dport[[:space:]]+22([^0-9]|$)'"
        tcp = "grep -E -- '-p[[:space:]]+tcp([[:space:]]|$)'"
        accept = "grep -Eq -- '-j[[:space:]]+ACCEPT([[:space:]]|$)'"
        for stage in (sed_stage, neg, port, tcp, accept):
            self.assertIn(stage, line)
        self.assertLess(line.find(sed_stage), line.find(neg))
        self.assertLess(line.find(neg), line.find(port))
        self.assertLess(line.find(port), line.find(tcp))
        self.assertLess(line.find(tcp), line.find(accept))

    def test_migration_iptables_guard_requires_opos_input_static(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        start = text.find("fw_activate_backend() {")
        self.assertNotEqual(start, -1)
        end = text.find("\nstep_firewall() {", start)
        self.assertNotEqual(end, -1)
        body = text[start:end]
        lines = [line for line in body.splitlines() if "/etc/opos/firewall.rules" in line]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        sed_stage = "sed -e 's/#.*//'"
        chain = "grep -E -- '^-A[[:space:]]+OPOS_INPUT[[:space:]]+'"
        neg = "grep -Ev -- '(^|[[:space:]])!([[:space:]]|$)'"
        port = "grep -E -- '--dport[[:space:]]+22([^0-9]|$)'"
        tcp = "grep -E -- '-p[[:space:]]+tcp([[:space:]]|$)'"
        accept = "grep -Eq -- '-j[[:space:]]+ACCEPT([[:space:]]|$)'"
        for stage in (sed_stage, chain, neg, port, tcp, accept):
            self.assertIn(stage, line)
        self.assertLess(line.find(sed_stage), line.find(chain))
        self.assertLess(line.find(chain), line.find(neg))
        self.assertLess(line.find(neg), line.find(port))
        self.assertLess(line.find(port), line.find(tcp))
        self.assertLess(line.find(tcp), line.find(accept))

    def test_migration_guard_rejects_state_restricted_ssh_static(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        start = text.find("fw_activate_backend() {")
        self.assertNotEqual(start, -1)
        end = text.find("step_firewall() {", start)
        self.assertNotEqual(end, -1)
        body = text[start:end]
        nft_lines = [line for line in body.splitlines() if "/etc/opos/firewall.nft" in line]
        ipt_lines = [line for line in body.splitlines() if "/etc/opos/firewall.rules" in line]
        self.assertEqual(len(nft_lines), 1)
        self.assertEqual(len(ipt_lines), 1)
        nft_line = nft_lines[0]
        ipt_line = ipt_lines[0]
        nft_excl = "grep -Evi '(^|[^[:alnum:]_-])ct[[:space:]]+state([^[:alnum:]_-]|$)'"
        ipt_excl = "grep -Evi '(^|[^[:alnum:]_-])--(ctstate|state)([[:space:]]|=)'"
        self.assertEqual(nft_line.count(nft_excl), 1)
        self.assertEqual(ipt_line.count(ipt_excl), 1)
        self.assertLess(nft_line.find("sed "), nft_line.find(nft_excl))
        self.assertLess(nft_line.find(nft_excl), nft_line.find("dport"))
        self.assertLess(ipt_line.find("sed "), ipt_line.find(ipt_excl))
        self.assertLess(ipt_line.find(ipt_excl), ipt_line.find("OPOS_INPUT"))
        self.assertLess(ipt_line.find(ipt_excl), ipt_line.find("--dport"))

    def test_nft_migration_guard_requires_terminal_accept_verdict(self):
        script = Path(__file__).resolve().parents[1] / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        start = text.find("fw_activate_backend() {")
        self.assertNotEqual(start, -1)
        end = text.find("step_firewall() {", start)
        self.assertNotEqual(end, -1)
        body = text[start:end]
        lines = [line for line in body.splitlines() if "/etc/opos/firewall.nft" in line]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        pipeline = line[line.index("sed "):line.index(" >/dev/null")]
        pipeline = pipeline.replace(' "$(p /etc/opos/firewall.nft)"', "")
        self.assertNotIn("firewall.nft", pipeline)
        self.assertTrue(pipeline.startswith("sed "))
        self.assertTrue(pipeline.endswith("grep -Evi 'drop|reject'"))
        for fixture, should_pass in [
            ("tcp dport 22 accept;", True),
            ("tcp dport 22 accept; # SSH", True),
            ("tcp dport 2222 accept;", False),
            ("tcp dport 22 drop; # accept", False),
            ("tcp dport 22 reject;", False),
            ('tcp dport 22 log prefix "accept" drop;', False),
            ("tcp dport 22 counter;", False),
            ("tcp dport 22 accept counter;", False),
        ]:
            with self.subTest(fixture=fixture):
                proc = subprocess.run(["sh", "-c", pipeline], input=fixture + "\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                self.assertEqual(proc.stderr, "", msg=fixture)
                if should_pass:
                    self.assertEqual(proc.returncode, 0, msg=fixture + ": " + proc.stderr)
                else:
                    self.assertNotEqual(proc.returncode, 0, msg=fixture)

    def test_nft_migration_guard_strips_trailing_comment_static(self):
        script = Path(__file__).resolve().parents[1] / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        start = text.find("fw_activate_backend() {")
        self.assertNotEqual(start, -1)
        end = text.find("step_firewall() {", start)
        self.assertNotEqual(end, -1)
        body = text[start:end]
        lines = [line for line in body.splitlines() if "/etc/opos/firewall.nft" in line]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        kw = "".join(chr(c) for c in [99, 111, 109, 109, 101, 110, 116])
        self.assertIn("[[:space:]]" + kw + "[[:space:]]", line)
        self.assertIn(chr(34), line)
        self.assertIn(chr(92) + ".", line)
        self.assertIn(";" + chr(92) + "?", line)
        self.assertIn(chr(36) + "//", line)
        comment_pos = line.find(kw + "[[:space:]]")
        hash_pos = line.find("s/#.*//")
        quote_pos = line.find('s/"[^"]*"//g')
        self.assertNotEqual(comment_pos, -1)
        self.assertNotEqual(hash_pos, -1)
        self.assertNotEqual(quote_pos, -1)
        self.assertLess(comment_pos, hash_pos)
        self.assertLess(comment_pos, quote_pos)
        self.assertLess(hash_pos, line.find("dport"))
        self.assertLess(quote_pos, line.find("dport"))
        self.assertLess(quote_pos, line.find("accept"))
        self.assertLess(quote_pos, line.find("drop"))

    def test_delete_final_entry_empties_state_and_propagates_failures(self):
        chin = "OPOS" + "_" + "INPUT"
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state")
            tool = os.path.join(tmp, "iptables-legacy")
            seed = "-A INPUT -j " + chin + "\n"
            with open(state, "w", encoding="utf-8") as handle:
                handle.write(seed)
            _write_exe(tool, _shared_state_mock(state))
            proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with open(state, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "")
            proc = subprocess.run([tool, "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0)
            self.assertNotIn(chin, proc.stdout)
            os.remove(state)
            proc = subprocess.run([tool, "-D", "INPUT", "-j", chin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertNotEqual(proc.returncode, 0)

    def test_legacy_chain_snapshot_reset_before_probe(self):
        from pathlib import Path
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")

        def _body(name):
            lines = text.splitlines()
            start = None
            for idx, line in enumerate(lines):
                if line.strip() == name + "() {":
                    start = idx
                    break
            self.assertIsNotNone(start, msg=name)
            end = None
            for idx in range(start + 1, len(lines)):
                if lines[idx] == "}":
                    end = idx
                    break
            self.assertIsNotNone(end, msg=name)
            return "\n".join(lines[start:end + 1])

        claim = _body("fw_legacy_claim_chain")
        restore = _body("fw_legacy_restore_chain")
        assign = claim.find("fw_prev=")
        trunc = claim.find(": > \"$fw_prev\"")
        self.assertNotEqual(assign, -1)
        self.assertNotEqual(trunc, -1)
        self.assertLess(assign, trunc)
        probe = claim.find("\"$fw_bin\" -S \"$fw_chain\"")
        self.assertNotEqual(probe, -1)
        self.assertLess(trunc, probe)
        trunc_line = [line for line in claim.splitlines() if ": > \"$fw_prev\"" in line]
        self.assertEqual(len(trunc_line), 1)
        self.assertIn("die \"firewall backup failed", trunc_line[0])
        owned = claim.find("unowned chain")
        dump = claim.find("printf '%s\\n' \"$fw_dump\" > \"$fw_prev\"")
        self.assertNotEqual(owned, -1)
        self.assertNotEqual(dump, -1)
        self.assertLess(owned, dump)
        self.assertLess(trunc, dump)
        nonempty = restore.find("[ -s \"$fw_prev\" ]")
        self.assertNotEqual(nonempty, -1)
        tmp = restore.find("$(mktemp)")
        self.assertNotEqual(tmp, -1)
        self.assertLess(nonempty, tmp)


    def test_failsafe_ipv4_restore_failure_has_verified_ssh_fallback(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        start = text.find("opos_failsafe() {")
        self.assertNotEqual(start, -1)
        end = text.find("\n}", start)
        self.assertNotEqual(end, -1)
        body = text[start:end + 2]
        bs = chr(92)
        dl = chr(36)
        unesc = body.replace(bs + dl, dl)
        self.assertIn("v4_fallback", unesc)
        self.assertGreaterEqual(unesc.count('v4_fallback="1"'), 3)
        mktemp_pos = unesc.find("mktemp")
        self.assertNotEqual(mktemp_pos, -1)
        write_pos = unesc.find('> "$tmp"')
        self.assertNotEqual(write_pos, -1)
        restore_pos = unesc.find("OPOS_IPTABLES_LEGACY_RESTORE")
        self.assertNotEqual(restore_pos, -1)
        for pos in (mktemp_pos, write_pos, restore_pos):
            window = unesc[pos:pos + 2000]
            self.assertIn('v4_fallback="1"', window)
        jump_pos = unesc.find("-C INPUT -j OPOS_INPUT")
        self.assertNotEqual(jump_pos, -1)
        loop_key = "for "
        loop_pos = unesc.find(loop_key, jump_pos)
        self.assertNotEqual(loop_pos, -1)
        loop_line_end = unesc.find("\n", loop_pos)
        self.assertIn("OPOS_USB_IFACES", unesc[loop_pos:loop_line_end])
        fallback = unesc[loop_pos:]
        self.assertNotIn("mktemp", fallback)
        self.assertNotIn("RESTORE", fallback)
        self.assertNotIn("restore", fallback.lower())
        insert = '"$ipt" -I INPUT 1 -i "$fb_iface" -p tcp --dport 22 -j ACCEPT'
        check = '"$ipt" -C INPUT -i "$fb_iface" -p tcp --dport 22 -j ACCEPT'
        self.assertIn(insert, fallback)
        self.assertIn(check, fallback)
        self.assertGreaterEqual(fallback.count('fb_ok="0"'), 2)
        diag = "IPv4 SSH recovery failed"
        self.assertIn(diag, fallback)
        ret1 = fallback.find("return 1")
        self.assertNotEqual(ret1, -1)
        diag_pos = fallback.find(diag)
        self.assertLess(diag_pos, ret1)
        tail0 = fallback.find("return 0")
        self.assertNotEqual(tail0, -1)
        self.assertLess(ret1, tail0)

    def test_legacy_to_nft_migration_refused_before_mutation_static(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = None
        for idx, line in enumerate(lines):
            if line.strip() == "step_firewall() {":
                start = idx
                break
        self.assertIsNotNone(start)
        end = None
        for idx in range(start + 1, len(lines)):
            if re.match(r"^[A-Za-z0-9_]+\(\) \{$", lines[idx]):
                end = idx
                break
        self.assertIsNotNone(end)
        self.assertEqual([lines[start + 1].strip(), lines[start + 2].strip(), lines[start + 3].strip(), lines[start + 4].strip(), lines[start + 5].strip(), lines[start + 6].strip(), lines[start + 7].strip(), lines[start + 8].strip()], ['local iface=""', 'local ifaces_nft=""', 'local prev_backend=""', 'fw_pick_backend', 'prev_backend="$(fw_saved_backend)" || die "cannot read firewall backend state"', 'if [ "$prev_backend" = "iptables-legacy" ] && [ "$FW_BACKEND" = "nft" ]; then', 'die "firewall migration refused: legacy-to-nft requires verified inbound SSH"', 'fi'])
        body = "\n".join(lines[start:end])
        cond = re.search(r'if \[ "\$prev_backend" = "iptables-legacy" \] && \[ "\$FW_BACKEND" = "nft" \]; then', body)
        self.assertIsNotNone(cond)
        diag = 'die "firewall migration refused: legacy-to-nft requires verified inbound SSH"'
        self.assertIn(diag, body)
        self.assertLess(cond.start(), body.find(diag))
        pos_saved = body.find("fw_saved_backend")
        pos_guard = body.find(diag)
        pos_legacy = body.find("step_firewall_legacy")
        pos_write = body.find("write_file")
        pos_load = body.find("nft -c -f")
        if pos_load == -1:
            pos_load = body.find("nft -f")
        first_activate = body.find("fw_activate_backend")
        last_activate = body.rfind("fw_activate_backend")
        self.assertNotEqual(pos_saved, -1)
        self.assertNotEqual(pos_legacy, -1)
        self.assertNotEqual(pos_write, -1)
        self.assertNotEqual(pos_load, -1)
        self.assertNotEqual(first_activate, -1)
        self.assertNotEqual(last_activate, -1)
        self.assertNotEqual(first_activate, last_activate)
        self.assertLess(pos_saved, pos_guard)
        self.assertLess(pos_guard, pos_legacy)
        self.assertLess(pos_guard, pos_write)
        self.assertLess(pos_guard, pos_load)
        self.assertLess(pos_guard, first_activate)
        self.assertLess(pos_guard, last_activate)

    def test_legacy_to_nft_static_check_rejects_unreachable_refusal(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        source = script.read_text(encoding="utf-8")
        target = "\tif [ \"$prev_backend\" = \"iptables-legacy\" ] && [ \"$FW_BACKEND\" = \"nft\" ]; then\n\t\tdie \"firewall migration refused: legacy-to-nft requires verified inbound SSH\"\n\tfi\n"
        self.assertEqual(source.count(target), 1)
        variant = source.replace(target, "\tif false; then\n" + target + "\tfi\n", 1)
        from unittest import mock
        with mock.patch.object(Path, "read_text", return_value=variant):
            with self.assertRaises(AssertionError):
                self.test_legacy_to_nft_migration_refused_before_mutation_static()

    def test_nft_to_legacy_migration_refused_before_mutation_static(self):
        script = Path(__file__).resolve().parent.parent / "deploy" / "harden.d" / "20-firewall.sh"
        text = script.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = None
        for idx, line in enumerate(lines):
            if line.strip() == "step_firewall() {":
                start = idx
                break
        self.assertIsNotNone(start)
        end = None
        for idx in range(start + 1, len(lines)):
            if re.match(r"^[A-Za-z0-9_]+\(\) \{$", lines[idx]):
                end = idx
                break
        self.assertIsNotNone(end)
        body = "\n".join(lines[start:end])
        cond = re.search(r'if \[ "\$prev_backend" = "nft" \] && \[ "\$FW_BACKEND" = "iptables-legacy" \]; then', body)
        self.assertIsNotNone(cond)
        diag = 'die "firewall migration refused: nft-to-legacy requires verified inbound SSH"'
        self.assertIn(diag, body)
        self.assertLess(cond.start(), body.find(diag))
        pos_saved = body.find("fw_saved_backend")
        pos_guard = body.find(diag)
        pos_legacy = body.find("step_firewall_legacy")
        pos_write = body.find("write_file")
        pos_load = body.find("nft -c -f")
        if pos_load == -1:
            pos_load = body.find("nft -f")
        first_activate = body.find("fw_activate_backend")
        last_activate = body.rfind("fw_activate_backend")
        self.assertNotEqual(pos_saved, -1)
        self.assertNotEqual(pos_legacy, -1)
        self.assertNotEqual(pos_write, -1)
        self.assertNotEqual(pos_load, -1)
        self.assertNotEqual(first_activate, -1)
        self.assertNotEqual(last_activate, -1)
        self.assertNotEqual(first_activate, last_activate)
        self.assertLess(pos_saved, pos_guard)
        self.assertLess(pos_guard, pos_legacy)
        self.assertLess(pos_guard, pos_write)
        self.assertLess(pos_guard, pos_load)
        self.assertLess(pos_guard, first_activate)
        self.assertLess(pos_guard, last_activate)


if __name__ == "__main__":
    unittest.main()
