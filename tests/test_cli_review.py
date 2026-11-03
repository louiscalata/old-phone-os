# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for setup CLI device detection handling."""
from __future__ import annotations
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
import opos.setup.cli
class CliDetectionTest(unittest.TestCase):
    def test_defaults_detection_error_is_reported(self):
        out = io.StringIO()
        with mock.patch("opos.setup.config.default_config", return_value={}), mock.patch("opos.setup.config.validate", return_value=[]), mock.patch("opos.setup.device.detect_device", side_effect=RuntimeError("".join(["detector ", "exploded"]))), mock.patch("opos.setup.apply.apply") as do_apply:
            do_apply.return_value.snapshot.return_value = []
            rc = opos.setup.cli.main(["run", "--defaults", "--dry-run"], env={}, home=Path.cwd(), platform="linux", out=out)
        self.assertEqual(rc, 1)
        self.assertIn("detection", out.getvalue().lower())
        self.assertNotIn("set up with the default settings", out.getvalue())
    def test_defaults_unsupported_hardware_still_succeeds(self):
        out = io.StringIO()
        with mock.patch("opos.setup.config.default_config", return_value={}), mock.patch("opos.setup.config.validate", return_value=[]), mock.patch("opos.setup.device.detect_device", return_value=object()), mock.patch("opos.setup.device.to_config_device", return_value={"source": "stub", "id": "unknown"}), mock.patch("opos.setup.apply.apply") as do_apply:
            do_apply.return_value.snapshot.return_value = []
            rc = opos.setup.cli.main(["run", "--defaults", "--dry-run"], env={}, home=Path.cwd(), platform="linux", out=out)
        self.assertEqual(rc, 0)
        self.assertIn("Dry run finished. Nothing was configured or started.", out.getvalue())
class CliStatusMarkerTest(unittest.TestCase):
    def test_status_fifo_marker_is_unverified(self):
        import io as _io
        import os as _os
        import subprocess as _sp
        import sys as _sys
        import tempfile as _tf
        from pathlib import Path as _Path
        from unittest import mock as _mock
        with _tf.TemporaryDirectory() as td:
            td_path = _Path(td)
            cfg_path = td_path / "config.json"
            cfg_path.write_text("{}")
            marker = td_path / "persist-marker.json"
            try:
                _os.mkfifo(marker)
            except Exception:
                marker.write_text("{}")
                with _mock.patch.object(type(marker), "stat") as mstat:
                    import stat as _stat
                    fake_mode = _stat.S_IFIFO | 0o600
                    class _S:
                        st_mode = fake_mode
                        st_size = 0
                    mstat.return_value = _S()
                    out = _io.StringIO()
                    with _mock.patch("opos.setup.config.config_path", return_value=cfg_path), _mock.patch("opos.setup.config.load", return_value={"applied": True}), _mock.patch("opos.setup.apply.http_health", return_value=(False, None)):
                        opos.setup.cli.main(["status"], env={}, home=_Path.cwd(), platform="linux", out=out)
                    self.assertIn("persist: unverified", out.getvalue())
                    return
            lines = [
                "import io",
                "import sys",
                "from pathlib import Path",
                "from unittest import mock",
                "cfg_path = Path(sys.argv[1])",
                "home = Path(sys.argv[2])",
                "import opos.setup.cli",
                "out = io.StringIO()",
                "with mock.patch(\"opos.setup.config.config_path\", return_value=cfg_path), mock.patch(\"opos.setup.config.load\", return_value={\"applied\": True}), mock.patch(\"opos.setup.apply.http_health\", return_value=(False, None)):",
                "    opos.setup.cli.main([\"status\"], env={}, home=home, platform=\"linux\", out=out)",
                "sys.stdout.write(out.getvalue())",
            ]
            proc = _sp.Popen([_sys.executable, "-c", "\n".join(lines), str(cfg_path), str(_Path.cwd())], stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)
            try:
                stdout, _err = proc.communicate(timeout=2.0)
            except _sp.TimeoutExpired:
                proc.kill()
                proc.communicate()
                self.fail("status hung on FIFO marker")
            self.assertEqual(proc.returncode, 0, stdout)
            self.assertIn("persist: unverified", stdout)
    def test_status_marker_swap_race_is_unverified(self):
        import io as _io
        import os as _os
        import stat as _stat
        import tempfile as _tf
        import threading as _th
        from pathlib import Path as _Path
        from unittest import mock as _mock
        with _tf.TemporaryDirectory() as td:
            td_path = _Path(td)
            cfg_path = td_path / "config.json"
            cfg_path.write_text("{}")
            marker = td_path / "persist-marker.json"
            marker.write_text("{}")
            _os.unlink(marker)
            _os.mkfifo(marker)
            _orig_stat = _Path.stat
            def _fake_stat(self, *a, **k):
                if str(self) == str(marker):
                    class _S:
                        st_mode = _stat.S_IFREG | 0o600
                        st_size = 2
                    return _S()
                return _orig_stat(self, *a, **k)
            out = _io.StringIO()
            result = {}
            def _run():
                try:
                    with _mock.patch.object(_Path, "stat", _fake_stat), _mock.patch("opos.setup.config.config_path", return_value=cfg_path), _mock.patch("opos.setup.config.load", return_value={"applied": True}), _mock.patch("opos.setup.apply.http_health", return_value=(False, None)):
                        rc = opos.setup.cli.main(["status"], env={}, home=_Path.cwd(), platform="linux", out=out)
                    result["rc"] = rc
                    result["out"] = out.getvalue()
                except Exception as e:
                    result["exc"] = e
            t = _th.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=3.0)
            hung = t.is_alive()
            try:
                fd = _os.open(marker, _os.O_RDWR | _os.O_NONBLOCK)
                _os.close(fd)
            except Exception:
                pass
            t.join(timeout=1.0)
            self.assertFalse(hung, "status hung on swapped-in FIFO marker")
            self.assertNotIn("exc", result)
            self.assertIn("persist: unverified", result.get("out", out.getvalue()))
            self.assertNotIn("persist: ok", result.get("out", out.getvalue()))
    def test_status_oversized_marker_is_unverified(self):
        import io as _io
        import json as _json
        import tempfile as _tf
        from pathlib import Path as _Path
        from unittest import mock as _mock
        with _tf.TemporaryDirectory() as td:
            td_path = _Path(td)
            cfg_path = td_path / "config.json"
            cfg_path.write_text("{}")
            marker = td_path / "persist-marker.json"
            pad_len = 1200000
            large = "A" * pad_len
            doc = {"last_verify": {"result": "ok"}, "pad": large}
            marker.write_text(_json.dumps(doc))
            self.assertGreater(marker.stat().st_size, 65536)
            out = _io.StringIO()
            with _mock.patch("opos.setup.config.config_path", return_value=cfg_path), _mock.patch("opos.setup.config.load", return_value={"applied": True}), _mock.patch("opos.setup.apply.http_health", return_value=(False, None)):
                rc = opos.setup.cli.main(["status"], env={}, home=_Path.cwd(), platform="linux", out=out)
            self.assertIn("persist: unverified", out.getvalue())
            self.assertNotIn("persist: ok", out.getvalue())
class CliUninstallReviewTest(unittest.TestCase):
    def test_f236_uninstall_reports_partial_actions_before_error_once(self):
        out = io.StringIO()
        exc = opos.setup.cli.ServiceError("service removal failed: denied")
        exc.actions = ["removed unit", "service removal failed: denied"]
        with mock.patch("opos.setup.apply.uninstall", side_effect=exc):
            rc = opos.setup.cli.main(["uninstall"], env={}, home=Path.cwd(), platform="linux", out=out)
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "removed unit\nservice removal failed: denied\n")
if __name__ == "__main__":
    unittest.main()
