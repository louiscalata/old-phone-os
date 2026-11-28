# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import os
import pathlib
import re
import subprocess
import sys
import unittest


def read_install_sh() -> str:
    candidates = [
        pathlib.Path(__file__).resolve().parents[1] / "install.sh",
        pathlib.Path("install.sh"),
    ]
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("install.sh not found in expected locations")


def uninstall_restore_block(text):
    start = text.index("do_uninstall() {")
    marker = text.index("printf '==> restore summary:", start)
    end = text.index("\n", marker) + 1
    return text[start:end]


def run_sshd_dropin_restore(tmp_path, sshd_exit):
    text = read_install_sh()
    start = text.index('    _sshd="skipped"')
    end = text.index("\n  fi\n  opos_owned_path", start)
    body = text[start:end]
    prod = "/etc/ssh/" + "sshd_config"
    prod_dropin = prod + ".d/60-opos.conf"
    home = tmp_path / "home"
    opos = tmp_path / "opos"
    stubs = tmp_path / "stubs"
    home.mkdir()
    opos.mkdir()
    stubs.mkdir()
    dest = tmp_path / "dest"
    dest.write_bytes(b"dest-" + b"bytes-61")
    bak_bytes = b"bak-" + b"bytes-62"
    bak = opos / ("sshd_config" + ".bak")
    bak.write_bytes(bak_bytes)
    dropin_text = "# Managed by OPOS - sshd drop-in\n" + "X\n"
    dropin = tmp_path / "dropin"
    dropin.write_text(dropin_text)
    (opos / "sshd_dropin.expected").write_text(dropin_text)
    stat_path = stubs / "stat"
    stat_path.write_text("#!/bin/sh\n" + "printf '0 600'\n")
    stat_path.chmod(0o755)
    sshd_path = stubs / "sshd"
    sshd_path.write_text("#!/bin/sh\n" + "exit " + str(sshd_exit) + "\n")
    sshd_path.chmod(0o755)
    systemctl_path = stubs / "systemctl"
    systemctl_path.write_text("#!/bin/sh\n" + "exit 0\n")
    systemctl_path.chmod(0o755)
    script = body.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["OPOS_HOME"] = str(opos)
    env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
    return (proc, bak, bak_bytes, dest, dropin)


def prepare_fallback_restore_snippet(dropin, dest):
    text = read_install_sh()
    start = text.index("do_uninstall() {")
    found = text.index("FOUND=0", start)
    marker = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
    end = text.index(marker, found)
    fragment = text[found:end]
    prod = "/etc/ssh/" + "sshd_config"
    prod_dropin = prod + ".d/60-opos.conf"
    rewritten = fragment.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
    joined = join_continuations(rewritten)
    pattern = r"\b(?:" + "|".join(map(re.escape, _PATH_OPS)) + r")\s"
    kept = [line for line in joined.splitlines() if writes_backup(line) or re.search(pattern, line)]
    assert_snippet_confined("\n".join(kept))
    return rewritten


def open_symlink_fd(path: str) -> int:
    if hasattr(os, "O_PATH"):
        return os.open(path, os.O_PATH | os.O_NOFOLLOW)
    if hasattr(os, "O_SYMLINK"):
        return os.open(path, os.O_RDONLY | os.O_SYMLINK)
    raise unittest.SkipTest("no supported symlink-handle mechanism")


def artifact_data(path, kind):
    if kind == "file":
        return pathlib.Path(path).read_bytes()
    if kind == "link":
        return os.readlink(path)
    return None


def run_retain_rollback(tmp_path, preamble):
    text = read_install_sh()
    names = ("opos_owned_path", "canon_file", "py_ok", "find_python", "retain_hardening_rollback")
    helpers = ""
    for name in names:
        start = text.index(name + "() {")
        end = text.index("\n}\n", start) + 3
        helpers += text[start:end] + "\n"
    (tmp_path / "clone" / "deploy").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    helper_bytes = b"".join([b"helper-", b"bytes-9"])
    (tmp_path / "clone" / "deploy" / "".join(["harden-device", ".sh"])).write_bytes(helper_bytes)
    dest = tmp_path / "home" / "".join(["opos-harden-", "rollback.sh"])
    runner = tmp_path / "run.sh"
    runner.write_text(helpers + preamble + 'retain_hardening_rollback "$1"\n')
    env = os.environ.copy()
    env.update(
        {
            "OPOS_HOME": str(tmp_path / "home"),
            "DRY": "0",
            "OPOS_DEST": str(dest),
            "OPOS_VICTIM": str(tmp_path / "victim"),
        }
    )
    proc = subprocess.run(
        ["sh", str(runner), str(tmp_path / "clone")], capture_output=True, text=True, env=env, timeout=30
    )
    return (proc, tmp_path / "home", dest, helper_bytes)


_PATH_OPS = ("rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod", "chown", "ln")


def isolated_git_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def contained_candidate(tmpdir, candidate):
    base = os.path.realpath(str(tmpdir))
    resolved = os.path.realpath(str(candidate))
    try:
        inside = os.path.commonpath([base, resolved]) == base
    except ValueError:
        inside = False
    if not inside:
        raise AssertionError("candidate escapes fixture: %r" % (candidate,))
    return resolved


def run_contained(tmpdir, candidate, argv):
    validated = contained_candidate(tmpdir, candidate)
    cmd = [validated if a == str(candidate) else a for a in argv]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _word_has_bare_glob_or_tilde(word):
    n = len(word)
    unquoted = [False] * n
    in_single = False
    in_double = False
    i = 0
    while i < n:
        c = word[i]
        if in_single:
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        unquoted[i] = True
        i += 1
    for i, c in enumerate(word):
        if not unquoted[i]:
            continue
        if c == "*" or c == "?" or c == "[":
            return True
        if c == "~":
            if i == 0:
                return True
            prev = word[i - 1]
            if prev == "=" and unquoted[i - 1]:
                return True
            if prev == ":" and unquoted[i - 1]:
                for j in range(i):
                    if word[j] == "=" and unquoted[j]:
                        return True
    return False


def strip_shell_comment(line):
    stack = [[False, False, True, None, 0]]
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        single, double, word, closer, depth = stack[-1]
        if single:
            if c == "'":
                stack[-1][0] = False
                stack[-1][2] = False
            i += 1
            continue
        if double:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                stack[-1][1] = False
                stack[-1][2] = False
                i += 1
                continue
            if c == "$" and i + 1 < n and line[i + 1] == "(":
                stack.append([False, False, True, ")", 1])
                i += 2
                continue
            if c == "`":
                stack.append([False, False, True, "`", 0])
                i += 1
                continue
            i += 1
            continue
        if c == "\\":
            i += 2
            stack[-1][2] = False
            continue
        if c == "'":
            stack[-1][0] = True
            stack[-1][2] = False
            i += 1
            continue
        if c == '"':
            stack[-1][1] = True
            stack[-1][2] = False
            i += 1
            continue
        if c == "$" and i + 1 < n and line[i + 1] == "(":
            stack.append([False, False, True, ")", 1])
            i += 2
            continue
        if closer == "`" and c == "`":
            stack.pop()
            stack[-1][2] = False
            i += 1
            continue
        if c == "`":
            stack.append([False, False, True, "`", 0])
            i += 1
            continue
        if closer == ")" and c == "(":
            stack[-1][4] += 1
            stack[-1][2] = True
            i += 1
            continue
        if closer == ")" and c == ")":
            if depth > 1:
                stack[-1][4] -= 1
                stack[-1][2] = False
            else:
                stack.pop()
                stack[-1][2] = False
            i += 1
            continue
        if c == "#":
            if word and closer == "`":
                j = i + 1
                while j < n:
                    if line[j] == "\\":
                        j += 2
                        continue
                    if line[j] == "`":
                        break
                    j += 1
                if j < n:
                    i = j
                    continue
            if word:
                return line[:i]
            stack[-1][2] = False
            i += 1
            continue
        if c in " \t;|&()<>\n":
            stack[-1][2] = True
            i += 1
            continue
        stack[-1][2] = False
        i += 1
    return line


def _heredoc_substitution_code(body):
    norm = []
    k = 0
    m = len(body)
    while k < m:
        b = body[k]
        if b == "\\" and k + 1 < m:
            nxt = body[k + 1]
            if nxt == "\n":
                k += 2
                continue
            norm.append(b)
            norm.append(nxt)
            k += 2
            continue
        norm.append(b)
        k += 1
    body = "".join(norm)
    parts = []
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "$" and i + 1 < n and body[i + 1] == "(":
            if i + 2 < n and body[i + 2] == "(":
                i += 3
                continue
            depth = 1
            j = i + 2
            buf = []
            jsingle = False
            jdouble = False
            while j < n and depth > 0:
                cj = body[j]
                if jsingle:
                    buf.append(cj)
                    if cj == "'":
                        jsingle = False
                    j += 1
                    continue
                if jdouble:
                    if cj == "\\" and j + 1 < n:
                        buf.append(cj)
                        buf.append(body[j + 1])
                        j += 2
                        continue
                    buf.append(cj)
                    if cj == '"':
                        jdouble = False
                    j += 1
                    continue
                if cj == "\\" and j + 1 < n:
                    buf.append(cj)
                    buf.append(body[j + 1])
                    j += 2
                    continue
                if cj == "'":
                    jsingle = True
                    buf.append(cj)
                    j += 1
                    continue
                if cj == '"':
                    jdouble = True
                    buf.append(cj)
                    j += 1
                    continue
                if cj == "#":
                    raise AssertionError("unsupported heredoc substitution syntax")
                if body.startswith("case", j):
                    before = j == i + 2 or body[j - 1] in " \t\n;|&()<>"
                    after = j + 4 >= n or body[j + 4] in " \t\n;|&()<>"
                    if before and after:
                        raise AssertionError("unsupported heredoc substitution syntax")
                if cj == "$" and j + 1 < n and body[j + 1] == "(":
                    if j + 2 < n and body[j + 2] == "(":
                        depth += 2
                        buf.append(cj)
                        buf.append(body[j + 1])
                        buf.append(body[j + 2])
                        j += 3
                        continue
                    depth += 1
                    buf.append(cj)
                    buf.append(body[j + 1])
                    j += 2
                    continue
                if cj == "(":
                    depth += 1
                    buf.append(cj)
                    j += 1
                    continue
                if cj == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                    buf.append(cj)
                    j += 1
                    continue
                buf.append(cj)
                j += 1
            if depth == 0:
                parts.append("".join(buf))
                i = j
                continue
            i += 2
            continue
        if c == "`":
            j = i + 1
            buf = []
            while j < n:
                if body[j] == "\\" and j + 1 < n:
                    buf.append(body[j])
                    buf.append(body[j + 1])
                    j += 2
                    continue
                if body[j] == "`":
                    break
                buf.append(body[j])
                j += 1
            if j < n:
                parts.append("".join(buf))
                i = j + 1
                continue
            i += 1
            continue
        i += 1
    return "\n".join(parts)


def mask_heredoc_bodies(text):
    lines = text.splitlines(keepends=True)
    out = []
    pending = []
    in_single = False
    in_double = False
    stack = []
    for line in lines:
        if pending:
            delim, strip_tabs, quoted, buf = pending[0]
            body = line.rstrip("\r\n")
            if strip_tabs:
                check = body.lstrip("\t")
                content = body.lstrip("\t")
            else:
                check = body
                content = body
            if check == delim:
                pending.pop(0)
                if not quoted:
                    inner = _heredoc_substitution_code("\n".join(buf))
                    if inner:
                        out.append(inner + ("\n" if line.endswith("\n") else ""))
                    else:
                        out.append("\n" if line.endswith("\n") else "")
                else:
                    out.append("\n" if line.endswith("\n") else "")
            else:
                if not quoted:
                    buf.append(content)
                out.append("\n" if line.endswith("\n") else "")
            continue
        out.append(line)
        n = len(line)
        at_word_start = True
        i = 0
        while i < n:
            c = line[i]
            if in_single:
                if c == "'":
                    in_single = False
                i += 1
                continue
            if in_double:
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == '"':
                    in_double = False
                i += 1
                continue
            if c == "\\" and i + 1 < n:
                i += 2
                at_word_start = False
                continue
            if c == "'":
                in_single = True
                at_word_start = False
                i += 1
                continue
            if c == '"':
                in_double = True
                at_word_start = False
                i += 1
                continue
            in_arith = len(stack) > 0 and stack[-1][0] == "arith"
            if not in_arith and c == "$" and i + 2 < n and line[i + 1] == "(" and line[i + 2] == "(":
                stack.append(["arith", 2])
                i += 3
                at_word_start = False
                continue
            if not in_arith and c == "(" and i + 1 < n and line[i + 1] == "(":
                stack.append(["arith", 2])
                i += 2
                at_word_start = False
                continue
            if in_arith:
                if c == "$" and i + 1 < n and line[i + 1] == "(":
                    if i + 2 < n and line[i + 2] == "(":
                        stack.append(["arith", 2])
                        i += 3
                    else:
                        stack.append(["subst", 1])
                        i += 2
                    at_word_start = False
                    continue
                if c == "(":
                    stack[-1][1] += 1
                    at_word_start = False
                    i += 1
                    continue
                if c == ")":
                    stack[-1][1] -= 1
                    if stack[-1][1] <= 0:
                        stack.pop()
                    at_word_start = False
                    i += 1
                    continue
                if c == "<" and i + 1 < n and line[i + 1] == "<":
                    i += 2
                    at_word_start = False
                    continue
            if stack and stack[-1][0] == "subst":
                if c == "$" and i + 1 < n and line[i + 1] == "(":
                    if i + 2 < n and line[i + 2] == "(":
                        stack.append(["arith", 2])
                        i += 3
                    else:
                        stack.append(["subst", 1])
                        i += 2
                    at_word_start = False
                    continue
                if c == "(":
                    stack[-1][1] += 1
                    at_word_start = False
                    i += 1
                    continue
                if c == ")":
                    stack[-1][1] -= 1
                    if stack[-1][1] <= 0:
                        stack.pop()
                    at_word_start = False
                    i += 1
                    continue
            in_arith_now = len(stack) > 0 and stack[-1][0] == "arith"
            if c == "#" and at_word_start:
                break
            if not in_arith_now and c == "<" and i + 1 < n and line[i + 1] == "<":
                if i + 2 < n and line[i + 2] == "<":
                    i += 3
                    at_word_start = False
                    continue
                j = i + 2
                strip_tabs = False
                if j < n and line[j] == "-":
                    strip_tabs = True
                    j += 1
                while j < n and line[j] in " \t":
                    j += 1
                k = j
                parts = []
                found = False
                quoted = False
                while k < n:
                    d = line[k]
                    if d == "'":
                        e = line.find("'", k + 1)
                        if e < 0:
                            break
                        parts.append(line[k + 1 : e])
                        k = e + 1
                        found = True
                        quoted = True
                        continue
                    if d == '"':
                        e = line.find('"', k + 1)
                        if e < 0:
                            break
                        parts.append(line[k + 1 : e])
                        k = e + 1
                        found = True
                        quoted = True
                        continue
                    if d == "\\" and k + 1 < n:
                        parts.append(line[k + 1])
                        k += 2
                        found = True
                        quoted = True
                        continue
                    if d in " \t;|&()<>\n":
                        break
                    parts.append(d)
                    k += 1
                    found = True
                delim = "".join(parts)
                if found and (delim or quoted):
                    pending.append((delim, strip_tabs, quoted, []))
                    i = k
                    at_word_start = False
                    continue
                i += 2
                at_word_start = False
                continue
            at_word_start = c in " \t;|&()<>\n"
            i += 1
    for _delim, _strip, _quoted, _buf in pending:
        if not _quoted:
            _inner = _heredoc_substitution_code("\n".join(_buf))
            if _inner:
                out.append(_inner + "\n")
    return "".join(out)


def join_continuations(text):
    text = mask_heredoc_bodies(text)
    out = []
    stack = [[False, False, False, True, None, 0]]
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        single, double, comment, word, closer, depth = stack[-1]
        if single:
            out.append(c)
            if c == "'":
                stack[-1][0] = False
                stack[-1][3] = False
            i += 1
            continue
        if comment:
            out.append(c)
            i += 1
            if c == "\n":
                stack[-1][2] = False
                stack[-1][3] = True
            continue
        if double:
            if c == "\\" and i + 1 < n:
                nxt = text[i + 1]
                if nxt == "\n":
                    i += 2
                    continue
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            if c == '"':
                out.append(c)
                stack[-1][1] = False
                stack[-1][3] = False
                i += 1
                continue
            if c == "$" and i + 1 < n and text[i + 1] == "(":
                out.append(c)
                out.append("(")
                stack.append([False, False, False, True, ")", 1])
                i += 2
                continue
            if c == "`":
                out.append(c)
                stack.append([False, False, False, True, "`", 0])
                i += 1
                continue
            out.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "\n":
                i += 2
                continue
            out.append(c)
            out.append(nxt)
            stack[-1][3] = False
            i += 2
            continue
        if c == "'":
            stack[-1][0] = True
            stack[-1][3] = False
            out.append(c)
            i += 1
            continue
        if c == '"':
            stack[-1][1] = True
            stack[-1][3] = False
            out.append(c)
            i += 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            out.append(c)
            out.append("(")
            stack.append([False, False, False, True, ")", 1])
            i += 2
            continue
        if closer == "`" and c == "`":
            out.append(c)
            stack.pop()
            stack[-1][3] = False
            i += 1
            continue
        if c == "`":
            out.append(c)
            stack.append([False, False, False, True, "`", 0])
            i += 1
            continue
        if closer == ")" and c == "(":
            out.append(c)
            stack[-1][5] += 1
            stack[-1][3] = True
            i += 1
            continue
        if closer == ")" and c == ")":
            out.append(c)
            if depth > 1:
                stack[-1][5] -= 1
                stack[-1][3] = False
            else:
                stack.pop()
                stack[-1][3] = False
            i += 1
            continue
        if c == "#":
            if word:
                stack[-1][2] = True
            out.append(c)
            i += 1
            continue
        out.append(c)
        if c in " \t;|&()<>\n":
            stack[-1][3] = True
        else:
            stack[-1][3] = False
        i += 1
    return "".join(out)


def header_substitution_code(text, spans=None):
    parts = []
    i = 0
    n = len(text)
    in_single = False
    in_double = False
    while i < n:
        c = text[i]
        if in_single:
            if c == "'":
                in_single = False
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = not in_double
            i += 1
            continue
        if c == "`":
            j = i + 1
            buf = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j])
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == "`":
                    break
                buf.append(text[j])
                j += 1
            if j < n:
                parts.append("".join(buf))
                if spans is not None:
                    spans.append((i, j + 1, in_double))
                i = j + 1
                continue
            i += 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            if i + 2 < n and text[i + 2] == "(":
                i += 3
                continue
            depth = 1
            j = i + 2
            buf = []
            jsingle = False
            jdouble = False
            while j < n and depth > 0:
                cj = text[j]
                if jsingle:
                    buf.append(cj)
                    if cj == "'":
                        jsingle = False
                    j += 1
                    continue
                if jdouble:
                    if cj == "\\" and j + 1 < n:
                        buf.append(cj)
                        buf.append(text[j + 1])
                        j += 2
                        continue
                    buf.append(cj)
                    if cj == '"':
                        jdouble = False
                    j += 1
                    continue
                if cj == "\\" and j + 1 < n:
                    buf.append(cj)
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if cj == "'":
                    jsingle = True
                    buf.append(cj)
                    j += 1
                    continue
                if cj == '"':
                    jdouble = True
                    buf.append(cj)
                    j += 1
                    continue
                if cj == "$" and j + 1 < n and text[j + 1] == "(":
                    if j + 2 < n and text[j + 2] == "(":
                        depth += 2
                        buf.append(cj)
                        buf.append(text[j + 1])
                        buf.append(text[j + 2])
                        j += 3
                        continue
                    depth += 1
                    buf.append(cj)
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if cj == "(":
                    depth += 1
                    buf.append(cj)
                    j += 1
                    continue
                if cj == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                    buf.append(cj)
                    j += 1
                    continue
                buf.append(cj)
                j += 1
            if depth == 0:
                parts.append("".join(buf))
                if spans is not None:
                    spans.append((i, j, in_double))
                i = j
                continue
            i += 2
            continue
        i += 1
    return " ".join(parts)


def shell_delimiter_index(text, word):
    n = len(text)
    m = len(word)
    stack = [[False, False, None, 0]]
    i = 0
    while i < n:
        single, double, closer, depth = stack[-1]
        c = text[i]
        if single:
            if c == "'":
                stack[-1][0] = False
            i += 1
            continue
        if double:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                stack[-1][1] = False
                i += 1
                continue
            if c == "$" and i + 1 < n and text[i + 1] == "(":
                if i + 2 < n and text[i + 2] == "(":
                    stack.append([False, False, ")", 2])
                    i += 3
                    continue
                stack.append([False, False, ")", 1])
                i += 2
                continue
            if c == "$" and i + 1 < n and text[i + 1] == "{":
                stack.append([False, False, "}", 0])
                i += 2
                continue
            if c == "`":
                stack.append([False, False, "`", 0])
                i += 1
                continue
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "'":
            stack[-1][0] = True
            i += 1
            continue
        if c == '"':
            stack[-1][1] = True
            i += 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            if i + 2 < n and text[i + 2] == "(":
                stack.append([False, False, ")", 2])
                i += 3
                continue
            stack.append([False, False, ")", 1])
            i += 2
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "{":
            stack.append([False, False, "}", 0])
            i += 2
            continue
        if c == "`":
            if closer == "`":
                stack.pop()
            else:
                stack.append([False, False, "`", 0])
            i += 1
            continue
        if text.startswith("case", i):
            before = text[i - 1] if i > 0 else " "
            after = text[i + 4] if i + 4 < n else " "
            if before in " \t\n;|&()<>" and after in " \t\n;|&()<>" and any(s[2] == ")" for s in stack):
                raise AssertionError("unsupported case in command substitution")
        if closer == ")" and c == "(":
            stack[-1][3] += 1
            i += 1
            continue
        if closer == ")" and c == ")":
            if depth > 1:
                stack[-1][3] -= 1
            else:
                stack.pop()
            i += 1
            continue
        if closer == "}" and c == "}":
            stack.pop()
            i += 1
            continue
        if len(stack) == 1 and text.startswith(word, i):
            before = text[i - 1] if i > 0 else " "
            after = text[i + m] if i + m < n else " "
            if word != "in":
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    return i
            elif before in " \t;|&()<>\n" and after in " \t;|&()<>\n#":
                k = 0
                while k < n and text[k] in " \t\n":
                    k += 1
                end = 0
                if text.startswith("case", k) and (k + 4 >= n or text[k + 4] in " \t\n;|&()<>#"):
                    k += 4
                    while k < n and text[k] in " \t\n":
                        k += 1
                    j = k
                    s_stack = [[False, False, None, 0]]
                    while j < n:
                        s0, s1, s2, s3 = s_stack[-1]
                        d = text[j]
                        if s0:
                            if d == "'":
                                s_stack[-1][0] = False
                            j += 1
                            continue
                        if s1:
                            if d == "\\" and j + 1 < n:
                                j += 2
                                continue
                            if d == '"':
                                s_stack[-1][1] = False
                                j += 1
                                continue
                            if d == "$" and j + 1 < n and text[j + 1] == "(":
                                if j + 2 < n and text[j + 2] == "(":
                                    s_stack.append([False, False, ")", 2])
                                    j += 3
                                    continue
                                s_stack.append([False, False, ")", 1])
                                j += 2
                                continue
                            if d == "$" and j + 1 < n and text[j + 1] == "{":
                                s_stack.append([False, False, "}", 0])
                                j += 2
                                continue
                            if d == "`":
                                s_stack.append([False, False, "`", 0])
                                j += 1
                                continue
                            if s2 == ")" and d == "(":
                                s_stack[-1][3] += 1
                                j += 1
                                continue
                            if s2 == ")" and d == ")":
                                if s3 > 1:
                                    s_stack[-1][3] -= 1
                                else:
                                    s_stack.pop()
                                j += 1
                                continue
                            if s2 == "}" and d == "}":
                                s_stack.pop()
                                j += 1
                                continue
                            if s2 == "`" and d == "`":
                                s_stack.pop()
                                j += 1
                                continue
                            j += 1
                            continue
                        if d == "\\" and j + 1 < n:
                            j += 2
                            continue
                        if d == "'":
                            s_stack[-1][0] = True
                            j += 1
                            continue
                        if d == '"':
                            s_stack[-1][1] = True
                            j += 1
                            continue
                        if d == "$" and j + 1 < n and text[j + 1] == "(":
                            if j + 2 < n and text[j + 2] == "(":
                                s_stack.append([False, False, ")", 2])
                                j += 3
                                continue
                            s_stack.append([False, False, ")", 1])
                            j += 2
                            continue
                        if d == "$" and j + 1 < n and text[j + 1] == "{":
                            s_stack.append([False, False, "}", 0])
                            j += 2
                            continue
                        if d == "`":
                            if s2 == "`":
                                s_stack.pop()
                            else:
                                s_stack.append([False, False, "`", 0])
                            j += 1
                            continue
                        if s2 == ")" and d == "(":
                            s_stack[-1][3] += 1
                            j += 1
                            continue
                        if s2 == ")" and d == ")":
                            if s3 > 1:
                                s_stack[-1][3] -= 1
                            else:
                                s_stack.pop()
                            j += 1
                            continue
                        if s2 == "}" and d == "}":
                            s_stack.pop()
                            j += 1
                            continue
                        if len(s_stack) == 1 and d in " \t;|&()<>\n":
                            break
                        j += 1
                    end = j
                if i >= end:
                    return i
        i += 1
    return -1


def line_path_violations(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []
    code = strip_shell_comment(line)
    s = code.strip()
    header_extra = ""
    if s.startswith(("for ", "for\t")):
        idx = shell_delimiter_index(s, "do")
        if idx < 0:
            return []
        header_extra = header_substitution_code(s[:idx])
        code = s[idx + 2 :]
    elif s.startswith(("case ", "case\t")):
        idx = shell_delimiter_index(s, "in")
        if idx < 0:
            return []
        header_extra = header_substitution_code(s[:idx])
        rest = s[idx + 2 :]
        kept = []
        in_single = False
        in_double = False
        in_body = False
        buf = []
        i = 0
        n = len(rest)
        while i < n:
            c = rest[i]
            if in_single:
                if in_body:
                    buf.append(c)
                if c == "'":
                    in_single = False
                i += 1
                continue
            if in_double:
                if c == "\\" and i + 1 < n:
                    if in_body:
                        buf.append(c)
                        buf.append(rest[i + 1])
                    i += 2
                    continue
                if in_body:
                    buf.append(c)
                if c == '"':
                    in_double = False
                i += 1
                continue
            if c == "\\" and i + 1 < n:
                if in_body:
                    buf.append(c)
                    buf.append(rest[i + 1])
                i += 2
                continue
            if c == "'":
                in_single = True
                if in_body:
                    buf.append(c)
                i += 1
                continue
            if c == '"':
                in_double = True
                if in_body:
                    buf.append(c)
                i += 1
                continue
            if i + 4 <= n and rest[i : i + 4] == "esac":
                prev = rest[i - 1] if i > 0 else " "
                nxt = rest[i + 4] if i + 4 < n else " "
                if prev in " \t;|&()<>\n" and nxt in " \t;|&()<>\n#":
                    if in_body:
                        kept.append("".join(buf))
                        buf = []
                        in_body = False
                    kept.append(rest[i + 4 :])
                    break
            if not in_body:
                if c == ")":
                    in_body = True
                    buf = []
                i += 1
                continue
            if c == ";" and i + 1 < n and rest[i + 1] == ";":
                kept.append("".join(buf))
                buf = []
                in_body = False
                i += 2
                continue
            buf.append(c)
            i += 1
        if in_body:
            kept.append("".join(buf))
        code = " ".join(kept)
    if header_extra:
        code = header_extra + " " + code
    toks = re.findall(r"""(?:"[^"]*"|'[^']*'|\\.|[^\s;|&<>()"'])+""", code)
    if not toks:
        return []
    if toks[0] in _PATH_OPS or "/" + toks[0] in code[: len(toks[0]) + 1]:
        base = toks[0].rsplit("/", 1)[-1]
        scoped = base in _PATH_OPS
    else:
        scoped = False
    if scoped:
        words = []
        opts_done = False
        for tok in toks[1:]:
            if not opts_done and tok == "--":
                opts_done = True
                continue
            if not opts_done and len(tok) > 1 and tok.startswith("-"):
                continue
            if tok in (">", ">>", "<", "<<", "2>", "&>"):
                continue
            words.append(tok)
    else:
        words = list(toks)
    return [w for w in words if _word_has_bare_glob_or_tilde(w)]


def group_for_case_headers(lines):
    out = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith(("for ", "for\t")) or s.startswith(("case ", "case\t")):
            pat = r"\bdo\b" if s.startswith(("for ", "for\t")) else r"\bin\b"
            if not re.search(pat, lines[i]):
                combined = lines[i]
                j = i + 1
                while j < n and not re.search(pat, combined):
                    combined = combined + "\n" + lines[j]
                    j += 1
                if re.search(pat, combined):
                    out.append(combined)
                    i = j
                    continue
        out.append(lines[i])
        i += 1
    return out


EXPECTED_BACKUP_PROBE = 'OPOS_HOME="$OPOS_HOME" test -f "$OPOS_HOME/sshd_config.bak"'


def assert_snippet_confined(snippet):
    joined = join_continuations(snippet)
    for raw in group_for_case_headers(joined.splitlines()):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if writes_backup(raw):
            raise AssertionError("backup write: %r" % raw)
        if line_path_violations(raw):
            raise AssertionError("unconfined path operation: %r" % raw)


def writes_backup(line):
    leaf = "sshd_config" + ".bak"
    code = strip_shell_comment(line)
    if leaf not in code:
        return False
    if re.search(r">\s*>? ?\S*" + re.escape(leaf), code):
        return True
    for seg in re.split(r"[;&|]+", code):
        toks = seg.split()
        while toks and toks[0] in ("if", "then", "elif", "else", "while", "until", "do"):
            toks = toks[1:]
        if not toks:
            continue
        base = toks[0].rsplit("/", 1)[-1]
        args = [t for t in toks[1:] if not t.startswith("-")]
        if base in ("tee", "touch", "truncate", "dd") and any(leaf in arg for arg in args):
            return True
        if base in ("cp", "mv", "ln", "install") and args and leaf in args[-1]:
            return True
    return False


def _dynamic_hit_for_code(code):
    spans = []
    header_substitution_code(code, spans)
    marked = ""
    pos = 0
    for start, end, quoted in spans:
        marked += code[pos:start] + "X"
        pos = end
    marked += code[pos:]
    marked = re.sub(r"\$\([^)]*\)", '""', marked)
    marked = re.sub(r"`[^`]*`", '""', marked)
    masked = re.sub(r'"[^"]*"', '""', marked)
    masked = re.sub(r"'[^']*'", "''", masked)
    if re.search(r"\$[{A-Za-z_(*@#?$!0-9-]", masked):
        return "unquoted"
    for start, end, quoted in spans:
        if not quoted:
            return "unquoted"
    n = len(code)
    for start, end, quoted in spans:
        bare = (
            start > 0
            and code[start - 1] == '"'
            and (start < 2 or code[start - 2] in " \t;|&()<>\n")
            and end < n
            and code[end] == '"'
            and (end + 1 >= n or code[end + 1] in " \t;|&()<>\n")
        )
        if not bare:
            return "dynamic"
    for tok in re.findall(r"""(?:"[^"]*"|'[^']*'|\\.|[^\s;|&<>()"'])+""", marked):
        frags = re.findall(r'"[^"]*"', tok)
        if not frags:
            continue
        refs = sum(len(re.findall(r"\$[{A-Za-z_(*@#?$!0-9-]", lit)) for lit in frags)
        if refs > 1:
            return "dynamic"
        if any(re.search(r"\$[@*]|\$\{[@*]", lit) for lit in frags):
            return "dynamic"
        if refs == 1 and re.search(r"\$[{A-Za-z_0-9*@#?$!-][A-Za-z0-9_@*#$?!-]*}?/", tok):
            rest = tok.split("/", 1)[1]
            if any("$" in lit for lit in re.findall(r'"[^"]*"', rest)):
                return "dynamic"
    return ""


def scan_snippet_unquoted_dynamic(snippet):
    uq = []
    dyn = []
    joined = join_continuations(snippet)
    for raw in joined.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        code = strip_shell_comment(raw)
        hit = _dynamic_hit_for_code(code)
        if hit == "unquoted":
            uq.append(raw)
        elif hit == "dynamic":
            dyn.append(raw)
    return uq, dyn


def scan_installer_paths(text):
    ops = ("rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod", "chown", "ln")
    unquoted = []
    dynamic = []
    logical = join_continuations(text)
    for raw in logical.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not re.search(r"(^|[;|&]\s*|\b)(%s)\s" % "|".join(ops), raw):
            continue
        code = strip_shell_comment(raw)
        hit = _dynamic_hit_for_code(code)
        if hit == "unquoted":
            unquoted.append(raw.strip())
        elif hit == "dynamic":
            dynamic.append(raw.strip())
    for raw in group_for_case_headers(logical.splitlines()):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if (
            not re.search(r"(^|[;|&]\s*|\b)(%s)\s" % "|".join(ops), raw)
            and not raw.strip().startswith(("for ", "for\t"))
            and not raw.strip().startswith(("case ", "case\t"))
        ):
            continue
        for bad in line_path_violations(raw):
            entry = raw.strip()
            if entry not in unquoted:
                unquoted.append(entry)
            break
    return unquoted, dynamic


def install_review_modules():
    directory = pathlib.Path(__file__).resolve().parent
    parts = {}
    for path in directory.glob("test_install_review_part*.py"):
        if (match := re.fullmatch(r"test_install_review_part([1-9][0-9]*)", path.stem)) and int(match[1]) >= 7:
            parts[int(match[1])] = path
    paths = (directory / "test_install_review.py", *(parts[number] for number in sorted(parts)))
    main = sys.modules["__main__"]
    main_file = getattr(main, "__file__", None)
    main_path = pathlib.Path(main_file).resolve() if main_file else None
    return tuple(main if path == main_path else importlib.import_module(f"tests.{path.stem}") for path in paths)
