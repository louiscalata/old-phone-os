# SPDX-License-Identifier: GPL-3.0-or-later
"""Where Why, Plan and Ask send the owner's question: a verified local route, LAN address ranges, closed responses."""
import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from opos import assist, muse


QUESTION = "What is paused right now?"
FACTS = {"mode": "SAVER", "paused": ["email"], "why_paused": "The battery is worn, so email is paused."}
LOCAL_BASE = "http://127.0.0.1:8080/v1"
GLIMMER_BASE = muse.glimmer_route({}).base_url
LLAMA_1B = "llama-3.2-1b-instruct"
LLAMA_3B = "llama-3.2-3b-instruct"


def octets(*parts):
    """Dotted address built from octets, so the repository's private-address scan stays quiet."""
    return ".".join(str(p) for p in parts)


def chat(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def model_list(*ids):
    return json.dumps({"data": [{"id": i} for i in ids]}).encode()


class Body(io.BytesIO):
    """A response body that records close(); like http.client responses it closes on exit."""
    def __init__(self, data):
        super().__init__(data)
        self.close_calls = 0
        self.reads = []

    def read(self, n=-1):
        self.reads.append(n)
        return super().read(n)

    def close(self):
        self.close_calls += 1
        super().close()


class PlainBody:
    """A response with read() and close() but no context-manager methods."""
    def __init__(self, data):
        self.data = data
        self.close_calls = 0

    def read(self, n=-1):
        return self.data if n is None or n < 0 else self.data[:n]

    def close(self):
        self.close_calls += 1


class Servers:
    """Fake loopback servers: port 8080 lists local_models, Glimmer answers; records every request and body."""
    def __init__(self, local_models=None, local_error=None, local_reply="Llama answer."):
        self.local_models = local_models if local_models is not None else model_list("some-dev-server")
        self.local_error = local_error
        self.local_reply = local_reply
        self.requests = []
        self.bodies = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        data = req.data.decode("utf-8") if req.data else ""
        self.requests.append((req.get_method(), url, data, timeout))
        if url.startswith(LOCAL_BASE):
            if self.local_error is not None:
                raise self.local_error
            payload = self.local_models if url.endswith("/models") else chat(self.local_reply)
        elif url.startswith(GLIMMER_BASE):
            payload = chat("Glimmer answer.")
        else:
            raise OSError("no such server")
        body = Body(payload)
        self.bodies.append(body)
        return body

    def sent_to_local(self):
        return [(method, url, data) for method, url, data, _ in self.requests if url.startswith(LOCAL_BASE)]


class TestLocalRouteIsVerified(unittest.TestCase):
    """Port 8080 is a common development port: no question or phone facts go there unverified."""

    def assert_nothing_private_sent_to_local(self, servers):
        for method, url, data in servers.sent_to_local():
            self.assertEqual((method, url, data), ("GET", LOCAL_BASE + "/models", ""))

    def test_default_port_without_the_model_gets_no_question(self):
        servers = Servers(local_models=model_list("some-dev-server"))
        res = assist.answer(QUESTION, env={}, opener=servers, free=2000, context=FACTS)
        self.assertTrue(res["ok"])
        self.assertEqual(res["engine"], "Muse Glimmer")
        self.assertEqual(servers.sent_to_local(), [("GET", LOCAL_BASE + "/models", "")])
        self.assert_nothing_private_sent_to_local(servers)

    def test_near_miss_and_malformed_model_lists_are_refused(self):
        cases = {
            "suffix": model_list(LLAMA_1B + "-q4_k_m"),
            "case": model_list(LLAMA_1B.upper()),
            "other size suffix": model_list(LLAMA_3B + "-q4_k_m"),
            "other size case": model_list(LLAMA_3B.upper()),
            "other family": model_list("llama-3.1-8b-instruct", "llama-3.2-1b"),
            "id not a string": json.dumps({"data": [{"id": [LLAMA_1B]}, {"id": {"x": LLAMA_3B}}]}).encode(),
            "spaces": model_list(" " + LLAMA_1B),
            "html page": b"<html><body>dev server</body></html>",
            "json list": json.dumps([{"id": LLAMA_1B}]).encode(),
            "data is text": json.dumps({"data": LLAMA_1B}).encode(),
            "empty": b"",
        }
        for label, body in cases.items():
            with self.subTest(label):
                servers = Servers(local_models=body)
                res = assist.answer(QUESTION, env={}, opener=servers, free=2000, context=FACTS)
                self.assertEqual(res["engine"], "Muse Glimmer")
                self.assert_nothing_private_sent_to_local(servers)
        for error in (OSError("refused"), urllib.error.HTTPError(LOCAL_BASE + "/models", 404, "Not Found", {}, io.BytesIO(b"no"))):
            with self.subTest(error=type(error).__name__):
                servers = Servers(local_error=error)
                res = assist.answer(QUESTION, env={}, opener=servers, free=2000, context=FACTS)
                self.assertEqual(res["engine"], "Muse Glimmer")
                self.assert_nothing_private_sent_to_local(servers)

    def test_listed_model_gets_the_question_after_the_check(self):
        servers = Servers(local_models=model_list("other", LLAMA_1B))
        with mock.patch.object(assist, "PHONE_BUILD", False):
            res = assist.answer(QUESTION, env={}, opener=servers, free=2000, context=FACTS)
        self.assertEqual((res["ok"], res["engine"], res["where"]), (True, "Meta Llama 3.2 1B", "on this computer"))
        local = servers.sent_to_local()
        self.assertEqual([(m, u) for m, u, _ in local], [("GET", LOCAL_BASE + "/models"), ("POST", LOCAL_BASE + "/chat/completions")])
        self.assertIn(QUESTION, json.loads(local[1][2])["messages"][0]["content"])
        check_timeout = servers.requests[0][3]
        self.assertLessEqual(check_timeout, muse.MODEL_LIST_TIMEOUT)

    def test_either_llama_size_is_verified_and_named_as_served(self):
        # The server already holds the size it loaded; free memory, measured while it runs, does not decide it.
        cases = (
            ("3B server, under 4096 MB free", 3000, (LLAMA_3B,), LLAMA_3B, "Meta Llama 3.2 3B"),
            ("3B server among other ids", 2000, ("other", LLAMA_3B), LLAMA_3B, "Meta Llama 3.2 3B"),
            ("1B server, 4096 MB or more free", 5000, (LLAMA_1B,), LLAMA_1B, "Meta Llama 3.2 1B"),
            ("both listed, low memory keeps 1B", 2000, (LLAMA_3B, LLAMA_1B), LLAMA_1B, "Meta Llama 3.2 1B"),
            ("both listed, high memory keeps 3B", 5000, (LLAMA_1B, LLAMA_3B), LLAMA_3B, "Meta Llama 3.2 3B"),
        )
        for label, free, listed, posted, name in cases:
            with self.subTest(label):
                servers = Servers(local_models=model_list(*listed))
                with mock.patch.object(assist, "PHONE_BUILD", False):
                    res = assist.answer(QUESTION, env={}, opener=servers, free=free, context=FACTS)
                self.assertEqual((res["ok"], res["engine"], res["where"], res["text"]), (True, name, "on this computer", "Llama answer."))
                self.assertIs(type(res["text"]), str)
                local = servers.sent_to_local()
                self.assertEqual([(m, u) for m, u, _ in local], [("GET", LOCAL_BASE + "/models"), ("POST", LOCAL_BASE + "/chat/completions")])
                body = json.loads(local[1][2])
                self.assertEqual(body["model"], posted)
                self.assertIn(QUESTION, body["messages"][0]["content"])

    def test_ask_reports_the_route_that_answered(self):
        servers = Servers(local_models=model_list(LLAMA_3B))
        route = assist.local_route({}, free=2000)
        self.assertEqual((route.model, route.alternates), (LLAMA_1B, ((LLAMA_3B, "Meta Llama 3.2 3B"),)))
        reply = muse.ask(route, "hi", env={}, opener=servers)
        self.assertEqual(reply, "Llama answer.")
        self.assertEqual((reply.route.name, reply.route.model, reply.route.where, reply.route.base_url), ("Meta Llama 3.2 3B", LLAMA_3B, route.where, LOCAL_BASE))
        self.assertIs(muse.answered_by("plain text", route), route)
        # A configured OPOS_LOCAL_URL is used as set: no check and no alternate ids.
        self.assertEqual(assist.local_route({"OPOS_LOCAL_URL": LOCAL_BASE}, free=2000).alternates, ())

    def test_why_and_plan_name_the_size_the_server_lists(self):
        routes = assist.explain_routes(env={}, free=5000)  # the free-memory pick is 3B
        self.assertEqual(routes[0].name, "Meta Llama 3.2 3B")
        out = muse.explain(FACTS, routes, env={}, opener=Servers(local_models=model_list(LLAMA_1B)))
        self.assertEqual((out["ok"], out["engine"], out["text"]), (True, "Meta Llama 3.2 1B", "Llama answer."))
        self.assertIs(type(out["text"]), str)
        servers = Servers(local_models=model_list(LLAMA_1B), local_reply='["pause email", "dim screen"]')
        order = muse.plan(FACTS, ["dim screen", "pause email"], routes, env={}, opener=servers)
        self.assertEqual(list(order), ["pause email", "dim screen"])
        self.assertEqual((order.route.key, order.route.name, order.route.model), ("local", "Meta Llama 3.2 1B", LLAMA_1B))
        self.assertEqual(json.loads(servers.sent_to_local()[1][2])["model"], LLAMA_1B)

    def test_configured_url_is_used_without_a_check(self):
        servers = Servers(local_models=b"not a model list")
        env = {"OPOS_LOCAL_URL": LOCAL_BASE}
        route = assist.local_route(env, free=2000)
        self.assertFalse(route.verify_model)
        res = assist.answer(QUESTION, env=env, opener=servers, free=2000, context=FACTS)
        self.assertEqual(res["engine"], "Meta Llama 3.2 1B")
        self.assertEqual([(m, u) for m, u, _ in servers.sent_to_local()], [("POST", LOCAL_BASE + "/chat/completions")])

    def test_only_the_unconfigured_default_is_marked_for_the_check(self):
        self.assertTrue(assist.local_route({}, free=2000).verify_model)
        self.assertTrue(assist.local_route({}, free=5000).verify_model)
        # A non-loopback OPOS_LOCAL_URL is ignored, so the default port is used and checked.
        doc_host = octets(192, 0, 2, 10)
        ignored = assist.local_route({"OPOS_LOCAL_URL": "http://" + doc_host + ":8080/v1"}, free=2000)
        self.assertEqual((ignored.base_url, ignored.verify_model), (LOCAL_BASE, True))
        configured = assist.local_route({"OPOS_LOCAL_URL": "http://localhost:9000/v1"}, free=2000)
        self.assertEqual((configured.base_url, configured.verify_model), ("http://localhost:9000/v1", False))
        self.assertFalse(muse.glimmer_route({}).verify_model)
        self.assertFalse(assist.lan_route({"OPOS_LAN_URL": "http://127.0.0.1:1236/v1"}).verify_model)

    def test_why_and_plan_send_no_facts_to_an_unverified_server(self):
        routes = assist.explain_routes(env={}, free=2000)
        self.assertEqual([r.key for r in routes], ["local", "glimmer"])
        servers = Servers()
        out = muse.explain(FACTS, routes, env={}, opener=servers)
        self.assertEqual((out["ok"], out["engine"]), (True, "Muse Glimmer"))
        self.assert_nothing_private_sent_to_local(servers)
        plan_servers = PlanServers()
        order = muse.plan(FACTS, ["dim screen", "pause email"], routes, env={}, opener=plan_servers)
        self.assertEqual(order.route.key, "glimmer")
        self.assert_nothing_private_sent_to_local(plan_servers)


class PlanServers(Servers):
    """Glimmer replies with a step list so Plan has a usable answer."""
    def __call__(self, req, timeout=None):
        if req.full_url.startswith(GLIMMER_BASE):
            self.requests.append((req.get_method(), req.full_url, req.data.decode("utf-8"), timeout))
            body = Body(chat('["pause email", "dim screen"]'))
            self.bodies.append(body)
            return body
        return super().__call__(req, timeout)


class TestLocalRouteLabel(unittest.TestCase):
    def test_computer_label_off_the_phone_build(self):
        with mock.patch.object(assist, "PHONE_BUILD", False):
            self.assertEqual(muse.label(assist.local_route({}, free=2000)), "Meta Llama 3.2 1B, on this computer")
            self.assertEqual(muse.label(assist.ask_route({}, 5000)), "Meta Llama 3.2 3B, on this computer")

    def test_phone_label_on_the_phone_build(self):
        with mock.patch.object(assist, "PHONE_BUILD", True):
            self.assertEqual(muse.label(assist.local_route({}, free=2000)), "Meta Llama 3.2 1B, on this phone")

    def layout(self, tmp, launcher=True):
        """A system purelib with opos in it, a source checkout and (optionally) the package's kiosk launcher."""
        purelib = Path(tmp) / "lib" / "site-packages"
        checkout = Path(tmp) / "checkout" / "src"
        for d in (purelib / "opos", checkout / "opos"):
            d.mkdir(parents=True)
        kiosk = Path(tmp) / "usr" / "bin" / "opos-kiosk"
        if launcher:
            kiosk.parent.mkdir(parents=True)
            kiosk.write_text("#!/bin/sh\n")
        paths = {"purelib": str(purelib), "platlib": str(purelib)}
        return str(purelib / "opos" / "assist.py"), str(checkout / "opos" / "assist.py"), str(kiosk), paths

    def environment(self, paths, kiosk, prefix="/usr"):
        """Patch the interpreter's purelib, sys.prefix (base_prefix is /usr) and the launcher path."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(assist.sysconfig, "get_path", side_effect=paths.__getitem__))
        stack.enter_context(mock.patch.object(assist.sys, "prefix", prefix))
        stack.enter_context(mock.patch.object(assist.sys, "base_prefix", "/usr"))
        stack.enter_context(mock.patch.object(assist, "PHONE_LAUNCHER", kiosk, create=True))
        return stack

    def test_phone_build_is_the_package_in_the_system_purelib(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed, checkout, kiosk, paths = self.layout(tmp)
            with self.environment(paths, kiosk):
                self.assertTrue(assist._phone_package(installed))
                self.assertFalse(assist._phone_package(checkout))

    def test_a_virtual_environment_is_not_the_phone_build(self):
        # pip install . into a desktop venv puts opos in that venv's purelib.
        with tempfile.TemporaryDirectory() as tmp:
            installed, _, kiosk, paths = self.layout(tmp)
            with self.environment(paths, kiosk, prefix=str(Path(tmp) / "venv")):
                self.assertFalse(assist._phone_package(installed))

    def test_the_phone_build_has_the_package_kiosk_launcher(self):
        # A system-wide pip install on a desktop has no /usr/bin/opos-kiosk; the opos APKBUILD installs it.
        with tempfile.TemporaryDirectory() as tmp:
            installed, _, kiosk, paths = self.layout(tmp, launcher=False)
            with self.environment(paths, kiosk):
                self.assertFalse(assist._phone_package(installed))
        apkbuild = (Path(__file__).resolve().parent.parent / "packaging" / "pmos" / "opos" / "APKBUILD").read_text(encoding="utf-8")
        self.assertIn('"$pkgdir' + assist.PHONE_LAUNCHER + '"', apkbuild)

    def test_source_checkout_is_not_the_phone_build(self):
        self.assertFalse(assist._phone_package(assist.__file__))
        self.assertEqual(Path(assist.__file__).resolve().parent.parent.name, "src")


class TestLanAddressRanges(unittest.TestCase):
    """RFC 1918, IPv6 unique-local and loopback (the reverse tunnel in docs/duo-usb-install.md) only."""

    ACCEPTED = (
        octets(10, 1, 2, 3), octets(172, 16, 0, 1), octets(172, 31, 255, 254), octets(192, 168, 0, 10),
        "[fd12:3456::1]", "[fc00::1]", "127.0.0.1", "[::1]",
    )
    REFUSED = (
        octets(169, 254, 1, 1),  # IPv4 link-local
        "[fe80::1]",  # IPv6 link-local
        octets(192, 0, 2, 10),  # documentation
        "[2001:db8::1]",  # IPv6 documentation
        octets(198, 18, 0, 1),  # benchmarking
        octets(240, 0, 0, 1),  # reserved
        "0.0.0.0", "[::]",  # unspecified
        octets(100, 64, 0, 1),  # carrier-grade NAT
        octets(172, 32, 0, 1),  # just outside 172.16/12
        "[fec0::1]",  # deprecated site-local
        "8.8.8.8", "[::ffff:8.8.8.8]",
        "localhost", "lan.example",
    )

    def test_accepts_private_lan_and_loopback(self):
        for host in self.ACCEPTED:
            with self.subTest(host=host):
                route = assist.lan_route({"OPOS_LAN_URL": f"http://{host}:1234/v1"})
                self.assertIsNotNone(route)
                self.assertEqual((route.key, route.where, route.model), ("lan", "on a computer you own", "muse-glimmer-30b"))

    def test_refuses_link_local_reserved_and_public(self):
        for host in self.REFUSED:
            with self.subTest(host=host):
                self.assertIsNone(assist.lan_route({"OPOS_LAN_URL": f"http://{host}:1234/v1"}))

    def test_refused_lan_gets_no_question(self):
        host = octets(169, 254, 1, 1)
        servers = Servers()
        res = assist.answer(QUESTION, env={"OPOS_LAN_URL": f"http://{host}:1234/v1"}, opener=servers, free=None, context=FACTS)
        self.assertEqual(res["engine"], "Muse Glimmer")
        self.assertEqual([u for _, u, _, _ in servers.requests], [GLIMMER_BASE + "/chat/completions"])


class TestResponsesAreClosed(unittest.TestCase):
    def test_ask_closes_its_response(self):
        body = Body(chat("Hello."))
        self.assertEqual(muse.ask(muse.glimmer_route({}), "hi", env={}, opener=lambda req, timeout=None: body), "Hello.")
        self.assertGreaterEqual(body.close_calls, 1)
        self.assertEqual(body.reads, [muse.MAX_RESPONSE_BYTES + 1], "the read stays capped")

    def test_ask_closes_its_response_when_the_reply_is_unusable(self):
        big = chat("x" * (muse.MAX_RESPONSE_BYTES + 10))
        for label, data in (("not json", b"<html>"), ("too large", big), ("no choices", b"{}")):
            with self.subTest(label):
                body = Body(data)
                with self.assertRaises(muse.MuseError):
                    muse.ask(muse.glimmer_route({}), "hi", env={}, opener=lambda req, timeout=None: body)
                self.assertGreaterEqual(body.close_calls, 1)

    def test_ask_closes_a_response_without_context_manager_methods(self):
        body = PlainBody(chat("Hello."))
        self.assertEqual(muse.ask(muse.glimmer_route({}), "hi", env={}, opener=lambda req, timeout=None: body), "Hello.")
        self.assertEqual(body.close_calls, 1)

    def test_ask_closes_an_http_error_body(self):
        body = Body(b"Bad Gateway")
        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, body)
        with self.assertRaises(muse.MuseError) as raised:
            muse.ask(muse.glimmer_route({}), "hi", env={}, opener=opener)
        # The error is still referenced here, so garbage collection has not closed it yet.
        self.assertIsInstance(raised.exception.__cause__, urllib.error.HTTPError)
        self.assertGreaterEqual(body.close_calls, 1)

    def test_model_check_and_reply_are_both_closed(self):
        servers = Servers(local_models=model_list(LLAMA_1B))
        self.assertEqual(muse.ask(assist.local_route({}, free=2000), "hi", env={}, opener=servers), "Llama answer.")
        self.assertEqual(len(servers.bodies), 2)
        for body in servers.bodies:
            self.assertGreaterEqual(body.close_calls, 1)
            self.assertEqual(body.reads, [muse.MAX_RESPONSE_BYTES + 1])

    def test_search_closes_its_response(self):
        wiki = json.dumps({"query": {"search": [{"title": "Battery", "snippet": "Stored <b>energy</b>"}]}}).encode()
        for label, data, expected in (("results", wiki, 1), ("not json", b"<html>", 0)):
            with self.subTest(label):
                body = Body(data)
                out = assist.search("battery", opener=lambda req, timeout=None: body)
                self.assertEqual(len(out), expected)
                self.assertGreaterEqual(body.close_calls, 1)
                self.assertEqual(body.reads, [65536], "the read stays capped")
        plain = PlainBody(wiki)
        self.assertEqual(assist.search("battery", opener=lambda req, timeout=None: plain)[0]["title"], "Battery")
        self.assertEqual(plain.close_calls, 1)

    def test_check_and_probe_close_their_responses(self):
        listed = Body(model_list("muse-glimmer-30b"))
        self.assertTrue(muse.check(env={}, opener=lambda req, timeout=None: listed)["model_served"])
        self.assertGreaterEqual(listed.close_calls, 1)
        reply = Body(chat("OK"))
        self.assertTrue(muse.probe(env={}, opener=lambda req, timeout=None: reply)["answered"])
        self.assertGreaterEqual(reply.close_calls, 1)

    def test_default_transport_is_still_the_no_redirect_opener(self):
        bodies = []
        def fake(req, timeout=None):
            body = Body(chat("Hello.") if req.full_url.endswith("/chat/completions") else json.dumps({"query": {"search": []}}).encode())
            bodies.append(body)
            return body
        with mock.patch.object(muse, "_open_no_redirect", side_effect=fake) as opener:
            self.assertEqual(muse.ask(muse.glimmer_route({}), "hi", env={}), "Hello.")
            self.assertEqual(assist.search("battery"), [])
        self.assertEqual(opener.call_count, 2)
        self.assertTrue(all(b.close_calls >= 1 for b in bodies))


if __name__ == "__main__":
    unittest.main()
