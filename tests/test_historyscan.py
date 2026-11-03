# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for guard/historyscan.py using throwaway git repos."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Tuple


def _root() -> Path:
    """Return repository root above the tests directory."""
    return Path(__file__).resolve().parent.parent


def _git(repo: str, *args: str) -> None:
    """Run git with fixed identity in the given repo."""
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr)


def _seed(repo: str) -> None:
    """Copy guard files into temp repo including loader-nested path."""
    src = _root() / "guard"
    dst = Path(repo) / "guard"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "check.py", dst / "check.py")
    shutil.copy(src / "historyscan.py", dst / "historyscan.py")


def _commit(repo: str, name: str, data: str, msg: str) -> None:
    """Write a file, add it, and commit it."""
    if not name or not msg:
        raise AssertionError("bad commit args")
    p = Path(repo) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", msg)


def _scan(repo: str, env: dict | None = None) -> Tuple[int, str]:
    """Run historyscan in repo and return exit code and output."""
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(["python3", "guard/historyscan.py"], cwd=repo, capture_output=True, text=True, env=e)
    return r.returncode, r.stdout + r.stderr


def _tok() -> Tuple[str, str]:
    """Build token and suffix from fragments at runtime."""
    pre = "gh" + "p_"
    sfx = chr(65) * 24
    return pre + sfx, sfx


class HistoryScanTest(unittest.TestCase):
    """End-to-end history scan checks on throwaway repos."""

    def _new(self, tmp: str) -> str:
        """Init repo with guard files and a main branch."""
        _seed(tmp)
        _git(tmp, "init")
        _git(tmp, "branch", "-M", "main")
        return tmp

    def test_clean_pass(self) -> None:
        """Clean history exits zero with PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "ok.txt", "hello\n", "hello")
            code, out = _scan(tmp)
            self.assertEqual(code, 0)
            self.assertIn("PASS", out)

    def test_removed_still_found(self) -> None:
        """Removed token stays visible in history with FAIL."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "a.txt", "v=" + full + "\n", "one")
            p = Path(tmp) / "a.txt"
            p.write_text("clean\n", encoding="utf-8")
            _git(tmp, "add", "a.txt")
            _git(tmp, "commit", "-m", "two")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(sfx, out)

    def test_side_branch_found(self) -> None:
        """Token on uncheckout side branch is found."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "ok.txt", "hello\n", "base")
            _git(tmp, "checkout", "-b", "side")
            _commit(tmp, "b.txt", "v=" + full + "\n", "side work")
            _git(tmp, "checkout", "main")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(sfx, out)

    def test_dotenv_path_found(self) -> None:
        """Dotenv filename in history is flagged by path."""
        nm = "." + "env"
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, nm, "ok\n", "config")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)

    def test_subject_found(self) -> None:
        """Token inside commit subject is flagged."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "ok.txt", "hello\n", "add " + full)
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(sfx, out)

    def test_output_hides_value(self) -> None:
        """Scanner output never echoes the full suffix."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "a.txt", "v=" + full + "\n", "one")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertNotIn(sfx, out)
            self.assertNotIn(full, out)

    def test_merge_resolution_found(self) -> None:
        """Token added only in merge commit is found."""
        pre = "gh" + "p_"
        sfx = chr(65) * 24
        full = pre + sfx
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "ok.txt", "hello\n", "base")
            _git(tmp, "checkout", "-b", "side")
            _commit(tmp, "side.txt", "side\n", "side work")
            _git(tmp, "checkout", "main")
            _commit(tmp, "main.txt", "main\n", "main work")
            _git(tmp, "merge", "--no-ff", "--no-commit", "side")
            p = Path(tmp) / "merged.txt"
            p.write_text("v=" + full + "\n", encoding="utf-8")
            _git(tmp, "add", "merged.txt")
            _git(tmp, "commit", "-m", "merge side")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(sfx, out)
            self.assertNotIn(full, out)

    def test_bad_repo_fails_closed(self) -> None:
        """Bad repo exits nonzero with FAIL and no traceback."""
        r = subprocess.run(["python3", str(_root() / "guard" / "historyscan.py"), "--repo", "/nonexistent-x"], capture_output=True, text=True)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FAIL", out)
        self.assertNotIn("Traceback", out)

    def test_external_diff_not_executed(self) -> None:
        """Hostile diff driver config is not executed by scan."""
        marker = Path(tempfile.gettempdir()) / "hs-marker-x"
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "ok.txt", "hello\n", "hello")
            _git(tmp, "config", "diff.external", "touch " + str(marker))
            if marker.exists():
                marker.unlink()
            code, out = _scan(tmp)
            self.assertEqual(code, 0)
            self.assertIn("PASS", out)
            self.assertFalse(marker.exists())

    def test_binary_blob_found(self) -> None:
        """NUL blob content is diffed as text and found."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            p = Path(tmp) / "b.bin"
            p.write_bytes(b"\x00" + full.encode() + b"\n")
            _git(tmp, "add", "b.bin")
            _git(tmp, "commit", "-m", "bin")
            p.write_bytes(b"clean\n")
            _git(tmp, "add", "b.bin")
            _git(tmp, "commit", "-m", "clean")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(sfx, out)

    def test_plusplus_line_found(self) -> None:
        """Added line starting with ++ is not a header."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "a.txt", "++" + full + "\n", "one")
            p = Path(tmp) / "a.txt"
            p.write_text("clean\n", encoding="utf-8")
            _git(tmp, "add", "a.txt")
            _git(tmp, "commit", "-m", "two")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(sfx, out)

    def test_colour_config_found(self) -> None:
        """Colourised git output is still parsed."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "a.txt", "v=" + full + "\n", "one")
            p = Path(tmp) / "a.txt"
            p.write_text("clean\n", encoding="utf-8")
            _git(tmp, "add", "a.txt")
            _git(tmp, "commit", "-m", "two")
            env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "color.ui", "GIT_CONFIG_VALUE_0": "always"}
            code, out = _scan(tmp, env)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(sfx, out)

    def test_message_body_found(self) -> None:
        """Token in commit message body is flagged."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "ok.txt", "hello\n", "subject\n\nbody " + full)
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(sfx, out)

    def test_secret_path_redacted(self) -> None:
        """Credential inside filename is redacted in output."""
        full, sfx = _tok()
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, full + ".pem", "ok\n", "add key")
            p = Path(tmp) / (full + ".pem")
            p.unlink()
            _git(tmp, "add", "-A")
            _git(tmp, "commit", "-m", "drop key")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)
            self.assertNotIn(full, out)
            self.assertNotIn(sfx, out)

    def test_private_path_found(self) -> None:
        """Guard private path fragment in content is flagged."""
        val = "/Us" + "ers/jane/x"
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "note.txt", "see " + val + "\n", "note")
            code, out = _scan(tmp)
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)

    def test_worktree_pass(self) -> None:
        """Worktree with .git file passes repo check."""
        with tempfile.TemporaryDirectory() as tmp:
            self._new(tmp)
            _commit(tmp, "ok.txt", "hello\n", "hello")
            with tempfile.TemporaryDirectory() as tmp2:
                wt = os.path.join(tmp2, "wt")
                _git(tmp, "worktree", "add", wt)
                r = subprocess.run(["python3", str(_root() / "guard" / "historyscan.py"), "--repo", wt], capture_output=True, text=True)
                out = r.stdout + r.stderr
                self.assertEqual(r.returncode, 0)
                self.assertIn("PASS", out)


if __name__ == "__main__":
    unittest.main()
