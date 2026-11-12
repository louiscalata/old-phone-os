# SPDX-License-Identifier: GPL-3.0-or-later
"""Image identity against scratch roots and extracted shell blocks; no mount, root, phone or sockets."""
import copy
import hashlib
import io
import json
import os
import re
import runpy
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from opos import image_identity as identity, notices, update_page
from opos.version import project_version
from tests.duo_identity_fixture import add_identity, REVISION

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging/duo-os"


def block(name, label):
    text = (DUO / name).read_text()
    found = re.search(r"^# --- " + re.escape(label) + r": BEGIN[^\n]*\n(.*?)^# --- " + re.escape(label) + r": END", text, re.M | re.S)
    assert found, label
    return found.group(1)


class BuildIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.base = self.t / "base.img"
        self.base.write_bytes(b"simulated base image, not ext4\n")
        self.root = self.t / "root"
        self.state = self.t / "state"
        self.original = b'NAME="Droidian next"\nID=droidian\nID_LIKE="debian gnu debian"\nVERSION="next"\n'
        self.put("usr/lib/os-release", self.original)
        self.put("etc/passwd", b"droidian:x:32011:32011::/srv/droidian:/bin/sh\n")
        (self.root / "etc/os-release").symlink_to("/usr/lib/os-release")
        self.put("usr/local/sbin/sfduo-composer-watchdog", b"#!/bin/sh\ntrue\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_") and k != "SOURCE_DATE_EPOCH"}
        self.env["SOURCE_DATE_EPOCH"] = "1794093790"

    def put(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def shell(self, script, env=None):
        assignments = {"r": self.root, "src": ROOT, "base": self.base, "identity_state": self.state, "ref": "fixture"}
        prelude = "set -eu\n" + "\n".join(k + "=" + shlex.quote(str(v)) for k, v in assignments.items()) + "\n"
        prelude += ('g() { case "$1" in show) cat "$src/pyproject.toml" ;; rev-parse) echo ' + REVISION +
                    ' ;; log) [ "$*" = "log -1 --no-show-signature --format=%ct fixture" ] || exit 9; echo 1794000000 ;; *) exit 9 ;; esac; }\n')
        return subprocess.run(["sh", "-c", prelude + script], env=self.env if env is None else env,
                              capture_output=True, text=True, timeout=30)

    def prepare(self):
        p = self.shell(block("build-image.sh", "identity epoch") + block("build-image.sh", "identity prepare"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def installs(self):
        self.put("opt/opos-releases/0-" + REVISION[:12] + "/REVISION", (REVISION + "\n").encode())
        self.put("opt/opos-releases/0-" + REVISION[:12] + "/src/opos/example.py", b"# simulated runtime\n")
        (self.root / "opt/opos").symlink_to("opos-releases/0-" + REVISION[:12])
        self.put("etc/systemd/system/opos-server.service", (DUO / "opos-server.service").read_bytes())
        for name in ("opos-network.conf", "opos-apps.conf"):
            self.put("etc/systemd/system/opos-server.service.d/20-" + name, (DUO / name).read_bytes())
        self.put("etc/systemd/system/multi-user.target.wants/opos-server.service", b"")
        p = self.root / "etc/systemd/system/multi-user.target.wants/opos-server.service"
        p.unlink()
        p.symlink_to("/etc/systemd/system/opos-server.service")
        self.put("etc/opos/update/allowed_signers", b"")

    def finish(self):
        p = self.shell(block("build-image.sh", "identity finish"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return (self.root / "etc/opos/image.json").read_bytes()

    def build(self):
        self.prepare()
        self.installs()
        return self.finish()

    def test_preserves_symlinked_base_without_following_it_outside_image(self):
        self.build()
        self.assertEqual((self.root / "usr/lib/os-release").read_bytes(), self.original)
        self.assertEqual((self.root / "usr/lib/os-release.base").read_bytes(), self.original)
        self.assertFalse((self.root / "etc/os-release").is_symlink())
        got = identity.os_release((self.root / "etc/os-release").read_text())
        self.assertEqual(got["NAME"], "Old Phone OS")
        self.assertEqual(got["ID"], "opos")
        self.assertEqual(got["ID_LIKE"], "droidian debian gnu")
        self.assertEqual((got["VERSION"], got["VERSION_ID"]), (project_version(), project_version()))
        self.assertEqual(got["PRETTY_NAME"], "Old Phone OS " + project_version())
        self.assertEqual(got["BUILD_ID"], REVISION[:12])
        meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(got["HOME_URL"], meta["urls"]["Source"])
        self.assertEqual((self.root / "etc/hostname").read_text(), "opos\n")
        self.assertIn("Old Phone OS " + project_version(), (self.root / "etc/issue").read_text())

    def test_exact_inputs_give_identical_manifest_bytes_on_repeated_finish(self):
        first = self.build()
        os.utime(self.root / "etc/issue", (1, 1))
        self.assertEqual(first, self.finish())
        self.assertEqual(first, identity.canonical(json.loads(first)))
        self.assertNotIn(str(self.t).encode(), first)

    def test_fresh_roots_with_identical_inputs_have_identical_manifests(self):
        first = self.build()
        import shutil
        shutil.rmtree(self.root)
        self.put("usr/lib/os-release", self.original)
        self.put("etc/passwd", b"droidian:x:32011:32011::/srv/droidian:/bin/sh\n")
        (self.root / "etc/os-release").symlink_to("/usr/lib/os-release")
        self.put("usr/local/sbin/sfduo-composer-watchdog", b"#!/bin/sh\ntrue\n")
        self.assertEqual(first, self.build())

    def test_base_hash_time_and_effective_switches_are_recorded(self):
        m = json.loads(self.build())
        self.assertEqual(m["base"]["sha256"], hashlib.sha256(self.base.read_bytes()).hexdigest())
        self.assertEqual(m["base"]["os_release"], identity.os_release(self.original.decode()))
        self.assertEqual(m["source_date_epoch"], 1794093790)
        self.assertEqual(m["revision"], REVISION)
        for key in ("OPOS_OSK", "OPOS_NETWORK", "OPOS_FIRSTRUN", "OPOS_MONITOR", "OPOS_PANES"):
            self.assertEqual(m["switches"][key], "1")
        self.assertEqual(m["switches"]["OPOS_SPAN"], "675,42")
        self.assertEqual(m["switches"]["OPOS_APPS"], "clock,contacts,notes,calc,calls")
        self.assertEqual(m["switches"]["OPOS_DEV_SSH_PUBKEY"], "0")
        self.assertEqual(m["switches"]["OPOS_UPDATE_URL"], "")

    def test_omitted_epoch_build_uses_selected_ref_commit_time(self):
        self.env.pop("SOURCE_DATE_EPOCH")
        m = json.loads(self.build())
        self.assertEqual(m["source_date_epoch"], 1794000000)
        self.assertEqual(identity.verify(self.root), m)

    def test_invalid_explicit_epoch_and_no_shell_execution_from_base(self):
        script = block("build-image.sh", "identity epoch") + block("build-image.sh", "identity prepare")
        for value in ("", "-1", "tomorrow", "1.2", "253402300800"):
            p = self.shell(script, dict(self.env, SOURCE_DATE_EPOCH=value))
            self.assertNotEqual(p.returncode, 0, value)
            self.assertIn("SOURCE_DATE_EPOCH", p.stderr)
        marker = self.t / "not-executed"
        self.original += ('HOME_URL="$(touch %s)"\n' % marker).encode()
        self.put("usr/lib/os-release", self.original)
        self.build()
        self.assertFalse(marker.exists())

    def test_absolute_dropin_link_reads_image_target_instead_of_builder(self):
        self.prepare()
        self.installs()
        host = self.t / "builder.conf"
        host.write_text("Environment=OPOS_TEST=builder\n", encoding="utf-8")
        self.put(str(host).lstrip("/"), b"Environment=OPOS_TEST=image\n")
        link = self.root / "etc/systemd/system/opos-server.service.d/90-test.conf"
        link.symlink_to(host)
        m = json.loads(self.finish())
        self.assertEqual(m["switches"]["OPOS_TEST"], "image")
        self.assertEqual(host.read_text(encoding="utf-8"), "Environment=OPOS_TEST=builder\n")

    def test_absolute_dropin_directory_link_is_resolved_in_image(self):
        self.prepare()
        self.installs()
        import shutil
        directory = self.root / "etc/systemd/system/opos-server.service.d"
        shutil.rmtree(directory)
        directory.symlink_to("/etc/opos/dropins")
        self.put("etc/opos/dropins/90-test.conf", b"Environment=OPOS_TEST=image-only\n")
        self.assertEqual(json.loads(self.finish())["switches"]["OPOS_TEST"], "image-only")

    def test_utf8_inputs_have_identical_manifests_in_non_utf8_locale(self):
        self.put("etc/passwd", "droidian:x:32011:32011:Simul\u00e9:/srv/droidian:/bin/sh\n".encode("utf-8"))
        self.prepare()
        self.installs()
        self.put("etc/systemd/system/opos-server.service.d/90-test.conf", 'Environment="OPOS_TEST=caf\u00e9"\n'.encode("utf-8"))
        self.put("etc/default/opos-osk", '# caf\u00e9\nOPOS_OSK="caf\u00e9"\n'.encode("utf-8"))
        self.put("etc/opos/usb-shell.conf", '# caf\u00e9\nenabled=1\n'.encode("utf-8"))
        expected = self.finish()
        env = dict(self.env, LC_ALL="C", LANG="C")
        p = subprocess.run([sys.executable, "-I", "-X", "utf8=0", "-c",
                            "import locale; print(locale.getencoding())"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("utf", p.stdout.lower(), "test requires a non-UTF-8 locale")
        # -I ignores PYTHONUTF8; explicitly disable UTF-8 mode in both phases.
        for label in ("identity prepare", "identity finish"):
            script = block("build-image.sh", label).replace("python3 -I -B", "python3 -I -B -X utf8=0")
            p = self.shell(script, env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.root / "etc/opos/image.json").read_bytes(), expected)
        self.assertEqual(json.loads(expected)["switches"]["OPOS_TEST"], "caf\u00e9")

    def test_covers_runtime_config_helpers_symlinks_and_changed_base_files(self):
        self.prepare()
        self.installs()
        self.put("etc/inetd.conf", b"# opos-closed simulated telnet\n")
        m = json.loads(self.finish())
        files = {f["path"]: f for f in m["files"]}
        self.assertTrue(identity.REQUIRED <= files.keys())
        self.assertIn("etc/inetd.conf", files)
        self.assertIn("opt/opos-releases/0-" + REVISION[:12] + "/src/opos/example.py", files)
        self.assertEqual(files["opt/opos"]["type"], "symlink")
        self.assertNotIn("etc/opos/image.json", files)
        self.assertNotIn("etc/passwd", files)
        self.assertEqual(len(m["files"]), len(files))
        identity.verify(self.root)

    def test_optional_switches_and_config_are_baked_without_builder_paths(self):
        self.env.update(OPOS_DEV_SSH_PUBKEY="/not-recorded/login.pub", OPOS_DEV_USB_SHELL="1", OPOS_NET_POLKIT="1")
        self.prepare()
        self.installs()
        self.put("etc/default/opos-kiosk", b"OPOS_KIOSK_DEBUG_PORT=9222\n")
        self.put("etc/default/opos-osk", b"OPOS_OSK=stevia\n")
        self.put("etc/polkit-1/rules.d/50-opos-network.rules", b"// simulated rule\n")
        self.put("etc/opos/usb-shell.conf", b"enabled=1\ngate=dev\n")
        self.put("etc/opos/update.json", b'{"url":"https://updates.example.org/latest.json"}\n')
        self.put("etc/opos/update/allowed_signers", b"simulated public trust file\n")
        raw = self.finish()
        s = json.loads(raw)["switches"]
        for key in ("OPOS_DEV_SSH_PUBKEY", "OPOS_DEV_USB_SHELL", "OPOS_NET_POLKIT", "OPOS_UPDATE_SIGNERS"):
            self.assertEqual(s[key], "1")
        self.assertEqual(s["OPOS_KIOSK_DEBUG_PORT"], "9222")
        self.assertEqual(s["OPOS_OSK"], "stevia")
        self.assertEqual(s["OPOS_UPDATE_URL"], "https://updates.example.org/latest.json")
        self.assertNotIn(b"/not-recorded", raw)

    def test_plain_base_and_missing_id_like(self):
        (self.root / "etc/os-release").unlink()
        self.original = b'NAME="Example Base"\nID=example\n'
        self.put("etc/os-release", self.original)
        self.build()
        fields = identity.os_release((self.root / "etc/os-release").read_text())
        self.assertEqual(fields["ID_LIKE"], "example")

    def test_rebuild_preserves_upstream_identity_and_hashes_identical_installs(self):
        first = json.loads(self.build())
        self.prepare()
        second = json.loads(self.finish())
        self.assertEqual(second["base"], first["base"])
        self.assertEqual((self.root / "usr/lib/os-release.base").read_bytes(), self.original)
        self.assertEqual(second["files"], first["files"])

    def test_verify_shell_block_accepts_build_then_rejects_corruption(self):
        self.build()
        prelude = 'identity_checker=' + shlex.quote(str(ROOT / "src/opos/image_identity.py")) + '\nfail=0\ncheck() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n'
        script = prelude + block("verify-image.sh", "identity") + "\nexit $fail\n"
        self.assertEqual(self.shell(script).returncode, 0)
        self.put("etc/issue", b"damaged\n")
        p = self.shell(script)
        self.assertEqual(p.returncode, 1)
        self.assertIn("FAIL", p.stdout)


class ReadersAndChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.m = add_identity(self.root)

    def save(self, m):
        (self.root / "etc/opos/image.json").write_bytes(identity.canonical(m))

    def test_manifest_rejects_missing_file_wrong_hash_and_symlink_substitution(self):
        for variant in ("missing", "hash", "link"):
            with self.subTest(variant):
                add_identity(self.root)
                p = self.root / "etc/issue"
                if variant == "missing":
                    p.unlink()
                elif variant == "hash":
                    p.write_bytes(b"changed")
                else:
                    p.unlink()
                    p.symlink_to("hostname")
                with self.assertRaises((OSError, ValueError)):
                    identity.verify(self.root)

    def test_rejects_incomplete_manifest_bad_identity_missing_base_and_path_tricks(self):
        for mutate in (lambda m: m.update(files=[]), lambda m: m["files"].append(m["files"][0]),
                       lambda m: m["files"][0].update(path="../escape"),
                       lambda m: m["files"][0].update(path="/etc/issue"),
                       lambda m: m.update(revision="bad"), lambda m: m.update(source_date_epoch=True)):
            m = copy.deepcopy(self.m)
            mutate(m)
            self.save(m)
            with self.assertRaises(ValueError):
                identity.verify(self.root)
        self.save(self.m)
        (self.root / "etc/os-release").write_text("ID=droidian\n")
        with self.assertRaises(ValueError):
            identity.verify(self.root)
        add_identity(self.root)
        (self.root / "usr/lib/os-release.base").unlink()
        with self.assertRaises(OSError):
            identity.verify(self.root)

    def test_rejects_duplicate_json_keys(self):
        raw = identity.canonical(self.m).replace(b'"format": 1', b'"format": 1, "format": 1')
        with self.assertRaises(ValueError):
            identity.parse_manifest(raw)

    def test_opos_version_cli_and_about_agree_and_escape_fields(self):
        with mock.patch("platform.release", return_value="fixture-kernel"):
            facts = identity.facts(self.root)
            html = update_page.about(self.root)
        self.assertIn("Old Phone OS " + project_version() + ", based on Droidian", html)
        self.assertIn(REVISION, html)
        self.assertIn("fixture-kernel", html)
        self.assertEqual(facts["version"], project_version())
        p = subprocess.run([sys.executable, "-I", "-B", str(self.root / "usr/libexec/opos/image_identity.py"), "version", "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Old Phone OS " + project_version() + ", based on Droidian", p.stdout)
        self.assertIn(REVISION, p.stdout)
        self.assertIn("Kernel:", p.stdout)
        with mock.patch.object(identity, "facts", return_value=dict(facts, base="<base>&", kernel='"kernel"')):
            html = update_page.about(self.root)
        self.assertIn("&lt;base&gt;&amp;", html)
        self.assertNotIn("<base>", html)

    def test_about_and_version_do_not_mislabel_the_desktop_host(self):
        (self.root / "etc/opos/image.json").unlink()
        self.assertIsNone(identity.facts(self.root))
        self.assertIn("unavailable", update_page.about(self.root))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(identity.main(["version", "--root", str(self.root)]), 1)

    def test_selftest_identity_only_passes_and_fails_without_probes_or_writes(self):
        for damaged in (False, True):
            if damaged:
                (self.root / "etc/issue").write_text("changed")
            before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
            p = subprocess.run([sys.executable, "-I", "-B", str(DUO / "opos-selftest"), "--root", str(self.root),
                                "--only", "identity", "--bin", str(self.root / "no-commands")], capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, int(damaged), p.stdout + p.stderr)
            self.assertIn(("FAIL" if damaged else "PASS") + "  identity.image", p.stdout)
            self.assertNotIn("server.http", p.stdout)
            self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")))

    def test_one_runtime_version_source_and_identity_does_not_decorate_home(self):
        self.assertEqual(notices.VERSION, project_version())
        from opos import web
        source = Path(web.__file__).read_text()
        self.assertNotIn("image_identity", source)
        self.assertNotIn("u-h-about", web.page())

    def test_ota_tree_reports_current_application_version_with_or_without_image_identity(self):
        from importlib import metadata
        from opos.setup import cli
        release = self.root / "opt/opos-releases/1-test"
        module = release / "src/opos/version.py"
        module.parent.mkdir(parents=True)
        module.write_bytes((ROOT / "src/opos/version.py").read_bytes())
        (release / "REVISION").write_text(REVISION + "\n", encoding="utf-8")
        manifest = release / "manifest.json"
        manifest.write_text(json.dumps({"version": "0.2.0", "revision": REVISION}), encoding="utf-8")
        self.assertEqual(self.m["version"], "0.1.0")
        for has_identity in (True, False):
            with self.subTest(has_identity=has_identity):
                if not has_identity:
                    (self.root / "etc/opos/image.json").unlink()
                    (self.root / "usr/libexec/opos/image_identity.py").unlink()
                with mock.patch("opos.version.__file__", str(module)), mock.patch(
                        "opos.version.metadata.version", side_effect=metadata.PackageNotFoundError):
                    self.assertEqual(project_version(), "0.2.0")
                    self.assertEqual(runpy.run_path(str(ROOT / "src/opos/notices.py"))["VERSION"], "0.2.0")
                    out = io.StringIO()
                    with redirect_stdout(out), self.assertRaises(SystemExit) as exit_result:
                        cli._build_parser().parse_args(["--version"])
                    self.assertEqual(exit_result.exception.code, 0)
                    self.assertEqual(out.getvalue(), "opos 0.2.0\n")
                    with mock.patch("opos.version.metadata.version", return_value="0.1.0"):
                        self.assertEqual(project_version(), "0.2.0", "current release precedes older installed distribution metadata")
                    manifest.write_text(json.dumps({"version": "0.3.0", "revision": "f" * 40}), encoding="utf-8")
                    self.assertEqual(project_version(), "unknown", "mismatched release metadata must not label this application")
                    manifest.write_text(json.dumps({"version": "0.2.0", "revision": REVISION}), encoding="utf-8")

    def test_permission_denied_is_incomplete_coverage_only_for_unprivileged_opt_in(self):
        private = self.root / "etc/opos/monitor.env"
        private.write_bytes(b"# simulated private configuration\n")
        private.chmod(0o600)
        self.m["files"].append(identity.record(self.root, "etc/opos/monitor.env"))
        self.save(self.m)
        original_hash = identity.hash_file
        def denied(path):
            if path.resolve() == private.resolve():
                raise PermissionError("simulated root-owned mode-0600 file")
            return original_hash(path)
        with mock.patch.object(identity, "hash_file", side_effect=denied), mock.patch.object(os, "geteuid", return_value=1000):
            with self.assertRaises(PermissionError):
                identity.verify(self.root)
            unreadable = []
            self.assertEqual(identity.verify(self.root, unreadable=unreadable), self.m)
            self.assertEqual(unreadable, ["etc/opos/monitor.env"])
            with mock.patch.object(os, "geteuid", return_value=0), self.assertRaises(PermissionError):
                identity.verify(self.root, unreadable=[])
            (self.root / "etc/issue").write_bytes(b"damaged\n")
            with self.assertRaises(ValueError):
                identity.verify(self.root, unreadable=[])
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.geteuid() == 0, "requires root to create a root-owned mode-0600 fixture and drop to an unprivileged UID")
    def test_root_owned_private_file_root_verification_and_unprivileged_selftest(self):
        private = self.root / "etc/opos/monitor.env"
        private.write_bytes(b"# simulated private configuration\n")
        private.chmod(0o600)
        os.chown(private, 0, 0)
        self.m["files"].append(identity.record(self.root, "etc/opos/monitor.env"))
        self.save(self.m)
        self.assertEqual(identity.verify(self.root), self.m)
        self.root.chmod(0o755)
        def demote():
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)
        p = subprocess.run([sys.executable, "-I", "-B", str(DUO / "opos-selftest"), "--root", str(self.root),
                            "--only", "identity"], preexec_fn=demote, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("WARN  identity.image", p.stdout)
        self.assertIn("incomplete coverage", p.stdout)
        self.assertIn("etc/opos/monitor.env", p.stdout)
        self.assertEqual((private.stat().st_uid, private.stat().st_mode & 0o777), (0, 0o600))
        private.write_bytes(b"changed\n")
        with self.assertRaises(ValueError):
            identity.verify(self.root)

    def test_identity_description_keeps_owner_claim_boundary(self):
        text = " ".join((ROOT / "docs/duo-os-identity.md").read_text(encoding="utf-8").split())
        self.assertIn("Old Phone OS is an interface, and a work in progress as a Linux distribution, built on the community Droidian port, "
                      "which supplies the kernel, drivers and system services.", text)


class NoSocketIntegration(unittest.TestCase):
    def test_actual_handler_keeps_home_digests_and_all_switches_under_gate(self):
        from opos import firstrun, web
        from tests.test_update_client import MainPageIsUntouched
        from tests.test_uxp30_budget import SWITCHES, WAVE_GATE
        # Run the existing digest assertion without Env.setUp's unrelated HTTP
        # source. No expected hash is changed and no socket is substituted.
        MainPageIsUntouched("test_recorded_digests").test_recorded_digests()
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_")}
        env.update(SWITCHES, OPOS_APPS="clock,contacts,notes,calc")
        with tempfile.TemporaryDirectory() as cfg, mock.patch.dict(os.environ, dict(env, OPOS_CONFIG_DIR=cfg), clear=True):
            undo = firstrun.install(web)
            try:
                h = object.__new__(web.Handler)
                h.path, h.command, h.port = "/", "GET", 8765
                h.headers = {"Host": "127.0.0.1:8765"}
                h.token, h.span, h.updater = "t" * 43, web.parse_span(SWITCHES["OPOS_SPAN"]), None
                h.wfile = io.BytesIO()
                headers, codes = {}, []
                h.send_response = codes.append
                h.send_header = lambda k, v: headers.update({k: v})
                h.end_headers = lambda: None
                with mock.patch.object(web.secrets, "token_urlsafe", return_value="n" * 22):
                    h._get()
                body = h.wfile.getvalue()
                self.assertEqual(codes, [200])
                self.assertEqual(int(headers["Content-Length"]), len(body))
                self.assertLessEqual(len(body), WAVE_GATE)
                self.assertNotIn(b"u-h-about", body)
                print("No-socket handler, all image switches including apps: %d bytes (gate %d)" % (len(body), WAVE_GATE))
            finally:
                if undo:
                    undo()
