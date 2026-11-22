# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo image's service graph, read from the unit files and build-image.sh, and the boot page checked against it.

packaging/duo-os/bootgraph.py turns the units OPOS ships into data: ordering, enablement, entry points and a failure
class per unit. These tests check that data, and each mutation below is applied to a copy of the tree and must be
caught. None of this runs systemd: whether systemd on the phone orders the units this way stays NOT_RUN
(systemd-analyze verify and critical-chain on the booted image, PC session)."""

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging" / "duo-os"
DOC = ROOT / "docs" / "os-architecture.md"

_spec = importlib.util.spec_from_file_location("opos_bootgraph", PKG / "bootgraph.py")
bootgraph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootgraph)

# Orders the boot depends on, written here independently of bootgraph.py: (earlier, later).
REQUIRED_ORDER = (
    ("opos-update-boot.service", "opos-server.service"),
    ("opos-update-boot.service", "opos.service"),
    ("opos-clock.service", "opos-server.service"),
    ("timekeeper.service", "opos-clock.service"),
    ("lxc@android.service", "opos-server.service"),
    ("opos-server.service", "opos.service"),
    ("android-service@hwcomposer.service", "opos.service"),
    ("lxc@android.service", "opos.service"),
    ("opos.service", "opos-powerkey.service"),
    ("opos.service", "opos-volumekey.service"),
    ("opos.service", "opos-osk.service"),
    ("opos-server.service", "opos-update-confirm.service"),
    ("opos-update-boot.service", "opos-update-confirm.service"),
    ("opos-sensorfwd-delayed.timer", "opos-sensorfwd-delayed.service"),
    ("opos-sensorfwd-delayed.timer", "timers.target"),
)

SHIPPED = {
    "opos-brightness.service",
    "opos-clock-set.path",
    "opos-clock-set.service",
    "opos-clock.service",
    "opos-diag.service",
    "opos-fallback.service",
    "opos-osk.service",
    "opos-powerkey.service",
    "opos-server.service",
    "opos-volumekey.service",
    "opos.service",
    "opos-update-boot.service",
    "opos-update-confirm.service",
    "opos-update.path",
    "opos-update.service",
    "opos-sensorfwd-delayed.timer",
    "opos-sensorfwd-delayed.service",
}


class BootGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = bootgraph.load(ROOT)

    def test_every_unit_file_is_read(self):
        self.assertEqual(SHIPPED, set(self.g.units))
        found = {
            p.name for d in (PKG, PKG / "updater") for p in d.iterdir() if p.suffix in (".service", ".path", ".timer")
        }
        self.assertEqual(found, set(self.g.units))

    def test_drop_ins_are_merged_into_the_server(self):
        dropins = {d.dest for d in self.g.units["opos-server.service"].dropins}
        self.assertIn("/etc/systemd/system/opos-server.service.d/20-opos-network.conf", dropins)
        self.assertIn("/etc/systemd/system/opos-server.service.d/20-opos-apps.conf", dropins)
        env = self.g.units["opos-server.service"].get("Service", "Environment")
        self.assertIn("OPOS_NETWORK=1", env)
        self.assertTrue(any(v.startswith("OPOS_APPS=") for v in env))

    def test_build_script_is_read(self):
        self.assertIn(("graphical.target", "opos.service"), {(l.target, l.unit) for l in self.g.links})
        self.assertIn(("opos.service", "opos-powerkey.service"), {(l.target, l.unit) for l in self.g.links})
        self.assertNotIn("phosh.service", {l.unit for l in self.g.links})
        self.assertIn("/usr/libexec/opos-updater/opos-updater", self.g.installs)
        self.assertIn("/usr/sbin/opos-selftest", self.g.installs)
        self.assertGreaterEqual(len(self.g.links), 12)

    def test_no_ordering_cycle(self):
        self.assertEqual([], bootgraph.check_cycles(self.g))

    def test_required_order(self):
        for a, b in REQUIRED_ORDER:
            self.assertTrue(self.g.precedes(a, b), f"{a} is not ordered before {b}")
            self.assertFalse(self.g.precedes(b, a), f"{b} is ordered before {a}")

    def test_every_reference_is_shipped_or_inherited(self):
        self.assertEqual([], bootgraph.check_references(self.g))
        for name in ("lxc@android.service", "android-service@hwcomposer.service", "dbus.socket", "timekeeper.service"):
            self.assertIn(name, bootgraph.INHERITED)
        self.assertFalse(set(bootgraph.INHERITED) & SHIPPED)

    def test_every_unit_has_a_failure_class(self):
        self.assertEqual([], bootgraph.check_failure_classes(self.g))
        self.assertEqual(SHIPPED, set(bootgraph.FAILURE_CLASS))
        self.assertEqual({"fatal-to-session", "degrade"}, {c for c, _ in bootgraph.FAILURE_CLASS.values()})
        self.assertEqual("fatal-to-session", bootgraph.FAILURE_CLASS["opos.service"][0])
        self.assertEqual("fatal-to-session", bootgraph.FAILURE_CLASS["opos-server.service"][0])

    def test_session_fallback_is_wired(self):
        self.assertEqual([], bootgraph.check_fallback(self.g))
        u = self.g.units["opos.service"]
        self.assertIn("opos-fallback.service", u.deps("OnFailure"))
        self.assertIn("75", " ".join(u.get("Service", "RestartPreventExitStatus")).split())

    def test_every_unit_is_installed_and_enabled(self):
        self.assertEqual([], bootgraph.check_installed(self.g))
        self.assertEqual([], bootgraph.check_enabled(self.g))
        self.assertEqual(SHIPPED, self.g.reachable(dev=True) & SHIPPED)
        # A release image (no developer key) leaves exactly the developer's clock drop off.
        self.assertEqual({"opos-clock-set.path", "opos-clock-set.service"}, SHIPPED - self.g.reachable(dev=False))

    def test_every_entry_point_is_installed(self):
        self.assertEqual([], bootgraph.check_entry_points(self.g))
        eps = bootgraph.entry_points(self.g)
        self.assertIn("/usr/libexec/opos/opos-session", eps["opos.service"])
        self.assertIn("opos.web", eps["opos-server.service"])

    def test_doc_matches_the_graph(self):
        self.assertEqual([], bootgraph.check_doc(self.g, ROOT))

    def test_all_checks_pass_and_cli(self):
        self.assertEqual([], bootgraph.all_problems(ROOT))
        for args, needle in (
            (["--check"], "boot graph: OK"),
            (["--print"], "opos-server.service"),
            (["--mermaid"], "flowchart TD"),
            (["--layers"], "flowchart TD"),
        ):
            p = subprocess.run(
                [sys.executable, "-I", "-B", "-W", "error", str(PKG / "bootgraph.py"), *args],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
            self.assertEqual(0, p.returncode, p.stderr)
            self.assertIn(needle, p.stdout)

    def test_docs_index_links_the_page(self):
        self.assertIn("(os-architecture.md)", (ROOT / "docs" / "README.md").read_text(encoding="utf-8"))

    def test_the_sensor_timer_is_read_as_a_trigger_and_enabled_through_timers_target(self):
        timer = self.g.units["opos-sensorfwd-delayed.timer"]
        self.assertEqual(["120"], timer.get("Timer", "OnBootSec"))
        self.assertEqual("opos-sensorfwd-delayed.service", self.g.trigger_unit(timer))
        self.assertIn(("opos-sensorfwd-delayed.timer", "opos-sensorfwd-delayed.service"), self.g.trigger)
        self.assertIn(("timers.target", "opos-sensorfwd-delayed.timer"), {(l.target, l.unit) for l in self.g.links})
        # Both are in a release image too; sensorfwd.service itself is inherited and is linked nowhere.
        rel = self.g.reachable(dev=False)
        self.assertLessEqual({"timers.target", "opos-sensorfwd-delayed.timer", "opos-sensorfwd-delayed.service"}, rel)
        self.assertNotIn("sensorfwd.service", self.g.units)
        self.assertNotIn("sensorfwd.service", {l.unit for l in self.g.links})
        self.assertIn("sensorfwd.service", bootgraph.INHERITED)
        self.assertEqual("degrade", bootgraph.FAILURE_CLASS["opos-sensorfwd-delayed.service"][0])

    def test_a_timer_without_unit_starts_the_service_of_its_own_name(self):
        u = bootgraph.Unit("a.timer", Path("a.timer"), bootgraph.parse_unit_text("[Timer]\nOnBootSec=5\n"))
        self.assertEqual("a.service", bootgraph.Graph.trigger_unit(u))
        u = bootgraph.Unit("a.timer", Path("a.timer"), bootgraph.parse_unit_text("[Timer]\nUnit=b.service\n"))
        self.assertEqual("b.service", bootgraph.Graph.trigger_unit(u))
        # A path unit's Unit= is read from [Path], not [Timer].
        u = bootgraph.Unit("a.path", Path("a.path"), bootgraph.parse_unit_text("[Timer]\nUnit=b.service\n"))
        self.assertEqual("a.service", bootgraph.Graph.trigger_unit(u))

    def test_build_script_rm_removes_a_link_only_outside_an_if(self):
        link = 'ln -sf /etc/systemd/system/a.service "$r/etc/systemd/system/multi-user.target.wants/a.service"\n'
        rm = 'rm -f "$r/etc/systemd/system/multi-user.target.wants/a.service"\n'
        self.assertEqual({}, bootgraph.BuildScript(link + rm).links)
        kept = bootgraph.BuildScript(link + 'if [ -n "${X:-}" ]; then\n' + rm + "fi\n").links
        self.assertEqual([("multi-user.target", "a.service")], list(kept))


class MutationTest(unittest.TestCase):
    """Each change, made to a copy of the tree, must be reported."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="opos-bootgraph-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copytree(PKG, self.tmp / "packaging" / "duo-os")
        shutil.copytree(ROOT / "src" / "opos", self.tmp / "src" / "opos", ignore=shutil.ignore_patterns("__pycache__"))
        (self.tmp / "docs").mkdir()
        shutil.copy2(DOC, self.tmp / "docs" / "os-architecture.md")
        self.assertEqual([], bootgraph.all_problems(self.tmp), "the copy must start clean")

    def edit(self, rel, old, new, count=1):
        p = self.tmp / rel
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text, rel)
        p.write_text(text.replace(old, new, count), encoding="utf-8")

    def problems(self):
        return bootgraph.all_problems(self.tmp)

    def assertCaught(self, needle):
        found = self.problems()
        self.assertTrue(found, "the mutation was not caught")
        self.assertTrue(any(needle in p for p in found), found)

    def test_1_updater_boot_step_loses_its_before(self):
        self.edit("packaging/duo-os/updater/opos-update-boot.service", "Before=opos-server.service opos.service\n", "")
        g = bootgraph.load(self.tmp)
        self.assertFalse(g.precedes("opos-update-boot.service", "opos-server.service"))
        self.assertCaught("opos-update-boot.service is not ordered before opos-server.service")
        self.assertCaught("opos-update-boot.service is not ordered before opos.service")

    def test_2_cycle(self):
        self.edit("packaging/duo-os/opos-server.service", "[Service]", "After=opos.service\n\n[Service]")
        self.assertTrue(bootgraph.check_cycles(bootgraph.load(self.tmp)))
        self.assertCaught("ordering cycle: ")
        self.assertTrue(
            any(
                "opos-server.service" in p and "opos.service" in p
                for p in self.problems()
                if p.startswith("ordering cycle: ")
            )
        )

    def test_3_entry_point_not_installed(self):
        self.edit(
            "packaging/duo-os/opos-diag.service",
            "ExecStart=/usr/libexec/opos/opos-diag",
            "ExecStart=/usr/libexec/opos/opos-diag-missing",
        )
        self.assertCaught(
            "opos-diag.service: runs /usr/libexec/opos/opos-diag-missing, which build-image.sh does not install"
        )

    def test_3b_python_module_missing(self):
        self.edit("packaging/duo-os/opos-volumekey.service", "-m opos.volumekey", "-m opos.volumekeys")
        self.assertCaught(
            "opos-volumekey.service: runs python3 -m opos.volumekeys, and src/opos/volumekeys.py does not exist"
        )

    def test_3c_entry_point_not_executable(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            'install -m 0755 "$here/opos-diag" ',
            'install -m 0644 "$here/opos-diag" ',
        )
        self.assertCaught("opos-diag.service: runs /usr/libexec/opos/opos-diag, installed with mode 0644, not 0755")

    def test_4_wants_link_deleted(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            'ln -sf /etc/systemd/system/opos-diag.service "$r/etc/systemd/system/multi-user.target.wants/opos-diag.service"\n',
            "",
        )
        self.assertCaught("opos-diag.service: nothing starts it")
        self.assertCaught(
            "opos-diag.service: [Install] WantedBy=multi-user.target but build-image.sh links no "
            "multi-user.target.wants/opos-diag.service"
        )

    def test_4b_server_link_deleted_is_caught_although_opos_wants_it(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            'ln -sf /etc/systemd/system/opos-server.service "$r/etc/systemd/system/multi-user.target.wants/opos-server.service"\n',
            "",
        )
        self.assertCaught(
            "opos-server.service: [Install] WantedBy=multi-user.target but build-image.sh links no "
            "multi-user.target.wants/opos-server.service"
        )

    def test_4c_looped_link_deleted(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            "for u in opos-update.path opos-update-boot.service opos-update-confirm.service; do\n    ln -sf",
            "for u in opos-update.path opos-update-confirm.service; do\n    ln -sf",
        )
        self.assertCaught("opos-update-boot.service: [Install] WantedBy=")

    def test_4d_unit_installed_under_an_undeclared_build_input(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            'install -m 0644 "$here/opos-diag.service" "$r/etc/systemd/system/opos-diag.service"\n'
            'ln -sf /etc/systemd/system/opos-diag.service "$r/etc/systemd/system/multi-user.target.wants/opos-diag.service"\n',
            'if [ "${OPOS_SOMETHING:-0}" = 1 ]; then\n'
            '    install -m 0644 "$here/opos-diag.service" "$r/etc/systemd/system/opos-diag.service"\n'
            '    ln -sf /etc/systemd/system/opos-diag.service "$r/etc/systemd/system/multi-user.target.wants/opos-diag.service"\n'
            "fi\n",
        )
        self.assertCaught("opos-diag.service: installed only when ")
        self.assertCaught("OPOS_SOMETHING")

    def test_4e_unit_file_not_installed(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            'install -m 0644 "$here/opos-diag.service" "$r/etc/systemd/system/opos-diag.service"\n',
            "",
        )
        self.assertCaught("opos-diag.service: build-image.sh does not install it into /etc/systemd/system/")

    def test_4f_dev_link_moved_out_of_its_build_input(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            'if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then\n    ln -sf /etc/systemd/system/opos-clock-set.path',
            "ln -sf /etc/systemd/system/opos-clock-set.path",
        )
        self.edit(
            "packaging/duo-os/build-image.sh",
            '/multi-user.target.wants/opos-clock-set.path"\n    echo "DEV IMAGE',
            '/multi-user.target.wants/opos-clock-set.path"\nif [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then\n    echo "DEV IMAGE',
        )
        self.assertCaught("opos-clock-set.path: its link is not under OPOS_DEV_SSH_PUBKEY")
        self.assertCaught(
            "units enabled only under a build condition: [], BUILD_CONDITIONAL says ['opos-clock-set.path']"
        )

    def test_4g_unit_enabled_only_under_a_build_input_not_declared(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            'ln -sf /etc/systemd/system/opos-diag.service "$r/etc/systemd/system/multi-user.target.wants/opos-diag.service"\n',
            'if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then\n'
            '    ln -sf /etc/systemd/system/opos-diag.service "$r/etc/systemd/system/multi-user.target.wants/opos-diag.service"\n'
            "fi\n",
        )
        self.assertCaught(
            "units enabled only under a build condition: ['opos-clock-set.path', 'opos-diag.service'], "
            "BUILD_CONDITIONAL says ['opos-clock-set.path']"
        )
        self.assertCaught(
            "units missing from an image without build inputs, and not in BUILD_CONDITIONAL: ['opos-diag.service']"
        )

    def test_5_unit_without_failure_class(self):
        (self.tmp / "packaging/duo-os/opos-extra.service").write_text(
            "[Unit]\nDescription=x\n[Service]\nType=oneshot\nExecStart=/usr/libexec/opos/opos-diag\n", encoding="utf-8"
        )
        self.assertCaught("opos-extra.service: no entry in FAILURE_CLASS")

    def test_6_unknown_reference(self):
        self.edit(
            "packaging/duo-os/opos-server.service",
            "Wants=lxc@android.service",
            "Wants=lxc@android.service nowhere.service",
        )
        self.assertCaught(
            "opos-server.service: nowhere.service is neither shipped in packaging/duo-os nor listed in INHERITED"
        )

    def test_6b_inherited_entry_no_longer_referenced(self):
        self.edit(
            "packaging/duo-os/opos.service", "After=getty@tty1.service rc-local.service ", "After=getty@tty1.service "
        )
        self.assertCaught("INHERITED lists rc-local.service, which nothing references any more")

    def test_7_mermaid_arrow_changed(self):
        text = (self.tmp / "docs/os-architecture.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```mermaid\n(.*?)```", text, re.S)
        self.assertEqual(2, len(blocks))
        for block in blocks:
            line = next(l for l in block.splitlines() if " --> " in l)
            a, b = line.split(" --> ", 1)
            flipped = block.replace(line + "\n", f"{b.strip()} --> {a.strip()}\n", 1)
            mutated = text.replace(block, flipped, 1)
            self.assertNotEqual(text, mutated)
            (self.tmp / "docs/os-architecture.md").write_text(mutated, encoding="utf-8")
            flag = "--layers" if block == bootgraph.layers_mermaid() else "--mermaid"
            self.assertCaught(f"no mermaid block equals bootgraph.py {flag}")
            self.assertCaught("a mermaid block equals neither bootgraph.py --mermaid nor --layers")
            (self.tmp / "docs/os-architecture.md").write_text(text, encoding="utf-8")

    def test_8_timer_names_another_unit(self):
        self.edit(
            "packaging/duo-os/opos-sensorfwd-delayed.timer",
            "Unit=opos-sensorfwd-delayed.service",
            "Unit=opos-sensorfwd-later.service",
        )
        self.assertCaught(
            "opos-sensorfwd-delayed.timer: opos-sensorfwd-later.service is neither shipped in "
            "packaging/duo-os nor listed in INHERITED"
        )
        self.assertCaught("opos-sensorfwd-delayed.service: nothing starts it")

    def test_8b_timer_link_deleted(self):
        self.edit(
            "packaging/duo-os/build-image.sh",
            "ln -sfn /etc/systemd/system/opos-sensorfwd-delayed.timer "
            '"$r/etc/systemd/system/timers.target.wants/opos-sensorfwd-delayed.timer"\n',
            "",
        )
        self.assertCaught(
            "opos-sensorfwd-delayed.timer: [Install] WantedBy=timers.target but build-image.sh links no "
            "timers.target.wants/opos-sensorfwd-delayed.timer"
        )
        self.assertCaught("opos-sensorfwd-delayed.timer: nothing starts it")
        self.assertCaught("opos-sensorfwd-delayed.service: nothing starts it")

    def test_8c_timer_file_missing(self):
        (self.tmp / "packaging/duo-os/opos-sensorfwd-delayed.timer").unlink()
        self.assertCaught(
            "build-image.sh installs /etc/systemd/system/opos-sensorfwd-delayed.timer from "
            "opos-sensorfwd-delayed.timer, which does not exist"
        )
        self.assertCaught(
            "build-image.sh links timers.target.wants/opos-sensorfwd-delayed.timer, which OPOS does not ship"
        )
        self.assertCaught("opos-sensorfwd-delayed.service: nothing starts it")

    def test_8d_timer_install_section_names_another_target(self):
        self.edit(
            "packaging/duo-os/opos-sensorfwd-delayed.timer", "WantedBy=timers.target", "WantedBy=multi-user.target"
        )
        self.assertCaught(
            "build-image.sh links timers.target.wants/opos-sensorfwd-delayed.timer, but its [Install] "
            "does not name timers.target"
        )

    def test_8e_doc_row_for_the_timer_deleted(self):
        text = (self.tmp / "docs/os-architecture.md").read_text(encoding="utf-8")
        row = next(
            l for l in text.splitlines() if l.startswith("| 120 s after boot | `opos-sensorfwd-delayed.timer` |")
        )
        self.edit("docs/os-architecture.md", row + "\n", "")
        self.assertCaught("boot table has no row for opos-sensorfwd-delayed.timer")

    def test_fallback_unwired(self):
        self.edit("packaging/duo-os/opos.service", "OnFailure=opos-fallback.service\n", "")
        self.assertCaught("opos.service: OnFailure=opos-fallback.service is missing")

    def test_exit_75_no_longer_prevents_restart(self):
        self.edit("packaging/duo-os/opos.service", "RestartPreventExitStatus=75", "RestartPreventExitStatus=")
        self.assertCaught("opos.service: RestartPreventExitStatus= does not hold 75")

    def test_session_no_longer_exits_75(self):
        self.edit("packaging/duo-os/opos-session", "    exit 75\n", "    exit 1\n")
        self.assertCaught("opos-session: no 'exit 75' for the kiosk give-up")

    def test_fallback_no_longer_starts_phosh(self):
        self.edit("packaging/duo-os/opos-fallback.service", "systemctl start phosh.service'", "true'")
        self.assertCaught("opos-fallback.service: does not start phosh.service")

    def test_fatal_unit_pulls_in_a_degrade_unit(self):
        self.edit(
            "packaging/duo-os/opos.service",
            "Wants=dbus.socket opos-server.service",
            "Wants=dbus.socket opos-server.service\nRequires=opos-diag.service",
        )
        self.assertCaught("opos.service (fatal-to-session) has Requires=opos-diag.service, a degrade unit")

    def test_doc_class_column_wrong(self):
        self.edit(
            "docs/os-architecture.md", "| `opos-diag.service` | degrade |", "| `opos-diag.service` | fatal-to-session |"
        )
        self.assertCaught("boot table says opos-diag.service is fatal-to-session, FAILURE_CLASS says degrade")

    def test_doc_waits_for_pair_wrong(self):
        self.edit(
            "docs/os-architecture.md",
            "| `opos-brightness.service` | degrade | `local-fs.target` |",
            "| `opos-brightness.service` | degrade | `opos.service` |",
        )
        self.assertCaught(
            "boot table says opos-brightness.service waits for opos.service; the unit files do not order them"
        )

    def test_doc_row_deleted(self):
        text = (self.tmp / "docs/os-architecture.md").read_text(encoding="utf-8")
        row = next(l for l in text.splitlines() if l.startswith("| 13 | `opos-diag.service` |"))
        self.edit("docs/os-architecture.md", row + "\n", "")
        self.assertCaught("boot table has no row for opos-diag.service")

    def test_doc_print_block_stale(self):
        self.edit(
            "packaging/duo-os/opos-diag.service", "After=multi-user.target", "After=multi-user.target opos.service"
        )
        self.assertCaught("no text block equals bootgraph.py --print")


if __name__ == "__main__":
    unittest.main()
