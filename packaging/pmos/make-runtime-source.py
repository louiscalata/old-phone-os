#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the declared runtime package input; the full source handoff is separate."""
import argparse
import gzip
import hashlib
import os
import re
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def regular_source(root, relative):
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("source symlink is not permitted: " + relative)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("source must be a regular file: " + relative)
    return path


def runtime_paths(root):
    inventory = regular_source(root, "packaging/pmos/runtime-files.txt")
    paths = []
    for line in inventory.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = PurePosixPath(line).parts
        if (str(PurePosixPath(line)) != line
                or not re.fullmatch(r"src/opos/[A-Za-z0-9_./-]+", line)
                or any(p in (".", "..", "__pycache__") for p in parts)
                or line.endswith((".pyc", ".pyo"))
                or any(p.startswith("._") for p in parts)):
            raise ValueError("unsafe runtime inventory path")
        paths.append(line)
    if not paths or paths != sorted(set(paths)):
        raise ValueError("runtime inventory must be nonempty, unique and sorted")
    for path in paths:
        regular_source(root, path)
    return paths


def create_archive(root, output, archive_root):
    root = root.resolve(strict=True)
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", archive_root)
            or archive_root in (".", "..")):
        raise ValueError("unsafe archive root")
    paths = runtime_paths(root) + ["LICENSE", "THIRD_PARTY.md"]
    for name in paths:
        regular_source(root, name)
    if output.resolve().is_relative_to(root):
        raise ValueError("archive output must be outside the source tree")
    directories = {archive_root}
    for name in paths:
        parent = PurePosixPath(archive_root, name).parent
        while str(parent) != ".":
            directories.add(str(parent))
            parent = parent.parent
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".opos-runtime-", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as raw:
            with (
                gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                              compresslevel=9, mtime=0) as compressed,
                tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as tar,
            ):
                members = {name: None for name in directories}
                members.update({str(PurePosixPath(archive_root, name)): name for name in paths})
                for name, relative in sorted(members.items()):
                    info = tarfile.TarInfo(name)
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    if relative is None:
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        tar.addfile(info)
                    else:
                        source = regular_source(root, relative)
                        info.mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
                        with source.open("rb") as stream:
                            info.size = os.fstat(stream.fileno()).st_size
                            tar.addfile(info, stream)
            os.fchmod(raw.fileno(), 0o644)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive-root", default="old-phone-os-source")
    args = parser.parse_args()
    try:
        paths = create_archive(args.root, args.output, args.archive_root)
    except (ValueError, OSError) as exc:
        parser.exit(1, "make-runtime-source: " + str(exc) + "\n")
    data = args.output.read_bytes()
    print("runtime-source: " + str(len(paths) - 2) + " runtime files; "
          + hashlib.sha512(data).hexdigest() + "  " + args.output.name)


if __name__ == "__main__":
    main()
