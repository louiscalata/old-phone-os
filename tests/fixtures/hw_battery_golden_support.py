# SPDX-License-Identifier: GPL-3.0-or-later
"""Replay battery fixtures against temporary sysfs trees and ordered I/O faults."""

from __future__ import annotations

import builtins
import contextlib
import json
import tempfile
from pathlib import Path
from unittest import mock

CORPUS_PATH = Path(__file__).parent / "hw_battery_golden" / "cases.json"


def load_cases(group=None):
    cases = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
    return [case for case in cases if group is None or case["group"] == group]


def _make_tree(root, case):
    base = root / "sys/class/power_supply"
    mode = case.get("base", "directory")
    if mode == "missing":
        return base, []
    base.parent.mkdir(parents=True)
    if mode == "file":
        base.write_bytes(b"not a directory")
        return base, []
    base.mkdir()
    entries = []
    for index, fields in enumerate(case.get("supplies_override", case["supplies"])):
        name = case.get("names", [f"Z{i}" for i in range(len(case["supplies"]) + 2)])[index]
        entry = base / name
        entries.append(entry)
        if fields.get("entry_file"):
            entry.write_bytes(b"not a supply directory")
            continue
        entry.mkdir()
        for field, value in fields.items():
            if value is None:
                continue
            path = entry / field
            if isinstance(value, dict) and value.get("directory"):
                path.mkdir()
            else:
                raw = bytes.fromhex(value["hex"]) if isinstance(value, dict) else str(value).encode("utf-8")
                path.write_bytes(raw)
    return base, entries


def _fixture_error(name):
    return getattr(builtins, name)("fixture " + name)


def _invoke(module, case, root):
    argument = case.get("invalid_root", root)
    if case.get("root_form") == "str":
        argument = str(root)
    if isinstance(argument, dict) and "bytes" in argument:
        argument = argument["bytes"].encode("utf-8")
    try:
        return {"result": module.read_battery(argument)}
    except BaseException as error:
        # Deliberately capture synthetic BaseException faults as well as Exception.
        # Keep only the class because pathlib wording differs by Python version.
        return {"error": type(error).__name__}


def _patch_numeric_globals(stack, module, case, calls):
    for name, fault in case.get("global_faults", {}).items():
        original = getattr(builtins, name)
        count = [0]

        def call(value, operation=name, effect=fault, function=original, counter=count):
            counter[0] += 1
            calls.append([operation, value])
            selected = counter[0] == effect.get("call") or (
                effect.get("when") == "numeric" and not isinstance(value, str)
            )
            if selected:
                raise _fixture_error(effect["error"])
            return function(value)

        stack.enter_context(mock.patch.object(module, name, side_effect=call, create=True))


def _run_with_tree(module, case, root, base, entries):
    calls = []
    original_text, original_int = module._read_text, module._read_int
    original_methods = {name: getattr(Path, name) for name in ("is_dir", "iterdir", "is_file", "read_text")}

    def relative(path):
        # Refuse any accidental access outside this case's temporary tree.
        return str(path.relative_to(root))

    def helper(name, original, path):
        label = str(path.relative_to(base))
        relative(path)
        calls.append([name, label])
        fault = case.get("helper_faults", {}).get(f"{name}:{label}")
        if fault is not None:
            if "error" in fault:
                raise _fixture_error(fault["error"])
            return fault["value"]
        return original(path)

    def iterate():
        for index, entry in enumerate(entries):
            if index == case.get("iteration_error_after"):
                raise OSError("fixture iteration")
            yield entry
        if len(entries) == case.get("iteration_error_after"):
            raise OSError("fixture iteration")

    def path_call(name, path, *args, **kwargs):
        label = relative(path)
        calls.append([name, label])
        fault = case.get("path_faults", {}).get(name, {}).get(label)
        if fault:
            raise _fixture_error(fault)
        if name == "iterdir":
            if path != base:
                raise AssertionError("Unexpected fixture iteration")
            return iterate()
        return original_methods[name](path, *args, **kwargs)

    with contextlib.ExitStack() as stack:
        _patch_numeric_globals(stack, module, case, calls)
        stack.enter_context(
            mock.patch.object(module, "_read_text", side_effect=lambda path: helper("_read_text", original_text, path))
        )
        stack.enter_context(
            mock.patch.object(module, "_read_int", side_effect=lambda path: helper("_read_int", original_int, path))
        )
        for name in original_methods:
            stack.enter_context(
                mock.patch.object(
                    Path,
                    name,
                    lambda path, *args, operation=name, **kwargs: path_call(operation, path, *args, **kwargs),
                )
            )
        outcome = _invoke(module, case, root)
    outcome["calls"] = calls
    return outcome


def run_case(module, case):
    """Return the complete result or escaping exception and ordered patched calls."""
    with tempfile.TemporaryDirectory(prefix="battery-golden-") as directory:
        root = Path(directory)
        base, entries = _make_tree(root, case)
        return _run_with_tree(module, case, root, base, entries)
