# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/opos-release.py (build, verify, index) and deploy/duo-push-update.ps1. Keys are throw-away, made with ssh-keygen at run time."""
import ast
import calendar
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "opos-release.py"
PUSH = ROOT / "deploy" / "duo-push-update.ps1"
HOWTO = ROOT / "docs" / "ota-release-howto.md"
STAMP = "2026-11-08T12:00:00Z"
EPOCH = calendar.timegm((2026, 11, 8, 12, 0, 0))

spec = importlib.util.spec_from_file_location("opos_release", TOOL)
rel = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rel
spec.loader.exec_module(rel)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(*cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def tool(*args):
    """main() in this process: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = rel.main([str(a) for a in args])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            if isinstance(exc.code, str):
                err.write(exc.code)
    return code, out.getvalue(), err.getvalue()


def keygen(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "opos-test", "-f", path)
    assert proc.returncode == 0, proc.stderr
    return path


def signers_line(pub, identity="opos-release"):
    kind, blob = pub.read_text().split()[:2]
    return "%s %s %s\n" % (identity, kind, blob)


def ssh_sign(key, data, namespace="opos-update"):
    with tempfile.TemporaryDirectory() as td:
        Path(td, "m").write_bytes(data)
        proc = run("ssh-keygen", "-Y", "sign", "-f", key, "-n", namespace, "m", cwd=td)
        assert proc.returncode == 0, proc.stderr
        return Path(td, "m.sig").read_bytes()


def info(name, data=b"", kind=tarfile.REGTYPE, mode=0o644, link=""):
    t = tarfile.TarInfo(name)
    t.size, t.mode, t.type, t.linkname, t.mtime = len(data) if kind == tarfile.REGTYPE else 0, mode, kind, link, EPOCH
    return t, data


def make_tar(entries, fmt=tarfile.USTAR_FORMAT, gz=False):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz" if gz else "w", format=fmt) as tf:
        for t, data in entries:
            tf.addfile(t, io.BytesIO(data) if t.size else None)
    return raw.getvalue()


def make_payload(files):
    """files: {path: (data, mode)} as a gzip tar, written by the test itself."""
    return gzip.compress(make_tar([info(p, d, mode=m) for p, (d, m) in sorted(files.items())]), mtime=0)


def members(path):
    with tarfile.open(path) as tf:
        return [(m.name, tf.extractfile(m).read()) for m in tf]


BASE_FILES = {
    "opt/opos/src/opos/__init__.py": (b"# fixture\n", 0o644),
    "opt/opos/src/opos/web.py": (b"print('hi')\n", 0o644),
    "usr/libexec/opos/opos-kiosk": (b"#!/bin/sh\necho kiosk\n", 0o755),
}
PARTS = {"manifest_json": "manifest.json", "manifest_json_sig": "manifest.json.sig", "payload_tar_gz": "payload.tar.gz"}


def manifest_for(files, payload, over=None):
    m = {"format": 1, "serial": 9, "version": "1.0.0", "revision": "a" * 40, "created": STAMP, "requires_base": 1, "scope": "app",
         "payload": {"sha256": sha(payload), "size": len(payload)},
         "files": [{"path": p, "sha256": sha(d), "size": len(d), "mode": "%04o" % m} for p, (d, m) in sorted(files.items())],
         "notes": ""}
    m.update(over or {})
    return m


def as_json(m):
    return (json.dumps(m, indent=2) + "\n").encode()


def git(cwd, *args, **env):
    proc = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                           "-c", "core.autocrlf=false", "-c", "core.hooksPath=" + str(cwd), "-C", str(cwd)] + [str(a) for a in args],
                          capture_output=True, text=True, env=dict(os.environ, **env))
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


REPO_FILES = {
    "src/opos/__init__.py": "# fixture\n",
    "src/opos/web.py": "print('hi')\n",
    "src/opos/web_static/page.css": "body { margin: 0 }\n",
    "LICENSE": "license\n",
    "THIRD_PARTY.md": "third party\n",
    "packaging/duo-os/opos-kiosk": "#!/bin/sh\necho kiosk\n",
    "packaging/duo-os/opos-switch": "#!/bin/sh\necho switch\n",
    "packaging/duo-os/opos-server.service": "[Service]\n",
    "packaging/duo-os/opos.service": "[Service]\n",
    "packaging/duo-os/build-image.sh": "#!/bin/sh\necho no\n",
}
COMMIT_DATE = "2026-11-08T12:00:00+00:00"


def make_repo(path):
    for rel_path, text in REPO_FILES.items():
        f = path / rel_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, newline="\n")
        if text.startswith("#!"):
            f.chmod(0o755)
    git(path, "init", "-q")
    git(path, "add", ".")
    git(path, "update-index", "--chmod=+x", *[p for p, t in REPO_FILES.items() if t.startswith("#!")])
    git(path, "commit", "-q", "-m", "fixture", GIT_AUTHOR_DATE=COMMIT_DATE, GIT_COMMITTER_DATE=COMMIT_DATE)
    return git(path, "rev-parse", "HEAD")


needs_keygen = unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen (OpenSSH client) is not installed")
needs_git = unittest.skipUnless(shutil.which("git"), "git is not installed")
BAD_PATH = re.compile(r"outside the allowlist|is not allowed|not part of the app tree|bad path")


@needs_keygen
@needs_git
class ReleaseCase(unittest.TestCase):
    """A fixture repository, two throw-away keys and helpers shared by the tests below."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.top = top = Path(cls.tmp.name)
        cls.key = keygen(top / "keys" / "release")
        cls.other = keygen(top / "keys" / "other")
        cls.signers = top / "allowed_signers"
        cls.signers.write_text(signers_line(cls.key.with_name("release.pub")))
        cls.other_signers = top / "other_signers"
        cls.other_signers.write_text(signers_line(cls.other.with_name("other.pub")))
        cls.repo = top / "repo"
        cls.repo.mkdir()
        cls.rev = make_repo(cls.repo)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def new_dir(self, name="d"):
        type(self).n += 1
        d = self.top / ("%s%d" % (name, self.n))
        d.mkdir()
        return d

    def build(self, serial=7, out=None, extra=(), ref="HEAD", key=None, version="0.2.0"):
        out = out or self.new_dir("out")
        code, so, se = tool("build", "--repo", self.repo, "--ref", ref, "--serial", serial, "--version", version, "--key", key or self.key,
                            "--out", out, *extra)
        return code, so, se, Path(out) / ("opos-%d.opos-update" % serial)

    def built(self, serial=7, **kw):
        code, so, se, path = self.build(serial, **kw)
        self.assertEqual(code, 0, se)
        return path

    def verify_cmd(self, bundle, *extra, signers=None):
        return tool("verify", bundle, "--allowed-signers", signers or self.signers, *extra)

    def assertOk(self, bundle, *extra):
        code, so, se = self.verify_cmd(bundle, *extra)
        self.assertEqual(code, 0, se)
        return so

    def assertRefused(self, bundle, text, *extra, signers=None):
        code, so, se = self.verify_cmd(bundle, *extra, signers=signers)
        self.assertEqual(code, 1, "accepted: %s" % so)
        if isinstance(text, re.Pattern):
            self.assertRegex(se, text)
        else:
            self.assertIn(text, se)

    def write_bundle(self, data):
        path = self.new_dir("b") / "opos-9.opos-update"
        path.write_bytes(data)
        return path

    def signed(self, files=None, payload=None, key=None, namespace="opos-update", raw=None, over=None):
        """A bundle signed with the real key whose manifest and payload the test controls."""
        files = BASE_FILES if files is None else files
        payload = make_payload(files) if payload is None else payload
        manifest = raw if raw is not None else as_json(manifest_for(files, payload, over))
        sig = ssh_sign(key or self.key, manifest, namespace)
        return self.write_bundle(make_tar([info("manifest.json", manifest), info("manifest.json.sig", sig), info("payload.tar.gz", payload)]))

    def repacked(self, bundle, **replace):
        parts = dict(members(bundle))
        parts.update({PARTS[k]: v for k, v in replace.items()})
        return self.write_bundle(make_tar([info(n, parts[n]) for n in rel.MEMBERS]))


class BuildTest(ReleaseCase):
    def test_build_then_verify(self):
        bundle = self.built(extra=["--overlay", "opos-kiosk", "--overlay", "opos-server.service", "--notes", "first"])
        out = self.assertOk(bundle, "--list")
        self.assertIn("serial 7", out)
        self.assertIn("usr/libexec/opos/opos-kiosk", out)
        self.assertEqual([n for n, _ in members(bundle)], ["manifest.json", "manifest.json.sig", "payload.tar.gz"])
        with tarfile.open(bundle) as tf:
            self.assertEqual([(m.type, m.mode, m.uid, m.gid, m.mtime) for m in tf], [(tarfile.REGTYPE, 0o644, 0, 0, EPOCH)] * 3)
        parts = dict(members(bundle))
        manifest = json.loads(parts["manifest.json"])
        self.assertEqual(list(manifest), ["format", "serial", "version", "revision", "created", "requires_base", "scope", "payload", "files", "notes"])
        self.assertEqual((manifest["format"], manifest["serial"], manifest["version"], manifest["requires_base"], manifest["scope"], manifest["notes"]),
                         (1, 7, "0.2.0", 1, "app", "first"))
        self.assertEqual((manifest["revision"], manifest["created"]), (self.rev, STAMP))
        self.assertEqual(manifest["payload"], {"sha256": sha(parts["payload.tar.gz"]), "size": len(parts["payload.tar.gz"])})
        self.assertTrue(parts["manifest.json.sig"].startswith(b"-----BEGIN SSH SIGNATURE-----"))
        want = {"opt/opos/src/opos/__init__.py": "0644", "opt/opos/src/opos/web.py": "0644", "opt/opos/src/opos/web_static/page.css": "0644",
                "opt/opos/LICENSE": "0644", "opt/opos/THIRD_PARTY.md": "0644",
                "usr/libexec/opos/opos-kiosk": "0755", "etc/systemd/system/opos-server.service": "0644"}
        self.assertEqual({f["path"]: f["mode"] for f in manifest["files"]}, want)
        self.assertEqual([f["path"] for f in manifest["files"]], sorted(want))
        with tarfile.open(fileobj=io.BytesIO(parts["payload.tar.gz"]), mode="r:gz") as tf:
            for f in manifest["files"]:
                data = tf.extractfile(f["path"]).read()
                self.assertEqual((sha(data), len(data)), (f["sha256"], f["size"]))
            self.assertEqual(tf.extractfile("usr/libexec/opos/opos-kiosk").read(), REPO_FILES["packaging/duo-os/opos-kiosk"].encode())

    def test_payload_is_normalised(self):
        payload = dict(members(self.built(extra=["--overlay", "opos-switch", "--overlay", "opos.service"])))["payload.tar.gz"]
        self.assertEqual(payload[:2], b"\x1f\x8b")
        self.assertEqual(payload[3], 0, "gzip header carries no file name")
        self.assertEqual(payload[4:8], b"\0\0\0\0", "gzip header time is zero")
        self.assertEqual(gzip.decompress(payload)[257:265], b"ustar\x0000", "plain ustar, no pax headers")
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tf:
            ms = tf.getmembers()
        self.assertEqual([m.name for m in ms], sorted(m.name for m in ms))
        for m in ms:
            self.assertEqual((m.type, m.uid, m.gid, m.uname, m.gname, m.mtime, m.pax_headers), (tarfile.REGTYPE, 0, 0, "", "", EPOCH, {}), m.name)
            self.assertIn(m.mode, (0o644, 0o755), m.name)
        modes = {m.name: m.mode for m in ms}
        self.assertEqual((modes["usr/sbin/opos-switch"], modes["etc/systemd/system/opos.service"]), (0o755, 0o644))

    def test_scope_base_and_created_are_taken_from_the_arguments(self):
        bundle = self.built(8, extra=["--scope", "boot", "--requires-base", "2", "--created", "2030-01-02T03:04:05Z"])
        manifest = json.loads(dict(members(bundle))["manifest.json"])
        self.assertEqual((manifest["scope"], manifest["requires_base"], manifest["created"]), ("boot", 2, "2030-01-02T03:04:05Z"))
        with tarfile.open(bundle) as tf:
            self.assertEqual({m.mtime for m in tf}, {calendar.timegm((2030, 1, 2, 3, 4, 5))})

    def test_reproducible_from_a_ref(self):
        a, b = self.built(extra=["--overlay", "opos-kiosk"]), self.built(extra=["--overlay", "opos-kiosk"])
        self.assertNotEqual(a, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertNotEqual(a.read_bytes(), self.built(extra=["--overlay", "opos-kiosk", "--notes", "x"]).read_bytes())
        self.assertNotEqual(a.read_bytes(), self.built(extra=["--overlay", "opos-switch"]).read_bytes())

    def test_reproducible_from_a_directory_and_the_same_as_the_ref(self):
        tree = self.new_dir("tree") / "t"
        shutil.copytree(self.repo, tree, ignore=shutil.ignore_patterns(".git"))
        common = ["--serial", 7, "--version", "0.2.0", "--key", self.key, "--created", STAMP, "--overlay", "opos-kiosk"]

        def from_dir():
            out = self.new_dir("out")
            self.assertEqual(tool("build", "--dir", tree, "--revision", self.rev, "--out", out, *common)[0], 0)
            return (out / "opos-7.opos-update").read_bytes()
        first = from_dir()
        for p in tree.rglob("*"):
            if p.is_file():
                os.utime(p, (1, 1))
        (tree / "src" / "opos" / "__pycache__").mkdir()
        (tree / "src" / "opos" / "__pycache__" / "web.cpython-314.pyc").write_bytes(b"junk")
        self.assertEqual(from_dir(), first, "file times and bytecode caches do not matter")
        out = self.new_dir("out")
        self.assertEqual(tool("build", "--repo", self.repo, "--out", out, *common)[0], 0)
        self.assertEqual((out / "opos-7.opos-update").read_bytes(), first, "a directory and the ref it came from give the same bytes")

    def test_a_different_key_changes_only_the_signature(self):
        a, b = self.built(), self.built(key=self.other, extra=["--allowed-signers", self.other_signers])
        pa, pb = dict(members(a)), dict(members(b))
        self.assertEqual(pa["payload.tar.gz"], pb["payload.tar.gz"])
        self.assertEqual(pa["manifest.json"], pb["manifest.json"])
        self.assertNotEqual(pa["manifest.json.sig"], pb["manifest.json.sig"])

    def test_real_tree_builds_and_verifies_with_every_known_overlay(self):
        out = self.new_dir("real")
        args = ["build", "--dir", ROOT, "--revision", "b" * 40, "--created", STAMP, "--serial", 3, "--version", "0.0.1", "--key", self.key, "--out", out]
        for name in sorted(rel.OVERLAY_DEST):
            args += ["--overlay", name]
        code, so, se = tool(*args)
        self.assertEqual(code, 0, se)
        bundle = out / "opos-3.opos-update"
        manifest = json.loads(dict(members(bundle))["manifest.json"])
        paths = {f["path"] for f in manifest["files"]}
        self.assertIn("opt/opos/src/opos/web.py", paths)
        self.assertIn("opt/opos/src/opos/web_static/js-1.js", paths)
        for dest in rel.OVERLAY_DEST.values():
            self.assertIn(dest, paths)
        self.assertEqual([p for p in paths if "__pycache__" in p or p.endswith(".pyc") or p.startswith("packaging/") or p == "opt/opos/REVISION"], [])
        self.assertEqual(sha((ROOT / "src" / "opos" / "web.py").read_bytes()), next(f["sha256"] for f in manifest["files"] if f["path"] == "opt/opos/src/opos/web.py"))
        self.assertEqual(self.verify_cmd(bundle)[0], 0)

    def test_a_relative_key_path_works(self):
        here = os.getcwd()
        os.chdir(self.key.parent)
        try:
            code, so, se = tool("build", "--repo", self.repo, "--serial", 7, "--version", "0.2.0", "--key", self.key.name, "--out", self.new_dir("out"))
        finally:
            os.chdir(here)
        self.assertEqual(code, 0, se)

    def test_overlay_sources_come_from_the_ref_not_the_working_tree(self):
        (self.repo / "packaging" / "duo-os" / "opos-kiosk").write_text("#!/bin/sh\necho edited\n", newline="\n")
        try:
            bundle = self.built(extra=["--overlay", "opos-kiosk"])
        finally:
            (self.repo / "packaging" / "duo-os" / "opos-kiosk").write_text(REPO_FILES["packaging/duo-os/opos-kiosk"], newline="\n")
        with tarfile.open(fileobj=io.BytesIO(dict(members(bundle))["payload.tar.gz"])) as tf:
            self.assertEqual(tf.extractfile("usr/libexec/opos/opos-kiosk").read(), REPO_FILES["packaging/duo-os/opos-kiosk"].encode())


class RefusalTest(ReleaseCase):
    def test_overlay_outside_the_allowlist_is_refused(self):
        for spec_text, reason in (
                ("build-image.sh", "no known destination"),
                ("README.md", "no known destination"),
                ("opos-kiosk=etc/passwd", "outside the allowlist"),
                ("opos-kiosk=etc/opos/update/allowed_signers", "outside the allowlist"),
                ("opos-kiosk=etc/opos/update.json", "outside the allowlist"),
                ("opos-kiosk=etc/opos/base-version", "outside the allowlist"),
                ("opos-kiosk=usr/libexec/opos-updater/opos-updater", "outside the allowlist"),
                ("opos-kiosk=usr/sbin/opos-updater", "outside the allowlist"),
                ("opos-server.service=etc/systemd/system/opos-update-boot.service", "outside the allowlist"),
                ("opos-kiosk=usr/libexec/opos/sub/x", "outside the allowlist"),
                ("opos-kiosk=usr/bin/opos-kiosk", "outside the allowlist"),
                ("opos-kiosk=opt/opos/REVISION", "app tree"),
                ("opos-kiosk=opt/opos/src/opos/x.py", "app tree"),
                ("opos-kiosk=etc/default/other", "outside the allowlist"),
                ("opos-kiosk=etc/systemd/system/other.service", "outside the allowlist"),
                ("../opos-kiosk", "expected a file name"),
                ("sub/opos-kiosk", "expected a file name"),
                ("opos-kiosk=../etc/passwd", "not allowed"),
                ("opos-kiosk=/etc/opos/x", "not allowed")):
            with self.subTest(spec_text):
                out = self.new_dir("out")
                code, so, se, path = self.build(extra=["--overlay", spec_text], out=out)
                self.assertEqual(code, 1)
                self.assertIn(reason, se)
                self.assertEqual(list(out.iterdir()), [], "nothing is written for a refused build")

    def test_allowed_overlay_destinations(self):
        for dest in ("usr/libexec/opos/anything", "usr/sbin/opos-anything", "etc/systemd/system/opos-x.service", "etc/default/opos-x",
                     "etc/default/opos", "etc/opos/some.conf"):
            with self.subTest(dest):
                self.assertEqual(rel.classify(dest), "overlay")
                code, so, se, path = self.build(extra=["--overlay", "opos-kiosk=" + dest])
                self.assertEqual(code, 0, se)

    def test_classify_paths(self):
        for ok in ("opt/opos/src/opos/web.py", "opt/opos/src/opos/web_static/js-1.js", "opt/opos/LICENSE", "opt/opos/THIRD_PARTY.md"):
            self.assertEqual(rel.classify(ok), "app")
        for bad in ("", "/", "opt/opos", "opt/opos/", "opt/opos/REVISION", "opt/opos/overlay-backup/x", "opt/opos/src/x.py", "opt/opos/src/opos",
                    "opt/opos/src/other/x.py", "opt/opos/LICENSE/x", "opt/opos/src/opos/./x.py", "opt/opos/src/opos/../x.py", "opt/opos/src//opos/x.py",
                    "opt/opos/src/opos/.hidden", "opt/opos/src/opos/a b.py", "opt/opos/src/opos/a\\b.py", "opt/opos/src/opos/x.new",
                    "opt/opos/src/opos/__pycache__/x.cpython-314.pyc", "opt/opos/src/opos/x.pyc", "opt/opos/src/opos/\u00e9.py",
                    "opt/opos/src/opos/x\n.py", "opt/opos2/x", "etc/passwd", "etc/shadow", "etc/ssh/sshd_config", "usr/bin/python3", "boot/x", "lib/x",
                    "etc/opos/update/allowed_signers", "etc/opos/update", "etc/opos/update.json", "etc/opos/base-version",
                    "usr/libexec/opos-updater/opos-updater", "usr/libexec/opos/a/b", "usr/libexec/opos", "usr/sbin/opos-updater", "usr/sbin/opos-update-x",
                    "usr/sbin/ssh", "usr/sbin/opos", "etc/systemd/system/opos-update-boot.service", "etc/systemd/system/opos-update-confirm.service",
                    "etc/systemd/system/opos.service.d/x.conf", "etc/systemd/system/ssh.service", "etc/systemd/system/opos.timer",
                    "etc/default/opos-updater", "etc/default/ssh", "etc/opos", "etc/opos/a/b", "x" * 101, "opt/opos/" + "/".join(["a"] * 130)):
            with self.subTest(bad):
                with self.assertRaises(rel.Refused):
                    rel.classify(bad)

    def test_directory_with_files_off_the_allowlist_is_refused(self):
        for extra, reason in (("src/evil/x.py", "app tree"), ("src/opos/.DS_Store", "not allowed"), ("src/opos/x.new", "not allowed"), ("LICENSE.bak", None)):
            with self.subTest(extra):
                tree = self.new_dir("tree") / "t"
                shutil.copytree(self.repo, tree, ignore=shutil.ignore_patterns(".git"))
                (tree / extra).parent.mkdir(parents=True, exist_ok=True)
                (tree / extra).write_text("x\n")
                out = self.new_dir("out")
                code, so, se = tool("build", "--dir", tree, "--revision", self.rev, "--created", STAMP, "--serial", 7, "--version", "1", "--key", self.key, "--out", out)
                if reason is None:  # only src/, LICENSE and THIRD_PARTY.md are read from a directory
                    self.assertEqual(code, 0, se)
                else:
                    self.assertEqual(code, 1)
                    self.assertIn(reason, se)
                    self.assertEqual(list(out.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "symbolic links need privileges on Windows")
    def test_links_are_never_shipped(self):
        tree = self.new_dir("tree") / "t"
        shutil.copytree(self.repo, tree, ignore=shutil.ignore_patterns(".git"))
        args = ["--dir", tree, "--revision", self.rev, "--created", STAMP, "--serial", 7, "--version", "1", "--key", self.key]
        os.symlink("web.py", tree / "src" / "opos" / "link.py")
        code, so, se = tool("build", *args, "--out", self.new_dir("out"))
        self.assertEqual((code, "not a regular file" in se), (1, True), se)
        os.unlink(tree / "src" / "opos" / "link.py")
        os.symlink(".", tree / "src" / "opos" / "loop")
        code, so, se = tool("build", *args, "--out", self.new_dir("out"))
        self.assertEqual((code, "directory links" in se), (1, True), se)

    def test_a_ref_with_a_link_is_refused(self):
        repo = self.new_dir("lnk")
        make_repo(repo)
        blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="web.py", capture_output=True, text=True).stdout.strip()
        git(repo, "update-index", "--add", "--cacheinfo", "120000,%s,src/opos/link.py" % blob)
        git(repo, "commit", "-q", "-m", "link", GIT_AUTHOR_DATE=COMMIT_DATE, GIT_COMMITTER_DATE=COMMIT_DATE)
        code, so, se = tool("build", "--repo", repo, "--serial", 7, "--version", "1", "--key", self.key, "--out", self.new_dir("out"))
        self.assertEqual((code, "not a regular file" in se), (1, True), se)

    def test_argument_errors(self):
        out = self.new_dir("out")
        base = ["build", "--repo", self.repo, "--key", self.key, "--out", out]
        for args, text in (
                (["--serial", 0, "--version", "1"], "at least 1"),
                (["--serial", -3, "--version", "1"], "at least 1"),
                (["--serial", 1, "--version", "1", "--requires-base", 0], "at least 1"),
                (["--serial", 1, "--version", "bad version"], "malformed"),
                (["--serial", 1, "--version", "../x"], "malformed"),
                (["--serial", 1, "--version", "1", "--created", "yesterday"], "not a UTC time"),
                (["--serial", 1, "--version", "1", "--created", "2026-13-40T00:00:00Z"], "not a UTC time"),
                (["--serial", 1, "--version", "1", "--ref", "no-such-ref"], "git rev-parse"),
                (["--serial", 1, "--version", "1", "--ref=--upload-pack=x"], "bad ref"),
                (["--serial", 1, "--version", "1", "--revision", "a" * 40], "only for --dir"),
                (["--serial", 1, "--version", "1", "--key", self.top / "missing"], "no such file"),
                (["--serial", 1, "--version", "1", "--overlay", "opos-kiosk", "--overlay", "opos-kiosk"], "twice"),
                (["--serial", 1, "--version", "1", "--identity", "a b"], "malformed"),
                (["--serial", 1, "--version", "1", "--identity=-Ofoo"], "malformed")):
            with self.subTest(args):
                code, so, se = tool(*base, *args)
                self.assertNotEqual(code, 0)
                self.assertIn(text, se)
        for args in (["--serial", "x", "--version", "1"], ["--serial", 1], ["--version", "1"], ["--serial", 1, "--version", "1", "--scope", "reboot"]):
            with self.subTest(args):
                self.assertEqual(tool("build", "--repo", self.repo, "--key", self.key, *args)[0], 2)
        self.assertEqual(list(out.iterdir()), [])

    def test_directory_outside_git_needs_revision_and_created(self):
        tree = self.new_dir("tree") / "t"
        shutil.copytree(self.repo, tree, ignore=shutil.ignore_patterns(".git"))
        code, so, se = tool("build", "--dir", tree, "--serial", 1, "--version", "1", "--key", self.key, "--out", self.new_dir("out"))
        self.assertEqual(code, 1)
        self.assertIn("give --revision and --created", se)
        code, so, se = tool("build", "--dir", tree, "--revision", "xyz", "--created", STAMP, "--serial", 1, "--version", "1", "--key", self.key, "--out", self.new_dir("out"))
        self.assertEqual(code, 1)
        self.assertIn("not a 40-hex", se)

    def test_private_key_inside_the_repository_is_refused(self):
        stray = self.repo / "stray-key"
        shutil.copy(self.key, stray)
        try:
            code, so, se, path = self.build(key=stray)
            self.assertEqual(code, 1)
            self.assertIn("inside the repository", se)
            self.assertFalse(path.exists())
        finally:
            stray.unlink()

    def test_serial_discipline(self):
        out = self.new_dir("out")
        self.assertEqual(self.build(7, out=out)[0], 0)
        code, so, se, _ = self.build(7, out=out)
        self.assertEqual(code, 1)
        self.assertIn("never reused", se)
        self.assertEqual(tool("index", out / "opos-7.opos-update")[0], 0)
        for serial in (6, 7):
            code, so, se, _ = self.build(serial, out=out)
            self.assertEqual(code, 1, serial)
            self.assertTrue("already announces serial 7" in se or "never reused" in se, se)
        self.assertEqual(self.build(8, out=out)[0], 0)
        self.assertEqual(tool("build", "--repo", self.repo, "--serial", 7, "--version", "0.2.0", "--key", self.key, "--out", out, "--force")[0], 0)

    def test_a_signing_failure_leaves_nothing_behind(self):
        junk = self.top / "not-a-key"
        junk.write_text("this is not a key\n")
        code, so, se, path = self.build(key=junk)
        self.assertEqual(code, 1)
        self.assertIn("could not sign", se)
        self.assertEqual(list(path.parent.iterdir()), [])

    def test_signing_with_the_wrong_key_fails_the_self_check(self):
        code, so, se, path = self.build(extra=["--allowed-signers", self.other_signers])
        self.assertEqual(code, 1)
        self.assertIn("failed its own check", se)
        self.assertEqual(list(path.parent.iterdir()), [])


class VerifyTest(ReleaseCase):
    def test_good_bundle_reports_its_facts(self):
        out = self.assertOk(self.built(), "--current-serial", "6", "--base", "1")
        self.assertIn('Good "opos-update" signature for opos-release', out)
        self.assertIn("scope app", out)
        self.assertIn(self.rev[:12], out)

    def test_serial_and_base_gates(self):
        bundle = self.built(extra=["--requires-base", "2"])
        self.assertRefused(bundle, "needs base 2", "--base", "1")
        self.assertRefused(bundle, "not greater than the current serial 7", "--current-serial", "7")
        self.assertRefused(bundle, "not greater than the current serial 9", "--current-serial", "9")
        self.assertOk(bundle, "--base", "2", "--current-serial", "6")

    def test_signature_checks(self):
        bundle = self.built()
        self.assertRefused(bundle, "does not verify", signers=self.other_signers)
        self.assertRefused(bundle, "does not verify", "--identity", "somebody-else")
        self.assertRefused(bundle, "allowed_signers file not found", signers=self.top / "nope")
        both = self.top / "both"
        both.write_text(self.other_signers.read_text() + self.signers.read_text())
        self.assertEqual(self.verify_cmd(bundle, signers=both)[0], 0)
        self.assertRefused(self.signed(namespace="something-else"), "does not verify")
        by_other = self.signed(key=self.other)
        self.assertRefused(by_other, "does not verify")
        self.assertEqual(self.verify_cmd(by_other, signers=self.other_signers)[0], 0)

    def test_tampering_is_detected(self):
        bundle = self.built(extra=["--overlay", "opos-kiosk"])
        parts = dict(members(bundle))
        manifest = json.loads(parts["manifest.json"])
        # a changed manifest keeps its old signature
        self.assertRefused(self.repacked(bundle, manifest_json=as_json(dict(manifest, serial=99))), "does not verify")
        self.assertRefused(self.repacked(bundle, manifest_json=parts["manifest.json"] + b" "), "does not verify")
        self.assertRefused(self.repacked(bundle, manifest_json=parts["manifest.json"].replace(b"0.2.0", b"0.2.1")), "does not verify")
        # a changed signature
        sig = bytearray(parts["manifest.json.sig"])
        middle = len(sig) // 2
        sig[middle] = ord("B") if sig[middle] != ord("B") else ord("C")
        self.assertRefused(self.repacked(bundle, manifest_json_sig=bytes(sig)), "does not verify")
        self.assertRefused(self.repacked(bundle, manifest_json_sig=b"-----BEGIN SSH SIGNATURE-----\n"), "does not verify")
        self.assertRefused(self.repacked(bundle, manifest_json_sig=b"nonsense"), "not an SSH signature")
        # a changed payload: one bit, appended, truncated, swapped for another build's
        payload = parts["payload.tar.gz"]
        flipped = bytearray(payload)
        flipped[len(flipped) // 2] ^= 1
        other = dict(members(self.built(8, extra=["--overlay", "opos-switch"])))["payload.tar.gz"]
        for new in (bytes(flipped), payload + b"\0", payload[:-1], other):
            self.assertRefused(self.repacked(bundle, payload_tar_gz=new), "does not match the manifest's sha256/size")

    def test_a_substituted_file_with_a_matching_hash_still_needs_a_signed_manifest(self):
        bundle = self.signed()
        evil = dict(BASE_FILES, **{"opt/opos/src/opos/web.py": (b"import os; os.system('x')\n", 0o644)})
        payload = make_payload(evil)
        forged = as_json(manifest_for(evil, payload))
        self.assertRefused(self.repacked(bundle, manifest_json=forged, payload_tar_gz=payload), "does not verify")

    def test_tar_structure_is_strict(self):
        good = self.built()
        m, s, p = (dict(members(good))[n] for n in rel.MEMBERS)
        raw = good.read_bytes()

        def put(reason, data):
            with self.subTest(reason, size=len(data)):
                self.assertRefused(self.write_bundle(data), reason)
        three = [info("manifest.json", m), info("manifest.json.sig", s), info("payload.tar.gz", p)]
        put("not a plain tar", b"")
        put("not a plain tar", b"hello")
        put("not a plain tar", raw[:700])
        put("not a plain tar", gzip.compress(raw))
        put("exactly manifest.json", make_tar([three[1], three[0], three[2]]))
        put("exactly manifest.json", make_tar(three[:1] + three[2:]))
        put("exactly manifest.json", make_tar([info("./manifest.json", m)] + three[1:]))
        put("more than 3 members", make_tar(three + [info("extra", b"x")]))
        put("plain regular file", make_tar(three[:2] + [info("payload.tar.gz", kind=tarfile.SYMTYPE, link="/etc/passwd")]))
        put("plain regular file", make_tar(three[:2] + [info("payload.tar.gz", kind=tarfile.DIRTYPE)]))
        with_pax = info("payload.tar.gz", p)
        with_pax[0].pax_headers = {"comment": "x"}
        put("plain regular file", make_tar(three[:2] + [with_pax], fmt=tarfile.PAX_FORMAT))
        put("data after the last tar member", raw + b"trailing")
        put("data after the last tar member", raw[:-1] + b"x")
        put("tar end marker", raw[:self.padded_end(raw)])

    @staticmethod
    def padded_end(raw):
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            last = tf.getmembers()[-1]
        end = last.offset_data + last.size
        return end + (-end % 512)

    def test_manifest_schema_is_strict(self):
        files = dict(BASE_FILES)
        good = manifest_for(files, make_payload(files))
        self.assertOk(self.signed(files))

        def case(reason, **over):
            with self.subTest(reason, **{k: str(v)[:30] for k, v in over.items()}):
                self.assertRefused(self.signed(files, over=over), reason)

        def raw_case(reason, manifest):
            with self.subTest(reason):
                self.assertRefused(self.signed(files, raw=json.dumps(manifest).encode()), reason)

        case("unsupported format", format=2)
        case("format must be", format="1")
        for serial in (0, -1, "7", True, 1.5, None, 10**10):
            case("serial must be an integer", serial=serial)
        case("version is malformed", version="")
        case("version is malformed", version="../1")
        case("version is malformed", version=1)
        case("revision is malformed", revision="abc")
        case("revision is malformed", revision="A" * 40)
        case("revision is malformed", revision="a" * 64)
        case("created is malformed", created="yesterday")
        case("created is malformed", created="2026-11-08 12:00:00")
        case("created is not a real date", created="2026-13-40T25:61:61Z")
        case("requires_base must be an integer", requires_base=0)
        case("requires_base must be an integer", requires_base="1")
        case("requires_base must be an integer", requires_base=True)
        case("scope must be one of", scope="reboot")
        case("scope must be one of", scope=None)
        case("notes must be text", notes=5)
        case("notes must be text", notes="x" * 1001)
        case("notes must be text", notes="bell\x07")
        case("payload must have exactly", payload={"sha256": "0" * 64})
        case("payload must have exactly", payload={"sha256": "0" * 64, "size": 1, "extra": 1})
        case("payload.sha256 is malformed", payload={"sha256": "ABC", "size": 5})
        case("payload.size must be an integer", payload={"sha256": "0" * 64, "size": 0})
        case("payload.size must be an integer", payload={"sha256": "0" * 64, "size": 10**9})
        case("files must list", files=[])
        case("files must list", files="x")
        case("must have exactly the keys", extra="key")
        for drop in ("serial", "scope", "files", "payload", "revision"):
            raw_case("must have exactly the keys", {k: v for k, v in good.items() if k != drop})
        entry = good["files"][0]
        for reason, bad in (
                ("must have exactly the keys", {"path": entry["path"], "sha256": entry["sha256"], "size": entry["size"]}),
                ("must have exactly the keys", dict(entry, owner="root")),
                ("mode must be 0644 or 0755", dict(entry, mode="0777")),
                ("mode must be 0644 or 0755", dict(entry, mode="0600")),
                ("mode must be 0644 or 0755", dict(entry, mode="644")),
                ("mode must be 0644 or 0755", dict(entry, mode=420)),
                ("files.sha256 is malformed", dict(entry, sha256="xyz")),
                ("files.size must be an integer", dict(entry, size=-1)),
                ("files.size must be an integer", dict(entry, size="5")),
                ("files.size must be an integer", dict(entry, size=1.5)),
                ("files.size must be an integer", dict(entry, size=10**12)),
                ("outside the allowlist", dict(entry, path="etc/passwd")),
                ("is not allowed", dict(entry, path="opt/opos/src/opos/../../../etc/passwd")),
                ("is not allowed", dict(entry, path="/etc/passwd")),
                ("bad path", dict(entry, path=5))):
            raw_case(reason, dict(good, files=[bad] + good["files"][1:]))
        raw_case("listed twice", dict(good, files=good["files"] + [good["files"][0]]))
        raw_case("files must list", dict(good, files=[dict(entry, path="opt/opos/src/opos/f%d.py" % i) for i in range(rel.MAX_FILES + 1)]))
        raw_case("unpacked size", dict(good, files=[dict(entry, path="opt/opos/src/opos/f%d.py" % i, size=rel.MAX_UNPACKED // 2 + 1) for i in range(2)]))
        raw_case("also used as a directory", dict(good, files=good["files"] + [dict(entry, path="opt/opos/src/opos/web.py/x.py")]))

    def test_the_app_tree_is_required_and_forbidden_paths_are_refused_even_when_signed(self):
        no_app = {"usr/libexec/opos/opos-kiosk": BASE_FILES["usr/libexec/opos/opos-kiosk"]}
        self.assertRefused(self.signed(no_app), "the app tree is missing")
        for path in ("etc/opos/update/allowed_signers", "etc/opos/update.json", "etc/opos/base-version", "usr/libexec/opos-updater/opos-updater",
                     "usr/sbin/opos-updater", "etc/systemd/system/opos-update-boot.service", "opt/opos/REVISION", "etc/passwd", "etc/shadow",
                     "usr/bin/python3", "opt/opos/src/evil/x.py", "../x", "/etc/cron.d/x", "usr/libexec/opos/a/b", "boot/x"):
            with self.subTest(path):
                self.assertRefused(self.signed(dict(BASE_FILES, **{path: (b"x\n", 0o644)})), BAD_PATH)

    def test_json_oddities(self):
        files = dict(BASE_FILES)
        good = as_json(manifest_for(files, make_payload(files)))
        for reason, raw in (
                ("duplicate key", good.replace(b'"format": 1,', b'"format": 1, "format": 1,', 1)),
                ("NaN is not allowed", good.replace(b'"serial": 9', b'"serial": NaN')),
                ("Infinity is not allowed", good.replace(b'"serial": 9', b'"serial": Infinity')),
                ("not valid UTF-8 JSON", b"\xef\xbb\xbf" + good),
                ("not valid UTF-8 JSON", good + b"x"),
                ("not valid UTF-8 JSON", b"\xff\xfe" + good),
                ("not valid UTF-8 JSON", b"[" * 100000),
                ("not valid UTF-8 JSON", b""),
                ("must have exactly the keys", b"[]"),
                ("must have exactly the keys", b"null"),
                ("must have exactly the keys", b'"manifest"'),
                ("larger than", b" " * (rel.MAX_MANIFEST + 1))):
            with self.subTest(reason, raw=raw[:30]):
                self.assertRefused(self.signed(files, raw=raw), reason)

    def test_payload_contents_are_strict(self):
        files = dict(BASE_FILES)
        pad = {"opt/opos/src/opos/extra.py": (b"x = 1\n", 0o644)}
        regular = [info(p, d, mode=m) for p, (d, m) in sorted(files.items())]

        def case(reason, payload, listed=None):
            with self.subTest(reason):
                self.assertRefused(self.signed(listed or files, payload=payload), reason)
        web = "opt/opos/src/opos/web.py"
        kiosk = "usr/libexec/opos/opos-kiosk"
        case("is not in the manifest", make_payload(dict(files, **pad)))
        case("payload lacks", make_payload({k: v for k, v in files.items() if k != web}))
        case("does not match the manifest hash", make_payload(dict(files, **{web: (b"print('HI')\n", 0o644)})))
        case("tar mode or size differs", make_payload(dict(files, **{kiosk: (files[kiosk][0], 0o644)})))
        case("tar mode or size differs", make_payload(dict(files, **{web: (files[web][0], 0o666)})))
        case("plain regular files", make_tar(regular + [info("opt/opos/src/opos/extra.py", kind=tarfile.SYMTYPE, link="/etc/shadow")], gz=True), dict(files, **pad))
        for name, kind in (("sub", tarfile.DIRTYPE), ("dev", tarfile.CHRTYPE), ("fifo", tarfile.FIFOTYPE)):
            case("plain regular files", make_tar(regular + [info("opt/opos/src/opos/" + name, kind=kind)], gz=True),
                 dict(files, **{"opt/opos/src/opos/" + name: (b"", 0o644)}))
        case("plain regular files", make_tar(regular + [info("opt/opos/src/opos/hard", kind=tarfile.LNKTYPE, link=web)], gz=True),
             dict(files, **{"opt/opos/src/opos/hard": (b"", 0o644)}))
        case("is not in the manifest", make_tar([info("/etc/passwd", b"x")] + regular, gz=True))
        case("is not in the manifest", make_tar([info("./" + web, b"x")] + regular, gz=True))
        case("appears twice", make_tar(regular + [regular[0]], gz=True))
        case("not a valid gzip tar", make_tar(regular))
        case("not a valid gzip tar", b"not gzip at all")
        case("payload", make_payload(files)[:len(make_payload(files)) // 2])
        case("data after the tar end", gzip.compress(make_tar(regular) + b"\0" * 1024 + b"junk"))
        payload = make_payload(files)
        lie = manifest_for(files, payload)
        lie["files"][1]["sha256"] = "0" * 64
        self.assertRefused(self.signed(files, payload=payload, raw=as_json(lie)), "does not match the manifest hash")
        lie = manifest_for(files, payload)
        lie["files"][1]["size"] += 1
        self.assertRefused(self.signed(files, payload=payload, raw=as_json(lie)), "tar mode or size differs")
        lie = manifest_for(files, payload, {"payload": {"sha256": sha(payload), "size": len(payload) + 1}})
        self.assertRefused(self.signed(files, payload=payload, raw=as_json(lie)), "does not match the manifest's sha256/size")

    def test_files_missing_or_unreadable(self):
        for path in (self.top / "absent.opos-update", self.top):
            code, so, se = self.verify_cmd(path)
            self.assertEqual(code, 1)
            self.assertIn("not a plain tar", se)


class IndexTest(ReleaseCase):
    def test_index_matches_the_bundle(self):
        bundle = self.built(extra=["--overlay", "opos-kiosk"])
        code, so, se = tool("index", bundle, "--allowed-signers", self.signers)
        self.assertEqual(code, 0, se)
        doc = json.loads((bundle.parent / "latest.json").read_text())
        self.assertEqual(doc, {"serial": 7, "version": "0.2.0", "url": "opos-7.opos-update", "sha256": sha(bundle.read_bytes()), "size": bundle.stat().st_size})
        self.assertEqual(list(doc), ["serial", "version", "url", "sha256", "size"])
        self.assertEqual(list(bundle.parent.glob("*.tmp")), [])

    def test_index_url_must_be_relative(self):
        bundle = self.built()
        for good in ("releases/opos-7.opos-update", "a-b/c.d/opos-7.opos-update"):
            with self.subTest(good):
                out = self.new_dir("o") / "latest.json"
                self.assertEqual(tool("index", bundle, "--url", good, "--out", out)[0], 0)
                self.assertEqual(json.loads(out.read_text())["url"], good)
        for bad in ("https://example.invalid/opos-7.opos-update", "//example.invalid/x", "/opos-7.opos-update", "../opos-7.opos-update", "a/../b",
                    "opos-7.opos-update?x=1", "opos-7.opos-update#x", "a b", "", "a//b", "a/", "javascript:x", "C:\\x", ".hidden"):
            with self.subTest(bad):
                code, so, se = tool("index", bundle, "--url", bad, "--out", self.new_dir("o") / "latest.json")
                self.assertEqual(code, 1)
                self.assertIn("relative path", se)

    def test_index_refuses_what_verify_refuses(self):
        bundle = self.built()
        parts = dict(members(bundle))
        bad = self.repacked(bundle, payload_tar_gz=parts["payload.tar.gz"] + b"x")
        code, so, se = tool("index", bad, "--out", self.new_dir("o") / "latest.json")
        self.assertEqual(code, 1)
        self.assertIn("sha256/size", se)
        forged = self.repacked(bundle, manifest_json=parts["manifest.json"].replace(b"0.2.0", b"6.6.6"))
        self.assertEqual(tool("index", forged, "--allowed-signers", self.signers, "--out", self.new_dir("o") / "latest.json")[0], 1)
        self.assertEqual(tool("index", self.top / "absent", "--out", self.new_dir("o") / "latest.json")[0], 1)

    def test_index_does_not_go_backwards(self):
        out = self.new_dir("o")
        seven, eight = self.built(7, out=out), self.built(8, out=out)
        latest = out / "latest.json"
        self.assertEqual(tool("index", eight)[0], 0)
        code, so, se = tool("index", seven)
        self.assertEqual(code, 1)
        self.assertIn("announces serial 8", se)
        self.assertEqual(json.loads(latest.read_text())["serial"], 8)
        self.assertEqual(tool("index", eight)[0], 0, "the same bundle again is fine")
        other_eight = self.built(8, out=self.new_dir("o"), extra=["--overlay", "opos-switch"])
        self.assertEqual(tool("index", other_eight, "--out", latest)[0], 1, "same serial, different bundle")
        self.assertEqual(tool("index", seven, "--out", latest, "--force")[0], 0)
        self.assertEqual(json.loads(latest.read_text())["serial"], 7)


def marker_samples():
    """One example of every kind of private key material the tool knows. Built from pieces, so this file is not a hit itself."""
    pem = lambda kind: b"-----BEGIN " + kind + b" PRIVATE " + b"KEY-----\nMIIB\n-----END " + kind + b" PRIVATE " + b"KEY-----\n"
    return {
        "openssh": pem(b"OPENSSH"), "rsa": pem(b"RSA"), "ec": pem(b"EC"), "pkcs8": b"-----BEGIN PRIVATE " + b"KEY-----\nMIIB\n",
        "encrypted pkcs8": pem(b"ENCRYPTED"), "pgp": b"-----BEGIN PGP PRIVATE " + b"KEY BLOCK-----\n",
        "putty": b"PuTTY-User-Key-File-" + b"3: ssh-ed25519\nEncryption: none\n", "putty lines": b"Private-" + b"Lines: 1\nAAAA\n",
        "armor stripped": b"b3BlbnNzaC1rZXktdjE" + b"AAAAABG5vbmU\n", "ssh2": b"---- BEGIN SSH2 ENCRYPTED PRIVATE " + b"KEY ----\n",
    }


class PrivateKeyLeakTest(ReleaseCase):
    """Review finding 1: a bundle is public. A private key under any name, `.pub` included, must stop the build, and `verify`
    must refuse a bundle that carries one, whoever built it."""

    def build_dir(self, rel_path, data, *extra, key=None, signers=True):
        tree = self.new_dir("tree") / "t"
        shutil.copytree(self.repo, tree, ignore=shutil.ignore_patterns(".git"))
        (tree / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel_path).write_bytes(data)
        out = self.new_dir("out")
        key = tree / rel_path if key == "tree" else key or self.key  # "tree": sign with the file that was just written
        args = ["build", "--dir", tree, "--revision", self.rev, "--created", STAMP, "--serial", 7, "--version", "1", "--key", key,
                "--out", out, *extra] + (["--allowed-signers", self.signers] if signers else [])
        return tool(*args) + (out, tree)

    def assertNoKeyLeak(self, code, se, out, name):
        self.assertEqual(code, 1, se)
        self.assertIn(name, se, "the refusal names the file")
        self.assertIn("private key", se)
        self.assertNotIn("failed its own check", se, "stopped before anything was signed, not by the check after building")
        self.assertEqual(list(out.iterdir()), [], "nothing is written for a refused build")

    def test_a_private_key_in_the_tree_is_refused_by_content_whatever_it_is_called(self):
        private = self.key.read_bytes()
        for name in ("src/opos/release.pub", "src/opos/web_static/notes.txt", "src/opos/data.bin", "src/opos/id_ed25519", "THIRD_PARTY.md", "LICENSE"):
            with self.subTest(name):
                code, so, se, out, tree = self.build_dir(name, private)
                self.assertNoKeyLeak(code, se, out, name)
                for line in private.decode().strip().splitlines()[1:-1]:
                    self.assertNotIn(line, so + se, "the key itself is never printed")

    def test_a_private_key_in_an_overlay_file_is_refused(self):
        code, so, se, out, tree = self.build_dir("packaging/duo-os/opos-kiosk", b"#!/bin/sh\n" + self.key.read_bytes(), "--overlay", "opos-kiosk")
        self.assertNoKeyLeak(code, se, out, "packaging/duo-os/opos-kiosk")

    def test_a_private_key_committed_in_a_ref_is_refused(self):
        repo = self.new_dir("keyrepo")
        make_repo(repo)
        (repo / "src" / "opos" / "release.pub").write_bytes(self.key.read_bytes())
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "oops", GIT_AUTHOR_DATE=COMMIT_DATE, GIT_COMMITTER_DATE=COMMIT_DATE)
        out = self.new_dir("out")
        code, so, se = tool("build", "--repo", repo, "--serial", 7, "--version", "1", "--key", self.key, "--allowed-signers", self.signers, "--out", out)
        self.assertNoKeyLeak(code, se, out, "src/opos/release.pub")

    def test_every_known_kind_of_private_key_is_recognised_and_public_things_are_not(self):
        for label, data in marker_samples().items():
            with self.subTest(label):
                self.assertTrue(rel.private_key_marker(b"x" * 3 + data + b"y"))
                code, so, se, out, tree = self.build_dir("src/opos/web_static/page.css", b"body{}\n" + data)
                self.assertNoKeyLeak(code, se, out, "src/opos/web_static/page.css")
        pub = (self.key.with_name("release.pub")).read_bytes()
        for fine in (pub, b"-----BEGIN " + b"CERTIFICATE-----\nMIIB\n", b"-----BEGIN PUBLIC " + b"KEY-----\nMIIB\n", b"private key handling\n", b""):
            self.assertIsNone(rel.private_key_marker(fine), fine[:30])

    def test_the_tool_and_the_repository_scan_know_the_same_markers(self):
        self.assertEqual({rx.pattern for _, rx in rel.PRIVATE_MARKERS}, {rx.pattern for rx in NoPrivateKeysTest.MARKERS})

    def test_a_public_key_in_the_tree_is_not_a_leak(self):
        pub = self.key.with_name("release.pub").read_bytes()
        code, so, se, out, tree = self.build_dir("src/opos/release.pub", pub)
        self.assertEqual(code, 0, se)
        bundle = out / "opos-7.opos-update"
        with tarfile.open(fileobj=io.BytesIO(dict(members(bundle))["payload.tar.gz"])) as tf:
            self.assertEqual(tf.extractfile("opt/opos/src/opos/release.pub").read(), pub)

    def test_a_key_file_called_pub_must_be_a_public_key(self):
        """The `.pub` name lifts the 'key is inside the repository' rule; the content has to earn it."""
        private = self.key.read_bytes()
        kind, b64 = self.key.with_name("release.pub").read_text().split()[:2]
        good = ("%s %s\n" % (kind, b64)).encode()
        samples = {"a private key": private, "empty": b"", "words": b"ssh-ed25519 not-base64!!\n", "no blob": b"ssh-ed25519\n",
                   "a blob of another type": ("ssh-rsa %s\n" % b64).encode(), "a type nobody has": ("ssh-nope %s\n" % b64).encode(),
                   "binary": bytes(range(256)), "a good line then junk": good + b"rubbish\n", "a good line then a private key": good + private,
                   "a truncated blob": ("%s %s\n" % (kind, b64[:20])).encode(), "a huge file": b"# " + b"x" * 20000 + b"\n" + good}
        for label, data in samples.items():
            for where in ("outside the tree", "inside the tree"):
                with self.subTest(label + ", " + where):
                    key = self.top / "keys" / "decoy.pub" if where == "outside the tree" else "tree"
                    if key != "tree":
                        key.write_bytes(data)
                    code, so, se, out, tree = self.build_dir("src/opos/release.pub", data, key=key)
                    self.assertEqual(code, 1, so)
                    self.assertIn("is not an OpenSSH public key", se)
                    self.assertIn("release.pub" if key == "tree" else "decoy.pub", se, "the refusal names the file")
                    self.assertEqual(list(out.iterdir()), [])
                    for line in private.decode().strip().splitlines()[1:-1]:
                        self.assertNotIn(line, so + se)

    def test_verify_refuses_a_bundle_that_carries_a_private_key(self):
        """The second line: whoever built the bundle, and whatever it is called inside, `verify` (and the build's own check) say no."""
        private = self.key.read_bytes()
        for name in ("opt/opos/src/opos/release.pub", "opt/opos/src/opos/notes.txt"):
            with self.subTest(name):
                files = dict(BASE_FILES, **{name: (private, 0o644)})
                bundle = self.signed(files=files)
                self.assertRefused(bundle, name + " contains a PEM or OpenSSH private key")
        files = dict(BASE_FILES)
        files["opt/opos/src/opos/straddle.bin"] = (b"x" * ((1 << 20) - 9) + marker_samples()["openssh"], 0o644)
        self.assertRefused(self.signed(files=files), "opt/opos/src/opos/straddle.bin contains")
        self.assertOk(self.signed(files=dict(BASE_FILES, **{"opt/opos/src/opos/release.pub": (self.key.with_name("release.pub").read_bytes(), 0o644)})))


class KeygenHelpTest(ReleaseCase):
    def test_help_text(self):
        code, so, se = tool("keygen-help")
        self.assertEqual(code, 0, se)
        for needle in ("ssh-keygen -t ed25519", "opos-release ssh-ed25519 AAAA...", "OPOS_UPDATE_SIGNERS", "docs/ota-release-howto.md", "outside the repository"):
            self.assertIn(needle, so)

    def test_public_key_line_and_private_key_refusal(self):
        pub = self.key.with_name("release.pub")
        code, so, se = tool("keygen-help", "--pub", pub)
        self.assertEqual(code, 0, se)
        line = signers_line(pub).strip()
        self.assertTrue(so.rstrip().endswith(line), so)
        self.assertNotIn("opos-test", line)
        code, so, se = tool("keygen-help", "--pub", self.key)
        self.assertEqual(code, 1)
        self.assertIn("not an OpenSSH public key", se)
        for private_line in self.key.read_text().strip().splitlines()[1:-1]:
            self.assertNotIn(private_line, so + se)
        self.assertEqual(tool("keygen-help", "--pub", self.top / "absent")[0], 1)

    def test_a_file_that_only_looks_like_a_public_key_gets_no_line(self):
        """Review finding 1: two words and a known prefix were enough before; now the blob has to be a key of that type."""
        kind, b64 = self.key.with_name("release.pub").read_text().split()[:2]
        for label, text in (("not base64", "ssh-ed25519 not-base64!!\n"), ("two words", "hello world\n"), ("other type", "ssh-rsa %s\n" % b64),
                            ("a good line then a banner", "ssh-ed25519 %s\n-----BEGIN %s PRIVATE %s-----\n" % (b64, "OPENSSH", "KEY"))):
            with self.subTest(label):
                f = self.top / "keys" / "lookalike.pub"
                f.write_text(text)
                code, so, se = tool("keygen-help", "--pub", f)
                self.assertEqual(code, 1, so)
                self.assertIn("not an OpenSSH public key", se)
                self.assertNotIn("allowed_signers line for", so)


class ToolHygieneTest(unittest.TestCase):
    def test_header_and_endings(self):
        for path in (TOOL, PUSH, Path(__file__)):
            data = path.read_bytes()
            self.assertNotIn(b"\r", data, path.name)
            self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n"), path.name)
            self.assertIn(b"# SPDX-License-Identifier: GPL-3.0-or-later", data.splitlines()[:2], path.name)
        self.assertEqual(TOOL.read_text().splitlines()[0], "#!/usr/bin/env python3")

    def test_standard_library_only(self):
        names = set()
        for node in ast.walk(ast.parse(TOOL.read_text())):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertTrue(names)
        self.assertEqual(names - set(sys.stdlib_module_names), set())
        self.assertNotIn("opos", names, "the tool must not import the updater or any OPOS module")

    def test_nothing_secret_in_what_this_work_adds(self):
        for path in (TOOL, PUSH, Path(__file__), HOWTO):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"-----BEGIN [A-Z ]*PRIVATE KEY", path.name)
            self.assertNotRegex(text, r"(?<![\d.])\d{1,3}(\.\d{1,3}){3}(?![\d.])", path.name)


class NoPrivateKeysTest(unittest.TestCase):
    MARKERS = [
        re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE " + rb"KEY[A-Z ]*-----"),
        re.compile(rb"-----BEGIN PGP PRIVATE " + rb"KEY BLOCK-----"),
        re.compile(rb"PuTTY-User-Key-File-\d+:"),
        re.compile(rb"Private-" + rb"Lines:"),
        re.compile(rb"b3BlbnNzaC1rZXktdjE" + rb"A"),  # base64 of "openssh-key-v1": a private key with its armor stripped
        re.compile(rb"---- BEGIN SSH2 ENCRYPTED PRIVATE " + rb"KEY ----"),
    ]
    SECRET_NAMES = re.compile(r"(^|/)(id_(rsa|dsa|ecdsa|ed25519)|[^/]*_(ed25519|ecdsa|rsa)|[^/]*\.(pem|p12|pfx|ppk|jks|keystore))$")

    def files(self):
        try:
            proc = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True)
            names = [n for n in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
            if names:
                return names, "tracked"
        except (OSError, subprocess.CalledProcessError):
            pass
        names = []  # a source archive, or a worktree git cannot read from here: every file there is checked instead
        for dp, dns, fns in os.walk(ROOT):
            dns[:] = [d for d in dns if d not in (".git", ".scratch", "__pycache__", "node_modules")]
            names += [os.path.relpath(os.path.join(dp, f), ROOT).replace(os.sep, "/") for f in fns]
        return names, "all"

    def test_no_private_key_material_in_the_repository(self):
        names, kind = self.files()
        self.assertGreater(len(names), 50, kind)
        hits = []
        for name in names:
            path = ROOT / name
            if not path.is_file() or path.is_symlink():
                continue
            if self.SECRET_NAMES.search(name):
                hits.append(name + " (name)")
            with open(path, "rb") as fh:
                tail = b""
                while chunk := fh.read(1 << 20):
                    window = tail + chunk
                    if any(rx.search(window) for rx in self.MARKERS):
                        hits.append(name)
                        break
                    tail = chunk[-64:]
        self.assertEqual(hits, [], "private key material or key-like file names among the %s files" % kind)

    @needs_keygen
    def test_the_scan_would_catch_a_key(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", Path(td) / "k").returncode, 0)
            data = (Path(td) / "k").read_bytes()
        self.assertTrue(any(rx.search(data) for rx in self.MARKERS))
        self.assertTrue(self.SECRET_NAMES.search("keys/id_ed25519"))
        self.assertTrue(self.SECRET_NAMES.search("release/opos_ed25519"))
        self.assertFalse(self.SECRET_NAMES.search("release/allowed_signers"))
        self.assertFalse(self.SECRET_NAMES.search("release/opos_ed25519.pub"))

    def test_gitignore_keeps_bundles_and_keys_out(self):
        lines = (ROOT / ".gitignore").read_text().splitlines()
        for line in ("*.opos-update", "*_ed25519", "*.pem"):
            self.assertIn(line, lines)


class PushScriptTextTest(unittest.TestCase):
    def setUp(self):
        self.text = PUSH.read_text(encoding="ascii")

    def test_header_and_parameters(self):
        self.assertEqual(self.text.splitlines()[0], "# SPDX-License-Identifier: GPL-3.0-or-later")
        param = self.text.split("param(", 1)[1].split("\n)", 1)[0]
        self.assertRegex(param, r"\[Parameter\(Mandatory = \$true\)\]\[string\]\$Bundle")
        self.assertRegex(param, r'\[Parameter\(Mandatory = \$true\)\]\[Alias\("Host"\)\]\[string\]\$PhoneHost')
        self.assertRegex(param, r"\[Parameter\(Mandatory = \$true\)\]\[string\]\$Key")
        self.assertRegex(param, r'\[string\]\$User = "droidian"')
        self.assertNotRegex(param, r"\$Host\b", "$Host is a read-only automatic variable in PowerShell")
        self.assertNotIn("PhoneHost =", param, "the host has no default")
        self.assertNotIn("Key =", param.replace("Mandatory = $true", ""), "the key has no default")

    def test_only_cat_and_mv_and_one_read_only_question_run_on_the_phone(self):
        calls = re.findall(r'Invoke-Phone\s+"([^"]*)"', self.text)
        self.assertEqual(sorted(c.split()[0] for c in calls), ["cat", "cat", "mv", "systemctl"], calls)
        for call in calls:
            self.assertRegex(call, r"^(cat \$statusFile|mv \$partial \$ready|systemctl is-active opos-update\.path opos-update\.service)$")
        self.assertEqual(len(re.findall(r"& ssh\b", self.text)), 1, "ssh is invoked in one place, through Invoke-Phone")
        self.assertEqual(len(re.findall(r"& scp\b", self.text)), 1)
        # systemctl is asked one read-only question, only once the wait has timed out; the script never tells it to do anything
        # (the advice printed for the owner, after "$hint = ", is text, not a command that runs).
        code = self.text.split("$hint = ", 1)[0]
        self.assertEqual(len(re.findall(r"(?<![\w-])systemctl(?![\w-])", code)), 1)
        self.assertGreater(self.text.index('Invoke-Phone "systemctl is-active'), self.text.index("Start-Sleep"))
        self.assertGreater(self.text.index('Invoke-Phone "systemctl is-active'), self.text.index("if ($null -eq $final)"))
        for word in ("sudo", "su", "rm", "chmod", "chown", "bash", "eval", "exec", "tee", "dd", "Invoke-Expression", "iex"):
            self.assertNotRegex(self.text, r"(?<![\w-])%s(?![\w-])" % re.escape(word), word)

    def test_partial_then_rename(self):
        for needle in ('$inbox = "/var/lib/opos-update/inbox"', '$statusFile = "/var/lib/opos-update/status.json"', '$partial = "$inbox/$($file.Name).partial"',
                       '$ready = "$inbox/$($file.Name)"', '${User}@${scpHost}:$partial"'):
            self.assertIn(needle, self.text)
        self.assertLess(self.text.index('Invoke-Phone "cat'), self.text.index("& scp"), "the status from before the push is read first")
        self.assertLess(self.text.index("& scp"), self.text.index('Invoke-Phone "mv'))
        self.assertLess(self.text.index('Invoke-Phone "mv'), self.text.index("Start-Sleep"))

    def test_settles_on_done_rolled_back_failed_and_exits_non_zero_otherwise(self):
        self.assertIn('@("done", "rolled-back", "failed")', self.text)
        self.assertIn('if ($final.phase -eq "done") { exit 0 }', self.text)
        self.assertRegex(self.text, r"exit 1\s*\}")
        self.assertRegex(self.text, r'Fail "no settled status within \$TimeoutS s[^\n]*" 2\b')
        self.assertIn("[int]$TimeoutS = 300", self.text)

    def test_input_checks_and_ssh_options(self):
        for needle in ("^opos-[0-9]{1,10}\\.opos-update$", "^[a-z_][a-z0-9_-]{0,31}$", "268435456", "BatchMode=yes", "IdentitiesOnly=yes",
                       "StrictHostKeyChecking=accept-new", "ConnectTimeout=10", '"-i", $Key'):
            self.assertIn(needle, self.text)
        self.assertNotIn("StrictHostKeyChecking=no", self.text)
        self.assertNotIn("UserKnownHostsFile", self.text)

    def test_no_machine_specific_values(self):
        self.assertTrue(self.text.isascii(), "Windows PowerShell 5.1 reads a file without a byte-order mark as ANSI")
        self.assertNotIn("\r", self.text)
        for pattern in (r"[A-Za-z]:\\", r"/home/", r"/Users/", r"\\Users\\", r"(?<![\d.])\d{1,3}(\.\d{1,3}){3}(?![\d.])", r"https?://", r"(?i)\.local\b",
                        r"(?i)localhost", r"id_(rsa|ed25519)", r"\.pub\b", r"BEGIN [A-Z ]*KEY"):
            self.assertNotRegex(self.text, pattern)
        self.assertEqual(re.findall(r"fe80\S*", self.text), ["fe80::1%8,"], "the one address is the documentation example in the header")

    def test_the_key_is_never_read_and_the_bundle_only_for_its_serial(self):
        self.assertNotRegex(self.text, r"Get-Content[^\n]*\$(Key|Bundle)")
        self.assertNotRegex(self.text, r"ReadAll|\[IO\.File\]::Read")
        self.assertEqual(re.findall(r"\$Key\b", self.text), ["$Key"] * 3, "the key is tested for existence and handed to ssh with -i, nothing else")
        self.assertEqual(self.text.count("[IO.File]::OpenRead"), 1, "one reader, and it stops after the manifest")
        self.assertIn("-gt 8388608", self.text, "the manifest is read only up to the updater's own limit")

    def test_only_a_status_that_is_matched_to_the_bundle_can_end_the_wait(self):
        """Review finding 12, structure only; what it does is in PushScriptRunTest."""
        loop = self.text.split("while ((Get-Date) -lt $deadline)", 1)[1].split("if ($null -eq $final)", 1)[0]
        self.assertEqual(loop.count("$final ="), 1)
        self.assertIn('if ($ours -and $settled -contains $phase) { $final = $st; break }', loop)
        self.assertIn('"$($st.serial)" -eq "$serial"', loop)
        self.assertIn('"checking $($file.Name)"', loop)


def powershells(native_only=False):
    seen, found = set(), []
    for name in (["pwsh"] if native_only else ["pwsh", "powershell.exe", "powershell"]):
        exe = shutil.which(name)
        if exe and os.path.normcase(exe) not in seen:
            seen.add(os.path.normcase(exe))
            found.append(exe)
    return found


class PushScriptRunTest(unittest.TestCase):
    PARSE = ("$t=[Console]::In.ReadToEnd(); $e=$null; $tok=$null; "
             "$ast=[System.Management.Automation.Language.Parser]::ParseInput($t,[ref]$tok,[ref]$e); "
             "if($e.Count){$e|ForEach-Object{$_.ToString()}; exit 1}; 'parsed ok params=' + $ast.ParamBlock.Parameters.Count")

    def test_script_parses(self):
        shells = powershells()
        if not shells:
            self.skipTest("neither pwsh nor powershell is installed")
        ran = 0
        for exe in shells:
            try:
                proc = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", self.PARSE], input=PUSH.read_text(), capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired):
                continue
            ran += 1
            with self.subTest(exe):
                self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "parsed ok params=6"), proc.stdout + proc.stderr)
        if not ran:
            self.skipTest("no PowerShell could be started here")

    # A stand-in for ssh and scp: records every call, answers `cat` from a script of canned status files, accepts `mv`.
    STUB = '''import json, os, sys
d = os.environ["STUB_DIR"]
tool, args = sys.argv[1], sys.argv[2:]
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(json.dumps([tool] + args) + "\\n")
if tool == "scp":
    sys.exit(int(os.environ.get("STUB_SCP_RC", "0")))
remote = args[-1]
if remote.startswith("cat "):
    script = json.load(open(os.path.join(d, "cat.json")))
    n = int(open(os.path.join(d, "n")).read()) if os.path.exists(os.path.join(d, "n")) else 0
    open(os.path.join(d, "n"), "w").write(str(n + 1))
    item = script[min(n, len(script) - 1)]
    if isinstance(item, int):
        sys.stderr.write("stub: exit %d\\n" % item)
        sys.exit(item)
    sys.stdout.write(item + "\\n")
    sys.exit(0)
if remote.startswith("mv "):
    sys.exit(0)
if remote.startswith("systemctl is-active "):
    units = os.environ.get("STUB_UNITS", "active active")
    if units == "NOANSWER":
        sys.exit(255)
    sys.stdout.write("\\n".join(units.split()) + "\\n")
    sys.exit(0 if units.split()[0] == "active" else 3)
sys.stderr.write("unexpected remote command: " + remote + "\\n")
sys.exit(99)
'''

    def stubbed(self, exe, cat, args=(), name="opos-8.opos-update", scp_rc=0, timeout="30", units="active active", content=None):
        """Run the script against the stand-in ssh and scp. `content`: the bundle's bytes (default: a tar whose manifest says the serial in `name`)."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            (top / "bin").mkdir()
            (top / "stub.py").write_text(self.STUB)
            (top / "cat.json").write_text(json.dumps(cat))
            for tool_name in ("ssh", "scp"):
                if os.name == "nt":
                    (top / "bin" / (tool_name + ".cmd")).write_text('@"%s" "%s" %s %%*\r\n' % (sys.executable, top / "stub.py", tool_name))
                else:
                    f = top / "bin" / tool_name
                    f.write_text('#!/bin/sh\nexec "%s" "%s" %s "$@"\n' % (sys.executable, top / "stub.py", tool_name))
                    f.chmod(f.stat().st_mode | stat.S_IXUSR)
            bundle, key = top / name, top / "login-key"
            digits = re.search(r"\d+", name)
            bundle.write_bytes(self.bundle_bytes(int(digits.group()) if digits else 0) if content is None else content)
            key.write_text("not a key\n")
            env = dict(os.environ, STUB_DIR=str(top), STUB_SCP_RC=str(scp_rc), STUB_UNITS=units, PATH=str(top / "bin") + os.pathsep + os.environ["PATH"])
            cmd = [exe, "-NoProfile", "-NonInteractive", "-File", str(PUSH), "-Bundle", str(bundle), "-Host", "fe80::1%8", "-Key", str(key),
                   "-TimeoutS", timeout, "-PollS", "1"] + list(args)
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=240)
            log = top / "calls.log"
            return proc, ([json.loads(line) for line in log.read_text().splitlines()] if log.exists() else [])

    @staticmethod
    def bundle_bytes(serial):
        """A tar in the release tool's layout whose manifest says `serial`. Not signed: the push script never looks at that."""
        return make_tar([info("manifest.json", as_json({"format": 1, "serial": serial})), info("manifest.json.sig", b"sig"), info("payload.tar.gz", b"x")])

    def native(self):
        shells = powershells(native_only=os.name != "nt")
        if not shells:
            self.skipTest("no PowerShell that can run the script with stub ssh and scp here")
        return shells

    @staticmethod
    def status(phase, time, serial=8, message=""):
        return json.dumps({"phase": phase, "message": message or phase, "serial": serial, "time": time})

    # What the updater really writes (packaging/duo-os/updater/opos-updater, Updater.status): "verifying" has no serial yet and names
    # the inbox file; so does a refusal before the manifest is read. Everything after the manifest carries the bundle's serial.
    @classmethod
    def verifying(cls, time, name="opos-8.opos-update"):
        return cls.status("verifying", time, None, "checking " + name)

    @classmethod
    def refused(cls, time, why="signature check failed: Could not verify signature."):
        return cls.status("failed", time, None, "rejected: " + why)

    def test_done(self):
        for exe in self.native():
            with self.subTest(exe):
                # the first read (is the phone there?), the read just before the hand-over, then one read per poll
                proc, calls = self.stubbed(exe, [self.status("idle", "t0", 7), self.status("idle", "t0", 7), self.verifying("t1"), self.status("applying", "t2"),
                                                 self.status("checking", "t3"), self.status("done", "t4", message="ok")])
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("result: phase=done serial=8 message=ok", proc.stdout)
                self.assertIn("verifying: checking opos-8.opos-update", proc.stdout)
                self.assertNotIn("idle", proc.stdout, "the status from before the push is not reported as progress")
                self.assertNotIn("not this update", proc.stdout)
                self.assertEqual([c[0] for c in calls[:4]], ["ssh", "scp", "ssh", "ssh"])
                self.assertEqual(calls[0][-2:], ["droidian@fe80::1%8", "cat /var/lib/opos-update/status.json"])
                self.assertEqual(calls[1][-1], "droidian@[fe80::1%8]:/var/lib/opos-update/inbox/opos-8.opos-update.partial")
                self.assertTrue(calls[1][-2].endswith("opos-8.opos-update"))
                self.assertIn("-q", calls[1])
                self.assertEqual(calls[2][-1], "cat /var/lib/opos-update/status.json", "the baseline is read after the copy, just before the rename")
                self.assertEqual(calls[3][-1], "mv /var/lib/opos-update/inbox/opos-8.opos-update.partial /var/lib/opos-update/inbox/opos-8.opos-update")
                for c in calls:
                    if c[0] == "ssh":
                        self.assertTrue(c[-1].startswith(("cat ", "mv ")), c)
                        self.assertIn("BatchMode=yes", c)
                        self.assertIn("-i", c)
                self.assertEqual(len(calls), 4 + 4)
                self.assertTrue(all(c[0] == "ssh" and c[-1].startswith("cat ") for c in calls[4:]))

    def test_an_earlier_done_is_not_mistaken_for_this_update(self):
        old, exe = self.status("done", "old", 7), self.native()[0]
        proc, calls = self.stubbed(exe, [old, old, old, self.status("applying", "n1"), self.status("done", "n2")])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len([c for c in calls if c[-1].startswith("cat ")]), 5)
        proc, calls = self.stubbed(exe, [old], timeout="3")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("no settled status within 3 s", proc.stderr)

    def test_another_updates_end_between_two_polls_is_not_this_updates_end(self):
        """Review finding 12. Serial 7 is applying when serial 8 is handed over; a poll then sees 7 change to a terminal state. The old script
        took any changed terminal status for its own and exited 0 here, with serial 8 not yet verified, let alone installed."""
        busy = self.status("applying", "a0", 7)
        cases = ((self.status("done", "a1", 7, "update 7 is installed and healthy"), "done"),
                 (self.status("rolled-back", "a1", 7, "update 7 failed its health check"), "rolled-back"),
                 (self.status("failed", "a1", 7, "rejected: serial 7 is not newer"), "failed"))
        for exe in self.native():
            for theirs, label in cases[:1] if exe != self.native()[0] else cases:  # every shell sees the case of the review; the first sees all
                with self.subTest("%s, then this one succeeds (%s)" % (label, os.path.basename(exe))):
                    proc, calls = self.stubbed(exe, [busy, busy, theirs, self.verifying("a2"), self.status("applying", "a3"), self.status("done", "a4", message="ok")])
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn("result: phase=done serial=8 message=ok", proc.stdout)
                    self.assertIn("(not this update, serial 7) " + label, proc.stdout, "the other update's end is shown, and marked as not this one")
                    self.assertEqual(len([c for c in calls if c[-1].startswith("cat ")]), 2 + 4, "it kept waiting past the other update's end")
        exe = self.native()[0]
        with self.subTest("then this one fails"):
            proc, calls = self.stubbed(exe, [busy, busy, self.status("done", "a1", 7), self.status("applying", "a3"), self.status("rolled-back", "a4", message="health")])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("result: phase=rolled-back serial=8 message=health", proc.stdout)
        with self.subTest("then nothing more is heard"):
            proc, calls = self.stubbed(exe, [busy, busy, self.status("done", "a1", 7, "update 7 is installed and healthy")], timeout="3")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertIn("no settled status within 3 s for serial 8", proc.stderr)
            self.assertIn("does not name serial 8", proc.stderr)
            self.assertIn("update 7 is installed and healthy", proc.stderr, "what the updater said last is quoted, so the owner can see why")
            self.assertNotIn("result:", proc.stdout)

    def test_a_status_for_this_serial_that_was_already_there_is_not_this_push(self):
        """The same serial pushed again (it failed before): its old failure is the baseline, not the answer."""
        exe = self.native()[0]
        old = self.status("failed", "old", 8, "rejected: serial 8 failed on this phone before")
        proc, calls = self.stubbed(exe, [old, old, old, old], timeout="3")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        proc, calls = self.stubbed(exe, [old, old, old, self.status("failed", "new", 8, "rejected: serial 8 is not newer than the running serial 8")])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("not newer", proc.stdout)

    def test_a_refusal_before_the_manifest_has_no_serial_and_is_attributed_with_care(self):
        """A bad signature is refused before the serial is known. The script takes it for this bundle's only when the updater was idle at the
        hand-over, or was seen verifying this file; otherwise another bundle's refusal could be taken for this one."""
        exe = self.native()[0]
        idle, busy = self.status("done", "i0", 7), self.status("verifying", "b0", None, "checking opos-7.opos-update")
        with self.subTest("idle at the hand-over: the refusal is this bundle's, even if the verifying status was missed between two polls"):
            proc, calls = self.stubbed(exe, [idle, idle, self.refused("r1")])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("result: phase=failed serial= message=rejected: signature check failed", proc.stdout)
        with self.subTest("busy at the hand-over: a refusal with no serial is somebody else's until this file is seen verifying"):
            proc, calls = self.stubbed(exe, [busy, busy, self.refused("r1", "bad signature of the other bundle")], timeout="3")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertIn("(not this update, serial ) failed: rejected: bad signature of the other bundle", proc.stdout)
            self.assertIn("rejected: bad signature of the other bundle", proc.stderr)
        with self.subTest("idle at the first read, busy by the hand-over (the copy took a while): the baseline is the later read"):
            # (a working phase with no serial and no file name: nothing a poll could use to tell, so only the baseline can)
            working = self.status("applying", "w0", None, "working")
            proc, calls = self.stubbed(exe, [idle, working, self.refused("r1", "the other one")], timeout="3")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual([c[0] for c in calls[:4]], ["ssh", "scp", "ssh", "ssh"], "read, copy, read again, rename")
        with self.subTest("busy at the hand-over, then this file is seen verifying, then refused"):
            proc, calls = self.stubbed(exe, [busy, busy, self.status("done", "b1", 7), self.verifying("v1"), self.refused("r1")])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("result: phase=failed serial= message=rejected: signature check failed", proc.stdout)
        with self.subTest("idle, but the updater is then seen on another file: a later refusal is not this one's"):
            other = self.verifying("v1", "opos-9.opos-update")
            proc, calls = self.stubbed(exe, [idle, idle, other, self.refused("r1", "the other one")], timeout="3")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        with self.subTest("idle, but another serial is then seen working: a later refusal is not this one's"):
            proc, calls = self.stubbed(exe, [idle, idle, self.status("applying", "o1", 7), self.refused("r1", "the other one")], timeout="3")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertNotIn("result:", proc.stdout)
        with self.subTest("a status with no serial is never a success"):
            proc, calls = self.stubbed(exe, [idle, idle, self.status("done", "d1", None, "something")], timeout="3")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_the_serial_comes_from_the_manifest_and_must_agree_with_the_name(self):
        exe = self.native()[0]
        for label, name, content in (("name and manifest disagree", "opos-8.opos-update", self.bundle_bytes(9)),
                                     ("not a tar", "opos-8.opos-update", b"not a real bundle\n"),
                                     ("manifest is not first", "opos-8.opos-update", make_tar([info("payload.tar.gz", b"x"), info("manifest.json", as_json({"serial": 8}))])),
                                     ("no serial", "opos-8.opos-update", make_tar([info("manifest.json", as_json({"format": 1}))])),
                                     ("empty manifest", "opos-8.opos-update", make_tar([info("manifest.json", b"")])),
                                     ("manifest cut short", "opos-8.opos-update", self.bundle_bytes(8)[:520])):
            with self.subTest(label):
                proc, calls = self.stubbed(exe, [2], name=name, content=content)
                self.assertEqual((proc.returncode, calls), (3, []), proc.stdout + proc.stderr)
        proc, calls = self.stubbed(exe, [2, 2, self.status("done", "x", 123456789)], name="opos-123456789.opos-update")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc, calls = self.stubbed(exe, [2, 2, self.status("done", "x", 1000000000)], name="opos-1000000000.opos-update")
        self.assertEqual(proc.returncode, 0, "ten digits: the release tool and the updater both allow them\n" + proc.stdout + proc.stderr)

    def test_a_bundle_the_release_tool_built_is_read_the_same_way(self):
        """The tool's own manifest layout (serial in the second place, indent 2), not a test's."""
        payload = make_payload(BASE_FILES)
        bundle = rel.make_bundle(as_json(manifest_for(BASE_FILES, payload, {"serial": 12})), b"-----BEGIN SSH SIGNATURE-----\n", payload, EPOCH)
        exe = self.native()[0]
        proc, calls = self.stubbed(exe, [2, 2, self.status("done", "x", 12, "ok")], name="opos-12.opos-update", content=bundle)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("queued as serial 12", proc.stdout)
        proc, calls = self.stubbed(exe, [2, 2, self.status("done", "x", 13, "ok")], name="opos-12.opos-update", content=bundle, timeout="3")
        self.assertEqual(proc.returncode, 2, "a status for another serial does not end it\n" + proc.stdout + proc.stderr)

    def test_a_timeout_says_whether_the_update_path_unit_is_running(self):
        """Review finding: a path unit that the start limit had failed left the push waiting 300 s for nothing, with no reason."""
        exe, old = self.native()[0], self.status("done", "old", 7)
        for units, want in (("failed failed", "the update path unit is failed"), ("inactive inactive", "the update path unit is inactive"),
                            ("active inactive", "may still be working"), ("NOANSWER", "may still be working")):
            with self.subTest(units):
                proc, calls = self.stubbed(exe, [old], timeout="3", units=units)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("no settled status within 3 s", proc.stderr)
                self.assertIn(want, proc.stderr)
                if units != "NOANSWER":
                    self.assertIn("update units (path, service): " + ", ".join(units.split()), proc.stderr)
                asked = [c[-1] for c in calls if c[-1].startswith("systemctl")]
                self.assertEqual(asked, ["systemctl is-active opos-update.path opos-update.service"])
                self.assertEqual(calls[-1][-1], asked[0], "asked once, after the last status read")
        proc, calls = self.stubbed(exe, [old, old, self.status("applying", "n1"), self.status("done", "n2")])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual([c for c in calls if c[-1].startswith("systemctl")], [], "a push that settles never asks")

    def test_failed_and_rolled_back_exit_non_zero(self):
        exe = self.native()[0]
        for phase in ("failed", "rolled-back"):
            with self.subTest(phase):
                proc, calls = self.stubbed(exe, [2, 2, self.status("applying", "a"), self.status(phase, "b", message="health check failed")])
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertIn("phase=" + phase, proc.stdout)

    def test_transport_problems_exit_3_and_never_rename(self):
        exe = self.native()[0]
        proc, calls = self.stubbed(exe, [255])
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual([c[0] for c in calls], ["ssh"], "unreachable: nothing is copied")
        proc, calls = self.stubbed(exe, [2], scp_rc=1)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual([c[0] for c in calls], ["ssh", "scp"], "a failed copy is never renamed into the inbox")

    def test_bad_arguments_exit_3_before_touching_the_phone(self):
        exe = self.native()[0]
        for name in ("opos-8.opos-update.partial", "opos.opos-update", "opos-8.opos-update;id", "opos-8.tar"):
            with self.subTest(name):
                proc, calls = self.stubbed(exe, [2], name=name)
                self.assertEqual((proc.returncode, calls), (3, []), proc.stdout + proc.stderr)
        for bad in (["-User", "a b"], ["-User", "Root;id"]):
            with self.subTest(bad):
                proc, calls = self.stubbed(exe, [2], args=bad)
                self.assertEqual((proc.returncode, calls), (3, []), proc.stdout + proc.stderr)


class HowtoTest(unittest.TestCase):
    def setUp(self):
        self.text = HOWTO.read_text(encoding="utf-8")

    def test_indexed_from_the_docs_readme(self):
        self.assertIn("(ota-release-howto.md)", (ROOT / "docs" / "README.md").read_text(encoding="utf-8"))
        self.assertEqual(self.text.splitlines()[0], "<!-- SPDX-License-Identifier: GPL-3.0-or-later -->")

    def test_covers_what_the_owner_needs(self):
        flat = " ".join(self.text.split())
        for needle in ("ssh-keygen -t ed25519", "opos-release ssh-ed25519 AAAA...", "OPOS_UPDATE_SIGNERS", "never reuse", "latest.json", "GitHub",
                       "tools/opos-release.py build", "tools/opos-release.py verify", "tools/opos-release.py index", "deploy/duo-push-update.ps1",
                       "both keys", "rollback.request", "status.json", "off the repository", "gzip"):
            self.assertIn(needle, flat)
        for heading in ("## Make the release key", "## Build an image that trusts the key", "## Cut a release", "## Host the update", "## Push over USB",
                        "## Rotate the key", "## After a failed update", "## Limits"):
            self.assertIn("\n" + heading, self.text)

    def test_nothing_machine_specific(self):
        self.assertNotRegex(self.text, r"[A-Za-z]:\\Users")
        self.assertNotRegex(self.text, r"/home/[a-z]")
        self.assertNotRegex(self.text, r"(?<![\d.])\d{1,3}(\.\d{1,3}){3}(?![\d.])")
        self.assertNotRegex(self.text, r"-----BEGIN")

    def test_command_lines_use_flags_that_exist(self):
        help_text = {}
        for sub in ("build", "verify", "index", "keygen-help"):
            proc = run(sys.executable, TOOL, sub, "--help")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            help_text[sub] = proc.stdout
        blocks = re.findall(r"```[a-z]*\n(.*?)```", self.text, re.S)
        seen = set()
        for block in blocks:
            for command in re.split(r"\n(?=\S)", block):
                flat = " ".join(command.replace("\\\n", " ").replace("`\n", " ").split())
                m = re.search(r"opos-release\.py\s+(build|verify|index|keygen-help)\b(.*)", flat)
                if m:
                    seen.add(m.group(1))
                    for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", m.group(2)):
                        self.assertIn(flag, help_text[m.group(1)], flat)
        self.assertEqual(seen, {"build", "verify", "index", "keygen-help"})
        script = PUSH.read_text()
        pushes = [line for block in blocks for line in re.findall(r"duo-push-update\.ps1[^\n]*", block)]
        self.assertTrue(pushes)
        for line in pushes:
            for name in re.findall(r"(?<![\w-])-([A-Z][A-Za-z]+)", line):
                self.assertIn("$" + {"Host": "PhoneHost"}.get(name, name), script, line)


if __name__ == "__main__":
    unittest.main()
