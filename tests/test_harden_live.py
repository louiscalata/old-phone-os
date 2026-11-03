# SPDX-License-Identifier: GPL-3.0-or-later
"""Live apply/rollback of service enablement crossed with activity."""

import glob
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARDEN = os.path.join(REPO, "deploy", "harden-device.sh")

UNITS = {
    "svc-ea.service": (True, True),
    "svc-ei.service": (True, False),
    "svc-da.service": (False, True),
    "svc-di.service": (False, False),
}

SYSTEMCTL_DOUBLE = """#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
state="$OPOS_TEST_STATE"
if [ -n "${OPOS_TEST_CALLS:-}" ]; then
  printf '%s\\n' "$*" >> "$OPOS_TEST_CALLS"
fi
printf '%s\\n' "$HOME" >> "$state/observed_home"
printf '%s\\n' "$TMPDIR" >> "$state/observed_tmpdir"
unit=""
for arg in "$@"; do
  case "$arg" in
    -*) continue ;;
    systemctl|enable|disable|start|stop|is-enabled|is-active|list-unit-files|daemon-reload|now) continue ;;
    *) unit="$arg" ;;
  esac
done
cmd="$1"
printf '%s\n' "$cmd $*" >> "$state/calls.log"
case "$cmd" in
  list-unit-files)
    grep -qx "$unit" "$state/all" && printf '%s disabled\\n' "$unit"
    ;;
  is-enabled) test -f "$state/enabled_$unit" ;;
  is-active) test -f "$state/active_$unit" ;;
  enable) touch "$state/enabled_$unit" ;;
  disable)
    rm -f "$state/enabled_$unit"
    case " $* " in *" --now "*) rm -f "$state/active_$unit" ;; esac
    ;;
  start) touch "$state/active_$unit" ;;
  stop) rm -f "$state/active_$unit" ;;
  daemon-reload) exit 0 ;;
  *) exit 0 ;;
esac
"""

_ALLOWED_ENV = ("PATH", "TERM", "LANG", "LC_ALL", "HOME", "TMPDIR")


def _harden_mutating_commands():
    # Maintained inventory covers host-facing commands and probes from the main script and sourced libraries; mkdir, chmod, cp, mv, rm, and touch stay outside so temporary-root file operations execute normally.
    return ("systemctl", "id", "nft", "sysctl", "apt-get", "chown", "passwd", "sshd", "ssh-keygen", "dpkg", "findmnt", "iptables-legacy", "ip6tables-legacy", "iptables-legacy-restore", "ip6tables-legacy-restore")


def _assert_resolves_inside_stub(env, stub_dir):
    for cmd in _harden_mutating_commands():
        p = shutil.which(cmd, path=env.get("PATH", ""))
        if p is None or not p.startswith(stub_dir + os.sep):
            raise AssertionError(cmd + " must resolve inside stub: " + str(p))


def _build_env(bindir, root, statedir, sandbox_base):
    base = os.path.abspath(sandbox_base)
    sys_tmp = os.path.abspath(tempfile.gettempdir())
    if base == os.path.sep or base == sys_tmp:
        raise AssertionError("sandbox base must be per-test temp dir: " + base)
    if os.path.commonpath((sys_tmp, base)) != sys_tmp:
        raise AssertionError("sandbox base must be inside system temp: " + base)
    home_dir = os.path.join(base, "home")
    tmpdir_dir = os.path.join(base, "tmp")
    for candidate in (os.path.abspath(home_dir), os.path.abspath(tmpdir_dir)):
        if os.path.commonpath((base, candidate)) != base:
            raise AssertionError("derived dir escapes sandbox base: " + candidate)
    env = {}
    for k in _ALLOWED_ENV:
        if k in ("HOME", "TMPDIR"):
            continue
        v = os.environ.get(k)
        if v is not None:
            env[k] = v
    env["PATH"] = bindir + ":" + env.get("PATH", "")
    env["OPOS_ROOT"] = root
    env["OPOS_HARDEN_TEST"] = "0"
    env["OPOS_TEST_STATE"] = statedir
    env["OPOS_DISABLE_UNITS"] = " ".join(sorted(UNITS))
    os.makedirs(home_dir, exist_ok=True)
    os.makedirs(tmpdir_dir, exist_ok=True)
    env["HOME"] = home_dir
    env["TMPDIR"] = tmpdir_dir
    env["OPOS_TEST_STUB_DIR"] = bindir
    _assert_resolves_inside_stub(env, bindir)
    return env


def run_harden(args, env, stdin_text):
    expected = env.get("OPOS_TEST_STUB_DIR")
    if not expected:
        raise AssertionError("OPOS_TEST_STUB_DIR must be set")
    path = env.get("PATH")
    if not path:
        raise AssertionError("PATH must be set")
    first = path.split(os.pathsep)[0]
    if not first or first != expected:
        raise AssertionError("PATH first entry must be OPOS_TEST_STUB_DIR")
    _assert_resolves_inside_stub(env, expected)
    return subprocess.run(
        ["bash", HARDEN] + args,
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        timeout=60,
    )


HARDEN_LIB_DIR = os.path.join(REPO, "deploy", "harden.d")


def _harden_source_paths():
    paths = [HARDEN]
    paths.extend(sorted(glob.glob(os.path.join(HARDEN_LIB_DIR, "*.sh"))))
    return paths


def _read_harden_sources():
    texts = {}
    for path in _harden_source_paths():
        with open(path, encoding="utf-8") as f:
            texts[path] = f.read()
    return texts


_VALID_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")


def _is_host_word(text):
    if not text or text[0] in "-$./":
        return False
    if "/" in text or "$" in text or "=" in text or ":" in text:
        return False
    return bool(_VALID_WORD.match(text))


def _strip_unquoted_comment(line):
    out = []
    sq = False
    dq = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\" and not sq and i + 1 < n:
            out.append(c)
            out.append(line[i + 1])
            i += 2
            continue
        if c == "'" and not dq:
            sq = not sq
            out.append(c)
        elif c == '"' and not sq:
            dq = not dq
            out.append(c)
        elif c == "#" and not sq and not dq:
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


_SHELL_OPS = (";;&", ";;", "&&", "||", "<<-", "<<", ">>", "<&", ">&", "<>", ";", "&", "|", "(", ")", "{", "}", "<", ">")

_SHELL_BOUNDARY = frozenset((";", "&", "|", "&&", "||", "(", ")", "{", "}", ";;", ";;&"))

_SHELL_REDIR = frozenset(("<", ">", "<<", "<<-", ">>", "<&", ">&", "<>", "|"))

_SHELL_KEYWORDS_COMMAND_NEXT = frozenset(("if", "then", "else", "elif", "do", "while", "until", "{", "!", "time"))

_SHELL_KEYWORDS_STMT_NEXT = frozenset(("fi", "done", "esac", "}"))

_SHELL_NON_COMMAND = frozenset((
    "if", "then", "else", "elif", "fi", "for", "in", "while", "until",
    "do", "done", "case", "esac", "select", "function", "time", "coproc",
    "!", "{", "}", "[[", "]]", "[", ":", ".", "source",
    "true", "false", "test", "echo", "printf", "read", "set", "shift",
    "trap", "return", "exit", "break", "continue", "declare", "local",
    "export", "readonly", "typeset", "unset", "eval", "exec", "command",
    "builtin", "type", "hash", "pwd", "cd", "pushd", "popd", "dirs",
    "jobs", "bg", "fg", "wait", "kill", "disown", "suspend", "ulimit",
    "umask", "shopt", "getopts", "history", "alias", "unalias", "enable",
    "help", "let", "logout", "bind", "caller", "compgen", "complete",
    "compopt", "mapfile", "run", "have_cmd",
))

_ALLOWED_DIRECT = frozenset((
    "mkdir", "chmod", "cp", "mv", "rm", "touch", "mktemp",
    "cat", "dirname", "date", "awk", "grep", "wc", "tr",
    "head", "tail", "zgrep", "uname",
))

_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_VALID_FUNC = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _shell_words(line):
    words = []
    buf = []
    quoted = False
    sq = False
    dq = False
    i = 0
    n = len(line)
    def flush():
        if buf:
            words.append(("".join(buf), not quoted))
            del buf[:]
    def flush_quoted_state():
        return False
    while i < n:
        c = line[i]
        if sq:
            if c == "'":
                sq = False
            else:
                buf.append(c)
            quoted = True
            i += 1
            continue
        if dq:
            if c == '"':
                dq = False
            elif c == "\\" and i + 1 < n:
                buf.append(line[i + 1])
                i += 2
                continue
            else:
                buf.append(c)
            quoted = True
            i += 1
            continue
        if c == "'":
            sq = True
            quoted = True
            i += 1
            continue
        if c == '"':
            dq = True
            quoted = True
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(line[i + 1])
            i += 2
            continue
        if c == "$" and i + 1 < n and line[i + 1] == "{":
            depth = 0
            j = i
            while j < n:
                if line.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if line[j] == "}":
                    depth -= 1
                    j += 1
                    if depth == 0:
                        break
                    continue
                j += 1
            end = j if depth == 0 else n
            buf.append(line[i:end])
            i = end
            continue
        if c in " \t":
            flush()
            quoted = flush_quoted_state()
            i += 1
            continue
        matched = None
        for op in _SHELL_OPS:
            if line.startswith(op, i):
                if op in ("{", "}"):
                    if buf:
                        continue
                    nxt = i + len(op)
                    if nxt < n and line[nxt] not in " \t;&|(){}<>\"'$\\":
                        continue
                matched = op
                break
        if matched is not None:
            flush()
            quoted = False
            words.append((matched, False))
            i += len(matched)
            continue
        buf.append(c)
        i += 1
    flush()
    return words


def _next_bare(words, idx):
    j = idx + 1
    while j < len(words):
        val, bare = words[j]
        if not bare and (val in _SHELL_BOUNDARY or val in _SHELL_REDIR):
            return None
        if val.startswith("-") and len(val) > 1:
            j += 1
            continue
        return val
    return None


def _shell_code_lines(text):
    kept = []
    pending = []
    current = None
    sq = False
    dq = False
    for raw in text.splitlines():
        if current is not None:
            delim, dash = current
            if raw == delim or (dash and raw.startswith("\t") and raw.lstrip("\t") == delim):
                if pending:
                    current = pending.pop(0)
                else:
                    current = None
            continue
        starts_in_quote = sq or dq
        opens = []
        i = 0
        n = len(raw)
        while i < n:
            c = raw[i]
            if sq:
                if c == "'":
                    sq = False
                i += 1
                continue
            if dq:
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == '"':
                    dq = False
                i += 1
                continue
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == "'":
                sq = True
                i += 1
                continue
            if c == '"':
                dq = True
                i += 1
                continue
            if c == "#":
                break
            if c == "<" and raw.startswith("<<", i):
                m = re.match(r"<<(-?)\s*\\?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", raw[i:])
                if m:
                    opens.append((m.group(2), m.group(1) == "-"))
                    i += m.end()
                    continue
            i += 1
        if starts_in_quote:
            continue
        pending.extend(opens)
        kept.append(raw)
        if current is None and pending:
            current = pending.pop(0)
    return kept


def _harden_defined_functions(source_texts):
    names = set()
    for text in source_texts.values():
        for line in _shell_code_lines(text):
            m = re.search(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\b", line)
            if m:
                names.add(m.group(1))
            m = re.search(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)", line)
            if m:
                names.add(m.group(1))
    return names


def _collect_harden_host_words(text):
    found = set()
    for m in re.finditer(r"\$\{OPOS_(?:NFT|IPTABLES_LEGACY|IP6TABLES_LEGACY)[A-Z_]*:-(.*?)\}", text):
        default = m.group(1).strip().strip("'\"").strip()
        if default:
            first = default.split()[0].strip("'\"")
            if _is_host_word(first):
                found.add(first)
    for raw in _shell_code_lines(text):
        code = _strip_unquoted_comment(raw)
        words = _shell_words(code)
        for idx in range(len(words)):
            val, bare = words[idx]
            if not bare:
                continue
            target = None
            if val == "run" or val == "have_cmd":
                target = _next_bare(words, idx)
            elif val == "command" and idx + 1 < len(words) and words[idx + 1][0] == "-v":
                target = _next_bare(words, idx + 1)
            if target is not None and _is_host_word(target):
                found.add(target)
    return found


def _missing_harden_host_words(source_texts=None):
    if source_texts is None:
        source_texts = _read_harden_sources()
    have = set()
    for text in source_texts.values():
        have.update(_collect_harden_host_words(text))
    return sorted(have - set(_harden_mutating_commands()))


class HardenCodeLinesTest(unittest.TestCase):
    def test_skips_multiline_quotes(self):
        qcmd = "opos-" + "quoted-" + "command"
        with self.subTest(kind="single"):
            text = "\n".join(["value='start", "run " + qcmd, "end'", "run systemctl"])
            self.assertEqual(_shell_code_lines(text), ["value='start", "run systemctl"])
        with self.subTest(kind="double"):
            dq = chr(34)
            bs = chr(92)
            opening = "value=" + dq + "start"
            middle = "mid " + bs + dq + " # <<EOF"
            closing = "end" + dq
            text = "\n".join([opening, middle, closing, "run systemctl"])
            self.assertEqual(_shell_code_lines(text), [opening, "run systemctl"])
        with self.subTest(kind="heredoc-opener-in-quotes"):
            text = "\n".join(["value='<<EOF'", "run systemctl"])
            self.assertEqual(_shell_code_lines(text), ["value='<<EOF'", "run systemctl"])

    def test_quote_state_resets_between_calls(self):
        _shell_code_lines("value='start")
        self.assertEqual(_shell_code_lines("run systemctl"), ["run systemctl"])

    def test_skips_heredoc_bodies(self):
        before = "echo before"
        after = "echo after"
        fake = "stray_func() {"
        openers = [
            "cat << EOF",
            "cat << 'EOF'",
            'cat << "EOF"',
            "cat << \\EOF",
            "cat <<- EOF",
        ]
        for opener in openers:
            with self.subTest(opener=opener):
                if "<<-" in opener:
                    text = "\n".join([before, opener, fake, "\tEOF", after])
                else:
                    text = "\n".join([before, opener, fake, "\tEOF", "EOF", after])
                self.assertEqual(_shell_code_lines(text), [before, opener, after])
        commented = "# cat << EOF"
        following = "echo hi"
        self.assertEqual(_shell_code_lines(commented + "\n" + following), [commented, following])
        unterminated = "cat << EOF\n" + fake + "\n"
        self.assertEqual(_shell_code_lines(unterminated), ["cat << EOF"])
        self.assertEqual(_shell_code_lines(following), [following])
        qcmd = "opos-" + "quoted-" + "command"
        text = "\n".join(["cat <<EOF", "run " + qcmd, "EOF", "run systemctl"])
        self.assertEqual(_shell_code_lines(text), ["cat <<EOF", "run systemctl"])


class HardenCommandInventoryTest(unittest.TestCase):
    def test_harden_inventory_covers_host_commands(self):
        missing = _missing_harden_host_words(_read_harden_sources())
        self.assertEqual(missing, [], msg="add stub coverage for: " + ", ".join(missing))

    def test_harden_inventory_rejects_new_source_command(self):
        base = _read_harden_sources()
        self.assertIn(HARDEN, base)
        libs = sorted(p for p in base if p != HARDEN)
        self.assertTrue(libs)
        for path, label in ((HARDEN, "main"), (libs[0], "lib")):
            with self.subTest(where=label):
                mod = dict(base)
                mod[path] = base[path] + "\nrun opos-unapproved-host-command\n"
                self.assertEqual(_missing_harden_host_words(mod), ["opos-unapproved-host-command"])

    def test_harden_inventory_rejects_quoted_source_command(self):
        base = _read_harden_sources()
        self.assertIn(HARDEN, base)
        cmd = "opos-" + "unapproved-" + "host-command"
        for prefix in ("run", "run --", "have_cmd", "command -v"):
            for quote in ("'", '"'):
                with self.subTest(prefix=prefix, quote=quote):
                    mod = dict(base)
                    mod[HARDEN] = base[HARDEN] + "\n" + prefix + " " + quote + cmd + quote + "\n"
                    self.assertEqual(_missing_harden_host_words(mod), [cmd])
        self.assertIsNone(_next_bare(_shell_words("run ; nft"), 0))
        self.assertIsNone(_next_bare(_shell_words("run > nft"), 0))

    def test_shell_words_preserves_parameter_expansions_and_brace_boundaries(self):
        db = "$" + "{"
        rb = "}"
        exp1 = db + "OPOS_" + "NFT:-nft" + rb
        exp2 = db + "OUTER:-" + db + "INNER:-nft" + rb + rb
        exp3 = db + "CMD:-nft ; fallback" + rb
        self.assertEqual(_shell_words("run " + exp1), [("run", True), (exp1, True)])
        self.assertEqual(_next_bare(_shell_words("run " + exp1), 0), exp1)
        self.assertEqual(_shell_words("run " + exp2), [("run", True), (exp2, True)])
        self.assertEqual(_shell_words("run " + exp3), [("run", True), (exp3, True)])
        arg = "foo" + "{" + "bar" + rb + "baz"
        self.assertEqual(_shell_words("run " + arg), [("run", True), (arg, True)])
        self.assertEqual(
            _shell_words("{ run nft; }"),
            [("{", False), ("run", True), ("nft", True), (";", False), ("}", False)],
        )

    def test_defined_functions_ignores_heredocs_and_multiline_quotes(self):
        pre_func = "real_before_func"
        pre_paren = "real_before_paren"
        fake_func = "fake_inside_func"
        fake_paren = "fake_inside_paren"
        post_func = "real_after_func"
        post_paren = "real_after_paren"
        want = {pre_func, pre_paren, post_func, post_paren}
        pre = ["function " + pre_func, pre_paren + "()"]
        fake = ["function " + fake_func, fake_paren + "()"]
        post = ["function " + post_func, post_paren + "()"]
        with self.subTest(kind="unquoted"):
            text = "\n".join(pre + ["cat <<EOF"] + fake + ["EOF"] + post)
            self.assertEqual(_harden_defined_functions({"s": text}), want)
        with self.subTest(kind="quoted"):
            text = "\n".join(pre + ["cat <<'EOF'"] + fake + ["EOF"] + post)
            self.assertEqual(_harden_defined_functions({"s": text}), want)
        with self.subTest(kind="dash"):
            text = "\n".join(pre + ["cat <<-EOF", "\t" + fake[0], "\t" + fake[1], "\tEOF"] + post)
            self.assertEqual(_harden_defined_functions({"s": text}), want)
        with self.subTest(kind="single"):
            text = "\n".join(pre + ["value='start"] + fake + ["end'"] + post)
            self.assertEqual(_harden_defined_functions({"s": text}), want)
        with self.subTest(kind="double"):
            dq = chr(34)
            text = "\n".join(pre + ["value=" + dq + "start"] + fake + ["end" + dq] + post)
            self.assertEqual(_harden_defined_functions({"s": text}), want)


class ServicesRollbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        self.bindir = os.path.join(self.tmp.name, "bin")
        self.statedir = os.path.join(self.tmp.name, "state")
        os.makedirs(self.root)
        os.makedirs(self.bindir)
        os.makedirs(self.statedir)
        with open(os.path.join(self.statedir, "all"), "w", encoding="utf-8") as f:
            for unit in UNITS:
                f.write(unit + "\n")
        for unit, (enabled, active) in UNITS.items():
            if enabled:
                open(os.path.join(self.statedir, "enabled_" + unit), "w").close()
            if active:
                open(os.path.join(self.statedir, "active_" + unit), "w").close()
        with open(os.path.join(self.bindir, "systemctl"), "w", encoding="utf-8") as f:
            f.write(SYSTEMCTL_DOUBLE)
        os.chmod(os.path.join(self.bindir, "systemctl"),
                 os.stat(os.path.join(self.bindir, "systemctl")).st_mode
                 | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        with open(os.path.join(self.bindir, "id"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho 0\n")
        os.chmod(os.path.join(self.bindir, "id"),
                 os.stat(os.path.join(self.bindir, "id")).st_mode
                 | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        for cmd in _harden_mutating_commands() + ("chmod",):
            if cmd in ("systemctl", "id"):
                continue
            stub_path = os.path.join(self.bindir, cmd)
            if not os.path.exists(stub_path):
                with open(stub_path, "w", encoding="utf-8") as f:
                    f.write("#!/bin/sh\n")
                    f.write("printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$OPOS_TEST_STATE/calls.log\"\n")
                    f.write("printf '%s\\n' \"$HOME\" >> \"$OPOS_TEST_STATE/observed_home\"\n")
                    f.write("printf '%s\\n' \"$TMPDIR\" >> \"$OPOS_TEST_STATE/observed_tmpdir\"\n")
                    f.write("cmd=\"$(basename \"$0\")\"\n")
                    f.write("if [ \"$cmd\" = \"chmod\" ] && [ \"$#\" -eq 2 ] && [ \"$1\" = \"0700\" ]; then\n")
                    f.write("  tgt=\"$2\"\n")
                    f.write("  base=\"$OPOS_ROOT/var/backups/opos-harden\"\n")
                    f.write("  if [ -d \"$tgt\" ]; then\n")
                    f.write("    parent=\"$(dirname \"$tgt\")\"\n")
                    f.write("    if [ \"$parent\" = \"$base\" ]; then\n")
                    f.write("      exit 0\n")
                    f.write("    fi\n")
                    f.write("  fi\n")
                    f.write("fi\n")
                    f.write("printf 'unexpected command: %s %s\\n' \"$cmd\" \"$*\" >&2\n")
                    f.write("exit 97\n")
                os.chmod(stub_path, os.stat(stub_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.env = _build_env(self.bindir, self.root, self.statedir, self.tmp.name)
        self.calls = os.path.join(self.statedir, "calls.log")
        self.env["OPOS_TEST_CALLS"] = self.calls

    def tearDown(self):
        self.tmp.cleanup()

    def assert_env_allowlisted(self, env):
        allowed = _ALLOWED_ENV + ("OPOS_ROOT", "OPOS_HARDEN_TEST", "OPOS_TEST_STATE", "OPOS_DISABLE_UNITS")
        for k in env:
            if k == "OPOS_TEST_STUB_DIR":
                self.assertEqual(env[k], self.bindir, msg=k + " leaked into subprocess env")
                continue
            if k == "OPOS_TEST_CALLS":
                self.assertIs(env, self.env, msg=k + " leaked into subprocess env")
                self.assertEqual(env[k], self.calls, msg=k + " leaked into subprocess env")
                continue
            self.assertIn(k, allowed, msg=k + " leaked into subprocess env")

    def state_of(self, unit):
        enabled = os.path.exists(os.path.join(self.statedir, "enabled_" + unit))
        active = os.path.exists(os.path.join(self.statedir, "active_" + unit))
        return (enabled, active)

    def manifest_text(self):
        base = os.path.join(self.root, "var", "backups", "opos-harden")
        names = sorted(os.listdir(base))
        with open(os.path.join(base, names[-1], "MANIFEST"), encoding="utf-8") as f:
            return f.read()

    def test_apply_then_rollback_restores_all_states(self):
        apply = run_harden(["--apply", "--only", "services"], self.env, "yes-apply\n")
        self.assertEqual(apply.returncode, 0, msg=apply.stdout + apply.stderr)
        for unit in UNITS:
            self.assertEqual(self.state_of(unit), (False, False),
                             msg="apply must leave " + unit + " off")
        manifest = self.manifest_text().splitlines()
        # svc-ea: both changed
        self.assertIn("enabled svc-ea.service", manifest)
        self.assertIn("active svc-ea.service", manifest)
        # svc-ei: only enabled changed
        self.assertIn("enabled svc-ei.service", manifest)
        self.assertNotIn("inactive svc-ei.service", manifest)
        # svc-da: only active changed
        self.assertIn("active svc-da.service", manifest)
        self.assertNotIn("disabled svc-da.service", manifest)
        # svc-di: no change, no manifest lines
        self.assertNotIn("disabled svc-di.service", manifest)
        self.assertNotIn("inactive svc-di.service", manifest)
        rollback = run_harden(["--rollback"], self.env, "")
        self.assertEqual(rollback.returncode, 0, msg=rollback.stdout + rollback.stderr)
        for unit, want in UNITS.items():
            self.assertEqual(self.state_of(unit), want,
                             msg="rollback must restore " + unit)

    def test_activity_assertions_reject_inactive_manifest_lines(self):
        orig = self.manifest_text

        def _fake():
            return orig().replace("active svc-ea.service", "inactive svc-ea.service").replace("active svc-da.service", "inactive svc-da.service")

        with mock.patch.object(self, "manifest_text", side_effect=_fake):
            with self.assertRaisesRegex(AssertionError, "active svc-ea.service"):
                self.test_apply_then_rollback_restores_all_states()

    def test_owner_drift_preserved_after_rollback(self):
        apply = run_harden(["--apply", "--only", "services"], self.env, "yes-apply\n")
        self.assertEqual(apply.returncode, 0, msg=apply.stdout + apply.stderr)
        open(os.path.join(self.statedir, "enabled_svc-di.service"), "w").close()
        open(os.path.join(self.statedir, "active_svc-di.service"), "w").close()
        for marker in ("enabled_svc-ea.service", "active_svc-ea.service"):
            path = os.path.join(self.statedir, marker)
            if os.path.exists(path):
                os.remove(path)
        self.assertEqual(self.state_of("svc-di.service"), (True, True))
        self.assertEqual(self.state_of("svc-ea.service"), (False, False))
        rollback = run_harden(["--rollback"], self.env, "")
        self.assertEqual(rollback.returncode, 0, msg=rollback.stdout + rollback.stderr)
        self.assertEqual(self.state_of("svc-di.service"), (True, True))
        self.assertEqual(self.state_of("svc-ei.service"), (True, False))
        self.assertEqual(self.state_of("svc-ea.service"), (True, True))
        self.assertEqual(self.state_of("svc-da.service"), (False, True))

    def test_rollback_rejects_active_path_traversal(self):
        backup = os.path.join(self.root, "var", "backups", "opos-harden", "20260101T000000Z")
        os.makedirs(backup, exist_ok=True)
        with open(os.path.join(backup, "MANIFEST"), "w", encoding="utf-8") as f:
            f.write("active ../x\n")
        proc = run_harden(["--rollback"], self.env, "")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("bad manifest entry", proc.stderr)
        self.assertNotIn("systemctl", self.stub_calls())

    def test_owner_active_drift_preserved_after_rollback(self):
        apply = run_harden(["--apply", "--only", "services"], self.env, "yes-apply\n")
        self.assertEqual(apply.returncode, 0, msg=apply.stdout + apply.stderr)
        self.assertEqual(self.state_of("svc-ei.service"), (False, False))
        # Simulate owner starting a unit that hardening only changed enablement for
        open(os.path.join(self.statedir, "active_svc-ei.service"), "w").close()
        rollback = run_harden(["--rollback"], self.env, "")
        self.assertEqual(rollback.returncode, 0, msg=rollback.stdout + rollback.stderr)
        # Owner start must persist because hardening recorded no active change for svc-ei
        self.assertEqual(self.state_of("svc-ei.service"), (True, True))
        # Other units restored to original
        self.assertEqual(self.state_of("svc-ea.service"), (True, True))
        self.assertEqual(self.state_of("svc-da.service"), (False, True))
        self.assertEqual(self.state_of("svc-di.service"), (False, False))

    def test_owner_enable_drift_preserved_after_rollback_for_svc_da(self):
        apply = run_harden(["--apply", "--only", "services"], self.env, "yes-apply\n")
        self.assertEqual(apply.returncode, 0, msg=apply.stdout + apply.stderr)
        # Simulate owner enabling a unit that hardening only stopped
        open(os.path.join(self.statedir, "enabled_svc-da.service"), "w").close()
        rollback = run_harden(["--rollback"], self.env, "")
        self.assertEqual(rollback.returncode, 0, msg=rollback.stdout + rollback.stderr)
        # Rollback must restore active but preserve owner's enablement
        self.assertEqual(self.state_of("svc-da.service"), (True, True))
        # Other units restored to original
        self.assertEqual(self.state_of("svc-ea.service"), (True, True))
        self.assertEqual(self.state_of("svc-ei.service"), (True, False))
        self.assertEqual(self.state_of("svc-di.service"), (False, False))

    def test_owner_start_drift_preserved_after_rollback_for_svc_di(self):
        apply = run_harden(["--apply", "--only", "services"], self.env, "yes-apply\n")
        self.assertEqual(apply.returncode, 0, msg=apply.stdout + apply.stderr)
        # Simulate owner starting a unit that hardening never touched
        open(os.path.join(self.statedir, "active_svc-di.service"), "w").close()
        rollback = run_harden(["--rollback"], self.env, "")
        self.assertEqual(rollback.returncode, 0, msg=rollback.stdout + rollback.stderr)
        # Owner start must persist because hardening recorded no change for svc-di
        self.assertEqual(self.state_of("svc-di.service"), (False, True))
        # Other units restored to original
        self.assertEqual(self.state_of("svc-ea.service"), (True, True))
        self.assertEqual(self.state_of("svc-ei.service"), (True, False))
        self.assertEqual(self.state_of("svc-da.service"), (False, True))

    def test_subprocess_env_is_allowlisted_and_mutating_commands_resolve_inside_stub(self):
        leak_key = "OPOS_TEST_" + "LEAK_MARKER"
        os.environ[leak_key] = "LEAKED-" + "MARKER"
        try:
            fresh = _build_env(self.bindir, self.root, self.statedir, self.tmp.name)
            self.assertNotIn(leak_key, fresh)
            allowed = set(_ALLOWED_ENV) | {"OPOS_ROOT", "OPOS_HARDEN_TEST", "OPOS_TEST_STATE", "OPOS_DISABLE_UNITS", "OPOS_TEST_STUB_DIR"}
            for k in fresh:
                self.assertIn(k, allowed, msg=k + " leaked into subprocess env")
            _assert_resolves_inside_stub(fresh, self.bindir)
        finally:
            os.environ.pop(leak_key, None)

    def test_guard_rejects_command_resolving_outside_stub(self):
        outside_bin = os.path.join(self.tmp.name, "outside-bin")
        os.makedirs(outside_bin, exist_ok=True)
        outside_id = os.path.join(outside_bin, "id")
        with open(outside_id, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho 0\n")
        os.chmod(outside_id, os.stat(outside_id).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        copied_env = dict(self.env)
        copied_env["PATH"] = outside_bin + os.pathsep + copied_env["PATH"]
        with self.assertRaisesRegex(AssertionError, "id must resolve inside stub:"):
            _assert_resolves_inside_stub(copied_env, self.bindir)

    def test_env_allowlist_rejects_unexpected_opos_key(self):
        fresh = _build_env(self.bindir, self.root, self.statedir, self.tmp.name)
        bad_key = "OPOS_UNEXPECTED_" + "HOST_SECRET"
        fresh[bad_key] = "dummy"
        with mock.patch(__name__ + "._build_env", return_value=fresh):
            with self.assertRaisesRegex(AssertionError, bad_key):
                self.test_subprocess_env_is_allowlisted_and_mutating_commands_resolve_inside_stub()

    def test_home_and_tmpdir_are_isolated_under_temp_root(self):
        home = self.env.get("HOME")
        self.assertIsNotNone(home)
        self.assertTrue(
            home.startswith(self.tmp.name + os.sep)
            or home.startswith(self.root + os.sep)
        )
        host_home = os.environ.get("HOME")
        if host_home is not None:
            self.assertNotEqual(home, host_home)
        self.assertTrue(os.path.isdir(home))
        tmpdir = self.env.get("TMPDIR")
        self.assertIsNotNone(tmpdir)
        self.assertTrue(
            tmpdir.startswith(self.tmp.name + os.sep)
            or tmpdir.startswith(self.root + os.sep)
        )
        host_tmpdir = os.environ.get("TMPDIR")
        if host_tmpdir is not None:
            self.assertNotEqual(tmpdir, host_tmpdir)
        self.assertTrue(os.path.isdir(tmpdir))

    def test_sandbox_base_keeps_derived_dirs_inside_temp(self):
        tmp_base = self.tmp.name
        for variant in (tmp_base, self.root + os.sep):
            fresh = _build_env(self.bindir, variant, self.statedir, tmp_base)
            self.assertTrue(fresh["HOME"].startswith(tmp_base + os.sep))
            self.assertTrue(fresh["TMPDIR"].startswith(tmp_base + os.sep))
            self.assertTrue(os.path.isdir(fresh["HOME"]))
            self.assertTrue(os.path.isdir(fresh["TMPDIR"]))
        outside = os.path.dirname(tmp_base)
        with self.assertRaises(AssertionError):
            _build_env(self.bindir, self.root, self.statedir, outside)
        with self.assertRaises(AssertionError):
            _build_env(self.bindir, self.root, self.statedir, os.path.sep)

    def test_child_process_sees_isolated_home_and_tmpdir(self):
        home = self.env["HOME"]
        tmpdir = self.env["TMPDIR"]
        result = run_harden(["--apply", "--only", "services"], self.env, "yes-apply\n")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        home_log = os.path.join(self.statedir, "observed_home")
        tmpdir_log = os.path.join(self.statedir, "observed_tmpdir")
        self.assertTrue(os.path.exists(home_log))
        self.assertTrue(os.path.exists(tmpdir_log))
        with open(home_log, encoding="utf-8") as f:
            homes = [line.rstrip("\n") for line in f]
        with open(tmpdir_log, encoding="utf-8") as f:
            tmpdirs = [line.rstrip("\n") for line in f]
        self.assertGreater(len(homes), 0)
        self.assertGreater(len(tmpdirs), 0)
        for observed in homes:
            self.assertEqual(observed, home)
        for observed in tmpdirs:
            self.assertEqual(observed, tmpdir)
        host_home = os.environ.get("HOME")
        if host_home is not None:
            for observed in homes:
                self.assertNotEqual(observed, host_home)
        host_tmpdir = os.environ.get("TMPDIR")
        if host_tmpdir is not None:
            for observed in tmpdirs:
                self.assertNotEqual(observed, host_tmpdir)

    def test_stray_test_env_does_not_force_test_mode(self):
        env = dict(self.env)
        env["TEST"] = "1"
        env["OPOS_HARDEN_TEST"] = "0"
        apply = run_harden(["--apply", "--only", "services"], env, "yes-apply\n")
        self.assertEqual(apply.returncode, 0, msg=apply.stdout + apply.stderr)
        for unit in UNITS:
            self.assertEqual(self.state_of(unit), (False, False),
                             msg="stray TEST must not force test mode for " + unit)

    def test_stray_test_env_without_override_keeps_real_root_mode(self):
        env = dict(self.env)
        env["TEST"] = "1"
        env.pop("OPOS_HARDEN_TEST", None)
        env["OPOS_ROOT"] = "/"
        proc = run_harden(["--check", "--only", "services"], env, "")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("TEST is ignored; use OPOS_HARDEN_TEST instead", proc.stderr)
        self.assertIn("would run: systemctl disable --now -- svc-ea.service", proc.stdout)
        for unit, want in UNITS.items():
            self.assertEqual(self.state_of(unit), want,
                             msg="stray TEST must not suppress inspection for " + unit)

    def test_invalid_harden_test_value_rejected(self):
        env = dict(self.env)
        env["OPOS_HARDEN_TEST"] = "2"
        proc = run_harden(["--apply", "--only", "services"], env, "yes-apply\n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("must be 0 or 1", proc.stdout + proc.stderr)
        for unit, want in UNITS.items():
            self.assertEqual(self.state_of(unit), want,
                             msg="rejection must precede mutation for " + unit)
        calls_path = os.path.join(self.statedir, "calls.log")
        calls = []
        if os.path.exists(calls_path):
            with open(calls_path, encoding="utf-8") as f:
                calls = f.read().splitlines()
        mutating = [ln for ln in calls if ln.strip() and ln.strip().split()[0] not in ("is-enabled", "is-active", "list-unit-files")]
        self.assertEqual(mutating, [], msg="rejection must precede mutating stub calls: " + "\n".join(calls))

    def test_f004_effective_sshd_probe_gates_reload(self):
        user = "droi" + "dian"
        ssh_dir = os.path.join(self.root, "home", user, "".join([".", "ssh"]))
        auth_name = "authorized_" + "keys"
        auth = os.path.join(ssh_dir, auth_name)
        os.makedirs(ssh_dir, exist_ok=True)
        key_type = "ssh-" + "ed25519"
        key_b64 = "A" * 68
        with open(auth, "w", encoding="utf-8") as f:
            f.write(key_type + " " + key_b64 + " test\n")
        conf_rel = os.path.join("etc", "ssh", "sshd_config.d", "10-opos-hardening.conf")
        conf_abs = os.path.join(self.root, conf_rel)
        orig_bytes = ("Port " + "22\n").encode("utf-8")
        backup_base = os.path.join(self.root, "var", "backups", "opos-harden")
        with open(os.path.join(self.bindir, "passwd"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
            f.write("printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$OPOS_TEST_STATE/calls.log\"\n")
            f.write("exit 0\n")
        with open(os.path.join(self.bindir, "chmod"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
            f.write("printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$OPOS_TEST_STATE/calls.log\"\n")
            f.write("/bin/chmod \"$@\"\n")
        for probe_ok in (False, True, None):
            for had_dropin in (False, True):
                with self.subTest(probe_ok=probe_ok, had_dropin=had_dropin):
                    if os.path.exists(self.calls):
                        os.remove(self.calls)
                    shutil.rmtree(backup_base, ignore_errors=True)
                    if os.path.exists(conf_abs):
                        os.remove(conf_abs)
                    if had_dropin:
                        os.makedirs(os.path.dirname(conf_abs), exist_ok=True)
                        with open(conf_abs, "wb") as f:
                            f.write(orig_bytes)
                    with open(os.path.join(self.bindir, "sshd"), "w", encoding="utf-8") as f:
                        f.write("#!/bin/sh\n")
                        f.write("printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$OPOS_TEST_STATE/calls.log\"\n")
                        f.write("for a in \"$@\"; do\n")
                        f.write("  if [ \"$a\" = \"-T\" ]; then\n")
                        if probe_ok:
                            f.write("    printf '%s\\n' 'passwordauthentication no' 'kbdinteractiveauthentication no' 'pubkeyauthentication yes' 'authenticationmethods publickey' 'usedns no'\n")
                            f.write("    exit 0\n")
                        else:
                            f.write("    exit 1\n")
                        f.write("  fi\n")
                        f.write("done\n")
                        f.write("exit 0\n")
                    os.chmod(os.path.join(self.bindir, "sshd"), 0o755)
                    env = dict(self.env)
                    env["SSH_CONNECTION"] = "192.0.2.10 41000 198.51.100.20 22"
                    if probe_ok is None:
                        bash_env = os.path.join(self.statedir, "bash_env_no_sshd")
                        with open(bash_env, "w", encoding="utf-8") as f:
                            f.write("command() {\n")
                            f.write('  if [ "$1" = "-v" ] && [ "$2" = "sshd" ]; then\n')
                            f.write("    return 1\n")
                            f.write("  fi\n")
                            f.write('  builtin command "$@"\n')
                            f.write("}\n")
                        env["BASH_ENV"] = bash_env
                    proc = run_harden(["--apply", "--only", "ssh"], env, "yes-apply\n")
                    calls = self.stub_calls().splitlines()
                    has_t = any(ln.strip() == "sshd -t" for ln in calls)
                    has_big_t = any(ln.strip().startswith("sshd -T") for ln in calls)
                    reloaded = any("reload" in ln for ln in calls)
                    passwd_called = any(ln.strip().split()[0] == "passwd" for ln in calls if ln.strip())
                    if probe_ok is None:
                        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr + "\n" + "\n".join(calls))
                        self.assertIn("sshd not found, change reverted", proc.stdout + proc.stderr)
                        if had_dropin:
                            with open(conf_abs, "rb") as f:
                                self.assertEqual(f.read(), orig_bytes)
                        else:
                            self.assertFalse(os.path.exists(conf_abs))
                        self.assertFalse(any(ln.strip().split()[0] == "sshd" for ln in calls if ln.strip()), msg="must not invoke sshd: " + "\n".join(calls))
                        self.assertFalse(reloaded, msg="must not reload: " + "\n".join(calls))
                        self.assertFalse(passwd_called, msg="must not passwd: " + "\n".join(calls))
                    elif not probe_ok:
                        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr + "\n" + "\n".join(calls))
                        self.assertTrue(has_t, msg="missing sshd -t: " + "\n".join(calls))
                        self.assertTrue(has_big_t, msg="missing sshd -T: " + "\n".join(calls))
                        if had_dropin:
                            with open(conf_abs, "rb") as f:
                                self.assertEqual(f.read(), orig_bytes)
                        else:
                            self.assertFalse(os.path.exists(conf_abs))
                        self.assertFalse(reloaded, msg="must not reload: " + "\n".join(calls))
                        self.assertFalse(passwd_called, msg="must not passwd: " + "\n".join(calls))
                    else:
                        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr + "\n" + "\n".join(calls))
                        self.assertTrue(has_big_t, msg="missing sshd -T: " + "\n".join(calls))
                        self.assertTrue(os.path.exists(conf_abs))
                        with open(conf_abs, encoding="utf-8") as f:
                            self.assertIn("PasswordAuthentication no", f.read())
                        idx_t = next(i for i, ln in enumerate(calls) if ln.strip().startswith("sshd -T -C"))
                        idx_reload = next(i for i, ln in enumerate(calls) if "reload" in ln)
                        self.assertLess(idx_t, idx_reload, msg="\n".join(calls))

    def test_f005_ssh_effective_context(self):
        user = "droi" + "dian"
        ssh_dir = os.path.join(self.root, "home", user, "".join([".", "ssh"]))
        auth = os.path.join(ssh_dir, "authorized_" + "keys")
        os.makedirs(ssh_dir, exist_ok=True)
        key_type = "ssh-" + "ed25519"
        key_b64 = "A" * 68
        with open(auth, "w", encoding="utf-8") as f:
            f.write(key_type + " " + key_b64 + " test\n")
        conf_rel = os.path.join("etc", "ssh", "sshd_config.d", "10-opos-hardening.conf")
        conf_abs = os.path.join(self.root, conf_rel)
        sentinel = ("Port " + "22\n").encode("utf-8")
        backup_base = os.path.join(self.root, "var", "backups", "opos-harden")
        with open(os.path.join(self.bindir, "passwd"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
            f.write("printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$OPOS_TEST_STATE/calls.log\"\n")
            f.write("exit 0\n")
        with open(os.path.join(self.bindir, "chmod"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
            f.write("printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$OPOS_TEST_STATE/calls.log\"\n")
            f.write("/bin/chmod \"$@\"\n")
        good = ["passwordauthentication no", "kbdinteractiveauthentication no", "pubkeyauthentication yes", "authenticationmethods publickey", "usedns no"]
        bad = ["passwordauthentication yes", "kbdinteractiveauthentication no", "pubkeyauthentication yes", "authenticationmethods publickey", "usedns no"]

        def write_sshd(mode):
            with open(os.path.join(self.bindir, "sshd"), "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
                f.write("printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$OPOS_TEST_STATE/calls.log\"\n")
                f.write("for a in \"$@\"; do\n")
                f.write("  if [ \"$a\" = \"-T\" ]; then\n")
                if mode == "fail":
                    f.write("    exit 1\n")
                elif mode == "bad":
                    f.write("    printf '%s\\n' " + " ".join("'" + line + "'" for line in bad) + "\n")
                    f.write("    exit 0\n")
                else:
                    f.write("    printf '%s\\n' " + " ".join("'" + line + "'" for line in good) + "\n")
                    f.write("    exit 0\n")
                f.write("  fi\n")
                f.write("done\n")
                f.write("exit 0\n")
            os.chmod(os.path.join(self.bindir, "sshd"), 0o755)

        def reset():
            if os.path.exists(self.calls):
                os.remove(self.calls)
            shutil.rmtree(backup_base, ignore_errors=True)
            os.makedirs(os.path.dirname(conf_abs), exist_ok=True)
            with open(conf_abs, "wb") as f:
                f.write(sentinel)

        def context_arg(client, cport, server, sport):
            return "sshd -T -C user=" + user + ",host=" + client + ",addr=" + client + ",laddr=" + server + ",lport=" + sport

        for conn, client, cport, server, sport in (
            ("192.0.2.10 41000 198.51.100.20 22", "192.0.2.10", "41000", "198.51.100.20", "22"),
            ("203.0.113.30 42000 192.0.2.40 2222", "203.0.113.30", "42000", "192.0.2.40", "2222"),
        ):
            with self.subTest(conn=conn):
                reset()
                write_sshd("good")
                env = dict(self.env)
                env["SSH_CONNECTION"] = conn
                proc = run_harden(["--apply", "--only", "ssh"], env, "yes-apply\n")
                calls = self.stub_calls().splitlines()
                stripped = [ln.strip() for ln in calls]
                self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr + "\n" + "\n".join(calls))
                self.assertIn(context_arg(client, cport, server, sport), stripped, msg="\n".join(calls))
                self.assertTrue(os.path.exists(conf_abs))
                with open(conf_abs, encoding="utf-8") as f:
                    self.assertIn("PasswordAuthentication no", f.read())
                idx_eff = next(i for i, ln in enumerate(calls) if ln.strip().startswith("sshd -T -C"))
                idx_reload = next(i for i, ln in enumerate(calls) if "reload" in ln)
                self.assertLess(idx_eff, idx_reload, msg="\n".join(calls))
        valid = "192.0.2.10 41000 198.51.100.20 22"
        for name, conn, mode in (
            ("missing", None, "good"),
            ("malformed", "bogus", "good"),
            ("probe-failed", valid, "fail"),
            ("password-enabled", valid, "bad"),
        ):
            with self.subTest(name=name):
                reset()
                write_sshd(mode)
                env = dict(self.env)
                env.pop("SSH_CONNECTION", None)
                if conn is not None:
                    env["SSH_CONNECTION"] = conn
                proc = run_harden(["--apply", "--only", "ssh"], env, "yes-apply\n")
                calls = self.stub_calls().splitlines()
                self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr + "\n" + "\n".join(calls))
                with open(conf_abs, "rb") as f:
                    self.assertEqual(f.read(), sentinel)
                self.assertFalse(any("reload" in ln for ln in calls), msg="\n".join(calls))
                self.assertFalse(any(ln.strip().split()[0] == "passwd" for ln in calls if ln.strip()), msg="\n".join(calls))

    def stub_calls(self):
        if not os.path.exists(self.calls):
            return ""
        with open(self.calls, encoding="utf-8") as f:
            return f.read()

    def test_harden_test_mode_invokes_no_stubs(self):
        env = dict(self.env)
        env["OPOS_HARDEN_TEST"] = "1"
        apply = run_harden(["--apply", "--only", "services"], env, "yes-apply\n")
        self.assertEqual(apply.returncode, 0, msg=apply.stdout + apply.stderr)
        for unit, want in UNITS.items():
            self.assertEqual(self.state_of(unit), want,
                             msg="test mode must not mutate " + unit)
        calls = self.stub_calls()
        combined = apply.stdout + apply.stderr
        detail = "calls=" + calls + " stdout=" + apply.stdout + " stderr=" + apply.stderr
        self.assertEqual(calls, "", msg="test mode must not invoke stubs: " + detail)
        self.assertIn("TEST", combined, msg="apply output must report test mode: " + detail)
        self.assertNotIn("chmod 0700", combined, msg="apply output must not report live chmod: " + detail)

    def test_live_apply_chmods_backup_directory(self):
        apply = run_harden(["--apply", "--only", "services"], self.env, "yes-apply\n")
        self.assertEqual(apply.returncode, 0, msg=apply.stdout + apply.stderr)
        base = os.path.join(self.root, "var", "backups", "opos-harden")
        self.assertTrue(os.path.isdir(base))
        entries = sorted(os.listdir(base))
        self.assertEqual(len(entries), 1, msg="expected single backup dir: " + str(entries))
        backup_dir = os.path.join(base, entries[0])
        self.assertTrue(os.path.isfile(os.path.join(backup_dir, "MANIFEST")))
        calls = self.stub_calls().splitlines()
        expected = "chmod 0700 " + backup_dir
        self.assertIn(expected, calls, msg="missing chmod entry: " + str(calls))
        self.assertEqual(calls.count(expected), 1, msg="expected one chmod entry: " + str(calls))

    def test_generic_stubs_reject_unexpected_invocations(self):
        for cmd in _harden_mutating_commands():
            if cmd in ("systemctl", "id"):
                continue
            exe = os.path.join(self.bindir, cmd)
            proc = subprocess.run([exe, "--unexpected"], env=self.env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 97, msg=proc.stdout + proc.stderr)
            self.assertIn("unexpected command:", proc.stderr)
            self.assertIn(cmd + " --unexpected", proc.stderr)
            self.assertIn(cmd + " --unexpected", self.stub_calls().splitlines())
        tgt = os.path.join(self.root, "var", "backups", "opos-harden", "20261029T000000Z")
        os.makedirs(tgt, exist_ok=True)
        proc = subprocess.run([os.path.join(self.bindir, "chmod"), "0700", tgt], env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("chmod 0700 " + tgt, self.stub_calls().splitlines())

    def test_run_harden_rejects_untrusted_stub_path_before_subprocess(self):
        other = os.path.join(self.tmp.name, "other_bin")
        shutil.copytree(self.bindir, other)
        cases = {
            "missing PATH": lambda e: e.pop("PATH", None),
            "empty PATH": lambda e: e.update({"PATH": ""}),
            "wrong first entry": lambda e: e.update({"PATH": other + os.pathsep + e["PATH"]}),
            "missing OPOS_TEST_STUB_DIR": lambda e: e.pop("OPOS_TEST_STUB_DIR", None),
            "empty OPOS_TEST_STUB_DIR": lambda e: e.update({"OPOS_TEST_STUB_DIR": ""}),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                env = dict(self.env)
                mutate(env)
                with mock.patch(__name__ + ".subprocess.run") as mr:
                    with self.assertRaises(AssertionError):
                        run_harden(["--apply", "--only", "services"], env, "yes-apply\n")
                    mr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
