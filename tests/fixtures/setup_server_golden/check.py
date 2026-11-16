# SPDX-License-Identifier: GPL-3.0-or-later
"""Run focused in-memory mutation and deterministic factory differential checks."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import types
from pathlib import Path
from unittest import mock

from tests.fixtures.setup_server_golden import support
from tests.fixtures.setup_server_golden.record import REPO, git

MUTATIONS = (
    (
        "request_logging",
        '"""Suppress request logging so the token never reaches a log."""',
        '"""Suppress request logging so the token never reaches a log."""\n'
        "            super().log_message(format, *args)",
    ),
    (
        "post_state_route",
        'if self.path not in (\n                "/setup/api/device",',
        'if self.path not in (\n                "/setup/api/state",\n                "/setup/api/device",',
    ),
    (
        "post_progress_route",
        'if self.path not in (\n                "/setup/api/device",',
        'if self.path not in (\n                "/setup/api/progress",\n                "/setup/api/device",',
    ),
    ("index_alias", 'if self.path == "/":', 'if self.path in ("/", "/index.html"):'),
    ("missing_progress_state", "server.progress_state = state", "pass  # progress state omitted"),
    ("detached_progress_state", "server.progress_state = state", "server.progress_state = _ProgressState()"),
)


def compile_factory(source):
    tree = ast.parse(source)
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "make_server")
    module = compile(ast.Module(body=[node], type_ignores=[]), "<setup factory check>", "exec")
    code = next(item for item in module.co_consts if isinstance(item, types.CodeType) and item.co_name == "make_server")
    return types.FunctionType(code, vars(support.setup), argdefs=support.setup.make_server.__defaults__)


def observe(factory, case):
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append("external operation")
        raise RuntimeError("In-memory check refused an external operation")

    with (
        mock.patch.object(support.setup, "make_server", factory),
        mock.patch("socket.socket", side_effect=refuse),
        mock.patch("subprocess.Popen", side_effect=refuse),
    ):
        result = support.run_case(case)
    if attempts:
        raise RuntimeError("In-memory isolation failed")
    return result


def assert_control(factory, cases):
    for case in cases:
        if observe(factory, case) != case["expected"]:
            raise AssertionError("Unmodified control differs: " + case["name"])


def mutation_checks(source, cases):
    original = support.setup.make_server
    assert_control(original, cases)
    assert_control(compile_factory(source), cases)
    results = []
    for name, before, after in MUTATIONS:
        if source.count(before) != 1:
            raise ValueError("Mutation target must occur once: " + name)
        factory = compile_factory(source.replace(before, after))
        for case in cases:
            if observe(factory, case) != case["expected"]:
                results.append({"name": name, "status": "killed", "case": case["name"]})
                break
        else:
            results.append({"name": name, "status": "survived"})
    assert_control(original, cases)
    return {
        "controls_passed": True,
        "mutants": len(results),
        "killed": sum(item["status"] == "killed" for item in results),
        "survived": sum(item["status"] == "survived" for item in results),
        "results": results,
    }


def extra_inputs():
    rng = random.Random(271828)
    paths = (
        "/",
        "/index.html",
        "/setup/api/state",
        "/setup/api/progress",
        "/setup/api/device",
        "/setup/api/validate",
        "/setup/api/apply",
        "/setup/api/prefs",
        "/setup/api/prefs/skip",
        "/setup/api/prefs/skip-all",
        "/setup/api/prefs/finish",
        "/unknown",
        "/setup/api",
    )
    header_options = (
        ("Host", [None, "bad", "localhost:43123", "127.0.0.1:43123"]),
        ("X-OPOS-Token", [None, "bad", support.SESSION_VALUE]),
        ("Origin", [None, "null", "http://localhost:43123"]),
        ("Content-Type", [None, "text/plain", "application/json;charset=utf-8"]),
    )
    for number in range(500):
        headers = {key: rng.choice(options) for key, options in header_options if rng.random() < 0.4}
        yield {
            "name": f"extra_{number}",
            "group": "http",
            "requests": [
                {
                    "path": rng.choice(paths),
                    "method": rng.choice(["GET", "POST", "HEAD", "PUT", "OPTIONS", "TRACE", "CONNECT"]),
                    "payload": {
                        "config": rng.choice([None, {}, {"invalid": True}, []]),
                        "profile_id": rng.choice([None, "slab", "bad", 2]),
                        "step": rng.choice(["theme", "layout", "unknown", None]),
                        "value": rng.choice([None, "dark", "later", {"favourites": []}]),
                    },
                    "headers": headers,
                }
            ],
            "state_mode": rng.choice(["missing", "empty", "loaded", "config_error", "ai_error"]),
        }


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def differential_checks(baseline, cases):
    current = support.setup.make_server
    inputs = [{key: value for key, value in case.items() if key != "expected"} for case in cases]
    inputs.extend(extra_inputs())
    results = []
    mismatches = []
    for case in inputs:
        before = observe(baseline, case)
        after = observe(current, case)
        if before != after:
            mismatches.append(case["name"])
        results.append({"input": case, "baseline_sha256": digest(before), "candidate_sha256": digest(after)})
    return {
        "inputs": len(inputs),
        "corpus": len(cases),
        "extra": len(inputs) - len(cases),
        "mismatches": mismatches,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON report destination")
    args = parser.parse_args()
    metadata = json.loads(support.CORPUS.read_text(encoding="utf-8"))["generated_from"]
    source = (REPO / metadata["source"]).read_bytes()
    baseline = git("show", metadata["revision"] + ":" + metadata["source"])
    if hashlib.sha256(baseline).hexdigest() != metadata["sha256"]:
        raise ValueError("Pinned source hash differs from corpus metadata")
    if source != baseline:
        raise ValueError("Focused mutation catalog requires the recorded production source")
    cases = support.corpus_cases()
    report = {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "corpus_sha256": hashlib.sha256(support.CORPUS.read_bytes()).hexdigest(),
        "baseline_cases": len(cases),
        "mutations": mutation_checks(source.decode(), cases),
        "differential": differential_checks(compile_factory(baseline), cases),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in report.items() if key not in ("mutations", "differential")}
    summary["mutations"] = {key: value for key, value in report["mutations"].items() if key != "results"}
    summary["differential"] = {key: value for key, value in report["differential"].items() if key != "results"}
    print(json.dumps(summary))
    return int(bool(report["mutations"]["survived"] or report["differential"]["mismatches"]))


if __name__ == "__main__":
    raise SystemExit(main())
