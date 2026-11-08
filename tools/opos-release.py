#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build, sign, check and index OPOS update bundles (docs/ota-update.md). Standard library only; signing and
verifying run the OpenSSH `ssh-keygen -Y` commands, reading the source tree runs `git`.

  opos-release.py build --serial 8 --version 0.2.1 --key KEY --out dist [--ref REF | --dir DIR] [--overlay NAME ...]
  opos-release.py verify BUNDLE --allowed-signers FILE
  opos-release.py index BUNDLE            writes latest.json next to the bundle
  opos-release.py keygen-help [--pub FILE]

`verify` is its own implementation of the checks the phone's updater must make; it shares no code with it."""
import argparse
import calendar
import contextlib
import datetime
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

NAMESPACE = "opos-update"
IDENTITY = "opos-release"
FORMAT = 1
MEMBERS = ("manifest.json", "manifest.json.sig", "payload.tar.gz")
MAX_FILES = 20000
MAX_UNPACKED = 256 * 1024 * 1024
MAX_BUNDLE = 256 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
MAX_SIG = 16 * 1024
APP = "opt/opos/"
APP_MUST_HAVE = APP + "src/opos/__init__.py"
# Overlay sources in packaging/duo-os and where each lands on the phone (all inside the spec's allowlist).
OVERLAY_DEST = {
    "opos-kiosk": "usr/libexec/opos/opos-kiosk",
    "opos-session": "usr/libexec/opos/opos-session",
    "opos-diag": "usr/libexec/opos/opos-diag",
    "opos-switch": "usr/sbin/opos-switch",
    "opos.service": "etc/systemd/system/opos.service",
    "opos-server.service": "etc/systemd/system/opos-server.service",
    "opos-fallback.service": "etc/systemd/system/opos-fallback.service",
    "opos-diag.service": "etc/systemd/system/opos-diag.service",
}
SCOPES = ("app", "boot")
PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+-]{0,99}")  # the updater's component rule (it allows 128 characters, the tool stops at 100)
UNIT = re.compile(r"opos[A-Za-z0-9._+-]*\.service")
HEX40, HEX64 = re.compile(r"[0-9a-f]{40}"), re.compile(r"[0-9a-f]{64}")
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")
URLPATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*(/[A-Za-z0-9][A-Za-z0-9._+-]*)*")
HERE = Path(__file__).resolve().parent


class Refused(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def classify(path):
    """'app' or 'overlay' for a path the spec's allowlist permits in a payload; Refused (naming the rule) otherwise."""
    if not isinstance(path, str) or not path.isascii() or not 0 < len(path) <= 240:
        raise Refused("bad path %r: must be 1-240 ASCII characters" % (path,))
    parts = path.split("/")
    for p in parts:
        if not PART.fullmatch(p) or p.endswith(".new") or p == "__pycache__" or p.endswith((".pyc", ".pyo")):
            raise Refused("%s: component %r is not allowed (relative names only: letters, digits and . _ + -, no leading dot)" % (path, p))
    if path.startswith(APP):
        rest = parts[2:]
        if rest[:2] == ["src", "opos"] and len(rest) >= 3 or rest in (["LICENSE"], ["THIRD_PARTY.md"]):
            return "app"
        raise Refused("%s: not part of the app tree (only src/opos/**, LICENSE and THIRD_PARTY.md go to /opt/opos; REVISION is written by the updater)" % path)
    name, where = parts[-1], "/".join(parts[:-1])
    if where == "usr/libexec/opos":
        return "overlay"
    # opos-update* is the root updater's own: its units, its tools and its config are changed by a new image only.
    if not name.startswith("opos-update"):
        if where == "usr/sbin" and name.startswith("opos-") or where == "etc/systemd/system" and UNIT.fullmatch(name) \
                or where == "etc/default" and name.startswith("opos"):
            return "overlay"
    if where == "etc/opos" and name not in ("update", "update.json", "base-version"):
        return "overlay"
    raise Refused("%s: outside the allowlist (app tree, usr/libexec/opos/*, usr/sbin/opos-*, etc/systemd/system/opos*.service, "
                  "etc/default/opos*, etc/opos/* except the updater's own files)" % path)


def overlay_mode(dest):
    return 0o755 if dest.startswith(("usr/libexec/opos/", "usr/sbin/")) else 0o644


def parse_overlay(spec):
    name, _, dest = spec.partition("=")
    if not PART.fullmatch(name):
        raise Refused("--overlay %r: expected a file name in packaging/duo-os" % spec)
    dest = dest or OVERLAY_DEST.get(name)
    if not dest:
        raise Refused("--overlay %s: no known destination; known: %s (or give NAME=DEST inside the allowlist)" % (name, ", ".join(sorted(OVERLAY_DEST))))
    if classify(dest) != "overlay":
        raise Refused("--overlay %s: %s is in the app tree, which comes from src/" % (name, dest))
    return name, dest


# ---- strict reading of a bundle (nothing here is shared with the on-device updater) ----

def _int(v, what, lo, hi):
    if type(v) is not int or not lo <= v <= hi:
        raise Refused("manifest: %s must be an integer in %d..%d" % (what, lo, hi))
    return v


def _str(v, what, rx):
    if type(v) is not str or not rx.fullmatch(v):
        raise Refused("manifest: %s is malformed" % what)
    return v


def _keys(d, what, need, opt=()):
    if type(d) is not dict or set(need) - set(d) or set(d) - set(need) - set(opt):
        raise Refused("manifest: %s must have exactly the keys %s" % (what, ", ".join(need + tuple(opt))))


def parse_manifest(raw):
    if len(raw) > MAX_MANIFEST:
        raise Refused("manifest is larger than %d bytes" % MAX_MANIFEST)

    def pairs(items):
        d = {}
        for k, v in items:
            if k in d:
                raise Refused("manifest: duplicate key %r" % k)
            d[k] = v
        return d

    def const(c):
        raise Refused("manifest: %s is not allowed" % c)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=const)
    except (ValueError, RecursionError) as exc:
        raise Refused("manifest is not valid UTF-8 JSON: %s" % exc)


def check_manifest(m):
    """Schema and allowlist checks on a parsed manifest; returns it."""
    _keys(m, "manifest", ("format", "serial", "version", "revision", "created", "requires_base", "scope", "payload", "files"), ("notes",))
    if _int(m["format"], "format", 0, 10**6) != FORMAT:
        raise Refused("manifest: unsupported format %r" % m["format"])
    _int(m["serial"], "serial", 1, 10**9)
    _str(m["version"], "version", VERSION)
    _str(m["revision"], "revision", HEX40)
    _str(m["created"], "created", STAMP)
    try:
        datetime.datetime.strptime(m["created"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise Refused("manifest: created is not a real date")
    _int(m["requires_base"], "requires_base", 1, 10**6)
    if m["scope"] not in SCOPES:
        raise Refused("manifest: scope must be one of %s" % ", ".join(SCOPES))
    notes = m.get("notes", "")
    if type(notes) is not str or len(notes) > 1000 or any(ord(c) < 32 and c != "\n" or ord(c) == 127 for c in notes):
        raise Refused("manifest: notes must be text of at most 1000 characters")
    _keys(m["payload"], "payload", ("sha256", "size"))
    _str(m["payload"]["sha256"], "payload.sha256", HEX64)
    _int(m["payload"]["size"], "payload.size", 1, MAX_BUNDLE)
    files = m["files"]
    if type(files) is not list or not 1 <= len(files) <= MAX_FILES:
        raise Refused("manifest: files must list 1..%d entries" % MAX_FILES)
    seen, total = set(), 0
    for f in files:
        _keys(f, "a files entry", ("path", "sha256", "size", "mode"))
        classify(f["path"])
        _str(f["sha256"], "files.sha256", HEX64)
        total += _int(f["size"], "files.size", 0, MAX_UNPACKED)
        if f["mode"] not in ("0644", "0755"):
            raise Refused("%s: mode must be 0644 or 0755" % f["path"])
        if f["path"] in seen:
            raise Refused("%s: listed twice" % f["path"])
        seen.add(f["path"])
    if total > MAX_UNPACKED:
        raise Refused("manifest: unpacked size %d exceeds %d" % (total, MAX_UNPACKED))
    for p in seen:
        parts = p.split("/")
        if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
            raise Refused("%s: a file is also used as a directory" % p)
    if APP_MUST_HAVE not in seen:
        raise Refused("manifest: the app tree is missing (%s)" % APP_MUST_HAVE)
    return m


class Bundle:
    """A bundle opened and checked for tar structure only: three regular members, in order, nothing after them."""

    def __init__(self, path):
        self.path = str(path)

    def __enter__(self):
        try:
            return self._open()
        except BaseException:
            self.__exit__()
            raise

    def _open(self):
        try:
            self.size = os.path.getsize(self.path)
            if self.size > MAX_BUNDLE + MAX_MANIFEST + MAX_SIG + 16384:
                raise Refused("bundle is larger than the %d byte limit" % MAX_BUNDLE)
            self.tf = tarfile.open(self.path, "r:")
            members = []
            while True:
                m = self.tf.next()
                if m is None:
                    break
                members.append(m)
                if len(members) > len(MEMBERS):
                    raise Refused("bundle has more than %d members" % len(MEMBERS))
        except (OSError, tarfile.TarError) as exc:
            raise Refused("not a plain tar bundle: %s" % exc)
        if tuple(m.name for m in members) != MEMBERS:
            raise Refused("bundle members must be exactly %s, in that order; found %s" % (", ".join(MEMBERS), ", ".join(m.name for m in members) or "none"))
        for m, cap in zip(members, (MAX_MANIFEST, MAX_SIG, MAX_BUNDLE)):
            if m.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or m.pax_headers or m.issparse():
                raise Refused("%s: must be a plain regular file" % m.name)
            if m.size > cap:
                raise Refused("%s: larger than %d bytes" % (m.name, cap))
        end = members[-1].offset_data + members[-1].size
        end += -end % 512
        tail = 0
        with open(self.path, "rb") as fh:
            fh.seek(end)
            while chunk := fh.read(1 << 20):
                tail += len(chunk)
                if chunk.count(0) != len(chunk) or tail > 1 << 20:
                    raise Refused("data after the last tar member")
        if tail < 1024:
            raise Refused("tar end marker is missing or truncated")
        self.members = members
        self.manifest = self.tf.extractfile(members[0]).read()
        self.sig = self.tf.extractfile(members[1]).read()
        return self

    def __exit__(self, *exc):
        if getattr(self, "tf", None) is not None:
            self.tf.close()

    def payload(self):
        return self.tf.extractfile(self.members[2])


def keygen_exe():
    exe = os.environ.get("OPOS_SSH_KEYGEN") or shutil.which("ssh-keygen")
    if not exe:
        raise Refused("ssh-keygen not found: install the OpenSSH client (Windows: Settings > Optional features > OpenSSH Client), or set OPOS_SSH_KEYGEN")
    return exe


def check_signature(manifest, sig, signers, identity):
    if not sig.startswith(b"-----BEGIN SSH SIGNATURE-----") or not sig.isascii():
        raise Refused("manifest.json.sig is not an SSH signature")
    if not Path(signers).is_file():
        raise Refused("allowed_signers file not found: %s" % signers)
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td, "manifest.json.sig")
        sp.write_bytes(sig)
        p = subprocess.run([keygen_exe(), "-Y", "verify", "-f", str(signers), "-I", identity, "-n", NAMESPACE, "-s", str(sp)],
                           input=manifest, capture_output=True)
    out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    if p.returncode or not out.startswith("Good"):
        raise Refused("signature does not verify against %s for %s in namespace %s%s" % (signers, identity, NAMESPACE, ": " + out if out else ""))
    return out


def hash_stream(fh):
    h, n = hashlib.sha256(), 0
    while chunk := fh.read(1 << 20):
        h.update(chunk)
        n += len(chunk)
    return h.hexdigest(), n


def check_payload(fh, m):
    """Unpack nothing to disk: stream the gzip tar and compare every member with the manifest."""
    want = {f["path"]: f for f in m["files"]}
    seen, total = set(), 0
    try:
        with tarfile.open(fileobj=fh, mode="r|gz") as tf:
            for t in tf:
                f = want.get(t.name)
                if f is None:
                    raise Refused("payload member %r is not in the manifest" % t.name)
                if t.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or t.pax_headers or t.issparse():
                    raise Refused("%s: payload members must be plain regular files (no links, devices or directories)" % t.name)
                if t.name in seen:
                    raise Refused("%s: appears twice in the payload" % t.name)
                if t.mode != int(f["mode"], 8) or t.size != f["size"]:
                    raise Refused("%s: tar mode or size differs from the manifest" % t.name)
                total += t.size
                if total > MAX_UNPACKED:
                    raise Refused("payload unpacks to more than %d bytes" % MAX_UNPACKED)
                digest, n = hash_stream(tf.extractfile(t))
                if digest != f["sha256"] or n != f["size"]:
                    raise Refused("%s: content does not match the manifest hash" % t.name)
                seen.add(t.name)
            rest = tf.fileobj.read(1 << 20)
            if rest.count(0) != len(rest) or len(rest) == 1 << 20:
                raise Refused("payload has data after the tar end")
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise Refused("payload.tar.gz is not a valid gzip tar: %s" % exc)
    if seen != set(want):
        raise Refused("payload lacks %s" % ", ".join(sorted(set(want) - seen)[:5]))


def inspect(path):
    """Structure, manifest schema and payload hash/size (no signature, no unpacking). Returns the manifest."""
    with Bundle(path) as b:
        m = check_manifest(parse_manifest(b.manifest))
        digest, n = hash_stream(b.payload())
        if (digest, n) != (m["payload"]["sha256"], m["payload"]["size"]):
            raise Refused("payload.tar.gz does not match the manifest's sha256/size")
        return m


def verify(path, signers, identity=IDENTITY, current=None, base=None):
    """Everything the on-device updater must check, in its order. Returns (manifest, ssh-keygen's 'Good' line)."""
    with Bundle(path) as b:
        good = check_signature(b.manifest, b.sig, signers, identity)
        m = check_manifest(parse_manifest(b.manifest))
        if current is not None and m["serial"] <= current:
            raise Refused("serial %d is not greater than the current serial %d" % (m["serial"], current))
        if base is not None and m["requires_base"] > base:
            raise Refused("bundle needs base %d, the image is base %d" % (m["requires_base"], base))
        digest, n = hash_stream(b.payload())
        if (digest, n) != (m["payload"]["sha256"], m["payload"]["size"]):
            raise Refused("payload.tar.gz does not match the manifest's sha256/size")
        check_payload(b.payload(), m)
        return m, good


# ---- building ----

def git(root, *args):
    # autocrlf off: a bundle must hold the committed bytes whatever the building PC's git is configured to do to line endings.
    p = subprocess.run(["git", "-c", "safe.directory=" + Path(root).as_posix(), "-c", "core.autocrlf=false", "-C", str(root)] + list(args), capture_output=True)
    if p.returncode:
        raise Refused("git %s failed: %s" % (args[0], p.stderr.decode("utf-8", "replace").strip() or "exit %d" % p.returncode))
    return p.stdout


def read_git(repo, ref, overlays):
    if ref.startswith("-"):
        raise Refused("bad ref %r" % ref)
    top = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip())
    rev = git(top, "rev-parse", "--verify", "--quiet", ref + "^{commit}").decode().strip()
    if not HEX40.fullmatch(rev):
        raise Refused("ref %r did not resolve to a 40-hex SHA-1 commit" % ref)
    stamp = int(git(top, "log", "-1", "--format=%ct", rev).decode().strip())
    paths = ["src", "LICENSE", "THIRD_PARTY.md"] + ["packaging/duo-os/" + n for n, _ in overlays]
    src = {}
    with tarfile.open(fileobj=io.BytesIO(git(top, "archive", "--format=tar", rev, *paths)), mode="r:") as tf:
        for t in tf:
            if t.isdir():
                continue
            if not t.isreg():
                raise Refused("%s: not a regular file in %s (links and special files are never shipped)" % (t.name, ref))
            src[t.name] = (tf.extractfile(t).read(), bool(t.mode & 0o111))
    return src, rev, stamp, top


def read_dir(root, overlays, revision, created):
    def is_exec(p, data):
        # Only scripts are executable, and a filesystem without permissions (Windows, drvfs) cannot say otherwise.
        return data.startswith(b"#!") and (os.name == "nt" or bool(p.stat().st_mode & 0o111))
    src = {}

    def take(p):
        if p.is_symlink() or not p.is_file():
            raise Refused("%s: not a regular file (links and special files are never shipped)" % p)
        data = p.read_bytes()
        src[p.relative_to(root).as_posix()] = (data, is_exec(p, data))
    if not (root / "src").is_dir():
        raise Refused("%s has no src/ directory" % root)
    for dp, dns, fns in os.walk(root / "src"):
        dns[:] = sorted(d for d in dns if d != "__pycache__" and not d.endswith(".egg-info"))
        for d in dns:
            if (Path(dp) / d).is_symlink():
                raise Refused("%s: directory links are never shipped" % (Path(dp) / d))
        for fn in sorted(fns):
            if not fn.endswith((".pyc", ".pyo")):
                take(Path(dp) / fn)
    for rel in ["LICENSE", "THIRD_PARTY.md"] + ["packaging/duo-os/" + n for n, _ in overlays]:
        if not (root / rel).exists():
            raise Refused("%s: not found in %s" % (rel, root))
        take(root / rel)
    try:
        rev = revision or git(root, "rev-parse", "HEAD").decode().strip()
        if not HEX40.fullmatch(rev):
            raise Refused("revision %r is not a 40-hex commit" % rev)
        stamp = created or int(git(root, "log", "-1", "--format=%ct", rev).decode().strip())
        if not revision and git(root, "status", "--porcelain", "--", "src", "LICENSE", "THIRD_PARTY.md", "packaging/duo-os").strip():
            print("opos-release: warning: %s has changes that %s does not contain; the manifest's revision will not describe these files exactly" % (root, rev[:12]), file=sys.stderr)
    except Refused as exc:
        raise Refused("%s (give --revision and --created for a directory outside git)" % exc)
    return src, rev, stamp, root


def parse_stamp(text):
    try:
        return calendar.timegm(datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").timetuple())
    except ValueError:
        raise Refused("%r is not a UTC time like 2026-11-08T12:00:00Z" % text)


def stamp_text(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_payload(entries, mtime):
    """Deterministic payload.tar.gz: sorted entries, fixed mtime, root owner, ustar, gzip header time 0."""
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tf:
            for path in sorted(entries):
                data, mode = entries[path]
                t = tarfile.TarInfo(path)
                t.size, t.mode, t.mtime, t.uid, t.gid, t.uname, t.gname = len(data), mode, mtime, 0, 0, "", ""
                try:
                    tf.addfile(t, io.BytesIO(data))
                except ValueError as exc:
                    raise Refused("%s: %s" % (path, exc))
    return raw.getvalue()


def make_bundle(manifest, sig, payload, mtime):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in zip(MEMBERS, (manifest, sig, payload)):
            t = tarfile.TarInfo(name)
            t.size, t.mode, t.mtime, t.uid, t.gid, t.uname, t.gname = len(data), 0o644, mtime, 0, 0, "", ""
            tf.addfile(t, io.BytesIO(data))
    return raw.getvalue()


def sign(manifest, key):
    with tempfile.TemporaryDirectory() as td:
        Path(td, "manifest.json").write_bytes(manifest)
        p = subprocess.run([keygen_exe(), "-Y", "sign", "-f", str(key), "-n", NAMESPACE, "manifest.json"], cwd=td, capture_output=True)
        sigfile = Path(td, "manifest.json.sig")
        if p.returncode or not sigfile.is_file():
            raise Refused("ssh-keygen could not sign with %s: %s" % (key, p.stderr.decode("utf-8", "replace").strip() or "exit %d" % p.returncode))
        return sigfile.read_bytes()


def inside(path, root):
    try:
        p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([p, r]) == r
    except ValueError:
        return False


@contextlib.contextmanager
def signers_for(a, key):
    """allowed_signers for the self-check after signing: the given file, else one made from the key's .pub."""
    if a.allowed_signers:
        yield a.allowed_signers
        return
    pub = key if key.suffix == ".pub" else key.with_name(key.name + ".pub")
    fields = pub.read_text().split() if pub.is_file() else []
    if len(fields) < 2 or "PRIVATE" in fields[0]:
        print("opos-release: note: no public key beside the signing key; the signature was not checked after signing", file=sys.stderr)
        yield None
        return
    with tempfile.TemporaryDirectory() as td:
        f = Path(td, "allowed_signers")
        f.write_text("%s %s %s\n" % (a.identity, fields[0], fields[1]))
        yield f


def cmd_build(a):
    keygen_exe()
    key = Path(os.path.abspath(Path(a.key).expanduser()))  # ssh-keygen runs in a scratch directory, so the path must not be relative
    if not key.is_file():
        raise Refused("--key %s: no such file" % key)
    overlays = [parse_overlay(s) for s in a.overlay]
    if len({d for _, d in overlays}) != len(overlays):
        raise Refused("--overlay lists the same destination twice")
    explicit = parse_stamp(a.created) if a.created else None
    if a.dir:
        src, rev, stamp, root = read_dir(Path(a.dir).resolve(), overlays, a.revision, explicit)
    else:
        if a.revision:
            raise Refused("--revision is only for --dir; a ref supplies its own")
        src, rev, stamp, root = read_git(Path(a.repo).resolve() if a.repo else HERE.parent, a.ref, overlays)
    if explicit is not None:
        stamp = explicit
    if key.suffix != ".pub" and inside(key, root):
        raise Refused("--key %s is inside the repository being built; keep the private key outside it" % key)
    entries = {}
    for rel, (data, exe) in src.items():
        if rel.startswith("packaging/"):
            continue
        entries[APP + rel] = (data, 0o755 if exe else 0o644)
    for name, dest in overlays:
        entries[dest] = (src["packaging/duo-os/" + name][0], overlay_mode(dest))
    for p in entries:
        classify(p)
    if len(entries) > MAX_FILES or sum(len(d) for d, _ in entries.values()) > MAX_UNPACKED:
        raise Refused("more than %d files or %d unpacked bytes" % (MAX_FILES, MAX_UNPACKED))
    if APP_MUST_HAVE not in entries:
        raise Refused("the source has no %s" % APP_MUST_HAVE.replace(APP, ""))
    payload = make_payload(entries, stamp)
    manifest = {"format": FORMAT, "serial": a.serial, "version": a.version, "revision": rev, "created": stamp_text(stamp),
                "requires_base": a.requires_base, "scope": a.scope,
                "payload": {"sha256": sha256(payload), "size": len(payload)},
                "files": [{"path": p, "sha256": sha256(d), "size": len(d), "mode": "%04o" % m} for p, (d, m) in sorted(entries.items())],
                "notes": a.notes}
    check_manifest(manifest)
    mbytes = (json.dumps(manifest, indent=2) + "\n").encode()
    bundle = make_bundle(mbytes, sign(mbytes, key), payload, stamp)
    if len(bundle) > MAX_BUNDLE:
        raise Refused("bundle would be %d bytes, over the %d limit" % (len(bundle), MAX_BUNDLE))
    out = Path(a.out)
    target = out / ("opos-%d.opos-update" % a.serial)
    if not a.force:
        if target.exists():
            raise Refused("%s exists: a serial is never reused (use a new serial, or --force to overwrite)" % target)
        latest = out / "latest.json"
        if latest.is_file():
            try:
                last = json.loads(latest.read_text())["serial"]
            except (ValueError, KeyError, TypeError):
                last = None
            if type(last) is int and a.serial <= last:
                raise Refused("%s already announces serial %d; this build is serial %d (serials only go up; --force to override)" % (latest, last, a.serial))
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / (".opos-%d.opos-update.tmp" % a.serial)
    tmp.write_bytes(bundle)
    try:
        with signers_for(a, key) as sf:
            if sf:
                verify(tmp, sf, a.identity)
            else:
                inspect(tmp)
        os.replace(tmp, target)
    except Refused as exc:
        tmp.unlink(missing_ok=True)
        raise Refused("the bundle just built failed its own check: %s" % exc)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if a.keep_parts:
        d = out / ("opos-%d.parts" % a.serial)
        d.mkdir(exist_ok=True)
        for name, data in zip(MEMBERS, (mbytes, bundle_member(bundle, 1), payload)):
            (d / name).write_bytes(data)
    print("built %s" % target)
    print("  serial %d  version %s  scope %s  requires_base %d  revision %s  created %s" % (a.serial, a.version, a.scope, a.requires_base, rev[:12], manifest["created"]))
    print("  %d files (%d overlay), %d bytes unpacked; bundle %d bytes" % (len(entries), len(overlays), sum(len(d) for d, _ in entries.values()), len(bundle)))
    print("  sha256 %s" % sha256(bundle))


def bundle_member(bundle, i):
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as tf:
        return tf.extractfile(MEMBERS[i]).read()


def cmd_verify(a):
    m, good = verify(a.bundle, a.allowed_signers, a.identity, a.current_serial, a.base)
    print("OK %s" % a.bundle)
    print("  %s" % good)
    print("  serial %d  version %s  scope %s  requires_base %d  revision %s  created %s" % (m["serial"], m["version"], m["scope"], m["requires_base"], m["revision"][:12], m["created"]))
    print("  %d files, %d bytes unpacked, payload sha256 %s" % (len(m["files"]), sum(f["size"] for f in m["files"]), m["payload"]["sha256"][:16]))
    if a.list:
        for f in m["files"]:
            print("  %s %s %9d  %s" % (f["mode"], f["sha256"][:12], f["size"], f["path"]))


def cmd_index(a):
    path = Path(a.bundle)
    m = verify(path, a.allowed_signers, a.identity)[0] if a.allowed_signers else inspect(path)
    with open(path, "rb") as fh:
        digest, size = hash_stream(fh)
    url = path.name if a.url is None else a.url
    if not URLPATH.fullmatch(url):
        raise Refused("--url %r must be a plain relative path (no scheme, no leading slash, no '..', no query)" % url)
    doc = {"serial": m["serial"], "version": m["version"], "url": url, "sha256": digest, "size": size}
    out = Path(a.out) if a.out else path.with_name("latest.json")
    if out.is_file() and not a.force:
        try:
            old = json.loads(out.read_text())
        except ValueError:
            old = {}
        if not isinstance(old, dict):
            old = {}
        if type(old.get("serial")) is int and (old["serial"] > doc["serial"] or old["serial"] == doc["serial"] and old.get("sha256") != digest):
            raise Refused("%s announces serial %s; refusing to replace it with a different or older serial %d (--force to override)" % (out, old["serial"], doc["serial"]))
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, out)
    print("wrote %s" % out)
    print(json.dumps(doc))


KEYGEN_HELP = """Release key (once, on your PC). The private half stays off the repository and off the phone; back it up offline.

  ssh-keygen -t ed25519 -C opos-release -f <a folder outside the repository>/opos-release-key

That makes opos-release-key (private: never copy it into the repository, the image, the phone or a chat) and
opos-release-key.pub (public). Give the public half to the image build and to `verify`: one line per trusted key,

  %s ssh-ed25519 AAAA...

Print that line from a .pub file:  opos-release.py keygen-help --pub opos-release-key.pub
Image build:  sudo env OPOS_UPDATE_SIGNERS=<allowed_signers file> sh packaging/duo-os/build-image.sh <base.img> <out.img> HEAD
Release:      opos-release.py build --serial N --version X.Y.Z --key <private key> --out dist
Details: docs/ota-release-howto.md"""


def cmd_keygen_help(a):
    print(KEYGEN_HELP % a.identity)
    if a.pub:
        text = Path(a.pub).read_text()
        fields = text.split()
        if "PRIVATE" in text or len(fields) < 2 or not fields[0].startswith(("ssh-ed25519", "sk-ssh-ed25519", "ecdsa-", "ssh-rsa", "sk-ecdsa")):
            raise Refused("%s is not an OpenSSH public key file (never give this command a private key)" % a.pub)
        print("\nallowed_signers line for %s:\n\n%s %s %s" % (a.pub, a.identity, fields[0], fields[1]))


def parser():
    ap = argparse.ArgumentParser(prog="opos-release.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build, sign and pack opos-<serial>.opos-update")
    src = b.add_mutually_exclusive_group()
    src.add_argument("--ref", default="HEAD", help="git ref to build (default HEAD)")
    src.add_argument("--dir", help="build from a directory instead (a checkout or an unpacked archive)")
    b.add_argument("--repo", help="git repository for --ref (default: the one holding this script)")
    b.add_argument("--revision", help="with --dir: the 40-hex commit the files come from (default: git HEAD of the directory)")
    b.add_argument("--serial", type=int, required=True, help="strictly increasing; never reused")
    b.add_argument("--version", required=True)
    b.add_argument("--requires-base", type=int, default=1)
    b.add_argument("--scope", choices=SCOPES, default="app")
    b.add_argument("--overlay", action="append", default=[], metavar="NAME", help="packaging/duo-os file to ship (repeatable): " + ", ".join(sorted(OVERLAY_DEST)) + "; or NAME=DEST inside the allowlist")
    b.add_argument("--notes", default="")
    b.add_argument("--created", help="UTC time YYYY-MM-DDTHH:MM:SSZ (default: the commit's time, so rebuilds are identical)")
    b.add_argument("--key", required=True, help="private key for ssh-keygen -Y sign (or its .pub to sign through ssh-agent)")
    b.add_argument("--allowed-signers", help="check the finished bundle against this file (default: the key's .pub)")
    b.add_argument("--identity", default=IDENTITY)
    b.add_argument("--out", default=".")
    b.add_argument("--force", action="store_true", help="overwrite an existing bundle or build a serial that is not newer than latest.json")
    b.add_argument("--keep-parts", action="store_true", help="also write manifest.json, manifest.json.sig and payload.tar.gz to opos-<serial>.parts/")
    b.set_defaults(fn=cmd_build)
    v = sub.add_parser("verify", help="check a bundle as the phone's updater must")
    v.add_argument("bundle")
    v.add_argument("--allowed-signers", required=True)
    v.add_argument("--identity", default=IDENTITY)
    v.add_argument("--current-serial", type=int, help="also require serial > this")
    v.add_argument("--base", type=int, help="also require requires_base <= this")
    v.add_argument("--list", action="store_true", help="list the files")
    v.set_defaults(fn=cmd_verify)
    i = sub.add_parser("index", help="write latest.json for a bundle")
    i.add_argument("bundle")
    i.add_argument("--url", help="bundle URL relative to latest.json (default: the bundle's file name)")
    i.add_argument("--out", help="default: latest.json beside the bundle")
    i.add_argument("--allowed-signers", help="also run the full verify first")
    i.add_argument("--identity", default=IDENTITY)
    i.add_argument("--force", action="store_true", help="replace a newer or conflicting latest.json")
    i.set_defaults(fn=cmd_index)
    k = sub.add_parser("keygen-help", help="how to make the release key and the allowed_signers line")
    k.add_argument("--pub", help="a .pub file to print the allowed_signers line for")
    k.add_argument("--identity", default=IDENTITY)
    k.set_defaults(fn=cmd_keygen_help)
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    if not PART.fullmatch(getattr(a, "identity", IDENTITY)):
        sys.exit("opos-release: --identity %r is malformed" % a.identity)
    if a.cmd == "build":
        if a.serial < 1 or a.requires_base < 1:
            sys.exit("opos-release: --serial and --requires-base must be at least 1 (serial 0 is the image's own release)")
        if not VERSION.fullmatch(a.version):
            sys.exit("opos-release: --version %r is malformed" % a.version)
    try:
        a.fn(a)
    except (Refused, OSError, UnicodeError) as exc:
        print("opos-release: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
