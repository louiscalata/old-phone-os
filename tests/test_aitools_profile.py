# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from opos.aitools.launcher import BEGIN, END, add_profile_block, remove_profile_block, render_env
def _block(env: Path) -> bytes:
    """Build expected block bytes."""
    q = shlex.quote(str(env))
    return f"{BEGIN}\nif [ -r {q} ]; then . {q}; else :; fi\n{END}\n".encode()
def _write(p: Path, b: bytes) -> None:
    """Write bytes creating parents."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b)
def _shell_path(snippet: str, orig: str) -> str:
    """Run snippet in sh and return PATH."""
    script = snippet + 'printf "%s" "$PATH"'
    cp = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, env={"PATH": orig}, timeout=2)
    assert cp.returncode == 0
    return cp.stdout
class TestProfileBlock(unittest.TestCase):
    def test_add_creates_missing_and_parents(self):
        """Missing file and parent dir created."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a" / "b" / ".profile"
            e = Path("/fake/home/u/env.sh")
            self.assertTrue(add_profile_block(p, e))
            self.assertTrue(p.is_file())
            self.assertEqual(p.read_bytes(), _block(e))
            self.assertTrue(p.parent.is_dir())
    def test_add_idempotent(self):
        """Second add returns False and leaves file unchanged."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            e = Path("/fake/home/u/env.sh")
            add_profile_block(p, e)
            before = p.read_bytes()
            self.assertFalse(add_profile_block(p, e))
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse(add_profile_block(p, Path("/fake/home/u/other.sh")))
            self.assertEqual(p.read_bytes(), before)
    def test_add_preserves_and_adds_newline(self):
        """Preserves existing content and inserts newline when needed."""
        cases = [
            (b"hello\n", b"hello\n"),
            (b"hello", b"hello\n"),
            (b"a\nb\n", b"a\nb\n"),
            (b"", b""),
        ]
        for initial, expected_prefix in cases:
            with self.subTest(initial=initial):
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / ".profile"
                    e = Path("/fake/home/u/env.sh")
                    if initial or True:
                        _write(p, initial) if initial else _write(p, b"")
                        # empty case: file exists empty, still should get block without extra newline
                        if initial == b"":
                            p.write_bytes(b"")
                    else:
                        pass
                    # recreate for empty vs missing distinction
                    # test missing already covered, here test existing empty
                    add_profile_block(p, e)
                    data = p.read_bytes()
                    self.assertTrue(data.endswith(_block(e)))
                    if initial:
                        self.assertTrue(data.startswith(expected_prefix))
                        self.assertEqual(data, expected_prefix + _block(e))
                    else:
                        # file existed empty -> result is just block (no extra leading newline)
                        # if file did not exist, same result; both are valid
                        self.assertEqual(data, _block(e))
    def test_add_with_crlf_preserves_bytes(self):
        """CRLF content stays CRLF, block appended as LF."""
        with tempfile.TemporaryDirectory() as td:
            for initial, exp_prefix in [
                (b"a\r\nb\r\n", b"a\r\nb\r\n"),
                (b"a\r\nb", b"a\r\nb\n"),
            ]:
                with self.subTest(initial=initial):
                    p = Path(td) / f"p_{initial[:1].hex()}.profile"
                    e = Path("/fake/home/u/env.sh")
                    _write(p, initial)
                    add_profile_block(p, e)
                    data = p.read_bytes()
                    self.assertEqual(data, exp_prefix + _block(e))
    def test_remove_deletes_when_only_block(self):
        """File containing only block is deleted."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            e = Path("/fake/home/u/env.sh")
            add_profile_block(p, e)
            self.assertTrue(p.exists())
            self.assertTrue(remove_profile_block(p))
            self.assertFalse(p.exists())
    def test_remove_preserves_before_and_after(self):
        """Bytes before and after block preserved exactly."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            e = Path("/fake/home/u/env.sh")
            block = _block(e)
            cases = [
                (b"first\n", b"last\n"),
                (b"one\ntwo\n", b"three\nfour\n"),
                (b"", b"after\n"),
                (b"before\n", b""),
            ]
            for before, after in cases:
                with self.subTest(before=before, after=after):
                    _write(p, before + block + after)
                    self.assertTrue(remove_profile_block(p))
                    if before + after == b"":
                        self.assertFalse(p.exists())
                    else:
                        self.assertEqual(p.read_bytes(), before + after)
    def test_remove_no_block_returns_false_untouched(self):
        """No BEGIN marker leaves file untouched."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            orig = b"hello\nworld\n"
            _write(p, orig)
            self.assertFalse(remove_profile_block(p))
            self.assertEqual(p.read_bytes(), orig)
            q = Path(td) / "missing"
            self.assertFalse(remove_profile_block(q))
    def test_remove_crlf_preserved(self):
        """CRLF before/after preserved byte for byte."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            e = Path("/fake/home/u/env.sh")
            block = _block(e)
            before = b"a\r\nb\r\n"
            after = b"c\r\nd\r\n"
            _write(p, before + block + after)
            self.assertTrue(remove_profile_block(p))
            self.assertEqual(p.read_bytes(), before + after)
    def test_remove_twice_removes_both(self):
        """Two blocks both removed, middle kept."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            e = Path("/fake/home/u/env.sh")
            block = _block(e)
            before = b"pre\n"
            mid = b"mid\n"
            after = b"post\n"
            _write(p, before + block + mid + block + after)
            self.assertTrue(remove_profile_block(p))
            self.assertEqual(p.read_bytes(), before + mid + after)
    def test_remove_orphan_begin_without_end(self):
        """Orphan BEGIN leaves file unchanged."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            cases = [
                b"keep\n" + BEGIN.encode() + b"\norphan\nmore\n",
                BEGIN.encode() + b"\norphan\n",
                b"a\r\n" + BEGIN.encode() + b"\nrest\n",
                b"before\n" + BEGIN.encode() + b"\n",
                b"keep\n" + BEGIN.encode() + b"\n",
            ]
            for orig in cases:
                with self.subTest(orig=orig):
                    _write(p, orig)
                    self.assertFalse(remove_profile_block(p))
                    self.assertEqual(p.read_bytes(), orig)
            q = Path(td) / "missing"
            self.assertFalse(remove_profile_block(q))
    def test_symlink_stays_symlink(self):
        """Symlink preserved and target changed."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            target = d / "real"
            link = d / ".profile"
            e = Path("/fake/home/u/env.sh")
            _write(target, b"orig\n")
            os.symlink(target, link)
            self.assertTrue(link.is_symlink())
            self.assertTrue(add_profile_block(link, e))
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_bytes(), b"orig\n" + _block(e))
            self.assertEqual(link.read_bytes(), b"orig\n" + _block(e))
            self.assertTrue(remove_profile_block(link))
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_bytes(), b"orig\n")
            # symlink to missing target
            missing_target = d / "missing_target"
            link2 = d / "link2"
            os.symlink(missing_target, link2)
            self.assertTrue(link2.is_symlink())
            self.assertFalse(missing_target.exists())
            self.assertTrue(add_profile_block(link2, e))
            self.assertTrue(link2.is_symlink())
            self.assertTrue(missing_target.is_file())
            self.assertEqual(missing_target.read_bytes(), _block(e))
            self.assertTrue(remove_profile_block(link2))
            self.assertTrue(link2.is_symlink())
            self.assertFalse(missing_target.exists())
    def test_two_complete_blocks_both_removed(self):
        """Two complete blocks both removed."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            e = Path("/fake/home/u/env.sh")
            block = _block(e)
            _write(p, block + block)
            self.assertTrue(remove_profile_block(p))
            self.assertFalse(p.exists())
            _write(p, b"pre\n" + block + b"mid\n" + block + b"post\n")
            self.assertTrue(remove_profile_block(p))
            self.assertEqual(p.read_bytes(), b"pre\nmid\npost\n")
    @unittest.skipIf(sys.platform == "win32", "POSIX chmod modes not supported on Windows")
    def test_mode_600_preserved(self):
        """Mode 0o600 preserved after add and remove."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".profile"
            e = Path("/fake/home/u/env.sh")
            _write(p, b"keep\n")
            os.chmod(p, 0o600)
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertTrue(add_profile_block(p, e))
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertTrue(remove_profile_block(p))
            self.assertTrue(p.exists())
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual(p.read_bytes(), b"keep\n")
    def test_no_stray_temp_files(self):
        """No stray temp files left."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            p = d / ".profile"
            e = Path("/fake/home/u/env.sh")
            add_profile_block(p, e)
            self.assertEqual({x.name for x in d.iterdir()}, {".profile"})
            for f in d.iterdir():
                self.assertFalse(f.name.startswith("tmp"))
            self.assertTrue(remove_profile_block(p))
            self.assertEqual(list(d.iterdir()), [])
            _write(p, b"keep\n")
            _write(d / "keep.txt", b"other\n")
            add_profile_block(p, e)
            self.assertEqual({x.name for x in d.iterdir()}, {".profile", "keep.txt"})
            for f in d.iterdir():
                self.assertFalse("tmp" in f.name.lower())
            self.assertTrue(remove_profile_block(p))
            self.assertEqual({x.name for x in d.iterdir()}, {".profile", "keep.txt"})
            for f in d.iterdir():
                self.assertFalse("tmp" in f.name.lower())
            self.assertEqual(p.read_bytes(), b"keep\n")
    @unittest.skipIf(sys.platform == "win32", "POSIX shell quoting requires /bin/sh")
    def test_render_env_quoting_via_shell(self):
        """PATH with spaces and single quote correctly quoted."""
        orig = "/usr/bin:/bin"
        cases = [
            "/fake/home/u/my tools/o'neil/bin",
            "/fake/home/u/a b/c'd/e f",
            "/tmp/simple",
            "/home/u/weird ' \" $path",
        ]
        for bin_path in cases:
            with self.subTest(bin_path=bin_path):
                snippet = render_env(Path(bin_path))
                self.assertTrue(snippet.startswith("# SPDX-License-Identifier: GPL-3.0-or-later\n"))
                self.assertTrue(snippet.endswith("\n"))
                self.assertIn("export PATH=", snippet)
                got = _shell_path(snippet, orig)
                self.assertEqual(got, f"{bin_path}:{orig}")
                # second line is export with quoted dir plus :"$PATH"
                lines = snippet.splitlines()
                self.assertEqual(len(lines), 2)
                self.assertEqual(lines[1], f"export PATH={shlex.quote(bin_path)}:\"$PATH\"")

    @unittest.skipIf(sys.platform == "win32", "POSIX shell quoting requires /bin/sh")
    def test_profile_sources_readable_env_and_tolerates_missing_env(self):
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            env_file = td_p / "a b" / "c'd" / "env.sh"
            profile = td_p / "profile"
            _write(profile, b"export F012_USER=kept\n")
            self.assertTrue(add_profile_block(profile, env_file))
            saved = profile.read_bytes()

            def _run():
                env = os.environ.copy()
                env.pop("F012_USER", None)
                env.pop("F012_SENTINEL", None)
                cp = subprocess.run(
                    ["/bin/sh", "-ec", '. "$1"; printf "%s\\n" "$F012_USER"; printf "%s\\n" "$F012_SENTINEL"', "sh", str(profile)],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    env=env,
                )
                return cp

            cp = _run()
            self.assertEqual(cp.returncode, 0)
            self.assertEqual(cp.stderr, "")
            self.assertEqual(cp.stdout, "kept\n\n")
            self.assertEqual(profile.read_bytes(), saved)
            env_file.parent.mkdir(parents=True, exist_ok=True)
            env_file.write_text("export F012_SENTINEL=loaded\n", encoding="utf-8")
            cp = _run()
            self.assertEqual(cp.returncode, 0)
            self.assertEqual(cp.stderr, "")
            self.assertEqual(cp.stdout, "kept\nloaded\n")
            self.assertEqual(profile.read_bytes(), saved)
