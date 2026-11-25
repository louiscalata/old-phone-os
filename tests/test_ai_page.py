# SPDX-License-Identifier: GPL-3.0-or-later
"""AI mode registration, coherent snapshots, actions and bounded phone workers."""

import ast
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opos import ai_answers, ai_page, apps, apps_page, assist, lockpin, muse, web
from opos.store import Store
from tests.ai_page_support import PC, ROUTE, FakeLink, FakeStream, request, running

ROOT = Path(__file__).resolve().parents[1]


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class AiPage(unittest.TestCase):
    def setUp(self):
        # All state, including temporary HTTP-server state, stays in this checkout.
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {"OPOS_STATE_DIR": self.tmp.name, "OPOS_APPS": "ai"}))
        self.answers = ai_answers.AnswerStore(Store(self.tmp.name))
        self.enterContext(mock.patch.object(ai_answers, "shared", return_value=self.answers))
        self.state = ai_page.PageState()
        self.enterContext(mock.patch.object(ai_page, "_STATE", self.state))
        self.enterContext(mock.patch.object(assist, "free_mb", return_value=2048))
        self.enterContext(mock.patch.object(assist, "local_route", return_value=ROUTE))
        self.link = FakeLink(self.answers)
        self.service = SimpleNamespace(service=lambda: self.link)
        self.enterContext(mock.patch.object(ai_page, "gpu_link_server", self.service))
        apps.register(ai_page.APP)
        # Added last: join every worker while its storage and patches still exist.
        self.addCleanup(self.close_workers)

    def close_workers(self):
        self.state.close()
        self.assertFalse(any(t.name.startswith("ai-phone-") for t in threading.enumerate()))

    def job(self, pieces=(), error=None):
        stream = FakeStream(pieces, error)
        self.enterContext(mock.patch.object(muse, "stream", return_value=stream))
        return stream

    def join_workers(self):
        jobs = list(self.state.workers.values())
        for job in jobs:
            job.thread.join(5)
            self.assertFalse(job.thread.is_alive())
        return jobs

    def ask(self, panes=("phone",), question="A public question"):
        return ai_page.post({"action": "ask", "question": question, "panes": list(panes)})

    def test_registration_token_nonce_disclosure_and_home_unchanged(self):
        app = apps.by_id("ai")
        self.assertEqual((app.id, app.title, app.path), ("ai", "AI mode", "/ai"))
        self.assertEqual(app.summary, ai_page.DISCLOSURE)
        self.assertEqual(
            app.card(),
            "Works offline: yes, with a local model. Sends data: questions to the enrolled PC when the GPU pane is used. Uses: phone model and optional PC GPU. Real or practice: real.",
        )
        self.assertEqual(apps.MODULES["ai"], "opos.ai_page")
        html = app.render("fixture-token", "fixture-nonce")
        self.assertIn('name="opos-token" content="fixture-token"', html)
        self.assertIn('nonce="fixture-nonce"', html)
        before = web.page()
        with mock.patch.dict(os.environ, {"OPOS_APPS": ""}):
            self.assertEqual(web.page(), before)
        self.assertNotIn("AI mode", before)
        with running() as server:
            self.assertEqual(request(server, "/ai")[0], 200)
            self.assertEqual(request(server, "/api/ai", token=False)[0], 403)
            self.assertEqual(request(server, "/api/ai")[0], 200)
            self.assertEqual(request(server, "/ai?pane=phone")[0], 200)
            self.assertNotIn(b"ai-pane", request(server, "/")[1])
            self.assertIn(ai_page.DISCLOSURE.encode(), request(server, "/apps")[1])

    def test_exact_snapshot_keys_and_no_answers(self):
        snap = ai_page.snapshot()
        self.assertEqual(set(snap), {"boot", "rev", "phone", "gpu", "presets"})
        self.assertRegex(snap["boot"], r"^[0-9a-f]{8}$")
        self.assertIs(type(snap["rev"]), int)
        self.assertEqual(set(snap["phone"]), {"model", "where", "ready", "answer", "live_speed"})
        self.assertIsNone(snap["phone"]["live_speed"])
        self.assertEqual(set(snap["gpu"]), {"model", "where", "ready", "answer", "link"})
        self.assertIsNone(snap["phone"]["answer"])
        self.assertIsNone(snap["gpu"]["answer"])
        self.assertEqual(snap["phone"]["model"], ROUTE.name)
        self.assertEqual(snap["phone"]["where"], ROUTE.where)
        self.assertTrue(snap["phone"]["ready"])
        self.assertEqual(len(snap["presets"]), 3)
        self.assertIsInstance(snap["presets"], list)
        for preset, question in zip(snap["presets"], ai_page.QUESTIONS, strict=True):
            self.assertEqual(set(preset), {"label", "question"})
            self.assertLessEqual(len(preset["label"]), 30)
            self.assertEqual(preset["question"], question)
        self.assertEqual(ai_page.snapshot(), snap)
        snap["presets"][0]["label"] = "changed copy"
        snap["gpu"]["link"]["model"]["name"] = "changed copy"
        self.assertEqual(ai_page.snapshot()["presets"], list(ai_page.PRESETS))
        self.assertEqual(ai_page.snapshot()["gpu"]["model"], "Large model")

    def test_link_off_missing_module_and_each_pc_shape(self):
        self.link = None
        off = ai_page.snapshot()
        self.assertIsNone(off["gpu"]["link"])
        self.assertFalse(off["gpu"]["ready"])
        with mock.patch.object(ai_page, "gpu_link_server", None):
            self.assertEqual(ai_page.snapshot(), off)
        self.link = FakeLink(self.answers)
        on = ai_page.snapshot()
        self.assertGreater(on["rev"], off["rev"])
        self.link.value["pending"] = {"label": "Demo PC", "code": "482913", "seconds": 60}
        pending = ai_page.snapshot()
        self.assertGreater(pending["rev"], on["rev"])
        self.assertEqual(pending["gpu"]["link"]["pending"]["code"], "482913")
        self.link.value["pc"] = deepcopy(PC)
        live = ai_page.snapshot()
        self.assertTrue(live["gpu"]["ready"])
        self.link.value["pc"]["live"] = False
        down = ai_page.snapshot()
        self.assertFalse(down["gpu"]["ready"])
        self.assertGreater(down["rev"], live["rev"])

    def test_snapshots_open_finished_and_hidden_store_changes_raise_revision(self):
        for pane in ai_answers.PANES:
            a = self.answers.ask(pane, "Public question", model="Named model")
            snap = ai_page.snapshot()
            self.assertEqual(snap[pane]["answer"]["state"], "waiting")
            self.answers.begin(a["id"])
            self.answers.note(a["id"], where="on this phone", tokens=20, tps=10)
            self.answers.append(a["id"], 0, b"Plain answer", done=True, finish="stop")
            done = ai_page.snapshot()
            self.assertGreater(done["rev"], snap["rev"])
            self.assertEqual(done[pane]["answer"], self.answers.latest(pane))
            self.assertEqual(done[pane]["answer"]["state"], "done")
            newer = self.answers.ask(pane, "Next question")
            before = ai_page.snapshot()
            self.answers.note(a["id"], tokens=21)
            after = ai_page.snapshot()
            self.assertGreater(after["rev"], before["rev"])
            self.assertEqual(after[pane]["answer"]["id"], newer["id"])

    def test_snapshot_retries_a_store_write_between_panes(self):
        old = self.answers.ask("phone", "Phone question")
        original = self.answers.current
        once = []

        def latest(pane):
            result = original(pane)
            if pane == "phone" and not once:
                once.append(True)
                self.answers.cancel(old["id"])
                self.answers.ask("gpu", "GPU question")
            return result

        with mock.patch.object(self.answers, "current", side_effect=latest):
            snap = ai_page.snapshot()
        self.assertEqual(snap["phone"]["answer"]["state"], "cancelled")
        self.assertEqual(snap["gpu"]["answer"]["question"], "GPU question")

    def test_snapshot_retries_link_change_and_refuses_continuous_churn(self):
        original = self.link.snapshot
        seen = []

        def changing():
            value = original()
            if not seen:
                seen.append(True)
                self.link.value["pc"] = deepcopy(PC)
            return value

        with mock.patch.object(self.link, "snapshot", side_effect=changing):
            self.assertTrue(ai_page.snapshot()["gpu"]["ready"])
        count = [0]

        def churn():
            value = original()
            count[0] += 1
            value["model"]["size"] = count[0]
            return value

        with mock.patch.object(self.link, "snapshot", side_effect=churn), self.assertRaises(OSError):
            ai_page.snapshot()

    def test_ask_each_pane_and_both_with_clean_question(self):
        self.job(["Answer"])
        for panes in (("phone",), ("gpu",), ("phone", "gpu")):
            with self.subTest(panes=panes):
                status, snap = self.ask(panes, "Public\r\nquestion\x00")
                self.assertEqual(status, 200)
                for pane in panes:
                    self.assertEqual(snap[pane]["answer"]["question"], "Public\nquestion")
                self.join_workers()
                for pane in panes:
                    current = self.answers.current(pane)
                    if current:
                        ai_page.post({"action": "cancel", "id": current["id"]})

    def test_bad_questions_and_panes_are_refused_without_writes(self):
        for question in ("", " \t\n", "\x00\r", None, "é" * 2049):
            status, body = self.ask(("gpu",), question)
            self.assertEqual(status, 400)
            self.assertTrue(body["error"].endswith("."))
        for panes in (None, [], "phone", ["phone", "phone"], ["other"], [["phone"]]):
            status, body = ai_page.post({"action": "ask", "question": "Question", "panes": panes})
            self.assertEqual(status, 400, body)
        self.assertEqual(self.answers.snapshot()["answers"], [])
        self.assertEqual(self.ask(("gpu",), "é" * 2048)[0], 200)

    def test_full_store_does_not_partially_ask_both(self):
        for i in range(ai_answers.MAX_ANSWERS):
            self.answers.ask("gpu", str(i))
        before = self.answers.snapshot()
        # Isolate capacity from the earlier busy-pane guard. The store view is
        # full, while this stand-in reports no pane currently in progress.
        with (
            mock.patch.object(self.answers, "current", return_value=None),
            mock.patch.object(self.answers, "ask") as ask,
        ):
            self.assertIsNone(self.answers.current("phone"))
            self.assertIsNone(self.answers.current("gpu"))
            self.assertEqual(
                self.ask(("phone", "gpu")),
                (400, {"error": "Stop an unfinished answer before asking again."}),
            )
            ask.assert_not_called()
        self.assertEqual(self.answers.snapshot(), before)

    def test_snapshot_uses_current_pane_answer_or_else_latest_pane_answer(self):
        for pane in ai_answers.PANES:
            first = self.answers.ask(pane, "Running question")
            self.answers.begin(first["id"])
            queued = self.answers.ask(pane, "Newer queued question")
            self.assertEqual(ai_page.snapshot()[pane]["answer"]["id"], first["id"])
            self.answers.cancel(queued["id"])
            self.assertEqual(ai_page.snapshot()[pane]["answer"]["id"], first["id"])
            self.answers.cancel(first["id"])
            self.assertEqual(ai_page.snapshot()[pane]["answer"]["id"], queued["id"])

    def test_off_link_asks_only_phone_but_on_link_waits_for_absent_or_dead_pc(self):
        self.job(["Phone answer"])
        self.link = None
        self.assertEqual(self.ask(("phone", "gpu"))[0], 200)
        self.join_workers()
        self.assertIsNone(self.answers.latest("gpu"))
        self.assertEqual(self.ask(("gpu",)), (400, {"error": "GPU Link is off."}))
        previous_gpu = self.answers.ask("gpu", "Stored while link was on")
        self.assertEqual(self.ask(("phone", "gpu"))[0], 200)
        self.join_workers()
        self.assertEqual(self.answers.latest("gpu")["id"], previous_gpu["id"])
        self.answers.cancel(previous_gpu["id"])
        self.link = FakeLink(self.answers)
        for pc in (None, {**deepcopy(PC), "live": False}):
            self.link.value["pc"] = pc
            status, snap = self.ask(("gpu",))
            self.assertEqual(status, 200)
            self.assertEqual(snap["gpu"]["answer"]["state"], "waiting")
            self.answers.cancel(snap["gpu"]["answer"]["id"])

    def test_busy_target_refuses_whole_question_without_partial_writes(self):
        for pane in ai_answers.PANES:
            for state in ("waiting", "loading", "answering", "paused", "queued"):
                with self.subTest(pane=pane, state=state):
                    held = self.answers.ask(pane, "Existing question")
                    # The store owns state transitions; paused and queued can
                    # also arrive from the link or older clients.
                    current = {**held, "state": state}
                    before = self.answers.snapshot()
                    with mock.patch.object(
                        self.answers,
                        "current",
                        side_effect=lambda p, row=current, target=pane: row if p == target else None,
                    ):
                        self.assertEqual(
                            self.ask(("phone", "gpu")),
                            (400, {"error": "Wait for this answer, or stop it."}),
                        )
                    self.assertEqual(self.answers.snapshot(), before)
                    self.answers.cancel(held["id"])

    def test_link_actions_and_gpu_cancel_reach_service(self):
        for action in ("allow", "deny"):
            self.link.value["pending"] = {"label": "Demo PC", "code": "001234", "seconds": 60}
            self.assertEqual(ai_page.post({"action": action, "code": "001234"})[0], 200)
        for action in ("reopen", "forget"):
            self.assertEqual(ai_page.post({"action": action})[0], 200)
        self.assertEqual(self.link.calls, [("allow", "001234"), ("deny", "001234"), "reopen", "forget"])
        self.ask(("gpu",))
        answer = self.answers.latest("gpu")
        status, snap = ai_page.post({"action": "cancel", "id": answer["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(snap["gpu"]["answer"]["state"], "cancelled")
        self.assertEqual(self.link.calls[-1], ("cancel", answer["id"]))

    def test_enrol_answers_refuse_missing_invalid_stale_and_expired_codes_without_changes(self):
        for action in ("allow", "deny"):
            for pending in (None, {"label": "PC B", "code": "482913", "seconds": 60}):
                self.link.value["pending"] = deepcopy(pending)
                for fields in (
                    {},
                    *(
                        {"code": code}
                        for code in (
                            None,
                            "",
                            "48291",
                            "4829130",
                            "482 913",
                            "abcdef",
                            "４８２９１３",
                            482913,
                            [],
                            {},
                            "590079",
                        )
                    ),
                ):
                    with self.subTest(action=action, pending=pending, fields=fields):
                        before = ai_page.snapshot()
                        stored = self.answers.snapshot()
                        self.assertEqual(
                            ai_page.post({"action": action, **fields}),
                            (400, {"error": "That request is no longer waiting."}),
                        )
                        self.assertEqual(self.link.calls[-1], (action, fields.get("code")))
                        self.assertEqual(ai_page.snapshot(), before)
                        self.assertEqual(self.answers.snapshot(), stored)

    def test_enrol_answers_report_service_refusal_for_the_displayed_code(self):
        for action in ("allow", "deny"):
            with mock.patch.object(self.link, action, return_value=False) as answer:
                self.assertEqual(
                    ai_page.post({"action": action, "code": "482913"}),
                    (400, {"error": "That request is no longer waiting."}),
                )
                answer.assert_called_once_with("482913")

    def test_unknown_actions_unknown_id_and_off_link_are_refused(self):
        for value in (None, [], {}, {"action": "unknown"}, {"action": ["ask"]}, {"action": "cancel", "id": []}):
            self.assertEqual(ai_page.post(value)[0], 400)
        self.link = None
        self.assertEqual(ai_page.post({"action": "allow"}), (400, {"error": "GPU Link is off."}))
        self.answers.ask("gpu", "Stored before link went off")
        ident = self.answers.latest("gpu")["id"]
        self.assertEqual(ai_page.post({"action": "cancel", "id": ident})[0], 200)
        self.assertEqual(ai_page.post({"action": "cancel", "id": ident})[0], 400)

    def test_worker_order_unicode_metrics_and_rate_including_final_append(self):
        stream = self.job(["A", "é", "\x00B\r\n", "<b>text</b>", "\ud800C"])
        times = []
        append = self.answers.append

        def record(*args, **kwargs):
            times.append(time.monotonic())
            return append(*args, **kwargs)

        with mock.patch.object(self.answers, "append", side_effect=record):
            self.assertEqual(self.ask()[0], 200)
            self.join_workers()
        answer = self.answers.latest("phone")
        self.assertEqual(answer["text"], "AéB\n<b>text</b>C")
        self.assertEqual((answer["state"], answer["tokens"], answer["tps"]), ("done", 17, 12.5))
        self.assertEqual((answer["model"], answer["where"]), (ROUTE.name, ROUTE.where))
        self.assertTrue(stream.closed)
        self.assertGreaterEqual(len(times), 2)
        self.assertTrue(all(b - a >= 0.249 for a, b in zip(times, times[1:], strict=False)))
        self.assertEqual(answer["bytes"], len(answer["text"].encode()))

    def test_rate_limit_across_a_longer_stream(self):
        def pieces():
            for _ in range(18):
                yield "x"
                time.sleep(0.05)

        self.job(pieces())
        times = []
        append = self.answers.append

        def record(*args, **kwargs):
            times.append(time.monotonic())
            return append(*args, **kwargs)

        with mock.patch.object(self.answers, "append", side_effect=record):
            self.ask()
            self.join_workers()
        self.assertEqual(self.answers.latest("phone")["text"], "x" * 18)
        self.assertTrue(all(b - a >= 0.249 for a, b in zip(times, times[1:], strict=False)))
        for start in times:
            self.assertLessEqual(sum(start <= at < start + 1 for at in times), 4)

    def test_cancel_closes_stream_in_worker_and_keeps_partial_text(self):
        arrived = threading.Event()
        cancel = []

        def pieces():
            yield "Keep this prefix"
            arrived.set()
            cancel[0].wait(3)
            yield "Discard this suffix"

        stream = FakeStream(pieces())

        def open_stream(*args, **kwargs):
            cancel.append(kwargs["cancel"])
            return stream

        with mock.patch.object(muse, "stream", side_effect=open_stream):
            self.ask()
            self.assertTrue(arrived.wait(3))
            ident = self.answers.latest("phone")["id"]
            self.assertEqual(ai_page.post({"action": "cancel", "id": ident})[0], 200)
            jobs = self.join_workers()
        answer = self.answers.get(ident)
        self.assertEqual((answer["state"], answer["text"]), ("cancelled", "Keep this prefix"))
        self.assertTrue(stream.closed)
        self.assertEqual(stream.closed_thread, jobs[0].thread.ident)

    def test_busy_phone_refuses_then_accepts_after_stop(self):
        release = threading.Event()
        started = threading.Event()

        def pieces():
            started.set()
            release.wait(3)
            yield "First"

        first = FakeStream(pieces())
        second = FakeStream(["Second"])
        with mock.patch.object(muse, "stream", side_effect=[first, second]) as opening:
            self.ask(question="First question")
            self.assertTrue(started.wait(3))
            self.assertEqual(
                self.ask(question="Refused question"), (400, {"error": "Wait for this answer, or stop it."})
            )
            cancelled = self.answers.current("phone")["id"]
            ai_page.post({"action": "cancel", "id": cancelled})
            release.set()
            self.join_workers()
            self.ask(question="Second question")
            self.join_workers()
            self.assertEqual(opening.call_count, 2)
        self.assertEqual(self.answers.latest("phone")["text"], "Second")
        self.assertEqual(self.answers.get(cancelled)["state"], "cancelled")
        self.assertTrue(first.closed and second.closed)

    def test_stream_failure_is_stored_and_not_printed(self):
        stream = self.job(["Prefix"], RuntimeError("private model error"))
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.ask(question="Private question")
            self.join_workers()
        answer = self.answers.latest("phone")
        self.assertEqual((answer["state"], answer["error"], answer["text"]), ("failed", "stream_error", "Prefix"))
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        self.assertTrue(stream.closed)

    def test_open_failure_no_model_and_answer_limit(self):
        with mock.patch.object(muse, "stream", side_effect=muse.MuseError("unavailable")):
            self.ask()
            self.join_workers()
            self.assertEqual(self.answers.latest("phone")["error"], "no_model")
            self.assertFalse(ai_page.snapshot()["phone"]["ready"])
        with mock.patch.object(assist, "local_route", return_value=None), mock.patch.object(muse, "stream") as opening:
            self.ask()
            self.join_workers()
            self.assertEqual(self.answers.latest("phone")["error"], "no_model")
            self.assertFalse(ai_page.snapshot()["phone"]["ready"])
            opening.assert_not_called()
        self.job(["x" * (ai_answers.MAX_ANSWER_BYTES + 1)])
        self.ask()
        self.join_workers()
        self.assertEqual(self.answers.latest("phone")["error"], "too_long")

    def test_missing_metrics_are_not_guessed(self):
        stream = self.job(["Answer"])
        stream.tokens = stream.tps = None
        self.ask()
        self.join_workers()
        answer = self.answers.latest("phone")
        self.assertIsNone(answer["tokens"])
        self.assertIsNone(answer["tps"])

    def test_live_speed_stays_in_memory_and_only_server_metrics_are_stored(self):
        clock = [10.0]

        def pieces():
            for at in (10.0, 10.5, 11.0, 11.5, 12.0, 12.5):
                clock[0] = at
                yield "piece"

        self.job(pieces())
        answer = self.answers.ask("phone", "Timed answer")
        self.answers.begin(answer["id"])
        notes = []
        original = self.answers.note

        def note(*args, **kwargs):
            result = original(*args, **kwargs)
            if "tps" in kwargs:
                notes.append((clock[0], kwargs["tps"], result["state"]))
            return result

        worker = ai_page.PhoneWorker(self.answers, answer["id"])
        with (
            mock.patch.object(ai_page.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(self.answers, "note", side_effect=note),
        ):
            worker._read(ROUTE, answer["question"])
        self.assertEqual(notes, [(12.5, 12.5, "answering")])
        self.assertEqual(worker.live_speed, 2)
        self.assertEqual(self.answers.get(answer["id"])["tps"], 12.5)

    def test_long_stream_without_server_speed_has_live_estimate_but_no_stored_metrics(self):
        estimated = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def pieces():
            yield "First"
            time.sleep(1.05)
            yield " second"
            estimated.set()
            release.wait(3)

        stream = self.job(pieces())
        stream.tokens = stream.tps = None
        self.assertEqual(self.ask()[0], 200)
        self.assertTrue(estimated.wait(3))
        during = ai_page.snapshot()["phone"]
        self.assertEqual(during["answer"]["state"], "answering")
        self.assertIs(type(during["live_speed"]), int)
        self.assertIsNone(during["answer"]["tps"])
        self.assertIsNone(self.answers.latest("phone")["tps"])
        self.assertIsNone(self.answers.latest("phone")["tokens"])
        release.set()
        self.join_workers()
        after = ai_page.snapshot()["phone"]
        self.assertEqual(after["answer"]["state"], "done")
        self.assertIsNone(after["live_speed"])
        self.assertIsNone(after["answer"]["tps"])
        self.assertIsNone(after["answer"]["tokens"])

    def test_worker_start_failure_is_a_stored_failure(self):
        with mock.patch.object(ai_page.PhoneWorker, "start", side_effect=RuntimeError("no threads")):
            status, snap = self.ask()
        self.assertEqual(status, 200)
        self.assertEqual(snap["phone"]["answer"]["state"], "failed")
        self.assertEqual(snap["phone"]["answer"]["error"], "worker_start")
        self.assertEqual(self.state.workers, {})

    def test_worker_start_and_storage_failure_returns_sentence_then_snapshot_retries(self):
        with (
            mock.patch.object(ai_page.PhoneWorker, "start", side_effect=RuntimeError("no threads")),
            mock.patch.object(self.answers, "fail", side_effect=OSError("storage unavailable")),
        ):
            self.assertEqual(self.ask(), (500, {"error": "The phone could not save the answer."}))
            answer = self.answers.latest("phone")
            self.assertEqual(answer["state"], "waiting")
            self.assertEqual(self.state.workers[answer["id"]].problem, "worker_start")
            with self.assertRaises(OSError):
                ai_page.snapshot()
            self.assertEqual(self.state.workers[answer["id"]].problem, "worker_start")
        snap = ai_page.snapshot()
        self.assertEqual(snap["phone"]["answer"]["state"], "failed")
        self.assertEqual(snap["phone"]["answer"]["error"], "worker_start")
        self.assertEqual(self.state.workers, {})
        self.job(["Recovered"])
        self.assertEqual(self.ask()[0], 200)
        self.join_workers()
        self.assertEqual(self.answers.latest("phone")["text"], "Recovered")

    def test_storage_failure_is_retained_and_retried_without_an_unhandled_thread_error(self):
        self.job([], RuntimeError("stream failed"))
        with (
            mock.patch.object(self.answers, "fail", side_effect=OSError("storage unavailable")),
            mock.patch.object(threading, "excepthook") as unhandled,
        ):
            # Call ask without its following snapshot so the failed write can be
            # observed before the page retries it. No invented answer is served.
            self.state.ask({"question": "Public question", "panes": ["phone"]})
            jobs = self.join_workers()
            self.assertEqual(jobs[0].problem, "storage_error")
            with self.assertRaises(OSError):
                ai_page.snapshot()
            unhandled.assert_not_called()
        snap = ai_page.snapshot()
        self.assertEqual(snap["phone"]["answer"]["state"], "failed")
        self.assertEqual(snap["phone"]["answer"]["error"], "storage_error")

    def test_close_cancels_queued_workers_and_joins_every_thread(self):
        event = []
        started = threading.Event()

        def pieces():
            started.set()
            event[0].wait(3)
            yield "Discard after cancellation"

        stream = FakeStream(pieces())

        def open_stream(*args, **kwargs):
            event.append(kwargs["cancel"])
            return stream

        with mock.patch.object(muse, "stream", side_effect=open_stream):
            self.ask()
            self.assertTrue(started.wait(3))
            self.assertEqual(self.ask(question="Refused question")[0], 400)
            self.state.close()
        self.assertTrue(stream.closed)
        self.assertEqual({a["state"] for a in self.answers.snapshot()["answers"]}, {"cancelled"})
        self.assertEqual(self.state.workers, {})

    def test_route_uses_free_memory_even_when_unknown(self):
        self.job(["Answer"])
        with (
            mock.patch.object(assist, "free_mb", return_value=None),
            mock.patch.object(assist, "local_route", return_value=None) as route,
        ):
            self.ask()
            self.join_workers()
            route.assert_called_with(free=None)
            self.assertEqual(self.answers.latest("phone")["error"], "no_model")

    def test_locked_data_route_contains_no_question_or_answer(self):
        a = self.answers.ask("gpu", "PRIVATE_QUESTION")
        self.answers.begin(a["id"])
        self.answers.append(a["id"], 0, b"PRIVATE_ANSWER")
        with running() as server, mock.patch.object(lockpin, "is_locked", return_value=True):
            status, raw = request(server, "/api/ai")
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)["locked"])
            self.assertNotIn(b"PRIVATE_", raw)
            self.assertNotIn("phone", json.loads(raw))
            self.assertEqual(json.loads(raw), json.loads(request(server, "/api/state")[1]))
            self.assertEqual(
                request(server, "/api/ai", {"action": "ask", "panes": ["gpu"], "question": "hidden"})[0], 403
            )
        self.assertEqual(len(self.answers.snapshot()["answers"]), 1)

    def test_document_controls_tokens_layout_and_plain_text(self):
        html = ai_page.render("t", "n")
        tags = Document(html).tags
        ids = {attrs["id"]: tag for tag, attrs in tags if "id" in attrs}
        for ident in ("phone-pane", "gpu-pane", "phone-answer", "gpu-answer"):
            self.assertIn(ident, ids)
        for ident in ("ask", "allow", "deny", "forget", "reopen", "phone-stop", "gpu-stop"):
            self.assertEqual(ids[ident], "button")
        self.assertEqual(ids["question"], "input")
        self.assertFalse(any(tag in {"img", "svg", "canvas", "iframe"} for tag, _ in tags))
        style = re.search(r"<style[^>]*>(.*?)</style>", html, re.S)[1]
        self.assertLessEqual(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", style)), {"#1a1a1a", "#f3f0e8", "#fff", "#000"})
        self.assertIn("max(18px,1rem)", style)
        self.assertIn("prefers-reduced-motion", style)
        self.assertIn(":focus-visible", style)
        self.assertIn("var(--hinge)", style)
        self.assertIn("html[data-pane] main{display:block}", style)
        self.assertIn("Newsreader", style)
        self.assertIn("Inter Tight", style)
        self.assertIn("font-size:32px", style)
        self.assertIn("max-height:30vh", style)
        self.assertIn("max-height:55vh", style)
        self.assertIn("grid-template-rows:subgrid", style)
        self.assertIn('button:disabled::after{content:"\u00a0unavailable"}', style)
        attrs = {attrs["id"]: attrs for _, attrs in tags if "id" in attrs}
        for pane in ("phone", "gpu"):
            self.assertEqual(attrs[pane + "-answer"]["tabindex"], "0")
            self.assertEqual(attrs[pane + "-answer"]["role"], "region")
            self.assertTrue(attrs[pane + "-answer"]["aria-label"])
        self.assertLess(html.index('id="ai-title"'), html.index('id="controls"'))
        self.assertLess(html.index('id="controls"'), html.index('id="phone-pane"'))
        self.assertLess(html.index('id="phone-pane"'), html.index('id="gpu-pane"'))
        self.assertLess(html.index('id="gpu-pane"'), html.index('id="home"'))
        self.assertEqual(html.count('id="home"'), 1)
        for pane in ("phone", "gpu"):
            positions = [
                html.index('id="' + pane + suffix + '"')
                for suffix in ("-title", "-model", "-speed", "-state", "-answer")
            ]
            self.assertEqual(positions, sorted(positions))
        # The cable line sits between the speed details and the disclosure, so it is read with the headline.
        self.assertLess(html.index('id="gpu-state"'), html.index('id="gpu-cable"'))
        self.assertLess(html.index('id="gpu-cable"'), html.index('id="gpu-disclosure"'))
        self.assertIn("#gpu-cable{font-size:24px", style)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("insertAdjacentHTML", html)
        self.assertNotIn("document.write", html)
        # No content print/log call in the implementation, including error paths.
        tree = ast.parse(Path(ai_page.__file__).read_text(encoding="utf-8"))
        self.assertFalse(
            any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"
                for n in ast.walk(tree)
            )
        )
        self.assertEqual(apps.by_id("ai").locked_json, None)
        self.assertIn(ai_page.DISCLOSURE, apps_page.index_html("t", "n"))

    def test_wide_layout_rows_are_consistent(self):
        css = ai_page._CSS
        block = css[css.index("@media(min-width:1000px){") :]
        block = block[: block.index("\n}\n")]
        rules = dict(re.findall(r"html:not\(\[data-pane\]\) ([^{]+)\{([^}]*)\}", block))

        def row(selector):
            return int(re.search(r"grid-row:(\d+)", rules[selector])[1])

        pane = re.search(r"grid-row:(\d+) / span (\d+)", rules[".pane"])
        start, span = int(pane[1]), int(pane[2])
        children = [".pane h2", ".model", ".speed", ".details", "#gpu-cable", "#gpu-disclosure", ".answer", ".stop"]
        children.append("#link-controls")
        # Every part of a pane has its own row, they fill the subgrid exactly, and both panes share them.
        self.assertEqual([row(selector) for selector in children], list(range(1, span + 1)))
        self.assertEqual(start, row("#controls") + 1)
        # Home sits in the row after the panes and the hinge gap spans every row down to it.
        self.assertEqual(row(".nav"), start + span)
        self.assertEqual(int(re.search(r"grid-row:1 / span (\d+)", rules["#gap"])[1]), row(".nav"))

    def test_wide_layout_columns_and_grid(self):
        css = ai_page._CSS
        block = css[css.index("@media(min-width:1000px){") :]
        block = block[: block.index("\n}\n")]
        rules = dict(re.findall(r"html:not\(\[data-pane\]\) ([^{]+)\{([^}]*)\}", block))

        def column(selector):
            return int(re.search(r"grid-column:(\d+)", rules[selector])[1])

        selectors = ("#ai-title", "#controls", "#phone-pane", "#gap", "#gpu-pane", ".nav")
        self.assertEqual([column(selector) for selector in selectors], [1, 1, 1, 2, 3, 3])
        self.assertIn("display:grid", rules[".pane"])
        self.assertIn("grid-template-rows:subgrid", rules[".pane"])

    def test_actual_document_script_in_dom(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed; AI page DOM checks need Node.js")
        # Supply actual store output with hostile-looking text; it must remain text.
        answer = self.answers.ask("gpu", "Public question")
        self.answers.begin(answer["id"])
        self.answers.append(answer["id"], 0, b"<script>attack()</script><b>literal</b>", done=True, finish="stop")
        path = Path(self.tmp.name) / "dom.json"
        # Read the script from the rendered document, not from a copied fixture.
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", ai_page.render("t", "n"), re.S)
        path.write_text(json.dumps({"script": scripts[-1], "snapshot": ai_page.snapshot()}), encoding="utf-8")
        result = subprocess.run(
            [node, "--test-reporter=tap", str(Path(__file__).with_name("ai_page_test.js")), str(path)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("# fail 0", result.stdout)

    def test_phone_stream_asks_for_4096_tokens(self):
        with mock.patch.object(muse, "stream", return_value=FakeStream(["a"])) as opening:
            self.ask()
            self.join_workers()
        self.assertEqual(opening.call_args.kwargs["max_tokens"], 4096)

    def test_exactly_full_answer_is_stored_not_too_long(self):
        # An answer of exactly the byte limit is allowed; only one byte more is refused.
        self.job(["x" * ai_answers.MAX_ANSWER_BYTES])
        self.ask()
        self.join_workers()
        row = self.answers.latest("phone")
        self.assertEqual((row["state"], row["bytes"], row["error"]), ("done", ai_answers.MAX_ANSWER_BYTES, None))

    def test_finish_reason_is_kept_and_defaults_to_stop(self):
        for finish, expected in (("length", "length"), (None, "stop")):
            with self.subTest(finish=finish):
                stream = self.job(["Cut"])
                stream.finish = finish
                self.ask()
                self.join_workers()
                self.assertEqual(self.answers.latest("phone")["finish"], expected)

    def test_user_cancel_leaves_no_storage_problem(self):
        # Ending a worker whose answer is already final must not touch the answer again.
        arrived = threading.Event()
        cancel = []

        def pieces():
            yield "Keep"
            arrived.set()
            cancel[0].wait(3)
            yield "Drop"

        def open_stream(*args, **kwargs):
            cancel.append(kwargs["cancel"])
            return FakeStream(pieces())

        with mock.patch.object(muse, "stream", side_effect=open_stream):
            self.ask()
            self.assertTrue(arrived.wait(3))
            ident = self.answers.latest("phone")["id"]
            self.assertEqual(ai_page.post({"action": "cancel", "id": ident})[0], 200)
            # Stop reaches the worker through its own event, not only through the store refusing its next append.
            self.assertTrue(cancel[0].is_set())
            jobs = self.join_workers()
        self.assertEqual([job.problem for job in jobs], [None])
        self.assertEqual(ai_page.post({"action": "ask", "question": "again", "panes": ["gpu"]})[0], 200)

    def test_ask_is_accepted_with_exactly_max_unfinished_answers(self):
        # Unfinished answers plus the new one may equal the limit; one more is refused.
        for i in range(ai_answers.MAX_ANSWERS - 1):
            self.answers.ask("phone", str(i))
        status, body = ai_page.post({"action": "ask", "question": "last slot", "panes": ["gpu"]})
        self.assertEqual(status, 200, body)


if __name__ == "__main__":
    unittest.main()
