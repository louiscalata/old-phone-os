# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent wire vectors, strict parsing and malformed-input checks."""

from __future__ import annotations

import ast
import hashlib
import json
import random
import re
import unittest
from pathlib import Path

from opos import gpu_link_proto as proto

FIXTURE = Path(__file__).parent / "fixtures" / "gpu_link" / "examples.json"
KEY = bytes(range(32))
EPOCH = bytes(range(16, 32))
NONCE = b"\x07" * 12
LEASE = bytes(range(64, 80))
MODEL = "ab" * 32


def status_body():
    return {
        "state": "idle",
        "lease": None,
        "copied": None,
        "total": None,
        "gpu": None,
        "tokens": None,
        "tps": None,
        "error": None,
    }


def text_body():
    return {"offset": 0, "text": "", "done": False, "finish": None}


def flip(value):
    return bytes([value[0] ^ 1]) + value[1:]


def fuzz_mutation(rng, body):
    raw = bytearray(body)
    for _ in range(rng.randrange(1, 5)):
        position = rng.randrange(len(raw) + 1)
        raw[position : position + rng.randrange(3)] = rng.randbytes(rng.randrange(4))
    return bytes(raw)


class ProtoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def assert_error(self, code, function, *args):
        with self.assertRaises(proto.ProtoError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_fixture_constants(self):
        self.assertEqual(proto.PROTO, self.examples["proto"])
        for name, constant in (
            ("code", proto.CODE_PREFIX),
            ("request", proto.REQUEST_PREFIX),
            ("reply", proto.REPLY_PREFIX),
        ):
            self.assertEqual(constant.decode("ascii"), self.examples["prefixes"][name])
        sizes = {
            "key": proto.KEY_BYTES,
            "nonce": proto.NONCE_BYTES,
            "epoch": proto.EPOCH_BYTES,
            "lease": proto.LEASE_BYTES,
            "pc_id": proto.PC_ID_BYTES,
            "enrol_body": proto.ENROL_BODY_LIMIT,
            "status_body": proto.STATUS_BODY_LIMIT,
            "text_body": proto.TEXT_BODY_LIMIT,
            "work_reply": proto.WORK_REPLY_LIMIT,
            "question": proto.MAX_QUESTION_BYTES,
            "answer": proto.MAX_ANSWER_BYTES,
            "nonces_per_pc": proto.NONCES_PER_PC,
        }
        self.assertEqual(sizes, self.examples["sizes"])

    def test_fixture_frames_and_labels(self):
        for item in self.examples["frames"]:
            self.assertEqual(proto.frame(bytes.fromhex(item["value_hex"])).hex(), item["framed_hex"])
        for label in self.examples["labels"]["valid"]:
            self.assertTrue(proto.valid_label(label), repr(label))
        for label in self.examples["labels"]["invalid"] + [None, 1, b"PC", []]:
            self.assertFalse(proto.valid_label(label), repr(label))

    def test_fixture_enrol(self):
        for item in self.examples["enrol"]:
            with self.subTest(label=item["pc_label"]):
                key = bytes.fromhex(item["key_hex"])
                raw = bytes.fromhex(item["body_hex"])
                self.assertEqual(raw, item["body_text"].encode("utf-8"))
                self.assertEqual(proto.code(item["pc_label"], key), item["code"])
                self.assertEqual(proto.pc_id(key), item["pc_id"])
                self.assertEqual(proto.parse_enrol(raw), (item["pc_label"], key))
                self.assertEqual(proto.dumps(proto.loads(raw, 1024)), raw)

    def test_fixture_requests(self):
        for item in self.examples["requests"]:
            with self.subTest(name=item["name"]):
                key = bytes.fromhex(item["key_hex"])
                epoch = bytes.fromhex(item["epoch_hex"])
                nonce = bytes.fromhex(item["nonce_hex"])
                raw = bytes.fromhex(item["body_hex"])
                self.assertEqual(raw, item["body_text"].encode("utf-8"))
                self.assertEqual(hashlib.sha256(raw).hexdigest(), item["body_sha256"])
                self.assertEqual(proto.pc_id(key), item["pc_id"])
                self.assertEqual(
                    proto.request_mac(key, epoch, item["method"], item["target"], item["range"], nonce, raw),
                    item["mac"],
                )
                signed = b"".join(
                    proto.frame(value)
                    for value in (
                        proto.REQUEST_PREFIX,
                        epoch,
                        item["method"].encode("ascii"),
                        item["target"].encode("ascii"),
                        item["range"].encode("ascii"),
                        nonce,
                        hashlib.sha256(raw).digest(),
                    )
                )
                self.assertEqual(signed.hex(), item["signed_hex"])
                route = proto.parse_target(item["method"], item["target"])
                if route[0] in ("status", "text"):
                    parsed = proto.parse_status(raw) if route[0] == "status" else proto.parse_text(raw)
                    expected = proto.loads(raw, 8192)
                    if route[0] == "status" and expected["lease"] is not None:
                        expected["lease"] = bytes.fromhex(expected["lease"])
                    if route[0] == "text":
                        expected["data"] = expected.pop("text").encode("utf-8")
                    self.assertEqual(parsed, expected)
                    self.assertEqual(proto.dumps(proto.loads(raw, 8192)), raw)
                else:
                    self.assertEqual(raw, b"")
                if item["range"]:
                    proto.parse_range(item["range"], self.examples["ranges"]["total"])

    def test_fixture_replies(self):
        for item in self.examples["replies"]:
            with self.subTest(name=item["name"]):
                raw = None if item["model_file"] else bytes.fromhex(item["body_hex"])
                nonce = bytes.fromhex(item["nonce_hex"])
                self.assertEqual(
                    proto.reply_mac(bytes.fromhex(item["key_hex"]), nonce, item["status"], item["content_range"], raw),
                    item["mac"],
                )
                signed = b"".join(
                    proto.frame(value)
                    for value in (
                        proto.REPLY_PREFIX,
                        nonce,
                        str(item["status"]).encode("ascii"),
                        item["content_range"].encode("ascii"),
                        b"" if raw is None else hashlib.sha256(raw).digest(),
                    )
                )
                self.assertEqual(signed.hex(), item["signed_hex"])
                if item["model_file"]:
                    self.assertIsNone(item["body_text"])
                    self.assertIsNone(item["body_hex"])
                else:
                    self.assertEqual(raw, item["body_text"].encode("utf-8"))
                    if raw:
                        obj = proto.loads(raw, 65536)
                        self.assertEqual(proto.dumps(obj), raw)
                        self.assertEqual(self.build_fixture_reply(obj), raw)

    def build_fixture_reply(self, obj):
        if "cancel" in obj:
            return proto.status_reply(None if obj["cancel"] is None else bytes.fromhex(obj["cancel"]))
        if "lease" in obj:
            return proto.work_reply(bytes.fromhex(obj["lease"]), obj["model_sha256"], obj["question"], obj["partial"])
        if "acked" in obj:
            return proto.text_reply(obj["acked"])
        if "stored" in obj:
            return proto.stored_reply(obj["stored"])
        return proto.error_reply(obj["error"])

    def test_fixture_targets(self):
        for item in self.examples["targets"]["valid"]:
            kind = item["kind"]
            method = "POST" if kind in ("enrol", "status", "text") else "GET"
            expected = (kind,)
            if kind == "work":
                expected += (item["wait"],)
            elif kind == "text":
                expected += (bytes.fromhex(item["lease"]),)
            elif kind == "model":
                expected += (item["sha256"],)
            self.assertEqual(proto.parse_target(method, item["target"]), expected)
            self.assert_error("request", proto.parse_target, "GET" if method == "POST" else "POST", item["target"])
        for target in self.examples["targets"]["invalid"]:
            for method in ("GET", "POST"):
                self.assert_error("request", proto.parse_target, method, target)
        for wait in range(21):
            self.assertEqual(proto.parse_target("GET", f"/v1/work?wait={wait}"), ("work", wait))
        for method in ("get", "HEAD", "PUT", " GET", None, b"GET", []):
            self.assert_error("request", proto.parse_target, method, "/v1/hello")
        for target in (None, b"/v1/hello", [], "/v1/hello\n", "/v1/héllo"):
            self.assert_error("request", proto.parse_target, "GET", target)

    def test_fixture_ranges(self):
        total = self.examples["ranges"]["total"]
        for item in self.examples["ranges"]["valid"]:
            self.assertEqual(proto.parse_range(item["header"], total), (item["start"], item["end"]))
            self.assertEqual(proto.content_range(item["start"], item["end"], total), item["content_range"])
        for value in self.examples["ranges"]["invalid"] + [None, b"bytes=0-1", [], "bytes=0-1\n"]:
            self.assert_error("range", proto.parse_range, value, total)
        self.assert_error("range", proto.parse_range, "bytes=0-" + "9" * 100000, total)
        for bad_total in (0, -1, 1.5, True, None):
            self.assert_error("range", proto.parse_range, "bytes=0-0", bad_total)
        self.assertEqual(proto.parse_range("bytes=0-0", 1), (0, 0))

    def test_request_mac_binds_each_input(self):
        inputs = [KEY, EPOCH, "POST", "/v1/status", "bytes=0-1", NONCE, b"{}"]
        expected = proto.request_mac(*inputs)
        for index, value in enumerate(inputs):
            changed = list(inputs)
            if index == 2:
                changed[index] = "GET"
            else:
                changed[index] = (
                    flip(value) if isinstance(value, bytes) else flip(value.encode("ascii")).decode("ascii")
                )
            with self.subTest(input=index):
                self.assertNotEqual(proto.request_mac(*changed), expected)

    def test_reply_mac_binds_each_input(self):
        inputs = [KEY, NONCE, 200, "", b"{}"]
        expected = proto.reply_mac(*inputs)
        for index in (0, 1, 2, 4):
            value = inputs[index]
            changed = list(inputs)
            if isinstance(value, bytes):
                changed[index] = flip(value)
            else:
                changed[index] = value ^ 1
            with self.subTest(input=index):
                self.assertNotEqual(proto.reply_mac(*changed), expected)
        self.assertNotEqual(proto.reply_mac(KEY, NONCE, 200, "", None), proto.reply_mac(KEY, NONCE, 200, "", b""))
        self.assertNotEqual(
            proto.reply_mac(KEY, NONCE, 206, "bytes 0-1/2", None),
            proto.reply_mac(KEY, NONCE, 206, "bytes 0-0/2", None),
        )

    def test_framing_separates_adjacent_inputs(self):
        self.assertNotEqual(proto.frame(b"ab") + proto.frame(b"c"), proto.frame(b"a") + proto.frame(b"bc"))
        self.assertNotEqual(
            proto.request_mac(KEY, EPOCH, "POST", "/v1/status", "", NONCE, b""),
            proto.request_mac(KEY, EPOCH, "POST", "/v1/sta", "tus", NONCE, b""),
        )

    def test_signature_argument_validation(self):
        request = [KEY, EPOCH, "POST", "/v1/status", "", NONCE, b""]
        reply = [KEY, NONCE, 200, "", b""]
        for function, valid, invalid in (
            (
                proto.request_mac,
                request,
                {
                    0: (None, "k" * 32, bytearray(KEY), b"", KEY + b"x"),
                    1: (None, "e" * 16, bytearray(EPOCH), b"", EPOCH + b"x"),
                    2: (None, [], b"GET", "DELETE", "get", "GÉT", "\ud800"),
                    3: (None, [], b"/x", "/é", "\ud800"),
                    4: (None, [], b"", "bytes=0-é", "\ud800"),
                    5: (None, "n" * 12, bytearray(NONCE), b"", NONCE + b"x"),
                    6: (None, "", bytearray(), 0),
                },
            ),
            (
                proto.reply_mac,
                reply,
                {
                    0: (None, "k" * 32, bytearray(KEY), b"", KEY + b"x"),
                    1: (None, "n" * 12, bytearray(NONCE), b"", NONCE + b"x"),
                    2: (None, "200", 200.0, True, False, [], 99, 600),
                    3: (None, [], b"", "bytes 0-é/2", "\ud800"),
                    4: ("", bytearray(), 0),
                },
            ),
        ):
            for index, values in invalid.items():
                for value in values:
                    with self.subTest(function=function.__name__, index=index, value=value):
                        args = valid.copy()
                        args[index] = value
                        self.assert_error("request", function, *args)
        self.assert_error("request", proto.request_mac, b"", b"", "DELETE", "/x", "x", b"", b"")
        for status in (100, 599):
            self.assertEqual(len(proto.reply_mac(KEY, NONCE, status, "", b"")), 64)

    def test_reply_mac_rejects_invalid_body_range_combinations(self):
        for status, content_range, body in (
            *((status, "", None) for status in (100, 204, 404, 416, 599)),
            (200, "bytes 0-1/2", None),
            (206, "", None),
            (200, "bytes 0-1/2", b""),
            (206, "bytes 0-1/2", b"{}"),
        ):
            with self.subTest(status=status, content_range=content_range, body=body):
                self.assert_error("request", proto.reply_mac, KEY, NONCE, status, content_range, body)

    def test_same_mac(self):
        value = "ab" * 32
        self.assertTrue(proto.same_mac(value, value))
        self.assertFalse(proto.same_mac(value, "ac" * 32))
        for malformed in ("a" * 63, "a" * 65, value.upper(), "g" * 64, value.encode(), None, 3, [], ""):
            self.assertFalse(proto.same_mac(value, malformed))
            self.assertFalse(proto.same_mac(malformed, value))
            self.assertFalse(proto.same_mac(malformed, malformed))

    def test_unhex(self):
        self.assertEqual(proto.unhex(KEY.hex(), 32), KEY)
        for value in (KEY.hex().upper(), "a" * 63, "a" * 66, " " + "a" * 63, "0x" + "a" * 62, "g" * 64):
            self.assertIsNone(proto.unhex(value, 32))
        for value in (None, 5, b"ab" * 32, [], {}):
            self.assertIsNone(proto.unhex(value, 32))
        self.assertIsNone(proto.unhex(KEY.hex(), 31))

    def test_loads_rejects_malformed_json(self):
        for raw in (
            b"\xff",
            b"\xc0\xaf",
            b"\xed\xa0\x80",
            b"[]",
            b'"text"',
            b"1",
            b"true",
            b"null",
            b'{"a":1,"a":2}',
            b'{"a":{"b":1,"b":2}}',
            b'{"a":1,"\\u0061":2}',
            b'{"a":NaN}',
            b'{"a":Infinity}',
            b'{"a":-Infinity}',
            b'{"a":1e999}',
            b'{"a":-1e999}',
            b"{}{}",
            b"\xef\xbb\xbf{}",
            b'{"a":"\x00"}',
        ):
            with self.subTest(raw=raw):
                self.assert_error("request", proto.loads, raw, 65536)
        for raw in (None, "{}", 5, []):
            self.assert_error("request", proto.loads, raw, 100)
        huge_int = b'{"a":' + b"9" * 10000 + b"}"
        self.assert_error("request", proto.loads, huge_int, len(huge_int))

    def test_loads_size_and_deep_nesting(self):
        self.assertEqual(proto.loads(b"{}  ", 4), {})
        self.assert_error("too_long", proto.loads, b"{}   ", 4)
        # Size refusal must precede decoding, even for malformed input.
        self.assert_error("too_long", proto.loads, b"\xff" * 5, 4)
        for opening, closing in ((b"[", b"]"), (b'{"a":', b"}")):
            for depth in (4, 5, 100000, 100001):
                raw = b'{"a":' + opening * (depth - 1) + b"0" + closing * (depth - 1) + b"}"
                with self.subTest(opening=opening, depth=depth):
                    if depth == 4:
                        self.assertEqual(proto.loads(raw, len(raw)), json.loads(raw))
                    else:
                        self.assert_error("request", proto.loads, raw, len(raw))
                if depth >= 100000:
                    self.assert_error("too_long", proto.loads, raw, 1024)
        raw = b'{"a":"[[[[[{{{{{\\"\\\\","b":{}}'
        self.assertEqual(proto.loads(raw, len(raw)), json.loads(raw))
        for limit in (None, "100", 100.0, True, False, [], {}, -1):
            self.assert_error("request", proto.loads, b"{}", limit)

    def test_enrol_schema(self):
        obj = {"pc_label": "PC", "key": KEY.hex()}
        invalid = [
            {**obj, "key": "a" * 63},
            {**obj, "key": "a" * 65},
            {**obj, "key": KEY.hex().upper()},
            {**obj, "key": None},
            {**obj, "pc_label": "bad_label"},
            {**obj, "pc_label": False},
            {"pc_label": "PC"},
            {"key": KEY.hex()},
            {**obj, "extra": 0},
        ]
        for value in invalid:
            self.assert_error("request", proto.parse_enrol, proto.dumps(value))

    def test_body_size_boundaries(self):
        for parser, obj, limit in (
            (proto.parse_enrol, {"pc_label": "PC", "key": KEY.hex()}, 1024),
            (proto.parse_status, status_body(), 2048),
            (proto.parse_text, text_body(), 8192),
        ):
            with self.subTest(parser=parser.__name__):
                raw = proto.dumps(obj)
                at_limit = raw + b" " * (limit - len(raw))
                self.assertEqual(len(at_limit), limit)
                self.assertEqual(parser(at_limit), parser(raw))
                self.assert_error("too_long", parser, at_limit + b" ")

    def test_status_states_and_numeric_boundaries(self):
        for state in ("idle", "copying", "loading", "ready", "busy", "stopping", "error"):
            obj = {**status_body(), "state": state, "error": "gpu_error-1" if state == "error" else None}
            self.assertEqual(proto.parse_status(proto.dumps(obj)), obj)
        for field in ("copied", "total", "tokens"):
            for value in (0, 1, 9007199254740991):
                obj = {**status_body(), field: value}
                if field in ("copied", "total"):
                    obj.update(copied=value, total=value)
                self.assertEqual(proto.parse_status(proto.dumps(obj)), obj)
            for value in (True, False, -1, 1.0, "1", [], {}, 9007199254740992):
                raw = json.dumps({**status_body(), field: value}).encode()
                self.assert_error("request", proto.parse_status, raw)
        for value in (0, 0.0, 96.4, 100000, 100000.0):
            obj = {**status_body(), "tps": value}
            self.assertEqual(proto.parse_status(proto.dumps(obj)), obj)
        for value in (True, False, -1, 100001, "1", [], {}):
            self.assert_error("request", proto.parse_status, json.dumps({**status_body(), "tps": value}).encode())

    def test_surv_status_error_code_grammar(self):
        for bad in ("Bad Code", "x" * 33, "", "UPPER", 7):
            with self.subTest(bad=bad):
                body = proto.dumps({**status_body(), "state": "error", "error": bad})
                self.assert_error("request", proto.parse_status, body)

    def test_status_error_matches_state(self):
        self.assert_error("request", proto.parse_status, proto.dumps({**status_body(), "state": "error"}))
        for state in ("idle", "copying", "loading", "ready", "busy", "stopping"):
            with self.subTest(state=state):
                self.assert_error(
                    "request",
                    proto.parse_status,
                    proto.dumps({**status_body(), "state": state, "error": "gpu_error-1"}),
                )

    def test_status_copy_progress_consistency(self):
        for copied, total in ((0, None), (None, 0), (2, 1)):
            with self.subTest(copied=copied, total=total):
                self.assert_error(
                    "request", proto.parse_status, proto.dumps({**status_body(), "copied": copied, "total": total})
                )
        for copied, total in ((None, None), (0, 0), (1, 1), (0, 1)):
            with self.subTest(copied=copied, total=total):
                obj = {**status_body(), "copied": copied, "total": total}
                self.assertEqual(proto.parse_status(proto.dumps(obj)), obj)

    def test_status_schema_and_gpu(self):
        gpu = {"name": "GPU", "memory_mb": 10240, "driver": "1.2"}
        valid = {**status_body(), "state": "error", "gpu": gpu, "lease": LEASE.hex(), "error": "gpu_error-1"}
        self.assertEqual(proto.parse_status(proto.dumps(valid)), {**valid, "lease": LEASE})
        self.assertEqual(
            proto.parse_status(proto.dumps({**valid, "gpu": {"name": "~" * 64, "driver": " " * 32, "memory_mb": 0}}))[
                "gpu"
            ]["memory_mb"],
            0,
        )
        for field, value in (
            ("state", "unknown"),
            ("state", []),
            ("lease", "A" * 32),
            ("lease", "a" * 31),
            ("lease", []),
            ("error", "Bad error!"),
            ("error", ""),
            ("error", "a" * 33),
            ("error", []),
            ("gpu", []),
        ):
            self.assert_error("request", proto.parse_status, proto.dumps({**status_body(), field: value}))
        for field in ("name", "driver"):
            limit = 64 if field == "name" else 32
            for value in ("bad\n", "é", "\x7f", "x" * (limit + 1), None, 3):
                self.assert_error(
                    "request", proto.parse_status, proto.dumps({**status_body(), "gpu": {**gpu, field: value}})
                )
        self.assertEqual(
            proto.parse_status(proto.dumps({**status_body(), "gpu": {**gpu, "memory_mb": 9007199254740991}}))["gpu"][
                "memory_mb"
            ],
            9007199254740991,
        )
        for value in (True, -1, 1.5, None, "1024", 9007199254740992):
            self.assert_error(
                "request",
                proto.parse_status,
                json.dumps({**status_body(), "gpu": {**gpu, "memory_mb": value}}).encode(),
            )
        for obj in (status_body(), gpu):
            for field in obj:
                missing = {key: value for key, value in obj.items() if key != field}
                body = missing if obj is not gpu else {**status_body(), "gpu": missing}
                self.assert_error("request", proto.parse_status, proto.dumps(body))
            extra = {**obj, "extra": 1}
            body = extra if obj is not gpu else {**status_body(), "gpu": extra}
            self.assert_error("request", proto.parse_status, proto.dumps(body))

    def test_text_fields(self):
        self.assertEqual(proto.parse_text(proto.dumps({**text_body(), "offset": 12288}))["offset"], 12288)
        for changes in (
            {"done": True},
            {"finish": "stop"},
            {"done": True, "finish": "unknown"},
            {"offset": -1},
            {"offset": 1.0},
            {"offset": True},
            {"offset": "1"},
            {"offset": 12289},
            {"text": 1},
            {"text": None},
            {"done": 1},
            {"done": None},
            {"done": True, "finish": []},
            {"extra": 1},
        ):
            self.assert_error("request", proto.parse_text, json.dumps({**text_body(), **changes}).encode())
        for field in text_body():
            self.assert_error(
                "request",
                proto.parse_text,
                proto.dumps({key: value for key, value in text_body().items() if key != field}),
            )
        for finish in ("stop", "length"):
            parsed = proto.parse_text(proto.dumps({**text_body(), "done": True, "finish": finish}))
            self.assertEqual(parsed, {"offset": 0, "data": b"", "done": True, "finish": finish})

    def test_text_utf8_and_policy_boundary(self):
        for escape in (b"\\ud800", b"\\udfff", b"\\ud800a"):
            self.assert_error(
                "text", proto.parse_text, b'{"offset":0,"text":"' + escape + b'","done":false,"finish":null}'
            )
        for item in self.examples["text"]["accepted"]:
            raw = proto.dumps({**text_body(), "text": item["text"]})
            self.assertEqual(proto.parse_text(raw)["data"].hex(), item["hex"])
        paired = b'{"offset":0,"text":"\\ud83d\\ude42","done":false,"finish":null}'
        self.assertEqual(proto.parse_text(paired)["data"], "🙂".encode())
        # Control-character policy belongs to the answer store, not this parser.
        for item in self.examples["text"]["refused"]:
            data = bytes.fromhex(item["hex"])
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raw = b'{"offset":0,"text":"' + data + b'","done":false,"finish":null}'
                self.assert_error("request", proto.parse_text, raw)
            else:
                raw = json.dumps({**text_body(), "text": text}).encode("utf-8")
                self.assertEqual(proto.parse_text(raw)["data"], data)

    def test_dumps_text_validation(self):
        for char in [chr(value) for value in range(32) if value not in (9, 10)] + ["\ud800", "\udfff"]:
            for obj in ({"x": char}, {char: "x"}, {"x": [{"y": [char]}]}):
                with self.subTest(char=repr(char), obj=repr(obj)):
                    self.assert_error("text", proto.dumps, obj)
        self.assertEqual(proto.dumps({"\n\t": "\t\n"}), b'{"\\n\\t":"\\t\\n"}')
        self.assertEqual(proto.dumps({"数据": "é🙂"}), '{"数据":"é🙂"}'.encode())

    def test_dumps_value_validation(self):
        for value in (b"x", bytearray(b"x"), (1,), {1}, object(), float("nan"), float("inf")):
            for obj in (value, {"x": [value]}):
                with self.subTest(value=repr(value), obj=repr(obj)):
                    self.assert_error("request", proto.dumps, obj)
        self.assertEqual(proto.dumps(["x", 1, 1.5, True, False, None]), b'["x",1,1.5,true,false,null]')
        self.assertEqual(proto.dumps({"x": [{"y": [0]}]}), b'{"x":[{"y":[0]}]}')
        self.assert_error("request", proto.dumps, {"x": [{"y": [[0]]}]})
        cyclic = []
        cyclic.append(cyclic)
        self.assert_error("request", proto.dumps, cyclic)

    def test_dumps_number_written_form(self):
        for value in (
            -1,
            9007199254740992,
            12345678901234567890,
            -0.0,
            -1.0,
            1e-7,
            1e16,
            96.123456,
            100000.5,
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            for obj in (value, {"x": [value]}, {"x": {value: None}}):
                with self.subTest(value=value, obj=obj):
                    self.assert_error("request", proto.dumps, obj)
        self.assert_error("request", proto.dumps, {**status_body(), "tps": 96.123456})
        for value, written in (
            (0, b"0"),
            (9007199254740991, b"9007199254740991"),
            (0.0, b"0.0"),
            (0.001, b"0.001"),
            (96.123, b"96.123"),
            (100000.0, b"100000.0"),
        ):
            with self.subTest(value=value):
                raw = proto.dumps({"n": value})
                self.assertEqual(raw, b'{"n":' + written + b"}")
                self.assertEqual(proto.loads(raw, 100), {"n": value})
        raw = proto.dumps({**status_body(), "tps": 96.123})
        self.assertIn(b'"tps":96.123', raw)
        self.assertEqual(proto.parse_status(raw)["tps"], 96.123)

    def test_dumps_and_reply_builders(self):
        self.assertEqual(proto.dumps({"text": "数据 é", "value": 1}), '{"text":"数据 é","value":1}'.encode())
        self.assertEqual(
            proto.loads(proto.hello_body("OPOS phone", EPOCH), 1024),
            {"proto": 1, "label": "OPOS phone", "epoch": EPOCH.hex()},
        )
        for error in ("lease", "state", "text", "too_long", "rate", "range", "model", "request"):
            self.assertEqual(proto.loads(proto.error_reply(error), 1024), {"error": error})

    def test_work_reply_size_and_roundtrip(self):
        question = "数据?"
        partial = "é🙂"
        raw = proto.work_reply(LEASE, MODEL, question, partial)
        self.assertNotIn(b"\\u", raw)
        self.assertEqual(
            proto.loads(raw, 65536),
            {"lease": LEASE.hex(), "model_sha256": MODEL, "question": question, "partial": partial},
        )
        for index, limit in ((2, 4096), (3, 12288)):
            args = [LEASE, MODEL, "", ""]
            args[index] = "🙂" * (limit // 4)
            self.assertEqual(len(args[index].encode("utf-8")), limit)
            self.assertEqual(
                proto.loads(proto.work_reply(*args), 65536)[("question", "partial")[index - 2]], args[index]
            )
            args[index] += "a"
            self.assert_error("too_long", proto.work_reply, *args)
        self.assert_error("too_long", proto.work_reply, LEASE, MODEL, "a" * 32768, "")
        self.assert_error("too_long", proto.work_reply, LEASE, MODEL, "a" * 32769, "b" * 32768)
        self.assert_error("too_long", proto.work_reply, LEASE, MODEL, "🙂" * 8192, "🙂" * 8192)

    def test_work_reply_text_validation(self):
        for index in (2, 3):
            for char in [chr(value) for value in range(32) if value not in (9, 10)] + ["\ud800", "\udfff"]:
                with self.subTest(index=index, char=repr(char)):
                    args = [LEASE, MODEL, "", ""]
                    args[index] = char
                    self.assert_error("text", proto.work_reply, *args)
        raw = proto.work_reply(LEASE, MODEL, "\n\t", "\t\n")
        self.assertIn(b'"question":"\\n\\t"', raw)
        self.assertIn(b'"partial":"\\t\\n"', raw)
        self.assertNotIn(b"\\u", raw)

    def test_builder_argument_validation(self):
        for function, valid, invalid in (
            (
                proto.hello_body,
                ["Phone", EPOCH],
                {0: (None, [], "bad!", "é", ""), 1: (None, "", b"", bytearray(EPOCH), EPOCH + b"x")},
            ),
            (proto.status_reply, [LEASE], {0: (b"", LEASE + b"x", "", [], bytearray(LEASE))}),
            (
                proto.work_reply,
                [LEASE, MODEL, "", ""],
                {
                    0: (None, b"", LEASE + b"x", "", bytearray(LEASE)),
                    1: (None, MODEL.upper(), "a" * 63, "a" * 65, "g" * 64, MODEL.encode()),
                    2: (None, b"", 1, []),
                    3: (None, b"", 1, []),
                },
            ),
            (proto.text_reply, [0], {0: (-1, 12289, True, False, 1.0, "1", None, [])}),
            (proto.stored_reply, [0], {0: (-1, 12289, True, False, 1.0, "1", None, [])}),
            (proto.error_reply, ["request"], {0: ("Bad!", "", "unknown", None, [], b"request")}),
        ):
            for index, values in invalid.items():
                for value in values:
                    with self.subTest(function=function.__name__, index=index, value=value):
                        args = valid.copy()
                        args[index] = value
                        self.assert_error("request", function, *args)
        for function, field in ((proto.text_reply, "acked"), (proto.stored_reply, "stored")):
            for value in (0, 12288):
                self.assertEqual(json.loads(function(value)), {field: value})
        self.assertEqual(json.loads(proto.status_reply(None)), {"cancel": None})

    def test_number_tokens(self):
        for token in (b"-0", b"-1", b"1e2", b"1E2", b"01", b"10000000000000000", b"9007199254740992", b"9" * 4000):
            with self.subTest(token=token[:30]):
                raw = b'{"n":' + token + b"}"
                self.assert_error("request", proto.loads, raw, len(raw))
        for field in ("copied", "total", "tokens", "memory_mb", "offset"):
            for token in (b"9007199254740991", b"9007199254740992", b"1.0", b"9" * 4000):
                obj = status_body()
                parser = proto.parse_status
                if field == "memory_mb":
                    obj["gpu"] = {"name": "GPU", "driver": "1", "memory_mb": "TOKEN"}
                elif field == "offset":
                    obj = {**text_body(), "offset": "TOKEN"}
                    parser = proto.parse_text
                else:
                    obj[field] = "TOKEN"
                    if field in ("copied", "total"):
                        obj["total" if field == "copied" else "copied"] = 9007199254740991
                raw = proto.dumps(obj).replace(b'"TOKEN"', token)
                with self.subTest(field=field, token=token[:30]):
                    if token == b"9007199254740991" and field != "offset":
                        parser(raw)
                    else:
                        # The status size gate precedes token validation for 4,000 digits.
                        self.assert_error(
                            "too_long" if len(raw) > 2048 and parser is proto.parse_status else "request", parser, raw
                        )
        self.assertEqual(proto.loads(b'{"n":9007199254740991}', 100), {"n": 9007199254740991})
        for token, accepted in (
            (b"96.4", True),
            (b"96", True),
            (b"0", True),
            (b"100000", True),
            (b"100000.001", False),
            (b"96.1234", False),
            (b"1e-324", False),
            (b"9.6e1", False),
        ):
            raw = proto.dumps({**status_body(), "tps": "TOKEN"}).replace(b'"TOKEN"', token)
            with self.subTest(tps=token):
                if accepted:
                    self.assertEqual(proto.parse_status(raw)["tps"], json.loads(token))
                else:
                    self.assert_error("request", proto.parse_status, raw)

    def check_json_types(self, result):
        self.assertIs(type(result), dict)
        pending = [(result, 1)]
        while pending:
            value, depth = pending.pop()
            self.assertIn(type(value), (dict, list, str, int, float, bool, type(None)))
            if type(value) in (dict, list):
                self.assertLessEqual(depth, 4)
                if type(value) is dict:
                    self.assertTrue(all(type(key) is str for key in value))
                    children = value.values()
                else:
                    children = value
                pending.extend((child, depth + 1) for child in children)
            elif type(value) is int:
                self.assertTrue(0 <= value <= 9007199254740991)
            elif type(value) is float:
                self.assertTrue(0 <= value <= 100000)

    def check_body_types(self, parser, result):
        # These checks use no production validators or constants.
        if parser is proto.parse_enrol:
            self.assertIs(type(result), tuple)
            self.assertEqual(len(result), 2)
            label, key = result
            self.assertIs(type(label), str)
            self.assertRegex(label, r"\A[A-Za-z0-9](?:[A-Za-z0-9 -]{0,22}[A-Za-z0-9])?\Z")
            self.assertIs(type(key), bytes)
            self.assertEqual(len(key), 32)
            return
        self.assertIs(type(result), dict)
        if parser is proto.parse_text:
            self.assertEqual(set(result), {"offset", "data", "done", "finish"})
            self.assertIs(type(result["offset"]), int)
            self.assertTrue(0 <= result["offset"] <= 12288)
            self.assertIs(type(result["data"]), bytes)
            result["data"].decode("utf-8")
            self.assertIs(type(result["done"]), bool)
            self.assertIn(result["finish"], ("stop", "length") if result["done"] else (None,))
            return
        self.assertEqual(set(result), {"state", "lease", "copied", "total", "gpu", "tokens", "tps", "error"})
        self.assertIs(type(result["state"]), str)
        self.assertIn(result["state"], ("idle", "copying", "loading", "ready", "busy", "stopping", "error"))
        if result["lease"] is not None:
            self.assertIs(type(result["lease"]), bytes)
            self.assertEqual(len(result["lease"]), 16)
        for field in ("copied", "total", "tokens"):
            if result[field] is not None:
                self.assertIs(type(result[field]), int)
                self.assertTrue(0 <= result[field] <= 9007199254740991)
        if result["tps"] is not None:
            self.assertIn(type(result["tps"]), (int, float))
            self.assertTrue(0 <= result["tps"] <= 100000)
        if result["error"] is not None:
            self.assertIs(type(result["error"]), str)
            self.assertRegex(result["error"], r"\A[a-z0-9_-]{1,32}\Z")
        gpu = result["gpu"]
        if gpu is not None:
            self.assertIs(type(gpu), dict)
            self.assertEqual(set(gpu), {"name", "memory_mb", "driver"})
            self.assertIs(type(gpu["memory_mb"]), int)
            self.assertTrue(0 <= gpu["memory_mb"] <= 9007199254740991)
            for field, limit in (("name", 64), ("driver", 32)):
                self.assertIs(type(gpu[field]), str)
                self.assertLessEqual(len(gpu[field]), limit)
                self.assertTrue(all(32 <= ord(char) <= 126 for char in gpu[field]))

    def check_target_types(self, method, value, result):
        self.assertIs(type(result), tuple)
        self.assertTrue(result)
        self.assertIs(type(result[0]), str)
        if result[0] in ("hello", "enrol", "status"):
            self.assertEqual(result, (result[0],))
            self.assertEqual(value, "/v1/" + result[0])
            self.assertEqual(method, "GET" if result[0] == "hello" else "POST")
        elif result[0] == "work":
            self.assertEqual(len(result), 2)
            self.assertIs(type(result[1]), int)
            self.assertTrue(0 <= result[1] <= 20)
            self.assertEqual((method, value), ("GET", f"/v1/work?wait={result[1]}"))
        elif result[0] == "text":
            self.assertEqual(len(result), 2)
            self.assertIs(type(result[1]), bytes)
            self.assertEqual(len(result[1]), 16)
            self.assertEqual((method, value), ("POST", f"/v1/runs/{result[1].hex()}/text"))
        else:
            self.assertEqual(result[0], "model")
            self.assertEqual(len(result), 2)
            self.assertIs(type(result[1]), str)
            self.assertRegex(result[1], r"\A[0-9a-f]{64}\Z")
            self.assertEqual((method, value), ("GET", f"/v1/models/{result[1]}"))

    def test_fuzz_all_parsers(self):
        rng = random.Random(119)
        bodies = [bytes.fromhex(item["body_hex"]) for item in self.examples["enrol"] + self.examples["requests"]]
        bodies += [bytes.fromhex(item["body_hex"]) for item in self.examples["replies"] if item["body_hex"] is not None]
        parsers = (proto.parse_enrol, proto.parse_status, proto.parse_text)
        counts = {parser: [0, 0] for parser in (*parsers, proto.loads, proto.parse_target, proto.parse_range)}
        codes = {"request", "too_long", "text", "range"}
        # Seed valid and invalid inputs so every parser must take both paths.
        seeds = bodies + [b"\xff"]
        seeds += [item["target"].encode("ascii") for item in self.examples["targets"]["valid"]]
        seeds += [item["header"].encode("ascii") for item in self.examples["ranges"]["valid"]]
        for index in range(len(seeds) + 6000):
            if index < len(seeds):
                raw = seeds[index]
            elif index % 2:
                raw = rng.randbytes(rng.randrange(300))
            else:
                raw = fuzz_mutation(rng, rng.choice(bodies))
            for parser in parsers:
                try:
                    result = parser(raw)
                except proto.ProtoError as err:
                    counts[parser][1] += 1
                    self.assertIn(err.code, codes)
                else:
                    counts[parser][0] += 1
                    self.check_body_types(parser, result)
            try:
                result = proto.loads(raw, 8192)
            except proto.ProtoError as err:
                counts[proto.loads][1] += 1
                self.assertIn(err.code, codes)
            else:
                counts[proto.loads][0] += 1
                self.check_json_types(result)
            # Exercise string grammars too, including arbitrary non-ASCII bytes.
            value = raw.decode("latin-1")
            for method in ("GET", "POST"):
                try:
                    result = proto.parse_target(method, value)
                except proto.ProtoError as err:
                    counts[proto.parse_target][1] += 1
                    self.assertEqual(err.code, "request")
                else:
                    counts[proto.parse_target][0] += 1
                    self.check_target_types(method, value, result)
            try:
                result = proto.parse_range(value, 2019377696)
            except proto.ProtoError as err:
                counts[proto.parse_range][1] += 1
                self.assertEqual(err.code, "range")
            else:
                counts[proto.parse_range][0] += 1
                self.assertIs(type(result), tuple)
                self.assertEqual(len(result), 2)
                self.assertTrue(all(type(part) is int for part in result))
                start, end = result
                self.assertTrue(0 <= start <= end < 2019377696)
                match = re.fullmatch(r"bytes=(0|[1-9][0-9]*)-(0|[1-9][0-9]*)", value)
                self.assertIsNotNone(match)
                self.assertEqual(result, (int(match[1]), int(match[2])))
        for parser, (accepted, refused) in counts.items():
            with self.subTest(parser=parser.__name__):
                self.assertGreater(accepted, 0)
                self.assertGreater(refused, 0)

    def test_imports_are_pure_standard_library(self):
        source = Path(proto.__file__).read_text(encoding="utf-8")
        imports = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertEqual(set(imports), {"__future__", "hashlib", "hmac", "json", "math", "re"})


if __name__ == "__main__":
    unittest.main()
