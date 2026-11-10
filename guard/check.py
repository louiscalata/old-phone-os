#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local repository check for the Meta hackathon: each commit's Provenance ID must appear in docs/provenance.md on a line naming a Meta model, and src/, deploy/ and the top-level install files are scanned for listed non-Meta model names (src/ and deploy/ also for hosts outside an allow-list). It cannot certify contest eligibility or who wrote a change. Stdlib only, no network.
Usage: check.py [--staged] [--push]   Config: guard.json in the git common dir (untracked)."""
import hashlib, json, os, re, stat, subprocess, sys
import urllib.parse

KICKOFF = 1793041200  # 2026-10-26 12:00 PDT
DEADLINE = 1793844000  # 2026-11-04 21:00 EST (18:00 PST)
TEXT_EXT = {'.py','.md','.txt','.sh','.json','.toml','.cfg','.yml','.yaml','.js','.ts','.c','.h','.kt','.java','.html','.css'}
META = re.compile(r'muse|glimmer|spark|wonder|llama|sam[- ]?3', re.I)
NON_META = re.compile(r'\b(gpt-?[0-9o]|claude|gemini|mistral|mixtral|qwen|deepseek|phi-[0-9]|gemma|command-r|grok|codex|openai|anthropic|chatgpt)', re.I)
# This public snapshot has no private-history provenance exceptions.
META_HOSTS = {'dev.meta.ai', 'developer.meta.ai', 'ai.developer.meta.com', 'api.meta.ai', 'meta.ai', 'inference.baseten.co', '127.0.0.1', 'localhost'}
SEARCH_HOSTS = {'en.wikipedia.org'}  # free search hosts, not models, used for cited web grounding
def meta_only_errors(f, text):
    if not (f.startswith('src/') or f.startswith('deploy/') or f in ('install.sh', 'install.ps1', 'requirements.txt')):
        return []
    errs = []
    for i, l in enumerate(text.splitlines(), 1):
        if NON_META.search(l):
            errs.append(f'{f}:{i}: non-Meta model reference')
    if f.startswith('src/') or f.startswith('deploy/'):
        # Only these literal navigation anchors in the public landing are project links.
        # Model/network URLs, other files and other anchor targets remain checked.
        host_text = text
        if f == 'src/opos/public_static/public-demo.html':
            for link in ('https://github.com/louiscalata/old-phone-os',
                         'https://fortheloveof.studio/code/old-phone-os/'):
                host_text = host_text.replace('<a href="' + link + '">', '<a>')
        candidates = re.findall(r'https?://[^\s\'"<>]+', host_text)
        hosts = set()
        bad = set()
        for u in candidates:
            try:
                p = urllib.parse.urlparse(u)
            except Exception:
                continue
            h = p.hostname
            if not h:
                continue
            h = h.lower()
            if p.username is not None or p.password is not None:
                bad.add(h)
            else:
                hosts.add(h)
        for host in sorted(bad):
            errs.append(f'{f}: non-Meta host {host}')
        for host in sorted(hosts - META_HOSTS - SEARCH_HOSTS - bad):
            errs.append(f'{f}: non-Meta host {host}')
    return errs
GPL_OK = {'MIT','BSD-2-Clause','BSD-3-Clause','Apache-2.0','ISC','Zlib','PSF-2.0','LGPL-2.1-or-later','LGPL-3.0','GPL-3.0','GPL-3.0-or-later','MPL-2.0','Unlicense','CC0-1.0','Llama-3.1','Llama-3.2'}
# generic patterns here; site-specific names live in the untracked guard.json (private_extra)
PRIVATE = [re.compile(p) for p in (
    '/' + 'Users/[A-Za-z]',
    r'(?<![\w.-])/' + 'home/[A-Za-z][A-Za-z0-9._-]',
    r'[A-Za-z]:\\{1,2}' + r'Users\\{1,2}',
    r'(?<![\d.])10\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])(?![\d.])',
    r'(?<![\d.])172\.(?:1[6-9]|2[0-9]|3[01])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])(?![\d.])',
    r'(?<![\d.])192\.168\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])(?![\d.])',
    r'-----BEGIN [A-Z ]*PRIVATE' + ' KEY',
    r'\b(sk|pk|ghp|gho|xox[bp])[-_][A-Za-z0-9_]{16,}',
    r'AKIA[0-9A-Z]{16}',
    r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*(?P<q>[\"'])(?!var\(--[A-Za-z0-9_-]+\)(?P=q))[^\"']{8,}",
)]
def extra_patterns(cfg):
    """Compile site-specific patterns from the untracked guard.json key private_extra."""
    return [re.compile(p) for p in cfg.get('private_extra', []) if isinstance(p, str) and p]

def allowed_text(cfg):
    return [s for s in cfg.get('allow_text', []) if isinstance(s, str) and s]

def strip_allowed(text, allowed):
    for s in allowed:
        text = re.sub(re.escape(s) + r'(?![\w-])', '', text)
    return text

def git(*a):
    return subprocess.run(('git',) + a, capture_output=True, text=True).stdout

def files(staged):
    if staged:
        out = git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z')
    else:
        out = git('ls-files', '-co', '--exclude-standard', '-z')
    paths = [f for f in out.split('\x00') if f]
    if not staged:
        kept = []
        for f in paths:
            try:
                mode = os.stat(f).st_mode
            except OSError:
                print(f'GUARD FAIL: audit input unavailable: {f}', file=sys.stderr)
                raise SystemExit(1)
            if stat.S_ISREG(mode):
                kept.append(f)
        paths = kept
    return paths

INDEX_MODES = {'100644', '100755', '120000'}

def index_blobs():
    blobs = {}
    errs = []
    try:
        proc = subprocess.run(('git', 'ls-files', '--stage', '-z'), capture_output=True, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        return {}, [f'index read failed: {exc}']
    for rec in proc.stdout.split(b'\x00'):
        if not rec:
            continue
        meta, sep, name = rec.partition(b'\t')
        if not sep:
            errs.append('index read failed: malformed entry')
            continue
        try:
            path = name.decode('utf-8', 'surrogateescape')
        except Exception:
            errs.append('index read failed: bad filename')
            continue
        parts = meta.split(b' ')
        if len(parts) != 3:
            errs.append(f'{path}: index read failed: malformed entry')
            continue
        try:
            mode = parts[0].decode('ascii')
            oid = parts[1].decode('ascii')
            stage = parts[2].decode('ascii')
        except Exception:
            errs.append(f'{path}: index read failed: malformed entry')
            continue
        if stage != '0':
            errs.append(f'{path}: unmerged index entry')
            continue
        if mode not in INDEX_MODES:
            errs.append(f'{path}: unsupported index mode {mode}')
            continue
        if path in blobs:
            continue
        try:
            blob = subprocess.run(('git', 'cat-file', 'blob', oid), capture_output=True, check=True).stdout
        except (subprocess.CalledProcessError, OSError) as exc:
            errs.append(f'{path}: index blob read failed: {exc}')
            continue
        blobs[path] = blob
    return blobs, errs

def index_text(blobs, path):
    blob = blobs.get(path)
    if blob is None:
        return ''
    return blob[:2_000_000].decode('utf-8', 'ignore')

def read(path):
    try:
        with open(path, 'rb') as fh:
            return fh.read(2_000_000).decode('utf-8', 'ignore')
    except OSError:
        return ''

BOILERPLATE = ("# SPDX-License-Identifier", "import ", "from ", "if __name__", "with self.assert", "self.assert", "unittest.main")

def norm_lines(text):
    return {hashlib.md5(l.encode(), usedforsecurity=False).hexdigest() for l in (re.sub(r'\s+', ' ', x).strip() for x in text.splitlines()) if len(l) >= 25 and not any(l.startswith(p) for p in BOILERPLATE)}

def old_index(roots):
    lines, whole = set(), set()
    for root in roots:
        for d, dirs, fs in os.walk(os.path.expanduser(root)):
            dirs[:] = [x for x in dirs if x not in ('.git', 'node_modules', '.venv', '__pycache__')]
            for f in fs:
                if os.path.splitext(f)[1] in TEXT_EXT:
                    t = read(os.path.join(d, f))
                    whole.add(hashlib.md5(t.encode(), usedforsecurity=False).hexdigest())
                    lines |= norm_lines(t)
    return lines, whole

import fnmatch
import os

MAX_BYTES = 5 * 1024 * 1024
BLOB_PATTERNS = ["*.img", "*.img.*", "rootfs*", "*.deb", "*.apk", "*.zip", "*.tar*", "*.zst", "*.bin"]

def blob_errors(paths, staged=False, blobs=None):
    errs = []
    for p in paths:
        if staged:
            base = os.path.basename(p).lower()
            if any(fnmatch.fnmatch(base, pat.lower()) for pat in BLOB_PATTERNS):
                errs.append(f"{p}: device image or vendor blob")
            blob = (blobs or {}).get(p)
            if blob is None:
                errs.append(f"{p}: index blob read failed")
            elif len(blob) > MAX_BYTES:
                errs.append(f"{p}: larger than 5 MB")
            continue
        if not os.path.isfile(p):
            continue
        base = os.path.basename(p).lower()
        if any(fnmatch.fnmatch(base, pat.lower()) for pat in BLOB_PATTERNS):
            errs.append(f"{p}: device image or vendor blob")
        try:
            if os.path.getsize(p) > MAX_BYTES:
                errs.append(f"{p}: larger than 5 MB")
        except OSError:
            pass
    return errs


def main():
    git_root = git('rev-parse', '--show-toplevel').strip()
    if not git_root or os.path.realpath(git_root) != os.path.realpath(os.getcwd()):
        print('guard: NOT RUN (run from the root of an OPOS Git checkout; source archives lack Git metadata)')
        return 1
    staged = '--staged' in sys.argv
    cfg_path = os.path.join(git("rev-parse", "--git-common-dir").strip() or ".git", "guard.json")
    has_cfg = os.path.isfile(cfg_path)
    cfg = json.loads(read(cfg_path) or '{}')
    private_patterns = PRIVATE + extra_patterns(cfg)
    allowed = allowed_text(cfg)
    errs, fl = [], files(staged)
    blobs = {}
    if staged:
        blobs, index_errs = index_blobs()
        errs += index_errs
        errs += blob_errors(fl, True, blobs)
    else:
        errs += blob_errors(fl)
    if staged:
        license_text = index_text(blobs, 'LICENSE')
    else:
        license_text = read('LICENSE')
    if 'GNU GENERAL PUBLIC LICENSE' not in license_text or 'Version 3' not in license_text:
        errs.append('LICENSE: missing or not GPL-3.0')
    if staged:
        if 'NEW-WORK.md' not in blobs:
            errs.append('NEW-WORK.md: missing')
    else:
        if not os.path.isfile('NEW-WORK.md'):
            errs.append('NEW-WORK.md: missing')
    if staged:
        prov = index_text(blobs, 'docs/provenance.md')
    else:
        prov = read('docs/provenance.md')
    for rec in git('log', '--format=%H %at %ct %(trailers:key=Provenance,valueonly,separator=%x20)%x1e').split('\x1e'):
        if not rec.strip():
            continue
        h, at, ct, *pid = rec.split()
        if min(int(at), int(ct)) < KICKOFF:
            errs.append(f'commit {h[:8]}: dated before kickoff')
        if int(ct) > DEADLINE:
            errs.append(f'commit {h[:8]}: after deadline')
        ok = pid and any(re.search(r'(?<![\w-])' + re.escape(pid[0]) + r'(?![\w-])', l) and META.search(l) for l in prov.splitlines())
        if not ok:
            errs.append(f'commit {h[:8]}: no Provenance trailer logged with a Meta model in docs/provenance.md')
    roots = cfg.get('old_repos', [])
    if has_cfg and not roots:
        errs.append('.git/guard.json: old_repos not configured')
        old_lines, old_whole = set(), set()
    elif not has_cfg:
        old_lines, old_whole = set(), set()
        print("guard: no guard.json, overlap check skipped")
    else:
        old_lines, old_whole = old_index(roots)
    thr = cfg.get('overlap', 0.10)
    for f in fl:
        if staged:
            if f not in blobs:
                continue
            t = blobs[f][:2_000_000].decode('utf-8', 'ignore')
        else:
            t = read(f)
        if f.endswith(('.py', '.sh', '.mjs', '.ps1')) or f.startswith('.githooks/'):
            if not any(l.startswith('# SPDX-License-Identifier: GPL-3.0-or-later') for l in t.splitlines()[:3]):
                errs.append(f'{f}: missing SPDX header')
        for p in private_patterns:
            m = p.search(strip_allowed(t, allowed))
            if m:
                errs.append(f'{f}: private/secret match {m.group(0)[:12]!r}...')
        if os.path.splitext(f)[1] in TEXT_EXT or f in ('LICENSE',):
            if f not in ('LICENSE',) and hashlib.md5(t.encode(), usedforsecurity=False).hexdigest() in old_whole and t.strip():
                errs.append(f'{f}: identical to an old-repo file')
            nl = norm_lines(t)
            if f != 'LICENSE' and len(nl) >= 5 and len(nl & old_lines) / len(nl) > thr:
                errs.append(f'{f}: {len(nl & old_lines)}/{len(nl)} lines overlap old repos')
        errs += meta_only_errors(f, t)
    if staged:
        allowed = json.loads(index_text(blobs, 'guard/deps.json') or '{}')
    else:
        allowed = json.loads(read('guard/deps.json') or '{}')
    deps = []
    for f in fl:
        b = os.path.basename(f)
        if staged:
            if f not in blobs:
                continue
            ft = blobs[f][:2_000_000].decode('utf-8', 'ignore')
        else:
            ft = read(f)
        if b.startswith('requirements') and b.endswith('.txt'):
            deps += [re.split(r'[<>=!~\[; ]', l.strip())[0] for l in ft.splitlines() if l.strip() and not l.startswith('#')]
        elif b == 'package.json':
            j = json.loads(ft or '{}')
            deps += list(j.get('dependencies', {}))
        elif b == 'pyproject.toml':
            m = re.search(r'dependencies\s*=\s*\[(.*?)\]', ft, re.S)
            if m:
                deps += [re.split(r'[<>=!~\[; ]', d)[0] for d in re.findall(r'"([^"]+)"', m.group(1))]
    for d in deps:
        lic = allowed.get(d.lower())
        if lic not in GPL_OK:
            errs.append(f'dependency {d}: license {lic or "unlisted in guard/deps.json"} not GPL-3.0-compatible')
    for e in errs:
        print('GUARD FAIL:', e)
    print(f'guard: {"FAIL" if errs else "PASS"} ({len(fl)} files, {len(errs)} problems)')
    return 1 if errs else 0

if __name__ == '__main__':
    sys.exit(main())
