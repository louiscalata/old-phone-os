# SPDX-License-Identifier: GPL-3.0-or-later
"""Stream raw ext4 to Android sparse format and verify its hash."""
import hashlib
import os
import struct
import sys

_MAGIC = 0xed26ff3a
_MAJOR = 1
_MINOR = 0
_FILE_HDR_SZ = 28
_CHUNK_HDR_SZ = 12
_BLK_SZ = 4096
_RAW = 0xCAC1
_FILL = 0xCAC2
_DONT_CARE = 0xCAC3
_CRC32 = 0xCAC4
_MAX_RAW_BLOCKS = 1048575
_HDR_FMT = "<IHHHHIIII"
_CHUNK_FMT = "<HHII"
_ZERO_BLOCK = b"\x00" * 4096
_ZERO_FILL = b"\x00" * 4
_BOUND = 65536


def _read_exact(f, n):
    buf = f.read(n)
    if len(buf) != n:
        raise ValueError("truncated input")
    return buf


def convert(raw_path, sparse_path):
    size = os.path.getsize(raw_path)
    if size % _BLK_SZ != 0:
        raise ValueError("raw size not multiple of 4096")
    total_blocks = size // _BLK_SZ
    if total_blocks == 0:
        with open(sparse_path, "wb") as out:
            out.write(struct.pack(_HDR_FMT, _MAGIC, _MAJOR, _MINOR, _FILE_HDR_SZ, _CHUNK_HDR_SZ, _BLK_SZ, 0, 0, 0))
        return (0, 0)
    total_chunks = 0
    with open(raw_path, "rb") as inp, open(sparse_path, "w+b") as out:
        out.write(b"\x00" * _FILE_HDR_SZ)
        run_kind = None
        run_blocks = 0
        raw_pos = 0
        while True:
            blk = inp.read(_BLK_SZ)
            if not blk:
                break
            if len(blk) != _BLK_SZ:
                raise ValueError("truncated input")
            if blk == _ZERO_BLOCK:
                if run_kind == "fill":
                    run_blocks += 1
                elif run_kind == "raw":
                    end = out.tell()
                    out.seek(raw_pos)
                    out.write(struct.pack(_CHUNK_FMT, _RAW, 0, run_blocks, _CHUNK_HDR_SZ + run_blocks * _BLK_SZ))
                    out.seek(end)
                    run_kind = "fill"
                    run_blocks = 1
                else:
                    run_kind = "fill"
                    run_blocks = 1
            else:
                if run_kind == "raw":
                    if run_blocks >= _MAX_RAW_BLOCKS:
                        end = out.tell()
                        out.seek(raw_pos)
                        out.write(struct.pack(_CHUNK_FMT, _RAW, 0, run_blocks, _CHUNK_HDR_SZ + run_blocks * _BLK_SZ))
                        out.seek(end)
                        raw_pos = out.tell()
                        out.write(b"\x00" * _CHUNK_HDR_SZ)
                        total_chunks += 1
                        run_blocks = 1
                        out.write(blk)
                    else:
                        run_blocks += 1
                        out.write(blk)
                elif run_kind == "fill":
                    out.write(struct.pack(_CHUNK_FMT, _FILL, 0, run_blocks, _CHUNK_HDR_SZ + 4))
                    out.write(_ZERO_FILL)
                    total_chunks += 1
                    raw_pos = out.tell()
                    out.write(b"\x00" * _CHUNK_HDR_SZ)
                    total_chunks += 1
                    run_kind = "raw"
                    run_blocks = 1
                    out.write(blk)
                else:
                    raw_pos = out.tell()
                    out.write(b"\x00" * _CHUNK_HDR_SZ)
                    total_chunks += 1
                    run_kind = "raw"
                    run_blocks = 1
                    out.write(blk)
        if run_kind == "raw":
            end = out.tell()
            out.seek(raw_pos)
            out.write(struct.pack(_CHUNK_FMT, _RAW, 0, run_blocks, _CHUNK_HDR_SZ + run_blocks * _BLK_SZ))
            out.seek(end)
        elif run_kind == "fill":
            out.write(struct.pack(_CHUNK_FMT, _FILL, 0, run_blocks, _CHUNK_HDR_SZ + 4))
            out.write(_ZERO_FILL)
            total_chunks += 1
        out.seek(0)
        out.write(struct.pack(_HDR_FMT, _MAGIC, _MAJOR, _MINOR, _FILE_HDR_SZ, _CHUNK_HDR_SZ, _BLK_SZ, total_blocks, total_chunks, 0))
    return (total_blocks, total_chunks)


def check(raw_path, sparse_path):
    raw_hash = hashlib.sha256()
    with open(raw_path, "rb") as f:
        while True:
            data = f.read(_BOUND)
            if not data:
                break
            raw_hash.update(data)
    raw_size = os.path.getsize(raw_path)
    exp_hash = hashlib.sha256()
    with open(sparse_path, "rb") as f:
        hdr = f.read(_FILE_HDR_SZ)
        if len(hdr) != _FILE_HDR_SZ:
            raise ValueError("truncated header")
        magic, major, minor, fhs, chs, blk_sz, total_blks, total_chunks, _crc = struct.unpack(_HDR_FMT, hdr)
        if magic != _MAGIC or major != _MAJOR or minor != _MINOR or fhs != _FILE_HDR_SZ or chs != _CHUNK_HDR_SZ or blk_sz != _BLK_SZ:
            raise ValueError("unsupported header")
        expanded = 0
        for _ in range(total_chunks):
            chdr = f.read(_CHUNK_HDR_SZ)
            if len(chdr) != _CHUNK_HDR_SZ:
                raise ValueError("truncated chunk header")
            ctype, _rsv, chunk_sz, total_sz = struct.unpack(_CHUNK_FMT, chdr)
            if ctype == _RAW:
                if total_sz != _CHUNK_HDR_SZ + chunk_sz * _BLK_SZ:
                    raise ValueError("bad raw chunk size")
                remaining = chunk_sz * _BLK_SZ
                while remaining > 0:
                    want = _BOUND if remaining > _BOUND else remaining
                    data = f.read(want)
                    if not data:
                        raise ValueError("truncated raw payload")
                    exp_hash.update(data)
                    remaining -= len(data)
                expanded += chunk_sz
            elif ctype == _FILL:
                if total_sz != _CHUNK_HDR_SZ + 4:
                    raise ValueError("bad fill chunk size")
                val = _read_exact(f, 4)
                remaining = chunk_sz * _BLK_SZ
                while remaining > 0:
                    n = _BOUND if remaining > _BOUND else remaining
                    exp_hash.update((val * ((n + 3) // 4))[:n])
                    remaining -= n
                expanded += chunk_sz
            elif ctype == _DONT_CARE:
                if total_sz != _CHUNK_HDR_SZ:
                    raise ValueError("bad dontcare chunk size")
                remaining = chunk_sz * _BLK_SZ
                while remaining > 0:
                    n = _BOUND if remaining > _BOUND else remaining
                    exp_hash.update(b"\x00" * n)
                    remaining -= n
                expanded += chunk_sz
            elif ctype == _CRC32:
                if total_sz != _CHUNK_HDR_SZ + 4:
                    raise ValueError("bad crc chunk size")
                _read_exact(f, 4)
            else:
                raise ValueError("unknown chunk type")
    raw_hex = raw_hash.hexdigest()
    exp_hex = exp_hash.hexdigest()
    print(raw_hex)
    print(exp_hex)
    if raw_hex != exp_hex:
        return False
    if expanded != total_blks:
        return False
    if raw_size != total_blks * _BLK_SZ:
        return False
    return True


def main(argv):
    if len(argv) != 3 or argv[0] not in ("convert", "check"):
        print("usage: sparse_image.py convert RAW SPARSE | check RAW SPARSE", file=sys.stderr)
        return 2
    try:
        if argv[0] == "convert":
            convert(argv[1], argv[2])
            return 0
        ok = check(argv[1], argv[2])
        if ok:
            return 0
        print("hashes differ: do not flash", file=sys.stderr)
        return 1
    except ValueError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
