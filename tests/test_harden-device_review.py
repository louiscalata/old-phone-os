# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
import tempfile
import unittest
import unittest.mock


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _snapshot_root(tmp):
    snapshot = {}
    listing = []
    for root, dirs, files in os.walk(tmp):
        for name in dirs + files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, tmp)
            listing.append(rel)
            st = os.lstat(path)
            entry = (st.st_mode, st.st_uid, st.st_gid, st.st_mtime_ns)
            if os.path.islink(path):
                entry += (os.readlink(path),)
            elif os.path.isfile(path) and not os.path.islink(path):
                with open(path, "rb") as handle:
                    entry += (handle.read(),)
            snapshot[rel] = entry
    return snapshot, sorted(listing)


def _run_hardener(extra_env, extra_args=None):
    script = os.path.join(_repo_root(), "deploy", "harden-device.sh")
    env = dict(os.environ)
    env.pop("OPOS_HARDEN_TEST", None)
    env.pop("TEST", None)
    env.update(extra_env)
    args = ["bash", script, "--check"]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class HardenLegacyTestVarTest(unittest.TestCase):
    def test_legacy_test_warns_when_new_var_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_hardener({"TEST": "1", "OPOS_ROOT": tmp})
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("TEST", proc.stderr)
        self.assertIn("OPOS_HARDEN_TEST", proc.stderr)
        self.assertIn("ignor", proc.stderr.lower())

    def test_no_legacy_warning_when_new_var_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_hardener(
                {"TEST": "1", "OPOS_HARDEN_TEST": "1", "OPOS_ROOT": tmp}
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertNotIn("is ignored", proc.stderr)


class HardenCheckPreservesTemporaryRootTest(unittest.TestCase):
    def test_legacy_test_check_preserves_temporary_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_hardener(
                {"TEST": "1", "OPOS_ROOT": tmp}, ["--only", "no-such-step"]
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertIn("TEST is ignored", proc.stderr)

    def test_check_mode_preserves_populated_temporary_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            etc_dir = os.path.join(tmp, "etc")
            ssh_dir = os.path.join(etc_dir, "ssh")
            os.makedirs(ssh_dir)
            payload = ("sentinel-" + "unchanged").encode("utf-8")
            seeds = {
                os.path.join("etc", "sentinel"): payload,
                os.path.join("etc", "example.conf"): b"key=value\n",
                os.path.join("etc", "ssh", "sshd_config"): b"Port 22\n",
            }
            for rel, data in seeds.items():
                full = os.path.join(tmp, rel)
                with open(full, "wb") as handle:
                    handle.write(data)
                os.chmod(full, 0o644)
            before, _ = _snapshot_root(tmp)
            proc = _run_hardener(
                {"OPOS_HARDEN_TEST": "1", "OPOS_ROOT": tmp}
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            after, found = _snapshot_root(tmp)
            self.assertEqual(after, before)
            self.assertCountEqual(found, sorted(list(seeds.keys()) + ["etc", os.path.join("etc", "ssh")]))

    def test_run_hardener_uses_check_mode_and_temporary_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
                _run_hardener({"OPOS_ROOT": tmp})
                self.assertTrue(mock_run.called)
                args, kwargs = mock_run.call_args
                expected_script = os.path.join(_repo_root(), "deploy", "harden-device.sh")
                self.assertEqual(args[0], ["bash", expected_script, "--check"])
                self.assertIn("OPOS_ROOT", kwargs["env"])
                self.assertEqual(kwargs["env"]["OPOS_ROOT"], tmp)


def _run_apply_with_isolated_root(extra_env):
    script = os.path.join(_repo_root(), "deploy", "harden-device.sh")
    env = dict(os.environ)
    env.pop("OPOS_HARDEN_TEST", None)
    env.pop("TEST", None)
    env.update(extra_env)
    return subprocess.run(
        ["bash", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class HardenAmbiguousLegacyRejectionTest(unittest.TestCase):
    def test_ambiguous_legacy_request_warns_and_hardens_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            etc_dir = os.path.join(tmp, "etc")
            os.makedirs(etc_dir)
            sentinel = os.path.join(etc_dir, "sentinel")
            expected = b"sentinel-unchanged"
            with open(sentinel, "wb") as handle:
                handle.write(expected)
            mutation_log = os.path.join(tmp, "mutations.log")
            with open(mutation_log, "w", encoding="utf-8") as handle:
                handle.write("")
            bin_dir = os.path.join(tmp, "bin")
            os.makedirs(bin_dir)
            mutators = ("chmod", "chown", "systemctl", "iptables", "useradd")
            for name in mutators:
                stub = os.path.join(bin_dir, name)
                with open(stub, "w", encoding="utf-8") as handle:
                    handle.write("#!/bin/sh\necho \"$0 $*\" >> \"$OPOS_MUTATION_LOG\"\nexit 0\n")
                os.chmod(stub, 0o755)
            proc = _run_apply_with_isolated_root(
                {
                    "TEST": "1",
                    "OPOS_ROOT": tmp,
                    "OPOS_MUTATION_LOG": mutation_log,
                    "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                }
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            self.assertIn("TEST is ignored; use OPOS_HARDEN_TEST instead", proc.stderr)
            with open(mutation_log, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "")
            with open(sentinel, "rb") as handle:
                self.assertEqual(handle.read(), expected)
            found = []
            for root, dirs, files in os.walk(tmp):
                for name in dirs:
                    found.append(os.path.relpath(os.path.join(root, name), tmp))
                for name in files:
                    found.append(os.path.relpath(os.path.join(root, name), tmp))
            expected_files = ["etc", os.path.join("etc", "sentinel"), "mutations.log", "bin"]
            for name in mutators:
                expected_files.append(os.path.join("bin", name))
            self.assertCountEqual(found, expected_files)


if __name__ == "__main__":
    unittest.main()
