# SPDX-License-Identifier: GPL-3.0-or-later
import os
import unittest
from unittest.mock import patch

from opos import assist, muse
from opos.web import Phone


class TestEngineLabel(unittest.TestCase):
    def setUp(self):
        # These are the phone build's labels; a source checkout says "on this computer" (test_panel_helper_route).
        phone = patch.object(assist, "PHONE_BUILD", True)
        phone.start()
        self.addCleanup(phone.stop)

    @patch("opos.assist.free_mb", return_value=2000)
    def test_local_llama(self, free_mb):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPOS_LAN_URL", None)
            expected = "Meta Llama 3.2 1B, on this phone"
            self.assertEqual(muse.label(assist.explain_route(free=2000)), expected)
            phone = Phone(visible=True)
            self.assertEqual(phone.state()["helper"]["engine"], expected)
            self.assertEqual(muse.label(phone.explain_request()[1]), expected)

    @patch("opos.assist.free_mb", return_value=None)
    def test_unknown_memory_uses_glimmer(self, free_mb):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPOS_LAN_URL", None)
            self.assertEqual(
                Phone(visible=True).state()["helper"]["engine"],
                "Muse Glimmer, on this device",
            )
            free_mb.assert_called_once_with()

    def _selected(self, question, env, free):
        # Mocked inference: the first route answer() tries is the selected one.
        tried = []
        def fake_ask(route, prompt, **kw):
            tried.append(route)
            return "ok"
        with patch("opos.assist.muse.ask", side_effect=fake_ask):
            r = assist.answer(question, env=env, free=free)
        self.assertTrue(r["ok"])
        self.assertEqual(r["engine"], tried[0].name)
        return muse.label(tried[0])

    def _check_label_matches_ask(self, env, free, expected):
        with patch.dict(os.environ, env, clear=False), patch("opos.assist.free_mb", return_value=free):
            if "OPOS_LAN_URL" not in env:
                os.environ.pop("OPOS_LAN_URL", None)
            label = Phone(visible=True).state()["helper"]["engine"]
            self.assertEqual(label, expected)
            self.assertEqual(label, self._selected("What is paused?", os.environ, free))

    def test_lan_and_local_short_ask_uses_local(self):
        # Synthetic RFC1918 example; route selection makes no network request.
        host = ".".join(str(octet) for octet in (192, 168, 0, 10))
        self._check_label_matches_ask({"OPOS_LAN_URL": f"http://{host}:1234/v1"}, 2000, "Meta Llama 3.2 1B, on this phone")

    def test_lan_only_when_local_unavailable(self):
        host = ".".join(str(octet) for octet in (192, 168, 0, 10))
        for free in (None, 1000):
            with self.subTest(free=free):
                self._check_label_matches_ask({"OPOS_LAN_URL": f"http://{host}:1234/v1"}, free, "Muse Glimmer, on a computer you own")

    def test_local_only(self):
        self._check_label_matches_ask({}, 5000, "Meta Llama 3.2 3B, on this phone")

    def test_neither_uses_glimmer(self):
        self._check_label_matches_ask({}, 1000, "Muse Glimmer, on this device")

    def test_hard_ask_prefers_lan(self):
        host = ".".join(str(octet) for octet in (192, 168, 0, 10))
        env = {"OPOS_LAN_URL": f"http://{host}:1234/v1"}
        q = " ".join(["word"] * 25)
        self.assertEqual(muse.label(assist.ask_route(env, 2000, q)), "Muse Glimmer, on a computer you own")
        self.assertEqual(self._selected(q, env, 2000), "Muse Glimmer, on a computer you own")

    def test_malformed_lan_url_does_not_crash_state(self):
        for url in ("http://[invalid", "http://[::1", "http://host:notaport/v1"):
            with self.subTest(url=url), patch.dict(os.environ, {"OPOS_LAN_URL": url}, clear=False), patch("opos.assist.free_mb", return_value=2000):
                self.assertIsNone(assist.lan_route(os.environ))
                self.assertEqual(Phone(visible=True).state()["helper"]["engine"], "Meta Llama 3.2 1B, on this phone")
                self.assertEqual(self._selected("What is paused?", os.environ, 2000), "Meta Llama 3.2 1B, on this phone")

    def test_explain_route_keeps_lan_precedence(self):
        # Ask labels follow short Ask routing; Explain keeps its LAN-first route.
        host = ".".join(str(octet) for octet in (192, 168, 0, 10))
        env = {"OPOS_LAN_URL": f"http://{host}:1234/v1"}
        self.assertEqual(
            muse.label(assist.explain_route(env=env, free=2000)),
            "Muse Glimmer, on a computer you own",
        )

if __name__ == "__main__":
    unittest.main()
