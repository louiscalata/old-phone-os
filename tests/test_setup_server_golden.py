# SPDX-License-Identifier: GPL-3.0-or-later
"""Full HTTP outputs and ordered effects recorded from the original setup factory."""

import ast
import base64
import copy
import inspect
import types
import unittest
from unittest import mock

from opos import notices
from opos.setup import server
from tests.fixtures.setup_server_golden.check import compile_factory, observe
from tests.fixtures.setup_server_golden.support import Driver, corpus_cases, run_case


class SetupServerGoldenTests(unittest.TestCase):
    """Keep request routing, refusal precedence and persistence observable."""

    # Page bytes are recorded from the original source. Legitimate page changes
    # require regeneration from the intended baseline and an explanation here.
    # The portability repair keeps the real page bytes unchanged; only notice
    # version/build metadata is pinned to the values of the recorded baseline.


class SetupServerEnvironmentTests(unittest.TestCase):
    """Release metadata and stdlib templates do not define factory behavior."""

    def test_driver_isolates_release_metadata(self):
        case = next(case for case in corpus_cases("http") if case["name"] == "get_route_105")
        with mock.patch.object(notices, "VERSION", "99.8.7"), mock.patch.object(notices, "BUILD", "abc1234"):
            self.assertEqual(run_case(case), case["expected"])
            self.assertEqual(notices.VERSION, "99.8.7")
            self.assertEqual(notices.BUILD, "abc1234")

    def test_unsupported_methods_ignore_stdlib_error_template(self):
        cases = [case for case in corpus_cases("http") if case["name"] in ("method_TRACE", "method_CONNECT")]
        for template in ("<html>Old runtime error</html>", "<html>New runtime error with style</html>"):
            with mock.patch.object(server.BaseHTTPRequestHandler, "error_message_format", template):
                for case in cases:
                    with self.subTest(template=template, method=case["name"]):
                        self.assertEqual(run_case(case), case["expected"])


class SetupServerDecoderPortabilityTests(unittest.TestCase):
    """Exercise decoder refusal inside the real body reader on every runtime."""

    def setUp(self):
        self.case = next(case for case in corpus_cases("http") if case["name"] == "json_recursion")

    def test_recursion_reaches_real_reader_and_decoder_in_order(self):
        reader = server.secure.read_json_body
        decoder = server.secure.json.loads
        with mock.patch.object(server.secure, "read_json_body", wraps=reader) as real_reader:
            actual = observe(server.make_server, self.case)
        real_reader.assert_called_once()
        self.assertIs(server.secure.json.loads, decoder)
        self.assertEqual(actual, self.case["expected"])
        response = actual["responses"][0]
        self.assertEqual(response["status"], 400)
        self.assertEqual(base64.b64decode(response["body_base64"]), b'{"error": "bad json"}')
        names = [call["call"] for call in actual["calls"]]
        self.assertEqual(names.count("json.loads"), 1)
        ordered = [
            "host_ok",
            "token_ok",
            "origin_ok",
            "json_type_ok",
            "limiter.allow",
            "body.decode",
            "body.read",
            "json.loads",
        ]
        self.assertEqual([name for name in names if name in ordered], ordered)
        call = next(call for call in actual["calls"] if call["call"] == "json.loads")
        self.assertEqual(call["args"], ['{"config": {}}'])
        self.assertEqual(call["kwargs"], {"parse_constant": True})
        self.assertNotIn("validate", names)

    def test_bounded_nested_json_still_succeeds(self):
        case = next(case for case in corpus_cases("http") if case["name"] == "json_nested_success")
        actual = observe(server.make_server, case)
        self.assertEqual(actual, case["expected"])
        self.assertEqual(actual["responses"][0]["status"], 200)
        self.assertEqual(base64.b64decode(actual["responses"][0]["body_base64"]), b'{"ok": true, "errors": []}')
        self.assertNotIn("json.loads", [call["call"] for call in actual["calls"]])

    def test_unknown_path_does_not_reach_decoder(self):
        case = copy.deepcopy(self.case)
        case["requests"][0]["path"] = "/unknown"
        actual = observe(server.make_server, case)
        self.assertEqual(actual["responses"][0]["status"], 404)
        self.assertNotIn("body.decode", [call["call"] for call in actual["calls"]])
        self.assertNotIn("json.loads", [call["call"] for call in actual["calls"]])
        self.assertNotEqual(actual, self.case["expected"])

    def test_refusal_before_body_read_does_not_reach_decoder(self):
        case = copy.deepcopy(self.case)
        case["requests"][0]["headers"] = {"Content-Length": "-1"}
        actual = observe(server.make_server, case)
        self.assertEqual(actual["responses"][0]["status"], 400)
        self.assertNotIn("body.read", [call["call"] for call in actual["calls"]])
        self.assertNotIn("json.loads", [call["call"] for call in actual["calls"]])
        self.assertNotEqual(actual, self.case["expected"])

    def test_missing_recursion_catch_is_detected(self):
        tree = ast.parse(inspect.getsource(server.secure.read_json_body))
        catch = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple)
        )
        self.assertEqual([node.id for node in catch.type.elts], ["ValueError", "RecursionError"])
        catch.type = ast.Name(id="ValueError", ctx=ast.Load())
        code = compile(ast.fix_missing_locations(tree), "<body reader control>", "exec")
        reader_code = next(
            item for item in code.co_consts if isinstance(item, types.CodeType) and item.co_name == "read_json_body"
        )
        reader = types.FunctionType(reader_code, vars(server.secure), argdefs=server.secure.read_json_body.__defaults__)
        with mock.patch.object(server.secure, "read_json_body", reader):
            actual = observe(server.make_server, self.case)
        self.assertEqual(actual["responses"][0]["status"], 500)
        self.assertIn("json.loads", [call["call"] for call in actual["calls"]])
        self.assertNotEqual(actual, self.case["expected"])

    def test_wrong_refusal_status_is_detected(self):
        source = inspect.getsource(server.make_server)
        before = 'self._json(exc.status, {"error": exc.message})'
        self.assertEqual(source.count(before), 1)
        factory = compile_factory(source.replace(before, 'self._json(200, {"error": exc.message})'))
        actual = observe(factory, self.case)
        self.assertEqual(actual["responses"][0]["status"], 200)
        self.assertNotEqual(actual, self.case["expected"])

    def test_wrong_decoder_call_order_is_detected(self):
        record = Driver.record

        def reordered(driver, name, *args, **kwargs):
            record(driver, name, *args, **kwargs)
            if name == "json.loads":
                driver.calls[-2:] = reversed(driver.calls[-2:])

        with mock.patch.object(Driver, "record", reordered):
            actual = observe(server.make_server, self.case)
        self.assertEqual(actual["responses"], self.case["expected"]["responses"])
        self.assertNotEqual(actual, self.case["expected"])


def golden_test(case):
    def test(self):
        self.assertEqual(run_case(case), case["expected"])

    return test


for _case in corpus_cases("http"):
    _name = "test_" + _case["name"].replace(".", "_").replace("-", "_")
    if hasattr(SetupServerGoldenTests, _name):
        raise ValueError(f"Duplicate golden case: {_name}")
    setattr(SetupServerGoldenTests, _name, golden_test(_case))


if __name__ == "__main__":
    unittest.main()
