#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refresh or check the release recipe checksum from an explicit runtime tarball."""
import argparse
import hashlib
import re
from pathlib import Path


def update_checksum(recipe, archive, check=False):
    text = recipe.read_text(encoding="utf-8")
    declarations = re.findall(
        r"(?m)^[ \t]*(?:(?:export|readonly)[ \t]+)?"
        r"_source_archive[ \t]*(?:\+)?=.*$", text)
    match = re.fullmatch(r'_source_archive="([A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz)"',
                         declarations[0]) if len(declarations) == 1 else None
    if match is None:
        raise ValueError("invalid literal _source_archive")
    if archive.name != match[1]:
        raise ValueError("archive filename must match _source_archive")
    declarations = re.findall(
        r"(?m)^[ \t]*(?:(?:export|readonly)[ \t]+)?sha512sums[ \t]*(?:\+)?=", text)
    block = re.search(r'(?m)^sha512sums="\n([^"]*)^"$', text)
    if len(declarations) != 1 or block is None:
        raise ValueError("invalid sha512sums block")
    tokens = block[1].split()
    if (len(tokens) % 2 or tokens[1::2].count("$_source_archive") != 1
            or not all(re.fullmatch(r"[0-9a-f]{128}", d) for d in tokens[::2])):
        raise ValueError("invalid source archive checksum entry")
    entry = re.search(r"(?m)^([0-9a-f]{128})([ \t]+\$_source_archive)$", block[1])
    if entry is None:
        raise ValueError("invalid source archive checksum entry")
    digest = hashlib.sha512(archive.read_bytes()).hexdigest()
    if check:
        if entry[1] != digest:
            raise ValueError("source archive checksum does not match " + archive.name)
    elif entry[1] != digest:
        start = block.start(1) + entry.start(1)
        end = block.start(1) + entry.end(1)
        recipe.write_text(text[:start] + digest + text[end:], encoding="utf-8")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="exact release tarball to measure")
    parser.add_argument("--apkbuild", type=Path,
                        default=Path(__file__).resolve().parent / "opos/APKBUILD")
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()
    try:
        digest = update_checksum(args.apkbuild, args.archive, args.check)
    except (ValueError, OSError) as exc:
        parser.exit(1, "update-runtime-checksum: " + str(exc) + "\n")
    print(digest + "  " + args.archive.name)


if __name__ == "__main__":
    main()
