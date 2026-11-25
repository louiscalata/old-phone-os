# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the soak helpers and optional bounded child-process runs.

OPOS_SOAK=1 enables the process tests, which share a 90-second wall-time budget.
They stub only resource measurements; the phone, PC, sockets and faults are real.
This keeps the process tests usable in sandboxes where ps is refused.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gpu_link_soak", ROOT / "tools/gpu-link-soak.py")
soak = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(soak)
PROCESS_LAUNCHER = """import importlib.util, sys
spec = importlib.util.spec_from_file_location("soak", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def sample(pid):
    return dict(rss_kib=1000, threads=5, descriptors=8, source="self-test stub")
module.process_sample = sample
sys.exit(module.main(sys.argv[2:]))
"""


class SoakHelpers(unittest.TestCase):
    def test_fixture_is_deterministic_question_specific_unicode_and_bounded(self):
        first = soak.expected_text("one", 743)
        self.assertEqual(first, soak.expected_text("one", 743))
        self.assertNotEqual(first, soak.expected_text("two", 743))
        self.assertNotEqual(first, soak.expected_text("one", 744))
        self.assertTrue(60 <= len(first.split()) <= 400)
        self.assertIn("🪢", first)
        self.assertLess(len(first.encode()), 12288)

    def test_mac_sampler_uses_pid_arguments_without_a_shell(self):
        with (
            mock.patch.object(soak.sys, "platform", "darwin"),
            mock.patch.object(
                soak,
                "command_output",
                side_effect=["2048\n", "header\na\nb\n", "p123\nfcwd\nftxt\nfmem\nf0\nf1\nf23\nf2x\n"],
            ) as run,
        ):
            self.assertEqual(soak.process_sample(123), dict(rss_kib=2048, threads=2, descriptors=3))
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(["ps", "-o", "rss=", "-p", "123"]),
                mock.call(["ps", "-M", "-p", "123"]),
                mock.call(["lsof", "-p", "123", "-F", "f"]),
            ],
        )

    def test_restart_resets_resource_warning_baseline(self):
        rows = []
        for generation, age, rss, descriptors in ((1, 0, 900, 3), (1, 31, 1000, 4), (1, 70, 1400, 26), (2, 0, 700, 2)):
            rows.append(
                dict(
                    generation=generation,
                    age_seconds=age,
                    rss_kib=rss,
                    descriptors=descriptors,
                    threads=4,
                    store_files=1,
                    store_bytes=500,
                    pid=generation,
                )
            )
        summary, warnings = soak.resource_summary(rows, 30)
        self.assertEqual(len(summary), 2)
        self.assertEqual(summary[1]["metrics"]["rss_kib"]["first"], 700)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(all("generation 1" in warning for warning in warnings))

    def test_argument_limits_are_checked_before_starting(self):
        self.assertEqual(soak.interval("0.3-8"), (0.3, 8))
        for value in ("0-1", "2-1", "nan-3", "1-inf", "bad"):
            with self.subTest(value=value), self.assertRaises(soak.argparse.ArgumentTypeError):
                soak.interval(value)

    def test_monotonic_and_acknowledgement_invariants(self):
        run = soak.Soak.__new__(soak.Soak)
        run.counts, run.last_answer = soak.Counter(), None
        proxy = soak.FaultProxy(1, mock.Mock())
        self.addCleanup(proxy.server_close)
        run.proxy = proxy

        def view(text):
            return dict(
                gpu=dict(answer=dict(id="id", question="q", text=text, state="answering")), phone=dict(answer=None)
            )

        run.stored = lambda: [dict(id="id", text="abc")]
        run.validate_view(view("abc"), "q", "abcdef", "id")
        with self.assertRaisesRegex(soak.InvariantError, "shrank"):
            run.validate_view(view(""), "q", "abcdef", "id")
        run.last_answer = None
        request = b'POST /v1/runs/old/text HTTP/1.1\r\n\r\n{"done":false}'
        proxy.lease_questions["old"] = proxy.question
        proxy.observe(request, b'HTTP/1.1 200 OK\r\n\r\n{"acked":4}')
        with self.assertRaisesRegex(soak.InvariantError, "acknowledged bytes"):
            run.validate_view(view("abc"), "q", "abcdef", "id")

    def test_model_requires_exact_durable_resume_not_merely_an_expected_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            args = soak.arguments(["--token-rate", "400"])
            server = soak.ModelServer(args)
            server.store_root = Path(folder)
            expected = soak.expected_text("q", 743)
            (Path(folder) / "ai-answer-fixture.json").write_text(
                json.dumps(dict(answer=dict(question="q", text=expected[:100])))
            )
            thread = soak.threading.Thread(target=server.serve_forever, kwargs=dict(poll_interval=0.01))
            thread.start()
            connection = soak.http.client.HTTPConnection(*server.server_address, timeout=2)
            try:
                body = soak.wire_json(dict(messages=[dict(content="q"), dict(content=expected[:99])]))
                connection.request("POST", "/v1/chat/completions", body)
                reply = connection.getresponse()
                reply.read()
                self.assertEqual(reply.status, 400)
                self.assertIn("resume partial differs from durable text", server.errors[0])
                self.assertLess(len(server.errors[0]), 400)
            finally:
                connection.close()
                server.stop()
                thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_resource_growth_fails_after_warmup_within_one_generation(self):
        run = soak.Soak.__new__(soak.Soak)
        run.args = soak.arguments(["--duration", "90"])
        run.generation, run.usable_generation, run.usable_generation_count = 1, 0, 0
        run.sample_baseline, run.resource_failures = None, soak.deque(maxlen=20)
        row = dict(rss_kib=1000, threads=5, descriptors=8, age_seconds=1)
        run.check_resources(row)
        self.assertIsNone(run.sample_baseline)
        row = dict(row, age_seconds=5)
        run.check_resources(row)
        with self.assertRaisesRegex(soak.InvariantError, "descriptors grew by more than 20"):
            run.check_resources(dict(row, age_seconds=7, descriptors=29))
        run.generation, run.sample_baseline = 2, None
        run.check_resources(dict(row, age_seconds=7, descriptors=29))
        self.assertEqual(run.usable_generation_count, 2)

    def test_late_ack_for_previous_question_cannot_poison_current_watermark(self):
        proxy = soak.FaultProxy(1, mock.Mock())
        self.addCleanup(proxy.server_close)
        proxy.question = 2
        proxy.lease_questions["old"] = 1
        request = b'POST /v1/runs/old/text HTTP/1.1\r\n\r\n{"done":true}'
        proxy.observe(request, b'HTTP/1.1 200 OK\r\n\r\n{"acked":500}')
        self.assertEqual(proxy.acked, 0)

    def test_failed_cut_is_not_applied_and_unused_arm_is_recorded(self):
        emit = mock.Mock()
        proxy = soak.FaultProxy(1, emit)
        self.addCleanup(proxy.server_close)
        proxy.arm("cut", "pc_to_phone", 0.5, 0.1)
        with self.assertRaises(BrokenPipeError):
            proxy.forward(mock.Mock(sendall=mock.Mock(side_effect=BrokenPipeError)), b"abc", "pc_to_phone")
        self.assertEqual(emit.call_args.args[0], "not_applied")
        proxy.arm("cut", "pc_to_phone", 0.5, 0.1)
        proxy.arm("stall", "phone_to_pc", 0.5, 0.1)
        self.assertEqual(emit.call_args.args[0], "not_applied")
        emit.reset_mock()
        destination = mock.Mock()
        proxy.forward(destination, b"abc", "phone_to_pc")
        self.assertEqual(emit.call_args.args[0], "stall")
        self.assertGreaterEqual(emit.call_args.kwargs["seconds"], 0.1)

    def test_unused_fault_does_not_block_completion_or_count_as_applied(self):
        emit = mock.Mock()
        proxy = soak.FaultProxy(1, emit)
        self.addCleanup(proxy.server_close)
        run = soak.Soak.__new__(soak.Soak)
        run.proxy, run.fault_counts = proxy, soak.Counter()
        proxy.arm("cut", "phone_to_pc", 0.5, 0.1)
        self.assertIsNone(run.pending_fault("cut", 0, "done"))
        self.assertEqual(emit.call_args.args[0], "not_applied")
        proxy.inflight = 1
        self.assertEqual(run.pending_fault("stall", 0, "done"), "stall")
        run.fault_counts["stall"] = 1
        self.assertIsNone(run.pending_fault("stall", 0, "done"))

    def test_long_pull_allows_terminal_completion_and_records_interleaving(self):
        run = soak.Soak.__new__(soak.Soak)
        run.started, run.pull_trace = 0, soak.deque(maxlen=256)
        pull = dict(end=4, pause_seen=False, done_seen=False, required=True)
        self.assertIsNone(run.check_pull(pull, "done", dict(text="abc"), 4.1))
        self.assertEqual(run.pull_trace[-1]["state"], "done")
        pull = dict(end=4, pause_seen=False, done_seen=False, required=True)
        with self.assertRaisesRegex(soak.InvariantError, "long pull"):
            run.check_pull(pull, "answering", dict(text="abc"), 4.1)

    def test_diagnostics_redact_hex_and_text_markers(self):
        secret = "a" * 64
        self.assertNotIn(secret, soak.excerpt("error " + secret))
        for marker in ("--show-text", "--show_text", "Question:", "Text:"):
            self.assertNotIn("PRIVATE", soak.excerpt("prefix " + marker + " PRIVATE"))
        self.assertLessEqual(len(soak.excerpt("x" * 10000)), 160)

    def test_test_flags_require_explicit_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(sys, "stderr"):
            for flag in ("--self-test", "--plant-drop-token"):
                with self.assertRaises(SystemExit) as raised:
                    soak.arguments([flag])
                self.assertEqual(raised.exception.code, 2)

    def test_resistant_child_is_killed_and_reaped_with_bounded_diagnostics(self):
        script = (
            "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); print('a'*10000,flush=True); time.sleep(60)"
        )
        with tempfile.TemporaryDirectory() as folder:
            child = soak.Child("test", [sys.executable, "-B", "-c", script], os.environ.copy(), folder)
            try:
                deadline = time.monotonic() + 3
                while not child.lines and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(child.lines)
                self.assertTrue(all("withheld" in row for row in child.lines))
                child.stop()
                self.assertEqual(child.process.returncode, -signal.SIGKILL)
                self.assertFalse(child.reader.is_alive())
                with self.assertRaises(ProcessLookupError):
                    os.kill(child.process.pid, 0)
            finally:
                if child.process.poll() is None:
                    child.process.kill()
                    child.process.wait()
                child.reader.join(2)

    def test_cleanup_continues_after_child_and_server_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            run = soak.Soak(soak.arguments(["--out", folder]))
            process = mock.Mock()
            process.poll.return_value = 0
            child = mock.Mock(process=process)
            child.stop.side_effect = ValueError("diagnostic must not escape")
            enrolment = mock.Mock()
            enrolment.poll.return_value = 0
            failing = mock.Mock(halt=soak.threading.Event(), lock=soak.threading.Lock(), sockets=set())
            failing.stop.side_effect = ValueError("private diagnostic")
            failing.shutdown.side_effect = ValueError("private diagnostic")
            other = mock.Mock()
            run.children, run.enrol_process, run.servers = [child], enrolment, [other, failing]
            with self.assertRaisesRegex(soak.HarnessError, "child cleanup: ValueError") as raised:
                run.cleanup()
            self.assertNotIn("private", str(raised.exception))
            self.assertFalse(run.tmp.exists())
            process.wait.assert_called()
            enrolment.wait.assert_called()
            failing.server_close.assert_called_once()
            other.stop.assert_called_once()
            self.assertTrue(run.fault_file.closed)

    def test_fault_history_is_bounded_and_full_log_is_streamed(self):
        with tempfile.TemporaryDirectory() as folder:
            run = soak.Soak(soak.arguments(["--out", folder]))
            try:
                with mock.patch.object(soak.sys, "stdout"):
                    for _ in range(1000):
                        run.emit("cut", question=1)
                self.assertLess(len(run.faults), 1000)
                self.assertEqual(run.fault_counts["cut"], 1000)
                events = (Path(folder) / "faults.jsonl").read_text().splitlines()
                self.assertEqual(len(events), 1000)
                self.assertTrue(all(json.loads(event)["type"] == "cut" for event in events))
            finally:
                run.cleanup()

    def test_no_sampling_cannot_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            args = soak.arguments(["--out", folder])
            with mock.patch.object(soak.Soak, "run"), mock.patch.object(soak.sys, "stdout"):
                self.assertEqual(soak.main(["--out", str(args.out)]), 2)
            report = json.loads((Path(folder) / "report.json").read_text())
            self.assertIn("no usable samples", report["harness_error"])


@unittest.skipUnless(os.environ.get("OPOS_SOAK") == "1", "process soak is optional; set OPOS_SOAK=1 (not part of CI)")
class SoakProcesses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deadline = time.monotonic() + 90

    def run_soak(self, mode, planted=False, interrupt=False):
        with tempfile.TemporaryDirectory(prefix="opos-soak-selftest-") as folder:
            command = [
                sys.executable,
                "-B",
                "-c",
                PROCESS_LAUNCHER,
                str(ROOT / "tools/gpu-link-soak.py"),
                "--mode",
                mode,
                "--questions",
                "5",
                "--seed",
                "743",
                "--self-test",
                "--token-rate",
                "400",
                "--pull-every",
                "0.25-0.35",
                "--question-timeout",
                "20",
                "--sample-every",
                "1",
                "--out",
                folder,
            ]
            if planted:
                command.extend(["--plant-drop-token", "--pull-every", "20-21"])
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT) as process:
                try:
                    if interrupt:
                        try:
                            process.communicate(timeout=1.5)
                        except subprocess.TimeoutExpired:
                            process.send_signal(signal.SIGTERM)
                    output, error = process.communicate(timeout=max(0.1, self.deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.send_signal(signal.SIGTERM)
                    output, error = process.communicate(timeout=12)
                    self.fail("process self-tests exceeded 90 seconds: " + output.decode() + error.decode())
            report = json.loads((Path(folder) / "report.json").read_text())
            self.assertTrue(report["temporary_directory_removed"])
            self.assertEqual(process.returncode, report["exit_status"], output.decode() + error.decode())
            for child in report["child_processes"]:
                self.assertIsNotNone(child["returncode"])
                with self.assertRaises(ProcessLookupError, msg=f"child still exists: {child}"):
                    os.kill(child["pid"], 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(report["enrolment_pid"], 0)
            self.assertTrue(report["resource_samples"], "no resource samples")
            generations = {row["generation"] for row in report["resource_samples"]}
            self.assertEqual(generations, set(range(1, len(report["restart_ready_seconds"]) + 2)))
            self.assertTrue(all(row["source"] == "self-test stub" for row in report["resource_samples"]))
            return process.returncode, report, output.decode() + error.decode()

    def check_mode(self, mode):
        status, report, output = self.run_soak(mode)
        self.assertEqual(status, 0, output)
        self.assertGreaterEqual(report["questions"]["finished"], 1, output)
        self.assertTrue(report["self_test"])
        self.assertFalse(report["plant_drop_token"])
        self.assertGreaterEqual(report["stale_lease_probes"], 1)
        if mode in ("pull", "both"):
            self.assertTrue({"cut", "stall", "short pull", "long pull"} <= set(report["fault_coverage"]), output)
            pulls = [event["seconds"] for event in report["fault_log"] if event["type"] == "pull"]
            self.assertTrue(any(seconds < 2 for seconds in pulls), output)
            self.assertTrue(any(seconds > 3.5 for seconds in pulls), output)
            self.assertGreaterEqual(report["pauses"], 1, output)
            self.assertGreaterEqual(report["resumes"], 1, output)
        else:
            self.assertEqual(set(report["faults_by_type"]), {"kill"})
        if mode in ("kill", "both"):
            self.assertEqual(report["faults_by_type"]["kill"], 1, output)
            self.assertEqual(len(report["restart_ready_seconds"]), 1)
            self.assertLess(report["restart_ready_seconds"][0], 10)
        return report

    def test_pull(self):
        self.check_mode("pull")

    def test_kill(self):
        self.check_mode("kill")

    def test_both(self):
        self.check_mode("both")

    def test_sigterm_reaps_children_and_reports_interruption(self):
        status, report, output = self.run_soak("pull", interrupt=True)
        self.assertEqual(status, 1, output)
        self.assertIn("interrupted", report["failures"][0]["reason"])

    def test_planted_missing_token_is_an_invariant_failure(self):
        status, report, output = self.run_soak("pull", planted=True)
        self.assertEqual(status, 1, output)
        self.assertTrue(report["plant_drop_token"])
        self.assertIn("mismatch", report["failures"][0]["reason"])
        self.assertIn("mismatch", output)
        self.assertEqual(report["questions"]["failed"], 1)
        self.assertTrue(report["failures"][0]["expected"])
        self.assertNotEqual(report["failures"][0]["expected"], report["failures"][0]["actual"])
        self.assertLessEqual(len(report["failures"][0]["expected"]), 200)


if __name__ == "__main__":
    unittest.main()
