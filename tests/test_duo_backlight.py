# SPDX-License-Identifier: GPL-3.0-or-later
"""opos-selftest gfx.backlight on the Duo's vendor kernel.

On the owner's Surface Duo (November 8, 2026, read over ssh) both panels were lit while `actual_brightness` of `panel0-backlight` and `panel1-backlight`
read 0 and their `brightness` read 90 and 200 of 255, `bl_power` 0; the DSI connectors `card0-DSI-1` and `card0-DSI-2` in /sys/class/drm were connected,
enabled and dpms On, and wlr-randr listed HWCOMPOSER-1 as Enabled. The check used actual_brightness, so it warned "power on but brightness 0" about a phone
that was lit. It now trusts the requested level, bl_power and the display pipeline, and still prints every number. The fake phone is test_selftest's."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tests import test_selftest as TS  # noqa: E402

BL = "sys/class/backlight/"
DRM = "sys/class/drm/"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class Backlight(TS.SelftestCase):
    def panel(self, name, requested, actual, power=0, maximum=255):
        for k, v in (("bl_power", power), ("brightness", requested), ("actual_brightness", actual), ("max_brightness", maximum)):
            if v is None:
                self.f.rm(BL + name + "/" + k)
            else:
                self.f.put(BL + name + "/" + k, "%s\n" % v)

    def dsi(self, n, status="connected", enabled="enabled", dpms="On"):
        for k, v in (("status", status), ("enabled", enabled), ("dpms", dpms)):
            if v is None:
                self.f.rm(DRM + "card0-DSI-%d/%s" % (n, k))
            else:
                self.f.put(DRM + "card0-DSI-%d/%s" % (n, k), v + "\n")

    def phone(self):
        """The owner's phone as read over ssh: requested 90 and 200, actual 0 on both, the WLED backlight at full, both DSI connectors on."""
        self.panel("panel0-backlight", 90, 0)
        self.panel("panel1-backlight", 200, 0)
        self.panel("backlight", 4095, 4095, maximum=4095)
        self.dsi(1)
        self.dsi(2)
        self.f.put(DRM + "card0-DP-1/status", "disconnected\n")
        self.f.put(DRM + "card0-DP-1/enabled", "disabled\n")
        self.f.put(DRM + "card0-DP-1/dpms", "On\n")
        self.f.put(DRM + "card0-Virtual-1/status", "connected\n")
        self.f.put(DRM + "card0-Virtual-1/enabled", "disabled\n")
        self.f.put(DRM + "card0-Virtual-1/dpms", "On\n")

    def row(self, *a):
        r = self.run_st(*a)
        return r, r.status("gfx.backlight"), r.ev("gfx.backlight")

    # --- the false warning
    def test_the_owners_phone_actual_zero_requested_above_zero_and_the_outputs_on_does_not_warn(self):
        self.phone()
        r, status, ev = self.row()
        self.assertEqual(status, "PASS", ev)
        self.assertEqual(r.by("WARN"), [], r.text)
        self.assertEqual(r.rc, 0)

    def test_every_number_is_still_reported(self):
        self.phone()
        _, _, ev = self.row()
        self.assertIn("panel0-backlight bl_power=0 brightness=90/255 actual=0", ev)
        self.assertIn("panel1-backlight bl_power=0 brightness=200/255 actual=0", ev)
        self.assertIn("DRM: DSI-1 on, DSI-2 on", ev)
        self.assertIn("(actual 0 not trusted)", ev)
        self.assertNotIn("4095", ev, "the WLED backlight is not a panel")

    def test_with_no_connectors_in_sysfs_the_compositors_enabled_output_is_the_evidence(self):
        self.phone()
        self.f.rm("sys/class/drm")
        r, status, ev = self.row()
        self.assertEqual(status, "PASS", ev)
        self.assertIn("wlr-randr: HWCOMPOSER-1 on", ev)
        self.assertIn("brightness=90/255 actual=0", ev)

    def test_with_nothing_to_ask_a_requested_level_is_not_a_warning(self):
        self.phone()
        self.f.rm("sys/class/drm")
        self.f.wlr(rc=1)  # the compositor cannot be queried: gfx.output says so, and the backlight has no second opinion
        r, status, ev = self.row()
        self.assertEqual(r.status("gfx.output"), "WARN")
        self.assertEqual(status, "PASS", ev)
        self.assertNotIn("wlr-randr:", ev)

    # --- the true warnings
    def test_a_requested_level_of_zero_warns(self):
        self.phone()
        self.panel("panel0-backlight", 0, 0)
        r, status, ev = self.row()
        self.assertEqual(status, "WARN", ev)
        self.assertTrue(ev.startswith("power on but requested brightness 0: "), ev)
        self.assertIn("panel0-backlight bl_power=0 brightness=0/255 actual=0", ev)
        self.assertIn("panel1-backlight bl_power=0 brightness=200/255", ev)
        self.assertEqual(r.rc, 0, "a warning, not a failure")

    def test_a_requested_level_of_zero_warns_whatever_the_outputs_say(self):
        for drm in (True, False):
            with self.subTest(drm=drm):
                self.phone()
                if not drm:
                    self.f.rm("sys/class/drm")
                self.panel("panel0-backlight", 0, 0)
                self.panel("panel1-backlight", 0, 0)
                self.assertEqual(self.row()[1], "WARN")

    def test_a_dsi_connector_that_is_off_warns_though_a_level_is_requested(self):
        for field, value in (("dpms", "Off"), ("dpms", "Standby"), ("dpms", "Suspend"), ("enabled", "disabled")):
            with self.subTest(field=field, value=value):
                self.phone()
                self.dsi(2, **{field: value})
                _, status, ev = self.row()
                self.assertEqual(status, "WARN", ev)
                self.assertIn("backlight requested but the display pipeline has DSI-2 off", ev)
                self.assertIn("brightness=200/255 actual=0", ev)
                self.assertIn("DSI-1 on, DSI-2 off", ev)

    def test_both_connectors_off_warns_and_names_both(self):
        self.phone()
        self.dsi(1, dpms="Off")
        self.dsi(2, enabled="disabled")
        _, status, ev = self.row()
        self.assertEqual(status, "WARN", ev)
        self.assertIn("DSI-1, DSI-2 off", ev)

    def test_a_disabled_compositor_output_warns_when_the_kernel_lists_no_connectors(self):
        self.phone()
        self.f.rm("sys/class/drm")
        self.f.wlr(enabled="no")
        r, status, ev = self.row()
        self.assertEqual(status, "WARN", ev)
        self.assertIn("HWCOMPOSER-1 off", ev)
        self.assertEqual(r.status("gfx.output"), "FAIL", "gfx.output still fails on its own")

    def test_power_off_is_still_a_failure_whatever_else_is_lit(self):
        self.phone()
        self.panel("panel1-backlight", 200, 0, power=4)
        r, status, ev = self.row()
        self.assertEqual(status, "FAIL", ev)
        self.assertIn("panel off (bl_power 4 = off)", ev)
        self.assertIn("panel1-backlight bl_power=4 brightness=200/255", ev)
        self.assertEqual(r.rc, 1)

    # --- what is not evidence of darkness
    def test_only_dsi_connectors_that_have_a_panel_count(self):
        self.phone()  # DP-1 is disconnected and disabled, Virtual-1 is connected and disabled: neither is a panel
        self.assertEqual(self.row()[1], "PASS")
        self.dsi(2, status="disconnected", enabled="disabled", dpms="Off")
        _, status, ev = self.row()
        self.assertEqual(status, "PASS", ev)
        self.assertIn("DRM: DSI-1 on", ev)
        self.assertNotIn("DSI-2", ev)

    def test_a_connector_whose_state_cannot_be_read_is_not_taken_for_dark(self):
        self.phone()
        self.dsi(1, dpms=None)
        _, status, ev = self.row()
        self.assertEqual(status, "PASS", ev)
        self.assertIn("DSI-1 ?", ev)

    def test_a_level_the_kernel_does_report_above_zero_is_believed(self):
        self.phone()
        self.panel("panel0-backlight", 0, 12)
        self.assertEqual(self.row()[1], "PASS")

    def test_an_unreadable_requested_level_is_not_darkness(self):
        self.phone()
        self.panel("panel0-backlight", None, 0)
        _, status, ev = self.row()
        self.assertEqual(status, "PASS", ev)
        self.assertIn("panel0-backlight bl_power=0 brightness=?/255 actual=0", ev)

    def test_one_panel_only_still_warns_and_no_backlight_at_all_still_fails(self):
        self.phone()
        self.f.rm(BL + "panel1-backlight")
        _, status, ev = self.row()
        self.assertEqual(status, "WARN", ev)
        self.assertTrue(ev.startswith("one panel only: "), ev)
        self.f.rm("sys/class/backlight")
        self.assertEqual(self.row()[1], "FAIL")

    def test_the_healthy_fake_phone_of_test_selftest_still_passes_with_its_old_words_in_front(self):
        r, status, ev = self.row()
        self.assertEqual(status, "PASS", ev)
        self.assertTrue(ev.startswith("panel0-backlight bl_power=0 brightness=102/255"), ev)

    # --- it only reads
    def test_it_reads_files_and_asks_for_nothing_new(self):
        self.phone()
        before = self.f.snapshot()
        self.run_st()
        self.assertEqual(before, self.f.snapshot())
        self.assertLessEqual({ln.split()[0] for ln in self.f.calls()}, {"systemctl", "journalctl", "wlr-randr", "ip", "nmcli", "timedatectl"})

    def test_only_gfx_backlight_shows_just_that_line(self):
        self.phone()
        r = self.run_st("--only", "gfx.backlight")
        self.assertEqual(sorted(r.rows), ["gfx.backlight"])


class Docs(unittest.TestCase):
    def test_the_acceptance_doc_says_which_signals_are_trusted(self):
        row = next(ln for ln in read("docs/duo-acceptance.md").splitlines() if ln.startswith("| `gfx.backlight`"))
        flat = " ".join(row.split())
        for phrase in ("`brightness`", "`actual_brightness`", "not trusted", "DSI", "`/sys/class/drm`", "requested", "`bl_power`"):
            self.assertIn(phrase, flat)

    def test_the_check_does_not_run_anything(self):
        t = read("packaging/duo-os/opos-selftest")
        i = t.index("def check_backlight(c):")
        j = t.index("# ----", i)
        self.assertNotIn("c.run(", t[t.index("def display_pipeline(c):"):j])


if __name__ == "__main__":
    unittest.main()
