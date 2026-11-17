# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic in-memory driver shared by the setup factory golden tests.

Connection I/O, scheduling, clock/entropy and external state discovery are
controlled. The named recursion case injects only the JSON decoder failure;
security parsing and preference persistence otherwise use their real code.
The corpus was recorded from pinned, unmodified source; expected values are
never assembled by this driver. Temporary path substitution applies only to
recorded call arguments, never to response or file bytes.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opos import notices
from opos.aitools import wizard
from opos.setup import server as setup

CORPUS = Path(__file__).with_name("cases.json")
SESSION_VALUE = "fixture-token"
NONCE = "a" * 32


class FixtureAbortError(BaseException):
    """Controlled process-control stand-in for testing narrow exception catches."""


def corpus_cases(group=None):
    cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
    return [case for case in cases if group is None or case["group"] == group]


class MemoryInput(io.BytesIO):
    def __init__(self, data, driver):
        super().__init__(data)
        self.driver = driver

    def read(self, size=-1):
        self.driver.record("body.read", size)
        self.driver.fail("body_read", TimeoutError)
        return super().read(size)


class MemorySocket:
    def __init__(self, driver, data=b""):
        self.driver = driver
        self.input = MemoryInput(data, driver)
        self.output = bytearray()

    def makefile(self, mode, buffering):
        self.driver.record("socket.makefile", mode, buffering)
        self.driver.fail("makefile")
        return self.input

    def settimeout(self, seconds):
        self.driver.record("socket.settimeout", seconds)
        self.driver.fail("settimeout")

    def close(self):
        self.driver.record("socket.close")
        self.driver.fail("close")

    def shutdown(self, how):
        self.driver.record("socket.shutdown", how)
        self.driver.fail("shutdown", OSError)

    def sendall(self, data):
        self.driver.fail("write", BrokenPipeError)
        self.output.extend(data)


class Slots:
    def __init__(self, driver, capacity):
        self.driver = driver
        self.capacity = capacity
        self.held = driver.case.get("held", 0)

    def acquire(self, blocking=True):
        allowed = self.held < self.capacity
        self.driver.record("slots.acquire", blocking=blocking, allowed=allowed)
        if allowed:
            self.held += 1
        return allowed

    def release(self):
        self.driver.record("slots.release")
        self.held -= 1


class Timer:
    def __init__(self, driver, interval, function):
        self.driver = driver
        self.function = function
        self.daemon = False
        driver.record("timer.init", interval)
        driver.fail("timer_init")

    def start(self):
        self.driver.record("timer.start", daemon=self.daemon)
        self.driver.fail("timer_start")

    def cancel(self):
        self.driver.record("timer.cancel")
        self.driver.fail("timer_cancel")


class ApplyThread:
    def __init__(self, driver, target, daemon):
        self.driver = driver
        self.target = target
        driver.record("thread.init", daemon=daemon)
        driver.fail("thread_init")

    def start(self):
        self.driver.record("thread.start")
        self.driver.fail("thread_start")
        if not self.driver.case.get("defer_thread"):
            self.target()


class Progress:
    def __init__(self, driver):
        self.driver = driver
        self.finished = False
        driver.record("progress.init")

    def done(self):
        self.driver.record("progress.done")
        return self.finished

    def result(self):
        self.driver.record("progress.result")
        return {"running": not self.finished, "done": self.finished, "ok": self.finished, "steps": ["fixture"]}


class Driver:
    def __init__(self, case, root):
        self.case = case
        self.root = root
        self.now = 0.0
        self.clock_calls = 0
        self.calls = []
        self.server = None
        self.slots = None
        self.responses = []
        self.context = setup.SetupContext(
            repo_root=root / "repo",
            python="python-fixture",
            home=root / "home",
            platform="linux",
            env={"OPOS_CONFIG_DIR": str(root / "state")},
            dry_run=True,
            runner="runner-fixture",
            health_check="health-fixture",
            tools_runner="tools-fixture",
            app_port=8765,
            root=str(root / "device"),
        )
        for name, value in case.get("context", {}).items():
            setattr(self.context, name, value)

    def normalize(self, value):
        if isinstance(value, Path):
            return self.normalize(str(value))
        if isinstance(value, str) and value.startswith(str(self.root)):
            return "<state>" + value[len(str(self.root)) :]
        if isinstance(value, (list, tuple)):
            return [self.normalize(item) for item in value]
        if isinstance(value, dict):
            return {name: self.normalize(item) for name, item in value.items()}
        if isinstance(value, Progress):
            return "<progress>"
        return value

    def record(self, name, *args, **kwargs):
        self.calls.append({"call": name, "args": self.normalize(args), "kwargs": self.normalize(kwargs)})

    def fail(self, name, exception=RuntimeError):
        if name in self.case.get("faults", []):
            kind = self.case.get("fault_kinds", {}).get(name)
            exception = {"abort": FixtureAbortError, "runtime": RuntimeError}.get(kind, exception)
            raise exception("fixture " + name)

    def wrapper(self, name, function):
        def call(*args, **kwargs):
            self.record(name, *args, **kwargs)
            self.fail(name)
            return function(*args, **kwargs)

        return call

    def factory(self, address, handler):
        self.record("server.init", address)
        self.fail("server")
        return SimpleNamespace(
            server_address=(address[0], self.case.get("actual_port", 43123)), RequestHandlerClass=handler
        )

    def config_load(self, path):
        mode = self.case.get("state_mode", "missing")
        if mode == "config_error":
            raise setup._config.ConfigError("fixture invalid config")
        if mode == "load_error":
            raise OSError("fixture unreadable config")
        if mode == "load_value_error":
            raise ValueError("fixture load value error")
        return {"fixture": True} if mode == "loaded" else {} if mode == "empty" else None

    def validate(self, config):
        return ("fixture invalid", "second error") if config.get("invalid") else ()

    def apply(self, config, **kwargs):
        (self.root / "state" / "applied.json").write_text(json.dumps(config), encoding="utf-8")
        kwargs["progress"].finished = True

    def public_state(self, **kwargs):
        if self.case.get("state_mode") == "ai_error":
            raise RuntimeError("fixture tools unavailable")
        return {"supported": True, "tools": ["fixture"]}

    def rate_limiter(self, rate, burst):
        self.record("limiter.init", rate, burst)
        limiter = self.real_limiter(rate, burst, clock=lambda: self.now)
        return SimpleNamespace(allow=self.wrapper("limiter.allow", limiter.allow))

    def semaphore(self, capacity):
        self.record("slots.init", capacity)
        self.slots = Slots(self, capacity)
        return self.slots

    def clock(self):
        self.clock_calls += 1
        if self.clock_calls == 2:
            self.now += self.case.get("setup_elapsed", 0)
        if self.clock_calls == self.case.get("expire_clock_call"):
            self.now += 10
        self.record("clock", self.now)
        return self.now

    def patches(self, stack):
        def patch(obj, name, value):
            stack.enter_context(mock.patch.object(obj, name, value))

        def wrapped(obj, name, function=None, label=None):
            patch(obj, name, self.wrapper(label or name, function or getattr(obj, name)))

        self.real_limiter = setup.secure.RateLimiter
        for name in ("check_bind", "host_ok", "token_ok", "origin_ok", "json_type_ok", "security_headers"):
            wrapped(setup.secure, name)
        # read_json_body's stream is recorded separately, avoiding object identities.
        patch(setup.secure, "read_json_body", self.body_decode)
        wrapped(setup.secure, "new_token", lambda: SESSION_VALUE)
        patch(setup.secure, "RateLimiter", self.rate_limiter)
        wrapped(setup._config, "config_dir", lambda *args: self.root / "state")
        wrapped(setup._config, "config_path", lambda *args: self.root / "state" / "config.json")
        wrapped(setup._config, "load", self.config_load)
        wrapped(setup._config, "default_config")
        wrapped(setup._config, "validate", self.validate)
        wrapped(setup._device, "detect_device", lambda *args: {"profile_id": "slab"})
        wrapped(setup._device, "list_profiles", lambda: [{"profile_id": "slab"}])
        wrapped(setup._device, "choose_profile", lambda value: {"profile_id": "slab"} if value == "slab" else None)
        for name in ("answer", "skip", "skip_all", "finish", "load", "resume_step", "effective"):
            wrapped(setup.prefs, name, label="prefs." + name)
        wrapped(setup._apply, "apply", self.apply)
        wrapped(wizard, "public_state", self.public_state)
        wrapped(setup._platform_mod, "machine", lambda: "fixture-arch")
        wrapped(setup.secrets, "token_hex", lambda size: NONCE)
        # Release metadata belongs to the page tests, not the setup factory.
        patch(notices, "VERSION", "0.1.0")
        patch(notices, "BUILD", "unpublished")
        wrapped(setup, "page")
        patch(setup.time, "monotonic", self.clock)
        patch(setup.threading, "Semaphore", self.semaphore)
        patch(setup.threading, "Timer", lambda interval, function: Timer(self, interval, function))
        patch(setup.threading, "Thread", lambda **kwargs: ApplyThread(self, **kwargs))
        patch(setup, "Progress", self.progress)
        patch(setup, "ThreadingHTTPServer", self.factory)

    def body_decode(self, stream, headers):
        self.record("body.decode", list(headers.items()))
        self.fail("body_decode")
        if self.case.get("decoder_error") == "recursion":
            with mock.patch.object(setup.secure.json, "loads", self.decoder_recursion):
                return self.real_body_decode(stream, headers)
        return self.real_body_decode(stream, headers)

    def decoder_recursion(self, text, *, parse_constant):
        self.record("json.loads", text, parse_constant=callable(parse_constant))
        raise RecursionError("fixture decoder recursion")

    def progress(self):
        self.fail("progress_init")
        return Progress(self)

    def build(self):
        args = {"host": self.case["host"]} if "host" in self.case else {}
        self.server = setup.make_server(self.case.get("port", 0), self.context, **args)
        self.record("server.return", self.server.server_address, session=self.server.token)
        self.observe_progress()

    def observe_progress(self):
        progress = self.server.progress_state.progress
        self.record("server.progress", progress, finished=progress.finished if isinstance(progress, Progress) else None)

    def handler(self, sock):
        handler = object.__new__(self.server.RequestHandlerClass)
        handler.request = sock
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = self.server
        return handler

    def request_bytes(self, spec):
        body = base64.b64decode(spec["raw_base64"]) if "raw_base64" in spec else json.dumps(spec["payload"]).encode()
        headers = {
            "Host": "127.0.0.1:43123",
            "X-OPOS-Token": SESSION_VALUE,
            "Origin": "http://127.0.0.1:43123",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        headers.update(spec.get("headers", {}))
        items = [(key, value) for key, value in headers.items() if value is not None]
        items.extend(spec.get("extra_headers", []))
        lines = [f"{spec['method']} {spec['path']} HTTP/1.0"] + [f"{key}: {value}" for key, value in items]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body

    def http(self, spec):
        self.now += spec.get("advance", 0)
        sock = MemorySocket(self, self.request_bytes(spec))
        handler = self.handler(sock)
        error = None
        try:
            handler.setup()
            if "left" in spec:
                handler._deadline = self.now + spec["left"]
            if spec.get("direct"):
                handler.raw_requestline = handler.rfile.readline(65537)
                handler.parse_request()
                handler._dispatch()
            else:
                handler.handle_one_request()
        except (Exception, FixtureAbortError) as exc:
            error = exception_record(exc)
        finally:
            if hasattr(handler, "_budget_timer"):
                handler.finish()
        # Only methods still owned by BaseHTTPRequestHandler have a runtime-
        # dependent 501 template. Factory-produced response bytes stay exact.
        stdlib_method = not hasattr(handler, "do_" + spec["method"])
        self.responses.append(response_record(sock.output, error, stdlib_method=stdlib_method))
        self.observe_progress()

    def lifecycle(self):
        action = self.case["action"]
        if action == "factory":
            return
        sock = MemorySocket(self)
        handler = self.handler(sock)
        if action == "log":
            handler.log_message("private %s", SESSION_VALUE)
            return
        if action in ("shrink", "handle"):
            handler.connection = sock
            handler._deadline = self.now + self.case.get("left", 5)
            handler.close_connection = False
            with mock.patch.object(setup.BaseHTTPRequestHandler, "handle_one_request", self.base_handle):
                getattr(handler, "_shrink_timeout" if action == "shrink" else "handle_one_request")()
            self.record("handler.close_connection", handler.close_connection)
            return
        handler.setup()
        if action == "expire":
            handler._budget_timer.function()
        if "flush" in self.case.get("faults", []):
            handler.wfile = SimpleNamespace(closed=False, flush=lambda: self.fail("flush"), close=lambda: None)
        handler.finish()

    def base_handle(self):
        self.record("base.handle_one_request")

    def run(self):
        error = None
        (self.root / "state").mkdir()
        self.real_body_decode = setup.secure.read_json_body
        stderr = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stderr(stderr))
            self.patches(stack)
            try:
                self.build()
                if self.case["group"] == "lifecycle":
                    self.lifecycle()
                else:
                    for spec in self.case["requests"]:
                        self.http(spec)
            except (Exception, FixtureAbortError) as exc:
                error = exception_record(exc)
        files = {
            str(path.relative_to(self.root)): base64.b64encode(path.read_bytes()).decode("ascii")
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }
        return {
            "calls": self.calls,
            "responses": self.responses,
            "files": files,
            "exception": error,
            "slots_held": self.slots.held if self.slots else None,
            "stderr": stderr.getvalue(),
        }


def exception_record(exc):
    return {"type": type(exc).__name__, "message": str(exc)}


def response_record(raw, error, *, stdlib_method=False):
    head, separator, body = bytes(raw).partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n") if separator else []
    headers = [line.split(": ", 1) for line in lines[1:]]
    status = int(lines[0].split(" ")[1]) if lines else None
    stdlib_error = stdlib_method and status == 501
    return {
        "status": status,
        "headers": [
            [key, value]
            for key, value in headers
            if key.lower() not in ("server", "date") and not (stdlib_error and key.lower() == "content-length")
        ],
        "body_base64": None if stdlib_error else base64.b64encode(body).decode("ascii"),
        "exception": error,
    }


def run_case(case):
    with tempfile.TemporaryDirectory(prefix="setup-golden-") as folder:
        return Driver(case, Path(folder)).run()
