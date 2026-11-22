# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure byte framing, signatures and message validation for GPU Link."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re

PROTO = 1
CODE_PREFIX = b"opos-link-v1-code"
REQUEST_PREFIX = b"opos-link-v1"
REPLY_PREFIX = b"opos-link-v1-reply"
KEY_BYTES = 32
NONCE_BYTES = 12
EPOCH_BYTES = 16
LEASE_BYTES = 16
PC_ID_BYTES = 8
ENROL_BODY_LIMIT = 1024
STATUS_BODY_LIMIT = 2048
TEXT_BODY_LIMIT = 8192
WORK_REPLY_LIMIT = 65536
MAX_QUESTION_BYTES = 4096
MAX_ANSWER_BYTES = 12288
MAX_WHOLE = 9007199254740991
NONCES_PER_PC = 4096

_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 -]{0,22}[A-Za-z0-9])?")
_HEX = re.compile(r"[0-9a-f]*")
_WORK = re.compile(r"/v1/work\?wait=(0|[1-9]|1[0-9]|20)")
_TEXT = re.compile(r"/v1/runs/([0-9a-f]{32})/text")
_MODEL = re.compile(r"/v1/models/([0-9a-f]{64})")
_RANGE = re.compile(r"bytes=(0|[1-9][0-9]*)-(0|[1-9][0-9]*)")
_ERROR = re.compile(r"[a-z0-9_-]{1,32}")
_INTEGER = re.compile(r"0|[1-9][0-9]{0,15}")
_DECIMAL = re.compile(r"(0|[1-9][0-9]{0,5})\.[0-9]{1,3}")
_STATES = ("idle", "copying", "loading", "ready", "busy", "stopping", "error")
_ERROR_CODES = ("lease", "state", "text", "too_long", "rate", "range", "model", "request")


class ProtoError(ValueError):
    """A refused message, with its wire error code in ``code``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def frame(value: bytes) -> bytes:
    """Prefix a value with its unsigned four-byte big-endian length."""
    return len(value).to_bytes(4, "big") + value


def valid_label(label: object) -> bool:
    """Return whether a label meets the shared screen-label grammar."""
    return isinstance(label, str) and _LABEL.fullmatch(label) is not None


def code(pc_label: str, key: bytes) -> str:
    """Return the six-digit enrolment confirmation code."""
    digest = hashlib.sha256(frame(CODE_PREFIX) + frame(pc_label.encode("utf-8")) + frame(key)).digest()
    return f"{int.from_bytes(digest, 'big') % 1000000:06d}"


def pc_id(key: bytes) -> str:
    """Return the first eight bytes of the key hash as lowercase hex."""
    return hashlib.sha256(key).digest()[:PC_ID_BYTES].hex()


def unhex(value: object, size: int) -> bytes | None:
    """Decode only lowercase hexadecimal text of exactly ``size`` bytes."""
    if not isinstance(value, str) or len(value) != size * 2 or _HEX.fullmatch(value) is None:
        return None
    return bytes.fromhex(value)


def request_mac(key: bytes, epoch: bytes, method: str, target: str, range_value: str, nonce: bytes, body: bytes) -> str:
    """Sign the exact request bytes, including its target and range."""
    if (
        not isinstance(key, bytes)
        or len(key) != KEY_BYTES
        or not isinstance(epoch, bytes)
        or len(epoch) != EPOCH_BYTES
        or not isinstance(nonce, bytes)
        or len(nonce) != NONCE_BYTES
        or not isinstance(method, str)
        or method not in ("GET", "POST")
        or not isinstance(target, str)
        or not target.isascii()
        or not isinstance(range_value, str)
        or not range_value.isascii()
        or not isinstance(body, bytes)
    ):
        raise ProtoError("request")
    values = (
        REQUEST_PREFIX,
        epoch,
        method.encode("ascii"),
        target.encode("ascii"),
        range_value.encode("ascii"),
        nonce,
        hashlib.sha256(body).digest(),
    )
    return hmac.new(key, b"".join(frame(value) for value in values), hashlib.sha256).hexdigest()


def reply_mac(key: bytes, nonce: bytes, status: int, content_range: str, body: bytes | None) -> str:
    """Sign a reply, using an empty hash frame for a model file."""
    if (
        not isinstance(key, bytes)
        or len(key) != KEY_BYTES
        or not isinstance(nonce, bytes)
        or len(nonce) != NONCE_BYTES
        or type(status) is not int
        or not 100 <= status <= 599
        or not isinstance(content_range, str)
        or not content_range.isascii()
        or (body is not None and not isinstance(body, bytes))
        or (body is None and not ((status == 200 and not content_range) or (status == 206 and content_range)))
        or (body is not None and content_range)
    ):
        raise ProtoError("request")
    values = (
        REPLY_PREFIX,
        nonce,
        str(status).encode("ascii"),
        content_range.encode("ascii"),
        b"" if body is None else hashlib.sha256(body).digest(),
    )
    return hmac.new(key, b"".join(frame(value) for value in values), hashlib.sha256).hexdigest()


def same_mac(a: object, b: object) -> bool:
    """Compare two well-formed 64-character lowercase MACs in constant time."""
    if unhex(a, 32) is None or unhex(b, 32) is None:
        return False
    return hmac.compare_digest(a, b)


def dumps(obj: object) -> bytes:
    """Encode compact JSON as UTF-8, retaining non-ASCII characters."""
    pending = [(obj, 0)]
    while pending:
        value, depth = pending.pop()
        if type(value) is str:
            if any(ord(char) < 32 and char not in "\n\t" for char in value):
                raise ProtoError("text")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as err:
                raise ProtoError("text") from err
        elif type(value) in (dict, list):
            if depth >= 4:
                raise ProtoError("request")
            children = (child for pair in value.items() for child in pair) if type(value) is dict else value
            pending.extend((child, depth + 1) for child in children)
        elif value is not None and type(value) not in (int, float, bool):
            raise ProtoError("request")
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as err:
        raise ProtoError("request") from err


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtoError("request")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ProtoError("request")


def _float(value: str) -> float:
    if _DECIMAL.fullmatch(value) is None:
        raise ProtoError("request")
    result = float(value)
    if not math.isfinite(result) or result > 100000:
        raise ProtoError("request")
    return result


def _int(value: str) -> int:
    if _INTEGER.fullmatch(value) is None or (len(value) == 16 and value > "9007199254740991"):
        raise ProtoError("request")
    return int(value)


def loads(raw: bytes, limit: int) -> dict:
    """Decode a size-bounded JSON object, rejecting duplicates and nonfinite numbers."""
    if not isinstance(raw, bytes) or type(limit) is not int or limit < 0:
        raise ProtoError("request")
    if len(raw) > limit:
        raise ProtoError("too_long")
    # Count containers before decoding, without relying on Python's recursion limit.
    depth = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
        elif byte == 34:
            in_string = True
        elif byte in (123, 91):
            depth += 1
            if depth > 4:
                raise ProtoError("request")
        elif byte in (125, 93):
            depth -= 1
    try:
        obj = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant, parse_int=_int, parse_float=_float
        )
    except (ValueError, RecursionError) as err:
        raise ProtoError("request") from err
    if not isinstance(obj, dict):
        raise ProtoError("request")
    return obj


def parse_target(method: str, target: str) -> tuple:
    """Validate the method and exact request target, returning its route values."""
    if not isinstance(method, str) or not isinstance(target, str):
        raise ProtoError("request")
    for verb, path, kind in (
        ("GET", "/v1/hello", "hello"),
        ("POST", "/v1/enrol", "enrol"),
        ("POST", "/v1/status", "status"),
    ):
        if method == verb and target == path:
            return (kind,)
    if method == "GET":
        match = _WORK.fullmatch(target)
        if match:
            return ("work", int(match[1]))
        match = _MODEL.fullmatch(target)
        if match:
            return ("model", match[1])
    if method == "POST":
        match = _TEXT.fullmatch(target)
        if match:
            return ("text", bytes.fromhex(match[1]))
    raise ProtoError("request")


def parse_range(value: str, total: int) -> tuple[int, int]:
    """Validate one inclusive byte range against the model's total size."""
    if not isinstance(value, str) or type(total) is not int:
        raise ProtoError("range")
    match = _RANGE.fullmatch(value)
    if match is None:
        raise ProtoError("range")
    try:
        start, end = int(match[1]), int(match[2])
    except ValueError as err:
        raise ProtoError("range") from err
    if not 0 <= start <= end < total:
        raise ProtoError("range")
    return start, end


def content_range(start: int, end: int, total: int) -> str:
    """Format the Content-Range value for an already validated byte range."""
    return f"bytes {start}-{end}/{total}"


def _keys(obj: dict, keys: tuple[str, ...]) -> None:
    if obj.keys() != set(keys):
        raise ProtoError("request")


def _whole(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_WHOLE


def parse_enrol(raw: bytes) -> tuple[str, bytes]:
    """Validate an enrolment body and return its label and binary key."""
    obj = loads(raw, ENROL_BODY_LIMIT)
    _keys(obj, ("pc_label", "key"))
    key = unhex(obj["key"], KEY_BYTES)
    if not valid_label(obj["pc_label"]) or key is None:
        raise ProtoError("request")
    return obj["pc_label"], key


def _gpu(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    _keys(value, ("name", "memory_mb", "driver"))
    for key, limit in (("name", 64), ("driver", 32)):
        text = value[key]
        if not isinstance(text, str) or len(text) > limit or any(not " " <= char <= "~" for char in text):
            return False
    return _whole(value["memory_mb"])


def parse_status(raw: bytes) -> dict:
    """Validate every status field, decoding a non-null lease to bytes."""
    obj = loads(raw, STATUS_BODY_LIMIT)
    _keys(obj, ("state", "lease", "copied", "total", "gpu", "tokens", "tps", "error"))
    if obj["state"] not in _STATES:
        raise ProtoError("request")
    if obj["lease"] is not None:
        lease = unhex(obj["lease"], LEASE_BYTES)
        if lease is None:
            raise ProtoError("request")
        obj["lease"] = lease
    for field in ("copied", "total", "tokens"):
        if obj[field] is not None and not _whole(obj[field]):
            raise ProtoError("request")
    copied, total = obj["copied"], obj["total"]
    if (copied is None) != (total is None) or (copied is not None and copied > total):
        raise ProtoError("request")
    if obj["gpu"] is not None and not _gpu(obj["gpu"]):
        raise ProtoError("request")
    tps = obj["tps"]
    if tps is not None and (type(tps) not in (int, float) or not 0 <= tps <= 100000):
        raise ProtoError("request")
    error = obj["error"]
    if error is not None and (not isinstance(error, str) or _ERROR.fullmatch(error) is None):
        raise ProtoError("request")
    if (error is not None) != (obj["state"] == "error"):
        raise ProtoError("request")
    return obj


def parse_text(raw: bytes) -> dict:
    """Validate text-post fields and encode text; leave text policy to its store."""
    obj = loads(raw, TEXT_BODY_LIMIT)
    _keys(obj, ("offset", "text", "done", "finish"))
    if (
        not _whole(obj["offset"])
        or obj["offset"] > MAX_ANSWER_BYTES
        or not isinstance(obj["text"], str)
        or type(obj["done"]) is not bool
    ):
        raise ProtoError("request")
    if (obj["done"] and obj["finish"] not in ("stop", "length")) or (not obj["done"] and obj["finish"] is not None):
        raise ProtoError("request")
    try:
        data = obj["text"].encode("utf-8")
    except UnicodeEncodeError as err:
        raise ProtoError("text") from err
    return {"offset": obj["offset"], "data": data, "done": obj["done"], "finish": obj["finish"]}


def hello_body(label: str, epoch: bytes) -> bytes:
    """Encode the unsigned protocol greeting."""
    if not valid_label(label) or not isinstance(epoch, bytes) or len(epoch) != EPOCH_BYTES:
        raise ProtoError("request")
    return dumps({"proto": PROTO, "label": label, "epoch": epoch.hex()})


def status_reply(cancel: bytes | None) -> bytes:
    """Encode a status acknowledgement with an optional lease cancellation."""
    if cancel is not None and (not isinstance(cancel, bytes) or len(cancel) != LEASE_BYTES):
        raise ProtoError("request")
    return dumps({"cancel": None if cancel is None else cancel.hex()})


def work_reply(lease: bytes, model_sha256: str, question: str, partial: str) -> bytes:
    """Encode work, refusing a reply larger than the raw wire limit."""
    if (
        not isinstance(lease, bytes)
        or len(lease) != LEASE_BYTES
        or unhex(model_sha256, 32) is None
        or not isinstance(question, str)
        or not isinstance(partial, str)
    ):
        raise ProtoError("request")
    for text, limit in ((question, MAX_QUESTION_BYTES), (partial, MAX_ANSWER_BYTES)):
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise ProtoError("text")
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError as err:
            raise ProtoError("text") from err
        if len(data) > limit:
            raise ProtoError("too_long")
    raw = dumps({"lease": lease.hex(), "model_sha256": model_sha256, "question": question, "partial": partial})
    if len(raw) > WORK_REPLY_LIMIT:
        raise ProtoError("too_long")
    return raw


def text_reply(acked: int) -> bytes:
    """Encode the acknowledged answer-byte offset."""
    if not _whole(acked) or acked > MAX_ANSWER_BYTES:
        raise ProtoError("request")
    return dumps({"acked": acked})


def stored_reply(stored: int) -> bytes:
    """Encode the stored answer-byte count after a conflict or overflow."""
    if not _whole(stored) or stored > MAX_ANSWER_BYTES:
        raise ProtoError("request")
    return dumps({"stored": stored})


def error_reply(code: str) -> bytes:
    """Encode a protocol error body."""
    if not isinstance(code, str) or code not in _ERROR_CODES:
        raise ProtoError("request")
    return dumps({"error": code})
