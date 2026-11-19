# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep seeded validator receipts independent of pre-existing bytecode."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opos.setup import config
from tests.fixtures.setup_config_validate import support

CORPUS_PATH = Path(__file__).parent / "fixtures" / "setup_config_validate" / "cases.json"
DOCUMENT = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
CASES = {case["name"]: case for case in DOCUMENT["cases"]}
SEEDS = DOCUMENT["generated_from"]["hash_seeds"]
CACHE_WRITER = """
import importlib.util
import marshal
import struct
import sys
from pathlib import Path

source = Path(sys.argv[1])
raw = source.read_bytes()
code = compile(raw + sys.argv[2].encode("utf-8"), str(source), "exec", dont_inherit=True)
header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(source.stat().st_mtime), len(raw))
cache = Path(importlib.util.cache_from_source(str(source)))
cache.parent.mkdir(exist_ok=True)
cache.write_bytes(header + marshal.dumps(code))
"""


def environment(seed):
    result = {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG_")}
    result.update(PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1")
    return result


class ValidateReplayTests(unittest.TestCase):
    def check_private_cache(self, suffix):
        source_bytes = Path(config.__file__).read_bytes()
        self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), DOCUMENT["generated_from"]["sha256"])
        case = CASES["device_empty"]
        with tempfile.TemporaryDirectory(prefix="validate-cache-") as directory:
            root = Path(directory)
            source = root / "config.py"
            source.write_bytes(source_bytes)
            corpus = root / "cases.json"
            corpus.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
            for seed in SEEDS:
                with self.subTest(seed=seed):
                    subprocess.run(
                        [sys.executable, "-c", CACHE_WRITER, str(source), suffix],
                        env=environment(seed),
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    cache = Path(importlib.util.cache_from_source(str(source)))
                    cache_bytes = cache.read_bytes()
                    outcomes = support.replay_cases(SimpleNamespace(__file__=str(source)), corpus, seed)
                    self.assertEqual(outcomes, [support.expected_outcome(case, seed)])
                    self.assertEqual(source.read_bytes(), source_bytes)
                    self.assertEqual(cache.read_bytes(), cache_bytes)

    def test_unmodified_compiled_source_cache(self):
        self.check_private_cache("")

    def test_poisoned_cache_cannot_replace_source(self):
        self.check_private_cache('\ndef validate(value):\n    return ["bytecode cache used"]\n')

    def test_isolated_module_retains_file_and_identity(self):
        source_text = """
import math
import re
import sys

def default_config():
    return {}

def validate(value):
    return [__name__, __file__, __spec__.origin, __package__,
            __loader__.get_filename(__name__), sys.modules[__name__].__file__,
            validate.__code__.co_filename]
"""
        with tempfile.TemporaryDirectory(prefix="validate-context-") as directory:
            source = Path(directory) / "config.py"
            source.write_text(source_text, encoding="utf-8")
            corpus = source.with_name("cases.json")
            corpus.write_text(json.dumps({"cases": [{"input": {}}]}), encoding="utf-8")
            outcome = support.replay_cases(SimpleNamespace(__file__=str(source)), corpus, "0")[0]
            expected = ["golden_config", str(source), str(source), "", str(source), str(source), str(source)]
            self.assertEqual(outcome["return"], expected)
            self.assertEqual(outcome["calls"], [])
            self.assertTrue(outcome["input_unchanged"])
            relative = subprocess.run(
                [
                    sys.executable,
                    str(Path(support.__file__)),
                    "--source",
                    source.name,
                    "--cases",
                    corpus.name,
                    "--seed",
                    "0",
                ],
                cwd=directory,
                env=environment("0"),
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            self.assertEqual(json.loads(relative.stdout)[0]["return"], expected)

    def test_seed_must_be_active_before_worker_starts(self):
        process = subprocess.run(
            [
                sys.executable,
                str(Path(support.__file__)),
                "--source",
                str(Path(config.__file__)),
                "--cases",
                str(CORPUS_PATH),
                "--seed",
                "17",
            ],
            env=environment("0"),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Set the hash seed before starting the replay interpreter", process.stderr)

    def test_exact_recorded_python_311_cycle_variants(self):
        messages = {
            "recursive_dict": "maximum recursion depth exceeded while calling a Python object",
            "recursive_list": "maximum recursion depth exceeded while getting the str of an object",
        }
        runtime = SimpleNamespace(version_info=SimpleNamespace(major=3, minor=11))
        with mock.patch.object(support, "sys", runtime):
            for name, message in messages.items():
                case = CASES[name]
                for seed in SEEDS:
                    with self.subTest(case=name, seed=seed):
                        expected = {**case["expected_by_seed"][seed], "message": message}
                        self.assertEqual(support.expected_outcome(case, seed), expected)


if __name__ == "__main__":
    unittest.main()
