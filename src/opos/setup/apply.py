# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply and uninstall orchestration for first-run wizard."""
from __future__ import annotations
import contextlib
import json
import os
import platform as _plat
import shlex
import stat
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Mapping
from opos.setup.config import config_dir, config_path, ensure_token, save, token_path, validate
from opos.setup.services import Runner, ServiceError, install_services, uninstall_services
STEP_IDS = ("config", "token", "key", "services", "health", "tools")
STEP_LABELS = {"config": "Save your preferences", "token": "Create the session token", "key": "Set aside your SSH key", "services": "Install the background service", "health": "Check that it is running", "tools": "Install AI coding tools"}
def _sanitize(detail: str) -> str:
    """Sanitize detail string."""
    return "".join(ch for ch in detail if ord(ch) >= 32 and ord(ch) != 127)[:300]
class Progress:
    """Thread-safe progress tracker."""
    def __init__(self) -> None:
        """Initialize progress."""
        self._lock = threading.Lock()
        self._steps: list[dict[str, str]] = [{"id": sid, "label": STEP_LABELS[sid], "status": "pending", "detail": ""} for sid in STEP_IDS]
        self._index: dict[str, dict[str, str]] = {s["id"]: s for s in self._steps}
    def _get(self, step_id: str) -> dict[str, str]:
        """Get step or raise."""
        if step_id not in self._index:
            raise KeyError(step_id)
        return self._index[step_id]
    def start(self, step_id: str) -> None:
        """Mark step running."""
        with self._lock:
            rec = self._get(step_id)
            rec["status"] = "running"
            rec["detail"] = ""
    def finish(self, step_id: str, detail: str = "") -> None:
        """Mark step ok."""
        clean = _sanitize(detail)
        with self._lock:
            rec = self._get(step_id)
            rec["status"] = "ok"
            rec["detail"] = clean
    def fail(self, step_id: str, detail: str) -> None:
        """Mark step failed."""
        clean = _sanitize(detail)
        with self._lock:
            rec = self._get(step_id)
            rec["status"] = "failed"
            rec["detail"] = clean
    def skip(self, step_id: str, detail: str = "") -> None:
        """Mark step skipped."""
        clean = _sanitize(detail)
        with self._lock:
            rec = self._get(step_id)
            rec["status"] = "skipped"
            rec["detail"] = clean
    def snapshot(self) -> list[dict[str, str]]:
        """Return copy."""
        with self._lock:
            return [dict(s) for s in self._steps]
    def done(self) -> bool:
        """Return if finished."""
        with self._lock:
            return all(s["status"] not in ("pending", "running") for s in self._steps)
    def ok(self) -> bool:
        """Return overall success."""
        with self._lock:
            if any(s["status"] in ("pending", "running") for s in self._steps):
                return False
            return all(s["status"] != "failed" for s in self._steps)
def http_health(port: int, timeout_s: float = 10.0, interval_s: float = 0.5, opener=None, token: str | None = None) -> tuple[bool, str]:
    """Poll health endpoint."""
    if opener is None:
        opener = urllib.request.urlopen
    url = f"http://127.0.0.1:{port}/api/state"
    request = urllib.request.Request(url, headers={"X-OPOS-Token": token}) if token is not None else url
    deadline = time.monotonic() + timeout_s
    while True:
        with contextlib.suppress(Exception):
            with opener(request, timeout=2) as resp:
                code = getattr(resp, "status", None)
                if code is None:
                    try:
                        code = resp.getcode()
                    except Exception:
                        code = None
                if code == 200:
                    raw = resp.read()
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        return True, f"responding on port {port}"
        if time.monotonic() >= deadline:
            break
        time.sleep(interval_s)
    return False, f"no answer on port {port} after {timeout_s:g} s"
def apply(cfg: dict, *, repo_root: Path, python: str, home: Path, platform: str, env: Mapping[str, str] | None = None, port: int = 8765, dry_run: bool = False, runner: Runner | None = None, health_check: Callable[[int], tuple[bool, str]] | None = None, progress: Progress | None = None, tools_runner: Callable | None = None) -> Progress:
    """Run setup steps."""
    prog = progress if progress is not None else Progress()
    failed = False
    token = None
    with contextlib.suppress(Exception):
        if failed:
            prog.skip("config", "skipped after an earlier failure")
        else:
            prog.start("config")
            try:
                errs = validate(cfg)
                if errs:
                    prog.fail("config", "; ".join(errs))
                    failed = True
                else:
                    cpath = config_path(env, platform)
                    if dry_run:
                        prog.finish("config", f"would write {cpath}")
                    else:
                        data = dict(cfg)
                        data["applied"] = False
                        save(data, cpath)
                        prog.finish("config", f"saved {cpath}")
            except Exception as exc:
                prog.fail("config", type(exc).__name__)
                failed = True
        if failed:
            prog.skip("token", "skipped after an earlier failure")
        else:
            prog.start("token")
            try:
                tpath = token_path(env, platform)
                if dry_run:
                    prog.finish("token", "would create the token file")
                else:
                    token = ensure_token(tpath)
                    prog.finish("token", "ready")
            except Exception as exc:
                prog.fail("token", type(exc).__name__)
                failed = True
        if failed:
            prog.skip("key", "skipped after an earlier failure")
        else:
            k_val = cfg.get("security", {}).get("ssh_public_key")
            if k_val is None:
                prog.skip("key", "no key given")
            else:
                prog.start("key")
                try:
                    if dry_run:
                        prog.finish("key", "would save the key file")
                    else:
                        cdir = config_dir(env, platform)
                        os.makedirs(cdir, mode=0o700, exist_ok=True)
                        with contextlib.suppress(Exception):
                            os.chmod(cdir, 0o700)
                        kpath = cdir / "authorized_key.pub"
                        tmp: Path | None = None
                        fd: int | None = None
                        try:
                            fd, tmp_name = tempfile.mkstemp(dir=cdir, prefix=".authorized_key.pub.tmp.")
                            tmp = Path(tmp_name)
                            with contextlib.suppress(Exception):
                                os.fchmod(fd, 0o600)
                            with os.fdopen(fd, "w", encoding="utf-8") as f:
                                fd = None
                                f.write(f"{k_val}\n")
                            os.replace(tmp, kpath)
                        except Exception:
                            if fd is not None:
                                with contextlib.suppress(Exception):
                                    os.close(fd)
                            if tmp is not None:
                                with contextlib.suppress(Exception):
                                    tmp.unlink(missing_ok=True)
                            raise
                        prog.finish("key", f"saved; to use it run: sudo deploy/harden-device.sh --apply --only ssh --add-key {shlex.quote(str(kpath))}")
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        if tmp is not None:  # type: ignore[possibly-undefined]
                            tmp.unlink(missing_ok=True)
                    prog.fail("key", type(exc).__name__)
                    failed = True
        if failed:
            prog.skip("services", "skipped after an earlier failure")
        else:
            prog.start("services")
            try:
                cdir = config_dir(env, platform)
                if not dry_run:
                    logs = cdir / "logs"
                    os.makedirs(logs, mode=0o700, exist_ok=True)
                    with contextlib.suppress(Exception):
                        os.chmod(logs, 0o700)
                lines = install_services(repo_root, python, cdir, home, platform, port=port, dry_run=dry_run, runner=runner)
                prog.finish("services", "; ".join(lines))
            except (ServiceError, ValueError, FileNotFoundError, OSError) as exc:
                prog.fail("services", str(exc))
                failed = True
            except Exception as exc:
                prog.fail("services", type(exc).__name__)
                failed = True
        if failed:
            prog.skip("health", "skipped after an earlier failure")
        else:
            if dry_run:
                prog.skip("health", "dry run")
            elif platform not in ("linux", "darwin"):
                prog.skip("health", f"no service on {platform}")
            else:
                prog.start("health")
                try:
                    if health_check is None:
                        ok_, msg = http_health(port, token=token)
                    else:
                        ok_, msg = health_check(port)
                    if ok_:
                        prog.finish("health", msg)
                    else:
                        prog.fail("health", msg)
                        failed = True
                except Exception as exc:
                    prog.fail("health", type(exc).__name__)
                    failed = True
        if failed:
            prog.skip("tools", "skipped after an earlier failure")
        else:
            _ai = cfg.get("ai_tools", {})
            if not isinstance(_ai, dict):
                _ai = {}
            _tids = _ai.get("tools")
            if not _tids:
                prog.skip("tools", "none chosen")
            else:
                prog.start("tools")
                try:
                    if tools_runner is None:
                        from opos.aitools.wizard import install_selected as tools_runner
                    _mode = _ai.get("mode", "lite")
                    _pm = _plat.machine()
                    _lines = tools_runner(_tids, _mode, env=env or {}, home=home, machine=_pm, platform=platform, python=python, src_dir=repo_root / "src", dry_run=dry_run)
                    prog.finish("tools", "; ".join(_lines))
                except Exception as exc:
                    if exc.__class__.__name__ == "InstallError":
                        prog.fail("tools", str(exc))
                    else:
                        prog.fail("tools", type(exc).__name__)
        if not dry_run:
            _snap = prog.snapshot()
            _ok = all(s["status"] not in ("pending", "running", "failed") for s in _snap if s["id"] != "tools")
            if _ok:
                with contextlib.suppress(Exception):
                    cpath = config_path(env, platform)
                    data = dict(cfg)
                    data["applied"] = True
                    save(data, cpath)
    return prog
def uninstall(*, home: Path, platform: str, env: Mapping[str, str] | None = None, dry_run: bool = False, runner: Runner | None = None, keep_config: bool = False, tools_uninstaller: Callable | None = None) -> list[str]:
    """Remove services and config."""
    actions: list[str] = []
    try:
        actions.extend(uninstall_services(home, platform, dry_run=dry_run, runner=runner))
    except ServiceError as exc:
        for prior in getattr(exc, "actions", []) or []:
            if prior not in actions:
                actions.append(prior)
        msg = str(exc).strip()
        actions.append(f"service removal failed: {msg}" if msg else "service removal failed: ServiceError")
        raise ServiceError(actions[-1], actions=list(actions)) from exc
    except Exception as exc:  # pragma: no cover - unexpected runner failure
        actions.append(f"service removal failed: {type(exc).__name__}")
        raise ServiceError(actions[-1], actions=list(actions)) from exc
    if tools_uninstaller is None:
        try:
            from opos.aitools.wizard import uninstall_all as tools_uninstaller
        except Exception:
            tools_uninstaller = None  # type: ignore[assignment]
    if tools_uninstaller is not None:
        try:
            actions.extend(tools_uninstaller(env=env or {}, home=home, dry_run=dry_run))
        except Exception as exc:
            actions.append(f"could not remove AI tools: {type(exc).__name__}")
    if keep_config:
        return actions
    # Preserve configuration if service removal failed so a retry can succeed;
    # callers that want to keep config explicitly already returned above.
    # The service-removal path raises on failure and never reaches here.
    base = config_dir(env, platform)
    if os.path.islink(base):
        return actions
    candidates = [base / "config.json", base / "session.token", base / "authorized_key.pub", base / "drain-calibration.json", base / "logs"]
    for p in candidates:
        if not os.path.lexists(p):
            continue
        if dry_run:
            actions.append(f"would remove {p}")
            continue
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode):
            with contextlib.suppress(Exception):
                os.unlink(p)
                actions.append(f"removed {p}")
        elif stat.S_ISDIR(st.st_mode):
            for entry in os.scandir(p):
                try:
                    cst = os.lstat(entry.path)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(cst.st_mode):
                    child = Path(entry.path)
                    stack: list[Path] = [child]
                    while stack:
                        cur = stack.pop()
                        with contextlib.suppress(Exception):
                            for e2 in os.scandir(cur):
                                try:
                                    s2 = os.lstat(e2.path)
                                except FileNotFoundError:
                                    continue
                                if stat.S_ISDIR(s2.st_mode):
                                    stack.append(Path(e2.path))
                                else:
                                    with contextlib.suppress(Exception):
                                        os.unlink(e2.path)
                                        actions.append(f"removed {Path(e2.path)}")
                            os.rmdir(cur)
                            actions.append(f"removed {cur}")
                else:
                    with contextlib.suppress(Exception):
                        os.unlink(entry.path)
                        actions.append(f"removed {Path(entry.path)}")
            with contextlib.suppress(Exception):
                os.rmdir(p)
                actions.append(f"removed {p}")
        else:
            with contextlib.suppress(Exception):
                os.unlink(p)
                actions.append(f"removed {p}")
    if dry_run:
        with contextlib.suppress(Exception):
            if os.path.lexists(base):
                try:
                    entries = list(os.scandir(base))
                except Exception:
                    entries = []
                remain = 0
                for e in entries:
                    is_candidate = any(str(c) == e.path for c in candidates if os.path.lexists(c))
                    if not is_candidate:
                        remain += 1
                if entries and remain == 0:
                    actions.append(f"would remove {base}")
                elif not entries:
                    actions.append(f"would remove {base}")
    else:
        with contextlib.suppress(Exception):
            os.rmdir(base)
            actions.append(f"removed {base}")
    return actions
