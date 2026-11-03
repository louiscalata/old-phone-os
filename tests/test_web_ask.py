# SPDX-License-Identifier: GPL-3.0-or-later
import json
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import opos.assist as assist
import opos.muse as muse
from opos.web import Phone
from src.opos import web as srv

def _post(port, token, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api", data=data, headers={"Content-Type": "application/json", "X-OPOS-Token": token}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def _get(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        return r.read().decode()

class TestWebAsk(unittest.TestCase):
    def test_ask_visible(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            got = {}
            def fake(question, web=False, env=None, opener=None, free=None, context=None):
                got["web"] = web
                got["q"] = question
                got["context"] = context
                return {"ok": True, "text": "hi", "engine": "E", "where": "W", "sources": []}
            facts = {"mode": "normal", "reserve": 20.0}
            with patch.object(assist, "answer", side_effect=fake):
                with patch.object(phone, "ask_facts", return_value=facts):
                    st, body = _post(port, token, {"action": "ask", "text": "why is the sky blue"})
                    self.assertEqual(st, 200)
                    self.assertEqual(body, {"ok": True, "text": "hi", "engine": "E", "where": "W", "sources": []})
                    self.assertEqual(got["web"], False)
                    self.assertEqual(got["context"], facts)
                    st, body = _post(port, token, {"action": "ask", "text": "why is the sky blue", "web": True})
                    self.assertEqual(st, 200)
                    self.assertEqual(got["web"], True)
                    self.assertEqual(got["context"], facts)
        finally:
            server.shutdown()
            server.server_close()

    def test_ask_hidden(self):
        phone = Phone(visible=False)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            def fake(question, web=False, env=None, opener=None, free=None, context=None):
                return {"ok": True, "text": "x", "engine": "E", "where": "W", "sources": []}
            with patch.object(assist, "answer", side_effect=fake):
                st, body = _post(port, token, {"action": "ask", "text": "why is the sky blue"})
                self.assertEqual(st, 200)
                self.assertEqual(body, {"error": "helper hidden"})
        finally:
            server.shutdown()
            server.server_close()

    def test_ask_rate_limit(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            def fake(question, web=False, env=None, opener=None, free=None, context=None):
                return {"ok": True, "text": "hi", "engine": "E", "where": "W", "sources": []}
            with patch.object(assist, "answer", side_effect=fake):
                for _ in range(3):
                    st, _ = _post(port, token, {"action": "ask", "text": "why is the sky blue"})
                    self.assertEqual(st, 200)
                st, body = _post(port, token, {"action": "ask", "text": "why is the sky blue"})
                self.assertEqual(st, 429)
        finally:
            server.shutdown()
            server.server_close()

    def test_ask_bad_request(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            def fake(question, web=False, env=None, opener=None, free=None, context=None):
                return {"ok": True, "text": "hi", "engine": "E", "where": "W", "sources": []}
            with patch.object(assist, "answer", side_effect=fake):
                st, _ = _post(port, token, {"action": "ask", "text": 123})
                self.assertEqual(st, 400)
        finally:
            server.shutdown()
            server.server_close()

    def test_ask_facts_when_visible(self):
        phone = Phone(visible=True)
        helper = {"mode": "normal", "reserve": 20.0, "source": "router"}
        with patch.object(phone, "state", return_value={"helper": helper}) as ms:
            facts = phone.ask_facts()
            self.assertEqual(facts, {"mode": "normal", "reserve": 20.0})
            ms.assert_called_once()

    def test_ask_facts_empty_when_hidden(self):
        phone = Phone(visible=False)
        with patch.object(phone, "state") as ms:
            self.assertEqual(phone.ask_facts(), {})
            ms.assert_not_called()

    def test_plan(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            with patch.object(muse, "plan", return_value=["b", "a"]) as mp:
                st, body = _post(port, token, {"action": "plan"})
                self.assertEqual(st, 200)
                self.assertEqual(body["order"], ["b", "a"])
                self.assertIn("engine", body)
                self.assertIn("where", body)
                mp.assert_called_once()
        finally:
            server.shutdown()
            server.server_close()

    def test_plan_fixed_order(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            with patch.object(muse, "plan", side_effect=lambda facts, steps, route: list(steps)):
                st, body = _post(port, token, {"action": "plan"})
                self.assertEqual(st, 200)
                self.assertEqual(body["engine"], "fixed order")
        finally:
            server.shutdown()
            server.server_close()

    def test_page_contains_ask(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            html = _get(port)
            self.assertIn('id="ask"', html)
            self.assertIn('askbtn', html)
            idx = html.find("getElementById('askbtn')")
            if idx == -1:
                idx = html.find("askbtn")
            seg = html[idx:idx+1500]
            self.assertIn("textContent", seg)
            self.assertNotIn("innerHTML", seg)
        finally:
            server.shutdown()
            server.server_close()

if __name__ == "__main__":
    unittest.main()
