# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for deploy/sparse_image.py."""
import contextlib
import hashlib
import importlib.util
import io
import struct
from pathlib import Path
import tempfile
import unittest


_REPO = Path(__file__).resolve().parents[1]
_MOD_PATH = _REPO / "deploy" / "sparse_image.py"
_spec = importlib.util.spec_from_file_location("sparse_image", _MOD_PATH)
sparse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sparse)


def _blk(val):
    return bytes((val,)) * 4096


def _run_cli(args):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = sparse.main(args)
    return status, out.getvalue(), err.getvalue()


def _check_quiet(raw, sp):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = sparse.check(raw, sp)
    return ok, out.getvalue()


def _header(sp):
    with open(sp, "rb") as f:
        data = f.read(28)
    return struct.unpack("<IHHHHIIII", data)


def _chunks(sp):
    found = []
    with open(sp, "rb") as f:
        f.read(28)
        while True:
            h = f.read(12)
            if not h:
                break
            ctype, _rsv, csz, tsz = struct.unpack("<HHII", h)
            found.append((ctype, csz, tsz))
            if ctype == 0xCAC1:
                f.seek(csz * 4096, 1)
            elif ctype in (0xCAC2, 0xCAC4):
                f.seek(4, 1)
    return found


class TestSparseImage(unittest.TestCase):
    def test_convert_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = str(Path(tmp) / "raw.img")
            sp = str(Path(tmp) / "out.sp")
            with open(raw, "wb") as f:
                f.write(_blk(65))
                f.write(b"\x00" * 8192)
                f.write(_blk(66))
            res = sparse.convert(raw, sp)
            self.assertEqual(res, (4, 3))
            with open(sp, "rb") as f:
                data = f.read()
            self.assertEqual(len(data), 8260)
            self.assertEqual(struct.unpack("<IHHHHIIII", data[0:28]), (0xed26ff3a, 1, 0, 28, 12, 4096, 4, 3, 0))
            self.assertEqual(struct.unpack("<HHII", data[28:40]), (0xCAC1, 0, 1, 4108))
            self.assertEqual(struct.unpack("<HHII", data[4136:4148]), (0xCAC2, 0, 2, 16))
            self.assertEqual(struct.unpack("<HHII", data[4152:4164]), (0xCAC1, 0, 1, 4108))
            self.assertEqual(data[40:4136], _blk(65))
            self.assertEqual(data[4148:4152], b"\x00" * 4)
            self.assertEqual(data[4164:8260], _blk(66))
    def test_round_trip_and_flipped_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = str(Path(tmp) / "raw.img")
            sp = str(Path(tmp) / "out.sp")
            with open(raw, "wb") as f:
                f.write(_blk(65))
                f.write(b"\x00" * 4096)
                f.write(b"\x00" * 4096)
                f.write(_blk(66))
            res = sparse.convert(raw, sp)
            self.assertEqual(res, (4, 3))
            magic, major, minor, fhs, chs, bsz, nblk, nchk, _crc = _header(sp)
            self.assertEqual((magic, major, minor, fhs, chs, bsz, nblk, nchk), (0xed26ff3a, 1, 0, 28, 12, 4096, 4, 3))
            self.assertEqual(_chunks(sp), [(0xCAC1, 1, 12 + 4096), (0xCAC2, 2, 16), (0xCAC1, 1, 12 + 4096)])
            ok, out = _check_quiet(raw, sp)
            self.assertTrue(ok)
            with open(raw, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            self.assertIn(digest, out)
            with open(sp, "rb") as f:
                data = bytearray(f.read())
            data[-1] ^= 1
            with open(sp, "wb") as f:
                f.write(data)
            ok2, _ = _check_quiet(raw, sp)
            self.assertFalse(ok2)
            status, _, err = _run_cli(["check", raw, sp])
            self.assertEqual(status, 1)
            self.assertIn("hashes differ: do not flash", err)

    def test_rejects_size_not_multiple_of_4096(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = str(Path(tmp) / "raw.img")
            sp = str(Path(tmp) / "out.sp")
            with open(raw, "wb") as f:
                f.write(bytes((7,)) * 4097)
            with self.assertRaises(ValueError):
                sparse.convert(raw, sp)
            self.assertFalse(Path(sp).exists())
            status, _, _ = _run_cli(["convert", raw, sp])
            self.assertEqual(status, 2)

    def test_empty_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = str(Path(tmp) / "raw.img")
            sp = str(Path(tmp) / "out.sp")
            with open(raw, "wb") as f:
                pass
            self.assertEqual(sparse.convert(raw, sp), (0, 0))
            magic, major, minor, fhs, chs, bsz, nblk, nchk, _crc = _header(sp)
            self.assertEqual((nblk, nchk), (0, 0))
            self.assertEqual((magic, major, minor, fhs, chs, bsz), (0xed26ff3a, 1, 0, 28, 12, 4096))
            ok, _ = _check_quiet(raw, sp)
            self.assertTrue(ok)

    def test_cli_convert_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = str(Path(tmp) / "raw.img")
            sp = str(Path(tmp) / "out.sp")
            with open(raw, "wb") as f:
                f.write(_blk(67))
                f.write(b"\x00" * 4096)
            status, _, _ = _run_cli(["convert", raw, sp])
            self.assertEqual(status, 0)
            status, out, _ = _run_cli(["check", raw, sp])
            self.assertEqual(status, 0)
            self.assertEqual(len(out.strip().split()), 2)

    def test_bad_usage(self):
        bad = [[], ["convert"], ["convert", "a"], ["convert", "a", "b", "c"], ["bogus", "a", "b"], ["check", "only-one"]]
        for args in bad:
            status, _, err = _run_cli(args)
            self.assertEqual(status, 2, args)
            self.assertTrue(err.strip())

    def test_wrong_magic_truncation_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = str(Path(tmp) / "raw.img")
            sp = str(Path(tmp) / "out.sp")
            with open(raw, "wb") as f:
                f.write(_blk(68))
            sparse.convert(raw, sp)
            with open(sp, "rb") as f:
                good = f.read()
            with open(sp, "wb") as f:
                f.write(struct.pack("<I", 0x0) + good[4:])
            with self.assertRaises(ValueError):
                _check_quiet(raw, sp)[0] and None or sparse.check(raw, sp)
            status, _, _ = _run_cli(["check", raw, sp])
            self.assertEqual(status, 2)
            with open(sp, "wb") as f:
                f.write(good[:20])
            with self.assertRaises(ValueError):
                sparse.check(raw, sp)
            status, _, _ = _run_cli(["check", raw, sp])
            self.assertEqual(status, 2)
            with open(sp, "wb") as f:
                f.write(struct.pack("<IHHHHIIII", 0xed26ff3a, 1, 0, 28, 12, 4096, 1, 1, 0))
                f.write(struct.pack("<HHII", 0xCACA, 0, 1, 12 + 4096))
                f.write(b"\x00" * 4096)
            with self.assertRaises(ValueError):
                sparse.check(raw, sp)
            status, _, _ = _run_cli(["check", raw, sp])
            self.assertEqual(status, 2)
            with open(sp, "wb") as f:
                f.write(good[:28 + 12 + 100])
            with self.assertRaises(ValueError):
                sparse.check(raw, sp)

    def test_hand_built_fill_dontcare_crc32(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = str(Path(tmp) / "raw.img")
            sp = str(Path(tmp) / "out.sp")
            val = bytes((65, 66, 67, 68))
            expanded_fill = val * 1024
            with open(raw, "wb") as f:
                f.write(expanded_fill)
                f.write(b"\x00" * 4096)
            with open(sp, "wb") as f:
                f.write(struct.pack("<IHHHHIIII", 0xed26ff3a, 1, 0, 28, 12, 4096, 2, 3, 0))
                f.write(struct.pack("<HHII", 0xCAC2, 0, 1, 16))
                f.write(val)
                f.write(struct.pack("<HHII", 0xCAC3, 0, 1, 12))
                f.write(struct.pack("<HHII", 0xCAC4, 0, 0, 16))
                f.write(bytes((0,)) * 4)
            ok, out = _check_quiet(raw, sp)
            self.assertTrue(ok)
            with open(raw, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            self.assertIn(digest, out)
            status, _, _ = _run_cli(["check", raw, sp])
            self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
