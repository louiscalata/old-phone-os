# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise GPU Link child processes with loopback TCP faults and phone crashes.

Example: python3 -B tools/gpu-link-soak.py --duration 300 --seed 743 --out ./soak-report
No browser, model engine, device, package installation or external service is used.
The model and phone battery are simulated. Physical phone checks remain NOT_RUN.
The optional process self-test runs with OPOS_SOAK=1; it is not a CI requirement.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import math
import os
import platform
import random
import re
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORDS = "river stone café tree light mañana bridge 雪 garden window ember quiet λ cloud shore étoile".split()
LAUNCHER = """# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import os
from pathlib import Path
from unittest import mock
from opos import assist, firstrun, gpu_link, gpu_link_server, web
make_server = web.make_server
link_start = gpu_link_server.start
link_tick = gpu_link.LinkService.tick

def tick(self):
    if not Path(os.environ["OPOS_STATE_DIR"], "restart.barrier").exists():
        link_tick(self)

def start(*args, **kwargs):
    try:
        result = link_start(*args, **kwargs)
        root = Path(os.environ["OPOS_STATE_DIR"])
        if (root / "restart.barrier").exists():
            import json
            (root / "restart.snapshot").write_text(json.dumps(args[0].snapshot()))
        return result
    except OSError as err:
        print(f"GPU Link listener startup: {err}", file=sys.stderr, flush=True)
        raise

def make(*args, **kwargs):
    server = make_server(*args, **kwargs)
    server.RequestHandlerClass.updater.start_auto_check = mock.Mock()
    return server

# Stubs that the page calls on every request are plain functions: a Mock records every call, which grows the
# phone process by about one kilobyte per request and would show up as a false memory leak in a long run.
with (mock.patch.object(assist, "local_route", new=lambda *args, **kwargs: None),
      mock.patch.object(firstrun, "install"),
      mock.patch.object(web, "_route_with_glimmer", new=lambda *args, **kwargs: None),
      mock.patch.object(web, "_install_helper_visible", new=lambda *args, **kwargs: False),
      mock.patch.object(web, "make_server", side_effect=make),
      mock.patch.object(gpu_link_server, "start", side_effect=start),
      mock.patch.object(gpu_link.LinkService, "tick", tick)):
    web.main(sys.argv[1:])
"""


def expected_text(question, seed, short=False):
    """Return a question-specific fixture of 60 to 400 words, including Unicode."""
    digest = hashlib.sha256(f"{seed}:{question}".encode()).digest()
    rng = random.Random(int.from_bytes(digest, "big"))
    words = [rng.choice(WORDS) for _ in range(60 if short else rng.randint(60, 400))]
    words[0] = digest.hex()[:16]
    text = " ".join(words)
    # Put whole multi-byte characters beside the stand-in's 256-byte post boundaries.
    for offset, character in ((254, "🪢"), (510, "界")):
        raw = text.encode()
        if len(raw) > offset:
            prefix = raw[:offset].decode("utf-8", "ignore")
            text = prefix + character + text[len(prefix) :]
    return text


def excerpt(value, limit=160):
    """Trim fixture excerpts, redact long hex and suppress text-display diagnostics."""
    value = re.split(r"(?i:--show[-_]text)|Question:|Text:", str(value), maxsplit=1)[0]
    value = re.sub(r"[0-9a-fA-F]{16,}", "[hex withheld]", value)
    return value.replace("\n", " ").replace("\r", " ")[:limit]


def stop_process(process, timeout=1):
    """Escalate every owned process, including a stopped or resistant child, then reap."""
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        if process.poll() is not None:
            break
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signum)
            if signum != signal.SIGKILL:
                process.send_signal(signal.SIGCONT)
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            continue
    if process.poll() is None:
        process.kill()
    process.wait()


def stop_server(server):
    """Continue closing owned resources even if a server shutdown step raises."""
    try:
        server.stop()
        return []
    except Exception as err:
        errors = ["server cleanup: " + type(err).__name__]
    server.halt.set()
    with server.lock:
        sockets = list(server.sockets)
    for sock in sockets:
        try:
            close_socket(sock)
        except Exception as err:
            errors.append("server socket cleanup: " + type(err).__name__)
    for action in (server.shutdown, server.server_close):
        try:
            action()
        except Exception as err:
            errors.append("server fallback: " + type(err).__name__)
    return errors


def close_socket(sock):
    """Wake socket readers and release an owned descriptor."""
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    sock.close()


def free_port():
    """Ask the kernel for a loopback port; startup checks report any bind race."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wire_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class HarnessError(RuntimeError):
    """A setup, measurement or harness operation could not complete."""


class InvariantError(RuntimeError):
    """An observed process or answer broke an invariant."""


class ModelHandler(BaseHTTPRequestHandler):
    """Stream deterministic SSE deltas, continuing an exact assistant prefix."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        server = self.server
        with server.lock:
            server.counts["requests"] += 1
        try:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = payload["messages"]
            question = messages[0]["content"]
            text = expected_text(question, server.seed, server.short)
            partial = messages[1]["content"] if len(messages) == 2 else ""
            stored = [json.loads(path.read_text())["answer"] for path in server.store_root.glob("ai-answer-*.json")]
            matches = [row for row in stored if row["question"] == question]
            durable = matches[0]["text"] if len(matches) == 1 else None
            if partial != durable:
                with server.lock:
                    server.errors.append(
                        "resume partial differs from durable text: supplied="
                        + excerpt(partial)
                        + " durable="
                        + excerpt(durable)
                    )
                self.send_error(400)
                return
            if not text.startswith(partial):
                with server.lock:
                    server.counts["invalid_prefixes"] += 1
                self.send_error(400)
                return
            if partial:
                with server.lock:
                    server.counts["resumes"] += 1
            rest = text[len(partial) :]
            pieces = rest.split(" ") if rest else []
            pieces = [piece + (" " if i < len(pieces) - 1 else "") for i, piece in enumerate(pieces)]
            if server.drop_token and not partial and len(pieces) > 3:
                del pieces[2]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in pieces:
                if server.halt.wait(1 / server.rate):
                    return
                self.wfile.write(b"data: " + wire_json({"choices": [{"delta": {"content": piece}}]}) + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            with server.lock:
                server.counts["disconnects"] += 1
        except (ValueError, KeyError, TypeError, OSError) as err:
            with server.lock:
                server.errors.append(type(err).__name__)


class ModelServer(ThreadingHTTPServer):
    """Own the simulated model's sockets and request threads."""

    daemon_threads = False
    block_on_close = True

    def __init__(self, args):
        super().__init__(("127.0.0.1", 0), ModelHandler)
        self.seed, self.short, self.rate = args.seed, args.self_test, args.token_rate
        self.drop_token = args.plant_drop_token
        self.counts = Counter(requests=0, resumes=0, disconnects=0, invalid_prefixes=0)
        self.errors = deque(maxlen=20)
        self.lock, self.halt = threading.Lock(), threading.Event()
        self.sockets = set()

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(3)
        with self.lock:
            self.sockets.add(sock)
        return sock, address

    def close_request(self, request):
        with self.lock:
            self.sockets.discard(request)
        super().close_request(request)

    def stop(self):
        self.halt.set()
        self.shutdown()
        with self.lock:
            for sock in list(self.sockets):
                close_socket(sock)
        self.server_close()


def read_message(sock):
    """Read one bounded Content-Length HTTP message without interpreting secrets."""
    data = bytearray()
    end, size = -1, None
    while size is None or len(data) < size:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("incomplete proxy message")
        data.extend(chunk)
        if len(data) > 131072:
            raise HarnessError("proxy message exceeded its bound")
        if end < 0:
            end = data.find(b"\r\n\r\n")
            if end >= 0:
                headers = data[:end].split(b"\r\n")
                lengths = [
                    int(line.split(b":", 1)[1]) for line in headers[1:] if line.lower().startswith(b"content-length:")
                ]
                size = end + 4 + (lengths[0] if lengths else 0)
    return bytes(data)


class ProxyHandler(socketserver.BaseRequestHandler):
    """Forward a single HTTP exchange, cutting at a selected byte when armed."""

    def handle(self):
        proxy = self.server
        client, upstream = self.request, None
        tracked, final = False, False
        with proxy.lock:
            if time.monotonic() < proxy.refuse_until or proxy.halt.is_set():
                proxy.applied_pull()
                return
            proxy.sockets.add(client)
        try:
            upstream = socket.create_connection(("127.0.0.1", proxy.phone_port), timeout=2)
            upstream.settimeout(12)
            client.settimeout(12)
            with proxy.lock:
                proxy.sockets.add(upstream)
                # A pull between accept and connect must also close this new upstream.
                if time.monotonic() < proxy.refuse_until:
                    proxy.applied_pull()
                    return
            request = read_message(client)
            final = False
            if b"/text HTTP/" in request.split(b"\r\n", 1)[0]:
                final = bool(json.loads(request.split(b"\r\n\r\n", 1)[1]).get("done"))
            with proxy.lock:
                proxy.inflight += 1
                proxy.final_inflight += int(final)
            tracked = True
            text_post = b"/text HTTP/" in request.split(b"\r\n", 1)[0]
            if not proxy.forward(upstream, request, "pc_to_phone", text_post):
                return
            reply = read_message(upstream)
            proxy.observe(request, reply)
            proxy.forward(client, reply, "phone_to_pc", text_post and reply.startswith(b"HTTP/1.1 200 "))
        except (OSError, ValueError):
            pass
        except Exception as err:
            with proxy.lock:
                proxy.errors.append("proxy exchange failed: " + type(err).__name__)
        finally:
            with proxy.lock:
                if tracked:
                    proxy.inflight -= 1
                    proxy.final_inflight -= int(final)
                proxy.sockets.discard(client)
                proxy.sockets.discard(upstream)
            if upstream is not None:
                close_socket(upstream)
            close_socket(client)


class FaultProxy(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Own fault plans and loopback connections; report actual injections only."""

    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(self, phone_port, emit):
        super().__init__(("127.0.0.1", 0), ProxyHandler)
        self.phone_port, self.emit = phone_port, emit
        self.lock, self.halt = threading.RLock(), threading.Event()
        self.sockets, self.errors, self.observations = set(), deque(maxlen=20), deque(maxlen=100)
        self.refuse_until, self.armed, self.question = 0, None, 0
        self.pending_pull = None
        self.final_posts = {}
        self.generation = 1
        self.acked = 0
        self.leases = deque(maxlen=32)
        self.lease_questions = {}
        self.inflight = 0
        self.final_inflight = 0

    def clear_arm(self):
        with self.lock:
            if self.armed is not None:
                self.emit("not_applied", question=self.armed[4], kind=self.armed[0])
                self.armed = None

    def applied_pull(self, closed=0):
        with self.lock:
            if self.pending_pull is not None:
                duration, question = self.pending_pull
                self.pending_pull = None
                # Time the outage from the actual closed/refused connection boundary.
                self.refuse_until = time.monotonic() + duration
                self.emit("pull", question=question, seconds=round(duration, 4), closed=closed)

    def pull(self, duration):
        with self.lock:
            self.clear_arm()
            if self.pending_pull is not None:
                self.emit("not_applied", question=self.pending_pull[1], kind="pull")
            self.pending_pull = duration, self.question
            self.refuse_until = time.monotonic() + duration
            closed = 0
            for sock in list(self.sockets):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                    closed += 1
                except OSError:
                    pass
            if closed:
                self.applied_pull(closed)
            return dict(seconds=duration)

    def arm(self, kind, direction, fraction, seconds):
        with self.lock:
            self.clear_arm()
            self.armed = (kind, direction, fraction, seconds, self.question)

    def forward(self, destination, data, direction, eligible=True):
        with self.lock:
            plan = self.armed
            if eligible and plan is not None and plan[1] == direction:
                self.armed = None
            else:
                plan = None
        if plan is not None:
            kind, _, fraction, seconds, question = plan
            if kind == "cut":
                # Keep at least one byte unsent, including for the shortest hello request.
                count = 1 + int(fraction * (len(data) - 1))
                try:
                    destination.sendall(data[:count])
                except OSError:
                    self.emit("not_applied", question=question, kind=kind, reason="prefix send failed")
                    raise
                self.emit(kind, question=question, direction=direction, after_bytes=count, message_bytes=len(data))
                return False
            started = time.monotonic()
            interrupted = self.halt.wait(seconds)
            held = time.monotonic() - started
            if held > 0:
                self.emit(kind, question=question, direction=direction, seconds=round(held, 4))
            if interrupted:
                return False
        destination.sendall(data)
        return True

    def observe(self, request, reply):
        """Record only recognizable limit replies; never report wire bytes."""
        status = int(reply.split(b" ", 2)[1])
        value = json.loads(reply.split(b"\r\n\r\n", 1)[1]) if status != 204 else {}
        with self.lock:
            if status == 200 and "acked" in value:
                lease = request.split(b" ", 2)[1].decode().split("/")[-2]
                if self.lease_questions.get(lease) == self.question:
                    self.acked = max(self.acked, value["acked"])
            if status == 200 and "lease" in value:
                self.leases.append((self.question, value["lease"], value["partial"]))
                self.lease_questions[value["lease"]] = self.question
                while len(self.lease_questions) > 100:
                    self.lease_questions.pop(next(iter(self.lease_questions)))
        if status not in (404, 429):
            # Track final post fingerprints so a repeated final after restart is identifiable.
            if b"/text HTTP/" in request.split(b"\r\n", 1)[0]:
                body = json.loads(request.split(b"\r\n\r\n", 1)[1])
                if body.get("done"):
                    with self.lock:
                        self.final_posts[
                            hashlib.sha256(
                                request.split(b"\r\n\r\n", 1)[0].split(b"\r\n", 1)[0] + request.split(b"\r\n\r\n", 1)[1]
                            ).hexdigest()
                        ] = self.generation
            with self.lock:
                while len(self.final_posts) > 100:
                    self.final_posts.pop(next(iter(self.final_posts)))
            return
        value = json.loads(reply.split(b"\r\n\r\n", 1)[1])
        kind = "rate_429" if status == 429 and value.get("error") == "rate" else None
        if status == 404 and value.get("error") == "lease" and b"/text HTTP/" in request.split(b"\r\n", 1)[0]:
            body = request.split(b"\r\n\r\n", 1)[1]
            if (
                json.loads(body).get("done")
                and self.final_posts.get(
                    hashlib.sha256(request.split(b"\r\n", 1)[0] + body).hexdigest(), self.generation
                )
                < self.generation
            ):
                kind = "final_repeat_404_lease"
        if kind:
            with self.lock:
                self.observations.append((self.question, kind))

    def stop(self):
        self.clear_arm()
        with self.lock:
            if self.pending_pull is not None:
                self.emit("not_applied", question=self.pending_pull[1], kind="pull")
                self.pending_pull = None
        self.halt.set()
        self.shutdown()
        with self.lock:
            for sock in list(self.sockets):
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
        self.server_close()


class Child:
    """Own one subprocess and bounded diagnostic lines without retaining wire secrets."""

    def __init__(self, role, command, env, folder):
        self.role, self.lines = role, deque(maxlen=30)
        self.process = subprocess.Popen(
            command, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        self.reader = threading.Thread(target=self._read, name=f"soak-{role}-output")
        self.reader.start()

    def _read(self):
        with self.process.stdout as stream:
            for line in iter(lambda: stream.readline(2000), b""):
                # Arbitrary child diagnostics never enter reports, even after redaction.
                self.lines.append(f"diagnostic chunk: {len(line)} bytes withheld")

    def stop(self):
        try:
            stop_process(self.process)
        finally:
            self.reader.join(2)
        if self.reader.is_alive():
            raise HarnessError("child output reader did not stop")

    def tail(self):
        return [f"{self.role}: {len(self.lines)} diagnostic chunks withheld"]


def command_output(command):
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=3)
    if result.returncode:
        raise HarnessError("resource command failed: " + command[0])
    return result.stdout


def process_sample(pid):
    """Measure only the given phone PID, with no shell or process search."""
    if sys.platform.startswith("linux"):
        status = Path(f"/proc/{pid}/status").read_text()
        fields = dict(line.split(":", 1) for line in status.splitlines())
        return {
            "rss_kib": int(fields["VmRSS"].split()[0]),
            "threads": int(fields["Threads"]),
            "descriptors": len(list(Path(f"/proc/{pid}/fd").iterdir())),
        }
    if sys.platform == "darwin":
        commands = {
            "rss_kib": ["ps", "-o", "rss=", "-p", str(pid)],
            "threads": ["ps", "-M", "-p", str(pid)],
            "descriptors": ["lsof", "-p", str(pid), "-F", "f"],
        }
        result, errors = {}, []
        for field, command in commands.items():
            try:
                output = command_output(command)
                if field == "rss_kib":
                    result[field] = int(output.strip())
                elif field == "descriptors":
                    result[field] = sum(bool(re.fullmatch(r"f[0-9]+", line)) for line in output.splitlines())
                else:
                    result[field] = max(0, len(output.splitlines()) - 1)
            except (OSError, ValueError, KeyError, HarnessError, subprocess.SubprocessError) as err:
                result[field] = None
                errors.append(f"{field}: {type(err).__name__}: " + excerpt(err))
        if errors:
            result["measurement_errors"] = errors
        return result
    raise HarnessError("resource sampling supports Linux and macOS")


def resource_summary(samples, warmup):
    result, warnings = [], []
    for generation in sorted({row["generation"] for row in samples}):
        rows = [row for row in samples if row["generation"] == generation]
        metrics = {}
        for field in ("rss_kib", "threads", "descriptors", "store_files", "store_bytes"):
            values = [row[field] for row in rows]
            available = [value for value in values if value is not None]
            metrics[field] = dict(
                first=values[0],
                last=values[-1],
                minimum=min(available) if available else None,
                maximum=max(available) if available else None,
            )
        baseline = next((row for row in rows if row["age_seconds"] >= warmup), None)
        if (
            baseline
            and baseline["rss_kib"] is not None
            and rows[-1]["rss_kib"] is not None
            and rows[-1]["rss_kib"] > baseline["rss_kib"] * 1.3
        ):
            warnings.append(
                f"phone generation {generation}: resident memory grew by more than 30 percent after warm-up"
            )
        result.append(dict(generation=generation, pid=rows[0]["pid"], samples=len(rows), metrics=metrics))
    return result, warnings


class Soak:
    """Run serial page questions against independently owned phone and PC processes."""

    def __init__(self, args):
        self.args, self.rng = args, random.Random(args.seed)
        self.started = time.monotonic()
        self.wall_started = datetime.now(UTC).isoformat()
        self.halt = threading.Event()
        self.tmp = Path(tempfile.mkdtemp(prefix="opos-gpu-soak-"))
        self.env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
        self.env.update(
            PYTHONPATH=str(ROOT / "src"),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONUNBUFFERED="1",
            OPOS_APPS="ai",
            OPOS_GPU_LINK="1",
            OPOS_STATE_DIR=str(self.tmp),
            OPOS_CONFIG_DIR=str(self.tmp),
            XDG_STATE_HOME=str(self.tmp),
            HOME=str(self.tmp),
            TMPDIR=str(self.tmp),
        )
        self.children, self.servers, self.threads = [], [], []
        self.phone, self.pc, self.proxy, self.model = None, None, None, None
        self.faults, self.samples = deque(maxlen=256), deque(maxlen=2048)
        self.failures, self.restart_seconds = deque(maxlen=20), deque(maxlen=100)
        self.fault_counts = Counter()
        self.coverage = set()
        self.usable_generation_count, self.usable_generation = 0, 0
        self.sample_baseline = None
        self.resource_failures = deque(maxlen=20)
        self.enrol_process = None
        self.probes = 0
        self.pull_trace = deque(maxlen=256)
        self.args.out.mkdir(parents=True, exist_ok=True)
        self.fault_file = (self.args.out / "faults.jsonl").open("w")
        self.counts = Counter(asked=0, finished=0, failed=0, lost=0, pauses=0, resumes=0, kills=0)
        self.known = Counter(final_repeat_404_lease=0, rate_429=0)
        self.generation, self.generation_started, self.next_sample = 0, 0, 0
        self.fault_lock = threading.Lock()
        self.question, self.last_answer = 0, None
        self.interrupted, self.setup_error = False, None
        self.completed = {}
        self.fault_number = 0
        self.measurement_errors = deque(maxlen=20)

    def emit(self, kind, **parameters):
        event = dict(timestamp=datetime.now(UTC).isoformat(), elapsed=round(time.monotonic() - self.started, 4))
        event.update(type=kind, **parameters)
        with self.fault_lock:
            self.faults.append(event)
            self.fault_counts[kind] += 1
            if kind in ("cut", "stall", "pull"):
                self.fault_number += 1
            if kind in ("cut", "stall", "kill"):
                self.coverage.add(kind)
            if kind == "pull":
                self.coverage.add("short pull" if parameters["seconds"] < 2 else "long pull")
            self.fault_file.write(json.dumps(event) + "\n")
            self.fault_file.flush()
            print("FAULT " + json.dumps(event, ensure_ascii=False), flush=True)
        return event

    def start_server(self, server):
        self.servers.append(server)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        self.threads.append(thread)
        return server

    def page(self, body=None):
        token = (self.tmp / "page.token").read_text().strip()
        connection = http.client.HTTPConnection("127.0.0.1", self.page_port, timeout=0.7)
        try:
            connection.request(
                "GET" if body is None else "POST",
                "/api/ai",
                None if body is None else wire_json(body),
                {"X-OPOS-Token": token, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read()
            if response.status != 200:
                raise InvariantError(f"page API returned HTTP {response.status}")
            return json.loads(raw)
        finally:
            connection.close()

    def start_phone(self, restart=False):
        before = time.monotonic()
        self.phone = Child(
            "phone",
            [
                sys.executable,
                "-B",
                str(self.tmp / "launcher.py"),
                "--port",
                str(self.page_port),
                "--token-file",
                str(self.tmp / "page.token"),
            ],
            self.env,
            self.tmp,
        )
        self.children = [child for child in self.children if child.process.poll() is None]
        self.children.append(self.phone)
        self.generation += 1
        self.sample_baseline = None
        self.generation_started = before
        self.next_sample = before
        while time.monotonic() - before < 10 and not self.halt.is_set():
            if self.phone.process.poll() is not None:
                self.phone.reader.join(2)
                message = "phone failed to bind or start: " + "\n".join(self.phone.tail())
                raise InvariantError(message) if restart else HarnessError(message)
            try:
                view = self.page()
                link = view["gpu"]["link"]
                if link is not None and link["model"]["state"] == "ready":
                    if restart:
                        self.restart_seconds.append(round(time.monotonic() - before, 4))
                    self.sample(force=True)
                    return view
            except (OSError, http.client.HTTPException):
                pass
            self.halt.wait(0.04)
        message = "phone API or link listener did not return within 10 seconds: " + "\n".join(self.phone.tail())
        raise InvariantError(message) if restart else HarnessError(message)

    def enrol(self):
        command = [
            sys.executable,
            "-B",
            str(ROOT / "tools/gpu-link-standin-pc.py"),
            "--key-file",
            str(self.tmp / "pc.key"),
            "--enrol-request",
        ]
        # The key-bearing stdout is a pipe read only into memory, never a log or report.
        process = subprocess.Popen(
            command, cwd=self.tmp, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self.enrol_process = process
        self.enrol_pid = process.pid
        captured = deque(maxlen=1)
        reader = threading.Thread(target=lambda: captured.append(process.stdout.read(16385)), name="soak-enrolment")
        self.threads.append(reader)
        reader.start()
        try:
            process.wait(5)
            reader.join(2)
            if reader.is_alive() or not captured or len(captured[0]) > 16384:
                raise HarnessError("enrolment output exceeded bound or did not finish")
            raw = captured.pop()
        except subprocess.TimeoutExpired as err:
            stop_process(process)
            reader.join(2)
            raise HarnessError("enrolment child timed out") from err
        finally:
            if not reader.is_alive():
                process.stdout.close()
        self.enrol_pid = process.pid
        if process.returncode:
            raise HarnessError("enrolment tool exited unsuccessfully")
        with socket.create_connection(("127.0.0.1", self.link_port), timeout=2) as sock:
            sock.sendall(raw)
            reply = read_message(sock)
        del raw
        if not reply.startswith(b"HTTP/1.1 202 "):
            raise HarnessError("direct enrolment was not accepted")
        code = self.page()["gpu"]["link"]["pending"]["code"]
        self.page({"action": "allow", "code": code})

    def setup(self):
        self.page_port, self.link_port = free_port(), free_port()
        while self.link_port == self.page_port:
            self.link_port = free_port()
        self.env["OPOS_GPU_LINK_PORT"] = str(self.link_port)
        model_path = self.tmp / "fixture.bin"
        model_path.write_bytes(b"GPU Link fixture; not a model\n")
        self.env["OPOS_GPU_LINK_MODEL"] = str(model_path)
        (self.tmp / "launcher.py").write_text(LAUNCHER)
        self.start_phone()
        self.enrol()
        model = ModelServer(self.args)
        model.store_root = self.tmp / "opos"
        self.model = self.start_server(model)
        self.proxy = self.start_server(FaultProxy(self.link_port, self.emit))
        endpoint = f"http://127.0.0.1:{self.model.server_address[1]}/v1/chat/completions"
        self.pc = Child(
            "pc",
            [
                sys.executable,
                "-B",
                str(ROOT / "tools/gpu-link-standin-pc.py"),
                "--key-file",
                str(self.tmp / "pc.key"),
                "--phone",
                f"127.0.0.1:{self.proxy.server_address[1]}",
                "--text",
                "server",
                "--server",
                endpoint,
                "--model",
                "fake",
                "--give-up-after",
                "0",
            ],
            self.env,
            self.tmp,
        )
        self.children.append(self.pc)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not self.halt.is_set():
            self.check_children()
            if self.page()["gpu"]["ready"]:
                return
            self.halt.wait(0.05)
        raise HarnessError("PC did not become ready within 10 seconds")

    def check_children(self):
        for child in (self.phone, self.pc):
            if child is not None and child.process.poll() is not None:
                raise InvariantError(
                    f"{child.role} process exited with {child.process.returncode}: " + "\n".join(child.tail())
                )
        if self.proxy and self.proxy.errors:
            raise HarnessError("proxy worker: " + "; ".join(self.proxy.errors))
        if self.model and self.model.errors:
            raise InvariantError("model worker: " + "; ".join(self.model.errors))

    def sample(self, force=False):
        now = time.monotonic()
        if not force and now < self.next_sample:
            return
        pid = self.phone.process.pid
        try:
            row = process_sample(pid)
        except (OSError, ValueError, KeyError, HarnessError, subprocess.SubprocessError) as err:
            message = f"phone generation {self.generation}: resource sample unavailable: {type(err).__name__}: {err}"
            if message not in self.measurement_errors:
                self.measurement_errors.append(message)
                print(message, file=sys.stderr)
            self.next_sample = time.monotonic() + self.args.sample_every
            return
        for error in row.get("measurement_errors", []):
            message = f"resource sample unavailable: {error}"
            if message not in self.measurement_errors:
                self.measurement_errors.append(message)
                print(message, file=sys.stderr)
        sizes = []
        for path in (self.tmp / "opos").iterdir():
            try:
                if path.is_file():
                    sizes.append(path.stat().st_size)
            except FileNotFoundError:
                # Atomic writes and retention can remove files during the directory scan.
                continue
        row.update(
            elapsed=round(now - self.started, 4),
            age_seconds=round(now - self.generation_started, 4),
            generation=self.generation,
            pid=pid,
            store_files=len(sizes),
            store_bytes=sum(sizes),
        )
        self.samples.append(row)
        self.check_resources(row)
        self.next_sample = time.monotonic() + min(self.args.sample_every, 2)

    def check_resources(self, row):
        if (
            all(row.get(field) is not None for field in ("rss_kib", "threads", "descriptors"))
            and self.usable_generation != self.generation
        ):
            self.usable_generation_count += 1
            self.usable_generation = self.generation
        warmup = min(5, (self.args.duration or 50) * 0.1)
        if row["age_seconds"] >= warmup:
            if self.sample_baseline is None:
                self.sample_baseline = row
            baseline = self.sample_baseline
            for field in ("threads", "descriptors"):
                if row[field] is not None and baseline[field] is not None and row[field] - baseline[field] > 20:
                    message = f"resource growth: phone generation {self.generation}: {field} grew by more than 20"
                    if message not in self.resource_failures:
                        self.resource_failures.append(message)
                    raise InvariantError(message)

    def inject(self, number):
        # The first four applied faults cover each category; parameters remain seeded.
        kind = ("cut", "stall", "short pull", "long pull")[number % 4]
        if "pull" in kind:
            bounds = (0.4, 0.8) if kind == "short pull" else (3.8, 4.2)
            return self.proxy.pull(self.rng.uniform(*bounds))
        self.proxy.arm(kind, "phone_to_pc", self.rng.random(), self.rng.uniform(0.1, 0.3))
        return None

    def stored(self):
        return [json.loads(path.read_text())["answer"] for path in (self.tmp / "opos").glob("ai-answer-*.json")]

    def validate_view(self, view, question, expected, ident):
        answer = view["gpu"]["answer"]
        previous = self.last_answer
        self.last_answer = answer
        if answer is None:
            self.counts["lost"] += 1
            raise InvariantError("question lost from page")
        if answer["id"] != ident or answer["question"] != question:
            raise InvariantError("page showed another question's text or identity")
        if previous is not None and not answer["text"].startswith(previous["text"]):
            raise InvariantError("observed text changed an earlier byte or shrank")
        with self.proxy.lock:
            acked = self.proxy.acked
        matches = [row for row in self.stored() if row["id"] == ident]
        if len(matches) != 1 or len(matches[0]["text"].encode()) < acked:
            raise InvariantError("acknowledged bytes rolled back in durable text")
        if not expected.startswith(answer["text"]):
            raise InvariantError("text mismatch: page text is not an exact expected prefix")
        if view["phone"]["answer"] is not None:
            raise InvariantError("GPU question appeared in the phone pane")
        if answer["state"] in ("failed", "cancelled"):
            raise InvariantError("answer ended in state " + answer["state"])
        return answer

    def finish(self, ident, question, expected):
        answer = self.last_answer
        if answer["text"] != expected:
            raise InvariantError("text mismatch: final GPU answer differs from expected text")
        records = self.stored()
        matches = [row for row in records if row["id"] == ident or row["question"] == question]
        if len(matches) != 1 or matches[0]["text"] != expected or matches[0]["state"] != "done":
            raise InvariantError("answer missing, duplicated or different in durable store")
        # Check retained older answers too; normal retention is allowed to remove them.
        for row in records:
            previous = self.completed.get(row["id"])
            if previous is not None and (row["text"], row["state"], row["question"]) != previous:
                raise InvariantError("another question's stored answer changed")
        self.completed[ident] = (expected, "done", question)
        while len(self.completed) > 100:
            self.completed.pop(next(iter(self.completed)))
        with self.proxy.lock:
            observations = [kind for index, kind in self.proxy.observations if index == self.question]
            self.known.update(observations)
            self.proxy.observations.clear()
            self.proxy.clear_arm()
        self.counts["finished"] += 1
        print(f"FINISHED seed={self.args.seed} question={self.question} bytes={len(expected.encode())}", flush=True)

    def quiet_pc(self):
        """Stop only our PC and let previously dispatched exchanges finish."""
        self.pc.process.send_signal(signal.SIGSTOP)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.proxy.lock:
                quiet = self.proxy.inflight == 0
            if quiet:
                return
            self.halt.wait(0.02)
        raise InvariantError("PC exchanges did not quiesce before probe")

    def signed(self, method, target, body=b"", through_proxy=False):
        # Same framing as tests/gpu_link_fake_pc.py; key and wire bytes stay private.
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        from opos import gpu_link_proto as proto

        port = self.proxy.server_address[1] if through_proxy else self.link_port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        key = (self.tmp / "pc.key").read_bytes()
        try:
            connection.request("GET", "/v1/hello")
            response = connection.getresponse()
            epoch = bytes.fromhex(json.loads(response.read())["epoch"])
            nonce = os.urandom(proto.NONCE_BYTES)
            headers = {
                "X-Link-Pc": proto.pc_id(key),
                "X-Link-Nonce": nonce.hex(),
                "X-Link-Mac": proto.request_mac(key, epoch, method, target, "", nonce, body),
                "Content-Type": "application/json",
            }
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request(method, target, body, headers)
            response = connection.getresponse()
            raw = response.read()
            expected = proto.reply_mac(key, nonce, response.status, response.getheader("Content-Range", ""), raw)
            if not proto.same_mac(response.getheader("X-Link-Mac"), expected):
                raise InvariantError("probe reply signature differs")
            return response.status, json.loads(raw) if raw else None
        finally:
            connection.close()

    def probe_stale(self, ident):
        """Replace a live lease at a durable resume seam, then deliberately use the old id."""
        try:
            self.quiet_pc()
            before = next(row for row in self.stored() if row["id"] == ident)
            if before["state"] not in ("loading", "answering"):
                return False
            with self.proxy.lock:
                leases = [lease for index, lease, _ in self.proxy.leases if index == self.question]
            if not leases:
                return False
            old = leases[-1]
            status, replacement = self.signed("GET", "/v1/work?wait=0", through_proxy=True)
            if status != 200 or replacement["lease"] == old:
                raise InvariantError("stale lease probe could not obtain replacement")
            if replacement["partial"] != before["text"]:
                raise InvariantError(
                    "resume partial differs from durable text: supplied="
                    + excerpt(replacement["partial"])
                    + " durable="
                    + excerpt(before["text"])
                )
            # This is a valid non-final append if the replaced lease is wrongly accepted.
            body = wire_json(dict(offset=len(before["text"].encode()), text="!", done=False, finish=None))
            status, reply = self.signed("POST", f"/v1/runs/{old}/text", body)
            after = next(row for row in self.stored() if row["id"] == ident)
            if status != 404 or reply != {"error": "lease"} or after["text"] != before["text"]:
                raise InvariantError("replaced lease accepted or changed durable text")
            self.probes += 1
            return True
        finally:
            self.pc.process.send_signal(signal.SIGCONT)

    def restart(self, ident):
        """Observe recovery before the PC or service timer can repair a broken load."""
        barrier = self.tmp / "restart.barrier"
        try:
            self.quiet_pc()
            before = next(row for row in self.stored() if row["id"] == ident)
            if before["state"] not in ("loading", "answering"):
                return None
            with self.proxy.lock:
                if len(before["text"].encode()) < self.proxy.acked:
                    raise InvariantError("acknowledged bytes rolled back before kill")
            self.sample(force=True)
            barrier.touch()
            self.phone.process.kill()
            self.phone.process.wait(5)
            self.phone.reader.join(2)
            self.emit("kill", question=self.question, pid=self.phone.process.pid)
            self.counts["kills"] += 1
            view = self.start_phone(restart=True)
            answer = view["gpu"]["answer"]
            if (
                answer is None
                or answer["id"] != ident
                or answer["state"] != "paused"
                or answer["text"] != before["text"]
            ):
                raise InvariantError("restart did not recover paused answer with pre-kill durable text")
            recovered = json.loads((self.tmp / "restart.snapshot").read_text())["answers"]
            retained = {row["id"] for row in self.stored()}
            recovered_ids = {row["id"] for row in recovered}
            if any(key in retained and key not in recovered_ids for key in self.completed):
                raise InvariantError("restart lost a retained completed answer")
            with self.proxy.lock:
                self.proxy.generation = self.generation
            return view
        finally:
            barrier.unlink(missing_ok=True)
            self.pc.process.send_signal(signal.SIGCONT)

    def pending_fault(self, kind, count, state):
        with self.proxy.lock:
            if self.fault_counts[kind] > count:
                return None
            if state == "done" and (self.proxy.armed is not None or not self.proxy.inflight):
                self.proxy.clear_arm()
                return None
        return kind

    def check_pull(self, pull, state, answer, now):
        self.pull_trace.append(
            dict(elapsed=round(now - self.started, 4), state=state, bytes=len(answer["text"].encode()))
        )
        pull["pause_seen"] |= state == "paused"
        pull["done_seen"] |= state == "done"
        if now >= pull["end"]:
            if pull["required"] and not pull["pause_seen"] and not pull["done_seen"]:
                raise InvariantError("long pull did not show the running answer paused")
            return None
        return pull

    def run_question(self):
        self.question += 1
        question = f"Soak {self.args.seed} question {self.question}?"
        expected = expected_text(question, self.args.seed, self.args.self_test)
        self.last_answer = None
        self.pull_trace.clear()
        with self.proxy.lock:
            self.proxy.question = self.question
            self.proxy.acked = 0
        view = self.page({"action": "ask", "question": question, "panes": ["gpu"]})
        self.counts["asked"] += 1
        ident = view["gpu"]["answer"]["id"]
        start = time.monotonic()
        # Deterministic early admission; a fault must be consumed before advancing the cycle.
        next_fault = start + (0.08 if self.fault_number < 4 else self.rng.uniform(*self.args.pull_every))
        killed, injected, previous_state, pull = False, False, None, None
        pending_fault, pending_count = None, 0
        probed = False
        try:
            while not self.halt.is_set():
                self.check_children()
                answer = self.validate_view(view, question, expected, ident)
                state, now = answer["state"], time.monotonic()
                if state == "paused" and previous_state != "paused":
                    self.counts["pauses"] += 1
                if previous_state == "paused" and state in ("loading", "answering", "done"):
                    self.counts["resumes"] += 1
                previous_state = state
                if pull:
                    with self.proxy.lock:
                        pull["end"] = self.proxy.refuse_until
                    pull = self.check_pull(pull, state, answer, now)
                if pending_fault is not None:
                    pending_fault = self.pending_fault(pending_fault, pending_count, state)
                if state == "done" and pull is None and pending_fault is None:
                    self.finish(ident, question, expected)
                    return
                if now - start > self.args.question_timeout:
                    raise InvariantError("question timeout; answer did not finish (including any pause)")
                active = state in ("loading", "answering") and bool(answer["text"])
                # The active negative request is protocol traffic, not a transport fault.
                if (
                    active
                    and not probed
                    and self.probes < (self.question + 9) // 10
                    and pull is None
                    and pending_fault is None
                ):
                    probed = self.probe_stale(ident)
                    view = self.page()
                    continue
                if (
                    self.args.mode in ("pull", "both")
                    and active
                    and now >= next_fault
                    and pull is None
                    and not injected
                    and self.proxy.armed is None
                ):
                    kind = ("cut", "stall", "short pull", "long pull")[self.fault_number % 4]
                    with self.proxy.lock:
                        final_inflight = bool(self.proxy.final_inflight)
                    self.pull_trace.clear()
                    self.pull_trace.append(
                        dict(
                            elapsed=round(now - self.started, 4),
                            state=state,
                            bytes=len(answer["text"].encode()),
                            final_inflight=final_inflight,
                        )
                    )
                    pending_count = self.fault_counts[kind]
                    event = self.inject(self.fault_number)
                    injected = True
                    if event:
                        pull = dict(
                            end=self.proxy.refuse_until,
                            pause_seen=False,
                            done_seen=False,
                            required=event["seconds"] > 3.5 and state != "done" and not final_inflight,
                        )
                    else:
                        pending_fault = kind
                ready_to_kill = self.args.mode == "kill" or {"cut", "stall", "short pull", "long pull"} <= self.coverage
                if (
                    active
                    and not killed
                    and self.args.mode in ("kill", "both")
                    and ready_to_kill
                    and pull is None
                    and pending_fault is None
                    and now - start > 0.15
                    and (self.counts["kills"] == 0 or self.question % 10 == 0)
                ):
                    recovered = self.restart(ident)
                    if recovered is not None:
                        killed = True
                        view = recovered
                        continue
                self.sample()
                self.halt.wait(0.04)
                view = self.page()
            raise InvariantError("run interrupted with a question in flight")
        except InvariantError as err:
            self.failure(str(err), expected)
            raise

    def failure(self, reason, expected=""):
        self.counts["failed"] += 1
        detail = dict(
            seed=self.args.seed,
            question=self.question,
            reason=excerpt(reason, 400),
            expected=excerpt(expected, 200),
            actual=excerpt((self.last_answer or {}).get("text", ""), 200),
            pull_trace=list(self.pull_trace),
            faults=[event for event in self.faults if event.get("question") == self.question][-12:],
        )
        self.failures.append(detail)
        print("FAILURE " + json.dumps(detail, ensure_ascii=False), flush=True)

    def run(self):
        self.setup()
        self.run_started = time.monotonic()
        while not self.halt.is_set():
            if self.args.questions is not None and self.counts["asked"] >= self.args.questions:
                break
            if self.args.duration is not None and time.monotonic() - self.run_started >= self.args.duration:
                break
            self.run_question()
            # A short pull may have outlived a final post; finish the outage before a new question.
            self.halt.wait(max(0, self.proxy.refuse_until - time.monotonic()))
        self.check_children()
        self.sample(force=True)
        if self.halt.is_set():
            raise InvariantError("run interrupted")
        required = {"cut", "stall", "short pull", "long pull"} if self.args.mode in ("pull", "both") else set()
        if self.args.mode in ("kill", "both"):
            required.add("kill")
        missing = sorted(required - self.coverage)
        if missing:
            raise InvariantError("fault coverage: " + ", ".join(missing))
        if self.probes < (self.question + 9) // 10:
            raise InvariantError("stale lease probe coverage missing")

    def cleanup(self):
        errors = []
        for child in reversed(self.children):
            try:
                child.stop()
            except Exception as err:
                errors.append("child cleanup: " + type(err).__name__)
                # A faulty reader must not leave its process alive.
                try:
                    stop_process(child.process)
                except Exception as fallback:
                    errors.append("child escalation: " + type(fallback).__name__)
        if self.enrol_process is not None:
            try:
                stop_process(self.enrol_process)
                if self.enrol_process.stdout is not None:
                    self.enrol_process.stdout.close()
            except Exception as err:
                errors.append("enrolment cleanup: " + type(err).__name__)
        for server in reversed(self.servers):
            errors.extend(stop_server(server))
        for thread in self.threads:
            try:
                thread.join(3)
                if thread.is_alive():
                    errors.append("server thread did not stop")
            except Exception as err:
                errors.append("server join: " + type(err).__name__)
        try:
            self.fault_file.close()
        except Exception as err:
            errors.append("fault log cleanup: " + type(err).__name__)
        if not self.args.keep:
            try:
                shutil.rmtree(self.tmp)
            except OSError:
                errors.append("temporary state removal failed")
        if errors:
            raise HarnessError("; ".join(errors))

    def report(self, exit_status):
        duration = self.args.duration or (time.monotonic() - self.started)
        resources, warnings = resource_summary(self.samples, min(5, duration * 0.1))
        report = dict(
            seed=self.args.seed,
            mode=self.args.mode,
            exit_status=exit_status,
            questions={key: self.counts[key] for key in ("asked", "finished", "failed", "lost")},
            faults_by_type=dict(self.fault_counts),
            fault_coverage=sorted(self.coverage),
            stale_lease_probes=self.probes,
            fault_log=list(self.faults),
            pauses=self.counts["pauses"],
            resumes=self.counts["resumes"],
            fake_model=dict(self.model.counts) if self.model else {},
            known_limits=dict(self.known),
            resources=resources,
            resource_samples=list(self.samples),
            resource_measurement_sources=sorted({row.get("source", "native process commands") for row in self.samples}),
            warnings=warnings,
            resource_failures=list(self.resource_failures),
            restart_ready_seconds=list(self.restart_seconds),
            durations=dict(
                requested_seconds=self.args.duration,
                elapsed_seconds=round(time.monotonic() - self.started, 4),
                question_timeout_seconds=self.args.question_timeout,
            ),
            started_utc=self.wall_started,
            interpreter=sys.executable,
            python=sys.version,
            platform=platform.platform(),
            child_processes=[
                dict(role=child.role, pid=child.process.pid, returncode=child.process.returncode)
                for child in self.children
            ],
            enrolment_pid=getattr(self, "enrol_pid", None),
            temporary_directory=str(self.tmp) if self.args.keep else None,
            temporary_directory_removed=not self.tmp.exists(),
            failures=list(self.failures),
            harness_error=self.setup_error,
            measurement_errors=list(self.measurement_errors),
            self_test=self.args.self_test,
            plant_drop_token=self.args.plant_drop_token,
            scope="Loopback fixture only; model, battery, calls and texts simulated; physical phone NOT_RUN",
        )
        lines = ["| Measure | Result |", "| --- | --- |"]
        rows = dict(
            seed=report["seed"],
            mode=report["mode"],
            exit_status=exit_status,
            questions=report["questions"],
            faults=report["faults_by_type"],
            pauses=report["pauses"],
            resumes=report["resumes"],
            fake_model=report["fake_model"],
            known_limits=report["known_limits"],
            durations=report["durations"],
            interpreter=sys.executable,
            python=platform.python_version(),
            platform=report["platform"],
            restart_ready_seconds=list(self.restart_seconds),
            children_reaped=all(child.process.poll() is not None for child in self.children),
            self_test=report["self_test"],
            plant_drop_token=report["plant_drop_token"],
            scope=report["scope"],
            resource_measurement_sources=report["resource_measurement_sources"],
            resource_order="first/last/min/max; unavailable values are None",
        )
        for key, value in rows.items():
            rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            lines.append(f"| {key} | {rendered.replace('|', '/')} |")
        for resource in resources:
            values = []
            for field, metrics in resource["metrics"].items():
                values.append(
                    field + "=" + "/".join(str(metrics[key]) for key in ("first", "last", "minimum", "maximum"))
                )
            lines.append(f"| phone generation {resource['generation']} | " + "; ".join(values) + " |")
        for warning in warnings:
            print("WARN " + warning, flush=True)
            lines.extend(["", "WARN " + warning])
        for error in self.measurement_errors:
            lines.extend(["", "Measurement error: " + error])
        if self.setup_error:
            lines.extend(["", "Harness error: " + self.setup_error])
        for failure in self.failures:
            lines.extend(["", "Failure:", "```json", json.dumps(failure, ensure_ascii=False, indent=2), "```"])
        self.args.out.mkdir(parents=True, exist_ok=True)
        (self.args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        (self.args.out / "report.md").write_text("\n".join(lines) + "\n")
        print(
            f"SUMMARY seed={self.args.seed} mode={self.args.mode} exit={exit_status} "
            f"asked={self.counts['asked']} finished={self.counts['finished']} failed={self.counts['failed']} "
            f"reports={self.args.out}",
            flush=True,
        )


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def interval(value):
    try:
        low, high = map(positive, value.split("-"))
        if high < low:
            raise ValueError
        return low, high
    except (ValueError, argparse.ArgumentTypeError) as err:
        raise argparse.ArgumentTypeError("use MIN-MAX with 0 < MIN <= MAX") from err


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("pull", "kill", "both"), default="both")
    parser.add_argument("--duration", type=positive, help="stop new questions after this many seconds; default 300")
    parser.add_argument("--questions", type=int)
    parser.add_argument("--seed", type=int, default=743)
    parser.add_argument(
        "--pull-every",
        type=interval,
        default=(2.0, 5.0),
        metavar="MIN-MAX",
        help="fault delay after initial category coverage; initial faults occur early",
    )
    parser.add_argument("--out", type=Path, default=Path("gpu-link-soak-report"))
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--sample-every", type=positive, default=2, help="sample interval, capped at 2 seconds")
    parser.add_argument("--token-rate", type=positive, default=40)
    parser.add_argument("--question-timeout", type=positive, default=120)
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--plant-drop-token", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if (args.self_test or args.plant_drop_token) and os.environ.get("OPOS_SOAK") != "1":
        parser.error("test-only switches require OPOS_SOAK=1")
    if args.questions is not None and args.questions <= 0:
        parser.error("--questions must be greater than zero")
    if args.duration is None and args.questions is None:
        args.duration = 300
    return args


def main(argv=None):
    args = arguments(argv)
    try:
        run = Soak(args)
    except OSError as err:
        print(f"cannot create private temporary state: {err}", file=sys.stderr)
        return 2
    previous = {}

    def interrupted(signum, frame):
        run.interrupted = True
        run.halt.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, interrupted)
    status = 0
    try:
        run.run()
        if run.resource_failures:
            raise InvariantError(run.resource_failures[0])
        if not run.samples or run.usable_generation_count != run.generation:
            raise HarnessError("resource measurements unavailable: no usable samples for every phone generation")
        if run.measurement_errors:
            raise HarnessError("resource measurements unavailable: " + "; ".join(run.measurement_errors))
    except InvariantError as err:
        if not run.failures:
            run.failure(str(err))
        status = 1
    except (OSError, ValueError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as err:
        run.setup_error = type(err).__name__ + ": " + excerpt(err, 400)
        print(run.setup_error, file=sys.stderr)
        status = 2
    except Exception as err:
        run.setup_error = "unexpected harness error: " + type(err).__name__
        print(run.setup_error, file=sys.stderr)
        status = 2
    finally:
        try:
            run.cleanup()
        except Exception as err:
            run.setup_error = "cleanup: " + type(err).__name__ + ": " + excerpt(err)
            print(run.setup_error, file=sys.stderr)
            status = 2
        try:
            run.report(status)
        except OSError as err:
            print(f"cannot write reports: {err}", file=sys.stderr)
            status = 2
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return status


if __name__ == "__main__":
    sys.exit(main())
