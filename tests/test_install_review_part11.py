# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from tests.install_review_support import (
    _contained_candidate,
    _install_review_modules,
    _isolated_git_env,
    _read_install_sh,
    _run_contained,
)


class TestInstallReview(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_remove_clone_preserves_clean_checkout_without_ownership_record(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("canon_repo") + _extract("remove_clone")
        for name in ("committed_addition", "hook_only"):
            for dry in ("live", "dry_run"):
                with self.subTest(name=name, dry=dry):
                    with tempfile.TemporaryDirectory() as tmp:
                        tmp_path = pathlib.Path(tmp)
                        clone = tmp_path / "clone"
                        clone.mkdir()
                        (clone / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                        base_env = dict(os.environ)
                        subprocess.run(
                            ["git", "-C", str(clone), "init"],
                            check=True,
                            capture_output=True,
                            text=True,
                            env=base_env,
                            timeout=30,
                        )
                        subprocess.run(
                            ["git", "-C", str(clone), "add", "install.sh"],
                            check=True,
                            capture_output=True,
                            text=True,
                            env=base_env,
                            timeout=30,
                        )
                        subprocess.run(
                            [
                                "git",
                                "-C",
                                str(clone),
                                "-c",
                                "user.email=" + "".join(["o", "pos@", "example.com"]),
                                "-c",
                                "user.name=" + "".join(["o", "pos"]),
                                "-c",
                                "commit.gpgsign=false",
                                "commit",
                                "-m",
                                "".join(["i", "nit"]),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                            env=base_env,
                            timeout=30,
                        )
                        if name == "committed_addition":
                            owned = clone / "".join(["owner-ad", "ded.txt"])
                            owned_bytes = "".join(["owner-", "added-", "9", "\n"]).encode()
                            owned.write_bytes(owned_bytes)
                            subprocess.run(
                                ["git", "-C", str(clone), "add", owned.name],
                                check=True,
                                capture_output=True,
                                text=True,
                                env=base_env,
                                timeout=30,
                            )
                            subprocess.run(
                                [
                                    "git",
                                    "-C",
                                    str(clone),
                                    "-c",
                                    "user.email=" + "".join(["o", "pos@", "example.com"]),
                                    "-c",
                                    "user.name=" + "".join(["o", "pos"]),
                                    "-c",
                                    "commit.gpgsign=false",
                                    "commit",
                                    "-m",
                                    "".join(["add-", "owner"]),
                                ],
                                check=True,
                                capture_output=True,
                                text=True,
                                env=base_env,
                                timeout=30,
                            )
                            check_path = owned
                        else:
                            hook = clone / ".git" / "hooks" / "pre-commit"
                            hook_bytes = "".join(["#!/bin/sh\nexit ", "0\n"]).encode()
                            hook.write_bytes(hook_bytes)
                            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                            check_path = hook
                            owned = hook
                            owned_bytes = hook_bytes
                        status = subprocess.run(
                            ["git", "-C", str(clone), "status", "--porcelain", "--ignored", "--untracked-files=all"],
                            capture_output=True,
                            text=True,
                            env=base_env,
                            timeout=30,
                        )
                        self.assertEqual(status.returncode, 0, status.stderr)
                        self.assertEqual(status.stdout, "", name)
                        runner = tmp_path / "run.sh"
                        runner.write_text(helpers + 'remove_clone "$1"\n', encoding="utf-8")
                        env = dict(os.environ)
                        env["ROOT"] = str(clone)
                        env["HOME"] = str(tmp_path)
                        env["DRY"] = "1" if dry == "dry_run" else "0"
                        proc = subprocess.run(
                            ["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30
                        )
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertTrue(clone.is_dir(), name)
                        self.assertTrue(check_path.is_file(), name)
                        self.assertEqual(check_path.read_bytes(), owned_bytes, name)
                        self.assertIn("preserving checkout with owner contents", proc.stdout, name)
                        self.assertNotIn("==> removed " + str(clone), proc.stdout.splitlines(), name)
                        self.assertNotIn("==> would remove " + str(clone), proc.stdout.splitlines(), name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_remove_clone_preserves_owner_created_commit(self):
        text = _read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("canon_repo") + _extract("remove_clone")
        for dry in ("live", "dry_run"):
            with self.subTest(dry=dry):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    clone = tmp_path / "clone"
                    clone.mkdir()
                    (clone / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    base_env = _isolated_git_env()
                    subprocess.run(
                        ["git", "-C", str(clone), "init"],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    subprocess.run(
                        ["git", "-C", str(clone), "add", "install.sh"],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(clone),
                            "-c",
                            "user.email=" + "".join(["o", "pos@", "example.com"]),
                            "-c",
                            "user.name=" + "".join(["o", "pos"]),
                            "-c",
                            "commit.gpgsign=false",
                            "commit",
                            "-m",
                            "".join(["i", "nit"]),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    owned = clone / "".join(["owner-ad", "ded.txt"])
                    owned_bytes = "".join(["owner-", "added-", "5", "\n"]).encode()
                    owned.write_bytes(owned_bytes)
                    subprocess.run(
                        ["git", "-C", str(clone), "add", owned.name],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(clone),
                            "-c",
                            "user.email=" + "".join(["o", "pos@", "example.com"]),
                            "-c",
                            "user.name=" + "".join(["o", "pos"]),
                            "-c",
                            "commit.gpgsign=false",
                            "commit",
                            "-m",
                            "".join(["add-", "owner"]),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    status = subprocess.run(
                        ["git", "-C", str(clone), "status", "--porcelain", "--ignored", "--untracked-files=all"],
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    self.assertEqual(status.returncode, 0, status.stderr)
                    self.assertEqual(status.stdout, "", dry)
                    runner = tmp_path / "run.sh"
                    runner.write_text(helpers + 'remove_clone "$1"\n', encoding="utf-8")
                    env = dict(base_env)
                    env["ROOT"] = str(clone)
                    env["HOME"] = str(tmp_path)
                    env["DRY"] = "1" if dry == "dry_run" else "0"
                    proc = subprocess.run(
                        ["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertTrue(clone.is_dir(), dry)
                    self.assertTrue(owned.is_file(), dry)
                    self.assertEqual(owned.read_bytes(), owned_bytes, dry)
                    self.assertIn("preserving checkout with owner contents", proc.stdout, dry)
                    self.assertNotIn("==> removed " + str(clone), proc.stdout.splitlines(), dry)
                    self.assertNotIn("==> would remove " + str(clone), proc.stdout.splitlines(), dry)

    def test_owner_commit_git_environment_is_sanitized(self):
        sentinel_key = "".join(["OPOS_", "SENTINEL"])
        sentinel_value = "".join(["sentinel-", "value-1"])
        seed = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp/" + "opos-home",
            sentinel_key: sentinel_value,
            "GIT_DIR": "/tmp/" + "owner-repo",
            "GIT_WORK_TREE": "/tmp/" + "owner-work",
            "GIT_COMMON_DIR": "/tmp/" + "owner-common",
            "GIT_INDEX_FILE": "/tmp/" + "owner-index",
            "GIT_OBJECT_DIRECTORY": "/tmp/" + "owner-objects",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/" + "owner-alt",
        }
        with unittest.mock.patch.dict(os.environ, seed, clear=True):
            before = dict(os.environ)
            result = _isolated_git_env()
            self.assertEqual(
                result, {"PATH": "/usr/bin:/bin", "HOME": "/tmp/" + "opos-home", sentinel_key: sentinel_value}
            )
            self.assertEqual(dict(os.environ), before)
            self.assertEqual(dict(os.environ), seed)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_tmp_prefix_candidates_resolve_and_contain(self):
        with tempfile.TemporaryDirectory() as tmp:
            with tempfile.TemporaryDirectory() as out:
                base = pathlib.Path(tmp)
                target = pathlib.Path(out)
                inner = base / "inner"
                inner.mkdir()
                good = inner / "ok"
                good.write_text("ok\n", encoding="utf-8")
                dotdot = str(base) + "/../" + base.name + "-sibling"
                with self.assertRaises(AssertionError):
                    _contained_candidate(base, dotdot)
                link = base / "link"
                link.symlink_to(str(target))
                escaped = str(link) + "/payload"
                with self.assertRaises(AssertionError):
                    _contained_candidate(base, escaped)
                self.assertEqual(_contained_candidate(base, good), os.path.realpath(str(good)))
                back_inside = str(inner) + "/../inner/ok"
                self.assertEqual(_contained_candidate(base, back_inside), os.path.realpath(str(good)))
                proc = _run_contained(base, good, ["cat", str(good)])
                self.assertEqual(proc.returncode, 0)
                self.assertIn("ok", proc.stdout)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_tmp_execution_path_rejects_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as outer:
            outer_path = pathlib.Path(outer)
            base = outer_path / "base"
            inner = base / "inner"
            inner.mkdir(parents=True)
            outside = outer_path / (base.name + "-sibling")
            outside.mkdir()
            payload = outside / "payload.sh"
            payload.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            traversal = str(inner) + "/../../" + outside.name + "/payload.sh"
            link = inner / "link"
            link.symlink_to(str(outside))
            via_link = str(link) + "/payload.sh"
            for hostile in (traversal, via_link):
                with self.subTest(hostile=hostile):
                    with unittest.mock.patch("tests.install_review_support.subprocess.run") as run_mock:
                        with self.assertRaises(AssertionError):
                            _run_contained(base, hostile, ["sh", hostile])
                        self.assertEqual(run_mock.call_count, 0)
            good = inner / "ok.sh"
            done = inner / "done"
            good.write_text('#!/bin/sh\ntouch "' + str(done) + '"\n', encoding="utf-8")
            proc = _run_contained(base, good, ["sh", str(good)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(done.is_file())

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_tmp_execution_sites_reject_hostile_paths(self):
        skip = (
            "test_tmp_execution_sites_reject_hostile_paths",
            "test_tmp_prefix_candidates_resolve_and_contain",
            "test_tmp_execution_path_rejects_traversal_and_symlink",
        )
        sites = {}
        for module in _install_review_modules():
            src = pathlib.Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(src)
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestInstallReview")
            for node in cls.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name in skip:
                    continue
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        if (
                            isinstance(func, ast.Attribute)
                            and func.attr == "run"
                            and isinstance(func.value, ast.Name)
                            and func.value.id == "subprocess"
                        ):
                            sites[node.name] = getattr(module.TestInstallReview, node.name)
                            break
        self.assertTrue(sites)
        for site, test in sites.items():
            with self.subTest(site=site):
                with tempfile.TemporaryDirectory() as outer:
                    outer_path = pathlib.Path(outer)
                    base = outer_path / "base"
                    inner = base / "inner"
                    inner.mkdir(parents=True)
                    outside = outer_path / (base.name + "-sibling")
                    outside.mkdir()
                    payload = outside / "payload.sh"
                    marker_name = "".join(["bypassed-", site])
                    bypass_marker = outside / marker_name
                    payload.write_text('#!/bin/sh\ntouch "' + str(bypass_marker) + '"\nexit 0\n', encoding="utf-8")
                    traversal = str(inner) + "/../../" + outside.name + "/payload.sh"
                    link = inner / "link"
                    link.symlink_to(str(outside))
                    via_link = str(link) + "/payload.sh"
                    for hostile in (traversal, via_link):
                        with self.subTest(hostile=hostile):
                            with self.assertRaises(AssertionError):
                                _contained_candidate(base, hostile)
                            with unittest.mock.patch.object(subprocess, "run") as run_mock:
                                with self.assertRaises(AssertionError):
                                    _run_contained(base, hostile, ["sh", hostile])
                                self.assertEqual(run_mock.call_count, 0)
                            self.assertFalse(bypass_marker.exists())
                            with unittest.mock.patch(
                                "tests.install_review_support._contained_candidate", side_effect=lambda t, c: str(c)
                            ):
                                proc = _run_contained(base, hostile, ["sh", hostile])
                            self.assertEqual(proc.returncode, 0, proc.stderr)
                            self.assertTrue(
                                bypass_marker.is_file(),
                                "removing containment must let hostile payload execute for " + site,
                            )
                            bypass_marker.unlink()
                    good = inner / "ok.sh"
                    done = inner / "done"
                    good.write_text('#!/bin/sh\ntouch "' + str(done) + '"\n', encoding="utf-8")
                    proc = _run_contained(base, good, ["sh", str(good)])
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertTrue(done.is_file())
                    test(self)
                    self.assertFalse(bypass_marker.exists(), "site must not execute hostile payload for " + site)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_preserves_owner_checkout_contents(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            clone = tmp_path / "clone"
            for p in (home, opos_home, bindir, root, clone):
                p.mkdir(parents=True)
            clone_install = clone / "install.sh"
            clone_install.write_bytes(install.read_bytes())
            base_env = dict(os.environ)
            subprocess.run(["git", "-C", str(clone), "init"], check=True, capture_output=True, text=True, env=base_env)
            tracked = clone / "".join(["tra", "cked.txt"])
            tracked.write_bytes("".join(["base-", "content-", "1", "\n"]).encode())
            subprocess.run(
                ["git", "-C", str(clone), "add", "install.sh", tracked.name],
                check=True,
                capture_output=True,
                text=True,
                env=base_env,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(clone),
                    "-c",
                    "user.email=" + "".join(["o", "pos@", "example.com"]),
                    "-c",
                    "user.name=" + "".join(["o", "pos"]),
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    "".join(["i", "nit"]),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=base_env,
            )
            tracked.write_bytes(tracked.read_bytes() + "".join(["owner-", "edit-", "2", "\n"]).encode())
            owned = clone / "".join(["owner-ad", "ded.txt"])
            owned_bytes = "".join(["owner-", "added-", "3", "\n"]).encode()
            owned.write_bytes(owned_bytes)
            tracked_after = tracked.read_bytes()
            (opos_home / "manifest").write_text(
                "launcher="
                + str(bindir / "opos")
                + "\nvenv="
                + str(tmp_path / "elsewhere" / "venv")
                + "\nrepo="
                + str(clone)
                + "\n",
                encoding="utf-8",
            )
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(clone_install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(clone.is_dir(), "checkout with owner contents must be preserved")
            self.assertTrue(owned.is_file(), "untracked owner file must survive")
            self.assertEqual(owned.read_bytes(), owned_bytes)
            self.assertTrue(tracked.is_file(), "modified tracked file must survive")
            self.assertEqual(tracked.read_bytes(), tracked_after)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_preserves_each_owner_condition_independently(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def git(clone, *args):
            subprocess.run(
                ["git", "-C", str(clone)] + list(args), check=True, capture_output=True, text=True, env=dict(os.environ)
            )

        def commit_all(clone, message):
            git(clone, "add", "-A")
            git(
                clone,
                "-c",
                "user.email=" + "".join(["o", "pos@", "example.com"]),
                "-c",
                "user.name=" + "".join(["o", "pos"]),
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                message,
            )

        def run_case(name):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                root = tmp_path / "root"
                clone = tmp_path / "clone"
                for p in (home, opos_home, bindir, root, clone):
                    p.mkdir(parents=True)
                clone_install = clone / "install.sh"
                clone_install.write_bytes(install.read_bytes())
                fakebin = tmp_path / "fakebin"
                fakebin.mkdir()
                for tool in ("ufw", "loginctl", "nft"):
                    stub = fakebin / tool
                    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                ctl_stub = fakebin / "systemctl"
                ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                path_value = str(fakebin) + os.pathsep + os.environ.get("PATH", "")
                owned = clone / "".join(["owner-ad", "ded.txt"])
                owned_bytes = "".join(["owner-", "added-", "3", "\n"]).encode()
                tracked = clone / "".join(["tra", "cked.txt"])
                if name in ("git_unavailable", "inspection_fails"):
                    (clone / ".git").mkdir(parents=True)
                    owned.write_bytes(owned_bytes)
                    if name == "inspection_fails":
                        stub = fakebin / "git"
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    else:
                        farm = tmp_path / "farm"
                        farm.mkdir()
                        seen = set()
                        for entry in os.environ.get("PATH", "").split(os.pathsep):
                            if not entry or not os.path.isdir(entry):
                                continue
                            for entry_name in os.listdir(entry):
                                if entry_name == "git" or entry_name in seen:
                                    continue
                                src = os.path.join(entry, entry_name)
                                if os.path.isfile(src) and os.access(src, os.X_OK):
                                    try:
                                        os.symlink(src, str(farm / entry_name))
                                    except FileExistsError:
                                        pass
                                    seen.add(entry_name)
                        path_value = str(fakebin) + os.pathsep + str(farm)
                else:
                    git(clone, "init")
                    tracked.write_bytes("".join(["base-", "content-", "1", "\n"]).encode())
                    if name == "ignored_only":
                        (clone / ".gitignore").write_text("".join(["ignored-", "owner.txt\n"]), encoding="utf-8")
                    commit_all(clone, "".join(["i", "nit"]))
                    if name == "modified_tracked_only":
                        tracked.write_bytes(tracked.read_bytes() + "".join(["owner-", "edit-", "2", "\n"]).encode())
                        owned = tracked
                        owned_bytes = tracked.read_bytes()
                    elif name == "untracked_only":
                        owned.write_bytes(owned_bytes)
                    elif name == "ignored_only":
                        owned = clone / "".join(["ignored-", "owner.txt"])
                        owned.write_bytes(owned_bytes)
                    elif name == "committed_addition":
                        owned.write_bytes(owned_bytes)
                        commit_all(clone, "".join(["add-", "owner"]))
                    else:
                        raise AssertionError(name)
                (opos_home / "manifest").write_text(
                    "launcher="
                    + str(bindir / "opos")
                    + "\nvenv="
                    + str(tmp_path / "elsewhere" / "venv")
                    + "\nrepo="
                    + str(clone)
                    + "\n",
                    encoding="utf-8",
                )
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["OPOS_BIN_DIR"] = str(bindir)
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = path_value
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                proc = subprocess.run(
                    ["sh", str(clone_install), "--uninstall", "--force"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=60,
                )
                self.assertEqual(proc.returncode, 0, name + proc.stdout + proc.stderr)
                self.assertTrue(clone.is_dir(), name + ": checkout must be preserved")
                self.assertTrue(owned.is_file(), name + ": owner file must survive")
                self.assertEqual(owned.read_bytes(), owned_bytes, name)

        for name in (
            "modified_tracked_only",
            "untracked_only",
            "ignored_only",
            "committed_addition",
            "git_unavailable",
            "inspection_fails",
        ):
            with self.subTest(name=name):
                run_case(name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_preserves_untracked_owner_when_status_hides_them(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            clone = tmp_path / "clone"
            for p in (home, opos_home, bindir, root, clone):
                p.mkdir(parents=True)
            clone_install = clone / "install.sh"
            clone_install.write_bytes(install.read_bytes())
            base_env = dict(os.environ)
            subprocess.run(["git", "-C", str(clone), "init"], check=True, capture_output=True, text=True, env=base_env)
            tracked = clone / "".join(["tra", "cked.txt"])
            tracked.write_bytes("".join(["base-", "content-", "1", "\n"]).encode())
            subprocess.run(
                ["git", "-C", str(clone), "add", "install.sh", tracked.name],
                check=True,
                capture_output=True,
                text=True,
                env=base_env,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(clone),
                    "-c",
                    "user.email=" + "".join(["o", "pos@", "example.com"]),
                    "-c",
                    "user.name=" + "".join(["o", "pos"]),
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    "".join(["i", "nit"]),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=base_env,
            )
            subprocess.run(
                ["git", "-C", str(clone), "config", "status.showUntrackedFiles", "no"],
                check=True,
                capture_output=True,
                text=True,
                env=base_env,
            )
            owned = clone / "".join(["owner-ad", "ded.txt"])
            owned_bytes = "".join(["owner-", "added-", "7", "\n"]).encode()
            owned.write_bytes(owned_bytes)
            tracked_before = tracked.read_bytes()
            (opos_home / "manifest").write_text(
                "launcher="
                + str(bindir / "opos")
                + "\nvenv="
                + str(tmp_path / "elsewhere" / "venv")
                + "\nrepo="
                + str(clone)
                + "\n",
                encoding="utf-8",
            )
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(clone_install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(clone.is_dir(), "checkout with untracked owner must be preserved")
            self.assertTrue(owned.is_file(), "untracked owner file must survive hidden status")
            self.assertEqual(owned.read_bytes(), owned_bytes)
            self.assertTrue(tracked.is_file())
            self.assertEqual(tracked.read_bytes(), tracked_before)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_preserves_checkout_when_git_inspection_fails(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        for name in ("rev_parse_fails", "git_missing"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    root = tmp_path / "root"
                    clone = tmp_path / "clone"
                    for p in (home, opos_home, bindir, root, clone):
                        p.mkdir(parents=True)
                    (clone / ".git").mkdir(parents=True)
                    clone_install = clone / "install.sh"
                    clone_install.write_bytes(install.read_bytes())
                    owned = clone / "".join(["owner-ad", "ded.txt"])
                    owned_bytes = "".join(["owner-", "bytes-", "9", "\n"]).encode()
                    owned.write_bytes(owned_bytes)
                    (opos_home / "manifest").write_text(
                        "launcher="
                        + str(bindir / "opos")
                        + "\nvenv="
                        + str(tmp_path / "elsewhere" / "venv")
                        + "\nrepo="
                        + str(clone)
                        + "\n",
                        encoding="utf-8",
                    )
                    fakebin = tmp_path / "fakebin"
                    fakebin.mkdir()
                    for tool in ("ufw", "loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_stub = fakebin / "systemctl"
                    ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    if name == "rev_parse_fails":
                        failing = fakebin / "git"
                        failing.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        failing.chmod(failing.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        path_value = str(fakebin) + os.pathsep + os.environ.get("PATH", "")
                    else:
                        farm = tmp_path / "farm"
                        farm.mkdir()
                        for entry in os.environ.get("PATH", "").split(os.pathsep):
                            if not entry or not os.path.isdir(entry):
                                continue
                            for entry_name in os.listdir(entry):
                                if entry_name == "git":
                                    continue
                                src = os.path.join(entry, entry_name)
                                dst = farm / entry_name
                                if dst.exists() or dst.is_symlink():
                                    continue
                                if os.path.isfile(src) and os.access(src, os.X_OK):
                                    try:
                                        os.symlink(src, dst)
                                    except FileExistsError:
                                        pass
                        path_value = str(fakebin) + os.pathsep + str(farm)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = path_value
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(clone_install), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn("preserving checkout with owner contents", proc.stdout + proc.stderr)
                    self.assertTrue(clone.is_dir(), "checkout must be preserved when git inspection fails")
                    self.assertTrue(owned.is_file(), "owner file must survive")
                    self.assertEqual(owned.read_bytes(), owned_bytes)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_preserves_valid_checkout_regardless_of_git_validation(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        for outcome in ("success", "failure"):
            with self.subTest(outcome=outcome):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(os.path.realpath(tmp))
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    root = tmp_path / "root"
                    clone = tmp_path / "clone"
                    for p in (home, opos_home, bindir, root, clone):
                        p.mkdir(parents=True)
                    clone_install = clone / "install.sh"
                    clone_install.write_bytes(install.read_bytes())
                    base_env = dict(os.environ)
                    subprocess.run(
                        ["git", "-C", str(clone), "init"],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    tracked = clone / "".join(["tra", "cked.txt"])
                    tracked.write_bytes("".join(["base-", "content-", "1", "\n"]).encode())
                    subprocess.run(
                        ["git", "-C", str(clone), "add", "install.sh", tracked.name],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(clone),
                            "-c",
                            "user.email=" + "".join(["o", "pos@", "example.com"]),
                            "-c",
                            "user.name=" + "".join(["o", "pos"]),
                            "-c",
                            "commit.gpgsign=false",
                            "commit",
                            "-m",
                            "".join(["i", "nit"]),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    valid = subprocess.run(
                        ["git", "-C", str(clone), "rev-parse", "--show-toplevel"],
                        capture_output=True,
                        text=True,
                        env=base_env,
                        timeout=30,
                    )
                    self.assertEqual(valid.returncode, 0)
                    (opos_home / "manifest").write_text(
                        "launcher="
                        + str(bindir / "opos")
                        + "\nvenv="
                        + str(tmp_path / "elsewhere" / "venv")
                        + "\nrepo="
                        + str(clone)
                        + "\n",
                        encoding="utf-8",
                    )
                    install_before = clone_install.read_bytes()
                    tracked_before = tracked.read_bytes()
                    fakebin = tmp_path / "fakebin"
                    fakebin.mkdir()
                    for tool in ("ufw", "loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_stub = fakebin / "systemctl"
                    ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    real_git = None
                    for entry in os.environ.get("PATH", "").split(os.pathsep):
                        if not entry or not os.path.isdir(entry):
                            continue
                        cand = os.path.join(entry, "git")
                        if os.path.isfile(cand) and os.access(cand, os.X_OK):
                            real_git = cand
                            break
                    self.assertIsNotNone(real_git)
                    log = tmp_path / "".join(["git-", "call-", outcome, ".log"])
                    log.write_text("", encoding="utf-8")
                    marker = "".join(["git-", "validation-", "failed-", outcome])
                    git_stub = fakebin / "git"
                    if outcome == "success":
                        git_stub.write_text(
                            "#!/bin/sh\n"
                            + 'printf \'%s\\n\' "$*" >> "'
                            + str(log)
                            + '"\n'
                            + 'if [ "$#" = "4" ] && [ "$1" = "-C" ] && [ "$2" = "'
                            + str(clone)
                            + '" ] && [ "$3" = "rev-parse" ] && [ "$4" = "--show-toplevel" ]; then\n'
                            + 'exec "'
                            + real_git
                            + '" "$@"\n'
                            + "fi\n"
                            + "exit 1\n",
                            encoding="utf-8",
                        )
                    else:
                        git_stub.write_text(
                            "#!/bin/sh\n"
                            + 'printf \'%s\\n\' "$*" >> "'
                            + str(log)
                            + '"\n'
                            + 'if [ "$#" = "4" ] && [ "$1" = "-C" ] && [ "$2" = "'
                            + str(clone)
                            + '" ] && [ "$3" = "rev-parse" ] && [ "$4" = "--show-toplevel" ]; then\n'
                            + "printf '%s\\n' \""
                            + marker
                            + '" >> "'
                            + str(log)
                            + '"\n'
                            + "exit 1\n"
                            + "fi\n"
                            + "exit 1\n",
                            encoding="utf-8",
                        )
                    git_stub.chmod(git_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(clone_install), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn("preserving checkout with owner contents", proc.stdout + proc.stderr)
                    calls = log.read_text(encoding="utf-8")
                    want_call = "-C " + str(clone) + " rev-parse --show-toplevel"
                    self.assertIn(want_call, calls)
                    if outcome == "failure":
                        self.assertIn(marker, calls)
                    else:
                        self.assertNotIn(marker, calls)
                    self.assertTrue((clone / ".git").is_dir(), "checkout git metadata must survive")
                    self.assertTrue(clone_install.is_file())
                    self.assertEqual(clone_install.read_bytes(), install_before)
                    self.assertTrue(tracked.is_file())
                    self.assertEqual(tracked.read_bytes(), tracked_before)


if __name__ == "__main__":
    unittest.main()
