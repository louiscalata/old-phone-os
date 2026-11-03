# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for deploy manifest scripts."""
import hashlib
import pathlib
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
MAKE = REPO / "deploy" / "make-manifest.sh"
VERIFY = REPO / "deploy" / "verify-manifest.sh"
NEED = shutil.which("bash") and (shutil.which("sha256sum") or shutil.which("shasum"))


def run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a bash script and capture output."""
    return subprocess.run(["bash", *args], capture_output=True, text=True)


@unittest.skipUnless(NEED, "needs bash and sha256sum/shasum")
class ManifestTest(unittest.TestCase):
    """Make and verify manifest against a fake tree."""

    def setUp(self) -> None:
        """Build a fake payload tree."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "src" / "opos").mkdir(parents=True)
        (self.root / "deploy").mkdir(parents=True)
        (self.root / "src" / "opos" / "a.py").write_text("a=1\n")
        (self.root / "src" / "opos" / "b.py").write_text("b=2\n")
        (self.root / "deploy" / "x.sh").write_text("#!/bin/bash\n")
        (self.root / "LICENSE").write_text("GPL\n")
        r = run(str(MAKE), str(self.root))
        self.assertEqual(r.returncode, 0)

    def verify(self, *extra: str) -> subprocess.CompletedProcess[str]:
        """Verify the fixture tree with extra args."""
        return run(str(VERIFY), *extra, str(self.root))

    def test_make_then_verify(self) -> None:
        """Fresh manifest verifies four files."""
        r = self.verify()
        self.assertEqual(r.returncode, 0)
        self.assertIn("ok: 4 files verified", r.stdout)

    def test_manifest_excludes_pyc_and_self(self) -> None:
        """Manifest omits itself."""
        body = (self.root / "SHA256SUMS").read_text()
        self.assertNotIn("SHA256SUMS", body)

    def test_planted_pyc_fails_verify(self) -> None:
        """Planted .pyc fails verification."""
        (self.root / "src" / "opos" / "evil.pyc").write_bytes(b"\x00")
        r = self.verify()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stderr)

    def test_pycache_dir_fails_verify(self) -> None:
        """Planted __pycache__ fails verification."""
        d = self.root / "src" / "opos" / "__pycache__"
        d.mkdir(parents=True)
        (d / "a.pyc").write_bytes(b"\x00")
        r = self.verify()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stderr)

    def test_make_refuses_pyc(self) -> None:
        """Make refuses a tree with bytecode."""
        (self.root / "src" / "opos" / "evil.pyc").write_bytes(b"\x00")
        r = run(str(MAKE), str(self.root))
        self.assertNotEqual(r.returncode, 0)

    def test_make_refuses_pycache(self) -> None:
        """Make refuses a tree with __pycache__."""
        d = self.root / "src" / "opos" / "__pycache__"
        d.mkdir(parents=True)
        (d / "a.pyc").write_bytes(b"\x00")
        r = run(str(MAKE), str(self.root))
        self.assertNotEqual(r.returncode, 0)

    def test_make_refuses_symlink_manifest(self) -> None:
        """Make refuses a symlinked SHA256SUMS."""
        outside = self.root / "outside.txt"
        outside.write_text("secret\n")
        m = self.root / "SHA256SUMS"
        m.unlink()
        m.symlink_to(outside)
        r = run(str(MAKE), str(self.root))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(outside.read_text(), "secret\n")

    def test_empty_expect_sha_fails(self) -> None:
        """Empty --expect-manifest-sha fails."""
        r = run(str(VERIFY), "--expect-manifest-sha", "", str(self.root))
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stderr)

    def test_modified_byte_fails(self) -> None:
        """One changed byte fails verification."""
        p = self.root / "src" / "opos" / "a.py"
        p.write_text(p.read_text() + "x")
        r = self.verify()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stderr)

    def test_deleted_file_fails(self) -> None:
        """A deleted listed file fails verification."""
        (self.root / "src" / "opos" / "b.py").unlink()
        self.assertEqual(self.verify().returncode, 1)

    def test_extra_src_file_fails(self) -> None:
        """An unlisted file under src fails verification."""
        (self.root / "src" / "opos" / "c.py").write_text("c=3\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_extra_deploy_file_fails(self) -> None:
        """An unlisted file under deploy fails verification."""
        (self.root / "deploy" / "y.sh").write_text("#!/bin/bash\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_symlink_fails(self) -> None:
        """A symlink under src fails verification."""
        (self.root / "src" / "link.py").symlink_to("opos/a.py")
        self.assertEqual(self.verify().returncode, 1)

    def test_expect_sha_ok(self) -> None:
        """Correct --expect-manifest-sha passes."""
        r = run(str(MAKE), str(self.root))
        self.assertEqual(r.returncode, 0)
        line = next(x for x in r.stdout.splitlines() if "manifest sha256:" in x)
        digest = line.rsplit(":", 1)[1].strip()
        v = run(str(VERIFY), "--expect-manifest-sha", digest, str(self.root))
        self.assertEqual(v.returncode, 0)

    def test_expect_sha_bad(self) -> None:
        """Wrong --expect-manifest-sha fails."""
        v = run(str(VERIFY), "--expect-manifest-sha", "0" * 64, str(self.root))
        self.assertEqual(v.returncode, 1)

    def test_absolute_entry_fails(self) -> None:
        """An absolute path entry fails verification."""
        m = self.root / "SHA256SUMS"
        m.write_text(m.read_text() + "0" * 64 + "  /etc/passwd\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_duplicate_entry_fails(self) -> None:
        """A duplicated manifest line fails verification."""
        m = self.root / "SHA256SUMS"
        first = m.read_text().splitlines()[0]
        m.write_text(m.read_text() + first + "\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_fifo_fails(self) -> None:
        """A fifo under src fails verification."""
        try:
            import os as _os

            _os.mkfifo(self.root / "src" / "opos" / "q.fifo")
        except (AttributeError, OSError):
            self.skipTest("no fifo")
        self.assertEqual(self.verify().returncode, 1)

    def test_dotdot_entry_fails(self) -> None:
        """A ../ entry in the manifest fails verification."""
        m = self.root / "SHA256SUMS"
        m.write_text(m.read_text() + "0" * 64 + "  ../etc/passwd\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_missing_manifest_fails(self) -> None:
        """Missing SHA256SUMS fails verification."""
        (self.root / "SHA256SUMS").unlink()
        self.assertEqual(self.verify().returncode, 1)

    def test_unknown_option_exits_2(self) -> None:
        """Unknown option exits 2."""
        r = run(str(VERIFY), "--nope", str(self.root))
        self.assertEqual(r.returncode, 2)

    def test_bad_hex_entry_fails(self) -> None:
        """A non-hex hash entry fails verification."""
        m = self.root / "SHA256SUMS"
        m.write_text(m.read_text() + "z" * 64 + "  src/opos/a.py\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_short_hash_entry_fails(self) -> None:
        """A short hash entry fails verification."""
        m = self.root / "SHA256SUMS"
        m.write_text(m.read_text() + "0" * 63 + "  src/opos/a.py\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_unexpected_entry_fails(self) -> None:
        """An out-of-scope path fails verification."""
        m = self.root / "SHA256SUMS"
        first = m.read_text().splitlines()[0]
        h = first.split()[0]
        m.write_text(m.read_text() + h + "  other/x\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_bad_expect_shape_fails(self) -> None:
        """A malformed pin fails verification."""
        v = run(str(VERIFY), "--expect-manifest-sha", "zz", str(self.root))
        self.assertEqual(v.returncode, 1)

    def test_empty_manifest_fails(self) -> None:
        """Empty manifest fails verification."""
        (self.root / "SHA256SUMS").write_text("")
        self.assertEqual(self.verify().returncode, 1)

    def test_listed_symlink_fails(self) -> None:
        """A listed symlink entry fails verification."""
        target = self.root / "src" / "opos" / "a.py"
        link = self.root / "src" / "opos" / "l.py"
        link.symlink_to(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        m = self.root / "SHA256SUMS"
        m.write_text(m.read_text() + digest + "  src/opos/l.py\n")
        self.assertEqual(self.verify().returncode, 1)

    def test_bash_syntax(self) -> None:
        """Both scripts pass bash -n."""
        self.assertEqual(run("-n", str(MAKE)).returncode, 0)
        self.assertEqual(run("-n", str(VERIFY)).returncode, 0)

    def test_shellcheck(self) -> None:
        """Both scripts pass shellcheck when installed."""
        if not shutil.which("shellcheck"):
            self.skipTest("no shellcheck")
        exe = shutil.which("shellcheck")
        assert exe is not None
        for s in (str(MAKE), str(VERIFY)):
            r = subprocess.run([exe, s], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
