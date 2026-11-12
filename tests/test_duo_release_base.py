# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline release boundaries. Tiny fixture images use fake mounts, never a phone or socket.

The real builder runs from start to finish. Only Linux image operations, root ownership,
Git's archive source and the final verifier are stand-ins; policy uses the native sshd -T.
The builder's verifier stand-in tests invocation and cleanup on failure. Separate
tests run the real verifier's early gates and its lifecycle/final-gate blocks;
they do not execute an image or stand in for Linux image verification.
"""
import hashlib
import io
import json
import os
import re
from pathlib import Path
import runpy
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from opos import image_identity as identity
from tests import test_ckpt2_security as ck, test_duo_usb_hardening as usb, test_selftest as st

PKG = usb.PKG
SSHD = shutil.which("sshd") or ("/usr/sbin/sshd" if Path("/usr/sbin/sshd").exists() else None)

# All generated commands act only in the TemporaryDirectory. No actual mount, root,
# fsck, Git write, Linux ownership, image execution, listener or device is involved.
FIXTURE_TOOL = r'''
import hashlib, io, json, os, shutil, sys, tarfile, tempfile
from pathlib import Path
work = Path(os.environ["FIXTURE_WORK"])
name, args = Path(sys.argv[0]).name, sys.argv[1:]
with (work / "events").open("a") as stream:
    stream.write(json.dumps([name] + args) + "\n")
if name == "id":
    print(0)
elif name == "readlink":
    print(Path(args[-1]).resolve() if args[0] == "-f" else os.readlink(args[-1]))
elif name == "mktemp":
    template = args[-1] if args and args[-1] != "-d" else str(work / "tmp/scratch.XXXXXX")
    kwargs = dict(prefix=Path(template).name.replace("XXXXXX", ""), dir=Path(template).parent)
    if "-d" in args:
        path = Path(tempfile.mkdtemp(**kwargs))
        if path.name.startswith(".opos-build.") and os.environ.get("FIXTURE_PRECREATE_STAGE"):
            (path / "rootfs.img").symlink_to(work / "victim")
        print(path)
    else:
        fd, path = tempfile.mkstemp(**kwargs)
        os.close(fd)
        print(path)
elif name == "cp":
    if os.environ.get("FIXTURE_ASSERT_PRIVATE"):
        target = Path(args[-1])
        assert target.parent.parent == work and target.parent.name.startswith(".opos-build.")
        assert target.parent.stat().st_mode & 0o777 == 0o700
        assert target.is_file() and not target.is_symlink() and target.stat().st_nlink == 1
    shutil.copyfile(args[-2], args[-1])
    if os.environ.get("FIXTURE_STAGE_SWAP"):
        stage = Path(args[-1]).parent
        saved = stage.with_name("swapped-stage")
        stage.rename(saved)
        replacement = work / "replacement-stage"
        replacement.mkdir()
        shutil.copyfile(saved / "rootfs.img", replacement / "rootfs.img")
        stage.symlink_to(replacement, target_is_directory=True)
        with (work / "events").open("a") as stream:
            stream.write(json.dumps(["stage-swap", str(stage)]) + "\n")
    if os.environ.get("FIXTURE_CORRUPT_COPY"):
        Path(args[-1]).write_bytes(b"changed during copy")
    if os.environ.get("FIXTURE_CHANGE_SOURCE"):
        Path(args[-2]).write_bytes(b"source replaced after copy")
elif name == "mount":
    assert args[0] == "-o" and not (work / "mounted").exists()
    image = Path(args[-2])
    # Model a caller swapping BASE for the layout mount, then restoring it before
    # the copy. Private staged images are outside that caller-controlled path.
    if str(image) == str(work / "base.img") and os.environ.get("FIXTURE_LAYOUT_SWAP"):
        (work / "saved-base").write_bytes(image.read_bytes())
        image.write_bytes((work / "pristine.img").read_bytes())
    # These archives are made only by this test from its own temporary tree.
    # Read the actual argument: never substitute a shared seed for an image.
    with tarfile.open(image) as archive:
        archive.extractall(args[-1], filter="fully_trusted")
    (work / "mounted").write_text(json.dumps(args[1:]))
    if os.environ.get("FIXTURE_FAIL_MOUNT"):
        sys.exit(8)
elif name == "mountpoint":
    sys.exit(0 if (work / "mounted").exists() else 1)
elif name == "losetup":
    state = work / "mounted"
    if state.exists() and json.loads(state.read_text())[1] == args[-1]:
        print("/dev/loop-fixture: fixture backing file still attached")
elif name == "umount":
    state = work / "mounted"
    if not state.exists():
        sys.exit(1)
    options, image, target = json.loads(state.read_text())
    assert target == args[0]
    if os.environ.get("FIXTURE_FAIL_UMOUNT"):
        sys.exit(8)
    if "ro" not in options.split(","):
        shutil.rmtree(work / "result", ignore_errors=True)
        shutil.copytree(target, work / "result", symlinks=True, dirs_exist_ok=True)
        with tarfile.open(image, "w") as archive:
            archive.add(target, arcname=".")
    if (work / "saved-base").exists():
        Path(image).write_bytes((work / "saved-base").read_bytes())
        (work / "saved-base").unlink()
    for path in Path(target).iterdir():
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path)
    state.unlink()
elif name == "git":
    if "archive" in args:
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
            for path in ("src/opos/__init__.py", "LICENSE", "THIRD_PARTY.md"):
                data = b"fixture archive\n"
                info = tarfile.TarInfo(path)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    elif "show" in args:
        print((Path(os.environ["FIXTURE_REPO"]) / "pyproject.toml").read_text(), end="")
    elif "log" in args:
        print(1794355200)
    else:
        print("a" * (7 if "--short" in args else 40))
elif name == "sha256sum":
    for path in args:
        print(hashlib.sha256(Path(path).read_bytes()).hexdigest() + "  " + path)
elif name == "e2fsck":
    if "-fy" in args and os.environ.get("FIXTURE_FSCK_CHANGES_IMAGE"):
        with Path(args[-1]).open("ab") as stream:
            stream.write(b"simulated fsck metadata change\n")
    if "-fn" in args and os.environ.get("FIXTURE_FAIL_FINAL_FSCK"):
        sys.exit(4)
elif name == "stat":
    assert args[:2] == ["-c", "%u %a"]
    print("0 " + oct(Path(args[-1]).stat().st_mode & 0o777)[2:])
elif name == "python3":
    import signal, subprocess
    result = subprocess.run([sys.executable] + args)
    if "publish" in args and result.returncode == 0 and os.environ.get("FIXTURE_SIGNAL_PUBLICATION"):
        os.kill(os.getppid(), int(os.environ["FIXTURE_SIGNAL_PUBLICATION"]))
    sys.exit(result.returncode)
else:
    assert name in ("chown", "sync"), name
'''


class Builder(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name).resolve()
        self.pkg = self.work / "checkout/packaging/duo-os"
        self.pkg.mkdir(parents=True)
        for path in PKG.iterdir():
            if path.name in ("build-image.sh", "image-policy.py", "release-bases.sha256"):
                shutil.copyfile(path, self.pkg / path.name)
            elif path.name != "verify-image.sh":
                (self.pkg / path.name).symlink_to(path, target_is_directory=path.is_dir())
        (self.pkg / "verify-image.sh").write_text(
            'printf "verify %s %s\\n" "${OPOS_RELEASE:-0}" "$1" >> "$FIXTURE_WORK/verify-calls"\n'
            '[ ! -e "$FIXTURE_WORK/out.img" ] && [ ! -L "$FIXTURE_WORK/out.img" ] || exit 10\n'
            '[ -f "$1" ] || exit 9\n'
            'if [ "${FIXTURE_CREATE_OUT:-0}" = 1 ]; then ln -s "$FIXTURE_WORK/victim" "$FIXTURE_WORK/out.img"; fi\n'
            'exit "${FIXTURE_VERIFY_STATUS:-0}"\n')
        for name in ("tools", "src"):
            (self.pkg.parent.parent / name).symlink_to(usb.ROOT / name, target_is_directory=True)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        tool = self.work / "fixture-tool.py"
        tool.write_text("#!" + sys.executable + "\n" + FIXTURE_TOOL)
        tool.chmod(0o755)
        for name in ("id", "readlink", "mktemp", "cp", "mount", "mountpoint", "losetup", "umount", "git", "sha256sum", "e2fsck", "chown", "sync"):
            (self.bin / name).symlink_to(tool)
        (self.bin / "python3").symlink_to(sys.executable)
        (self.work / "tmp").mkdir()
        self.seed = usb.make_root(self.work / "seed")
        self.put("etc/os-release", 'NAME="Droidian"\nID=droidian\nID_LIKE=debian\n')
        for name in ("phosh-session", "chromium", "python3"):
            self.put("usr/bin/" + name, "fixture, never executed\n", 0o755)
        self.put("usr/local/sbin/sfduo-composer-watchdog",
                 "#!/bin/sh\nsystemctl try-restart phosh.service opos.service 2>/dev/null || true\n", 0o755)
        self.put("etc/passwd", f"droidian:x:{os.getuid()}:{os.getgid()}::/home/droidian:/bin/sh\n")
        self.put("etc/group", f"droidian:x:{os.getgid()}:\n")
        self.put("etc/sudoers", "%sudo ALL=(ALL:ALL) ALL\n@includedir /etc/sudoers.d\n")
        for name in ("etc/default", "etc/sudoers.d", "etc/systemd/system/graphical.target.wants",
                     "etc/systemd/system/multi-user.target.wants", "usr/sbin", "home/droidian"):
            (self.seed / name).mkdir(parents=True, exist_ok=True)
        self.base = self.work / "base.img"
        self.out = self.work / "out.img"
        self.allowlist = self.pkg / "release-bases.sha256"
        self.pin()

    def put(self, rel, text, mode=0o644):
        path = self.seed / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)

    def pin(self):
        self.pack_base()
        self.digest = hashlib.sha256(self.base.read_bytes()).hexdigest()
        self.allowlist.write_text(self.digest + "  tiny fixture, never a release base\n")

    def pack_base(self):
        with tarfile.open(self.base, "w") as archive:
            archive.add(self.seed, arcname=".")

    def run_builder(self, release="1", **extra):
        self.pack_base()
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_")}
        env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"], TMPDIR=str(self.work / "tmp"),
                   FIXTURE_WORK=str(self.work), FIXTURE_REPO=str(usb.ROOT), OPOS_RELEASE=release, OPOS_NO_SSH="1")
        env.update(extra)
        command = ["sh", str(self.pkg / "build-image.sh"), str(self.base), str(self.out)]
        wrappers = []
        if env.pop("FIXTURE_FAIL_REPORT", ""):
            wrappers.append('printf() { [ "$1" != "%s  %s\\n" ] || return 9; command printf "$@"; }; ')
        if env.pop("FIXTURE_FAIL_CLEANUP", ""):
            wrappers.append('rm() { if [ -f "$FIXTURE_WORK/out.img" ] && [ "$1" = -rf ]; then return 9; fi; command rm "$@"; }; ')
        if wrappers:
            command = ["sh", "-c", "".join(wrappers) + '. "$0"'] + command[1:]
        return subprocess.run(command,
                              env=env, text=True, capture_output=True, timeout=60)

    def events(self):
        path = self.work / "events"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_clean(self):
        self.assertFalse(os.path.lexists(self.out))
        self.assertEqual(list(self.work.glob(".opos-build.*")), [])
        self.assertEqual(list((self.work / "tmp").iterdir()), [])
        self.assertFalse((self.work / "mounted").exists())

    def test_unlisted_hash_is_refused_before_mount_and_names_the_staged_hash(self):
        self.allowlist.write_text("0" * 64 + "  some other base\n")
        p = self.run_builder()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn(self.digest, p.stderr)
        self.assertFalse(any(e[0] == "mount" for e in self.events()))
        self.assertEqual(len([e for e in self.events() if e[0] == "cp"]), 1)
        self.assert_clean()

    def test_listed_opos_metadata_or_release_layout_is_refused_on_the_staged_copy(self):
        for rel in ("etc/opos/image.json", "opt/opos-releases", "opt/opos"):
            with self.subTest(marker=rel):
                marker = self.seed / rel
                marker.parent.mkdir(parents=True, exist_ok=True)
                if rel == "opt/opos":
                    marker.symlink_to("opos-releases/old")
                elif rel == "opt/opos-releases":
                    marker.mkdir()
                else:
                    marker.write_text("OPOS\n")
                self.pin()
                p = self.run_builder()
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("already an OPOS image", p.stderr)
                self.assertFalse(self.out.exists())
                self.assert_clean()
                marker.rmdir() if rel == "opt/opos-releases" else marker.unlink()

    def test_listed_opos_os_release_without_manifest_is_refused(self):
        for value in ('ID=opos\n', 'ID="opos" # previous OPOS\n'):
            with self.subTest(os_release=value):
                self.put("etc/os-release", 'NAME="Old Phone OS"\n' + value)
                self.pin()
                p = self.run_builder()
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("already an OPOS image", p.stderr)
                self.assert_clean()

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_listed_base_runs_the_full_builder_and_verifies_its_own_output(self):
        p = self.run_builder()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        events = self.events()
        first_mount = next(e for e in events if e[0] == "mount")
        staged = Path(first_mount[3])
        self.assertEqual(first_mount[2], "ro,loop,noload")
        self.assertEqual(staged.parent.parent, self.out.parent)
        self.assertTrue(staged.parent.name.startswith(".opos-build."))
        self.assertEqual([e[3] for e in events if e[0] == "mount"], [str(staged)] * 2)
        self.assertEqual((self.work / "verify-calls").read_text(), f"verify 1 {staged}\n")
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)
        with tarfile.open(self.out) as archive:
            self.assertEqual(archive.extractfile("./etc/opos/build-mode").read(), b"release\n")
            manifest = json.load(archive.extractfile("./etc/opos/image.json"))
            self.assertEqual(manifest["base"]["sha256"], self.digest)
            self.assertEqual(manifest["source_date_epoch"], 1794355200)
            marker = next(f for f in manifest["files"] if f["path"] == "etc/opos/build-mode")
            self.assertEqual(marker["sha256"], hashlib.sha256(b"release\n").hexdigest())
            self.assertIn(b'ID=opos\n', archive.extractfile("./etc/os-release").read())
            self.assertIn(b'ID=droidian\n', archive.extractfile("./usr/lib/os-release.base").read())
        self.assertEqual(identity.verify(self.work / "result"), manifest)
        gadget = self.work / "result" / usb.GADGET_REL
        entry = next(f for f in manifest["files"] if f["path"] == usb.GADGET_REL)
        self.assertEqual(entry["sha256"], hashlib.sha256(gadget.read_bytes()).hexdigest())
        gadget.write_text(usb.GADGET)
        with self.assertRaisesRegex(ValueError, usb.GADGET_REL):
            identity.verify(self.work / "result")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = st.ST.main(["--root", str(self.work / "result"), "--only", "identity"])
        r = st.Result(rc, stdout.getvalue())
        self.assertEqual(r.rc, 1, r.text)
        self.assertEqual(r.status("identity.image"), "FAIL", r.text)
        self.assertIn(usb.GADGET_REL, r.ev("identity.image"))
        self.assertEqual(list(self.work.glob(".opos-build.*")), [])
        self.assertFalse((self.work / "mounted").exists())

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_already_hardened_dev_base_still_records_gadget_hash(self):
        self.put(usb.GADGET_REL, usb.GADGET.replace(usb.TELNET_LINE, "# root telnet disabled by OPOS"), 0o755)
        self.put(usb.DROPIN_REL, "PasswordAuthentication no\nKbdInteractiveAuthentication no\n")
        before = (self.seed / usb.GADGET_REL).read_bytes()
        p = self.run_builder(release="0")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        result = self.work / "result"
        self.assertEqual((result / "etc/opos/build-mode").read_text(), "dev\n")
        self.assertEqual((result / usb.GADGET_REL).read_bytes(), before)
        manifest = identity.verify(result)
        entry = next(f for f in manifest["files"] if f["path"] == usb.GADGET_REL)
        self.assertEqual(entry["sha256"], hashlib.sha256(before).hexdigest())

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_manifest_keeps_admitted_staged_hash_when_fsck_changes_image(self):
        p = self.run_builder(FIXTURE_FSCK_CHANGES_IMAGE="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with tarfile.open(self.out) as archive:
            manifest = json.load(archive.extractfile("./etc/opos/image.json"))
        self.assertEqual(manifest["base"]["sha256"], self.digest)
        self.assertEqual(identity.verify(self.work / "result"), manifest)
        self.assertFalse((self.work / "mounted").exists())

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_release_verifier_failure_leaves_no_output(self):
        p = self.run_builder(FIXTURE_VERIFY_STATUS="7", OPOS_VERIFY_IMAGE="0")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("image verification failed", p.stderr)
        self.assert_clean()
        staged = next(e[3] for e in self.events() if e[0] == "mount")
        self.assertEqual((self.work / "verify-calls").read_text(), f"verify 1 {staged}\n")

    def test_changed_copy_is_refused_before_a_writable_mount_and_removed(self):
        p = self.run_builder(FIXTURE_CORRUPT_COPY="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(hashlib.sha256(b"changed during copy").hexdigest(), p.stderr)
        self.assert_clean()
        self.assertFalse(any(e[0] == "mount" for e in self.events()))

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_dev_accepts_an_unlisted_reused_base_and_reports_verification_not_run(self):
        self.put("etc/opos/image.json", "reused dev image\n")
        self.allowlist.unlink()
        p = self.run_builder(release="0")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("DEV IMAGE: verify-image.sh was NOT_RUN", p.stdout)
        self.assertTrue(self.out.exists())
        self.assertFalse((self.work / "verify-calls").exists())
        self.assertEqual(len([e for e in self.events() if e[0] == "mount"]), 1)

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_dev_can_request_verification_explicitly(self):
        p = self.run_builder(release="0", OPOS_VERIFY_IMAGE="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        staged = next(e[3] for e in self.events() if e[0] == "mount")
        self.assertEqual((self.work / "verify-calls").read_text(), f"verify 0 {staged}\n")

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_dev_key_span_and_sudo_keep_the_existing_markers_on_a_reused_base(self):
        self.put("etc/opos/image.json", "reused dev fixture\n")
        self.put("usr/sbin/sshd", "fixture, never run\n", 0o755)
        self.put("lib/systemd/system/ssh.service", "[Service]\nExecStart=/usr/sbin/sshd -D $SSHD_OPTS\n")
        (self.seed / "etc/systemd/system/multi-user.target.wants/ssh.service").symlink_to("/lib/systemd/system/ssh.service")
        key = self.work / "owner.pub"
        key.write_text(ck.sudo.TEST_KEY)
        p = self.run_builder(release="0", OPOS_NO_SSH="0", OPOS_OWNER_SSH_PUBKEY=str(key),
                             OPOS_DEV_SSH_PUBKEY=str(key), OPOS_DEV_SPAN="1", OPOS_DEV_SUDO="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        result = self.work / "result"
        for rel, value in (("etc/default/opos-kiosk", "OPOS_KIOSK_DEBUG_PORT=9222"),
                           ("etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf", "Environment=OPOS_SPAN=675,42"),
                           ("etc/sudoers.d/90-opos-dev", "droidian ALL=(ALL) NOPASSWD:ALL")):
            self.assertIn(value, (result / rel).read_text())
        self.assertIn("DEV IMAGE:", p.stdout)

    def test_no_ssh_conflicts_with_keys_before_copying(self):
        p = self.run_builder(OPOS_OWNER_SSH_PUBKEY="unused.pub")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("OPOS_NO_SSH conflicts with SSH keys", p.stderr)
        self.assertFalse(any(e[0] in ("cp", "mount") for e in self.events()))

    def test_existing_output_file_symlink_dangling_symlink_hardlink_and_directory_are_untouched(self):
        victim = self.work / "victim"
        victim.write_bytes(b"do not modify this inode")
        for release in ("0", "1"):
            for kind in ("file", "symlink", "dangling", "hardlink", "directory"):
                with self.subTest(release=release, kind=kind):
                    if kind == "file":
                        self.out.write_bytes(b"existing output")
                    elif kind == "directory":
                        self.out.mkdir()
                    elif kind == "hardlink":
                        os.link(victim, self.out)
                    else:
                        self.out.symlink_to(victim if kind == "symlink" else self.work / "missing")
                    before = self.out.lstat()
                    p = self.run_builder(release=release)
                    try:
                        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                        self.assertIn("OUT already exists", p.stderr)
                        self.assertEqual(self.out.lstat().st_ino, before.st_ino)
                        self.assertEqual(victim.read_bytes(), b"do not modify this inode")
                        if kind == "file":
                            self.assertEqual(self.out.read_bytes(), b"existing output")
                        self.assertFalse(any(e[0] in ("cp", "mount") for e in self.events()))
                    finally:
                        self.out.rmdir() if kind == "directory" else self.out.unlink(missing_ok=True)

    def test_unwritable_output_directory_is_refused_before_copy(self):
        directory = self.work / "unwritable"
        directory.mkdir(mode=0o555)
        self.addCleanup(directory.chmod, 0o755)
        self.out = directory / "out.img"
        p = self.run_builder()
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("OUT directory is not writable", p.stderr)
        self.assertFalse(any(e[0] in ("cp", "mount") for e in self.events()))
        self.assert_clean()

    def test_staged_image_is_created_exclusively_and_never_follows_a_preexisting_link(self):
        victim = self.work / "victim"
        victim.write_bytes(b"keep this")
        p = self.run_builder(FIXTURE_PRECREATE_STAGE="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(victim.read_bytes(), b"keep this")
        self.assertFalse(any(e[0] in ("cp", "mount") for e in self.events()))
        self.assert_clean()

    def test_override_cannot_admit_an_unlisted_base(self):
        alternate = self.work / "alternate.sha256"
        shutil.copyfile(self.allowlist, alternate)
        self.allowlist.write_text("0" * 64 + "  another base\n")
        p = self.run_builder(OPOS_RELEASE_BASES=str(alternate))
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("not listed", p.stderr)
        self.assert_clean()

    def test_policy_has_no_allowlist_argument(self):
        alternate = self.work / "alternate.sha256"
        shutil.copyfile(self.allowlist, alternate)
        self.allowlist.write_text("0" * 64 + "  another base\n")
        p = subprocess.run([sys.executable, "-I", "-B", str(self.pkg / "image-policy.py"), "release-base",
                            str(self.base), str(alternate)], text=True, capture_output=True, timeout=10)
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_missing_override_is_ignored_and_the_checkout_list_is_used(self):
        p = self.run_builder(OPOS_RELEASE_BASES=str(self.work / "absent.sha256"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_base_layout_swap_cannot_hide_an_allowlisted_opos_image(self):
        shutil.copyfile(self.base, self.work / "pristine.img")
        self.put("etc/opos/image.json", "reused image\n")
        self.pin()
        p = self.run_builder(FIXTURE_LAYOUT_SWAP="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("already an OPOS image", p.stderr)
        self.assert_clean()

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_source_replacement_after_copy_cannot_change_the_staged_image(self):
        p = self.run_builder(FIXTURE_CHANGE_SOURCE="1", FIXTURE_ASSERT_PRIVATE="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.base.read_bytes(), b"source replaced after copy")
        self.assertTrue(self.out.is_file())

    def test_partial_mount_failure_removes_staging_and_never_creates_output(self):
        for release in ("0", "1"):
            with self.subTest(release=release):
                p = self.run_builder(release=release, FIXTURE_FAIL_MOUNT="1")
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assert_clean()

    def test_untrusted_parent_refuses_before_a_staging_entry_can_be_swapped(self):
        self.work.chmod(0o777)
        self.addCleanup(self.work.chmod, 0o700)
        p = self.run_builder(FIXTURE_STAGE_SWAP="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("OUT parent", p.stderr)
        self.assertFalse(any(e[0] in ("mktemp", "cp", "stage-swap", "mount") for e in self.events()))
        self.assert_clean()

    def test_failed_builder_unmount_retains_the_backing_file_and_reports_mount(self):
        p = self.run_builder(FIXTURE_FAIL_UMOUNT="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        _, image, target = json.loads((self.work / "mounted").read_text())
        self.assertTrue(Path(image).is_file(), p.stdout + p.stderr)
        self.assertIn(target, p.stderr)
        self.assertIn(image, p.stderr)
        self.assertFalse(self.out.exists())

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_verifier_leaving_a_mount_cannot_publish_or_unlink_the_backing_file(self):
        (self.pkg / "verify-image.sh").write_text('mount -o ro,loop,noload "$1" "$(mktemp -d)"\nexit 0\n')
        p = self.run_builder()
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        _, image, target = json.loads((self.work / "mounted").read_text())
        self.assertTrue(Path(image).is_file())
        self.assertIn(image, p.stderr)
        self.assertIn("still attached", p.stderr)
        self.assertFalse(self.out.exists())

    def test_release_rejects_developer_key_zero_before_copying(self):
        p = self.run_builder(OPOS_DEV_SSH_PUBKEY="0")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("refuses", p.stderr)
        self.assertFalse(any(e[0] in ("cp", "mount") for e in self.events()))

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_failed_checksum_reporting_happens_before_publication(self):
        p = self.run_builder(FIXTURE_FAIL_REPORT="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assert_clean()

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_empty_stage_cleanup_failure_after_publication_preserves_success(self):
        p = self.run_builder(FIXTURE_FAIL_CLEANUP="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.out.is_file())
        stages = list(self.work.glob(".opos-build.*"))
        self.assertEqual(len(stages), 1)
        self.assertEqual(list(stages[0].iterdir()), [])

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_publication_commit_ignores_handled_signals_and_reports_success(self):
        (self.bin / "python3").unlink()
        (self.bin / "python3").symlink_to(self.work / "fixture-tool.py")
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                p = self.run_builder(FIXTURE_SIGNAL_PUBLICATION=str(int(sig)))
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertTrue(self.out.is_file())
                self.assertEqual(list(self.work.glob(".opos-build.*")), [])
                self.out.unlink()

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_newlineless_owner_comment_preserves_two_distinct_authorized_keys(self):
        owner, developer = self.work / "owner.pub", self.work / "developer.pub"
        owner.write_text(ck.sudo.TEST_KEY + "# owner comment without newline")
        import base64
        blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + bytes(reversed(range(32)))
        developer.write_text("ssh-ed25519 " + base64.b64encode(blob).decode() + " developer\n")
        self.put("usr/sbin/sshd", "fixture, never run\n", 0o755)
        self.put("lib/systemd/system/ssh.service", "[Service]\nExecStart=/usr/sbin/sshd -D $SSHD_OPTS\n")
        (self.seed / "etc/systemd/system/multi-user.target.wants/ssh.service").symlink_to("/lib/systemd/system/ssh.service")
        p = self.run_builder(release="0", OPOS_NO_SSH="0", OPOS_OWNER_SSH_PUBKEY=str(owner), OPOS_DEV_SSH_PUBKEY=str(developer))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        keys = (self.work / "result/home/droidian/.ssh/authorized_keys").read_text().splitlines()
        self.assertIn(ck.sudo.TEST_KEY.strip(), keys)
        self.assertIn(developer.read_text().strip(), keys)

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_final_fsck_failure_never_verifies_or_publishes(self):
        for release in ("0", "1"):
            with self.subTest(release=release):
                p = self.run_builder(release=release, FIXTURE_FAIL_FINAL_FSCK="1")
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertFalse((self.work / "verify-calls").exists())
                self.assert_clean()

    def test_mount_fixture_reads_the_requested_image_instead_of_the_seed(self):
        self.put("etc/mount-proof", "from the image argument\n")
        self.pack_base()
        other = self.work / "other.img"
        shutil.copyfile(self.base, other)
        self.put("etc/mount-proof", "from the wrong seed tree\n")
        target = self.work / "fixture-mount"
        target.mkdir()
        env = dict(os.environ, FIXTURE_WORK=str(self.work))
        p = subprocess.run([str(self.bin / "mount"), "-o", "ro,loop,noload", str(other), str(target)],
                           env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((target / "etc/mount-proof").read_text(), "from the image argument\n")
        p = subprocess.run([str(self.bin / "umount"), str(target)], env=env, capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_whole_release_verifier_refuses_dev_and_unmarked_image_before_image_code(self):
        for name in ("qemu-aarch64-static", "stat", "mountpoint"):
            if not (self.bin / name).exists():
                (self.bin / name).symlink_to(self.work / "fixture-tool.py")
        marker = self.seed / "etc/opos/build-mode"
        marker.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, FIXTURE_WORK=str(self.work), OPOS_RELEASE="1", TMPDIR=str(self.work / "tmp"),
                   PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        for value in (None, "dev\n"):
            with self.subTest(value=value):
                marker.unlink(missing_ok=True)
                if value:
                    marker.write_text(value)
                self.pack_base()
                p = subprocess.run(["sh", str(PKG / "verify-image.sh"), str(self.base)],
                                   env=env, text=True, capture_output=True, timeout=20)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("FAIL image records its build mode", p.stdout)
                self.assertIn("image build mode" if value is None else "marked dev", p.stderr)
                self.assertFalse(any(e[0] == "qemu-aarch64-static" for e in self.events()))
                self.assert_clean()

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_dev_verifier_failure_removes_staging_and_never_creates_output(self):
        p = self.run_builder(release="0", OPOS_VERIFY_IMAGE="1", FIXTURE_VERIFY_STATUS="7")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("image verification failed", p.stderr)
        self.assert_clean()

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_plain_dev_build_overwrites_an_inherited_release_mode_and_selftest_warns(self):
        self.put("etc/opos/build-mode", "release\n")
        p = self.run_builder(release="0", FIXTURE_ASSERT_PRIVATE="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        result = self.work / "result"
        self.assertEqual((result / "etc/opos/build-mode").read_text(), "dev\n")
        ctx = st.ST.Ctx(str(result))
        with redirect_stdout(io.StringIO()):
            st.ST.check_dev(ctx)
        self.assertIn(("WARN", "dev.build-mode", "DEV image: build mode is dev"), ctx.rows)

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_output_created_during_verification_is_never_replaced_or_followed(self):
        victim = self.work / "victim"
        victim.write_bytes(b"keep this")
        p = self.run_builder(FIXTURE_CREATE_OUT="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.out.is_symlink())
        self.assertEqual(victim.read_bytes(), b"keep this")
        self.assertEqual(list(self.work.glob(".opos-build.*")), [])

    @unittest.skipUnless(SSHD, "needs a host sshd -T parser")
    def test_no_ssh_choice_masks_services_sockets_and_removes_enablements(self):
        wants = self.seed / "etc/systemd/system/multi-user.target.wants"
        for name in ("ssh.service", "sshd.service", "ssh.socket", "sshd.socket"):
            (wants / name).symlink_to("/lib/systemd/system/" + name)
        self.pin()
        p = self.run_builder()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        result = self.work / "result"
        for name in ("ssh.service", "sshd.service", "ssh.socket", "sshd.socket"):
            self.assertEqual(os.readlink(result / "etc/systemd/system" / name), "/dev/null")
            self.assertFalse(os.path.lexists(result / "etc/systemd/system/multi-user.target.wants" / name))
        self.assertIn("OPOS_NO_SSH=1", (result / "etc/opos/no-ssh").read_text())


@unittest.skipUnless(SSHD, "needs a host sshd -T parser")
class Recovery(unittest.TestCase):
    setUp = ck.Findings.setUp
    shell = ck.Findings.shell
    policy = ck.Findings.policy
    recovery_root = ck.Findings.recovery_root
    provision = ck.Findings.provision

    def test_authorized_keys_default_and_matching_explicit_paths_are_accepted(self):
        pub, _ = self.recovery_root()
        main = self.root / "etc/ssh/sshd_config"
        original = main.read_text()
        for setting in ("", "AuthorizedKeysFile .ssh/authorized_keys", "AuthorizedKeysFile %h/.ssh/authorized_keys",
                        "AuthorizedKeysFile /home/%u/.ssh/authorized_keys"):
            with self.subTest(setting=setting):
                main.write_text(original + setting + "\n")
                p = self.provision(pub)
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_authorized_keys_none_other_path_or_match_override_is_refused(self):
        pub, _ = self.recovery_root()
        main = self.root / "etc/ssh/sshd_config"
        original = main.read_text()
        for setting in ("AuthorizedKeysFile none", "AuthorizedKeysFile /elsewhere/keys",
                        "Match User droidian\n AuthorizedKeysFile none", "PubkeyAuthentication no"):
            with self.subTest(setting=setting):
                main.write_text(original + setting + "\n")
                p = self.provision(pub)
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_untraversable_or_writable_home_ancestor_and_unreadable_key_are_refused(self):
        pub, home = self.recovery_root()
        self.assertEqual(self.provision(pub).returncode, 0)
        for path, mode in ((home, 0o600), (home / ".ssh", 0o600), (home / ".ssh/authorized_keys", 0),
                           (home.parent, 0o777), (home.parent, 0o600)):
            with self.subTest(path=path, mode=oct(mode)):
                before = path.stat().st_mode & 0o777
                path.chmod(mode)
                try:
                    self.assertNotEqual(self.policy("recovery").returncode, 0)
                finally:
                    path.chmod(before)

    def test_no_ssh_marker_alone_cannot_pass_without_masks(self):
        self.recovery_root()
        self.assertEqual(self.provision("", OPOS_NO_SSH="1").returncode, 0)
        (self.root / "etc/systemd/system/ssh.service").unlink()
        self.assertNotEqual(self.policy("recovery").returncode, 0)

    def test_key_path_owned_by_another_user_is_refused(self):
        pub, _ = self.recovery_root()
        self.assertEqual(self.provision(pub).returncode, 0)
        passwd = self.root / "etc/passwd"
        fields = passwd.read_text().strip().split(":")
        fields[2] = str(os.getuid() + 1)
        passwd.write_text(":".join(fields) + "\n")
        p = self.policy("recovery")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("unsafe ownership", p.stderr)


class OutputParent(unittest.TestCase):
    def setUp(self):
        self.policy = runpy.run_path(str(PKG / "image-policy.py"))
        self.assertIn("output_parent", self.policy)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_parent_rejects_group_or_other_writes_even_with_sticky_bit(self):
        parent = self.root / "out"
        parent.mkdir()
        for mode in (0o770, 0o707, 0o1777):
            with self.subTest(mode=oct(mode)):
                parent.chmod(mode)
                with self.assertRaisesRegex(ValueError, "OUT parent"):
                    self.policy["output_parent"](parent / "image")

    def test_parent_owner_is_root_or_invoking_sudo_user(self):
        parent = self.root / "out"
        parent.mkdir(mode=0o700)
        for uid in (0, 12345, 54321):
            original = Path.stat
            def stated(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if path == parent:
                    fields = list(value)
                    fields[4] = uid
                    return os.stat_result(fields)
                return value
            with self.subTest(uid=uid), mock.patch.dict(os.environ, SUDO_UID="12345"), mock.patch.object(Path, "stat", stated):
                if uid in (0, 12345):
                    with redirect_stdout(io.StringIO()):
                        self.policy["output_parent"](parent / "image")
                else:
                    with self.assertRaisesRegex(ValueError, "OUT parent"):
                        self.policy["output_parent"](parent / "image")

    def test_world_writable_ancestor_requires_sticky_bit_and_parent_is_resolved(self):
        ancestor = self.root / "ancestor"
        parent = ancestor / "out"
        parent.mkdir(parents=True, mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        ancestor.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "OUT ancestor"):
            self.policy["output_parent"](alias / "image")
        ancestor.chmod(0o1777)
        with redirect_stdout(io.StringIO()) as printed:
            self.policy["output_parent"](alias / "image")
        self.assertEqual(printed.getvalue().strip(), str(parent))


class VerifierUnmount(unittest.TestCase):
    setUp = Builder.setUp
    put = Builder.put
    pin = Builder.pin
    pack_base = Builder.pack_base
    events = Builder.events
    assert_clean = Builder.assert_clean
    def run_verifier(self, code=None, **extra):
        for name in ("qemu-aarch64-static", "stat"):
            (self.bin / name).symlink_to(self.work / "fixture-tool.py")
        self.put("etc/opos/build-mode", "release\n")
        self.pack_base()
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_")}
        env.update(FIXTURE_WORK=str(self.work), OPOS_RELEASE="1", TMPDIR=str(self.work / "tmp"),
                   PATH=str(self.bin) + os.pathsep + os.environ["PATH"], **extra)
        if code is None:
            command = ["sh", str(PKG / "verify-image.sh"), str(self.base)]
        else:
            script = self.pkg / "verify-image.sh"
            script.write_text(code)
            command = ["sh", "-x", str(script), str(self.base)]
        return subprocess.run(command, env=env, text=True, capture_output=True, timeout=30)

    def lifecycle(self, middle):
        # Execute the real lifecycle and final gate, omitting unrelated static
        # and qemu smoke checks. No replacement verifier or image code runs.
        text = (PKG / "verify-image.sh").read_text()
        prefix = text.split('r=$mnt\n', 1)[0]
        footer = text.rsplit('# --- qa: END\n', 1)[1]
        return prefix + middle + "\n" + footer

    def test_real_verifier_failed_first_unmount_reports_and_retains_image(self):
        p = self.run_verifier(FIXTURE_FAIL_UMOUNT="1")
        self.assertNotEqual(p.returncode, 0)
        _, image, target = json.loads((self.work / "mounted").read_text())
        self.assertIn("still attached", p.stderr)
        self.assertIn(target, p.stderr)
        self.assertIn(image, p.stderr)
        self.assertNotIn("VERIFY: PASS", p.stdout)
        self.assertTrue(Path(image).is_file())

    def test_failed_final_and_cleanup_unmount_cannot_pass(self):
        p = self.run_verifier(self.lifecycle("fail=0"), FIXTURE_FAIL_UMOUNT="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("VERIFY: PASS", p.stdout)
        self.assertIn("still attached", p.stderr)
        self.assertEqual(len([e for e in self.events() if e[0] == "umount"]), 2)

    def test_cleanup_failure_overrides_an_otherwise_successful_exit(self):
        p = self.run_verifier(self.lifecycle("exit 0"), FIXTURE_FAIL_UMOUNT="1")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("still attached", p.stderr)

    def test_cleanup_waits_for_owned_children_before_unmount(self):
        code = self.lifecycle('sleep 30 &\npids=$!\necho "OWNED_PID=$pids" >&2\nexit 0')
        p = self.run_verifier(code)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        pid = re.match(r"\d+", p.stderr.split("OWNED_PID=", 1)[1]).group()  # the job's own trace can interleave here
        self.assertIn("+ wait " + pid, p.stderr)
        self.assertLess(p.stderr.index("+ wait " + pid), p.stderr.index('+ umount '))
        self.assert_clean()

    def test_normal_stop_waits_for_the_owned_server(self):
        text = (PKG / "verify-image.sh").read_text()
        stop = text.split("stop_server() {", 1)[1].split("# --- servers: END", 1)[0]
        code = self.lifecycle('stop_server() {' + stop + '\nsleep 30 &\npids=$!\n'
                              'echo "OWNED_PID=$pids" >&2\nstop_server "$pids"\nfail=0')
        p = self.run_verifier(code)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        pid = re.match(r"\d+", p.stderr.split("OWNED_PID=", 1)[1]).group()  # the job's own trace can interleave here
        self.assertIn("+ wait " + pid, p.stderr)
        self.assertIn("VERIFY: PASS", p.stdout)
        self.assert_clean()


class SelftestSyntax(unittest.TestCase):
    def test_release_developer_key_is_a_path_while_boolean_zero_is_allowed(self):
        release_inputs = runpy.run_path(str(PKG / "image-policy.py"))["release_inputs"]
        for value in ("", "0", "1", "key.pub"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {
                    "OPOS_DEV_SSH_PUBKEY": value, "OPOS_DEV_SPAN": "0", "OPOS_DEV_SUDO": "0"}, clear=True):
                if value:
                    with self.assertRaisesRegex(ValueError, "refuses"):
                        release_inputs("unused")
                else:
                    release_inputs("unused")

    def test_atomic_publish_refuses_a_hardlink_created_after_the_last_path_check(self):
        namespace = runpy.run_path(str(PKG / "image-policy.py"))
        publish = namespace["publish"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, out, victim = (root / name for name in ("staged.img", "out.img", "victim"))
            source.write_bytes(b"verified image")
            victim.write_bytes(b"do not replace")

            def race(path):
                namespace["check_output"](path)
                os.link(victim, out)

            with mock.patch.dict(publish.__globals__, check_output=race):
                with self.assertRaises(FileExistsError):
                    publish(source, out)
            self.assertEqual(out.stat().st_ino, victim.stat().st_ino)
            self.assertEqual(out.read_bytes(), b"do not replace")
            self.assertEqual(source.read_bytes(), b"verified image")

    def test_selftest_reports_missing_and_invalid_build_mode(self):
        fake = st.Fake()
        self.addCleanup(fake.cleanup)
        marker = fake.root / "etc/opos/build-mode"
        for value in (None, "invalid\n", "dev\n", "release\n"):
            with self.subTest(value=value):
                marker.unlink(missing_ok=True)
                if value is not None:
                    fake.put("etc/opos/build-mode", value)
                ctx = st.ST.Ctx(str(fake.root))
                with redirect_stdout(io.StringIO()):
                    st.ST.check_dev(ctx)
                row = next((r for r in ctx.rows if r[1] == "dev.build-mode"), None)
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "PASS" if value == "release\n" else "WARN")

    def test_verifier_release_mode_requires_a_release_marker_before_running_image_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "etc/opos/build-mode"
            marker.parent.mkdir(parents=True)
            text = (PKG / "verify-image.sh").read_text()
            block = text.split("# --- build mode: BEGIN\n", 1)[1].split("# --- build mode: END", 1)[0]
            code = 'fail=0; check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }; ' + block + '\nexit "$fail"\n'
            for mode in ("0", "1"):
                for value in (None, "dev\n", "release\n", "release\nextra\n"):
                    with self.subTest(mode=mode, value=value):
                        marker.unlink(missing_ok=True)
                        if value is not None:
                            marker.write_text(value)
                        p = subprocess.run(["sh", "-c", code], env=dict(os.environ, r=str(root), here=str(PKG), OPOS_RELEASE=mode),
                                           text=True, capture_output=True, timeout=20)
                        accepted = value == "release\n" or (mode == "0" and value == "dev\n")
                        self.assertEqual(p.returncode, 0 if accepted else 1, p.stdout + p.stderr)

    def test_quoted_and_equals_syntax_password_match_is_always_a_failure(self):
        fake = st.Fake()
        self.addCleanup(fake.cleanup)
        for option in ("PasswordAuthentication", "KbdInteractiveAuthentication", "ChallengeResponseAuthentication"):
            for syntax in (' = "yes"', '= "yes"', '="yes"', ' ="yes"', ' "yes"', '= yes', ' = yes', '=yes', ' =yes'):
                with self.subTest(option=option, syntax=syntax):
                    fake.put("etc/ssh/sshd_config.d/20-local.conf", "Match User droidian\n " + option + syntax + " # comment\n")
                    ctx = st.ST.Ctx(str(fake.root), [str(fake.bin)], fake.url, st.NOW, None)
                    with redirect_stdout(io.StringIO()):
                        st.ST.check_ssh(ctx)
                    self.assertEqual(ctx.rows[0][0], "FAIL", ctx.rows)
