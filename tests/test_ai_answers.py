# SPDX-License-Identifier: GPL-3.0-or-later
"""Answer lifecycle, durable byte seams, recovery, and concurrent snapshots."""

import contextlib
import errno
import io
import json
import os
import random
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from opos import ai_answers as answers
from opos import gpu_link_server
from opos.store import Store

ROOT = Path(__file__).resolve().parents[1]
REFUSED = "".join(chr(value) for value in (*range(32), *range(127, 160)) if value not in (9, 10))


class FailingStore(Store):
    """Record completed writes and optionally refuse a chosen write."""

    def __init__(self, root):
        super().__init__(root)
        self.remaining = None
        self.persistent = False
        self.writes = []
        self.events = []
        self.after_write = None

    def write_json(self, name, payload, limit=256 * 1024, *, durable=False):
        if self.remaining is not None:
            self.remaining -= 1
            if self.remaining <= 0:
                if not self.persistent:
                    self.remaining = None
                raise OSError("write refused")
        result = super().write_json(name, payload, limit, durable=durable)
        self.writes.append(name)
        self.events.append("write finished")
        if self.after_write is not None:
            self.after_write(name, payload)
        return result


class UnreadableStore(Store):
    """Raise a read error for one selected name, as Store does for I/O faults."""

    def __init__(self, root, unreadable):
        super().__init__(root)
        self.unreadable = unreadable

    def read_json(self, name, default, **kwargs):
        if name == self.unreadable:
            raise PermissionError("read denied")
        return super().read_json(name, default, **kwargs)


class AnswerTests(unittest.TestCase):
    """Exercise answer behaviour, storage failures, and restart normalisation."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.disk = FailingStore(self.root)
        self.store = answers.AnswerStore(self.disk)

    def files(self):
        # Compare answer files; Store keeps lock files of its own.
        return {path.name: path.read_bytes() for path in self.root.glob("ai-answer-*.json") if path.is_file()}

    def unchanged(self, exception, function, *args, **kwargs):
        snapshot, files = self.store.snapshot(), self.files()
        with self.assertRaises(exception) as caught:
            function(*args, **kwargs)
        self.assertEqual(snapshot, self.store.snapshot())
        self.assertEqual(files, self.files())
        return caught.exception

    def make_state(self, state, pane="gpu"):
        opened = self.store.ask(pane, "question")
        ident = opened["id"]
        if state == "queued":
            return self.store.ask(pane, "queued")["id"]
        if state in ("loading", "answering", "paused", "done"):
            self.store.begin(ident)
        if state in ("answering", "paused"):
            self.store.append(ident, 0, b"prefix")
        if state == "paused":
            self.store.pause(ident)
        if state == "done":
            self.store.append(ident, 0, b"", done=True, finish="stop")
        if state == "failed":
            self.store.fail(ident, "failed")
        if state == "cancelled":
            self.store.cancel(ident)
        return ident

    def empty_store(self):
        for path in self.root.iterdir():
            path.unlink()
        self.store = answers.AnswerStore(self.disk)

    def test_ask_validation_and_independent_panes(self):
        self.unchanged(ValueError, self.store.ask, "other", "question")
        for text in ("", "bad\r", "bad\ud800", "bad\x85"):
            self.unchanged(answers.TextError, self.store.ask, "phone", text)
        self.unchanged(answers.TextError, self.store.ask, "phone", None)
        for question in ("x" * 4097, "é" * 2048 + "x"):
            self.unchanged(answers.TooLongError, self.store.ask, "phone", question)
        phone = self.store.ask("phone", "é" * 2048)
        gpu = self.store.ask("gpu", "x" * 4096, "large")
        for row in (phone, gpu):
            self.assertEqual(row["state"], "waiting")
            self.assertRegex(row["id"], r"^[a-f0-9]{12}$")
            self.assertEqual(row["bytes"], 0)
            self.assertEqual(row["text"], "")
            self.assertIs(type(row["asked"]), int)
            self.assertGreater(row["asked"], 0)
            self.assertEqual(self.store.current(row["pane"])["id"], row["id"])
            self.assertEqual(self.store.ask(row["pane"], "next")["state"], "queued")
        self.assertEqual(self.store.snapshot()["answers"][0]["question"], "next")
        self.assertEqual(gpu["model"], "large")
        self.unchanged(ValueError, self.store.current, "bad")

    def test_transition_matrix_and_disk_immutability(self):
        legal = {
            "begin": {"waiting": "loading", "paused": "loading"},
            "pause": {"loading": "paused", "answering": "paused"},
            "cancel": dict.fromkeys(set(answers.STATES) - set(answers.FINAL), "cancelled"),
            "fail": dict.fromkeys(set(answers.STATES) - set(answers.FINAL), "failed"),
            "append": {"loading": "answering", "answering": "answering"},
            "done": {"loading": "done", "answering": "done"},
        }
        for method, transitions in legal.items():
            for state in answers.STATES:
                with self.subTest(method=method, state=state):
                    self.empty_store()
                    ident = self.make_state(state)
                    before = self.store.get(ident)
                    if method in ("append", "done"):
                        function = self.store.append
                        args = (ident, before["bytes"], b"new")
                        kwargs = {"done": True, "finish": "length"} if method == "done" else {}
                    else:
                        function = getattr(self.store, method)
                        args = (ident, "broken") if method == "fail" else (ident,)
                        kwargs = {}
                    if state not in transitions:
                        self.unchanged(answers.StateError, function, *args, **kwargs)
                    else:
                        rev = self.store.rev
                        result = function(*args, **kwargs)
                        self.assertEqual(result["state"], transitions[state])
                        self.assertEqual(result["rev"], rev + 1)
                        self.assertEqual(self.store.rev, rev + 1)
                        if method in ("pause", "begin", "cancel", "fail"):
                            self.assertEqual(result["text"], before["text"])
        for method in ("begin", "pause", "cancel", "fail", "append", "note"):
            args = ("absent",)
            if method == "fail":
                args += ("broken",)
            if method == "append":
                args += (0, b"text")
            self.unchanged(answers.StateError, getattr(self.store, method), *args)
        self.assertIsNone(self.store.get("absent"))

    def test_final_promotes_oldest_in_one_revision(self):
        for action in ("done", "cancel", "fail"):
            with self.subTest(action=action):
                self.empty_store()
                ident = self.make_state("answering")
                oldest = self.store.ask("gpu", "oldest")["id"]
                newer = self.store.ask("gpu", "newer")["id"]
                phone = self.store.ask("phone", "independent")["id"]
                rev = self.store.rev
                self.disk.writes.clear()
                if action == "done":
                    self.store.append(ident, 6, b"end", True, "stop")
                elif action == "fail":
                    self.store.fail(ident, "broken")
                else:
                    self.store.cancel(ident)
                self.assertEqual(self.disk.writes, [f"ai-answer-{ident}.json"])
                self.assertEqual(self.store.rev, rev + 1)
                self.assertEqual(self.store.get(ident)["rev"], rev + 1)
                self.assertEqual(self.store.get(oldest)["state"], "waiting")
                self.assertLess(self.store.get(oldest)["rev"], rev + 1)
                self.assertEqual(self.store.get(newer)["state"], "queued")
                self.assertEqual(self.store.current("phone")["id"], phone)
                snapshot = self.store.snapshot()
                self.assertEqual(snapshot["rev"], rev + 1)
                self.assertEqual(next(row for row in snapshot["answers"] if row["id"] == oldest)["state"], "waiting")
                record, _ = self.disk.read_json(f"ai-answer-{oldest}.json", {})
                self.assertEqual(record["answer"]["state"], "queued")
                recovered = answers.AnswerStore(self.disk)
                phone_row = next(row for row in snapshot["answers"] if row["id"] == phone)
                phone_row.update(state="failed", error="restart", rev=snapshot["rev"] + 1)
                snapshot["rev"] += 1
                self.assertEqual(recovered.snapshot(), snapshot)
                self.assertEqual(self.disk.writes, [f"ai-answer-{ident}.json", f"ai-answer-{phone}.json"])
                self.assertEqual(recovered.begin(oldest)["state"], "loading")

    def test_queued_phone_answer_fails_on_restart_but_gpu_stays_queued(self):
        phone = self.store.ask("phone", "q")["id"]
        gpu = self.store.ask("gpu", "q")["id"]
        recovered = answers.AnswerStore(self.disk)
        row = recovered.get(phone)
        self.assertEqual((row["state"], row["error"]), ("failed", "restart"))
        self.assertIsNone(recovered.current("phone"))
        self.assertEqual(recovered.ask("phone", "next")["state"], "waiting")
        self.assertEqual(recovered.current("gpu")["id"], gpu)
        self.assertEqual(recovered.get(gpu)["state"], "waiting")
        saved, _ = self.disk.read_json(f"ai-answer-{phone}.json", {})
        self.assertEqual(saved["answer"]["state"], "failed")

    def test_appending_offset_and_repeats(self):
        ident = self.make_state("loading")
        result = self.store.append(ident, 0, "éabc".encode())
        self.assertEqual(result["state"], "answering")
        self.assertEqual(result["bytes"], 5)
        self.assertEqual(result["text"], "éabc")
        before, files = self.store.snapshot(), self.files()
        self.assertEqual(self.store.append(ident, 2, b"ab"), result)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.files(), files)
        for offset, raw in ((1, b"x"), (2, b"ax"), (3, b"bcde"), (6, b"x"), (-1, b"x"), (True, b"x")):
            error = self.unchanged(answers.OffsetError, self.store.append, ident, offset, raw)
            self.assertEqual(error.stored, 5)
            self.assertIn("5", str(error))
        error = self.unchanged(answers.OffsetError, self.store.append, ident, 2, b"ab", True, "stop")
        self.assertEqual(error.stored, 5)
        self.assertEqual(self.store.append(ident, 5, b"!")["text"], "éabc!")

    def test_text_refused_whole_and_before_offsets(self):
        ident = self.make_state("loading")
        invalid = [b"\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"\xe2\x82", "é".encode()[:1], "x", bytearray(b"x")]
        invalid.extend(char.encode() for char in REFUSED)
        for raw in invalid:
            with self.subTest(raw=raw):
                self.unchanged(answers.TextError, self.store.append, ident, 999, raw)
                self.unchanged(answers.TextError, self.store.append, ident, 0, raw)
        for offset in (0, 9):
            self.unchanged(answers.TextError, self.store.append, ident, offset, b"")
        self.assertEqual(self.store.append(ident, 0, b"\t\n")["text"], "\t\n")

    def test_exact_answer_limit_and_overflow(self):
        ident = self.make_state("loading")
        self.assertEqual(self.store.append(ident, 0, b"x" * 12288)["bytes"], 12288)
        error = self.unchanged(answers.TooLongError, self.store.append, ident, 12288, b"x")
        self.assertEqual(error.stored, 12288)
        self.store.cancel(ident)
        ident = self.make_state("loading")
        error = self.unchanged(answers.TooLongError, self.store.append, ident, 0, b"x" * 12289)
        self.assertEqual(error.stored, 0)
        self.assertEqual(self.store.append(ident, 0, "é".encode() * 6144, True, "length")["bytes"], 12288)

    def test_finish_values_empty_final_and_exact_retry(self):
        for finish in ("stop", "length"):
            for prefix in (b"", "earlier é".encode()):
                with self.subTest(finish=finish, prefix=prefix):
                    self.empty_store()
                    ident = self.make_state("loading")
                    if prefix:
                        self.store.append(ident, 0, prefix)
                    for invalid in (None, "bad", ""):
                        self.unchanged(answers.StateError, self.store.append, ident, len(prefix), b"", True, invalid)
                    self.unchanged(answers.StateError, self.store.append, ident, len(prefix), b"x", False, "stop")
                    self.unchanged(answers.StateError, self.store.append, ident, len(prefix), b"x", 1, "stop")
                    result = self.store.append(ident, len(prefix), b"", True, finish)
                    self.assertEqual(result["state"], "done")
                    self.assertEqual(result["text"], prefix.decode())
                    self.assertEqual(result["finish"], finish)
                    before, files = self.store.snapshot(), self.files()
                    self.assertEqual(self.store.append(ident, len(prefix), b"", True, finish), result)
                    self.assertEqual(before, self.store.snapshot())
                    self.assertEqual(files, self.files())
                    for offset, raw, done, value in (
                        (len(prefix), b"new", True, finish),
                        (len(prefix), b"", False, finish),
                        (len(prefix), b"", True, "length" if finish == "stop" else "stop"),
                        (len(prefix) + 1, b"", True, finish),
                    ):
                        self.unchanged(answers.StateError, self.store.append, ident, offset, raw, done, value)
                    resumed = answers.AnswerStore(Store(self.root))
                    self.assertEqual(resumed.append(ident, len(prefix), b"", True, finish), result)
        self.empty_store()
        ident = self.make_state("loading")
        final = self.store.append(ident, 0, "final 🙂".encode(), True, "stop")
        rev = self.store.rev
        resumed = answers.AnswerStore(Store(self.root))
        self.assertEqual(resumed.append(ident, 0, "final 🙂".encode(), True, "stop"), final)
        self.assertEqual(resumed.rev, rev)
        self.unchanged(answers.StateError, self.store.append, ident, 0, "final 🙃".encode(), True, "stop")

    def test_fixture_text_and_clean(self):
        fixture = json.loads((ROOT / "tests/fixtures/gpu_link/examples.json").read_text(encoding="utf-8"))["text"]
        for entry in fixture["accepted"]:
            self.assertTrue(answers.text_ok(bytes.fromhex(entry["hex"])), entry)
        for entry in fixture["refused"]:
            self.assertFalse(answers.text_ok(bytes.fromhex(entry["hex"])), entry)
        self.assertFalse(answers.text_ok("text"))
        self.assertEqual(answers.clean(REFUSED + "\ud800\udfff"), "")
        self.assertEqual(answers.clean("one\r\ntwo\rthree\t🙂"), "one\ntwothree\t🙂")
        rng = random.Random(120)
        for _ in range(200):
            text = "".join(chr(rng.randrange(0x110000)) for _ in range(50))
            result = answers.clean(text)
            self.assertTrue(answers.text_ok(result.encode()))
            self.assertEqual(answers.clean(result), result)

    def test_random_byte_seams(self):
        raw = ("Aé数据🙂\t\n" * 12).encode()
        rng = random.Random(120_4096)
        cuts = [0, len(raw)] + [rng.randrange(len(raw) + 1) for _ in range(320)]
        accepted = refused = 0
        for cut in cuts:
            with self.subTest(cut=cut):
                ident = self.make_state("loading")
                if not answers.text_ok(raw[:cut]):
                    refused += 1
                    self.unchanged(answers.TextError, self.store.append, ident, 0, raw[:cut])
                    self.store.cancel(ident)
                    continue
                accepted += 1
                if cut:
                    self.store.append(ident, 0, raw[:cut])
                old = self.store.get(ident)["text"].encode()
                self.store.pause(ident)
                self.store.begin(ident)
                stored = self.store.get(ident)["bytes"]
                result = self.store.append(ident, stored, raw[stored:], True, "stop")
                self.assertTrue(result["text"].encode().startswith(old))
                self.assertEqual(result["text"].encode(), raw)
        self.assertGreater(accepted, 50)
        self.assertGreater(refused, 50)

    def test_restart_recovers_both_panes_and_promotes_phone(self):
        for state in ("loading", "answering"):
            with self.subTest(state=state):
                self.empty_store()
                gpu = self.make_state(state, "gpu")
                phone = self.make_state(state, "phone")
                queued = self.store.ask("phone", "next")["id"]
                self.store.note(gpu, model="large", where="PC", tokens=3, tps=2.5)
                before = self.store.snapshot()
                restarted = answers.AnswerStore(Store(self.root))
                self.assertGreater(restarted.rev, before["rev"])
                self.assertEqual(restarted.get(gpu)["state"], "paused")
                self.assertEqual(restarted.get(phone)["state"], "failed")
                self.assertEqual(restarted.get(phone)["error"], "restart")
                self.assertEqual(restarted.get(queued)["state"], "failed")
                for row in before["answers"]:
                    after = restarted.get(row["id"])
                    for key in ("id", "question", "text", "bytes", "model", "where", "tokens", "tps", "asked"):
                        self.assertEqual(after[key], row[key])
                again = answers.AnswerStore(Store(self.root))
                self.assertEqual(again.snapshot(), restarted.snapshot())

    def test_restart_between_post_and_acknowledgement(self):
        ident = self.make_state("loading")
        self.store.append(ident, 0, "saved é🙂".encode())
        restarted = answers.AnswerStore(Store(self.root))
        partial = restarted.get(ident)
        self.assertEqual(partial["text"], "saved é🙂")
        restarted.begin(ident)
        rev = restarted.rev
        restarted.append(ident, 0, "saved é🙂".encode())
        self.assertEqual(restarted.rev, rev)
        final = restarted.append(ident, partial["bytes"], b"!", True, "stop")
        self.assertEqual(final["text"], "saved é🙂!")

    def test_limits_remove_oldest_finished_file(self):
        unfinished = self.make_state("waiting", "phone")
        oldest = self.make_state("cancelled", "gpu")
        later = self.make_state("failed", "gpu")
        for _ in range(47):
            self.store.ask("phone", "queued")
        self.assertEqual(len(self.store.snapshot()["answers"]), 50)
        self.store.ask("gpu", "new")
        self.assertIsNone(self.store.get(oldest))
        self.assertFalse((self.root / f"ai-answer-{oldest}.json").exists())
        self.assertIsNotNone(self.store.get(later))
        self.assertIsNotNone(self.store.get(unfinished))
        self.store.ask("phone", "newer")
        self.assertIsNone(self.store.get(later))
        self.unchanged(answers.FullError, self.store.ask, "gpu", "full")
        self.assertEqual(len(list(self.root.glob("ai-answer-*.json"))), 50)
        recovered = answers.AnswerStore(Store(self.root))
        self.assertEqual(len(recovered.snapshot()["answers"]), 50)
        self.assertTrue(
            all(row["state"] == "failed" for row in recovered.snapshot()["answers"] if row["pane"] == "phone")
        )

    def test_killed_write_temp_is_swept_on_load(self):
        ident = self.make_state("loading")
        code = """
import os
import sys
from unittest.mock import patch
from opos.ai_answers import AnswerStore
from opos.store import Store
answers = AnswerStore(Store(sys.argv[1]))
answers.begin(sys.argv[2])
with patch('opos.store.os.replace', side_effect=lambda *args: os._exit(9)):
    answers.append(sys.argv[2], 0, b'partial from child')
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.root), ident], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 9)
        self.assertEqual((result.stdout, result.stderr), (b"", b""))
        leftovers = list(self.root.glob(".ai-answer-*.json.*.tmp"))
        self.assertEqual(len(leftovers), 1)
        self.assertTrue(b'"question":"question"' in leftovers[0].read_bytes())
        self.assertTrue(b"partial from child" in leftovers[0].read_bytes())
        recovered = answers.AnswerStore(self.disk)
        self.assertEqual(recovered.get(ident)["state"], "paused")
        self.assertFalse(list(self.root.glob(".ai-answer-*.json.*.tmp")))

    def test_sweep_failure_loads_answer_and_retries_after_save(self):
        ident = self.make_state("loading")
        temporary = self.root / f".ai-answer-{ident}.json.stale.tmp"
        temporary.write_text("leftover", encoding="ascii")
        unlink = Path.unlink

        def blocked(path, *args, **kwargs):
            if path == temporary:
                raise PermissionError("unlink refused")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", blocked):
            recovered = answers.AnswerStore(self.disk)
            self.assertEqual(recovered.get(ident)["state"], "paused")
            self.assertTrue(recovered._sweep_pending)
        recovered.note(ident, tokens=1)
        self.assertFalse(temporary.exists())
        self.assertFalse(recovered._sweep_pending)

    def test_removed_answer_leaves_no_temp(self):
        ident = self.make_state("done")
        (self.root / f".ai-answer-{ident}.json.stale.tmp").write_bytes(b"partial")
        for _ in range(answers.MAX_ANSWERS):
            self.store.ask("gpu", "next")
        self.assertIsNone(self.store.get(ident))
        self.assertFalse(any(ident in path.name for path in self.root.iterdir()))

    def test_finished_answers_do_not_accumulate_lock_files(self):
        for number in range(60):
            ident = self.store.ask("gpu", f"question {number}")["id"]
            self.store.begin(ident)
            self.store.append(ident, 0, b"finished", True, "stop")
        files = list(self.root.glob("ai-answer-*.json"))
        locks = list(self.root.glob(".ai-answer-*.json.lock"))
        self.assertEqual(len(files), 50)
        self.assertLessEqual(len(locks), 50)
        self.assertEqual({path.name for path in locks}, {f".{path.name}.lock" for path in files})

    def test_failed_file_or_lock_cleanup_is_retried_by_later_ask(self):
        for refused in ("answer", "lock", "both"):
            with self.subTest(refused=refused):
                self.empty_store()
                oldest = self.make_state("done")
                # The dropped answer was the latest in its pane. No reader may
                # fall back to it, even while its file is still on disk.
                for _ in range(49):
                    self.store.ask("phone", "queue")
                answer = self.root / f"ai-answer-{oldest}.json"
                lock = self.root / f".{answer.name}.lock"
                self.assertTrue(lock.exists())
                original = Path.unlink
                attempted = []

                def unlink(
                    path,
                    *args,
                    answer=answer,
                    lock=lock,
                    attempted=attempted,
                    refused=refused,
                    original=original,
                    **kwargs,
                ):
                    if path in (answer, lock):
                        attempted.append(path)
                    if (path == answer and refused in ("answer", "both")) or (
                        path == lock and refused in ("lock", "both")
                    ):
                        raise PermissionError("cleanup refused")
                    return original(path, *args, **kwargs)

                self.disk.writes.clear()
                with patch.object(Path, "unlink", unlink):
                    row = self.store.ask("phone", "new")
                self.assertEqual(attempted, [answer, lock])
                self.assertEqual(self.disk.writes, [f"ai-answer-{row['id']}.json"])
                self.assertEqual(len(self.store.snapshot()["answers"]), 50)
                self.assertIsNone(self.store.get(oldest))
                self.assertIsNone(self.store.latest("gpu"))
                self.assertNotIn(oldest, [row["id"] for row in self.store.snapshot()["answers"]])
                self.assertEqual(self.store.get(row["id"]), row)
                self.assertEqual(answer.exists(), refused in ("answer", "both"))
                self.assertEqual(lock.exists(), refused in ("lock", "both"))
                # Retry also runs when this ask must refuse the full history.
                self.disk.writes.clear()
                before = self.store.snapshot()
                with self.assertRaises(answers.FullError):
                    self.store.ask("gpu", "full")
                self.assertEqual(self.store.snapshot(), before)
                self.assertEqual(self.disk.writes, [])
                self.assertFalse(answer.exists())
                self.assertFalse(lock.exists())
                with patch.object(Path, "unlink", wraps=original) as removal:
                    self.unchanged(answers.FullError, self.store.ask, "gpu", "full again")
                removal.assert_not_called()

    def test_oversized_store_evicts_until_below_limit_before_asking(self):
        template_id = self.make_state("done")
        template, _ = self.disk.read_json(f"ai-answer-{template_id}.json", {})
        self.empty_store()
        ids = [f"{number:012x}" for number in range(53)]
        for number, ident in enumerate(ids):
            record = {
                **template,
                "seq": number,
                "answer": {**template["answer"], "id": ident, "rev": number},
            }
            (self.root / f"ai-answer-{ident}.json").write_text(json.dumps(record), encoding="utf-8")
        recovered = answers.AnswerStore(self.disk)
        self.assertEqual(len(recovered.snapshot()["answers"]), 53)
        self.disk.writes.clear()
        new = recovered.ask("gpu", "new")
        self.assertEqual(len(recovered.snapshot()["answers"]), 50)
        self.assertEqual(len(self.files()), 50)
        self.assertEqual(self.disk.writes, [f"ai-answer-{new['id']}.json"])
        for ident in ids[:4]:
            self.assertIsNone(recovered.get(ident))
            self.assertFalse((self.root / f"ai-answer-{ident}.json").exists())
        for ident in ids[4:]:
            self.assertIsNotNone(recovered.get(ident))

    def test_full_unfinished_history_writes_nothing(self):
        for number in range(50):
            self.store.ask(answers.PANES[number % 2], "unfinished")
        self.disk.writes.clear()
        self.unchanged(answers.FullError, self.store.ask, "gpu", "full")
        self.assertEqual(self.disk.writes, [])

    def test_restart_with_leftover_final_file_and_fifty_unfinished_answers(self):
        oldest = self.make_state("done")
        for _ in range(49):
            self.store.ask("phone", "queue")
        path = self.root / f"ai-answer-{oldest}.json"
        original = Path.unlink

        def unlink(target, *args, **kwargs):
            if target == path:
                raise PermissionError("cleanup refused")
            return original(target, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            self.store.ask("phone", "accepted")
        self.assertEqual(len(self.store.snapshot()["answers"]), 50)
        recovered = answers.AnswerStore(self.disk)
        self.assertEqual(len(recovered.snapshot()["answers"]), 51)
        self.assertEqual(recovered.get(oldest)["state"], "done")
        self.disk.writes.clear()
        self.assertTrue(
            all(row["state"] == "failed" for row in recovered.snapshot()["answers"] if row["pane"] == "phone")
        )
        self.assertEqual(recovered.ask("phone", "after restart")["state"], "waiting")
        self.assertIsNone(recovered.get(oldest))
        self.assertFalse(path.exists())
        self.assertEqual(len(self.files()), 50)

    def test_each_change_writes_once_and_failed_write_can_be_retried(self):
        for action in ("ask", "append", "note", "begin", "pause", "cancel", "fail", "done"):
            with self.subTest(action=action):
                self.empty_store()
                ident = self.make_state("waiting" if action == "begin" else "answering")
                self.store.ask("gpu", "next")
                if action == "ask":
                    function, args, kwargs = self.store.ask, ("phone", "new"), {}
                elif action in ("append", "done"):
                    function, args = self.store.append, (ident, 6, b"new")
                    kwargs = {"done": True, "finish": "stop"} if action == "done" else {}
                elif action == "note":
                    function, args, kwargs = self.store.note, (ident,), {"tokens": 1}
                else:
                    function, args, kwargs = getattr(self.store, action), (ident,), {}
                    if action == "fail":
                        args += ("broken",)
                before = self.store.snapshot()
                files = {path.name: path.read_bytes() for path in self.root.iterdir()}
                self.disk.writes.clear()
                self.disk.remaining = 1
                self.unchanged(OSError, function, *args, **kwargs)
                self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, files)
                self.assertEqual(self.disk.writes, [])
                # Any second write would fail. The exact same call now succeeds.
                self.disk.remaining = 2
                result = function(*args, **kwargs)
                self.assertEqual(self.disk.remaining, 1)
                self.disk.remaining = None
                self.assertEqual(self.disk.writes, [f"ai-answer-{result['id']}.json"])
                self.assertEqual(self.store.rev, before["rev"] + 1)
                self.assertEqual(result["rev"], self.store.rev)
                self.assertEqual(self.store.get(result["id"]), result)
                record, _ = self.disk.read_json(self.disk.writes[0], {})
                self.assertEqual(record["answer"]["rev"], self.store.rev)
                self.assertEqual(record["answer"]["state"], "queued" if action == "ask" else result["state"])
        self.empty_store()
        ident = self.make_state("waiting")
        self.disk.remaining = 1
        self.disk.persistent = True
        self.unchanged(OSError, self.store.begin, ident)

    def test_failed_write_with_matching_disk_record_keeps_memory(self):
        def after_write(name, payload):
            raise OSError("injected failure after replacement")

        for action in ("ask", "begin", "pause", "cancel", "fail", "note", "append", "done"):
            with self.subTest(action=action):
                self.empty_store()
                ident = self.make_state("waiting" if action == "begin" else "answering")
                if action == "ask":
                    function, args, kwargs = self.store.ask, ("phone", "new"), {}
                elif action in ("append", "done"):
                    function, args = self.store.append, (ident, 6, b"!")
                    kwargs = {"done": True, "finish": "stop"} if action == "done" else {}
                elif action == "note":
                    function, args, kwargs = self.store.note, (ident,), {"tokens": 1}
                else:
                    function, args, kwargs = getattr(self.store, action), (ident,), {}
                    if action == "fail":
                        args += ("broken",)
                before = self.store.snapshot()
                self.disk.writes.clear()
                with (
                    patch.object(self.disk, "after_write", after_write),
                    patch.object(self.disk, "read_json", wraps=self.disk.read_json) as read,
                ):
                    with self.assertRaises(OSError):
                        function(*args, **kwargs)
                read.assert_not_called()
                self.assertEqual(self.store.snapshot(), before)
                self.assertEqual(len(self.disk.writes), 1)
                name = self.disk.writes[0]
                record, _ = self.disk.read_json(name, {})
                self.assertEqual(record["answer"]["rev"], before["rev"] + 1)
                # The same operation must write again even though matching bytes are visible.
                result = function(*args, **kwargs)
                self.assertEqual(self.disk.writes, [name, f"ai-answer-{result['id']}.json"])
                self.assertEqual(self.store.rev, before["rev"] + 1)
                self.assertEqual(self.store.get(result["id"]), result)

    def test_failed_write_without_matching_readback_keeps_memory(self):
        for fault in ("unreadable", "read error", "different record"):
            with self.subTest(fault=fault):
                self.empty_store()
                ident = self.make_state("answering")
                before = self.store.snapshot()
                error = OSError("original write error")

                def after_write(name, payload, fault=fault, error=error):
                    if fault == "different record":
                        Store(self.root).write_json(name, {**payload, "seq": payload["seq"] + 1})
                    else:
                        (self.root / name).write_bytes(b"{bad")
                    raise error

                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(self.disk, "after_write", after_write))
                    if fault == "read error":
                        stack.enter_context(patch.object(self.disk, "read_json", side_effect=OSError("read refused")))
                    with self.assertRaises(OSError) as caught:
                        self.store.note(ident, tokens=1)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.store.snapshot(), before)

    def test_write_value_error_is_raised_without_readback(self):
        ident = self.make_state("answering")
        error = ValueError("record too big")
        with (
            patch.object(self.disk, "write_json", side_effect=error),
            patch.object(self.disk, "read_json") as read,
        ):
            self.assertIs(self.unchanged(ValueError, self.store.note, ident, tokens=1), error)
        read.assert_not_called()

    def test_failed_eviction_keeps_files_and_memory(self):
        oldest = self.make_state("cancelled")
        for _ in range(49):
            self.store.ask("phone", "queue")
        self.disk.remaining = 1
        self.unchanged(OSError, self.store.ask, "gpu", "new")
        self.assertTrue((self.root / f"ai-answer-{oldest}.json").exists())

    def test_finishing_has_no_second_write_and_cannot_lose_final_bytes(self):
        for action in ("done", "cancel", "fail"):
            with self.subTest(action=action):
                self.empty_store()
                ident = self.make_state("answering")
                queued = self.store.ask("gpu", "next")["id"]
                before = self.store.snapshot()
                self.disk.remaining = 2
                self.disk.writes.clear()
                if action == "done":
                    self.store.append(ident, 6, b"!", True, "stop")
                elif action == "fail":
                    self.store.fail(ident, "broken")
                else:
                    self.store.cancel(ident)
                self.assertEqual(self.disk.remaining, 1)
                self.disk.remaining = None
                self.assertEqual(self.disk.writes, [f"ai-answer-{ident}.json"])
                self.unchanged(answers.StateError, self.store.cancel, ident)
                if action == "done":
                    self.assertEqual(self.store.get(ident)["text"], "prefix!")
                    self.assertEqual(self.store.get(ident)["state"], "done")
                    self.store.note(ident, tokens=1)
                    record, _ = self.disk.read_json(f"ai-answer-{ident}.json", {})
                    self.assertEqual(record["answer"]["text"], "prefix!")
                snapshot = self.store.snapshot()
                recovered = answers.AnswerStore(Store(self.root))
                self.assertEqual(
                    recovered.get(ident)["state"], {"done": "done", "cancel": "cancelled", "fail": "failed"}[action]
                )
                self.assertEqual(recovered.get(queued)["state"], "waiting")
                self.assertEqual(recovered.snapshot(), snapshot)
                self.assertEqual(recovered.rev, before["rev"] + (2 if action == "done" else 1))
                self.assertEqual(recovered.get(queued)["rev"], self.store.get(queued)["rev"])

    def test_crash_after_creation_before_eviction(self):
        oldest = self.make_state("cancelled")
        next_oldest = self.make_state("failed")
        for _ in range(48):
            self.store.ask("phone", "queue")
        original = Path.unlink

        def unlink(path, *args, **kwargs):
            if path.name == f"ai-answer-{oldest}.json":
                raise SystemExit("interrupted before eviction")
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink), self.assertRaises(SystemExit):
            self.store.ask("gpu", "new")
        # The answer was accepted before tidying was interrupted.
        self.assertEqual(self.store.latest("gpu")["question"], "new")
        self.assertIsNone(self.store.get(oldest))
        files = self.files()
        recovered = answers.AnswerStore(Store(self.root))
        self.assertEqual(len(recovered.snapshot()["answers"]), 51)
        self.assertEqual(set(self.files()), set(files))
        self.assertTrue(
            all(row["state"] == "failed" for row in recovered.snapshot()["answers"] if row["pane"] == "phone")
        )
        self.assertEqual(recovered.latest("gpu")["question"], "new")
        recovered.ask("phone", "newer")
        self.assertIsNone(recovered.get(oldest))
        self.assertFalse((self.root / f"ai-answer-{oldest}.json").exists())
        self.assertIsNone(recovered.get(next_oldest))
        self.assertFalse((self.root / f"ai-answer-{next_oldest}.json").exists())
        self.assertEqual(len(self.files()), 50)

    def assert_files_preserved(self, before):
        # Include quarantined answers, but not Store's own lock files.
        after = {path.name: path.read_bytes() for path in self.root.glob("ai-answer-*.json*") if path.is_file()}
        self.assertEqual(len(after), len(before))
        for name, raw in before.items():
            matches = [value for key, value in after.items() if key == name or key.startswith(name + ".bad-")]
            self.assertEqual(matches, [raw], name)

    def test_damaged_and_unreadable_files_report_startup_notice(self):
        for damage in ("json", "schema", "record", "unreadable"):
            with self.subTest(damage=damage):
                self.empty_store()
                damaged = self.store.ask("phone", "damaged")["id"]
                good = self.store.ask("gpu", "good")["id"]
                path = self.root / f"ai-answer-{damaged}.json"
                if damage == "json":
                    path.write_bytes(b"{bad")
                elif damage == "schema":
                    path.write_bytes(b"{}")
                elif damage == "record":
                    self.disk.write_json(path.name, {"answer": {"text": 12}})
                before, rev = self.files(), self.store.rev
                disk = UnreadableStore(self.root, path.name) if damage == "unreadable" else Store(self.root)
                recovered = answers.AnswerStore(disk)
                self.assertEqual([row["id"] for row in recovered.snapshot()["answers"]], [good])
                self.assertEqual(recovered.rev, rev)
                self.assert_files_preserved(before)
                self.assertTrue(recovered.startup_notice)
                notice = recovered.startup_notice
                self.assertTrue(notice.endswith("restart AI mode.") or notice.endswith("Restart AI mode to continue."))
                if damage == "unreadable":
                    self.assertEqual(path.read_bytes(), before[path.name])
                    self.assertFalse(list(self.root.glob(path.name + ".bad-*")))
                else:
                    self.assertFalse(path.exists())
                    self.assertEqual(
                        [p.read_bytes() for p in self.root.glob(path.name + ".bad-*")], [before[path.name]]
                    )
                    self.assertEqual(answers.AnswerStore(Store(self.root)).startup_notice, "")
                recovered.ask("phone", "still usable")
                self.assertEqual(recovered.startup_notice, notice)
                recovered.snapshot()
                self.assertEqual(recovered.startup_notice, notice)
                with self.assertRaises(OSError):
                    recovered.ask("gpu", "replacement")

    def test_revision_uses_surviving_records_when_highest_is_damaged(self):
        good = self.store.ask("phone", "kept")
        damaged = self.store.ask("gpu", "damaged")
        (self.root / f"ai-answer-{damaged['id']}.json").write_bytes(b"bad")
        before = self.files()
        recovered = answers.AnswerStore(Store(self.root))
        good.update(state="failed", error="restart", rev=good["rev"] + 1)
        self.assertEqual(recovered.snapshot(), {"rev": good["rev"], "answers": [good]})
        backups = list(self.root.glob(f"ai-answer-{damaged['id']}.json.bad-*"))
        self.assertEqual([path.read_bytes() for path in backups], [before[f"ai-answer-{damaged['id']}.json"]])

    def test_every_answer_damaged_warns_and_blocks_gpu_work(self):
        for pane in answers.PANES:
            ident = self.store.ask(pane, "lost")["id"]
            (self.root / f"ai-answer-{ident}.json").write_bytes(b"bad")
        before = self.files()
        recovered = answers.AnswerStore(Store(self.root))
        self.assertEqual(recovered.snapshot(), {"rev": 0, "answers": []})
        self.assertTrue(recovered.startup_notice)
        with self.assertRaises(OSError):
            recovered.ask("gpu", "replacement")
        self.assert_files_preserved(before)
        row = recovered.ask("phone", "usable")
        self.assertEqual(row["rev"], 1)
        row.update(state="failed", error="restart", rev=row["rev"] + 1)
        self.assertEqual(answers.AnswerStore(Store(self.root)).get(row["id"]), row)

    def test_non_answer_files_are_left_alone(self):
        kept = self.store.ask("phone", "kept")
        names = ("ai-answers.json", "unrelated.txt", "ai-answer-invalid.json", ".temporary")
        for name in names:
            (self.root / name).write_bytes(b"leave alone")
        before = self.files()
        recovered = answers.AnswerStore(Store(self.root))
        self.assertEqual(recovered.get(kept["id"])["state"], "failed")
        self.assertEqual(set(self.files()), set(before))
        for name in names:
            self.assertEqual((self.root / name).read_bytes(), b"leave alone")

    def test_startup_open_answers_and_legacy_waiting(self):
        cases = (
            ("gpu", ("paused", "paused"), ("paused", "queued"), (1,)),
            ("phone", ("paused", "paused"), ("paused", "queued"), (1,)),
            ("gpu", ("loading", "paused"), ("paused", "queued"), (0, 1)),
            ("phone", ("loading", "paused"), ("failed", "paused"), (0,)),
            ("gpu", ("waiting", "paused"), ("queued", "paused"), ()),
            ("phone", ("waiting", "paused"), ("failed", "paused"), (0,)),
            ("gpu", ("waiting", "waiting"), ("waiting", "queued"), ()),
            ("phone", ("waiting", "waiting"), ("failed", "failed"), (0, 1)),
            ("gpu", ("queued", "waiting"), ("waiting", "queued"), ()),
        )
        for pane, states, expected, changed in cases:
            with self.subTest(pane=pane, states=states):
                self.empty_store()
                ids = [self.store.ask(pane, question)["id"] for question in ("oldest", "newer")]
                for ident, state in zip(ids, states, strict=True):
                    path = self.root / f"ai-answer-{ident}.json"
                    record, _ = self.disk.read_json(path.name, {})
                    record["answer"].update(state=state, text="partial é", bytes=len("partial é".encode()))
                    path.write_text(json.dumps(record), encoding="utf-8")
                before = self.files()
                self.disk.writes.clear()
                recovered = answers.AnswerStore(self.disk)
                self.assertEqual([recovered.get(ident)["state"] for ident in ids], list(expected))
                self.assertEqual(recovered.rev, self.store.rev + bool(changed))
                self.assertEqual(self.disk.writes, [f"ai-answer-{ids[index]}.json" for index in changed])
                for index, ident in enumerate(ids):
                    self.assertEqual(recovered.get(ident)["text"], "partial é")
                    if index in changed:
                        self.assertEqual(recovered.get(ident)["rev"], recovered.rev)
                    else:
                        name = f"ai-answer-{ident}.json"
                        self.assertEqual(self.files()[name], before[name])
                active = recovered.current(pane)
                if active is not None:
                    self.assertIn(active["state"], ("waiting", "paused"))
                if "failed" in expected:
                    self.assertEqual(recovered.get(ids[0])["error"], "restart")
                self.disk.writes.clear()
                again = answers.AnswerStore(self.disk)
                if pane == "phone" and expected == ("paused", "queued"):
                    self.assertEqual(again.get(ids[1])["state"], "failed")
                else:
                    self.assertEqual(again.snapshot(), recovered.snapshot())
                    self.assertEqual(self.disk.writes, [])
                # A legacy waiting file must never be written back as waiting.
                for ident in ids:
                    record, _ = self.disk.read_json(f"ai-answer-{ident}.json", {})
                    if record["answer"]["state"] == "waiting":
                        state = recovered.get(ident)["state"]
                        self.assertEqual(recovered.note(ident, tokens=1)["state"], state)
                        record, _ = self.disk.read_json(f"ai-answer-{ident}.json", {})
                        self.assertEqual(record["answer"]["state"], "queued")
                if "failed" not in expected and pane == "gpu":
                    recovered.cancel(active["id"])
                    promoted = recovered.current(pane)
                    self.assertEqual(promoted["state"], "waiting")
                    self.assertEqual(promoted["text"], "partial é")
                    ident = promoted["id"]
                    self.assertEqual(recovered.begin(ident)["text"], "partial é")
                    self.assertEqual(
                        recovered.append(ident, promoted["bytes"], b"!", True, "stop")["text"], "partial é!"
                    )

    def test_reconciliation_write_failure_keeps_recovery_and_retries_on_any_save(self):
        ident = self.make_state("answering")
        done = self.make_state("done", "phone")
        rev = self.store.rev
        with patch.object(self.disk, "write_json", side_effect=OSError(errno.ENOSPC, "storage full")):
            recovered = answers.AnswerStore(self.disk)
        row = recovered.get(ident)
        self.assertEqual((row["state"], row["text"]), ("paused", "prefix"))
        self.assertEqual(recovered.latest("phone")["id"], done)
        self.assertEqual(recovered.rev, rev + 1)
        saved, _ = self.disk.read_json(f"ai-answer-{ident}.json", {})
        self.assertEqual(saved["answer"]["state"], "answering")
        recovered.note(done, tokens=1)
        saved, _ = self.disk.read_json(f"ai-answer-{ident}.json", {})
        self.assertEqual(saved["answer"], row)
        self.assertFalse(recovered._recoveries)
        self.assertEqual(answers.AnswerStore(self.disk).get(ident), row)

    def test_service_starts_when_reconciliation_write_fails(self):
        ident = self.make_state("answering")
        self.addCleanup(gpu_link_server.stop)
        with patch.object(self.disk, "write_json", side_effect=OSError(errno.ENOSPC, "storage full")):
            recovered = answers.AnswerStore(self.disk)
            service = gpu_link_server.start(
                recovered,
                {"OPOS_GPU_LINK": "1", "OPOS_GPU_LINK_PORT": "0", "XDG_STATE_HOME": str(self.root)},
            )
        self.assertIsNotNone(service)
        self.assertIs(gpu_link_server.service(), service)
        self.assertEqual(recovered.get(ident)["state"], "paused")
        again = answers.AnswerStore(self.disk)
        saved, _ = self.disk.read_json(f"ai-answer-{ident}.json", {})
        self.assertEqual(saved["answer"]["state"], "paused")
        self.assertEqual(again.get(ident)["text"], "prefix")

    def test_startup_second_write_failure_converges_on_retry(self):
        self.make_state("loading", "gpu")
        phone = self.make_state("answering", "phone")
        queued = self.store.ask("phone", "next")["id"]
        baseline_root = self.root / "uninterrupted"
        baseline_root.mkdir()
        for name, raw in self.files().items():
            (baseline_root / name).write_bytes(raw)
        baseline_disk = FailingStore(baseline_root)
        expected = answers.AnswerStore(baseline_disk)
        self.assertEqual(len(baseline_disk.writes), 3)
        self.assertEqual(expected.rev, self.store.rev + 1)
        self.assertEqual(expected.get(phone)["state"], "failed")
        self.assertEqual(expected.get(queued)["state"], "failed")
        self.disk.writes.clear()
        self.disk.remaining = 2
        partial = answers.AnswerStore(self.disk)
        self.assertEqual(len(self.disk.writes), 2)
        self.assertEqual(partial.get(phone)["state"], "failed")
        self.assertEqual(len(partial._recoveries), 1)
        self.disk.writes.clear()
        recovered = answers.AnswerStore(self.disk)
        self.assertEqual(len(self.disk.writes), 1)
        # A fresh constructor uses the highest surviving revision. The retry
        # is a new change; the answer contents still converge without a journal.
        self.assertEqual(recovered.rev, expected.rev + 1)
        for row in self.store.snapshot()["answers"]:
            ident = row["id"]
            actual, wanted = recovered.get(ident), expected.get(ident)
            actual.pop("rev")
            wanted.pop("rev")
            self.assertEqual(actual, wanted)
        self.disk.writes.clear()
        self.assertEqual(answers.AnswerStore(self.disk).snapshot(), recovered.snapshot())
        self.assertEqual(self.disk.writes, [])

    @unittest.skipUnless(os.name == "posix", "requires POSIX directory fsync")
    def test_recovery_directory_error_remains_pending_until_synced(self):
        ident = self.make_state("loading")
        self.disk.writes.clear()
        fsync = os.fsync
        attempts = []

        def synced(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                attempts.append("directory")
                raise OSError(errno.EIO, "directory sync failed")
            return fsync(fd)

        with patch("opos.store.os.fsync", synced):
            recovered = answers.AnswerStore(self.disk)
            self.assertEqual(recovered._recoveries, {ident})
            self.assertEqual(self.disk.writes, [])
            recovered._retry_recoveries()
            self.assertEqual(recovered._recoveries, {ident})
        self.assertEqual(attempts, ["directory", "directory"])
        recovered._retry_recoveries()
        self.assertEqual(recovered._recoveries, set())
        self.assertEqual(self.disk.writes, [f"ai-answer-{ident}.json"])
        record, _ = self.disk.read_json(f"ai-answer-{ident}.json", {})
        self.assertEqual(record["answer"], recovered.get(ident))
        self.assertEqual(record["answer"]["state"], "paused")

    def test_restart_revision_and_only_changed_records_are_written(self):
        gpu = self.make_state("loading")
        phone = self.make_state("answering", "phone")
        queued = self.store.ask("phone", "next")["id"]
        unchanged = self.store.ask("gpu", "queued")["id"]
        self.store.note(gpu, tokens=1)
        before, rev = self.files(), self.store.rev
        self.disk.writes.clear()
        recovered = answers.AnswerStore(self.disk)
        self.assertEqual(recovered.rev, rev + 1)
        self.assertEqual(set(self.disk.writes), {f"ai-answer-{ident}.json" for ident in (gpu, phone, queued)})
        self.assertEqual(len(self.disk.writes), 3)
        for ident in (gpu, phone, queued):
            self.assertEqual(recovered.get(ident)["rev"], rev + 1)
        self.assertEqual(recovered.get(queued)["rev"], rev + 1)
        self.assertEqual(recovered.get(queued)["state"], "failed")
        path = f"ai-answer-{unchanged}.json"
        self.assertEqual(self.files()[path], before[path])
        self.disk.writes.clear()
        again = answers.AnswerStore(self.disk)
        self.assertEqual(again.rev, rev + 1)
        self.assertEqual(again.snapshot(), recovered.snapshot())
        self.assertEqual(self.disk.writes, [])

    def test_append_writes_once_and_flushes_before_return(self):
        ident = self.make_state("loading")
        before = self.store.snapshot()
        self.disk.writes.clear()
        self.disk.events.clear()
        original = os.fsync

        def fsync(fd):
            result = original(fd)
            self.disk.events.append("flushed")
            return result

        def after_write(name, payload):
            self.assertEqual(self.store.snapshot(), before)

        with (
            patch("opos.store.os.fsync", side_effect=fsync),
            patch.object(self.disk, "after_write", after_write),
        ):
            result = self.store.append(ident, 0, b"durable")
            self.disk.events.append("append returned")
        self.assertEqual(self.disk.writes, [f"ai-answer-{ident}.json"])
        self.assertEqual(
            self.disk.events, ["flushed"] * (2 if os.name == "posix" else 1) + ["write finished", "append returned"]
        )
        record, _ = self.disk.read_json(self.disk.writes[0], {})
        self.assertEqual(record["answer"], result)
        self.assertEqual(set(record), {"schema", "answer", "final_offset", "seq"})
        self.assertIsNone(record["final_offset"])

    def test_changes_copy_only_the_records_they_touch(self):
        ident = self.make_state("loading")
        queued = self.store.ask("gpu", "next")["id"]
        for _ in range(10):
            self.store.ask("phone", "unrelated")
        for finish in (False, True):
            with self.subTest(finish=finish):
                with patch("opos.ai_answers.deepcopy", wraps=answers.deepcopy) as copy:
                    self.store.append(ident, self.store.get(ident)["bytes"], b"x", finish, "stop" if finish else None)
                copied = [call.args[0] for call in copy.call_args_list]
                records = [value for value in copied if "answer" in value]
                self.assertEqual([record["answer"]["id"] for record in records], [ident])
                self.assertTrue(all("id" in value or "answer" in value for value in copied))
        self.assertEqual(self.store.get(queued)["state"], "waiting")

    def test_latest_in_each_pane_returns_copies_in_every_state(self):
        self.unchanged(ValueError, self.store.latest, "unknown")
        for pane in answers.PANES:
            self.assertIsNone(self.store.latest(pane))
        for pane in answers.PANES:
            with self.subTest(pane=pane):
                self.empty_store()
                other_pane = next(value for value in answers.PANES if value != pane)
                other = self.store.ask(other_pane, "independent")
                row = self.store.ask(pane, "first")
                self.assertEqual(self.store.latest(pane), row)
                self.store.latest(pane)["question"] = "mutated"
                self.assertEqual(self.store.latest(pane), row)
                self.store.begin(row["id"])
                final = self.store.append(row["id"], 0, b"done", True, "stop")
                self.assertEqual(self.store.latest(pane), final)
                newer = self.store.ask(pane, "newer")
                self.assertEqual(self.store.latest(pane), newer)
                newest = self.store.ask(pane, "queued")
                self.assertEqual(self.store.latest(pane), newest)
                self.assertEqual(self.store.latest(other_pane), other)

    def test_negative_clock_answer_loads_after_restart(self):
        with patch("opos.ai_answers.time.time", return_value=-5):
            row = self.store.ask("gpu", "question")
        self.assertEqual(row["asked"], 0)
        self.assertEqual(answers.AnswerStore(self.disk).get(row["id"]), row)

    def test_creation_order_survives_restart_with_equal_seconds(self):
        ids = ["f" * 12, "a" * 12, "0" * 12]
        with patch("opos.ai_answers.time.time", return_value=123456), patch("opos.ai_answers.uuid.uuid4") as uuid4:
            for ident in ids:
                uuid4.return_value.hex = ident
                self.store.ask("gpu", ident)
        for number, ident in enumerate(ids, 1):
            record, _ = self.disk.read_json(f"ai-answer-{ident}.json", {})
            self.assertEqual(record["seq"], number)
        before = self.store.snapshot()
        self.assertEqual([row["id"] for row in before["answers"]], ids[::-1])
        self.assertEqual({row["asked"] for row in before["answers"]}, {123456})
        recovered = answers.AnswerStore(Store(self.root))
        self.assertEqual(recovered.snapshot(), before)
        self.assertEqual(recovered.latest("gpu")["id"], ids[-1])
        recovered.cancel(ids[0])
        self.assertEqual(recovered.current("gpu")["id"], ids[1])
        new = recovered.ask("phone", "later")
        record, _ = self.disk.read_json(f"ai-answer-{new['id']}.json", {})
        self.assertEqual(record["seq"], 4)

    def test_equal_sequences_are_ordered_by_id(self):
        ids = []
        for _ in range(3):
            ident = self.store.ask("gpu", "question")["id"]
            ids.append(ident)
            path = f"ai-answer-{ident}.json"
            record, _ = self.disk.read_json(path, {})
            record["seq"] = 1
            record["answer"]["state"] = "queued"
            self.disk.write_json(path, record)
        recovered = answers.AnswerStore(Store(self.root))
        self.assertEqual([row["id"] for row in recovered.snapshot()["answers"]], sorted(ids, reverse=True))
        self.assertEqual(recovered.current("gpu")["id"], min(ids))

    def test_metadata_and_copied_results(self):
        ident = self.make_state("waiting")
        result = self.store.note(ident, model="m" * 64, where="w" * 64, tokens=0, tps=100_000)
        self.assertEqual(result["tokens"], 0)
        self.assertEqual(result["tps"], 100_000)
        self.assertEqual(len(result["model"]), 64)
        for kwargs in (
            {"model": "m" * 65},
            {"where": "w" * 65},
            {"model": "\ud800"},
            {"tokens": True},
            {"tokens": -1},
            {"tokens": 1.5},
            {"tps": True},
            {"tps": -1},
            {"tps": 100_001},
            {"tps": float("nan")},
            {"tps": float("inf")},
        ):
            self.unchanged(ValueError, self.store.note, ident, **kwargs)
        for code in ("", "A", "space code", "x" * 33, None):
            self.unchanged(ValueError, self.store.fail, ident, code)
        rev, files = self.store.rev, self.files()
        self.store.note(ident)
        self.store.note(ident, tokens=0)
        self.assertEqual(self.store.rev, rev)
        self.assertEqual(self.files(), files)
        result["text"] = "changed"
        self.store.get(ident)["text"] = "changed"
        self.store.current("gpu")["text"] = "changed"
        self.store.snapshot()["answers"][0]["text"] = "changed"
        self.assertEqual(self.store.get(ident)["text"], "")
        self.store.cancel(ident)
        self.assertEqual(self.store.note(ident, tps=2.5)["state"], "cancelled")

    def test_file_modes(self):
        ident = self.make_state("loading")
        self.store.append(ident, 0, b"text", True, "stop")
        for path in self.root.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_threads_observe_consistent_snapshots(self):
        start = threading.Event()
        stop = threading.Event()

        def writer(pane):
            start.wait()
            for _ in range(24):
                ident = self.store.ask(pane, "thread")["id"]
                self.store.begin(ident)
                self.store.append(ident, 0, "é🙂".encode())
                self.store.append(ident, 6, b"!", True, "stop")
            stop.set()

        def reader():
            start.wait()
            rev, observed = 0, 0
            while not stop.is_set() or observed < 30:
                snapshot = self.store.snapshot()
                self.assertGreaterEqual(snapshot["rev"], rev)
                rev = snapshot["rev"]
                for pane in answers.PANES:
                    self.assertLessEqual(
                        sum(row["pane"] == pane and row["state"] in answers.OPEN for row in snapshot["answers"]), 1
                    )
                for row in snapshot["answers"]:
                    self.assertEqual(row["bytes"], len(row["text"].encode()))
                    self.assertLessEqual(row["rev"], rev)
                observed += 1
                time.sleep(0.0005)
            return observed

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(writer, pane) for pane in answers.PANES]
            futures += [pool.submit(reader) for _ in range(3)]
            start.set()
            for future in futures:
                future.result(timeout=30)
        self.assertEqual(len(self.store.snapshot()["answers"]), 48)

    def test_no_output(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            ident = self.make_state("loading")
            self.store.note(ident, model="small", where="phone", tokens=1, tps=2)
            self.store.append(ident, 0, b"part")
            self.store.pause(ident)
            self.store.begin(ident)
            self.store.append(ident, 4, b"end", True, "stop")
            self.store.append(ident, 4, b"end", True, "stop")
            self.store.get(ident)
            self.store.current("gpu")
            self.store.snapshot()
            self.store.cancel(self.store.ask("phone", "cancel")["id"])
            self.store.fail(self.store.ask("phone", "fail")["id"], "broken")
            answers.AnswerStore(Store(self.root))
            self.unchanged(answers.StateError, self.store.begin, ident)
            (self.root / f"ai-answer-{ident}.json").write_bytes(b"bad")
            answers.AnswerStore(Store(self.root))
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_shared_default_and_reset(self):
        answers.reset()
        self.addCleanup(answers.reset)
        previous = os.environ.get("XDG_STATE_HOME")
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            first = answers.shared()
            self.assertIs(answers.shared(), first)
            row = first.ask("phone", "default")
            # Compare answer files; Store keeps lock files of its own.
            self.assertEqual(
                list((self.root / "opos").glob("ai-answer-*.json")),
                [self.root / "opos" / f"ai-answer-{row['id']}.json"],
            )
            answers.reset()
            second = answers.shared()
            self.assertIsNot(first, second)
            self.assertIs(answers.shared(), second)
            row.update(state="failed", error="restart", rev=row["rev"] + 1)
            self.assertEqual(second.get(row["id"]), row)
        self.assertEqual(os.environ.get("XDG_STATE_HOME"), previous)

    def slow_writes(self):
        real = self.disk.write_json

        def slow(*args, **kwargs):
            time.sleep(0.15)
            return real(*args, **kwargs)

        return patch.object(self.disk, "write_json", slow)

    def test_surv_full_length_answer_survives_restart(self):
        # The maximum accepted byte count remains valid on disk after restart.
        ident = self.store.ask("gpu", "q")["id"]
        self.store.begin(ident)
        self.store.append(ident, 0, b"x" * answers.MAX_ANSWER_BYTES, done=True, finish="length")
        restarted = answers.AnswerStore(Store(self.root))
        row = restarted.get(ident)
        self.assertIsNotNone(row)
        self.assertEqual((row["state"], row["bytes"]), ("done", answers.MAX_ANSWER_BYTES))

    def test_surv_concurrent_appends_at_one_offset_store_one_winner(self):
        # Competing posts must not both acknowledge different bytes at one offset.
        ident = self.store.ask("phone", "q")["id"]
        self.store.begin(ident)
        results = []

        def post(data):
            try:
                results.append(("ok", self.store.append(ident, 0, data)["bytes"]))
            except answers.AnswerError as err:
                results.append(("err", type(err).__name__))

        with self.slow_writes(), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(post, data) for data in (b"abc", b"xyz")]
            for future in futures:
                future.result(timeout=5)
        self.assertEqual(sorted(kind for kind, _ in results), ["err", "ok"])
        self.assertEqual(self.store.get(ident)["bytes"], 3)

    def test_surv_concurrent_asks_get_distinct_sequence_numbers(self):
        # Creation order must remain distinct when both panes ask concurrently.
        def ask(pane):
            self.store.ask(pane, "q")

        with self.slow_writes(), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ask, pane) for pane in ("phone", "gpu")]
            for future in futures:
                future.result(timeout=5)
        self.assertEqual(sorted(row["seq"] for row in self.store._records.values()), [1, 2])


if __name__ == "__main__":
    unittest.main()
