# SPDX-License-Identifier: GPL-3.0-or-later
"""Run only the launcher-generation block and launcher, never Termux setup or device tools."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NVIDIA = "nvidia-aceinstruct-1.5b"
LLAMA = "llama-3.2-1b-instruct"


@unittest.skipUnless(
    os.name != "nt" and shutil.which("sh", path=os.defpath), "needs a POSIX host with sh in /bin or /usr/bin"
)
class TermuxModelId(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.drop = self.home / "drop"
        self.drop.mkdir()
        for name in ("bin", "models", "opos"):
            (self.home / name).mkdir()
        (self.home / "models/model.gguf").touch()
        stubs = {
            "llama-server": 'printf "%s\\n" "$@" > "$HOME/llama-args"\n',
            "python": 'printf "%s\\n" "${OPOS_LLAMA_ID-unset}" > "$HOME/python-id"\n',
            "pkill": "exit 0\n",
            "nohup": 'exec "$@"\n',  # the host's nohup is not under test, and macOS's needs a console
            "termux-wake-lock": "exit 0\n",
        }
        for name, body in stubs.items():
            path = self.home / "bin" / name
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(0o755)
        self.env = {
            "HOME": str(self.home),
            "PATH": str(self.home / "bin") + os.pathsep + os.defpath,
            "drop": str(self.drop),
        }

    def generate(self, value=None):
        if value is not None:
            (self.drop / "llama-id").write_text(value, encoding="utf-8")
        source = (ROOT / "deploy/termux-setup.sh").read_text(encoding="utf-8")
        # Extract only generation, excluding package installation, copying weights and automatic startup.
        start = source.find("llama_id=")
        if start < 0:
            start = source.index('cat > "$HOME/bin/opos-start"')
        block = source[start : source.index('chmod +x "$HOME/bin/opos-start"')]
        return subprocess.run(
            ["sh", "-eu", "-c", 'status() { printf "%s\\n" "$1"; };\n' + block],
            env=self.env,
            cwd=self.home,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def generate_ok(self, value=None):
        result = self.generate(value)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def launch(self, expected_alias, expected_id, override=None):
        env = dict(self.env)
        if override is not None:
            env["OPOS_LLAMA_ID"] = override
        script = (self.home / "bin/opos-start").read_text(encoding="utf-8")
        result = subprocess.run(
            ["sh", "-c", script + "\nwait\n"],
            env=env,
            cwd=self.home,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.home / "llama-args").read_text(encoding="utf-8").splitlines()
        self.assertEqual(args[args.index("--alias") + 1], expected_alias)
        self.assertEqual((self.home / "python-id").read_text(encoding="utf-8"), expected_id + "\n")

    def test_no_file_preserves_original_defaults(self):
        self.generate_ok()
        self.launch(LLAMA, "")

    def test_drop_id_survives_plain_restart(self):
        self.generate_ok(NVIDIA + "\n")
        (self.drop / "llama-id").unlink()
        self.launch(NVIDIA, NVIDIA)
        self.launch(NVIDIA, NVIDIA)

    def test_environment_wins_over_saved_id(self):
        self.generate_ok(NVIDIA + "\n")
        self.launch(LLAMA, LLAMA, override=LLAMA)
        self.launch(LLAMA, "", override="")

    def test_id_grammar_accepts_boundaries(self):
        for value in ("a", "0", "a" * 64, "0.model-1.5"):
            with self.subTest(value=value):
                self.generate_ok(value + "\n")
                self.launch(value, value)

    def test_invalid_drop_id_refuses_launcher_generation(self):
        # The id is written into shell text, so the validator is the only guard. Every shell-significant ASCII
        # character must be refused on its own, in the first, the middle and the last position, and so must an
        # uppercase letter or an underscore after the first character. Setup only generates the launcher, so a
        # value that is wrongly accepted shows up as a zero exit status and a launcher file, not as a side effect.
        hostile = "'\";&|\\`$(){}[]<>*?#!~%:@+=,^/ \t\r\x0b\x0c"
        values = ["", "a" * 65, ".model", "-model", "UPPER", "aB", "a_b", "a b", "é", "a\nb", "$(touch pwned)"]
        values.append("x'};exit;${y-")  # closes the quoted default and runs code if it is ever accepted
        for character in hostile:
            values += [character + "a", "a" + character + "b", "a" + character]
        launcher = self.home / "bin/opos-start"
        for value in values:
            with self.subTest(value=value):
                launcher.unlink(missing_ok=True)  # one wrongly accepted value must not fail every later one
                result = self.generate(value + "\n")
                self.assertNotEqual(result.returncode, 0, f"the validator accepted {value!r}")
                self.assertIn("failed: invalid llama-id", result.stdout)
                self.assertFalse(launcher.exists(), f"a launcher was written for {value!r}")

    def test_launcher_stops_when_it_cannot_enter_the_opos_folder(self):
        self.generate_ok(NVIDIA + "\n")
        (self.home / "opos").rmdir()
        (self.home / "models/model.gguf").unlink()  # no background model server may outlive the test
        script = (self.home / "bin/opos-start").read_text(encoding="utf-8")
        result = subprocess.run(
            ["sh", "-c", script + "\nwait\n"],
            env=self.env,
            cwd=self.home,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "python-id").exists(), "OPOS must not start from the wrong folder")
        self.assertFalse((self.home / "llama-args").exists())

    def test_without_model_starts_only_opos_with_saved_id(self):
        self.generate_ok(NVIDIA + "\n")
        (self.home / "models/model.gguf").unlink()
        script = (self.home / "bin/opos-start").read_text(encoding="utf-8")
        subprocess.run(["sh", "-eu", "-c", script + "\nwait\n"], env=self.env, cwd=self.home, timeout=10, check=True)
        self.assertFalse((self.home / "llama-args").exists())
        self.assertEqual((self.home / "python-id").read_text(encoding="utf-8"), NVIDIA + "\n")
