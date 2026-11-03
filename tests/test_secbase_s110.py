# SPDX-License-Identifier: GPL-3.0-or-later
"""SECBASE: S110 removal keeps notices._build fail-open and prefs.save bytes/cleanup."""
import builtins
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from opos import notices
from opos.setup import prefs

# Frozen prefs.save output for dict(z="\u00e9", a=[True, None]): sorted, indent=2, ASCII, final newline.
EXPECTED = b'\x7b\n  "a": [\n    true,\n    null\n  ],\n  "z": "\\u00e9"\n\x7d\n'


def _build_with(failure=None, metadata=None, env="abcdef0"):
    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "opos" and fromlist and "_build_info" in fromlist:
            if failure is not None:
                raise failure
            return types.SimpleNamespace(_build_info=metadata)
        return real(name, globals, locals, fromlist, level)

    with mock.patch.dict(os.environ, OPOS_BUILD=env), \
            mock.patch("builtins.__import__", side_effect=fake):
        return notices._build()


class TestNoticesBuildFailOpen(unittest.TestCase):
    def test_any_metadata_failure_falls_back_to_env(self):
        for err in (ImportError("absent"), OSError("unreadable"), RuntimeError("broken")):
            with self.subTest(err=type(err).__name__):
                self.assertEqual(_build_with(failure=err), "abcdef0")

    def test_metadata_still_wins_when_present(self):
        self.assertEqual(_build_with(metadata=types.SimpleNamespace(BUILD="1234567")), "1234567")


class TestPrefsSave(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "nested" / "prefs.json"

    def test_bytes_frozen(self):
        for _ in range(2):
            prefs.save(dict(z="\u00e9", a=[True, None]), self.path)
            self.assertEqual(self.path.read_bytes(), EXPECTED)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_replace_failure_reraises_original_and_cleans_temp(self):
        boom = OSError("replace failed")
        with mock.patch.object(prefs.os, "replace", side_effect=boom):
            with self.assertRaises(OSError) as cm:
                prefs.save(dict(ok=True), self.path)
        self.assertIs(cm.exception, boom)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_vanished_temp_does_not_mask_original(self):
        boom = OSError("replace failed")
        with mock.patch.object(prefs.os, "replace", side_effect=boom), \
                mock.patch.object(prefs.os, "unlink", side_effect=FileNotFoundError("gone")):
            with self.assertRaises(OSError) as cm:
                prefs.save(dict(ok=True), self.path)
        self.assertIs(cm.exception, boom)

    def test_cleanup_programming_error_propagates(self):
        boom = OSError("replace failed")
        with mock.patch.object(prefs.os, "replace", side_effect=boom), \
                mock.patch.object(prefs.os, "unlink", side_effect=RuntimeError("cleanup bug")):
            with self.assertRaisesRegex(RuntimeError, "^cleanup bug$") as cm:
                prefs.save(dict(ok=True), self.path)
        self.assertIs(cm.exception.__context__, boom)

    def _final_chmod_raises(self, exc):
        real = os.chmod
        calls = []

        def chmod(p, mode):
            calls.append(mode)
            if Path(p) == self.path:
                raise exc
            return real(p, mode)
        return mock.patch.object(prefs.os, "chmod", side_effect=chmod), calls

    def test_final_chmod_oserror_is_best_effort(self):
        patch, calls = self._final_chmod_raises(PermissionError("no modes on FAT"))
        with patch:
            self.assertIsNone(prefs.save(dict(ok=True), self.path))
        self.assertEqual(calls, [0o600, 0o600])
        self.assertEqual(json.loads(self.path.read_bytes()), dict(ok=True))

    def test_final_chmod_programming_error_propagates(self):
        patch, _ = self._final_chmod_raises(RuntimeError("unexpected"))
        with patch, self.assertRaisesRegex(RuntimeError, "^unexpected$"):
            prefs.save(dict(ok=True), self.path)
        self.assertEqual(json.loads(self.path.read_bytes()), dict(ok=True))


if __name__ == "__main__":
    unittest.main()
