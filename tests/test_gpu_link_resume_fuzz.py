# SPDX-License-Identifier: GPL-3.0-or-later
"""Seeded fault injection for GPU Link pause and resume (design section 9).

A PC with a fixed multibyte text per question meets lost replies, pulled cables, phone restarts, failed
writes, replayed and wrong posts, and cancels. Each stored answer must be a byte prefix of its PC text,
hold every acknowledged byte, never lose bytes or change once final, and equal the PC text when done.
Only a late failed write may leave its exact replacement on disk ahead of unchanged memory, until a
successful write or restart. OPOS_FUZZ_SEEDS (default 200) and OPOS_FUZZ_FIRST choose the seeds.
"""

import contextlib
import os
import random
import tempfile
import unittest
from pathlib import Path

from opos import gpu_link_proto as proto
from opos.ai_answers import FINAL, AnswerError, AnswerStore
from opos.gpu_link import LinkService
from opos.store import Store
from tests.gpu_link_fake_pc import Clock, FakePC, RandomBytes

PIECES = ("plain words ", "héllo ", "世界", "🙂", "αβγ\n", "a\tb ", "naïve — ", "x")
LIMIT = proto.MAX_ANSWER_BYTES
STEPS = ("post",) * 8 + ("lost",) * 3 + ("cut", "cut", "poll", "poll", "poll_lost", "poll_failed")
STEPS += ("replay", "replay", "bad", "bad", "status", "drop", "restart", "churn")


class Run:
    """One phone and one PC; ``log`` names every step taken, for a failure report."""

    def __init__(self, seed, root):
        self.rng = random.Random(seed)
        self.store, self.clock, self.rand = Store(root / "state"), Clock(), RandomBytes()
        self.fault, self.real, self.store.write_json = "", self.store.write_json, self.write
        self.link, self.pc, self.model = None, FakePC(), root / "model.gguf"
        self.model.write_bytes(b"model")
        self.late = {}
        self.log, self.posts, self.jobs, self.seen, self.acked, self.final = [], [], {}, {}, {}, set()
        self.restart()
        assert self.pc.enrol().status == 202 and self.link.allow(self.pc.code)
        self.pc.hello()
        self.offered = set()
        self.lease, self.pending, self.job, self.offset, self.said = None, None, None, 0, -10.0
        for _ in range(49 if seed % 8 == 0 else 0):  # start next to the 50-answer limit
            self.answers.cancel(self.answers.ask("phone", "another pane")["id"])
        self.ask()

    def write(self, name, record, *, durable=False):
        ident = record["answer"]["id"] if name.startswith("ai-answer-") else None
        if self.fault != "+write-error":  # "late": the file is replaced, then the call fails
            self.real(name, record, durable=durable)
            if ident is not None:
                if self.fault:
                    before = self.answers.get(ident)
                    if before is not None:
                        before["state"] = before["state"].replace("waiting", "queued")
                    self.late[ident] = record["answer"].copy(), before
                else:
                    self.late.pop(ident, None)
        if self.fault:
            raise OSError("disk")

    def restart(self):
        files = {
            ident: self.store.read_json(f"ai-answer-{ident}.json", {})[0]["answer"]
            for ident, _, _ in self.jobs.values()
        }
        if self.link is not None:
            # Model a power loss: close joins the reader, but its pause must not overwrite the file.
            self.fault = "+write-error"
            try:
                self.link.close()
            finally:
                self.fault = ""
        self.answers = AnswerStore(self.store)
        self.link = self.pc.link = LinkService(self.answers, self.store, self.model, clock=self.clock, rand=self.rand)
        if self.link._hasher is not None:
            self.link._hasher.join()
        self.pc.hello()
        for ident, before in files.items():
            answer = self.answers.get(ident)
            disk = self.store.read_json(f"ai-answer-{ident}.json", {})[0]["answer"]
            assert answer["text"] == before["text"], f"{ident}: restart did not load the file's text"
            assert len(answer["text"].encode()) >= self.acked[ident], f"{ident}: restart lost acknowledged bytes"
            expected_state = "paused" if before["state"] in ("loading", "answering") else before["state"]
            answer["state"] = answer["state"].replace("waiting", "queued")
            assert answer["state"] == expected_state, f"{ident}: restart did not recover the file's state"
            assert answer == disk, f"{ident}: memory differs from the file after restart"
        self.late.clear()

    def ask(self):
        size = self.rng.randrange(LIMIT - 400, LIMIT + 900) if self.rng.random() < 0.1 else self.rng.randrange(30, 700)
        text = ""
        while len(text.encode()) < size:
            text += self.rng.choice(PIECES)
        raw, question = text.encode(), f"question {len(self.jobs)}"
        self.answer = self.answers.ask("gpu", question)["id"]
        whole = raw[:LIMIT].decode("utf-8", "ignore").encode()
        self.jobs[question] = (self.answer, whole, "length" if len(raw) > LIMIT else "stop")
        self.seen[self.answer], self.acked[self.answer] = b"", 0

    def tock(self, seconds, plugged=True):
        self.clock.advance(seconds)
        if plugged and self.clock() - self.said >= 1:
            self.status(self.lease)
        self.link.tick()

    def status(self, lease, state=None, **values):
        self.said = self.clock()
        reply = self.pc.status(state or ("busy" if lease else "ready"), lease, **values)
        if reply.status == 404 or (reply.status == 200 and self.pc.data(reply)["cancel"] == self.lease):
            self.lease = self.pending = None

    def post(self, lost=False, big=False):
        self.tock(self.rng.choice((0.05, 0.25, 0.25, 0.3)))
        if self.lease is None:
            return self.poll()
        ident, raw, finish = self.job
        if self.pending is None:
            rest = raw[self.offset :].decode()
            count = 900 if big else self.rng.randrange(1, 900 if len(raw) > 4000 else 24)
            self.pending = (self.offset, rest[:count], count >= len(rest))
        offset, text, done = self.pending
        sent = (self.lease, ident, offset, text, done, finish if done else None)
        self.posts.append(sent)
        reply = self.pc.text(self.lease, *sent[2:])
        if lost or reply.status in (429, 500):
            return None  # The PC sends this same post again.
        assert reply.status in (200, 404, 409), f"a post was answered {reply.status}"
        self.pending, value = None, self.pc.data(reply)
        self.offset = value.get("acked", value.get("stored", self.offset))
        self.acked[ident] = max(self.acked[ident], value.get("acked", 0))
        if reply.status == 404 or (reply.status == 200 and done):
            self.lease = None

    def poll(self, outcome="ok"):
        method, target, headers, body = self.pc.prepare("GET", "/v1/work?wait=0")
        reply = self.link.handle(method, target, headers, body)
        self.pc.verify(reply, target, bytes.fromhex(headers["X-Link-Nonce"]))
        assert reply.status in (200, 204, 500), f"a work request was answered {reply.status}"
        if reply.status == 200:  # "failed": the phone could not write the reply
            lease = self.pc.data(reply)["lease"]
            assert lease not in self.offered, "work reused an earlier lease"
            self.offered.add(lease)
            (reply.failed if outcome == "failed" else reply.sent)()
        if reply.status == 200 and outcome == "ok":  # "lost": written by the phone, never read by the PC
            work = self.pc.data(reply)
            self.job = self.jobs[work["question"]]
            stored = self.answers.get(self.job[0])["text"]
            assert work["partial"] == stored, "partial differs from the stored text"
            self.lease, self.offset, self.pending = work["lease"], len(stored.encode()), None

    def replay(self):
        if self.posts:
            lease, ident, offset, text, done, finish = self.rng.choice(self.posts)
            if self.lease and self.job[0] == ident and self.rng.random() < 0.5:
                lease = self.lease  # an old post of this answer under the live lease
            self.pc.text(lease, offset, text, done, finish)

    def bad(self):
        stored, lease = len(self.seen[self.answer]), self.lease or "ff" * 16
        wrong = (
            (lease, stored + 1, "Z"),  # ahead of the stored bytes
            (lease, 0, "Z" * min(stored, 4000)),  # other bytes where text is stored
            (lease, stored, "Z" * (LIMIT - stored + 1)),  # one byte past the answer limit, or past the body limit
            (self.rand(16).hex(), stored, "Z"),  # a lease that was never given out
        )
        reply = self.pc.text(*self.rng.choice(wrong))
        assert reply.status in (400, 401, 404, 409, 413, 429), f"a wrong post was answered {reply.status}"

    def drop(self):  # The PC gives up its work; two statuses without the lease pause the answer.
        self.lease = self.pending = None
        for _ in range(self.rng.randrange(1, 3)):
            self.status(None)

    def step(self, name):
        action = {
            "lost": lambda: self.post(lost=True),
            "poll_lost": lambda: self.poll("lost"),
            "poll_failed": lambda: self.poll("failed"),
            "status": lambda: self.status(self.lease, tokens=self.rng.randrange(99), tps=self.rng.randrange(99)),
            "cut": lambda: [self.tock(0.25, plugged=False) for _ in range(self.rng.randrange(1, 16))],
            "churn": lambda: self.answers.cancel(self.answers.ask("phone", "another pane")["id"]),
            "cancel": lambda: self.link.cancel(self.answer),
            "error": lambda: self.lease and self.status(self.lease, "error", error="engine"),
        }.get(name) or getattr(self, name)
        self.fault = "" if name == "restart" else self.rng.choice(("",) * 23 + ("+write-error", "+late-write-error"))
        self.log.append(name + self.fault)
        with contextlib.suppress(OSError, AnswerError):  # raised by the page's calls: cancel and churn
            action()
        self.fault = ""
        self.check()
        if self.answer in self.final and len(self.jobs) < 3:
            self.ask()

    def check(self):
        for ident, raw, finish in self.jobs.values():
            answer = self.answers.get(ident)
            got, state = answer["text"].encode(), answer["state"]
            disk = self.store.read_json(f"ai-answer-{ident}.json", {})[0]["answer"]
            assert raw.startswith(got), f"{ident}: the stored text is not a prefix of the PC's text"
            assert got.startswith(self.seen[ident]), f"{ident}: the stored text lost bytes"
            assert len(got) >= self.acked[ident], f"{ident}: acknowledged bytes are missing"
            answer["state"] = state.replace("waiting", "queued")
            if ident in self.late:
                attempted, before = self.late[ident]
                assert answer == before, f"{ident}: a failed write changed memory"
                assert disk == attempted, f"{ident}: file differs from the interrupted write"
                assert disk["text"].encode().startswith(got), f"{ident}: file is behind memory"
                assert raw.startswith(disk["text"].encode()), f"{ident}: file is not a prefix of the PC's text"
                assert disk["state"] != "done" or (disk["text"].encode(), disk["finish"]) == (raw, finish), (
                    f"{ident}: file is done but incomplete"
                )
            else:
                assert disk == answer, f"{ident}: file differs"
            assert ident not in self.final or got == self.seen[ident], f"{ident}: the text changed after its end"
            assert state != "done" or (got, answer["finish"]) == (raw, finish), f"{ident}: done but incomplete"
            self.seen[ident] = got
            self.final.update({ident} if state in FINAL else ())

    def play(self):
        for _ in range(self.rng.randrange(20, 90)):
            self.step(self.rng.choice(("cancel", "error")) if self.rng.random() < 0.008 else self.rng.choice(STEPS))
        for _ in range(3000):  # No more faults: the open answer must now finish.
            if self.answer in self.final:
                break
            self.post(big=True)
            self.check()
        assert self.answer in self.final, "the answer did not finish after the faults stopped"


class ResumeFuzz(unittest.TestCase):
    def test_stored_answer_is_always_a_prefix_and_ends_exact(self):
        first = int(os.environ.get("OPOS_FUZZ_FIRST", "0"))
        for seed in range(first, first + int(os.environ.get("OPOS_FUZZ_SEEDS", "200"))):
            with self.subTest(seed=seed), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
                run = Run(seed, Path(root))
                try:
                    run.play()
                except AssertionError as err:
                    self.fail(f"seed {seed}: {err}; steps: {' '.join(run.log)}")
                finally:
                    run.link.close()


if __name__ == "__main__":
    unittest.main()
