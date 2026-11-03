# SPDX-License-Identifier: GPL-3.0-or-later
import json
import tempfile
import unittest
from pathlib import Path
from opos.aitools import catalog
from opos.aitools.catalog import CatalogError

def _tool(**over) -> dict:
    """Base valid tool dict."""
    base = {"id": "test-tool", "name": "Test Tool", "binary": "testbin", "installer_url": "https://example.com/install.sh", "interpreter": "sh", "installer_args": [], "installer_env": {}, "archs": ["aarch64"], "min_free_mb": 100, "state_subdir": ".test", "run_env": {}, "lite_env": {}, "signin": "Run testbin and sign in.", "docs_url": "https://example.com/docs"}
    base.update(over)
    return base

def _catalog(tools) -> dict:
    """Wrap tools in top level."""
    return {"version": 1, "tools": tools}

def _load(data) -> tuple:
    """Write data as json and load."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return catalog.load(p)

def _load_text(text: str):
    """Write raw text and load."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.json"
        p.write_text(text, encoding="utf-8")
        return catalog.load(p)

class TestDefaultPath(unittest.TestCase):
    """Default path resolution."""
    def test_default_path(self):
        """Returns repo_root/deploy/ai-tools.json."""
        r = Path("/home/u/repo")
        self.assertEqual(catalog.default_path(r), r / "deploy" / "ai-tools.json")

class TestLoadSuccess(unittest.TestCase):
    """Valid loads."""
    def test_load_minimal(self):
        """Valid file returns tools."""
        t = _load(_catalog([_tool()]))
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].id, "test-tool")
        self.assertEqual(t[0].binary, "testbin")

    def test_load_preserves_types(self):
        """Tuples and dicts are copied."""
        tool = _tool(installer_args=["a", "b"], installer_env={"FOO": "1"}, archs=["aarch64", "x86_64"], run_env={"BAR": "2"})
        loaded = _load(_catalog([tool]))[0]
        self.assertEqual(loaded.installer_args, ("a", "b"))
        self.assertEqual(loaded.installer_env, {"FOO": "1"})
        self.assertEqual(loaded.archs, ("aarch64", "x86_64"))
        self.assertEqual(loaded.run_env, {"BAR": "2"})

    def test_tool_frozen(self):
        """Tool is frozen."""
        t = _load(_catalog([_tool()]))[0]
        with self.assertRaises(AttributeError):
            t.id = "x"  # type: ignore

    def test_load_two_tools(self):
        """Multiple ids load."""
        d = _catalog([_tool(id="a"), _tool(id="b", binary="b2")])
        out = _load(d)
        self.assertEqual({x.id for x in out}, {"a", "b"})

class TestLoadFailures(unittest.TestCase):
    """Error paths."""
    def test_missing_file(self):
        """Missing file raises CatalogError."""
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(CatalogError):
                catalog.load(Path(td) / "no.json")

    def test_bad_json(self):
        """Invalid json raises."""
        with self.assertRaises(CatalogError):
            _load_text("{bad")

    def test_top_not_dict(self):
        """Top level not dict raises."""
        with self.assertRaises(CatalogError):
            _load_text("[]")

    def test_top_missing_keys(self):
        """Missing top keys raises."""
        with self.assertRaises(CatalogError):
            _load({"version": 1})
        with self.assertRaises(CatalogError):
            _load({"tools": []})

    def test_top_unknown_keys(self):
        """Unknown top keys raises."""
        with self.assertRaises(CatalogError):
            _load({"version": 1, "tools": [_tool()], "extra": 1})

    def test_bad_version(self):
        """Bad version raises."""
        for v in [0, 2, "1", 1.0, True, None]:
            with self.subTest(v=v):
                with self.assertRaises(CatalogError):
                    _load({"version": v, "tools": [_tool()]})

    def test_tools_not_list(self):
        """Tools not list raises."""
        with self.assertRaises(CatalogError):
            _load({"version": 1, "tools": {}})
        with self.assertRaises(CatalogError):
            _load({"version": 1, "tools": "x"})

    def test_empty_tools(self):
        """Empty list raises."""
        with self.assertRaises(CatalogError):
            _load({"version": 1, "tools": []})

    def test_tool_not_dict(self):
        """Tool entry not dict raises."""
        with self.assertRaises(CatalogError):
            _load({"version": 1, "tools": ["x"]})

    def test_tool_missing_keys(self):
        """Missing tool keys raises."""
        d = _tool()
        del d["binary"]
        with self.assertRaises(CatalogError):
            _load(_catalog([d]))

    def test_tool_unknown_keys(self):
        """Unknown tool keys raises."""
        d = _tool()
        d["extra"] = "1"
        with self.assertRaises(CatalogError):
            _load(_catalog([d]))

    def test_bad_id(self):
        """Invalid ids rejected."""
        bad = ["", "A", "a_b", "a b", "a/b", "a" * 33, "UPPER", "a." ]
        for bid in bad:
            with self.subTest(bid=bid):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(id=bid)]))

    def test_duplicate_id(self):
        """Duplicate ids rejected."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(id="a"), _tool(id="a")]))

    def test_duplicate_binary(self):
        """Duplicate binaries rejected."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(id="a", binary="same"), _tool(id="b", binary="same")]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(id="a", binary="testbin"), _tool(id="b", binary="testbin")]))

    def test_bad_name(self):
        """Invalid names rejected."""
        cases = ["", "a" * 301, "a\nb", "a\rb", "a\x00b", "a\n"]
        for n in cases:
            with self.subTest(n=repr(n)):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(name=n)]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(name=123)]))  # type: ignore

    def test_bad_binary(self):
        """Invalid binaries rejected."""
        cases = ["", ".hidden", "Bad", "a/b", "a b", "a" * 33, ".a"]
        for b in cases:
            with self.subTest(b=b):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(binary=b)]))

    def test_bad_installer_url(self):
        """Installer url must be https without whitespace."""
        bad = ["http://example.com/x", "https://example.com/a b", "https://example.com/a\nb", "", "ftp://x", 123]
        for u in bad:
            with self.subTest(u=repr(u)):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(installer_url=u)]))  # type: ignore

    def test_bad_docs_url(self):
        """Docs url validated same as installer_url."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(docs_url="http://example.com")]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(docs_url="https://a b")]))

    def test_bad_interpreter(self):
        """Only sh bash allowed."""
        for v in ["zsh", "SH", "", "bash ", "sh\n"]:
            with self.subTest(v=v):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(interpreter=v)]))

    def test_bad_installer_args(self):
        """Installer args validated."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_args="x")]))  # type: ignore
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_args=["a"] * 9)]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_args=["ok", "bad\n"])]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_args=["bad\rb"])]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_args=[123])]))  # type: ignore

    def test_bad_installer_env(self):
        """Installer env keys and values validated."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env="x")]))  # type: ignore
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env={"bad-key": "1"})]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env={"lower": "1"})]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env={"": "1"})]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env={"FOO": "a\nb"})]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env={"FOO": "a\x00b"})]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env={"FOO": 123})]))  # type: ignore
        for k in ["API_KEY", "MY_TOKEN", "SECRET_X", "PASSWORD_HASH", "FOO_KEY_BAR"]:
            with self.subTest(k=k):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(installer_env={k: "1"})]))

    def test_bad_archs(self):
        """Archs must be non-empty unique subset."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(archs=[])]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(archs="aarch64")]))  # type: ignore
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(archs=["aarch64", "aarch64"])]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(archs=["arm"])]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(archs=["aarch64", "bad"])]))

    def test_bad_min_free(self):
        """min_free_mb int 1..100000 not bool."""
        for v in [0, 100001, -1, True, False, 1.5, "100", None]:
            with self.subTest(v=repr(v)):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(min_free_mb=v)]))  # type: ignore

    def test_bad_state_subdir(self):
        """State subdir relative no .. no leading slash."""
        bad = ["", "/abs", "\\win", "a/../b", "../a", "a/..", "..", "a" * 65, "a\nb", "a\rb", "a\x00b"]
        for s in bad:
            with self.subTest(s=repr(s)):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(state_subdir=s)]))
        self.assertIsNotNone(_load(_catalog([_tool(state_subdir="a..b")])))
        self.assertIsNotNone(_load(_catalog([_tool(state_subdir="a/b_c-d.e")])))

    def test_bad_run_env(self):
        """run_env same validation as installer_env."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(run_env={"API_SECRET": "1"})]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(run_env={"foo": "1"})]))

    def test_bad_lite_env(self):
        """lite_env same validation."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(lite_env={"TOKEN": "1"})]))

    def test_bad_signin(self):
        """Signin single line 1..300."""
        for s in ["", "a" * 301, "a\nb", "a\rb"]:
            with self.subTest(s=repr(s[:10])):
                with self.assertRaises(CatalogError):
                    _load(_catalog([_tool(signin=s)]))

    def test_env_key_pattern(self):
        """Env keys must match [A-Z][A-Z0-9_]{0,63}."""
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env={"1ABC": "1"})]))
        with self.assertRaises(CatalogError):
            _load(_catalog([_tool(installer_env={"A" * 65: "1"})]))
        ok = _load(_catalog([_tool(installer_env={"A": "1", "A1_B2": "x"})]))
        self.assertEqual(ok[0].installer_env["A"], "1")

class TestGetAndView(unittest.TestCase):
    """get and public_view."""
    def test_get_found(self):
        """Get returns correct tool."""
        tools = _load(_catalog([_tool(id="alpha", name="Alpha"), _tool(id="beta", binary="b2")]))
        self.assertEqual(catalog.get(tools, "beta").binary, "b2")

    def test_get_unknown(self):
        """Unknown raises with message."""
        tools = _load(_catalog([_tool()]))
        with self.assertRaises(CatalogError) as cm:
            catalog.get(tools, "nope")
        self.assertIn("unknown tool", str(cm.exception))

    def test_public_view(self):
        """Only allowed keys exposed."""
        tools = _load(_catalog([_tool()]))
        view = catalog.public_view(tools)
        self.assertEqual(len(view), 1)
        self.assertEqual(set(view[0].keys()), {"id", "name", "min_free_mb", "archs", "signin", "docs_url"})
        self.assertIsInstance(view[0]["archs"], list)
        self.assertNotIn("installer_url", view[0])
        self.assertNotIn("binary", view[0])

    def test_public_view_archs_copy(self):
        """Archs list is copy."""
        tools = _load(_catalog([_tool(archs=["x86_64"])]))
        v = catalog.public_view(tools)
        v[0]["archs"].append("aarch64")
        self.assertEqual(tools[0].archs, ("x86_64",))

class TestRealCatalog(unittest.TestCase):
    """Real deploy file."""
    def test_real_catalog_valid(self):
        """Real file is valid and meets invariants."""
        p = Path(__file__).resolve().parents[1] / "deploy" / "ai-tools.json"
        tools = catalog.load(p)
        self.assertGreater(len(tools), 0)
        ids = [t.id for t in tools]
        self.assertEqual(len(ids), len(set(ids)))
        bins = [t.binary for t in tools]
        self.assertEqual(len(bins), len(set(bins)))
        self.assertEqual(ids, ["muse-code"])
        for t in tools:
            self.assertTrue(t.installer_url.startswith("https://"))
            self.assertNotIn(" ", t.installer_url)
            self.assertNotIn("\n", t.installer_url)
            self.assertTrue(t.docs_url.startswith("https://"))
            self.assertNotIn(" ", t.docs_url)
            self.assertGreater(len(t.signin), 0)
            self.assertLessEqual(len(t.signin), 300)
            self.assertNotIn("\n", t.signin)
            self.assertNotIn("\r", t.signin)
            for env in (t.installer_env, t.run_env, t.lite_env):
                for k in env:
                    for bad in ("KEY", "TOKEN", "SECRET", "PASSWORD"):
                        self.assertNotIn(bad, k)

    def test_real_catalog_unique_binaries(self):
        """Real catalog has unique binaries."""
        p = Path(__file__).resolve().parents[1] / "deploy" / "ai-tools.json"
        tools = catalog.load(p)
        bins = [t.binary for t in tools]
        self.assertEqual(len(bins), len(set(bins)))
