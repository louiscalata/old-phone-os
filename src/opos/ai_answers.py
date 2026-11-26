# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable plain-text answers, with one record per file and a start-up scan."""

from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
import uuid
from copy import deepcopy
from typing import Any

from opos.store import Store

PANES = ("phone", "gpu")
STATES = ("queued", "waiting", "loading", "answering", "paused", "done", "failed", "cancelled")
OPEN = ("waiting", "loading", "answering", "paused")
FINAL = ("done", "failed", "cancelled")
MAX_QUESTION_BYTES = 4096
MAX_ANSWER_BYTES = 12288
MAX_ANSWERS = 50
_ID = re.compile(r"[0-9a-f]{12}")
_ERROR = re.compile(r"[a-z0-9_-]{1,32}")


class AnswerError(Exception):
    """Base error for an answer operation."""


class StateError(AnswerError):
    """The answer does not allow this operation."""


class OffsetError(AnswerError):
    """A post disagrees with the stored byte position; ``stored`` gives it."""

    def __init__(self, stored: int) -> None:
        self.stored = stored
        super().__init__(f"stored: {stored}")


class TextError(AnswerError):
    """Text is empty where forbidden, is not bytes, or breaks the text rule."""


class TooLongError(AnswerError):
    """The byte limit would be exceeded; ``stored`` gives the prior length."""

    def __init__(self, stored: int) -> None:
        self.stored = stored
        super().__init__(f"stored: {stored}")


class FullError(AnswerError):
    """All answer slots are occupied by unfinished answers."""


def text_ok(data: bytes) -> bool:
    """Accept strict UTF-8 with no Cc characters except newline and tab."""
    if not isinstance(data, bytes):
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(unicodedata.category(char) != "Cc" or char in "\n\t" for char in text)


def clean(text: str) -> str:
    """Drop refused characters, including surrogates; CRLF becomes LF."""
    return "".join(char for char in text if text_ok(char.encode("utf-8", errors="surrogatepass")))


def _whole(value: Any) -> bool:
    return type(value) is int and value >= 0


def _label(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _speed(value: Any) -> bool:
    return type(value) in (int, float) and 0 <= value <= 100_000 and math.isfinite(value)


def _question(value: Any) -> bytes:
    if not isinstance(value, str):
        raise TextError("question")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as err:
        raise TextError("question") from err
    if not raw or not text_ok(raw):
        raise TextError("question")
    if len(raw) > MAX_QUESTION_BYTES:
        raise TooLongError(0)
    return raw


def _name(ident: str) -> str:
    return f"ai-answer-{ident}.json"


class AnswerStore:
    """Serialize readers and writers with one lock and one file per answer.

    Returned answers are copies. Waiting is derived under the lock, never
    stored: the oldest queued answer waits only when its pane has no loading,
    answering or paused answer. Revisions rise once per logical change. An
    answer's rev is the store revision when its own file last changed, so a
    derived promotion does not change that answer's rev. Each file holds an
    answer, its final post offset, and its creation sequence number. Start-up
    scans valid records in sequence order and normalises them in one change.
    ``rev`` never goes down while this object lives. After a restart it is at
    least the highest revision of any valid answer that could be read. Readers
    that must never go back in time should also compare a value that changes
    with every start.
    Start-up keeps unreadable answer files and reports a notice. Store may move
    corrupt files aside. Either notice blocks new GPU questions until a fresh scan;
    readable open GPU answers can still resume. Phone-only questions remain available.

    Each changing method first writes its answer, then replaces memory and
    retries pending recovery writes. A failed primary write leaves memory
    unchanged and raises: readable replacement bytes do not prove a directory sync. Start-up may
    write several changed answers once each; if a write
    fails, memory keeps the recovery and later successful saves retry it.
    The next scan also recomputes recovery. Each write uses Store's flush/fsync/replace pattern.

    Asking accepts the new answer before dropping old final answers from memory.
    Failed file or lock removals are retried at every ask and cannot fail it.
    Dropped answers are never returned again by this instance. If a file remains
    at restart, the scan loads it as an old finished answer and the next ask
    drops it again. Start-up does not trim the history. If an oversized scanned
    history runs out of final answers during tidying, unfinished answers stay;
    the next ask raises FullError until an answer finishes.
    Invalid pane or metadata values raise ValueError; invalid questions raise TextError or
    TooLongError. Unknown ids and forbidden transitions raise StateError.
    """

    def __init__(self, store: Store | None = None) -> None:
        self._lock = threading.RLock()
        self._store = store if store is not None else Store()
        self._rev = 0
        self._records: dict[str, dict] = {}
        self._removals: set[str] = set()
        self._recoveries: set[str] = set()
        self._sweep_pending = False
        self._startup_notice = ""
        with self._lock:
            self._load()

    @property
    def startup_notice(self) -> str:
        """A startup read fault or recovery warning, retained until reconstruction."""
        return self._startup_notice

    @property
    def rev(self) -> int:
        """Return the committed store revision under the store lock."""
        with self._lock:
            return self._rev

    def _sweep_temps(self) -> None:
        for temporary in self._store.root.glob(".ai-answer-*.json.*.tmp"):
            name = temporary.name[1:].split(".json.", 1)[0] + ".json"
            if _ID.fullmatch(name[len("ai-answer-") : -len(".json")]):
                self._store.sweep_temps(name)

    def _retry_sweep(self) -> None:
        try:
            self._sweep_temps()
        except OSError:
            self._sweep_pending = True
        else:
            self._sweep_pending = False

    def _read_record(self, name: str, ident: str) -> dict | None:
        try:
            record, notice = self._store.read_json(
                name, None, strict=True, validate=lambda value: self._valid_record(value, ident)
            )
        except OSError:
            self._startup_notice = "The phone could not read a saved answer. Restore access and restart AI mode."
            return None
        if notice:
            self._startup_notice = self._startup_notice or (
                "A saved answer was damaged and moved aside. Restart AI mode to continue."
            )
        if record is None:
            return None
        return record

    def _load(self) -> None:
        self._retry_sweep()
        loaded = []
        for path in self._store.root.glob("ai-answer-*.json"):
            ident = path.name[len("ai-answer-") : -len(".json")]
            if not _ID.fullmatch(ident):
                continue
            record = self._read_record(path.name, ident)
            if record is not None:
                if record["answer"]["state"] == "waiting":
                    record["answer"]["state"] = "queued"
                loaded.append(record)
        loaded.sort(key=lambda record: (record["seq"], record["answer"]["id"]))
        self._records = {record["answer"]["id"]: record for record in loaded}
        self._rev = max((record["answer"]["rev"] for record in loaded), default=0)
        recovered = self._records.copy()
        for ident, record in self._records.items():
            answer = record["answer"]
            if answer["state"] in ("loading", "answering") or (
                answer["pane"] == "phone" and answer["state"] == "queued"
            ):
                recovered[ident] = record = deepcopy(record)
                answer = record["answer"]
                answer["state"] = "paused" if answer["pane"] == "gpu" else "failed"
                if answer["pane"] == "phone":
                    answer["error"] = "restart"
        for pane in PANES:
            opened = False
            for ident, record in recovered.items():
                answer = record["answer"]
                if answer["pane"] == pane and answer["state"] in ("loading", "answering", "paused"):
                    if opened:
                        recovered[ident] = record = deepcopy(record)
                        record["answer"]["state"] = "queued"
                    opened = True
        if recovered != self._records:
            revision = self._rev + 1
            for ident, record in recovered.items():
                if record != self._records[ident]:
                    record["answer"]["rev"] = revision
                    self._recoveries.add(ident)
            self._records = recovered
            self._rev = revision
            self._retry_recoveries()

    def _retry_recoveries(self) -> None:
        for ident, record in self._records.items():
            if ident in self._recoveries:
                try:
                    self._store.write_json(_name(ident), record, durable=True)
                except OSError:
                    continue
                self._recoveries.discard(ident)
                if self._sweep_pending:
                    self._retry_sweep()
        self._recoveries.intersection_update(self._records)

    def _valid_record(self, record: Any, ident: str) -> bool:
        if not isinstance(record, dict) or not isinstance(record.get("answer"), dict):
            return False
        answer = record["answer"]
        try:
            _question(answer["question"])
            raw = answer["text"].encode("utf-8")
            valid = (
                _whole(record["seq"])
                and answer["id"] == ident
                and answer["pane"] in PANES
                and answer["state"] in STATES
                and text_ok(raw)
                and len(raw) <= MAX_ANSWER_BYTES
                and type(answer["bytes"]) is int
                and answer["bytes"] == len(raw)
                and _whole(answer["rev"])
                and _whole(answer["asked"])
                and _label(answer["model"])
                and _label(answer["where"])
                and (answer["tokens"] is None or _whole(answer["tokens"]))
                and (answer["tps"] is None or _speed(answer["tps"]))
                and answer["finish"] in (None, "stop", "length")
                and (answer["error"] is None or isinstance(answer["error"], str) and _ERROR.fullmatch(answer["error"]))
            )
            offset = record["final_offset"]
            if answer["state"] == "done":
                return bool(valid and answer["finish"] is not None and _whole(offset) and offset <= len(raw))
            return bool(valid and offset is None and answer["finish"] is None)
        except (KeyError, AttributeError, UnicodeError, AnswerError, TypeError):
            return False

    def _retry_removals(self) -> None:
        for ident in tuple(self._removals):
            removed = True
            try:
                self._store.sweep_temps(_name(ident))
            except OSError:
                removed = False
            for name in (_name(ident), f".{_name(ident)}.lock"):
                try:
                    (self._store.root / name).unlink(missing_ok=True)
                except OSError:
                    removed = False
            if removed:
                self._removals.discard(ident)

    def _find(self, ident: str) -> dict:
        if not isinstance(ident, str) or ident not in self._records:
            raise StateError("answer")
        return deepcopy(self._records[ident])

    def _open_id(self, pane: str) -> str | None:
        queued = None
        for ident, record in self._records.items():
            answer = record["answer"]
            if answer["pane"] != pane:
                continue
            if answer["state"] in ("loading", "answering", "paused"):
                return ident
            if queued is None and answer["state"] == "queued":
                queued = ident
        return queued

    def _view(self, record: dict) -> dict:
        answer = deepcopy(record["answer"])
        if answer["state"] == "queued" and self._open_id(answer["pane"]) == answer["id"]:
            answer["state"] = "waiting"
        return answer

    def _save(self, record: dict) -> dict:
        ident = record["answer"]["id"]
        if record != self._records.get(ident):
            record["answer"]["rev"] = self._rev + 1
            self._store.write_json(_name(ident), record, durable=True)
            self._records[ident] = record
            self._rev += 1
            self._recoveries.discard(ident)
            if self._sweep_pending:
                self._retry_sweep()
        self._retry_recoveries()
        return self._view(record)

    def ask(self, pane: str, question: str, model: str = "") -> dict:
        """Store a queued answer, then drop old final answers with retryable cleanup."""
        with self._lock:
            if pane not in PANES:
                raise ValueError("pane")
            if pane == "gpu" and self._startup_notice:
                raise OSError(self._startup_notice)
            self._retry_removals()
            _question(question)
            if not _label(model):
                raise ValueError("model")
            if sum(row["answer"]["state"] not in FINAL for row in self._records.values()) >= MAX_ANSWERS:
                raise FullError("full")
            seq = max((row["seq"] for row in self._records.values()), default=0) + 1
            ident = uuid.uuid4().hex[:12]
            while ident in self._records or (self._store.root / _name(ident)).exists():
                ident = uuid.uuid4().hex[:12]
            answer = dict(
                id=ident,
                pane=pane,
                question=question,
                state="queued",
                text="",
                bytes=0,
                model=model,
                where="",
                tokens=None,
                tps=None,
                finish=None,
                error=None,
                asked=max(0, int(time.time())),
                rev=self._rev + 1,
            )
            record = {"answer": answer, "final_offset": None, "seq": seq}
            self._save(record)
            while len(self._records) > MAX_ANSWERS:
                oldest = next((ident for ident, row in self._records.items() if row["answer"]["state"] in FINAL), None)
                if oldest is None:
                    # A scanned oversized history may have too few final rows.
                    # The new answer is already accepted; keep unfinished work.
                    break
                del self._records[oldest]
                self._removals.add(oldest)
            self._retry_removals()
            return self._view(record)

    def get(self, answer_id: str) -> dict | None:
        """Return a copy of an answer, or None for an unknown id."""
        with self._lock:
            record = self._records.get(answer_id)
            return self._view(record) if record is not None else None

    def current(self, pane: str) -> dict | None:
        """Return the open answer of a pane, or None; refuse an unknown pane."""
        with self._lock:
            if pane not in PANES:
                raise ValueError("pane")
            ident = self._open_id(pane)
            return self._view(self._records[ident]) if ident is not None else None

    def latest(self, pane: str) -> dict | None:
        """Return the newest answer in a pane in any state, or None."""
        with self._lock:
            if pane not in PANES:
                raise ValueError("pane")
            return next(
                (self._view(row) for row in reversed(self._records.values()) if row["answer"]["pane"] == pane),
                None,
            )

    def snapshot(self) -> dict:
        """Return a consistent revision and copied answers, newest first."""
        with self._lock:
            return {"rev": self._rev, "answers": [self._view(row) for row in reversed(self._records.values())]}

    def begin(self, answer_id: str) -> dict:
        """Move a waiting or paused answer to loading, keeping its bytes."""
        with self._lock:
            record = self._find(answer_id)
            if self._view(record)["state"] not in ("waiting", "paused"):
                raise StateError("begin")
            record["answer"]["state"] = "loading"
            return self._save(record)

    def append(self, answer_id: str, offset: int, data: bytes, done: bool = False, finish: str | None = None) -> dict:
        """Append whole UTF-8 bytes, acknowledge repeats, or finish and promote.

        Offsets count bytes. Invalid finish/done combinations raise StateError.
        A completed answer accepts only its exact final post, also after restart.
        """
        with self._lock:
            record = self._find(answer_id)
            answer = record["answer"]
            raw = answer["text"].encode("utf-8")
            if answer["state"] == "done":
                if (
                    type(offset) is int
                    and offset == record["final_offset"]
                    and isinstance(data, bytes)
                    and data == raw[offset:]
                    and done is True
                    and finish == answer["finish"]
                ):
                    return answer
                raise StateError("done")
            if answer["state"] not in ("loading", "answering"):
                raise StateError("append")
            if not text_ok(data) or (not data and not done):
                raise TextError("text")
            if not _whole(offset):
                raise OffsetError(len(raw))
            if offset == len(raw):
                if len(raw) + len(data) > MAX_ANSWER_BYTES:
                    raise TooLongError(len(raw))
            elif (
                offset < len(raw)
                and offset + len(data) <= len(raw)
                and raw[offset : offset + len(data)] == data
                and not done
            ):
                return answer
            else:
                raise OffsetError(len(raw))
            if (
                type(done) is not bool
                or (done and finish not in ("stop", "length"))
                or (not done and finish is not None)
            ):
                raise StateError("finish")
            answer["text"] += data.decode("utf-8")
            answer["bytes"] += len(data)
            if done:
                answer["state"] = "done"
                answer["finish"] = finish
                record["final_offset"] = offset
            elif data:
                answer["state"] = "answering"
            return self._save(record)

    def pause(self, answer_id: str) -> dict:
        """Pause loading or answering work without changing its text."""
        with self._lock:
            record = self._find(answer_id)
            if record["answer"]["state"] not in ("loading", "answering"):
                raise StateError("pause")
            record["answer"]["state"] = "paused"
            return self._save(record)

    def cancel(self, answer_id: str) -> dict:
        """Cancel unfinished work and promote its pane's oldest queued answer."""
        with self._lock:
            return self._finish(answer_id, "cancelled", None)

    def fail(self, answer_id: str, error: str) -> dict:
        """Fail unfinished work with a short code and promote queued work."""
        with self._lock:
            record = self._find(answer_id)
            if record["answer"]["state"] in FINAL:
                raise StateError("fail")
            if not isinstance(error, str) or not _ERROR.fullmatch(error):
                raise ValueError("error")
            return self._finish(answer_id, "failed", error)

    def _finish(self, ident: str, state: str, error: str | None) -> dict:
        record = self._find(ident)
        if record["answer"]["state"] in FINAL:
            raise StateError(state)
        record["answer"].update(state=state, error=error)
        return self._save(record)

    def note(
        self,
        answer_id: str,
        model: str | None = None,
        where: str | None = None,
        tokens: int | None = None,
        tps: float | None = None,
    ) -> dict:
        """Update supplied display metadata in any state; None leaves it alone."""
        with self._lock:
            record = self._find(answer_id)
            if (
                (model is not None and not _label(model))
                or (where is not None and not _label(where))
                or (tokens is not None and not _whole(tokens))
                or (tps is not None and not _speed(tps))
            ):
                raise ValueError("metadata")
            for key, value in (("model", model), ("where", where), ("tokens", tokens), ("tps", tps)):
                if value is not None:
                    record["answer"][key] = value
            return self._save(record)


class _Shared:
    lock = threading.RLock()
    store: AnswerStore | None = None


def shared() -> AnswerStore:
    """Return the lazily constructed process store using the default Store."""
    with _Shared.lock:
        if _Shared.store is None:
            _Shared.store = AnswerStore()
        return _Shared.store


def reset() -> None:
    """Forget the process store so the next shared call constructs another."""
    with _Shared.lock:
        _Shared.store = None
