# SPDX-License-Identifier: GPL-3.0-or-later
"""AST guard against unsafe calls and log content leaks."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "opos"
UNSAFE_IMPORTS = {"pickle", "marshal", "shelve", "yaml"}
UNSAFE_BUILTINS = {"eval", "exec", "__import__"}
SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
OS_FUNCS = {"system", "popen"}
UNVERIFIED = {"_create_unverified_context", "CERT_NONE"}
LOG_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
SENSITIVE = {"text", "message", "body", "number"}


def _py_files() -> list[pathlib.Path]:
    """List Python files under src/opos."""
    if not SRC.is_dir():
        return []
    return sorted(SRC.rglob("*.py"))


def _has_shell_true(call: ast.Call) -> bool:
    """Fail closed on estrell values and unpacked kwargs."""
    for kw in call.keywords:
        if kw.arg is None:
            return True
        if kw.arg == "shell":
            val = kw.value
            if not (isinstance(val, ast.Constant) and val.value is False):
                return True
    return False


def _mentions_unverified(node: ast.AST) -> bool:
    """Check for unverified SSL names in a node."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in UNVERIFIED:
            return True
        if isinstance(child, ast.Attribute) and child.attr in UNVERIFIED:
            return True
    return False


def _uses_sensitive_arg(call: ast.Call) -> bool:
    """Check for sensitive identifiers inside call arguments."""
    for arg in list(call.args) + [k.value for k in call.keywords]:
        for child in ast.walk(arg):
            if isinstance(child, ast.Name) and child.id in SENSITIVE:
                return True
            if isinstance(child, ast.Attribute) and child.attr in SENSITIVE:
                return True
            if isinstance(child, ast.Constant) and child.value in SENSITIVE:
                return True
    return False


def _is_log_call(func: ast.AST) -> bool:
    """Check for print or logging level calls."""
    if isinstance(func, ast.Name) and func.id == "print":
        return True
    if isinstance(func, ast.Attribute) and func.attr in LOG_LEVELS:
        return True
    return False


def _subprocess_names(tree: ast.AST) -> set[str]:
    """Names bound to subprocess launchers via from-import."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_FUNCS:
                    names.add(alias.asname or alias.name)
    return names


def _is_subprocess_call(func: ast.AST, extra: set[str] | None = None) -> bool:
    """Check for subprocess launcher names."""
    if isinstance(func, ast.Name) and func.id in SUBPROCESS_FUNCS:
        return True
    if extra and isinstance(func, ast.Name) and func.id in extra:
        return True
    if isinstance(func, ast.Attribute) and func.attr in SUBPROCESS_FUNCS:
        return True
    return False


def _is_builtins_call(func: ast.AST) -> bool:
    """Check for builtins attribute eval and exec."""
    if isinstance(func, ast.Attribute) and func.attr in UNSAFE_BUILTINS:
        base = func.value
        if isinstance(base, ast.Name) and base.id in {"builtins", "__builtins__"}:
            return True
    return False


def _os_names(tree: ast.AST) -> set[str]:
    """Names bound to the os module via imports."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or alias.name.startswith("os."):
                    names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and (node.module or "") == "os":
            for alias in node.names:
                if alias.name in OS_FUNCS:
                    names.add(alias.asname or alias.name)
    names.add("os")
    return names


def _is_os_call(func: ast.AST, os_names: set[str]) -> bool:
    """Check for os.system or os.popen calls."""
    if isinstance(func, ast.Attribute) and func.attr in OS_FUNCS:
        base = func.value
        if isinstance(base, ast.Name) and base.id in os_names:
            return True
    if isinstance(func, ast.Name) and func.id in os_names and func.id != "os":
        return True
    return False


def _re_compile_names(tree: ast.AST) -> set[str]:
    """Names bound to re.compile via from-re import."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "re":
            for alias in node.names:
                if alias.name == "compile":
                    names.add(alias.asname or alias.name)
    return names


def _check_tree(tree: ast.AST, rel: str) -> list[str]:
    """Collect unsafe-code findings from one AST."""
    found: list[str] = []
    allowed = _re_compile_names(tree)
    os_names = _os_names(tree)
    sp_names = _subprocess_names(tree)
    for node in ast.walk(tree):
        if _mentions_unverified(node):
            found.append(f"{rel}:{getattr(node, 'lineno', 0)} forbidden unverified context")
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in UNSAFE_IMPORTS:
                    found.append(f"{rel}:{node.lineno} forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in UNSAFE_IMPORTS:
                found.append(f"{rel}:{node.lineno} forbidden import {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in UNSAFE_BUILTINS:
                found.append(f"{rel}:{node.lineno} forbidden call {func.id}")
            elif isinstance(func, ast.Name) and func.id == "compile":
                if func.id not in allowed:
                    found.append(f"{rel}:{node.lineno} forbidden call {func.id}")
            elif isinstance(func, ast.Attribute) and func.attr == "compile":
                base = func.value
                if not (isinstance(base, ast.Name) and base.id == "re"):
                    found.append(f"{rel}:{node.lineno} forbidden call {func.attr}")
            elif _is_builtins_call(func):
                found.append(f"{rel}:{node.lineno} forbidden call {func.attr}")
            elif _is_os_call(func, os_names):
                found.append(f"{rel}:{node.lineno} forbidden call os.{getattr(func, 'attr', func)}")
            elif _is_subprocess_call(func, sp_names) and _has_shell_true(node):
                found.append(f"{rel}:{node.lineno} forbidden shell=True")
    return found


def _check_log_tree(tree: ast.AST, rel: str) -> list[str]:
    """Collect log content leak findings from one AST."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_log_call(node.func):
            if _uses_sensitive_arg(node):
                found.append(f"{rel}:{node.lineno} forbidden log of content")
    return found


def _scan(check: object) -> list[str]:
    """Parse every file and apply one check function."""
    from collections.abc import Callable as _Cb
    found: list[str] = []
    paths = _py_files()
    if not paths:
        return ["src/opos:0 no Python files scanned"]
    for path in paths:
        rel = str(path.relative_to(ROOT))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError, ValueError):
            found.append(f"{rel}:0 unparsable file")
            continue
        typed: _Cb[[ast.AST, str], list[str]] = check  # type: ignore[assignment]
        found.extend(typed(tree, rel))
    return sorted(found)


class NoUnsafeCodeTest(unittest.TestCase):
    """Fail on unsafe AST patterns under src/opos."""

    def test_no_unsafe_code(self) -> None:
        """Fail if unsafe calls or imports exist."""
        found = _scan(_check_tree)
        self.assertEqual([], found, f"unsafe code found: {found}")

    def test_no_log_content(self) -> None:
        """Fail if content identifiers reach log calls."""
        found = _scan(_check_log_tree)
        self.assertEqual([], found, f"log content found: {found}")

    def test_scanner_catches_bypasses(self) -> None:
        """Check scanner closes alias and fail-open gaps."""
        cases = [
            "import subprocess as sp\nsp.run(cmd, shell=flag)\n",
            "import os as o\no.system(cmd)\n",
            "from os import system\nsystem(cmd)\n",
            "import ssl\nctx = ssl.CERT_NONE\n",
            "from os import system as s\ns(cmd)\n",
            "from subprocess import run as r\nr(cmd, shell=True)\n",
            "run(cmd, **opts)\n",
            "import builtins\nbuiltins.eval(cmd)\n",
            "import logging\nlogging.info(store[group])\n".replace("group", "message"),
        ]
        for src in cases:
            tree = ast.parse(src)
            hits = _check_tree(tree, "x") + _check_log_tree(tree, "x")
            self.assertTrue(hits, f"missed bypass: {src!r}")

    def test_scanner_allows_safe_patterns(self) -> None:
        """Check scanner stays silent on safe code."""
        tree = ast.parse("import re\nre.compile('a')\n")
        self.assertEqual([], _check_tree(tree, "x"))
        tree = ast.parse("import logging\nlogging.info('done')\n")
        self.assertEqual([], _check_log_tree(tree, "x"))


if __name__ == "__main__":
    unittest.main()
