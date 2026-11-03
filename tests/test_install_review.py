# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock


def _read_install_sh() -> str:
    candidates = [
        pathlib.Path(__file__).resolve().parents[1] / "install.sh",
        pathlib.Path("install.sh"),
    ]
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("install.sh not found in expected locations")


def _uninstall_restore_block(text):
    start = text.index("do_uninstall() {")
    marker = text.index("printf '==> restore summary:", start)
    end = text.index("\n", marker) + 1
    return text[start:end]


def _run_sshd_dropin_restore(tmp_path, sshd_exit):
    text = _read_install_sh()
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


def _prepare_fallback_restore_snippet(dropin, dest):
    text = _read_install_sh()
    start = text.index("do_uninstall() {")
    found = text.index("FOUND=0", start)
    marker = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
    end = text.index(marker, found)
    fragment = text[found:end]
    prod = "/etc/ssh/" + "sshd_config"
    prod_dropin = prod + ".d/60-opos.conf"
    rewritten = fragment.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
    joined = _join_continuations(rewritten)
    pattern = r"\b(?:" + "|".join(map(re.escape, _PATH_OPS)) + r")\s"
    kept = [line for line in joined.splitlines() if _writes_backup(line) or re.search(pattern, line)]
    _assert_snippet_confined("\n".join(kept))
    return rewritten


def _open_symlink_fd(path: str) -> int:
    if hasattr(os, "O_PATH"):
        return os.open(path, os.O_PATH | os.O_NOFOLLOW)
    if hasattr(os, "O_SYMLINK"):
        return os.open(path, os.O_RDONLY | os.O_SYMLINK)
    raise unittest.SkipTest("no supported symlink-handle mechanism")


def _artifact_data(path, kind):
    if kind == "file":
        return pathlib.Path(path).read_bytes()
    if kind == "link":
        return os.readlink(path)
    return None


def _run_retain_rollback(tmp_path, preamble):
    text = _read_install_sh()
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
    env.update({
        "OPOS_HOME": str(tmp_path / "home"),
        "DRY": "0",
        "OPOS_DEST": str(dest),
        "OPOS_VICTIM": str(tmp_path / "victim"),
    })
    proc = subprocess.run(["sh", str(runner), str(tmp_path / "clone")], capture_output=True, text=True, env=env, timeout=30)
    return (proc, tmp_path / "home", dest, helper_bytes)


_PATH_OPS = ("rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod", "chown", "ln")


def _isolated_git_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _contained_candidate(tmpdir, candidate):
    base = os.path.realpath(str(tmpdir))
    resolved = os.path.realpath(str(candidate))
    try:
        inside = os.path.commonpath([base, resolved]) == base
    except ValueError:
        inside = False
    if not inside:
        raise AssertionError("candidate escapes fixture: %r" % (candidate,))
    return resolved


def _run_contained(tmpdir, candidate, argv):
    validated = _contained_candidate(tmpdir, candidate)
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


def _strip_shell_comment(line):
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


def _mask_heredoc_bodies(text):
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
            if not stack or stack[-1][0] != "arith":
                in_arith_now = len(stack) > 0 and stack[-1][0] == "arith"
            else:
                in_arith_now = True
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
                        parts.append(line[k + 1:e])
                        k = e + 1
                        found = True
                        quoted = True
                        continue
                    if d == '"':
                        e = line.find('"', k + 1)
                        if e < 0:
                            break
                        parts.append(line[k + 1:e])
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
            if c in " \t;|&()<>\n":
                at_word_start = True
            else:
                at_word_start = False
            i += 1
    for _delim, _strip, _quoted, _buf in pending:
        if not _quoted:
            _inner = _heredoc_substitution_code("\n".join(_buf))
            if _inner:
                out.append(_inner + "\n")
    return "".join(out)


def _join_continuations(text):
    text = _mask_heredoc_bodies(text)
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


def _header_substitution_code(text, spans=None):
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


def _shell_delimiter_index(text, word):
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


def _line_path_violations(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []
    code = _strip_shell_comment(line)
    s = code.strip()
    header_extra = ""
    if s.startswith(("for ", "for\t")):
        idx = _shell_delimiter_index(s, "do")
        if idx < 0:
            return []
        header_extra = _header_substitution_code(s[:idx])
        code = s[idx + 2:]
    elif s.startswith(("case ", "case\t")):
        idx = _shell_delimiter_index(s, "in")
        if idx < 0:
            return []
        header_extra = _header_substitution_code(s[:idx])
        rest = s[idx + 2:]
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
            if i + 4 <= n and rest[i:i + 4] == "esac":
                prev = rest[i - 1] if i > 0 else " "
                nxt = rest[i + 4] if i + 4 < n else " "
                if prev in " \t;|&()<>\n" and nxt in " \t;|&()<>\n#":
                    if in_body:
                        kept.append("".join(buf))
                        buf = []
                        in_body = False
                    kept.append(rest[i + 4:])
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
    toks = re.findall(r'''(?:"[^"]*"|'[^']*'|\\.|[^\s;|&<>()"'])+''', code)
    if not toks:
        return []
    if toks[0] in _PATH_OPS or "/" + toks[0] in code[:len(toks[0]) + 1]:
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


def _group_for_case_headers(lines):
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


_EXPECTED_BACKUP_PROBE = 'OPOS_HOME="$OPOS_HOME" test -f "$OPOS_HOME/sshd_config.bak"'


def _assert_snippet_confined(snippet):
    joined = _join_continuations(snippet)
    for raw in _group_for_case_headers(joined.splitlines()):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if _writes_backup(raw):
            raise AssertionError('backup write: %r' % raw)
        if _line_path_violations(raw):
            raise AssertionError("unconfined path operation: %r" % raw)


def _writes_backup(line):
    leaf = 'sshd_config' + '.bak'
    code = _strip_shell_comment(line)
    if leaf not in code:
        return False
    if re.search(r'>\s*>? ?\S*' + re.escape(leaf), code):
        return True
    for seg in re.split(r'[;&|]+', code):
        toks = seg.split()
        while toks and toks[0] in ('if', 'then', 'elif', 'else', 'while', 'until', 'do'):
            toks = toks[1:]
        if not toks:
            continue
        base = toks[0].rsplit('/', 1)[-1]
        args = [t for t in toks[1:] if not t.startswith('-')]
        if base in ('tee', 'touch', 'truncate', 'dd') and any(leaf in arg for arg in args):
            return True
        if base in ('cp', 'mv', 'ln', 'install') and args and leaf in args[-1]:
            return True
    return False


def _dynamic_hit_for_code(code):
    spans = []
    _header_substitution_code(code, spans)
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
    for tok in re.findall(r'''(?:"[^"]*"|'[^']*'|\\.|[^\s;|&<>()"'])+''', marked):
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


def _scan_snippet_unquoted_dynamic(snippet):
    uq = []
    dyn = []
    joined = _join_continuations(snippet)
    for raw in joined.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        code = _strip_shell_comment(raw)
        hit = _dynamic_hit_for_code(code)
        if hit == "unquoted":
            uq.append(raw)
        elif hit == "dynamic":
            dyn.append(raw)
    return uq, dyn


def _scan_installer_paths(text):
    ops = ("rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod", "chown", "ln")
    unquoted = []
    dynamic = []
    logical = _join_continuations(text)
    for raw in logical.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not re.search(r"(^|[;|&]\s*|\b)(%s)\s" % "|".join(ops), raw):
            continue
        code = _strip_shell_comment(raw)
        hit = _dynamic_hit_for_code(code)
        if hit == "unquoted":
            unquoted.append(raw.strip())
        elif hit == "dynamic":
            dynamic.append(raw.strip())
    for raw in _group_for_case_headers(logical.splitlines()):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not re.search(r"(^|[;|&]\s*|\b)(%s)\s" % "|".join(ops), raw):
            if not raw.strip().startswith(("for ", "for\t")) and not raw.strip().startswith(("case ", "case\t")):
                continue
        for bad in _line_path_violations(raw):
            entry = raw.strip()
            if entry not in unquoted:
                unquoted.append(entry)
            break
    return unquoted, dynamic


class TestInstallReview(unittest.TestCase):
    def test_f1116_substitution_spans_quote_context(self):
        spans = []
        a = chr(96); b = chr(96)
        txt = "x=" + a + "a" + b + " y=$(b) z='$(c)' w=\"$(d)\""
        out = _header_substitution_code(txt, spans)
        self.assertEqual(out, "a b d")
        self.assertEqual(len(spans), 3)
        s0, e0, q0 = spans[0]
        s1, e1, q1 = spans[1]
        s2, e2, q2 = spans[2]
        self.assertEqual(txt[s0:e0], a + "a" + b)
        self.assertEqual(txt[s1:e1], "$(b)")
        self.assertEqual(txt[s2:e2], "$(d)")
        self.assertFalse(q0)
        self.assertFalse(q1)
        self.assertTrue(q2)
        spans2 = []
        nested = "cmd=$(outer$(inner))"
        _header_substitution_code(nested, spans2)
        self.assertEqual(len(spans2), 1)
        self.assertEqual(spans2, [(4, 20, False)])
        self.assertEqual(nested[spans2[0][0]:spans2[0][1]], "$(outer$(inner))")
        self.assertFalse(spans2[0][2])

    def test_f1116_command_substitution_operands(self):
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        bt = chr(96)
        dq = chr(34)
        sq = chr(39)
        nl = chr(10)
        dollar = ds + lp + "gen_target" + rp
        ticks = bt + "gen_target" + bt
        cases = [
            ("dollar_unquoted", "rm -rf " + dollar + "/rest", True),
            ("backtick_unquoted", "rm -rf " + ticks + "/rest", True),
            ("dollar_quoted", "rm -rf " + dq + dollar + "/rest" + dq, False),
            ("backtick_quoted", "rm -rf " + dq + ticks + "/rest" + dq, False),
        ]
        for name, line, is_unquoted in cases:
            with self.subTest(command=name):
                snippet = line + nl
                if is_unquoted:
                    want = ([line], [])
                    message = "must quote every path operand"
                else:
                    want = ([], [line])
                    message = "must not use dynamically built paths"
                self.assertEqual(_scan_snippet_unquoted_dynamic(snippet), want)
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=snippet):
                    with self.assertRaisesRegex(AssertionError, message):
                        self.test_production_paths_reject_unquoted_and_dynamic()
        controls = [
            ("dollar_single", "rm -rf " + sq + dollar + "/rest" + sq),
            ("backtick_single", "rm -rf " + sq + ticks + "/rest" + sq),
        ]
        for name, line in controls:
            with self.subTest(control=name):
                snippet = line + nl
                self.assertEqual(_scan_snippet_unquoted_dynamic(snippet), ([], []))
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=snippet):
                    self.test_production_paths_reject_unquoted_and_dynamic()

    def test_unquoted_heredoc_substitution_rejects_unsafe_removal(self):
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        bt = chr(96)
        bs = chr(92)
        nl = chr(10)
        base = dq + "$BASE" + dq + "/"
        inner = "rm -rf " + base + star
        for snippet in (
            "cat <<EOF" + nl + ds + lp + inner + rp + nl + "EOF" + nl,
            "cat <<EOF" + nl + bt + inner + bt + nl + "EOF" + nl,
        ):
            with self.subTest(snippet=snippet[:8]):
                with self.assertRaisesRegex(AssertionError, "unconfined path operation"):
                    _assert_snippet_confined(snippet)
        quoted_snippets = (
            "cat <<'EOF'" + nl + ds + lp + inner + rp + nl + "EOF" + nl,
            'cat <<"EOF"' + nl + ds + lp + inner + rp + nl + "EOF" + nl,
            "cat <<EO" + bs + "F" + nl + ds + lp + inner + rp + nl + "EOF" + nl,
        )
        for snippet in quoted_snippets:
            with self.subTest(snippet=snippet[:8]):
                _assert_snippet_confined(snippet)
        escaped = (
            "cat <<EOF" + nl + bs + ds + lp + inner + rp + nl + "EOF" + nl,
            "cat <<EOF" + nl + bs + bt + inner + bt + nl + "EOF" + nl,
        )
        for snippet in escaped:
            with self.subTest(snippet=snippet[:8]):
                _assert_snippet_confined(snippet)
        _assert_snippet_confined("cat <<EOF" + nl + inner + nl + "EOF" + nl)

    def test_unquoted_heredoc_split_substitution_rejects_unsafe_removal(self):
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        bs = chr(92)
        nl = chr(10)
        base = dq + ds + "BASE" + dq + "/"
        inner = "rm -rf " + base + star
        split = ds + bs + nl + lp + inner + rp
        snippet = "cat <<EOF" + nl + split + nl + "EOF" + nl
        with self.assertRaisesRegex(AssertionError, "unconfined path operation"):
            _assert_snippet_confined(snippet)
        _assert_snippet_confined("cat <<'EOF'" + nl + split + nl + "EOF" + nl)
        _assert_snippet_confined("cat <<EOF" + nl + bs + split + nl + "EOF" + nl)
        _assert_snippet_confined("cat <<EOF" + nl + ds + bs + bs + nl + lp + inner + rp + nl + "EOF" + nl)

    def test_p018_heredoc_substitution_rejects_comment_and_case(self):
        h = chr(35)
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        nl = chr(10)
        sq = chr(39)
        inner = "rm -rf " + dq + ds + "BASE" + dq + "/" + star
        comment_body = ds + lp + "echo ok " + h + " " + rp + nl + inner + nl + rp
        case_kw = "".join(["ca", "se"])
        case_body = ds + lp + case_kw + " x in x" + rp + nl + inner + nl + ";; esac" + rp
        for label, body in (("comment", comment_body), ("case", case_body)):
            with self.subTest(bypass=label):
                snippet = "cat <<EOF" + nl + body + nl + "EOF" + nl
                with self.assertRaisesRegex(AssertionError, "unsupported heredoc substitution syntax"):
                    _assert_snippet_confined(snippet)
        for label, body in (("comment", comment_body), ("case", case_body)):
            with self.subTest(quoted_delim=label):
                _assert_snippet_confined("cat <<'EOF'" + nl + body + nl + "EOF" + nl)
                _assert_snippet_confined('cat <<"EOF"' + nl + body + nl + "EOF" + nl)
        quoted_hash = ds + lp + "printf " + sq + "%s" + sq + " " + sq + h + " " + rp + sq + rp
        quoted_case = ds + lp + "printf " + sq + "%s" + sq + " " + sq + case_kw + " x in x" + sq + rp
        for label, sub in (("hash", quoted_hash), ("case", quoted_case)):
            with self.subTest(quoted_content=label):
                _assert_snippet_confined("cat <<EOF" + nl + sub + nl + "EOF" + nl)

    def test_unterminated_unquoted_heredoc_substitution_rejects_unsafe_removal(self):
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        bt = chr(96)
        nl = chr(10)
        base = dq + "$BASE" + dq + "/"
        inner = "rm -rf " + base + star
        subs = (
            ds + lp + inner + rp,
            bt + inner + bt,
        )
        for sub in subs:
            for ending in ("", nl):
                snippet = "cat <<EOF" + nl + sub + ending
                with self.subTest(sub=sub[:1], newline=ending != ""):
                    with self.assertRaisesRegex(AssertionError, "unconfined path operation"):
                        _assert_snippet_confined(snippet)

    def test_empty_quoted_heredoc_body_quote_does_not_hide_command(self):
        star = chr(42)
        dq = chr(34)
        nl = chr(10)
        base = dq + "$BASE" + dq + "/"
        inner = "rm -rf " + base + star
        for opener in ("cat <<''" + nl, 'cat <<""' + nl):
            snippet = opener + dq + nl + nl + inner + nl
            with self.subTest(opener=opener[:8]):
                with self.assertRaisesRegex(AssertionError, "unconfined path operation"):
                    _assert_snippet_confined(snippet)

    def test_remove_clone_preserves_clean_checkout_without_ownership_record(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("canon_repo") + _extract("remove_clone")
        for name in ("committed_addition", "hook_only"):
            for dry in ("live", "dry_run"):
                with self.subTest(name=name, dry=dry):
                    with tempfile.TemporaryDirectory() as tmp:
                        tmp_path = pathlib.Path(tmp)
                        clone = tmp_path / "clone"
                        clone.mkdir()
                        (clone / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                        base_env = dict(os.environ)
                        subprocess.run(["git", "-C", str(clone), "init"], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                        subprocess.run(["git", "-C", str(clone), "add", "install.sh"], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                        subprocess.run(["git", "-C", str(clone), "-c", "user.email=" + "".join(["o", "pos@", "example.com"]), "-c", "user.name=" + "".join(["o", "pos"]), "-c", "commit.gpgsign=false", "commit", "-m", "".join(["i", "nit"])], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                        if name == "committed_addition":
                            owned = clone / "".join(["owner-ad", "ded.txt"])
                            owned_bytes = "".join(["owner-", "added-", "9", "\n"]).encode()
                            owned.write_bytes(owned_bytes)
                            subprocess.run(["git", "-C", str(clone), "add", owned.name], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                            subprocess.run(["git", "-C", str(clone), "-c", "user.email=" + "".join(["o", "pos@", "example.com"]), "-c", "user.name=" + "".join(["o", "pos"]), "-c", "commit.gpgsign=false", "commit", "-m", "".join(["add-", "owner"])], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                            check_path = owned
                        else:
                            hook = clone / ".git" / "hooks" / "pre-commit"
                            hook_bytes = "".join(["#!/bin/sh\nexit ", "0\n"]).encode()
                            hook.write_bytes(hook_bytes)
                            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                            check_path = hook
                            owned = hook
                            owned_bytes = hook_bytes
                        status = subprocess.run(["git", "-C", str(clone), "status", "--porcelain", "--ignored", "--untracked-files=all"], capture_output=True, text=True, env=base_env, timeout=30)
                        self.assertEqual(status.returncode, 0, status.stderr)
                        self.assertEqual(status.stdout, "", name)
                        runner = tmp_path / "run.sh"
                        runner.write_text(helpers + 'remove_clone "$1"\n', encoding="utf-8")
                        env = dict(os.environ)
                        env["ROOT"] = str(clone)
                        env["HOME"] = str(tmp_path)
                        env["DRY"] = "1" if dry == "dry_run" else "0"
                        proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertTrue(clone.is_dir(), name)
                        self.assertTrue(check_path.is_file(), name)
                        self.assertEqual(check_path.read_bytes(), owned_bytes, name)
                        self.assertIn("preserving checkout with owner contents", proc.stdout, name)
                        self.assertNotIn("==> removed " + str(clone), proc.stdout.splitlines(), name)
                        self.assertNotIn("==> would remove " + str(clone), proc.stdout.splitlines(), name)

    def test_remove_clone_preserves_owner_created_commit(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("canon_repo") + _extract("remove_clone")
        for dry in ("live", "dry_run"):
            with self.subTest(dry=dry):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    clone = tmp_path / "clone"
                    clone.mkdir()
                    (clone / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    base_env = _isolated_git_env()
                    subprocess.run(["git", "-C", str(clone), "init"], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                    subprocess.run(["git", "-C", str(clone), "add", "install.sh"], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                    subprocess.run(["git", "-C", str(clone), "-c", "user.email=" + "".join(["o", "pos@", "example.com"]), "-c", "user.name=" + "".join(["o", "pos"]), "-c", "commit.gpgsign=false", "commit", "-m", "".join(["i", "nit"])], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                    owned = clone / "".join(["owner-ad", "ded.txt"])
                    owned_bytes = "".join(["owner-", "added-", "5", "\n"]).encode()
                    owned.write_bytes(owned_bytes)
                    subprocess.run(["git", "-C", str(clone), "add", owned.name], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                    subprocess.run(["git", "-C", str(clone), "-c", "user.email=" + "".join(["o", "pos@", "example.com"]), "-c", "user.name=" + "".join(["o", "pos"]), "-c", "commit.gpgsign=false", "commit", "-m", "".join(["add-", "owner"])], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                    status = subprocess.run(["git", "-C", str(clone), "status", "--porcelain", "--ignored", "--untracked-files=all"], capture_output=True, text=True, env=base_env, timeout=30)
                    self.assertEqual(status.returncode, 0, status.stderr)
                    self.assertEqual(status.stdout, "", dry)
                    runner = tmp_path / "run.sh"
                    runner.write_text(helpers + 'remove_clone "$1"\n', encoding="utf-8")
                    env = dict(base_env)
                    env["ROOT"] = str(clone)
                    env["HOME"] = str(tmp_path)
                    env["DRY"] = "1" if dry == "dry_run" else "0"
                    proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertTrue(clone.is_dir(), dry)
                    self.assertTrue(owned.is_file(), dry)
                    self.assertEqual(owned.read_bytes(), owned_bytes, dry)
                    self.assertIn("preserving checkout with owner contents", proc.stdout, dry)
                    self.assertNotIn("==> removed " + str(clone), proc.stdout.splitlines(), dry)
                    self.assertNotIn("==> would remove " + str(clone), proc.stdout.splitlines(), dry)

    def test_owner_commit_git_environment_is_sanitized(self):
        sentinel_key = "".join(["OPOS_", "SENTINEL"])
        sentinel_value = "".join(["sentinel-", "value-1"])
        seed = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp/" + "opos-home",
            sentinel_key: sentinel_value,
            "GIT_DIR": "/tmp/" + "owner-repo",
            "GIT_WORK_TREE": "/tmp/" + "owner-work",
            "GIT_COMMON_DIR": "/tmp/" + "owner-common",
            "GIT_INDEX_FILE": "/tmp/" + "owner-index",
            "GIT_OBJECT_DIRECTORY": "/tmp/" + "owner-objects",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/" + "owner-alt",
        }
        with unittest.mock.patch.dict(os.environ, seed, clear=True):
            before = dict(os.environ)
            result = _isolated_git_env()
            self.assertEqual(result, {"PATH": "/usr/bin:/bin", "HOME": "/tmp/" + "opos-home", sentinel_key: sentinel_value})
            self.assertEqual(dict(os.environ), before)
            self.assertEqual(dict(os.environ), seed)

    def test_rollback_publication_rejects_concurrent_destination(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = (
            _extract("opos_owned_path")
            + _extract("canon_file")
            + _extract("py_ok")
            + _extract("find_python")
            + _extract("retain_hardening_rollback")
        )
        leaf = "".join(["opos-harden-", "rollback.sh"])
        helper_rel = "".join(["harden-device", ".sh"])
        stage_pat = "".join([".opos-harden-rollback."])
        ok_mark = "".join(["hardening rollback retained at "])
        for mode in ("directory", "symlink"):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    clone = tmp_path / "clone"
                    deploy = clone / "deploy"
                    deploy.mkdir(parents=True)
                    helper_src = deploy / helper_rel
                    helper_bytes = b"".join([b"helper-", b"bytes-7"])
                    helper_src.write_bytes(helper_bytes)
                    home = tmp_path / "home"
                    home.mkdir()
                    dest = home / leaf
                    target = tmp_path / "".join(["tar", "getdir"])
                    target.mkdir()
                    marker = target / "".join(["keep", ".txt"])
                    marker_bytes = b"".join([b"keep-", b"bytes-3"])
                    marker.write_bytes(marker_bytes)
                    runner = tmp_path / "run.sh"
                    if mode == "directory":
                        plant = 'mkdir -p "$OPOS_PLANT"\n'
                    else:
                        plant = 'mkdir -p "$OPOS_TARGET"\nln -s "$OPOS_TARGET" "$OPOS_PLANT"\n'
                    script = (
                        helpers
                        + 'chmod() {\ncommand chmod "$@"\n'
                        + plant
                        + '}\n'
                        + 'retain_hardening_rollback "$1"\n'
                    )
                    runner.write_text(script, encoding="utf-8")
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_PLANT"] = str(dest)
                    env["OPOS_TARGET"] = str(target)
                    env["DRY"] = "0"
                    proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertNotEqual(proc.returncode, 0, mode)
                    self.assertIn(mode, ("directory", "symlink"))
                    if mode == "directory":
                        self.assertTrue(dest.is_dir() and not dest.is_symlink(), mode)
                    else:
                        self.assertTrue(dest.is_symlink(), mode)
                        self.assertEqual(os.readlink(str(dest)), str(target), mode)
                    self.assertEqual(marker.read_bytes(), marker_bytes, mode)
                    nested = dest / leaf
                    self.assertFalse(os.path.lexists(str(nested)), mode)
                    leftovers = [p for p in home.iterdir() if stage_pat in p.name]
                    self.assertEqual(leftovers, [], mode)
                    self.assertNotIn(ok_mark, proc.stdout, mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-8"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(dest.is_file() and not dest.is_symlink())
            self.assertEqual(dest.read_bytes(), helper_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(dest)).st_mode), 0o700)
            leftovers = [p for p in home.iterdir() if stage_pat in p.name]
            self.assertEqual(leftovers, [])
            self.assertIn(ok_mark, proc.stdout)

    def test_rollback_publication_rejects_destination_replaced_after_staging(self):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as td:
            tmp_path = pathlib.Path(td)
            victim = tmp_path / "victim"
            victim.write_bytes(b"".join([b"vic", b"tim-bytes-5"]))
            preamble = 'cp() { command cp "$@" || return 1; ln -s "$OPOS_VICTIM" "$OPOS_DEST"; }\n'
            proc, home, dest, helper_bytes = _run_retain_rollback(tmp_path, preamble)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(victim.read_bytes(), b"".join([b"vic", b"tim-bytes-5"]))
            self.assertTrue(dest.is_symlink())
            self.assertEqual(os.readlink(str(dest)), str(victim))
            self.assertFalse(any(".opos-harden-rollback." in p.name for p in home.iterdir()))
            self.assertNotIn("hardening rollback retained at ", proc.stdout)

    def test_cleanup_preserves_exit_and_signal_status(self):
        text = _read_install_sh()
        start = text.index("cleanup() {")
        end = text.index('if [ "$UNINST"', start)
        fragment = text[start:end]
        cases = [
            ("exit_zero", "exit 0\n", 0, False),
            ("exit_seven", "exit 7\n", 7, False),
            ("sigint", "true\nkill -INT \"$$\"\n: > \"$CONTINUATION\"\n", 130, True),
            ("sigterm", "true\nkill -TERM \"$$\"\n: > \"$CONTINUATION\"\n", 143, True),
        ]
        for name, ending, want_rc, is_signal in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    staging = pathlib.Path(tempfile.mkdtemp(prefix=".venv-staging.", dir=str(opos_home)))
                    sentinel = staging / "sentinel"
                    sentinel.write_text("sentinel\n", encoding="utf-8")
                    tmp_launcher = tmp_path / "launcher.tmp"
                    tmp_launcher.write_text("launcher\n", encoding="utf-8")
                    tmp_manifest = tmp_path / "manifest.tmp"
                    tmp_manifest.write_text("manifest\n", encoding="utf-8")
                    continuation = tmp_path / "continued"
                    runner = tmp_path / "run.sh"
                    runner.write_text(
                        fragment
                        + "_staging_root=\"$STAGING_ROOT\"\n"
                        + ending,
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(opos_home)
                    env["STAGING_ROOT"] = str(staging)
                    env["TMP_LAUNCHER"] = str(tmp_launcher)
                    env["TMP_MANIFEST"] = str(tmp_manifest)
                    env["CONTINUATION"] = str(continuation)

                    def _reset_signals():
                        signal.signal(signal.SIGINT, signal.SIG_DFL)
                        signal.signal(signal.SIGTERM, signal.SIG_DFL)

                    proc = subprocess.run(
                        ["sh", str(runner)],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                        preexec_fn=_reset_signals,
                    )
                    self.assertEqual(proc.returncode, want_rc, proc.stdout + proc.stderr)
                    self.assertFalse(staging.exists(), "staging root must be removed")
                    self.assertFalse(sentinel.exists(), "staging sentinel must be removed")
                    self.assertFalse(tmp_launcher.exists(), "temporary launcher must be removed")
                    self.assertFalse(tmp_manifest.exists(), "temporary manifest must be removed")
                    if is_signal:
                        self.assertFalse(continuation.exists(), "signal must stop execution")

    def test_rollback_retention_partial_copy_failure_leaves_nothing(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        try:
            tmp_path = pathlib.Path(tmp.name)
            preamble = "cp() { for a in \"$@\"; do last=\"$a\"; done; printf '%s' partial-7 > \"$last\"; return 1; }\n"
            proc, home, dest, helper_bytes = _run_retain_rollback(tmp_path, preamble)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(os.path.lexists(str(dest)))
            self.assertFalse(any(".opos-harden-rollback." in p.name for p in home.iterdir()))
            self.assertNotIn("hardening rollback retained at ", proc.stdout)
            proc2, home2, dest2, helper_bytes2 = _run_retain_rollback(tmp_path, "")
            self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
            self.assertTrue(dest2.is_file())
            self.assertFalse(dest2.is_symlink())
            self.assertEqual(dest2.read_bytes(), helper_bytes)
        finally:
            tmp.cleanup()

    def test_venv_staging_rm_and_mv_are_checked(self):
        text = _read_install_sh()

        self.assertRegex(
            text,
            r'rm -rf "\$VENV"\s*\n\s*if \[ -e "\$VENV" \]',
            "must verify $VENV no longer exists after rm -rf",
        )

        self.assertRegex(
            text,
            r'if \[ -e "\$VENV" \]; then\s*\n\s*rm -rf "\$_staging_root"',
            "on rm leaving $VENV behind must clean staging root",
        )

        self.assertRegex(
            text,
            r'if \[ -e "\$VENV" \]; then[^#]*?fail ',
            "on rm failure must abort with clear message",
        )

        self.assertRegex(
            text,
            r'if ! mv "\$_staging_venv" "\$VENV"',
            "must check exit status of mv",
        )

        self.assertRegex(
            text,
            r'if ! mv "\$_staging_venv" "\$VENV"; then\s*\n\s*rm -rf "\$_staging_root"',
            "on mv failure must clean staging root",
        )

        self.assertRegex(
            text,
            r'if ! mv "\$_staging_venv" "\$VENV"; then[^#]*?fail ',
            "on mv failure must abort with clear message",
        )

        bare_mv = re.findall(r'^\s*mv "\$_staging_venv" "\$VENV"\s*$', text, re.MULTILINE)
        self.assertEqual(bare_mv, [], "bare unchecked mv must not remain")

        rm_idx = text.index('rm -rf "$VENV"')
        exist_idx = text.index('[ -e "$VENV" ]')
        mv_idx = text.index('if ! mv "$_staging_venv" "$VENV"')
        rmdir_idx = text.index('rmdir "$_staging_root"')
        self.assertLess(rm_idx, exist_idx)
        self.assertLess(exist_idx, mv_idx)
        self.assertLess(mv_idx, rmdir_idx)

    def test_rollback_retention_rejects_replaced_staging_file(self):
        import os, stat, tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        try:
            tmp_path = Path(tmp.name)
            victim = tmp_path / "victim"
            original_bytes = b"vic" + b"tim-keep-8"
            victim.write_bytes(original_bytes)
            os.chmod(victim, 0o640)
            mode = stat.S_IMODE(os.stat(victim).st_mode)
            preamble = 'cp() { command cp "$@" || return 1; for a in "$@"; do last="$a"; done; [ -f "$last" ] || return 1; rm -f "$last" || return 1; [ ! -e "$last" ] || return 1; ln -s "$OPOS_VICTIM" "$last" || return 1; [ -L "$last" ] || return 1; [ "$(readlink "$last")" = "$OPOS_VICTIM" ] || return 1; printf "F2160-replaced:%s\\n" "$last"; return 0; }\n'
            proc, home, dest, helper_bytes = _run_retain_rollback(tmp_path, preamble)
            self.assertEqual(proc.returncode, 1)
            markers = [line for line in proc.stdout.splitlines() if "F2160-replaced:" in line]
            self.assertEqual(len(markers), 1)
            staged = markers[0].split("F2160-replaced:", 1)[1].strip()
            self.assertTrue(staged.startswith(str(home) + os.sep))
            self.assertTrue(os.path.basename(staged).startswith(".opos-harden-rollback."))
            self.assertFalse(os.path.islink(victim))
            self.assertEqual(victim.read_bytes(), original_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(victim).st_mode), mode)
            self.assertFalse(os.path.lexists(str(dest)))
            self.assertFalse(any(".opos-harden-rollback." in p.name for p in home.iterdir()))
            self.assertNotIn("hardening rollback retained at ", proc.stdout)
        finally:
            tmp.cleanup()

    def test_fallback_uses_only_trusted_manifest_paths(self):
        text = _read_install_sh()
        self.assertNotIn('"$OPOS_HOME"/*', text, "must not use prefix trust for launcher")
        self.assertNotIn('"$OPOS_BIN_DIR"/*', text, "must not use prefix trust for launcher")
        start = text.index("verified_recovery()")
        end = text.index("do_uninstall()", start)
        helper = text[start:end]
        self.assertRegex(helper, r'(?m)^\s*\[ "(\$\{UVENV:-\}|\$UVENV)" = "\$OPOS_HOME/venv" \]', "fallback must validate UVENV with strict equality")
        self.assertRegex(helper, r'(?m)^\s*opos_owned_path "\$UVENV" "\$OPOS_HOME/venv"', "fallback must check trusted venv ownership")
        self.assertRegex(helper, r'(?m)^\s*\[ -x "\$UVENV/bin/python" \]', "fallback must check recovery python")
        helper_start = text.index("run_verified_recovery()")
        helper_end = text.index("do_uninstall()", helper_start)
        helper_fn = text[helper_start:helper_end]
        anchor = text.index("do_uninstall()")
        body = text[anchor:]
        helper_sites = re.findall(r'(?m)^\s*if verified_recovery; then\s*\n\s*_verified=1', helper_fn)
        body_sites = re.findall(r'(?m)^\s*if verified_recovery; then\s*\n\s*_verified=1', body)
        self.assertEqual(len(helper_sites), 1, "execution helper must have one verification site")
        self.assertEqual(len(body_sites), 1, "do_uninstall must retain one dry-run verification site")
        self.assertEqual(len(helper_sites) + len(body_sites), 2, "must have two verification sites across the two functions")
        self.assertNotRegex(body, r'(?<![A-Za-z0-9_])UH(?![A-Za-z0-9_])', "must not retain standalone UH cleanup")
        self.assertRegex(body, r'(?m)^\s*if \[ "\$LAUNCHER" = "\$DEF_LAUNCHER" \]; then\s*\n\s*remove_path "\$LAUNCHER"', "fallback must validate LAUNCHER with strict equality")
        exec_lines = [line for line in helper_fn.splitlines() if "$UVENV/bin/python" in line and "-m opos.setup" in line]
        self.assertEqual(len(exec_lines), 1, "execution helper must run one verified command")
        preview_lines = [line for line in body.splitlines() if "would run recovery entry point" in line and "$UVENV/bin/python" in line]
        self.assertEqual(len(preview_lines), 1, "dry-run must preview one recovery entry point")
        for fragment, target in ((helper_fn, exec_lines[0]), (body, preview_lines[0])):
            split_lines = fragment.splitlines()
            use = next(i for i, line in enumerate(split_lines) if target in line)
            stack = []
            for line in split_lines[:use]:
                stripped = line.strip()
                if stripped.startswith("if ") or stripped.startswith("if\t"):
                    stack.append("_verified" in line and "-eq 1" in line)
                elif stripped == "else" or stripped.startswith("else ") or stripped.startswith("else;") or stripped.startswith("else\t") or stripped.startswith("elif ") or stripped.startswith("elif\t"):
                    if stack:
                        stack[-1] = False
                elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                    if stack:
                        stack.pop()
            self.assertTrue(any(stack), "recovery python must stay inside verified recovery")

    def test_recovery_python_moved_outside_guard_is_rejected(self):
        text = _read_install_sh()
        self.test_fallback_uses_only_trusted_manifest_paths()
        marker = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        split = text.splitlines()
        targets = [i for i, line in enumerate(split) if ("$UVENV/bin/python" in line and "-m opos.setup" in line) or ("would run recovery entry point" in line and "$UVENV/bin/python" in line)]
        self.assertEqual(len(targets), 2, "must target execution command and dry-run preview")
        for pos in range(len(targets)):
            with self.subTest(site=pos):
                idx = targets[pos]
                moving = split[idx]
                remaining = split[:idx] + split[idx + 1:]
                at = next(i for i, line in enumerate(remaining) if marker in line)
                mutant = "\n".join(remaining[:at] + [moving] + remaining[at:]) + "\n"
                self.assertNotEqual(mutant, text)
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
                    with self.assertRaises(AssertionError):
                        self.test_fallback_uses_only_trusted_manifest_paths()

    def test_recovery_python_in_unverified_branch_is_rejected(self):
        text = _read_install_sh()
        self.test_fallback_uses_only_trusted_manifest_paths()
        split = text.splitlines()
        hits = [i for i, line in enumerate(split) if "would run recovery entry point" in line and "$UVENV/bin/python" in line]
        self.assertEqual(len(hits), 1, "must find one dry-run preview")
        idx = hits[0]
        self.assertEqual(split[idx + 1].strip(), "else", "preview must be followed by else")
        branches = ["else", "elif true; then", "elif\ttrue; then"]
        for branch in branches:
            with self.subTest(branch=branch):
                lines = list(split)
                preview = lines[idx]
                indent = preview[:len(preview) - len(preview.lstrip())]
                lines[idx] = indent + ":"
                nxt = lines[idx + 1]
                nindent = nxt[:len(nxt) - len(nxt.lstrip())]
                lines[idx + 1] = nindent + branch
                lines.insert(idx + 2, preview)
                mutant = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
                self.assertNotEqual(mutant, text)
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
                    with self.assertRaisesRegex(AssertionError, "recovery python must stay inside verified recovery"):
                        self.test_fallback_uses_only_trusted_manifest_paths()

    def test_each_manifest_destructive_op_has_enclosing_guard(self):
        text = _read_install_sh()
        self.test_fallback_uses_only_trusted_manifest_paths()
        recovery = "if verified_recovery; then"
        parts = text.split(recovery)
        self.assertEqual(len(parts), 3, "expected two recovery sites")
        for idx in range(2):
            with self.subTest(site="recovery_%d" % idx):
                fixture = parts[0]
                for j in range(2):
                    guard = "if true; then # " + recovery if j == idx else recovery
                    fixture += guard + parts[j + 1]
                self.assertNotEqual(fixture, text)
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=fixture):
                    with self.assertRaises(AssertionError):
                        self.test_fallback_uses_only_trusted_manifest_paths()
        lg = 'if [ "$LAUNCHER" = "$DEF_LAUNCHER" ]; then'
        lparts = text.split(lg)
        self.assertGreaterEqual(len(lparts), 3, "expected launcher guards")
        with self.subTest(site="launcher"):
            fixture = lparts[0]
            for j in range(len(lparts) - 1):
                guard = "if true; then # " + lg if j == 1 else lg
                fixture += guard + lparts[j + 1]
            self.assertNotEqual(fixture, text)
            with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=fixture):
                with self.assertRaises(AssertionError):
                    self.test_fallback_uses_only_trusted_manifest_paths()

    def test_fallback_rejects_reintroduced_uh(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall()")
        idx = text.index("FOUND=0", anchor)
        mutated = text[:idx] + 'UH="$OPOS_HOME"\n' + text[idx:]
        self.assertNotEqual(mutated, text, "mutation must reintroduce UH")
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutated):
            with self.assertRaises(AssertionError):
                self.test_fallback_uses_only_trusted_manifest_paths()

    def test_fallback_trust_checks_accept_guarded_uvenv(self):
        text = _read_install_sh()
        plain = '"$UVENV" = "$OPOS_HOME/venv"'
        guarded_operand = '"${UVENV:-}" = "$OPOS_HOME/venv"'
        fixture = text.replace(plain, guarded_operand)
        self.assertNotIn(plain, fixture, "fixture must be fully guarded")
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=fixture):
            self.test_fallback_uses_only_trusted_manifest_paths()
        prefix = '[ "${UVENV:-}" = "$OPOS_HOME/venv" ]'
        broken = fixture.replace(prefix, "", 1)
        self.assertNotEqual(broken, fixture, "must remove one guard")
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=broken):
            with self.assertRaises(AssertionError):
                self.test_fallback_uses_only_trusted_manifest_paths()

    def test_uninstall_fallback_survives_unset_uvenv(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall()")
        head = text[anchor:anchor + 400]
        self.assertRegex(head, r'do_uninstall\(\) \{\n\s*UVENV=""')

    def test_uninstall_dry_run_removes_home_once(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        for name, use_external in (("internal", False), ("external", True)):
            with self.subTest(venv=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home_dir = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    internal_venv = opos_home / "venv"
                    external_venv = tmp_path / "external" / "opos" / "venv"
                    bindir = tmp_path / "bin"
                    opos_root = tmp_path / "root"
                    for p in (home_dir, internal_venv, external_venv, bindir, opos_root):
                        p.mkdir(parents=True)
                    internal_marker = internal_venv / "".join(["dum", "my"])
                    external_marker = external_venv / "".join(["dum", "my"])
                    internal_bytes = b"".join([b"internal-", b"ven", b"v-1"])
                    external_bytes = b"".join([b"external-", b"ven", b"v-2"])
                    internal_marker.write_bytes(internal_bytes)
                    external_marker.write_bytes(external_bytes)
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    selected = external_venv if use_external else internal_venv
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(selected) + "\nrepo=\n",
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["HOME"] = str(home_dir)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(opos_root)
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--dry-run"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    lines = proc.stdout.splitlines()
                    home_line = "==> would remove " + str(opos_home)
                    self.assertEqual(lines.count(home_line), 1, name)
                    self.assertNotIn("==> would remove " + str(internal_venv), lines, name)
                    self.assertNotIn("==> would remove " + str(external_venv), lines, name)
                    self.assertEqual(internal_marker.read_bytes(), internal_bytes, name)
                    self.assertEqual(external_marker.read_bytes(), external_bytes, name)

    def test_uninstall_ignores_manifest_launcher(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            expected = bindir / "opos"
            expected.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            expected.chmod(expected.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            other = bindir / "unrelated"
            other.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            other.chmod(other.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(other) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
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
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(
                "==> would run " + str(expected) + " uninstall --dry-run",
                proc.stdout.splitlines(),
            )
            self.assertIn(str(other), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertNotIn("would run " + str(other), proc.stdout + proc.stderr)

    def test_uninstall_rejects_replaced_recorded_launcher(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        for flags in ([], ["--force"], ["--dry-run"]):
            with self.subTest(flags=flags):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos = tmp_path / "opos"
                    venv = opos / "venv"
                    bindir = tmp_path / "bin"
                    root = tmp_path / "root"
                    for p in (home, venv, bindir, root):
                        p.mkdir(parents=True)
                    marker = tmp_path / "ran"
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    recorded = subprocess.run(["cksum"], input=launcher.read_bytes(), capture_output=True, check=True).stdout.decode().strip()
                    (opos / "manifest").write_text("launcher=" + str(launcher) + "\nvenv=" + str(venv) + "\nrepo=\nlauncher_cksum=" + recorded + "\n", encoding="utf-8")
                    launcher.write_text("#!/bin/sh\n# replaced\ntouch \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    launcher_before = launcher.read_bytes()
                    manifest_before = (opos / "manifest").read_bytes()
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    proc = subprocess.run(["sh", str(install), "--uninstall", *flags], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 1)
                    self.assertIn("warning: ownership verification failed: " + str(launcher), proc.stderr)
                    self.assertFalse(marker.exists())
                    self.assertEqual(launcher.read_bytes(), launcher_before)
                    self.assertEqual((opos / "manifest").read_bytes(), manifest_before)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos = tmp_path / "opos"
            venv = opos / "venv"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            for p in (home, venv, bindir, root):
                p.mkdir(parents=True)
            marker = tmp_path / "ran"
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            recorded = subprocess.run(["cksum"], input=launcher.read_bytes(), capture_output=True, check=True).stdout.decode().strip()
            (opos / "manifest").write_text("launcher=" + str(launcher) + "\nvenv=" + str(venv) + "\nrepo=\nlauncher_cksum=" + recorded + "\n", encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            proc = subprocess.run(["sh", str(install), "--uninstall", "--dry-run"], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("==> would run " + str(launcher) + " uninstall --dry-run", proc.stdout.splitlines())
            self.assertFalse(marker.exists())

    def test_uninstall_manifest_launcher_execution_gate(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        cases = ["outside", "dotdot", "symlink", "directory", "default_launcher", "empty_opos_home"]
        for name in cases:
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = pathlib.Path(os.path.realpath(tmp))
                    home_dir = root / "home"
                    xdg = root / "xdg"
                    bindir = root / "bin"
                    if name == "empty_opos_home":
                        opos_home = xdg / "opos"
                    else:
                        opos_home = root / "opos"
                    home_dir.mkdir(parents=True)
                    xdg.mkdir(parents=True)
                    bindir.mkdir(parents=True)
                    opos_home.mkdir(parents=True)
                    mal_marker = root / "malicious.marker"
                    def_marker = root / "default.marker"
                    outside = root / "outside"
                    if name == "directory":
                        launch_dir = root / "launcher-dir"
                        launch_dir.mkdir()
                        value = str(launch_dir)
                    else:
                        outside.write_text(
                            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$OPOS_TEST_MAL_MARKER\"\nexit 42\n",
                            encoding="utf-8",
                        )
                        outside.chmod(outside.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        if name == "dotdot":
                            value = str(opos_home) + "/../outside"
                        elif name == "symlink":
                            link = opos_home / "launcher-link"
                            link.symlink_to(outside)
                            value = str(link)
                        else:
                            value = str(outside)
                    if name == "default_launcher" or name == "empty_opos_home":
                        default = bindir / "opos"
                        default.write_text(
                            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$OPOS_TEST_DEF_MARKER\"\nexit 42\n",
                            encoding="utf-8",
                        )
                        default.chmod(default.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (opos_home / "manifest").write_text(
                        "launcher=" + value + "\nvenv=" + str(opos_home) + "/venv\nrepo=\n",
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["HOME"] = str(home_dir)
                    env["XDG_DATA_HOME"] = str(xdg)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_TEST_MAL_MARKER"] = str(mal_marker)
                    env["OPOS_TEST_DEF_MARKER"] = str(def_marker)
                    if name == "empty_opos_home":
                        env["OPOS_HOME"] = ""
                    else:
                        env["OPOS_HOME"] = str(opos_home)
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                    )
                    diag = proc.stdout + proc.stderr
                    if name == "default_launcher" or name == "empty_opos_home":
                        self.assertEqual(proc.returncode, 42, diag)
                        self.assertEqual(def_marker.read_text(encoding="utf-8"), "uninstall\n", diag)
                        self.assertFalse(mal_marker.exists(), diag)
                    else:
                        self.assertEqual(proc.returncode, 1, diag)
                        self.assertFalse(mal_marker.exists(), diag)

    def test_f992_sshd_leaves_unverified_customized_active_config(self):
        text = _read_install_sh()
        uninstall = text.index("do_uninstall() {")
        sshd_start = text.index('    _sshd="skipped"', uninstall)
        fw_leaf = "".join(["fire", "wall.state"])
        fw_anchor = 'if [ -f "$OPOS_HOME/' + fw_leaf + '"'
        fw_idx = text.index(fw_anchor, sshd_start)
        line_start = text.rindex("\n", 0, fw_idx) + 1
        body = text[sshd_start:line_start]
        prod_prefix = "/etc/" + "ssh/"
        self.assertIn(prod_prefix, body)
        cases = ("missing_expected", "mismatched_expected")
        for name in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    sshdir = tmp_path / "ssh"
                    dropin_dir = sshdir / ("sshd_config" + ".d")
                    dropin_dir.mkdir(parents=True)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    admin_user = "".join(["admin@", "example.", "invalid"])
                    marker = "# Managed by " + "OPOS - sshd drop-in\n"
                    active_dropin = dropin_dir / "60-opos.conf"
                    active_dropin.write_bytes((marker + "Port 2222\nAllowUsers " + admin_user + "\n").encode())
                    dest = sshdir / ("sshd_" + "config")
                    dest.write_bytes(b"Port 2222\n")
                    bak = opos_home / ("sshd_config" + ".bak")
                    bak.write_bytes(b"Port 22\n")
                    expected = opos_home / "sshd_dropin.expected"
                    legacy = opos_home / "sshd_dropin"
                    if name == "mismatched_expected":
                        expected.write_bytes((marker + "Port 2222\n").encode())
                    self.assertFalse(expected.is_file() and name == "missing_expected")
                    before_dropin = active_dropin.read_bytes()
                    before_dest = dest.read_bytes()
                    before_bak = bak.read_bytes()
                    script = body.replace(prod_prefix, str(sshdir) + "/")
                    self.assertNotIn(prod_prefix, script)
                    script += '\nprintf "%s" "$_sshd"\n'
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(opos_home)
                    env["HOME"] = str(tmp_path)
                    proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(active_dropin.read_bytes(), before_dropin, name)
                    self.assertEqual(dest.read_bytes(), before_dest, name)
                    self.assertEqual(bak.read_bytes(), before_bak, name)
                    self.assertEqual(proc.stdout, "failed", name)
                    self.assertIn("manual sshd drop-in restoration required", proc.stderr, name)
                    self.assertIn(str(active_dropin), proc.stderr, name)

    def test_restore_steps_report_failures_and_preserve_sshd_backup(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        start = text.index("FOUND=0", anchor)
        stop = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        end = text.index(stop)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest.write_bytes(b"".join([b"dest-bytes-", b"31"]))
            sentinel = b"".join([b"bak-bytes-", b"42"])
            bak = opos_home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(sentinel)
            state = opos_home / "".join(["fire", "wall.state"])
            state.write_bytes(b"inactive\n")
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            markers = {}
            for tool in ("cp", "ufw"):
                marker = tmp_path / (tool + ".invoked")
                markers[tool] = marker
                stub = stubdir / tool
                stub.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            marker = tmp_path / "systemctl.invoked"
            markers["systemctl"] = marker
            stub = stubdir / "systemctl"
            stub.write_text(
                "#!/bin/sh\ntouch \"" + str(marker) + "\"\n"
                + 'case "$*" in *list-unit-files*) printf \'opos-server.service disabled\\n\'; exit 0;; esac\n'
                + "exit 1\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            stat_stub = stubdir / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_stub = stubdir / "sshd"
            sshd_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = fragment.replace(dest_literal, str(dest))
            preamble = (
                "is_hardening_active() { return 1; }\n"
                + "canon_repo() { printf '%s' \"$1\"; }\n"
                + "print_hardening_instructions() { :; }\n"
                + "opos_owns_linger() { return 1; }\n"
                + "is_linger_enabled() { return 1; }\n"
            )
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["DRY"] = "0"
            env["UVENV"] = ""
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for tool, marker in markers.items():
                self.assertTrue(marker.is_file(), tool + " stub must run")
            self.assertIn("==> restore summary: sshd=failed firewall=failed service=failed", proc.stdout.splitlines())
            self.assertEqual(bak.read_bytes(), sentinel)
            self.assertEqual(dest.read_bytes(), b"".join([b"dest-bytes-", b"31"]))

    def test_fallback_preserves_prior_firewall_failure(self):
        text = _read_install_sh()
        leaf = "".join(["fire", "wall.state"])
        fail_mark = "".join(["fire", "wall=failed"])
        ok_mark = "".join(["fire", "wall=succeeded"])
        anchor = 'if [ -f "$OPOS_HOME/' + leaf + '" ]'
        start = text.index(anchor)
        line_start = text.rindex("\n", 0, start) + 1
        tail = text[line_start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[line_start:line_start + used]
        self.assertIn(leaf, block)
        self.assertIn("==> restore summary", text)
        for state in ("active", "on", "inactive", "off"):
            with self.subTest(state=state, mode="preserve"):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    before = (state + "\n").encode()
                    saved = opos_home / leaf
                    saved.write_bytes(before)
                    stubdir = tmp_path / "stubs"
                    stubdir.mkdir()
                    log = tmp_path / "ufw.log"
                    stub = stubdir / "ufw"
                    stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 0\n", encoding="utf-8")
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    script = "OPOS_HOME=\"" + str(opos_home) + "\"\n_firewall=\"failed\"\n" + block + "\nprintf '%s' \"$_firewall\"\n"
                    runner = tmp_path / "run.sh"
                    runner.write_text(script, encoding="utf-8")
                    env = dict(os.environ)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout, "failed", state)
                    self.assertEqual(saved.read_bytes(), before, state)
                    self.assertFalse(log.exists(), state + " must make no UFW calls")
        for state in ("active", "on", "inactive", "off"):
            with self.subTest(state=state, mode="control"):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    saved = opos_home / leaf
                    saved.write_bytes((state + "\n").encode())
                    stubdir = tmp_path / "stubs"
                    stubdir.mkdir()
                    log = tmp_path / "ufw.log"
                    stub = stubdir / "ufw"
                    stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 0\n", encoding="utf-8")
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    script = "OPOS_HOME=\"" + str(opos_home) + "\"\n_firewall=\"skipped\"\n" + block + "\nprintf '%s' \"$_firewall\"\n"
                    runner = tmp_path / "run.sh"
                    runner.write_text(script, encoding="utf-8")
                    env = dict(os.environ)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout, "succeeded", state)
                    self.assertFalse(saved.exists(), state)
                    if log.exists():
                        calls = log.read_text(encoding="utf-8").splitlines()
                    else:
                        calls = []
                    if state in ("active", "on"):
                        self.assertEqual(calls, [], state)
                    else:
                        self.assertIn("".join(["--", "force disable"]), calls, state)
                        self.assertIn("reload", calls, state)
        self.assertTrue(fail_mark is not None)
        self.assertTrue(ok_mark is not None)

    def test_restore_reports_skipped_sshd_and_retains_backup(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        start = text.index("FOUND=0", anchor)
        stop = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        end = text.index(stop)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        dropin_literal = "/etc/ssh/" + "sshd_config.d/60-opos.conf"
        self.assertIn(dest_literal, fragment)
        self.assertIn("==> restore summary", fragment)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest_bytes = b"".join([b"dest-bytes-", b"31"])
            dest.write_bytes(dest_bytes)
            sentinel = b"".join([b"bak-bytes-", b"43"])
            keep = opos_home / "keep"
            keep.write_bytes(sentinel)
            bak = opos_home / "".join(["sshd_config", ".bak"])
            state = opos_home / "".join(["fire", "wall.state"])
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            self.assertFalse(bak.exists())
            self.assertFalse(state.exists())
            self.assertFalse(unit.exists())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            markers = {}
            for tool in ("cp", "ufw", "stat", "sshd", "mv"):
                marker = tmp_path / (tool + ".invoked")
                markers[tool] = marker
                stub = stubdir / tool
                stub.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_marker = tmp_path / "systemctl.invoked"
            ctl_stub = stubdir / "systemctl"
            ctl_stub.write_text("#!/bin/sh\ntouch \"" + str(ctl_marker) + "\"\ncase \"$*\" in *list-unit-files*) exit 0;; esac\nexit 1\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = fragment.replace(dest_literal, str(dest))
            replaced = replaced.replace(dropin_literal, str(tmp_path / "no-dropin"))
            preamble = (
                "is_hardening_active() { return 1; }\n"
                + "canon_repo() { printf '%s' \"$1\"; }\n"
                + "print_hardening_instructions() { :; }\n"
                + "opos_owns_linger() { return 1; }\n"
                + "is_linger_enabled() { return 1; }\n"
            )
            checks = (
                "\n[ \"$_sshd\" = \"skipped\" ] || exit 1\n"
                + "[ \"$_firewall\" = \"skipped\" ] || exit 1\n"
                + "[ \"$_service\" = \"skipped\" ] || exit 1\n"
                + "[ \"$_restore_failed\" = \"0\" ] || exit 1\n"
            )
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced + checks, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["DRY"] = "0"
            env["UVENV"] = ""
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("==> restore summary: sshd=skipped firewall=skipped service=skipped", proc.stdout.splitlines())
            self.assertTrue(ctl_marker.is_file())
            for tool, marker in markers.items():
                self.assertFalse(marker.exists(), tool + " must not run when restoration is skipped")
            self.assertEqual(dest.read_bytes(), dest_bytes)
            self.assertEqual(keep.read_bytes(), sentinel)
            self.assertFalse(bak.exists())
            self.assertFalse(state.exists())
            self.assertFalse(unit.exists())

    def test_restore_skipped_sshd_preserves_existing_backup(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        start = text.index("FOUND=0", anchor)
        stop = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        end = text.index(stop)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        dropin_literal = "/etc/ssh/" + "sshd_config.d/60-opos.conf"
        self.assertIn(dest_literal, fragment)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest_before = b"".join([b"dest-bytes-", b"55"])
            dest.write_bytes(dest_before)
            sentinel = b"".join([b"bak-bytes-", b"44"])
            bak = opos_home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(sentinel)
            self.assertFalse((opos_home / "".join(["fire", "wall.state"])).exists())
            self.assertFalse((home / ".config" / "systemd" / "user" / "opos-server.service").exists())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            markers = {}
            for tool in ("cp", "ufw", "mv"):
                marker = tmp_path / (tool + ".invoked")
                markers[tool] = marker
                stub = stubdir / tool
                stub.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            stat_stub = stubdir / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_stub = stubdir / "sshd"
            sshd_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_marker = tmp_path / "systemctl.invoked"
            ctl_stub = stubdir / "systemctl"
            ctl_stub.write_text("#!/bin/sh\ntouch \"" + str(ctl_marker) + "\"\ncase \"$*\" in *list-unit-files*) exit 0;; esac\nexit 1\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = fragment.replace(dest_literal, str(dest))
            replaced = replaced.replace(dropin_literal, str(tmp_path / "no-dropin"))
            preamble = (
                "is_hardening_active() { return 1; }\n"
                + "canon_repo() { printf '%s' \"$1\"; }\n"
                + "print_hardening_instructions() { :; }\n"
                + "opos_owns_linger() { return 1; }\n"
                + "is_linger_enabled() { return 1; }\n"
            )
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["DRY"] = "0"
            env["UVENV"] = ""
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("==> restore summary: sshd=failed firewall=skipped service=skipped", proc.stdout.splitlines())
            self.assertTrue(ctl_marker.is_file())
            self.assertFalse(markers["ufw"].exists(), "firewall must stay skipped")
            self.assertFalse(markers["mv"].exists(), "failed sshd must not move staged file")
            self.assertEqual(dest.read_bytes(), dest_before)
            self.assertEqual(bak.read_bytes(), sentinel)

    def test_fallback_dry_run_lists_exact_actions(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall()")
        start = text.index("FOUND=0", anchor)
        end = text.index('opos_owned_path "$LAUNCHER"', start)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        self.assertIn(dest_literal, fragment)
        cases = [
            ("all_present", True, True, True),
            ("destination_missing", True, False, True),
            ("all_absent", False, False, False),
            ("files_present_tools_absent", True, True, False),
        ]
        for name, files_present, dest_present, tools_present in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    home.mkdir()
                    opos_home.mkdir()
                    unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                    bak = opos_home / "sshd_config.bak"
                    state = opos_home / "firewall.state"
                    dest = tmp_path / "sshd_config"
                    if files_present:
                        bak.write_bytes(b"backup-bytes-1")
                        state.write_bytes(b"state-bytes-2")
                        unit.parent.mkdir(parents=True, exist_ok=True)
                        unit.write_bytes(b"unit-bytes-3")
                    if dest_present:
                        dest.write_bytes(b"dest-bytes-4")
                    before = {}
                    for p in (bak, state, unit, dest):
                        if p.is_file() and not p.is_symlink():
                            before[str(p)] = p.read_bytes()
                    stubdir = tmp_path / "stubs"
                    stubdir.mkdir()
                    marker = tmp_path / "invoked"
                    if tools_present:
                        for tool in ("ufw", "systemctl"):
                            stub = stubdir / tool
                            stub.write_text("#!/bin/sh\n: > \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
                            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    replaced = fragment.replace(dest_literal, str(dest))
                    preamble = (
                        "is_hardening_active() { return 1; }\n"
                        + "print_hardening_instructions() { :; }\n"
                        + "opos_owns_linger() { return 1; }\n"
                        + "is_linger_enabled() { return 1; }\n"
                    )
                    runner = tmp_path / "run.sh"
                    runner.write_text(preamble + replaced, encoding="utf-8")
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["DRY"] = "1"
                    env["PATH"] = str(stubdir)
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(["/bin/sh", str(runner)], capture_output=True, text=True, env=env)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    lines = proc.stdout.splitlines()
                    if files_present and dest_present and tools_present:
                        expected = [
                            "==> would remove " + str(bak),
                            "==> would overwrite " + str(dest) + " from " + str(bak),
                            "==> would run ufw --force disable",
                            "==> would remove " + str(state),
                            "==> would run systemctl --user disable --now opos-server.service",
                            "==> would run systemctl --user daemon-reload",
                            "==> would remove " + str(unit),
                        ]
                        self.assertEqual(lines, expected)
                    elif files_present and not dest_present and tools_present:
                        expected = [
                            "==> would remove " + str(bak),
                            "==> would run ufw --force disable",
                            "==> would remove " + str(state),
                            "==> would run systemctl --user disable --now opos-server.service",
                            "==> would run systemctl --user daemon-reload",
                            "==> would remove " + str(unit),
                        ]
                        self.assertEqual(lines, expected)
                        self.assertNotIn("would overwrite", proc.stdout)
                    elif not files_present and not dest_present and not tools_present:
                        self.assertEqual(lines, [])
                        self.assertNotIn("would remove", proc.stdout)
                        self.assertNotIn("would overwrite", proc.stdout)
                        self.assertNotIn("would run", proc.stdout)
                    else:
                        expected = [
                            "==> would remove " + str(bak),
                            "==> would overwrite " + str(dest) + " from " + str(bak),
                            "==> would remove " + str(state),
                            "==> would remove " + str(unit),
                        ]
                        self.assertEqual(lines, expected)
                        self.assertNotIn("ufw --force disable", proc.stdout)
                        self.assertNotIn("systemctl --user disable --now", proc.stdout)
                        self.assertNotIn("systemctl --user daemon-reload", proc.stdout)
                    self.assertFalse(marker.exists(), "dry-run must not execute stubs")
                    for key, value in before.items():
                        self.assertEqual(pathlib.Path(key).read_bytes(), value)

    def test_dry_run_stub_execution_is_detectable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            marker = tmp_path / "invoked"
            for tool in ("ufw", "systemctl"):
                stub = stubdir / tool
                stub.write_text("#!/bin/sh\n: > \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["PATH"] = str(stubdir)
            for tool in ("ufw", "systemctl"):
                if marker.exists():
                    marker.unlink()
                proc = subprocess.run([str(stubdir / tool)], capture_output=True, text=True, env=env, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(marker.is_file(), tool + " stub must create marker under restricted PATH")
                with self.assertRaises(AssertionError):
                    self.assertFalse(marker.exists(), "dry-run must not execute stubs")
        text = _read_install_sh()
        prod_dest = "/etc/ssh/" + "sshd_config"
        prod_dropin = "/etc/ssh/" + "sshd_config.d/60-opos.conf"
        self.assertIn(prod_dest, text)
        self.assertIn(prod_dropin, text)
        marker_text = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker_text)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]
        self.assertIn(prod_dest, block)
        self.assertNotRegex(block, r'>\s*"/etc/ssh/')
        self.assertNotRegex(block, r'>>\s*"/etc/ssh/')
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            bak_leaf = "".join(["sshd_config", ".bak"])
            bak = opos_home / bak_leaf
            bak_bytes = b"".join([b"backup-bytes-", b"71"])
            bak.write_bytes(bak_bytes)
            dropin = tmp_path / "no-dropin"
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            prod_prefix = "/etc/" + "ssh/"
            def write_stub(path, body):
                path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            write_stub(stubdir / "stat", "printf '%s\\n' \"0 600\"\n")
            write_stub(stubdir / "sshd", "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\nexit 0\n")
            write_stub(stubdir / "mktemp", "printf 'mktemp %s\\n' \"$*\" >> \"" + str(ops) + "\"\nstaged=\"$OPOS_TMPDIR/staged.tmp\"\n: > \"$staged\"\nprintf '%s\\n' \"$staged\"\nexit 0\n")
            write_stub(stubdir / "cp", "printf 'cp %s\\n' \"$*\" >> \"" + str(ops) + "\"\ncase \"$*\" in *\"$OPOS_PROD_PREFIX\"*) exit 1;; esac\nexec /bin/cp \"$@\"\n")
            write_stub(stubdir / "mv", "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\ncase \"$*\" in *\"$OPOS_PROD_PREFIX\"*) exit 1;; esac\nexec /bin/mv \"$@\"\n")
            write_stub(stubdir / "rm", "printf 'rm %s\\n' \"$*\" >> \"" + str(ops) + "\"\ncase \"$*\" in *\"$OPOS_PROD_PREFIX\"*) exit 1;; esac\nexec /bin/rm \"$@\"\n")
            script = "_sshd=\"skipped\"\n_sshd_dropin=\"" + str(dropin) + "\"\n" + block + "\nprintf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_TMPDIR"] = str(tmp_path)
            env["OPOS_PROD_PREFIX"] = prod_prefix
            env["PATH"] = str(stubdir) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "failed")
            self.assertEqual(bak.read_bytes(), bak_bytes)
            ops_text = ops.read_text(encoding="utf-8")
            self.assertIn(prod_dest, ops_text)
            self.assertIn("mv ", ops_text)
            self.assertFalse((tmp_path / "staged.tmp").exists())
            self.assertFalse(dropin.exists())

    def test_dry_run_probe_outcomes_never_mutate(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        table_line = "".join(["table inet opos_", "filter"])
        linger_line = "".join(["Linger=", "yes"])
        mutating_nft = "".join(["delete ", "table"])
        mutating_linger = "".join(["disable", "-linger"])
        cases = (
            ("nft_ok_login_ok", True, True),
            ("nft_ok_login_fail", True, False),
            ("nft_fail_login_ok", False, True),
            ("nft_fail_login_fail", False, False),
        )
        for name, nft_ok, login_ok in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    fakebin = tmp_path / "fakebin"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, fakebin, root):
                        p.mkdir(parents=True)
                    (root / "var" / "lib" / "opos-linger").parent.mkdir(parents=True, exist_ok=True)
                    (root / "var" / "lib" / "opos-linger").write_text("owned\n", encoding="utf-8")
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                    os.symlink("".join(["dum", "my"]), str(venv_dir / "".join(["lin", "k-ok"])))
                    os.symlink("".join(["miss", "ing-target"]), str(venv_dir / "".join(["lin", "k-bad"])))
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                        encoding="utf-8",
                    )
                    state = opos_home / "".join(["fire", "wall.state"])
                    state_bytes = b"".join([b"state-bytes-", b"9"])
                    state.write_bytes(state_bytes)
                    unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                    unit.parent.mkdir(parents=True, exist_ok=True)
                    unit_bytes = b"".join([b"unit-bytes-", b"5"])
                    unit.write_bytes(unit_bytes)
                    markers = {}
                    for tool in ("ufw", "systemctl"):
                        marker = tmp_path / (tool + ".invoked")
                        markers[tool] = marker
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    nft_log = tmp_path / "nft.argv"
                    nft_stub = fakebin / "nft"
                    if nft_ok:
                        nft_body = "case \"$*\" in \"list tables\") printf '%s\\n' \"" + table_line + "\"\nexit 0;; esac\nexit 1\n"
                    else:
                        nft_body = "case \"$*\" in \"list tables\") exit 1;; esac\nexit 1\n"
                    nft_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(nft_log) + "\"\n" + nft_body, encoding="utf-8")
                    nft_stub.chmod(nft_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    loginctl_log = tmp_path / "loginctl.argv"
                    loginctl_stub = fakebin / "loginctl"
                    if login_ok:
                        login_body = "case \"$*\" in \"show-user \"*) printf '%s\\n' \"" + linger_line + "\"\nexit 0;; esac\nexit 1\n"
                    else:
                        login_body = "case \"$*\" in \"show-user \"*) exit 1;; esac\nexit 1\n"
                    loginctl_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(loginctl_log) + "\"\n" + login_body, encoding="utf-8")
                    loginctl_stub.chmod(loginctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    manifest = opos_home / "manifest"
                    linger = root / "var" / "lib" / "".join(["opos-", "linger"])
                    user = env["USER"]
                    recorded = [launcher, manifest, state, unit, linger, venv_dir]
                    for _base, _dirs, _files in os.walk(str(venv_dir)):
                        for _n in _dirs + _files:
                            _full = pathlib.Path(_base) / _n
                            if str(_full) not in [str(_p) for _p in recorded]:
                                recorded.append(_full)
                    snaps = {}
                    for _p in recorded:
                        _st = os.lstat(str(_p))
                        _mode = stat.S_IMODE(_st.st_mode)
                        if stat.S_ISLNK(_st.st_mode):
                            _kind = "link"
                        elif stat.S_ISDIR(_st.st_mode):
                            _kind = "dir"
                        elif stat.S_ISREG(_st.st_mode):
                            _kind = "file"
                        else:
                            _kind = "other"
                        _data = _artifact_data(str(_p), _kind)
                        snaps[str(_p)] = (_kind, _mode, _data)
                    venv_inventory = sorted(os.path.relpath(str(_p), str(venv_dir)) for _p in recorded if str(_p) == str(venv_dir) or str(_p).startswith(str(venv_dir) + os.sep))
                    venv_inventory = sorted(os.path.relpath(os.path.join(_b, _n), str(venv_dir)) for _b, _ds, _fs in os.walk(str(venv_dir)) for _n in _ds + _fs)
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--dry-run", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn("".join(["--", "force disable"]), proc.stdout)
                    self.assertIn("disable --now opos-server.service", proc.stdout)
                    nft_advice = "".join(["==> then run: sudo nft ", "delete table inet opos_", "filter"])
                    if nft_ok:
                        self.assertIn(nft_advice, proc.stdout)
                    else:
                        self.assertNotIn(nft_advice, proc.stdout)
                    self.assertIn("".join(["==> linger is enabled: run sudo loginctl ", "disable", "-linger ", user]), proc.stdout)
                    for tool, marker in markers.items():
                        self.assertFalse(marker.exists(), tool + " must not run during dry-run")
                    _current = sorted(os.path.relpath(os.path.join(_b, _n), str(venv_dir)) for _b, _ds, _fs in os.walk(str(venv_dir)) for _n in _ds + _fs)
                    self.assertEqual(sorted(_current), venv_inventory, "virtual-environment inventory must be unchanged")
                    for _p_str, (_kind, _mode, _data) in snaps.items():
                        self.assertTrue(os.path.lexists(_p_str), "artifact must still exist: " + _p_str)
                        _st = os.lstat(_p_str)
                        if stat.S_ISLNK(_st.st_mode):
                            _now_kind = "link"
                        elif stat.S_ISDIR(_st.st_mode):
                            _now_kind = "dir"
                        elif stat.S_ISREG(_st.st_mode):
                            _now_kind = "file"
                        else:
                            _now_kind = "other"
                        self.assertEqual(_now_kind, _kind, "artifact type must be unchanged: " + _p_str)
                        self.assertEqual(stat.S_IMODE(_st.st_mode), _mode, "artifact permissions must be unchanged: " + _p_str)
                        self.assertEqual(_artifact_data(_p_str, _now_kind), _data, "artifact bytes must be unchanged: " + _p_str)
                    self.assertTrue(nft_log.is_file(), "nft probe must run")
                    self.assertTrue(loginctl_log.is_file(), "loginctl probe must run")
                    nft_calls = nft_log.read_text(encoding="utf-8").splitlines()
                    loginctl_calls = loginctl_log.read_text(encoding="utf-8").splitlines()
                    self.assertTrue(nft_calls, "nft log must be nonempty")
                    self.assertTrue(loginctl_calls, "loginctl log must be nonempty")
                    for line in nft_calls:
                        self.assertEqual(line, "list tables")
                    for line in loginctl_calls:
                        self.assertEqual(line, "show-user " + user)
                    combined = "\n".join(nft_calls + loginctl_calls)
                    self.assertNotIn(mutating_nft, combined)
                    self.assertNotIn(mutating_linger, combined)

    def test_artifact_data_detects_symlink_retargeting(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            first = tmp_path / "".join(["fi", "rst"])
            second = tmp_path / "".join(["sec", "ond"])
            payload = b"".join([b"same-", b"bytes-1"])
            first.write_bytes(payload)
            second.write_bytes(payload)
            link = tmp_path / "".join(["li", "nk"])
            os.symlink(first.name, str(link))
            saved = _artifact_data(str(link), "link")
            self.assertEqual(saved, first.name)
            os.unlink(str(link))
            os.symlink(second.name, str(link))
            current = _artifact_data(str(link), "link")
            self.assertEqual(current, second.name)
            self.assertNotEqual(saved, current)
            missing_one = "".join(["miss", "ing-one"])
            missing_two = "".join(["miss", "ing-two"])
            os.unlink(str(link))
            os.symlink(missing_one, str(link))
            saved = _artifact_data(str(link), "link")
            self.assertEqual(saved, missing_one)
            os.unlink(str(link))
            os.symlink(missing_two, str(link))
            current = _artifact_data(str(link), "link")
            self.assertEqual(current, missing_two)
            self.assertNotEqual(saved, current)

    def test_dry_run_preserves_firewall_state_and_service_unit(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_leaf = "".join(["fire", "wall.state"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, fakebin, root):
                p.mkdir(parents=True)
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_dir = opos_home / "venv"
            venv_dir.mkdir(parents=True)
            (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
            (opos_home / "manifest").write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            state = opos_home / state_leaf
            state_bytes = b"".join([b"state-bytes-", b"77"])
            state.write_bytes(state_bytes)
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit_bytes = b"".join([b"unit-bytes-", b"88"])
            unit.write_bytes(unit_bytes)
            for tool in ("ufw", "systemctl"):
                stub = fakebin / tool
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            for name in ("loginctl", "nft"):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(state.read_bytes(), state_bytes)
            self.assertEqual(unit.read_bytes(), unit_bytes)

    def test_repo_canonicalized_and_mismatch_reported(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def run(home, bindir, args):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"] + args,
                capture_output=True,
                text=True,
                env=env,
            )

        def make_recovery_only(home, repo_value):
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(home.parent / "bin" / "opos") + "\n"
                + "venv=" + str(home / "venv") + "\n"
                + "repo=" + repo_value + "\n",
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            make_recovery_only(home, str(root) + "/")
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            variant = str(root) + "/."
            make_recovery_only(home, variant)
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            link = pathlib.Path(tmp) / "link"
            link.symlink_to(root)
            make_recovery_only(home, str(link))
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            recorded = str(pathlib.Path(tmp) / "elsewhere")
            pathlib.Path(recorded).mkdir()
            make_recovery_only(home, recorded)
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("repo mismatch: run the uninstaller from the original checkout", proc.stderr.splitlines())
            self.assertNotIn(recorded, proc.stdout + proc.stderr)

    def test_live_recovery_canonicalizes_repo_and_preserves_mismatches(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]
        resolved = os.path.realpath(str(root))
        cases = [
            ("launcher_absent_equivalent", False, True),
            ("launcher_absent_mismatch", False, False),
            ("launcher_fails_equivalent", True, True),
            ("launcher_fails_mismatch", True, False),
        ]
        for name, with_launcher, equivalent in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    elsewhere = tmp_path / "elsewhere"
                    for p in (home, opos, bindir, elsewhere):
                        p.mkdir()
                    recorded = resolved + "/" if equivalent else str(elsewhere)
                    launcher = bindir / "opos"
                    venv_python = opos / "venv" / "bin" / "python"
                    venv_python.parent.mkdir(parents=True, exist_ok=True)
                    log = tmp_path / "recovery.log"
                    venv_python.write_text(
                        "#!/bin/sh\n"
                        # Installer probes recovery python with -c; keep the probe out of the log.
                        + 'if [ "$1" = "-c" ]; then exit 0; fi\n'
                        + "printf '%s\\n' \"$@\" >> \"" + str(log) + "\"\n"
                        + "exit 42\n",
                        encoding="utf-8",
                    )
                    venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    if with_launcher:
                        launcher.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
                        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (opos / "manifest").write_text(
                        "launcher=" + str(launcher) + "\n"
                        + "venv=" + str(opos / "venv") + "\n"
                        + "repo=" + recorded + "\n",
                        encoding="utf-8",
                    )
                    keep = opos / "keep"
                    keep.write_bytes(b"keep-bytes")
                    venv_keep = opos / "venv" / "keep"
                    venv_keep.write_bytes(b"venv-keep-bytes")
                    before = {}
                    for p in (opos / "manifest", venv_python, keep, venv_keep):
                        before[str(p)] = p.read_bytes()
                    if with_launcher:
                        before[str(launcher)] = launcher.read_bytes()
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                    )
                    if equivalent:
                        self.assertEqual(proc.returncode, 42, proc.stdout + proc.stderr)
                        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["-m", "opos.setup", "uninstall"])
                    else:
                        self.assertEqual(proc.returncode, 127 if with_launcher else 1, proc.stdout + proc.stderr)
                        self.assertIn("repo mismatch: run the uninstaller from the original checkout", proc.stderr.splitlines())
                        self.assertNotIn(recorded, proc.stdout + proc.stderr)
                        self.assertFalse(log.exists())
                    for key, value in before.items():
                        self.assertTrue(pathlib.Path(key).is_file())
                        self.assertEqual(pathlib.Path(key).read_bytes(), value)
                    if not with_launcher:
                        self.assertFalse(launcher.exists())

    def test_repo_mismatch_diagnostics_hide_recorded_path(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        want = "repo mismatch: run the uninstaller from the original checkout"
        marker = "".join(["private", "-user"])
        cases = [
            ("absent_dry", True, False, 1),
            ("absent_live", False, False, 1),
            ("fails_127", False, True, 127),
        ]
        for name, dry, with_launcher, want_rc in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    bindir = tmp_path / "bin"
                    recorded_dir = tmp_path / marker / "checkout"
                    recorded_dir.mkdir(parents=True)
                    home.mkdir()
                    bindir.mkdir()
                    recorded = str(recorded_dir)
                    venv_python = home / "venv" / "bin" / "python"
                    venv_python.parent.mkdir(parents=True, exist_ok=True)
                    venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    launcher = bindir / "opos"
                    if with_launcher:
                        launcher.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
                        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\n"
                        + "venv=" + str(home / "venv") + "\n"
                        + "repo=" + recorded + "\n",
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    args = ["sh", "--", str(install), "--uninstall"]
                    if dry:
                        args.append("--dry-run")
                    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, want_rc, proc.stdout + proc.stderr)
                    self.assertIn(want, proc.stderr.splitlines())
                    self.assertNotIn(recorded, proc.stdout + proc.stderr)
                    self.assertNotIn(marker, proc.stdout + proc.stderr)

    def test_recovery_python_ignores_caller_cwd(self):
        text = _read_install_sh()
        lines = [line for line in text.splitlines() if "-m opos.setup" in line and ("UVENV/bin/python" in line or "_recovery_python" in line)]
        self.assertEqual(len(lines), 1, "expected one verified recovery entry point")
        for line in lines:
            self.assertTrue(
                "PYTHONSAFEPATH=1" in line or 'cd -- "$ROOT/src"' in line,
                "recovery python -m must not import caller cwd: %r" % line,
            )

    def test_failed_launcher_recovers_with_system_python(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]
        resolved_root = pathlib.Path(os.path.realpath(str(root)))
        cases = [
            ("exit_1_executable_venv_fails", 1, "file"),
            ("exit_127_dangling_venv", 127, "dangling"),
        ]
        for name, launcher_exit, venv_kind in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    bindir = tmp_path / "bin"
                    tools = tmp_path / "tools"
                    home.mkdir()
                    bindir.mkdir()
                    tools.mkdir()
                    venv_dir = home / "venv"
                    venv_bin = venv_dir / "bin"
                    venv_bin.mkdir(parents=True)
                    venv_python = venv_bin / "python"
                    if venv_kind == "file":
                        venv_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    else:
                        os.symlink(str(tmp_path / "missing-interpreter"), str(venv_python))
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit " + str(launcher_exit) + "\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(resolved_root) + "\n",
                        encoding="utf-8",
                    )
                    log = tmp_path / "recovery.log"
                    log.write_text("", encoding="utf-8")
                    stub = tools / "python3"
                    stub.write_text(
                        "#!/bin/sh\n"
                        + 'if [ "$1" = "-c" ]; then exit 0; fi\n'
                        + "printf '%s\\n' \"$*\" >> \"" + str(log) + "\"\n"
                        + "pwd -P >> \"" + str(log) + "\"\n"
                        + "printf '%s\\n' \"$PYTHONPATH\" >> \"" + str(log) + "\"\n"
                        + "printf '%s\\n' \"$PYTHONSAFEPATH\" >> \"" + str(log) + "\"\n"
                        + "exit 42\n",
                        encoding="utf-8",
                    )
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["PATH"] = str(tools) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--keep-config"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 42, proc.stdout + proc.stderr)
                    records = log.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(len(records), 4, records)
                    self.assertEqual(records[0], "-m opos.setup uninstall --keep-config")
                    self.assertEqual(records[1], str(resolved_root / "src"))
                    self.assertEqual(records[2], str(resolved_root / "src"))
                    self.assertEqual(records[3], "1")
                    self.assertTrue(launcher.is_file(), "launcher must be retained on recovery failure")
                    self.assertTrue((home / "manifest").is_file(), "manifest must be retained on recovery failure")
                    self.assertTrue(venv_dir.is_dir(), "venv directory must be retained on recovery failure")

    def test_recovery_ownership_all_branches(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        install = root / "install.sh"
        cases = []
        for mode in ("absent-dry", "absent-exec", "126", "127"):
            for venv in ("owned", "external"):
                cases.append((mode, venv))
        for mode, venv in cases:
            with self.subTest(launcher=mode, venv=venv):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    bindir = tmp_path / "bin"
                    outside = tmp_path / "external"
                    for p in (home, bindir, outside):
                        p.mkdir(parents=True)
                    owned_venv = home / "venv"
                    ext_venv = outside / "venv"
                    picked = owned_venv if venv == "owned" else ext_venv
                    (picked / "bin").mkdir(parents=True)
                    ilog = tmp_path / "interp.log"
                    llog = tmp_path / "launcher.log"
                    interp = picked / "bin" / "python"
                    interp.write_text(
                        "#!/bin/sh\n"
                        + 'if [ "$1" = "-c" ]; then exit 0; fi\n'
                        + "printf '%s\\n' \"$*\" >> \"" + str(ilog) + "\"\n"
                        + "exit 42\n",
                        encoding="utf-8",
                    )
                    interp.chmod(interp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    launcher = bindir / "opos"
                    status = None
                    if mode in ("126", "127"):
                        status = int(mode)
                        launcher.write_text(
                            "#!/bin/sh\n"
                            + "printf '%s\\n' \"$*\" >> \"" + str(llog) + "\"\n"
                            + "exit " + str(status) + "\n",
                            encoding="utf-8",
                        )
                        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(picked) + "\nrepo=" + str(root) + "\n",
                        encoding="utf-8",
                    )
                    home_keep = home / "keep"
                    ext_keep = outside / "keep"
                    home_keep.write_bytes(b"".join([b"home-", b"keep-", mode.encode(), b"-", venv.encode()]))
                    ext_keep.write_bytes(b"".join([b"ext-", b"keep-", mode.encode(), b"-", venv.encode()]))
                    snaps = {}
                    for p in (home / "manifest", interp, home_keep, ext_keep):
                        snaps[str(p)] = p.read_bytes()
                    if launcher.is_file():
                        snaps[str(launcher)] = launcher.read_bytes()
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    args = ["sh", str(install), "--uninstall"]
                    dry = mode == "absent-dry"
                    if dry:
                        args.append("--dry-run")
                    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
                    is_owned = venv == "owned"
                    if dry and is_owned:
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        want = "==> would run recovery entry point " + str(owned_venv / "bin" / "python") + " uninstall --dry-run"
                        self.assertIn(want, proc.stdout.splitlines())
                        self.assertFalse(ilog.exists())
                    elif dry and not is_owned:
                        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                        self.assertIn("would fail: launcher not found", proc.stderr)
                        self.assertFalse(ilog.exists())
                    elif not dry and is_owned:
                        self.assertEqual(proc.returncode, 42, proc.stdout + proc.stderr)
                        self.assertEqual(ilog.read_text(encoding="utf-8").splitlines(), ["-m opos.setup uninstall"])
                    else:
                        self.assertFalse(ilog.exists())
                        if mode == "absent-exec":
                            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                        else:
                            self.assertEqual(proc.returncode, status, proc.stdout + proc.stderr)
                    if mode in ("126", "127") and not dry:
                        self.assertEqual(llog.read_text(encoding="utf-8").splitlines(), ["uninstall"])
                    else:
                        self.assertFalse(llog.exists())
                    for key, value in snaps.items():
                        self.assertTrue(pathlib.Path(key).is_file(), key)
                        self.assertEqual(pathlib.Path(key).read_bytes(), value, key)

    def test_recovery_verification_is_shared(self):
        text = _read_install_sh()
        self.assertEqual(len(re.findall(r"^verified_recovery\(\)", text, re.MULTILINE)), 1, "exactly one helper definition")
        start = text.index("verified_recovery()")
        end = text.index("do_uninstall()", start)
        helper = text[start:end]
        self.assertIn('[ -f "$MANIFEST" ]', helper)
        self.assertRegex(helper, r'\[ "(\$\{UVENV:-\}|\$UVENV)" = "\$OPOS_HOME/venv" \]')
        self.assertIn('opos_owned_path "$UVENV" "$OPOS_HOME/venv"', helper)
        self.assertIn('[ -x "$UVENV/bin/python" ]', helper)
        self.assertIn('[ -d "$ROOT/src/opos" ]', helper)
        self.assertIn('canon_repo "$UREPO"', helper)
        self.assertIn('canon_repo "$ROOT"', helper)
        helper_start = text.index("run_verified_recovery()")
        helper_end = text.index("do_uninstall()", helper_start)
        helper_fn = text[helper_start:helper_end]
        anchor = text.index("do_uninstall()")
        uninstall_body = text[anchor:]
        helper_calls = re.findall(r"if verified_recovery; then", helper_fn)
        body_calls = re.findall(r"if verified_recovery; then", uninstall_body)
        self.assertEqual(len(helper_calls), 1, "exactly one conditional verification call inside execution helper")
        self.assertEqual(len(body_calls), 1, "exactly one conditional verification call inside do_uninstall")
        self.assertNotIn('[ -x "$UVENV/bin/python" ]', uninstall_body, "no executable-Python check may remain in do_uninstall")



    def test_uninstall_recovery_uses_one_helper(self):
        text = _read_install_sh()
        self.assertEqual(len(re.findall(r"^run_verified_recovery\(\)", text, re.MULTILINE)), 1, "exactly one execution-helper definition")
        verified_start = text.index("verified_recovery()")
        helper_start = text.index("run_verified_recovery()", verified_start)
        uninstall_start = text.index("do_uninstall()", helper_start)
        verified_block = text[verified_start:helper_start]
        helper_block = text[helper_start:uninstall_start]
        uninstall_body = text[uninstall_start:]
        self.assertRegex(helper_block, r"(?m)^\s*if verified_recovery; then", "execution helper must call verification conditionally")
        self.assertIn("_verified=1", helper_block)
        lines = [line for line in text.splitlines() if "UVENV/bin/python" in line and "-m opos.setup" in line]
        self.assertEqual(len(lines), 1, "exactly one verified recovery command")
        self.assertIn(lines[0], helper_block, "recovery command must live inside execution helper")
        calls = re.findall(r'run_verified_recovery "\$@"', uninstall_body)
        self.assertEqual(len(calls), 2, "do_uninstall must delegate twice to execution helper")
        self.assertIn('[ -f "$MANIFEST" ]', verified_block)
        self.assertRegex(verified_block, r'\[ "(\$\{UVENV:-\}|\$UVENV)" = "\$OPOS_HOME/venv" \]')
        self.assertIn('opos_owned_path "$UVENV" "$OPOS_HOME/venv"', verified_block)
        self.assertIn('[ -x "$UVENV/bin/python" ]', verified_block)
        self.assertIn('[ -d "$ROOT/src/opos" ]', verified_block)
        self.assertIn('canon_repo "$UREPO"', verified_block)
        self.assertIn('canon_repo "$ROOT"', verified_block)

    def test_linger_advice_requires_opos_ownership(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def run_uninstall(home, bindir, root, faked):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(faked) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run", "--force"],
                capture_output=True,
                text=True,
                env=env,
            )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            faked = tmp_path / "fakebin"
            for p in (home, bindir, root, faked):
                p.mkdir(parents=True)
            stub = faked / "loginctl"
            stub.write_text("#!/bin/sh\nprintf 'Linger=yes\\n'\nexit 0\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            marker = root / "var" / "lib" / "opos-linger"
            advice = "".join(["disable", "-linger"])
            proc = run_uninstall(home, bindir, root, faked)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(advice, proc.stdout + proc.stderr)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("owned\n", encoding="utf-8")
            proc = run_uninstall(home, bindir, root, faked)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(advice, proc.stdout + proc.stderr)

    def test_linger_advice_real_uninstall_requires_ownership(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def run_real(home, bindir, root, faked):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(faked) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
            )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            faked = tmp_path / "fakebin"
            for p in (home, bindir, root, faked):
                p.mkdir(parents=True)
            trusted = bindir / "opos"
            trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(trusted) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            login_stub = faked / "loginctl"
            login_stub.write_text("#!/bin/sh\nprintf 'Linger=yes\\n'\nexit 0\n", encoding="utf-8")
            login_stub.chmod(login_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            for name in ("cp", "ufw", "systemctl"):
                stub = faked / name
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            advice = "".join(["disable", "-linger"])
            proc = run_real(home, bindir, root, faked)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(advice, proc.stdout + proc.stderr)
            marker = root / "var" / "lib" / "opos-linger"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("owned\n", encoding="utf-8")
            proc = run_real(home, bindir, root, faked)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(advice, proc.stdout + proc.stderr)

    def test_recovery_stub_positive_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            stub = home / "venv" / "bin" / "python"
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text("#!/bin/sh\nif [ $# -eq 3 ] && [ \"$1\" = \"-m\" ] && [ \"$2\" = \"opos.setup\" ] && [ \"$3\" = \"uninstall\" ]; then\ntouch \"$OPOS_RECOVERY_MARKER\"\nexit 42\nfi\nexit 1\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            marker = home / ".recovery_invoked"
            env = dict(os.environ)
            env["OPOS_RECOVERY_MARKER"] = str(marker)
            proc = subprocess.run([str(stub), "-m", "opos.setup", "uninstall"], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 42, "stub must exit with distinctive code")
            self.assertTrue(marker.is_file(), "stub must create marker so dry-run check can fail")
            marker.unlink()
            proc = subprocess.run([str(stub)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 1, "stub must fail without recovery entry point")
            self.assertFalse(marker.is_file(), "stub must not create marker without recovery entry point")

    def test_verified_recovery_blocks_each_failed_guard(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def make_venv(home):
            target = home / "venv" / "bin" / "python"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def write_manifest(home, launcher, venv, repo):
            (home / "manifest").write_text(
                "launcher=" + launcher + "\nvenv=" + venv + "\nrepo=" + repo + "\n",
                encoding="utf-8",
            )

        def run_dry(home, bindir, extra=None):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            if extra:
                env.update(extra)
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            make_venv(home)
            write_manifest(home, str(home.parent / "bin" / "opos"), str(home / "venv"), str(root))
            proc = run_dry(home, bindir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            for p in (home, bindir, fakebin):
                p.mkdir(parents=True)
            marker = tmp_path / "recovery-marker"
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            write_manifest(home, str(bindir / "opos"), str(home / "venv"), str(root))
            for tool in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd"):
                stub = fakebin / tool
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            stat_stub = fakebin / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(install), "--uninstall", "--force"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(marker.is_file(), "valid control must execute recovery")
            self.assertIn("==> restore summary:", proc.stdout)

        failing = ["missing_manifest", "venv_mismatch", "python_missing", "repo_mismatch", "src_missing"]
        for name in failing:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    home = pathlib.Path(tmp) / "home"
                    bindir = pathlib.Path(tmp) / "bin"
                    home.mkdir()
                    bindir.mkdir()
                    extra = None
                    if name == "missing_manifest":
                        make_venv(home)
                    elif name == "venv_mismatch":
                        make_venv(home)
                        write_manifest(home, str(home.parent / "bin" / "opos"), str(pathlib.Path(tmp) / "other" / "venv"), str(root))
                    elif name == "python_missing":
                        write_manifest(home, str(home.parent / "bin" / "opos"), str(home / "venv"), str(root))
                    elif name == "repo_mismatch":
                        make_venv(home)
                        write_manifest(home, str(home.parent / "bin" / "opos"), str(home / "venv"), str(pathlib.Path(tmp) / "elsewhere"))
                    elif name == "src_missing":
                        bare = pathlib.Path(tmp) / "bare"
                        bare.mkdir()
                        make_venv(home)
                        write_manifest(home, str(home.parent / "bin" / "opos"), str(home / "venv"), str(bare))
                        extra = {"OPOS_ROOT": str(bare)}
                    if name == "missing_manifest":
                        self.assertFalse((home / "manifest").exists())
                    proc = run_dry(home, bindir, extra)
                    self.assertEqual(proc.returncode, 1, name + " dry-run must fail; " + proc.stdout + proc.stderr)
                    self.assertNotIn("would run recovery entry point", proc.stdout, name)
                    live_marker = pathlib.Path(tmp) / "live-marker"
                    if name != "python_missing":
                        live_python = home / "venv" / "bin" / "python"
                        if live_python.is_file():
                            live_python.write_text("#!/bin/sh\ntouch \"" + str(live_marker) + "\"\nexit 0\n", encoding="utf-8")
                            live_python.chmod(live_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    live_fake = pathlib.Path(tmp) / "live-fake"
                    live_fake.mkdir()
                    for tool in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd"):
                        stub = live_fake / tool
                        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    stat_stub = live_fake / "stat"
                    stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
                    stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    live_env = dict(os.environ)
                    live_env["HOME"] = str(home)
                    live_env["OPOS_HOME"] = str(home)
                    live_env["OPOS_BIN_DIR"] = str(bindir)
                    live_env["PATH"] = str(live_fake) + os.pathsep + live_env.get("PATH", "")
                    if extra:
                        live_env.update(extra)
                    if not live_env.get("USER"):
                        live_env["USER"] = "opos-test-user"
                    live_proc = subprocess.run(["sh", str(install), "--uninstall", "--force"], capture_output=True, text=True, env=live_env, timeout=60)
                    self.assertEqual(live_proc.returncode, 0, name + " must reach fallback; " + live_proc.stdout + live_proc.stderr)
                    self.assertIn("==> restore summary:", live_proc.stdout, name)
                    self.assertFalse(live_marker.is_file(), name + " must not execute recovery")























    def test_uninstall_refuses_untrusted_sshd_backup(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        dest_literal = "/etc/ssh/" + "sshd_config"
        bak_name = "".join(["sshd_config", ".bak"])
        for kind in ("regular", "symlink", "dangling"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    fakebin = tmp_path / "fakebin"
                    for p in (home, opos_home, bindir, fakebin):
                        p.mkdir(parents=True)
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    # Meta fixture: a valid ownership marker that the fixed installer
                    # will require before restoring; an unrelated refusal alone must not pass.
                    meta = opos_home / "".join(["meta", ".verified"])
                    meta.write_bytes(b"".join([b"own", b"er-", b"ok-1"]))
                    meta_before = meta.read_bytes()
                    launcher_before = launcher.read_bytes()
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    sentinel_file = venv_dir / "".join(["dum", "my"])
                    sentinel_file.write_bytes(b"".join([b"dummy-venv-", b"1"]))
                    sentinel_before = sentinel_file.read_bytes()
                    elsewhere = tmp_path / "elsewhere"
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(elsewhere / "venv") + "\nrepo=" + str(elsewhere) + "\n",
                        encoding="utf-8",
                    )
                    manifest_before = (opos_home / "manifest").read_bytes()
                    dest = tmp_path / "sshd_config"
                    dest.write_bytes(b"".join([b"dest-bytes-", b"31"]))
                    dest_before = dest.read_bytes()
                    bak = opos_home / bak_name
                    bak_bytes = b"".join([b"bak-bytes-", b"42"])
                    if kind == "regular":
                        bak.write_bytes(bak_bytes)
                    elif kind == "symlink":
                        real = tmp_path / "real"
                        real.write_bytes(bak_bytes)
                        os.symlink(str(real), str(bak))
                    else:
                        target = tmp_path / "missing-target"
                        os.symlink(str(target), str(bak))
                        self.assertFalse(bak.exists())
                        self.assertTrue(bak.is_symlink())
                    script = tmp_path / "install-copy.sh"
                    script.write_bytes(install.read_bytes().replace(dest_literal.encode(), str(dest).encode()))
                    call_log = tmp_path / "".join(["call", ".log"])
                    call_log.write_text("", encoding="utf-8")
                    for tool in ("ufw", "systemctl"):
                        stub = fakebin / tool
                        stub.write_text(
                            "#!/bin/sh\nprintf '%s\\n' \"" + tool + " $*\" >> \"" + str(call_log) + "\"\nexit 0\n",
                            encoding="utf-8",
                        )
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(tmp_path / "root")
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(script), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn(str(bak), proc.stderr)
                    self.assertIn("".join(["man", "ual"]), proc.stderr.lower())
                    self.assertEqual(dest.read_bytes(), dest_before)
                    self.assertEqual(launcher.read_bytes(), launcher_before)
                    self.assertEqual(sentinel_file.read_bytes(), sentinel_before)
                    self.assertEqual((opos_home / "manifest").read_bytes(), manifest_before)
                    self.assertEqual(meta.read_bytes(), meta_before, "ownership fixture must be preserved")
                    self.assertTrue(bak.is_symlink() or bak.exists(), "backup must be preserved")
                    if kind == "regular":
                        self.assertEqual(bak.read_bytes(), bak_bytes)
                    calls = call_log.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(calls, [], "no firewall/service calls allowed")
    def test_sshd_restore_block_snippet_restores_trusted_backup(self):
        """Run only the extracted restore snippet, not install.sh --uninstall.

        The real uninstall refuses any sshd_config.bak since F297
        (do_uninstall returns 1 with "manual SSH restoration required"), so
        this snippet is kept as a unit check of the restore logic.
        """
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            opos_home = tmp_path / "opos"
            opos_home.mkdir()
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            bak = opos_home / "".join(["sshd_config", ".bak"])
            bak_bytes = b"".join([b"bak-bytes-", b"71"])
            bak.write_bytes(bak_bytes)
            live_bytes = b"".join([b"dest-bytes-", b"72"])
            dest = tmp_path / "".join(["sshd", "_config"])
            dest.write_bytes(live_bytes)
            stat_stub = fakebin / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_log = tmp_path / "".join(["sshd", ".log"])
            sshd_log.write_text("", encoding="utf-8")
            sshd_stub = fakebin / "sshd"
            sshd_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(sshd_log) + "\"\nexit 0\n", encoding="utf-8")
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = block.replace("/etc/ssh/" + "sshd_config", str(dest))
            dropin = tmp_path / "no-dropin"
            script = "_sshd=\"skipped\"\n"
            script += "_sshd_dropin=\"" + str(dropin) + "\"\n"
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(opos_home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "succeeded")
            self.assertEqual(dest.read_bytes(), bak_bytes)
            self.assertFalse(bak.exists())
            self.assertFalse(bak.is_symlink())
            logged = sshd_log.read_text(encoding="utf-8").splitlines()
            staged = [line for line in logged if "-t" in line.split() and "-f" in line.split()]
            self.assertTrue(staged)
            checked = staged[-1].rsplit(" ", 1)[-1]
            self.assertNotEqual(checked, str(dest))
            self.assertNotEqual(checked, str(bak))
            self.assertEqual(os.path.dirname(checked), str(dest.parent))
            self.assertFalse(os.path.lexists(checked))

    def test_trusted_backup_snippet_test_is_labelled_snippet_only(self):
        self.assertFalse(hasattr(TestInstallReview, "test_uninstall_trusted_sshd_backup_restores_successfully"))
        renamed = getattr(TestInstallReview, "test_sshd_restore_block_snippet_restores_trusted_backup", None)
        self.assertIsNotNone(renamed)
        self.assertIn("snippet", renamed.__doc__)
        self.assertIn("F297", renamed.__doc__)

    def test_uninstall_preserves_symlink_backup_targets(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        dest_literal = "/etc/ssh/" + "sshd_config"
        bak_name = "".join(["sshd_config", ".bak"])
        for kind in ("symlink", "dangling"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    fakebin = tmp_path / "fakebin"
                    for p in (home, opos_home, bindir, fakebin):
                        p.mkdir(parents=True)
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                    elsewhere = tmp_path / "elsewhere"
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(elsewhere / "venv") + "\nrepo=" + str(elsewhere) + "\n",
                        encoding="utf-8",
                    )
                    dest = tmp_path / "sshd_config"
                    dest.write_bytes(b"".join([b"dest-bytes-", b"31"]))
                    dest_before = dest.read_bytes()
                    bak = opos_home / bak_name
                    real = tmp_path / "real"
                    missing = tmp_path / "".join(["missing-", "target"])
                    if kind == "symlink":
                        real.write_bytes(b"".join([b"bak-bytes-", b"42"]))
                        os.symlink(str(real), str(bak))
                    else:
                        os.symlink(str(missing), str(bak))
                        self.assertFalse(bak.exists())
                    self.assertTrue(bak.is_symlink())
                    before_link = os.readlink(str(bak))
                    before_stat = os.lstat(str(bak))
                    link_fd = _open_symlink_fd(str(bak))
                    real_before = real.read_bytes() if kind == "symlink" else None
                    if kind == "dangling":
                        self.assertFalse(os.path.lexists(str(missing)))
                    script = tmp_path / "install-copy.sh"
                    script.write_bytes(install.read_bytes().replace(dest_literal.encode(), str(dest).encode()))
                    for tool in ("ufw", "systemctl"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(tmp_path / "root")
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(script), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertTrue(bak.is_symlink(), "backup link must remain a symlink")
                    self.assertEqual(os.readlink(str(bak)), before_link, "backup link target must remain unchanged")
                    after_stat = os.lstat(str(bak))
                    self.assertEqual(after_stat.st_ino, before_stat.st_ino, "backup link must keep identity (unlink-and-recreate with same target must fail)")
                    self.assertEqual(after_stat.st_dev, before_stat.st_dev, "backup link device must be preserved")
                    self.assertEqual(after_stat.st_mode, before_stat.st_mode, "backup link metadata must be preserved")
                    if kind == "symlink":
                        self.assertEqual(real.read_bytes(), real_before, "existing link target must retain original bytes")
                    else:
                        self.assertFalse(os.path.lexists(str(missing)), "dangling target must remain absent")
                        self.assertFalse(bak.exists())
                    try:
                        orig_stat = os.fstat(link_fd)
                        self.assertEqual(orig_stat.st_ino, before_stat.st_ino)
                        self.assertEqual(orig_stat.st_dev, before_stat.st_dev)
                        after_stat = os.lstat(str(bak))
                        self.assertEqual(after_stat.st_ino, orig_stat.st_ino)
                        self.assertEqual(after_stat.st_dev, orig_stat.st_dev)
                        self.assertEqual(after_stat.st_mode, orig_stat.st_mode)
                        self.assertEqual(os.readlink(str(bak)), before_link)
                    finally:
                        os.close(link_fd)
                    self.assertEqual(dest.read_bytes(), dest_before)

    def test_symlink_identity_holds_original_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            target = tmp_path / "target"
            target.write_bytes(b"".join([b"ta", b"rget-1"]))
            link = tmp_path / "link"
            os.symlink(str(target), str(link))
            held_fd = _open_symlink_fd(str(link))
            try:
                orig = os.fstat(held_fd)
                saved_ino = os.lstat(str(link)).st_ino
                saved_dev = os.lstat(str(link)).st_dev
                self.assertEqual(orig.st_ino, saved_ino)
                self.assertEqual(orig.st_dev, saved_dev)
                os.unlink(str(link))
                os.symlink(str(target), str(link))
                after = os.lstat(str(link))
                held = os.fstat(held_fd)
                self.assertEqual(held.st_ino, saved_ino)
                self.assertEqual(held.st_dev, saved_dev)
                self.assertEqual(held.st_nlink, 0)
                self.assertEqual(after.st_nlink, 1)
                self.assertNotEqual((after.st_dev, after.st_ino), (held.st_dev, held.st_ino))
            finally:
                os.close(held_fd)

    def test_symlink_handle_requires_supported_mechanism(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        needle = "getattr(os, " + '"O_SYMLINK", 0)'
        self.assertNotIn(needle, src, "must not fall back to opening the link target")

        class _FakeOs:
            pass

        def _make_fake(with_path, with_link):
            fake = _FakeOs()
            fake.O_RDONLY = 0
            fake.O_NOFOLLOW = 128
            if with_path:
                fake.O_PATH = 64
            if with_link:
                fake.O_SYMLINK = 256
            fake.open = unittest.mock.Mock(return_value=99)
            return fake

        with unittest.mock.patch("tests.test_install_review.os", _make_fake(True, True)) as fake:
            import tests.test_install_review as review

            self.assertEqual(review._open_symlink_fd("dummy"), 99)
            fake.open.assert_called_once_with("dummy", 64 | 128)
        with unittest.mock.patch("tests.test_install_review.os", _make_fake(False, True)) as fake:
            import tests.test_install_review as review

            self.assertEqual(review._open_symlink_fd("dummy"), 99)
            fake.open.assert_called_once_with("dummy", 0 | 256)
        with unittest.mock.patch("tests.test_install_review.os", _make_fake(False, False)):
            import tests.test_install_review as review

            with self.assertRaises(unittest.SkipTest):
                review._open_symlink_fd("dummy")

    def test_restore_commands_preserve_stderr(self):
        text = _read_install_sh()
        restore_block = _uninstall_restore_block(text)
        self.assertNotIn("2>/dev/null", restore_block)

    def test_restore_block_ignores_unrelated_anchors(self):
        marker = "".join(["unique-restore-", "marker-7"])
        prefix = 'UH="$OPOS_HOME"\nrmdir "$OPOS_BIN_DIR"\n'
        header = "do_uninstall() {\n"
        body = "  echo \"" + marker + "\"\n  printf '==> restore summary: %s\\n' \"done\"\n"
        suffix = "  cmd || true\n  rmdir \"$OPOS_BIN_DIR\"\n}\n"
        text = prefix + header + body + suffix
        want = header + body
        self.assertEqual(_uninstall_restore_block(text), want)

    def test_cmp_preserves_error_diagnostics(self):
        text = _read_install_sh()
        restore_block = _uninstall_restore_block(text)
        self.assertNotIn("cmp -s", restore_block)
        self.assertRegex(restore_block, r'cmp "[^"]+" "[^"]+" >/dev/null')
        self.assertNotIn("2>/dev/null", restore_block)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            first = tmp_path / "first"
            second = tmp_path / "second"
            first.write_bytes(b"".join([b"al", b"pha-1"]))
            second.write_bytes(b"".join([b"be", b"ta-2"]))
            proc = subprocess.run(
                ["sh", "-c", 'cmp "$1" "$2" >/dev/null', "sh", str(first), str(second)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stderr, "")
            missing = tmp_path / "missing"
            proc = subprocess.run(
                ["sh", "-c", 'cmp "$1" "$2" >/dev/null', "sh", str(first), str(missing)],
                capture_output=True,
                text=True,
            )
            self.assertGreater(proc.returncode, 1)
            self.assertNotEqual(proc.stderr, "")

    def test_failed_firewall_restore_returns_failure_and_keeps_state(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, fakebin, root):
                p.mkdir(parents=True)
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_dir = opos_home / "venv"
            venv_dir.mkdir(parents=True)
            dummy = venv_dir / "".join(["dum", "my"])
            dummy.write_bytes(b"".join([b"dummy-venv-", b"1"]))
            manifest = opos_home / "manifest"
            manifest.write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            sentinel = b"inactive\n"
            state = opos_home / "".join(["fire", "wall.state"])
            state.write_bytes(sentinel)
            self.assertFalse((opos_home / "".join(["sshd_config", ".bak"])).exists())
            for name, code in (("ufw", "exit 1"), ("systemctl", "exit 0"), ("loginctl", "exit 1"), ("nft", "exit 1")):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\n" + code + "\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("".join(["fire", "wall=failed"]), proc.stdout)
            self.assertIn("".join(["retain", "ed"]), proc.stderr)
            self.assertIn(str(opos_home), proc.stderr)
            self.assertEqual(state.read_bytes(), sentinel)
            self.assertFalse(launcher.exists())
            self.assertFalse(manifest.exists())
            self.assertTrue(venv_dir.exists(), "venv must be retained when restore fails")

    def test_ufw_disable_and_reload_failures_preserve_state_independently(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_name = "".join(["fire", "wall.state"])
        failed_mark = "".join(["fire", "wall=failed"])
        for name, disable_exit, reload_exit in (
            ("disable_fails", 1, 0),
            ("reload_fails", 0, 1),
        ):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    fakebin = tmp_path / "fakebin"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, fakebin, root):
                        p.mkdir(parents=True)
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                        encoding="utf-8",
                    )
                    sentinel = b"inactive\n"
                    state = opos_home / state_name
                    state.write_bytes(sentinel)
                    log = tmp_path / "".join(["ufw", ".log"])
                    log.write_text("", encoding="utf-8")
                    stub = fakebin / "ufw"
                    stub.write_text(
                        "#!/bin/sh\n"
                        + "printf '%s\\n' \"ufw $*\" >> \"" + str(log) + "\"\n"
                        + "case \"$*\" in *reload*) exit " + str(reload_exit) + ";; esac\n"
                        + "case \"$*\" in *disable*) exit " + str(disable_exit) + ";; esac\n"
                        + "exit 0\n",
                        encoding="utf-8",
                    )
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("systemctl", "loginctl", "nft"):
                        other = fakebin / tool
                        other.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                        other.chmod(other.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60)
                    self.assertNotEqual(proc.returncode, 0, name + proc.stdout + proc.stderr)
                    self.assertIn(failed_mark, proc.stdout, name)
                    self.assertEqual(state.read_bytes(), sentinel, name)
                    calls = log.read_text(encoding="utf-8")
                    self.assertIn("disable", calls, name)
                    self.assertIn("reload", calls, name)

    def test_sshd_backup_restore_requires_trusted_valid_backup(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]
        self.assertIn('sshd -t -f "$OPOS_HOME/sshd_config.bak"', block)
        self.assertIn('[ ! -f "/etc/ssh/sshd_config" ]', block)

        def run_block(home, dest, fakebin, extra_env):
            replaced = block.replace("/etc/ssh/sshd_config", str(dest))
            dropin = pathlib.Path(home).parent / "no-dropin"
            script = "_sshd=\"skipped\"\n"
            script += "_sshd_dropin=\"" + str(dropin) + "\"\n"
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = pathlib.Path(home).parent / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            env.update(extra_env)
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return proc.stdout

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        cases = [
            ("non-root", "1000 600", "exit 0", False, False, False),
            ("group-write", "0 660", "exit 0", False, False, False),
            ("other-write", "0 602", "exit 0", False, False, False),
            ("symlink", "0 600", "exit 0", False, True, False),
            ("metadata-failure", None, "exit 0", False, False, False),
            ("missing-validator", "0 600", None, True, False, False),
            ("invalid-config", "0 600", "exit 1", False, False, False),
            ("copy-failure", "0 600", "exit 0", False, False, True),
            ("valid", "0 600", "exit 0", False, False, False),
        ]
        for name, meta, sshd_body, missing_sshd, as_link, fail_cp in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    home.mkdir()
                    fakebin = tmp_path / "fakebin"
                    fakebin.mkdir()
                    dest = tmp_path / "dest"
                    dest_b = ("".join(["de", "st"]) + "".join(["-se", "nt-"]) + "1").encode()
                    dest.write_bytes(dest_b)
                    bak = home / "sshd_config.bak"
                    bak_b = ("".join(["ba", "ck"]) + "".join(["-up", "-9"])).encode()
                    if as_link:
                        real = tmp_path / "real"
                        real.write_bytes(bak_b)
                        os.symlink(str(real), str(bak))
                    else:
                        bak.write_bytes(bak_b)
                    log = tmp_path / "sshd.log"
                    log.write_text("", encoding="utf-8")
                    if meta is None:
                        make_stub(fakebin / "stat", "exit 1")
                    else:
                        make_stub(fakebin / "stat", "printf '%s\\n' \"" + meta + "\"")
                    if sshd_body is not None:
                        body = "printf '%s\\n' \"$@\" >> \"" + str(log) + "\"\n" + sshd_body
                        make_stub(fakebin / "sshd", body)
                    if fail_cp:
                        make_stub(
                            fakebin / "cp",
                            "for a in \"$@\"; do last=\"$a\"; done\n"
                            + "printf '%s' \"" + "".join(["par", "tial-", "9"]) + "\" > \"$last\"\n"
                            + "exit 1",
                        )
                    status = run_block(home, dest, fakebin, {"SSHD_LOG": str(log)})
                    if name == "valid":
                        self.assertEqual(status, "succeeded")
                        self.assertEqual(dest.read_bytes(), bak_b)
                        self.assertFalse(bak.exists() or bak.is_symlink())
                        logged = log.read_text(encoding="utf-8")
                        self.assertIn("-t", logged.split())
                        self.assertIn("-f", logged.split())
                        self.assertIn(str(bak), logged)
                    else:
                        self.assertEqual(status, "failed")
                        self.assertEqual(dest.read_bytes(), dest_b)
                        if name == "invalid-config":
                            logged = log.read_text(encoding="utf-8")
                            self.assertIn("-t", logged.split())
                            self.assertIn("-f", logged.split())
                            self.assertIn(str(bak), logged)
                        if name in ("non-root", "group-write", "other-write", "symlink", "metadata-failure", "copy-failure"):
                            self.assertTrue(bak.exists() or bak.is_symlink())
                        if name == "copy-failure":
                            self.assertEqual(bak.read_bytes(), bak_b)
                            self.assertNotIn("".join(["par", "tial-", "9"]), dest.read_text(encoding="utf-8"))

    def test_installer_checks_have_no_unrelated_probe_assignments(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestInstallReview")
        targets = ("test_fallback_restores_sshd_firewall_and_service", "test_restore_steps_report_summary")
        actual = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(set(targets), actual & set(targets), "both installer methods must exist for hygiene check")
        stored = set()
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        stored.add(sub.id)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                if node.id in ("key_name", "octet", "token_prefix", "private_ip", "home_path"):
                    stored.add(node.id)
        forbidden = set(["token_prefix", "private_ip", "home_path", "key_name", "octet"])
        self.assertTrue(stored.isdisjoint(forbidden), stored & forbidden)

    def test_fallback_restores_sshd_firewall_and_service(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_once(fail_ufw, fail_disable):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, opos_home, bindir, fakebin, root):
                    p.mkdir(parents=True)
                launcher = bindir / "opos"
                launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                venv_dir = opos_home / "venv"
                venv_dir.mkdir(parents=True)
                (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                (opos_home / "manifest").write_text(
                    "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                    encoding="utf-8",
                )
                state = opos_home / "".join(["fire", "wall.state"])
                state.write_bytes(b"inactive\n")
                unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                unit.parent.mkdir(parents=True, exist_ok=True)
                unit.write_bytes(b"".join([b"unit-bytes-", b"5"]))
                write_stub(fakebin / "ufw", "exit " + ("1" if fail_ufw else "0") + "\n")
                disable_code = "exit 1\n" if fail_disable else "exit 0\n"
                write_stub(
                    fakebin / "systemctl",
                    "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                    + "case \"$*\" in *disable*) " + disable_code + ";; esac\n"
                    + "case \"$*\" in *daemon-reload*) exit 0;; esac\n"
                    + "exit 0\n",
                )
                for name in ("loginctl", "nft"):
                    write_stub(fakebin / name, "exit 1\n")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["OPOS_BIN_DIR"] = str(bindir)
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60)
                return proc

        proc = run_once(False, False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
        self.assertIn("".join(["fire", "wall=succeeded"]), line)
        self.assertIn("service=succeeded", line)
        proc = run_once(True, True)
        self.assertNotEqual(proc.returncode, 0)
        line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
        self.assertIn("".join(["fire", "wall=failed"]), line)
        self.assertIn("service=failed", line)

    def test_fallback_restores_sshd_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest.write_bytes(b"".join([b"dest-bytes-", b"91"]))
            backup_bytes = b"".join([b"backup-bytes-", b"37"])
            bak = opos_home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(backup_bytes)
            dropin = tmp_path / "no-dropin"
            self.assertFalse(dropin.exists())
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_bytes(b"".join([b"unit-bytes-", b"52"]))
            state = opos_home / "".join(["fire", "wall.state"])
            state.write_bytes(b"inactive\n")
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            stat_stub = stubdir / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_stub = stubdir / "sshd"
            sshd_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ufw_log = tmp_path / "ufw.log"
            ufw_log.write_text("", encoding="utf-8")
            ufw_stub = stubdir / "ufw"
            ufw_stub.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(ufw_log) + "\"\n"
                + 'case "$*" in "--force disable") exit 0;; esac\n'
                + 'case "$*" in "reload") exit 0;; esac\n'
                + "exit 0\n",
                encoding="utf-8",
            )
            ufw_stub.chmod(ufw_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_log = tmp_path / "systemctl.log"
            ctl_log.write_text("", encoding="utf-8")
            ctl_stub = stubdir / "systemctl"
            ctl_stub.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(ctl_log) + "\"\n"
                + 'case "$*" in *list-unit-files*--no-legend*opos-server.service*)\n'
                + "printf 'opos-server.service enabled\\n'\nexit 0;;\n"
                + "esac\n"
                + 'case "$*" in *disable*--now*opos-server.service*) exit 0;; esac\n'
                + 'case "$*" in *daemon-reload*) exit 0;; esac\n'
                + "exit 0\n",
                encoding="utf-8",
            )
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = _prepare_fallback_restore_snippet(dropin, dest)
            self.assertIn('_sshd_dropin="' + str(dropin) + '"', replaced)
            self.assertNotIn(str(dest) + ".d/60-opos.conf", replaced)
            preamble = (
                "is_hardening_active() { return 1; }\n"
                + "canon_repo() { printf '%s' \"$1\"; }\n"
                + "print_hardening_instructions() { :; }\n"
                + "opos_owns_linger() { return 1; }\n"
                + "is_linger_enabled() { return 1; }\n"
            )
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["DRY"] = "0"
            env["UVENV"] = ""
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
            self.assertIn("sshd=succeeded", line)
            self.assertIn("".join(["fire", "wall=succeeded"]), line)
            self.assertIn("service=succeeded", line)
            self.assertEqual(dest.read_bytes(), backup_bytes)
            self.assertFalse(bak.exists() or bak.is_symlink())
            self.assertEqual(ufw_log.read_text(encoding="utf-8").splitlines(), ["--force disable", "reload"])
            calls = ctl_log.read_text(encoding="utf-8")
            self.assertIn("--user disable --now opos-server.service", calls)
            self.assertIn("--user daemon-reload", calls)
            self.assertFalse(unit.exists())
            self.assertFalse(state.exists())
            self.assertFalse(dropin.exists())

    def test_rewritten_restore_snippet_rejects_backup_write(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        idx = text.index("FOUND=0", anchor)
        leaf = "sshd_config" + ".bak"
        insert = "\nprintf x > \"$OPOS_HOME/" + leaf + "\""
        token = "FOUND=0"
        mutant = text[:idx + len(token)] + insert + text[idx + len(token):]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            dropin = tmp_path / "dropin"
            dest = tmp_path / "dest"
            with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
                with self.assertRaisesRegex(AssertionError, "backup write"):
                    _prepare_fallback_restore_snippet(dropin, dest)
            result = _prepare_fallback_restore_snippet(dropin, dest)
            self.assertTrue(result)

    def test_restore_steps_report_summary(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_once(fail_ufw):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, opos_home, bindir, fakebin, root):
                    p.mkdir(parents=True)
                launcher = bindir / "opos"
                launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                venv_dir = opos_home / "venv"
                venv_dir.mkdir(parents=True)
                (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                (opos_home / "manifest").write_text(
                    "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                    encoding="utf-8",
                )
                state = opos_home / "".join(["fire", "wall.state"])
                state.write_bytes(b"inactive\n")
                unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                unit.parent.mkdir(parents=True, exist_ok=True)
                unit.write_bytes(b"".join([b"unit-bytes-", b"4"]))
                write_stub(fakebin / "ufw", "exit " + ("1" if fail_ufw else "0") + "\n")
                write_stub(
                    fakebin / "systemctl",
                    "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                    + "case \"$*\" in *disable*) exit 0;; esac\n"
                    + "case \"$*\" in *daemon-reload*) exit 0;; esac\n"
                    + "exit 0\n",
                )
                for name in ("loginctl", "nft"):
                    write_stub(fakebin / name, "exit 1\n")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["OPOS_BIN_DIR"] = str(bindir)
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60)
                return proc

        good = run_once(False)
        good_line = next((x for x in good.stdout.splitlines() if "restore summary" in x), "")
        self.assertNotEqual(good_line, "", "success path must emit a restore summary")
        self.assertIn("".join(["fire", "wall=succeeded"]), good_line)
        bad = run_once(True)
        bad_line = next((x for x in bad.stdout.splitlines() if "restore summary" in x), "")
        self.assertNotEqual(bad_line, "", "failure path must emit a restore summary")
        self.assertIn("".join(["fire", "wall=failed"]), bad_line)
        self.assertNotEqual(good_line, bad_line, "summary must reflect the restoration outcome")
        self.assertNotEqual(bad.returncode, 0)

    def test_restore_summary_reports_mixed_outcomes(self):
        text = _read_install_sh()
        start = text.index('    _sshd="skipped"')
        end = text.index("\n  fi\n  opos_owned_path", start)
        body = text[start:end]
        prod = "/etc/ssh/" + "sshd_config"
        prod_dropin = prod + ".d/60-opos.conf"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            state = tmp_path / "".join(["fire", "wall.state"])
            state.write_bytes(b"".join([b"state-bytes-", b"31"]))
            unit = tmp_path / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_bytes(b"".join([b"unit-bytes-", b"32"]))
            dest = tmp_path / "".join(["sshd", "_config"])
            dropin = tmp_path / "".join(["60-opos", ".conf"])
            dest.write_bytes(b"".join([b"dest-bytes-", b"33"]))
            stubs = tmp_path / "stubs"
            stubs.mkdir()
            for tool in ("cp", "mv", "rm", "mktemp", "stat", "sshd"):
                stub = stubs / tool
                stub.write_text("#!/bin/sh\n" + 'case "$*" in *"$OPOS_PROD_PREFIX"*) exit 1;; esac\nexec /bin/' + tool + ' "$@"\n', encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            body = body.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
            script = "ufw() { return 1; }\n" + "systemctl() { return 0; }\n" + "id() { printf '1001\\n'; }\n" + body
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["OPOS_HOME"] = str(tmp_path)
            env["HOME"] = str(tmp_path)
            env["OPOS_PROD_PREFIX"] = prod
            env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("==> restore summary: sshd=skipped firewall=failed service=succeeded", proc.stdout.splitlines())

    def test_restore_mixed_outcomes_isolates_host_ssh(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        start = src.index("def test_restore_summary_reports_mixed_outcomes")
        end = src.index("\n    def ", start)
        block = src[start:end]
        prod = "/etc/ssh/" + "sshd_config"
        prod_dropin = prod + ".d/60-opos.conf"
        self.assertIn(".replace(", block)
        self.assertNotIn(prod, block)
        self.assertNotIn(prod_dropin, block)
        for tool in ("cp", "mv", "rm", "mktemp", "stat", "sshd"):
            self.assertIn(tool, block)

    def test_restore_reports_firewall_and_service_independently(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_once(fail_ufw, fail_disable):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, opos_home, bindir, fakebin, root):
                    p.mkdir(parents=True)
                launcher = bindir / "opos"
                launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                venv_dir = opos_home / "venv"
                venv_dir.mkdir(parents=True)
                (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                (opos_home / "manifest").write_text(
                    "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                    encoding="utf-8",
                )
                state = opos_home / "".join(["fire", "wall.state"])
                state.write_bytes(b"inactive\n")
                unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                unit.parent.mkdir(parents=True, exist_ok=True)
                unit.write_bytes(b"".join([b"unit-bytes-", b"7"]))
                write_stub(fakebin / "ufw", "exit " + ("1" if fail_ufw else "0") + "\n")
                disable_code = "exit 1\n" if fail_disable else "exit 0\n"
                write_stub(
                    fakebin / "systemctl",
                    "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                    + "case \"$*\" in *disable*) " + disable_code + ";; esac\n"
                    + "case \"$*\" in *daemon-reload*) exit 0;; esac\n"
                    + "exit 0\n",
                )
                for name in ("loginctl", "nft"):
                    write_stub(fakebin / name, "exit 1\n")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["OPOS_BIN_DIR"] = str(bindir)
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                return subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env)

        cases = [
            (False, True, "succeeded", "failed"),
            (True, False, "failed", "succeeded"),
        ]
        for fail_ufw, fail_disable, want_fw, want_svc in cases:
            with self.subTest(fail_ufw=fail_ufw, fail_disable=fail_disable):
                proc = run_once(fail_ufw, fail_disable)
                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
                self.assertNotEqual(line, "", "mixed outcome must emit a restore summary")
                self.assertIn("".join(["fire", "wall=" + want_fw]), line)
                self.assertIn("service=" + want_svc, line)

    def test_uninstall_service_cleanup_outcomes(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_case(name, with_file, list_exit, list_out, disable_exit, disable_msg, reload_exit, want_service, want_disable, want_reload, want_rc):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    stubdir = tmp_path / "stubs"
                    for p in (home, opos_home, bindir, stubdir):
                        p.mkdir(parents=True)
                    unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                    if with_file:
                        unit.parent.mkdir(parents=True, exist_ok=True)
                        unit.write_text("unit-bytes\n", encoding="utf-8")
                    log = tmp_path / "systemctl.log"
                    log.write_text("", encoding="utf-8")
                    write_stub(
                        stubdir / "systemctl",
                        "LOG=\"" + str(log) + "\"\n"
                        + "UNIT=\"" + str(unit) + "\"\n"
                        + "HAS=\"absent\"\n"
                        + "if [ -f \"$UNIT\" ]; then HAS=\"present\"; fi\n"
                        + "printf '%s|%s\\n' \"$*\" \"$HAS\" >> \"$LOG\"\n"
                        + 'case "$*" in *list-unit-files*)\n'
                        + "printf '%s' \"" + list_out + "\"\n"
                        + "exit " + str(list_exit) + ";;\n"
                        + "esac\n"
                        + 'case "$*" in *disable*)\n'
                        + "if [ \"" + disable_msg + "\" != \"\" ]; then printf '%s\\n' \"" + disable_msg + "\" >&2; fi\n"
                        + "exit " + str(disable_exit) + ";;\n"
                        + "esac\n"
                        + 'case "$*" in *daemon-reload*) exit ' + str(reload_exit) + ";;\n"
                        + "esac\n"
                        + "exit 0\n",
                    )
                    write_stub(stubdir / "ufw", "exit 0\n")
                    write_stub(stubdir / "id", "printf '1001\\n'\n")
                    env = dict(os.environ)
                    env.pop("SUDO_USER", None)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, want_rc, proc.stdout + proc.stderr)
                    self.assertIn("service=" + want_service, proc.stdout)
                    calls = log.read_text(encoding="utf-8").splitlines()
                    disables = [c for c in calls if "disable" in c]
                    reloads = [c for c in calls if "daemon-reload" in c]
                    listings = [c for c in calls if "list-unit-files" in c]
                    self.assertEqual(len(listings), 1)
                    if want_disable:
                        self.assertTrue(disables)
                    else:
                        self.assertEqual(disables, [])
                    if want_reload:
                        self.assertTrue(reloads)
                    else:
                        self.assertEqual(reloads, [])
                    if name == "local_unit_success":
                        self.assertTrue(disables and disables[0].endswith("|present"))
                        self.assertTrue(reloads and reloads[0].endswith("|absent"))
                    if name in ("failed_probe", "disable_other_failure"):
                        self.assertTrue(unit.is_file())
                        self.assertEqual(unit.read_bytes(), b"unit-bytes\n")
                    else:
                        self.assertFalse(unit.exists())
                    return proc

        gone_msg = "Failed to disable unit: Unit file opos-server.service does not exist."
        run_case("empty_success_no_file", False, 0, "", 0, "", 0, "skipped", False, False, 0)
        run_case("failed_probe_no_file", False, 1, "", 0, "", 0, "failed", False, False, 1)
        run_case("failed_probe", True, 1, "", 0, "", 0, "failed", False, False, 1)
        run_case("local_unit_success", True, 0, "", 0, "", 0, "succeeded", True, True, 0)
        run_case("listed_no_file", False, 0, "opos-server.service disabled\\n", 0, "", 0, "succeeded", True, True, 0)
        run_case("disable_gone", True, 0, "", 1, gone_msg, 0, "skipped", True, True, 0)
        run_case("disable_other_failure", True, 0, "", 1, "boom", 0, "failed", True, True, 1)
        run_case("reload_failure", True, 0, "", 0, "", 1, "failed", True, True, 1)

    def test_failed_probe_preserves_unit_and_reports_failed(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            for p in (home, opos_home, bindir, stubdir):
                p.mkdir(parents=True)
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            marker = b"".join([b"unit-bytes-", b"31"])
            unit.write_bytes(marker)
            log = tmp_path / "calls.log"
            log.write_text("", encoding="utf-8")
            stub = stubdir / "systemctl"
            stub.write_text(
                "#!/bin/sh\n"
                + "printf '%s\\n' \"systemctl $*\" >> \"" + str(log) + "\"\n"
                + "case \"$*\" in *list-unit-files*) exit 1;; esac\n"
                + "exit 0\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            id_stub = stubdir / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("service=failed", proc.stdout)
            self.assertTrue(unit.is_file())
            self.assertEqual(unit.read_bytes(), marker)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any("list-unit-files" in c for c in calls))
            self.assertFalse(any("disable" in c for c in calls))
            self.assertFalse(any("daemon-reload" in c for c in calls))

    def test_failed_probe_without_unit_file_reports_failed(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            for p in (home, opos_home, bindir, stubdir):
                p.mkdir(parents=True)
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            self.assertFalse(unit.exists())
            log = tmp_path / "calls.log"
            log.write_text("", encoding="utf-8")
            stub = stubdir / "systemctl"
            stub.write_text(
                "#!/bin/sh\n"
                + "printf '%s\\n' \"systemctl $*\" >> \"" + str(log) + "\"\n"
                + "case \"$*\" in *list-unit-files*) exit 1;; esac\n"
                + "exit 0\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            id_stub = stubdir / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("service=failed", proc.stdout)
            self.assertFalse(unit.exists())
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any("list-unit-files" in c for c in calls))
            self.assertFalse(any("disable" in c for c in calls))
            self.assertFalse(any("daemon-reload" in c for c in calls))

    def test_restore_failures_surface_stderr_and_fail(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        diags = {
            "ufw_reload": "".join(["ufw-", "reload-", "diag-", "31"]),
            "service_disable": "".join(["service-", "disable-", "diag-", "42"]),
            "daemon_reload": "".join(["daemon-", "reload-", "diag-", "43"]),
        }
        wants = {
            "ufw_reload": "ufw reload",
            "service_disable": "systemctl --user disable --now opos-server.service",
            "daemon_reload": "systemctl --user daemon-reload",
        }
        for name in ("ufw_reload", "service_disable", "daemon_reload"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    fakebin = tmp_path / "fakebin"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, fakebin, root):
                        p.mkdir(parents=True)
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                        encoding="utf-8",
                    )
                    state = opos_home / "".join(["fire", "wall.state"])
                    state.write_bytes(b"inactive\n")
                    unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                    unit.parent.mkdir(parents=True, exist_ok=True)
                    unit.write_bytes(b"".join([b"unit-bytes-", b"5"]))
                    log = tmp_path / "".join(["call", ".log"])
                    log.write_text("", encoding="utf-8")
                    diag = diags[name]
                    ufw_stub = fakebin / "ufw"
                    if name == "ufw_reload":
                        ufw_body = "printf '%s\\n' \"ufw $*\" >> \"" + str(log) + "\"\nprintf '%s\\n' \"" + diag + "\" >&2\nexit 1\n"
                    else:
                        ufw_body = "printf '%s\\n' \"ufw $*\" >> \"" + str(log) + "\"\nexit 0\n"
                    ufw_stub.write_text("#!/bin/sh\n" + ufw_body, encoding="utf-8")
                    ufw_stub.chmod(ufw_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_stub = fakebin / "systemctl"
                    disable_diag = diags["service_disable"]
                    reload_diag = diags["daemon_reload"]
                    if name == "service_disable":
                        disable_action = "printf '%s\\n' \"" + disable_diag + "\" >&2\nexit 1\n"
                    else:
                        disable_action = "exit 0\n"
                    if name == "daemon_reload":
                        reload_action = "printf '%s\\n' \"" + reload_diag + "\" >&2\nexit 1\n"
                    else:
                        reload_action = "exit 0\n"
                    ctl_stub.write_text(
                        "#!/bin/sh\n"
                        + "printf '%s\\n' \"systemctl $*\" >> \"" + str(log) + "\"\n"
                        + "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                        + "case \"$*\" in *disable*) " + disable_action + ";; esac\n"
                        + "case \"$*\" in *daemon-reload*) " + reload_action + ";; esac\n"
                        + "exit 0\n",
                        encoding="utf-8",
                    )
                    ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    id_stub = fakebin / "id"
                    id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
                    id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env.pop("SUDO_USER", None)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env)
                    calls = log.read_text(encoding="utf-8").splitlines()
                    self.assertIn(wants[name], calls)
                    self.assertIn(diag, proc.stderr)
                    self.assertNotEqual(proc.returncode, 0)
                    if name == "ufw_reload":
                        self.assertIn("".join(["fire", "wall=failed"]), proc.stdout)
                    else:
                        self.assertIn("service=failed", proc.stdout)


    def test_uninstall_dir_validation_and_trailing_slash(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def run_with(home_dir, home_env, bin_env):
            venv_python = home_dir / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home_dir / "manifest").write_text(
                "launcher=" + str(home_dir.parent / "bin" / "opos") + "\n"
                + "venv=" + str(home_dir / "venv") + "\n"
                + "repo=" + str(root) + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPOS_HOME"] = home_env
            env["OPOS_BIN_DIR"] = bin_env
            env["HOME"] = str(home_dir)
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
            proc = run_with(home, str(home) + "/", str(bindir) + "/")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            proc = run_with(home, "", str(bindir))
            self.assertNotIn("would run recovery entry point", proc.stdout)
            self.assertEqual(proc.returncode, 1)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            proc = run_with(home, "/", "/")
            self.assertNotIn("would run recovery entry point", proc.stdout)

    def test_real_uninstall_preserves_unrelated_sentinels(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_real(home, bindir, root, fakebin, extra_env=None):
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            if extra_env:
                env.update(extra_env)
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        with self.subTest(name="manifest_paths_outside_ownership"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                outside = tmp_path / "outside"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, opos_home, bindir, outside, fakebin, root):
                    p.mkdir(parents=True)
                trusted = bindir / "opos"
                trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                sentinel_launcher = outside / "".join(["unre", "lated-launcher"])
                sentinel_launcher.write_bytes(b"".join([b"senti", b"nel-launcher-", b"7"]))
                sentinel_venv = outside / "".join(["unre", "lated-venv"])
                sentinel_venv.mkdir()
                sentinel_file = sentinel_venv / "".join(["dum", "my"])
                sentinel_file.write_bytes(b"".join([b"senti", b"nel-venv-", b"9"]))
                (opos_home / "manifest").write_text(
                    "launcher=" + str(sentinel_launcher) + "\n"
                    + "venv=" + str(sentinel_venv) + "\n"
                    + "repo=" + str(home) + "\n",
                    encoding="utf-8",
                )
                for name in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd", "stat"):
                    if name == "systemctl":
                        write_stub(fakebin / name, "case \"$*\" in *list-unit-files*) exit 0;; esac\nexit 0\n")
                    elif name == "stat":
                        write_stub(fakebin / name, "printf '%s\\n' \"0 600\"\n")
                    elif name == "sshd":
                        write_stub(fakebin / name, "exit 0\n")
                    else:
                        write_stub(fakebin / name, "exit 0\n")
                proc = run_real(opos_home, bindir, root, fakebin)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertTrue(sentinel_launcher.is_file(), "untrusted manifest launcher must survive real uninstall")
                self.assertEqual(sentinel_launcher.read_bytes(), b"".join([b"senti", b"nel-launcher-", b"7"]))
                self.assertTrue(sentinel_file.is_file(), "untrusted manifest venv content must survive real uninstall")
                self.assertEqual(sentinel_file.read_bytes(), b"".join([b"senti", b"nel-venv-", b"9"]))

        with self.subTest(name="invalid_directories"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                bindir = tmp_path / "bin"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, bindir, fakebin, root):
                    p.mkdir(parents=True)
                sentinel = tmp_path / "".join(["unre", "lated-sentinel"])
                sentinel.write_bytes(b"".join([b"senti", b"nel-invalid-", b"3"]))
                for name in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd", "stat"):
                    if name == "systemctl":
                        write_stub(fakebin / name, "case \"$*\" in *list-unit-files*) exit 1;; esac\nexit 0\n")
                    elif name == "stat":
                        write_stub(fakebin / name, "printf '%s\\n' \"0 600\"\n")
                    elif name == "sshd":
                        write_stub(fakebin / name, "exit 0\n")
                    else:
                        write_stub(fakebin / name, "exit 0\n")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = "/"
                env["OPOS_BIN_DIR"] = "/"
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                proc = subprocess.run(
                    ["sh", str(install), "--uninstall", "--force"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=60,
                )
                self.assertNotEqual(proc.returncode, 0)
                self.assertTrue(sentinel.is_file(), "unrelated sentinel must survive invalid directory uninstall")
                self.assertEqual(sentinel.read_bytes(), b"".join([b"senti", b"nel-invalid-", b"3"]))

        with self.subTest(name="owned_artifacts_removed"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                outside = tmp_path / "outside"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, opos_home, bindir, outside, fakebin, root):
                    p.mkdir(parents=True)
                owned_launcher = bindir / "opos"
                owned_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                owned_launcher.chmod(owned_launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                owned_venv = opos_home / "venv"
                owned_venv.mkdir(parents=True)
                owned_file = owned_venv / "".join(["dum", "my"])
                owned_file.write_bytes(b"".join([b"owned-venv-", b"4"]))
                (opos_home / "manifest").write_text(
                    "launcher=" + str(owned_launcher) + "\n"
                    + "venv=" + str(owned_venv) + "\n"
                    + "repo=" + str(home) + "\n",
                    encoding="utf-8",
                )
                unrelated = outside / "".join(["unre", "lated-keep"])
                unrelated.write_bytes(b"".join([b"senti", b"nel-keep-", b"5"]))
                bin_sentinel = bindir / "".join(["unre", "lated-bin"])
                bin_sentinel.write_bytes(b"".join([b"senti", b"nel-bin-", b"6"]))
                home_sentinel = opos_home / "".join(["unre", "lated-home"])
                home_sentinel.write_bytes(b"".join([b"senti", b"nel-home-", b"8"]))
                for name in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd", "stat"):
                    if name == "systemctl":
                        write_stub(fakebin / name, "case \"$*\" in *list-unit-files*) exit 0;; esac\nexit 0\n")
                    elif name == "stat":
                        write_stub(fakebin / name, "printf '%s\\n' \"0 600\"\n")
                    elif name == "sshd":
                        write_stub(fakebin / name, "exit 0\n")
                    else:
                        write_stub(fakebin / name, "exit 0\n")
                proc = run_real(opos_home, bindir, root, fakebin)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertFalse(owned_launcher.exists(), "owned launcher must be removed on successful uninstall")
                self.assertFalse(owned_venv.exists(), "validated venv must be removed on successful uninstall")
                self.assertTrue(opos_home.is_dir(), "home with unrelated files must be preserved")
                self.assertFalse((opos_home / "manifest").exists(), "manifest must be removed on successful uninstall")
                self.assertTrue(unrelated.is_file(), "unrelated artifact must survive successful uninstall")
                self.assertEqual(unrelated.read_bytes(), b"".join([b"senti", b"nel-keep-", b"5"]))
                self.assertTrue(bin_sentinel.is_file(), "unrelated bindir sentinel must survive successful uninstall")
                self.assertEqual(bin_sentinel.read_bytes(), b"".join([b"senti", b"nel-bin-", b"6"]))
                self.assertTrue(home_sentinel.is_file(), "unrelated opos_home sentinel must survive successful uninstall")
                self.assertEqual(home_sentinel.read_bytes(), b"".join([b"senti", b"nel-home-", b"8"]))


    def test_uninstall_dir_validation_isolated(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def run_isolated(home_dir, home_env, bin_env):
            venv_python = home_dir / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home_dir / "manifest").write_text(
                "launcher=" + str(home_dir.parent / "bin" / "opos") + "\n"
                + "venv=" + str(home_dir / "venv") + "\n"
                + "repo=" + str(root) + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPOS_HOME"] = home_env
            env["OPOS_BIN_DIR"] = bin_env
            env["HOME"] = str(home_dir)
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
            )

        cases = [
            ("home_invalid_bin_valid", "/", None),
            ("home_valid_bin_invalid", None, "/"),
        ]
        for name, home_override, bin_override in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    home = pathlib.Path(tmp) / "home"
                    bindir = pathlib.Path(tmp) / "bin"
                    home.mkdir()
                    bindir.mkdir()
                    good = run_isolated(home, str(home), str(bindir))
                    good_msg = name + " stdout=" + good.stdout + " stderr=" + good.stderr
                    self.assertEqual(good.returncode, 0, good_msg)
                    self.assertIn("would run recovery entry point", good.stdout, good_msg)
                    home_env = home_override if home_override is not None else str(home)
                    bin_env = bin_override if bin_override is not None else str(bindir)
                    proc = run_isolated(home, home_env, bin_env)
                    msg = name + " stdout=" + proc.stdout + " stderr=" + proc.stderr
                    self.assertEqual(proc.returncode, 1, msg)
                    self.assertIn("==> would fail: launcher not found ", proc.stderr.splitlines(), msg)
                    self.assertNotIn("would run recovery entry point", proc.stdout, msg)
                    self.assertNotIn("would run recovery entry point", proc.stderr, msg)

    def test_custom_bin_launcher_is_named_when_fallback_refuses_it(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            custombin = tmp_path / "custombin"
            fakebin = tmp_path / "fakebin"
            for p in (home, bindir, custombin, fakebin):
                p.mkdir(parents=True)
            custom_launcher = custombin / "opos"
            custom_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            custom_launcher.chmod(custom_launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(custom_launcher) + "\n"
                + "venv=" + str(home / "venv") + "\n"
                + "repo=" + str(root) + "\n",
                encoding="utf-8",
            )
            for name in ("ufw", "loginctl", "nft"):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(custom_launcher.is_file(), "fallback must leave the untrusted launcher behind")
            self.assertIn("not removing", proc.stderr)
            self.assertIn(str(custom_launcher), proc.stderr)

    def test_sshd_restore_stages_before_atomic_replace(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]
        self.assertIn('mktemp "/etc/ssh/sshd_config.opos.XXXXXX"', block)
        self.assertIn("cp -p", block)
        self.assertIn('sshd -t -f "$_sshd_tmp"', block)
        self.assertIn('mv -f "$_sshd_tmp"', block)
        self.assertIn('cmp "$OPOS_HOME/sshd_config.bak" "$_sshd_tmp"', block)

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_mode(mode):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / "".join(["sshd", "_config"])
            live_b = b"".join([b"li", b"ve-bytes-", b"11"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"22"])
            dest.write_bytes(live_b)
            bak = home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(bak_b)
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            if mode == "copy-fail":
                make_stub(
                    fakebin / "cp",
                    "printf 'cp %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "for a in \"$@\"; do last=\"$a\"; done\n"
                    + "printf '%s' \"" + "".join(["par", "tial-", "9"]) + "\" > \"$last\"\n"
                    + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "cp",
                    "printf 'cp %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "exec /bin/cp \"$@\"",
                )
            if mode == "validation-fail":
                make_stub(
                    fakebin / "sshd",
                    "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "sshd",
                    "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\n" + "exit 0",
                )
            if mode == "rename-fail":
                make_stub(
                    fakebin / "mv",
                    "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n" + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "mv",
                    "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "exec /bin/mv \"$@\"",
                )
            replaced = block.replace("/etc/ssh/sshd_config", str(dest))
            dropin = tmp_path / "no-dropin"
            script = "_sshd=\"skipped\"\n"
            script += "_sshd_dropin=\"" + str(dropin) + "\"\n"
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            leftovers = sorted(p.name for p in sshdir.iterdir())
            ops_lines = ops.read_text(encoding="utf-8").splitlines()
            dest_b = dest.read_bytes() if dest.is_file() else None
            bak_b_now = bak.read_bytes() if bak.is_file() else None
            return tmp, proc.stdout, dest_b, bak_b_now, leftovers, ops_lines, live_b, bak_b

        for mode in ("copy-fail", "validation-fail", "rename-fail"):
            with self.subTest(mode=mode):
                tmp, status, dest_b, bak_now, leftovers, ops_lines, live_b, bak_b = run_mode(mode)
                try:
                    self.assertEqual(status, "failed")
                    self.assertEqual(dest_b, live_b)
                    self.assertEqual(bak_now, bak_b)
                    self.assertEqual(leftovers, ["".join(["sshd", "_config"])])
                    if mode == "validation-fail":
                        sshd_idxs = [i for i, l in enumerate(ops_lines) if l.startswith("sshd ")]
                        self.assertEqual(len(sshd_idxs), 2)
                        self.assertNotIn(".bak", ops_lines[sshd_idxs[1]])
                        self.assertEqual([l for l in ops_lines if l.startswith("mv ")], [])
                finally:
                    tmp.cleanup()
        with self.subTest(mode="success"):
            tmp, status, dest_b, bak_now, leftovers, ops_lines, live_b, bak_b = run_mode("success")
            try:
                self.assertEqual(status, "succeeded")
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertEqual(leftovers, ["".join(["sshd", "_config"])])
                sshd_idxs = [i for i, l in enumerate(ops_lines) if l.startswith("sshd ")]
                self.assertGreaterEqual(len(sshd_idxs), 2)
                staged_idx = sshd_idxs[-1]
                self.assertNotIn(".bak", ops_lines[staged_idx])
                mv_idx = next(i for i, l in enumerate(ops_lines) if l.startswith("mv "))
                self.assertLess(staged_idx, mv_idx)
            finally:
                tmp.cleanup()


    def test_sshd_restore_stages_before_replacing_active_config(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]
        dest_literal = "/etc/" + "ssh/" + "sshd_config"
        bak_leaf = "".join(["sshd_config", ".bak"])
        live_leaf = "".join(["sshd_config", ".live"])

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_case(cp_mode, sshd_reject_staged):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / "".join(["sshd", "_config"])
            live_b = b"".join([b"li", b"ve-bytes-", b"51"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"52"])
            dest.write_bytes(live_b)
            bak = home / bak_leaf
            bak.write_bytes(bak_b)
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            if cp_mode == "partial":
                make_stub(
                    fakebin / "cp",
                    "for a in \"$@\"; do last=\"$a\"; done\n"
                    + "src=\"$(eval echo \\${$(($# - 1))})\"\n"
                    + "head -c 2 \"$src\" > \"$last\"\n"
                    + "exit 1",
                )
            elif cp_mode == "mismatch":
                make_stub(
                    fakebin / "cp",
                    "for a in \"$@\"; do last=\"$a\"; done\n"
                    + "printf '%s' \"" + "".join(["wro", "ng-bytes-", "9"]) + "\" > \"$last\"\n"
                    + "exit 0",
                )
            else:
                make_stub(fakebin / "cp", "exec /bin/cp \"$@\"")
            observed = tmp_path / "observed-live"
            if sshd_reject_staged:
                make_stub(
                    fakebin / "sshd",
                    "for a in \"$@\"; do last=\"$a\"; done\n"
                    + 'case "$last" in *.opos.*) cat "' + str(dest) + '" > "' + str(observed) + '"\n'
                    + "exit 1;; esac\n"
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + "exit 0",
                )
            else:
                make_stub(
                    fakebin / "sshd",
                    "for a in \"$@\"; do last=\"$a\"; done\n"
                    + 'case "$last" in *.opos.*) cat "' + str(dest) + '" > "' + str(observed) + '"\n'
                    + "exit 0;; esac\n"
                    + "exit 0",
                )
            replaced = block.replace(dest_literal, str(dest))
            dropin = tmp_path / "no-dropin"
            script = "_sshd=\"skipped\"\n"
            script += "_sshd_dropin=\"" + str(dropin) + "\"\n"
            script += replaced + "\n"
            script += "printf 'sshd=%s\\n' \"$_sshd\"\n"
            script += "[ \"$_sshd\" = failed ] && exit 1\n"
            script += "exit 0\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            dest_b = dest.read_bytes() if dest.is_file() else None
            bak_now = bak.read_bytes() if (bak.exists() or bak.is_symlink()) else None
            leftovers = sorted(p.name for p in sshdir.iterdir() if ".opos." in p.name)
            live_copy_exists = os.path.lexists(str(home / live_leaf))
            observed_b = observed.read_bytes() if observed.is_file() else None
            return tmp, proc, dest_b, bak_now, leftovers, live_copy_exists, live_b, bak_b, observed_b

        for name, cp_mode, sshd_reject in (
            ("partial_copy", "partial", False),
            ("mismatched_copy", "mismatch", False),
            ("sshd_rejects_staged", "ok", True),
        ):
            with self.subTest(name=name):
                tmp, proc, dest_b, bak_now, leftovers, live_copy_exists, live_b, bak_b, observed_b = run_case(cp_mode, sshd_reject)
                try:
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn("sshd=failed", proc.stdout, name)
                    self.assertEqual(dest_b, live_b, name)
                    self.assertEqual(bak_now, bak_b, name)
                    self.assertEqual(leftovers, [], name)
                    self.assertFalse(live_copy_exists, name)
                    if name == "sshd_rejects_staged":
                        self.assertIsNotNone(observed_b, name)
                        self.assertEqual(observed_b, live_b, name)
                finally:
                    tmp.cleanup()
        with self.subTest(name="validation_succeeds"):
            tmp, proc, dest_b, bak_now, leftovers, live_copy_exists, live_b, bak_b, observed_b = run_case("ok", False)
            try:
                self.assertEqual(observed_b, live_b)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("sshd=succeeded", proc.stdout)
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertEqual(leftovers, [])
                self.assertFalse(live_copy_exists)
            finally:
                tmp.cleanup()

    def test_sshd_restore_stages_validates_and_replaces_atomically(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]
        self.assertIn('mktemp "/etc/ssh/sshd_config.opos.XXXXXX"', block)
        self.assertIn("cp -p", block)
        self.assertIn('sshd -t -f "$_sshd_tmp"', block)
        self.assertIn('mv -f "$_sshd_tmp"', block)
        self.assertIn('cmp "$OPOS_HOME/sshd_config.bak" "$_sshd_tmp"', block)

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_mode(mode):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / "".join(["sshd", "_config"])
            live_b = b"".join([b"li", b"ve-bytes-", b"71"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"72"])
            dest.write_bytes(live_b)
            bak = home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(bak_b)
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            if mode in ("copy-fail", "copy-incomplete-success"):
                make_stub(
                    fakebin / "cp",
                    "printf 'cp %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "for a in \"$@\"; do last=\"$a\"; done\n"
                    + "printf '%s' \"" + "".join(["par", "tial-", "9"]) + "\" > \"$last\"\n"
                    + ("exit 1" if mode == "copy-fail" else "exit 0"),
                )
            else:
                make_stub(
                    fakebin / "cp",
                    "printf 'cp %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "exec /bin/cp \"$@\"",
                )
            if mode == "validation-fail":
                make_stub(
                    fakebin / "sshd",
                    "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "sshd",
                    "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\n" + "exit 0",
                )
            if mode == "rename-fail":
                make_stub(
                    fakebin / "mv",
                    "if [ ! -f \"" + str(bak) + "\" ]; then exit 99; fi\n"
                    + "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "mv",
                    "if [ ! -f \"" + str(bak) + "\" ]; then exit 99; fi\n"
                    + "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "exec /bin/mv \"$@\"",
                )
            replaced = block.replace("/etc/ssh/" + "sshd_config", str(dest))
            dropin = tmp_path / "no-dropin"
            script = "_sshd=\"skipped\"\n"
            script += "_sshd_dropin=\"" + str(dropin) + "\"\n"
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ops_lines = ops.read_text(encoding="utf-8").splitlines()
            names = sorted(p.name for p in sshdir.iterdir())
            dest_b = dest.read_bytes() if dest.is_file() else None
            bak_now = bak.read_bytes() if (bak.exists() or bak.is_symlink()) else None
            return tmp, proc.stdout, dest_b, bak_now, names, ops_lines, live_b, bak_b

        for mode in ("copy-fail", "copy-incomplete-success", "validation-fail", "rename-fail"):
            with self.subTest(mode=mode):
                tmp, status, dest_b, bak_now, names, ops_lines, live_b, bak_b = run_mode(mode)
                try:
                    self.assertEqual(status, "failed")
                    self.assertEqual(dest_b, live_b)
                    self.assertEqual(bak_now, bak_b)
                    self.assertEqual(names, ["".join(["sshd", "_config"])])
                    if mode == "validation-fail":
                        sshd_idxs = [i for i, l in enumerate(ops_lines) if l.startswith("sshd ")]
                        self.assertEqual(len(sshd_idxs), 2)
                        self.assertNotIn(".bak", ops_lines[sshd_idxs[1]])
                        self.assertEqual([l for l in ops_lines if l.startswith("mv ")], [])
                    if mode == "copy-incomplete-success":
                        sshd_lines = [l for l in ops_lines if l.startswith("sshd ")]
                        self.assertEqual(len(sshd_lines), 1)
                        self.assertIn(".bak", sshd_lines[0])
                        self.assertEqual([l for l in ops_lines if l.startswith("mv ")], [])
                finally:
                    tmp.cleanup()
        with self.subTest(mode="success"):
            tmp, status, dest_b, bak_now, names, ops_lines, live_b, bak_b = run_mode("success")
            try:
                self.assertEqual(status, "succeeded")
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertEqual(names, ["".join(["sshd", "_config"])])
                sshd_idxs = [i for i, l in enumerate(ops_lines) if l.startswith("sshd ")]
                self.assertGreaterEqual(len(sshd_idxs), 2)
                staged_idx = sshd_idxs[-1]
                self.assertNotIn(".bak", ops_lines[staged_idx])
                mv_idx = next(i for i, l in enumerate(ops_lines) if l.startswith("mv "))
                self.assertLess(staged_idx, mv_idx)
            finally:
                tmp.cleanup()

    def test_sshd_restore_preserves_live_copy_until_verified(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_mode(mode):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / "".join(["sshd", "_config"])
            live_b = b"".join([b"li", b"ve-bytes-", b"83"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"84"])
            dest.write_bytes(live_b)
            bak = home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(bak_b)
            live_copy = home / "".join(["sshd_config", ".live"])
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            make_stub(
                fakebin / "cp",
                "printf 'cp %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                + "exec /bin/cp \"$@\"",
            )
            make_stub(
                fakebin / "sshd",
                "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\n" + "exit 0",
            )
            if mode == "success":
                make_stub(
                    fakebin / "mv",
                    "cmp -s \"" + str(live_copy) + "\" \"" + str(dest) + "\" || exit 99\n"
                    + "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "exec /bin/mv \"$@\"",
                )
            elif mode == "verify-fail":
                make_stub(
                    fakebin / "mv",
                    "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "/bin/mv \"$@\"\n"
                    + "printf 'corrupt' > \"" + str(dest) + "\"\n"
                    + "exit 0",
                )
            else:
                make_stub(
                    fakebin / "mv",
                    "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n" + "exit 1",
                )
            replaced = block.replace("/etc/ssh/" + "sshd_config", str(dest))
            dropin = tmp_path / "no-dropin"
            script = "_sshd=\"skipped\"\n"
            script += "_sshd_dropin=\"" + str(dropin) + "\"\n"
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            names = sorted(p.name for p in sshdir.iterdir())
            dest_b = dest.read_bytes() if dest.is_file() and not dest.is_symlink() else None
            bak_now = bak.read_bytes() if (bak.exists() or bak.is_symlink()) else None
            live_now = live_copy.read_bytes() if (live_copy.exists() or live_copy.is_symlink()) else None
            live_exists = os.path.lexists(str(live_copy))
            return tmp, proc.stdout, proc.stderr, dest_b, bak_now, live_now, live_exists, names, live_b, bak_b, bak, dest, live_copy

        with self.subTest(mode="success"):
            tmp, status, err, dest_b, bak_now, live_now, live_exists, names, live_b, bak_b, bak, dest, live_copy = run_mode("success")
            try:
                self.assertEqual(status, "succeeded")
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertFalse(live_exists)
                self.assertEqual(names, ["".join(["sshd", "_config"])])
            finally:
                tmp.cleanup()
        with self.subTest(mode="verify-fail"):
            tmp, status, err, dest_b, bak_now, live_now, live_exists, names, live_b, bak_b, bak, dest, live_copy = run_mode("verify-fail")
            try:
                self.assertEqual(status, "failed")
                self.assertEqual(live_now, live_b)
                self.assertEqual(bak_now, bak_b)
                self.assertIn(str(live_copy), err)
                self.assertEqual(names, ["".join(["sshd", "_config"])])
            finally:
                tmp.cleanup()
        with self.subTest(mode="rename-fail"):
            tmp, status, err, dest_b, bak_now, live_now, live_exists, names, live_b, bak_b, bak, dest, live_copy = run_mode("rename-fail")
            try:
                self.assertEqual(status, "failed")
                self.assertEqual(dest_b, live_b)
                self.assertEqual(bak_now, bak_b)
                self.assertFalse(live_exists)
                self.assertEqual(names, ["".join(["sshd", "_config"])])
            finally:
                tmp.cleanup()

    def test_sshd_restore_retry_preserves_original_live_copy(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]
        dest_literal = "/etc/ssh/" + "sshd_config"
        bak_leaf = "".join(["sshd_config", ".bak"])
        live_leaf = "".join(["sshd_config", ".live"])
        staged_leaf = "".join(["sshd", "_config"])

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / staged_leaf
            live_b = b"".join([b"li", b"ve-bytes-", b"95"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"96"])
            dest.write_bytes(live_b)
            bak = home / bak_leaf
            bak.write_bytes(bak_b)
            live_copy = home / live_leaf
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            corrupt_b = b"".join([b"cor", b"rupt-9"])
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            make_stub(fakebin / "sshd", "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\nexit 0")
            make_stub(fakebin / "cp", "printf 'cp %s\\n' \"$*\" >> \"" + str(ops) + "\"\nexec /bin/cp \"$@\"")
            make_stub(fakebin / "mv", "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n/bin/mv \"$@\"\nprintf '%s' \"" + corrupt_b.decode() + "\" > \"" + str(dest) + "\"\nexit 0")
            replaced = block.replace(dest_literal, str(dest))
            dropin = tmp_path / "no-dropin"
            script = "_sshd=\"skipped\"\n"
            script += "_sshd_dropin=\"" + str(dropin) + "\"\n"
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            for idx in range(3):
                proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, "failed")
                self.assertEqual(live_copy.read_bytes(), live_b)
                self.assertEqual(bak.read_bytes(), bak_b)
                self.assertEqual(dest.read_bytes(), corrupt_b)
                if idx > 0:
                    self.assertIn(str(live_copy), proc.stderr)
            mv_lines = [line for line in ops.read_text(encoding="utf-8").splitlines() if line.startswith("mv ")]
            self.assertEqual(len(mv_lines), 1)

    def test_sshd_restore_rejects_directory_and_verifies_install(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start:start + used]
        dest_literal = "/etc/ssh/" + "sshd_config"
        bak_leaf = "".join(["sshd_config", ".bak"])
        staged_leaf = "".join(["sshd", "_config"])

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_mode(mode):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / staged_leaf
            live_b = b"".join([b"li", b"ve-bytes-", b"31"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"52"])
            dest.write_bytes(live_b)
            bak = home / bak_leaf
            bak.write_bytes(bak_b)
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            target_dir = tmp_path / "targetdir"
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            make_stub(
                fakebin / "cp",
                "printf 'cp %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                + "exec /bin/cp \"$@\"",
            )
            if mode == "dir":
                make_stub(
                    fakebin / "sshd",
                    "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + "rm -rf \"" + str(dest) + "\"\n"
                    + "mkdir -p \"" + str(dest) + "\"\n"
                    + "exit 0",
                )
            elif mode == "symlink-dir":
                make_stub(
                    fakebin / "sshd",
                    "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + "mkdir -p \"" + str(target_dir) + "\"\n"
                    + "rm -rf \"" + str(dest) + "\"\n"
                    + "ln -s \"" + str(target_dir) + "\" \"" + str(dest) + "\"\n"
                    + "exit 0",
                )
            else:
                make_stub(
                    fakebin / "sshd",
                    "printf 'sshd %s\\n' \"$*\" >> \"" + str(ops) + "\"\n" + "exit 0",
                )
            if mode == "false-success-mv":
                make_stub(
                    fakebin / "mv",
                    "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n" + "exit 0",
                )
            else:
                make_stub(
                    fakebin / "mv",
                    "printf 'mv %s\\n' \"$*\" >> \"" + str(ops) + "\"\n"
                    + "exec /bin/mv \"$@\"",
                )
            replaced = block.replace(dest_literal, str(dest))
            dropin = tmp_path / "no-dropin"
            script = "_sshd=\"skipped\"\n"
            script += "_sshd_dropin=\"" + str(dropin) + "\"\n"
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ops_lines = ops.read_text(encoding="utf-8").splitlines()
            mv_lines = [line for line in ops_lines if line.startswith("mv ")]
            names = sorted(p.name for p in sshdir.iterdir())
            bak_now = bak.read_bytes() if (bak.exists() or bak.is_symlink()) else None
            if dest.is_symlink():
                dest_kind = "symlink"
                dest_b = None
            elif dest.is_dir():
                dest_kind = "dir"
                dest_b = None
            elif dest.is_file():
                dest_kind = "file"
                dest_b = dest.read_bytes()
            else:
                dest_kind = "missing"
                dest_b = None
            return tmp, proc.stdout, dest_kind, dest_b, bak_now, names, mv_lines, live_b, bak_b

        for mode in ("dir", "symlink-dir"):
            with self.subTest(mode=mode):
                tmp, status, kind, dest_b, bak_now, names, mv_lines, live_b, bak_b = run_mode(mode)
                try:
                    self.assertEqual(status, "failed")
                    self.assertEqual(bak_now, bak_b)
                    self.assertEqual(mv_lines, [], "no move may run after destination became a directory")
                    self.assertEqual(names, [staged_leaf], "staged file must be removed, destination entry retained")
                    if mode == "dir":
                        self.assertEqual(kind, "dir")
                    else:
                        self.assertEqual(kind, "symlink")
                finally:
                    tmp.cleanup()
        with self.subTest(mode="false-success-mv"):
            tmp, status, kind, dest_b, bak_now, names, mv_lines, live_b, bak_b = run_mode("false-success-mv")
            try:
                self.assertEqual(status, "failed")
                self.assertEqual(kind, "file")
                self.assertEqual(dest_b, live_b)
                self.assertEqual(bak_now, bak_b)
                self.assertEqual(names, [staged_leaf])
            finally:
                tmp.cleanup()
        with self.subTest(mode="success"):
            tmp, status, kind, dest_b, bak_now, names, mv_lines, live_b, bak_b = run_mode("success")
            try:
                self.assertEqual(status, "succeeded")
                self.assertEqual(kind, "file")
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertEqual(names, [staged_leaf])
                self.assertTrue(mv_lines)
            finally:
                tmp.cleanup()

    def test_fallback_routes_restore_privileges(self):
        text = _read_install_sh()
        begin_marker = "# BEGIN fallback service cleanup"
        end_marker = "# END fallback service cleanup"
        block_start = text.index(begin_marker) + len(begin_marker)
        block_end = text.index(end_marker, block_start)
        block = text[block_start:block_end]
        alice = "".join(["al", "ice"])
        target_uid = "1001"
        runtime = "".join(["/run/", "user/", target_uid])
        bus = "".join(["unix:path=", runtime, "/bus"])
        cases = [
            ("ordinary", target_uid, "", "1", True),
            ("sudo", "0", alice, "1", True),
            ("sudo_without_user", "0", "", "1", True),
            ("sudo_lookup_fails", "0", alice, "0", True),
            ("ordinary_registered_no_file", target_uid, "", "1", False),
            ("sudo_registered_no_file", "0", alice, "1", False),
        ]
        for name, current_uid, sudo_user, target_ok, with_unit in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    root_home = tmp_path / "".join(["root", "-home"])
                    user_home = tmp_path / "".join(["user", "-home"])
                    root_unit = root_home / ".config" / "systemd" / "user" / "opos-server.service"
                    user_unit = user_home / ".config" / "systemd" / "user" / "opos-server.service"
                    root_unit.parent.mkdir(parents=True)
                    user_unit.parent.mkdir(parents=True)
                    root_bytes = b"".join([b"root-unit-", b"11"])
                    user_bytes = b"".join([b"user-unit-", b"22"])
                    root_unit.write_bytes(root_bytes)
                    if with_unit:
                        user_unit.write_bytes(user_bytes)
                    stubdir = tmp_path / "stubs"
                    stubdir.mkdir()
                    sudo_log = tmp_path / "sudo.log"
                    ctl_log = tmp_path / "systemctl.log"
                    rm_log = tmp_path / "rm.log"

                    def write_stub(path, body):
                        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                    def read_lines(path):
                        if path.is_file():
                            return path.read_text(encoding="utf-8").splitlines()
                        return []

                    write_stub(
                        stubdir / "id",
                        "if [ \"$#\" -eq 1 ]; then printf '%s\\n' \"$OPOS_TEST_CURRENT_UID\"\nexit 0\nfi\n"
                        + "if [ \"$OPOS_TEST_TARGET_OK\" = \"1\" ]; then printf '%s\\n' \"$OPOS_TEST_TARGET_UID\"\nexit 0\nfi\n"
                        + "exit 1\n",
                    )
                    write_stub(
                        stubdir / "getent",
                        "if [ \"$OPOS_TEST_TARGET_OK\" != \"1\" ]; then exit 2\nfi\n"
                        + "if [ \"$1\" = \"passwd\" ]; then printf '%s:x:%s:1001::%s:/bin/sh\\n' \"$2\" \"$OPOS_TEST_TARGET_UID\" \"$OPOS_TEST_USER_HOME\"\nexit 0\nfi\n"
                        + "exit 2\n",
                    )
                    write_stub(
                        stubdir / "sudo",
                        "printf '%s\\n' \"$*\" >> \"" + str(sudo_log) + "\"\n"
                        + "if [ \"$1\" != \"-u\" ]; then exit 1\nfi\n"
                        + "if [ -z \"$2\" ]; then exit 1\nfi\n"
                        + "shift 2\n"
                        + "exec \"$@\"\n",
                    )
                    write_stub(
                        stubdir / "systemctl",
                        "printf '%s\\n' \"systemctl $* HOME=$HOME XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-} DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-} LC_ALL=${LC_ALL:-}\" >> \"" + str(ctl_log) + "\"\n"
                        + "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                        + "exit 0\n",
                    )
                    write_stub(
                        stubdir / "rm",
                        "printf '%s\\n' \"rm $*\" >> \"" + str(rm_log) + "\"\n"
                        + "for a in \"$@\"; do case \"$a\" in -*) ;; \"$OPOS_TEST_ROOT_UNIT\"|\"$OPOS_TEST_USER_UNIT\") ;; *) exit 1;; esac; done\n"
                        + "exec /bin/rm \"$@\"\n",
                    )
                    runner = tmp_path / "run.sh"
                    runner.write_text("_service=\"skipped\"\n" + block + "\nprintf '%s' \"$_service\"\n", encoding="utf-8")
                    env = dict(os.environ)
                    env.pop("SUDO_USER", None)
                    if sudo_user:
                        env["SUDO_USER"] = sudo_user
                    env["HOME"] = str(user_home) if name.startswith("ordinary") else str(root_home)
                    env["PATH"] = str(stubdir) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
                    env["OPOS_TEST_CURRENT_UID"] = current_uid
                    env["OPOS_TEST_TARGET_UID"] = target_uid
                    env["OPOS_TEST_TARGET_OK"] = target_ok
                    env["OPOS_TEST_USER_HOME"] = str(user_home)
                    env["OPOS_TEST_ROOT_UNIT"] = str(root_unit)
                    env["OPOS_TEST_USER_UNIT"] = str(user_unit)
                    proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    sudo_lines = read_lines(sudo_log)
                    ctl_lines = read_lines(ctl_log)
                    rm_lines = read_lines(rm_log)
                    if name in ("ordinary", "ordinary_registered_no_file"):
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertEqual(proc.stdout, "succeeded")
                        self.assertEqual(sudo_lines, [])
                        if with_unit:
                            self.assertEqual(len(ctl_lines), 3)
                            self.assertIn("disable --now opos-server.service", ctl_lines[1])
                            self.assertIn("daemon-reload", ctl_lines[2])
                            self.assertEqual(rm_lines, ["rm -f " + str(user_unit)])
                        else:
                            joined = " ".join(ctl_lines)
                            self.assertIn("disable --now opos-server.service", joined)
                            self.assertIn("daemon-reload", joined)
                        self.assertFalse(user_unit.exists())
                        self.assertEqual(root_unit.read_bytes(), root_bytes)
                    elif name in ("sudo", "sudo_registered_no_file"):
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertEqual(proc.stdout, "succeeded")
                        if with_unit:
                            self.assertEqual(len(sudo_lines), 4, sudo_lines)
                        else:
                            self.assertGreaterEqual(len(sudo_lines), 3, sudo_lines)
                        self.assertIn("systemctl --user list-unit-files --no-legend opos-server.service", sudo_lines[0])
                        self.assertIn("systemctl --user disable --now opos-server.service", sudo_lines[1])
                        self.assertIn("systemctl --user daemon-reload", sudo_lines[-1])
                        if with_unit:
                            self.assertIn("rm -f " + str(user_unit), sudo_lines[2])
                        for line in sudo_lines:
                            self.assertIn("-u " + alice + " ", line)
                            self.assertIn("HOME=" + str(user_home), line)
                            self.assertIn("XDG_RUNTIME_DIR=" + runtime, line)
                            self.assertIn("DBUS_SESSION_BUS_ADDRESS=" + bus, line)
                            self.assertIn("LC_ALL=C", line)
                        if with_unit:
                            self.assertEqual(len(ctl_lines), 3)
                            self.assertEqual(rm_lines, ["rm -f " + str(user_unit)])
                        else:
                            joined = " ".join(ctl_lines)
                            self.assertIn("disable --now opos-server.service", joined)
                            self.assertIn("daemon-reload", joined)
                        self.assertFalse(user_unit.exists())
                        self.assertEqual(root_unit.read_bytes(), root_bytes)
                    else:
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertEqual(proc.stdout, "skipped")
                        self.assertIn("service cleanup skipped: no original non-root user could be resolved", proc.stderr)
                        self.assertEqual(sudo_lines, [])
                        self.assertEqual(ctl_lines, [])
                        self.assertEqual(rm_lines, [])
                        self.assertEqual(root_unit.read_bytes(), root_bytes)
                        self.assertEqual(user_unit.read_bytes(), user_bytes)

    def test_service_failure_diagnostics_hide_resolved_home(self):
        text = _read_install_sh()
        begin_marker = "# BEGIN fallback service cleanup"
        end_marker = "# END fallback service cleanup"
        block_start = text.index(begin_marker) + len(begin_marker)
        block_end = text.index(end_marker, block_start)
        block = text[block_start:block_end]
        svc_name = "".join(["opos-server", ".service"])
        user_name = "".join(["al", "ice"])
        target_uid = "1001"
        cases = [
            ("ordinary_probe", False, True),
            ("ordinary_missing", False, False),
            ("sudo_probe", True, True),
            ("sudo_missing", True, False),
        ]
        for name, use_sudo, with_ctl in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    caller_home = tmp_path / "".join(["cal", "ler"])
                    target_home = tmp_path / "".join(["tar", "get"])
                    caller_home.mkdir()
                    target_home.mkdir()
                    caller_unit = caller_home / ".config" / "systemd" / "user" / svc_name
                    target_unit = target_home / ".config" / "systemd" / "user" / svc_name
                    caller_unit.parent.mkdir(parents=True, exist_ok=True)
                    target_unit.parent.mkdir(parents=True, exist_ok=True)
                    caller_bytes = b"".join([b"caller-unit-", b"31"])
                    target_bytes = b"".join([b"target-unit-", b"42"])
                    caller_unit.write_bytes(caller_bytes)
                    target_unit.write_bytes(target_bytes)
                    stubdir = tmp_path / "".join(["stu", "bs"])
                    stubdir.mkdir()
                    ctl_log = tmp_path / "".join(["ctl", ".log"])
                    rm_log = tmp_path / "".join(["rm", ".log"])
                    ctl_log.write_text("", encoding="utf-8")
                    rm_log.write_text("", encoding="utf-8")

                    def write_stub(path, body):
                        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                    current_uid = "0" if use_sudo else target_uid
                    write_stub(
                        stubdir / "id",
                        "if [ \"$#\" -eq 0 ]; then printf '%s\\n' \"$OPOS_CURRENT_UID\"\nexit 0\nfi\n"
                        + "if [ \"$#\" -eq 1 ]; then printf '%s\\n' \"" + target_uid + "\"\nexit 0\nfi\nexit 1\n",
                    )
                    write_stub(
                        stubdir / "getent",
                        "if [ \"$1\" = \"passwd\" ]; then printf '%s:x:%s:1001::%s:/bin/sh\\n' \"$2\" \"" + target_uid + "\" \"$OPOS_TARGET_HOME\"\nexit 0\nfi\nexit 2\n",
                    )
                    write_stub(
                        stubdir / "sudo",
                        "if [ \"$1\" != \"-u\" ]; then exit 1\nfi\nshift 2\nexec \"$@\"\n",
                    )
                    if with_ctl:
                        write_stub(
                            stubdir / "systemctl",
                            "printf '%s\\n' \"systemctl $*\" >> \"" + str(ctl_log) + "\"\n"
                            + "case \"$*\" in *list-unit-files*) exit 1;; esac\nexit 0\n",
                        )
                    write_stub(
                        stubdir / "rm",
                        "printf '%s\\n' \"rm $*\" >> \"" + str(rm_log) + "\"\nexec /bin/rm \"$@\"\n",
                    )
                    for tool in ("cut", "grep", "env"):
                        found = None
                        for entry in os.environ.get("PATH", "").split(os.pathsep):
                            cand = os.path.join(entry, tool)
                            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                                found = cand
                                break
                        self.assertIsNotNone(found, tool)
                        os.symlink(found, str(stubdir / tool))
                    runner = tmp_path / "run.sh"
                    runner.write_text("_service=\"skipped\"\n" + block + "\nprintf '%s' \"$_service\"\n", encoding="utf-8")
                    env = dict(os.environ)
                    env.pop("SUDO_USER", None)
                    if use_sudo:
                        env["SUDO_USER"] = user_name
                    env["HOME"] = str(caller_home)
                    env["PATH"] = str(stubdir)
                    env["OPOS_CURRENT_UID"] = current_uid
                    env["OPOS_TARGET_HOME"] = str(target_home)
                    proc = subprocess.run(["/bin/sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(proc.stdout, "failed", name)
                    if with_ctl:
                        want = "".join(["systemd probe failed; preserving "]) + svc_name
                    else:
                        want = "".join(["systemctl not found; preserving "]) + svc_name
                    self.assertIn(want, proc.stderr.splitlines(), name)
                    for leak in (str(caller_home), str(target_home), str(caller_unit), str(target_unit)):
                        self.assertNotIn(leak, proc.stdout, name)
                        self.assertNotIn(leak, proc.stderr, name)
                    self.assertEqual(caller_unit.read_bytes(), caller_bytes, name)
                    self.assertEqual(target_unit.read_bytes(), target_bytes, name)
                    calls = ctl_log.read_text(encoding="utf-8")
                    self.assertNotIn("disable", calls, name)
                    self.assertNotIn("daemon-reload", calls, name)
                    rm_calls = rm_log.read_text(encoding="utf-8")
                    self.assertEqual(rm_calls, "", name)

    def test_missing_systemctl_preserves_unit_and_reports_failed(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            farm = tmp_path / "farm"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, farm, root):
                p.mkdir(parents=True)
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_dir = opos_home / "venv"
            venv_dir.mkdir(parents=True)
            (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
            (opos_home / "manifest").write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            marker = b"".join([b"unit-bytes-", b"91"])
            unit.write_bytes(marker)
            seen = set()
            for entry in os.environ.get("PATH", "").split(os.pathsep):
                if not entry or not os.path.isdir(entry):
                    continue
                for name in os.listdir(entry):
                    if name == "systemctl" or name in seen:
                        continue
                    src = os.path.join(entry, name)
                    if os.path.isfile(src) and os.access(src, os.X_OK):
                        try:
                            os.symlink(src, farm / name)
                        except FileExistsError:
                            pass
                        seen.add(name)
            id_link = farm / "id"
            if id_link.is_symlink() or id_link.exists():
                id_link.unlink()
            id_stub = farm / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(farm)
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("service=failed", proc.stdout)
            self.assertTrue(unit.is_file())
            self.assertEqual(unit.read_bytes(), marker)

    def test_uninstall_subprocesses_are_bounded(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestInstallReview")
        wanted = ("test_fallback_restores_sshd_firewall_and_service", "test_restore_steps_report_summary")
        found = 0
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        is_run = (
                            isinstance(func, ast.Attribute)
                            and func.attr == "run"
                            and isinstance(func.value, ast.Name)
                            and func.value.id == "subprocess"
                        )
                        if is_run and any(
                            isinstance(a, ast.List)
                            and any(isinstance(e, ast.Constant) and e.value == "--uninstall" for e in a.elts)
                            for a in sub.args
                        ):
                            keys = [k.arg for k in sub.keywords]
                            self.assertIn("timeout", keys, node.name + " uninstall run must bound execution")
                            found += 1
        self.assertEqual(found, 2, "expected two bounded uninstall runs")


    def test_failed_firewall_restore_preserves_state_for_retry(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_name = "".join(["fire", "wall.state"])
        failed_mark = "".join(["fire", "wall=failed"])
        ok_mark = "".join(["fire", "wall=succeeded"])
        want_args = "".join(["--", "force disable"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, stubdir, root):
                p.mkdir(parents=True)
            sentinel = b"inactive\n"
            state = opos_home / state_name
            state.write_bytes(sentinel)
            self.assertFalse((bindir / "opos").exists())
            self.assertFalse((opos_home / "manifest").exists())
            log = tmp_path / "ufw.log"
            log.write_text("", encoding="utf-8")
            ufw = stubdir / "ufw"
            ufw.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 1\n", encoding="utf-8")
            ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl = stubdir / "systemctl"
            ctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl.chmod(ctl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn(failed_mark, proc.stdout)
            self.assertIn(want_args, log.read_text(encoding="utf-8"))
            self.assertEqual(state.read_bytes(), sentinel)
            ufw.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 0\n", encoding="utf-8")
            ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn(ok_mark, proc.stdout)
            self.assertFalse(state.exists(), "state file must be removed after successful retry")


    def test_verified_recovery_enforces_each_prerequisite(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]
        text = _read_install_sh()
        hstart = text.index("verified_recovery()")
        hend = text.index("do_uninstall()", hstart)
        helper = text[hstart:hend]
        prereqs = [
            ("manifest", r'\[ -f "\$MANIFEST" \]'),
            ("venv_equality", r'\[ "(\$\{UVENV:-\}|\$UVENV)" = "\$OPOS_HOME/venv" \]'),
            ("venv_ownership", r'opos_owned_path "(\$\{UVENV:-\}|\$UVENV)" "\$OPOS_HOME/venv"'),
            ("recovery_python", r'\[ -x "\$UVENV/bin/python" \]'),
            ("src_dir", r'\[ -d "\$ROOT/src/opos" \]'),
            ("repo_match", r'canon_repo "\$UREPO"'),
        ]

        def make_valid(tmp_path):
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            home.mkdir()
            bindir.mkdir()
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            marker = tmp_path / "recovery-marker"
            venv_python.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(tmp_path / "bin" / "opos") + "\n"
                + "venv=" + str(home / "venv") + "\n"
                + "repo=" + str(root) + "\n",
                encoding="utf-8",
            )
            return home, bindir, marker

        def run_copy(script_path, home, bindir):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            return subprocess.run(
                ["sh", str(script_path), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home, bindir, marker = make_valid(tmp_path)
            proc = run_copy(install, home, bindir)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)
        for label, pat in prereqs:
            with self.subTest(prereq=label):
                found = re.search(pat, helper)
                self.assertIsNotNone(found, label + " prerequisite must exist")
                mutated_helper = helper[:found.start()] + "false" + helper[found.end():]
                mutated = text[:hstart] + mutated_helper + text[hend:]
                self.assertNotEqual(mutated, text)
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home, bindir, marker = make_valid(tmp_path)
                    copy_path = tmp_path / "install-copy.sh"
                    copy_path.write_text(mutated, encoding="utf-8")
                    proc = run_copy(copy_path, home, bindir)
                    self.assertNotEqual(proc.returncode, 0, label)
                    self.assertNotIn("would run recovery entry point", proc.stdout, label)
                    self.assertFalse(marker.is_file(), label + " recovery python must not execute")

    def test_verified_recovery_actual_uninstall_marker_control(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def make_valid(tmp_path):
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            for p in (home, bindir, fakebin):
                p.mkdir(parents=True)
            marker = tmp_path / "recovery-marker"
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\ntouch \"" + str(marker) + "\"\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(bindir / "opos") + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(root) + "\n",
                encoding="utf-8",
            )
            write_stub(fakebin / "ufw", "exit 0\n")
            write_stub(fakebin / "systemctl", "case \"$*\" in *list-unit-files*) exit 0;; esac\nexit 0\n")
            for name in ("loginctl", "nft", "cp", "sshd"):
                write_stub(fakebin / name, "exit 0\n")
            write_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"\n")
            return home, bindir, fakebin, marker

        def run_real(home, bindir, fakebin, extra=None, script_path=install):
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            if extra:
                env.update(extra)
            return subprocess.run(
                ["sh", str(script_path), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home, bindir, fakebin, marker = make_valid(tmp_path)
            proc = run_real(home, bindir, fakebin)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(marker.is_file(), "valid actual uninstall must execute recovery")

        cases = ("missing_manifest", "venv_mismatch", "python_missing", "repo_mismatch", "src_missing")
        for name in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home, bindir, fakebin, marker = make_valid(tmp_path)
                    extra = None
                    script_path = install
                    if name == "missing_manifest":
                        (home / "manifest").unlink()
                    elif name == "venv_mismatch":
                        (home / "manifest").write_text(
                            "launcher=" + str(bindir / "opos") + "\nvenv=" + str(tmp_path / "other" / "venv") + "\nrepo=" + str(root) + "\n",
                            encoding="utf-8",
                        )
                    elif name == "python_missing":
                        target = home / "venv" / "bin" / "python"
                        if target.is_file() or target.is_symlink():
                            target.unlink()
                    elif name == "repo_mismatch":
                        (home / "manifest").write_text(
                            "launcher=" + str(bindir / "opos") + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(tmp_path / "elsewhere") + "\n",
                            encoding="utf-8",
                        )
                    elif name == "src_missing":
                        bare = tmp_path / "bare"
                        bare.mkdir()
                        (home / "manifest").write_text(
                            "launcher=" + str(bindir / "opos") + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(bare) + "\n",
                            encoding="utf-8",
                        )
                        install_src = bare / "install.sh"
                        install_src.write_bytes(install.read_bytes())
                        script_path = install_src
                    proc = run_real(home, bindir, fakebin, extra, script_path=script_path)
                    msg = name + " must prevent recovery execution; stdout=" + proc.stdout + "; stderr=" + proc.stderr
                    self.assertEqual(proc.returncode, 0, msg)
                    self.assertIn("==> restore summary:", proc.stdout, msg)
                    self.assertFalse(marker.is_file(), msg)
                    if name != "missing_manifest":
                        self.assertIn("warning: force uninstall without verified recovery entry point, removing files only", proc.stderr, msg)
                    if name == "repo_mismatch":
                        self.assertIn("repo mismatch: run the uninstaller from the original checkout", proc.stderr, msg)
                    if name == "src_missing":
                        self.assertNotIn("repo mismatch: run the uninstaller from the original checkout", proc.stdout + proc.stderr, msg)

    def test_untrusted_launcher_warning_omits_control_characters(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        first = "".join(["reject", "ed-"])
        second = "".join(["FOR", "GED"])
        esc = chr(27)
        cret = chr(13)
        bad_prefix = first
        bad_suffix = second
        for name, extra in (("normal", []), ("dry_run", ["--dry-run"])):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    bindir = tmp_path / "bin"
                    stubdir = tmp_path / "stubs"
                    home.mkdir()
                    bindir.mkdir()
                    stubdir.mkdir()
                    trusted = bindir / "opos"
                    trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    launcher_value = "".join([first, esc, "[2J", cret, second])
                    (home / "manifest").write_text(
                        "launcher=" + launcher_value + "\n"
                        + "venv=" + str(home / "venv") + "\n"
                        + "repo=" + str(home) + "\n",
                        encoding="utf-8",
                    )
                    for tool in ("ufw", "loginctl", "nft"):
                        stub = stubdir / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_stub = stubdir / "systemctl"
                    ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"] + extra,
                        capture_output=True,
                        env=env,
                        timeout=30,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    first_line = b"warning: ignoring untrusted launcher in manifest: [redacted]\n"
                    second_line = b"warning: not removing launcher outside trusted location: [redacted]\n"
                    self.assertIn(first_line, proc.stderr)
                    self.assertIn(second_line, proc.stderr)
                    self.assertIn(first_line.strip(), proc.stderr.splitlines())
                    self.assertIn(second_line.strip(), proc.stderr.splitlines())
                    self.assertNotIn(bad_prefix.encode(), proc.stdout)
                    self.assertNotIn(bad_prefix.encode(), proc.stderr)
                    self.assertNotIn(bad_suffix.encode(), proc.stdout)
                    self.assertNotIn(bad_suffix.encode(), proc.stderr)
                    self.assertNotIn(esc.encode(), proc.stdout)
                    self.assertNotIn(esc.encode(), proc.stderr)
                    self.assertNotIn(cret.encode(), proc.stdout)
                    self.assertNotIn(cret.encode(), proc.stderr)
                    if extra:
                        dry_line = b"==> warning: ignoring untrusted launcher in manifest: [redacted]\n"
                        run_line = ("==> would run " + str(trusted) + " uninstall --dry-run").encode() + b"\n"
                        self.assertIn(dry_line, proc.stdout)
                        self.assertIn(run_line, proc.stdout)
                        self.assertIn(dry_line.strip(), proc.stdout.splitlines())
                        self.assertIn(run_line.strip(), proc.stdout.splitlines())

    def test_production_paths_reject_unquoted_and_dynamic(self):
        text = _read_install_sh()
        unquoted, dynamic = _scan_installer_paths(text)
        probe_unquoted = 'rm -rf $TARGET/rest\nmv $SRC "$DST"\n'
        probe_dynamic = 'rm -rf "$BASE/$SUFFIX"\n'
        probe_fixed = 'rm -rf "$BASE/static-leaf"\n'
        probe_concat = 'rm -rf "$BASE"/"$SUFFIX"\n'
        probe_split = 'mv "$SRC" "$DST"\n'
        probe_concat_static = 'rm -rf "$BASE"/static-leaf\n'
        probe_positional_unquoted = 'rm -rf $1/rest\nmv $2 "$DST"\n'
        probe_special_unquoted = 'rm -rf $@\nrm -rf $*/leaf\n'
        probe_positional_dynamic = 'rm -rf "$BASE/$1"\n'
        probe_arglist_dynamic = 'rm -rf "$BASE/$@"\nrm -rf "$BASE/$*"\n'
        def _scan(snippet):
            return _scan_installer_paths(snippet)
        puq, _ = _scan(probe_unquoted)
        self.assertEqual(len(puq), 2, "scan must catch unquoted operands")
        _, pdyn = _scan(probe_dynamic)
        self.assertEqual(len(pdyn), 1, "scan must catch dynamic quoted paths")
        _, cpdyn = _scan(probe_concat)
        self.assertEqual(len(cpdyn), 1, "scan must catch split dynamic paths")
        fuq, fdyn = _scan(probe_fixed)
        self.assertEqual(fuq, [], "scan must accept static quoted paths")
        self.assertEqual(fdyn, [], "scan must accept static quoted paths")
        suq, spdyn = _scan(probe_split)
        self.assertEqual(suq, [], "scan must accept separate quoted operands")
        self.assertEqual(spdyn, [], "scan must accept separate quoted operands")
        csuq, csdyn = _scan(probe_concat_static)
        self.assertEqual(csuq, [], "scan must accept split static suffix")
        self.assertEqual(csdyn, [], "scan must accept split static suffix")
        puq_pos, _ = _scan(probe_positional_unquoted)
        self.assertEqual(len(puq_pos), 2, "scan must catch unquoted positional operands")
        suq_spec, _ = _scan(probe_special_unquoted)
        self.assertEqual(len(suq_spec), 2, "scan must catch unquoted special operands")
        _, pdyn_pos = _scan(probe_positional_dynamic)
        self.assertEqual(len(pdyn_pos), 1, "scan must catch dynamic paths from positional params")
        _, pdyn_arg = _scan(probe_arglist_dynamic)
        self.assertEqual(len(pdyn_arg), 2, "scan must catch dynamic paths from argument lists")
        probe_arglist_prefix = 'rm -rf "$@/leaf"\nrm -rf "$*/leaf"\n'
        _, pdyn_prefix = _scan(probe_arglist_prefix)
        self.assertEqual(len(pdyn_prefix), 2, "scan must catch argument lists with static suffix")
        proc = subprocess.run(
            ["sh", "-c", 'set -- alpha beta; printf "<%s>\\n" "$@/leaf"'],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("/leaf>"))
        self.assertNotEqual(lines[0], lines[1])
        self.assertEqual(unquoted, [], "filesystem ops must quote every path operand")
        self.assertEqual(dynamic, [], "filesystem ops must not use dynamically built paths")

    def test_regression_probes_and_installer_check_share_scanner(self):
        probe = 'rm -rf "$BASE/$SUFFIX"\n'
        uq, dyn = _scan_installer_paths(probe)
        self.assertEqual(len(dyn), 1)
        self.assertEqual(uq, [])
        with unittest.mock.patch.object(sys.modules[__name__], "_scan_installer_paths", wraps=_scan_installer_paths) as scan_mock:
            self.test_production_paths_reject_unquoted_and_dynamic()
            self.assertGreater(scan_mock.call_count, 0)
            probed = [c.args[0] for c in scan_mock.call_args_list if "$SUFFIX" in c.args[0]]
            self.assertTrue(probed)

    def test_production_paths_reject_split_positional_and_special_parameters(self):
        from unittest.mock import patch
        cases = (
            ("1", 'rm -rf "$BASE"/"$1"'),
            ("@", 'rm -rf "$BASE"/"$@"'),
            ("*", 'rm -rf "$BASE"/"$*"'),
        )
        for param, snippet in cases:
            with self.subTest(param=param):
                with patch("tests.test_install_review._read_install_sh", return_value=snippet):
                    with self.assertRaisesRegex(AssertionError, "filesystem ops must not use dynamically built paths"):
                        self.test_production_paths_reject_unquoted_and_dynamic()
        with patch("tests.test_install_review._read_install_sh", return_value='rm -rf "$BASE"/static-leaf'):
            self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_catch_unsafe_command_after_continuation_separator(self):
        from unittest.mock import patch
        for sep in ("", "# separator"):
            with self.subTest(sep=sep):
                marker = "echo " + chr(34) + "# marker" + chr(34) + " " + chr(92)
                payload = marker + chr(10) + sep + chr(10) + "rm -rf " + chr(36) + "TARGET/rest" + chr(10)
                with patch("tests.test_install_review._read_install_sh", return_value=payload):
                    with self.assertRaisesRegex(AssertionError, "filesystem ops must quote every path operand"):
                        self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_quoted_hash_does_not_hide_command(self):
        from unittest.mock import patch
        snippets = [
            "printf \"%s\" \"#\"; rm -rf $TARGET/rest",
            "printf '%s' '#'; rm -rf $TARGET/rest",
        ]
        for snippet in snippets:
            with patch("tests.test_install_review._read_install_sh", return_value=snippet):
                with self.assertRaisesRegex(AssertionError, "filesystem ops must quote every path operand"):
                    self.test_production_paths_reject_unquoted_and_dynamic()

        # dynamic operand after quoted hash must still be rejected
        dynamic_snippet = 'printf "%s" "#"; rm -rf "$BASE/$SUFFIX"'
        with patch("tests.test_install_review._read_install_sh", return_value=dynamic_snippet):
            with self.assertRaisesRegex(AssertionError, "filesystem ops must not use dynamically built paths"):
                self.test_production_paths_reject_unquoted_and_dynamic()

        # real trailing comment must be stripped and not hide unsafe command
        comment_snippet = 'rm -rf "$BASE/static-leaf" # rm -rf $TARGET/rest'
        with patch("tests.test_install_review._read_install_sh", return_value=comment_snippet):
            self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_catch_escaped_space_operands(self):
        q = chr(34)
        bs = chr(92)
        probe = "rm -rf " + q + "$BASE" + q + "/some" + bs + " " + chr(126) + "/rel/" + q + "$SUFFIX" + q + "\n"
        self.assertEqual(_line_path_violations(probe.strip()), [])
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=probe):
            with self.assertRaisesRegex(AssertionError, "must not use dynamically built paths"):
                self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_catch_continued_operands(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        head = 'rm -rf "$BASE/static-leaf" \\'
        tail = '"$BASE/$SUFFIX"'
        mutant = text + "\n" + head + "\n    " + tail + "\n"
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()
        safe = text + "\n" + head + "\n    " + '"$BASE/static-leaf"\n'
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe):
            self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_catch_continued_wildcard_operands(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        star_tail = "*" + ".bak"
        head = 'rm -rf "$BASE/static-leaf" \\'
        mutant = text + "\n" + head + "\n    " + star_tail + "\n"
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()
        safe = text + "\n" + head + "\n    " + '"$BASE/static-leaf"\n'
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe):
            self.test_production_paths_reject_unquoted_and_dynamic()

    def test_continuation_preserves_indented_unsafe_operand(self):
        probe = "rm" + chr(92) + chr(10) + "    " + chr(126) + "/opos-x"
        joined = _join_continuations(probe)
        self.assertIn("rm    " + chr(126), joined)
        self.assertTrue(_line_path_violations(joined))
        safe = "rm" + chr(92) + chr(10) + "    " + chr(34) + "$OPOS_HOME/x" + chr(34)
        self.assertEqual(_line_path_violations(_join_continuations(safe)), [])
        glued = "rm" + chr(92) + chr(10) + "bar"
        self.assertEqual(_join_continuations(glued), "rmbar")

    def test_continuation_blank_and_comment_lines_preserve_boundaries(self):
        bs = chr(92)
        nl = chr(10)
        h = chr(35)
        dq = chr(34)
        ds = chr(36)
        tgt = ds + "TARGET/rest"
        base = dq + ds + "BASE" + dq
        suffix = dq + ds + "SUFFIX" + dq
        for sep in ("", "   ", h + " separator", "   " + h + " indented"):
            with self.subTest(sep=repr(sep)):
                marker = "echo foo" + bs
                payload = marker + nl + sep + nl + "rm -rf " + tgt + nl
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=payload):
                    with self.assertRaisesRegex(AssertionError, "filesystem ops must quote every path operand"):
                        self.test_production_paths_reject_unquoted_and_dynamic()
                safe_head = "rm -rf " + base + "/static-leaf " + bs
                safe_tail = base + "/" + suffix
                safe_payload = safe_head + nl + sep + nl + safe_tail + nl
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe_payload):
                    self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_comment_backslash_does_not_swallow_command(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        mutant = text + "\n# note \\\nrm -rf $TARGET/rest\n"
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_subst_comment_backslash_does_not_swallow_command(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        dq = chr(34)
        ds = chr(36)
        op = chr(40)
        cp = chr(41)
        h = chr(35)
        bs = chr(92)
        nl = chr(10)
        tgt = ds + "TARGET/rest"
        mutant = text + nl + "echo " + dq + ds + op + " " + h + " note " + bs + nl + "rm -rf " + tgt + nl + cp + dq + nl
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_inline_comment_and_single_quote_continuations(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        bs = chr(92)
        nl = chr(10)
        h = chr(35)
        q = chr(39)
        dq = chr(34)
        inline = text + nl + "rm -rf " + dq + "$BASE/static-leaf" + dq + " " + h + " note " + bs + nl + "rm -rf $TARGET/rest" + nl
        self.assertNotEqual(inline, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=inline):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()
        quoted = "echo " + q + "a " + bs + nl + "b" + q + nl
        self.assertIn(bs + nl, _join_continuations(quoted))
        safe = text + nl + "echo " + q + "a " + bs + nl + "b" + q + nl
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe):
            self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_heredoc_quote_does_not_swallow_command(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        bs = chr(92)
        nl = chr(10)
        h = chr(35)
        dq = chr(34)
        heredoc = "cat <<EOF" + nl + dq + nl + "EOF" + nl
        mutant = text + nl + heredoc + "rm -rf " + dq + "$BASE/static-leaf" + dq + " " + h + " note " + bs + nl + "rm -rf $TARGET/rest" + nl
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()
        safe = text + nl + heredoc + "rm -rf " + dq + "$BASE/static-leaf" + dq + nl
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe):
            self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_heredoc_hash_delimiter_keeps_command(self):
        h = chr(35)
        star = chr(42)
        delim = "END" + h + "tag"
        cmd = "rm -rf " + chr(34) + "$BASE" + chr(34) + "/" + star
        snippet = "cat <<" + delim + "\nbody\n" + delim + "\n" + cmd + "\n"
        masked = _mask_heredoc_bodies(snippet)
        self.assertIn(cmd, masked)
        with self.assertRaises(AssertionError):
            _assert_snippet_confined(snippet)

    def test_production_paths_reject_bare_glob_and_tilde(self):
        tilde_x = "~" + "/x"
        tilde_y = "~" + "/y"
        star_bak = "*" + ".bak"
        home_var = "$" + "OPOS_HOME"
        bad = [
            "rm -rf " + home_var + "/" + "*",
            "rm -rf " + '"' + home_var + '"' + "/" + "*",
            "rm -rf " + tilde_x,
            "cp a " + "~",
            "mv a " + star_bak,
            "X=" + tilde_y,
        ]
        for line in bad:
            with self.subTest(line=line):
                self.assertTrue(_line_path_violations(line), "must flag: %r" % line)
        good = [
            "rm -rf " + '"' + home_var + "/" + '*' + '"',
            "rm -rf " + '"' + tilde_x + '"',
            "rm -rf " + "'" + "*" + "'",
            "rm -rf a" + "~" + "b",
            "rm -rf " + "\\" + "*",
        ]
        for line in good:
            with self.subTest(line=line):
                self.assertFalse(_line_path_violations(line), "must pass: %r" % line)
        text = _read_install_sh()
        anchor = text.splitlines()[0]
        self.assertTrue(anchor is not None)
        fixture = text + "\nrm -rf " + tilde_x + "\n"
        self.assertNotEqual(fixture, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=fixture):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_hash_inside_word_keeps_glob(self):
        h = chr(35)
        s = chr(42)
        q = chr(34)
        base = q + "$BASE" + q
        inside = "rm -rf " + base + "/foo" + h + s + ".bak"
        self.assertTrue(_line_path_violations(inside), "must flag: %r" % inside)
        quoted = "rm -rf " + q + h + "hash" + q + " " + base + "/" + s
        self.assertTrue(_line_path_violations(quoted), "must flag: %r" % quoted)
        comment = "rm -rf " + base + "/static-leaf " + h + " comment " + s
        self.assertEqual(_line_path_violations(comment), [])

    def test_production_paths_escaped_separator_keeps_glob(self):
        h = chr(35)
        s = chr(42)
        q = chr(34)
        bs = chr(92)
        sp = chr(32)
        semi = chr(59)
        base = q + "$BASE" + q
        escaped_space = "rm -rf " + base + "/foo" + bs + sp + h + s + ".bak"
        escaped_semi = "rm -rf " + base + "/foo" + bs + semi + h + s + ".bak"
        for line in (escaped_space, escaped_semi):
            with self.subTest(line=line):
                self.assertIn(s, _strip_shell_comment(line))
                self.assertTrue(_line_path_violations(line), "must flag: %r" % line)

    def test_disabled_firewall_requires_exact_saved_state(self):
        text = _read_install_sh()
        leaf = "".join(["fire", "wall.state"])
        anchor = 'if [ -f "$OPOS_HOME/' + leaf + '" ]'
        start = text.index(anchor)
        line_start = text.rindex("\n", 0, start) + 1
        tail = text[line_start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[line_start:line_start + used]
        self.assertIn(leaf, block)
        valid = "".join(["in", "active"]) + "\n"
        self.assertEqual(valid, "inactive\n")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            opos_home = tmp_path / "opos"
            opos_home.mkdir()
            saved = opos_home / leaf
            saved.write_bytes(valid.encode())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            log = tmp_path / "ufw.log"
            stub = stubdir / "ufw"
            stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 0\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            script = "OPOS_HOME=\"" + str(opos_home) + "\"\n_firewall=\"skipped\"\n" + block + "\nprintf '%s' \"$_firewall\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "succeeded", "exact disabled state must restore")
            self.assertFalse(saved.exists(), "exact disabled state must be consumed")
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertIn("".join(["--", "force disable"]), calls)
            self.assertIn("reload", calls)
        tricky = [
            "".join(["in", "active"]) + " \n",
            " " + "".join(["in", "active"]) + "\n",
            "".join(["IN", "ACTIVE"]) + "\n",
            "".join(["in", "active"]) + "\n" + "".join(["ex", "tra"]) + "\n",
            "".join(["of", "f"]) + " \n",
        ]
        for idx, data in enumerate(tricky):
            with self.subTest(case=idx):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    before = data.encode()
                    saved = opos_home / leaf
                    saved.write_bytes(before)
                    stubdir = tmp_path / "stubs"
                    stubdir.mkdir()
                    log = tmp_path / "ufw.log"
                    stub = stubdir / "ufw"
                    stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 0\n", encoding="utf-8")
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    script = "OPOS_HOME=\"" + str(opos_home) + "\"\n_firewall=\"skipped\"\n" + block + "\nprintf '%s' \"$_firewall\"\n"
                    runner = tmp_path / "run.sh"
                    runner.write_text(script, encoding="utf-8")
                    env = dict(os.environ)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout, "failed", "near-disabled snapshot must not restore")
                    self.assertEqual(saved.read_bytes(), before, "malformed snapshot must be preserved")
                    self.assertFalse(log.exists(), "malformed snapshot must make no UFW calls")

    def test_replacement_restores_disabled_firewall_and_expected_service(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        fw_leaf = "".join(["fire", "wall.state"])
        fw_ok = "".join(["fire", "wall=succeeded"])
        svc_ok = "service=succeeded"
        want_fw = "".join(["--", "force disable"])
        want_reload = "reload"
        want_disable = "disable --now opos-server.service"
        want_daemon = "daemon-reload"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, fakebin, root):
                p.mkdir(parents=True)
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_dir = opos_home / "venv"
            venv_dir.mkdir(parents=True)
            (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
            (opos_home / "manifest").write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            fw_state = opos_home / fw_leaf
            fw_state.write_bytes(b"inactive\n")
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_bytes(b"".join([b"unit-bytes-", b"32"]))
            ufw_log = tmp_path / "ufw.log"
            ufw_log.write_text("", encoding="utf-8")
            ctl_log = tmp_path / "ctl.log"
            ctl_log.write_text("", encoding="utf-8")
            ufw_stub = fakebin / "ufw"
            ufw_stub.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(ufw_log) + "\"\nexit 0\n",
                encoding="utf-8",
            )
            ufw_stub.chmod(ufw_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(ctl_log) + "\"\n"
                + "case \"$*\" in *list-unit-files*) printf 'opos-server.service enabled\\n'\nexit 0;; esac\n"
                + "case \"$*\" in *disable*) exit 0;; esac\n"
                + "case \"$*\" in *daemon-reload*) exit 0;; esac\n"
                + "exit 0\n",
                encoding="utf-8",
            )
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            for name in ("loginctl", "nft"):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
            self.assertNotEqual(line, "")
            self.assertIn(fw_ok, line)
            self.assertIn(svc_ok, line)
            logged_ufw = ufw_log.read_text(encoding="utf-8")
            self.assertIn(want_fw, logged_ufw)
            self.assertIn(want_reload, logged_ufw)
            logged_ctl = ctl_log.read_text(encoding="utf-8")
            self.assertIn(want_disable, logged_ctl)
            self.assertIn(want_daemon, logged_ctl)
            self.assertFalse(fw_state.exists())
            self.assertFalse(unit.exists())

    def test_force_hardening_retains_rollback_outside_checkout(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            clone = tmp_path / "clone"
            for p in (home, opos_home, bindir):
                p.mkdir(parents=True)
            (root / "var" / "backups").mkdir(parents=True)
            (root / "var" / "backups" / "opos-harden").write_text("on\n", encoding="utf-8")
            (clone / ".git").mkdir(parents=True)
            (clone / "deploy").mkdir(parents=True)
            (clone / "install.sh").write_bytes(install.read_bytes())
            helper_src = clone / "deploy" / "harden-device.sh"
            helper_bytes = b"".join([b"#!/bin/sh\n", b"exit 0\n"])
            helper_src.write_bytes(helper_bytes)
            helper_src.chmod(helper_src.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (opos_home / "manifest").write_text(
                "launcher=" + str(bindir / "opos") + "\nvenv=" + str(opos_home / "venv") + "\nrepo=" + str(clone) + "\n",
                encoding="utf-8",
            )
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            for name in ("ufw", "loginctl", "nft"):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            clean_stub = fakebin / "git"
            clean_stub.write_text(
                "#!/bin/sh\ncase \"$*\" in *show-toplevel*) printf '%s\\n' \"" + str(clone) + "\"\nexit 0;; esac\nexit 0\n",
                encoding="utf-8",
            )
            clean_stub.chmod(clean_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(clone / "install.sh"), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            retained = opos_home / "opos-harden-rollback.sh"
            self.assertTrue(retained.is_file(), "force must retain rollback helper outside checkout")
            self.assertEqual(retained.read_bytes(), helper_bytes)
            self.assertTrue(os.access(str(retained), os.X_OK), "retained helper must stay executable")
            self.assertIn(str(retained), proc.stdout)
            self.assertTrue((clone / "deploy" / "harden-device.sh").is_file(), "checkout must be preserved without ownership record")

    def test_retention_refuses_unverified_destination(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        leaf = "".join(["opos-harden-", "rollback.sh"])
        helper_rel = "".join(["harden-device", ".sh"])
        for kind in ("regular", "symlink", "dangling"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    clone = tmp_path / "clone"
                    deploy = clone / "deploy"
                    deploy.mkdir(parents=True)
                    helper_src = deploy / helper_rel
                    helper_bytes = b"".join([b"helper-", b"bytes-9"])
                    helper_src.write_bytes(helper_bytes)
                    home = tmp_path / "home"
                    home.mkdir()
                    dest = home / leaf
                    before_bytes = None
                    before_mode = None
                    before_link = None
                    before_lstat = None
                    before_real = None
                    before_real_mode = None
                    real = tmp_path / "".join(["real-", "target"])
                    missing = tmp_path / "".join(["missing-", "target"])
                    if kind == "regular":
                        dest.write_bytes(b"".join([b"existing-", b"bytes-3"]))
                        os.chmod(str(dest), 0o600)
                        before_bytes = dest.read_bytes()
                        before_mode = stat.S_IMODE(os.stat(str(dest)).st_mode)
                    elif kind == "symlink":
                        real.write_bytes(b"".join([b"real-", b"bytes-4"]))
                        os.chmod(str(real), 0o600)
                        os.symlink(str(real), str(dest))
                        before_link = os.readlink(str(dest))
                        before_lstat = os.lstat(str(dest))
                        before_real = real.read_bytes()
                        before_real_mode = stat.S_IMODE(os.stat(str(real)).st_mode)
                    else:
                        os.symlink(str(missing), str(dest))
                        before_link = os.readlink(str(dest))
                        before_lstat = os.lstat(str(dest))
                    runner = tmp_path / "run.sh"
                    runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(home)
                    env["DRY"] = "0"
                    proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertNotEqual(proc.returncode, 0, kind)
                    if kind == "regular":
                        self.assertFalse(dest.is_symlink())
                        self.assertEqual(dest.read_bytes(), before_bytes)
                        self.assertEqual(stat.S_IMODE(os.stat(str(dest)).st_mode), before_mode)
                    elif kind == "symlink":
                        self.assertTrue(dest.is_symlink())
                        self.assertEqual(os.readlink(str(dest)), before_link)
                        after_lstat = os.lstat(str(dest))
                        self.assertEqual(after_lstat.st_ino, before_lstat.st_ino)
                        self.assertEqual(after_lstat.st_dev, before_lstat.st_dev)
                        self.assertEqual(after_lstat.st_mode, before_lstat.st_mode)
                        self.assertEqual(real.read_bytes(), before_real)
                        self.assertEqual(stat.S_IMODE(os.stat(str(real)).st_mode), before_real_mode)
                    else:
                        self.assertTrue(dest.is_symlink())
                        self.assertFalse(dest.exists())
                        self.assertEqual(os.readlink(str(dest)), before_link)
                        after_lstat = os.lstat(str(dest))
                        self.assertEqual(after_lstat.st_ino, before_lstat.st_ino)
                        self.assertEqual(after_lstat.st_dev, before_lstat.st_dev)
                        self.assertEqual(after_lstat.st_mode, before_lstat.st_mode)
                        self.assertFalse(os.path.lexists(str(missing)))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-9"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            self.assertFalse(dest.exists() or dest.is_symlink())
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(dest.is_file())
            self.assertFalse(dest.is_symlink())
            self.assertEqual(dest.read_bytes(), helper_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(dest)).st_mode), 0o700)

    def test_retention_cleans_partial_copy_for_retry(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        helper_rel = "".join(["harden-device", ".sh"])
        leaf = "".join(["opos-harden-", "rollback.sh"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"31"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            stub = stubdir / "cp"
            part = b"".join([b"par", b"tial-", b"7"])
            stub.write_text(
                "#!/bin/sh\nfor a in \"$@\"; do last=\"$a\"; done\nprintf '%s' \""
                + part.decode() + "\" > \"$last\"\nexit 1\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(dest.exists() or dest.is_symlink(), "partial copy must be removed")
            stub.write_text("#!/bin/sh\nexec /bin/cp \"$@\"\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(dest.is_file())
            self.assertFalse(dest.is_symlink())
            self.assertEqual(dest.read_bytes(), helper_bytes)

    def test_retention_rejects_directory_and_temp_replacement(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        helper_rel = "".join(["harden-device", ".sh"])
        leaf = "".join(["opos-harden-", "rollback.sh"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"52"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            target_dir = tmp_path / "targetdir"
            target_dir.mkdir()
            keep = target_dir / "keep"
            keep_bytes = b"".join([b"keep-", b"bytes-3"])
            keep.write_bytes(keep_bytes)
            keep_mode = stat.S_IMODE(os.stat(str(keep)).st_mode)
            dir_mode = stat.S_IMODE(os.stat(str(target_dir)).st_mode)
            os.symlink(str(target_dir), str(dest))
            before_link = os.readlink(str(dest))
            before_lstat = os.lstat(str(dest))
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(dest.is_symlink())
            self.assertEqual(os.readlink(str(dest)), before_link)
            after_lstat = os.lstat(str(dest))
            self.assertEqual(after_lstat.st_ino, before_lstat.st_ino)
            self.assertEqual(after_lstat.st_dev, before_lstat.st_dev)
            self.assertEqual(after_lstat.st_mode, before_lstat.st_mode)
            self.assertEqual(keep.read_bytes(), keep_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(keep)).st_mode), keep_mode)
            self.assertEqual(stat.S_IMODE(os.stat(str(target_dir)).st_mode), dir_mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"53"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            dest.mkdir()
            sentinel = dest / "sentinel"
            sentinel_bytes = b"".join([b"foreign-", b"bytes-4"])
            sentinel.write_bytes(sentinel_bytes)
            os.chmod(str(dest), 0o755)
            os.chmod(str(sentinel), 0o600)
            before_mode = stat.S_IMODE(os.stat(str(dest)).st_mode)
            before_sentinel_mode = stat.S_IMODE(os.stat(str(sentinel)).st_mode)
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(dest.is_dir())
            self.assertFalse(dest.is_symlink())
            self.assertEqual(sentinel.read_bytes(), sentinel_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(dest)).st_mode), before_mode)
            self.assertEqual(stat.S_IMODE(os.stat(str(sentinel)).st_mode), before_sentinel_mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"54"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            self.assertFalse(dest.exists() or dest.is_symlink())
            foreign = tmp_path / "".join(["for", "eign"])
            foreign.write_bytes(b"".join([b"for", b"eign-bytes-", b"9"]))
            os.chmod(str(foreign), 0o640)
            foreign_bytes = foreign.read_bytes()
            foreign_mode = stat.S_IMODE(os.stat(str(foreign)).st_mode)
            marker = tmp_path / "".join(["setup-", "ok"])
            self.assertFalse(marker.exists())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            stub = stubdir / "cp"
            stub.write_text(
                "#!/bin/sh\n"
                + 'for a in "$@"; do last="$a"; done\n'
                + 'rm -f "$last" || exit 1\n'
                + 'ln -s "$OPOS_FOREIGN" "$last" || exit 1\n'
                + ': > "$OPOS_MARKER"\n'
                + "exit 1\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            env["OPOS_FOREIGN"] = str(foreign)
            env["OPOS_MARKER"] = str(marker)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertTrue(marker.is_file())
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(dest.exists() or dest.is_symlink())
            leftovers = [p for p in home.iterdir() if p.name != leaf]
            self.assertEqual(leftovers, [])
            self.assertFalse(foreign.is_symlink())
            self.assertTrue(stat.S_ISREG(os.lstat(str(foreign)).st_mode))
            self.assertEqual(foreign.read_bytes(), foreign_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(foreign)).st_mode), foreign_mode)

    def test_retention_directory_contents_unchanged(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        helper_rel = "".join(["harden-device", ".sh"])
        leaf = "".join(["opos-harden-", "rollback.sh"])

        def _snapshot_tree(root):
            found = {}
            for base, dirs, files in os.walk(str(root), followlinks=False):
                for name in dirs + files:
                    full = pathlib.Path(base) / name
                    rel = str(full.relative_to(root))
                    st = os.lstat(str(full))
                    if stat.S_ISLNK(st.st_mode):
                        found[rel] = ("link", os.readlink(str(full)), stat.S_IMODE(st.st_mode))
                    elif stat.S_ISDIR(st.st_mode):
                        found[rel] = ("dir", stat.S_IMODE(st.st_mode))
                    elif stat.S_ISREG(st.st_mode):
                        found[rel] = ("file", full.read_bytes(), stat.S_IMODE(st.st_mode))
                    else:
                        found[rel] = ("other", stat.S_IMODE(st.st_mode))
            return found

        for kind in ("symlink_dir", "real_dir"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    clone = tmp_path / "clone"
                    deploy = clone / "deploy"
                    deploy.mkdir(parents=True)
                    helper_src = deploy / helper_rel
                    helper_src.write_bytes(b"".join([b"helper-", b"bytes-", b"71"]))
                    home = tmp_path / "home"
                    home.mkdir()
                    dest = home / leaf
                    foreign = None
                    if kind == "symlink_dir":
                        foreign = tmp_path / "targetdir"
                        foreign.mkdir()
                        (foreign / "keep").write_bytes(b"".join([b"keep-", b"bytes-5"]))
                        os.symlink(str(foreign), str(dest))
                    else:
                        dest.mkdir()
                        (dest / "sentinel").write_bytes(b"".join([b"foreign-", b"bytes-6"]))
                        foreign = dest
                    before_home = _snapshot_tree(home)
                    before_foreign = _snapshot_tree(foreign)
                    runner = tmp_path / "run.sh"
                    runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(home)
                    env["DRY"] = "0"
                    proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertEqual(_snapshot_tree(home), before_home, "home tree must match including added entries")
                    self.assertEqual(_snapshot_tree(foreign), before_foreign, "foreign directory must match including added entries")

    def test_retention_rejects_concurrent_destination_replacement(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        helper_rel = "".join(["harden-device", ".sh"])
        leaf = "".join(["opos-harden-", "rollback.sh"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"91"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            victim = tmp_path / "".join(["vic", "tim"])
            victim_before = b"".join([b"vic", b"tim-bytes-", b"77"])
            victim.write_bytes(victim_before)
            self.assertFalse(dest.exists() or dest.is_symlink())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            stub = stubdir / "cp"
            stub.write_text(
                "#!/bin/sh\n"
                + "ln -sf \"" + str(victim) + "\" \"" + str(dest) + "\"\n"
                + "exec /bin/cp \"$@\"\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(victim.read_bytes(), victim_before)
            self.assertTrue(dest.is_symlink())
            self.assertEqual(os.readlink(str(dest)), str(victim))

    def test_fallback_invalid_firewall_state_preserves_recovery_evidence(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_leaf = "".join(["fire", "wall.state"])
        failed_mark = "".join(["fire", "wall=failed"])
        diag_mark = "".join(["invalid saved fire", "wall state"])
        for name, data in (
            ("empty", b""),
            ("unknown", b"not-tracked"),
            ("space", b" \n"),
        ):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    stubdir = tmp_path / "stubs"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, stubdir, root):
                        p.mkdir(parents=True)
                    state = opos_home / state_leaf
                    state.write_bytes(data)
                    log = tmp_path / "ufw.log"
                    ufw = stubdir / "ufw"
                    ufw.write_text(
                        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 0\n",
                        encoding="utf-8",
                    )
                    ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_log = tmp_path / "ctl.log"
                    ctl = stubdir / "systemctl"
                    ctl.write_text(
                        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(ctl_log) + "\"\nexit 0\n",
                        encoding="utf-8",
                    )
                    ctl.chmod(ctl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = stubdir / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    self.assertFalse((bindir / "opos").exists())
                    self.assertFalse((opos_home / "manifest").exists())
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn(failed_mark, proc.stdout, name)
                    self.assertIn(diag_mark, proc.stderr, name)
                    self.assertIn(str(state), proc.stderr, name)
                    self.assertFalse(log.exists(), "no UFW calls allowed")
                    self.assertTrue(opos_home.is_dir(), name)
                    self.assertEqual(state.read_bytes(), data, name)

    def test_fallback_firewall_respects_recorded_state(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_leaf = "".join(["fire", "wall.state"])
        ok_mark = "".join(["fire", "wall=succeeded"])
        skip_mark = "".join(["fire", "wall=skipped"])
        disable_call = "".join(["--", "force disable"])
        cases = (
            ("missing", None, skip_mark, []),
            ("active", b"active\n", ok_mark, []),
            ("on", b"on\n", ok_mark, []),
            ("inactive", b"inactive\n", ok_mark, [disable_call, "reload"]),
            ("off", b"off\n", ok_mark, [disable_call, "reload"]),
        )
        for name, data, want_mark, want_calls in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    stubdir = tmp_path / "stubs"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, stubdir, root):
                        p.mkdir(parents=True)
                    state = opos_home / state_leaf
                    if data is not None:
                        state.write_bytes(data)
                    log = tmp_path / "ufw.log"
                    ufw = stubdir / "ufw"
                    ufw.write_text(
                        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 0\n",
                        encoding="utf-8",
                    )
                    ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl = stubdir / "systemctl"
                    ctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl.chmod(ctl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = stubdir / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    self.assertFalse((bindir / "opos").exists())
                    self.assertFalse((opos_home / "manifest").exists())
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn(want_mark, proc.stdout, name)
                    self.assertFalse(state.exists(), name)
                    if log.exists():
                        calls = log.read_text(encoding="utf-8").splitlines()
                    else:
                        calls = []
                    self.assertEqual(calls, want_calls, name)

    def test_tmp_prefix_candidates_resolve_and_contain(self):
        with tempfile.TemporaryDirectory() as tmp:
            with tempfile.TemporaryDirectory() as out:
                base = pathlib.Path(tmp)
                target = pathlib.Path(out)
                inner = base / "inner"
                inner.mkdir()
                good = inner / "ok"
                good.write_text("ok\n", encoding="utf-8")
                dotdot = str(base) + "/../" + base.name + "-sibling"
                with self.assertRaises(AssertionError):
                    _contained_candidate(base, dotdot)
                link = base / "link"
                link.symlink_to(str(target))
                escaped = str(link) + "/payload"
                with self.assertRaises(AssertionError):
                    _contained_candidate(base, escaped)
                self.assertEqual(_contained_candidate(base, good), os.path.realpath(str(good)))
                back_inside = str(inner) + "/../inner/ok"
                self.assertEqual(_contained_candidate(base, back_inside), os.path.realpath(str(good)))
                proc = _run_contained(base, good, ["cat", str(good)])
                self.assertEqual(proc.returncode, 0)
                self.assertIn("ok", proc.stdout)

    def test_tmp_execution_path_rejects_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as outer:
            outer_path = pathlib.Path(outer)
            base = outer_path / "base"
            inner = base / "inner"
            inner.mkdir(parents=True)
            outside = outer_path / (base.name + "-sibling")
            outside.mkdir()
            payload = outside / "payload.sh"
            payload.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            traversal = str(inner) + "/../../" + outside.name + "/payload.sh"
            link = inner / "link"
            link.symlink_to(str(outside))
            via_link = str(link) + "/payload.sh"
            for hostile in (traversal, via_link):
                with self.subTest(hostile=hostile):
                    with unittest.mock.patch("tests.test_install_review.subprocess.run") as run_mock:
                        with self.assertRaises(AssertionError):
                            _run_contained(base, hostile, ["sh", hostile])
                        self.assertEqual(run_mock.call_count, 0)
            good = inner / "ok.sh"
            done = inner / "done"
            good.write_text("#!/bin/sh\ntouch \"" + str(done) + "\"\n", encoding="utf-8")
            proc = _run_contained(base, good, ["sh", str(good)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(done.is_file())

    def test_tmp_execution_sites_reject_hostile_paths(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestInstallReview")
        skip = ("test_tmp_execution_sites_reject_hostile_paths", "test_tmp_prefix_candidates_resolve_and_contain", "test_tmp_execution_path_rejects_traversal_and_symlink")
        sites = []
        for node in cls.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in skip:
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    if isinstance(func, ast.Attribute) and func.attr == "run" and isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                        sites.append(node.name)
                        break
        self.assertTrue(sites)
        for site in sites:
            with self.subTest(site=site):
                with tempfile.TemporaryDirectory() as outer:
                    outer_path = pathlib.Path(outer)
                    base = outer_path / "base"
                    inner = base / "inner"
                    inner.mkdir(parents=True)
                    outside = outer_path / (base.name + "-sibling")
                    outside.mkdir()
                    payload = outside / "payload.sh"
                    marker_name = "".join(["bypassed-", site])
                    bypass_marker = outside / marker_name
                    payload.write_text("#!/bin/sh\ntouch \"" + str(bypass_marker) + "\"\nexit 0\n", encoding="utf-8")
                    traversal = str(inner) + "/../../" + outside.name + "/payload.sh"
                    link = inner / "link"
                    link.symlink_to(str(outside))
                    via_link = str(link) + "/payload.sh"
                    for hostile in (traversal, via_link):
                        with self.subTest(hostile=hostile):
                            with self.assertRaises(AssertionError):
                                _contained_candidate(base, hostile)
                            with unittest.mock.patch.object(subprocess, "run") as run_mock:
                                with self.assertRaises(AssertionError):
                                    _run_contained(base, hostile, ["sh", hostile])
                                self.assertEqual(run_mock.call_count, 0)
                            self.assertFalse(bypass_marker.exists())
                            with unittest.mock.patch("tests.test_install_review._contained_candidate", side_effect=lambda t, c: str(c)):
                                proc = _run_contained(base, hostile, ["sh", hostile])
                            self.assertEqual(proc.returncode, 0, proc.stderr)
                            self.assertTrue(bypass_marker.is_file(), "removing containment must let hostile payload execute for " + site)
                            bypass_marker.unlink()
                    good = inner / "ok.sh"
                    done = inner / "done"
                    good.write_text("#!/bin/sh\ntouch \"" + str(done) + "\"\n", encoding="utf-8")
                    proc = _run_contained(base, good, ["sh", str(good)])
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertTrue(done.is_file())
                    getattr(self, site)()
                    self.assertFalse(bypass_marker.exists(), "site must not execute hostile payload for " + site)

    def test_uninstall_preserves_owner_checkout_contents(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            clone = tmp_path / "clone"
            for p in (home, opos_home, bindir, root, clone):
                p.mkdir(parents=True)
            clone_install = clone / "install.sh"
            clone_install.write_bytes(install.read_bytes())
            base_env = dict(os.environ)
            subprocess.run(["git", "-C", str(clone), "init"], check=True, capture_output=True, text=True, env=base_env)
            tracked = clone / "".join(["tra", "cked.txt"])
            tracked.write_bytes("".join(["base-", "content-", "1", "\n"]).encode())
            subprocess.run(["git", "-C", str(clone), "add", "install.sh", tracked.name], check=True, capture_output=True, text=True, env=base_env)
            subprocess.run(["git", "-C", str(clone), "-c", "user.email=" + "".join(["o", "pos@", "example.com"]), "-c", "user.name=" + "".join(["o", "pos"]), "-c", "commit.gpgsign=false", "commit", "-m", "".join(["i", "nit"])], check=True, capture_output=True, text=True, env=base_env)
            tracked.write_bytes(tracked.read_bytes() + "".join(["owner-", "edit-", "2", "\n"]).encode())
            owned = clone / "".join(["owner-ad", "ded.txt"])
            owned_bytes = "".join(["owner-", "added-", "3", "\n"]).encode()
            owned.write_bytes(owned_bytes)
            tracked_after = tracked.read_bytes()
            (opos_home / "manifest").write_text("launcher=" + str(bindir / "opos") + "\nvenv=" + str(tmp_path / "elsewhere" / "venv") + "\nrepo=" + str(clone) + "\n", encoding="utf-8")
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(clone_install), "--uninstall", "--force"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(clone.is_dir(), "checkout with owner contents must be preserved")
            self.assertTrue(owned.is_file(), "untracked owner file must survive")
            self.assertEqual(owned.read_bytes(), owned_bytes)
            self.assertTrue(tracked.is_file(), "modified tracked file must survive")
            self.assertEqual(tracked.read_bytes(), tracked_after)

    def test_uninstall_preserves_each_owner_condition_independently(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def git(clone, *args):
            subprocess.run(["git", "-C", str(clone)] + list(args), check=True, capture_output=True, text=True, env=dict(os.environ))

        def commit_all(clone, message):
            git(clone, "add", "-A")
            git(clone, "-c", "user.email=" + "".join(["o", "pos@", "example.com"]), "-c", "user.name=" + "".join(["o", "pos"]), "-c", "commit.gpgsign=false", "commit", "-m", message)

        def run_case(name):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                root = tmp_path / "root"
                clone = tmp_path / "clone"
                for p in (home, opos_home, bindir, root, clone):
                    p.mkdir(parents=True)
                clone_install = clone / "install.sh"
                clone_install.write_bytes(install.read_bytes())
                fakebin = tmp_path / "fakebin"
                fakebin.mkdir()
                for tool in ("ufw", "loginctl", "nft"):
                    stub = fakebin / tool
                    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                ctl_stub = fakebin / "systemctl"
                ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                path_value = str(fakebin) + os.pathsep + os.environ.get("PATH", "")
                owned = clone / "".join(["owner-ad", "ded.txt"])
                owned_bytes = "".join(["owner-", "added-", "3", "\n"]).encode()
                tracked = clone / "".join(["tra", "cked.txt"])
                if name in ("git_unavailable", "inspection_fails"):
                    (clone / ".git").mkdir(parents=True)
                    owned.write_bytes(owned_bytes)
                    if name == "inspection_fails":
                        stub = fakebin / "git"
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    else:
                        farm = tmp_path / "farm"
                        farm.mkdir()
                        seen = set()
                        for entry in os.environ.get("PATH", "").split(os.pathsep):
                            if not entry or not os.path.isdir(entry):
                                continue
                            for entry_name in os.listdir(entry):
                                if entry_name == "git" or entry_name in seen:
                                    continue
                                src = os.path.join(entry, entry_name)
                                if os.path.isfile(src) and os.access(src, os.X_OK):
                                    try:
                                        os.symlink(src, str(farm / entry_name))
                                    except FileExistsError:
                                        pass
                                    seen.add(entry_name)
                        path_value = str(fakebin) + os.pathsep + str(farm)
                else:
                    git(clone, "init")
                    tracked.write_bytes("".join(["base-", "content-", "1", "\n"]).encode())
                    if name == "ignored_only":
                        (clone / ".gitignore").write_text("".join(["ignored-", "owner.txt\n"]), encoding="utf-8")
                    commit_all(clone, "".join(["i", "nit"]))
                    if name == "modified_tracked_only":
                        tracked.write_bytes(tracked.read_bytes() + "".join(["owner-", "edit-", "2", "\n"]).encode())
                        owned = tracked
                        owned_bytes = tracked.read_bytes()
                    elif name == "untracked_only":
                        owned.write_bytes(owned_bytes)
                    elif name == "ignored_only":
                        owned = clone / "".join(["ignored-", "owner.txt"])
                        owned.write_bytes(owned_bytes)
                    elif name == "committed_addition":
                        owned.write_bytes(owned_bytes)
                        commit_all(clone, "".join(["add-", "owner"]))
                    else:
                        raise AssertionError(name)
                (opos_home / "manifest").write_text("launcher=" + str(bindir / "opos") + "\nvenv=" + str(tmp_path / "elsewhere" / "venv") + "\nrepo=" + str(clone) + "\n", encoding="utf-8")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["OPOS_BIN_DIR"] = str(bindir)
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = path_value
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                proc = subprocess.run(["sh", str(clone_install), "--uninstall", "--force"], capture_output=True, text=True, env=env, timeout=60)
                self.assertEqual(proc.returncode, 0, name + proc.stdout + proc.stderr)
                self.assertTrue(clone.is_dir(), name + ": checkout must be preserved")
                self.assertTrue(owned.is_file(), name + ": owner file must survive")
                self.assertEqual(owned.read_bytes(), owned_bytes, name)

        for name in ("modified_tracked_only", "untracked_only", "ignored_only", "committed_addition", "git_unavailable", "inspection_fails"):
            with self.subTest(name=name):
                run_case(name)

    def test_uninstall_preserves_untracked_owner_when_status_hides_them(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            clone = tmp_path / "clone"
            for p in (home, opos_home, bindir, root, clone):
                p.mkdir(parents=True)
            clone_install = clone / "install.sh"
            clone_install.write_bytes(install.read_bytes())
            base_env = dict(os.environ)
            subprocess.run(["git", "-C", str(clone), "init"], check=True, capture_output=True, text=True, env=base_env)
            tracked = clone / "".join(["tra", "cked.txt"])
            tracked.write_bytes("".join(["base-", "content-", "1", "\n"]).encode())
            subprocess.run(["git", "-C", str(clone), "add", "install.sh", tracked.name], check=True, capture_output=True, text=True, env=base_env)
            subprocess.run(["git", "-C", str(clone), "-c", "user.email=" + "".join(["o", "pos@", "example.com"]), "-c", "user.name=" + "".join(["o", "pos"]), "-c", "commit.gpgsign=false", "commit", "-m", "".join(["i", "nit"])], check=True, capture_output=True, text=True, env=base_env)
            subprocess.run(["git", "-C", str(clone), "config", "status.showUntrackedFiles", "no"], check=True, capture_output=True, text=True, env=base_env)
            owned = clone / "".join(["owner-ad", "ded.txt"])
            owned_bytes = "".join(["owner-", "added-", "7", "\n"]).encode()
            owned.write_bytes(owned_bytes)
            tracked_before = tracked.read_bytes()
            (opos_home / "manifest").write_text("launcher=" + str(bindir / "opos") + "\nvenv=" + str(tmp_path / "elsewhere" / "venv") + "\nrepo=" + str(clone) + "\n", encoding="utf-8")
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(clone_install), "--uninstall", "--force"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(clone.is_dir(), "checkout with untracked owner must be preserved")
            self.assertTrue(owned.is_file(), "untracked owner file must survive hidden status")
            self.assertEqual(owned.read_bytes(), owned_bytes)
            self.assertTrue(tracked.is_file())
            self.assertEqual(tracked.read_bytes(), tracked_before)

    def test_force_uninstall_targets_real_service(self):
        text = _read_install_sh()
        block = _uninstall_restore_block(text)
        begin_marker = "# BEGIN fallback service cleanup"
        service = block[block.index(begin_marker):]
        self.assertIn("opos-server.service", service)
        self.assertNotIn("ufw", service)
        self.assertNotIn("sshd -t", service)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            unit_dir = home / ".config" / "systemd" / "user"
            unit_dir.mkdir(parents=True)
            unit = unit_dir / "opos-server.service"
            keep = unit_dir / "keep.service"
            unit_bytes = b"".join([b"unit-", b"real-", b"1"])
            keep_bytes = b"".join([b"unit-", b"keep-", b"2"])
            unit.write_bytes(unit_bytes)
            keep.write_bytes(keep_bytes)
            stubdir = tmp_path / "stub"
            stubdir.mkdir()
            log = tmp_path / "calls.log"
            stub = stubdir / "systemctl"
            stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\nexit 0\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            id_stub = stubdir / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            script = "_sshd=skipped\n_firewall=skipped\n_service=skipped\n" + service
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["--user list-unit-files --no-legend opos-server.service", "--user disable --now opos-server.service", "--user daemon-reload"])
            self.assertFalse(unit.exists())
            self.assertEqual(keep.read_bytes(), keep_bytes)
            self.assertIn("sshd=skipped firewall=skipped service=succeeded", proc.stdout)


    def test_uninstall_preserves_checkout_when_git_inspection_fails(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        for name in ("rev_parse_fails", "git_missing"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    root = tmp_path / "root"
                    clone = tmp_path / "clone"
                    for p in (home, opos_home, bindir, root, clone):
                        p.mkdir(parents=True)
                    (clone / ".git").mkdir(parents=True)
                    clone_install = clone / "install.sh"
                    clone_install.write_bytes(install.read_bytes())
                    owned = clone / "".join(["owner-ad", "ded.txt"])
                    owned_bytes = "".join(["owner-", "bytes-", "9", "\n"]).encode()
                    owned.write_bytes(owned_bytes)
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(bindir / "opos") + "\nvenv=" + str(tmp_path / "elsewhere" / "venv") + "\nrepo=" + str(clone) + "\n",
                        encoding="utf-8",
                    )
                    fakebin = tmp_path / "fakebin"
                    fakebin.mkdir()
                    for tool in ("ufw", "loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_stub = fakebin / "systemctl"
                    ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    if name == "rev_parse_fails":
                        failing = fakebin / "git"
                        failing.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        failing.chmod(failing.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        path_value = str(fakebin) + os.pathsep + os.environ.get("PATH", "")
                    else:
                        farm = tmp_path / "farm"
                        farm.mkdir()
                        for entry in os.environ.get("PATH", "").split(os.pathsep):
                            if not entry or not os.path.isdir(entry):
                                continue
                            for entry_name in os.listdir(entry):
                                if entry_name == "git":
                                    continue
                                src = os.path.join(entry, entry_name)
                                dst = farm / entry_name
                                if dst.exists() or dst.is_symlink():
                                    continue
                                if os.path.isfile(src) and os.access(src, os.X_OK):
                                    try:
                                        os.symlink(src, dst)
                                    except FileExistsError:
                                        pass
                        path_value = str(fakebin) + os.pathsep + str(farm)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = path_value
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(clone_install), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn("preserving checkout with owner contents", proc.stdout + proc.stderr)
                    self.assertTrue(clone.is_dir(), "checkout must be preserved when git inspection fails")
                    self.assertTrue(owned.is_file(), "owner file must survive")
                    self.assertEqual(owned.read_bytes(), owned_bytes)


    def test_uninstall_preserves_valid_checkout_regardless_of_git_validation(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        for outcome in ("success", "failure"):
            with self.subTest(outcome=outcome):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(os.path.realpath(tmp))
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    root = tmp_path / "root"
                    clone = tmp_path / "clone"
                    for p in (home, opos_home, bindir, root, clone):
                        p.mkdir(parents=True)
                    clone_install = clone / "install.sh"
                    clone_install.write_bytes(install.read_bytes())
                    base_env = dict(os.environ)
                    subprocess.run(["git", "-C", str(clone), "init"], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                    tracked = clone / "".join(["tra", "cked.txt"])
                    tracked.write_bytes("".join(["base-", "content-", "1", "\n"]).encode())
                    subprocess.run(["git", "-C", str(clone), "add", "install.sh", tracked.name], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                    subprocess.run(["git", "-C", str(clone), "-c", "user.email=" + "".join(["o", "pos@", "example.com"]), "-c", "user.name=" + "".join(["o", "pos"]), "-c", "commit.gpgsign=false", "commit", "-m", "".join(["i", "nit"])], check=True, capture_output=True, text=True, env=base_env, timeout=30)
                    valid = subprocess.run(["git", "-C", str(clone), "rev-parse", "--show-toplevel"], capture_output=True, text=True, env=base_env, timeout=30)
                    self.assertEqual(valid.returncode, 0)
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(bindir / "opos") + "\nvenv=" + str(tmp_path / "elsewhere" / "venv") + "\nrepo=" + str(clone) + "\n",
                        encoding="utf-8",
                    )
                    install_before = clone_install.read_bytes()
                    tracked_before = tracked.read_bytes()
                    fakebin = tmp_path / "fakebin"
                    fakebin.mkdir()
                    for tool in ("ufw", "loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_stub = fakebin / "systemctl"
                    ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    real_git = None
                    for entry in os.environ.get("PATH", "").split(os.pathsep):
                        if not entry or not os.path.isdir(entry):
                            continue
                        cand = os.path.join(entry, "git")
                        if os.path.isfile(cand) and os.access(cand, os.X_OK):
                            real_git = cand
                            break
                    self.assertIsNotNone(real_git)
                    log = tmp_path / "".join(["git-", "call-", outcome, ".log"])
                    log.write_text("", encoding="utf-8")
                    marker = "".join(["git-", "validation-", "failed-", outcome])
                    git_stub = fakebin / "git"
                    if outcome == "success":
                        git_stub.write_text(
                            "#!/bin/sh\n"
                            + "printf '%s\\n' \"$*\" >> \"" + str(log) + "\"\n"
                            + "if [ \"$#\" = \"4\" ] && [ \"$1\" = \"-C\" ] && [ \"$2\" = \"" + str(clone) + "\" ] && [ \"$3\" = \"rev-parse\" ] && [ \"$4\" = \"--show-toplevel\" ]; then\n"
                            + "exec \"" + real_git + "\" \"$@\"\n"
                            + "fi\n"
                            + "exit 1\n",
                            encoding="utf-8",
                        )
                    else:
                        git_stub.write_text(
                            "#!/bin/sh\n"
                            + "printf '%s\\n' \"$*\" >> \"" + str(log) + "\"\n"
                            + "if [ \"$#\" = \"4\" ] && [ \"$1\" = \"-C\" ] && [ \"$2\" = \"" + str(clone) + "\" ] && [ \"$3\" = \"rev-parse\" ] && [ \"$4\" = \"--show-toplevel\" ]; then\n"
                            + "printf '%s\\n' \"" + marker + "\" >> \"" + str(log) + "\"\n"
                            + "exit 1\n"
                            + "fi\n"
                            + "exit 1\n",
                            encoding="utf-8",
                        )
                    git_stub.chmod(git_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(clone_install), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn("preserving checkout with owner contents", proc.stdout + proc.stderr)
                    calls = log.read_text(encoding="utf-8")
                    want_call = "-C " + str(clone) + " rev-parse --show-toplevel"
                    self.assertIn(want_call, calls)
                    if outcome == "failure":
                        self.assertIn(marker, calls)
                    else:
                        self.assertNotIn(marker, calls)
                    self.assertTrue((clone / ".git").is_dir(), "checkout git metadata must survive")
                    self.assertTrue(clone_install.is_file())
                    self.assertEqual(clone_install.read_bytes(), install_before)
                    self.assertTrue(tracked.is_file())
                    self.assertEqual(tracked.read_bytes(), tracked_before)

    def test_production_paths_inspect_for_and_case_bodies(self):
        star = chr(42)
        base = '"$BASE"'
        for_body = "for f in a b; do rm -rf " + base + "/" + star + "; done"
        case_body = "case \"$MODE\" in fast) rm -rf " + base + "/" + star + ";; esac"
        self.assertTrue(_line_path_violations(for_body), "for body must be inspected: %r" % for_body)
        self.assertTrue(_line_path_violations(case_body), "case body must be inspected: %r" % case_body)
        for_safe = "for f in " + star + ".sh; do echo ok; done"
        case_safe = "case \"$MODE\" in " + star + ".bak) echo ok;; esac"
        self.assertEqual(_line_path_violations(for_safe), [])
        self.assertEqual(_line_path_violations(case_safe), [])

    def test_tab_separated_for_case_headers(self):
        tab = chr(9)
        sp = chr(32)
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        nl = chr(10)
        base = dq + ds + "BASE" + dq
        inner = "echo " + base + "/" + star
        sub = ds + lp + inner + rp
        seps = [("space", sp), ("tab", tab)]
        for sep_name, sep in seps:
            for keyword in ("for", "case"):
                with self.subTest(sep=sep_name, keyword=keyword):
                    if keyword == "for":
                        header = "for" + sep + "f in " + sub
                        delimiter = "do"
                        body = "echo ok"
                        terminator = "done"
                    else:
                        header = "case" + sep + sub
                        delimiter = "in"
                        body = "fast) echo ok;;"
                        terminator = "esac"
                    snippet = header + nl + delimiter + nl + body + nl + terminator + nl
                    grouped = _group_for_case_headers(snippet.splitlines())
                    combined = header + nl + delimiter
                    self.assertEqual(grouped[0], combined)
                    unquoted, dynamic = _scan_installer_paths(snippet)
                    self.assertEqual(unquoted, [combined.strip()])
                    self.assertEqual(dynamic, [])
        mode = dq + ds + "MODE" + dq
        for_control = "for" + tab + "f in " + star + ".sh; do echo ok; done"
        case_control = "case" + tab + mode + " in " + star + ".bak) echo ok;; esac"
        self.assertEqual(_line_path_violations(for_control), [])
        self.assertEqual(_line_path_violations(case_control), [])

    def test_production_paths_case_quoted_delimiters_preserve_body(self):
        semi = chr(59)
        rparen = chr(41)
        star = chr(42)
        dq = chr(34)
        sq = chr(39)
        base = '"$BASE"'
        quoted_semi_dq = dq + semi + semi + dq
        quoted_paren_dq = dq + rparen + dq
        quoted_semi_sq = sq + semi + semi + sq
        quoted_paren_sq = sq + rparen + sq
        line_dq = "case " + dq + "$MODE" + dq + " in " + star + rparen + " echo " + quoted_semi_dq + semi + " rm -rf " + base + "/" + star + semi + " echo " + quoted_paren_dq + semi + semi + " esac"
        self.assertTrue(_line_path_violations(line_dq), "quoted delimiters must not erase body: %r" % line_dq)
        line_sq = "case " + dq + "$MODE" + dq + " in " + star + rparen + " echo " + quoted_semi_sq + semi + " rm -rf " + base + "/" + star + semi + " echo " + quoted_paren_sq + semi + semi + " esac"
        self.assertTrue(_line_path_violations(line_sq), "quoted delimiters must not erase body: %r" % line_sq)

    def test_production_paths_inspect_commands_after_esac(self):
        star = chr(42)
        base = '"$BASE"'
        dq = chr(34)
        semi = chr(59)
        line = "case " + dq + "$MODE" + dq + " in fast) echo ok" + semi + semi + " esac" + semi + " rm -rf " + base + "/" + star
        self.assertTrue(_line_path_violations(line), "commands after esac must be inspected: %r" % line)

    def test_dropin_removal_keeps_backup_when_restore_fails(self):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as tmp:
            proc, bak, bak_bytes, dest, dropin = _run_sshd_dropin_restore(pathlib.Path(tmp), 1)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
            self.assertIn("sshd=failed", line)
            self.assertEqual(bak.read_bytes(), bak_bytes)
            self.assertEqual(dest.read_bytes(), b"dest-" + b"bytes-61")
            self.assertFalse(dropin.exists())

    def test_dropin_removal_restores_config_before_deleting_backup(self):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as tmp:
            proc, bak, bak_bytes, dest, dropin = _run_sshd_dropin_restore(pathlib.Path(tmp), 0)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
            self.assertIn("sshd=succeeded", line)
            self.assertEqual(dest.read_bytes(), bak_bytes)
            self.assertFalse(bak.exists() or bak.is_symlink())
            self.assertFalse(dropin.exists())

    def test_sshd_verifies_recorded_content_and_preserves_admin_edits(self):
        text = _read_install_sh()
        start = text.index('    _sshd="skipped"')
        end = text.index("\n  fi\n  opos_owned_path", start)
        body = text[start:end]
        prod = "/etc/ssh/" + "sshd_config"
        prod_dropin = prod + ".d/60-opos.conf"
        baseline = "# Managed by OPOS - sshd drop-in\n" + "X\n"
        admin_line = "".join(["admin-", "edit\n"])
        cases = [
            ("verified-unmodified", True, False),
            ("modified", True, True),
            ("missing-records-unmodified", False, False),
            ("missing-records-modified", False, True),
        ]
        for name, has_record, modified in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
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
                    dropin = tmp_path / "dropin"
                    content = baseline + admin_line if modified else baseline
                    dropin.write_text(content, encoding="utf-8")
                    if has_record:
                        (opos / "sshd_dropin.expected").write_text(baseline, encoding="utf-8")
                    self.assertFalse((opos / "sshd_dropin").exists(), name)
                    if not has_record:
                        self.assertFalse((opos / "sshd_dropin.expected").exists(), name)
                    stat_path = stubs / "stat"
                    stat_path.write_text("#!/bin/sh\n" + "printf '0 600'\n", encoding="utf-8")
                    stat_path.chmod(0o755)
                    sshd_path = stubs / "sshd"
                    sshd_path.write_text("#!/bin/sh\n" + "exit 0\n", encoding="utf-8")
                    sshd_path.chmod(0o755)
                    systemctl_path = stubs / "systemctl"
                    systemctl_path.write_text("#!/bin/sh\n" + "exit 0\n", encoding="utf-8")
                    systemctl_path.chmod(0o755)
                    self.assertTrue(dropin.is_file(), "drop-in must exist before uninstall snippet runs")
                    dropin_before = dropin.read_bytes()
                    script = body.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos)
                    env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    if name == "verified-unmodified":
                        self.assertFalse(dropin.exists(), name)
                        self.assertIn("sshd=succeeded", proc.stdout, name)
                        self.assertEqual(dest.read_bytes(), bak_bytes, name)
                        self.assertFalse(bak.exists() or bak.is_symlink(), name)
                    else:
                        self.assertTrue(dropin.is_file(), name)
                        self.assertEqual(dropin.read_bytes(), dropin_before, name)
                        self.assertIn("sshd=failed", proc.stdout, name)
                        self.assertEqual(bak.read_bytes(), bak_bytes, name)

    def test_preserved_snapshot_does_not_overwrite_existing(self):
        text = _read_install_sh()
        start = text.index('    _sshd="skipped"')
        end = text.index("\n  fi\n  opos_owned_path", start)
        body = text[start:end]
        prod = "/etc/ssh/" + "sshd_config"
        prod_dropin = prod + ".d/60-opos.conf"
        baseline = "# Managed by OPOS - sshd drop-in\n" + "X\n"
        admin_line = "".join(["admin-", "edit\n"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
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
            dropin = tmp_path / "dropin"
            dropin.write_text(baseline + admin_line, encoding="utf-8")
            (opos / "sshd_dropin.expected").write_text(baseline, encoding="utf-8")
            snapshot = opos / ("sshd_dropin" + ".preserved")
            snapshot_bytes = b"snapshot-" + b"bytes-63"
            snapshot.write_bytes(snapshot_bytes)
            stat_path = stubs / "stat"
            stat_path.write_text("#!/bin/sh\n" + "printf '0 600'\n", encoding="utf-8")
            stat_path.chmod(0o755)
            sshd_path = stubs / "sshd"
            sshd_path.write_text("#!/bin/sh\n" + "exit 0\n", encoding="utf-8")
            sshd_path.chmod(0o755)
            systemctl_path = stubs / "systemctl"
            systemctl_path.write_text("#!/bin/sh\n" + "exit 0\n", encoding="utf-8")
            systemctl_path.chmod(0o755)
            modified_before = dropin.read_bytes()
            script = body.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos)
            env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("sshd=failed", proc.stdout)
            self.assertTrue(snapshot.is_file())
            self.assertEqual(snapshot.read_bytes(), snapshot_bytes)
            self.assertTrue(dropin.is_file())
            self.assertEqual(dropin.read_bytes(), modified_before)
            self.assertEqual(bak.read_bytes(), bak_bytes)


    def test_backup_probe_exception_is_exact(self):
        _assert_snippet_confined(_EXPECTED_BACKUP_PROBE + "\n")
        _assert_snippet_confined("  " + _EXPECTED_BACKUP_PROBE + "  \n")
        leaf = "".join(["sshd_config", ".bak"])
        home = "".join(["$OPOS_", "HOME"])
        prefix = _EXPECTED_BACKUP_PROBE
        redir = prefix + " > " + '"$OPOS_HOME/"' + leaf
        with self.assertRaisesRegex(AssertionError, 'backup write'):
            _assert_snippet_confined(redir + "\n")
        q = '"' + home + '/' + leaf + '"'
        writes = [prefix + '; cp a ' + q, prefix + ' && printf x > ' + q, prefix + ' | tee ' + q]
        for item in writes:
            with self.subTest(item=item):
                with self.assertRaisesRegex(AssertionError, 'backup write'):
                    _assert_snippet_confined(item + '\n')
        reads = [prefix + ' && cp -p ' + q + ' "$T"', prefix.replace('test -f', 'test -d')]
        for item in reads:
            with self.subTest(item=item):
                _assert_snippet_confined(item + '\n')
        star = chr(42)
        ordinary = "rm -rf " + '"$BASE"/' + star
        with self.assertRaises(AssertionError):
            _assert_snippet_confined(ordinary + "\n")


    def test_writes_backup_detects_backup_writes(self):
        leaf = 'sshd_config' + '.bak'
        bak = '"$OPOS_HOME/' + leaf + '"'
        bad = ['printf x > ' + bak, 'printf x >>' + bak, 'cp evil ' + bak, 'echo x | tee ' + bak, 'true; ln -s /x ' + bak]
        good = ['cp -p ' + bak + ' "$_sshd_tmp"', 'sshd -t -f ' + bak, 'rm -f ' + bak, 'cmp ' + bak + ' "$_sshd_tmp" >/dev/null', 'if [ -f ' + bak + ' ]; then', 'echo ok # > ' + bak, _EXPECTED_BACKUP_PROBE]
        for line in bad:
            with self.subTest(line=line):
                self.assertTrue(_writes_backup(line))
        for line in good:
            with self.subTest(line=line):
                self.assertFalse(_writes_backup(line))

    def test_writes_backup_detects_inline_control_bodies(self):
        leaf = 'sshd_config' + '.bak'
        bak = '"$OPOS_HOME/' + leaf + '"'
        bad = ['if true; then cp evil BAK; fi', 'if false; then :; else cp evil BAK; fi', 'for item in x; do cp evil BAK; done', 'while false; do cp evil BAK; done']
        for line in bad:
            with self.subTest(line=line):
                snippet = line.replace('BAK', bak)
                self.assertTrue(_writes_backup(snippet))
                with self.assertRaisesRegex(AssertionError, 'backup write'):
                    _assert_snippet_confined(snippet)
        good = ['if [ -f BAK ]; then :; fi', 'if true; then cp -p BAK "$_sshd_tmp"; fi']
        for line in good:
            with self.subTest(line=line):
                self.assertFalse(_writes_backup(line.replace('BAK', bak)))

    def test_production_paths_inspect_header_substitutions(self):
        star = chr(42)
        base = '"$BASE"'
        dollar = chr(36)
        lparen = chr(40)
        rparen = chr(41)
        btick = chr(96)
        inner = "rm -rf " + base + "/" + star
        dollar_sub = dollar + lparen + inner + rparen
        tick_sub = btick + inner + btick
        unsafe = [
            "for f in " + dollar_sub + "; do echo ok; done",
            "case " + dollar_sub + " in fast) echo ok;; esac",
            "for f in " + tick_sub + "; do echo ok; done",
            "case " + tick_sub + " in fast) echo ok;; esac",
        ]
        for line in unsafe:
            with self.subTest(line=line):
                self.assertTrue(_line_path_violations(line), "header substitution must be inspected: %r" % line)
        safe_inner = dollar + lparen + "echo ok" + rparen
        safe = [
            "for f in " + star + ".sh; do echo ok; done",
            "case \"$MODE\" in " + star + ".bak) echo ok;; esac",
            "for f in " + safe_inner + "; do echo ok; done",
            "case " + safe_inner + " in fast) echo ok;; esac",
        ]
        for line in safe:
            with self.subTest(line=line):
                self.assertEqual(_line_path_violations(line), [])

    def test_p039_case_delimiter_skips_complete_subject(self):
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        star = chr(42)
        base = dq + ds + "BASE" + dq
        sub_a = "/in/" + ds + lp + "rm -rf " + base + "/" + star + rp
        sub_b = "in" + ds + lp + "rm -rf " + base + "/" + star + rp
        for sub in (sub_a, sub_b):
            line = "case " + sub + " in x) echo ok;; esac"
            self.assertEqual(_shell_delimiter_index(line, "in"), line.rfind(" in ") + 1)
            self.assertTrue(_line_path_violations(line))
        safe_a = "in"
        safe_b = "/in/" + ds + lp + "echo ok" + rp
        for sub in (safe_a, safe_b):
            line = "case " + sub + " in x) echo ok;; esac"
            self.assertEqual(_shell_delimiter_index(line, "in"), line.rfind(" in ") + 1)
            self.assertEqual(_line_path_violations(line), [])

    def test_production_paths_inspect_multiline_header_substitutions(self):
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        nl = chr(10)
        base = dq + "$BASE" + dq
        inner = "rm -rf " + base + "/" + star
        sub = ds + lp + inner + rp
        snippets = [
            "for f in " + sub + nl + "do" + nl + "  echo ok" + nl + "done" + nl,
            "case " + sub + nl + "in" + nl + "  fast) echo ok;;" + nl + "esac" + nl,
        ]
        for snippet in snippets:
            with self.subTest(snippet=snippet.splitlines()[0]):
                with self.assertRaises(AssertionError):
                    _assert_snippet_confined(snippet)

    def test_production_paths_inspect_nested_subshell_and_arithmetic(self):
        star = chr(42)
        base = '"$BASE"'
        dollar = chr(36)
        lparen = chr(40)
        rparen = chr(41)
        nested_subshell = "for f in " + dollar + lparen + " " + lparen + "echo inner" + rparen + "; rm -rf " + base + "/" + star + " " + rparen + "; do echo ok; done"
        nested_arith = "for f in " + dollar + lparen + "echo " + dollar + lparen + lparen + "1 + 2" + rparen + rparen + "; rm -rf " + base + "/" + star + " " + rparen + "; do echo ok; done"
        for line in (nested_subshell, nested_arith):
            with self.subTest(line=line):
                self.assertTrue(_line_path_violations(line), "nested construct must not hide deletion: %r" % line)

    def test_production_paths_arithmetic_and_quote_do_not_mask(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        lt = chr(60)
        dq = chr(34)
        nl = chr(10)
        arith_cmd = "((" + " x = 1 " + lt + lt + " 2 ))" + nl + "rm -rf $TARGET/rest" + nl
        arith_exp = "$" + "((" + " 1 " + lt + lt + " 2 ))" + nl + "rm -rf $TARGET/rest" + nl
        quoted = "echo " + dq + "first" + nl + "second " + lt + lt + "HID" + nl + "third" + dq + nl + "rm -rf $TARGET/rest" + nl
        for name, snippet in (("arith_command", arith_cmd), ("arith_expand", arith_exp), ("multiline_quote", quoted)):
            with self.subTest(case=name):
                mutant = text + nl + snippet
                self.assertNotEqual(mutant, text)
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
                    with self.assertRaises(AssertionError):
                        self.test_production_paths_reject_unquoted_and_dynamic()

    def test_arithmetic_command_substitution_heredoc_cannot_hide_deletion(self):
        nl = chr(10)
        lt = chr(60)
        lines = [
            "x=$(( 1 " + lt + lt + " 2 + $(cat " + lt + lt + "HID",
            ")))",
            chr(34) + "quoted" + chr(34),
            lt + lt + "HID",
            "HID",
            ") " + lt + lt + " 1 ))",
            "rm -rf $TARGET/rest",
        ]
        fixture = nl.join(lines) + nl
        masked = _mask_heredoc_bodies(fixture)
        self.assertNotIn(nl + ")))`".replace("`", "") + nl, masked)
        self.assertNotIn(nl + lt + lt + "HID" + nl, masked)
        self.assertIn(") " + lt + lt + " 1 ))", masked)
        self.assertIn("rm -rf $TARGET/rest", masked)
        self.assertEqual(_scan_installer_paths(fixture), (["rm -rf $TARGET/rest"], []))

    def test_completed_arithmetic_heredoc_substitution_preserves_shift_and_deletion(self):
        nl = chr(10)
        lt = chr(60)
        lines = [
            "x=$(( 1 " + lt + lt + " 2 + $(cat " + lt + lt + "HID",
            "2",
            "HID",
            ") " + lt + lt + " 1 ))",
            "rm -rf $TARGET/rest",
        ]
        fixture = nl.join(lines) + nl
        proc = subprocess.run(["/bin/bash", "-n"], input=fixture, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        masked = _mask_heredoc_bodies(fixture)
        self.assertNotIn(nl + "2" + nl, masked)
        self.assertIn(") " + lt + lt + " 1 ))", masked)
        self.assertIn("rm -rf $TARGET/rest", masked)
        self.assertEqual(_scan_installer_paths(fixture), (["rm -rf $TARGET/rest"], []))

    def test_nested_command_substitution_shift_keeps_deletion_visible(self):
        nl = chr(10)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        lt = chr(60)
        tgt = ds + "TARGET/rest"
        first = "v=" + ds + lp + lp + " " + ds + lp + "echo " + ds + lp + "echo hi" + rp + rp + " " + lt + lt + " 2 " + rp + rp
        fixture = first + nl + "echo middle" + nl + "rm -rf " + tgt + nl
        self.assertEqual(_mask_heredoc_bodies(fixture), fixture)
        self.assertEqual(_scan_installer_paths(fixture), (["rm -rf " + tgt], []))

    def test_production_paths_misleading_delimiter_inside_substitution(self):
        star = chr(42)
        base = '"$BASE"'
        dollar = chr(36)
        lparen = chr(40)
        rparen = chr(41)
        btick = chr(96)
        dq = chr(34)
        for_line = "for f in " + dollar + lparen + "rm -rf " + base + "/" + star + "; echo do" + rparen + "; do echo ok; done"
        self.assertTrue(_line_path_violations(for_line), "misleading do inside substitution must not discard deletion: %r" % for_line)
        case_line = "case " + dollar + lparen + "rm -rf " + base + "/" + star + "; echo in" + rparen + " in fast) echo ok;; esac"
        self.assertTrue(_line_path_violations(case_line), "misleading in inside substitution must not discard deletion: %r" % case_line)
        tick_line = "for f in " + btick + "rm -rf " + base + "/" + star + "; echo do" + btick + "; do echo ok; done"
        self.assertTrue(_line_path_violations(tick_line), "misleading do inside backticks must not discard deletion: %r" % tick_line)
        quoted_line = "for f in " + dollar + lparen + "rm -rf " + base + "/" + star + "; echo " + dq + "do" + dq + rparen + "; do echo ok; done"
        self.assertTrue(_line_path_violations(quoted_line), "quoted misleading do must not discard deletion: %r" % quoted_line)

    def test_production_paths_reject_case_in_command_substitution(self):
        star = chr(42)
        base = '"$BASE"'
        dollar = chr(36)
        lparen = chr(40)
        rparen = chr(41)
        case_kw = "".join(["ca", "se"])
        destructive = "for f in " + dollar + lparen + case_kw + " x in x" + rparen + " rm -rf " + base + "/" + star + "; echo do;; esac" + rparen + "; do echo ok; done"
        minimal = "for f in " + dollar + lparen + case_kw + " x in x" + rparen + " echo do;; esac" + rparen + "; do echo ok; done"
        snippets = (("destructive", destructive), ("minimal", minimal))
        for label, snippet in snippets:
            with self.subTest(snippet=label):
                with self.assertRaisesRegex(AssertionError, "unsupported case in command substitution"):
                    _assert_snippet_confined(snippet)


    def test_retained_rollback_restores_state_after_checkout_removal(self):
        text = _read_install_sh()
        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"
        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        for _name in ("py_ok", "find_python"):
            if _name + "() {" in text:
                helpers += _extract(_name)
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        real_src = repo_root / "deploy" / "".join(["harden-device", ".sh"])
        self.assertTrue(real_src.is_file())
        real_bytes = real_src.read_bytes()
        self.assertIn(b"--rollback", real_bytes)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            (deploy / "".join(["harden-device", ".sh"])).write_bytes(real_bytes)
            home = tmp_path / "opos-home"
            home.mkdir()
            leaf = "".join(["opos-harden-", "rollback.sh"])
            retained = home / leaf
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(retained.is_file())
            self.assertFalse(retained.is_symlink())
            self.assertEqual(retained.read_bytes(), real_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(retained)).st_mode), 0o700)
            self.assertTrue(os.access(str(retained), os.X_OK))
            rm_proc = subprocess.run(["rm", "-rf", str(clone)], capture_output=True, text=True, timeout=30)
            self.assertEqual(rm_proc.returncode, 0)
            self.assertFalse(os.path.lexists(str(clone)))
            self.assertTrue(retained.is_file())
            self.assertEqual(retained.read_bytes(), real_bytes)
            isolated = tmp_path / "isolated"
            stamp = "20240101T000000Z"
            backup_dir = isolated / "var" / "backups" / "opos-harden" / stamp
            restore_rel = "/etc/opos/test-restore.conf"
            created_rel = "/etc/opos/created-prune.conf"
            live_path = isolated / "etc" / "opos" / "".join(["test-", "restore.conf"])
            created_path = isolated / "etc" / "opos" / "".join(["created-", "prune.conf"])
            live_bytes = b"".join([b"live-", b"bytes-", b"41"])
            backup_bytes = b"".join([b"backup-", b"bytes-", b"42"])
            created_bytes = b"".join([b"created-", b"bytes-", b"43"])
            live_path.parent.mkdir(parents=True, exist_ok=True)
            live_path.write_bytes(live_bytes)
            created_path.write_bytes(created_bytes)
            (backup_dir / "etc" / "opos").mkdir(parents=True, exist_ok=True)
            (backup_dir / "etc" / "opos" / "".join(["test-", "restore.conf"])).write_bytes(backup_bytes)
            (backup_dir / "MANIFEST").write_text("restore " + restore_rel + "\ncreated " + created_rel + "\n", encoding="utf-8")
            rb_env = dict(os.environ)
            rb_env["OPOS_ROOT"] = str(isolated)
            rb_proc = subprocess.run(["bash", str(retained), "--rollback"], capture_output=True, text=True, env=rb_env, timeout=30)
            self.assertEqual(rb_proc.returncode, 0, rb_proc.stdout + rb_proc.stderr)
            self.assertEqual(live_path.read_bytes(), backup_bytes)
            self.assertFalse(os.path.lexists(str(created_path)))


    def test_uninstall_preserves_unverified_service_unit(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            for p in (home, opos_home, bindir, stubdir):
                p.mkdir(parents=True)
            target = tmp_path / "".join(["exter", "nal-target"])
            target_bytes = b"".join([b"foreign-", b"unit-", b"9"])
            target.write_bytes(target_bytes)
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(target), str(unit))
            before_link = os.readlink(str(unit))
            log = tmp_path / "systemctl.log"
            log.write_text("", encoding="utf-8")
            stub = stubdir / "systemctl"
            stub.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\n"
                + "case \"$*\" in *list-unit-files*) printf 'opos-server.service enabled\\n'\nexit 0;; esac\n"
                + "exit 0\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            id_stub = stubdir / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("service=failed", proc.stdout)
            self.assertTrue(unit.is_symlink(), "unverified symlink unit must be preserved")
            self.assertEqual(os.readlink(str(unit)), before_link)
            self.assertEqual(target.read_bytes(), target_bytes)
            calls = log.read_text(encoding="utf-8")
            self.assertNotIn("disable", calls)

    def test_strip_shell_comment_keeps_command_after_backtick_comment(self):
        bt = chr(96)
        h = chr(35)
        star = chr(42)
        line = "x=" + bt + "echo a " + h + "b" + bt + "; rm -rf " + "/tmp/opos-t/" + star
        self.assertIn("rm -rf", _strip_shell_comment(line))
        self.assertTrue(_line_path_violations(line))
        with self.assertRaises(AssertionError):
            _assert_snippet_confined(line)
        self.assertEqual(_strip_shell_comment("echo ok # trailing"), "echo ok ")


    def test_retain_uses_stable_tmp_handles(self):
        text = _read_install_sh()
        start = text.index("retain_hardening_rollback() {")
        end = text.index("\n}\n", start) + 3
        block = text[start:end]
        self.assertIn("O_NOFOLLOW", block)
        self.assertIn("fchmod", block)
        self.assertIn("[ ! -L \"$_retain_tmp\" ]", block)

        def _extract(name):
            s = text.index(name + "() {")
            e = text.index("\n}\n", s) + 3
            return text[s:e] + "\n"

        helpers = (
            _extract("opos_owned_path")
            + _extract("canon_file")
            + _extract("py_ok")
            + _extract("find_python")
            + _extract("retain_hardening_rollback")
        )
        leaf = "".join(["opos-harden-", "rollback.sh"])
        helper_rel = "".join(["harden-device", ".sh"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_src.write_bytes(b"".join([b"helper-", b"bytes-5"]))
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            victim = tmp_path / "".join(["vic", "tim"])
            victim.write_bytes(b"".join([b"vic", b"tim-keep-1"]))
            runner = tmp_path / "run.sh"
            script = (
                helpers
                + 'cp() {\n'
                + '  last="";\n'
                + '  for a in "$@"; do last="$a"; done\n'
                + '  case "$last" in *".opos-harden-rollback."*) rm -f "$last"; ln -s "$OPOS_VICTIM" "$last";; esac\n'
                + '  command cp "$@"\n'
                + '}\n'
                + 'retain_hardening_rollback "$1"\n'
            )
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_VICTIM"] = str(victim)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(os.path.lexists(str(dest)))

    def test_retained_rollback_instruction_quotes_path(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = ""
        for _name in ("opos_owned_path", "canon_file", "py_ok", "find_python", "retain_hardening_rollback"):
            if _name + "() {" in text:
                helpers += _extract(_name)
        leaf = "".join(["opos-harden-", "rollback.sh"])
        helper_rel = "".join(["harden-device", ".sh"])
        sp = chr(32)
        dollar = chr(36)
        semi = chr(59)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            (deploy / helper_rel).write_bytes(b"".join([b"#!/bin/sh\n", b"exit 0\n"]))
            home = tmp_path / ("o" + "pos" + sp + "home" + dollar + semi)
            home.mkdir()
            dest = home / leaf
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "--rollback" in x), "")
            self.assertNotEqual(line, "")
            quoted = "'" + str(dest) + "'"
            self.assertIn(quoted, proc.stdout)
            prefix = "==> hardening active: run sudo "
            self.assertTrue(line.startswith(prefix))
            token = line[len(prefix):-len(" --rollback")]
            self.assertEqual(token, quoted)
            check = subprocess.run(["sh", "-c", "printf '%s' " + token], capture_output=True, text=True, timeout=30)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(check.stdout, str(dest))

    def test_retained_rollback_quoting_preserves_apostrophe_herein(self):
        import shlex
        text = _read_install_sh()
        start = text.index("_retain_q=\"'\"")
        marker = "printf '==> hardening active: run sudo "
        end = text.index(marker, start)
        block = text[start:end]
        apos = chr(39)
        here = " herein"
        leaf = "".join(["opos-harden-", "rollback.sh"])
        cases = [
            "/tmp/opos" + apos + here + "/home/" + leaf,
            "/tmp/opos" + apos + here + "/second" + apos + "part/" + leaf,
        ]
        for path in cases:
            with self.subTest(path=path):
                env = dict(os.environ)
                env["_retain_dst"] = path
                proc = subprocess.run(["sh", "-c", block + "\nprintf '%s' \"$_retain_q\""], capture_output=True, text=True, env=env, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(shlex.split(proc.stdout), [path])


if __name__ == "__main__":
    unittest.main()
