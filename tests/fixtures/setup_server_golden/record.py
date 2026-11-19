# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproduce the setup corpus using its recorded production-source revision."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

FIXTURES = Path(__file__).resolve().parent
REPO = FIXTURES.parents[2]


def git(*args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG_")}
    return subprocess.run(["git", "-C", str(REPO), *args], env=env, check=True, capture_output=True, timeout=60).stdout


def source_archive(revision, root):
    raw = git("archive", revision, "src", "pyproject.toml")
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not member.isfile():
                raise ValueError("Source archive must contain only relative regular files")
            target = root.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as stream:
                target.write_bytes(stream.read())


def record(revision, inputs):
    with tempfile.TemporaryDirectory(prefix="setup-golden-source-") as folder:
        root = Path(folder)
        source_archive(revision, root)
        sys.path.insert(0, str(root / "src"))
        spec = importlib.util.spec_from_file_location("setup_golden_support", FIXTURES / "support.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        source = git("show", revision + ":src/opos/setup/server.py")
        if (root / "src/opos/setup/server.py").read_bytes() != source:
            raise ValueError("Archived source differs from recorded revision")
        if not Path(helper.setup.__file__).is_relative_to(root):
            raise ValueError("Production imports must resolve to the source archive")
        if len({case["name"] for case in inputs}) != len(inputs):
            raise ValueError("Duplicate corpus input name")
        for case in inputs:
            case["expected"] = helper.run_case(case)
        return {
            "schema_version": 2,
            "generated_from": {
                "revision": revision,
                "source": "src/opos/setup/server.py",
                "sha256": hashlib.sha256(source).hexdigest(),
            },
            "cases": inputs,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare without replacing the corpus")
    parser.add_argument("--revision", help="Record from another explicit source revision")
    args = parser.parse_args()
    target = FIXTURES / "cases.json"
    prior = json.loads(target.read_text(encoding="utf-8"))
    inputs = [{key: value for key, value in case.items() if key != "expected"} for case in prior["cases"]]
    payload = record(args.revision or prior["generated_from"]["revision"], inputs)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode()
    if args.check:
        if encoded != target.read_bytes():
            raise AssertionError("Corpus reproduction differs")
    else:
        target.write_bytes(encoded)
    print(
        json.dumps(
            {
                "cases": len(inputs),
                "groups": {group: sum(case["group"] == group for case in inputs) for group in ("http", "lifecycle")},
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "reproduced": args.check,
            }
        )
    )


if __name__ == "__main__":
    main()
