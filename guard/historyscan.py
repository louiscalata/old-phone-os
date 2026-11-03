#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan full git history for secrets without printing them."""
import argparse
import fnmatch
import importlib.util
import os
import re
import subprocess
import sys
from typing import List, Tuple


def load_private(base: str) -> list:
    """Load PRIVATE patterns from guard/check.py without copying them."""
    path = os.path.join(base, "check.py")
    spec = importlib.util.spec_from_file_location("guard_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load guard/check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    pats = getattr(mod, "PRIVATE", None)
    if not isinstance(pats, list) or not pats:
        raise RuntimeError("PRIVATE list missing")
    for p in pats:
        if not hasattr(p, "search"):
            raise RuntimeError("PRIVATE list missing")
    return pats


def extra_patterns() -> List[Tuple[str, "re.Pattern[str]"]]:
    """Build history-only secret patterns from fragments."""
    frags = [
        ("pem", "-----BEGIN [A-Z ]*PRIVA" + "TE KEY"),
        ("aws", "AK" + "IA[0-9A-Z]{16}"),
        ("bearer", "Bear" + "er [A-Za-z0-9\\-._~+/=]{20,}"),
        ("apikey", "Api" + "-Key [A-Za-z0-9\\-._~+/=]{20,}"),
        ("jwt", "ey" + "J[A-Za-z0-9_-]*\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+"),
        ("assign", "(?i)(key|secret|token|pass" + "word)\\w*\\s*[:=]\\s*[\"']?[A-Za-z0-9+/=_-]{40,}"),
    ]
    return [(k, re.compile(p)) for k, p in frags]


def safe_path(path: str) -> str:
    """Strip unsafe characters and truncate a path for display."""
    clean = re.sub(r"[^A-Za-z0-9_./+-]", "_", path.strip())
    return clean[:80] or "?"


def redact_path(path: str, pats: list) -> str:
    """Redact secret matches in a path before display."""
    out = path
    for _, pat in pats:
        out = pat.sub("[redacted]", out)
    return safe_path(out)


def run_git(repo: str, *args: str) -> str:
    """Run git with an argument list and decode output safely."""
    env = dict(os.environ)
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    proc = subprocess.run(["git", "--no-pager", *args], cwd=repo, capture_output=True, env=env, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("git command failed")
    return proc.stdout.decode("utf-8", errors="replace")


def short(m: str) -> str:
    """Truncate a match to 6 characters plus an ellipsis."""
    return m[:6] + "..."


def is_secret_path(path: str) -> bool:
    """Check a path against common secret file names."""
    base = os.path.basename(path.strip().lower())
    if not base:
        return False
    pats = [".env", "id_rsa", "id_ed25519", "*.pem", "*.p12", "*.keystore", "credentials.json", ".npmrc", ".netrc"]
    return any(fnmatch.fnmatchcase(base, p) for p in pats)


def parse_args(argv: List[str]) -> str:
    """Parse and validate the --repo argument, failing closed."""
    ap = argparse.ArgumentParser(description="Scan git history for secrets.")
    ap.add_argument("--repo", default=".", help="Path to git repository")
    ns = ap.parse_args(argv)
    repo = os.path.abspath(ns.repo)
    if not os.path.isdir(repo):
        raise SystemExit("history scan: FAIL (bad --repo)")
    if not os.path.exists(os.path.join(repo, ".git")):
        raise SystemExit("history scan: FAIL (not a git repo)")
    return repo


def main(argv: List[str] | None = None) -> int:
    """Scan added lines, commit metadata and historic paths for secrets."""
    try:
        repo = parse_args(sys.argv[1:] if argv is None else argv)
        base = os.path.dirname(os.path.abspath(__file__))
        patterns = [(f"guard{i}", p) for i, p in enumerate(load_private(base))]
        patterns += extra_patterns()
        log = run_git(repo, "-c", "color.ui=false", "log", "--all", "-m", "-p", "--text", "--no-color", "--no-ext-diff", "--no-textconv", "--format=%x01C%x01%H%x09%an%x09%ae%x02%B%x01E%x01")
    except (SystemExit, Exception) as e:
        print(f"history scan: FAIL ({e})")
        return 1
    rec = re.compile(r"\x01C\x01(.*?)\x02(.*?)\x01E\x01", re.S)
    matches = list(rec.finditer(log))
    n_commits = 0
    n_lines = 0
    findings: List[str] = []
    for i, rm in enumerate(matches):
        meta, msg = rm.group(1), rm.group(2)
        parts = meta.split("\t")
        if len(parts) != 3:
            continue
        commit, an, ae = parts[0][:8], parts[1], parts[2]
        n_commits += 1
        for field, val in (("author", an), ("email", ae)):
            for _, pat in patterns:
                m = pat.search(val)
                if m:
                    findings.append(f"{commit} meta {field}: {short(m.group(0))}")
        for mline in msg.splitlines():
            for _, pat in patterns:
                m = pat.search(mline)
                if m:
                    findings.append(f"{commit} meta message: {short(m.group(0))}")
                    break
        start = rm.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(log)
        path = "?"
        in_hunk = False
        for line in log[start:end].splitlines():
            if line.startswith("diff --git"):
                in_hunk = False
                path = "?"
                continue
            if line.startswith("@@"):
                in_hunk = True
                continue
            if not in_hunk:
                if line.startswith("+++ "):
                    path = line[4:].strip().removeprefix("b/").strip('"')
                continue
            if not line.startswith("+"):
                continue
            n_lines += 1
            body = line[1:]
            for _, pat in patterns:
                m = pat.search(body)
                if m:
                    findings.append(f"{commit} content {redact_path(path, patterns)}: {short(m.group(0))}")
                    break
    try:
        names = run_git(repo, "-c", "color.ui=false", "log", "--all", "--no-color", "--no-ext-diff", "--no-textconv", "--name-only", "--format=%H")
    except Exception as e:
        print(f"history scan: FAIL ({e})")
        return 1
    cur = "00000000"
    for line in names.splitlines():
        s = line.strip()
        if re.fullmatch(r"[0-9a-f]{40}", s):
            cur = s[:8]
            continue
        if s and is_secret_path(s):
            findings.append(f"{cur} pathname {redact_path(s, patterns)}: {short(os.path.basename(s))}")
    for f in findings:
        print(f)
    if findings:
        print(f"history scan: FAIL ({len(findings)} findings)")
        return 1
    print(f"history scan: PASS ({n_commits} commits, {n_lines} added lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
