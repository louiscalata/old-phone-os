# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "packaging/pmos/qemu-demo.sh"


def _run(args, extra_env):
    env = dict(os.environ)
    env["OPOS_APORTS_DIR"] = ""
    env["OPOS_QEMU_DEVICE"] = ""
    env.update(extra_env)
    return subprocess.run(["/bin/sh", str(SCRIPT)] + args, check=False, capture_output=True,
                          text=True, timeout=10, env=env)


class TestQemuDemo(unittest.TestCase):
    def test_help_and_unknown_arguments(self):
        result = _run(["--help"], {})
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "usage: qemu-demo.sh [--help]\n", ""))
        for args in (["--run"], ["--unknown"], ["--help", "extra"]):
            with self.subTest(args=args):
                result = _run(args, {})
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, "", "usage: qemu-demo.sh [--help]\n"))

    def test_plan_stages_locally_before_init_checksum_and_build(self):
        with tempfile.TemporaryDirectory() as directory:
            result = _run([], {"OPOS_APORTS_DIR": directory})
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            commands = [line.split(". ", 1)[1] for line in result.stdout.splitlines()]
            self.assertEqual(len(commands), 11)
            self.assertEqual(shlex.split(commands[0]), ["COPYFILE_DISABLE=1", "OPOS_LOCAL_SOURCE=1", "sh",
                                                     "packaging/pmos/stage-aports.sh", directory])
            self.assertEqual(shlex.split(commands[1]), ["pmbootstrap", "config", "aports", directory])
            self.assertEqual(commands[2:9], [
                "pmbootstrap config device qemu-aarch64", "pmbootstrap config ui opos",
                "pmbootstrap init", "pmbootstrap checksum opos postmarketos-ui-opos",
                "pmbootstrap build opos", "pmbootstrap build postmarketos-ui-opos",
                "pmbootstrap install",
            ])
            self.assertNotIn("--src", result.stdout)
            self.assertEqual(commands[9], "pmbootstrap qemu --image-size=2G -m 2048 --no-gl")

    def test_ssh_uses_ipv4_loopback_and_plan_has_no_flashing_command(self):
        result = _run([], {})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "11. ssh -p 2222 user@127.0.0.1")
        self.assertNotIn("flasher", result.stdout)
        self.assertNotIn("--disk", result.stdout)

    def test_device_override_is_limited_to_qemu(self):
        result = _run([], {"OPOS_QEMU_DEVICE": "qemu-amd64"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[2], "3. pmbootstrap config device qemu-amd64")
        for device in ("real-phone", "dev$(true)", "--disk"):
            with self.subTest(device=device):
                rejected = _run([], {"OPOS_QEMU_DEVICE": device})
                self.assertEqual(rejected.returncode, 2)
                self.assertFalse(rejected.stdout)

    def test_path_metacharacters_are_printed_as_one_quoted_argument(self):
        path = "/srv/a b/$X/it's-a-checkout"
        result = _run([], {"OPOS_APORTS_DIR": path})
        self.assertEqual(result.returncode, 0, result.stderr)
        command = result.stdout.splitlines()[0].split(". ", 1)[1]
        self.assertEqual(shlex.split(command)[-1], path)
        self.assertIn(shlex.quote(path), command)

    def test_empty_environment_uses_home_defaults(self):
        with tempfile.TemporaryDirectory() as home:
            result = _run([], {"HOME": home})
            self.assertEqual(result.returncode, 0, result.stderr)
            command = result.stdout.splitlines()[0].split(". ", 1)[1]
            self.assertEqual(shlex.split(command)[-1], home + "/opos-pmaports")

    def test_no_sudo(self):
        self.assertNotIn("sudo", SCRIPT.read_text())
