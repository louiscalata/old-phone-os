# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for install.sh and install.ps1.

Interactive apt branches need a pty and are reasonably left untested.
"""

import contextlib
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"
TIMEOUT = 120

_TRACKED_SANDBOXES = set()


def _register_fresh(base):
    resolved = Path(base).resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    resolved.relative_to(tmp)
    if resolved == tmp:
        raise AssertionError("refusing sandbox root %s" % tmp)
    st = os.stat(resolved)
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise AssertionError("sandbox %s not owned" % resolved)
    _TRACKED_SANDBOXES.add(resolved)
    return resolved


def fresh_sandbox():
    """Create a harness-owned temp dir and register it."""
    td = tempfile.TemporaryDirectory()
    _register_fresh(Path(td.name))
    return td


@contextlib.contextmanager
def disposable_sandbox():
    """Yield a sandbox whose installer is a disposable copy of this checkout."""
    global INSTALL_SH
    td = fresh_sandbox()
    try:
        copy_root = Path(td.name) / "checkout-copy"
        shutil.copytree(
            ROOT,
            copy_root,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
        original = INSTALL_SH
        INSTALL_SH = copy_root / "install.sh"
        try:
            yield td.name
        finally:
            INSTALL_SH = original
    finally:
        td.cleanup()


def _overlaps(a, b):
    try:
        a.relative_to(b)
        return True
    except ValueError:
        pass
    try:
        b.relative_to(a)
        return True
    except ValueError:
        pass
    return False


def make_env(base):
    """Build a clean environment rooted at base."""
    home = Path(base) / "home"
    return {
        "HOME": str(home),
        "OPOS_HOME": str(home / ".local" / "share" / "opos"),
        "OPOS_BIN_DIR": str(home / ".local" / "bin"),
        "OPOS_CONFIG_DIR": str(home / ".config" / "opos"),
        "OPOS_PYTHON": sys.executable,
        "PATH": os.environ.get("PATH", ""),
    }


_GIT_REPO_OVERRIDE_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
)


def _sandbox_for(path):
    resolved = Path(path).resolve()
    for sandbox in _TRACKED_SANDBOXES:
        try:
            resolved.relative_to(sandbox)
            return sandbox
        except ValueError:
            continue
    raise AssertionError("path %s is outside tracked sandbox" % resolved)


def fixture_git_env(repo):
    """Build a git env rooted at repo without inherited redirects."""
    sandbox = _sandbox_for(repo)
    env = dict(os.environ)
    for var in _GIT_REPO_OVERRIDE_VARS:
        env.pop(var, None)
    env.pop("GIT_CONFIG", None)
    env.pop("GIT_CEILING_DIRECTORIES", None)
    for var in [v for v in env if v.startswith("GIT_CONFIG_KEY_") or v.startswith("GIT_CONFIG_VALUE_")]:
        del env[var]
    env.pop("GIT_CONFIG_COUNT", None)
    env.pop("GIT_CONFIG_PARAMETERS", None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.hooksPath"
    env["GIT_CONFIG_VALUE_0"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_TEMPLATE_DIR"] = tempfile.mkdtemp(dir=str(sandbox))
    return env


def run(args, env):
    """Run args with env and return the completed process."""
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=TIMEOUT)


def assert_sandboxed(env, args):
    tmp = Path(tempfile.gettempdir()).resolve()
    for var in ("HOME", "OPOS_HOME", "OPOS_BIN_DIR", "OPOS_CONFIG_DIR"):
        value = env.get(var)
        if not value:
            raise AssertionError(
                "%s=%s is not under temp dir %s: install.sh was not run" % (var, value, tmp)
            )
        resolved = Path(value).resolve()
        try:
            sandbox = _sandbox_for(resolved)
        except AssertionError:
            sandbox = None
        if sandbox is None or resolved == sandbox or _overlaps(resolved, ROOT.resolve()):
            raise AssertionError(
                "%s=%s is not under temp dir %s: install.sh was not run" % (var, value, tmp)
            )
    if "--uninstall" in args and "--dry-run" not in args:
        source = Path(INSTALL_SH).resolve()
        try:
            _sandbox_for(source)
        except AssertionError:
            raise AssertionError(
                "--uninstall would delete this checkout; run destructive uninstall only from a disposable sandbox copy"
            )
        if _overlaps(source, ROOT.resolve()):
            raise AssertionError(
                "--uninstall would delete this checkout; installer source overlaps the active checkout"
            )
    return None


def run_installer(env, *args):
    """Run install.sh with args."""
    assert_sandboxed(env, args)
    return run(["sh", str(INSTALL_SH), *args], env)


def save_config(env):
    """Save the default config via the Python package."""
    full = dict(env)
    full["PYTHONPATH"] = str(ROOT / "src")
    code = "import opos.setup.config as c; c.save(c.default_config())"
    return run([sys.executable, "-c", code], full)


def launcher_path(env):
    """Return the launcher path for env."""
    return Path(env["OPOS_BIN_DIR"]) / "opos"


def config_files(env):
    """Return files under the config directory."""
    root = Path(env["OPOS_CONFIG_DIR"])
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def _cmd_status_commandline(comspec, launcher):
    """Build a cmd line that bypasses Python Windows re-escaping."""
    return '"' + str(comspec) + '" /d /c ""' + str(launcher) + '" status"'


class SandboxGuardTest(unittest.TestCase):
    def test_rejects_unsafe_paths_before_execution(self):
        import unittest.mock as mock

        mod = sys.modules[__name__]
        with fresh_sandbox() as tmp:
            base = make_env(Path(tmp))
            tmp_resolved = str(Path(tempfile.gettempdir()).resolve())
            for var in ("HOME", "OPOS_HOME", "OPOS_BIN_DIR", "OPOS_CONFIG_DIR"):
                with self.subTest(var=var):
                    env = dict(base)
                    env[var] = "/usr/local/opos-never-created"
                    with mock.patch.object(mod, "run") as run_mock:
                        with self.assertRaises(AssertionError) as cm:
                            run_installer(env, "--uninstall")
                    message = str(cm.exception)
                    self.assertIn(var, message)
                    self.assertIn("/usr/local/opos-never-created", message)
                    self.assertIn(tmp_resolved, message)
                    self.assertIn("install.sh was not run", message)
                    run_mock.assert_not_called()

    def test_rejects_clone_uninstall_before_execution(self):
        import unittest.mock as mock

        mod = sys.modules[__name__]
        with fresh_sandbox() as tmp:
            env = make_env(Path(tmp))
            with tempfile.TemporaryDirectory() as root_tmp:
                fake_root = Path(root_tmp)
                (fake_root / ".git").mkdir()
                with mock.patch.object(mod, "ROOT", fake_root):
                    with mock.patch.object(mod, "run") as run_mock:
                        with self.assertRaises(AssertionError) as cm:
                            run_installer(env, "--uninstall")
                self.assertIn("uninstall would delete this checkout", str(cm.exception))
                run_mock.assert_not_called()

    def test_rejects_worktree_uninstall_before_execution(self):
        import unittest.mock as mock

        mod = sys.modules[__name__]
        with fresh_sandbox() as tmp:
            env = make_env(Path(tmp))
            fake_root = Path(tmp) / "worktree"
            fake_root.mkdir(parents=True)
            (fake_root / ".git").write_text(
                "gitdir" + chr(58) + " " + str(fake_root), encoding="utf-8"
            )
            self.assertTrue((fake_root / ".git").is_file())
            installer_copy = fake_root / "install.sh"
            installer_copy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            with mock.patch.object(mod, "ROOT", fake_root):
                with mock.patch.object(mod, "INSTALL_SH", installer_copy):
                    with mock.patch.object(mod, "run") as run_mock:
                        with self.assertRaises(AssertionError) as cm:
                            run_installer(env, "--uninstall")
            self.assertIn("installer source overlaps", str(cm.exception))
            run_mock.assert_not_called()

    def test_accepts_temporary_environment(self):
        with fresh_sandbox() as tmp:
            self.assertIsNone(assert_sandboxed(make_env(Path(tmp)), ()))


    def test_rejects_unowned_temp_directory(self):
        import unittest.mock as mock
        mod = sys.modules[__name__]
        with tempfile.TemporaryDirectory() as other:
            env = make_env(Path(other))
            with mock.patch.object(mod, "run") as run_mock:
                with self.assertRaises(AssertionError) as cm:
                    run_installer(env, "--help")
                self.assertIn("install.sh was not run", str(cm.exception))
                run_mock.assert_not_called()

    def test_rejects_sandbox_root_and_checkout_overlap(self):
        import unittest.mock as mock
        mod = sys.modules[__name__]
        with fresh_sandbox() as tmp:
            env = make_env(Path(tmp))
            env["HOME"] = str(Path(tmp))
            with mock.patch.object(mod, "run") as run_mock:
                with self.assertRaises(AssertionError):
                    run_installer(env, "--help")
                run_mock.assert_not_called()
            env = make_env(Path(tmp))
            with mock.patch.object(mod, "ROOT", Path(tmp) / "home"):
                with mock.patch.object(mod, "run") as run_mock:
                    with self.assertRaises(AssertionError):
                        run_installer(env, "--help")
                    run_mock.assert_not_called()


    def test_fixture_git_env_isolated(self):
        """Hostile inherited GIT_* must not leak into fixture git env."""
        import unittest.mock as mock

        with fresh_sandbox() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir(parents=True)
            at = chr(64)
            hostile = {
                "GIT_DIR": str(ROOT / ".git"),
                "GIT_WORK_TREE": str(ROOT),
                "GIT_INDEX_FILE": str(ROOT / ".git" / "index"),
                "GIT_COMMON_DIR": str(ROOT / ".git"),
                "GIT_NAMESPACE": "hostile",
                "GIT_OBJECT_DIRECTORY": str(ROOT / ".git" / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(ROOT / ".git" / "objects"),
                "GIT_PREFIX": str(ROOT),
                "GIT_CONFIG": "hostile",
                "GIT_CONFIG_GLOBAL": str(ROOT / "gitconfig"),
                "GIT_CONFIG_SYSTEM": str(ROOT / "gitconfig"),
                "GIT_CEILING_DIRECTORIES": str(ROOT),
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "user.email",
                "GIT_CONFIG_VALUE_0": "hostile" + at + "example.com",
                "GIT_CONFIG_KEY_1": "core.hooksPath",
                "GIT_CONFIG_VALUE_1": str(ROOT),
                "GIT_TERMINAL_PROMPT": "1",
            }
            with mock.patch.dict(os.environ, hostile):
                env = fixture_git_env(repo)
            for var in _GIT_REPO_OVERRIDE_VARS:
                self.assertNotIn(var, env)
            self.assertNotIn("GIT_CONFIG", env)
            self.assertNotIn("GIT_CEILING_DIRECTORIES", env)
            self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertEqual(env["GIT_CONFIG_SYSTEM"], os.devnull)
            self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
            self.assertEqual(env["GIT_CONFIG_KEY_0"], "core.hooksPath")
            self.assertEqual(env["GIT_CONFIG_VALUE_0"], os.devnull)
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            stray = sorted(v for v in env if v.startswith("GIT_CONFIG_KEY_") or v.startswith("GIT_CONFIG_VALUE_"))
            self.assertEqual(stray, ["GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"])
            for var, value in env.items():
                if not var.startswith("GIT_"):
                    continue
                if not isinstance(value, str):
                    continue
                if value in (os.devnull, "1", "0"):
                    continue
                if value.startswith("/"):
                    resolved = Path(value).resolve()
                    self.assertNotEqual(resolved, ROOT.resolve(), var)
                    with self.assertRaises(ValueError, msg=var):
                        resolved.relative_to(ROOT.resolve())
                else:
                    self.assertNotEqual(value, str(ROOT.resolve()), var)
            with mock.patch.dict(os.environ, {"PWD": str(ROOT.resolve())}):
                kept = fixture_git_env(repo)
            self.assertEqual(kept["PWD"], str(ROOT.resolve()))
            with self.assertRaises(AssertionError):
                with mock.patch.dict(os.environ, hostile):
                    fixture_git_env(ROOT)
            with tempfile.TemporaryDirectory() as other:
                with self.assertRaises(AssertionError):
                    with mock.patch.dict(os.environ, hostile):
                        fixture_git_env(Path(other) / "repo")

    def test_fixture_git_env_ignores_cwd_vars(self):
        """Cwd vars at checkout must not break git isolation."""
        import unittest.mock as mock

        with fresh_sandbox() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir(parents=True)
            at = chr(64)
            hostile = {
                "PWD": str(ROOT),
                "OLDPWD": str(ROOT),
                "GIT_DIR": str(ROOT),
                "GIT_WORK_TREE": str(ROOT),
                "GIT_INDEX_FILE": str(ROOT / "index"),
                "GIT_COMMON_DIR": str(ROOT),
                "GIT_NAMESPACE": "hostile",
                "GIT_OBJECT_DIRECTORY": str(ROOT),
                "GIT_PREFIX": str(ROOT),
                "GIT_CONFIG": "hostile",
                "GIT_CONFIG_GLOBAL": str(ROOT / "gitconfig"),
                "GIT_CONFIG_SYSTEM": str(ROOT / "gitconfig"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "user.email",
                "GIT_CONFIG_VALUE_0": "hostile" + at + "example.com",
                "GIT_CONFIG_KEY_1": "core.hooksPath",
                "GIT_CONFIG_VALUE_1": str(ROOT),
            }
            with mock.patch.dict(os.environ, hostile):
                env = fixture_git_env(repo)
            for var in _GIT_REPO_OVERRIDE_VARS:
                self.assertNotIn(var, env)
            self.assertNotIn("GIT_CONFIG", env)
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            for var, value in env.items():
                if not var.startswith("GIT_"):
                    continue
                if not isinstance(value, str):
                    continue
                if value in (os.devnull, "1", "0"):
                    continue
                if value.startswith("/"):
                    resolved = Path(value).resolve()
                    self.assertNotEqual(resolved, ROOT.resolve(), var)
                    with self.assertRaises(ValueError, msg=var):
                        resolved.relative_to(ROOT.resolve())
                else:
                    self.assertNotEqual(value, str(ROOT.resolve()), var)

    def test_fixture_git_env_ignores_hostile_template(self):
        """Hostile GIT_TEMPLATE_DIR must not supply fixture templates."""
        import unittest.mock as mock

        with fresh_sandbox() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir(parents=True)
            with tempfile.TemporaryDirectory() as outer:
                sentinel = Path(outer) / "sentinel-config"
                lb = chr(91)
                rb = chr(93)
                eq = chr(61)
                at = chr(64)
                section = lb + "user" + rb
                local = "opos-test"
                host = "example" + chr(46) + "com"
                content = section + "\n\tname " + eq + " opos-test\n\temail " + eq + " " + local + at + host + "\n"
                sentinel.write_text(content, encoding="utf-8")
                sentinel.chmod(0o600)
                before_bytes = sentinel.read_bytes()
                before_mode = stat.S_IMODE(sentinel.stat().st_mode)
                hostile = Path(outer) / "hostile-template"
                hostile.mkdir()
                os.symlink(str(sentinel), str(hostile / "config"))
                marker_name = "hostile-template-marker-7f3a9c2e"
                (hostile / marker_name).write_text("marker\n", encoding="utf-8")
                with mock.patch.dict(os.environ, {"GIT_TEMPLATE_DIR": str(hostile)}):
                    env = fixture_git_env(repo)
                template = Path(env["GIT_TEMPLATE_DIR"])
                self.assertTrue(template.is_dir())
                self.assertEqual(list(template.iterdir()), [])
                template.resolve().relative_to(_sandbox_for(repo))
                done = run(["git", "-C", str(repo), "init"], env)
                self.assertEqual(done.returncode, 0, done.stderr)
                cfg = repo / ".git" / "config"
                self.assertTrue(cfg.is_file())
                self.assertFalse(cfg.is_symlink())
                self.assertFalse((repo / ".git" / marker_name).exists())
                self.assertEqual(sentinel.read_bytes(), before_bytes)
                self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), before_mode)

    def test_fixture_git_env_drops_config_parameters(self):
        """Hostile GIT_CONFIG_PARAMETERS must not override effective git config."""
        import unittest.mock as mock

        with fresh_sandbox() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir(parents=True)
            hostile_dir = str(ROOT.resolve())
            q = chr(39)
            hostile_param = (
                q + "core.hooksPath=" + hostile_dir + q
                + " " + q + "core.worktree=" + hostile_dir + q
            )
            with mock.patch.dict(os.environ, {"GIT_CONFIG_PARAMETERS": hostile_param}):
                env = fixture_git_env(repo)
            self.assertNotIn("GIT_CONFIG_PARAMETERS", env)
            done = run(["git", "-C", str(repo), "init"], env)
            self.assertEqual(done.returncode, 0, done.stderr)
            hooks = run(["git", "-C", str(repo), "config", "--get", "core.hooksPath"], env)
            self.assertEqual(hooks.returncode, 0, hooks.stderr)
            self.assertEqual(hooks.stdout.strip(), os.devnull)
            worktree = run(["git", "-C", str(repo), "config", "--get", "core.worktree"], env)
            self.assertNotEqual(worktree.returncode, 0, worktree.stdout)
            listing = run(["git", "-C", str(repo), "config", "--list"], env)
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertNotIn(hostile_dir, listing.stdout)
            git_dir = run(["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"], env)
            self.assertEqual(git_dir.returncode, 0, git_dir.stderr)
            resolved_git_dir = Path(git_dir.stdout.strip()).resolve()
            resolved_git_dir.relative_to(Path(tmp).resolve())
            with self.assertRaises(ValueError):
                resolved_git_dir.relative_to(ROOT.resolve())
            top = run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], env)
            self.assertEqual(top.returncode, 0, top.stderr)
            resolved_top = Path(top.stdout.strip()).resolve()
            resolved_top.relative_to(Path(tmp).resolve())
            with self.assertRaises(ValueError):
                resolved_top.relative_to(ROOT.resolve())


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class SharedInstallTest(unittest.TestCase):
    """Tests sharing one real install."""

    @classmethod
    def setUpClass(cls):
        """Install once for the shared tests."""
        cls.tmp = fresh_sandbox()
        cls.env = make_env(Path(cls.tmp.name))
        cls.install = run_installer(cls.env, "--no-launch", "--yes")

    @classmethod
    def tearDownClass(cls):
        """Remove the shared temporary directory."""
        cls.tmp.cleanup()

    def test_real_install_and_idempotent(self):
        """Real install creates launcher, venv and manifest; rerun is identical."""
        self.assertEqual(self.install.returncode, 0, self.install.stderr)
        launcher = launcher_path(self.env)
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertTrue(stat.S_IMODE(launcher.stat().st_mode) & 0o111)
        venv_python = Path(self.env["OPOS_HOME"]) / "venv" / "bin" / "python"
        self.assertTrue(venv_python.exists())
        manifest = Path(self.env["OPOS_HOME"]) / "manifest"
        lines = manifest.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        data = dict(line.split("=", 1) for line in lines)
        self.assertEqual(set(data), {"launcher", "venv", "repo"})
        self.assertEqual(data["launcher"], str(launcher))
        self.assertEqual(data["venv"], str(Path(self.env["OPOS_HOME"]) / "venv"))
        self.assertEqual(data["repo"], str(ROOT))
        before = launcher.read_text(encoding="utf-8")
        status = run([str(launcher), "status"], self.env)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("configured: no", status.stdout)
        self.assertIn("config: ", status.stdout)
        self.assertIn("running: ", status.stdout)
        again = run_installer(self.env, "--no-launch", "--yes")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(launcher.read_text(encoding="utf-8"), before)

    def test_launcher_status_with_saved_config(self):
        """Status reports configured after saving a config."""
        with tempfile.TemporaryDirectory() as td:
            iso = dict(self.env)
            iso["OPOS_CONFIG_DIR"] = str(Path(td) / "cfg")
            saved = save_config(iso)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            self.assertTrue(config_files(iso))
            status = run([str(launcher_path(iso)), "status"], iso)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("configured: yes", status.stdout)

    def test_uninstall_dry_run(self):
        """Dry-run uninstall reports removals and changes nothing."""
        result = run_installer(self.env, "--uninstall", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would remove", result.stdout)
        self.assertTrue(launcher_path(self.env).is_file())
        self.assertTrue((Path(self.env["OPOS_HOME"]) / "venv" / "bin" / "python").exists())
        self.assertTrue((Path(self.env["OPOS_HOME"]) / "manifest").is_file())


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class InstallerTest(unittest.TestCase):
    """Tests using isolated temporary homes."""

    def test_help_and_unknown_option(self):
        """Help succeeds and mentions --uninstall; unknown options exit 2."""
        with fresh_sandbox() as tmp:
            env = make_env(Path(tmp))
            help_result = run_installer(env, "--help")
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("--uninstall", help_result.stdout + help_result.stderr)
            bad = run_installer(env, "--no-such-option")
            self.assertEqual(bad.returncode, 2)
            self.assertIn("usage", bad.stderr.lower())

    def test_dry_run_install_writes_nothing(self):
        """Dry-run install prints would lines and writes nothing."""
        with fresh_sandbox() as tmp:
            env = make_env(Path(tmp))
            Path(env["HOME"]).mkdir(parents=True)
            result = run_installer(env, "--dry-run", "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("would", result.stdout)
            self.assertTrue(any(line.startswith("==> ") for line in result.stdout.splitlines()))
            self.assertEqual(list(Path(env["HOME"]).iterdir()), [])

    def test_no_suitable_python(self):
        """Missing or failing OPOS_PYTHON exits 1 and writes nothing."""
        for candidate in ("/nonexistent/python", None):
            with self.subTest(candidate=candidate), fresh_sandbox() as tmp:
                base = Path(tmp)
                env = make_env(base)
                Path(env["HOME"]).mkdir(parents=True)
                if candidate is None:
                    fake = base / "fake-python"
                    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                    fake.chmod(0o755)
                    env["OPOS_PYTHON"] = str(fake)
                else:
                    env["OPOS_PYTHON"] = candidate
                result = run_installer(env, "--no-launch", "--yes")
                self.assertEqual(result.returncode, 1)
                self.assertIn("Python 3.11 or newer is required", result.stderr)
                self.assertEqual(list(Path(env["HOME"]).iterdir()), [])

    def install_in(self, base):
        """Install into base and return its environment."""
        env = make_env(base)
        result = run_installer(env, "--no-launch", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        return env

    def test_uninstall_keep_config(self):
        """Uninstall with --keep-config removes the install but keeps config."""
        with disposable_sandbox() as tmp:
            env = self.install_in(Path(tmp))
            saved = save_config(env)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            configs = config_files(env)
            self.assertTrue(configs)
            result = run_installer(env, "--uninstall", "--keep-config", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(launcher_path(env).exists())
            self.assertFalse(Path(env["OPOS_HOME"]).exists())
            for path in configs:
                self.assertTrue(path.is_file())

    def test_uninstall_full_and_second_run(self):
        """Full uninstall leaves no files or opos dirs; a rerun is a no-op."""
        with disposable_sandbox() as tmp:
            env = self.install_in(Path(tmp))
            saved = save_config(env)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            result = run_installer(env, "--uninstall", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            home = Path(env["HOME"])
            files = [p for p in home.rglob("*") if p.is_file()] if home.exists() else []
            self.assertEqual(files, [])
            dirs = [p for p in home.rglob("*") if p.is_dir()] if home.exists() else []
            self.assertFalse(any("opos" in p.name for p in dirs))
            again = run_installer(env, "--uninstall", "--yes")
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("==> nothing to remove", again.stdout)

    def test_repo_not_modified(self):
        """An install and uninstall cycle does not modify the repo."""
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        probe = run(["git", "-C", str(ROOT), "status", "--porcelain"], dict(os.environ))
        if probe.returncode != 0:
            self.skipTest("not a git checkout")
        before = probe.stdout
        with disposable_sandbox() as tmp:
            env = self.install_in(Path(tmp))
            result = run_installer(env, "--uninstall", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
        after = run(["git", "-C", str(ROOT), "status", "--porcelain"], dict(os.environ))
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertEqual(after.stdout, before)

    def test_install_ps1_static(self):
        """install.ps1 exists with SPDX header and expected strings."""
        self.assertTrue(INSTALL_PS1.is_file())
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# SPDX-License-Identifier: GPL-3.0-or-later"))
        for needle in ("DryRun", "Uninstall", "NoLaunch", "3, 11", "opos.setup.selftest", "must end in 'opos'"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_install_ps1_uninstall_deletion_scope(self):
        import re

        text = INSTALL_PS1.read_text(encoding="utf-8")
        start = text.index("if ($Uninstall) {")
        end = text.index("if ($env:OPOS_PYTHON)", start)
        block = text[start:end]
        loop = re.search(
            r"foreach\s*\(\$p\s+in\s+@\(\$Launcher,\s*\$VenvDir,\s*\$Manifest\)\)",
            block,
        )
        self.assertIsNotNone(loop, "cleanup loop must list exactly the three owned paths")
        self.assertNotIn("@($Launcher, $VenvDir, $Manifest, $OposHome)", block)
        self.assertIn("Test-Path -LiteralPath", block)
        self.assertIn("Remove-Item -LiteralPath", block)
        cond = re.search(r"\$p\s+-eq\s+\$VenvDir[\s\S]*?-Recurse", block)
        self.assertIsNotNone(cond, "recursive removal must be conditional on equality with $VenvDir")
        self.assertIn("Get-ChildItem -LiteralPath $OposHome -Force", block)
        empty = re.search(
            r"@\(Get-ChildItem\s+-LiteralPath\s+\$OposHome\s+-Force\)\.Count\s+-eq\s+0",
            block,
        )
        self.assertIsNotNone(empty, "home removal must follow an empty child-enumeration check")
        home_removals = [line for line in block.splitlines() if "Remove-Item" in line and "$OposHome" in line]
        self.assertTrue(home_removals, "home removal must exist")
        for line in home_removals:
            self.assertNotIn("-Recurse", line)
        self.assertGreaterEqual(len(re.findall(r"\$DryRun", block)), 2)

    def test_install_ps1_recursion_exclusive_to_venv(self):
        """Recursion only for venv; launcher and manifest removals preserve nested files."""
        text = INSTALL_PS1.read_text(encoding="utf-8")
        start = text.index("if ($Uninstall) {")
        end = text.index("if ($env:OPOS_PYTHON)", start)
        block = text[start:end]
        loop_start = block.index("foreach ($p in @($Launcher, $VenvDir, $Manifest))")
        loop_end = block.index("if ((Test-Path -LiteralPath $OposHome)", loop_start)
        loop = block[loop_start:loop_end]
        self.assertEqual(loop.count("-Recurse"), 1, "only venv removal may recurse")
        venv_idx = loop.index("$p -eq $VenvDir")
        recurse_idx = loop.index("-Recurse")
        else_idx = loop.index("} else {")
        self.assertLess(venv_idx, recurse_idx)
        self.assertLess(recurse_idx, else_idx)
        self.assertIn("Remove-Item", loop[else_idx:])
        self.assertNotIn("-Recurse", loop[else_idx:])
        sh_text = INSTALL_SH.read_text(encoding="utf-8")
        func_start = sh_text.index("remove_path() {")
        func_end = sh_text.index("is_hardening_active()", func_start)
        func = sh_text[func_start:func_end]
        token_prefix = "".join([chr(103), chr(104), chr(112), chr(95)])
        ip = ".".join(["".join([chr(49), chr(48)]), chr(48), chr(48), chr(55)])
        begin = "".join([chr(66), chr(69), chr(71), chr(73), chr(78)])
        private = "".join([chr(80), chr(82), chr(73), chr(86), chr(65), chr(84), chr(69)])
        key = "".join([chr(75), chr(69), chr(89)])
        pem = "".join([chr(45) * 5, begin, chr(32), private, chr(32), key, chr(45) * 5])
        content = "\n".join([token_prefix + "X" * 32, ip, pem]) + "\n"
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            home = base / "home"
            opos_home = home / ".local" / "share" / "opos"
            bin_dir = home / ".local" / "bin"
            launcher_dir = bin_dir / "opos"
            manifest_dir = opos_home / "manifest"
            venv_dir = opos_home / "venv"
            for target in (launcher_dir, manifest_dir):
                nested = target / "nested" / "keep.txt"
                nested.parent.mkdir(parents=True)
                nested.write_text(content, encoding="utf-8")
                script = "FOUND=0\nDRY=0\n" + 'HOME="' + str(home) + '"\n' + func + 'remove_path "' + str(target) + '" f\n'
                done = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=dict(os.environ), timeout=TIMEOUT)
                self.assertNotEqual(done.returncode, 0, done.stderr)
                self.assertTrue(nested.is_file())
                self.assertEqual(nested.read_text(encoding="utf-8"), content)
                self.assertTrue(target.is_dir())
                shutil.rmtree(target)
                target.write_text(content, encoding="utf-8")
                script = "FOUND=0\nDRY=0\n" + 'HOME="' + str(home) + '"\n' + func + 'remove_path "' + str(target) + '" f\n'
                done = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=dict(os.environ), timeout=TIMEOUT)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertFalse(target.exists())
            nested_venv = venv_dir / "lib" / "keep.txt"
            nested_venv.parent.mkdir(parents=True)
            nested_venv.write_text(content, encoding="utf-8")
            script = "FOUND=0\nDRY=0\n" + 'HOME="' + str(home) + '"\n' + func + 'remove_path "' + str(venv_dir) + '" d\n'
            done = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=dict(os.environ), timeout=TIMEOUT)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertFalse(venv_dir.exists())
            self.assertFalse(nested_venv.exists())

    def test_install_ps1_uninstall_executes_exclusively(self):
        """Execute PowerShell uninstall loop; only venv recurses."""
        import re

        text = INSTALL_PS1.read_text(encoding="utf-8")
        start = text.index("if ($Uninstall) {")
        end = text.index("if ($env:OPOS_PYTHON)", start)
        block = text[start:end]
        loop_start = block.index("foreach ($p in @($Launcher, $VenvDir, $Manifest))")
        loop_end = block.index("if ((Test-Path -LiteralPath $OposHome)", loop_start)
        loop = block[loop_start:loop_end]
        cond = re.search(r"elseif\s*\(([^)]*)\)[^{]*\{[^}]*-Recurse", loop)
        self.assertIsNotNone(cond, "recursive branch must be guarded")
        token_prefix = "".join([chr(103), chr(104), chr(112), chr(95)])
        ip = ".".join(["".join([chr(49), chr(48)]), chr(48), chr(48), chr(55)])
        begin = "".join([chr(66), chr(69), chr(71), chr(73), chr(78)])
        private = "".join([chr(80), chr(82), chr(73), chr(86), chr(65), chr(84), chr(69)])
        key = "".join([chr(75), chr(69), chr(89)])
        pem = "".join([chr(45) * 5, begin, chr(32), private, chr(32), key, chr(45) * 5])
        content = "\n".join([token_prefix + "X" * 32, ip, pem]) + "\n"
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            opos_home = base / "home" / "opos"
            bin_dir = base / "bin"
            launcher = bin_dir / "opos"
            manifest = opos_home / "manifest"
            venv = opos_home / "venv"
            for target in (launcher, manifest, venv):
                nested = target / "nested" / "keep.txt"
                nested.parent.mkdir(parents=True)
                nested.write_text(content, encoding="utf-8")
            pwsh = shutil.which("pwsh") or shutil.which("powershell")
            if pwsh is not None:
                def _q(p):
                    return "'" + str(p).replace("'", "''") + "'"
                wrapper = (
                    "function Say([string]$m) { Write-Host \"==> $m\" }\n"
                    "$found=$false\n"
                    "$ErrorActionPreference='Continue'\n"
                    "$Launcher=" + _q(launcher) + "\n"
                    "$VenvDir=" + _q(venv) + "\n"
                    "$Manifest=" + _q(manifest) + "\n"
                    "$OposHome=" + _q(opos_home) + "\n"
                    "$DryRun=$false\n"
                    + loop
                )
                script = base / "run_uninstall.ps1"
                script.write_text(wrapper, encoding="utf-8")
                subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-File", str(script)], capture_output=True, text=True, timeout=TIMEOUT)
            else:
                self.skipTest("PowerShell unavailable")
            for target in (launcher, manifest):
                nested = target / "nested" / "keep.txt"
                self.assertTrue(nested.is_file())
                self.assertEqual(nested.read_text(encoding="utf-8"), content)
            self.assertFalse(venv.exists())

    def test_install_ps1_uninstall_skips_without_powershell(self):
        import unittest.mock as mock

        with mock.patch.object(shutil, "which", return_value=None):
            with self.assertRaises(unittest.SkipTest) as ctx:
                self.test_install_ps1_uninstall_executes_exclusively()
        self.assertEqual(str(ctx.exception), "PowerShell unavailable")

    def test_install_ps1_artifact_encoding(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        start = text.index("New-Item -ItemType Directory -Force $OposHome | Out-Null")
        end = text.index('Say "wrote launcher $Launcher"', start)
        self.assertLess(start, end)
        block = text[start:end]
        self.assertIn("$enc = [System.Text.UTF8Encoding]::new($false)", block)
        bat_start = block.index("$bat = @(")
        bat_end = block.index("[System.IO.File]::WriteAllText", bat_start)
        bat_text = block[bat_start:bat_end]
        probes = ["'@echo off'", "'chcp 65001 >nul'", 'set "PYTHONPATH=', "-m opos.setup %*"]
        idxs = [bat_text.find(p) for p in probes]
        for probe, idx in zip(probes, idxs):
            with self.subTest(probe=probe):
                self.assertGreaterEqual(idx, 0)
        self.assertEqual(idxs, sorted(idxs))
        self.assertIn("('set \"PYTHONPATH=' + $BatchSrc + '\"')", bat_text)
        self.assertIn("('\"' + $BatchPy + '\" -m opos.setup %*')", bat_text)
        self.assertEqual(block.count("[System.IO.File]::WriteAllText("), 2)
        self.assertIn("WriteAllText($Launcher, ($bat -join \"`r`n\"), $enc)", block)
        self.assertIn("WriteAllText($Manifest, \"launcher=$Launcher`r`nvenv=$VenvDir`r`nrepo=$Root\", $enc)", block)
        self.assertNotIn("Set-Content", block)
        self.assertNotIn("encoding ascii", block.lower())

    def test_sh_syntax(self):
        """install.sh parses under sh -n."""
        result = run(["sh", "-n", str(INSTALL_SH)], dict(os.environ))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_space_path_install_fails(self):
        """Real install with spaced OPOS_HOME exits 1 and writes nothing."""
        with fresh_sandbox() as tmp:
            base = Path(tmp) / "my home"
            base.mkdir(parents=True)
            env = make_env(base)
            Path(env["HOME"]).mkdir(parents=True)
            result = run_installer(env, "--no-launch", "--yes")
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not contain spaces", result.stderr)
            self.assertEqual(list(Path(env["HOME"]).iterdir()), [])

    def test_space_path_dry_run_fails(self):
        """Dry run with spaced OPOS_HOME exits 1 and writes nothing."""
        with fresh_sandbox() as tmp:
            base = Path(tmp) / "my home"
            base.mkdir(parents=True)
            env = make_env(base)
            Path(env["HOME"]).mkdir(parents=True)
            result = run_installer(env, "--dry-run", "--no-launch", "--yes")
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not contain spaces", result.stderr)
            self.assertEqual(list(Path(env["HOME"]).iterdir()), [])

    def test_no_launch_stdout_without_duplicate_segment(self):
        """Real install stdout with --no-launch never contains opos/opos."""
        with fresh_sandbox() as tmp:
            env = make_env(Path(tmp))
            result = run_installer(env, "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("opos/opos", result.stdout)

    def test_dry_run_wording(self):
        """Dry-run wording uses would prefix and expected summary."""
        with fresh_sandbox() as tmp:
            env = make_env(Path(tmp))
            Path(env["HOME"]).mkdir(parents=True)
            result = run_installer(env, "--dry-run", "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            for line in result.stdout.splitlines():
                if line.startswith("==> "):
                    self.assertTrue(
                        line.startswith("==> would ") or line == "==> dry run finished, nothing was written",
                        line,
                    )
            self.assertNotIn("installed:", result.stdout)
            self.assertIn("would use Python", result.stdout)

    def test_venv_without_pip_fallback(self):
        """Fallback to venv without pip when ensurepip is missing."""
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            env = make_env(base)
            Path(env["HOME"]).mkdir(parents=True)
            fake = base / "fake-python"
            fake.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "-c" ]; then\n'
                "  exit 0\n"
                "fi\n"
                'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
                "  has_without_pip=0\n"
                '  for arg in "$@"; do\n'
                '    if [ "$arg" = "--without-pip" ]; then\n'
                "      has_without_pip=1\n"
                "    fi\n"
                "  done\n"
                '  if [ "$has_without_pip" -eq 1 ]; then\n'
                '    exec "$REAL_PYTHON" "$@"\n'
                "  else\n"
                '    echo "ensurepip missing" >&2\n'
                "    exit 1\n"
                "  fi\n"
                "fi\n"
                'exec "$REAL_PYTHON" "$@"\n',
                encoding="utf-8",
            )
            fake.chmod(0o755)
            env["OPOS_PYTHON"] = str(fake)
            env["REAL_PYTHON"] = sys.executable
            result = run_installer(env, "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "venv created without pip (ensurepip missing; OPOS has no dependencies)",
                result.stdout,
            )
            venv = Path(env["OPOS_HOME"]) / "venv"
            venv_python = venv / "bin" / "python"
            with self.subTest(name="created_layout"):
                self.assertTrue(venv_python.is_file())
                self.assertTrue((venv / "pyvenv.cfg").is_file())
                prefix = run([str(venv_python), "-c", "import sys; print(sys.prefix)"], env)
                self.assertEqual(prefix.returncode, 0, prefix.stderr)
                self.assertEqual(Path(prefix.stdout.strip()).resolve(), venv.resolve())
            with self.subTest(name="direct_host_python_symlink"):
                venv_python.unlink()
                os.symlink(sys.executable, venv_python)
                self.assertTrue(venv_python.is_symlink())
                self.assertEqual(os.readlink(venv_python), sys.executable)
                self.assertTrue(venv_python.is_file())
                self.assertTrue((venv / "pyvenv.cfg").is_file())
                prefix = run([str(venv_python), "-c", "import sys; print(sys.prefix)"], env)
                self.assertEqual(prefix.returncode, 0, prefix.stderr)
                self.assertEqual(Path(prefix.stdout.strip()).resolve(), venv.resolve())

    def test_f205_failed_venv_cleans_own_staging_preserves_foreign_staging(self):
        """Failed venv creation must not delete a pre-existing venv directory."""
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            env = make_env(base)
            venv_path = Path(env["OPOS_HOME"]) / "venv"
            venv_path.mkdir(parents=True)
            sentinel = venv_path / "sentinel.bin"
            sentinel_data = b"sentinel:" + os.urandom(16)
            sentinel.write_bytes(sentinel_data)
            nested = venv_path / "subdir" / "keep.txt"
            nested.parent.mkdir(parents=True)
            nested_data = b"keep-me-exact"
            nested.write_bytes(nested_data)
            pre_files = sorted(p.relative_to(venv_path).as_posix() for p in venv_path.rglob("*") if p.is_file())
            fake = base / "fake-python"
            fake.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "-c" ]; then exit 0; fi\n'
                'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then echo "fake venv failure" >&2; exit 1; fi\n'
                'exec "$REAL_PYTHON" "$@"\n',
                encoding="utf-8",
            )
            fake.chmod(0o755)
            env["OPOS_PYTHON"] = str(fake)
            env["REAL_PYTHON"] = sys.executable
            stale = Path(env["OPOS_HOME"]) / ".venv-staging.STALE"
            stale.mkdir(parents=True)
            stale_marker = stale / "marker.txt"
            stale_data = b"stale-marker-f205"
            stale_marker.write_bytes(stale_data)
            result = run_installer(env, "--no-launch", "--yes")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sudo apt install python3-venv", result.stderr)
            self.assertTrue(sentinel.is_file(), "sentinel was deleted by failed venv creation")
            self.assertEqual(sentinel.read_bytes(), sentinel_data)
            self.assertTrue(nested.is_file())
            self.assertEqual(nested.read_bytes(), nested_data)
            self.assertTrue(venv_path.is_dir())
            post_files = sorted(p.relative_to(venv_path).as_posix() for p in venv_path.rglob("*") if p.is_file())
            self.assertEqual(post_files, pre_files)
            self.assertFalse((venv_path / "bin" / "python").exists())
            self.assertFalse((venv_path / "pyvenv.cfg").exists())
            opos_home = Path(env["OPOS_HOME"])
            leftovers = list(opos_home.glob(".venv-staging.*"))
            self.assertEqual(leftovers, [stale], f"staging not cleaned after failure: {leftovers}")
            self.assertEqual(stale_marker.read_bytes(), stale_data)
            self.assertFalse((Path(env["OPOS_HOME"]) / "manifest").exists())
            self.assertFalse(launcher_path(env).exists())

    def test_successful_install_cleans_staging(self):
        """Successful install removes its own staging while preserving pre-existing staging."""
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            env = make_env(base)
            opos_home = Path(env["OPOS_HOME"])
            opos_home.mkdir(parents=True)
            stale = opos_home / ".venv-staging.STALE"
            stale.mkdir()
            stale_marker = stale / "marker.txt"
            stale_marker.write_text("stale", encoding="utf-8")
            result = run_installer(env, "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((opos_home / "venv" / "bin" / "python").is_file())
            self.assertTrue((opos_home / "manifest").is_file())
            self.assertTrue(launcher_path(env).is_file())
            leftovers = list(opos_home.glob(".venv-staging.*"))
            self.assertEqual(leftovers, [stale], f"unexpected staging: {leftovers}")
            self.assertEqual(stale_marker.read_text(encoding="utf-8"), "stale")

    def test_ensure_venv_preserves_other_staging(self):
        """ensure_venv keeps foreign staging for normal and dry runs."""
        for dry in (0, 1):
            with self.subTest(dry=dry), fresh_sandbox() as tmp:
                env = make_env(Path(tmp))
                opos_home = Path(env["OPOS_HOME"])
                other_staging = opos_home / ".venv-staging.other"
                sentinel = other_staging / "venv" / "sentinel"
                sentinel.parent.mkdir(parents=True)
                expected = b"other-staging-sentinel"
                sentinel.write_bytes(expected)
                venv_python = opos_home / "venv" / "bin" / "python"
                venv_python.parent.mkdir(parents=True)
                venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                venv_python.chmod(0o755)
                prefix = INSTALL_SH.read_text(encoding="utf-8").split("install_requirements() {", 1)[0]
                script = prefix + "DRY=" + str(dry) + "\n" + 'VENV="' + str(opos_home / "venv") + '"\nensure_venv\n'
                done = subprocess.run(
                    ["sh"],
                    input=script,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=TIMEOUT,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(list(opos_home.glob(".venv-staging.*")), [other_staging])
                self.assertEqual(sentinel.read_bytes(), expected)

    def test_interrupted_install_cleans_staging(self):
        """Interrupted install exits 130/143 and leaves no staging."""
        cases = (
            (signal.SIGINT, 130),
            (signal.SIGTERM, 143),
        )
        targets = ("shell", "group")
        for sig, expected in cases:
            for target in targets:
                with self.subTest(sig=sig.name, target=target):
                    with fresh_sandbox() as tmp:
                        base = Path(tmp)
                        env = make_env(base)
                        ready = base / "ready"
                        release = base / "release"
                        stub = base / "stub-python"
                        stub.write_text(
                            "#!" + sys.executable + "\n"
                            "import os\n"
                            "import signal\n"
                            "import sys\n"
                            "import time\n"
                            "from pathlib import Path\n"
                            "ready = Path(" + repr(str(ready)) + ")\n"
                            "release = Path(" + repr(str(release)) + ")\n"
                            "argv = sys.argv\n"
                            "if len(argv) > 1 and argv[1] == '-c':\n"
                            "    sys.exit(0)\n"
                            "if argv[1:3] == ['-m', 'venv']:\n"
                            "    Path(argv[-1]).mkdir(parents=True, exist_ok=True)\n"
                            "    signal.signal(signal.SIGINT, lambda n, f: os._exit(0))\n"
                            "    signal.signal(signal.SIGTERM, lambda n, f: os._exit(0))\n"
                            "    ready.write_text('ready', encoding='utf-8')\n"
                            "    end = time.monotonic() + 30\n"
                            "    while time.monotonic() < end:\n"
                            "        if release.exists():\n"
                            "            sys.exit(0)\n"
                            "        time.sleep(0.05)\n"
                            "    sys.exit(3)\n"
                            "print(argv[1:], file=sys.stderr)\n"
                            "sys.exit(2)\n",
                            encoding="utf-8",
                        )
                        stub.chmod(0o755)
                        env["OPOS_PYTHON"] = str(stub)

                        def _restore():
                            signal.signal(signal.SIGINT, signal.SIG_DFL)
                            signal.signal(signal.SIGTERM, signal.SIG_DFL)

                        assert_sandboxed(env, ("--no-launch", "--yes"))
                        proc = None
                        try:
                            proc = subprocess.Popen(
                                ["sh", str(INSTALL_SH), "--no-launch", "--yes"],
                                env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                start_new_session=True,
                                preexec_fn=_restore,
                            )
                            end = time.monotonic() + 30
                            while True:
                                if proc.poll() is not None:
                                    out, err = proc.communicate()
                                    self.fail(
                                        "installer exited early: %s stdout=%r stderr=%r"
                                        % (proc.returncode, out, err)
                                    )
                                if ready.exists():
                                    break
                                if time.monotonic() >= end:
                                    self.fail("stub never became ready")
                                time.sleep(0.05)
                            opos_home = Path(env["OPOS_HOME"])
                            self.assertTrue(
                                list(opos_home.glob(".venv-staging.*")),
                                "no staging directory before signal",
                            )
                            if target == "group":
                                os.killpg(proc.pid, sig)
                            else:
                                proc.send_signal(sig)
                                time.sleep(0.5)
                                release.write_text("go", encoding="utf-8")
                            out, err = proc.communicate(timeout=TIMEOUT)
                            self.assertEqual(proc.returncode, expected, err)
                            leftovers = (
                                list(opos_home.glob(".venv-staging.*"))
                                if opos_home.exists()
                                else []
                            )
                            self.assertEqual(leftovers, [], "staging left: %r" % (leftovers,))
                            self.assertFalse((opos_home / "manifest").exists())
                            self.assertFalse(launcher_path(env).exists())
                        finally:
                            if proc is not None:
                                try:
                                    os.killpg(proc.pid, signal.SIGKILL)
                                except (ProcessLookupError, PermissionError):
                                    pass
                                if proc.returncode is None:
                                    try:
                                        proc.communicate(timeout=10)
                                    except subprocess.TimeoutExpired:
                                        proc.kill()
                                        proc.wait(timeout=10)

    def test_interrupted_child_failure_cleans_staging(self):
        """Child signal death or nonzero exit keeps 130/143 and leaves no staging."""
        cases = (
            (signal.SIGINT, 130),
            (signal.SIGTERM, 143),
        )
        modes = ("default", "nonzero")
        for sig, expected in cases:
            for mode in modes:
                with self.subTest(sig=sig.name, mode=mode):
                    with fresh_sandbox() as tmp:
                        base = Path(tmp)
                        env = make_env(base)
                        ready = base / "ready"
                        stub = base / "stub-python"
                        if mode == "default":
                            handlers = (
                                "    signal.signal(signal.SIGINT, signal.SIG_DFL)\n"
                                "    signal.signal(signal.SIGTERM, signal.SIG_DFL)\n"
                            )
                        else:
                            handlers = (
                                "    signal.signal(signal.SIGINT, lambda n, f: os._exit(%d))\n"
                                "    signal.signal(signal.SIGTERM, lambda n, f: os._exit(%d))\n"
                            ) % (expected, expected)
                        stub.write_text(
                            "#!" + sys.executable + "\n"
                            "import os\n"
                            "import signal\n"
                            "import sys\n"
                            "import time\n"
                            "from pathlib import Path\n"
                            "ready = Path(" + repr(str(ready)) + ")\n"
                            "argv = sys.argv\n"
                            "if len(argv) > 1 and argv[1] == '-c':\n"
                            "    sys.exit(0)\n"
                            "if argv[1:3] == ['-m', 'venv']:\n"
                            "    Path(argv[-1]).mkdir(parents=True, exist_ok=True)\n"
                            + handlers +
                            "    ready.write_text('ready', encoding='utf-8')\n"
                            "    end = time.monotonic() + 30\n"
                            "    while time.monotonic() < end:\n"
                            "        time.sleep(0.05)\n"
                            "    sys.exit(3)\n"
                            "print(argv[1:], file=sys.stderr)\n"
                            "sys.exit(2)\n",
                            encoding="utf-8",
                        )
                        stub.chmod(0o755)
                        env["OPOS_PYTHON"] = str(stub)

                        def _restore():
                            signal.signal(signal.SIGINT, signal.SIG_DFL)
                            signal.signal(signal.SIGTERM, signal.SIG_DFL)

                        assert_sandboxed(env, ("--no-launch", "--yes"))
                        proc = None
                        try:
                            proc = subprocess.Popen(
                                ["sh", str(INSTALL_SH), "--no-launch", "--yes"],
                                env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                start_new_session=True,
                                preexec_fn=_restore,
                            )
                            end = time.monotonic() + 30
                            while True:
                                if proc.poll() is not None:
                                    out, err = proc.communicate()
                                    self.fail(
                                        "installer exited early: %s stdout=%r stderr=%r"
                                        % (proc.returncode, out, err)
                                    )
                                if ready.exists():
                                    break
                                if time.monotonic() >= end:
                                    self.fail("stub never became ready")
                                time.sleep(0.05)
                            opos_home = Path(env["OPOS_HOME"])
                            self.assertTrue(
                                list(opos_home.glob(".venv-staging.*")),
                                "no staging directory before signal",
                            )
                            os.killpg(proc.pid, sig)
                            out, err = proc.communicate(timeout=TIMEOUT)
                            self.assertEqual(proc.returncode, expected, err)
                            leftovers = (
                                list(opos_home.glob(".venv-staging.*"))
                                if opos_home.exists()
                                else []
                            )
                            self.assertEqual(leftovers, [], "staging left: %r" % (leftovers,))
                            self.assertFalse((opos_home / "manifest").exists())
                            self.assertFalse(launcher_path(env).exists())
                        finally:
                            if proc is not None:
                                try:
                                    os.killpg(proc.pid, signal.SIGKILL)
                                except (ProcessLookupError, PermissionError):
                                    pass
                                if proc.returncode is None:
                                    try:
                                        proc.communicate(timeout=10)
                                    except subprocess.TimeoutExpired:
                                        proc.kill()
                                        proc.wait(timeout=10)

    def test_venv_without_pip_fallback_uses_staging(self):
        """Fallback without pip creates both venvs under staging before promotion."""
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            env = make_env(base)
            Path(env["HOME"]).mkdir(parents=True)
            log = base / "venv-dests.log"
            fake = base / "fake-python"
            fake.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "-c" ]; then\n'
                "  exit 0\n"
                "fi\n"
                'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
                "  venv_path=\"\"\n"
                '  for arg in "$@"; do\n'
                '    venv_path="$arg"\n'
                "  done\n"
                '  printf "%s\\n" "$venv_path" >> "$OPOS_VENV_LOG"\n'
                "  has_without_pip=0\n"
                '  for arg in "$@"; do\n'
                '    if [ "$arg" = "--without-pip" ]; then\n'
                "      has_without_pip=1\n"
                "    fi\n"
                "  done\n"
                '  if [ "$has_without_pip" -eq 1 ]; then\n'
                '    exec "$REAL_PYTHON" "$@"\n'
                "  else\n"
                '    echo "ensurepip missing" >&2\n'
                "    exit 1\n"
                "  fi\n"
                "fi\n"
                'exec "$REAL_PYTHON" "$@"\n',
                encoding="utf-8",
            )
            fake.chmod(0o755)
            env["OPOS_PYTHON"] = str(fake)
            env["REAL_PYTHON"] = sys.executable
            env["OPOS_VENV_LOG"] = str(log)
            result = run_installer(env, "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "venv created without pip (ensurepip missing; OPOS has no dependencies)",
                result.stdout,
            )
            opos_home = Path(env["OPOS_HOME"])
            venv = opos_home / "venv"
            self.assertTrue((venv / "bin" / "python").is_file())
            self.assertTrue((venv / "pyvenv.cfg").is_file())
            self.assertFalse((venv / "venv").exists())
            self.assertEqual(list(opos_home.glob(".venv-staging.*")), [])
            dests = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(dests), 2, dests)
            staging_prefix = str(opos_home / ".venv-staging.")
            for dest in dests:
                self.assertNotEqual(os.path.realpath(dest), os.path.realpath(str(venv)), dests)
                self.assertTrue(dest.startswith(staging_prefix), dests)
            prefix = run([str(venv / "bin" / "python"), "-c", "import sys; print(sys.prefix)"], env)
            self.assertEqual(prefix.returncode, 0, prefix.stderr)
            self.assertEqual(Path(prefix.stdout.strip()).resolve(), venv.resolve())

    def test_existing_venv_replaced_on_success(self):
        """Replacing existing VENV removes old contents and avoids nesting."""
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            env = self.install_in(base)
            opos_home = Path(env["OPOS_HOME"])
            venv = opos_home / "venv"
            prefix = run([str(venv / "bin" / "python"), "-c", "import sys; print(sys.prefix)"], env)
            self.assertEqual(prefix.returncode, 0, prefix.stderr)
            self.assertEqual(Path(prefix.stdout.strip()).resolve(), venv.resolve())
            status = run([str(launcher_path(env)), "status"], env)
            self.assertEqual(status.returncode, 0, status.stderr)
            sentinel = venv / "sentinel.bin"
            token_prefix = "".join([chr(103), chr(104), chr(112), chr(95)])
            ip = ".".join(
                [
                    "".join([chr(49), chr(48)]),
                    chr(48),
                    chr(48),
                    chr(55),
                ]
            )
            begin = "".join([chr(66), chr(69), chr(71), chr(73), chr(78)])
            private = "".join([chr(80), chr(82), chr(73), chr(86), chr(65), chr(84), chr(69)])
            key = "".join([chr(75), chr(69), chr(89)])
            pem = "".join([chr(45) * 5, begin, chr(32), private, chr(32), key, chr(45) * 5])
            content = "\n".join([token_prefix + "X" * 32, ip, pem]) + "\n"
            sentinel.write_text(content, encoding="utf-8")
            nested = venv / "old_dir" / "keep.txt"
            nested.parent.mkdir(parents=True)
            nested.write_text(content, encoding="utf-8")
            (venv / "bin" / "python").unlink()
            result = run_installer(env, "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((venv / "bin" / "python").is_file())
            self.assertFalse(sentinel.exists())
            self.assertFalse(nested.exists())
            self.assertFalse((venv / "venv").exists())
            self.assertFalse((venv / "venv" / "bin" / "python").exists())
            self.assertEqual(list(opos_home.glob(".venv-staging.*")), [])
            self.assertTrue((opos_home / "manifest").is_file())
            self.assertTrue(launcher_path(env).is_file())
            replaced = run([str(venv / "bin" / "python"), "-c", "import sys; print(sys.prefix)"], env)
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            self.assertEqual(Path(replaced.stdout.strip()).resolve(), venv.resolve())
            restored = run([str(launcher_path(env)), "status"], env)
            self.assertEqual(restored.returncode, 0, restored.stderr)

    def test_bootstrap_clones_then_installs(self):
        """Fresh HOME gets default checkout and installer receives required flags."""
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir(parents=True)
            repo = base / "fixture-repo"
            repo.mkdir(parents=True)
            marker = home / "marker"
            installer = repo / "install.sh"
            installer.write_text(
                "#!/bin/sh\n"
                'd="$(cd "$(dirname "$0")" && pwd -P)"\n'
                'printf "%s\\n" "$d" "$*" > "' + str(marker) + '"\n',
                encoding="utf-8",
            )
            installer.chmod(0o755)
            git_env = fixture_git_env(repo)
            for args in (
                ["git", "-C", str(repo), "init"],
                ["git", "-C", str(repo), "config", "user.email", "opos-test@example.com"],
                ["git", "-C", str(repo), "config", "user.name", "opos-test"],
                ["git", "-C", str(repo), "add", "install.sh"],
                ["git", "-C", str(repo), "commit", "-m", "fixture"],
                ["git", "-C", str(repo), "branch", "-M", "main"],
            ):
                done = subprocess.run(args, capture_output=True, text=True, env=git_env, timeout=TIMEOUT)
                self.assertEqual(done.returncode, 0, done.stderr)
            env = {
                "HOME": str(home),
                "PATH": os.environ.get("PATH", ""),
                "OPOS_REPO_URL": str(repo),
            }
            result = run(["sh", str(ROOT / "bootstrap.sh")], env)
            self.assertEqual(result.returncode, 0, result.stderr)
            dest = home / "opos-src"
            self.assertTrue((dest / ".git").is_dir())
            lines = marker.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], str(dest.resolve()))
            self.assertEqual(lines[1], "--yes --no-launch")


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class UninstallRecoveryTest(unittest.TestCase):
    """Regression for uninstall failure propagation and verified recovery."""

    def test_propagates_failure_and_retains_artifacts(self):
        """Stub launcher failure propagates and retains launcher, venv, manifest and config; success removes only owned artifacts."""
        with disposable_sandbox() as tmp:
            base = Path(tmp)
            env = make_env(base)
            result = run_installer(env, "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = save_config(env)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            launcher = launcher_path(env)
            manifest = Path(env["OPOS_HOME"]) / "manifest"
            venv_python = Path(env["OPOS_HOME"]) / "venv" / "bin" / "python"
            config_dir = Path(env["OPOS_CONFIG_DIR"])
            launcher_bytes = launcher.read_bytes()
            manifest_bytes = manifest.read_bytes()
            configs_before = {p: p.read_bytes() for p in config_files(env)}
            self.assertTrue(configs_before)
            unowned = Path(env["HOME"]) / ".local" / "share" / "other" / "keep.txt"
            unowned.parent.mkdir(parents=True, exist_ok=True)
            unowned.write_text("important", encoding="utf-8")
            stub = "#!/bin/sh\necho stub failing >&2\nexit 42\n"
            launcher.write_text(stub, encoding="utf-8")
            launcher.chmod(0o755)
            stub_bytes = launcher.read_bytes()
            result_fail = run_installer(env, "--uninstall", "--yes")
            self.assertEqual(result_fail.returncode, 42, result_fail.stderr)
            self.assertIn("launcher uninstall failed", result_fail.stderr.lower())
            self.assertTrue(launcher.is_file())
            self.assertEqual(launcher.read_bytes(), stub_bytes)
            self.assertTrue(venv_python.exists())
            self.assertTrue(manifest.is_file())
            self.assertEqual(manifest.read_bytes(), manifest_bytes)
            configs_after = {p: p.read_bytes() for p in config_files(env)}
            self.assertEqual(configs_after, configs_before)
            self.assertTrue(unowned.is_file())
            self.assertEqual(unowned.read_text(encoding="utf-8"), "important")
            launcher.write_bytes(launcher_bytes)
            launcher.chmod(0o755)
            result_ok = run_installer(env, "--uninstall", "--yes")
            self.assertEqual(result_ok.returncode, 0, result_ok.stderr)
            self.assertFalse(launcher.exists())
            self.assertFalse(venv_python.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(Path(env["OPOS_HOME"]).exists())
            self.assertEqual(config_files(env), [])
            self.assertTrue(unowned.is_file())
            self.assertEqual(unowned.read_text(encoding="utf-8"), "important")

    def test_missing_launcher_verified_recovery_or_fails(self):
        """Missing launcher uses verified recovery or fails; success removes only owned artifacts."""
        with disposable_sandbox() as tmp:
            base = Path(tmp)
            env = make_env(base)
            result = run_installer(env, "--no-launch", "--yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = save_config(env)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            launcher = launcher_path(env)
            manifest = Path(env["OPOS_HOME"]) / "manifest"
            venv_python = Path(env["OPOS_HOME"]) / "venv" / "bin" / "python"
            manifest_bytes = manifest.read_bytes()
            configs_before = {p: p.read_bytes() for p in config_files(env)}
            self.assertTrue(configs_before)
            unowned = Path(env["HOME"]) / "keep" / "file.txt"
            unowned.parent.mkdir(parents=True, exist_ok=True)
            unowned.write_text("keep", encoding="utf-8")
            launcher.unlink()
            self.assertFalse(launcher.exists())
            self.assertFalse(
                Path("/etc/ssh/sshd_config.d/60-opos.conf").exists(),
                "host has a real OPOS sshd drop-in; this test would touch it",
            )
            manifest.write_text(f"launcher={base / 'nonexistent_launcher'}\nvenv={base / 'nonexistent_venv'}\nrepo={base / 'nonexistent_repo'}\n", encoding="utf-8")
            result_bad = run_installer(env, "--uninstall", "--yes")
            bad_detail = f"stdout={result_bad.stdout!r} stderr={result_bad.stderr!r}"
            self.assertNotEqual(result_bad.returncode, 0, bad_detail)
            self.assertIn("launcher not found", result_bad.stderr.lower(), bad_detail)
            self.assertIn("verified", result_bad.stderr.lower(), bad_detail)
            self.assertTrue(manifest.is_file(), bad_detail)
            self.assertTrue(venv_python.exists(), bad_detail)
            configs_after_bad = {p: p.read_bytes() for p in config_files(env)}
            self.assertEqual(configs_after_bad, configs_before, bad_detail)
            self.assertTrue(unowned.is_file(), bad_detail)
            manifest.write_bytes(manifest_bytes)
            result_ok = run_installer(env, "--uninstall", "--yes")
            ok_detail = f"stdout={result_ok.stdout!r} stderr={result_ok.stderr!r}"
            self.assertEqual(result_ok.returncode, 0, ok_detail)
            self.assertFalse(launcher.exists(), f'leftovers={sorted(p.name for p in Path(env["OPOS_HOME"]).glob("*")) if Path(env["OPOS_HOME"]).exists() else []} {ok_detail}')
            self.assertFalse(manifest.exists(), f'leftovers={sorted(p.name for p in Path(env["OPOS_HOME"]).glob("*")) if Path(env["OPOS_HOME"]).exists() else []} {ok_detail}')
            self.assertFalse(venv_python.exists(), f'leftovers={sorted(p.name for p in Path(env["OPOS_HOME"]).glob("*")) if Path(env["OPOS_HOME"]).exists() else []} {ok_detail}')
            self.assertFalse(Path(env["OPOS_HOME"]).exists(), f'leftovers={sorted(p.name for p in Path(env["OPOS_HOME"]).glob("*")) if Path(env["OPOS_HOME"]).exists() else []} {ok_detail}')
            self.assertEqual(config_files(env), [], f'leftovers={sorted(p.name for p in Path(env["OPOS_HOME"]).glob("*")) if Path(env["OPOS_HOME"]).exists() else []} {ok_detail}')
            self.assertTrue(unowned.is_file(), f'leftovers={sorted(p.name for p in Path(env["OPOS_HOME"]).glob("*")) if Path(env["OPOS_HOME"]).exists() else []} {ok_detail}')
            self.assertEqual(unowned.read_text(encoding="utf-8"), "keep", f'leftovers={sorted(p.name for p in Path(env["OPOS_HOME"]).glob("*")) if Path(env["OPOS_HOME"]).exists() else []} {ok_detail}')


class InstallerCloseTest(unittest.TestCase):
    """Regression for suppressed close errors."""

    def _make_tool(self):
        """Create a minimal tool for installer tests."""
        try:
            from opos.aitools.catalog import Tool
            import inspect

            sig = inspect.signature(Tool)
            kwargs = {}
            for name, param in sig.parameters.items():
                if name == "id":
                    kwargs[name] = "test-tool"
                elif name == "binary":
                    kwargs[name] = "testbin"
                elif name == "installer_url":
                    kwargs[name] = "https://example.com/installer"
                elif name == "signin":
                    kwargs[name] = "signin: test"
                elif name in ("installer_env", "run_env", "lite_env"):
                    kwargs[name] = {}
                elif name == "interpreter":
                    kwargs[name] = sys.executable
                elif name == "installer_args":
                    kwargs[name] = []
                elif name == "installer_sha256":
                    # Well formed; these tests fail at write or close, before the digest is compared.
                    kwargs[name] = "0" * 64
                elif param.default is not inspect.Parameter.empty:
                    kwargs[name] = param.default
                else:
                    kwargs[name] = "x"
            return Tool(**kwargs)
        except Exception:
            import types

            return types.SimpleNamespace(
                id="test-tool",
                binary="testbin",
                installer_url="https://example.com/installer",
                signin="signin: test",
                installer_env={},
                run_env={},
                lite_env={},
                interpreter=sys.executable,
                installer_args=[],
            )

    def test_close_failure_aborts_before_execution(self):
        """Close error must abort before runner and not leave partial install."""
        import contextlib
        import unittest.mock as mock

        from opos.aitools import installer, place

        with tempfile.TemporaryDirectory() as td:
            prefix = Path(td) / "prefix"
            home = Path(td) / "home"
            src_dir = Path(td) / "src"
            prefix.mkdir(parents=True)
            home.mkdir(parents=True)
            src_dir.mkdir(parents=True)
            tool = self._make_tool()
            fetch_data = b"#!/bin/sh\necho hi\n"

            def fetch(url):
                return fetch_data

            runner_calls = []

            def runner(argv, env, timeout_s):
                runner_calls.append(list(argv))
                tr = prefix / "opos-tools"
                real_bin = tr / tool.id / "home" / ".local" / "bin" / tool.binary
                real_bin.parent.mkdir(parents=True, exist_ok=True)
                real_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                real_bin.chmod(0o755)
                return (0, "ok")

            mode = next(iter(place.MODES))
            orig_close = os.close
            orig_mkstemp = tempfile.mkstemp
            captured = {}

            def fake_mkstemp(*args, **kwargs):
                fd, path = orig_mkstemp(*args, **kwargs)
                captured["fd"] = fd
                captured["path"] = path
                return fd, path

            def fake_close(fd):
                if fd == captured.get("fd"):
                    raise OSError("simulated delayed storage failure on close")
                return orig_close(fd)

            with mock.patch.object(place, "preflight", return_value=[]):
                with mock.patch.object(tempfile, "mkstemp", side_effect=fake_mkstemp):
                    with mock.patch.object(os, "close", side_effect=fake_close):
                        with self.assertRaises(OSError) as cm:
                            installer.install(
                                tool,
                                prefix=prefix,
                                mode=mode,
                                machine="x86_64",
                                home=home,
                                python=sys.executable,
                                src_dir=src_dir,
                                fetch=fetch,
                                runner=runner,
                                free_mb_fn=lambda p: 1000000,
                                profiles=[],
                                force=False,
                            )
                        self.assertIn("simulated delayed storage failure", str(cm.exception))
            self.assertEqual(runner_calls, [])
            with contextlib.suppress(Exception):
                if captured.get("fd") is not None:
                    with contextlib.suppress(Exception):
                        orig_close(captured["fd"])
            if captured.get("path"):
                self.assertFalse(Path(captured["path"]).exists())
            self.assertFalse((prefix / "opos-tools" / "manifest.json").exists())
            self.assertFalse((prefix / "opos-tools" / "bin" / tool.binary).exists())
            self.assertFalse((prefix / "opos-tools" / tool.id).exists())

    def test_write_failure_preserves_original_exception(self):
        """Original write exception must be preserved when close also fails."""
        import unittest.mock as mock

        from opos.aitools import installer, place

        with tempfile.TemporaryDirectory() as td:
            prefix = Path(td) / "prefix"
            home = Path(td) / "home"
            src_dir = Path(td) / "src"
            prefix.mkdir(parents=True)
            home.mkdir(parents=True)
            src_dir.mkdir(parents=True)
            tool = self._make_tool()

            def fetch(url):
                return b"data"

            def runner(argv, env, timeout_s):
                self.fail("runner must not be called when write fails")

            mode = next(iter(place.MODES))
            orig_write = os.write
            orig_close = os.close
            orig_mkstemp = tempfile.mkstemp
            captured = {}

            def fake_mkstemp(*args, **kwargs):
                fd, path = orig_mkstemp(*args, **kwargs)
                captured["fd"] = fd
                captured["path"] = path
                return fd, path

            def fake_write(fd, data):
                if fd == captured.get("fd"):
                    raise OSError("injected write failure")
                return orig_write(fd, data)

            def fake_close(fd):
                if fd == captured.get("fd"):
                    raise OSError("injected close failure")
                return orig_close(fd)

            with mock.patch.object(place, "preflight", return_value=[]):
                with mock.patch.object(tempfile, "mkstemp", side_effect=fake_mkstemp):
                    with mock.patch.object(os, "write", side_effect=fake_write):
                        with mock.patch.object(os, "close", side_effect=fake_close):
                            with self.assertRaises(OSError) as cm:
                                installer.install(
                                    tool,
                                    prefix=prefix,
                                    mode=mode,
                                    machine="x86_64",
                                    home=home,
                                    python=sys.executable,
                                    src_dir=src_dir,
                                    fetch=fetch,
                                    runner=runner,
                                    free_mb_fn=lambda p: 1000000,
                                    profiles=[],
                                    force=False,
                                )
                            self.assertIn("injected write failure", str(cm.exception))
                            self.assertNotIn("injected close failure", str(cm.exception))
            import contextlib

            with contextlib.suppress(Exception):
                if captured.get("fd") is not None:
                    with contextlib.suppress(Exception):
                        orig_close(captured["fd"])


    def test_missing_catalog_rejected_before_side_effects(self):
        """Missing catalog entry must fail before fetch, runner, or dirs."""
        import unittest.mock as mock

        from opos.aitools import installer
        from tests.test_aitools_installer import _fetch_ok, _runner_for, _tool

        for dry in (False, True):
            with self.subTest(dry_run=dry):
                with tempfile.TemporaryDirectory() as td:
                    prefix = Path(td) / "prefix"
                    home = Path(td) / "home"
                    src_dir = Path(td) / "src"
                    prefix.mkdir(parents=True)
                    home.mkdir(parents=True)
                    src_dir.mkdir(parents=True)
                    tool_a = _tool(id="tool-a", binary="bina")
                    installer.install(
                        tool_a,
                        prefix=prefix,
                        mode="lite",
                        machine="x86_64",
                        home=home,
                        python="python3",
                        src_dir=src_dir,
                        fetch=_fetch_ok(),
                        runner=_runner_for({tool_a.id: tool_a.binary}),
                        free_mb_fn=lambda _: 1000,
                        profiles=[],
                    )
                    wrapper_a = prefix / "opos-tools" / "bin" / "bina"
                    manifest = prefix / "opos-tools" / "manifest.json"
                    saved_wrapper = wrapper_a.read_bytes()
                    saved_manifest = manifest.read_bytes()
                    tool_b = _tool(id="tool-b", binary="binb")
                    base_fetch = _fetch_ok()
                    base_runner = _runner_for({tool_b.id: tool_b.binary})
                    calls = {"fetch": 0, "runner": 0}

                    def fetch(url, *a, **k):
                        calls["fetch"] += 1
                        return base_fetch(url)

                    def runner(argv, env, timeout_s, *a, **k):
                        calls["runner"] += 1
                        return base_runner(argv, env, timeout_s)

                    with mock.patch.object(installer, "_ensure_dirs") as m_dirs:
                        with self.assertRaisesRegex(installer.InstallError, "missing catalog entry: tool-a"):
                            installer.install(
                                tool_b,
                                prefix=prefix,
                                mode="full",
                                machine="x86_64",
                                home=home,
                                python="python3",
                                src_dir=src_dir,
                                fetch=fetch,
                                runner=runner,
                                free_mb_fn=lambda _: 1000,
                                profiles=[],
                                dry_run=dry,
                                all_tools=[tool_b],
                            )
                    self.assertEqual(calls["fetch"], 0)
                    self.assertEqual(calls["runner"], 0)
                    m_dirs.assert_not_called()
                    self.assertEqual(wrapper_a.read_bytes(), saved_wrapper)
                    self.assertEqual(manifest.read_bytes(), saved_manifest)
                    self.assertFalse((prefix / "opos-tools" / "tool-b").exists())


class SetModePerToolTest(unittest.TestCase):
    def _make_tool(self, tid, binary):
        import hashlib

        from opos.aitools.catalog import Tool

        return Tool(
            id=tid,
            name="Test Tool",
            binary=binary,
            installer_url="https://example.com/install.sh",
            interpreter="sh",
            installer_args=(),
            installer_env={},
            archs=("x86_64",),
            min_free_mb=1,
            state_subdir="state",
            run_env={},
            lite_env={},
            signin="run login",
            docs_url="https://example.com/docs",
            installer_sha256=hashlib.sha256(b"echo hi").hexdigest(),  # matches _fetch_ok()
        )

    def _fetch_ok(self):
        def fn(url):
            return b"echo hi"

        return fn

    def _runner_for(self, expected):
        def fn(argv, env, timeout_s):
            home = Path(env["HOME"])
            tid = home.parent.name
            binary = expected.get(tid, list(expected.values())[0])
            p = home / ".local" / "bin" / binary
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            p.chmod(0o755)
            return 0, "ok"

        return fn

    def test_set_mode_records_actual_mode_per_tool(self):
        from opos.aitools import installer

        orig = "full"
        target = "lite"
        t1 = self._make_tool("tool-a", "bina")
        t2 = self._make_tool("tool-b", "binb")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            fetch = self._fetch_ok()
            runner = self._runner_for({t1.id: t1.binary, t2.id: t2.binary})
            installer.install(
                t1,
                prefix=prefix,
                mode=orig,
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=src,
                fetch=fetch,
                runner=runner,
                free_mb_fn=lambda _: 1000,
                profiles=[],
                all_tools=[t1, t2],
            )
            installer.install(
                t2,
                prefix=prefix,
                mode=orig,
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=src,
                fetch=fetch,
                runner=runner,
                free_mb_fn=lambda _: 1000,
                profiles=[],
                all_tools=[t1, t2],
            )
            tr = installer.tools_root(prefix)
            w2_before = (tr / "bin" / t2.binary).read_bytes()
            out = installer.set_mode(prefix, target, [t1])
            self.assertEqual(out[0], "mode set to " + target)
            self.assertTrue(any("tool-b" in line and "missing catalog entry" in line for line in out))
            cur = installer.read_manifest(prefix)
            tools = cur["tools"]
            self.assertEqual(tools["tool-a"]["mode"], target)
            self.assertEqual(tools["tool-b"]["mode"], orig)
            self.assertEqual((tr / "bin" / t2.binary).read_bytes(), w2_before)


class PowerShellInstallerRegressionTest(unittest.TestCase):
    """Source contracts plus isolated PowerShell execution without installation."""

    @classmethod
    def setUpClass(cls):
        cls.text = INSTALL_PS1.read_text(encoding="utf-8")

    def selftest_block(self):
        marker = self.text.index("Say 'would run self-test'")
        start = self.text.rindex("if ($DryRun) {", 0, marker)
        end = self.text.index("if ($DryRun) {", marker)
        return self.text[start:end]

    def test_selftest_environment_is_scoped_and_dry_run_is_read_only(self):
        block = self.selftest_block()
        dry, live = block.split("} else {", 1)
        self.assertNotIn("$env:PYTHONPATH =", dry)
        self.assertIn("Test-Path Env:PYTHONPATH", live)
        self.assertLess(live.index("try {"), live.index("$env:PYTHONPATH ="))
        self.assertLess(live.index("opos.setup.selftest"), live.index("finally {"))
        cleanup = live.split("finally {", 1)[1]
        self.assertIn("$env:PYTHONPATH = $OldPythonPath", cleanup)
        self.assertIn("Remove-Item Env:PYTHONPATH", cleanup)
        self.assertNotIn("$env:PYTHONPATH =", self.text[:self.text.index(block)])

    def test_native_failures_are_checked_before_reporting_success(self):
        for invocation, success in (
            ("& $Python @PythonArgs -m venv $VenvDir", 'Say "created venv'),
            ("& $VenvPy -m pip install", "Say 'installed third-party packages'"),
        ):
            with self.subTest(invocation=invocation):
                start = self.text.index(invocation)
                end = self.text.index(success, start)
                statements = self.text[start:end].splitlines()
                self.assertIn("if ($LASTEXITCODE -ne 0)", statements[1])
                self.assertIn("exit 1", statements[1])
        self.assertNotIn("$VenvPyUse", self.text)

    def test_python_paths_and_launcher_arguments_remain_separate(self):
        self.assertNotIn("-split ' '", self.text)
        self.assertIn("[string[]]$Arguments", self.text)
        self.assertIn("& $g.Source @Arguments -c $Check", self.text)
        self.assertIn("Command = $env:OPOS_PYTHON", self.text)
        self.assertIn("@{ Command = 'py'; Arguments = @('-3') }", self.text)
        self.assertIn("& $Python @PythonArgs -m venv $VenvDir", self.text)
        for path in ("$OposHome", "$BinDir"):
            self.assertIn(f"GetUnresolvedProviderPathFromPSPath({path})", self.text)

    def test_launcher_is_scoped_and_has_a_real_server_entrypoint(self):
        block = self.text.split("$bat = @(", 1)[1].split("[System.IO.File]::WriteAllText", 1)[0]
        self.assertLess(block.index("setlocal DisableDelayedExpansion"), block.index('set "PYTHONPATH='))
        self.assertIn("$BatchSrc = (Join-Path $Root 'src').Replace('%', '%%')", self.text)
        self.assertIn("$BatchPy = $VenvPy.Replace('%', '%%')", self.text)
        self.assertIn('if /I "%~1"=="web" goto web', block)
        setup, web = block.split("':web'", 1)
        self.assertIn("-m opos.setup %*", setup)
        self.assertIn("exit /b %errorlevel%", setup)
        self.assertIn("-m opos.web --port 8765", web)
        self.assertNotIn("%*", web, "shift would not remove web from %*")
        self.assertIn("exit /b %errorlevel%", web)
        self.assertNotIn("SetEnvironmentVariable", self.text)
        self.assertNotRegex(self.text, r"(?im)^\s*\$env:path\s*=")
        self.assertIn("User variables > Path > Edit > New", self.text)
        self.assertLess(self.text.index("' web')"), self.text.index("if (-not $NoLaunch"))

    def run_fragment(self, block, setup):
        """Run actual installer statements with fake Python, never venv or pip."""
        import json

        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if pwsh is None:
            self.skipTest("PowerShell unavailable; behavioral installer check not run")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fragment = base / "fragment.ps1"
            fragment.write_text(block + "\n$global:ReachedEnd = $true\n", encoding="utf-8")
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            driver = base / "driver.ps1"
            driver.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "$global:LASTEXITCODE = 0\n"
                "$global:ReachedEnd = $false\n"
                "$global:During = $null\n"
                "$global:Caught = $null\n"
                "$global:Messages = @()\n"
                "function Say([string]$m) { $global:Messages += $m }\n"
                "function Invoke-FakePython {\n"
                "  $global:During = $env:PYTHONPATH\n"
                "  $global:LASTEXITCODE = $FakeCode\n"
                "  if ($FakeThrow) { throw 'fake Python failure' }\n"
                "}\n"
                f"$Root = {quote(base)}\n" + setup + "\n"
                f"try {{ & {quote(fragment)} }} catch {{ $global:Caught = $_.Exception.Message }}\n"
                "[ordered]@{ Reached = $ReachedEnd; Exit = $LASTEXITCODE; During = $During; "
                "Caught = $Caught; HasPath = (Test-Path Env:PYTHONPATH); Value = $env:PYTHONPATH; "
                "Messages = @($Messages); Path = $env:Path; Source = (Join-Path $Root 'src') } | ConvertTo-Json -Compress\n",
                encoding="utf-8",
            )
            done = subprocess.run(
                [pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                capture_output=True, text=True, timeout=TIMEOUT,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            result = json.loads(done.stdout.splitlines()[-1])
            result["stdout"] = done.stdout
            return result

    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell unavailable")
    def test_powershell_restores_pythonpath_on_every_selftest_exit(self):
        for initial in (None, "caller path;with spaces"):
            for mode in ("dry", "success", "nonzero", "throw"):
                with self.subTest(initial=initial, mode=mode):
                    setup = (
                        "Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue\n"
                        if initial is None else "$env:PYTHONPATH = 'caller path;with spaces'\n"
                    )
                    setup += (
                        "$VenvPy = 'Invoke-FakePython'\n"
                        f"$DryRun = ${str(mode == 'dry').lower()}\n"
                        f"$FakeCode = {17 if mode == 'nonzero' else 0}\n"
                        f"$FakeThrow = ${str(mode == 'throw').lower()}\n"
                    )
                    result = self.run_fragment(self.selftest_block(), setup)
                    self.assertEqual(result["HasPath"], initial is not None)
                    self.assertEqual(result["Value"], initial)
                    self.assertEqual(result["During"], None if mode == "dry" else result["Source"])
                    self.assertEqual(result["Reached"], mode in ("dry", "success"))
                    if mode == "nonzero":
                        self.assertEqual(result["Exit"], 1)
                    if mode == "throw":
                        self.assertEqual(result["Caught"], "fake Python failure")

    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell unavailable")
    def test_powershell_native_failure_stops_before_success(self):
        venv_start = self.text.index("if ((Test-Path -LiteralPath $VenvPy)")
        pip_start = self.text.index("if ($need) {")
        blocks = (
            (self.text[venv_start:self.text.index("$req =", venv_start)], "$VenvPy = Join-Path $Root 'missing-python.exe'"),
            (self.text[pip_start:self.text.index(self.selftest_block())], "$VenvPy = 'Invoke-FakePython'"),
        )
        for block, venv_setup in blocks:
            for code in (0, 17):
                with self.subTest(block=block.splitlines()[0], code=code):
                    result = self.run_fragment(
                        block, venv_setup + "\n$Python = 'Invoke-FakePython'\n$PythonArgs = @()\n"
                        "$VenvDir = Join-Path $Root 'venv'\n$req = 'unused-requirements.txt'\n"
                        f"$DryRun = $false\n$need = $true\n$FakeCode = {code}\n$FakeThrow = $false",
                    )
                    self.assertEqual(result["Reached"], code == 0)
                    self.assertEqual(result["Exit"], 0 if code == 0 else 1)
                    self.assertEqual(bool(result["Messages"]), code == 0)

    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell unavailable")
    def test_powershell_prints_exact_commands_without_changing_path(self):
        start = self.text.index("# Print commands as PowerShell literals")
        end = self.text.index("if (-not $NoLaunch", start)
        result = self.run_fragment(
            self.text[start:end],
            "$env:Path = 'original session path'\n"
            "$BinDir = 'C:\\OPOS user''s %literal%! bin'\n"
            "$Launcher = $BinDir + '\\opos.cmd'",
        )
        lines = result["stdout"].splitlines()
        self.assertIn("$env:Path = 'C:\\OPOS user''s %literal%! bin' + ';' + $env:Path", lines)
        for command in ("run --defaults", "web"):
            self.assertIn("& 'C:\\OPOS user''s %literal%! bin\\opos.cmd' " + command, lines)
        self.assertEqual(result["Path"], "original session path")


@unittest.skipIf(sys.platform != "win32", "needs Windows PowerShell installer")
class WindowsUnicodeExecutionTest(unittest.TestCase):
    """Install via install.ps1 under a Unicode path and prove artifacts execute."""

    def test_unicode_path_artifacts_execute(self):
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        self.assertIsNotNone(pwsh, "PowerShell unavailable")
        leaf = "opos-" + chr(233) + chr(241) + chr(252) + chr(945) + chr(946)
        with fresh_sandbox() as tmp:
            base = Path(tmp) / leaf
            template = make_env(base)
            assert_sandboxed(template, ())
            env = dict(os.environ)
            env.update(template)
            launcher = Path(template["OPOS_BIN_DIR"]) / "opos.cmd"
            opos_home = Path(template["OPOS_HOME"])
            venv_dir = opos_home / "venv"
            manifest = opos_home / "manifest"
            src_dir = str(ROOT / "src")
            ps_text = INSTALL_PS1.read_text(encoding="utf-8")
            ps_args = ["-NoLaunch"]
            if "$Yes" in ps_text:
                ps_args.append("-Yes")
            done = subprocess.run(
                [pwsh, "-NoProfile", "-NonInteractive", "-File", str(INSTALL_PS1)] + ps_args,
                capture_output=True,
                text=True,
                env=env,
                timeout=TIMEOUT,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(launcher.is_file(), "installer did not write %s" % launcher)
            self.assertTrue(manifest.is_file(), "installer did not write %s" % manifest)
            raw_launcher = launcher.read_bytes()
            raw_manifest = manifest.read_bytes()
            self.assertFalse(raw_launcher.startswith(b"\xef\xbb\xbf"))
            self.assertFalse(raw_manifest.startswith(b"\xef\xbb\xbf"))
            decoded_launcher = raw_launcher.decode("utf-8")
            decoded_manifest = raw_manifest.decode("utf-8")
            self.assertIn("\r\n", decoded_launcher)
            self.assertIn("chcp 65001", decoded_launcher)
            self.assertIn(src_dir, decoded_launcher)
            self.assertIn("-m opos.setup %*", decoded_launcher)
            lines = decoded_manifest.splitlines()
            self.assertEqual(len(lines), 3)
            data = dict(line.split("=", 1) for line in lines)
            self.assertEqual(set(data), {"launcher", "venv", "repo"})
            self.assertEqual(data["launcher"], str(launcher))
            self.assertEqual(data["venv"], str(venv_dir))
            self.assertEqual(data["repo"], str(ROOT))
            self.assertIn(leaf, data["launcher"])
            self.assertIn(leaf, data["venv"])
            self.assertTrue((venv_dir / "Scripts" / "python.exe").is_file())
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            cmdline = _cmd_status_commandline(comspec, launcher)
            started = subprocess.run(
                cmdline,
                capture_output=True,
                text=True,
                env=env,
                timeout=TIMEOUT,
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertIn("configured: no", started.stdout)
            self.assertIn("config: ", started.stdout)
            self.assertIn("running: ", started.stdout)

    def test_batch_launcher_runs_o_pos_via_cmd_with_venv(self):
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        self.assertIsNotNone(pwsh, "PowerShell unavailable")
        leaf = "opos " + chr(233) + chr(241) + chr(252) + chr(945) + chr(946)
        with fresh_sandbox() as tmp:
            base = Path(tmp) / leaf
            template = make_env(base)
            assert_sandboxed(template, ())
            env = dict(os.environ)
            env.update(template)
            launcher = Path(template["OPOS_BIN_DIR"]) / "opos.cmd"
            opos_home = Path(template["OPOS_HOME"])
            venv_dir = opos_home / "venv"
            venv_py = venv_dir / "Scripts" / "python.exe"
            src_dir = str(ROOT / "src")
            ps_text = INSTALL_PS1.read_text(encoding="utf-8")
            ps_args = ["-NoLaunch"]
            if "$Yes" in ps_text:
                ps_args.append("-Yes")
            done = subprocess.run(
                [pwsh, "-NoProfile", "-NonInteractive", "-File", str(INSTALL_PS1)] + ps_args,
                capture_output=True,
                text=True,
                env=env,
                timeout=TIMEOUT,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(launcher.is_file(), "installer did not write %s" % launcher)
            self.assertTrue(venv_py.is_file(), "installer did not write %s" % venv_py)
            decoded = launcher.read_bytes().decode("utf-8")
            self.assertIn('"' + str(venv_py) + '" -m opos.setup %*', decoded)
            run_env = dict(env)
            run_env.pop("PYTHONPATH", None)
            run_env["OPOS_CONFIG_DIR"] = str(Path(template["HOME"]) / ".config" / "opos")
            comspec = os.environ.get("ComSpec", "cmd.exe")
            cmdline = _cmd_status_commandline(comspec, launcher)
            executed = subprocess.run(
                cmdline,
                capture_output=True,
                text=True,
                env=run_env,
                timeout=TIMEOUT,
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("configured:", executed.stdout)
            direct_env = dict(run_env)
            direct_env["PYTHONPATH"] = src_dir
            direct = subprocess.run(
                [str(venv_py), "-m", "opos.setup", "status"],
                capture_output=True,
                text=True,
                env=direct_env,
                timeout=TIMEOUT,
            )
            self.assertEqual(direct.returncode, 0, direct.stderr)
            self.assertIn("configured:", direct.stdout)


class CmdQuotingTest(unittest.TestCase):
    def test_status_commandline_with_space_and_unicode(self):
        with fresh_sandbox() as tmp:
            leaf = "opos " + chr(233) + chr(241) + chr(252) + chr(945) + chr(946)
            launcher = str(Path(tmp) / leaf / "opos.cmd")
            cmdline = _cmd_status_commandline("cmd.exe", launcher)
            self.assertIn('""' + launcher + '" status"', cmdline)
            self.assertNotIn(chr(92) + '"', cmdline)
            broken = subprocess.list2cmdline(["cmd.exe", "/d", "/c", '"' + launcher + '" status'])
            self.assertIn(chr(92) + '"', broken)

    def test_windows_invocations_use_command_string(self):
        """Both Windows runs must use the quoted string, not an arg list."""
        text = Path(__file__).read_text(encoding="utf-8")
        good = chr(95) + "cmd" + chr(95) + "status" + chr(95) + "commandline(comspec, launcher)"
        self.assertEqual(text.count(good), 3)
        bad = chr(91) + "comspec, " + chr(34) + chr(47) + "d" + chr(34) + ", " + chr(34) + chr(47) + "c" + chr(34) + ", "
        self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
