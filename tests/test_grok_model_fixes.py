# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressions for Grok model-plumbing findings G through O; no network I/O."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from opos import assist, muse
from opos.advisor import GlimmerAdvisor
from opos.governor import Decision, Mode, PowerState
from opos.reserve import WORN_REASON, reserve_level
from opos.router import Router
from opos.runtime import DEFAULT_TASKS, Runtime
from opos.setup import services


def response(content):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


class TestGRouteBudget(unittest.TestCase):
    ENV = {"OPOS_LAN_URL": "http://127.0.0.1:9001/v1"}

    def run_slow_routes(self, operation):
        now = [0.0]
        calls = []

        def ask(route, prompt, **kwargs):
            calls.append((route.key, kwargs["timeout"]))
            if route.key != "glimmer":
                now[0] += kwargs["timeout"]
                raise muse.MuseError("timed out")
            return '["c", "a"]' if operation == "plan" else "Glimmer answered."

        with mock.patch.object(muse.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(muse, "ask", side_effect=ask):
            routes = assist.explain_routes(self.ENV, free=2000)
            if operation == "why":
                result = muse.explain({}, routes, env=self.ENV)
            elif operation == "plan":
                result = muse.plan({}, ("a", "b", "c"), routes, env=self.ENV)
            else:
                result = assist.answer("word " * 20, env=self.ENV, free=2000)
        self.assertEqual([key for key, _ in calls], ["lan", "local", "glimmer"])
        for _, timeout in calls:
            self.assertAlmostEqual(timeout, muse.HELPER_DEADLINE_S / 3)
        if operation == "plan":
            self.assertEqual(result, ["c", "a", "b"])
            self.assertEqual(result.route.key, "glimmer")
        else:
            self.assertTrue(result["ok"])
            self.assertEqual(result["where"], "on a local server")

    def test_why_reaches_third_route_after_two_timeouts(self):
        self.run_slow_routes("why")

    def test_plan_reaches_third_route_after_two_timeouts(self):
        self.run_slow_routes("plan")

    def test_ask_reaches_third_route_after_two_timeouts(self):
        self.run_slow_routes("ask")

    def test_a_single_route_gets_the_whole_remaining_budget(self):
        with mock.patch.object(muse.time, "monotonic", return_value=0):
            attempts = list(muse._attempts([muse.glimmer_route({})], {}, 115))
        self.assertEqual(attempts[0][1], 115)

    def test_ask_error_does_not_name_the_last_failed_route(self):
        with mock.patch.object(muse, "ask", side_effect=muse.MuseError("down")):
            result = assist.answer("word " * 20, env=self.ENV, free=2000)
        self.assertFalse(result["ok"])
        self.assertIn("The helper did not answer.", result["text"])
        self.assertNotIn("Muse Glimmer", result["text"])
        self.assertNotIn("on a local server", result["text"])
        self.assertNotIn("on your computer is busy", result["text"])

    def test_ask_error_is_neutral_when_only_one_route_was_attempted(self):
        now = [0.0]

        def expired(route, prompt, **kwargs):
            now[0] = muse.HELPER_DEADLINE_S
            raise muse.MuseError("deadline expired")

        with mock.patch.object(muse.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(muse, "ask", side_effect=expired) as ask:
            result = assist.answer("word " * 20, env=self.ENV, free=2000)
        ask.assert_called_once()
        self.assertIn("The helper did not answer.", result["text"])
        self.assertNotIn("on a computer you own", result["text"])
        self.assertNotIn("on a local server", result["text"])


class TestHPlanLists(unittest.TestCase):
    def check_order(self, content, expected):
        route = muse.glimmer_route({})
        result = muse.plan({}, ("a", "b", "c"), route, env={},
                           opener=lambda *a, **k: response(content))
        self.assertEqual(result, expected)
        return result

    def test_skips_leading_empty_list(self):
        self.check_order('[]\n["c","a"]', ["c", "a", "b"])

    def test_skips_leading_non_step_list(self):
        self.check_order('["email"]\n["c","a"]', ["c", "a", "b"])

    def test_uses_last_valid_step_list(self):
        self.check_order('["b"]\n["c","a"]\n["email"]\n[]', ["c", "a", "b"])

    def test_mixed_unknown_list_is_not_a_step_order(self):
        result = self.check_order('["c","unknown","a"]', ["a", "b", "c"])
        self.assertIsNone(result.route)


class TestIProbe(unittest.TestCase):
    def test_probe_rejects_empty_missing_or_nontext_content(self):
        for choice in ({"message": {"content": ""}}, {}, {"message": {}},
                       {"message": {"content": " \n "}},
                       {"message": {"content": None}}, {"message": {"content": []}}):
            with self.subTest(choice=choice):
                opener = lambda *a, **k: io.BytesIO(json.dumps({"choices": [choice]}).encode())
                self.assertEqual(muse.probe(env={}, opener=opener),
                                 {"answered": False, "error": "empty reply", "latency_s": mock.ANY, "finish_reason": None, "reply_model": None})

    def test_probe_uses_the_ask_token_budget_and_reasoning_effort(self):
        payloads = []

        def opener(req, timeout=None):
            payloads.append(json.loads(req.data))
            return response("OK")

        muse.ask(muse.glimmer_route({}), "Reply with OK.", env={}, opener=opener)
        self.assertEqual(muse.probe(env={}, opener=opener), {"answered": True, "latency_s": mock.ANY, "reply": "OK", "finish_reason": None, "reply_model": None})
        self.assertEqual(payloads[1]["max_tokens"], payloads[0]["max_tokens"])
        self.assertEqual(payloads[1].get("reasoning_effort"), payloads[0]["reasoning_effort"])

    def test_check_probe_exits_nonzero_for_an_empty_reply(self):
        out = io.StringIO()
        with mock.patch.object(muse, "check", return_value={"reachable": True, "model_served": True}), \
                mock.patch.object(muse, "_open_no_redirect", side_effect=lambda *a, **k: response("")), \
                contextlib.redirect_stdout(out):
            code = muse.main(["muse", "--check", "--probe"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["answered"])


class AdvisorFixture(unittest.TestCase):
    STATE = PowerState(90, False, 1, 25)

    def advisor(self, reply='{"defer":["email"]}', **kwargs):
        return GlimmerAdvisor(Router(visible=True), route=muse.glimmer_route({}),
                              ask=lambda *a, **k: reply, clock=lambda: 0, **kwargs)


class TestJAdvisorObjects(AdvisorFixture):
    def test_scans_past_invalid_or_unrelated_objects(self):
        for prefix in ('{note}', '{"note":"{braces}"}', '{"defer":"email"}',
                       '{"defer":null}'):
            with self.subTest(prefix=prefix):
                adv = self.advisor(prefix + ' {"defer":["email"]}', spawn=lambda fn: fn())
                adv(self.STATE, DEFAULT_TASKS)
                decision = adv(self.STATE, DEFAULT_TASKS)
                self.assertIn("email", decision.deferred)
                self.assertEqual(adv.down_until, 0)

    def test_last_defer_list_wins_even_when_empty(self):
        adv = self.advisor('{"defer":["email"]} {"defer":[]}', spawn=lambda fn: fn())
        adv(self.STATE, DEFAULT_TASKS)
        self.assertIn("email", adv(self.STATE, DEFAULT_TASKS).allowed)
        self.assertEqual(len(adv.cache), 1)


class TestKAdvisorConcurrency(AdvisorFixture):
    def test_spawn_failure_releases_pending_and_allows_retry(self):
        queued = []
        spawn = mock.Mock(side_effect=[RuntimeError("cannot start thread"), None])
        adv = self.advisor(spawn=spawn)
        with contextlib.suppress(RuntimeError):
            adv(self.STATE, DEFAULT_TASKS)
        self.assertEqual(adv.pending, set())
        adv.spawn = queued.append
        adv(self.STATE, DEFAULT_TASKS)
        self.assertEqual(len(queued), 1)
        queued[0]()
        self.assertIn("email", adv(self.STATE, DEFAULT_TASKS).deferred)

    def test_cached_pause_survives_concurrent_eviction(self):
        reader_checked = threading.Event()
        writer_progress = threading.Event()
        real_lock = threading.Lock()
        queued = []
        errors = []
        case = self

        class ObservableLock:
            def __enter__(self):
                if real_lock.locked():
                    writer_progress.set()
                real_lock.acquire()
                return self

            def __exit__(self, *exc):
                real_lock.release()

        with mock.patch("opos.advisor.threading.Lock", return_value=ObservableLock()):
            adv = self.advisor(spawn=queued.append)
        adv(self.STATE, DEFAULT_TASKS)
        queued.pop()()
        target = next(iter(adv.cache))
        adv.cache.update({("filler", i): () for i in range(63)})
        adv(PowerState(80, False, 1, 25), DEFAULT_TASKS)
        callback = queued.pop()

        class GateDict(dict):
            def gate(self, key):
                if key == target:
                    reader_checked.set()
                    case.assertTrue(writer_progress.wait(3), "writer never reached eviction")

            def __contains__(self, key):
                found = super().__contains__(key)
                self.gate(key)
                return found

            def get(self, key, default=None):
                value = super().get(key, default)
                self.gate(key)
                return value

            def clear(self):
                super().clear()
                writer_progress.set()

        adv.cache = GateDict(adv.cache)

        def worker():
            try:
                case.assertTrue(reader_checked.wait(3), "reader never looked up cached advice")
                callback()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            decision = adv(self.STATE, DEFAULT_TASKS)
            self.assertIn("email", decision.deferred)
        finally:
            thread.join(3)
        self.assertFalse(thread.is_alive(), "advisor worker did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(adv.pending, set())
        self.assertEqual(len(adv.cache), 1)


class TestLCharging(unittest.TestCase):
    def test_critical_charging_phone_does_not_rest(self):
        context = {"mode": "CRITICAL", "usable_percent": 10, "charging": True}
        self.assertFalse(assist.is_resting(context))

    def test_critical_charging_phone_can_ask(self):
        with mock.patch.object(muse, "ask", return_value="The phone is charging.") as ask:
            result = assist.answer("why paused?", env={}, free=None,
                                   context={"mode": "CRITICAL", "usable_percent": 10, "charging": True})
        self.assertTrue(result["ok"])
        ask.assert_called_once()


class TestMReserveAttribution(unittest.TestCase):
    def test_unchanged_worn_reserve_keeps_glimmer_credit(self):
        runtime = Runtime(visible=True)
        runtime.governor.decide = lambda *a: Decision(
            Mode.CRITICAL, ("calls", "messages"), ("email", "maps", "photos backup"), "router+glimmer")
        tick = runtime.step(0, reserve_level(30, 0.35) + 2, health=0.35, temp_c=30)
        self.assertTrue(tick.decision.reason.startswith("router+glimmer"), tick.decision.reason)
        self.assertIn("Helper: Muse Glimmer paused extra tasks", " ".join(tick.screen))

    def test_reserve_adding_pauses_keeps_reserve_reason(self):
        runtime = Runtime()
        decision = Decision(Mode.SAVER, ("calls", "messages", "email"),
                            ("maps", "photos backup"), "router+glimmer")
        runtime.governor.decide = lambda *a: decision
        with mock.patch("opos.runtime.rule_decide", return_value=decision):
            tick = runtime.step(0, reserve_level(30, 0.35) + 2, health=0.35, temp_c=30)
        self.assertEqual(tick.decision.reason, WORN_REASON)
        self.assertIn("email", tick.decision.deferred)


class TestNUninstall(unittest.TestCase):
    def check_failed_stop(self, platform):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            filename = services.UNIT_NAME if platform == "linux" else services.LAUNCHD_LABEL + ".plist"
            path = services.unit_dir(home, platform) / filename
            path.parent.mkdir(parents=True)
            path.write_text("keep this service definition\n", encoding="utf-8")
            # On macOS a successful `launchctl print` means the job is still loaded after bootout failed.
            runner = mock.Mock(side_effect=lambda cmd: (0, "") if "daemon-reload" in cmd or "print" in cmd
                               else (1, "stop denied"))
            with self.assertRaises(services.ServiceError) as raised:
                services.uninstall_services(home, platform, runner=runner)
            self.assertEqual(path.read_text(encoding="utf-8"), "keep this service definition\n")
            self.assertIn("stop failed", str(raised.exception).lower())
            self.assertIn("kept", str(raised.exception).lower())
            self.assertIn("stop denied", str(raised.exception))
            self.assertEqual(raised.exception.actions, [])
            self.assertEqual(runner.call_count, 1 if platform == "linux" else 2)

    def test_linux_failed_stop_preserves_unit(self):
        self.check_failed_stop("linux")

    def test_darwin_failed_stop_preserves_plist(self):
        self.check_failed_stop("darwin")


class TestOWhyFallback(unittest.TestCase):
    def test_why_fallback_preserves_the_supplied_reason(self):
        for why in ("At this rate the battery will run out before 23:00.",
                    "Nothing is paused; calls and messages remain on."):
            with self.subTest(why=why), mock.patch.object(muse, "ask", side_effect=muse.MuseError("down")):
                result = muse.explain({"why_paused": why}, muse.glimmer_route({}), env={})
                self.assertEqual(result["text"], why + " Calls and messages are never paused.")
                self.assertFalse(result["ok"])
                self.assertEqual(result["engine"], "rules")


if __name__ == "__main__":
    unittest.main()
