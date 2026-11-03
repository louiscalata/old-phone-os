# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenRC setup state contract and fail-closed startup control flow."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from opos.setup import config

INITD = Path(__file__).resolve().parents[1] / "packaging/pmos/opos/opos-server.initd"


class StateDirectoryTests(unittest.TestCase):
    def run_hook(self, fail_at=0):
        script = r'''
. "$1"
printf 'HOME_VALUE:%s\nCONFIG_VALUE:%s\n' "$HOME" "$OPOS_CONFIG_DIR"
# Contract exports above are read-only. Exercise hooks only under disposable paths.
HOME="$3/state"
OPOS_CONFIG_DIR="$HOME/.config/opos"
output_log="$3/service.log"
n=0
# Keep the failure index separate from the checkpath arguments.
fail_at=$2
checkpath() {
    n=$((n + 1))
    printf 'CALL:%s\n' "$*"
    [ "$n" -ne "$fail_at" ]
}
start_pre
result=$?
printf 'RESULT:%s\n' "$result"
'''
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["sh", "-c", script, "state-test", str(INITD), str(fail_at), temporary],
                capture_output=True, text=True, check=True)
            return result.stdout.splitlines(), Path(temporary)

    def test_service_and_default_wizard_resolve_same_config(self):
        lines, _ = self.run_hook()
        values = dict(line.split(":", 1) for line in lines if line.startswith(("HOME_VALUE:", "CONFIG_VALUE:")))
        self.assertEqual(values["HOME_VALUE"], "/var/lib/opos")
        self.assertEqual(values["CONFIG_VALUE"], "/var/lib/opos/.config/opos")
        self.assertEqual(config.config_dir({"HOME": values["HOME_VALUE"]}, "linux"),
                         config.config_dir({"HOME": values["HOME_VALUE"],
                                            "OPOS_CONFIG_DIR": values["CONFIG_VALUE"]}, "linux"))

    def test_each_failed_path_check_stops_before_later_changes(self):
        for failed in range(1, 5):
            with self.subTest(failed_check=failed):
                lines, _ = self.run_hook(failed)
                self.assertEqual(sum(line.startswith("CALL:") for line in lines), failed)
                self.assertIn("RESULT:1", lines)

    def test_success_keeps_parent_root_and_only_config_leaf_service_owned(self):
        lines, temporary = self.run_hook()
        state_home = temporary / "state"
        calls = [line.removeprefix("CALL:") for line in lines if line.startswith("CALL:")]
        self.assertEqual(calls, [
            f"-d -o root:root -m 0755 {state_home}",
            f"-d -o root:root -m 0755 {state_home / '.config'}",
            f"-d -o opos:opos -m 0750 {state_home / '.config/opos'}",
            f"-f -o opos:opos -m 0600 {temporary / 'service.log'}",
        ])
        self.assertIn("RESULT:0", lines)

    def test_invalid_state_path_is_rejected_before_checkpath(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            regular = root / "regular"
            regular.write_text("preserve")
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            dangling = root / "dangling"
            dangling.symlink_to(root / "absent")
            fifo = root / "fifo"
            os.mkfifo(fifo)
            for path in (regular, link, dangling, fifo):
                with self.subTest(path=path.name):
                    script = r'''
. "$1"
eerror() { printf 'REJECTED\n'; }
checkpath() { printf 'UNEXPECTED_CHECKPATH\n'; }
_opos_state_directory "$2" root:root 0755
'''
                    result = subprocess.run(["sh", "-c", script, "state-test", str(INITD), str(path)],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("REJECTED", result.stdout)
                    self.assertNotIn("UNEXPECTED_CHECKPATH", result.stdout)
            self.assertEqual(regular.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
