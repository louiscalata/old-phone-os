# SPDX-License-Identifier: GPL-3.0-or-later
"""Session tracking and power-aware pausing."""
from dataclasses import dataclass
from pathlib import Path
import contextlib
import os
import signal
import sys
import time
from typing import Callable
from opos.aitools import power
from opos.aitools.installer import read_manifest
from opos.battery import DrainEstimator, Sample, essentials_reserve
from opos.governor import Priority, Task
APP_TASK = "app in use"
FULL_FLOOR_PERCENT = 20.0
@dataclass(frozen=True)
class Session:
    """Live tool session."""
    pid: int
    tool_id: str
def _inside(candidate: str, base: Path) -> bool:
    """Check candidate inside base."""
    try:
        return Path(candidate).is_relative_to(base)
    except AttributeError:
        try:
            Path(candidate).relative_to(base)
            return True
        except ValueError:
            return False
def _ppid_from_stat(text: str) -> int | None:
    """Parse ppid from stat."""
    r = text.rfind(")")
    part = text[r + 1 :] if r != -1 else text
    parts = part.strip().split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None
def list_sessions(run_dir: Path, tools_root: Path, proc_root: Path = Path("/proc")) -> list[Session]:
    """Return live sessions and clean stale pid files."""
    if not run_dir.is_dir():
        return []
    out: list[Session] = []
    for p in run_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if "." not in name:
            continue
        tool_id, pid_s = name.rsplit(".", 1)
        if not pid_s.isdigit():
            continue
        try:
            txt = p.read_text().strip()
        except OSError:
            continue
        if not txt:
            with contextlib.suppress(OSError):
                p.unlink()
            continue
        if txt != tool_id:
            with contextlib.suppress(OSError):
                p.unlink()
            continue
        pid_dir = proc_root / pid_s
        if not pid_dir.exists():
            with contextlib.suppress(OSError):
                p.unlink()
            continue
        cand: str | None = None
        exe = pid_dir / "exe"
        try:
            cand = os.readlink(exe)
        except OSError:
            try:
                data = (pid_dir / "cmdline").read_bytes()
                if data:
                    cand = data.split(b"\x00")[0].decode(errors="ignore")
            except OSError:
                cand = None
        if not cand:
            with contextlib.suppress(OSError):
                p.unlink()
            continue
        if not _inside(cand, tools_root / tool_id):
            with contextlib.suppress(OSError):
                p.unlink()
            continue
        out.append(Session(pid=int(pid_s), tool_id=tool_id))
    return out
def descendants(pid: int, proc_root: Path = Path("/proc")) -> list[int]:
    """Return pid and its descendants."""
    ppid_map: dict[int, int] = {}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return [pid]
    for e in entries:
        if not e.is_dir():
            continue
        if not e.name.isdigit():
            continue
        try:
            text = (e / "stat").read_text()
        except OSError:
            continue
        ppid = _ppid_from_stat(text)
        if ppid is None:
            continue
        try:
            cur = int(e.name)
        except ValueError:
            continue
        ppid_map[cur] = ppid
    seen: set[int] = set()
    stack: list[int] = [pid]
    res: list[int] = []
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        res.append(cur)
        for child, pp in ppid_map.items():
            if pp == cur and child not in seen:
                stack.append(child)
    return res
def should_run(mode: str, on_power: bool, level: float | None, reserve: float | None) -> bool:
    """Decide if session should run."""
    if on_power:
        return True
    if mode == "lite":
        return False
    if mode == "full":
        if level is None:
            return False
        need = reserve if reserve is not None else 0
        floor = need if need > FULL_FLOOR_PERCENT else FULL_FLOOR_PERCENT
        return level > floor
    return False
class SessionGuard:
    """Pause and resume sessions on power changes."""
    def __init__(self, run_dir: Path, tools_root: Path, *, proc_root: Path = Path("/proc"), kill: Callable[[int, int], None] | None = None, notify: Callable[[str], None] = lambda m: None, own_pid: int | None = None) -> None:
        """Initialize guard."""
        if kill is None:
            kill = os.kill
        self.run_dir = run_dir
        self.tools_root = tools_root
        self.proc_root = proc_root
        self.kill = kill
        self.notify = notify
        self.own_pid = own_pid if own_pid is not None else os.getpid()
        self.suspended: dict[int, tuple[int, ...]] = {}
        self._last: list[Session] = []
    @property
    def session_count(self) -> int:
        """Number of valid sessions last seen."""
        return len(self._last)
    def resume_all(self) -> None:
        """Resume all suspended sessions."""
        for sess_pid, pids in list(self.suspended.items()):
            cur = descendants(sess_pid, self.proc_root)
            for p in set(pids) | set(cur):
                if p == self.own_pid:
                    continue
                try:
                    self.kill(p, signal.SIGCONT)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    continue
        self.suspended.clear()
    def tick(self, mode: str, on_power: bool, level: float | None = None, reserve: float | None = None) -> list[str]:
        """Pause or resume sessions based on power."""
        sessions = list_sessions(self.run_dir, self.tools_root, self.proc_root)
        self._last = sessions
        cur_pids = {s.pid for s in sessions}
        for pid in list(self.suspended.keys()):
            if pid not in cur_pids:
                del self.suspended[pid]
        msgs: list[str] = []
        run = should_run(mode, on_power, level, reserve)
        for s in sessions:
            if not run and s.pid not in self.suspended:
                pids = [p for p in descendants(s.pid, self.proc_root) if p != self.own_pid]
                for p in pids:
                    try:
                        self.kill(p, signal.SIGSTOP)
                    except ProcessLookupError:
                        continue
                    except PermissionError:
                        continue
                self.suspended[s.pid] = tuple(pids)
                if mode == "full":
                    msg = f"Paused {s.tool_id} (pid {s.pid}): battery is at the reserve for calls and messages"
                else:
                    msg = f"Paused {s.tool_id} (pid {s.pid}): on battery, it resumes when power returns"
                msgs.append(msg)
                with contextlib.suppress(OSError):
                    self.notify(msg)
            elif run and s.pid in self.suspended:
                remembered = self.suspended.pop(s.pid)
                cur = descendants(s.pid, self.proc_root)
                for p in set(remembered) | set(cur):
                    if p == self.own_pid:
                        continue
                    try:
                        self.kill(p, signal.SIGCONT)
                    except ProcessLookupError:
                        continue
                    except PermissionError:
                        continue
                msg = f"Resumed {s.tool_id} (pid {s.pid})"
                msgs.append(msg)
                with contextlib.suppress(OSError):
                    self.notify(msg)
        return msgs
    def active(self) -> int:
        """Count non-suspended valid sessions."""
        return sum(1 for s in self._last if s.pid not in self.suspended)
def with_session_task(tasks, active: bool) -> tuple:
    """Add app task when active."""
    if not active:
        return tuple(tasks)
    for t in tasks:
        if getattr(t, "name", None) == APP_TASK:
            return tuple(tasks)
    return tuple(tasks) + (Task(APP_TASK, Priority.ESSENTIAL, 1.0),)
def sync_dashboard(dash, active: bool) -> None:
    """Sync dashboard tasks with session state."""
    if active:
        dash.tasks = with_session_task(dash.tasks, True)
        dash.set_in_use(APP_TASK)
    elif getattr(dash, "in_use", None) == APP_TASK:
        dash.set_in_use(None)
def main_guard(run_dir: Path, *, interval_s: float = 5.0, sleep=time.sleep, proc_root: Path = Path("/proc"), supply_dir: Path = power.SUPPLY_DIR, idle_exit_s: float = 60.0, kill: Callable[[int, int], None] | None = None) -> int:
    """Run session guard until idle."""
    if kill is None:
        kill = os.kill
    if not run_dir.is_dir():
        return 0
    lock_path = run_dir / "guard.lock"
    try:
        lock_file = open(lock_path, "w")
    except OSError:
        return 0
    try:
        import fcntl
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        with contextlib.suppress(OSError):
            lock_file.close()
        return 0
    old_term = None
    try:
        import threading
        if threading.current_thread() is threading.main_thread():
            old_term = signal.getsignal(signal.SIGTERM)
            def _h(_a, _b):
                raise SystemExit(0)
            signal.signal(signal.SIGTERM, _h)
    except Exception:
        old_term = None
    estimator = DrainEstimator()
    tools_root = run_dir.parent
    prefix = run_dir.parent.parent
    guard = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=kill)
    idle = 0.0
    try:
        while True:
            if not run_dir.is_dir():
                break
            try:
                manifest = read_manifest(prefix)
                mode = manifest.get("mode", "lite")
                if mode not in ("lite", "full"):
                    mode = "lite"
            except Exception:
                mode = "lite"
            try:
                on_power = power.on_external_power(supply_dir)
            except Exception as e:
                on_power = False
                print(f"telemetry error: external power: {type(e).__name__}", file=sys.stderr)
            try:
                level = power.battery_percent(supply_dir)
            except Exception as e:
                level = None
                print(f"telemetry error: battery percent: {type(e).__name__}", file=sys.stderr)
            reserve: float | None = None
            if level is not None:
                with contextlib.suppress(Exception):
                    estimator.add(Sample(time.monotonic(), level))
                try:
                    reserve = essentials_reserve(estimator.rate())
                except Exception:
                    reserve = None
            try:
                events = guard.tick(mode, on_power, level, reserve)
            except Exception as e:
                print(f"guard error: {type(e).__name__}", file=sys.stderr)
                idle = 0.0
            else:
                for e in events:
                    print(e, file=sys.stderr)
                if guard.session_count == 0:
                    idle += interval_s
                    if idle >= idle_exit_s:
                        break
                else:
                    idle = 0.0
            try:
                sleep(interval_s)
            except SystemExit:
                raise
            except Exception:
                break
    except SystemExit:
        pass
    finally:
        with contextlib.suppress(Exception):
            guard.resume_all()
        with contextlib.suppress(Exception):
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            lock_file.close()
        if old_term is not None:
            with contextlib.suppress(Exception):
                signal.signal(signal.SIGTERM, old_term)
    return 0
