# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated PATH for installer tests: device commands are always local stubs."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


class InstallStubs:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.booted_log = self.root / "booted.jsonl"
        # Installers that make private copies make them here, inside the fixture.
        self.private = self.root / "private"
        self.private.mkdir()
        self.device = uuid.uuid4().hex  # Generated fixture, never a physical device ID.
        self.env = dict(
            os.environ,
            PATH=str(self.bin),
            STUB_LOG=str(self.log),
            STUB_BOOTED=str(self.booted_log),
            TMPDIR=str(self.private),
            STUB_DEVICE=self.device,
            STUB_PRODUCT="product: surfaceduo\n",
        )
        # No fallback to the host PATH, even if an installer starts using another tool.
        for name in (
            "sh",
            "dirname",
            "tr",
            "cut",
            "wc",
            "awk",
            "sed",
            "od",
            "cp",
            "mktemp",
            "chmod",
            "rm",
            "rmdir",
            "sha256sum",
            "shasum",
        ):
            target = shutil.which(name)
            if target:
                if sys.platform == "darwin" and name == "shasum":
                    script = self.bin / name
                    script.write_text(f'#!/bin/sh\nexec {target} "$@"\n')
                    script.chmod(0o755)
                else:
                    (self.bin / name).symlink_to(target)
        source = """import glob, hashlib, json, os, pathlib, sys
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
    # A pattern with * reaches a private copy whose folder name is not known ahead.
    target = os.environ["STUB_REPLACE_PATH"]
    for path in (glob.glob(target) if "*" in target else [target]):
        if os.path.exists(path):
            os.chmod(path, 0o600)  # The same user can always do this to a read-only copy.
        pathlib.Path(path).write_bytes(b"replaced image contents\\n")
if name == "fastboot":
    if args == ["devices"]:
        sys.stdout.write(os.environ.get("STUB_DEVICES", os.environ["STUB_DEVICE"] + "\\tfastboot\\n"))
    elif args == ["getvar", "product"]:
        answer = os.environ["STUB_PRODUCT"]
        if "STUB_PRODUCT_AFTER_REBOOT" in os.environ:
            with open(os.environ["STUB_LOG"]) as log:
                if any(json.loads(line)[-2:] == ["reboot", "bootloader"] for line in log):
                    answer = os.environ["STUB_PRODUCT_AFTER_REBOOT"]
        sys.stderr.write(answer)
    elif args == ["getvar", "unlocked"]:
        sys.stderr.write(os.environ.get("STUB_UNLOCKED", "unlocked: yes\\n"))
    elif args == ["getvar", "current-slot"]:
        sys.stderr.write(os.environ.get("STUB_SLOT", "current-slot: a\\n"))
    elif args == ["getvar", "battery-level"]:
        sys.stderr.write(os.environ.get("STUB_BATTERY", "battery-level: 80\\n"))
    elif args[:1] == ["boot"] and len(args) == 2 and os.path.isfile(args[1]):
        # Record the bytes a real fastboot would send, and how the file is protected.
        image = pathlib.Path(args[1])
        with open(os.environ["STUB_BOOTED"], "a") as booted:
            booted.write(json.dumps({
                "path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "mode": image.stat().st_mode & 0o777,
                "folder_mode": image.parent.stat().st_mode & 0o777}) + "\\n")
elif name == "adb":
    if args[:2] == ["shell", "sha256sum"]:
        print(os.environ.get("STUB_HASH", os.environ["ROOTFS_HASH"]) + "  /data/rootfs.img")
    elif args[:2] == ["shell", "wc"]:
        print(os.environ["ROOTFS_SIZE"] + " /data/rootfs.img")
elif name == "pmbootstrap":
    sys.exit(8)  # The old, unrelated build must never be flashed.
"""
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
        return subprocess.run(
            ["/bin/sh", str(script), *map(str, args)],
            input=answer,
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self.root,
            timeout=10,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def booted(self):
        """What each `fastboot boot` was given: path, SHA-256 and file modes."""
        if not self.booted_log.exists():
            return []
        return [json.loads(line) for line in self.booted_log.read_text().splitlines()]

    def commands(self):
        return [[call[0], *(call[3:] if call[1:2] == ["-s"] else call[1:])] for call in self.calls()]

    def set_rootfs(self, path):
        self.env["ROOTFS_HASH"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.env["ROOTFS_SIZE"] = str(path.stat().st_size)

    def close(self):
        self.tmp.cleanup()
