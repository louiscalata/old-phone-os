# SPDX-License-Identifier: GPL-3.0-or-later
"""Run focused in-memory mutation and deterministic factory differential checks."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import random
import tempfile
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
    ("host_check_bypass", 'if not secure.host_ok(self.headers.get("Host"), actual_port):', "if False:"),
    ("token_check_bypass", 'if is_api and not secure.token_ok(self.headers.get("X-OPOS-Token"), token):', "if False:"),
    ("method_check_bypass", 'if self.command not in ("GET", "POST"):', "if False:"),
    ("origin_check_bypass", 'if not secure.origin_ok(self.headers.get("Origin"), actual_port):', "if False:"),
    ("media_type_check_bypass", 'if not secure.json_type_ok(self.headers.get("Content-Type")):', "if False:"),
    ("rate_limit_bypass", "if not limiter.allow(self.client_address[0]):", "if False:"),
    (
        "media_type_status",
        'self._json(415, {"error": "unsupported media type"})',
        'self._json(400, {"error": "unsupported media type"})',
    ),
    (
        "rate_limit_status",
        'self._json(429, {"error": "too many requests"})',
        'self._json(400, {"error": "too many requests"})',
    ),
    (
        "method_status",
        'self._json(405, {"error": "method not allowed"})',
        'self._json(400, {"error": "method not allowed"})',
    ),
    ("state_route_removed", 'elif self.path == "/setup/api/state":', 'elif self.path == "/removed-state":'),
    ("progress_route_removed", 'elif self.path == "/setup/api/progress":', 'elif self.path == "/removed-progress":'),
    (
        "prefs_read_route_removed",
        'elif self.path == "/setup/api/prefs":\n                    self._json',
        'elif self.path == "/removed-prefs":\n                    self._json',
    ),
    ("device_route_removed", 'if self.path == "/setup/api/device":', 'if self.path == "/removed-device":'),
    ("validate_route_removed", 'elif self.path == "/setup/api/validate":', 'elif self.path == "/removed-validate":'),
    (
        "prefs_write_route_removed",
        'elif self.path == "/setup/api/prefs":\n                step',
        'elif self.path == "/removed-prefs":\n                step',
    ),
    ("prefs_skip_route_removed", 'elif self.path == "/setup/api/prefs/skip":', 'elif self.path == "/removed-skip":'),
    (
        "prefs_skip_all_route_removed",
        'elif self.path == "/setup/api/prefs/skip-all":',
        'elif self.path == "/removed-skip-all":',
    ),
    (
        "prefs_finish_route_removed",
        'elif self.path == "/setup/api/prefs/finish":',
        'elif self.path == "/removed-finish":',
    ),
    ("body_error_status", 'self._json(exc.status, {"error": exc.message})', 'self._json(200, {"error": exc.message})'),
    (
        "body_error_message",
        'self._json(exc.status, {"error": exc.message})',
        'self._json(exc.status, {"error": "wrong error"})',
    ),
    ("body_reader_bypass", "body = secure.read_json_body(self.rfile, self.headers)", "body = {}"),
    (
        "json_status",
        'body = json.dumps(payload).encode("utf-8")\n            self.send_response(status)',
        'body = json.dumps(payload).encode("utf-8")\n            self.send_response(200)',
    ),
    (
        "json_content_type",
        'self.send_header("Content-Type", "application/json")',
        'self.send_header("Content-Type", "text/plain")',
    ),
    ("head_body_written", 'if self.command != "HEAD":', "if True:"),
    ("connection_capacity", "threading.Semaphore(16)", "threading.Semaphore(15)"),
    ("request_budget", "request_budget = 10.0", "request_budget = 9.0"),
    ("timer_not_daemon", "self._budget_timer.daemon = True", "self._budget_timer.daemon = False"),
    ("timer_not_started", "self._budget_timer.start()", "pass"),
    ("expire_socket_not_shutdown", "self.connection.shutdown(socket.SHUT_RDWR)", "pass"),
    ("timeout_equality", "if left <= 0:", "if left < 0:"),
    (
        "timeout_not_shrunk",
        "self.connection.settimeout(min(self.timeout, left))",
        "self.connection.settimeout(self.timeout)",
    ),
    ("apply_status", 'self._json(202, {"started": True})', 'self._json(200, {"started": True})'),
    (
        "apply_duplicate_status",
        'self._json(409, {"error": "already running"})',
        'self._json(400, {"error": "already running"})',
    ),
    (
        "apply_thread_not_daemon",
        "threading.Thread(target=run, daemon=True)",
        "threading.Thread(target=run, daemon=False)",
    ),
    ("config_error_flag", 'payload["config_error"] = True', 'payload["config_error"] = False'),
    ("returned_token", "server.token = token", 'server.token = "wrong-token"'),
)


def compile_factory(source):
    tree = ast.parse(source)
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "make_server")
    module = compile(ast.Module(body=[node], type_ignores=[]), "<setup factory check>", "exec")
    code = next(item for item in module.co_consts if isinstance(item, types.CodeType) and item.co_name == "make_server")
    return types.FunctionType(code, vars(support.setup), argdefs=support.setup.make_server.__defaults__)


def observe(factory, case, *, runner=None):
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append("external operation")
        raise RuntimeError("In-memory check refused an external operation")

    with (
        mock.patch.object(support.setup, "make_server", factory),
        mock.patch("socket.socket", side_effect=refuse),
        mock.patch("subprocess.Popen", side_effect=refuse),
    ):
        result = (runner or support.run_case)(case)
    if attempts:
        raise RuntimeError("In-memory isolation failed")
    return result


def prior_driver(revision):
    source = git("show", revision + ":tests/fixtures/setup_server_golden/support.py")
    with tempfile.TemporaryDirectory(prefix="prior-golden-driver-") as folder:
        path = Path(folder) / "support.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("prior_setup_golden_support", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module.run_case


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
    for number in range(3000):
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


def differential_checks(baseline, cases, reference_runner):
    current = support.setup.make_server
    inputs = [{key: value for key, value in case.items() if key != "expected"} for case in cases]
    inputs.extend(extra_inputs())
    results = []
    mismatches = []
    driver_results = []
    driver_mismatches = []
    for case in inputs:
        before = observe(baseline, case)
        after = observe(current, case)
        if before != after:
            mismatches.append(case["name"])
        results.append({"input": case, "baseline_sha256": digest(before), "candidate_sha256": digest(after)})
        if "decoder_error" not in case:
            reference = observe(current, case, runner=reference_runner)
            if reference != after:
                driver_mismatches.append(case["name"])
            driver_results.append(
                {"name": case["name"], "prior_sha256": digest(reference), "candidate_sha256": digest(after)}
            )
    return {
        "inputs": len(inputs),
        "corpus": len(cases),
        "extra": len(inputs) - len(cases),
        "mismatches": mismatches,
        "results": results,
        "driver_inputs": len(driver_results),
        "driver_mismatches": driver_mismatches,
        "driver_results": driver_results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON report destination")
    parser.add_argument("--driver-revision", required=True, help="Revision containing the prior fixture driver")
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
        "differential": differential_checks(compile_factory(baseline), cases, prior_driver(args.driver_revision)),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in report.items() if key not in ("mutations", "differential")}
    summary["mutations"] = {key: value for key, value in report["mutations"].items() if key != "results"}
    summary["differential"] = {
        key: value for key, value in report["differential"].items() if key not in ("results", "driver_results")
    }
    print(json.dumps(summary))
    return int(
        bool(
            report["mutations"]["survived"]
            or report["differential"]["mismatches"]
            or report["differential"]["driver_mismatches"]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
