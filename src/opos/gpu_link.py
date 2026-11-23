# SPDX-License-Identifier: GPL-3.0-or-later
"""Owner-mode GPU Link state, without sockets.

The last 64 finished and page-cancelled leases live until forget or restart,
oldest evicted first. Final retries match the remembered final post. Paused leases
are not reusable. A condition releases the sole state lock during long polls;
each accepted poll advances a generation and wakes the previous poll with 204.

One background thread hashes a regular model file in bounded chunks, without the
state lock. A cache binds the hash to path, size, nanosecond mtime and file identity.
Checking/error models yield no work and model requests get signed 404 errors.
Call close() to wake polls and join the hashing thread before releasing the store.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO

from opos import ai_answers as answers_api
from opos import gpu_link_proto as proto
from opos.store import DurabilityError, Store

HISTORY_LIMIT = 64
LINK_SECONDS = 2.5
HASH_RETRY_SECONDS = 5.0


@dataclass(frozen=True)
class Reply:
    """Immutable response; file is (path, byte start, byte length), if present."""

    status: int
    headers: tuple = ()
    body: bytes = b""
    file: tuple | None = None
    sent: Callable[[], None] | None = None
    failed: Callable[[], None] | None = None
    file_identity: dict | None = None
    file_error: Reply | None = None


class ModelChangedError(OSError):
    """The advertised model changed; stop sending this response."""


def _error(status: int, code: str) -> Reply:
    return Reply(status, (), proto.error_reply(code))


def _identity(path: Path) -> dict:
    return _file_identity(path, path.stat())


def _file_identity(path: Path, info: os.stat_result) -> dict:
    if not stat.S_ISREG(info.st_mode):
        raise OSError("model")
    return {
        "path": str(path),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "device": info.st_dev,
        "inode": info.st_ino,
    }


class LinkService:
    """Serialize link state before entering the answer store's own lock.

    Authenticated failures still consume their nonce. Storage failures restore
    lease and rate state; speed metadata writes are best effort.
    Page mutations may raise OSError so their caller can report failed storage.
    The listener reports complete writes through each work reply's callbacks.
    """

    def __init__(
        self,
        answers: answers_api.AnswerStore,
        store: Store | None = None,
        model_path: str | Path | None = None,
        label: str = "OPOS phone",
        clock: Callable[[], float] = time.monotonic,
        rand: Callable[[int], bytes] = os.urandom,
    ) -> None:
        if not proto.valid_label(label):
            raise ValueError("label")
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._answers = answers
        self._store = store if store is not None else Store()
        self._clock, self._rand, self._label = clock, rand, label
        self._epoch = rand(16)
        self._nonces: set[bytes] = set()
        self._buckets: dict[str, tuple[float, float]] = {}
        self._pending: tuple[str, bytes, float] | None = None
        self._denials = 0
        self._pc: tuple[str, bytes] | None = None
        self._pairing_sweep_pending = True
        try:
            self._store.sweep_temps("gpu-link-pc.json")
        except OSError:
            pass
        else:
            self._pairing_sweep_pending = False
        saved, _ = self._store.read_json("gpu-link-pc.json", {})
        key = proto.unhex(saved.get("key"), 32)
        if key is not None and proto.valid_label(saved.get("label")):
            self._pc = (saved["label"], key)
        self._status: dict | None = None
        self._last_status: float | None = None
        self._lease: dict | None = None
        self._ending_answer: str | None = None
        self._storage_fault: str | None = None
        self._speed: dict | None = None
        self._history: OrderedDict[bytes, tuple[str, dict | None, int | None, bytes]] = OrderedDict()
        self._poll = 0
        self._closed = False
        self._model_path = Path(model_path).absolute() if model_path else None
        name = self._model_path.stem if self._model_path else ""
        self._model = {"state": "none", "name": self._display_name(name), "size": None}
        self._model_id: dict | None = None
        self._model_hash: str | None = None
        self._hasher: threading.Thread | None = None
        self._hash_failure: tuple[dict, float] | None = None
        self._hash_stop = threading.Event()
        with self._lock:
            self._check_model()

    @staticmethod
    def _display_name(name: str) -> str:
        return "".join(c for c in name if " " <= c <= "~")[:64]

    def set_model_name(self, name: str) -> None:
        """Set the printable display name supplied by the listener's environment."""
        with self._lock:
            self._model["name"] = self._display_name(name)

    def close(self) -> None:
        """Pause running work, end waiting polls and join the background reader."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._hash_stop.set()
                try:
                    if self._lease is not None:
                        self._pause()
                    else:
                        answer = self._answers.current("gpu")
                        if answer is not None and answer["state"] in ("loading", "answering"):
                            self._answers.pause(answer["id"])
                except (OSError, answers_api.AnswerError):
                    # A later service reconciles running work if storage is unavailable.
                    pass
            self._changed.notify_all()
            thread = self._hasher
        if thread is not None and thread.ident is not None:
            thread.join()

    def open_model(self, path: Path, target: str, identity: dict | None = None) -> BinaryIO:
        """Open only the still-hashed file before the listener sends any headers.

        The caller owns the returned stream. A changed file aborts the response;
        the peer must still hash the completed download, since an open regular
        file can be modified during transfer by its local owner.
        """
        with self._lock:
            if (
                self._closed
                or path != self._model_path
                or self._model["state"] != "ready"
                or target != f"/v1/models/{self._model_hash}"
            ):
                raise ModelChangedError("model changed")
            stream = path.open("rb")
            try:
                self.check_model_stream(stream, identity or self._model_id)
                return stream
            except OSError:
                stream.close()
                raise

    def check_model_stream(self, stream: BinaryIO, identity: dict) -> None:
        """Compare the open file with this reply's hashed identity, not a newer hash."""
        if _file_identity(self._model_path, os.fstat(stream.fileno())) != identity:
            with self._lock:
                self._model["state"] = "checking"
            raise ModelChangedError("model changed")

    @staticmethod
    def unsigned(method: str, target: str) -> bool:
        """Return whether this exact route accepts requests without a PC key."""
        return (method, target) in (("GET", "/v1/hello"), ("POST", "/v1/enrol"))

    @staticmethod
    def permits_range(method: str, target: str) -> bool:
        """Return whether this route accepts a model byte-range header."""
        try:
            return proto.parse_target(method, target)[0] == "model"
        except proto.ProtoError:
            return False

    def body_limit(self, method: str, target: str) -> int:
        """Return a raw body bound, zero for GET and unrecognised routes."""
        try:
            route = proto.parse_target(method, target)[0]
        except proto.ProtoError:
            return 0
        return {"enrol": 1024, "status": 2048, "text": 8192}.get(route, 0)

    def _token(self, name: str, rate: int) -> bool:
        now = self._clock()
        left, last = self._buckets.get(name, (float(rate), now))
        left = min(float(rate), left + max(0, now - last) * rate)
        allowed = left >= 1
        self._buckets[name] = (left - int(allowed), now)
        return allowed

    def _authenticate(self, method: str, target: str, headers: Any, body: bytes) -> tuple | None:
        ident = proto.unhex(headers.get("X-Link-Pc"), 8)
        nonce = proto.unhex(headers.get("X-Link-Nonce"), 12)
        mac = headers.get("X-Link-Mac")
        if ident is None or nonce is None or proto.unhex(mac, 32) is None or self._pc is None:
            return None
        key = self._pc[1]
        if ident.hex() != proto.pc_id(key):
            return None
        try:
            expected = proto.request_mac(key, self._epoch, method, target, headers.get("Range", ""), nonce, body)
        except proto.ProtoError:
            return None
        if not proto.same_mac(mac, expected) or nonce in self._nonces:
            return None
        if len(self._nonces) >= proto.NONCES_PER_PC:
            self._epoch = self._rand(16)
            self._nonces.clear()
            return None
        self._nonces.add(nonce)
        return key, nonce

    @staticmethod
    def _signed(reply: Reply, auth: tuple) -> Reply:
        content_range = dict(reply.headers).get("Content-Range", "")
        mac = proto.reply_mac(*auth, reply.status, content_range, None if reply.file else reply.body)
        return replace(
            reply,
            headers=reply.headers + (("X-Link-Mac", mac),),
            file_error=LinkService._signed(reply.file_error, auth) if reply.file_error else None,
        )

    def handle(self, method: str, target: str, headers: Any, body: bytes) -> Reply:
        """Validate a bounded request, atomically authenticate it, and return a reply.

        Oversize input is refused before hashing, so it has not been authenticated.
        Framing failures on signed targets are empty unsigned 401 responses.
        """
        with self._changed:
            unsigned = self.unsigned(method, target)
            if not isinstance(body, bytes):
                return _error(400, "request") if unsigned else Reply(401)
            if len(body) > self.body_limit(method, target):
                return _error(413, "too_long") if unsigned else Reply(401)
            if headers.get("Range") is not None and not self.permits_range(method, target):
                return _error(400, "request") if unsigned else Reply(401)
            if not unsigned and (
                headers.get("Transfer-Encoding") is not None
                or (method == "POST" and headers.get("Content-Length") is None)
            ):
                return Reply(401)
            auth = None if unsigned else self._authenticate(method, target, headers, body)
            if not unsigned and auth is None:
                return Reply(401)
            buckets = self._buckets.copy()
            try:
                route = proto.parse_target(method, target)
                if self._closed:
                    reply = _error(400, "state")
                else:
                    reply = self._dispatch(route, headers, body)
            except proto.ProtoError as err:
                reply = _error(413 if err.code == "too_long" else 400, err.code)
            except OSError:
                if auth is not None:
                    try:
                        answer = self._answers.current("gpu") if route[0] in ("work", "text", "status") else None
                    except OSError:
                        answer = None
                    self._storage_fault = answer["id"] if answer is not None else ""
                # Work can release the lock while waiting. Never roll back tokens
                # consumed by another request during that wait.
                if method == "POST" and target.startswith("/v1/runs/"):
                    self._buckets = buckets
                reply = _error(500, "state")
            except answers_api.StateError:
                reply = _error(404, "lease")
            except Exception:
                reply = _error(400, "request")
            return self._signed(reply, auth) if auth is not None else reply

    def _dispatch(self, route: tuple, headers: Any, body: bytes) -> Reply:
        kind = route[0]
        if kind == "hello":
            return (
                Reply(200, (), proto.hello_body(self._label, self._epoch))
                if self._token("hello", 10)
                else _error(429, "rate")
            )
        if kind == "enrol":
            return self._enrol(body)
        if kind == "status":
            return self._record_status(proto.parse_status(body))
        if kind == "work":
            return self._work(route[1])
        if kind == "text":
            return self._text(route[1], body)
        return self._model_reply(route[1], headers.get("Range"))

    def _expire(self) -> None:
        if self._pending is not None and self._clock() - self._pending[2] >= 60:
            self._pending = None
            self._denials += 1

    def _enrol(self, body: bytes) -> Reply:
        if not self._token("enrol", 2):
            return _error(429, "rate")
        label, key = proto.parse_enrol(body)
        self._expire()
        if self._pc is not None:
            status, state = 409, "enrolled"
        elif self._denials >= 3:
            status, state = 403, "closed"
        elif self._pending is not None and (
            not hmac.compare_digest(self._pending[1], key) or self._pending[0] != label
        ):
            status, state = 409, "busy"
        else:
            if self._pending is None:
                self._pending = (label, key, self._clock())
            status, state = 202, "pending"
        return Reply(status, (), proto.dumps({"state": state}))

    def _matches_pending(self, code: object) -> bool:
        """Check the displayed code under the state lock without changing state."""
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            return False
        if self._pending is None:
            return False
        label, key, since = self._pending
        return hmac.compare_digest(code, proto.code(label, key)) and self._clock() - since < 60

    def allow(self, code: object) -> bool:
        """Persist only the matching pending PC in one atomic mode-0600 write."""
        with self._changed:
            if not self._matches_pending(code):
                return False
            label, key, _ = self._pending
            if self._pairing_sweep_pending:
                self._store.sweep_temps("gpu-link-pc.json")
                self._pairing_sweep_pending = False
            if self._ending_answer is not None or self._pc is None or self._pc[1] != key:
                self._end_pc_answer()
            self._store.write_json("gpu-link-pc.json", {"label": label, "key": key.hex()}, durable=True)
            if self._pc is not None and self._pc[1] != key:
                self._poll += 1
            self._epoch = self._rand(16)
            self._nonces.clear()
            self._pc = label, key
            self._pending = None
            self._changed.notify_all()
            return True

    def deny(self, code: object) -> bool:
        """Discard only the matching pending enrolment and count a refusal."""
        with self._lock:
            if not self._matches_pending(code):
                return False
            self._pending = None
            self._denials += 1
            return True

    def reopen(self) -> None:
        """Clear the three-refusal gate without extending a pending request."""
        with self._lock:
            self._denials = 0

    def forget(self) -> bool:
        """End handed-out work and remove its PC, retaining failed endings for retry."""
        with self._changed:
            self._pairing_sweep_pending = True
            self._store.sweep_temps("gpu-link-pc.json")
            self._pairing_sweep_pending = False
            if self._pc is None:
                return False
            try:
                self._end_pc_answer()
            except DurabilityError:
                raise
            except Exception:
                # Revoke the PC now; tick retries cancellation before any new offer.
                if self._lease is not None:
                    self._lease["failed"] = True
            self._store.write_json("gpu-link-pc.json", {}, durable=True)
            self._pc = self._status = self._last_status = None
            self._history.clear()
            self._epoch = self._rand(16)
            self._nonces.clear()
            self._buckets.pop("text", None)
            self._poll += 1
            self._changed.notify_all()
            return True

    def _end_pc_answer(self) -> None:
        """Cancel work seen by a PC, keeping untouched queued questions available."""
        answer = (
            self._answers.get(self._ending_answer) if self._ending_answer is not None else self._answers.current("gpu")
        )
        if answer is not None and answer["state"] not in answers_api.FINAL:
            if answer["state"] in ("paused", "loading", "answering") or answer["text"] or answer["where"]:
                self._ending_answer = answer["id"]
                self.cancel(answer["id"])
        self._lease = None
        self._ending_answer = None

    def _remember(self, lease: bytes, state: str, post: dict | None = None, acked: int | None = None) -> None:
        if self._pc is None:
            return
        self._history[lease] = (state, post, acked, hashlib.sha256(self._pc[1]).digest())
        while len(self._history) > HISTORY_LIMIT:
            self._history.popitem(last=False)

    def cancel(self, answer_id: str) -> dict:
        """Drop this answer's lease before cancelling; restore it on write failure."""
        with self._changed:
            old = self._lease
            matching = old is not None and old["answer"] == answer_id
            if matching:
                self._lease = None
            try:
                answer = self._answers.cancel(answer_id)
                self._clear_storage_fault(answer_id)
            except Exception:
                self._storage_fault = answer_id
                self._lease = old
                raise
            if matching:
                self._remember(old["id"], "cancelled")
            self._changed.notify_all()
            return answer

    def _pause(self) -> None:
        old, self._lease = self._lease, None
        if old is not None:
            try:
                self._answers.pause(old["answer"])
                self._clear_storage_fault(old["answer"])
            except answers_api.StateError:
                # The answer already left loading/answering outside this service.
                pass
            except Exception:
                self._storage_fault = old["answer"]
                self._lease = old
                raise
            self._changed.notify_all()

    def _record_status(self, status: dict) -> Reply:
        live = self._lease if self._lease is None or not self._lease["failed"] else None
        misses = 0
        if live is not None:
            if status["lease"] == live["id"]:
                if status["state"] == "error":
                    self._lease = None
                    try:
                        self._answers.fail(live["answer"], status["error"])
                        self._clear_storage_fault(live["answer"])
                    except (OSError, answers_api.AnswerError):
                        self._lease = live
                        raise
                values = {key: status[key] for key in ("tokens", "tps") if status[key] is not None}
                if values:
                    if self._speed is None or self._speed["answer"] != live["answer"]:
                        self._speed = {"answer": live["answer"], "values": {}, "noted": None}
                    values.update(model=self._model["name"], where=self._pc[0])
                    self._speed["values"].update(values)
                    self._note_speed(live["answer"])
            elif live["sent"] is not None:
                misses = live["misses"] + 1
                if misses >= 2:
                    self._pause()
        if self._lease is not None:
            self._lease["misses"] = misses
        self._status = status
        self._last_status = self._clock()
        history = self._history.get(status["lease"])
        if history is not None and history[3] != hashlib.sha256(self._pc[1]).digest():
            history = None
        if status["lease"] is not None and history is None and (live is None or status["lease"] != live["id"]):
            return _error(404, "lease")
        cancel = status["lease"] if history is not None and history[0] == "cancelled" else None
        return Reply(200, (), proto.status_reply(cancel))

    def _note_speed(self, answer_id: str, final: bool = False) -> None:
        speed = self._speed
        if speed is None or speed["answer"] != answer_id:
            return
        now = self._clock()
        if not final and speed["noted"] is not None and now - speed["noted"] < 1:
            return
        try:
            answer = self._answers.get(answer_id)
            if answer is None or all(answer.get(key) == value for key, value in speed["values"].items()):
                return
            self._answers.note(answer_id, **speed["values"])
        except (OSError, answers_api.AnswerError):
            # Display metadata is best effort; retain it for the next attempt.
            return
        if any(key in speed["values"] for key in ("tokens", "tps")):
            speed["noted"] = now

    def _work(self, wait: int) -> Reply:
        if self._answers.startup_notice:
            raise OSError(self._answers.startup_notice)
        self._poll += 1
        generation = self._poll
        self._changed.notify_all()
        deadline = time.monotonic() + wait
        logical_deadline = self._clock() + wait
        while not self._closed and generation == self._poll and self._pc is not None:
            self._check_model()
            if self._model["state"] == "ready":
                reply = self._offer()
                if reply is not None:
                    return reply
            remaining = min(deadline - time.monotonic(), logical_deadline - self._clock())
            if remaining <= 0:
                break
            self._changed.wait(min(0.1, remaining))
        return Reply(204)

    def _offer(self) -> Reply | None:
        if self._ending_answer is not None:
            self._end_pc_answer()
        old = self._lease
        if old is not None:
            answer = self._answers.get(old["answer"])
            if answer is None or answer["state"] not in ("loading", "answering") or old["model"] != self._model_hash:
                return _error(400, "request")
        else:
            answer = self._answers.current("gpu")
            if answer is None:
                return None
            if answer["state"] in ("loading", "answering"):
                answer = self._answers.pause(answer["id"])
                self._clear_storage_fault(answer["id"])
            if answer["state"] not in ("waiting", "paused"):
                return _error(400, "request")
        lease = self._rand(16)
        body = proto.work_reply(lease, self._model_hash, answer["question"], answer["text"])
        if old is None:
            self._answers.begin(answer["id"])
            self._clear_storage_fault(answer["id"])
        self._lease = {
            "id": lease,
            "answer": answer["id"],
            "sent": None,
            "failed": False,
            "misses": 0,
            "model": self._model_hash,
        }
        if self._speed is None or self._speed["answer"] != answer["id"]:
            self._speed = {"answer": answer["id"], "values": {}, "noted": None}
        self._speed["values"].update(model=self._model["name"], where=self._pc[0])
        self._note_speed(answer["id"])
        return self._work_reply(body)

    def _work_reply(self, body: bytes) -> Reply:
        live = self._lease
        completed = False

        def delivered(success: bool) -> None:
            nonlocal completed
            with self._changed:
                if completed or self._lease is not live:
                    return
                completed = True
                if success:
                    live.update(sent=self._clock(), misses=0)
                else:
                    live["failed"] = True
                    try:
                        self._pause()
                    except (OSError, answers_api.StateError):
                        # A failed durable pause is retried by tick, even unsent.
                        return

        return Reply(200, (), body, sent=lambda: delivered(True), failed=lambda: delivered(False))

    def _text(self, lease: bytes, body: bytes) -> Reply:
        if not self._token("text", 4):
            return _error(429, "rate")
        post = proto.parse_text(body)
        live = self._lease if self._lease is None or not self._lease["failed"] else None
        history = self._history.get(lease)
        if history is not None and history[3] != hashlib.sha256(self._pc[1]).digest():
            history = None
        if live is not None and live["id"] == lease:
            answer_id = live["answer"]
        elif history is not None and history[0] == "done" and post == history[1]:
            return Reply(200, (), proto.text_reply(history[2]))
        else:
            return _error(404, "lease")
        if post["done"] and live is not None and live["id"] == lease:
            self._lease = None
        stored_bytes = self._answers.get(answer_id)["bytes"]
        try:
            answer = self._answers.append(answer_id, **post)
        except (OSError, answers_api.AnswerError) as err:
            self._lease = live
            if isinstance(err, answers_api.OffsetError):
                return Reply(409, (), proto.stored_reply(err.stored))
            if isinstance(err, answers_api.TooLongError):
                return Reply(413, (), proto.stored_reply(err.stored))
            if isinstance(err, answers_api.TextError):
                return _error(400, "text")
            raise
        # Retry metadata without requiring optional speed fields from the PC.
        self._note_speed(answer_id, final=post["done"])
        if answer["bytes"] > stored_bytes or answer["state"] in answers_api.FINAL:
            self._clear_storage_fault(answer_id)
        if post["done"]:
            self._remember(lease, "done", post, answer["bytes"])
        self._changed.notify_all()
        return Reply(200, (), proto.text_reply(answer["bytes"]))

    def tick(self) -> None:
        """Expire enrolment and pause silent work; storage failures retry next tick."""
        with self._changed:
            if self._closed:
                return
            self._expire()
            if self._ending_answer is not None:
                try:
                    self._end_pc_answer()
                except OSError:
                    self._storage_fault = self._ending_answer or ""
                    return
            if self._lease is not None:
                last = self._lease["sent"]
                if last is not None and self._last_status is not None:
                    last = max(last, self._last_status)
                # A model file that changed after the hand-out ends the work: the PC drops the lease and asks again.
                stale = self._model_hash is not None and self._lease["model"] != self._model_hash
                if self._lease["failed"] or stale or (last is not None and self._clock() - last >= LINK_SECONDS):
                    try:
                        self._pause()
                    except OSError:
                        return
                    except answers_api.StateError:
                        # Keep the lease until the pause can be made durable.
                        return
            else:
                answer = None
                try:
                    answer = self._answers.current("gpu")
                    if answer is not None and answer["state"] in ("loading", "answering"):
                        self._answers.pause(answer["id"])
                        self._clear_storage_fault(answer["id"])
                except OSError:
                    self._storage_fault = answer["id"] if answer is not None else ""
                    return
                except answers_api.StateError:
                    # Retry reconciliation on the next tick, even without a PC.
                    return
            try:
                self._check_model()
            except OSError:
                # A cache read failed. Retry on the next tick without publishing it.
                return

    def _clear_storage_fault(self, answer_id: str) -> None:
        if self._storage_fault == answer_id:
            self._storage_fault = None

    def snapshot(self) -> dict:
        """Return copies of display-only data, never keys, nonces or leases."""
        with self._lock:
            if self._storage_fault:
                answer = self._answers.get(self._storage_fault)
                if answer is None or answer["state"] in answers_api.FINAL:
                    self._storage_fault = None
            pending = None
            if self._pending is not None:
                label, key, since = self._pending
                pending = {
                    "label": label,
                    "code": proto.code(label, key),
                    "seconds": max(0, math.ceil(60 - (self._clock() - since))),
                }
            pc = None
            if self._pc is not None:
                status = self._status or {}
                pc = {name: deepcopy(status.get(name)) for name in ("state", "gpu", "copied", "total", "error")}
                pc.update(
                    label=self._pc[0],
                    live=self._last_status is not None and self._clock() - self._last_status < LINK_SECONDS,
                )
            return {
                "storage": "error" if self._storage_fault is not None or self._answers.startup_notice else None,
                "storage_notice": self._answers.startup_notice,
                "model": self._model.copy(),
                "pc": pc,
                "pending": pending,
                "enrol": "closed" if self._denials >= 3 else "open",
            }

    def _check_model(self) -> None:
        if self._model_path is None or self._closed:
            return
        try:
            identity = _identity(self._model_path)
        except OSError:
            self._model["state"] = "error"
            return
        if self._model_id == identity and self._model["state"] == "ready":
            return
        if self._hasher is not None and self._hasher.is_alive():
            self._model.update(state="checking", size=identity["size"])
            return
        if self._hash_failure is not None:
            failed_identity, retry_at = self._hash_failure
            if failed_identity == identity and self._clock() < retry_at:
                self._model["state"] = "error"
                return
        # A Store failure propagates to handle as signed 500 before state changes.
        cached, _ = self._store.read_json("gpu-link-model.json", {})
        self._model.update(state="checking", size=identity["size"])
        if cached.get("identity") == identity and proto.unhex(cached.get("sha256"), 32) is not None:
            self._model_id, self._model_hash = identity, cached["sha256"]
            self._model["state"] = "ready"
            return
        thread = threading.Thread(target=self._hash_model, args=(identity,), name="gpu-link-hash")
        thread.start()
        self._hasher = thread

    def _hash_model(self, identity: dict) -> None:
        try:
            digest = hashlib.sha256()
            with self._model_path.open("rb") as stream:
                while not self._hash_stop.is_set():
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            with self._changed:
                if self._closed or _identity(self._model_path) != identity:
                    return
                value = digest.hexdigest()
                self._model_id, self._model_hash = identity, value
                self._hash_failure = None
                self._model.update(state="ready", size=identity["size"])
                self._changed.notify_all()
                try:
                    self._store.write_json("gpu-link-model.json", {"identity": identity, "sha256": value})
                except OSError:
                    # Readiness depends on the digest; the restart cache is best effort.
                    pass
        except OSError:
            with self._lock:
                self._model["state"] = "error"
                self._hash_failure = identity, self._clock() + HASH_RETRY_SECONDS

    def _model_reply(self, value: str, range_value: str | None) -> Reply:
        self._check_model()
        if self._model["state"] != "ready" or value != self._model_hash:
            return _error(404, "model")
        size = self._model["size"]
        if range_value is None:
            return Reply(
                200,
                (),
                file=(self._model_path, 0, size),
                file_identity=self._model_id.copy(),
                file_error=_error(500, "state"),
            )
        try:
            start, end = proto.parse_range(range_value, size)
        except proto.ProtoError:
            return _error(416, "range")
        return Reply(
            206,
            (("Content-Range", proto.content_range(start, end, size)),),
            file=(self._model_path, start, end - start + 1),
            file_identity=self._model_id.copy(),
            file_error=_error(500, "state"),
        )
