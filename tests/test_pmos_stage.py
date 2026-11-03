# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import subprocess
import tempfile
import unittest


class TestStage(unittest.TestCase):
    def test_stages_both_package_contents(self):
        script = pathlib.Path(__file__).resolve().parent.parent / "packaging/pmos/stage-aports.sh"
        text = script.read_text()
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            staged = base / "repo/packaging/pmos/stage-aports.sh"
            staged.parent.mkdir(parents=True)
            staged.write_text(text)
            for pkg in ("opos", "postmarketos-ui-opos"):
                pkgdir = staged.parent / pkg
                pkgdir.mkdir()
                (pkgdir / "APKBUILD").write_text("fixture")
                (pkgdir / ".marker").write_text("hidden")
            repo_root = staged.parent.parent.parent
            deploy_dir = repo_root / "deploy"
            deploy_dir.mkdir(parents=True)
            (deploy_dir / "50-opos-modemmanager.rules").write_text("rule")
            (deploy_dir / "opos-sysusers.conf").write_text("users")
            out = base / "out"
            subprocess.run(["sh", str(staged), str(out)], cwd=tmp, check=True)
            found = {}
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    found[path.relative_to(out).as_posix()] = path.read_text()
            self.assertEqual(found, {"main/opos/APKBUILD": "fixture", "main/opos/.marker": "hidden", "main/opos/opos-sysusers.conf": "users", "main/postmarketos-ui-opos/APKBUILD": "fixture", "main/postmarketos-ui-opos/.marker": "hidden"})
