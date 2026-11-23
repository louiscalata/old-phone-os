# SPDX-License-Identifier: GPL-3.0-or-later
"""Local-only fixtures for the AI page unit and optional browser checks."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import closing, contextmanager
from copy import deepcopy
from typing import Any

from opos import muse, web

ROUTE = muse.Route("local", "Small local model", "on this phone", "http://127.0.0.1:8080/v1", "local-test")
LINK = {
    "model": {"state": "ready", "name": "Large model", "size": 100},
    "pc": None,
    "pending": None,
    "enrol": "open",
}
PC = {
    "label": "Demo PC",
    "live": True,
    "state": "ready",
    "gpu": {"name": "Demo GPU", "memory_mb": 8192, "driver": "test"},
    "copied": None,
    "total": None,
    "error": None,
}
LITERAL_ANSWER = "<script>literal</script><b>text</b>"
LONG_ANSWER = (LITERAL_ANSWER + "\n" + "A long answer line.\n" * 650)[:12000]


class FakeLink:
    """Implement just the public page-to-service contract."""

    def __init__(self, answers: Any) -> None:
        self.answers = answers
        self.value = deepcopy(LINK)
        self.calls: list = []

    def snapshot(self) -> dict:
        """Return an independent link view."""
        return deepcopy(self.value)

    def allow(self, code: Any) -> bool:
        """Approve only the request whose code was shown."""
        return self._answer("allow", code)

    def deny(self, code: Any) -> bool:
        """Refuse only the request whose code was shown."""
        return self._answer("deny", code)

    def _answer(self, action: str, code: Any) -> bool:
        self.calls.append((action, code))
        pending = self.value["pending"]
        if not (
            isinstance(code, str)
            and len(code) == 6
            and code.isascii()
            and code.isdigit()
            and pending
            and code == pending["code"]
        ):
            return False
        if action == "allow":
            self.value["pc"] = {**deepcopy(PC), "label": pending["label"]}
        self.value["pending"] = None
        return True

    def reopen(self) -> None:
        """Record reopening enrolment."""
        self.calls.append("reopen")

    def forget(self) -> bool:
        """Record forgetting the PC."""
        self.calls.append("forget")
        return True

    def cancel(self, ident: str) -> dict:
        """Cancel through the store, as the real service does after dropping its lease."""
        self.calls.append(("cancel", ident))
        return self.answers.cancel(ident)


class FakeStream:
    """Yield controlled pieces and report closing without opening a model socket."""

    def __init__(self, pieces: Any = (), error: Exception | None = None) -> None:
        self.route = ROUTE
        self.finish = "stop"
        self.tokens = 17
        self.tps = 12.5
        self.pieces = pieces
        self.error = error
        self.closed = False
        self.closed_thread: int | None = None

    def __enter__(self) -> FakeStream:
        """Own the fixture for one worker."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Close even when the worker fails or stops."""
        self.close()

    def __iter__(self) -> Any:
        """Yield pieces and then the optional error."""
        yield from self.pieces
        if self.error:
            raise self.error

    def close(self) -> None:
        """Record the worker thread that closed the fixture."""
        self.closed = True
        self.closed_thread = threading.get_ident()


@contextmanager
def running() -> Any:
    """Serve a test-owned phone on loopback and join it on exit."""
    server = web.make_server(0, web.Phone())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
        if thread.is_alive():
            raise RuntimeError("test phone did not stop")


def request(server: Any, path: str, body: dict | None = None, token: bool = True) -> tuple[int, bytes]:
    """Read one test request and close its socket."""
    headers = {"X-OPOS-Token": server.RequestHandlerClass.token} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    with closing(http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)) as conn:
        conn.request("GET" if body is None else "POST", path, None if body is None else json.dumps(body), headers)
        response = conn.getresponse()
        return response.status, response.read()
