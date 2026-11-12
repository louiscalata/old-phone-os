#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Identity phases used by build-image.sh; accepts a scratch rootfs for offline tests."""
import argparse
import importlib.util
import json
import os
import re
import shlex
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_identity", REPO / "src/opos/image_identity.py")
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


def leaves(root, name):
    p = identity.rooted(root, name, follow=False)
    if p.is_symlink() or p.is_file():
        yield name
    elif p.is_dir():
        for child in sorted(p.iterdir()):
            yield from leaves(root, name + "/" + child.name)


def watched(root):
    # All build destinations, including the exposure edits to upstream files and
    # dynamic masks for telnet units. /etc is small; unchanged base files stay out
    # of the manifest. No base file content is copied into this state inventory.
    names = ["etc", "usr/libexec/opos", "usr/libexec/opos-updater", "usr/bin/opos-version",
             "usr/lib/os-release.base", "usr/local/sbin/sfduo-composer-watchdog",
             "lib/systemd/system", "usr/lib/systemd/system",
             "var/lib/lxc/android/default.prop", "var/lib/lxc/android/rootfs/default.prop"]
    for p in identity.rooted(root, "usr/sbin").glob("opos-*"):
        names.append("usr/sbin/" + p.name)
    passwd = identity.rooted(root, "etc/passwd")
    if passwd.exists():
        for line in passwd.read_text(encoding="utf-8").splitlines():
            fields = line.split(":")
            if len(fields) == 7 and fields[0] == "droidian":
                names.append(fields[5].lstrip("/") + "/.ssh/authorized_keys")
    return {name: identity.record(root, name) for start in names for name in leaves(root, start)}


def write(root, name, data, mode=0o644):
    # Replace the last symlink itself. In particular, leave the upstream
    # /usr/lib/os-release intact when /etc/os-release used to point to it.
    p = identity.rooted(root, name, follow=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink():
        p.unlink()
    p.write_bytes(data)
    p.chmod(mode)


def prepare(a):
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not re.fullmatch(r"[0-9]+", epoch) or not 0 <= int(epoch) <= 253402300799:
        raise ValueError("SOURCE_DATE_EPOCH must be supplied as nonnegative Unix seconds")
    if a.base_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", a.base_sha256):
        raise ValueError("invalid staged base SHA-256")
    meta = tomllib.loads(Path(a.metadata).read_text(encoding="utf-8"))["project"]
    version, home = meta["version"], meta["urls"]["Source"]
    if not identity.VERSION.fullmatch(version) or not identity.REV.fullmatch(a.revision):
        raise ValueError("invalid repository version or revision")
    original = identity.rooted(a.root, "etc/os-release").read_bytes()
    fields = identity.os_release(original.decode("utf-8"))
    previous = []
    if fields.get("ID") == "opos":
        manifest = identity.rooted(a.root, "etc/opos/image.json")
        if manifest.exists():
            previous = [f["path"] for f in identity.parse_manifest(manifest.read_bytes())["files"]]
        original = identity.rooted(a.root, "usr/lib/os-release.base").read_bytes()
        fields = identity.os_release(original.decode("utf-8"))
    if not fields.get("NAME") or fields.get("ID") == "opos" or not re.fullmatch(r"[a-z0-9._-]+", fields.get("ID", "")):
        raise ValueError("base os-release needs its upstream NAME and ID")
    before = watched(a.root)
    data = {"format": 1, "version": version, "revision": a.revision, "build_id": a.revision[:12],
            "home_url": home, "source_date_epoch": int(epoch),
            "base": {"sha256": a.base_sha256 or identity.hash_file(a.base), "os_release": fields}}
    Path(a.state).write_bytes(identity.canonical({"manifest": data, "before": before, "previous": previous}))
    write(a.root, "usr/lib/os-release.base", original)
    values = {"NAME": "Old Phone OS", "ID": "opos", "ID_LIKE": identity.ancestry(fields),
              "VERSION": version, "VERSION_ID": version, "BUILD_ID": a.revision[:12],
              "HOME_URL": home, "PRETTY_NAME": "Old Phone OS " + version}
    def quote(value):
        return '"' + re.sub(r'([\\"$`])', r'\\\1', value) + '"'
    release = "".join(k + "=" + (v if k == "ID" else quote(v)) + "\n" for k, v in values.items())
    write(a.root, "etc/os-release", release.encode("utf-8"))
    write(a.root, "etc/issue", ("Old Phone OS " + version + " \\n \\l\n").encode("utf-8"))
    write(a.root, "etc/hostname", b"opos\n")
    write(a.root, "usr/libexec/opos/image_identity.py", (REPO / "src/opos/image_identity.py").read_bytes())
    write(a.root, "usr/bin/opos-version", (REPO / "packaging/duo-os/opos-version").read_bytes(), 0o755)


def baked_switches(root):
    result = {}
    unit = identity.rooted(root, "etc/systemd/system/opos-server.service")
    dropins = "etc/systemd/system/opos-server.service.d"
    directory = identity.rooted(root, dropins)
    paths = [unit, *(identity.rooted(root, dropins + "/" + p.name) for p in sorted(directory.glob("*.conf")))]
    for p in paths:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("Environment="):
                for assignment in shlex.split(line.partition("=")[2]):
                    key, sep, value = assignment.partition("=")
                    if sep and key.startswith("OPOS_"):
                        result[key] = value
    for name in ("opos-osk", "opos-kiosk"):
        cfg = identity.rooted(root, "etc/default/" + name)
        if cfg.exists():
            for line in cfg.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition("=")
                if sep and re.fullmatch(r"OPOS_[A-Z0-9_]+", key):
                    words = shlex.split(value)
                    if len(words) == 1:
                        result[key] = words[0]
    # Record what is actually baked, including a rule retained in an OPOS base.
    result["OPOS_NET_POLKIT"] = "1" if identity.rooted(root, "etc/polkit-1/rules.d/50-opos-network.rules").exists() else "0"
    usb = identity.rooted(root, "etc/opos/usb-shell.conf")
    result["OPOS_DEV_USB_SHELL"] = "1" if usb.exists() and "enabled=1" in usb.read_text(encoding="utf-8").splitlines() else "0"
    result["OPOS_DEV_SSH_PUBKEY"] = "1" if os.environ.get("OPOS_DEV_SSH_PUBKEY") else "0"
    cfg = identity.rooted(root, "etc/opos/update.json")
    result["OPOS_UPDATE_URL"] = json.loads(cfg.read_bytes())["url"] if cfg.exists() else ""
    signers = identity.rooted(root, "etc/opos/update/allowed_signers")
    result["OPOS_UPDATE_SIGNERS"] = "1" if signers.exists() and signers.stat().st_size else "0"
    return result


def finish(a):
    state = json.loads(Path(a.state).read_bytes())
    after = watched(a.root)
    names = {name for name, f in after.items() if state["before"].get(name) != f}
    names.update(name for name in state.get("previous", []) if identity.rooted(a.root, name, follow=False).exists()
                 or identity.rooted(a.root, name, follow=False).is_symlink())
    # Include every OPOS-installed file even if a previous OPOS base already had
    # identical bytes. Release files use their physical, immutable image paths.
    for start in ("opt/opos", "opt/opos-releases", "usr/libexec/opos", "usr/libexec/opos-updater", "etc/opos"):
        names.update(leaves(a.root, start))
    for name in after:
        if (name.startswith("usr/sbin/opos-") or
                any(part.startswith("opos") for part in name.split("/")[3:]) and name.startswith("etc/systemd/system/") or
                name.startswith("etc/default/opos") or
                name.startswith("etc/polkit-1/rules.d/50-opos-") or
                name == "etc/systemd/journald.conf.d/50-opos-persistent.conf"):
            names.add(name)
    names.update(identity.REQUIRED)
    names.add("usr/local/sbin/sfduo-composer-watchdog")
    names.add("usr/local/sbin/sfduo-usb-gadget.sh")
    names.discard("etc/opos/image.json")  # a manifest cannot hash itself
    m = state["manifest"]
    m["switches"] = baked_switches(a.root)
    m["files"] = [identity.record(a.root, name) for name in sorted(names)]
    identity.parse_manifest(identity.canonical(m))
    write(a.root, "etc/opos/image.json", identity.canonical(m))
    identity.verify(a.root)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for command in ("prepare", "finish"):
        p = sub.add_parser(command)
        p.add_argument("--root", required=True)
        p.add_argument("--state", required=True)
        if command == "prepare":
            p.add_argument("--base", required=True)
            # The builder hashes its private copy before fsck or mounting changes it.
            p.add_argument("--base-sha256")
            p.add_argument("--metadata", required=True)
            p.add_argument("--revision", required=True)
    a = ap.parse_args(argv)
    try:
        (prepare if a.command == "prepare" else finish)(a)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("image identity: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
