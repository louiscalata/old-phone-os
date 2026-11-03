# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for loopback security helpers."""
import io
import unittest
from opos import secure


def _rf(data: bytes) -> io.BytesIO:
    """Build a readable body stream."""
    return io.BytesIO(data)


def _hd(length: str | None) -> dict[str, str]:
    """Build minimal headers with optional length."""
    return {"Content-Length": length} if length is not None else {}


class SecureTest(unittest.TestCase):
    """Focused checks for secure helpers."""

    def test_check_bind(self) -> None:
        """Accept loopback and reject the rest."""
        for good in ("127.0.0.1", "::1"):
            with self.subTest(host=good):
                secure.check_bind(good)
        for bad in ("0.0.0.0", "", "192.0.2.1"):
            with self.subTest(host=bad):
                with self.assertRaises(ValueError):
                    secure.check_bind(bad)

    def test_token(self) -> None:
        """Compare tokens safely and mint unique ones."""
        self.assertTrue(secure.token_ok("abc", "abc"))
        for sup, exp in (("a", "b"), ("", "x"), ("x", ""), (None, "x"), ("x", None), (1, "x"), ("x", 1)):
            with self.subTest(sup=sup, exp=exp):
                self.assertFalse(secure.token_ok(sup, exp))
        self.assertFalse(secure.token_ok("a\ud800", "a\ud800"))
        self.assertFalse(secure.token_ok("abc", "a\ud800"))
        a = secure.new_token()
        b = secure.new_token()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 40)
        self.assertGreaterEqual(len(b), 40)

    def test_host_origin(self) -> None:
        """Accept exact loopback host and origin forms."""
        self.assertTrue(secure.host_ok("127.0.0.1:8765", 8765))
        self.assertTrue(secure.host_ok("localhost:8765", 8765))
        for bad in ("evil.com", "127.0.0.1.evil.com:8765", "localhost:8766", None):
            with self.subTest(host=bad):
                self.assertFalse(secure.host_ok(bad, 8765))
        self.assertTrue(secure.origin_ok(None, 8765))
        self.assertTrue(secure.origin_ok("http://127.0.0.1:8765", 8765))
        self.assertTrue(secure.origin_ok("http://localhost:8765", 8765))
        for bad in ("null", "http://evil.com"):
            with self.subTest(origin=bad):
                self.assertFalse(secure.origin_ok(bad, 8765))

    def test_json_type(self) -> None:
        """Accept only application/json media type."""
        self.assertTrue(secure.json_type_ok("application/json"))
        self.assertTrue(secure.json_type_ok("Application/JSON; charset=utf-8"))
        for bad in ("text/plain", "", None):
            with self.subTest(ct=bad):
                self.assertFalse(secure.json_type_ok(bad))

    def test_read_valid(self) -> None:
        """Return the decoded dict for a valid body."""
        out = secure.read_json_body(_rf(b'{"a": 1}'), _hd("8"))
        self.assertEqual(out, {"a": 1})

    def test_read_length_errors(self) -> None:
        """Map length problems to 411, 400, or 413."""
        with self.assertRaises(secure.BodyError) as cm:
            secure.read_json_body(_rf(b"{}"), {})
        self.assertEqual(cm.exception.status, 411)
        for raw in ("abc", "-5", "+5", "1e3", "", " 5", "-1", "1" + "0" * 400):
            with self.subTest(raw=raw):
                rf = _rf(b'{"a": 1}')
                pos = rf.tell()
                with self.assertRaises(secure.BodyError) as cm2:
                    secure.read_json_body(rf, _hd(raw))
                self.assertEqual(cm2.exception.status, 400)
                self.assertEqual(rf.tell(), pos)
        rf2 = _rf(b"{}")
        pos2 = rf2.tell()
        with self.assertRaises(secure.BodyError) as cm3:
            secure.read_json_body(rf2, _hd("5000"))
        self.assertEqual(cm3.exception.status, 413)
        self.assertEqual(rf2.tell(), pos2)

    def test_read_zero_length(self) -> None:
        """Reject empty body exactly as the helper does."""
        with self.assertRaises(secure.BodyError) as cm:
            secure.read_json_body(_rf(b""), _hd("0"))
        self.assertEqual(cm.exception.status, 400)

    def test_read_body_errors(self) -> None:
        """Reject chunked, short, and non-object bodies."""
        with self.assertRaises(secure.BodyError) as cm:
            secure.read_json_body(_rf(b"{}"), {"Content-Length": "2", "Transfer-Encoding": "chunked"})
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(secure.BodyError) as cm2:
            secure.read_json_body(_rf(b'{"a": 1}'), _hd("20"))
        self.assertEqual(cm2.exception.status, 400)
        for raw in (b"not json", b"\xff", b'{"a": NaN}', b"[1]", b"[" * 2048):
            with self.subTest(raw=raw):
                with self.assertRaises(secure.BodyError) as cm3:
                    secure.read_json_body(_rf(raw), _hd(str(len(raw))))
                self.assertEqual(cm3.exception.status, 400)

    def test_csp_headers(self) -> None:
        """Build CSP and hardening headers correctly."""
        policy = secure.csp("abc")
        self.assertIn("script-src 'nonce-abc'", policy)
        self.assertIn("default-src 'none'", policy)
        bare = secure.security_headers()
        self.assertNotIn("Content-Security-Policy", bare)
        with_csp = secure.security_headers("abc")
        self.assertIn("nonce-abc", with_csp["Content-Security-Policy"])
        for key in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Cross-Origin-Opener-Policy", "Cross-Origin-Resource-Policy", "Cache-Control", "Permissions-Policy"):
            with self.subTest(key=key):
                self.assertIn(key, with_csp)

    def test_add_nonce(self) -> None:
        """Tag script and style elements only."""
        out = secure.add_nonce("<script></script><style></style><p></p>", "n1")
        self.assertIn('<script nonce="n1">', out)
        self.assertIn('<style nonce="n1">', out)
        self.assertIn("<p>", out)
        self.assertNotIn('<p nonce="n1">', out)

    def test_limiter(self) -> None:
        """Enforce burst, refill, isolation, and key cap."""
        t = [0.0]
        lim = secure.RateLimiter(1.0, 3, clock=lambda: t[0])
        self.assertTrue(lim.allow("k"))
        self.assertTrue(lim.allow("k"))
        self.assertTrue(lim.allow("k"))
        self.assertFalse(lim.allow("k"))
        t[0] += 1.5
        self.assertTrue(lim.allow("k"))
        t2 = [0.0]
        lim2 = secure.RateLimiter(1.0, 2, clock=lambda: t2[0])
        self.assertTrue(lim2.allow("a"))
        self.assertTrue(lim2.allow("b"))
        self.assertTrue(lim2.allow("a"))
        t3 = [0.0]
        lim3 = secure.RateLimiter(1.0, 1, clock=lambda: t3[0])
        for i in range(70):
            with self.subTest(i=i):
                lim3.allow(f"k{i}")
        self.assertLessEqual(len(lim3._state), 64)

    def test_clean_text(self) -> None:
        """Strip bidi and controls but keep newline."""
        self.assertNotIn("‮", secure.clean_text("a‮b", 10))
        self.assertNotIn("\x00", secure.clean_text("a\x00b", 10))
        self.assertEqual(secure.clean_text("a\ud800b", 10), "ab")
        self.assertIn("\n", secure.clean_text("a\nb", 10))
        self.assertEqual(secure.clean_text("abcdef", 3), "abc")
        self.assertEqual(secure.clean_text("hi", 10), "hi")
        self.assertIn("😀", secure.clean_text("x😀y", 10))

    def test_mask_number(self) -> None:
        """Hide all but the last two characters."""
        self.assertEqual(secure.mask_number("+15550100"), "***00")
        self.assertEqual(secure.mask_number("5"), "***")
        self.assertEqual(secure.mask_number(None), "***")
        self.assertEqual(secure.mask_number(123), "***")

    def test_nonce_reject(self) -> None:
        """Reject nonces that could break out of CSP."""
        for bad in ('a"b', "a;b", "a'b", "", None, 123):
            with self.subTest(nonce=bad):
                with self.assertRaises(ValueError):
                    secure.csp(bad)
                with self.assertRaises(ValueError):
                    secure.add_nonce("<script></script>", bad)

    def test_limiter_full_denies(self) -> None:
        """Deny new keys when full instead of evicting."""
        t = [0.0]
        lim = secure.RateLimiter(1.0, 1, clock=lambda: t[0])
        for i in range(64):
            lim.allow(f"k{i}")
        self.assertFalse(lim.allow("new-key"))
        t[0] = 2.0
        self.assertTrue(lim.allow("k0"))
        self.assertFalse(lim.allow(None))
        self.assertFalse(lim.allow([]))
        self.assertFalse(lim.allow(""))

    def test_clean_text_types(self) -> None:
        """Fail closed on non-string input and bad length."""
        self.assertEqual(secure.clean_text(None, 10), "")
        self.assertEqual(secure.clean_text(123, 10), "")
        self.assertEqual(secure.clean_text("abc", 0), "")
        self.assertEqual(secure.clean_text("abc", -1), "")


if __name__ == "__main__":
    """Run the test suite."""
    unittest.main()
