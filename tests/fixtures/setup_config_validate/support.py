# SPDX-License-Identifier: GPL-3.0-or-later
"""Replay generated validator cases without changing hash-dependent ordering."""

from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ERROR_TYPES = {error.__name__: error for error in (ValueError, TypeError, RuntimeError)}


class FreshSourceLoader(importlib.machinery.SourceFileLoader):
    """Compile source after the worker's hash seed is active, ignoring bytecode."""

    def get_code(self, fullname):
        return compile(Path(self.path).read_bytes(), self.path, "exec", dont_inherit=True)


class ScriptedString(str):
    """Model accepted string subclasses with surprising but deterministic methods."""

    def __new__(cls, value, behavior):
        instance = super().__new__(cls, value)
        instance.behavior = behavior
        instance.cursor = 0
        return instance

    def __getitem__(self, key):
        value = super().__getitem__(key)
        return type(self)(value, self.behavior) if isinstance(key, slice) else value

    def __hash__(self):
        if "hash_raise" in self.behavior:
            raise ERROR_TYPES[self.behavior["hash_raise"]]("fixture hash failure")
        return super().__hash__()

    def strip(self, chars=None):
        return type(self)(super().strip(chars), self.behavior)

    def split(self, sep=None, maxsplit=-1):
        return self.behavior.get("split", super().split(sep, maxsplit))

    def startswith(self, prefix, *args):
        sequence = self.behavior.get("startswith", [])
        if self.cursor < len(sequence):
            result = sequence[self.cursor]
            self.cursor += 1
            return result
        return super().startswith(prefix, *args)


def expected_outcome(case, seed):
    """Select only recorded interpreter differences; retain exact list ordering."""
    runtime = f"{sys.version_info.major}.{sys.version_info.minor}"
    variants = case.get("expected_by_python", {}).get(runtime, {})
    return variants.get(str(seed), case["expected_by_seed"][str(seed)])


def decode(value):
    """Restore explicitly tagged non-JSON input values, including cycles."""
    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    tags = {
        "$float": lambda item: float(item),
        "$bytes": bytes.fromhex,
        "$tuple": lambda items: tuple(decode(item) for item in items),
        "$dict": lambda items: {decode(key): decode(item) for key, item in items},
        "$cycle": cycle,
        "$str": lambda item: ScriptedString(item["value"], item["behavior"]),
    }
    if len(value) == 1 and next(iter(value)) in tags:
        tag, item = next(iter(value.items()))
        return tags[tag](item)
    return {key: decode(item) for key, item in value.items()}


def cycle(kind):
    if kind == "dict":
        value = {}
        value["self"] = value
    elif kind == "list":
        value = []
        value.append(value)
    else:
        raise ValueError(f"Unknown cycle kind: {kind}")
    return value


def snapshot(value, seen=None):
    """Keep input types, mapping order, aliases and nonfinite values observable."""
    seen = {} if seen is None else seen
    if isinstance(value, ScriptedString):
        return {"$str": {"value": str(value), "behavior": value.behavior, "cursor": value.cursor}}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": str(value)}
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    if isinstance(value, (dict, list, tuple)):
        identity = id(value)
        if identity in seen:
            return {"$ref": seen[identity]}
        seen[identity] = len(seen)
        if isinstance(value, dict):
            return {"$dict": [[snapshot(key, seen), snapshot(item, seen)] for key, item in value.items()]}
        tag = "$tuple" if isinstance(value, tuple) else "$list"
        return {tag: [snapshot(item, seen) for item in value]}
    return value


def recorder(name, original, case, calls):
    def call(*args):
        calls.append([name, snapshot(args)])
        patch = case.get("patches", {}).get(name, {})
        if "raise" in patch:
            raise ERROR_TYPES[patch["raise"]](patch["message"])
        if "return" in patch:
            return decode(patch["return"])
        return original(*args)

    return call


def observe(module, case):
    """Capture complete returns or escaping exceptions and ordered global calls."""
    value = decode(case["input"])
    before = json.dumps(snapshot(value), ensure_ascii=True, allow_nan=False)
    calls = []
    defaults = recorder("default_config", module.default_config, case, calls)
    fullmatch = recorder("re.fullmatch", module.re.fullmatch, case, calls)
    isnan = recorder("math.isnan", module.math.isnan, case, calls)
    with contextlib.ExitStack() as stack:
        for name, replacement in case.get("globals", {}).items():
            stack.enter_context(mock.patch.object(module, name, decode(replacement)))
        stack.enter_context(mock.patch.object(module, "default_config", side_effect=defaults))
        stack.enter_context(mock.patch.object(module, "re", SimpleNamespace(fullmatch=fullmatch)))
        stack.enter_context(mock.patch.object(module, "math", SimpleNamespace(isnan=isnan)))
        try:
            outcome = {"return": module.validate(value)}
        except Exception as error:
            # This replay boundary records escaping validator failures as golden data.
            outcome = {"exception": type(error).__name__, "message": str(error)}
    after = json.dumps(snapshot(value), ensure_ascii=True, allow_nan=False)
    outcome.update(calls=calls, input_unchanged=after == before)
    return outcome


def replay_cases(module, corpus_path, seed):
    """Use a fresh interpreter: changing the parent environment cannot reseed hashes."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG_")}
    with tempfile.TemporaryDirectory(prefix="validate-replay-") as folder:
        environment.update(PYTHONHASHSEED=str(seed), TMPDIR=folder, PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__)),
                "--source",
                str(Path(module.__file__)),
                "--cases",
                str(corpus_path),
                "--seed",
                str(seed),
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
    return json.loads(process.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--seed", required=True)
    args = parser.parse_args()
    if os.environ.get("PYTHONHASHSEED") != args.seed:
        raise RuntimeError("Set the hash seed before starting the replay interpreter")
    source = os.path.abspath(args.source)
    loader = FreshSourceLoader("golden_config", source)
    spec = importlib.util.spec_from_file_location(loader.name, source, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    sys.setrecursionlimit(300)
    results = [observe(module, case) for case in cases]
    print(json.dumps(results, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
