# SPDX-License-Identifier: GPL-3.0-or-later
"""Loopback web server for the Old Phone OS Interface first-run setup wizard."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform as _platform_mod
import secrets
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from opos import secure
from opos.setup import apply as _apply
from opos.setup import config as _config
from opos.setup import device as _device
from opos.setup import prefs
from opos.setup.apply import Progress
from opos.setup.wizard_page import page


class SetupContext:
    """Values the setup server needs from its launcher."""

    def __init__(self, *, repo_root: Path, python: str, home: Path, platform: str, env: Mapping[str, str] | None = None, dry_run: bool = False, runner: Callable[..., Any] | None = None, health_check: Callable[..., Any] | None = None, tools_runner: Callable[..., Any] | None = None, app_port: int = 8765, root: str = "/") -> None:
        """Store the launcher-provided context."""
        self.repo_root = repo_root
        self.python = python
        self.home = home
        self.platform = platform
        self.env = env
        self.dry_run = dry_run
        self.runner = runner
        self.health_check = health_check
        self.tools_runner = tools_runner
        self.app_port = app_port
        self.root = root


class _ProgressState:
    """Lock-protected holder for the single in-flight apply run."""

    def __init__(self) -> None:
        """Create an empty holder."""
        self.lock = threading.Lock()
        self.progress: Progress | None = None


def make_server(port: int, ctx: SetupContext, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Build the setup server bound to a loopback address."""
    secure.check_bind(host)
    token = secure.new_token()
    limiter = secure.RateLimiter(20.0, 60)
    state = _ProgressState()
    bound: dict[str, int] = {"port": port}
    preference_path = _config.config_dir(ctx.env, ctx.platform) / "prefs.json"

    class Handler(BaseHTTPRequestHandler):
        """Handle setup wizard requests."""

        timeout = 10
        request_budget = 10.0
        _slots = threading.Semaphore(16)

        def setup(self) -> None:
            """Enforce absolute connection read budget and cap concurrent connections."""
            if not type(self)._slots.acquire(blocking=False):
                self.request.close()
                raise ConnectionAbortedError("too many connections")
            try:
                super().setup()
                self._deadline = time.monotonic() + type(self).request_budget
                self._shrink_timeout()
                def _expire() -> None:
                    with contextlib.suppress(OSError):
                        self.connection.shutdown(socket.SHUT_RDWR)
                self._budget_timer = threading.Timer(self._time_left(), _expire)
                self._budget_timer.daemon = True
                self._budget_timer.start()
            except Exception:
                with contextlib.suppress(Exception):
                    if getattr(self, "_budget_timer", None):
                        self._budget_timer.cancel()
                with contextlib.suppress(Exception):
                    self.connection.close()
                type(self)._slots.release()
                raise

        def finish(self) -> None:
            """Cancel absolute connection read budget timer and release the connection slot."""
            try:
                with contextlib.suppress(Exception):
                    self._budget_timer.cancel()
                super().finish()
            finally:
                type(self)._slots.release()

        def _time_left(self) -> float:
            """Return remaining seconds of the total request budget."""
            return self._deadline - time.monotonic()

        def _shrink_timeout(self) -> None:
            """Shrink the socket timeout to whatever remains of the budget."""
            left = self._time_left()
            if left <= 0:
                self.connection.close()
                raise TimeoutError("request budget spent")
            self.connection.settimeout(min(self.timeout, left))

        def handle_one_request(self) -> None:
            """Bound total time spent reading the request line and headers."""
            try:
                self._shrink_timeout()
            except TimeoutError:
                self.close_connection = True
                return
            super().handle_one_request()

        def log_message(self, format: str, *args: Any) -> None:
            """Suppress request logging so the token never reaches a log."""

        def _json(self, status: int, payload: Any) -> None:
            """Send a JSON response with security headers."""
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            for name, value in secure.security_headers().items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _html(self, status: int, text: str, nonce: str) -> None:
            """Send an HTML response with a nonced security header set."""
            body = text.encode("utf-8")
            self.send_response(status)
            for name, value in secure.security_headers(nonce).items():
                self.send_header(name, value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _state(self) -> dict[str, Any]:
            """Assemble the wizard state payload."""
            cfg_path = _config.config_path(ctx.env, ctx.platform)
            cfg_dir = _config.config_dir(ctx.env, ctx.platform)
            config_error = False
            try:
                loaded = _config.load(cfg_path)
            except _config.ConfigError:
                loaded = None
                config_error = True
            payload: dict[str, Any] = {
                "first_run": loaded is None,
                "config": loaded if loaded is not None else _config.default_config(),
                "device": _device.detect_device(ctx.root, cfg_dir),
                "profiles": _device.list_profiles(),
                "services": list(_config.SERVICES),
                "battery_modes": list(_config.BATTERY_MODES),
                "dry_run": ctx.dry_run,
                "platform": ctx.platform,
                "app_port": ctx.app_port,
            }
            if config_error:
                payload["config_error"] = True
            try:
                from opos.aitools.wizard import public_state

                payload["ai_tools"] = public_state(env=ctx.env or {}, home=ctx.home, machine=_platform_mod.machine(), platform=ctx.platform)
            except Exception:
                payload["ai_tools"] = {"supported": False, "tools": [], "modes": ["lite", "full"], "default_mode": "lite", "error": "unavailable"}
            return payload

        def _preferences(self) -> dict[str, Any]:
            """Build the preference response from one read of the selected profile."""
            saved = prefs.load(preference_path)
            return {"state": saved, "resume": prefs.resume_step(saved), "effective": prefs.effective(saved)}

        def _progress(self) -> dict[str, Any]:
            """Assemble the apply progress payload."""
            with state.lock:
                progress = state.progress
            if progress is None:
                return {"running": False, "done": False, "ok": False, "steps": []}
            snap = progress.snapshot()
            steps = snap.get("steps", []) if isinstance(snap, dict) else snap
            done = bool(progress.done())
            return {"running": not done, "done": done, "ok": bool(progress.ok()), "steps": list(steps)}

        def _apply(self, cfg: Any) -> None:
            """Validate and start an apply run, or report why not."""
            if not isinstance(cfg, dict):
                self._json(400, {"ok": False, "errors": ["config must be an object"]})
                return
            errors = _config.validate(cfg)
            if errors:
                self._json(400, {"ok": False, "errors": list(errors)})
                return
            with state.lock:
                if state.progress is not None and not state.progress.done():
                    self._json(409, {"error": "already running"})
                    return
                progress = Progress()
                state.progress = progress

            def run() -> None:
                """Run apply off the request thread."""
                _apply.apply(cfg, repo_root=ctx.repo_root, python=ctx.python, home=ctx.home, platform=ctx.platform, env=ctx.env, port=ctx.app_port, dry_run=ctx.dry_run, runner=ctx.runner, health_check=ctx.health_check, tools_runner=ctx.tools_runner, progress=progress)

            threading.Thread(target=run, daemon=True).start()
            self._json(202, {"started": True})

        def _dispatch(self) -> None:
            """Route one request after the shared security checks."""
            actual_port = bound["port"]
            if not secure.host_ok(self.headers.get("Host"), actual_port):
                self._json(403, {"error": "forbidden"})
                return
            is_api = self.path.startswith("/setup/api/")
            if is_api and not secure.token_ok(self.headers.get("X-OPOS-Token"), token):
                self._json(403, {"error": "forbidden"})
                return
            if self.command not in ("GET", "POST"):
                self._json(405, {"error": "method not allowed"})
                return
            if self.command == "GET":
                if self.path == "/":
                    nonce = secrets.token_hex(16)
                    self._html(200, page(token, nonce), nonce)
                elif self.path == "/setup/api/state":
                    self._json(200, self._state())
                elif self.path == "/setup/api/progress":
                    self._json(200, self._progress())
                elif self.path == "/setup/api/prefs":
                    self._json(200, self._preferences())
                else:
                    self._json(404, {"error": "not found"})
                return
            if not secure.origin_ok(self.headers.get("Origin"), actual_port):
                self._json(403, {"error": "forbidden"})
                return
            if not secure.json_type_ok(self.headers.get("Content-Type")):
                self._json(415, {"error": "unsupported media type"})
                return
            if not limiter.allow(self.client_address[0]):
                self._json(429, {"error": "too many requests"})
                return
            if self.path not in ("/setup/api/device", "/setup/api/validate", "/setup/api/apply", "/setup/api/prefs", "/setup/api/prefs/skip", "/setup/api/prefs/skip-all", "/setup/api/prefs/finish"):
                self._json(404, {"error": "not found"})
                return
            try:
                self._shrink_timeout()
                body = secure.read_json_body(self.rfile, self.headers)
            except secure.BodyError as exc:
                self._json(exc.status, {"error": exc.message})
                return
            if self.path == "/setup/api/device":
                profile_id = body.get("profile_id")
                chosen = _device.choose_profile(profile_id) if isinstance(profile_id, str) else None
                if chosen is None:
                    self._json(400, {"error": "unknown profile"})
                else:
                    self._json(200, chosen)
            elif self.path == "/setup/api/validate":
                cfg = body.get("config")
                errors = _config.validate(cfg) if isinstance(cfg, dict) else ["config must be an object"]
                self._json(200, {"ok": not errors, "errors": list(errors)})
            elif self.path == "/setup/api/prefs":
                step = body.get("step")
                value = body.get("value")
                try:
                    prefs.answer(step, value, path=preference_path)
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, self._preferences())
                return
            elif self.path == "/setup/api/prefs/skip":
                step = body.get("step")
                try:
                    prefs.skip(step, path=preference_path)
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, {})
                return
            elif self.path == "/setup/api/prefs/skip-all":
                prefs.skip_all(path=preference_path)
                self._json(200, {})
                return
            elif self.path == "/setup/api/prefs/finish":
                prefs.finish(path=preference_path)
                self._json(200, self._preferences())
                return
            else:
                self._apply(body.get("config"))

        def _handle(self) -> None:
            """Dispatch, hiding unexpected failures behind a generic 500."""
            try:
                self._dispatch()
            except Exception:
                with contextlib.suppress(Exception):
                    self._json(500, {"error": "internal error"})

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_PATCH = _handle
        do_HEAD = _handle
        do_OPTIONS = _handle

    server = ThreadingHTTPServer((host, port), Handler)
    bound["port"] = int(server.server_address[1])
    server.token = token  # type: ignore[attr-defined]
    server.progress_state = state  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    """Run the setup wizard server until interrupted."""
    parser = argparse.ArgumentParser(prog="opos-setup")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--config-dir")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.config_dir:
        env["OPOS_CONFIG_DIR"] = args.config_dir
    ctx = SetupContext(repo_root=Path(__file__).resolve().parents[3], python=sys.executable, home=Path.home(), platform=sys.platform, env=env, dry_run=args.dry_run)
    server = make_server(args.port, ctx)
    url = f"http://127.0.0.1:{int(server.server_address[1])}/"
    print(f"Setup wizard: {url}")
    print("No browser on this device? Run: opos run --defaults")
    print(f"Or from your computer: ssh -L {int(server.server_address[1])}:127.0.0.1:{int(server.server_address[1])} <user>@<phone>, then open {url} there.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    finally:
        server.server_close()
    return 0
