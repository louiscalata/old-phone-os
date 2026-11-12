# SPDX-License-Identifier: GPL-3.0-or-later
"""Release packaging with read-only debugfs/ssh-keygen stand-ins. No keys are generated.

The SSHSIG envelope is exercised; its fake signature is deliberately not
cryptographic evidence. Owner-key signing and real ext4 extraction are NOT_RUN.
"""
import base64
import hashlib
import importlib.util
import io
import json
import struct
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.duo_identity_fixture import add_identity

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_release", ROOT / "tools/opos-image-release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def field(data):
    return struct.pack(">I", len(data)) + data


def fake_signature(data, namespace=b"opos-update", kind=b"ssh-ed25519"):
    # A test double, not a signature made with a private key.
    key = field(kind) + field(b"x" * 32)
    sig = field(kind) + field(hashlib.sha512(data).digest())
    raw = b"SSHSIG" + struct.pack(">I", 1) + field(key) + field(namespace) + field(b"") + field(b"sha512") + field(sig)
    return b"-----BEGIN SSH SIGNATURE-----\n" + base64.b64encode(raw) + b"\n-----END SSH SIGNATURE-----\n"


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        add_identity(self.t / "fixture")
        self.manifest = (self.t / "fixture/etc/opos/image.json").read_bytes()
        self.image = self.t / "input.img"
        self.image.write_bytes(b"SIMULATED_IMAGE\n" + self.manifest)
        self.out = self.t / "release"
        self.key = self.t / "owner-supplied-key.pub"
        self.key.write_text("test double for an owner-supplied agent-backed public key\n")
        self.signers = self.t / "allowed_signers"
        self.signers.write_text("test double for the owner's public trust file\n")
        self.calls = []
        self.reject_signature = False
        self.patcher = mock.patch.object(release.subprocess, "run", side_effect=self.native)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def native(self, args, input=None, **kw):
        self.calls.append((list(args), input))
        if args[0] == "debugfs":
            self.assertEqual(args[1:3], ["-R", "cat /etc/opos/image.json"])
            image = Path(args[3]).read_bytes()
            data = image.partition(b"\n")[2]
            return subprocess.CompletedProcess(args, 0, data, b"debugfs: simulated read-only extraction\n")
        self.assertEqual(args[0], "ssh-keygen")
        self.assertEqual(args[1], "-Y")
        self.assertIn(args[2], ("sign", "verify"))
        self.assertEqual(args[args.index("-n") + 1], "opos-update")
        if args[2] == "sign":
            self.assertEqual(Path(args[args.index("-f") + 1]), self.key.resolve())
            path = Path(args[-1])
            Path(str(path) + ".sig").write_bytes(fake_signature(path.read_bytes()))
        else:
            self.assertEqual(args[args.index("-I") + 1], "opos-release")
            self.assertEqual(Path(args[args.index("-f") + 1]), self.signers.resolve())
            sig = Path(args[args.index("-s") + 1]).read_bytes()
            if self.reject_signature or sig != fake_signature(input):
                return subprocess.CompletedProcess(args, 1, b"", b"test double rejects signature\n")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = release.main(list(args))
        return rc, stdout.getvalue(), stderr.getvalue()

    def build(self, *extra):
        return self.invoke("build", str(self.image), "--key", str(self.key), "--out", str(self.out),
                           "--allowed-signers", str(self.signers), *extra)

    def verify(self):
        return self.invoke("verify", str(self.out), "--allowed-signers", str(self.signers))

    def test_build_and_verify_folder_with_detached_signature_and_no_keys(self):
        before = self.image.read_bytes()
        rc, out, err = self.build()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual({p.name for p in self.out.iterdir()}, release.ALL_FILES)
        self.assertEqual((self.out / "rootfs.img").read_bytes(), before)
        self.assertEqual(self.image.read_bytes(), before)
        self.assertEqual((self.out / "image.json").read_bytes(), self.manifest)
        self.assertEqual(self.verify()[0], 0)
        for name in release.FILES:
            self.assertIn(release.identity.hash_file(self.out / name) + "  " + name, (self.out / "SHA256SUMS").read_text())
        self.assertNotIn(self.key.name, {p.name for p in self.out.iterdir()})
        self.assertFalse(any("-w" in args or "-t" in args for args, _ in self.calls))

    def test_template_labels_unperformed_device_and_real_image_checks(self):
        self.assertEqual(self.build()[0], 0)
        notes = (self.out / "RELEASE_NOTES.txt").read_text()
        self.assertIn("NOT_RUN", notes)
        self.assertIn("Changes: TODO", notes)
        self.assertIn("Calls and messages remain simulated", notes)
        self.assertIn("not an OTA update bundle", notes)

    def test_completed_notes_are_signed_too(self):
        notes = self.t / "notes.txt"
        notes.write_text("Owner's completed notes\n")
        self.assertEqual(self.build("--notes", str(notes))[0], 0)
        self.assertEqual((self.out / "RELEASE_NOTES.txt").read_bytes(), notes.read_bytes())
        (self.out / "RELEASE_NOTES.txt").write_text("edited after signing\n")
        rc, _, err = self.verify()
        self.assertEqual(rc, 1)
        self.assertIn("hashes differ", err)

    def test_image_and_manifest_tampering_are_rejected(self):
        self.assertEqual(self.build()[0], 0)
        for name in ("rootfs.img", "image.json"):
            path = self.out / name
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            self.assertEqual(self.verify()[0], 1, name)
            path.write_bytes(original)

    def test_signature_and_signed_hash_list_tampering_are_rejected(self):
        self.assertEqual(self.build()[0], 0)
        p = self.out / "SHA256SUMS"
        p.write_bytes(p.read_bytes().replace(b"rootfs.img", b"../../file"))
        self.assertEqual(self.verify()[0], 1)

    def test_wrong_trust_rejected_before_output_is_published(self):
        self.reject_signature = True
        rc, _, err = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("rejects signature", err)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.t.glob(".opos-image-release-*")), [])

    def test_missing_manifest_or_invalid_manifest_stops_before_signing(self):
        self.image.write_bytes(b"SIMULATED_IMAGE\nnot JSON")
        self.assertEqual(self.build()[0], 1)
        self.assertFalse(any(a[0] == "ssh-keygen" for a, _ in self.calls))
        self.assertFalse(self.out.exists())

    def test_embedded_manifest_must_match_sidecar_even_with_matching_hashes(self):
        self.assertEqual(self.build()[0], 0)
        m = json.loads(self.manifest)
        m["version"] = "9.9.9"
        (self.out / "image.json").write_bytes(release.identity.canonical(m))
        data = release.sums(self.out)
        (self.out / "SHA256SUMS").write_bytes(data)
        (self.out / "SHA256SUMS.sig").write_bytes(fake_signature(data))
        rc, _, err = self.verify()
        self.assertEqual(rc, 1)
        self.assertIn("inside the image", err)

    def test_output_is_not_overwritten_and_extra_files_or_symlinks_refused(self):
        self.assertEqual(self.build()[0], 0)
        self.assertEqual(self.build()[0], 1)
        (self.out / "unexpected.txt").write_text("unexpected")
        self.assertEqual(self.verify()[0], 1)
        (self.out / "unexpected.txt").unlink()
        (self.out / "rootfs.img").unlink()
        (self.out / "rootfs.img").symlink_to(self.image)
        self.assertEqual(self.verify()[0], 1)

    def test_signature_parser_refuses_other_namespaces_keys_and_malformed_data(self):
        for sig in (b"garbage", fake_signature(b"x", namespace=b"other"), fake_signature(b"x", kind=b"ssh-rsa"),
                    b"-----BEGIN SSH SIGNATURE-----\ninvalid!\n-----END SSH SIGNATURE-----\n"):
            with self.subTest(sig=sig[:30]), self.assertRaises(ValueError):
                release.signature_format(sig)
        release.signature_format(fake_signature(b"x"))

    def test_missing_external_tool_is_an_actionable_failure(self):
        with mock.patch.object(release.subprocess, "run", side_effect=FileNotFoundError("debugfs not installed")):
            rc, _, err = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("debugfs", err)
        self.assertFalse(self.out.exists())
