# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for opos.aitools.sessions."""
import json
import os
import shutil
import signal
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

from opos.aitools import sessions
from opos.aitools.sessions import APP_TASK, Session, SessionGuard, descendants, list_sessions, should_run, sync_dashboard, with_session_task
from opos.governor import Priority, Task


def _write_stat(proc_root, pid, comm, ppid):
    """Create stat file."""
    d = proc_root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0 0\n")


def _link_exe(proc_root, pid, target):
    """Link exe."""
    d = proc_root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "exe"
    if p.is_symlink() or p.exists():
        p.unlink()
    os.symlink(str(target), p)


def _write_cmdline(proc_root, pid, data):
    """Write cmdline."""
    d = proc_root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes(data)


def _tool_bin(tools_root, tool_id, name="app"):
    """Create fake binary inside tools_root/tool_id."""
    b = tools_root / tool_id / "bin" / name
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("x")
    b.chmod(0o755)
    return b


def _put_supply(supply, name, typ=None, online=None, status=None, cap=None):
    """Create supply entry."""
    d = supply / name
    d.mkdir(parents=True, exist_ok=True)
    if typ is not None:
        (d / "type").write_text(typ)
    if online is not None:
        (d / "online").write_text(online)
    if status is not None:
        (d / "status").write_text(status)
    if cap is not None:
        (d / "capacity").write_text(cap)


class _KillRecorder:
    """Record kill calls."""

    def __init__(self, fails=None):
        self.calls = []
        self.fails = fails or {}

    def __call__(self, pid, sig):
        self.calls.append((pid, sig))
        if pid in self.fails:
            raise self.fails[pid]


class ListSessionsTests(unittest.TestCase):
    def test_missing_run_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(list_sessions(Path(td) / "no", Path(td) / "tools", Path(td) / "proc"), [])

    @unittest.skipIf(sys.platform == "win32", "POSIX signals and /proc-like process tree")
    def test_valid_session(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            tool_id = "t1"
            binp = _tool_bin(tools_root, tool_id)
            pid = 100
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, binp)
            (run_dir / f"{tool_id}.{pid}").write_text(tool_id)
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [Session(pid=pid, tool_id=tool_id)])

    def test_stale_pid_removed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            pid = 101
            (run_dir / f"t1.{pid}").write_text("t1")
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [])
            self.assertFalse((run_dir / f"t1.{pid}").exists())

    def test_wrong_exe_outside_removed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            outs = td / "other" / "bin"
            outs.mkdir(parents=True)
            (outs / "app").write_text("x")
            pid = 102
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, outs / "app")
            pf = run_dir / f"t1.{pid}"
            pf.write_text("t1")
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [])
            self.assertFalse(pf.exists())

    def test_pid_reuse_different_tool(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            _tool_bin(tools_root, "t1")
            other = _tool_bin(tools_root, "t2")
            pid = 103
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, other)
            pf = run_dir / f"t1.{pid}"
            pf.write_text("t1")
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [])
            self.assertFalse(pf.exists())

    def test_junk_filenames_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            (run_dir / "badfile").write_text("x")
            (run_dir / "t1.abc").write_text("t1")
            (run_dir / "t1.123.extra").write_text("t1")
            d = run_dir / "t1.200"
            d.mkdir()
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [])
            self.assertTrue((run_dir / "badfile").exists())
            self.assertTrue((run_dir / "t1.abc").exists())

    def test_content_mismatch_removed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            binp = _tool_bin(tools_root, "t1")
            pid = 104
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, binp)
            pf = run_dir / f"t1.{pid}"
            pf.write_text("t2")
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [])
            self.assertFalse(pf.exists())

    def test_empty_content_removed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            binp = _tool_bin(tools_root, "t1")
            pid = 105
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, binp)
            pf = run_dir / f"t1.{pid}"
            pf.write_text("   \n")
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [])
            self.assertFalse(pf.exists())

    def test_exe_fallback_cmdline_inside(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            binp = _tool_bin(tools_root, "t1")
            pid = 106
            _write_stat(proc_root, pid, "app", 1)
            _write_cmdline(proc_root, pid, str(binp).encode() + b"\x00arg\x00")
            (run_dir / f"t1.{pid}").write_text("t1")
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [Session(pid=pid, tool_id="t1")])

    def test_exe_outside_not_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            outs = td / "other"
            outs.mkdir()
            outside = outs / "app"
            outside.write_text("x")
            binp = _tool_bin(tools_root, "t1")
            pid = 107
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, outside)
            _write_cmdline(proc_root, pid, str(binp).encode() + b"\x00")
            pf = run_dir / f"t1.{pid}"
            pf.write_text("t1")
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [])
            self.assertFalse(pf.exists())

    def test_no_candidate_removed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir()
            proc_root.mkdir()
            pid = 108
            _write_stat(proc_root, pid, "app", 1)
            pf = run_dir / f"t1.{pid}"
            pf.write_text("t1")
            out = list_sessions(run_dir, tools_root, proc_root)
            self.assertEqual(out, [])
            self.assertFalse(pf.exists())


class DescendantsTests(unittest.TestCase):
    def test_tree(self):
        with tempfile.TemporaryDirectory() as td:
            proc_root = Path(td) / "proc"
            proc_root.mkdir()
            _write_stat(proc_root, 1, "init", 0)
            _write_stat(proc_root, 2, "a", 1)
            _write_stat(proc_root, 3, "b", 1)
            _write_stat(proc_root, 4, "c", 2)
            res = descendants(1, proc_root)
            self.assertEqual(set(res), {1, 2, 3, 4})
            self.assertIn(1, res)
            res2 = descendants(2, proc_root)
            self.assertEqual(set(res2), {2, 4})

    def test_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            proc_root = Path(td) / "proc"
            proc_root.mkdir()
            _write_stat(proc_root, 10, "a", 11)
            _write_stat(proc_root, 11, "b", 10)
            res = descendants(10, proc_root)
            self.assertEqual(set(res), {10, 11})
            self.assertEqual(len(res), 2)

    def test_comm_with_spaces_and_paren(self):
        with tempfile.TemporaryDirectory() as td:
            proc_root = Path(td) / "proc"
            proc_root.mkdir()
            _write_stat(proc_root, 20, "my app", 1)
            _write_stat(proc_root, 21, "a) b (c", 20)
            _write_stat(proc_root, 22, "x ) y", 20)
            res = descendants(20, proc_root)
            self.assertEqual(set(res), {20, 21, 22})
            res1 = descendants(1, proc_root)
            self.assertEqual(set(res1), {1, 20, 21, 22})

    def test_missing_root(self):
        self.assertEqual(descendants(999, Path("/fake/home/missing")), [999])

    def test_unreadable_stat_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            proc_root = Path(td) / "proc"
            proc_root.mkdir()
            _write_stat(proc_root, 30, "a", 1)
            d = proc_root / "31"
            d.mkdir()
            res = descendants(30, proc_root)
            self.assertEqual(res, [30])


class ShouldRunTests(unittest.TestCase):
    def test_on_power_true_always(self):
        self.assertTrue(should_run("lite", True, None, None))
        self.assertTrue(should_run("full", True, None, None))
        self.assertTrue(should_run("lite", True, 5, 50))
        self.assertTrue(should_run("unknown", True, None, None))

    def test_lite_on_battery_false(self):
        self.assertFalse(should_run("lite", False, 100, 0))
        self.assertFalse(should_run("lite", False, None, None))

    def test_full_no_level_fails_closed(self):
        self.assertFalse(should_run("full", False, None, None))
        self.assertFalse(should_run("full", False, None, 10))

    def test_full_floor_20(self):
        self.assertFalse(should_run("full", False, 20, None))
        self.assertFalse(should_run("full", False, 20, 0))
        self.assertFalse(should_run("full", False, 15, None))
        self.assertTrue(should_run("full", False, 21, None))
        self.assertTrue(should_run("full", False, 20.1, None))

    def test_full_reserve_higher(self):
        self.assertFalse(should_run("full", False, 30, 30))
        self.assertFalse(should_run("full", False, 25, 30))
        self.assertTrue(should_run("full", False, 31, 30))
        self.assertTrue(should_run("full", False, 21, 10))

    def test_unknown_mode_fails_closed(self):
        self.assertFalse(should_run("unknown", False, 100, 0))
        self.assertFalse(should_run("weird", False, None, None))


@unittest.skipIf(sys.platform == "win32", "POSIX signals and /proc-like process tree")
class SessionGuardTests(unittest.TestCase):
    def _setup_tree(self, td):
        td = Path(td)
        tools_root = td / "tools"
        run_dir = td / "run"
        proc_root = td / "proc"
        run_dir.mkdir(parents=True)
        proc_root.mkdir(parents=True)
        tid = "t1"
        binp = _tool_bin(tools_root, tid)
        _write_stat(proc_root, 10, "tool", 1)
        _link_exe(proc_root, 10, binp)
        _write_stat(proc_root, 11, "child", 10)
        _write_stat(proc_root, 12, "child2", 10)
        _write_stat(proc_root, 20, "sibling", 1)
        (tools_root / tid / "bin" / "child").write_text("x")
        _link_exe(proc_root, 11, _tool_bin(tools_root, tid, "child"))
        _link_exe(proc_root, 12, _tool_bin(tools_root, tid, "child2"))
        other_bin = td / "otherbin"
        other_bin.write_text("x")
        _write_stat(proc_root, 20, "sibling", 1)
        _link_exe(proc_root, 20, other_bin)
        _write_stat(proc_root, 30, "unrelated", 1)
        _link_exe(proc_root, 30, other_bin)
        (run_dir / f"{tid}.10").write_text(tid)
        return tools_root, run_dir, proc_root

    def test_lite_pause_children_only(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            msgs = g.tick("lite", False, 50, 10)
            self.assertEqual(len(msgs), 1)
            self.assertIn("Paused t1 (pid 10)", msgs[0])
            self.assertIn("on battery", msgs[0])
            pids = {p for p, _ in rec.calls}
            self.assertEqual(pids, {10, 11, 12})
            self.assertNotIn((20, signal.SIGSTOP), rec.calls)
            self.assertNotIn((30, signal.SIGSTOP), rec.calls)
            self.assertEqual(g.active(), 0)
            self.assertEqual(g.session_count, 1)

    def test_no_signal_own_pid(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools_root = td / "tools"
            run_dir = td / "run"
            proc_root = td / "proc"
            run_dir.mkdir(parents=True)
            proc_root.mkdir(parents=True)
            tid = "t1"
            binp = _tool_bin(tools_root, tid)
            _write_stat(proc_root, 10, "tool", 1)
            _link_exe(proc_root, 10, binp)
            _write_stat(proc_root, 11, "child", 10)
            _link_exe(proc_root, 11, binp)
            (run_dir / f"{tid}.10").write_text(tid)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=11)
            g.tick("lite", False)
            pids = [p for p, _ in rec.calls]
            self.assertNotIn(11, pids)
            self.assertIn(10, pids)

    def test_resume_on_power(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            g.tick("lite", False)
            rec.calls.clear()
            msgs = g.tick("lite", True)
            self.assertEqual(len(msgs), 1)
            self.assertIn("Resumed t1 (pid 10)", msgs[0])
            sigs = {s for _, s in rec.calls}
            self.assertEqual(sigs, {signal.SIGCONT})
            pids = {p for p, _ in rec.calls}
            self.assertEqual(pids, {10, 11, 12})
            self.assertEqual(g.active(), 1)

    def test_resume_new_child_gets_cont(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            g.tick("lite", False)
            _write_stat(proc_root, 13, "newchild", 10)
            rec.calls.clear()
            msgs = g.tick("lite", True)
            self.assertEqual(len(msgs), 1)
            self.assertIn("Resumed t1 (pid 10)", msgs[0])
            pids = {p for p, _ in rec.calls}
            self.assertIn(13, pids)
            self.assertIn(10, pids)
            self.assertIn(11, pids)
            self.assertIn(12, pids)
            self.assertEqual({s for _, s in rec.calls}, {signal.SIGCONT})

    def test_no_double_stop(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            g.tick("lite", False)
            rec.calls.clear()
            g.tick("lite", False)
            self.assertEqual(rec.calls, [])

    def test_permission_errors_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            fails = {11: ProcessLookupError(), 12: PermissionError()}
            rec = _KillRecorder(fails=fails)
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            msgs = g.tick("lite", False)
            self.assertEqual(len(msgs), 1)
            self.assertEqual(sorted(g.suspended[10]), [10, 11, 12])
            rec.calls.clear()
            g.tick("lite", True)
            self.assertTrue(any(s == signal.SIGCONT for _, s in rec.calls))

    def test_full_above_floor_runs(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            msgs = g.tick("full", False, 50, 20)
            self.assertEqual(msgs, [])
            self.assertEqual(rec.calls, [])
            self.assertEqual(g.active(), 1)

    def test_full_at_floor_pauses(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            msgs = g.tick("full", False, 20, 0)
            self.assertEqual(len(msgs), 1)
            self.assertIn("reserve for calls", msgs[0])
            msgs2 = g.tick("full", False, 20, 30)
            self.assertEqual(msgs2, [])

    def test_resume_all(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            g.tick("lite", False)
            rec.calls.clear()
            g.resume_all()
            self.assertTrue(all(s == signal.SIGCONT for _, s in rec.calls))
            self.assertEqual(g.suspended, {})
            self.assertEqual(g.active(), 1)

    def test_resume_all_reenumerates_descendants(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            g.tick("lite", False)
            _write_stat(proc_root, 13, "newchild", 11)
            rec.calls.clear()
            g.resume_all()
            pids = {p for p, _ in rec.calls}
            self.assertIn(13, pids)
            self.assertIn(10, pids)
            self.assertTrue(all(s == signal.SIGCONT for _, s in rec.calls))
            self.assertEqual(g.suspended, {})

    def test_session_vanished_forgets(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            g.tick("lite", False)
            self.assertIn(10, g.suspended)
            (run_dir / "t1.10").unlink()
            g.tick("lite", False)
            self.assertNotIn(10, g.suspended)
            self.assertEqual(g.session_count, 0)

    def test_active_count(self):
        with tempfile.TemporaryDirectory() as td:
            tools_root, run_dir, proc_root = self._setup_tree(td)
            rec = _KillRecorder()
            g = SessionGuard(run_dir, tools_root, proc_root=proc_root, kill=rec, own_pid=999)
            g.tick("lite", True)
            self.assertEqual(g.active(), 1)
            g.tick("lite", False)
            self.assertEqual(g.active(), 0)


class WithSessionTaskTests(unittest.TestCase):
    def test_inactive(self):
        t = Task("a", Priority.ESSENTIAL, 1.0)
        self.assertEqual(with_session_task([t], False), (t,))

    def test_active_adds(self):
        out = with_session_task([], True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].name, APP_TASK)
        self.assertEqual(out[0].priority, Priority.ESSENTIAL)

    def test_no_duplicate(self):
        cur = with_session_task([], True)
        again = with_session_task(cur, True)
        self.assertEqual(cur, again)
        self.assertEqual(len(again), 1)


class SyncDashboardTests(unittest.TestCase):
    def test_active(self):
        class FakeDash:
            def __init__(self):
                self.tasks = []
                self.in_use = None

            def set_in_use(self, n):
                self.in_use = n

        d = FakeDash()
        sync_dashboard(d, True)
        self.assertEqual(d.in_use, APP_TASK)
        self.assertTrue(any(getattr(t, "name", None) == APP_TASK for t in d.tasks))

    def test_inactive_clears(self):
        class FakeDash:
            def __init__(self):
                self.tasks = []
                self.in_use = APP_TASK

            def set_in_use(self, n):
                self.in_use = n

        d = FakeDash()
        sync_dashboard(d, False)
        self.assertIsNone(d.in_use)

    def test_inactive_other_stays(self):
        class FakeDash:
            def __init__(self):
                self.tasks = []
                self.in_use = "other"

            def set_in_use(self, n):
                self.in_use = n

        d = FakeDash()
        sync_dashboard(d, False)
        self.assertEqual(d.in_use, "other")

    def test_real_dashboard_if_available(self):
        try:
            from opos.dashboard import Dashboard
            from opos.runtime import DEFAULT_TASKS

            dash = Dashboard(DEFAULT_TASKS, history_len=60)
            sync_dashboard(dash, True)
            self.assertEqual(dash.in_use, APP_TASK)
            sync_dashboard(dash, False)
            self.assertIsNone(dash.in_use)
        except Exception:
            self.skipTest("no real dashboard")


@unittest.skipIf(sys.platform == "win32", "POSIX signals and /proc-like process tree")
class MainGuardTests(unittest.TestCase):
    def test_missing_run_dir(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rc = sessions.main_guard(td / "nope", interval_s=0.1, sleep=lambda _: None, proc_root=td / "proc", supply_dir=td / "sup", idle_exit_s=0.2)
            self.assertEqual(rc, 0)

    def test_lock_held(self):
        if fcntl is None:
            self.skipTest("no fcntl")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_dir = td / "run"
            run_dir.mkdir()
            lock_path = run_dir / "guard.lock"
            f = open(lock_path, "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                rc = sessions.main_guard(run_dir, interval_s=0.1, sleep=lambda _: None, proc_root=td / "proc", supply_dir=td / "sup", idle_exit_s=0.2)
                self.assertEqual(rc, 0)
            finally:
                try:
                    fcntl.flock(f, fcntl.LOCK_UN)
                except Exception:
                    pass
                f.close()

    def test_pause_resume_idle_exit(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            tools_root = prefix / "opos-tools"
            run_dir = tools_root / "run"
            run_dir.mkdir(parents=True)
            proc_root = td / "proc"
            proc_root.mkdir()
            supply = td / "supply"
            supply.mkdir()
            (tools_root / "manifest.json").write_text(json.dumps({"mode": "lite"}))
            tool_id = "mytool"
            binp = _tool_bin(tools_root, tool_id)
            pid = 200
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, binp)
            (run_dir / f"{tool_id}.{pid}").write_text(tool_id)
            _put_supply(supply, "BAT0", "Battery", None, "Discharging", "50")
            rec = _KillRecorder()
            cnt = [0]

            def fake_sleep(s):
                cnt[0] += 1
                if cnt[0] == 1:
                    (supply / "BAT0" / "status").write_text("Charging")
                elif cnt[0] == 2:
                    try:
                        (run_dir / f"{tool_id}.{pid}").unlink()
                    except FileNotFoundError:
                        pass
                    shutil.rmtree(proc_root / str(pid), ignore_errors=True)

            old = sys.stderr
            buf = StringIO()
            sys.stderr = buf
            try:
                rc = sessions.main_guard(run_dir, interval_s=1, sleep=fake_sleep, proc_root=proc_root, supply_dir=supply, idle_exit_s=2, kill=rec)
            finally:
                sys.stderr = old
            self.assertEqual(rc, 0)
            self.assertTrue(any(c[1] == signal.SIGSTOP for c in rec.calls))
            self.assertTrue(any(c[1] == signal.SIGCONT for c in rec.calls))
            txt = buf.getvalue()
            self.assertIn("Paused", txt)
            self.assertIn("Resumed", txt)

    def test_F017_telemetry_failure_after_charging_pauses_and_recovers(self):
        import unittest.mock
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            tools_root = prefix / "opos-tools"
            run_dir = tools_root / "run"
            run_dir.mkdir(parents=True)
            proc_root = td / "proc"
            proc_root.mkdir()
            supply = td / "supply"
            supply.mkdir()
            (tools_root / "manifest.json").write_text(json.dumps({"mode": "full"}))
            tool_id = "mytool"
            binp = _tool_bin(tools_root, tool_id)
            pid = 200
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, binp)
            (run_dir / f"{tool_id}.{pid}").write_text(tool_id)
            other = 201
            obin = td / "otherbin"
            obin.write_text("x")
            _write_stat(proc_root, other, "ess", 1)
            _link_exe(proc_root, other, obin)
            rec = _KillRecorder()
            snaps = []

            def fake_sleep(s):
                snaps.append(list(rec.calls))
                if len(snaps) >= 4:
                    raise SystemExit(0)

            clock = [1000.0]

            def fake_mono():
                clock[0] += 5.0
                return clock[0]

            on_vals = [True, OSError("x"), OSError("x"), True]
            lvl_vals = [90, OSError("x"), OSError("x"), 90]

            def fake_power(d):
                v = on_vals.pop(0)
                if isinstance(v, Exception):
                    raise v
                return v

            def fake_level(d):
                v = lvl_vals.pop(0)
                if isinstance(v, Exception):
                    raise v
                return v

            old = sys.stderr
            buf = StringIO()
            sys.stderr = buf
            try:
                with unittest.mock.patch.object(sessions.power, "on_external_power", fake_power):
                    with unittest.mock.patch.object(sessions.power, "battery_percent", fake_level):
                        with unittest.mock.patch.object(sessions.time, "monotonic", fake_mono):
                            rc = sessions.main_guard(run_dir, interval_s=5, sleep=fake_sleep, proc_root=proc_root, supply_dir=supply, idle_exit_s=1000, kill=rec)
            finally:
                sys.stderr = old
            self.assertEqual(rc, 0)
            self.assertEqual(len(snaps), 4)
            self.assertEqual(snaps[0], [])
            self.assertEqual(snaps[1], [(pid, signal.SIGSTOP)])
            self.assertEqual(snaps[2], snaps[1])
            self.assertEqual(len(snaps[3]), 2)
            self.assertEqual(snaps[3][:-1], snaps[2])
            self.assertEqual(snaps[3][-1], (pid, signal.SIGCONT))
            self.assertFalse([c for c in rec.calls if c[0] == other])
            txt = buf.getvalue()
            self.assertEqual(txt.count("telemetry error: external power: OSError"), 2)
            self.assertEqual(txt.count("telemetry error: battery percent: OSError"), 2)

    def test_resume_on_system_exit(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix2"
            tools_root = prefix / "opos-tools"
            run_dir = tools_root / "run"
            run_dir.mkdir(parents=True)
            proc_root = td / "proc"
            proc_root.mkdir()
            supply = td / "supply"
            supply.mkdir()
            (tools_root / "manifest.json").write_text(json.dumps({"mode": "lite"}))
            tool_id = "t2"
            binp = _tool_bin(tools_root, tool_id)
            pid = 201
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, binp)
            (run_dir / f"{tool_id}.{pid}").write_text(tool_id)
            _put_supply(supply, "BAT0", "Battery", None, "Discharging", "10")
            rec = _KillRecorder()

            def bad_sleep(s):
                raise SystemExit(0)

            rc = sessions.main_guard(run_dir, interval_s=1, sleep=bad_sleep, proc_root=proc_root, supply_dir=supply, idle_exit_s=10, kill=rec)
            self.assertEqual(rc, 0)
            self.assertTrue(any(c[0] == pid and c[1] == signal.SIGSTOP for c in rec.calls))
            self.assertTrue(any(c[0] == pid and c[1] == signal.SIGCONT for c in rec.calls))

    def test_bad_manifest_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix3"
            tools_root = prefix / "opos-tools"
            run_dir = tools_root / "run"
            run_dir.mkdir(parents=True)
            proc_root = td / "proc"
            proc_root.mkdir()
            supply = td / "supply"
            supply.mkdir()
            (tools_root / "manifest.json").write_text("not json")
            rc = sessions.main_guard(run_dir, interval_s=1, sleep=lambda _: None, proc_root=proc_root, supply_dir=supply, idle_exit_s=1)
            self.assertEqual(rc, 0)

    def test_tick_error_continues(self):
        """Tick failure does not cause early idle exit."""
        import unittest.mock
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            tools_root = prefix / "opos-tools"
            run_dir = tools_root / "run"
            run_dir.mkdir(parents=True)
            proc_root = td / "proc"
            proc_root.mkdir()
            supply = td / "supply"
            supply.mkdir()
            (tools_root / "manifest.json").write_text(json.dumps({"mode": "lite"}))
            tool_id = "mytool"
            binp = _tool_bin(tools_root, tool_id)
            pid = 200
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, binp)
            (run_dir / f"{tool_id}.{pid}").write_text(tool_id)
            _put_supply(supply, "BAT0", "Battery", None, "Discharging", "50")
            orig = sessions.SessionGuard.tick
            calls = {"n": 0}

            def flaky(self, mode, on_power, level=None, reserve=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("boom")
                return orig(self, mode, on_power, level, reserve)

            with unittest.mock.patch.object(sessions.SessionGuard, "tick", flaky):
                cnt = [0]

                def fake_sleep(s):
                    cnt[0] += 1
                    if cnt[0] == 2:
                        try:
                            (run_dir / f"{tool_id}.{pid}").unlink()
                        except FileNotFoundError:
                            pass
                        shutil.rmtree(proc_root / str(pid), ignore_errors=True)

                old = sys.stderr
                buf = StringIO()
                sys.stderr = buf
                try:
                    rc = sessions.main_guard(run_dir, interval_s=1, sleep=fake_sleep, proc_root=proc_root, supply_dir=supply, idle_exit_s=2, kill=lambda *_: None)
                finally:
                    sys.stderr = old
                self.assertEqual(rc, 0)
                self.assertIn("guard error", buf.getvalue())
                self.assertGreaterEqual(calls["n"], 3)

    def test_real_kill_not_reached(self):
        """Real kill never reached when injected."""
        import unittest.mock
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            tools_root = prefix / "opos-tools"
            run_dir = tools_root / "run"
            run_dir.mkdir(parents=True)
            proc_root = td / "proc"
            proc_root.mkdir()
            supply = td / "supply"
            supply.mkdir()
            (tools_root / "manifest.json").write_text(json.dumps({"mode": "lite"}))
            tool_id = "mytool"
            binp = _tool_bin(tools_root, tool_id)
            pid = 200
            _write_stat(proc_root, pid, "app", 1)
            _link_exe(proc_root, pid, binp)
            (run_dir / f"{tool_id}.{pid}").write_text(tool_id)
            _put_supply(supply, "BAT0", "Battery", None, "Discharging", "50")
            rec = _KillRecorder()
            cnt = [0]

            def fake_sleep(s):
                cnt[0] += 1
                if cnt[0] == 1:
                    (supply / "BAT0" / "status").write_text("Charging")
                elif cnt[0] == 2:
                    try:
                        (run_dir / f"{tool_id}.{pid}").unlink()
                    except FileNotFoundError:
                        pass
                    shutil.rmtree(proc_root / str(pid), ignore_errors=True)

            with unittest.mock.patch.object(os, "kill", side_effect=AssertionError("real kill called")):
                old = sys.stderr
                buf = StringIO()
                sys.stderr = buf
                try:
                    rc = sessions.main_guard(run_dir, interval_s=1, sleep=fake_sleep, proc_root=proc_root, supply_dir=supply, idle_exit_s=2, kill=rec)
                finally:
                    sys.stderr = old
            self.assertEqual(rc, 0)
            self.assertTrue(any(c[1] == signal.SIGSTOP for c in rec.calls))
            self.assertTrue(any(c[1] == signal.SIGCONT for c in rec.calls))


if __name__ == "__main__":
    unittest.main()
