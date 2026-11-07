# SPDX-License-Identifier: GPL-3.0-or-later
"""What the docs say about OPOS as the Duo's operating system, checked against the packaging files and the code.
The claims are about one run on one phone; these tests keep the wording honest and the docs consistent."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUO_OS = "packaging/duo-os"
LINKED = ("README.md", "ROADMAP.md", "CHANGELOG.md", "docs/README.md", "docs/duo-android.md", DUO_OS + "/README.md")


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def flat(text):
    return " ".join(text.split())


def section(text, heading):
    start = text.index(heading + "\n")
    end = text.find("\n## ", start + len(heading))
    return text[start:end if end != -1 else len(text)]


def script_params():
    block = read("deploy/duo-opos-boot.ps1")
    block = block[block.index("param("):block.index("\n)\n")]
    return set(re.findall(r"\]\$(\w+)", block))


class TestDuoOsReadme(unittest.TestCase):
    def setUp(self):
        self.text = read(DUO_OS + "/README.md")
        self.flat = flat(self.text)

    def test_status_is_the_run_not_a_plan(self):
        head = flat(self.text[:self.text.index("## What was seen")])
        for phrase in ("Status, November 7, 2026", "ran on the owner's Surface Duo 1, RAM-booted from a PC",
                       '"it\'s working"', "One phone, one day, one person watching",
                       "a power cycle returns the phone to Android"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, head)
        for stale in ("not yet booted on a phone", "image built and checked offline", "Install, once USB data works"):
            self.assertNotIn(stale, self.text)

    def test_observations_name_the_evidence_and_the_gaps(self):
        seen = flat(section(self.text, "## What was seen, and what was not"))
        for phrase in ("`hwcomposer` backend", "Adreno 640", "phone's sysfs", "both backlights were on",
                       "Not measured, and not claimed", "battery life", "stability over hours",
                       "suspend and resume", "`opos-fallback`", "simulated modem",
                       "The raw logs from that day are not part of this repository"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, seen)
        # Nothing in the README quotes a speed, a boot time or a battery figure for the OS image.
        self.assertIsNone(re.search(r"\d+(\.\d+)?\s*(%|tokens|fps|ms\b|mAh|hours? of battery)", self.text.replace("20 %", "")))

    def test_image_contents_match_the_packaging_files(self):
        table = section(self.text, "## What is in the image")
        for name in ("opos-server.service", "opos.service", "opos-session", "opos-kiosk", "opos-fallback.service",
                     "opos-switch", "opos-diag"):
            with self.subTest(name=name):
                self.assertIn("`" + name, table)
                self.assertTrue((ROOT / DUO_OS / name).is_file())
        self.assertIn("--port 8765 --live-battery", read(DUO_OS + "/opos-server.service"))
        self.assertIn("127.0.0.1:8765", table)
        self.assertIn("http://127.0.0.1:8765/", read(DUO_OS + "/opos-kiosk"))
        self.assertIn("opos-session", read(DUO_OS + "/opos.service"))

    def test_phoc_shell_mode_explanation_matches_the_session_script(self):
        session = read(DUO_OS + "/opos-session")
        command = [line for line in session.splitlines() if "/usr/bin/phoc" in line and not line.startswith("#")]
        self.assertEqual(len(command), 1)
        self.assertNotRegex(command[0], r"(^|\s)-S(\s|$)")
        self.assertIn("-E /usr/libexec/opos/opos-kiosk", command[0])
        why = flat(section(self.text, "## What is in the image"))
        for phrase in ("**Why no phoc shell mode.**", "starts phoc with `-S`", "opaque black shield",
                       "both panels stayed black", "`opos-session` leaves `-S` out"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, why)
        self.assertIn("phoc is NOT started in shell mode", read(DUO_OS + "/verify-image.sh"))

    def test_ram_boot_rule_and_misc_brake_are_stated_and_kept_by_the_tools(self):
        rule = flat(section(self.text, "## RAM-boot only, and the misc brake"))
        for phrase in ("`fastboot boot boot.img`", "refuses flashes of the boot partition with `Device Error`",
                       "forbid flashing boot, vbmeta, dtbo or recovery", "`bootonce-bootloader`",
                       "One RAM-boot, never retried", "Never run `reboot` or `systemctl reboot`",
                       "`reboot bootloader`", "It is a precaution, not a guarantee"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rule)
        for tool in ("deploy/duo-opos-boot.ps1", "deploy/duo-rootfs-install.sh", DUO_OS + "/build-image.sh"):
            with self.subTest(tool=tool):
                self.assertIsNone(re.search(r"flash\s+(boot|vbmeta|dtbo|recovery)\b", read(tool)))
                self.assertNotIn("set_active", read(tool))

    def test_install_section_names_the_scripts_options_and_limits(self):
        install = flat(section(self.text, "## Install: RAM-boot from a Windows PC"))
        script = ROOT / "deploy" / "duo-opos-boot.ps1"
        self.assertTrue(script.is_file())
        self.assertIn("deploy\\duo-opos-boot.ps1", self.text)
        params = script_params()
        for option in ("ImageDir", "RootfsSha256", "Key", "Yes", "PhoneIp"):
            with self.subTest(option=option):
                self.assertIn("-" + option, install)
                self.assertIn(option, params)
        for phrase in ("This erases Android and everything it stored", "without touching the phone",
                       "The first failure stops it and nothing is retried",
                       "derives the /24 from the address of the PC's RNDIS adapter", "TCP port 22",
                       "`10.15.19.82` first", "has been parsed and had its probe exercised against loopback, not run on a phone",
                       "the less guarded route"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, install)

    def test_dev_key_section_matches_the_build_script(self):
        dev = flat(section(self.text, "## Dev options: an ssh key"))
        build = read(DUO_OS + "/build-image.sh")
        verify = read(DUO_OS + "/verify-image.sh")
        self.assertIn("OPOS_DEV_SSH_PUBKEY", build)
        self.assertIn("OPOS_DEV_SSH_PUBKEY=<file.pub>", dev)
        self.assertIn(".ssh/authorized_keys", build)
        self.assertIn("/home/droidian/.ssh/authorized_keys", dev)
        self.assertIn("install -d -m 0700", build)
        self.assertIn("chmod 0600", build)
        self.assertIn("0700", dev)
        self.assertIn("0600", dev)
        self.assertIn("DEV IMAGE", build)
        self.assertIn("`DEV IMAGE` line", dev)
        self.assertIn("rm ~/.ssh/authorized_keys", build)
        self.assertIn("rm ~/.ssh/authorized_keys", dev)
        self.assertIn("DEV image", verify)
        for phrase in ("a shell as `droidian`", "whoever holds the matching private key",
                       "leave it unset for an image that will be carried", "deletes every key in the file"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, dev)

    def test_boot_evidence_section_matches_the_image_files(self):
        evidence = flat(section(self.text, "## Boot evidence: persistent journal and opos-diag"))
        build = read(DUO_OS + "/build-image.sh")
        diag = read(DUO_OS + "/opos-diag")
        self.assertIn("Storage=persistent", build)
        self.assertIn("SystemMaxUse=200M", build)
        self.assertIn("50-opos-persistent.conf", build)
        for phrase in ("Storage=persistent", "SystemMaxUse=200M", "50-opos-persistent.conf",
                       "`/var/log/opos-boot-diag.txt`", "90 seconds", "that has not been rehearsed"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, evidence)
        self.assertIn("out=/var/log/opos-boot-diag.txt", diag)
        self.assertIn("sleep 90", diag)
        self.assertIn("opos-diag.service", build)

    def test_known_issues_and_restore_are_documented(self):
        issues = flat(section(self.text, "## Known issues"))
        restore = flat(section(self.text, "## Restore to Android"))
        for phrase in ("The clock reads 1970 until it is set", "A power cycle boots stock Android, which wipes the OS image",
                       "Calls and texts are simulated", "have not been exercised on the phone"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, issues)
        for phrase in ("Nothing was written to boot, vbmeta, dtbo or recovery", "no slot was changed",
                       "factory reset", "zero the first 2 KB of `misc`", "all 25 partitions",
                       "No restore from it is recorded in this repository", "October 31, 2026"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, restore)


class TestDuoStatusDocs(unittest.TestCase):
    def test_calls_and_texts_are_simulated_on_every_route_in_the_code(self):
        from opos import web
        phone = web.Phone()
        self.assertEqual(type(phone.modem).__name__, "ScreenModem")
        source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "opos").rglob("*.py")
                           if path.name != "mm_modem.py")
        self.assertNotIn("mm_modem", source)
        for name in ("ROADMAP.md", DUO_OS + "/README.md"):
            with self.subTest(doc=name):
                self.assertRegex(flat(read(name)), r"(?i)calls and texts (are )?(still )?simulated")
        self.assertIn("Calls and texts are simulated on every route", flat(read("ROADMAP.md")))

    def test_roadmap_separates_what_ran_from_what_did_not(self):
        roadmap = flat(read("ROADMAP.md"))
        working = roadmap[roadmap.index("## Working today"):roadmap.index("## Simulated or not yet verified")]
        simulated = roadmap[roadmap.index("## Simulated or not yet verified"):roadmap.index("## Public browser demo")]
        for phrase in ("Surface Duo with OPOS as the whole OS", "RAM-booted from a PC with no Android",
                       "the owner confirmed on the screens that it was working", "hwcomposer backend",
                       "real battery from sysfs", "One phone, one session", "17.7-18.4 generated tokens/s"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, working)
        for phrase in ("no measurement of speed, battery life or stability", "It cannot be installed permanently",
                       "every start is a RAM-boot from a PC", "have not been run on the phone",
                       "nothing was timed on the Duo OS image", "The postmarketOS kiosk has not been tested on a physical phone"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, simulated)
        self.assertIn("Status as of November 7, 2026", roadmap)

    def test_index_readme_limits_and_changelog_point_at_the_duo_docs(self):
        index = read("docs/README.md")
        self.assertIn("(../packaging/duo-os/README.md)", index)
        self.assertIn("deploy/duo-opos-boot.ps1", index)
        self.assertNotIn("not yet run on the phone", index)
        self.assertNotIn("Duo work is paused", index)
        self.assertIn("(duo-android.md)", index)
        readme = read("README.md")
        limits = flat(readme[readme.index("## Limits"):readme.index("## Documentation")])
        for phrase in ("(packaging/duo-os/README.md)", "(docs/duo-android.md)", "(docs/duo-usb-install.md)",
                       "one phone, one day", "Calls and texts are still simulated", "RAM-boot only",
                       "Nothing here measures speed, battery life or stability over time", "are historical"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, limits)
        self.assertNotIn("Surface Duo work is paused", readme)
        self.assertNotIn("This source has not run on a physical phone", readme)
        unreleased = flat(read("CHANGELOG.md").split("## 0.1.0")[0])
        for phrase in ("packaging/duo-os", "deploy/duo-opos-boot.ps1", "TCP 22", "it has not been run in this form",
                       "RAM-boot only", "no speed, battery-life or stability measurements"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, unreleased)
        android = flat(read("docs/duo-android.md"))
        self.assertIn("(../packaging/duo-os/README.md)", android)
        self.assertIn("(duo-usb-install.md) stay historical", android)

    def test_links_in_the_edited_docs_resolve(self):
        link = re.compile(r"\]\(([^)\s#]+)(#[^)\s]*)?\)")
        for name in LINKED:
            base = (ROOT / name).parent
            for target, _ in link.findall(read(name)):
                if re.match(r"[a-z]+:", target):
                    continue
                with self.subTest(doc=name, link=target):
                    self.assertTrue((base / target).exists())


if __name__ == "__main__":
    unittest.main()
