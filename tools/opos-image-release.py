#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package/check a raw ext4 OPOS image without root, mounts, or phone access.

Needs e2fsprogs debugfs (read-only, no -w) and OpenSSH ssh-keygen -Y.
The owner supplies an existing Ed25519 key or agent-backed public key file.
"""
import argparse
import base64
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_identity", REPO / "src/opos/image_identity.py")
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
NAMESPACE, PRINCIPAL = "opos-update", "opos-release"
FILES = ("rootfs.img", "image.json", "RELEASE_NOTES.txt")
ALL_FILES = {*FILES, "SHA256SUMS", "SHA256SUMS.sig"}


def run(args, data=None):
    p = subprocess.run(args, input=data, capture_output=True, timeout=120)
    if p.returncode:
        raise ValueError(Path(args[0]).name + " failed: " + p.stderr.decode("utf-8", "replace").strip())
    return p.stdout


def extract(image, debugfs="debugfs"):
    # debugfs opens read-only unless -w is passed. It can exit 0 on a missing
    # inode, so successful JSON/schema parsing is also required.
    raw = run([debugfs, "-R", "cat /etc/opos/image.json", str(image)])
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("embedded image manifest is too large")
    identity.parse_manifest(raw)
    return raw


def signature_format(raw):
    """Require the updater's SSHSIG Ed25519 format, not RSA or another namespace."""
    lines = raw.strip().splitlines()
    if len(lines) < 3 or lines[0] != b"-----BEGIN SSH SIGNATURE-----" or lines[-1] != b"-----END SSH SIGNATURE-----":
        raise ValueError("not an SSH detached signature")
    try:
        data = base64.b64decode(b"".join(lines[1:-1]), validate=True)
        if data[:10] != b"SSHSIG" + struct.pack(">I", 1):
            raise ValueError("unsupported SSH signature version")
        def take(buf):
            if len(buf) < 4:
                raise ValueError("truncated SSH signature")
            n = struct.unpack(">I", buf[:4])[0]
            if n > len(buf) - 4:
                raise ValueError("truncated SSH signature field")
            return buf[4:4+n], buf[4+n:]
        key, rest = take(data[10:])
        kind, public = take(key)
        public, tail = take(public)
        if kind != b"ssh-ed25519" or len(public) != 32 or tail:
            raise ValueError("only Ed25519 release signatures are accepted")
        namespace, rest = take(rest)
        reserved, rest = take(rest)
        algorithm, rest = take(rest)
        sig, rest = take(rest)
        kind, sig = take(sig)
        sig, tail = take(sig)
        if (namespace != NAMESPACE.encode() or reserved or algorithm not in (b"sha256", b"sha512") or
                kind != b"ssh-ed25519" or len(sig) != 64 or tail or rest):
            raise ValueError("wrong SSH signature namespace or structure")
    except (struct.error, TypeError) as exc:
        raise ValueError("malformed SSH signature") from exc


def notes(m):
    return ("Old Phone OS {version}\nImage revision: {revision}\nBase: {base}\n"
            "Build time (SOURCE_DATE_EPOCH): {epoch}\n\nChanges: TODO\nKnown limitations: TODO\n"
            "Real image build verification: NOT_RUN\nSurface Duo identity and manifest check: NOT_RUN\n"
            "Surface Duo interface and fallback checks: NOT_RUN\n"
            "Calls and messages remain simulated.\n"
            "This folder is a full rootfs image release, not an OTA update bundle.\n").format(
                version=m["version"], revision=m["revision"], base=m["base"]["os_release"]["NAME"], epoch=m["source_date_epoch"])


def sums(folder):
    return "".join(identity.hash_file(folder / name) + "  " + name + "\n" for name in FILES).encode("ascii")


def verify(folder, allowed_signers, debugfs="debugfs", keygen="ssh-keygen"):
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir() or {p.name for p in folder.iterdir()} != ALL_FILES:
        raise ValueError("release folder must contain exactly the five release files")
    if any(p.is_symlink() or not p.is_file() for p in folder.iterdir()):
        raise ValueError("release files must be regular files, not links")
    signature_format((folder / "SHA256SUMS.sig").read_bytes())
    signed = (folder / "SHA256SUMS").read_bytes()
    run([keygen, "-Y", "verify", "-f", str(Path(allowed_signers).resolve()), "-I", PRINCIPAL,
         "-n", NAMESPACE, "-s", str((folder / "SHA256SUMS.sig").resolve())], signed)
    if signed != sums(folder):
        raise ValueError("release hashes differ from the signed SHA256SUMS")
    if (folder / "image.json").read_bytes() != extract(folder / "rootfs.img", debugfs):
        raise ValueError("release manifest differs from the manifest inside the image")


def build(a):
    image, key, out = Path(a.image).resolve(), Path(a.key).expanduser().resolve(), Path(a.out).absolute()
    if out.exists() or out.is_symlink():
        raise ValueError("output already exists; use a new release folder")
    if not image.is_file() or not key.is_file():
        raise ValueError("image and owner-supplied signing key must exist")
    if key.is_relative_to(REPO) or key.is_relative_to(out.resolve()):
        raise ValueError("keep the signing key outside the checkout and release folder")
    raw = extract(image, a.debugfs)
    m = identity.parse_manifest(raw)
    text = Path(a.notes).read_text(encoding="utf-8") if a.notes else notes(m)
    if len(text.encode("utf-8")) > 65536:
        raise ValueError("release notes exceed 64 KiB")
    # Stage in a new folder. Never read, copy, create, or export private key bytes:
    # ssh-keygen alone uses the owner-supplied path (or its agent-backed public key).
    with tempfile.TemporaryDirectory(prefix=".opos-image-release-", dir=out.parent) as tmp:
        folder = Path(tmp) / "release"
        folder.mkdir()
        shutil.copyfile(image, folder / "rootfs.img")
        (folder / "image.json").write_bytes(raw)
        (folder / "RELEASE_NOTES.txt").write_bytes(text.encode("utf-8"))
        (folder / "SHA256SUMS").write_bytes(sums(folder))
        run([a.ssh_keygen, "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(folder / "SHA256SUMS")])
        verify(folder, a.allowed_signers, a.debugfs, a.ssh_keygen)
        os.rename(folder, out)
    print("PASS release built and verified: " + str(out))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for command in ("build", "verify"):
        p = sub.add_parser(command)
        p.add_argument("--allowed-signers", required=True, help="owner's public trust file: opos-release ssh-ed25519 AAAA...")
        p.add_argument("--debugfs", default="debugfs", help="e2fsprogs read-only ext4 reader")
        p.add_argument("--ssh-keygen", default="ssh-keygen")
        if command == "build":
            p.add_argument("image")
            p.add_argument("--key", required=True, help="existing owner-supplied key; never generated or copied")
            p.add_argument("--out", required=True)
            p.add_argument("--notes", help="completed plain-text notes (otherwise a NOT_RUN template)")
        else:
            p.add_argument("folder")
    a = ap.parse_args(argv)
    try:
        if a.command == "build":
            build(a)
        else:
            verify(a.folder, a.allowed_signers, a.debugfs, a.ssh_keygen)
            print("PASS release signature, image/manifest/notes hashes, and embedded manifest")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("image release: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
