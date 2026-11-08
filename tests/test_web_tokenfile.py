# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import errno
import http.client
import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
import unittest.mock
from opos import tokenfile
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
        with tempfile.TemporaryDirectory() as d:
            for i, value in enumerate(cases):
                with self.subTest(token=value):
                    path = os.path.join(d, "token%d" % i)
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(value)
                    with unittest.mock.patch("opos.web.make_server") as mk:
                        buf = io.StringIO()
                        with contextlib.redirect_stderr(buf):
                            with self.assertRaises(SystemExit) as cm:
                                opos.web.main(["--token-file", path])
                        self.assertEqual(cm.exception.code, 2)
                        self.assertEqual(buf.getvalue(), "opos: cannot read session token\n")
                        mk.assert_not_called()
                    with open(path, encoding="utf-8", newline="") as f:
                        self.assertEqual(f.read(), value, "a bad file is refused, never replaced")

    def test_page_escapes_token_attribute(self):
        tok = chr(120) + chr(34) + " autofocus=" + chr(34) + "y" + chr(60) + chr(38) + chr(39) + chr(62)
        out = opos.web.page(tok)
        exp = '<meta name="opos-token" content="x&quot; autofocus=&quot;y&lt;&amp;&#x27;&gt;">'
        self.assertIn(exp, out)
        self.assertNotIn('<meta name="opos-token" content="' + tok + '">', out)

    def start_with(self, path):
        """Run main() against a mocked server; return the token it handed to make_server."""
        with unittest.mock.patch("opos.web.make_server") as mk:
            mk.return_value.serve_forever = unittest.mock.Mock()
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()):
                opos.web.main(["--token-file", path])
            self.assertTrue(mk.called)
            mk.return_value.serve_forever.assert_called()
            token = mk.call_args.kwargs["token"]
            self.assertNotIn(token, buf.getvalue())
            return token

    def test_main_missing_file_is_created_private_and_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "token")
            first = self.start_with(path)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), first)
            self.assertEqual(opos.web._validate_token(first), first)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            # a restart reads the same file: the page that is still open keeps a token the server accepts
            self.assertEqual(self.start_with(path), first)
            self.assertEqual(sorted(os.listdir(d)), ["token"], "no temporary file is left behind")

    def test_main_missing_directory_still_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "no-such-directory", "token")
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit) as cm:
                    opos.web.main(["--token-file", path])
            self.assertEqual(cm.exception.code, 2)
            self.assertEqual(buf.getvalue(), "opos: cannot read session token\n")
            self.assertEqual(os.listdir(d), [])

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


class TestTokenFileModule(unittest.TestCase):
    def read(self, path):
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()

    def test_creates_a_private_file_with_a_valid_token(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "token")
            self.assertTrue(tokenfile.ensure(path))
            text = self.read(path)
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(opos.web._validate_token(text.strip()), text.strip())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(os.listdir(d), ["token"])

    def test_two_files_get_two_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("a", "b"):
                tokenfile.ensure(os.path.join(d, n))
            self.assertNotEqual(self.read(os.path.join(d, "a")), self.read(os.path.join(d, "b")))

    def test_an_existing_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "token")
            for content in ("", "short", "a" * 40 + "\n"):
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                self.assertFalse(tokenfile.ensure(path))
                self.assertEqual(self.read(path), content)

    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "symbolic links")
    def test_a_symbolic_link_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "elsewhere")
            path = os.path.join(d, "token")
            os.symlink(target, path)
            self.assertFalse(tokenfile.ensure(path))
            self.assertFalse(os.path.exists(target))

    def test_losing_the_race_keeps_the_other_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "token")
            real_link = os.link

            def other_wins(src, dst, **kw):
                with open(dst, "w", encoding="utf-8", newline="") as f:
                    f.write("b" * 43 + "\n")
                return real_link(src, dst, **kw)  # raises FileExistsError

            with unittest.mock.patch("os.link", other_wins):
                self.assertFalse(tokenfile.ensure(path))
            self.assertEqual(self.read(path), "b" * 43 + "\n")
            self.assertEqual(os.listdir(d), ["token"])

    def test_without_hard_links_the_file_is_made_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "token")
            with unittest.mock.patch("os.link", side_effect=OSError(errno.EPERM, "no links")):
                self.assertTrue(tokenfile.ensure(path))
            text = self.read(path)
            self.assertEqual(opos.web._validate_token(text.strip()), text.strip())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(os.listdir(d), ["token"])

    def test_a_missing_directory_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OSError):
                tokenfile.ensure(os.path.join(d, "nope", "token"))


class TestServerUnit(unittest.TestCase):
    """The Duo image's unit keeps the token across restarts: the state directory and the file in it."""

    def unit(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "packaging", "duo-os", "opos-server.service"), encoding="utf-8") as f:
            return f.read().splitlines()

    def test_the_unit_has_a_state_directory_and_passes_the_token_file(self):
        ls = self.unit()
        self.assertEqual(ls.count("StateDirectory=opos"), 1)
        self.assertEqual(ls.count("StateDirectoryMode=0700"), 1)
        execs = [x for x in ls if x.startswith("ExecStart=")]
        self.assertEqual(len(execs), 1)
        self.assertIn(" -m opos.web ", execs[0])
        self.assertEqual(execs[0].split()[-2:], ["--token-file", "%S/opos/token"], "%S is /var/lib for a system unit, where StateDirectory=opos lives")
        self.assertLess(ls.index("[Service]"), ls.index("StateDirectory=opos"))
        self.assertLess(ls.index("StateDirectory=opos"), ls.index(execs[0]))
        self.assertLess(ls.index(execs[0]), ls.index("[Install]"))
        self.assertEqual(ls.count("Restart=always"), 1, "this is why the token must not change at a restart")
