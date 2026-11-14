# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated PATH for installer tests: device commands are always local stubs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


class InstallStubs:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.device = uuid.uuid4().hex  # Generated fixture, never a physical device ID.
        self.env = dict(os.environ, PATH=str(self.bin), STUB_LOG=str(self.log),
                        STUB_DEVICE=self.device, STUB_PRODUCT="product: surfaceduo\n")
        # No fallback to the host PATH, even if an installer starts using another tool.
        for name in ("sh", "dirname", "tr", "cut", "wc", "awk", "sed", "od",
                     "sha256sum", "shasum"):
            target = shutil.which(name)
            if target:
                (self.bin / name).symlink_to(target)
        source = '''import hashlib, json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if args[:1] == ["-s"]:
    if args[1] != os.environ["STUB_DEVICE"]:
        sys.exit(9)
    args = args[2:]
if " ".join(args) == os.environ.get("STUB_FAIL"):
    sys.exit(7)
if " ".join(args) == os.environ.get("STUB_REPLACE_ON"):
    # Simulates a local image that changes while a slow device command runs.
    pathlib.Path(os.environ["STUB_REPLACE_PATH"]).write_bytes(b"replaced image contents\\n")
if name == "fastboot":
    if args == ["devices"]:
        sys.stdout.write(os.environ.get("STUB_DEVICES", os.environ["STUB_DEVICE"] + "\\tfastboot\\n"))
    elif args == ["getvar", "product"]:
        sys.stderr.write(os.environ["STUB_PRODUCT"])
elif name == "adb":
    if args[:2] == ["shell", "sha256sum"]:
        print(os.environ.get("STUB_HASH", os.environ["ROOTFS_HASH"]) + "  /data/rootfs.img")
    elif args[:2] == ["shell", "wc"]:
        print(os.environ["ROOTFS_SIZE"] + " /data/rootfs.img")
elif name == "pmbootstrap":
    sys.exit(8)  # The old, unrelated build must never be flashed.
'''
        for name in ("adb", "fastboot", "pmbootstrap"):
            self.write_tool(name, f"#!{sys.executable}\n" + source)

    def write_tool(self, name, text):
        path = self.bin / name
        if path.is_symlink():
            path.unlink()
        path.write_text(text)
        path.chmod(0o755)

    def image(self, name, contents=b"fixture image contents\n"):
        path = self.root / name
        path.write_bytes(contents)
        return path

    def run(self, script, args, answer=""):
        return subprocess.run(["/bin/sh", str(script), *map(str, args)],
                              input=answer, capture_output=True, text=True,
                              env=self.env, cwd=self.root, timeout=10)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def commands(self):
        return [[call[0], *(call[3:] if call[1:2] == ["-s"] else call[1:])]
                for call in self.calls()]

    def set_rootfs(self, path):
        self.env["ROOTFS_HASH"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.env["ROOTFS_SIZE"] = str(path.stat().st_size)

    def close(self):
        self.tmp.cleanup()
