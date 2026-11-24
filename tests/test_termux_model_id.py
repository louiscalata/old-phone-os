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


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
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
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def launch(self, expected_alias, expected_id, override=None):
        env = dict(self.env)
        if override is not None:
            env["OPOS_LLAMA_ID"] = override
        script = (self.home / "bin/opos-start").read_text(encoding="utf-8")
        result = subprocess.run(
            ["sh", "-c", script + "\nwait\n"], env=env, capture_output=True, text=True, timeout=10, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.home / "llama-args").read_text(encoding="utf-8").splitlines()
        self.assertEqual(args[args.index("--alias") + 1], expected_alias)
        self.assertEqual((self.home / "python-id").read_text(encoding="utf-8"), expected_id + "\n")

    def test_no_file_preserves_original_defaults(self):
        self.assertEqual(self.generate().returncode, 0)
        self.launch(LLAMA, "")

    def test_drop_id_survives_plain_restart(self):
        self.assertEqual(self.generate(NVIDIA + "\n").returncode, 0)
        (self.drop / "llama-id").unlink()
        self.launch(NVIDIA, NVIDIA)
        self.launch(NVIDIA, NVIDIA)

    def test_environment_wins_over_saved_id(self):
        self.assertEqual(self.generate(NVIDIA + "\n").returncode, 0)
        self.launch(LLAMA, LLAMA, override=LLAMA)
        self.launch(LLAMA, "", override="")

    def test_id_grammar_accepts_boundaries(self):
        for value in ("a", "0", "a" * 64, "0.model-1.5"):
            with self.subTest(value=value):
                self.assertEqual(self.generate(value + "\n").returncode, 0)
                self.launch(value, value)

    def test_invalid_drop_id_refuses_launcher_generation(self):
        for value in ("", "a" * 65, ".model", "-model", "UPPER", "a_b", "a b", "é", "a\nb", "$(touch pwned)"):
            with self.subTest(value=value):
                result = self.generate(value + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("failed: invalid llama-id", result.stdout)
                self.assertFalse((self.home / "bin/opos-start").exists())
                self.assertFalse((self.home / "pwned").exists())

    def test_without_model_starts_only_opos_with_saved_id(self):
        self.assertEqual(self.generate(NVIDIA + "\n").returncode, 0)
        (self.home / "models/model.gguf").unlink()
        script = (self.home / "bin/opos-start").read_text(encoding="utf-8")
        subprocess.run(["sh", "-eu", "-c", script + "\nwait\n"], env=self.env, timeout=10, check=True)
        self.assertFalse((self.home / "llama-args").exists())
        self.assertEqual((self.home / "python-id").read_text(encoding="utf-8"), NVIDIA + "\n")
