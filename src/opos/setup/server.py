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

    def __init__(
        self,
        *,
        repo_root: Path,
        python: str,
        home: Path,
        platform: str,
        env: Mapping[str, str] | None = None,
        dry_run: bool = False,
        runner: Callable[..., Any] | None = None,
        health_check: Callable[..., Any] | None = None,
        tools_runner: Callable[..., Any] | None = None,
        app_port: int = 8765,
        root: str = "/",
    ) -> None:
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


_Refusal = tuple[int, dict[str, str]]


def _host_refusal(handler: _SetupHandler, port: int) -> _Refusal | None:
    """Refuse a request whose Host header does not name the bound loopback port."""
    if not secure.host_ok(handler.headers.get("Host"), port):
        return 403, {"error": "forbidden"}
    return None


def _token_refusal(handler: _SetupHandler, port: int) -> _Refusal | None:
    """Refuse an API request without the session token (constant-time check in secure)."""
    is_api = handler.path.startswith("/setup/api/")
    if is_api and not secure.token_ok(handler.headers.get("X-OPOS-Token"), handler._token):
        return 403, {"error": "forbidden"}
    return None


def _method_refusal(handler: _SetupHandler, port: int) -> _Refusal | None:
    """Refuse every method other than GET and POST."""
    if handler.command not in ("GET", "POST"):
        return 405, {"error": "method not allowed"}
    return None


def _origin_refusal(handler: _SetupHandler, port: int) -> _Refusal | None:
    """Refuse a write whose Origin header is not the bound loopback origin."""
    if not secure.origin_ok(handler.headers.get("Origin"), port):
        return 403, {"error": "forbidden"}
    return None


def _media_type_refusal(handler: _SetupHandler, port: int) -> _Refusal | None:
    """Refuse a write that is not declared as JSON."""
    if not secure.json_type_ok(handler.headers.get("Content-Type")):
        return 415, {"error": "unsupported media type"}
    return None


def _rate_refusal(handler: _SetupHandler, port: int) -> _Refusal | None:
    """Refuse a write once the client address is over the rate limit."""
    if not handler._limiter.allow(handler.client_address[0]):
        return 429, {"error": "too many requests"}
    return None


def _write_route_refusal(handler: _SetupHandler, port: int) -> _Refusal | None:
    """Refuse a write to a path with no POST route, before any body is read."""
    if handler.path not in _POST_ROUTES:
        return 404, {"error": "not found"}
    return None


# Ordered refusal checks. The first refusal is sent and the rest are not run.
# Every request: Host, then token (API paths only), then method.
_REQUEST_CHECKS: tuple[Callable[[_SetupHandler, int], _Refusal | None], ...] = (
    _host_refusal,
    _token_refusal,
    _method_refusal,
)
# POST only, after the request checks: Origin, media type, rate limit, route.
_WRITE_CHECKS: tuple[Callable[[_SetupHandler, int], _Refusal | None], ...] = (
    _origin_refusal,
    _media_type_refusal,
    _rate_refusal,
    _write_route_refusal,
)

# Path -> handler method name. Paths not listed answer 404.
_GET_ROUTES: dict[str, str] = {
    "/": "_get_page",
    "/setup/api/state": "_get_state",
    "/setup/api/progress": "_get_progress",
    "/setup/api/prefs": "_get_prefs",
}
_POST_ROUTES: dict[str, str] = {
    "/setup/api/device": "_post_device",
    "/setup/api/validate": "_post_validate",
    "/setup/api/apply": "_post_apply",
    "/setup/api/prefs": "_post_prefs",
    "/setup/api/prefs/skip": "_post_prefs_skip",
    "/setup/api/prefs/skip-all": "_post_prefs_skip_all",
    "/setup/api/prefs/finish": "_post_prefs_finish",
}


class _SetupHandler(BaseHTTPRequestHandler):
    """Shared setup wizard request handling; ``make_server`` binds the per-server values."""

    # Bound by the per-server subclass in make_server.
    timeout: float
    request_budget: float
    _slots: threading.Semaphore
    _ctx: SetupContext
    _token: str
    _limiter: secure.RateLimiter
    _progress_state: _ProgressState
    _bound: dict[str, int]
    _preference_path: Path

    # Connection budget and connection cap.

    def setup(self) -> None:
        """Enforce absolute connection read budget and cap concurrent connections."""
        if not type(self)._slots.acquire(blocking=False):
            self.request.close()
            raise ConnectionAbortedError("too many connections")
        try:
            super().setup()
            self._deadline = time.monotonic() + type(self).request_budget
            self._shrink_timeout()
            self._start_budget_timer()
        except Exception:
            self._undo_failed_setup()
            raise

    def _start_budget_timer(self) -> None:
        """Start the timer that shuts the socket when the request budget runs out."""

        def _expire() -> None:
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)

        self._budget_timer = threading.Timer(self._time_left(), _expire)
        self._budget_timer.daemon = True
        self._budget_timer.start()

    def _undo_failed_setup(self) -> None:
        """Cancel the timer, close the connection and release the slot after a failed setup."""
        with contextlib.suppress(Exception):
            if getattr(self, "_budget_timer", None):
                self._budget_timer.cancel()
        with contextlib.suppress(Exception):
            self.connection.close()
        type(self)._slots.release()

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

    # Responses.

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

    # Payloads.

    def _state(self) -> dict[str, Any]:
        """Assemble the wizard state payload."""
        ctx = self._ctx
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

            payload["ai_tools"] = public_state(
                env=ctx.env or {}, home=ctx.home, machine=_platform_mod.machine(), platform=ctx.platform
            )
        except Exception:
            payload["ai_tools"] = {
                "supported": False,
                "tools": [],
                "modes": ["lite", "full"],
                "default_mode": "lite",
                "error": "unavailable",
            }
        return payload

    def _preferences(self) -> dict[str, Any]:
        """Build the preference response from one read of the selected profile."""
        saved = prefs.load(self._preference_path)
        return {"state": saved, "resume": prefs.resume_step(saved), "effective": prefs.effective(saved)}

    def _progress(self) -> dict[str, Any]:
        """Assemble the apply progress payload."""
        state = self._progress_state
        with state.lock:
            progress = state.progress
        if progress is None:
            return {"running": False, "done": False, "ok": False, "steps": []}
        return progress.result()

    def _apply(self, cfg: Any) -> None:
        """Validate and start an apply run, or report why not."""
        if not isinstance(cfg, dict):
            self._json(400, {"ok": False, "errors": ["config must be an object"]})
            return
        errors = _config.validate(cfg)
        if errors:
            self._json(400, {"ok": False, "errors": list(errors)})
            return
        state = self._progress_state
        with state.lock:
            if state.progress is not None and not state.progress.done():
                self._json(409, {"error": "already running"})
                return
            progress = Progress()
            state.progress = progress
        ctx = self._ctx

        def run() -> None:
            """Run apply off the request thread."""
            _apply.apply(
                cfg,
                repo_root=ctx.repo_root,
                python=ctx.python,
                home=ctx.home,
                platform=ctx.platform,
                env=ctx.env,
                port=ctx.app_port,
                dry_run=ctx.dry_run,
                runner=ctx.runner,
                health_check=ctx.health_check,
                tools_runner=ctx.tools_runner,
                progress=progress,
            )

        threading.Thread(target=run, daemon=True).start()
        self._json(202, {"started": True})

    # Routing.

    def _refused(self, checks: tuple[Callable[[_SetupHandler, int], _Refusal | None], ...], port: int) -> bool:
        """Run ``checks`` in order and send the first refusal; return whether one was sent."""
        for check in checks:
            refusal = check(self, port)
            if refusal is not None:
                self._json(*refusal)
                return True
        return False

    def _read_body(self) -> dict[str, Any] | None:
        """Read the JSON body, or send the refusal and return None (bound in make_server)."""
        raise NotImplementedError

    def _dispatch(self) -> None:
        """Route one request after the shared security checks."""
        actual_port = self._bound["port"]
        if self._refused(_REQUEST_CHECKS, actual_port):
            return
        if self.command == "GET":
            name = _GET_ROUTES.get(self.path)
            if name is None:
                self._json(404, {"error": "not found"})
            else:
                getattr(self, name)()
            return
        if self._refused(_WRITE_CHECKS, actual_port):
            return
        body = self._read_body()
        if body is None:
            return
        getattr(self, _POST_ROUTES[self.path])(body)

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

    # GET routes.

    def _get_page(self) -> None:
        """Serve the wizard page with a fresh CSP nonce."""
        nonce = secrets.token_hex(16)
        self._html(200, page(self._token, nonce), nonce)

    def _get_state(self) -> None:
        """Serve the wizard state."""
        self._json(200, self._state())

    def _get_progress(self) -> None:
        """Serve the apply progress."""
        self._json(200, self._progress())

    def _get_prefs(self) -> None:
        """Serve the saved preferences."""
        self._json(200, self._preferences())

    # POST routes; ``body`` is the decoded JSON object.

    def _post_device(self, body: dict[str, Any]) -> None:
        """Choose a device profile."""
        profile_id = body.get("profile_id")
        chosen = _device.choose_profile(profile_id) if isinstance(profile_id, str) else None
        if chosen is None:
            self._json(400, {"error": "unknown profile"})
        else:
            self._json(200, chosen)

    def _post_validate(self, body: dict[str, Any]) -> None:
        """Validate a config without applying it."""
        cfg = body.get("config")
        errors = _config.validate(cfg) if isinstance(cfg, dict) else ["config must be an object"]
        self._json(200, {"ok": not errors, "errors": list(errors)})

    def _post_apply(self, body: dict[str, Any]) -> None:
        """Start an apply run."""
        self._apply(body.get("config"))

    def _post_prefs(self, body: dict[str, Any]) -> None:
        """Record one preference answer."""
        step = body.get("step")
        value = body.get("value")
        try:
            prefs.answer(step, value, path=self._preference_path)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, self._preferences())

    def _post_prefs_skip(self, body: dict[str, Any]) -> None:
        """Skip one preference step."""
        step = body.get("step")
        try:
            prefs.skip(step, path=self._preference_path)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {})

    def _post_prefs_skip_all(self, body: dict[str, Any]) -> None:
        """Skip every remaining preference step."""
        prefs.skip_all(path=self._preference_path)
        self._json(200, {})

    def _post_prefs_finish(self, body: dict[str, Any]) -> None:
        """Finish the preference steps."""
        prefs.finish(path=self._preference_path)
        self._json(200, self._preferences())


def make_server(port: int, ctx: SetupContext, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Build the setup server bound to a loopback address."""
    secure.check_bind(host)
    token = secure.new_token()
    limiter = secure.RateLimiter(20.0, 60)
    state = _ProgressState()
    bound: dict[str, int] = {"port": port}
    preference_path = _config.config_dir(ctx.env, ctx.platform) / "prefs.json"

    class Handler(_SetupHandler):
        """Handle setup wizard requests."""

        timeout = 10
        request_budget = 10.0
        _slots = threading.Semaphore(16)
        _ctx = ctx
        _token = token
        _limiter = limiter
        _progress_state = state
        _bound = bound
        _preference_path = preference_path

        def _read_body(self) -> dict[str, Any] | None:
            """Read the JSON body under the remaining budget, or send the body refusal."""
            try:
                self._shrink_timeout()
                return secure.read_json_body(self.rfile, self.headers)
            except secure.BodyError as exc:
                self._json(exc.status, {"error": exc.message})
                return None

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
    ctx = SetupContext(
        repo_root=Path(__file__).resolve().parents[3],
        python=sys.executable,
        home=Path.home(),
        platform=sys.platform,
        env=env,
        dry_run=args.dry_run,
    )
    server = make_server(args.port, ctx)
    url = f"http://127.0.0.1:{int(server.server_address[1])}/"
    print(f"Setup wizard: {url}")
    print("No browser on this device? Run: opos run --defaults")
    print(
        f"Or from your computer: ssh -L {int(server.server_address[1])}:127.0.0.1:{int(server.server_address[1])} <user>@<phone>, then open {url} there."
    )
    if not args.no_browser:
        webbrowser.open(url)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    finally:
        server.server_close()
    return 0
