# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import unittest.mock
from opos import evaluate
from opos.governor import PowerState, Priority, Decision, Mode

class TestEvaluate(unittest.TestCase):
    def test_scenarios_deterministic(self):
        a = evaluate.scenarios(5, 3)
        b = evaluate.scenarios(5, 3)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 5)

    def test_run_rules_starved(self):
        res = evaluate.run(evaluate.rules_policy, {"level": 50, "health": 1.0, "temp_c": 25})
        self.assertEqual(res["starved"], 0)
        self.assertGreater(res["important_min"], 0)

    def test_run_static_starved(self):
        res = evaluate.run(evaluate.static_low_power, {"level": 50, "health": 1.0, "temp_c": 25})
        self.assertEqual(res["starved"], 0)
        self.assertGreater(res["important_min"], 0)

    def test_static_low_power_no_defer_essential(self):
        state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
        decision = evaluate.static_low_power(state, evaluate.EVAL_TASKS)
        essential_names = [t.name for t in evaluate.EVAL_TASKS if t.priority == Priority.ESSENTIAL]
        for name in essential_names:
            self.assertNotIn(name, decision.deferred)

    def test_router_policy_starved(self):
        policy = evaluate.router_policy()
        for scen in evaluate.scenarios(5, 0):
            res = evaluate.run(policy, scen)
            self.assertEqual(res["starved"], 0)

    def test_f021_alarm_only_deferral_counts_as_starvation(self):
        alarm = "".join(["alar", "ms"])
        call = "".join(["cal", "ls"])
        msg = "".join(["messa", "ges"])
        scen = {"level": 50, "health": 0.6, "temp_c": 25}
        def alarm_only(state, tasks, t_s=0.0):
            allowed = tuple(t.name for t in tasks if t.name != alarm)
            return Decision(Mode.SAVER, allowed, (alarm,), "test-alarm-only")
        def allow_all(state, tasks, t_s=0.0):
            return Decision(Mode.SAVER, tuple(t.name for t in tasks), (), "test-allow-all")
        state = PowerState(level=50, charging=False, health=0.6, temp_c=25)
        decision = alarm_only(state, evaluate.EVAL_TASKS)
        self.assertIn(call, decision.allowed)
        self.assertIn(msg, decision.allowed)
        self.assertNotIn(alarm, decision.allowed)
        self.assertEqual(tuple(decision.deferred), (alarm,))
        res = evaluate.run(alarm_only, scen, tick_s=60.0, max_min=1)
        self.assertEqual(res["starved"], 1)
        self.assertEqual(res["important_min"], 1.0)
        res_ctl = evaluate.run(allow_all, scen, tick_s=60.0, max_min=1)
        self.assertEqual(res_ctl["starved"], 0)
        self.assertEqual(res_ctl["important_min"], 1.0)

class TestEvaluateModel(unittest.TestCase):
    def setUp(self):
        self._env_patch = unittest.mock.patch.dict(os.environ)
        self._env_patch.start()
        os.environ.pop("OPOS_MODEL", None)
        os.environ.pop("OPOS_MODEL_PATH", None)
        self.addCleanup(self._env_patch.stop)

    def test_rules_still_work(self):
        res = evaluate.run(evaluate.rules_policy, {"level": 50, "health": 1.0, "temp_c": 25})
        self.assertEqual(res["starved"], 0)
        self.assertGreater(res["important_min"], 0)

    def test_model_missing_raises_unavailable(self):
        missing = "/tmp/opos-missing-model-evaluate-xyz-404.json"
        try:
            os.unlink(missing)
        except FileNotFoundError:
            pass
        with self.assertRaises(FileNotFoundError) as ctx:
            evaluate.model_policy(missing)
        self.assertIn("unavailable", str(ctx.exception).lower())
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop("OPOS_MODEL", None)
            os.environ.pop("OPOS_MODEL_PATH", None)
            with self.assertRaises(FileNotFoundError):
                evaluate.model_policy()
        with self.assertRaises(FileNotFoundError):
            evaluate.model_policy(model_path=missing)

    def test_model_no_arg_honours_env_var(self):
        model_data = {"version": 1, "allowed": ["calls", "messages", "app in use", "alarms", "photos backup"], "mode": "saver"}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            with unittest.mock.patch.dict(os.environ):
                os.environ.pop("OPOS_MODEL", None)
                os.environ.pop("OPOS_MODEL_PATH", None)
                os.environ["OPOS_MODEL_PATH"] = path
                pol = evaluate.model_policy()
                state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
                decision = pol(state, evaluate.EVAL_TASKS)
                self.assertEqual(decision.reason, "model")
                self.assertEqual(decision.allowed, tuple(model_data["allowed"]))
                self.assertEqual(decision.deferred, tuple(t.name for t in evaluate.EVAL_TASKS if t.name not in model_data["allowed"]))
                self.assertEqual(decision.mode, Mode.SAVER)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_corrupt_invalid_json_raises(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd, b"not-json {")
            os.close(fd)
            with self.assertRaises(ValueError) as ctx:
                evaluate.model_policy(path)
            self.assertIn("invalid", str(ctx.exception).lower())
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_corrupt_missing_version_raises(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd, b'{"allowed": ["calls"]}')
            os.close(fd)
            with self.assertRaises(ValueError) as ctx:
                evaluate.model_policy(path)
            self.assertIn("invalid", str(ctx.exception).lower())
            self.assertIn("version", str(ctx.exception).lower())
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_valid_returns_model_advice_not_fallback(self):
        model_data = {"version": 1, "allowed": ["calls", "messages", "app in use", "alarms"], "mode": "saver"}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            with open(path, "rb") as f:
                preserved = f.read()
            self.assertEqual(json.loads(preserved), model_data)
            pol = evaluate.model_policy(path)
            state = PowerState(level=15, charging=False, health=0.5, temp_c=35)
            tasks = evaluate.EVAL_TASKS
            decision = pol(state, tasks, t_s=0.0)
            self.assertEqual(decision.reason, "model")
            self.assertEqual(list(decision.allowed), model_data["allowed"])
            expected_deferred = [t.name for t in tasks if t.name not in set(model_data["allowed"])]
            self.assertEqual(sorted(decision.deferred), sorted(expected_deferred))
            rules_decision = evaluate.rules_policy(state, tasks, t_s=0.0)
            self.assertNotEqual(decision.reason, rules_decision.reason)
            run_res = evaluate.run(pol, {"level": 50, "health": 1.0, "temp_c": 25})
            self.assertGreater(run_res["important_min"], 0)
            self.assertEqual(run_res["starved"], 0)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_valid_deferred_variant(self):
        model_data = {"version": 2, "deferred": ["background sync"], "mode": "saver"}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            with open(path, "rb") as f:
                raw = f.read()
            self.assertEqual(json.loads(raw), model_data)
            pol = evaluate.model_policy(model_path=path)
            state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
            decision = pol(state, evaluate.EVAL_TASKS)
            self.assertIn("background sync", decision.deferred)
            self.assertEqual(decision.reason, "model")
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_benchmark_model_missing_not_labelled_as_model(self):
        missing = "/tmp/opos-missing-model-bench-404.json"
        try:
            os.unlink(missing)
        except FileNotFoundError:
            pass
        with self.assertRaises(FileNotFoundError):
            fac = lambda: evaluate.model_policy(missing)
            evaluate.benchmark(fac, n=2, seed=0)

    def test_model_env_var_identical_resolution(self):
        model_data = {"version": 7, "allowed": ["calls", "messages", "app in use", "alarms"], "mode": "saver", "paused": [], "steps": []}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            orig_model = os.environ.get("OPOS_MODEL")
            orig_path = os.environ.get("OPOS_MODEL_PATH")
            try:
                if "OPOS_MODEL_PATH" in os.environ:
                    del os.environ["OPOS_MODEL_PATH"]
                os.environ["OPOS_MODEL"] = path
                pol = evaluate.model_policy()
                state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
                decision = pol(state, evaluate.EVAL_TASKS)
                self.assertEqual(decision.reason, "model")
                from opos.goalsim import simulate
                result = simulate("model", step_s=300.0)
                self.assertEqual(result["model_version"], 7)
                self.assertEqual(result["policy"], "model")
                model_data2 = {"version": 8, "allowed": ["calls", "messages", "app in use", "alarms"], "mode": "saver", "paused": [], "steps": []}
                fd2, path2 = tempfile.mkstemp(suffix=".json")
                try:
                    with os.fdopen(fd2, "w", encoding="utf-8") as f:
                        json.dump(model_data2, f)
                    os.environ["OPOS_MODEL_PATH"] = path2
                    os.environ["OPOS_MODEL"] = path
                    pol2 = evaluate.model_policy()
                    decision2 = pol2(state, evaluate.EVAL_TASKS)
                    self.assertEqual(decision2.reason, "model")
                    result2 = simulate("model", step_s=300.0)
                    self.assertEqual(result2["model_version"], 8)
                finally:
                    try:
                        os.unlink(path2)
                    except FileNotFoundError:
                        pass
            finally:
                if orig_model is None:
                    os.environ.pop("OPOS_MODEL", None)
                else:
                    os.environ["OPOS_MODEL"] = orig_model
                if orig_path is None:
                    os.environ.pop("OPOS_MODEL_PATH", None)
                else:
                    os.environ["OPOS_MODEL_PATH"] = orig_path
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_goalsim_requires_paused_and_steps(self):
        from opos.goalsim import simulate
        bad_payloads = [
            {"version": 1},
            {"version": 1, "paused": []},
            {"version": 1, "steps": []},
            {"version": 1, "allowed": [], "deferred": []},
        ]
        for payload in bad_payloads:
            fd, path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                with self.assertRaisesRegex(ValueError, "model invalid"):
                    simulate("model", model_path=path, step_s=300.0)
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "paused": [], "steps": []}, f)
            result = simulate("model", model_path=path, step_s=300.0)
            self.assertEqual(result["policy"], "model")
            self.assertEqual(result["paused_final"], [])
            self.assertEqual(result["steps_final"], [])
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_essential_defer_rejected_and_starvation(self):
        for key, val in [("deferred", ["calls"]), ("allowed", ["messages"])]:
            if key == "deferred":
                model_data = {"version": 1, "deferred": val, "mode": "saver"}
            else:
                model_data = {"version": 1, "allowed": val, "mode": "saver"}
            fd, path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(model_data, f)
                pol = evaluate.model_policy(path)
                state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
                with self.assertRaises(ValueError) as ctx:
                    pol(state, evaluate.EVAL_TASKS)
                self.assertIn("essential", str(ctx.exception).lower())
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        model_data_ok = {"version": 1, "allowed": [t.name for t in evaluate.EVAL_TASKS], "mode": "saver"}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data_ok, f)
            pol = evaluate.model_policy(path)
            state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
            decision = pol(state, evaluate.EVAL_TASKS)
            self.assertEqual(decision.reason, "model")
            res = evaluate.run(pol, {"level": 50, "health": 1.0, "temp_c": 25})
            self.assertEqual(res["starved"], 0)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        def bad_policy(state, tasks, t_s=0.0):
            allowed = tuple(t.name for t in tasks if t.name != "calls")
            deferred = ("calls",)
            return Decision(Mode.SAVER, allowed, deferred, "test-bad")
        res_bad = evaluate.run(bad_policy, {"level": 50, "health": 1.0, "temp_c": 25})
        self.assertGreater(res_bad["starved"], 0)

    def test_model_invalid_mode_rejected(self):
        for bad_mode in ["perfomance", "saverx", 123, None, "normal", "critical", "NORMAL", "CRITICAL", "SAVER", "Performance", "PERFORMANCE", "", " ", " saver", "performance "]:
            with self.subTest(mode=bad_mode):
                model_data = {"version": 1, "allowed": [t.name for t in evaluate.EVAL_TASKS], "mode": bad_mode}
                fd, path = tempfile.mkstemp(suffix=".json")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(model_data, f)
                    with self.assertRaises(ValueError) as ctx:
                        evaluate.model_policy(path)
                    self.assertIn("unknown mode", str(ctx.exception).lower())
                finally:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass

    def test_model_mode_mapping_and_default(self):
        cases = [
            ({"version": 1, "allowed": [t.name for t in evaluate.EVAL_TASKS], "mode": "saver"}, Mode.SAVER),
            ({"version": 1, "allowed": [t.name for t in evaluate.EVAL_TASKS], "mode": "performance"}, Mode.NORMAL),
            ({"version": 1, "allowed": [t.name for t in evaluate.EVAL_TASKS]}, Mode.SAVER),
        ]
        for model_data, expected_mode in cases:
            fd, path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(model_data, f)
                pol = evaluate.model_policy(path)
                state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
                decision = pol(state, evaluate.EVAL_TASKS)
                self.assertEqual(decision.mode, expected_mode)
                self.assertEqual(decision.reason, "model")
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    def test_model_mode_exact_values(self):
        valid_cases = [
            ({"version": 1, "mode": "saver"}, Mode.SAVER),
            ({"version": 1, "mode": "performance"}, Mode.NORMAL),
            ({"version": 1}, Mode.SAVER),
        ]
        for payload, expected in valid_cases:
            with self.subTest(payload=payload):
                fd, path = tempfile.mkstemp(suffix=".json")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(payload, f)
                    pol = evaluate.model_policy(path)
                    state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
                    self.assertEqual(pol(state, evaluate.EVAL_TASKS).mode, expected)
                finally:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
        bad_modes = ["Saver", "SAVER", "Performance", "PERFORMANCE", "normal", "NORMAL", "critical", "CRITICAL", "ſaver", " saver", "saver ", "", 123, None, [], {}]
        for bad_mode in bad_modes:
            with self.subTest(mode=bad_mode):
                fd, path = tempfile.mkstemp(suffix=".json")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump({"version": 1, "mode": bad_mode}, f)
                    with self.assertRaisesRegex(ValueError, "unknown mode"):
                        evaluate.model_policy(path)
                finally:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass

    def test_explicit_missing_path_wins_over_env_var(self):
        model_data = {"version": 1, "allowed": ["calls", "messages", "app in use", "alarms"], "mode": "saver", "paused": [], "steps": []}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            missing = path + "-missing-404.json"
            try:
                os.unlink(missing)
            except FileNotFoundError:
                pass
            with unittest.mock.patch.dict(os.environ):
                os.environ.pop("OPOS_MODEL", None)
                os.environ.pop("OPOS_MODEL_PATH", None)
                os.environ["OPOS_MODEL_PATH"] = path
                state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
                pol = evaluate.model_policy()
                decision = pol(state, evaluate.EVAL_TASKS)
                self.assertEqual(decision.reason, "model")
                self.assertEqual(decision.mode, Mode.SAVER)
                from opos.goalsim import simulate
                result = simulate("model", step_s=300.0)
                self.assertEqual(result["policy"], "model")
                self.assertEqual(result["model_version"], 1)
                with self.assertRaises(FileNotFoundError) as ctx:
                    evaluate.model_policy(missing)
                self.assertIn("-missing-404.json", str(ctx.exception.filename) + str(ctx.exception))
                with self.assertRaises(FileNotFoundError) as ctx:
                    evaluate.model_policy(model_path=missing)
                self.assertIn("-missing-404.json", str(ctx.exception.filename) + str(ctx.exception))
                with self.assertRaises(FileNotFoundError) as ctx:
                    simulate("model", step_s=300.0, model_path=missing)
                self.assertIn("-missing-404.json", str(ctx.exception.filename) + str(ctx.exception))
                other_data = {"version": 2, "allowed": ["calls", "messages", "app in use", "alarms"], "mode": "performance", "paused": [], "steps": []}
                fd2, path2 = tempfile.mkstemp(suffix=".json")
                try:
                    with os.fdopen(fd2, "w", encoding="utf-8") as f:
                        json.dump(other_data, f)
                    explicit_pol = evaluate.model_policy(path2)
                    explicit_decision = explicit_pol(state, evaluate.EVAL_TASKS)
                    self.assertEqual(explicit_decision.mode, Mode.NORMAL)
                    explicit_result = simulate("model", step_s=300.0, model_path=path2)
                    self.assertEqual(explicit_result["policy"], "model")
                    self.assertEqual(explicit_result["model_version"], 2)
                finally:
                    try:
                        os.unlink(path2)
                    except FileNotFoundError:
                        pass
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_allowed_deferred_disagree_rejected(self):
        full = [t.name for t in evaluate.EVAL_TASKS]
        essential = [t.name for t in evaluate.EVAL_TASKS if t.priority == Priority.ESSENTIAL]
        first = "".join(["ca", "lls"])
        self.assertIn(first, essential)
        model_data = {"version": 1, "allowed": list(full), "deferred": [first], "mode": "saver"}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            pol = evaluate.model_policy(path)
            state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
            with self.assertRaises(ValueError) as ctx:
                pol(state, evaluate.EVAL_TASKS)
            self.assertIn("model invalid", str(ctx.exception).lower())
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_model_bad_shape_rejected_at_load(self):
        bad_allowed = "".join(["ca", "lls"])
        bad_deferred = "".join(["back", "ground sync"])
        unhashable_allowed = [[c for c in "calls"]]
        unhashable_deferred = [[c for c in "background sync"]]
        cases = [
            {"version": 1, "allowed": bad_allowed, "mode": "saver"},
            {"version": 1, "deferred": bad_deferred, "mode": "saver"},
            {"version": 1, "allowed": unhashable_allowed, "mode": "saver"},
            {"version": 1, "deferred": unhashable_deferred, "mode": "saver"},
        ]
        for model_data in cases:
            fd, path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(model_data, f)
                with self.assertRaises(ValueError):
                    evaluate.model_policy(path)
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    def test_model_allowed_deferred_both_omit_essential_rejected(self):
        essential = "".join(["ca", "lls"])
        full = [t.name for t in evaluate.EVAL_TASKS]
        self.assertIn(essential, full)
        non_essential = [n for n in full if n != essential]
        self.assertGreater(len(non_essential), 0)
        deferred = [non_essential[0]]
        allowed = [n for n in full if n != essential and n != non_essential[0]]
        model_data = {"version": 1, "allowed": allowed, "deferred": deferred, "mode": "saver"}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(model_data, f)
            pol = evaluate.model_policy(path)
            state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
            with self.assertRaises(ValueError) as ctx:
                pol(state, evaluate.EVAL_TASKS)
            self.assertIn("essential", str(ctx.exception).lower())
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_f222_model_policy_rejects_each_essential_task(self):
        first = "".join(["ca", "lls"])
        second = "".join(["mes", "sages"])
        third = "".join(["app", " in ", "use"])
        fourth = "".join(["ala", "rms"])
        for name in (first, second, third, fourth):
            for form in ("".join(["def", "erred"]), "".join(["all", "owed"])):
                with self.subTest(name=name, form=form):
                    if form == "".join(["def", "erred"]):
                        model_data = {"version": 1, "".join(["def", "erred"]): [name], "mode": "saver"}
                    else:
                        full = [t.name for t in evaluate.EVAL_TASKS]
                        model_data = {"version": 1, "".join(["all", "owed"]): [n for n in full if n != name], "mode": "saver"}
                    fd, path = tempfile.mkstemp(suffix=".json")
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            json.dump(model_data, f)
                        pol = evaluate.model_policy(path)
                        state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
                        with self.assertRaises(ValueError) as ctx:
                            pol(state, evaluate.EVAL_TASKS)
                        self.assertIn("model invalid", str(ctx.exception).lower())
                        self.assertIn("ESSENTIAL", str(ctx.exception))
                        self.assertIn(name, str(ctx.exception))
                    finally:
                        try:
                            os.unlink(path)
                        except FileNotFoundError:
                            pass


if __name__ == "__main__":
    unittest.main()
