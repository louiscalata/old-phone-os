# SPDX-License-Identifier: GPL-3.0-or-later
"""Read and check the baked image identity. Standard library only; no probes or writes.

Also installed verbatim at /usr/libexec/opos/image_identity.py, outside the
application release, for opos-version and the image acceptance checks.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import stat
import sys
from pathlib import Path, PurePosixPath

HEX = re.compile(r"[0-9a-f]{64}")
REV = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")
REQUIRED = {"etc/os-release", "usr/lib/os-release.base", "etc/issue", "etc/hostname",
            "usr/bin/opos-version", "usr/libexec/opos/image_identity.py"}


def canonical(data):
    return (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def unique_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate JSON key: " + k)
        out[k] = v
    return out


def parse_manifest(data):
    m = json.loads(data, object_pairs_hook=unique_pairs)
    if not isinstance(m, dict) or m.get("format") != 1:
        raise ValueError("unsupported image manifest")
    if not isinstance(m.get("revision"), str) or not REV.fullmatch(m["revision"]):
        raise ValueError("invalid image revision")
    if m.get("build_id") != m["revision"][:12]:
        raise ValueError("image build_id differs from revision")
    if not isinstance(m.get("version"), str) or not VERSION.fullmatch(m["version"]):
        raise ValueError("invalid image version")
    if type(m.get("source_date_epoch")) is not int or not 0 <= m["source_date_epoch"] <= 253402300799:
        raise ValueError("invalid SOURCE_DATE_EPOCH")
    base = m.get("base")
    if not isinstance(base, dict) or not isinstance(base.get("sha256"), str) or not HEX.fullmatch(base["sha256"]):
        raise ValueError("invalid base SHA-256")
    fields = base.get("os_release")
    if not isinstance(fields, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
        raise ValueError("invalid base os-release fields")
    if not fields.get("NAME") or not re.fullmatch(r"[a-z0-9._-]+", fields.get("ID", "")) or fields["ID"] == "opos":
        raise ValueError("base must identify the upstream distribution")
    if not isinstance(m.get("home_url"), str) or not m["home_url"].startswith("https://"):
        raise ValueError("invalid repository URL")
    switches = m.get("switches")
    if not isinstance(switches, dict) or not all(isinstance(k, str) and k.startswith("OPOS_") and isinstance(v, str)
                                               for k, v in switches.items()):
        raise ValueError("invalid baked switches")
    files = m.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 100000:
        raise ValueError("invalid image file list")
    seen = set()
    for f in files:
        if not isinstance(f, dict) or f.get("type") not in ("file", "symlink"):
            raise ValueError("invalid file record")
        p = f.get("path")
        if (not isinstance(p, str) or not p or PurePosixPath(p).is_absolute() or
                any(x in ("", ".", "..") for x in p.split("/")) or "\x00" in p or
                "\\" in p or p in seen or p == "etc/opos/image.json"):
            raise ValueError("unsafe or duplicate manifest path")
        seen.add(p)
        if not isinstance(f.get("sha256"), str) or not HEX.fullmatch(f["sha256"]):
            raise ValueError("invalid file SHA-256: " + p)
        if f["type"] == "symlink" and not isinstance(f.get("target"), str):
            raise ValueError("missing symlink target: " + p)
    if not REQUIRED <= seen:
        raise ValueError("manifest lacks required identity files")
    return m


def rooted(root, name, follow=True):
    """Resolve image-absolute symlinks inside ROOT, including on merged-/usr bases.

    Never follow an image's /etc/os-release link into the builder's /usr/lib.
    The last component can be left unresolved when hashing a symlink itself.
    """
    root = Path(root).resolve()
    todo = list(PurePosixPath(name).parts)
    if todo and todo[0] == "/":
        todo.pop(0)
    parts, links = [], 0
    while todo:
        part = todo.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError("path escapes image root")
            parts.pop()
            continue
        dest = root.joinpath(*parts, part)
        if dest.is_symlink() and (follow or todo):
            links += 1
            if links > 40:
                raise ValueError("symlink loop in image")
            target = PurePosixPath(os.readlink(dest))
            if target.is_absolute():
                parts = []
                todo = list(target.parts[1:]) + todo
            else:
                todo = list(target.parts) + todo
        else:
            parts.append(part)
    return root.joinpath(*parts)


def os_release(data):
    """Parse assignment data without sourcing shell code or expanding substitutions."""
    out = {}
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("invalid os-release assignment")
        words = shlex.split(value, comments=False, posix=True)
        if len(words) > 1 or key in out:
            raise ValueError("invalid or repeated os-release field: " + key)
        out[key] = words[0] if words else ""
    return out


def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def record(root, name):
    p = rooted(root, name, follow=False)
    mode = p.lstat().st_mode
    if stat.S_ISLNK(mode):
        target = os.readlink(p)
        return {"path": name, "type": "symlink", "target": target,
                "sha256": hashlib.sha256(target.encode("utf-8")).hexdigest()}
    if not stat.S_ISREG(mode):
        raise ValueError("not a regular file: " + name)
    return {"path": name, "type": "file", "sha256": hash_file(p)}


def ancestry(fields):
    return " ".join(dict.fromkeys([fields["ID"], *fields.get("ID_LIKE", "").split()]))


def verify(root="/", *, unreadable=None):
    m = parse_manifest(rooted(root, "etc/opos/image.json").read_bytes())
    current = os_release(rooted(root, "etc/os-release").read_text(encoding="utf-8"))
    want = {"NAME": "Old Phone OS", "ID": "opos", "ID_LIKE": ancestry(m["base"]["os_release"]),
            "VERSION": m["version"], "VERSION_ID": m["version"], "BUILD_ID": m["build_id"],
            "PRETTY_NAME": "Old Phone OS " + m["version"], "HOME_URL": m["home_url"]}
    for key, value in want.items():
        if current.get(key) != value:
            raise ValueError("os-release " + key + " differs from image manifest")
    base = os_release(rooted(root, "usr/lib/os-release.base").read_text(encoding="utf-8"))
    if base != m["base"]["os_release"]:
        raise ValueError("preserved base os-release differs from manifest")
    for f in m["files"]:
        try:
            actual = record(root, f["path"])
        except PermissionError:
            # Only the unprivileged selftest opts into incomplete coverage.
            # Root image verification must still read and hash every file.
            if unreadable is None or os.geteuid() == 0:
                raise
            unreadable.append(f["path"])
            continue
        if actual != f:
            raise ValueError("manifest hash or type differs: " + f["path"])
    return m


def facts(root="/", kernel=None):
    """Display only this OS's recorded facts; never substitute the desktop host's identity."""
    try:
        current = os_release(rooted(root, "etc/os-release").read_text(encoding="utf-8"))
        m = parse_manifest(rooted(root, "etc/opos/image.json").read_bytes())
        if current.get("ID") != "opos" or current.get("VERSION_ID") != m["version"] or current.get("BUILD_ID") != m["build_id"]:
            raise ValueError("OS identity and manifest disagree")
        return {"version": current["VERSION_ID"], "revision": m["revision"],
                "base": m["base"]["os_release"]["NAME"], "kernel": kernel or platform.release()}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only OPOS image identity")
    ap.add_argument("command", choices=("version", "check"))
    ap.add_argument("--root", default="/", help="mounted image or offline fixture (no mount is performed)")
    a = ap.parse_args(argv)
    try:
        if a.command == "check":
            m = verify(a.root)
            print("PASS identity: ID=opos, preserved base, %d file hashes; %s %s" %
                  (len(m["files"]), m["version"], m["build_id"]))
        else:
            f = facts(a.root)
            if f is None:
                raise ValueError("OPOS image identity unavailable or inconsistent")
            print("Old Phone OS {version}, based on {base}\nRevision: {revision}\nKernel: {kernel}".format(**f))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("FAIL identity: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
