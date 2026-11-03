# SPDX-License-Identifier: GPL-3.0-or-later
"""Fast install-time self-test for Old Phone OS Interface."""
from __future__ import annotations
import contextlib
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

Check = tuple[str, bool, str]

def _fmt(e: Exception) -> str:
    """Format failure detail without leaking paths."""
    if isinstance(e, AssertionError):
        m = str(e)
        return f"{type(e).__name__}: {m}" if m else type(e).__name__
    return type(e).__name__

def _get(host: str, port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    """Perform HTTP GET."""
    c = http.client.HTTPConnection(host, port, timeout=2)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    d = r.read()
    s = r.status
    c.close()
    return s, d

def _serve(srv: Any) -> threading.Thread:
    """Serve server in background."""
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.15)
    return t

def run_checks(repo_root: Path | None = None) -> list[Check]:
    """Run all self-tests."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    out: list[Check] = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        try:
            v = sys.version_info
            ok = v >= (3, 11)
            det = f"{v.major}.{v.minor}.{v.micro}"
            out.append(("python", ok, det))
        except Exception as e:
            out.append(("python", False, _fmt(e)))
        try:
            import opos.web  # noqa: F401
            import opos.secure  # noqa: F401
            import opos.setup.config  # noqa: F401
            import opos.setup.device  # noqa: F401
            import opos.setup.services  # noqa: F401
            import opos.setup.apply  # noqa: F401
            import opos.setup.server  # noqa: F401
            import opos.setup.wizard_page  # noqa: F401
            out.append(("imports", True, "ok"))
        except Exception as e:
            out.append(("imports", False, _fmt(e)))
        try:
            from opos.setup.config import default_config, load, save
            cfg = default_config()
            p = tdp / "cfg.json"
            save(cfg, p)
            loaded = load(p)
            if loaded != cfg:
                raise AssertionError("config round-trip mismatch")
            if os.name == "posix" and (p.stat().st_mode & 0o777) != 0o600:
                raise AssertionError("config permissions not 0o600")
            out.append(("config", True, "ok"))
        except Exception as e:
            out.append(("config", False, _fmt(e)))
        try:
            from opos.setup.config import ensure_token
            pp = tdp / "tok"
            a = ensure_token(pp)
            b = ensure_token(pp)
            if a != b or len(a) < 32:
                raise AssertionError("token mismatch or too short")
            out.append(("token", True, str(len(a))))
        except Exception as e:
            out.append(("token", False, _fmt(e)))
        try:
            from opos.setup.device import detect_device
            d = detect_device("/", config_dir=tdp)  # type: ignore[call-arg]
            if not isinstance(d, dict) or not all(k in d for k in ("id", "name", "source")):
                raise AssertionError("device missing required keys")
            out.append(("device", True, str(d.get("id", ""))))
        except Exception as e:
            out.append(("device", False, _fmt(e)))
        try:
            from opos.setup.services import read_unit_template, render_launchd_plist, render_user_unit
            tpl = read_unit_template(repo_root)
            unit = render_user_unit(tpl, repo_root, sys.executable, tdp, port=8765)
            _ = render_launchd_plist(repo_root, sys.executable, tdp, port=8765)
            if "NoNewPrivileges=yes" not in unit or "token-file" not in unit:
                raise AssertionError("services unit missing required entries")
            out.append(("services", True, "ok"))
        except Exception as e:
            out.append(("services", False, _fmt(e)))
        try:
            from opos.setup.server import SetupContext, make_server
            ctx = SetupContext(repo_root=repo_root, python=sys.executable, home=tdp, platform=sys.platform, env={"OPOS_CONFIG_DIR": str(tdp)}, dry_run=True)
            srv = make_server(0, ctx, host="127.0.0.1")
            _serve(srv)
            port = srv.server_address[1]
            s1, _ = _get("127.0.0.1", port, "/", headers={"Host": f"127.0.0.1:{port}"})
            if s1 != 200:
                raise AssertionError(f"wizard root status {s1} != 200")
            s2, body = _get("127.0.0.1", port, "/setup/api/state", headers={"Host": f"127.0.0.1:{port}", "X-OPOS-Token": srv.token})
            if s2 != 200:
                raise AssertionError(f"wizard state status {s2} != 200")
            if "device" not in json.loads(body):
                raise AssertionError("wizard state missing device")
            srv.shutdown()
            srv.server_close()
            out.append(("wizard", True, f"127.0.0.1:{port}"))
        except Exception as e:
            with contextlib.suppress(Exception):
                srv.shutdown()  # type: ignore[possibly-undefined]
                srv.server_close()  # type: ignore[possibly-undefined]
            out.append(("wizard", False, _fmt(e)))
        try:
            from opos.setup.config import ensure_token as et2
            from opos.web import Phone, make_server as make_app
            tok = et2(tdp / "app-token")
            srv2 = make_app(0, Phone(), host="127.0.0.1", token=tok)
            _serve(srv2)
            port2 = srv2.server_address[1]
            s, _ = _get("127.0.0.1", port2, "/api/state", headers={"Host": f"127.0.0.1:{port2}", "X-OPOS-Token": tok})
            if s != 200:
                raise AssertionError(f"app status {s} != 200")
            if len(tok) < 43:
                raise AssertionError(f"app token length {len(tok)} < 43")
            srv2.shutdown()
            srv2.server_close()
            out.append(("app", True, f"127.0.0.1:{port2}"))
        except Exception as e:
            with contextlib.suppress(Exception):
                srv2.shutdown()  # type: ignore[possibly-undefined]
                srv2.server_close()  # type: ignore[possibly-undefined]
            out.append(("app", False, _fmt(e)))
    return out

def main(argv: list[str] | None = None) -> int:
    """Run CLI."""
    cs = run_checks()
    ok = True
    for n, p, d in cs:
        print(f"{'ok' if p else 'FAIL':4}  {n}  {d}")
        if not p:
            ok = False
    print("self-test passed" if ok else "self-test FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
