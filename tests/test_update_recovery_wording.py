# SPDX-License-Identifier: GPL-3.0-or-later
"""Update recovery must keep the phone running, including text used by live refresh."""
import ast
import re
import unittest
from pathlib import Path

from opos import update_page

ROOT = Path(__file__).resolve().parents[1]


class UpdateRecoveryWording(unittest.TestCase):
    def assert_service_only_recovery(self, text):
        # Only these explicitly service-scoped restarts are safe. An unqualified
        # "next restart" is unsafe too, even when it does not name the phone.
        text = re.sub(
            r"OPOS server restarts|OPOS restarts its server|systemctl restart opos-update\.path",
            "service recovery", text, flags=re.I,
        )
        self.assertNotRegex(
            text,
            r"(?i)\b(?:restart\w*|reboot\w*|power(?:ing|ed)?(?:[ -]?(?:off|down|cycl\w*)|"
            r" (?:the |your )?(?:phone|device))|shut(?:ting)?[ -]?down|"
            r"(?:turn|switch)(?:ing)? (?:the |your |it )?(?:phone |device )?off|"
            r"off and on|next boot)\b",
        )

    def test_recovery_guard_rejects_phone_restart_and_power_advice(self):
        for text in ("Restart the phone", "Try restarting", "The next restart",
                     "Reboot your device", "Try rebooting", "Power off the phone",
                     "Try powering off the phone", "Power the phone off", "Power-cycle it",
                     "Try power cycling", "Shut down the phone", "Try shutting down",
                     "Turn your phone off", "Try turning the device off",
                     "It applies at the next boot", "Switch the phone off and on again",
                     "Turn it off and on", "Try switching it off"):
            with self.subTest(text=text):
                with self.assertRaises(AssertionError):
                    self.assert_service_only_recovery(text)

    def test_all_update_page_string_literals_have_safe_recovery_wording(self):
        # Includes hidden HTML, the shared staged message and JavaScript messages.
        source = Path(update_page.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                with self.subTest(line=node.lineno):
                    self.assert_service_only_recovery(node.value)

    def test_rendered_update_states_never_advise_a_phone_restart(self):
        for available in (False, True):
            for stalled in (False, True):
                for staged in (None, 3):
                    with self.subTest(available=available, stalled=stalled, staged=staged):
                        html = update_page.page(snap={
                            "available": available, "stalled": stalled,
                            "rollback": {"possible": True, "staged": staged},
                        })
                        self.assert_service_only_recovery(html)

    def test_updater_status_messages_shown_on_the_page_have_safe_wording(self):
        # The Updates page shows status.json's message as it is, so the updater's
        # own status text is page text too.
        source = (ROOT / "packaging" / "duo-os" / "updater" / "opos-updater").read_text(encoding="utf-8")
        seen = 0
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "status"):
                for part in ast.walk(node):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        seen += 1
                        with self.subTest(line=part.lineno, text=part.value):
                            self.assert_service_only_recovery(part.value)
        self.assertGreater(seen, 20)
        for form, values in (("update %d (%s) is staged and applies the next time OPOS is started from the PC", (3, "1.2.3")),
                             ("staged update %d is kept for the next time OPOS is started from the PC: %s", (3, "no room"))):
            with self.subTest(form=form):
                self.assertIn("'%s'" % form, source)
                html = update_page.page(snap={"status": {"phase": "done", "message": form % values, "serial": 3},
                                              "rollback": {"possible": True, "staged": 3}})
                self.assertIn(form % values, html)
                self.assert_service_only_recovery(html)

    def test_stalled_updater_advice_says_where_and_on_which_image(self):
        # Root on the phone exists only on a development image (passwordless sudo
        # for the login user), and the commands run on the phone, over ssh.
        html = update_page.page(snap={"available": True, "stalled": True})
        stall = html.split('<p id="u-stall" role="alert">', 1)[1].split("</p>", 1)[0]
        source = (ROOT / "deploy" / "duo-push-update.ps1").read_text(encoding="utf-8")
        hint = re.findall(r'^\s*\$hint = "([^"\n]*)"', source, re.M)[1]
        for text in (stall, hint):
            with self.subTest(text=text):
                self.assertRegex(text, r"(?i)keep the phone on")
                self.assertIn("needs root on the phone", text)
                self.assertIn("development image", text)
                self.assertIn("ver ssh from the PC", text)
                self.assertNotIn("From the PC, run as root", text)
                self.assertNotIn("use the PC to run as root", text)
        self.assertIn("(a development image with passwordless sudo). Over ssh from the PC: "
                      "sudo systemctl reset-failed opos-update.path opos-update.service; "
                      "sudo systemctl restart opos-update.path", stall)
        # The push script's own text guard allows no privilege word in the file,
        # so its hint names root on the phone instead.
        self.assertIn("run as root on the phone: systemctl reset-failed opos-update.path opos-update.service; "
                      "systemctl restart opos-update.path", hint)

    def test_setup_wizard_failure_help_does_not_advise_a_device_restart(self):
        from opos.setup import wizard_page
        source = Path(wizard_page.__file__).read_text(encoding="utf-8")
        self.assertIn("If it still fails, run opos again.</p>", source)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                with self.subTest(line=node.lineno):
                    self.assert_service_only_recovery(node.value)

    def test_duo_documents_do_not_advise_a_plain_restart_of_the_phone(self):
        for name, unsafe in (("docs/ota-release-howto.md", "or reboot\n"),
                             ("docs/duo-acceptance.md", "Restart the phone and see"),
                             ("docs/duo-os-final.md", "**Reboot** from the PC"),
                             ("docs/duo-firstrun.md", "survives a power cycle")):
            with self.subTest(name=name):
                self.assertNotIn(unsafe, (ROOT / name).read_text(encoding="utf-8"))
        self.assertIn("Do not restart or power-cycle the phone itself.",
                      (ROOT / "docs/ota-release-howto.md").read_text(encoding="utf-8"))
        self.assertIn("RAM-boot the phone again from the PC (never a plain restart)",
                      (ROOT / "docs/duo-acceptance.md").read_text(encoding="utf-8"))
        self.assertIn("**RAM-boot again** from the PC",
                      (ROOT / "docs/duo-os-final.md").read_text(encoding="utf-8"))

    def test_push_timeout_hint_only_recovers_the_update_service(self):
        source = (ROOT / "deploy" / "duo-push-update.ps1").read_text(encoding="utf-8")
        hints = re.findall(r'^\s*\$hint = "([^"\n]*)"', source, re.M)
        self.assertEqual(len(hints), 2)
        for hint in hints:
            self.assert_service_only_recovery(hint)
        self.assertIn("systemctl reset-failed opos-update.path opos-update.service", hints[1])
        self.assertIn("systemctl restart opos-update.path", hints[1])


if __name__ == "__main__":
    unittest.main()
