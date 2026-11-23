# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only PC preflight against test-owned loopback servers and executables."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/gpu-link-preflight.py"
SPEC = importlib.util.spec_from_file_location("gpu_preflight", TOOL)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)
NAMES = [
    "python",
    "adb device",
    "adb forward",
    "link hello",
    "key file",
    "gpu",
    "chat server first answer",
    "chat server continuation",
    "gpu use and speed",
    "stand-in tool",
]


class Handler(BaseHTTPRequestHandler):
    """Only an unsigned hello or one of the three fixed chat prompts is allowed."""

    def log_message(self, *args):
        pass

    def reply(self, code, body, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.requests.append(("GET", self.path, dict(self.headers), None))
        if self.path != "/v1/hello":
            self.reply(404, b"{}")
            return
        value = dict(proto=1, label="Test phone", epoch="a" * 32)
        value.update(self.server.scenario.get("hello_body", {}))
        self.reply(
            self.server.scenario.get("hello_status", 200),
            self.server.scenario.get("hello_raw", json.dumps(value).encode()),
        )

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(("POST", self.path, dict(self.headers), payload))
        scenario = self.server.scenario
        if scenario.get("reject_temperature") and "temperature" in payload:
            self.reply(400, b"{}")
            return
        messages = payload["messages"]
        prompt = messages[0]["content"]
        if prompt == preflight.FIRST:
            text = "OK."
        elif prompt == preflight.CONTINUE:
            text = scenario.get("continuation", "  5, 6, 7, 8, 9, 10, 11, 12")
        elif prompt == preflight.GPU_PROMPT:
            text = "1. Two is the first prime."
        else:
            self.reply(400, b"{}")
            return
        text = scenario.get("text", text)
        delta = (
            {scenario.get("reasoning_field", "reasoning_content"): "PRIVATE REASONING"}
            if scenario.get("reasoning")
            else {"content": text}
        )
        event = {"choices": [{"delta": delta, "finish_reason": scenario.get("finish", "stop")}]}
        usage = {"choices": [], "usage": {"completion_tokens": scenario.get("tokens", 150)}}
        data = b"data: " + json.dumps(event).encode() + b"\n\n"
        if not scenario.get("no_usage"):
            data += b"data: " + json.dumps(usage).encode() + b"\n\n"
        data += b"data: [DONE]\n\n"
        if scenario.get("late_reasoning"):
            data = data.replace(
                b"data: [DONE]",
                b'data: {"choices":[{"delta":{"reasoning":"PRIVATE REASONING"}}]}\n\ndata: [DONE]',
            )
        if scenario.get("truncated"):
            data = b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
        if scenario.get("done_only"):
            data = b"data: [DONE]\n\n"
        for key, wanted in (
            ("first_events", preflight.FIRST),
            ("continuation_events", preflight.CONTINUE),
            ("gpu_events", preflight.GPU_PROMPT),
        ):
            if prompt == wanted and key in scenario:
                data = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in scenario[key])
                data += b"data: [DONE]\n\n"
        if "raw_stream" in scenario:
            data = scenario["raw_stream"]
        if "stream_tail" in scenario:
            data = data.replace(b"data: [DONE]", scenario["stream_tail"] + b"data: [DONE]")
        self.send_response(scenario.get("chat_status", 200))
        self.send_header("Content-Type", scenario.get("content_type", "text/event-stream; charset=utf-8"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        # A short real delay plus an injected threshold exercises the cold warning
        # without a six-second sleep. GPU probes last long enough to sample a thread.
        delay = scenario.get("first_delay", 0) if prompt == preflight.FIRST else 0
        if prompt == preflight.GPU_PROMPT:
            delay = scenario.get("gpu_delay", 0.28)
        time.sleep(delay)
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The check may time out before the fixture finishes.


@unittest.skipIf(os.name == "nt", "POSIX fake executable fixtures; Windows companion run is NOT_RUN")
class PreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01})
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join(2)
        cls.server.server_close()

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="gpu-preflight-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "commands.jsonl"
        self.config = self.root / "config.json"
        self.key = self.root / "pc.key"
        self.key.write_bytes(b"K" * 32)
        self.key.chmod(0o600)
        self.engine_log = self.root / "engine.log"
        self.engine_log.write_text(
            "ggml_cuda_init: found 1 CUDA devices:\nllm_load_tensors: offloaded 33/33 layers to GPU\n",
            encoding="utf-8",
        )
        self.port = self.server.server_port
        with self.engine_log.open("a") as log:
            log.write(f"main: server is listening on http://127.0.0.1:{self.port}\n")
        self.phone = f"127.0.0.1:{self.port}"
        self.server.requests = []
        self.server.scenario = {}
        self.settings = dict(
            devices="List of devices attached\nfixture-phone device\n",
            forwards=f"fixture-phone tcp:{self.port} tcp:8767\n",
            utilization=60,
        )
        self.configure()
        self.fake(
            "adb",
            """
if sys.argv[1:] == ['devices']:
    print(config['devices'], end='')
elif sys.argv[1:] == ['forward', '--list']:
    print(config['forwards'], end='')
else:
    sys.exit(91)
""",
        )
        self.fake(
            "nvidia-smi",
            """
if sys.argv[1:] != [
    '--query-gpu=name,memory.total,driver_version,utilization.gpu,memory.used',
    '--format=csv,noheader,nounits',
]:
    sys.exit(92)
if config.get('gpu_fail'):
    sys.exit(93)
count_file = pathlib.Path(__file__).with_name('gpu-count')
count = int(count_file.read_text()) if count_file.exists() else 0
count_file.write_text(str(count + 1))
utilization = config['utilization'] if count >= 2 else 5
print('Fixture NVIDIA, 8192, 555.0, %s, %s' % (utilization, 100 + count))
""",
        )
        self.environment = dict(os.environ, PATH=str(self.root), PYTHONDONTWRITEBYTECODE="1", PYTHONWARNINGS="error")
        self.env_patch = patch.dict(os.environ, self.environment, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def configure(self, **values):
        self.settings.update(values)
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")

    def fake(self, name, body):
        script = self.root / name
        script.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "root = pathlib.Path(__file__).parent\n"
            "config = json.loads((root / 'config.json').read_text())\n"
            "with (root / 'commands.jsonl').open('a') as log:\n"
            "    log.write(json.dumps([pathlib.Path(__file__).name] + sys.argv[1:]) + '\\n')\n" + body,
            encoding="utf-8",
        )
        script.chmod(0o700)

    def options(self, *extra):
        return [
            "--phone",
            self.phone,
            "--server",
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            "--model",
            "fixture",
            "--key-file",
            str(self.key),
            "--timeout",
            "2",
            *extra,
        ]

    def run_checks(self, *extra, full=False):
        args = preflight.arguments(self.options(*([] if full else ["--no-gpu-probe"]), *extra))
        self.results = preflight.Preflight(args).run()
        self.by_name = {row["name"]: row for row in self.results}
        return self.by_name

    def status(self, name, expected, detail=""):
        result = self.by_name[name]
        self.assertEqual(result["status"], expected, result)
        self.assertIn(detail, result["detail"])
        if expected in ("WARN", "FAIL"):
            self.assertTrue(result["fix"])
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def child(self, *extra):
        return subprocess.run(
            [sys.executable, "-B", str(TOOL), *self.options(*extra)],
            capture_output=True,
            text=True,
            timeout=5,
            env=self.environment,
            check=False,
        )

    def test_all_checks_pass(self):
        self.server.scenario["gpu_delay"] = 1.1
        self.run_checks("--engine-log", str(self.engine_log), full=True)
        self.assertEqual([row["name"] for row in self.results], NAMES)
        self.assertTrue(all(row["status"] == "PASS" for row in self.results), self.results)
        self.assertIn("protocol 1", self.by_name["link hello"]["detail"])
        self.assertIn("readiness not reported", self.by_name["link hello"]["detail"])
        self.assertIn("8192 MiB", self.by_name["gpu"]["detail"])
        self.assertIn("enrolment is checked by the stand-in's first status line", self.by_name["key file"]["detail"])
        self.assertIn("tokens/s (server usage)", self.by_name["gpu use and speed"]["detail"])
        self.assertIn("measured by this check, not a benchmark", self.by_name["gpu use and speed"]["detail"])
        self.assertIn("memory growth", self.by_name["gpu use and speed"]["detail"])
        self.assertIn(
            "engine log and utilization agree: evidence, not proof", self.by_name["gpu use and speed"]["detail"]
        )

    def test_adb_unauthorized(self):
        self.configure(devices="fixture-phone unauthorized\n")
        self.run_checks()
        row = self.status("adb device", "FAIL", "unauthorized")
        self.assertIn('Tap "Always allow from this computer"', row["fix"])
        self.status("stand-in tool", "PASS")
        self.status("chat server continuation", "PASS")

    def test_adb_offline(self):
        self.configure(devices="fixture-phone offline\n")
        self.run_checks()
        self.status("adb device", "FAIL", "offline")

    def test_two_devices(self):
        self.configure(devices="fixture-phone device\nother-fixture device\n")
        self.run_checks()
        self.status("adb device", "WARN", "several")
        self.status("adb forward", "WARN", "ambiguous")

    def test_selected_serial(self):
        self.configure(devices="fixture-phone device\nother-fixture unauthorized\n")
        self.run_checks("--serial", "fixture-phone")
        self.status("adb device", "PASS")
        self.status("adb forward", "PASS")

    def test_no_device(self):
        self.configure(devices="List of devices attached\n\n")
        self.run_checks()
        self.status("adb device", "FAIL", "no selected")

    def test_forward_missing(self):
        self.configure(forwards="")
        self.run_checks()
        row = self.status("adb forward", "FAIL", "no matching")
        self.assertIn(f"adb forward tcp:{self.port} tcp:8767", row["fix"])
        self.assertIn("does not survive a cable pull", row["fix"])

    def test_forward_present_with_different_local_port(self):
        self.configure(forwards=f"fixture-phone tcp:{1 if self.port != 1 else 2} tcp:8767\n")
        self.run_checks()
        self.status("adb forward", "FAIL")

    def test_forward_for_other_device(self):
        self.configure(forwards=f"other-fixture tcp:{self.port} tcp:8767\n")
        self.run_checks("--serial", "fixture-phone")
        self.status("adb forward", "FAIL")

    def test_hello_refused(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            # A bound socket with no listener deterministically refuses connections.
            self.run_checks("--phone", f"127.0.0.1:{reserved.getsockname()[1]}")
        row = self.status("link hello", "FAIL", "unavailable")
        self.assertIn("OPOS_GPU_LINK=1", row["fix"])

    def test_hello_429(self):
        self.server.scenario["hello_status"] = 429
        self.run_checks()
        self.status("link hello", "FAIL", "429")
        self.assertEqual(sum(row[0] == "GET" for row in self.server.requests), 1)

    def test_hello_model_ready_field_refused(self):
        self.server.scenario["hello_body"] = {"model_ready": False}
        self.run_checks()
        self.status("link hello", "FAIL", "phone reply refused by the stand-in's rules")

    def test_hello_invalid_protocol(self):
        self.server.scenario["hello_body"] = {"proto": 2}
        self.run_checks()
        self.status("link hello", "FAIL", "phone reply refused by the stand-in's rules")

    def test_chat_server_cold(self):
        self.server.scenario["first_delay"] = 0.03
        with patch.object(preflight, "SLOW_SECONDS", 0.01):
            self.run_checks()
        row = self.status("chat server first answer", "WARN", "first usable content")
        self.assertIn("full question", row["fix"])

    def test_chat_timeout(self):
        self.server.scenario["first_delay"] = 0.08
        self.run_checks("--timeout", "0.03")
        self.status("chat server first answer", "FAIL", "timed out")
        time.sleep(0.08)  # Let the fixture finish before its scenario is reused.

    def test_reasoning_only(self):
        self.server.scenario["reasoning"] = True
        self.run_checks()
        row = self.status("chat server first answer", "FAIL", "only reasoning")
        self.assertIn("--reasoning off", row["fix"])
        self.assertNotIn("PRIVATE REASONING", json.dumps(self.results))

    def test_http_500(self):
        self.server.scenario["chat_status"] = 500
        self.run_checks()
        self.status("chat server first answer", "FAIL", "HTTP 500")
        self.status("chat server continuation", "FAIL", "HTTP 500")

    def test_wrong_content_type(self):
        self.server.scenario["content_type"] = "application/json"
        self.run_checks()
        self.status("chat server first answer", "FAIL", "not text/event-stream")

    def test_continuation_continues(self):
        self.run_checks()
        self.status("chat server continuation", "PASS", "continues")

    def test_continuation_restarts(self):
        self.server.scenario["continuation"] = "  1, 2, 3, 4, 5"
        self.run_checks()
        self.status("chat server continuation", "FAIL", "restarts the answer")

    def test_continuation_unclear(self):
        self.server.scenario["continuation"] = "unclear " + "x" * 60 + "HIDDEN-TAIL"
        self.run_checks()
        row = self.status("chat server continuation", "WARN", "unclear:")
        excerpt = json.loads(row["detail"].split(": ", 1)[1])
        self.assertEqual(excerpt, self.server.scenario["continuation"][:40])
        self.assertNotIn("HIDDEN-TAIL", json.dumps(self.results))

    def test_continuation_empty_fails(self):
        self.server.scenario["continuation"] = ""
        self.run_checks()
        self.status("chat server continuation", "FAIL", "empty continuation")

    def test_continuation_temperature_zero(self):
        self.run_checks()
        continuation = [row[3] for row in self.server.requests if row[0] == "POST"][-1]
        self.assertEqual(continuation["temperature"], 0)

    def test_continuation_temperature_rejected(self):
        self.server.scenario["reject_temperature"] = True
        self.run_checks()
        self.status("chat server continuation", "PASS", "continues")
        posts = [row[3] for row in self.server.requests if row[0] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts[1]["temperature"], 0)
        self.assertNotIn("temperature", posts[2])
        self.assertEqual(posts[1]["messages"], posts[2]["messages"])

    def test_nvidia_smi_missing(self):
        (self.root / "nvidia-smi").unlink()
        self.run_checks(full=True)
        self.status("gpu", "WARN", "may have no NVIDIA driver")
        self.status("gpu use and speed", "WARN", "nvidia-smi unavailable")

    def test_nvidia_smi_fails(self):
        self.configure(gpu_fail=True)
        self.run_checks()
        self.status("gpu", "WARN", "may have no NVIDIA driver")

    def test_utilization_five_percent(self):
        self.configure(utilization=5)
        self.server.scenario["gpu_delay"] = 1.1
        self.run_checks(full=True)
        row = self.status("gpu use and speed", "WARN", "peak 5.0%")
        self.assertIn("CUDA device", row["fix"])
        self.assertIn("--engine-log not supplied", row["detail"])

    def test_chunk_speed_fallback(self):
        self.server.scenario["no_usage"] = True
        self.run_checks(full=True)
        self.status("gpu use and speed", "WARN", "content chunks/s (token count unavailable)")

    def test_stream_reported_errors(self):
        for tail in (
            b"event: error\n\n",
            b'data: {"error":{"message":"out of memory"}}\n\n',
            b'data: {"choices":[{"error":"out of memory"}]}\n\n',
            b'data: {"choices":[{"delta":{"error":"out of memory"}}]}\n\n',
        ):
            with self.subTest(tail=tail):
                self.server.scenario = {"stream_tail": tail}
                self.run_checks(full=True)
                for name in ("chat server first answer", "chat server continuation", "gpu use and speed"):
                    self.status(name, "FAIL", "server reported an error:")

    def test_error_finish_reason(self):
        self.server.scenario = {"finish": "error"}
        self.run_checks(full=True)
        for name in ("chat server first answer", "chat server continuation", "gpu use and speed"):
            self.status(name, "FAIL", "server reported an error: finish_reason: error")

    def test_unexpected_finish_reason(self):
        for finish in ("content_filter", "tool_calls", "unknown", "", 0, False):
            with self.subTest(finish=finish):
                self.server.scenario = {"finish": finish}
                self.run_checks(full=True)
                for name in ("chat server first answer", "chat server continuation", "gpu use and speed"):
                    self.status(name, "WARN", "unexpected finish reason")

    def test_non_event_body(self):
        for scenario in (
            {"raw_stream": b'{"error":"out of memory"}'},
            {"content_type": "application/json"},
        ):
            self.server.scenario = scenario
            self.run_checks(full=True)
            for name in ("chat server first answer", "chat server continuation", "gpu use and speed"):
                self.status(name, "FAIL", "server reported an error:")

    def test_invisible_content(self):
        for text in ("\u200b", "\u202e", "\u0301", " \u200b\u202e\u0301"):
            with self.subTest(text=repr(text)):
                self.server.scenario = {"text": text}
                self.run_checks(full=True)
                self.status("chat server first answer", "FAIL", "no content")
                self.status("chat server continuation", "FAIL", "empty continuation")
                self.status("gpu use and speed", "FAIL", "no content")

    def test_continuation_pass_scope(self):
        self.run_checks()
        row = self.status("chat server continuation", "PASS", "the reply continued the stored partial")
        self.assertIn("prompt cache may behave differently on a long real answer", row["detail"])

    def test_adb_malformed_lines(self):
        args = preflight.arguments(self.options())
        for line in (
            "bad\rfixture-phone device\n",
            "lonely\n",
            "bad!serial device\n",
            "a" * 65 + " device\n",
            "fixture-phone \x1b[2J\x1b[HPASS\n",
            "bad\u202eserial device\n",
        ):
            with self.subTest(line=repr(line)), patch.object(preflight, "command", return_value=line):
                checks = preflight.Preflight(args)
                status, detail, _ = checks.adb_device()
                self.assertEqual((status, detail), ("WARN", "ignored malformed adb line"))
                self.assertIsNone(checks.selected)
                self.assertEqual(checks.adb_forward()[0], "WARN")
        with patch.object(preflight, "command", return_value="List of devices attached\r\nfixture-phone device\r\n"):
            self.assertEqual(preflight.Preflight(args).adb_device()[0], "PASS")

    def test_display_controls_removed(self):
        # Cover terminal and decoded JSON details, including remote error excerpts.
        self.server.scenario = {"stream_tail": b'data: {"error":"bad\\u001b[2J\\u202e\\nmessage"}\n\n'}
        self.configure(devices="fixture-phone \x1b[2J\x1b[HPASS\n")
        output = self.root / "safe.json"
        child = self.child("--json", str(output))
        self.assertEqual(child.returncode, 1, child.stdout + child.stderr)
        rows = json.loads(output.read_text())
        for row in rows:
            for field in ("detail", "fix"):
                self.assertFalse(any(preflight.unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in row[field]), row)
        self.assertNotIn("\x1b", child.stdout)
        self.assertNotIn("\u202e", child.stdout)
        first = next(row for row in rows if row["name"] == "chat server first answer")
        self.assertIn("server reported an error:", first["detail"])
        self.assertNotIn("\\u001b", first["detail"])
        self.assertNotIn("\\n", first["detail"])
        self.assertLessEqual(len(first["detail"].split(": ", 1)[1]), 60)
        checks = preflight.Preflight(preflight.arguments([]))
        checks.add("fixture", lambda: ("WARN", "GPU\x1b[2J\u202e\r\n", "phone\x1b\t"))
        self.assertEqual(checks.results[0]["detail"], "GPU[2J")
        self.assertEqual(checks.results[0]["fix"], "phone")

    def log_result(self):
        return preflight.engine_offload(self.engine_log, self.port)

    def test_engine_log_port_and_order(self):
        good = self.engine_log.read_text()
        for text in (
            good.rsplit("main:", 1)[0],
            good.replace(f":{self.port}", ":1"),
            good.splitlines()[-1] + "\n" + good.rsplit("main:", 1)[0],
        ):
            self.engine_log.write_text(text)
            status, detail = self.log_result()
            self.assertEqual(status, "WARN", detail)
        self.engine_log.write_text(good.replace("main: server is ", ""))
        self.assertEqual(self.log_result()[0], "PASS")

    def test_engine_log_age(self):
        old = time.time() - 13 * 3600
        os.utime(self.engine_log, (old, old))
        status, detail = self.log_result()
        self.assertEqual(status, "WARN", detail)
        self.assertIn("stale: 13.00 hours old", detail)
        self.assertEqual(preflight.engine_offload(self.engine_log, self.port, 14)[0], "PASS")
        for value in ("0", "-1", "nan", "inf"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                preflight.arguments(["--engine-log-max-age", value])

    def test_engine_log_failure_after_offload(self):
        good = self.engine_log.read_text()
        for failure in (
            "FAILED TO allocate",
            "out of memory",
            "cudaMalloc failed",
            "no CUDA-capable device",
            "CUDA error",
            "falling back",
            "offloaded 0/33 layers to GPU",
            "error: context",
        ):
            with self.subTest(failure=failure):
                self.engine_log.write_text(good + failure + "\n")
                self.assertEqual(self.log_result()[0], "FAIL")
        self.engine_log.write_text("error: an earlier attempt\n" + good)
        self.assertEqual(self.log_result()[0], "PASS")

    def test_engine_log_special_files(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        directory = self.root / "directory"
        directory.mkdir()
        sockpath = self.root / "socket"
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(sockpath))
            for path in (fifo, directory, sockpath):
                with self.subTest(path=path):
                    child = self.child("--engine-log", str(path))
                    self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
                    self.assertIn("WARN gpu use and speed: engine log unavailable", child.stdout)
        info = self.engine_log.stat()
        device_info = os.stat_result((preflight.stat.S_IFCHR, *tuple(info)[1:]))
        with (
            patch.object(preflight.os, "stat", return_value=device_info),
            patch.object(preflight.os, "open", side_effect=AssertionError("must not open a device")),
            self.assertRaises(OSError),
        ):
            preflight.engine_offload(self.engine_log, self.port)
        with patch.object(preflight.os, "open", side_effect=AssertionError("must not open FIFO")):
            with self.assertRaises(OSError):
                preflight.engine_offload(fifo, self.port)

    def test_engine_log_aliases_refused(self):
        for option, original in (("--key-file", self.key), ("--json", self.engine_log)):
            alias = self.root / (option[2:] + "-alias")
            os.link(original, alias)
            before = original.read_bytes()
            for path in (original, alias):
                with self.subTest(option=option, path=path):
                    output = self.root / "fresh-results.json"
                    extra = ["--json", str(output)] if option == "--key-file" else ["--json", str(original)]
                    child = self.child("--engine-log", str(path), *extra)
                    self.assertEqual(child.returncode, 2, child.stdout + child.stderr)
                    self.assertIn("Engine log refused: same file", child.stderr)
                    self.assertEqual(original.read_bytes(), before)
                    self.assertEqual(child.stdout, "")
                    self.assertFalse(output.exists())
        with (
            patch.object(preflight.os, "open", side_effect=AssertionError("must not read the key")),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(preflight.main(self.options("--engine-log", str(self.key))), 2)
        with patch.object(preflight.os.path, "samefile", return_value=False):
            with self.assertRaises(preflight.LogAliasError):
                preflight.engine_offload(self.key, self.port, protected=(self.key,))

    def test_engine_log_symlink_and_limit(self):
        link = self.root / "log-link"
        link.symlink_to(self.engine_log)
        with self.assertRaises(OSError):
            preflight.engine_offload(link, self.port)
        good = self.engine_log.read_bytes()
        self.engine_log.write_bytes(good + b" " * preflight.ENGINE_LOG_LIMIT + b"falling back to CPU\n")
        status, detail = self.log_result()
        self.assertEqual(status, "WARN", detail)
        self.assertIn("2 MiB scan limit", detail)
        # The read is bounded even for a large regular file.
        original = os.fdopen
        reads = []

        class Reader:
            def __init__(self, fd, mode):
                self.handle = original(fd, mode)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def fileno(self):
                return self.handle.fileno()

            def read(self, size):
                reads.append(size)
                return self.handle.read(size)

        with patch.object(preflight.os, "fdopen", side_effect=Reader):
            self.log_result()
        self.assertEqual(reads, [2 * 1024 * 1024])

    def test_gpu_sampler_runs_during_stream(self):
        self.run_checks(full=True)
        gpu_calls = [call for call in self.calls() if call[0] == "nvidia-smi"]
        self.assertGreaterEqual(len(gpu_calls), 4)  # Inventory, baseline, thread and final sample.
        self.assertFalse(any(t.name == "preflight-gpu" for t in threading.enumerate()))

    def test_key_mode_0644(self):
        self.key.chmod(0o644)
        self.run_checks()
        self.status("key file", "FAIL", "0600")
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o644)

    def test_key_wrong_size(self):
        self.key.write_bytes(b"short fixture")
        self.run_checks()
        self.status("key file", "FAIL", "32-byte")
        self.assertEqual(self.key.read_bytes(), b"short fixture")

    def test_key_wrong_owner(self):
        with patch.object(preflight.os, "getuid", return_value=-1):
            self.run_checks()
        self.status("key file", "FAIL", "current owner")

    def test_missing_key_not_created(self):
        self.key.unlink()
        self.run_checks()
        self.status("key file", "FAIL", "unavailable")
        self.assertFalse(self.key.exists())

    def test_key_symlink_refused(self):
        link = self.root / "key-link"
        link.symlink_to(self.key)
        self.run_checks("--key-file", str(link))
        self.status("key file", "FAIL")

    def test_quick(self):
        self.run_checks("--quick", full=True)
        for name in ("gpu", "chat server first answer", "chat server continuation", "gpu use and speed"):
            self.status(name, "SKIP", "--quick")
        self.assertFalse(any(call[0] == "nvidia-smi" for call in self.calls()))
        self.assertEqual([row[0] for row in self.server.requests], ["GET"])
        self.status("link hello", "PASS")

    def test_no_gpu_probe(self):
        self.run_checks()
        self.status("gpu use and speed", "SKIP", "--no-gpu-probe")
        self.status("gpu", "PASS")
        self.status("chat server first answer", "PASS")
        self.assertEqual(sum(row[0] == "POST" for row in self.server.requests), 2)
        self.assertEqual(sum(call[0] == "nvidia-smi" for call in self.calls()), 1)

    def test_ssh_transport(self):
        self.run_checks("--transport", "ssh")
        self.status("adb device", "SKIP", "SSH")
        self.status("adb forward", "SKIP", "SSH")
        self.assertFalse(any(call[0] == "adb" for call in self.calls()))
        self.status("link hello", "PASS")

    def test_json_shape_and_privacy(self):
        output = self.root / "results.json"
        child = self.child("--json", str(output), "--color")
        self.assertEqual(child.returncode, 0, child.stderr + child.stdout)
        rows = json.loads(output.read_text())
        self.assertEqual([row["name"] for row in rows], NAMES)
        for row in rows:
            self.assertEqual(set(row), {"name", "status", "detail", "fix", "seconds"})
            self.assertIsInstance(row["seconds"], (float, int))
            self.assertGreaterEqual(row["seconds"], 0)
        self.assertIn("Summary: 9 PASS, 1 WARN, 0 FAIL, 0 SKIP", child.stdout)
        for private in ("K" * 32, "PRIVATE REASONING", "Two is the first prime", "OK.", "\033["):
            self.assertNotIn(private, child.stdout + output.read_text())

    def test_json_write_failure_keeps_final_summary(self):
        child = self.child("--quick", "--json", str(self.root))
        self.assertEqual(child.returncode, 2)
        self.assertIn("path already exists", child.stderr)

    def test_exit_zero_with_warnings(self):
        self.configure(devices="fixture-phone device\nother-fixture device\n")
        child = self.child("--no-gpu-probe")
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn("WARN adb device", child.stdout)
        self.assertIn("Fix:", child.stdout)

    def test_exit_one_on_failure(self):
        self.configure(forwards="")
        child = self.child("--quick")
        self.assertEqual(child.returncode, 1, child.stderr)
        self.assertIn("FAIL adb forward", child.stdout)
        self.assertIn("PASS stand-in tool", child.stdout)
        self.assertIn("1 FAIL", child.stdout)

    def test_exit_two_on_usage_error(self):
        child = self.child("--timeout", "nan")
        self.assertEqual(child.returncode, 2)
        self.assertIn("--timeout", child.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.server.requests, [])

    def test_no_forbidden_subprocess_or_phone_request(self):
        original = subprocess.run
        started = []

        def checked(argv, **kwargs):
            self.assertIsInstance(argv, list)
            self.assertFalse(kwargs.get("shell", False))
            self.assertGreater(kwargs["timeout"], 0)
            if argv[0] == "adb":
                self.assertIn(argv[1:], (["devices"], ["forward", "--list"]))
            elif argv[0] == "nvidia-smi":
                self.assertEqual(argv, preflight.GPU_COMMAND)
            else:
                self.assertEqual(argv, [sys.executable, "-B", str(TOOL.with_name("gpu-link-standin-pc.py")), "--help"])
                self.assertEqual(kwargs["timeout"], 5)
            started.append(argv)
            return original(argv, **kwargs)

        with patch.object(preflight.subprocess, "run", side_effect=checked):
            self.run_checks(full=True)
        self.assertGreaterEqual(len(started), 6)
        self.assertEqual([call[1:] for call in self.calls() if call[0] == "adb"], [["devices"], ["forward", "--list"]])
        phone = [row for row in self.server.requests if row[0] == "GET"]
        self.assertEqual(len(phone), 1)
        self.assertEqual(phone[0][1], "/v1/hello")
        self.assertEqual(phone[0][2]["Host"], "localhost")
        self.assertEqual(phone[0][2]["Accept-Encoding"], "identity")
        self.assertEqual(phone[0][2]["Connection"], "close")
        self.assertFalse(any(key.lower().startswith("x-link-") for key in phone[0][2]))
        posts = [row for row in self.server.requests if row[0] == "POST"]
        self.assertEqual(
            [row[3]["messages"][0]["content"] for row in posts],
            [preflight.FIRST, preflight.CONTINUE, preflight.GPU_PROMPT],
        )
        self.assertEqual([row[3]["max_tokens"] for row in posts], [16, 24, 150])
        self.assertEqual(posts[1][3]["messages"][1], {"role": "assistant", "content": preflight.PREFIX})
        for row in posts:
            self.assertEqual(row[1], "/v1/chat/completions")
            self.assertTrue(row[3]["stream"])
            self.assertEqual(row[3]["stream_options"], {"include_usage": True})

    def test_custom_adb_path(self):
        self.run_checks("--adb", str(self.root / "adb"))
        self.status("adb device", "PASS")
        self.status("adb forward", "PASS")

    def test_no_redirect_or_proxy(self):
        self.server.scenario["chat_status"] = 302
        with patch.dict(os.environ, {"HTTP_PROXY": "http://example.invalid", "HTTPS_PROXY": "http://example.invalid"}):
            self.run_checks()
        self.status("chat server first answer", "FAIL", "HTTP 302")
        self.assertEqual(sum(row[0] == "POST" for row in self.server.requests), 2)

    def test_adb_timeout_still_runs_later_checks(self):
        original = preflight.command

        def timeout_adb(argv, timeout):
            if argv[0] == "adb":
                raise subprocess.TimeoutExpired(argv, timeout)
            return original(argv, timeout)

        with patch.object(preflight, "command", side_effect=timeout_adb):
            self.run_checks()
        self.status("adb device", "FAIL")
        self.status("adb forward", "FAIL")
        self.status("link hello", "PASS")
        self.status("stand-in tool", "PASS")

    def test_json_existing_aliases_refused(self):
        for kind in ("hardlink", "symlink", "regular"):
            with self.subTest(kind=kind):
                output = self.root / kind
                if kind == "hardlink":
                    os.link(self.key, output)
                elif kind == "symlink":
                    output.symlink_to(self.key)
                else:
                    output.write_bytes(b"keep me")
                original = output.read_bytes()
                child = self.child("--quick", "--json", str(output))
                self.assertEqual(child.returncode, 2, child.stdout + child.stderr)
                self.assertIn("path already exists", child.stderr)
                self.assertEqual(output.read_bytes(), original)
                self.assertEqual(self.key.read_bytes(), b"K" * 32)

    def test_first_answer_and_gpu_reject_unusable_or_truncated(self):
        for scenario, detail in (
            ({"text": " \t\n"}, "no content"),
            ({"text": "\0\x7f\ud800"}, "no content"),
            ({"truncated": True}, "stream ended early"),
            ({"done_only": True}, "no content"),
        ):
            with self.subTest(scenario=scenario):
                self.server.scenario = scenario
                self.run_checks(full=True)
                self.status("chat server first answer", "FAIL", detail)
                self.status("gpu use and speed", "FAIL", detail)
                self.status("stand-in tool", "PASS")

    def test_reasoning_alias_and_late_reasoning(self):
        self.server.scenario = {"reasoning": True, "reasoning_field": "reasoning"}
        self.run_checks()
        row = self.status("chat server first answer", "FAIL", "only reasoning")
        self.assertIn("--reasoning off", row["fix"])
        for text in (" ", "OK"):
            self.server.scenario = {"text": text, "late_reasoning": True}
            self.run_checks()
            row = self.status("chat server first answer", "FAIL" if text == " " else "WARN", "reasoning")
            self.assertIn("--reasoning off", row["fix"])

    def test_continuation_boundaries(self):
        for text, status in (
            ("5 reasons why cats sleep.", "WARN"),
            ("50, 51, 52", "WARN"),
            ("5, 6, 70", "WARN"),
            ("5, 6, 7 but actually 1", "WARN"),
            ("5, 6, 7, 6", "WARN"),
            ("5, 6, 7" + " " * 5000 + "but actually 1", "WARN"),
            (" \0\t", "FAIL"),
            (" \n1, 2, 3", "FAIL"),
            (" 5 , 6 , 7", "PASS"),
            ("5,\n6,\n7, 8, 9, 10, 11, 12.", "PASS"),
            ("5, 6, 7\nmore on another line", "WARN"),
            ("5, 6, 7\n1, 2, 3", "WARN"),
            ("5, 6, 7,\n6, 5, 4", "WARN"),
            ("5,\n6,\n7\nI cannot continue the answer.", "WARN"),
            ("5, 6, 7 I cannot continue the answer.", "WARN"),
            ("5, 6, 7, 6, 5", "WARN"),
            ("5, 6, 7,", "WARN"),
            ("5, 6, 7, 8, 9, 10, 11, 12, 13.", "PASS"),
        ):
            with self.subTest(text=text):
                self.server.scenario = {"continuation": text}
                self.run_checks()
                self.status("chat server continuation", status)
        self.server.scenario = {"continuation": "5\0 reasons why cats sleep." + "x" * 100}
        self.run_checks()
        row = self.status("chat server continuation", "WARN")
        self.assertEqual(json.loads(row["detail"].split(": ", 1)[1]), ("5 reasons why cats sleep." + "x" * 100)[:40])

    def test_continuation_cut_by_token_limit(self):
        # A server that stops at the token limit can end the list right after a comma or inside the next number (a
        # real NVIDIA Nemotron 3 Nano 4B run ended "5, 6, 7, 8, 9, 10, 11,"). Only a stop at the limit excuses that,
        # and only for an empty last item or the start of the number that comes next: a wrong number stays wrong.
        for text, finish, status in (
            ("5, 6, 7, 8, 9, 10, 11,", "length", "PASS"),
            (" 5, 6, 7, 8, 9, 10, 11, \n", "length", "PASS"),
            ("5, 6, 7, 8, 9, 1", "length", "PASS"),
            ("5, 6, 7, 8, 9, 10, 1", "length", "PASS"),
            ("5, 6, 7, 8, 9, 10", "length", "PASS"),
            ("5, 6, 7,", "length", "PASS"),
            ("5, 6, 7", "length", "PASS"),
            ("5, 6, 7, 99", "length", "WARN"),
            ("5, 6, 7, 6", "length", "WARN"),
            ("5, 6, 7., 99", "length", "WARN"),
            ("5, 6, 7., ", "length", "WARN"),
            ("5, 6, 7, 8, 9, 2", "length", "WARN"),
            ("5, 6, 7, 8, 9, 0", "length", "WARN"),
            ("5, 6, 7, 8, 9, 10, 2", "length", "WARN"),
            ("5, 6, 7, 9", "length", "WARN"),
            ("5, 6, 7, 8, 9, 11", "length", "WARN"),
            ("5, 6, 7, 08", "length", "WARN"),
            ("5, 6, 7, 08", "stop", "WARN"),
            ("5, 6, 7, 8, 09,", "length", "WARN"),
            ("5, 6, 7, 8, 9, 010", "stop", "WARN"),
            ("5, 6, 7, " + "9" * 4087, "length", "WARN"),
            ("5, 6, 7," + " " * 5000, "length", "WARN"),
            ("5, 6,", "length", "WARN"),
            ("5, 6, 7, 9,", "length", "WARN"),
            ("5, 6, 7, 8 and then 9", "length", "WARN"),
            ("5, 6, 7, and more,", "length", "WARN"),
            ("5, 6, 7, 8,, 9", "length", "WARN"),
            ("1, 2, 3,", "length", "FAIL"),
            ("5, 6, 7, 8, 9, 10, 11,", "stop", "WARN"),
            ("5, 6, 7, 8, 9, 1", "stop", "WARN"),
            ("5, 6, 7, 99", "stop", "WARN"),
            ("5, 6, 7, 8, 9, 10, 11,", "content_filter", "WARN"),
        ):
            with self.subTest(text=text[:60], finish=finish):
                self.server.scenario = {"continuation": text, "finish": finish}
                self.run_checks()
                row = self.status("chat server continuation", status)
                if status == "PASS":
                    self.assertIn("continues", row["detail"])

    @staticmethod
    def chunk(text="", finish=None, index=0):
        """One streamed event in the llama.cpp shape: content or an empty delta, and a null or final finish reason."""
        choice = {"index": index, "delta": {"content": text} if text else {}, "finish_reason": finish}
        return {"choices": [choice]}

    def test_continuation_long_lists(self):
        # Numbers of any length count. The probe's requested stop at 12 is not enforced: the check asks whether the
        # reply goes on counting from the stored partial.
        for top, finish, tail, status in (
            (100, "stop", "", "PASS"),
            (13, "stop", ".", "PASS"),
            (99, "length", ",1", "PASS"),
            (99, "length", ",10", "PASS"),
            (99, "length", ",100", "PASS"),
            (99, "length", ",101", "WARN"),
            (99, "length", ",2", "WARN"),
        ):
            text = ",".join(str(n) for n in range(5, top + 1)) + tail
            with self.subTest(text=text[-12:], finish=finish):
                self.server.scenario = {"continuation": text, "finish": finish}
                self.run_checks()
                self.status("chat server continuation", status)

    def test_continuation_finish_reason_in_a_last_chunk(self):
        # llama.cpp sends the finish reason in a last chunk with an empty delta; earlier chunks carry null.
        text = [self.chunk("5, 6,"), self.chunk(" 7, 8, 9,"), self.chunk(" 10, 11,")]
        usage = {"choices": [], "usage": {"completion_tokens": 9}}
        for label, tail, status in (
            ("last chunk says length", [self.chunk(finish="length")], "PASS"),
            ("a later null does not erase it", [self.chunk(finish="length"), self.chunk()], "PASS"),
            ("a usage chunk after it", [self.chunk(finish="length"), usage], "PASS"),
            ("last chunk says stop", [self.chunk(finish="stop")], "WARN"),
            ("the last reason wins", [self.chunk(finish="length"), self.chunk(finish="stop")], "WARN"),
            ("no finish reason at all", [], "WARN"),
        ):
            with self.subTest(label):
                self.server.scenario = {"continuation_events": text + tail}
                self.run_checks()
                self.status("chat server continuation", status)
        self.server.scenario = {
            "continuation_events": [{"choices": [{"delta": {"content": "5, 6, 7,"}, "finish_reason": "length"}]}]
        }
        self.run_checks()
        self.status("chat server continuation", "PASS")

    def test_continuation_takes_everything_from_one_choice(self):
        # The probe asks for one completion. A second choice must not supply the finish reason that excuses the text
        # of the first, and text must not be assembled from two choices. A usage event with no choices follows, as
        # llama.cpp and OpenAI-style servers send it, and must not clear the warning.
        usage = {"choices": [], "usage": {"completion_tokens": 9}}
        content = {"index": 0, "delta": {"content": "5, 6, 7,"}, "finish_reason": "stop"}
        other = {"index": 1, "delta": {}, "finish_reason": "length"}
        whole = {"index": 0, "delta": {"content": "5, 6, 7, 8"}, "finish_reason": "stop"}
        for label, events in (
            ("two choices in one event", [{"choices": [content, other]}, usage]),
            ("two index 0 choices in one event", [{"choices": [content, dict(other, index=0)]}, usage]),
            (
                "a whole list and a second choice, both stopping",
                [{"choices": [whole, dict(other, finish_reason="stop")]}, usage],
            ),
            (
                "another choice in a later event",
                [{"choices": [dict(content, finish_reason=None)]}, {"choices": [other]}, usage],
            ),
            (
                "text assembled across choices",
                [
                    {"choices": [{"index": 0, "delta": {"content": "5, 6,"}, "finish_reason": None}]},
                    {"choices": [{"index": 1, "delta": {"content": " 7,"}, "finish_reason": "length"}]},
                    usage,
                ],
            ),
        ):
            with self.subTest(label):
                self.server.scenario = {"continuation_events": events}
                self.run_checks()
                self.status("chat server continuation", "WARN", "extra choice")

    def test_continuation_choice_index_forms(self):
        # An absent or null index and index 0 are one choice. Anything else is not, whatever its type.
        for index, status in (
            ("absent", "PASS"),
            (None, "PASS"),
            (0, "PASS"),
            (1, "WARN"),
            ("0", "WARN"),
            (True, "WARN"),
            ([], "WARN"),
            ({}, "WARN"),
            ("", "WARN"),
        ):
            choice = {"delta": {"content": "5, 6, 7,"}, "finish_reason": "length"}
            if index != "absent":
                choice["index"] = index
            with self.subTest(index=index):
                self.server.scenario = {"continuation_events": [{"choices": [choice]}]}
                self.run_checks()
                self.status("chat server continuation", status, "" if status == "PASS" else "extra choice")

    def test_extra_choice_warns_in_the_first_answer_and_the_gpu_check(self):
        two = {
            "choices": [
                {"index": 0, "delta": {"content": "OK."}, "finish_reason": "stop"},
                {"index": 1, "delta": {}, "finish_reason": None},
            ]
        }
        self.server.scenario = {"first_events": [two]}
        self.run_checks()
        self.status("chat server first answer", "WARN", "extra choice")
        self.server.scenario = {"gpu_delay": 1.1, "gpu_events": [two]}
        self.run_checks("--engine-log", str(self.engine_log), full=True)
        self.status("gpu use and speed", "WARN", "extra choice")

    def test_continuation_finish_reason_is_recorded(self):
        for finish in ("length", "stop"):
            with self.subTest(finish=finish):
                self.server.scenario = {"finish": finish}
                args = preflight.arguments(self.options("--no-gpu-probe"))
                result = preflight.stream(args, preflight.CONTINUE, 24, partial=preflight.PREFIX)
                self.assertEqual(result["finish"], finish)

    def test_hello_strict_replies(self):
        valid = dict(proto=1, label="Test phone", epoch="a" * 32)
        bodies = [b'{"proto":2,"proto":1,"label":"Test phone","epoch":"' + b"a" * 32 + b'"}']
        for update in (
            {"model_ready": True},
            {"label": "bad_label"},
            {"label": 1},
            {"proto": True},
            {"epoch": "A" * 32},
            {"epoch": 1},
            {"proto": 1.0},
        ):
            bodies.append(json.dumps(dict(valid, **update)).encode())
        bodies += [
            b'{"proto":1,"label":"Test phone","epoch":"' + b"a" * 32 + b'","x":NaN}',
            b'{"x":' + b"[" * 1000 + b"0" + b"]" * 1000 + b"}",
        ]
        for body in bodies:
            with self.subTest(body=body[:80]):
                self.server.scenario = {"hello_raw": body}
                self.run_checks()
                self.status("link hello", "FAIL", "phone reply refused by the stand-in's rules")

    def test_hello_request_matches_standin_bytes(self):
        from tests.test_gpu_link_standin_pc import standin

        captured = []
        # Capture http.client's serialized request, before any socket operation.
        with (
            patch.object(preflight.http.client.HTTPConnection, "send", lambda conn, data: captured.append(data)),
            patch.object(preflight.http.client.HTTPConnection, "getresponse", side_effect=OSError),
        ):
            args = preflight.arguments(self.options())
            preflight.Preflight(args).hello()
            expected_start = len(captured)
            with self.assertRaises(OSError):
                standin.PC(self.phone, b"K" * 32).send("GET", "/v1/hello", signed=False)
        self.assertEqual(b"".join(captured[:expected_start]), b"".join(captured[expected_start:]))
        self.assertIn(b"Content-Length: 0\r\n", b"".join(captured))

    def test_adb_attention_and_invalid_serial(self):
        for state in ("no permissions (user in plugdev group)", "unauthorized", "offline", "recovery"):
            with self.subTest(state=state):
                self.configure(devices=f"denied {state}\nfixture-phone device\n")
                self.run_checks()
                self.status("adb device", "FAIL", "a device needs attention:")
                self.assertIn("select the right one with --serial", self.by_name["adb device"]["detail"])
                self.run_checks("--serial", "fixture-phone")
                self.status("adb device", "PASS")
        self.configure(devices="bad!serial device\n")
        self.run_checks()
        self.status("adb device", "WARN", "ignored malformed adb line")
        child = self.child("--serial", "bad ; touch MARKER")
        self.assertEqual(child.returncode, 2)
        self.assertIn("invalid serial", child.stderr)
        self.assertNotIn("touch MARKER", child.stderr)

    def test_adb_trusted_path_and_command_rejection(self):
        child = self.child("--adb", "adb; touch MARKER")
        self.assertEqual(child.returncode, 2)
        self.assertIn("single path or command name", child.stderr)
        odd = self.root / "adb ; ' odd"
        odd.write_bytes((self.root / "adb").read_bytes())
        odd.chmod(0o700)
        self.run_checks("--adb", str(odd))
        self.status("adb device", "PASS")
        self.status("adb forward", "PASS")
        help_result = subprocess.run(
            [sys.executable, "-B", str(TOOL), "--help"], capture_output=True, text=True, check=False
        )
        self.assertIn("trusted executable code", help_result.stdout)

    def test_engine_log_forms_and_read_bound(self):
        for prefix in ("llm_load_tensors", "load_tensors"):
            for cuda in (
                "ggml_cuda_init: found 1 CUDA devices:",
                "load_tensors:        CUDA0 model buffer size =  2706.50 MiB",
            ):
                with self.subTest(prefix=prefix, cuda=cuda):
                    self.engine_log.write_text(
                        f"[2026-11-22 10:10:10]   {cuda}\n  0.12 {prefix}: offloaded 33/33 layers to GPU\n"
                        f"listening on http://127.0.0.1:{self.port}\n"
                    )
                    self.assertEqual(preflight.engine_offload(self.engine_log, self.port)[0], "PASS")
        for text in (
            "offloaded 33/33 layers to GPU",
            "ggml_cuda_init: found 0 CUDA devices:\noffloaded 33/33 layers to GPU",
            "ggml_cuda_init: found 1 CUDA devices:\noffloaded 0/33 layers to GPU",
            "CUDA0 model buffer size = 1 MiB\noffloaded 33/33 layers to GPU\noffloaded 0 layers to GPU",
        ):
            with self.subTest(text=text):
                self.engine_log.write_text(text)
                self.assertNotEqual(preflight.engine_offload(self.engine_log, self.port)[0], "PASS")
        self.engine_log.write_bytes(
            b" " * (2 * 1024 * 1024) + b"CUDA0 model buffer size = 1 MiB\noffloaded 33/33 layers to GPU"
        )
        self.assertNotEqual(preflight.engine_offload(self.engine_log, self.port)[0], "PASS")

    def gpu_fixture(self, *, delay=1.05, utilization=30, engine=True, final=90):
        args = preflight.arguments(self.options(*(["--engine-log", str(self.engine_log)] if engine else [])))
        ended, count = False, 0

        def sample(timeout):
            nonlocal count
            count += 1
            return [("fixture", 8192, final if ended else (5 if count == 1 else utilization), 100)]

        def generate(*args, **kwargs):
            nonlocal ended
            time.sleep(delay)
            ended = True
            return dict(first=0.001, elapsed=max(delay, 0.001), tokens=150, chunks=1, reasoning=False)

        with (
            patch.object(preflight, "gpu_sample", side_effect=sample),
            patch.object(preflight, "stream", side_effect=generate),
        ):
            return preflight.Preflight(args).gpu_probe()

    def test_gpu_evidence_and_generation_window(self):
        status, detail, _ = self.gpu_fixture()
        self.assertEqual(status, "PASS", detail)
        self.assertIn("4 samples inside generation window; baseline 5%; peak 30%", detail)
        for options, reason in (
            ({"engine": False}, "--engine-log not supplied"),
            ({"delay": 0}, "0 samples inside generation window"),
            ({"utilization": 19}, "did not rise"),
            ({"utilization": 5}, "did not rise"),
        ):
            with self.subTest(options=options):
                status, detail, _ = self.gpu_fixture(**options)
                self.assertEqual(status, "WARN", detail)
                self.assertIn(reason, detail)
                self.assertNotIn("peak 90%", detail)  # The after-generation spike is excluded.
        status, detail, _ = self.gpu_fixture(utilization=20)
        self.assertEqual(status, "PASS", detail)  # Exactly 15 points is sufficient.

    def test_gpu_missing_or_negative_engine_evidence(self):
        for text in (
            "CUDA0 model buffer size = 1 MiB\noffloaded 0/33 layers to GPU",
            "offloaded 33/33 layers to GPU",
            "ggml_cuda_init: found 1 CUDA devices:",
        ):
            self.engine_log.write_text(text)
            status, detail, _ = self.gpu_fixture(delay=0)
            self.assertEqual(status, "FAIL" if "offloaded 0/" in text else "WARN", detail)
            self.assertIn("engine log", detail)
        self.engine_log.unlink()
        status, detail, _ = self.gpu_fixture(delay=0)
        self.assertEqual(status, "WARN", detail)
        self.assertIn("engine log unavailable", detail)

    def test_final_sample_exception_always_joins(self):
        for exception in (KeyboardInterrupt, RuntimeError):
            args = preflight.arguments(self.options())
            result = dict(first=0.001, elapsed=0.001, tokens=1, chunks=1, reasoning=False)
            workers = []
            original = threading.Thread

            def thread(*args, _original=original, _workers=workers, **kwargs):
                worker = _original(*args, **kwargs)
                _workers.append(worker)
                return worker

            try:
                with (
                    patch.object(preflight, "gpu_sample", side_effect=[[("fixture", 8192, 5, 100)], exception]),
                    patch.object(preflight, "stream", return_value=result),
                    patch.object(preflight.threading, "Thread", side_effect=thread),
                ):
                    with self.assertRaises(exception):
                        preflight.Preflight(args).gpu_probe()
                    self.assertFalse(any(worker.is_alive() for worker in workers))
            finally:
                for worker in workers:
                    worker.join(3)  # Also clean up a deliberately reverted implementation.

    def test_keyboard_interrupt_exit_130(self):
        with (
            patch.object(preflight.Preflight, "run", side_effect=KeyboardInterrupt),
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            try:
                status = preflight.main(self.options())
            except KeyboardInterrupt:
                self.fail("KeyboardInterrupt escaped the top-level handler")
            self.assertEqual(status, 130)
        self.assertIn("Preflight interrupted", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_enormous_usage_keeps_summary(self):
        self.server.scenario = {"tokens": 10**400}
        child = self.child()
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn("content chunks/s (token count unavailable)", child.stdout)
        self.assertIn("PASS stand-in tool", child.stdout)
        self.assertIn("Summary:", child.stdout)
        self.assertNotIn("Traceback", child.stderr)


class ArgumentTests(unittest.TestCase):
    def test_loopback_only_url_enforcement(self):
        urls = [
            "http://example.com/v1/chat/completions",
            "http://192.0.2.1/v1/chat/completions",
            "http://127.0.0.1.example.com/x",
            "http://2130706433/x",
            "file:///tmp/x",
            "ftp://localhost/x",
            "http://user:password@localhost/x",
            "http://localhost/x#fragment",
            "http://localhost:0/x",
            "http://localhost:65536/x",
            "http://localhost",
            "http://[::1%lo0]/x",
        ]
        for url in urls:
            with self.subTest(url=url):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    preflight.arguments(["--server", url, "--model", "fixture"])
                self.assertEqual(error.exception.code, 2)
        for host in ("localhost", "127.0.0.1", "[::1]"):
            for scheme in ("http", "https"):
                preflight.arguments(["--server", f"{scheme}://{host}/v1/chat/completions", "--model", "fixture"])

    def test_phone_loopback_only(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            preflight.arguments(["--phone", "example.com:8767"])
        self.assertEqual(error.exception.code, 2)

    def test_server_model_pair(self):
        for argv in (["--server", "http://localhost/x"], ["--model", "fixture"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                preflight.arguments(argv)
            self.assertEqual(error.exception.code, 2)

    def test_json_cannot_overwrite_key(self):
        with TemporaryDirectory(prefix="gpu-preflight-args-") as root:
            path = Path(root) / "key"
            path.write_bytes(b"K" * 32)
            with contextlib.redirect_stderr(io.StringIO()), patch.object(preflight.Preflight, "run", return_value=[]):
                self.assertEqual(preflight.main(["--key-file", str(path), "--json", str(path)]), 2)
            self.assertEqual(path.read_bytes(), b"K" * 32)

    def test_first_usable_content_clock(self):
        events = [
            {"choices": [{"delta": {"content": "\0\t \n\ud800"}}]},
            {"choices": [{"delta": {"content": "OK\0"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        lines = iter([b"data: " + json.dumps(event).encode() + b"\n" for event in events] + [b""])
        ticks = iter([2.0, 4.0, 5.0, 6.0])
        now = [1.0]

        class Reader:
            def readline(self, limit):
                now[0] = next(ticks)
                return next(lines)

        with patch.object(preflight.time, "monotonic", side_effect=lambda: now[0]):
            result = preflight.read_stream(Reader(), 1.0, True)
        self.assertEqual(result["first"], 3.0)
        self.assertEqual(result["elapsed"], 5.0)
        self.assertEqual(result["text"], "\t \nOK")

    def test_stream_completion_markers(self):
        for finish in ("stop", "length", None):
            for done in (False, True):
                with self.subTest(finish=finish, done=done):
                    event = {"choices": [{"delta": {"content": "OK"}, "finish_reason": finish}]}
                    data = b"data: " + json.dumps(event).encode() + b"\n\n"
                    if done:
                        data += b"data: [DONE]\n\n"
                    if finish is None and not done:
                        with self.assertRaisesRegex(preflight.ProbeError, "stream ended early"):
                            preflight.read_stream(io.BytesIO(data), time.monotonic(), True)
                    else:
                        result = preflight.read_stream(io.BytesIO(data), time.monotonic(), True)
                        self.assertFalse(result["unexpected"])
                        self.assertIsNotNone(result["first"])

    def test_usage_integer_bound(self):
        for tokens in (0, 1_000_000, -1, 1_000_001, 10**400, True, 1.5, "150"):
            with self.subTest(tokens=tokens):
                data = (
                    b"data: " + json.dumps({"usage": {"completion_tokens": tokens}}).encode() + b"\n\ndata: [DONE]\n\n"
                )
                result = preflight.read_stream(io.BytesIO(data), time.monotonic(), False)
                self.assertEqual(result["tokens"], tokens if type(tokens) is int and 0 <= tokens <= 1_000_000 else None)

    def test_single_check_contains_arithmetic_and_parse_errors(self):
        checks = preflight.Preflight(preflight.arguments([]))
        for exception in (OverflowError, RecursionError, IndexError):
            with self.subTest(exception=exception):
                try:
                    checks.add("bad fixture", unittest.mock.Mock(side_effect=exception))
                except exception:
                    self.fail(f"{exception.__name__} escaped a single check")
                self.assertEqual(checks.results[-1]["status"], "FAIL")
        checks.add("next fixture", lambda: ("PASS", "still running", ""))
        self.assertEqual(checks.results[-1]["status"], "PASS")

    def test_default_timeout_and_phone(self):
        args = preflight.arguments([])
        self.assertEqual(args.timeout, 30)
        self.assertEqual(args.phone, "127.0.0.1:8767")

    def test_deadline_reader_does_not_accept_trickle_forever(self):
        class FakeSocket:
            def makefile(self, *args, **kwargs):
                return io.BytesIO(b"x")

            def settimeout(self, seconds):
                pass

        with preflight.DeadlineReader(FakeSocket(), 5) as reader:
            with patch.object(preflight.time, "monotonic", side_effect=[4, 6]), self.assertRaises(TimeoutError):
                reader.readinto(bytearray(1))


if __name__ == "__main__":
    unittest.main()
