# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
from dataclasses import dataclass
from pathlib import Path

class CatalogError(ValueError):
    """Invalid catalog data."""

@dataclass(frozen=True)
class Tool:
    """Tool definition from catalog."""
    id: str
    name: str
    binary: str
    installer_url: str
    interpreter: str
    installer_args: tuple[str, ...]
    installer_env: dict[str, str]
    archs: tuple[str, ...]
    min_free_mb: int
    state_subdir: str
    run_env: dict[str, str]
    lite_env: dict[str, str]
    signin: str
    docs_url: str

_ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_BIN_RE = re.compile(r"^[a-z0-9._-]{1,32}$")
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_WS_RE = re.compile(r"\s")
_ARCHS_ALLOWED = {"aarch64", "x86_64"}
_TOOL_KEYS = frozenset({"id", "name", "binary", "installer_url", "interpreter", "installer_args", "installer_env", "archs", "min_free_mb", "state_subdir", "run_env", "lite_env", "signin", "docs_url"})
_TOP_KEYS = frozenset({"version", "tools"})
_FORBIDDEN_ENV = ("KEY", "TOKEN", "SECRET", "PASSWORD")

def _check_url(s: str) -> None:
    """Validate https url without whitespace."""
    if not isinstance(s, str):
        raise CatalogError("url not str")
    if not s.startswith("https://"):
        raise CatalogError("url must start with https://")
    if _WS_RE.search(s):
        raise CatalogError("url has whitespace")

def _check_env(d: object) -> None:
    """Validate env mapping."""
    if not isinstance(d, dict):
        raise CatalogError("env not dict")
    for k, v in d.items():
        if not isinstance(k, str) or not _ENV_KEY_RE.fullmatch(k):
            raise CatalogError(f"bad env key: {k!r}")
        for bad in _FORBIDDEN_ENV:
            if bad in k:
                raise CatalogError(f"env key forbidden: {k!r}")
        if not isinstance(v, str):
            raise CatalogError(f"bad env value for {k!r}")
        if "\n" in v or "\r" in v or "\x00" in v:
            raise CatalogError(f"bad env value for {k!r}")

def default_path(repo_root: Path) -> Path:
    """Return default catalog path."""
    return repo_root / "deploy" / "ai-tools.json"

def load(path: Path) -> tuple[Tool, ...]:
    """Load and validate catalog."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CatalogError(str(e)) from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CatalogError(str(e)) from e
    if not isinstance(data, dict):
        raise CatalogError("top level not dict")
    if set(data.keys()) != _TOP_KEYS:
        raise CatalogError("missing or unknown top-level keys")
    ver = data["version"]
    if type(ver) is not int or ver != 1:
        raise CatalogError("bad version")
    raw_tools = data["tools"]
    if not isinstance(raw_tools, list):
        raise CatalogError("tools not list")
    if len(raw_tools) == 0:
        raise CatalogError("empty tools list")
    out: list[Tool] = []
    seen: set[str] = set()
    seen_bin: set[str] = set()
    for entry in raw_tools:
        if not isinstance(entry, dict):
            raise CatalogError("tool entry not dict")
        if set(entry.keys()) != _TOOL_KEYS:
            raise CatalogError("missing or unknown tool keys")
        tid = entry["id"]
        if not isinstance(tid, str) or not _ID_RE.fullmatch(tid):
            raise CatalogError(f"bad id: {tid!r}")
        if tid in seen:
            raise CatalogError(f"duplicate id: {tid!r}")
        seen.add(tid)
        name = entry["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 300 or "\n" in name or "\r" in name or "\x00" in name:
            raise CatalogError(f"bad name: {name!r}")
        binary = entry["binary"]
        if not isinstance(binary, str) or not _BIN_RE.fullmatch(binary) or binary.startswith("."):
            raise CatalogError(f"bad binary: {binary!r}")
        if binary in seen_bin:
            raise CatalogError(f"duplicate binary: {binary!r}")
        seen_bin.add(binary)
        installer_url = entry["installer_url"]
        _check_url(installer_url)
        interpreter = entry["interpreter"]
        if interpreter not in ("sh", "bash"):
            raise CatalogError(f"bad interpreter: {interpreter!r}")
        installer_args = entry["installer_args"]
        if not isinstance(installer_args, list) or len(installer_args) > 8:
            raise CatalogError("bad installer_args")
        for a in installer_args:
            if not isinstance(a, str) or "\n" in a or "\r" in a:
                raise CatalogError(f"bad installer_arg: {a!r}")
        installer_env = entry["installer_env"]
        _check_env(installer_env)
        archs = entry["archs"]
        if not isinstance(archs, list) or len(archs) == 0:
            raise CatalogError("bad archs")
        if len(set(archs)) != len(archs):
            raise CatalogError("duplicate archs")
        for a in archs:
            if a not in _ARCHS_ALLOWED:
                raise CatalogError(f"bad arch: {a!r}")
        min_free = entry["min_free_mb"]
        if type(min_free) is not int or not 1 <= min_free <= 100000:
            raise CatalogError(f"bad min_free_mb: {min_free!r}")
        state_subdir = entry["state_subdir"]
        if not isinstance(state_subdir, str) or not 1 <= len(state_subdir) <= 64:
            raise CatalogError(f"bad state_subdir: {state_subdir!r}")
        if state_subdir.startswith("/") or state_subdir.startswith("\\"):
            raise CatalogError(f"bad state_subdir: {state_subdir!r}")
        if "\n" in state_subdir or "\r" in state_subdir or "\x00" in state_subdir:
            raise CatalogError(f"bad state_subdir: {state_subdir!r}")
        if ".." in state_subdir.split("/"):
            raise CatalogError(f"bad state_subdir: {state_subdir!r}")
        run_env = entry["run_env"]
        _check_env(run_env)
        lite_env = entry["lite_env"]
        _check_env(lite_env)
        signin = entry["signin"]
        if not isinstance(signin, str) or not 1 <= len(signin) <= 300 or "\n" in signin or "\r" in signin:
            raise CatalogError(f"bad signin: {signin!r}")
        docs_url = entry["docs_url"]
        _check_url(docs_url)
        out.append(Tool(id=tid, name=name, binary=binary, installer_url=installer_url, interpreter=interpreter, installer_args=tuple(installer_args), installer_env=dict(installer_env), archs=tuple(archs), min_free_mb=min_free, state_subdir=state_subdir, run_env=dict(run_env), lite_env=dict(lite_env), signin=signin, docs_url=docs_url))
    return tuple(out)

def get(tools: tuple[Tool, ...], tool_id: str) -> Tool:
    """Return tool by id."""
    for t in tools:
        if t.id == tool_id:
            return t
    raise CatalogError(f"unknown tool: {tool_id}")

def public_view(tools: tuple[Tool, ...]) -> list[dict]:
    """Return wizard-safe view."""
    return [{"id": t.id, "name": t.name, "min_free_mb": t.min_free_mb, "archs": list(t.archs), "signin": t.signin, "docs_url": t.docs_url} for t in tools]
