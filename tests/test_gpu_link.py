# SPDX-License-Identifier: GPL-3.0-or-later
"""Socket-free GPU Link contract tests, with real durable answer files."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import hmac
import io
import json
import os
import random
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from opos import ai_answers, ai_page, gpu_link
from opos import gpu_link_proto as proto
from opos.ai_answers import AnswerError, AnswerStore
from opos.gpu_link import HASH_RETRY_SECONDS, HISTORY_LIMIT, LINK_SECONDS, LinkService
from opos.store import DurabilityError, Store
from tests.gpu_link_fake_pc import Clock, FakePC, RandomBytes


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state")
        self.answers = AnswerStore(self.store)
        self.clock = Clock()
        self.rand = RandomBytes()
        self.model = self.root / "test-model.gguf"
        self.model.write_bytes(b"model bytes\x00\xff" * 16)
        self.sha = hashlib.sha256(self.model.read_bytes()).hexdigest()
        self.link = self.make_link()
        self.pc = FakePC(self.link)
        self.assertEqual(self.pc.hello().status, 200)

    def make_link(self, **kwargs):
        link = LinkService(
            self.answers, self.store, kwargs.pop("model_path", self.model), clock=self.clock, rand=self.rand, **kwargs
        )
        self.addCleanup(link.close)
        if link._hasher:
            link._hasher.join(2)
            self.assertFalse(link._hasher.is_alive())
        return link

    def enrolled(self):
        self.assertEqual(self.pc.enrol().status, 202)
        self.assertTrue(self.link.allow(self.pc.code))
        self.assertEqual(self.pc.hello().status, 200)

    def work(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "A test question")
        reply = self.pc.work()
        self.assertEqual(reply.status, 200)
        return answer, self.pc.data(reply)

    def replace_pc(self, key=b"N" * 32):
        new = FakePC(self.link, key=key, label="PC-B")
        self.link._pending = (new.label, new.key, self.clock())
        return new

    def restart_with_page(self):
        self.link.close()
        self.store = Store(self.store.root)
        self.answers = AnswerStore(self.store)
        self.link = self.make_link()
        self.pc = FakePC(self.link)
        self.assertEqual(self.pc.hello().status, 200)
        if self.link._pc is not None:
            self.assertEqual(self.pc.status("ready").status, 200)
        with (
            patch.object(ai_answers, "shared", return_value=self.answers),
            patch.object(ai_page, "_service", return_value=self.link),
            patch.object(ai_page, "_route", return_value=None),
        ):
            return ai_page.PageState().snapshot()

    def test_startup_read_fault_preserves_acknowledged_prefix_for_retry(self):
        answer, work = self.work()
        prefix = "Saved café\nGPU text"
        reply = self.pc.text(work["lease"], 0, prefix)
        self.assertEqual((reply.status, self.pc.data(reply)), (200, {"acked": len(prefix.encode())}))
        self.link.close()
        path = self.store.root / f"ai-answer-{answer['id']}.json"
        saved = path.read_bytes()
        opened = Path.open

        for error in (PermissionError(errno.EACCES, "read denied"), OSError(errno.EIO, "read failed")):
            with self.subTest(error=type(error).__name__):
                reads = []

                def unreadable(target, *args, reads=reads, error=error, **kwargs):
                    if target == path and args == ("rb",):
                        reads.append(target)
                        raise error
                    return opened(target, *args, **kwargs)

                with patch.object(Path, "open", unreadable):
                    # Both startup attempts fail; the directory remains writable.
                    probe = path.parent / "writable"
                    probe.write_bytes(b"ok")
                    probe.unlink()
                    for _ in range(2):
                        page = self.restart_with_page()
                        self.assertTrue(path.exists())
                        self.assertEqual(list(path.parent.glob(path.name + ".bad-*")), [])
                        self.assertEqual(page["gpu"]["link"]["storage"], "error")
                        self.assertFalse(page["gpu"]["ready"])
                        self.assertIn("could not read", page["gpu"]["link"]["storage_notice"])
                        self.assertEqual(self.pc.work().status, 204)
                        with self.assertRaises(OSError):
                            self.answers.ask("gpu", "replacement")
                        before = self.answers.snapshot()
                        with (
                            patch.object(ai_answers, "shared", return_value=self.answers),
                            patch.object(ai_page, "_service", return_value=self.link),
                            self.assertRaisesRegex(ValueError, "could not read"),
                        ):
                            ai_page.PageState().ask({"panes": ["phone", "gpu"], "question": "replacement"})
                        self.assertEqual(self.answers.snapshot(), before)
                self.assertGreaterEqual(len(reads), 2)
                self.assertEqual(path.read_bytes(), saved)
        page = self.restart_with_page()
        self.assertIsNone(page["gpu"]["link"]["storage"])
        self.assertEqual(page["gpu"]["answer"]["text"], prefix)
        reply = self.pc.work()
        self.assertEqual(reply.status, 200)
        resumed = self.pc.data(reply)
        self.assertEqual(resumed["partial"], prefix)
        reply = self.pc.text(resumed["lease"], len(prefix.encode()), " continued")
        self.assertEqual((reply.status, self.pc.data(reply)), (200, {"acked": len((prefix + " continued").encode())}))
        self.assertEqual(self.answers.get(answer["id"])["text"], prefix + " continued")

    def test_startup_corrupt_json_quarantines_with_visible_warning(self):
        answer, work = self.work()
        self.assertEqual(self.pc.text(work["lease"], 0, "saved").status, 200)
        self.link.close()
        path = self.store.root / f"ai-answer-{answer['id']}.json"
        damaged = b"{bad JSON"
        path.write_bytes(damaged)
        page = self.restart_with_page()
        self.assertFalse(path.exists())
        self.assertEqual([p.read_bytes() for p in path.parent.glob(path.name + ".bad-*")], [damaged])
        self.assertEqual(page["gpu"]["link"]["storage"], "error")
        self.assertFalse(page["gpu"]["ready"])
        self.assertEqual(
            page["gpu"]["link"]["storage_notice"],
            "A saved answer was damaged and moved aside. Restart AI mode to continue.",
        )
        self.assertEqual(self.pc.work().status, 204)
        with self.assertRaises(OSError):
            self.answers.ask("gpu", "replacement")

    def test_startup_missing_answer_is_empty_and_ready_without_warning(self):
        self.enrolled()
        missing = self.store.root / "ai-answer-0123456789ab.json"
        glob = Path.glob

        def disappeared(folder, pattern):
            if folder == self.store.root and pattern == "ai-answer-*.json":
                return iter([missing])
            return glob(folder, pattern)

        with patch.object(Path, "glob", disappeared):
            page = self.restart_with_page()
        self.assertIsNone(page["gpu"]["answer"])
        self.assertTrue(page["gpu"]["ready"])
        self.assertIsNone(page["gpu"]["link"]["storage"])
        self.assertEqual(page["gpu"]["link"]["storage_notice"], "")
        self.assertEqual(list(self.store.root.glob("ai-answer-*.json*")), [])
        self.assertEqual(self.pc.work().status, 204)
        self.answers.ask("gpu", "new question")
        self.assertEqual(self.pc.work().status, 200)

    def test_readable_gpu_resumes_beside_damaged_or_unreadable_phone(self):
        self.enrolled()
        for damage in ("json", "record", "denied"):
            with self.subTest(damage=damage):
                answer = self.answers.ask("gpu", "good question")
                reply = self.pc.work()
                self.assertEqual(reply.status, 200)
                work = self.pc.data(reply)
                prefix = "Kept café prefix"
                self.assertEqual(self.pc.text(work["lease"], 0, prefix).status, 200)
                phone = self.answers.ask("phone", "old phone question")
                self.answers.cancel(phone["id"])
                self.link.close()
                path = self.store.root / f"ai-answer-{phone['id']}.json"
                if damage == "json":
                    path.write_bytes(b"{damaged")
                elif damage == "record":
                    self.store.write_json(path.name, {"answer": {"state": "bogus"}})
                raw = path.read_bytes()
                opened = Path.open

                def unreadable(target, *args, path=path, opened=opened, **kwargs):
                    if target == path and args == ("rb",):
                        raise PermissionError(errno.EACCES, "denied")
                    return opened(target, *args, **kwargs)

                with patch.object(Path, "open", unreadable) if damage == "denied" else contextlib.nullcontext():
                    page = self.restart_with_page()
                    notice = self.answers.startup_notice
                    self.assertTrue(notice)
                    self.assertEqual(page["gpu"]["link"]["storage_notice"], notice)
                    self.assertFalse(page["gpu"]["ready"])
                    self.assertEqual(self.answers.get(answer["id"])["text"], prefix)
                    reply = self.pc.work()
                    self.assertEqual(reply.status, 200)
                    resumed = self.pc.data(reply)
                    self.assertEqual(resumed["partial"], prefix)
                    reply = self.pc.text(resumed["lease"], len(prefix.encode()), " continued", done=True, finish="stop")
                    self.assertEqual(reply.status, 200)
                    self.assertEqual(self.answers.get(answer["id"])["text"], prefix + " continued")
                    self.assertEqual(self.answers.startup_notice, notice)
                if damage == "denied":
                    self.assertEqual(path.read_bytes(), raw)
                    self.assertFalse(list(path.parent.glob(path.name + ".bad-*")))
                    path.unlink()
                else:
                    self.assertFalse(path.exists())
                    self.assertEqual([p.read_bytes() for p in path.parent.glob(path.name + ".bad-*")], [raw])
                self.restart_with_page()
                self.assertEqual(self.answers.startup_notice, "")

    def test_unreadable_pairing_and_model_cache_do_not_stop_startup(self):
        for name in ("gpu-link-pc.json", "gpu-link-model.json"):
            with self.subTest(name=name):
                self.enrolled()
                self.link.close()
                path = self.store.root / name
                raw = path.read_bytes()
                opened = Path.open

                def unreadable(target, *args, path=path, opened=opened, **kwargs):
                    if target == path and args == ("rb",):
                        raise PermissionError(errno.EACCES, "denied")
                    return opened(target, *args, **kwargs)

                with patch.object(Path, "open", unreadable):
                    page = self.restart_with_page()
                self.assertEqual(page["gpu"]["link"]["model"]["state"], "ready")
                self.assertEqual([p.read_bytes() for p in path.parent.glob(name + ".bad-*")], [raw])
                self.assertEqual(self.answers.startup_notice, "")
                if name == "gpu-link-pc.json":
                    self.assertIsNone(page["gpu"]["link"]["pc"])
                else:
                    self.assertIsNotNone(page["gpu"]["link"]["pc"])

    def test_failed_forget_cancel_survives_restart(self):
        answer, w = self.work()
        self.assertEqual(self.pc.text(w["lease"], 0, "private prefix").status, 200)
        with patch.object(self.answers, "cancel", side_effect=OSError(errno.ENOSPC, "full")):
            self.assertTrue(self.link.forget())
        # Ordinary orderly shutdown also loses the retry intent, without needing a physical crash.
        self.link.close()
        self.store = Store(self.store.root)
        self.answers = AnswerStore(self.store)
        self.link = self.make_link()
        self.pc = FakePC(self.link, key=b"N" * 32, label="PC-B")
        self.enrolled()
        reply = self.pc.work()
        self.assertEqual(reply.status, 204)
        row = self.answers.get(answer["id"])
        self.assertEqual((row["state"], row["text"], row["where"]), ("cancelled", "private prefix", "Test PC"))

    @unittest.skipUnless(os.name == "posix", "requires POSIX directory fsync")
    def test_directory_sync_failure_is_not_acknowledged(self):
        answer, work = self.work()
        before = self.answers.get(answer["id"])
        fsync, open_directory = os.fsync, os.open
        for fault in ("open", "sync"):
            with self.subTest(fault=fault):

                def synced(fd):
                    if stat.S_ISDIR(os.fstat(fd).st_mode):
                        raise OSError(errno.EIO, "directory sync failed")
                    return fsync(fd)

                def opened(path, *args, **kwargs):
                    if Path(path) == self.store.root:
                        raise PermissionError(errno.EACCES, "directory open failed")
                    return open_directory(path, *args, **kwargs)

                target, replacement = ("fsync", synced) if fault == "sync" else ("open", opened)
                with patch("opos.store.os." + target, replacement):
                    reply = self.pc.text(work["lease"], 0, "final", True, "stop")
                self.assertEqual((reply.status, self.pc.data(reply)), (500, {"error": "state"}))
                self.assertEqual(self.answers.get(answer["id"]), before)
                self.assertEqual(self.link.snapshot()["storage"], "error")
        reply = self.pc.text(work["lease"], 0, "final", True, "stop")
        self.assertEqual((reply.status, self.pc.data(reply)), (200, {"acked": 5}))
        self.assertEqual(self.answers.get(answer["id"])["state"], "done")

    @unittest.skipUnless(os.name == "posix", "requires POSIX directory fsync")
    def test_failed_retry_never_acknowledges_unsynced_replacement(self):
        answer, work = self.work()
        before = self.answers.snapshot()
        fsync = os.fsync
        synced = []
        fault = "directory"

        def sync(fd):
            directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            if directory and fault == "directory":
                raise OSError(errno.EIO, "directory sync failed")
            if not directory and fault == "file":
                raise OSError(errno.ENOSPC, "temporary file sync failed")
            result = fsync(fd)
            synced.append("directory" if directory else "file")
            return result

        with patch("opos.store.os.fsync", sync):
            for fault in ("directory", "file"):
                with self.subTest(fault=fault):
                    reply = self.pc.text(work["lease"], 0, "final", True, "stop")
                    self.assertEqual((reply.status, self.pc.data(reply)), (500, {"error": "state"}))
                    self.assertEqual(self.answers.snapshot(), before)
                    self.assertEqual(self.link.snapshot()["storage"], "error")
            self.assertEqual(synced, ["file"])
            visible, _ = self.store.read_json(f"ai-answer-{answer['id']}.json", {})
            self.assertEqual(visible["answer"]["text"], "final")
            fault = None
            reply = self.pc.text(work["lease"], 0, "final", True, "stop")
            self.assertEqual((reply.status, self.pc.data(reply)), (200, {"acked": 5}))
            self.assertEqual(synced, ["file", "file", "directory"])
        self.assertEqual(self.answers.get(answer["id"])["state"], "done")
        self.assertIsNone(self.link.snapshot()["storage"])

    @unittest.skipUnless(os.name == "posix", "requires POSIX directory fsync")
    def test_allow_and_forget_propagate_durability_error(self):
        self.enrolled()
        fsync = os.fsync

        def synced(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "directory sync failed")
            return fsync(fd)

        new = self.replace_pc()
        with patch("opos.store.os.fsync", synced):
            with self.assertRaises(DurabilityError):
                self.link.allow(new.code)
            with self.assertRaises(DurabilityError):
                self.link.forget()
        self.assertEqual(self.link.snapshot()["pc"]["label"], self.pc.label)

    def test_sweep_preserves_other_store_live_write(self):
        self.store.write_json("race.json", {"value": "old"})
        other = Store(self.store.root)
        entered = threading.Event()
        release = threading.Event()
        sweeping = threading.Event()
        swept = threading.Event()
        errors = []
        fsync = os.fsync

        def block(fd):
            if threading.current_thread().name == "live-writer" and stat.S_ISREG(os.fstat(fd).st_mode):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("writer was not released")
            return fsync(fd)

        def write():
            try:
                self.store.write_json("race.json", {"value": "new"})
            except Exception as error:
                errors.append(error)

        def sweep():
            sweeping.set()
            try:
                other.sweep_temps("race.json")
            except Exception as error:
                errors.append(error)
            finally:
                swept.set()

        with patch("opos.store.os.fsync", side_effect=block):
            writer = threading.Thread(target=write, name="live-writer")
            sweeper = threading.Thread(target=sweep)
            writer.start()
            try:
                self.assertTrue(entered.wait(3))
                temporary = next(self.store.root.glob(".race.json.*.tmp"))
                # Simulate a writer suspended for arbitrarily long, beyond the old grace period.
                os.utime(temporary, (1, 1))
                sweeper.start()
                self.assertTrue(sweeping.wait(3))
                self.assertFalse(swept.wait(0.1), "sweep must wait for the writer's destination lock")
                self.assertTrue(temporary.exists())
            finally:
                release.set()
                writer.join(3)
                if sweeper.ident is not None:
                    sweeper.join(3)
        self.assertFalse(writer.is_alive() or sweeper.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(swept.is_set())
        self.assertEqual(other.read_json("race.json", {})[0]["value"], "new")
        self.assertFalse(list(self.store.root.glob(".race.json.*.tmp")))

    def test_pairing_crash_temps_are_swept_on_startup_and_forget(self):
        self.enrolled()
        self.link.close()
        for cleanup in ("startup", "forget"):
            with self.subTest(cleanup=cleanup):
                leftovers = [self.store.root / f".gpu-link-pc.json.{age}.tmp" for age in ("young", "old")]
                for temporary in leftovers:
                    temporary.write_bytes(b"old key material")
                os.utime(leftovers[1], (1, 1))
                if cleanup == "startup":
                    self.link = self.make_link()
                    self.assertEqual(self.link.snapshot()["pc"]["label"], self.pc.label)
                else:
                    self.assertTrue(self.link.forget())
                self.assertTrue(all(not temporary.exists() for temporary in leftovers))
                self.assertFalse(self.link._pairing_sweep_pending)

    def test_pairing_sweep_failure_loads_pc_and_retries(self):
        self.enrolled()
        self.link.close()
        temp = self.store.root / ".gpu-link-pc.json.stale.tmp"
        unlink = Path.unlink
        for retry in ("forget", "allow"):
            with self.subTest(retry=retry):
                temp.write_text("leftover", encoding="ascii")

                def blocked(path, *args, **kwargs):
                    if path == temp:
                        raise PermissionError(errno.EACCES, "unlink refused")
                    return unlink(path, *args, **kwargs)

                with patch.object(Path, "unlink", blocked):
                    self.link = self.make_link()
                self.assertEqual(self.link.snapshot()["pc"]["label"], self.pc.label)
                self.assertTrue(self.link._pairing_sweep_pending)
                self.assertTrue(temp.exists())
                if retry == "allow":
                    self.pc = self.replace_pc()
                    self.assertTrue(self.link.allow(self.pc.code))
                else:
                    self.assertTrue(self.link.forget())
                    self.pc = FakePC(self.link)
                    self.enrolled()
                self.assertFalse(temp.exists())
                self.assertFalse(self.link._pairing_sweep_pending)
                self.link.close()

    def test_successful_error_status_clears_storage_fault(self):
        answer, w = self.work()
        with patch.object(self.store, "write_json", side_effect=OSError(errno.ENOSPC, "full")):
            self.assertEqual(self.pc.status("error", w["lease"], error="engine").status, 500)
        self.assertEqual(self.link.snapshot()["storage"], "error")
        self.assertEqual(self.pc.status("error", w["lease"], error="engine").status, 200)
        self.assertEqual(self.answers.get(answer["id"])["state"], "failed")
        self.link.tick()
        self.assertIsNone(self.link.snapshot()["storage"])

    def test_metadata_save_does_not_hide_text_failure(self):
        answer, w = self.work()
        self.pc.text(w["lease"], 0, "abc")
        self.clock.advance(2)
        self.link._speed["values"]["tokens"] = 5
        write = self.store.write_json

        def selective(name, payload, *a, **kw):
            if payload.get("answer", {}).get("bytes", 0) > 3:
                raise OSError(errno.ENOSPC, "full at larger size")
            return write(name, payload, *a, **kw)

        with patch.object(self.store, "write_json", side_effect=selective):
            self.assertEqual(self.pc.text(w["lease"], 3, "def").status, 500)
            self.assertEqual(self.link.snapshot()["storage"], "error")
            self.assertEqual(self.pc.text(w["lease"], 0, "abc").status, 200)
            self.assertEqual(self.link.snapshot()["storage"], "error", "metadata-only save hid still-failing append")

    def test_phone_save_does_not_clear_gpu_fault_on_noop_repeat(self):
        phone = self.answers.ask("phone", "phone question")
        answer, w = self.work()
        self.pc.text(w["lease"], 0, "abc")
        original_write = self.store.write_json
        original_append = self.answers.append
        writes = []

        def write(name, payload, *a, **kw):
            writes.append(name)
            if payload.get("answer", {}).get("pane") == "gpu":
                raise OSError(errno.ENOSPC, "GPU file full")
            return original_write(name, payload, *a, **kw)

        with patch.object(self.store, "write_json", side_effect=write):
            self.assertEqual(self.pc.text(w["lease"], 3, "def").status, 500)
            self.assertEqual(self.link.snapshot()["storage"], "error")
            writes.clear()

            def interleave(*a, **kw):
                result = original_append(*a, **kw)
                t = threading.Thread(target=lambda: self.answers.note(phone["id"], tokens=1))
                t.start()
                t.join(3)
                self.assertFalse(t.is_alive())
                return result

            with patch.object(self.answers, "append", side_effect=interleave):
                self.assertEqual(self.pc.text(w["lease"], 0, "abc").status, 200)
            self.assertEqual(writes, [f"ai-answer-{phone['id']}.json"])
            self.assertEqual(self.link.snapshot()["storage"], "error", "unrelated phone save cleared GPU fault")

    def test_allow_invalidates_old_waiting_poll(self):
        self.enrolled()
        entered = threading.Event()
        result = []
        wait = self.link._changed.wait

        def waiting(*a, **kw):
            entered.set()
            return wait(*a, **kw)

        new = self.replace_pc()
        with patch.object(self.link._changed, "wait", side_effect=waiting):
            t = threading.Thread(target=lambda: result.append(self.pc.work(wait=1)))
            t.start()
            self.assertTrue(entered.wait(3))
            with self.link._changed:
                self.assertTrue(self.link.allow(new.code))
                self.answers.ask("gpu", "question intended for PC-B")
            t.join(3)
            self.assertFalse(t.is_alive())
        self.assertEqual(result[0].status, 204, f"old PC got {result[0].body!r}")

    def test_successful_forget_remains_cancelled_after_restart(self):
        answer, work = self.work()
        self.assertEqual(self.pc.text(work["lease"], 0, "private prefix").status, 200)
        self.assertTrue(self.link.forget())
        self.link.close()
        self.store = Store(self.store.root)
        self.answers = AnswerStore(self.store)
        self.link = self.make_link()
        self.pc = FakePC(self.link, key=b"N" * 32, label="PC-B")
        self.enrolled()
        self.assertEqual(self.pc.work().status, 204)
        row = self.answers.get(answer["id"])
        self.assertEqual((row["state"], row["text"], row["where"]), ("cancelled", "private prefix", "Test PC"))

    def test_first_pc_receives_untouched_queued_answer(self):
        answer = self.answers.ask("gpu", "queued before enrolment")
        self.enrolled()
        reply = self.pc.work()
        self.assertEqual(reply.status, 200)
        self.assertEqual(self.pc.data(reply)["question"], answer["question"])
        self.assertEqual(self.pc.data(reply)["partial"], "")

    def test_fault_belongs_to_one_answer_and_expires_when_it_ends(self):
        answer, work = self.work()
        with patch.object(self.store, "write_json", side_effect=OSError(errno.ENOSPC, "full")):
            self.assertEqual(self.pc.text(work["lease"], 0, "new").status, 500)
        other = self.answers.ask("gpu", "another question")
        self.link.cancel(other["id"])
        self.assertEqual(self.link.snapshot()["storage"], "error")
        # A terminal transition outside LinkService is noticed lazily.
        self.answers.cancel(answer["id"])
        self.assertIsNone(self.link.snapshot()["storage"])
        self.link._storage_fault = ""
        self.assertEqual(self.link.snapshot()["storage"], "error")
        self.link._storage_fault = "0" * 12
        self.assertIsNone(self.link.snapshot()["storage"])

    def test_forget_propagates_answer_durability_error(self):
        answer, work = self.work()
        with patch.object(self.answers, "cancel", side_effect=DurabilityError(errno.EIO, "directory sync failed")):
            with self.assertRaises(DurabilityError):
                self.link.forget()
        self.assertIsNotNone(self.link.snapshot()["pc"])
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.assertTrue(self.link.forget())
        self.assertEqual(self.answers.get(answer["id"])["state"], "cancelled")

    def test_reconciliation_failure_without_answer_sets_storage_error(self):
        with patch.object(self.answers, "current", side_effect=OSError(errno.EIO, "unreadable")):
            self.link.tick()
        self.assertEqual(self.link._storage_fault, "")
        self.assertEqual(self.link.snapshot()["storage"], "error")
        self.enrolled()
        self.answers.ask("phone", "unrelated")
        self.assertEqual(self.link.snapshot()["storage"], "error")

    def rejected(self, request):
        before = self.link.snapshot(), self.answers.snapshot()
        reply = self.pc.send(*request)
        self.assertEqual((reply.status, reply.body), (401, b""))
        self.assertNotIn("X-Link-Mac", dict(reply.headers))
        self.assertEqual((self.link.snapshot(), self.answers.snapshot()), before)

    def refuse_reads(self):
        """Make every binary read of the model file fail; writes still work."""
        original = Path.open

        def open_file(path, *args, **kwargs):
            if path == self.model and args[:1] == ("rb",):
                raise OSError("read refused")
            return original(path, *args, **kwargs)

        return patch.object(Path, "open", open_file)

    def test_status_under_a_replaced_lease_does_not_touch_the_answer(self):
        answer, first = self.work()
        second = self.pc.data(self.pc.work())
        self.assertEqual(self.pc.status("busy", first["lease"], tokens=5, tps=1.5).status, 404)
        row = self.answers.get(answer["id"])
        self.assertEqual((row["tokens"], row["tps"]), (None, None))
        self.assertEqual(self.pc.status("busy", second["lease"]).status, 200)
        self.assertEqual(self.pc.status("error", first["lease"], error="engine").status, 404)
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.assertEqual(self.pc.text(second["lease"], 0, "kept").status, 200)

    def test_new_lease_after_a_failed_one_is_not_failed(self):
        previous, _ = self.work()
        with patch.object(self.answers, "cancel", side_effect=OSError("disk")):
            self.assertTrue(self.link.forget())
        self.link.tick()
        self.assertEqual(self.answers.get(previous["id"])["state"], "cancelled")
        answer = self.answers.ask("gpu", "new question")
        self.clock.advance(1)
        self.enrolled()
        work = self.pc.data(self.pc.work())
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.assertEqual(self.pc.text(work["lease"], 0, "kept").status, 200)

    def test_forget_ends_the_open_answer_so_another_pc_never_receives_it(self):
        answer, work = self.work()
        self.assertEqual(self.pc.text(work["lease"], 0, "partial from A").status, 200)
        first = self.answers.get(answer["id"])["where"]
        self.assertTrue(self.link.forget())
        self.clock.advance(1)
        self.pc = FakePC(self.link, key=b"N" * 32, label="PC-B")
        self.enrolled()
        self.assertEqual(self.pc.work().status, 204)
        row = self.answers.get(answer["id"])
        self.assertEqual(row["state"], "cancelled")
        self.assertEqual((row["where"], row["text"]), (first, "partial from A"))
        self.assertIsNone(self.link._lease)

    def test_allow_replacement_ends_answer_before_persisting_new_pc(self):
        answer, work = self.work()
        self.assertEqual(self.pc.text(work["lease"], 0, "partial from A").status, 200)
        replacement = FakePC(self.link, key=b"N" * 32, label="PC-B")
        # Exercise allow's matching-pending path while an older PC is enrolled.
        self.link._pending = (replacement.label, replacement.key, self.clock())
        with patch.object(self.answers, "cancel", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.link.allow(replacement.code)
        self.assertEqual(self.link.snapshot()["pc"]["label"], self.pc.label)
        saved, _ = self.store.read_json("gpu-link-pc.json", {})
        self.assertEqual(saved["label"], self.pc.label)
        self.assertTrue(self.link.allow(replacement.code))
        self.pc = replacement
        self.assertEqual(self.pc.hello().status, 200)
        self.assertEqual(self.pc.work().status, 204)
        row = self.answers.get(answer["id"])
        self.assertEqual((row["state"], row["where"], row["text"]), ("cancelled", "Test PC", "partial from A"))

    def test_forget_keeps_only_never_handed_out_work(self):
        self.enrolled()
        for state, label in (("queued", ""), ("paused", ""), ("queued", "Test PC")):
            with self.subTest(state=state, label=label):
                answer = self.answers.ask("gpu", "question")
                if state == "paused":
                    self.answers.begin(answer["id"])
                    self.answers.pause(answer["id"])
                if label:
                    self.answers.note(answer["id"], where=label)
                self.assertTrue(self.link.forget())
                row = self.answers.get(answer["id"])
                self.assertEqual(row["state"], "waiting" if state == "queued" and not label else "cancelled")
                if row["state"] == "waiting":
                    self.answers.cancel(answer["id"])
                self.clock.advance(1)
                self.enrolled()

    def test_model_file_change_ends_the_live_lease_and_the_next_poll_starts_again(self):
        answer, first = self.work()
        self.assertEqual(self.pc.text(first["lease"], 0, "kept").status, 200)
        changed = b"another model file"
        self.model.write_bytes(changed)
        self.link.tick()
        self.link._hasher.join(2)
        self.link.tick()
        self.assertEqual(self.link.snapshot()["model"]["state"], "ready")
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
        self.assertEqual(self.pc.status("busy", first["lease"]).status, 404)
        reply = self.pc.work()
        self.assertEqual(reply.status, 200)
        work = self.pc.data(reply)
        self.assertNotEqual(work["lease"], first["lease"])
        self.assertEqual((work["model_sha256"], work["partial"]), (hashlib.sha256(changed).hexdigest(), "kept"))
        self.assertEqual(self.pc.status("busy", work["lease"]).status, 200)

    def test_tick_on_a_closed_service_leaves_an_answer_that_a_newer_service_runs(self):
        answer = self.answers.ask("gpu", "A test question")
        self.link.close()
        self.answers.begin(answer["id"])
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")

    def test_back_off_is_for_the_file_that_failed_and_lasts_five_seconds(self):
        with self.refuse_reads():
            self.model.write_bytes(b"changed")
            self.link.tick()
            first = self.link._hasher
            first.join(2)
            self.assertEqual(self.link.snapshot()["model"]["state"], "error")
            self.model.write_bytes(b"changed again, another size")
            self.link.tick()  # another file: read at once
            second = self.link._hasher
            self.assertIsNot(second, first)
            second.join(2)
            self.clock.advance(HASH_RETRY_SECONDS - 0.01)
            self.link.tick()
            self.assertIs(self.link._hasher, second)
            self.clock.advance(0.01)
            self.link.tick()
            self.assertIsNot(self.link._hasher, second)
            self.link._hasher.join(2)
        self.assertEqual(HASH_RETRY_SECONDS, 5.0)

    def test_status_that_names_a_replaced_lease_gets_404(self):
        _, first = self.work()
        self.pc.work()
        reply = self.pc.status("busy", first["lease"])
        self.assertEqual((reply.status, reply.body), (404, b'{"error":"lease"}'))

    def test_repeated_polls_do_not_evict_a_finished_lease(self):
        _, first = self.work()
        self.answers.ask("gpu", "the next question")
        self.assertEqual(self.pc.text(first["lease"], 0, "done", True, "stop").status, 200)
        for _ in range(HISTORY_LIMIT + 1):
            self.assertEqual(self.pc.work().status, 200)
        self.assertEqual(self.pc.text(first["lease"], 0, "done", True, "stop").status, 200)

    def test_tick_pauses_a_running_answer_that_has_no_lease(self):
        answer, _ = self.work()
        with patch.object(self.answers, "pause", side_effect=OSError("disk")):
            self.link.close()
        self.link = self.make_link()
        self.clock.advance(3)
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")

    def test_tick_retries_orphan_pause_without_a_model_or_pc(self):
        self.link.close()
        for state in ("loading", "answering"):
            with self.subTest(state=state):
                answer = self.answers.ask("gpu", "orphan")
                self.answers.begin(answer["id"])
                if state == "answering":
                    self.answers.append(answer["id"], 0, b"kept")
                link = self.make_link(model_path=None)
                for error in (OSError("disk"), gpu_link.answers_api.StateError("state")):
                    with patch.object(self.answers, "pause", side_effect=error):
                        link.tick()
                    self.assertEqual(self.answers.get(answer["id"])["state"], state)
                link.tick()
                self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
                self.assertEqual(self.answers.get(answer["id"])["text"], "kept" if state == "answering" else "")
                link.close()
                self.answers.cancel(answer["id"])
        waiting = self.answers.ask("gpu", "waiting")
        link = self.make_link(model_path=None)
        with patch.object(self.answers, "pause", side_effect=AssertionError("unexpected pause")):
            link.tick()
        self.assertEqual(self.answers.get(waiting["id"])["state"], "waiting")

    def test_lease_kept_by_forget_refuses_text_and_status(self):
        answer, work = self.work()
        with patch.object(self.answers, "pause", side_effect=OSError("disk")):
            self.assertTrue(self.link.forget())
        self.clock.advance(1)
        self.pc = FakePC(self.link, key=b"N" * 32)
        self.enrolled()
        self.assertEqual(self.pc.text(work["lease"], 0, "after forget").status, 404)
        self.assertEqual(self.answers.get(answer["id"])["text"], "")
        self.assertEqual(self.pc.status("busy", work["lease"]).status, 404)

    def test_back_off_does_not_leave_ready_for_another_file(self):
        first, other, keep = self.link._model_hash, self.root / "other.gguf", self.root / "keep.gguf"
        other.write_bytes(b"another model")
        with self.refuse_reads():
            self.model.rename(keep)
            other.rename(self.model)
            self.link.tick()
            self.link._hasher.join(2)
            self.assertEqual(self.link.snapshot()["model"]["state"], "error")
            self.model.rename(other)
            keep.rename(self.model)
            self.link.tick()
            self.assertEqual(self.link.snapshot()["model"]["state"], "ready")
            self.model.rename(keep)
            other.rename(self.model)
            self.link.tick()
            self.assertEqual(self.link._model_hash, first)
            self.assertNotEqual(self.link.snapshot()["model"]["state"], "ready")

    def test_close_pauses_a_running_answer_that_has_no_lease(self):
        # gpu_link.py close(): the else branch. A previous service left the answer running;
        # this service never leased it, yet close must pause it (also while "answering").
        for state in ("loading", "answering"):
            with self.subTest(state=state):
                answer = self.answers.ask("gpu", f"{state} question")
                self.answers.begin(answer["id"])
                if state == "answering":
                    self.answers.append(answer["id"], 0, b"kept")
                self.assertEqual(self.answers.get(answer["id"])["state"], state)
                self.assertIsNone(self.link._lease)
                link = self.make_link()
                link.close()
                self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
                self.assertEqual(self.answers.get(answer["id"])["text"], "kept" if state == "answering" else "")
                self.answers.cancel(answer["id"])

    def test_close_leaves_a_waiting_answer_without_a_lease_alone(self):
        # gpu_link.py close(): only loading/answering are paused.
        answer = self.answers.ask("gpu", "still waiting")
        link = self.make_link()
        with patch.object(self.answers, "pause", side_effect=AssertionError("unexpected pause")):
            link.close()
        self.assertEqual(self.answers.get(answer["id"])["state"], "waiting")

    def test_close_with_a_lease_releases_it_through_the_lease_path(self):
        # gpu_link.py close(): with a lease, _pause (which clears the lease) runs, not the pane fallback.
        answer, work = self.work()
        self.assertIsNotNone(self.link._lease)
        with patch.object(self.answers, "current", side_effect=AssertionError("fallback used")):
            self.link.close()
        self.assertIsNone(self.link._lease)
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")

    def test_close_stops_the_file_reader_promptly(self):
        # gpu_link.py close(): _hash_stop.set(). A long read must end after the current chunk.
        other = self.root / "slow.gguf"
        other.write_bytes(b"data")
        entered, release = threading.Event(), threading.Event()
        reads = []

        class Slow:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, size):
                reads.append(1)
                entered.set()
                if not release.wait(3):
                    raise OSError("reader gate timed out")
                return b"x" if len(reads) < 300 else b""

        real = Path.open

        def open_file(path, *args, **kwargs):
            return Slow() if path == other else real(path, *args, **kwargs)

        with patch.object(Path, "open", open_file):
            link = LinkService(self.answers, self.store, other, clock=self.clock, rand=self.rand)
            self.addCleanup(link.close)
            self.addCleanup(release.set)
            self.assertTrue(entered.wait(2))
            closer = threading.Thread(target=link.close)
            closer.start()
            try:
                self.assertTrue(link._hash_stop.wait(1))
            finally:
                release.set()
                closer.join(3)
            self.assertFalse(closer.is_alive())
        self.assertFalse(link._hasher.is_alive())
        self.assertEqual(len(reads), 1)

    def test_close_survives_any_answer_store_error_on_either_path(self):
        # gpu_link.py close(): the guard covers AnswerError as well as OSError, with and without a lease.
        answer, _ = self.work()
        with patch.object(self.answers, "pause", side_effect=AnswerError("refused")):
            self.link.close()
        orphan = self.make_link()
        other = self.answers.ask("gpu", "orphan")
        self.answers.cancel(answer["id"])
        self.answers.begin(other["id"])
        with patch.object(self.answers, "pause", side_effect=AnswerError("refused")):
            orphan.close()
        self.assertEqual(self.answers.get(other["id"])["state"], "loading")

    def test_non_final_text_post_does_not_defeat_the_speed_throttle(self):
        # gpu_link.py _text(): only a final post flushes throttled speed values.
        answer, work = self.work()
        self.pc.status("busy", work["lease"], tokens=12, tps=3.5)
        self.clock.advance(0.5)
        self.pc.status("busy", work["lease"], tokens=15, tps=4)
        with patch.object(self.answers, "note", wraps=self.answers.note) as note:
            self.assertEqual(self.pc.text(work["lease"], 0, "partial").status, 200)
        note.assert_not_called()
        self.assertEqual(self.answers.get(answer["id"])["tokens"], 12)

    def test_speed_throttle_starts_with_either_value_alone(self):
        # gpu_link.py _note_speed(): a write of tokens alone, or tps alone, starts the one-second throttle.
        answer, work = self.work()
        for index, (first, second) in enumerate(((dict(tokens=5), dict(tokens=6)), (dict(tps=2.5), dict(tps=3.5)))):
            with self.subTest(first=first):
                if index:
                    answer = self.answers.ask("gpu", "second question")
                    work = self.pc.data(self.pc.work())
                self.pc.status("busy", work["lease"], **first)
                self.assertEqual(self.answers.get(answer["id"])[next(iter(first))], next(iter(first.values())))
                self.clock.advance(0.5)
                with patch.object(self.answers, "note", wraps=self.answers.note) as note:
                    self.pc.status("busy", work["lease"], **second)
                note.assert_not_called()
                self.pc.text(work["lease"], 0, "x", True, "stop")

    def test_a_successful_hash_clears_an_earlier_failure_record(self):
        # gpu_link.py _hash_model(): after file B hashed fine, going back to the failed file A within the
        # retry window must hash A again instead of leaving B's digest ready for A.
        original = Path.open
        a_bytes = b"contents A " * 8
        self.model.write_bytes(a_bytes)
        a_mtime = self.model.stat().st_mtime_ns

        def refuse(path, *args, **kwargs):
            if path == self.model:
                raise OSError("read refused")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", refuse):
            self.link.tick()
            self.link._hasher.join(2)
        self.assertEqual(self.link.snapshot()["model"]["state"], "error")
        self.model.write_bytes(b"contents B, longer " * 8)
        self.link.tick()
        self.link._hasher.join(2)
        self.assertEqual(self.link.snapshot()["model"]["state"], "ready")
        self.model.write_bytes(a_bytes)
        os.utime(self.model, ns=(a_mtime, a_mtime))
        self.link.tick()
        if self.link._hasher is not None:
            self.link._hasher.join(2)
        self.link.tick()
        self.assertEqual(self.link._model_hash, hashlib.sha256(a_bytes).hexdigest())

    def test_failed_hash_is_retried_exactly_when_the_window_ends(self):
        # gpu_link.py _check_model(): blocked while clock < retry_at, retried at clock == retry_at.
        self.model.write_bytes(b"changed")
        original = Path.open

        def refuse(path, *args, **kwargs):
            if path == self.model:
                raise OSError("read refused")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", refuse):
            self.link.tick()
            first = self.link._hasher
            first.join(2)
            self.assertEqual(self.link.snapshot()["model"]["state"], "error")
            self.clock.advance(HASH_RETRY_SECONDS / 2)
            self.link.tick()
            self.assertIs(self.link._hasher, first)
        self.clock.advance(HASH_RETRY_SECONDS / 2)
        self.link.tick()
        self.assertIsNot(self.link._hasher, first)
        self.link._hasher.join(2)

    def test_replaced_lease_refuses_delayed_text_without_pausing(self):
        answer, first = self.work()
        self.assertEqual(self.pc.text(first["lease"], 0, "prefix é").status, 200)
        before = self.answers.get(answer["id"])
        with patch.object(self.answers, "pause", side_effect=AssertionError("unexpected pause")):
            reply = self.pc.work()
        self.assertEqual(reply.status, 200)
        second = self.pc.data(reply)
        self.assertNotEqual(second["lease"], first["lease"])
        self.assertEqual(second["partial"], before["text"])
        self.assertNotIn(bytes.fromhex(first["lease"]), self.link._history)
        self.assertEqual(self.pc.text(first["lease"], before["bytes"], "old run").status, 404)
        self.assertEqual(self.answers.get(answer["id"]), before)
        self.assertEqual(self.pc.text(second["lease"], before["bytes"], "new run").status, 200)
        self.assertEqual(self.answers.get(answer["id"])["text"], "prefix énew run")

    def test_close_pauses_before_new_service_reuses_answer_store(self):
        answer, first = self.work()
        self.pc.text(first["lease"], 0, "kept")
        self.link.close()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
        self.link = self.make_link()
        self.pc.link = self.link
        self.pc.hello()
        reply = self.pc.work()
        self.assertEqual(reply.status, 200)
        self.assertEqual(self.pc.data(reply)["partial"], "kept")
        self.assertNotEqual(self.pc.data(reply)["lease"], first["lease"])

    def test_repeated_close_does_not_pause_work_from_new_service(self):
        answer, _ = self.work()
        old = self.link
        old.close()
        self.link = self.make_link()
        self.pc.link = self.link
        self.pc.hello()
        reply = self.pc.work()
        self.assertEqual(reply.status, 200)
        old.close()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.assertEqual(self.pc.text(self.pc.data(reply)["lease"], 0, "kept").status, 200)

    def test_cancel_after_forget_with_failed_pause_needs_no_pc(self):
        answer, work = self.work()
        with patch.object(self.answers, "cancel", side_effect=OSError("disk")):
            self.assertTrue(self.link.forget())
        self.assertIsNone(self.link.snapshot()["pc"])
        self.assertIsNotNone(self.link._lease)
        self.assertEqual(self.link._lease["id"].hex(), work["lease"])
        result = self.link.cancel(answer["id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertIsNone(self.link._lease)
        self.assertFalse(self.link._history)

    @unittest.skipUnless(os.name == "posix", "requires POSIX directory fsync")
    def test_forget_directory_fsync_failure_keeps_pc_for_retry(self):
        self.enrolled()
        fsync = os.fsync

        def synced(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("directory flush failed")
            return fsync(fd)

        with patch("opos.store.os.fsync", synced), self.assertRaises(OSError):
            self.link.forget()
        self.assertEqual(self.link.snapshot()["pc"]["label"], self.pc.label)
        self.assertTrue(self.link.forget())
        self.assertIsNone(self.link.snapshot()["pc"])

    def test_forget_removes_pairing_temp(self):
        self.enrolled()
        (self.store.root / ".gpu-link-pc.json.stale.tmp").write_text(self.pc.key.hex(), encoding="ascii")
        self.assertTrue(self.link.forget())
        self.assertFalse(list(self.store.root.glob(".gpu-link-pc.json.*.tmp")))
        self.assertFalse(any(self.pc.key.hex().encode() in path.read_bytes() for path in self.store.root.iterdir()))

    def test_pairing_load_removes_pairing_temp(self):
        self.enrolled()
        (self.store.root / ".gpu-link-pc.json.stale.tmp").write_text(self.pc.key.hex(), encoding="ascii")
        os.utime(self.store.root / ".gpu-link-pc.json.stale.tmp", (1, 1))
        self.link.close()
        self.link = self.make_link()
        self.assertEqual(self.link.snapshot()["pc"]["label"], self.pc.label)
        self.assertFalse(list(self.store.root.glob(".gpu-link-pc.json.*.tmp")))

    def test_storage_fault_survives_status_until_text_is_saved(self):
        answer, work = self.work()
        lease = work["lease"]
        self.assertEqual(self.pc.text(lease, 0, "abc").status, 200)
        error = OSError(errno.ENOSPC, "storage full")
        with patch.object(self.store, "write_json", side_effect=error):
            for _ in range(21):
                reply = self.pc.text(lease, 3, "def")
                self.assertEqual((reply.status, self.pc.data(reply)), (500, {"error": "state"}))
                self.assertEqual(self.link.snapshot()["storage"], "error")
                self.assertEqual(self.pc.status("busy", lease).status, 200)
                self.link.tick()
                self.assertEqual(self.link.snapshot()["storage"], "error")
                row = self.answers.get(answer["id"])
                self.assertEqual((row["state"], row["text"]), ("answering", "abc"))
                self.clock.advance(1)
        # A repeat that writes nothing must not clear the fault.
        self.assertEqual(self.pc.text(lease, 0, "abc").status, 200)
        self.assertEqual(self.link.snapshot()["storage"], "error")
        self.assertEqual(self.pc.text(lease, 3, "def").status, 200)
        self.assertIsNone(self.link.snapshot()["storage"])
        self.assertEqual(self.pc.text(lease, 6, "", True, "stop").status, 200)
        self.assertEqual(self.answers.get(answer["id"])["state"], "done")

    def test_storage_fault_on_offer_clears_when_begin_is_saved(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        with patch.object(self.store, "write_json", side_effect=OSError(errno.ENOSPC, "storage full")):
            reply = self.pc.work()
        self.assertEqual((reply.status, self.pc.data(reply)), (500, {"error": "state"}))
        self.assertEqual(self.link.snapshot()["storage"], "error")
        self.assertEqual(self.answers.get(answer["id"])["state"], "waiting")
        self.assertEqual(self.pc.work().status, 200)
        self.assertIsNone(self.link.snapshot()["storage"])

    def test_storage_fault_survives_offer_or_text_metadata_retry(self):
        self.enrolled()
        for route in ("offer", "text"):
            with self.subTest(route=route):
                answer = self.answers.ask("gpu", "question")
                with patch.object(self.answers, "note", side_effect=OSError("storage full")):
                    work = self.pc.data(self.pc.work())
                self.answers.append(answer["id"], 0, b"abc")
                with patch.object(self.store, "write_json", side_effect=OSError(errno.ENOSPC, "storage full")):
                    self.assertEqual(self.pc.text(work["lease"], 3, "def").status, 500)
                self.assertEqual(self.link.snapshot()["storage"], "error")
                reply = self.pc.work() if route == "offer" else self.pc.text(work["lease"], 0, "abc")
                self.assertEqual(reply.status, 200)
                if route == "offer":
                    work = self.pc.data(reply)
                self.assertEqual(self.link.snapshot()["storage"], "error")
                self.assertEqual(self.answers.get(answer["id"])["where"], self.pc.label)
                self.assertEqual(self.pc.text(work["lease"], 3, "def").status, 200)
                self.assertIsNone(self.link.snapshot()["storage"])
                self.link.cancel(answer["id"])
                self.clock.advance(1)

    def test_tick_storage_fault_clears_after_pause_or_cancel(self):
        for orphan, cancel in ((False, False), (True, False), (False, True)):
            with self.subTest(orphan=orphan, cancel=cancel):
                self.empty_link_answer_for_fault_test()
                answer = self.answers.ask("gpu", "question")
                self.assertEqual(self.pc.work().status, 200)
                if orphan:
                    self.link._lease = None
                self.clock.advance(LINK_SECONDS)
                with patch.object(self.store, "write_json", side_effect=OSError(errno.ENOSPC, "storage full")):
                    self.link.tick()
                self.assertEqual(self.link.snapshot()["storage"], "error")
                if cancel:
                    self.link.cancel(answer["id"])
                else:
                    self.link.tick()
                self.assertIsNone(self.link.snapshot()["storage"])
                self.assertEqual(self.answers.get(answer["id"])["state"], "cancelled" if cancel else "paused")

    def empty_link_answer_for_fault_test(self):
        answer = self.answers.current("gpu")
        if answer is not None:
            self.link.cancel(answer["id"])
        if self.link.snapshot()["pc"] is None:
            self.enrolled()

    def test_provenance_is_stored_without_speed_values(self):
        answer, work = self.work()
        expected = self.pc.label, self.model.stem
        row = self.answers.get(answer["id"])
        self.assertEqual((row["where"], row["model"]), expected)
        self.assertEqual(self.pc.status("busy", work["lease"]).status, 200)
        self.assertEqual(self.pc.text(work["lease"], 0, "done", True, "stop").status, 200)
        self.link.forget()
        row = AnswerStore(self.store).get(answer["id"])
        self.assertEqual((row["where"], row["model"]), expected)
        self.assertIsNone(row["tokens"])
        self.assertIsNone(row["tps"])

    def test_provenance_write_failure_retries_on_text_without_speed(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        with patch.object(self.answers, "note", side_effect=OSError("disk")):
            reply = self.pc.work()
        self.assertEqual(reply.status, 200)
        lease = self.pc.data(reply)["lease"]
        self.assertEqual(self.answers.get(answer["id"])["where"], "")
        self.assertEqual(self.pc.text(lease, 0, "done", True, "stop").status, 200)
        row = self.answers.get(answer["id"])
        self.assertEqual((row["where"], row["model"]), (self.pc.label, self.model.stem))

    def test_close_failed_pause_is_reconciled_by_new_service(self):
        answer, _ = self.work()
        with patch.object(self.answers, "pause", side_effect=OSError("disk")):
            self.link.close()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.link = self.make_link()
        self.pc.link = self.link
        self.pc.hello()
        self.assertEqual(self.pc.work().status, 200)
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")

    def test_hash_read_failure_backs_off_then_retries(self):
        self.model.write_bytes(b"changed")
        original = Path.open

        def open_file(path, *args, **kwargs):
            if path == self.model:
                raise OSError("read refused")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", open_file):
            self.link.tick()
            first = self.link._hasher
            first.join(2)
            self.assertEqual(self.link.snapshot()["model"]["state"], "error")
            for _ in range(20):
                self.clock.advance(0.1)
                self.link.tick()
                self.assertIs(self.link._hasher, first)
        self.clock.advance(60)
        self.link.tick()
        self.assertIsNot(self.link._hasher, first)
        self.link._hasher.join(2)
        self.assertEqual(self.link.snapshot()["model"]["state"], "ready")

    def test_closed_service_does_not_publish_hash(self):
        other = self.root / "closed.gguf"
        other.write_bytes(b"data")
        entered, release = threading.Event(), threading.Event()
        original = LinkService._hash_model

        def hash_later(link, identity):
            entered.set()
            release.wait(2)
            original(link, identity)

        store = Store(self.root / "closed-state")
        with patch.object(LinkService, "_hash_model", hash_later):
            link = LinkService(AnswerStore(store), store, other, clock=self.clock, rand=self.rand)
            self.addCleanup(link.close)
            self.assertTrue(entered.wait(2))
            # Stop before the file reader starts, as close does under its lock.
            with link._lock:
                link._closed = True
                link._hash_stop.set()
            release.set()
            link.close()
        self.assertEqual(store.read_json("gpu-link-model.json", {})[0], {})
        self.assertNotEqual(link.snapshot()["model"]["state"], "ready")

    def test_forget_and_allow_rotate_epoch_and_refuse_recorded_request(self):
        initial = self.pc.epoch
        self.enrolled()
        enrolled = self.pc.epoch
        self.assertNotEqual(initial, enrolled)
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        self.assertEqual(self.pc.send(*request).status, 204)
        self.assertTrue(self.link.forget())
        self.assertEqual(self.pc.hello().status, 200)
        forgotten = self.pc.epoch
        self.assertNotEqual(enrolled, forgotten)
        self.enrolled()
        self.assertNotIn(self.pc.epoch, (initial, enrolled, forgotten))
        self.assertEqual(self.link._nonces, set())
        self.rejected(request)
        self.assertEqual(self.pc.work().status, 204)

    def test_waiting_key_uses_constant_time_comparison(self):
        self.assertEqual(self.pc.enrol().status, 202)
        with patch("opos.gpu_link.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertEqual(self.pc.enrol().status, 202)
            compare.assert_called_once_with(self.pc.key, self.pc.key)
        self.clock.advance(1)
        other = FakePC(self.link, key=b"B" * 32)
        with patch("opos.gpu_link.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertEqual(other.enrol().status, 409)
            compare.assert_called_once_with(self.pc.key, other.key)

    def test_external_answer_completion_drops_lease_on_tick_or_forget(self):
        self.enrolled()
        for action in ("tick", "forget"):
            for final in ("done", "cancelled"):
                with self.subTest(action=action, final=final):
                    answer = self.answers.ask("gpu", "external completion")
                    self.assertEqual(self.pc.work().status, 200)
                    if final == "done":
                        self.answers.append(answer["id"], 0, b"done", True, "stop")
                    else:
                        self.answers.cancel(answer["id"])
                    self.clock.advance(3)
                    if action == "forget":
                        self.assertTrue(self.link.forget())
                        self.assertIsNone(self.link.snapshot()["pc"])
                        self.assertNotIn("key", self.store.read_json("gpu-link-pc.json", {})[0])
                    else:
                        self.link.tick()
                        self.link.tick()
                    self.assertIsNone(self.link._lease)
                    self.assertEqual(self.answers.get(answer["id"])["state"], final)
                    if action == "forget":
                        self.enrolled()

    def test_forget_removes_pc_despite_unexpected_or_storage_pause_failure(self):
        self.enrolled()
        for error in (OSError("disk"), AnswerError("answer"), RuntimeError("unexpected")):
            with self.subTest(error=type(error).__name__):
                answer = self.answers.ask("gpu", "cannot pause")
                self.assertEqual(self.pc.work().status, 200)
                with patch.object(self.answers, "cancel", side_effect=error):
                    self.assertTrue(self.link.forget())
                self.assertIsNotNone(self.link._lease)
                self.assertTrue(self.link._lease["failed"])
                self.link.tick()
                self.assertIsNone(self.link._lease)
                self.assertEqual(self.answers.get(answer["id"])["state"], "cancelled")
                self.assertIsNone(self.link.snapshot()["pc"])
                self.assertNotIn("key", self.store.read_json("gpu-link-pc.json", {})[0])
                self.clock.advance(1)
                self.enrolled()

    def test_unexpected_store_errors_get_signed_request_error(self):
        self.enrolled()
        # Each store entry reached by an authenticated request, including the
        # best-effort metadata path (where expected write failures stay ignored).
        for method in ("current", "begin", "get", "append", "fail", "pause", "note", "read_json"):
            errors = (RuntimeError("unexpected"),)
            if method not in ("note",):
                errors += (AnswerError("unexpected"),)
            for error in errors:
                with self.subTest(method=method, error=type(error).__name__):
                    answer = self.answers.ask("gpu", "store failure")
                    work = None
                    if method not in ("current", "begin", "read_json"):
                        work = self.pc.data(self.pc.work())
                    if method == "pause":
                        self.assertEqual(self.pc.status().status, 200)
                    owner = self.store if method == "read_json" else self.answers
                    if method == "read_json":
                        self.link._model["state"] = "checking"
                    with patch.object(owner, method, side_effect=error):
                        if method == "append":
                            reply = self.pc.text(work["lease"], 0, "x")
                        elif method == "fail":
                            reply = self.pc.status("error", work["lease"], error="engine")
                        elif method == "pause":
                            reply = self.pc.status()
                        elif method == "note":
                            reply = self.pc.status("busy", work["lease"], tokens=1)
                        elif method == "read_json":
                            reply = self.pc.model(self.sha)
                        else:
                            reply = self.pc.work()
                    self.assertEqual((reply.status, self.pc.data(reply)), (400, {"error": "request"}))
                    self.link.cancel(answer["id"])
                    self.clock.advance(1)
                    self.link.tick()

    def test_speed_notes_answer_with_missing_metadata_field(self):
        answer, work = self.work()
        incomplete = self.answers.get(answer["id"])
        del incomplete["tokens"]
        with patch.object(self.answers, "get", return_value=incomplete):
            self.assertEqual(self.pc.status("busy", work["lease"], tokens=12).status, 200)
        self.assertEqual(self.answers.get(answer["id"])["tokens"], 12)

    def test_status_speed_notes_live_answer_without_changing_text_or_state(self):
        answer, work = self.work()
        self.link.set_model_name("Test model")
        self.pc.text(work["lease"], 0, "partial")
        before = self.answers.get(answer["id"])
        with patch.object(self.answers, "note", wraps=self.answers.note) as note:
            self.assertEqual(self.pc.status("busy", work["lease"], tokens=12, tps=3.5).status, 200)
        note.assert_called_once_with(answer["id"], tokens=12, tps=3.5, model="Test model", where=self.pc.label)
        after = self.answers.get(answer["id"])
        self.assertEqual(
            (after["tokens"], after["tps"], after["model"], after["where"]), (12, 3.5, "Test model", self.pc.label)
        )
        self.assertEqual((after["text"], after["state"]), (before["text"], before["state"]))

    def test_status_speed_ignores_missing_wrong_and_ended_leases(self):
        answer, work = self.work()
        for lease in (None, "ff" * 16):
            before = self.answers.snapshot()
            with patch.object(self.answers, "note", wraps=self.answers.note) as note:
                self.pc.status("busy", lease, tokens=12, tps=3.5)
            note.assert_not_called()
            self.assertEqual(self.answers.snapshot(), before)
            self.pc.status("busy", work["lease"])
        self.pc.text(work["lease"], 0, "done", True, "stop")
        before = self.answers.snapshot()
        self.assertEqual(self.pc.status("idle", work["lease"], tokens=99, tps=9).status, 200)
        self.assertEqual(self.answers.snapshot(), before)
        next_answer = self.answers.ask("gpu", "next")
        next_work = self.pc.data(self.pc.work())
        self.link.cancel(next_answer["id"])
        before = self.answers.snapshot()
        self.assertEqual(self.pc.status(lease=next_work["lease"], tokens=99, tps=9).status, 200)
        self.assertEqual(self.answers.snapshot(), before)
        self.assertIsNone(self.answers.get(answer["id"])["tokens"])

    def test_status_speed_throttles_changes_and_skips_unchanged_values(self):
        answer, work = self.work()
        with patch.object(self.store, "write_json", wraps=self.store.write_json) as write:
            self.pc.status("busy", work["lease"], tokens=12, tps=3.5)
            self.clock.advance(0.5)
            self.pc.status("busy", work["lease"], tokens=15, tps=4)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(self.answers.get(answer["id"])["tokens"], 12)
            self.clock.advance(0.5)
            self.pc.status("busy", work["lease"], tokens=15, tps=4)
            self.assertEqual(write.call_count, 2)
            self.clock.advance(1)
            self.pc.status("busy", work["lease"], tokens=15, tps=4)
            self.pc.status("busy", work["lease"])
            self.assertEqual(write.call_count, 2)

    def test_status_speed_partial_values_and_final_flush_are_durable(self):
        answer, work = self.work()
        self.pc.status("busy", work["lease"], tokens=0)
        self.assertEqual(self.answers.get(answer["id"])["tokens"], 0)
        self.clock.advance(0.1)
        self.pc.status("busy", work["lease"], tokens=20, tps=4.5)
        self.pc.status("busy", work["lease"], tps=0)
        with patch.object(self.answers, "note", wraps=self.answers.note) as note:
            self.assertEqual(self.pc.text(work["lease"], 0, "done", True, "stop").status, 200)
            self.assertEqual(self.pc.text(work["lease"], 0, "done", True, "stop").status, 200)
        self.assertEqual(note.call_count, 1)
        final = AnswerStore(self.store).get(answer["id"])
        self.assertEqual((final["state"], final["text"], final["tokens"], final["tps"]), ("done", "done", 20, 0))

    def test_status_speed_note_failure_is_best_effort_and_retried(self):
        answer, work = self.work()
        self.pc.text(work["lease"], 0, "partial")
        before = self.answers.snapshot()
        with patch.object(self.answers, "note", side_effect=OSError("disk")):
            self.assertEqual(self.pc.status("busy", work["lease"], tokens=12, tps=3.5).status, 200)
        self.assertEqual(self.answers.snapshot(), before)
        self.assertEqual(self.link.snapshot()["pc"]["state"], "busy")
        self.assertEqual(self.pc.text(work["lease"], 7, " done", True, "stop").status, 200)
        final = self.answers.get(answer["id"])
        self.assertEqual((final["tokens"], final["tps"]), (12, 3.5))

    def test_status_speed_throttle_follows_answer_across_leases(self):
        answer, work = self.work()
        self.pc.status("busy", work["lease"], tokens=12, tps=3.5)
        self.pc.status()
        self.pc.status()
        resumed = self.pc.data(self.pc.work())
        with patch.object(self.answers, "note", wraps=self.answers.note) as note:
            self.pc.status("busy", resumed["lease"], tokens=15, tps=4)
        note.assert_not_called()
        self.assertEqual(self.answers.get(answer["id"])["tokens"], 12)
        self.pc.text(resumed["lease"], 0, "done", True, "stop")
        next_answer = self.answers.ask("gpu", "next")
        next_work = self.pc.data(self.pc.work())
        self.pc.status("busy", next_work["lease"], tps=2)
        value = self.answers.get(next_answer["id"])
        self.assertEqual((value["tokens"], value["tps"]), (None, 2))

    def test_enrol_second_waiting(self):
        self.assertEqual(self.pc.enrol().status, 202)
        other = FakePC(self.link, key=b"B" * 32)
        self.assertEqual(other.data(other.enrol()), {"state": "busy"})
        self.assertEqual(self.link.snapshot()["pending"]["code"], proto.code(self.pc.label, self.pc.key))

    def test_owner_answer_rejects_invalid_and_wrong_codes_without_changes(self):
        self.assertEqual(self.pc.enrol().status, 202)
        wrong = f"{(int(self.pc.code) + 1) % 1000000:06d}"
        before = self.link.snapshot(), self.answers.snapshot(), self.link._denials
        for answer in (self.link.allow, self.link.deny):
            for code in (wrong, "", None, int(self.pc.code), "12345", "1234567", "12a456", "１２３４５６"):
                with self.subTest(answer=answer.__name__, code=code):
                    with patch.object(self.store, "write_json", wraps=self.store.write_json) as write:
                        self.assertFalse(answer(code))
                    write.assert_not_called()
                    self.assertEqual((self.link.snapshot(), self.answers.snapshot(), self.link._denials), before)
        self.assertTrue(self.link.allow(self.pc.code))

    def test_owner_answer_without_waiting_request_changes_nothing(self):
        before = self.link.snapshot(), self.link._denials
        with patch.object(self.store, "write_json", wraps=self.store.write_json) as write:
            self.assertFalse(self.link.allow(self.pc.code))
            self.assertFalse(self.link.deny(self.pc.code))
        write.assert_not_called()
        self.assertEqual((self.link.snapshot(), self.link._denials), before)

    def test_stale_owner_answers_leave_replacement_waiting(self):
        self.assertEqual(self.pc.enrol().status, 202)
        displayed_code = self.link.snapshot()["pending"]["code"]
        self.clock.advance(60)
        other = FakePC(self.link, key=b"B" * 32, label="PC B")
        self.assertEqual(other.enrol().status, 202)
        self.assertNotEqual(displayed_code, other.code)
        before = self.link.snapshot(), self.answers.snapshot(), self.link._denials
        for answer in (self.link.allow, self.link.deny):
            with self.subTest(answer=answer.__name__):
                with patch.object(self.store, "write_json", wraps=self.store.write_json) as write:
                    self.assertFalse(answer(displayed_code))
                write.assert_not_called()
                self.assertEqual((self.link.snapshot(), self.answers.snapshot(), self.link._denials), before)
                self.assertFalse((self.store.root / "gpu-link-pc.json").exists())
        self.assertTrue(self.link.deny(other.code))

    def test_expired_owner_answers_change_nothing_before_tick(self):
        self.pc.enrol()
        self.clock.advance(60)
        before = self.link.snapshot(), self.link._denials
        self.assertFalse(self.link.allow(self.pc.code))
        self.assertFalse(self.link.deny(self.pc.code))
        self.assertEqual((self.link.snapshot(), self.link._denials), before)
        self.assertFalse((self.store.root / "gpu-link-pc.json").exists())
        self.link.tick()
        self.assertIsNone(self.link.snapshot()["pending"])
        self.assertEqual(self.link._denials, 1)

    def test_three_wrong_codes_do_not_close_enrolling(self):
        self.pc.enrol()
        wrong = f"{(int(self.pc.code) + 1) % 1000000:06d}"
        for _ in range(3):
            self.assertFalse(self.link.allow(wrong))
            self.assertFalse(self.link.deny(wrong))
        self.assertEqual(self.link._denials, 0)
        self.assertEqual(self.link.snapshot()["enrol"], "open")
        self.assertEqual(self.link.snapshot()["pending"]["code"], self.pc.code)
        self.assertTrue(self.link.deny(self.pc.code))
        self.assertEqual(self.pc.enrol().status, 202)

    def test_enrol_exact_retry_does_not_extend(self):
        self.pc.enrol()
        self.clock.advance(59)
        self.assertEqual(self.pc.enrol().status, 202)
        self.assertEqual(self.link.snapshot()["pending"]["seconds"], 1)
        self.clock.advance(1)
        self.link.tick()
        self.assertIsNone(self.link.snapshot()["pending"])
        self.assertFalse(self.link.allow(self.pc.code))

    def test_three_denials_close_and_reopen(self):
        for _ in range(3):
            self.clock.advance(1)
            self.assertEqual(self.pc.enrol().status, 202)
            self.assertTrue(self.link.deny(self.pc.code))
        self.clock.advance(1)
        self.assertEqual(self.pc.enrol().status, 403)
        self.link.reopen()
        self.assertEqual(self.pc.enrol().status, 202)
        self.assertTrue(self.link.deny(self.pc.code))
        self.assertFalse(self.link.deny(self.pc.code))

    def test_three_expiries_close_and_reopen(self):
        for _ in range(3):
            self.assertEqual(self.pc.enrol().status, 202)
            self.clock.advance(60)
            self.link.tick()
        self.assertEqual(self.link.snapshot()["enrol"], "closed")
        self.assertEqual(self.pc.enrol().status, 403)
        self.link.reopen()
        self.assertEqual(self.pc.enrol().status, 202)

    def test_waiting_key_is_not_accepted(self):
        self.pc.enrol()
        self.rejected(self.pc.prepare("GET", "/v1/work?wait=0"))

    def test_second_pc_when_enrolled(self):
        self.enrolled()
        other = FakePC(self.link, key=b"B" * 32)
        self.assertEqual(other.enrol().status, 409)
        self.clock.advance(1)
        self.assertEqual(other.data(other.enrol()), {"state": "enrolled"})

    def test_enrol_body_limit_before_parse(self):
        with patch.object(proto, "parse_enrol", side_effect=AssertionError("parsed")):
            reply = self.pc.send("POST", "/v1/enrol", body=b"x" * 1025)
        self.assertEqual(reply.status, 413)
        self.assertIsNone(self.link.snapshot()["pending"])

    def test_allow_one_write_mode_and_restart(self):
        self.pc.enrol()
        with patch.object(self.store, "write_json", wraps=self.store.write_json) as write:
            self.assertTrue(self.link.allow(self.pc.code))
        self.assertEqual(write.call_count, 1)
        path = self.store.root / "gpu-link-pc.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.link.close()
        restarted = self.make_link()
        pc = FakePC(restarted)
        pc.hello()
        self.assertEqual(pc.work().status, 204)
        self.assertEqual(restarted.snapshot()["pc"]["label"], "Test PC")

    def test_allow_failure_keeps_pending(self):
        self.pc.enrol()
        before = self.link.snapshot()
        with patch.object(self.store, "write_json", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.link.allow(self.pc.code)
        self.assertEqual(self.link.snapshot(), before)
        self.assertTrue(self.link.allow(self.pc.code))

    def test_forget_revokes_and_survives_restart(self):
        answer, work = self.work()
        self.assertTrue(self.link.forget())
        self.assertEqual(self.answers.get(answer["id"])["state"], "cancelled")
        self.rejected(
            self.pc.prepare(
                "POST", f"/v1/runs/{work['lease']}/text", proto.dumps(dict(offset=0, text="x", done=False, finish=None))
            )
        )
        restarted = self.make_link()
        self.assertIsNone(restarted.snapshot()["pc"])
        self.assertFalse(self.link.forget())

    def test_wrong_key_and_unknown_pc(self):
        self.enrolled()
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        request[2]["X-Link-Mac"] = proto.request_mac(
            b"B" * 32, self.pc.epoch, request[0], request[1], "", bytes.fromhex(request[2]["X-Link-Nonce"]), b""
        )
        self.rejected(request)
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        request[2]["X-Link-Pc"] = "ab" * 8
        self.rejected(request)

    def test_nonce_replay_and_retry(self):
        self.enrolled()
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        self.assertEqual(self.pc.send(*request).status, 204)
        self.rejected(request)
        self.rejected(request)
        self.assertEqual(self.pc.work().status, 204)

    def test_simultaneous_nonce_exactly_one_served(self):
        self.enrolled()
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        barrier = threading.Barrier(2)

        class CriticalSection:
            def __init__(self):
                self.lock = threading.RLock()
                self.local = threading.local()

            def __enter__(self):
                self.acquire()
                return self

            def acquire(self, *args, **kwargs):
                if not hasattr(self.local, "serial"):
                    self.local.serial = 0
                    # Both contenders reach the actual state-lock entrance.
                    barrier.wait(timeout=5)
                acquired = self.lock.acquire(*args, **kwargs)
                self.local.serial += 1
                return acquired

            def release(self):
                self.lock.release()

            def __exit__(self, *args):
                self.release()

            def _is_owned(self):
                return self.lock._is_owned()

            def locked(self):
                if self._is_owned():
                    return True
                if self.lock.acquire(False):
                    self.lock.release()
                    return False
                return True

        critical = CriticalSection()
        test = self

        class Nonces(set):
            def __contains__(self, nonce):
                test.assertTrue(critical._is_owned(), "nonce check outside state lock")
                critical.local.checked = critical.local.serial
                return super().__contains__(nonce)

            def add(self, nonce):
                test.assertTrue(critical._is_owned(), "nonce insert outside state lock")
                test.assertEqual(critical.local.checked, critical.local.serial, "nonce check and insert split")
                super().add(nonce)

        with (
            patch.object(self.link, "_changed", threading.Condition(critical)),
            patch.object(self.link, "_nonces", Nonces()),
            ThreadPoolExecutor(2) as pool,
        ):
            futures = [pool.submit(self.pc.send, *request) for _ in range(2)]
            results = [future.result(timeout=5).status for future in futures]
        self.assertEqual(sorted(results), [204, 401])

    def test_live_lease_text_signed_by_another_key_is_refused(self):
        _, work = self.work()
        request = self.pc.prepare(
            "POST",
            f"/v1/runs/{work['lease']}/text",
            proto.dumps(dict(offset=0, text="wrong signer", done=False, finish=None)),
        )
        request[2]["X-Link-Mac"] = proto.request_mac(
            b"B" * 32,
            self.pc.epoch,
            request[0],
            request[1],
            "",
            bytes.fromhex(request[2]["X-Link-Nonce"]),
            request[3],
        )
        self.rejected(request)
        self.assertEqual(self.pc.text(work["lease"], 0, "right signer").status, 200)

    def test_changed_body_method_target_and_range(self):
        self.enrolled()
        payload = proto.dumps(
            dict(state="idle", lease=None, copied=None, total=None, gpu=None, tokens=None, tps=None, error=None)
        )
        request = list(self.pc.prepare("POST", "/v1/status", payload))
        request[3] = payload.replace(b"idle", b"busy")
        self.rejected(request)
        for index, value in ((0, "POST"), (1, "/v1/work?wait=1")):
            with self.subTest(index=index):
                request = list(self.pc.prepare("GET", "/v1/work?wait=0"))
                request[index] = value
                self.rejected(request)
        request = self.pc.prepare("GET", f"/v1/models/{self.sha}", range_value="bytes=0-1")
        request[2]["Range"] = "bytes=0-2"
        self.rejected(request)

    def test_old_epoch(self):
        self.enrolled()
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        self.link.close()
        self.link = self.make_link()
        self.pc.link = self.link
        self.rejected(request)

    def test_each_malformed_auth_header(self):
        self.enrolled()
        for name, size in (("X-Link-Pc", 8), ("X-Link-Nonce", 12), ("X-Link-Mac", 32)):
            for value in (None, "", "a" * (size * 2 - 1), "A" * (size * 2), "g" * (size * 2), "é" * (size * 2)):
                with self.subTest(name=name, value=value):
                    request = self.pc.prepare("GET", "/v1/work?wait=0")
                    if value is None:
                        del request[2][name]
                    else:
                        request[2][name] = value
                    self.rejected(request)

    def test_nonce_capacity_rotates_epoch_keeps_lease(self):
        _, work = self.work()
        old_epoch = self.pc.epoch
        for _ in range(4095):
            self.assertEqual(self.pc.request("GET", "/unknown").status, 400)
        self.assertEqual(self.pc.work().status, 401)
        self.pc.hello()
        self.assertNotEqual(self.pc.epoch, old_epoch)
        self.assertEqual(self.link._lease["id"].hex(), work["lease"])

    def test_no_work_returns_signed_204(self):
        self.enrolled()
        self.assertEqual(self.pc.work().status, 204)

    def test_waiting_becomes_loading_and_lost_reply_retries(self):
        answer, work = self.work()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.assertEqual(work["question"], answer["question"])
        self.assertEqual(work["partial"], "")
        second = self.pc.data(self.pc.work())
        self.assertNotEqual(second["lease"], work["lease"])
        self.pc.text(second["lease"], 0, "hello")
        retry = self.pc.data(self.pc.work())
        self.assertNotEqual(retry["lease"], second["lease"])
        self.assertEqual(retry["partial"], "hello")

    def test_no_model_no_work(self):
        self.link.close()
        self.link = self.make_link(model_path=None)
        self.pc.link = self.link
        self.pc.hello()
        self.enrolled()
        self.answers.ask("gpu", "question")
        self.assertEqual(self.pc.work().status, 204)
        self.assertEqual(self.link.snapshot()["model"]["state"], "none")

    def test_checking_model_no_work_or_file(self):
        self.link.close()
        (self.store.root / "gpu-link-model.json").unlink()
        gate = threading.Event()
        entered = threading.Event()
        original = LinkService._hash_model

        def hash_later(link, identity):
            entered.set()
            if gate.wait(2):
                original(link, identity)

        with patch.object(LinkService, "_hash_model", hash_later):
            link = LinkService(self.answers, self.store, self.model, clock=self.clock, rand=self.rand)
            self.addCleanup(link.close)
            self.addCleanup(gate.set)
            self.assertTrue(entered.wait(1))
            self.pc.link = link
            self.pc.hello()
            self.pc.enrol()
            link.allow(self.pc.code)
            self.pc.hello()
            self.answers.ask("gpu", "question")
            self.assertEqual(link.snapshot()["model"]["state"], "checking")
            self.assertEqual(self.pc.work().status, 204)
            self.assertEqual(self.pc.model(self.sha).status, 404)
            gate.set()
            link._hasher.join(2)
            self.assertEqual(self.pc.work().status, 200)

    def test_long_poll_question_arrives_without_lock_held(self):
        self.enrolled()
        entered = threading.Event()
        original = self.answers.current

        def current(pane):
            entered.set()
            return original(pane)

        with patch.object(self.answers, "current", side_effect=current), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.pc.work, 2)
            self.assertTrue(entered.wait(1))
            self.assertIsNotNone(self.link.snapshot()["pc"])
            answer = self.answers.ask("gpu", "Arrived during poll")
            reply = future.result(timeout=2)
        self.assertEqual(self.pc.data(reply)["question"], answer["question"])

    def test_new_poll_ends_old_poll(self):
        self.enrolled()
        entered = threading.Event()
        original = self.answers.current

        def current(pane):
            entered.set()
            return original(pane)

        with patch.object(self.answers, "current", side_effect=current), ThreadPoolExecutor(1) as pool:
            old = pool.submit(self.pc.work, 2)
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.pc.work().status, 204)
            self.assertEqual(old.result(timeout=1).status, 204)

    def test_wrong_and_cancelled_lease(self):
        answer, work = self.work()
        self.assertEqual(self.pc.text("ff" * 16, 0, "x").status, 404)
        self.link.cancel(answer["id"])
        self.assertEqual(self.pc.text(work["lease"], 0, "x").status, 404)

    def test_wrong_offset_repeat_identical_and_different(self):
        _, work = self.work()
        lease = work["lease"]
        self.assertEqual(self.pc.data(self.pc.text(lease, 0, "abc")), {"acked": 3})
        self.assertEqual(self.pc.text(lease, 0, "abc").status, 200)
        for offset, text in ((4, "d"), (0, "abd")):
            reply = self.pc.text(lease, offset, text)
            self.assertEqual(reply.status, 409)
            self.assertEqual(self.pc.data(reply), {"stored": 3})

    def test_invalid_text_all_forms(self):
        answer, work = self.work()
        target = f"/v1/runs/{work['lease']}/text"
        raws = [
            b'{"offset":0,"text":"' + value + b'","done":false,"finish":null}'
            for value in (b"\xff", b"\xc0\xaf", b"\xe2\x82", b"\\ud800", b"\\r", b"\\u0000", b"\\u0085", b"")
        ]
        for raw in raws:
            with self.subTest(raw=raw):
                self.clock.advance(1)
                self.assertEqual(self.pc.request("POST", target, raw).status, 400)
                self.assertEqual(self.answers.get(answer["id"])["bytes"], 0)

    def test_answer_overflow_signed_413(self):
        answer, work = self.work()
        lease = work["lease"]
        self.assertEqual(self.pc.text(lease, 0, "x" * 6000).status, 200)
        self.assertEqual(self.pc.text(lease, 6000, "x" * 6000).status, 200)
        reply = self.pc.text(lease, 12000, "x" * 289)
        self.assertEqual(reply.status, 413)
        self.assertEqual(self.pc.data(reply), {"stored": 12000})
        self.assertEqual(self.answers.get(answer["id"])["bytes"], 12000)

    def test_text_token_bucket_and_fractional_refill(self):
        _, work = self.work()
        for index in range(4):
            self.assertEqual(self.pc.text(work["lease"], index, "x").status, 200)
        self.assertEqual(self.pc.text(work["lease"], 4, "x").status, 429)
        self.clock.advance(0.25)
        self.assertEqual(self.pc.text(work["lease"], 4, "x").status, 200)
        self.assertEqual(self.pc.text(work["lease"], 5, "x").status, 429)

    def test_final_post_exact_repeat_and_different_after_final(self):
        answer, work = self.work()
        lease = work["lease"]
        reply = self.pc.text(lease, 0, "finished", True, "stop")
        before = self.answers.snapshot()
        repeated = self.pc.text(lease, 0, "finished", True, "stop")
        self.assertEqual((reply.status, reply.body), (repeated.status, repeated.body))
        self.assertEqual(self.answers.snapshot(), before)
        self.assertEqual(self.answers.get(answer["id"])["state"], "done")
        self.assertEqual(self.pc.text(lease, 0, "different", True, "stop").status, 404)
        self.assertEqual(self.pc.text(lease, 0, "finished", True, "length").status, 404)

    def test_finished_retry_does_not_disturb_new_lease(self):
        _, work = self.work()
        self.pc.text(work["lease"], 0, "done", True, "stop")
        self.answers.ask("gpu", "second")
        newer = self.pc.data(self.pc.work())
        self.assertEqual(self.pc.text(work["lease"], 0, "done", True, "stop").status, 200)
        self.assertEqual(self.link._lease["id"].hex(), newer["lease"])

    def test_restart_after_unacknowledged_post_keeps_bytes(self):
        answer, work = self.work()
        self.pc.text(work["lease"], 0, "prefix é")
        self.link.close()
        self.answers = AnswerStore(self.store)
        self.link = self.make_link()
        self.pc.link = self.link
        self.pc.hello()
        self.assertEqual(self.pc.text(work["lease"], 0, "prefix é").status, 404)
        resumed = self.pc.data(self.pc.work())
        self.assertEqual(resumed["partial"], "prefix é")
        self.assertNotEqual(resumed["lease"], work["lease"])
        self.assertEqual(self.answers.get(answer["id"])["bytes"], len("prefix é".encode()))

    def test_cancel_status_names_old_lease_without_cancelling_new(self):
        answer, work = self.work()
        self.link.cancel(answer["id"])
        self.answers.ask("gpu", "next")
        new = self.pc.data(self.pc.work())
        reply = self.pc.status(lease=work["lease"])
        self.assertEqual(self.pc.data(reply), {"cancel": work["lease"]})
        self.assertEqual(self.link._lease["id"].hex(), new["lease"])
        self.assertEqual(self.pc.data(self.pc.status(lease=new["lease"])), {"cancel": None})

    def test_cancel_without_lease_in_all_nonfinal_states(self):
        self.enrolled()
        for state in ("waiting", "queued", "paused"):
            with self.subTest(state=state):
                first = self.answers.ask("gpu", "first")
                target = first
                if state == "queued":
                    target = self.answers.ask("gpu", "queued")
                if state == "paused":
                    self.answers.begin(target["id"])
                    self.answers.pause(target["id"])
                self.assertEqual(self.link.cancel(target["id"])["state"], "cancelled")
                if target != first:
                    self.link.cancel(first["id"])

    def test_link_down_exact_boundary_resume_and_old_text_refused(self):
        answer, work = self.work()
        self.pc.text(work["lease"], 0, "prefix")
        self.clock.advance(2.499)
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "answering")
        self.clock.advance(0.0011)
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
        resumed = self.pc.data(self.pc.work())
        self.assertEqual(resumed["partial"], "prefix")
        self.assertNotEqual(resumed["lease"], work["lease"])
        self.assertEqual(self.pc.text(work["lease"], 6, "old").status, 404)

    def test_link_down_uses_later_status_or_work(self):
        self.enrolled()
        self.pc.status()
        self.clock.advance(100)
        answer = self.answers.ask("gpu", "question")
        work = self.pc.data(self.pc.work())
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.clock.advance(2)
        self.pc.status(lease=work["lease"])
        self.clock.advance(2)
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.clock.advance(0.5)
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")

    def test_two_missing_status_leases_pause_matching_resets_counter(self):
        answer, work = self.work()
        self.pc.status()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.pc.status(lease=work["lease"])
        self.pc.status(lease="ff" * 16)
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.pc.status()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")

    def test_seeded_resume_seam(self):
        self.enrolled()
        rng = random.Random(4317)
        text = "αβγ hello 世界\n" * 25
        raw = text.encode()
        cuts = rng.sample(range(1, len(raw)), 30)
        self.assertTrue(any(raw[cut] & 0xC0 == 0x80 for cut in cuts))
        self.assertTrue(any(raw[cut] & 0xC0 != 0x80 for cut in cuts))
        for cut in cuts:
            with self.subTest(cut=cut):
                self.clock.advance(3)
                answer = self.answers.ask("gpu", "seam")
                work = self.pc.data(self.pc.work())
                if raw[cut] & 0xC0 == 0x80:
                    # A raw JSON string cut inside UTF-8 is not valid text.
                    body = proto.dumps(dict(offset=0, text="SEAM", done=False, finish=None))
                    body = body.replace(b"SEAM", raw[:cut].replace(b"\n", b"\\n"))
                    target = f"/v1/runs/{work['lease']}/text"
                    self.assertEqual(self.pc.request("POST", target, body).status, 400)
                    self.assertEqual(self.answers.get(answer["id"])["bytes"], 0)
                    self.assertEqual(self.pc.text(work["lease"], 0, text).status, 200)
                    self.assertEqual(self.pc.text(work["lease"], cut, "x").status, 409)
                    self.assertEqual(self.pc.text(work["lease"], len(raw), "", True, "stop").status, 200)
                    continue
                prefix = raw[:cut].decode()
                self.assertEqual(self.pc.text(work["lease"], 0, prefix).status, 200)
                self.clock.advance(2.5)
                self.link.tick()
                resumed = self.pc.data(self.pc.work())
                partial = resumed["partial"].encode()
                self.assertEqual(partial, prefix.encode())
                remaining = text.encode()[len(partial) :].decode()
                self.pc.text(resumed["lease"], len(partial), remaining, True, "stop")
                result = self.answers.get(answer["id"])
                self.assertTrue(result["text"].encode().startswith(partial))
                self.assertEqual((result["text"], result["state"]), (text, "done"))

    def test_every_status_state_and_snapshot_copies(self):
        self.enrolled()
        gpu = dict(name="Test GPU", memory_mb=4096, driver="test")
        for state in ("idle", "copying", "loading", "ready", "busy", "stopping", "error"):
            reply = self.pc.status(state, gpu=gpu, copied=1, total=2, error="engine" if state == "error" else None)
            self.assertEqual(reply.status, 200)
            snap = self.link.snapshot()
            self.assertEqual(snap["pc"]["state"], state)
            self.assertEqual(snap["pc"]["gpu"], gpu)
            snap["pc"]["gpu"]["name"] = "changed"
            snap["model"]["name"] = "changed"
            self.assertEqual(self.link.snapshot()["pc"]["gpu"], gpu)
        encoded = json.dumps(self.link.snapshot())
        for secret in (self.pc.key.hex(), self.pc.epoch.hex(), "nonce", "lease", '"key"'):
            self.assertNotIn(secret, encoded)

    def test_error_matching_lease_fails_answer(self):
        answer, work = self.work()
        self.assertEqual(self.pc.status("error", work["lease"], error="engine").status, 200)
        self.assertEqual(self.answers.get(answer["id"])["state"], "failed")
        self.assertEqual(self.answers.get(answer["id"])["error"], "engine")
        self.assertEqual(self.pc.text(work["lease"], 0, "x").status, 404)

    def test_error_without_matching_lease_is_only_recorded(self):
        answer, work = self.work()
        for lease in (None, "ff" * 16):
            self.assertEqual(self.pc.status("error", lease, error="engine").status, 200 if lease is None else 404)
            self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
            self.pc.status(lease=work["lease"])

    def test_error_state_requires_code(self):
        self.enrolled()
        before = self.link.snapshot()
        self.assertEqual(self.pc.status("error").status, 400)
        self.assertEqual(self.pc.status("idle", error="engine").status, 400)
        self.assertEqual(self.link.snapshot(), before)

    def test_hash_cached_across_restart(self):
        self.assertEqual(self.link.snapshot()["model"]["state"], "ready")
        self.link.close()
        with patch.object(LinkService, "_hash_model", side_effect=AssertionError("rehash")):
            link = self.make_link()
            self.assertEqual(link.snapshot()["model"]["state"], "ready")
            self.assertIsNone(link._hasher)

    def test_model_unknown_ranges_whole_and_signatures(self):
        self.enrolled()
        reply = self.pc.model("ff" * 32)
        self.assertEqual(reply.status, 404)
        self.assertIsNone(reply.file)
        for start, end in ((0, 0), (1, 9), (0, self.model.stat().st_size - 1)):
            reply = self.pc.model(self.sha, f"bytes={start}-{end}")
            self.assertEqual(reply.status, 206)
            self.assertEqual(dict(reply.headers)["Content-Range"], f"bytes {start}-{end}/{self.model.stat().st_size}")
            path, offset, count = reply.file
            with path.open("rb") as stream:
                stream.seek(offset)
                self.assertEqual(stream.read(count), self.model.read_bytes()[start : end + 1])
        reply = self.pc.model(self.sha)
        self.assertEqual((reply.status, reply.file), (200, (self.model, 0, self.model.stat().st_size)))

    def test_invalid_ranges_have_signed_body(self):
        self.enrolled()
        for value in ("", "bytes=00-1", "bytes=2-1", "bytes=0-999", "bytes=-1", "bytes=1-", "bytes=0-1,2-3"):
            with self.subTest(value=value):
                reply = self.pc.model(self.sha, value)
                self.assertEqual(reply.status, 416)
                self.assertIsNone(reply.file)
                self.assertEqual(self.pc.data(reply), {"error": "range"})
                self.assertNotIn("Content-Range", dict(reply.headers))

    def test_model_changes_size_or_mtime_rehashes(self):
        self.enrolled()
        for data in (b"changed bytes" * 16, b"different"):
            before = self.model.stat().st_mtime_ns
            self.model.write_bytes(data)
            # The first data has the size of the original file, so only the modification time can show the change.
            # File times can be as coarse as a few milliseconds (Linux): move the time on explicitly instead of relying
            # on two writes microseconds apart getting different times.
            os.utime(self.model, ns=(before + 2_000_000_000, before + 2_000_000_000))
            reply = self.pc.model(self.sha)
            self.assertEqual(reply.status, 404)
            self.link._hasher.join(2)
            self.assertEqual(self.pc.model(hashlib.sha256(data).hexdigest()).status, 200)

    def test_model_error_and_display_name(self):
        self.model.unlink()
        self.link.tick()
        self.assertEqual(self.link.snapshot()["model"]["state"], "error")
        self.link.set_model_name("é\n" + "x" * 80)
        self.assertEqual(self.link.snapshot()["model"]["name"], "x" * 64)

    def test_model_cache_read_failure_is_signed_500_without_state_change(self):
        self.enrolled()
        self.model.write_bytes(b"new model")
        before = self.link.snapshot()
        with patch.object(self.store, "read_json", side_effect=OSError("disk")):
            reply = self.pc.model(self.sha)
        self.assertEqual((reply.status, self.pc.data(reply)), (500, {"error": "state"}))
        before["storage"] = "error"
        self.assertEqual(self.link.snapshot(), before)

    def test_model_cache_write_failure_keeps_ready_hash_without_rehashing(self):
        self.enrolled()
        self.model.write_bytes(b"new model")
        digest = hashlib.sha256(b"new model").hexdigest()
        with patch.object(self.store, "write_json", side_effect=OSError("disk")) as write:
            self.assertEqual(self.pc.model(self.sha).status, 404)
            self.link._hasher.join(2)
            first = self.link._hasher
            self.assertEqual(self.link.snapshot()["model"]["state"], "ready")
            self.assertEqual(self.link._model_hash, digest)
            for _ in range(20):
                self.clock.advance(1)
                self.link.tick()
                self.assertIs(self.link._hasher, first)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(self.pc.model(digest).status, 200)
        self.answers.ask("gpu", "question")
        self.assertEqual(self.pc.work().status, 200)

    def test_open_model_rechecks_identity_and_requested_hash(self):
        with self.link.open_model(self.model, f"/v1/models/{self.sha}") as stream:
            self.assertEqual(stream.read(), self.model.read_bytes())
        with self.assertRaises(OSError):
            self.link.open_model(self.model, "/v1/models/" + "ff" * 32)
        self.model.write_bytes(b"changed after handle")
        with self.assertRaises(OSError):
            self.link.open_model(self.model, f"/v1/models/{self.sha}")

    def test_undefined_changed_model_for_live_lease_is_refused(self):
        answer, work = self.work()
        self.model.write_bytes(b"new model")
        self.assertEqual(self.pc.model(self.sha).status, 404)
        self.link._hasher.join(2)
        before = self.answers.snapshot(), self.link._lease.copy()
        reply = self.pc.work()
        self.assertEqual((reply.status, self.pc.data(reply)), (400, {"error": "request"}))
        self.assertEqual((self.answers.snapshot(), self.link._lease), before)
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.assertEqual(self.link._lease["id"].hex(), work["lease"])

    def test_repeated_work_resets_handoff_time_and_missing_status_count(self):
        answer, work = self.work()
        self.pc.status()
        self.clock.advance(2)
        self.assertNotEqual(self.pc.data(self.pc.work())["lease"], work["lease"])
        self.pc.status()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.clock.advance(2)
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.clock.advance(0.5)
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")

    def test_finished_history_does_not_expire(self):
        _, work = self.work()
        self.pc.text(work["lease"], 0, "done", True, "stop")
        self.clock.advance(100000)
        self.link.tick()
        self.assertEqual(self.pc.text(work["lease"], 0, "done", True, "stop").status, 200)

    def test_forget_credential_failure_after_pause_failure_retries_lease(self):
        answer, work = self.work()
        with patch.object(self.store, "write_json", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.link.forget()
        self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
        self.assertIsNotNone(self.link._lease)
        self.assertEqual(self.link._lease["id"].hex(), work["lease"])
        self.assertTrue(self.link._lease["failed"])
        self.assertIsNotNone(self.link.snapshot()["pc"])
        self.link.tick()
        self.assertIsNone(self.link._lease)
        self.assertEqual(self.answers.get(answer["id"])["state"], "cancelled")
        self.assertEqual(self.pc.work().status, 204)

    def test_forget_credential_failure_keeps_pc_but_work_is_safely_paused(self):
        answer, _ = self.work()
        original = self.store.write_json

        def write(name, payload, *, durable=False):
            if name == "gpu-link-pc.json":
                raise OSError("disk")
            return original(name, payload, durable=durable)

        with patch.object(self.store, "write_json", side_effect=write), self.assertRaises(OSError):
            self.link.forget()
        self.assertIsNotNone(self.link.snapshot()["pc"])
        self.assertIsNone(self.link._lease)
        self.assertEqual(self.answers.get(answer["id"])["state"], "cancelled")
        self.assertTrue(self.link.forget())

    def test_second_missing_status_storage_failure_preserves_first(self):
        answer, work = self.work()
        self.pc.status()
        before = self.link.snapshot(), self.link._lease.copy(), self.answers.snapshot()
        with patch.object(self.store, "write_json", side_effect=OSError("disk")):
            self.assertEqual(self.pc.status().status, 500)
        before[0]["storage"] = "error"
        self.assertEqual((self.link.snapshot(), self.link._lease, self.answers.snapshot()), before)
        self.assertEqual(self.pc.status().status, 200)
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
        self.assertEqual(self.pc.text(work["lease"], 0, "late").status, 404)

    def test_cancelled_history_does_not_expire_and_count_bound(self):
        answer, work = self.work()
        self.link.cancel(answer["id"])
        self.clock.advance(100000)
        self.link.tick()
        self.assertEqual(self.pc.data(self.pc.status(lease=work["lease"])), {"cancel": work["lease"]})
        with self.link._lock:
            for value in range(HISTORY_LIMIT + 1):
                self.link._remember((value + 100).to_bytes(16, "big"), "cancelled")
        self.assertEqual(len(self.link._history), HISTORY_LIMIT)
        self.assertEqual(HISTORY_LIMIT, 64)
        self.assertNotIn((100).to_bytes(16, "big"), self.link._history)
        self.assertEqual(self.pc.status(lease=work["lease"]).status, 404)

    def test_finished_history_survives_answer_eviction_until_65th_lease(self):
        answer, first = self.work()
        self.assertEqual(self.pc.text(first["lease"], 0, "done", True, "stop").status, 200)
        for index in range(63):
            self.clock.advance(1)
            self.answers.ask("gpu", f"next {index}")
            work = self.pc.data(self.pc.work())
            self.assertEqual(self.pc.text(work["lease"], 0, "done", True, "stop").status, 200)
        self.assertIsNone(self.answers.get(answer["id"]))
        self.assertEqual(self.pc.text(first["lease"], 0, "done", True, "stop").status, 200)
        self.assertEqual(self.pc.text(first["lease"], 0, "different", True, "stop").status, 404)
        self.clock.advance(1)
        self.answers.ask("gpu", "65th")
        work = self.pc.data(self.pc.work())
        self.pc.text(work["lease"], 0, "done", True, "stop")
        self.assertEqual(self.pc.text(first["lease"], 0, "done", True, "stop").status, 404)

    def test_ended_history_is_cleared_by_forget_even_for_the_same_pc(self):
        _, first = self.work()
        self.pc.text(first["lease"], 0, "done", True, "stop")
        answer = self.answers.ask("gpu", "cancelled")
        cancelled = self.pc.data(self.pc.work())
        self.link.cancel(answer["id"])
        self.link.forget()
        other = FakePC(self.link, key=b"B" * 32)
        other.hello()
        other.enrol()
        self.link.allow(other.code)
        other.hello()
        self.assertEqual(other.text(first["lease"], 0, "done", True, "stop").status, 404)
        self.assertEqual(other.status(lease=cancelled["lease"]).status, 404)
        # Re-enrolling the original key must not recover forgotten history.
        self.link.forget()
        self.clock.advance(1)
        self.pc.enrol()
        self.link.allow(self.pc.code)
        self.pc.hello()
        self.assertEqual(self.pc.text(first["lease"], 0, "done", True, "stop").status, 404)
        self.assertEqual(self.pc.status(lease=cancelled["lease"]).status, 404)
        self.link.close()
        self.link = self.make_link()
        self.pc.link = self.link
        self.pc.hello()
        self.assertEqual(self.pc.text(first["lease"], 0, "done", True, "stop").status, 404)
        self.assertEqual(self.pc.status(lease=cancelled["lease"]).status, 404)

    def test_history_keeps_only_key_digest_and_forget_removes_all_key_references(self):
        _, work = self.work()
        final = proto.dumps(dict(offset=0, text="done", done=True, finish="stop"))
        target = f"/v1/runs/{work['lease']}/text"
        first = self.pc.request("POST", target, final)
        self.assertEqual(first.status, 200)
        self.assertIsNone(self.link._lease)
        retry = self.pc.request("POST", target, final)
        self.assertEqual((retry.status, retry.body), (200, first.body))
        request = self.pc.prepare("POST", target, final)
        request[2]["X-Link-Mac"] = proto.request_mac(
            b"B" * 32, self.pc.epoch, "POST", target, "", bytes.fromhex(request[2]["X-Link-Nonce"]), final
        )
        self.rejected(request)
        answer = self.answers.ask("gpu", "cancelled")
        cancelled = self.pc.data(self.pc.work())
        self.link.cancel(answer["id"])
        self.assertEqual(len(self.link._history), 2)
        for record in self.link._history.values():
            self.assertEqual(record[3], hashlib.sha256(self.pc.key).digest())
        # Exercise both history lookups with an authenticated different PC,
        # independently of forget's history clearing.
        other = FakePC(self.link, key=b"B" * 32)
        other.hello()
        other._counter = self.pc._counter
        with patch.object(self.link, "_pc", (other.label, other.key)):
            self.assertEqual(other.request("POST", target, final).status, 404)
            self.assertEqual(other.status(lease=cancelled["lease"]).status, 404)
        self.assertTrue(self.link.forget())
        self.assertFalse(self.link._history)
        seen = set()

        def check(value):
            if id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, (bytes, bytearray)):
                self.assertNotIn(self.pc.key, value)
            elif isinstance(value, dict):
                for key, item in value.items():
                    check(key)
                    check(item)
            elif isinstance(value, (tuple, list, set, frozenset)):
                for item in value:
                    check(item)
            elif hasattr(value, "__dict__"):
                check(vars(value))

        check(self.link)

    def test_two_work_replies_callbacks_in_reverse_order(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        for later_success in (True, False):
            with self.subTest(later_success=later_success):
                replies = []
                for _ in range(2):
                    request = self.pc.prepare("GET", "/v1/work?wait=0")
                    reply = self.link.handle(*request)
                    self.assertEqual(reply.status, 200)
                    replies.append(reply)
                self.assertNotEqual(self.pc.data(replies[0])["lease"], self.pc.data(replies[1])["lease"])
                self.assertIsNone(self.link._lease["sent"])
                if later_success:
                    replies[1].sent()
                    self.assertEqual(self.link._lease["sent"], self.clock())
                    replies[0].failed()
                    self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
                    lease = self.pc.data(replies[1])["lease"]
                    self.assertEqual(self.link._lease["id"].hex(), lease)
                    self.assertEqual(self.pc.text(lease, self.answers.get(answer["id"])["bytes"], "x").status, 200)
                    self.clock.advance(3)
                    self.link.tick()
                else:
                    replies[1].failed()
                    replies[0].sent()
                self.assertIsNone(self.link._lease)
                self.assertEqual(self.answers.get(answer["id"])["state"], "paused")
                new = self.pc.data(self.pc.work())
                for reply in replies:
                    reply.sent()
                    reply.failed()
                self.assertEqual(self.link._lease["id"].hex(), new["lease"])
                self.clock.advance(3)
                self.link.tick()

    def test_delivery_callbacks_are_once_only_and_ignore_ended_leases(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        # Call the service directly to control the transport notifications.
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        headers = Message()
        for name, value in request[2].items():
            headers[name] = value
        reply = self.link.handle(request[0], request[1], headers, request[3])
        self.assertIsNone(self.link._lease["sent"])
        reply.sent()
        sent = self.link._lease["sent"]
        self.clock.advance(1)
        reply.sent()
        reply.failed()
        self.assertEqual(self.link._lease["sent"], sent)
        self.link.cancel(answer["id"])
        self.answers.ask("gpu", "next")
        new = self.pc.data(self.pc.work())
        reply.failed()
        self.assertEqual(self.link._lease["id"].hex(), new["lease"])

    def test_storage_failures_leave_answer_lease_and_status_unchanged(self):
        answer, work = self.work()
        for action in (
            lambda: self.pc.text(work["lease"], 0, "x", True, "stop"),
            lambda: self.pc.status("error", work["lease"], error="engine"),
        ):
            before = self.answers.snapshot(), self.link.snapshot(), self.link._lease.copy(), self.link._buckets.copy()
            with patch.object(self.store, "write_json", side_effect=OSError("disk")):
                reply = action()
            self.assertEqual((reply.status, self.pc.data(reply)), (500, {"error": "state"}))
            before[1]["storage"] = "error"
            self.assertEqual(
                (self.answers.snapshot(), self.link.snapshot(), self.link._lease, self.link._buckets), before
            )
        self.assertEqual(self.pc.text(work["lease"], 0, "x", True, "stop").status, 200)
        self.assertEqual(self.answers.get(answer["id"])["state"], "done")

    def test_work_storage_failure_does_not_grant_lease(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        with patch.object(self.store, "write_json", side_effect=OSError("disk")):
            self.assertEqual(self.pc.work().status, 500)
        self.assertIsNone(self.link._lease)
        self.assertEqual(self.answers.get(answer["id"])["state"], "waiting")

    def test_pause_and_cancel_storage_failure_restore_lease(self):
        answer, work = self.work()
        self.clock.advance(3)
        with patch.object(self.store, "write_json", side_effect=OSError("disk")):
            self.link.tick()
            self.assertEqual(self.link._lease["id"].hex(), work["lease"])
            with self.assertRaises(OSError):
                self.link.cancel(answer["id"])
            self.assertEqual(self.link._lease["id"].hex(), work["lease"])
        self.link.tick()
        self.assertEqual(self.answers.get(answer["id"])["state"], "paused")

    def test_undefined_range_on_nonmodel_rejected_without_state_change(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        before = self.link.snapshot(), self.answers.snapshot()
        reply = self.pc.request("GET", "/v1/work?wait=0", range_value="bytes=0-1")
        self.assertEqual((reply.status, reply.body), (401, b""))
        self.assertEqual((self.link.snapshot(), self.answers.snapshot()), before)

    def test_running_answer_without_lease_is_paused_and_offered(self):
        self.enrolled()
        for text in ("", "kept é"):
            with self.subTest(text=text):
                answer = self.answers.ask("gpu", "question")
                self.answers.begin(answer["id"])
                if text:
                    self.answers.append(answer["id"], 0, text.encode())
                try:
                    with patch.object(self.answers, "pause", wraps=self.answers.pause) as pause:
                        reply = self.pc.work()
                    pause.assert_called_once_with(answer["id"])
                    self.assertEqual(reply.status, 200)
                    self.assertEqual(self.pc.data(reply)["partial"], text)
                    self.assertEqual(self.answers.get(answer["id"])["state"], "loading")
                finally:
                    self.link.cancel(answer["id"])

    def test_undefined_live_lease_with_externally_finished_answer_is_refused(self):
        answer, _ = self.work()
        self.answers.cancel(answer["id"])
        before = self.answers.snapshot(), self.link._lease.copy()
        reply = self.pc.work()
        self.assertEqual((reply.status, self.pc.data(reply)), (400, {"error": "request"}))
        self.assertEqual((self.answers.snapshot(), self.link._lease), before)

    def test_nothing_printed_or_logged(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.enrolled()
            self.pc.work()
            self.pc.status()
            self.pc.model("ff" * 32)
            self.pc.text("ff" * 16, 0, "secret answer")
            self.link.tick()
            self.link.forget()
        self.assertEqual(output.getvalue(), "")

    def test_fake_pc_refuses_missing_or_wrong_reply_signature(self):
        self.enrolled()
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        reply = self.pc.send(*request)
        with self.assertRaises(AssertionError):
            self.pc.verify(type(reply)(204), request[1], bytes.fromhex(request[2]["X-Link-Nonce"]))
        with self.assertRaises(AssertionError):
            self.pc.verify(
                type(reply)(204, (("X-Link-Mac", "00" * 32),)), request[1], bytes.fromhex(request[2]["X-Link-Nonce"])
            )

    def test_refused_requests_never_touch_the_nonce_table_or_the_epoch(self):
        """Contract wave 2, signed requests: no effect before the last step passes."""
        self.enrolled()
        self.assertEqual(self.pc.work().status, 204)
        epoch, table = self.link._epoch, set(self.link._nonces)
        for index in range(proto.NONCES_PER_PC + 10):
            method, target, headers, body = self.pc.prepare("GET", "/v1/work?wait=0")
            headers["X-Link-Mac"] = f"{index:064x}"  # right PC id, new nonce, wrong signature
            reply = self.pc.send(method, target, headers, body)
            self.assertEqual((reply.status, reply.body), (401, b""))
        self.assertEqual(self.link._nonces, table)
        self.assertEqual(self.link._epoch, epoch)
        self.assertEqual(self.pc.work().status, 204)

    def test_full_table_is_rotated_only_by_a_request_with_a_valid_signature(self):
        self.enrolled()
        while len(self.link._nonces) < proto.NONCES_PER_PC:
            self.assertEqual(self.pc.request("GET", "/unknown").status, 400)
        epoch = self.link._epoch
        method, target, headers, body = self.pc.prepare("GET", "/v1/work?wait=0")
        headers["X-Link-Mac"] = "0" * 64
        self.assertEqual(self.pc.send(method, target, headers, body).status, 401)
        self.assertEqual(self.link._epoch, epoch)
        self.assertEqual(len(self.link._nonces), proto.NONCES_PER_PC)
        self.assertEqual(self.pc.work().status, 401)
        self.assertNotEqual(self.link._epoch, epoch)

    def test_signature_and_code_are_compared_with_compare_digest(self):
        self.enrolled()
        request = self.pc.prepare("GET", "/v1/work?wait=0")
        with patch("opos.gpu_link_proto.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertEqual(self.pc.send(*request).status, 204)
        self.assertEqual(compare.call_count, 1)
        self.assertEqual(compare.call_args.args[0], request[2]["X-Link-Mac"])
        self.assertTrue(self.link.forget())
        self.pc.hello()
        self.assertEqual(self.pc.enrol().status, 202)
        with patch("opos.gpu_link.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertFalse(self.link.allow("000000" if self.pc.code != "000000" else "000001"))
            self.assertTrue(self.link.allow(self.pc.code))
        self.assertEqual(compare.call_count, 2)

    def test_status_that_names_a_finished_lease_gets_no_cancel(self):
        self.enrolled()
        self.answers.ask("gpu", "question")
        lease = self.pc.data(self.pc.work())["lease"]
        self.assertEqual(self.pc.text(lease, 0, "done", True, "stop").status, 200)
        reply = self.pc.status("busy", lease)
        self.assertEqual((reply.status, self.pc.data(reply)), (200, {"cancel": None}))

    def test_forget_after_a_failed_pause_does_not_strand_the_answer(self):
        self.enrolled()
        answer = self.answers.ask("gpu", "question")
        lease = self.pc.data(self.pc.work())["lease"]
        self.assertEqual(self.pc.text(lease, 0, "kept").status, 200)
        with patch.object(self.answers, "cancel", side_effect=OSError("disk")):
            self.assertTrue(self.link.forget())
        self.clock.advance(5)
        self.link.tick()
        self.pc.hello()
        self.assertEqual(self.pc.enrol().status, 202)
        self.assertTrue(self.link.allow(self.pc.code))
        self.pc.hello()
        self.clock.advance(5)
        self.link.tick()
        reply = self.pc.work()
        stored = self.answers.get(answer["id"])
        self.assertEqual(stored["text"], "kept")
        self.assertEqual(reply.status, 204)
        self.assertEqual(stored["state"], "cancelled")

    def test_surv_live_flag_flips_exactly_at_link_seconds(self):
        # The display becomes disconnected at the timeout, including equality.
        self.enrolled()
        self.assertEqual(self.pc.status().status, 200)
        self.clock.advance(LINK_SECONDS - 0.5)
        self.assertTrue(self.link.snapshot()["pc"]["live"])
        self.clock.advance(0.5)
        self.assertFalse(self.link.snapshot()["pc"]["live"])

    def test_surv_ready_unchanged_model_is_not_rechecked_against_the_cache(self):
        # A missing restart cache must not invalidate a digest already in memory.
        thread = self.link._hasher
        self.assertEqual(self.link.snapshot()["model"]["state"], "ready")
        (self.store.root / "gpu-link-model.json").unlink()
        self.link.tick()
        self.assertIs(self.link._hasher, thread)
        self.assertEqual(self.link.snapshot()["model"]["state"], "ready")

    def test_surv_model_changed_while_hashing_is_not_published(self):
        # A digest computed across a file change must not be published or cached.
        other = self.root / "other.gguf"
        other.write_bytes(b"x" * 64)
        real = gpu_link._identity
        calls = []

        def identity(path):
            calls.append(path)
            value = real(path)
            return value if len(calls) == 1 else {**value, "mtime_ns": value["mtime_ns"] + 1}

        store = Store(self.root / "state2")
        with patch.object(gpu_link, "_identity", identity):
            link = LinkService(AnswerStore(store), store, other, clock=self.clock, rand=self.rand)
            self.addCleanup(link.close)
            link._hasher.join(2)
        self.assertNotEqual(link.snapshot()["model"]["state"], "ready")
        self.assertEqual(store.read_json("gpu-link-model.json", {})[0], {})


if __name__ == "__main__":
    unittest.main()
