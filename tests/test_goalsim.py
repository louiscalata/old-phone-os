# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import unittest.mock
from opos.goalsim import KNOWN_STEPS, simulate
from opos.runtime import DEFAULT_TASKS, Priority, Task


class TestGoalSim(unittest.TestCase):
    def test_router_hours_ge_basic_hours(self):
        basic = simulate("basic", step_s=300.0)
        router = simulate("router", step_s=300.0)
        self.assertGreaterEqual(router["hours"], basic["hours"])

    def test_deterministic(self):
        first_basic = simulate("basic", step_s=300.0)
        second_basic = simulate("basic", step_s=300.0)
        self.assertEqual(first_basic, second_basic)
        first_router = simulate("router", step_s=300.0)
        second_router = simulate("router", step_s=300.0)
        self.assertEqual(first_router, second_router)

    def test_unknown_policy_raises(self):
        with self.assertRaises(ValueError):
            simulate("turbo", step_s=300.0)

    def test_short_goal_reached(self):
        result = simulate("router", step_s=300.0, goal_h=1.0)
        self.assertTrue(result["reaches_goal"])

    def test_essentials_never_paused(self):
        for policy in ("basic", "router"):
            result = simulate(policy, step_s=300.0)
            self.assertNotIn("calls", result["paused_final"])
            self.assertNotIn("messages", result["paused_final"])

    def test_steps_bounded(self):
        step_s = 300.0
        max_h = 48.0
        for policy in ("basic", "router"):
            result = simulate(policy, step_s=300.0, max_h=max_h)
            self.assertLessEqual(result["hours"], max_h + step_s / 3600.0)
            self.assertLessEqual(result["hours"] * 3600.0 / step_s, max_h * 3600.0 / step_s + 1)

    def test_bad_step_s_raises(self):
        for policy in ("basic", "router"):
            for bad in (0, -1, float("nan"), float("inf")):
                with self.subTest(policy=policy, step_s=bad):
                    with self.assertRaises(ValueError):
                        simulate(policy, step_s=bad)

    def test_tasks_generator_matches_list(self):
        task_list = list(DEFAULT_TASKS) + [Task("alarms", Priority.USEFUL, 0.1)]
        for policy in ("basic", "router"):
            from_list = simulate(policy, step_s=300.0, tasks=task_list)
            from_gen = simulate(policy, step_s=300.0, tasks=(t for t in task_list))
            self.assertEqual(from_gen, from_list)

    def test_custom_tasks_router_pauses_only_supplied(self):
        custom = [
            Task("calls", Priority.ESSENTIAL, 0.1),
            Task("messages", Priority.ESSENTIAL, 0.1),
            Task("expensive-opt", Priority.OPTIONAL, 50.0),
        ]
        allowed = {t.name for t in custom}
        result = simulate("router", step_s=300.0, tasks=custom)
        for name in result["paused_final"]:
            self.assertIn(name, allowed)

    def test_router_reaches_overnight_goal(self):
        result = simulate("router", level=80.0, health=1.0, goal_h=11.0, step_s=300.0)
        self.assertTrue(result["reaches_goal"])
        self.assertGreaterEqual(result["hours"], 11.0)
        self.assertLessEqual(result["hours"], 13.0)

    def test_router_never_pauses_alarms(self):
        result = simulate("router", level=80.0, health=1.0, goal_h=11.0, step_s=300.0)
        self.assertNotIn("alarms", result["paused_final"])

    def test_router_steps_final_has_four(self):
        result = simulate("router", level=80.0, health=1.0, goal_h=11.0, step_s=300.0)
        self.assertEqual(len(result["steps_final"]), 4)

    def test_router_close_to_plan(self):
        from opos import power
        tasks = tuple(list(DEFAULT_TASKS) + [Task("alarms", Priority.USEFUL, 0.1)])
        result = simulate("router", level=40.0, health=0.6, goal_h=11.0, step_s=300.0, tasks=tasks)
        expected = power.plan(40, 11, tasks, 0.6).hours_left
        self.assertLessEqual(abs(result["hours"] - expected), 0.15)
        self.assertGreaterEqual(result["hours"], 3.7)

    def test_horizon_caps_interval_and_depletion(self):
        import opos.goalsim as goalsim
        cases = ((1.0, 0.0, 60.0), (2.0, 0.5, 90.0), (10.0, 8.5, 90.0))
        with unittest.mock.patch.object(goalsim, "total_drain", return_value=60.0):
            for initial, exp_level, exp_t in cases:
                with self.subTest(level=initial):
                    level, t_s, _, _, _ = goalsim._drain_loop((), 1.0, 30.0, initial, 0.0, 60.0, 0.025, None, None, set(), ())
                    self.assertAlmostEqual(level, exp_level)
                    self.assertAlmostEqual(t_s, exp_t)
            result = simulate("basic", level=2.0, health=1.0, tasks=(), step_s=60.0, max_h=0.025, goal_h=0.03)
            self.assertAlmostEqual(result["hours"], 0.025)
            self.assertFalse(result["reaches_goal"])


class TestGoalSimModel(unittest.TestCase):
    def test_rules_still_work(self):
        r = simulate("basic", step_s=300.0)
        self.assertIn("hours", r)
        self.assertEqual(r["policy"], "basic")
        r2 = simulate("router", step_s=300.0)
        self.assertEqual(r2["policy"], "router")

    def test_model_missing_raises_unavailable(self):
        missing = "/tmp/opos-missing-model-goalsim-xyz-404.json"
        try:
            os.unlink(missing)
        except FileNotFoundError:
            pass
        with self.assertRaises(FileNotFoundError) as ctx:
            simulate("model", step_s=300.0, model_path=missing)
        self.assertIn("unavailable", str(ctx.exception).lower())
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop("OPOS_MODEL", None)
            os.environ.pop("OPOS_MODEL_PATH", None)
            with self.assertRaises(FileNotFoundError):
                simulate("model", step_s=300.0)

    def test_model_corrupt_invalid_json_raises(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd, b"{ invalid json }")
            os.close(fd)
            with self.assertRaises(ValueError) as ctx:
                simulate("model", step_s=300.0, model_path=path)
            self.assertIn("invalid", str(ctx.exception).lower())
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_corrupt_missing_version_raises(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd, b'{"paused": []}')
            os.close(fd)
            with self.assertRaises(ValueError) as ctx:
                simulate("model", step_s=300.0, model_path=path)
            self.assertIn("invalid", str(ctx.exception).lower())
            self.assertIn("version", str(ctx.exception).lower())
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_f230_model_load_errors_propagate_without_rules_fallback(self):
        import opos.goalsim as goalsim

        cases = (
            FileNotFoundError("model unavailable: missing file"),
            ValueError("model invalid: corrupt JSON"),
        )
        for exc in cases:
            with self.subTest(exc=exc):
                with (
                    unittest.mock.patch.object(goalsim, "_load_model", side_effect=exc) as load_mock,
                    unittest.mock.patch.object(goalsim, "static_low_power") as static_mock,
                    unittest.mock.patch.object(goalsim, "Runtime") as runtime_mock,
                    unittest.mock.patch.object(goalsim, "_drain_loop") as loop_mock,
                ):
                    with self.assertRaises(type(exc)) as ctx:
                        simulate("model", model_path="f230-model.json", step_s=300.0)
                    self.assertIs(ctx.exception, exc)
                    load_mock.assert_called_once_with("f230-model.json")
                    static_mock.assert_not_called()
                    runtime_mock.assert_not_called()
                    loop_mock.assert_not_called()

    def test_model_valid_uses_advice_not_fallback(self):
        custom = [
            Task("calls", Priority.ESSENTIAL, 0.1),
            Task("messages", Priority.ESSENTIAL, 0.1),
            Task("expensive-opt", Priority.OPTIONAL, 50.0),
        ]
        model_data = {"version": 1, "paused": ["expensive-opt"], "steps": ["dim screen"]}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
                f.flush()
            with open(path, "rb") as f:
                preserved = f.read()
            self.assertEqual(json.loads(preserved), model_data)
            result = simulate("model", step_s=300.0, tasks=custom, model_path=path)
            self.assertEqual(result["policy"], "model")
            self.assertEqual(result["paused_final"], ["expensive-opt"])
            self.assertEqual(result["steps_final"], ["dim screen"])
            self.assertEqual(result["model_version"], 1)
            basic = simulate("basic", step_s=300.0, tasks=custom)
            router = simulate("router", step_s=300.0, tasks=custom)
            self.assertEqual(result["paused_final"], basic["paused_final"])
            self.assertLess(result["hours"], basic["hours"])
            self.assertNotEqual(result["steps_final"], router["steps_final"])
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_valid_preserved_bytes_observable(self):
        model_data = {"version": 2, "paused": [], "steps": ["dim screen", "batch sync"]}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            raw = json.dumps(model_data, sort_keys=True).encode("utf-8")
            os.write(fd, raw)
            os.close(fd)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), raw)
            result = simulate("model", step_s=300.0, model_path=path)
            self.assertEqual(result["steps_final"], ["dim screen", "batch sync"])
            self.assertEqual(result["paused_final"], [])
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_essential_pause_rejected(self):
        custom = [
            Task("calls", Priority.ESSENTIAL, 0.1),
            Task("messages", Priority.ESSENTIAL, 0.1),
            Task("custom-essential", Priority.ESSENTIAL, 0.5),
            Task("optional-work", Priority.OPTIONAL, 1.0),
        ]
        for essential_name in ["calls", "custom-essential"]:
            model_data = {"version": 1, "paused": [essential_name], "steps": ["dim screen"]}
            fd, path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(model_data, f)
                with self.assertRaises(ValueError) as ctx:
                    simulate("model", step_s=300.0, tasks=custom, model_path=path)
                self.assertIn("invalid", str(ctx.exception).lower())
                self.assertIn("essential", str(ctx.exception).lower())
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        model_data_ok = {"version": 1, "paused": ["optional-work"], "steps": ["dim screen"]}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data_ok, f)
            result = simulate("model", step_s=300.0, tasks=custom, model_path=path)
            self.assertEqual(result["paused_final"], ["optional-work"])
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_default_alarms_essential(self):
        model_data = {"version": 1, "paused": ["alarms"], "steps": ["dim screen"]}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            with self.assertRaises(ValueError):
                simulate("model", step_s=300.0, model_path=path)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_f226_model_loads_from_opos_model_only(self):
        model_data = {"version": 226, "paused": [], "steps": ["dim screen"]}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            with unittest.mock.patch.dict(os.environ):
                os.environ.pop("OPOS_MODEL_PATH", None)
                os.environ["OPOS_MODEL"] = path
                result = simulate("model", step_s=300.0, max_h=0.1)
            self.assertEqual(result["policy"], "model")
            self.assertEqual(result["model_version"], 226)
            self.assertEqual(result["paused_final"], [])
            self.assertEqual(result["steps_final"], ["dim screen"])
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


class TestGoalSimModelSteps(unittest.TestCase):
    def test_model_unknown_step_rejected(self):
        bad_single = "model-step"
        bad_a = "a"
        bad_b = "b"
        for bad_steps in ([bad_single], [bad_a, bad_b], ["dim screen", bad_single], ["dim screen", bad_single, "batch sync"]):
            model_data = {"version": 1, "paused": [], "steps": bad_steps}
            fd, path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(model_data, f)
                with self.assertRaises(ValueError) as ctx:
                    simulate("model", step_s=300.0, model_path=path)
                self.assertIn("model invalid", str(ctx.exception).lower())
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


class TestGoalSimKnownSteps(unittest.TestCase):
    def test_model_accepts_every_known_step(self):
        for step in KNOWN_STEPS:
            model_data = {"version": 1, "paused": [], "steps": [step]}
            fd, path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(model_data, f)
                result = simulate("model", step_s=300.0, model_path=path)
                self.assertEqual(result["steps_final"], [step])
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    def test_known_steps_is_single_shared_tuple(self):
        import opos.goalsim as goalsim

        self.assertEqual(tuple(KNOWN_STEPS), ("dim screen", "batch sync", "model"))
        self.assertIs(goalsim.KNOWN_STEPS, KNOWN_STEPS)
        model_data = {"version": 1, "paused": [], "steps": list(KNOWN_STEPS)}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            result = simulate("model", step_s=300.0, model_path=path)
            self.assertEqual(result["steps_final"], list(KNOWN_STEPS))
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


class TestGoalSimModelEol(unittest.TestCase):
    def _write_model(self, model_data):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            return path
        except BaseException:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            raise

    def test_model_eol_uses_shared_loop(self):
        import opos.goalsim as goalsim

        self.assertTrue(hasattr(goalsim, "_drain_loop"))
        model_data = {"version": 1, "paused": [], "steps": ["dim screen", "batch sync"]}
        path = self._write_model(model_data)
        try:
            result = simulate("model", step_s=300.0, profile="eol", model_path=path)
            self.assertEqual(result["policy"], "model")
            self.assertEqual(result["profile"], "eol")
            self.assertEqual(result["steps_final"], ["dim screen", "batch sync"])
            self.assertLessEqual(result["hours"], 48.0 + 300.0 / 3600.0)
            self.assertTrue(result["shutdown_level"] is None or isinstance(result["shutdown_level"], float))
            again = simulate("model", step_s=300.0, profile="eol", model_path=path)
            self.assertEqual(result, again)
            with unittest.mock.patch.object(goalsim, "_drain_loop", wraps=goalsim._drain_loop) as spy:
                simulate("model", step_s=300.0, profile="eol", model_path=path)
                self.assertTrue(spy.called)
            with unittest.mock.patch.object(goalsim, "_drain_loop", wraps=goalsim._drain_loop) as spy:
                simulate("basic", step_s=300.0, profile="eol")
                simulate("router", step_s=300.0, profile="eol")
                self.assertEqual(spy.call_count, 2)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


class TestGoalSimRejectsSurplusArgs(unittest.TestCase):
    def test_rejects_misspelled_and_surplus(self):
        for policy in ("basic", "router", "model"):
            with self.subTest(policy=policy):
                with self.assertRaises(TypeError):
                    simulate(policy, goal_hours=11.0)
                with self.assertRaises(TypeError):
                    simulate(policy, profle="eol")
                with self.assertRaises(TypeError):
                    simulate(policy, 40.0, 0.6, 30.0, 11.0, None, 60.0, 48.0, None, None, "extra")
                with self.assertRaises(TypeError):
                    simulate(policy, model_path=None, unexpected="x")


class TestGoalSimEolBrownout(unittest.TestCase):
    def test_eol_brownout_after_draining(self):
        import opos.goalsim as goalsim
        cutoff = 30.0
        with unittest.mock.patch.object(goalsim, "total_drain", return_value=12.0):
            with unittest.mock.patch.object(
                goalsim, "brownout", side_effect=lambda level, *args, **kwargs: level <= cutoff
            ):
                eol = simulate("basic", level=cutoff + 2.0, health=1.0, step_s=300.0, max_h=1.0, profile="eol")
                healthy = simulate("basic", level=cutoff + 2.0, health=1.0, step_s=300.0, max_h=1.0, profile=None)
        self.assertGreater(eol["hours"], 0.0)
        self.assertLess(eol["hours"], 1.0)
        self.assertIsNotNone(eol["shutdown_level"])
        self.assertGreater(eol["shutdown_level"], cutoff - 1.0)
        self.assertLessEqual(eol["shutdown_level"], cutoff)
        self.assertEqual(healthy["hours"], 1.0)
        self.assertIsNone(healthy.get("shutdown_level"))

    def test_eol_low_battery_shuts_down(self):
        from opos.eol import CUTOFF_V
        level = CUTOFF_V
        first = simulate("basic", level=level, health=1.0, step_s=300.0, profile="eol")
        second = simulate("basic", level=level, health=1.0, step_s=300.0, profile="eol")
        self.assertEqual(first, second)
        self.assertEqual(first["hours"], 0.0)
        self.assertFalse(first["reaches_goal"])
        self.assertIsNotNone(first["shutdown_level"])
        self.assertGreaterEqual(first["shutdown_level"], 0.0)
        self.assertLessEqual(first["shutdown_level"], level)
        healthy = simulate("basic", level=level, health=1.0, step_s=300.0)
        self.assertGreater(healthy["hours"], first["hours"])
        self.assertIsNone(healthy.get("shutdown_level"))


class TestGoalSimFloorOverrides(unittest.TestCase):
    def test_basic_and_router_record_stripped_essential(self):
        import opos.goalsim as goalsim
        custom = [
            Task("calls", Priority.ESSENTIAL, 0.1),
            Task("messages", Priority.ESSENTIAL, 0.1),
            Task("expensive-opt", Priority.OPTIONAL, 50.0),
        ]
        with unittest.mock.patch.object(goalsim, "static_low_power") as mocked:
            mocked.return_value.paused = ["expensive-opt", "calls"]
            basic = simulate("basic", step_s=300.0, tasks=custom)
        self.assertNotIn("calls", basic["paused_final"])
        self.assertEqual(basic["floor_overrides"], ["calls"])
        decision = type("Decision", (), {"deferred": ["calls"]})()
        tick = type("Tick", (), {"decision": decision, "steps": ()})()

        class _FakeRuntime:
            def __init__(self, tasks=None):
                self._tasks = tasks
            def step(self, t, lvl, health=None, temp_c=None, goal_hours=None):
                return tick

        with unittest.mock.patch.object(goalsim, "Runtime", _FakeRuntime):
            router = simulate("router", step_s=300.0, tasks=custom)
        self.assertNotIn("calls", router["paused_final"])
        self.assertEqual(router["floor_overrides"], ["calls"])


if __name__ == "__main__":
    unittest.main()
