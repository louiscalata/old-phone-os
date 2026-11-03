# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import unittest.mock
from opos.web import Phone, make_server
import opos.web

class TestWebTokenFile(unittest.TestCase):
    def test_token_validation(self):
        with self.assertRaisesRegex(ValueError, "bad token"):
            make_server(0, Phone(), token="short")
        with self.assertRaisesRegex(ValueError, "bad token"):
            make_server(0, Phone(), token="a" * 31)
        with self.assertRaisesRegex(ValueError, "bad token"):
            make_server(0, Phone(), token=12345)
        with self.assertRaisesRegex(ValueError, "bad token"):
            make_server(0, Phone(), token=None if False else 3.14)

    def test_http_get_and_post_token(self):
        pre = chr(65) + chr(97) + chr(48) + chr(95) + chr(45)
        cases = [None, pre + chr(97) * 27, pre + chr(97) * 123]
        for tok in cases:
            with self.subTest(token=tok):
                srv = make_server(0, Phone(), token=tok)
                T = srv.RequestHandlerClass.token
                if tok is not None:
                    self.assertEqual(T, tok)
                E = tok if tok is not None else T
                port = srv.server_address[1]
                thr = threading.Thread(target=srv.serve_forever, daemon=True)
                thr.start()
                time.sleep(0.2)
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    conn.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
                    resp = conn.getresponse()
                    body = resp.read().decode()
                    self.assertEqual(resp.status, 200)
                    self.assertIn(E, body)
                    self.assertIn(f'content="{E}"', body)
                    payload = json.dumps({"action": "in_use", "name": "maps"})
                    conn2 = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    conn2.request("POST", "/api", body=payload, headers={"Host": f"127.0.0.1:{port}", "X-OPOS-Token": E, "Content-Type": "application/json"})
                    r2 = conn2.getresponse()
                    self.assertEqual(r2.status, 200)
                    r2.read()
                    conn3 = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    conn3.request("POST", "/api", body=payload, headers={"Host": f"127.0.0.1:{port}", "X-OPOS-Token": chr(98) * 32, "Content-Type": "application/json"})
                    r3 = conn3.getresponse()
                    self.assertEqual(r3.status, 403)
                    r3.read()
                finally:
                    srv.shutdown()
                    srv.server_close()

    def test_unsafe_tokens_rejected_before_bind(self):
        base = chr(97) * 32
        forb = [chr(34), chr(39), chr(60), chr(62), chr(38), chr(233), chr(10)]
        cases = [12345, chr(97) * 31, chr(97) * 129]
        for ch in forb:
            cases.append(base + ch + chr(97))
        for value in cases:
            with self.subTest(token=value):
                with unittest.mock.patch("opos.web._Server") as srv:
                    with self.assertRaises(ValueError) as cm:
                        make_server(0, Phone(), token=value)
                    self.assertEqual(str(cm.exception), "bad token")
                    srv.assert_not_called()

    def test_main_unsafe_token_file_rejected(self):
        base = chr(97) * 32
        forb = [chr(34), chr(39), chr(60), chr(62), chr(38), chr(233), chr(10)]
        cases = [chr(97) * 31, chr(97) * 129]
        for ch in forb:
            cases.append(base + ch + chr(97))
        for value in cases:
            with self.subTest(token=value):
                m = unittest.mock.mock_open(read_data=value)
                with unittest.mock.patch("builtins.open", m):
                    with unittest.mock.patch("opos.web.make_server") as mk:
                        buf = io.StringIO()
                        with contextlib.redirect_stderr(buf):
                            with self.assertRaises(SystemExit) as cm:
                                opos.web.main(["--token-file", "unused"])
                        self.assertEqual(cm.exception.code, 2)
                        self.assertEqual(buf.getvalue(), "opos: cannot read session token\n")
                        mk.assert_not_called()

    def test_page_escapes_token_attribute(self):
        tok = chr(120) + chr(34) + " autofocus=" + chr(34) + "y" + chr(60) + chr(38) + chr(39) + chr(62)
        out = opos.web.page(tok)
        exp = '<meta name="opos-token" content="x&quot; autofocus=&quot;y&lt;&amp;&#x27;&gt;">'
        self.assertIn(exp, out)
        self.assertNotIn('<meta name="opos-token" content="' + tok + '">', out)

    def test_main_missing_file(self):
        missing = os.path.join(tempfile.gettempdir(), "opos-missing-fake-xyz")
        if os.path.exists(missing):
            os.unlink(missing)
        for argv in (["opos", "--token-file", missing], ["--token-file", missing]):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                try:
                    opos.web.main(argv)
                except SystemExit as e:
                    err = buf.getvalue()
                    if e.code == 2 and "opos: cannot read session token" in err:
                        self.assertNotIn("a" * 32, err)
                        return
        self.fail("missing file should exit 2")

    def test_main_short_token_file(self):
        short = "short123"
        tf = tempfile.NamedTemporaryFile(delete=False, mode="w")
        tf.write(short)
        tf.close()
        try:
            for argv in (["opos", "--token-file", tf.name], ["--token-file", tf.name]):
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    try:
                        opos.web.main(argv)
                    except SystemExit as e:
                        err = buf.getvalue()
                        if e.code == 2 and "opos: cannot read session token" in err:
                            self.assertNotIn(short, err)
                            return
            self.fail("short token should exit 2")
        finally:
            os.unlink(tf.name)

    def test_main_valid_token_file_mocked(self):
        T = "a" * 40
        raw = f"  {T} \n"
        tf = tempfile.NamedTemporaryFile(delete=False, mode="w")
        tf.write(raw)
        tf.close()
        os.chmod(tf.name, 0o600)
        try:
            for argv in (["opos", "--token-file", tf.name], ["--token-file", tf.name]):
                with unittest.mock.patch("opos.web.make_server") as mk:
                    mk.return_value.serve_forever = unittest.mock.Mock()
                    buf = io.StringIO()
                    with contextlib.redirect_stderr(buf):
                        try:
                            opos.web.main(argv)
                        except SystemExit:
                            continue
                    if mk.called:
                        args, kwargs = mk.call_args
                        got = kwargs.get("token")
                        if got is None:
                            for a in args:
                                if isinstance(a, str) and len(a) >= 32:
                                    got = a
                                    break
                            if got is None and len(args) >= 4:
                                got = args[3]
                        self.assertEqual(got, T)
                        mk.return_value.serve_forever.assert_called()
                        self.assertNotIn(T, buf.getvalue())
                        return
            self.fail("valid token file should call make_server")
        finally:
            os.unlink(tf.name)
