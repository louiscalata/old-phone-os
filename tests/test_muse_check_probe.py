# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import io
import json
import pathlib
import sys
import unittest
import urllib.error
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from unittest import mock
import opos.muse as muse_mod


class FakeResponse:
    """Fake HTTP response that serves canned bytes."""
    def __init__(self, data):
        self._data = data
    def read(self, n=-1):
        return self._data if n is None or n < 0 else self._data[:n]
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class FakeOpener:
    """Record requests and timeouts, then return a response or raise."""
    def __init__(self, response=None, exc=None):
        self.requests = []
        self.timeouts = []
        self.response = response
        self.exc = exc
    def __call__(self, req, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        if self.exc:
            raise self.exc
        return self.response


def json_opener(obj):
    """Opener that answers with obj as JSON."""
    return FakeOpener(FakeResponse(json.dumps(obj).encode()))


def bad_gateway():
    """HTTP 502 error like urllib raises."""
    return urllib.error.HTTPError("http://127.0.0.1:1234/v1/chat/completions", 502, "Bad Gateway", None, None)


class TestCheckModelServed(unittest.TestCase):
    """check reports whether the exact route model id is served."""
    def test_model_served(self):
        """The route model id in the list means model_served is True."""
        res = muse_mod.check(env={}, opener=json_opener({"data": [{"id": "muse-glimmer-30b"}]}))
        self.assertTrue(res["reachable"])
        self.assertTrue(res["model_served"])
        self.assertEqual(res["served"], ["muse-glimmer-30b"])
    def test_model_not_served(self):
        """Other ids only means reachable but not served."""
        res = muse_mod.check(env={}, opener=json_opener({"data": [{"id": "glimmer-review"}]}))
        self.assertTrue(res["reachable"])
        self.assertFalse(res["model_served"])
    def test_similar_id_is_not_the_model(self):
        """An id that only contains the model name does not count."""
        res = muse_mod.check(env={}, opener=json_opener({"data": [{"id": "meta-models/muse-glimmer-30b-gguf"}]}))
        self.assertFalse(res["model_served"])
    def test_unreachable(self):
        """Unreachable means model_served is False and keys are kept."""
        res = muse_mod.check(env={}, opener=FakeOpener(exc=OSError("down")))
        self.assertFalse(res["reachable"])
        self.assertFalse(res["model_served"])
        self.assertEqual(set(res), {"route", "model", "endpoint", "reachable", "served", "model_served"})
    def test_malformed_port_is_unreachable_not_a_crash(self):
        """A non-numeric port in OPOS_GLIMMER_URL reports unreachable instead of raising."""
        res = muse_mod.check(env={"OPOS_GLIMMER_URL": "http://localhost:abc/v1"})
        self.assertFalse(res["reachable"])
        self.assertFalse(res["model_served"])


class TestProbe(unittest.TestCase):
    """probe sends one short request and never raises."""
    def test_success_with_content(self):
        """A non-empty model reply confirms that the probe was answered."""
        opener = json_opener({"choices": [{"message": {"content": "OK"}}]})
        self.assertEqual(muse_mod.probe(env={}, opener=opener), {"answered": True})
        self.assertEqual(len(opener.requests), 1)
        req = opener.requests[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "http://127.0.0.1:1234/v1/chat/completions")
        self.assertEqual(opener.timeouts, [30])
        body = json.loads(req.data.decode())
        self.assertEqual(body, {"model": "muse-glimmer-30b", "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": muse_mod.DEFAULT_MAX_TOKENS + muse_mod.REASONING_HEADROOM, "reasoning_effort": "low", "temperature": 0})
    def test_http_502(self):
        """An HTTP error reports its status code."""
        res = muse_mod.probe(env={}, opener=FakeOpener(exc=bad_gateway()))
        self.assertEqual(res, {"answered": False, "error": "HTTP 502"})
    def test_os_error(self):
        """A connection failure reports unreachable."""
        res = muse_mod.probe(env={}, opener=FakeOpener(exc=OSError("down")))
        self.assertEqual(res, {"answered": False, "error": "unreachable"})
    def test_timeout(self):
        """A timeout reports unreachable."""
        res = muse_mod.probe(env={}, opener=FakeOpener(exc=TimeoutError("slow")))
        self.assertEqual(res, {"answered": False, "error": "unreachable"})
    def test_bad_json(self):
        """A body that is not JSON reports bad response."""
        res = muse_mod.probe(env={}, opener=FakeOpener(FakeResponse(b"not json")))
        self.assertEqual(res, {"answered": False, "error": "bad response"})
    def test_unexpected_json(self):
        """JSON without a non-empty choices list reports bad response."""
        for obj in ({"choices": []}, {"choices": "x"}, {"error": "busy"}, [], None):
            res = muse_mod.probe(env={}, opener=json_opener(obj))
            self.assertEqual(res, {"answered": False, "error": "bad response"}, msg=repr(obj))


class TestMain(unittest.TestCase):
    """main prints the check JSON and exits 0 only when the model is confirmed."""
    GOOD = {"route": "Muse Glimmer, on this device", "model": "muse-glimmer-30b", "endpoint": "http://127.0.0.1:1234/v1", "reachable": True, "served": ["muse-glimmer-30b"], "model_served": True}
    def run_main(self, argv, check_result, probe_result=None):
        """Run main with check and probe patched; return code, printed JSON and the probe mock."""
        out = io.StringIO()
        with mock.patch.object(muse_mod, "check", return_value=dict(check_result)), mock.patch.object(muse_mod, "probe", return_value=probe_result) as probe_mock, contextlib.redirect_stdout(out):
            code = muse_mod.main(argv)
        return code, json.loads(out.getvalue()), probe_mock
    def test_check_served(self):
        """Reachable and served exits 0 without a hint or probe."""
        code, printed, probe_mock = self.run_main(["muse", "--check"], self.GOOD)
        self.assertEqual(code, 0)
        self.assertEqual(printed, self.GOOD)
        probe_mock.assert_not_called()
    def test_check_not_served(self):
        """Reachable but not served exits 1 with a hint."""
        code, printed, _ = self.run_main(["muse", "--check"], dict(self.GOOD, served=["glimmer-review"], model_served=False))
        self.assertEqual(code, 1)
        self.assertFalse(printed["model_served"])
        self.assertEqual(printed["hint"], "Serve the model id muse-glimmer-30b on a loopback chat-completions server, or set OPOS_GLIMMER_URL to that server's /v1 URL.")
    def test_check_probe_unreachable_still_probes(self):
        """--probe merges probe keys even when /models is unreachable."""
        down = dict(self.GOOD, reachable=False, served=[], model_served=False)
        code, printed, probe_mock = self.run_main(["muse", "--check", "--probe"], down, {"answered": False, "error": "unreachable"})
        self.assertEqual(code, 1)
        probe_mock.assert_called_once()
        self.assertEqual(printed["error"], "unreachable")
        self.assertFalse(printed["answered"])
        self.assertIn("OPOS_GLIMMER_URL", printed["hint"])
    def test_check_unreachable(self):
        """Unreachable exits 1 with a hint."""
        code, printed, _ = self.run_main(["muse", "--check"], dict(self.GOOD, reachable=False, served=[], model_served=False))
        self.assertEqual(code, 1)
        self.assertIn("hint", printed)
    def test_check_probe_answered(self):
        """A probe that answers merges its keys and exits 0."""
        code, printed, probe_mock = self.run_main(["muse", "--check", "--probe"], self.GOOD, {"answered": True})
        self.assertEqual(code, 0)
        self.assertTrue(printed["answered"])
        self.assertTrue(printed["model_served"])
        self.assertNotIn("hint", printed)
        probe_mock.assert_called_once()
    def test_check_probe_failed(self):
        """A probe that fails exits 1 with its error and a hint."""
        code, printed, _ = self.run_main(["muse", "--check", "--probe"], self.GOOD, {"answered": False, "error": "HTTP 502"})
        self.assertEqual(code, 1)
        self.assertFalse(printed["answered"])
        self.assertEqual(printed["error"], "HTTP 502")
        self.assertIn("hint", printed)
    def test_check_probe_not_served(self):
        """An answering probe does not hide a missing model id."""
        code, printed, _ = self.run_main(["muse", "--check", "--probe"], dict(self.GOOD, model_served=False), {"answered": True})
        self.assertEqual(code, 1)
        self.assertIn("hint", printed)
    def test_no_check_flag(self):
        """Without --check main prints nothing and exits 0."""
        out = io.StringIO()
        with mock.patch.object(muse_mod, "check") as check_mock, mock.patch.object(muse_mod, "probe") as probe_mock, contextlib.redirect_stdout(out):
            self.assertEqual(muse_mod.main(["muse", "--probe"]), 0)
        self.assertEqual(out.getvalue(), "")
        check_mock.assert_not_called()
        probe_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
