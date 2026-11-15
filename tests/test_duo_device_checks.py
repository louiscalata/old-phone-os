# SPDX-License-Identifier: GPL-3.0-or-later
"""docs/duo-device-checks.md, the runbook for the phone checks that are still NOT_RUN.

Every NOT_RUN statement in the source docs must have a check (or a "covered elsewhere" row) that quotes it, every
quote must still be in its source, every check must say how to run, judge, stop and record it, and the steps that
erase, reboot or change the phone's own connection must say so where they are written. No result is recorded here."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = "docs/duo-device-checks.md"
SOURCES = ("packaging/duo-os/NATIVE.md", "ROADMAP.md", "docs/duo-os-final.md", "docs/duo-usb-install.md")
# A quoted citation: `file:line` "phrase". The line number is a pointer for people; the phrase is what is checked.
CITE = re.compile(r"`(?P<file>[\w./-]+\.md):(?P<line>\d+)`\s+\"(?P<phrase>[^\"]+)\"")
PARTS = ("**Preconditions.**", "**Steps.**", "**Observe.**", "**Pass or fail.**", "**Stop and undo.**", "**Record.**")
# Starts of a block in the sources: a heading, a list item, a numbered item or a table row.
ITEM = re.compile(r"^\s*(#|- |\d+\. |\|)")
# Commands that erase or reboot the phone; a step line that holds one must carry the marker.
ERASES = re.compile(r"--erase-userdata|format:ext4|-Yes\b|--yes\b")
REBOOTS = re.compile(r"reboot bootloader|fastboot boot\b")
# A step that can end or change the phone's own Wi-Fi connection.
WIFI = re.compile(r"(?i)\b(join|forget|disconnect|turn wi-fi off|wifi off|roam)\b")
# What the repository refuses: private addresses, user paths and device identifiers.
PRIVATE = (r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])",
           r"(?<![\w.-])/home/[A-Za-z]", r"[A-Za-z]:\\{1,2}Users\\{1,2}", "/Users/[A-Za-z]",
           r"(?i)\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", r"(?<!\d)\d{15}(?!\d)")


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def flat(text):
    return " ".join(text.split())


def not_run_blocks(name):
    """Each block (paragraph, list item or table row) of a source that says NOT_RUN, flattened."""
    lines = read(name).splitlines()
    blocks = []
    for i, line in enumerate(lines):
        if "NOT_RUN" not in line:
            continue
        start = i
        while start > 0 and lines[start].strip() and not ITEM.match(lines[start]):
            start -= 1
        if not lines[start].strip():
            start += 1
        end = i + 1
        while end < len(lines) and lines[end].strip() and not ITEM.match(lines[end]):
            end += 1
        blocks.append((i + 1, flat("\n".join(lines[start:end]))))
    return blocks


def checks(text):
    """The check sections: (id, title, body)."""
    found = re.findall(r"^### (C\d{2})\. (.+)$", text, re.M)
    out = []
    for cid, title in found:
        start = text.index("### %s. %s" % (cid, title))
        nxt = re.search(r"^##", text[start + 4:], re.M)
        out.append((cid, title, text[start:start + 4 + nxt.start()] if nxt else text[start:]))
    return out


def steps(body):
    part = body[body.index("**Steps.**"):body.index("**Observe.**")]
    return [line for line in part.splitlines() if re.match(r"\s*\d+\. ", line)]


class TestDeviceChecksRunbook(unittest.TestCase):
    def setUp(self):
        self.text = read(RUNBOOK)
        self.flat = flat(self.text)
        self.cites = [m.groupdict() for m in CITE.finditer(self.text)]
        self.checks = checks(self.text)

    def test_every_not_run_statement_in_the_sources_has_a_row(self):
        for name in SOURCES:
            phrases = [flat(c["phrase"]) for c in self.cites if c["file"] == name]
            for line, block in not_run_blocks(name):
                with self.subTest(source="%s:%d" % (name, line)):
                    self.assertTrue(any(p in block for p in phrases),
                                    "no citation quotes the NOT_RUN block at %s:%d: %s" % (name, line, block[:160]))

    def test_every_quote_is_still_in_its_source(self):
        self.assertGreater(len(self.cites), 20)
        for c in self.cites:
            with self.subTest(cite="%s:%s" % (c["file"], c["line"])):
                self.assertIn(c["file"], SOURCES)
                self.assertGreaterEqual(len(flat(c["phrase"])), 12, "a quote this short proves nothing")
                self.assertIn(flat(c["phrase"]), flat(read(c["file"])))
                self.assertLessEqual(int(c["line"]), len(read(c["file"]).splitlines()))

    def test_each_check_says_how_to_run_judge_stop_and_record_it(self):
        self.assertGreaterEqual(len(self.checks), 20)
        ids = [cid for cid, _, _ in self.checks]
        self.assertEqual(len(ids), len(set(ids)))
        for cid, title, body in self.checks:
            with self.subTest(check=cid):
                positions = [body.find(part) for part in PARTS]
                self.assertNotIn(-1, positions, title)
                self.assertEqual(positions, sorted(positions), title)
                self.assertIn("Sources:", body)
                self.assertTrue(steps(body), title)

    def test_steps_that_erase_reboot_or_change_wi_fi_say_so_and_name_the_owner(self):
        for cid, title, body in self.checks:
            for line in steps(body):
                with self.subTest(check=cid, step=line.strip()[:60]):
                    if ERASES.search(line):
                        self.assertIn("[ERASES]", line)
                        self.assertIn("[OWNER ONLY]", line)
                    if REBOOTS.search(line):
                        self.assertIn("[REBOOTS]", line)
                        self.assertIn("[OWNER ONLY]", line)
                    if WIFI.search(line) and "`python3 -m opos.net status`" not in line:
                        self.assertIn("[OWNER ONLY]", line)
                    # A RAM-booted kernel must never be restarted with a plain reboot.
                    self.assertIsNone(re.search(r"`(?:sudo )?(?:systemctl )?reboot`", line))

    def test_the_ground_rules_keep_the_phone_lit_and_recoverable(self):
        rules = flat(self.text[self.text.index("## Ground rules"):self.text.index("## Recovery paths")])
        for phrase in ("No step wipes, flashes or changes Wi-Fi unless it is marked and the owner says go",
                       "Keep the USB cable in", "Never run `reboot` or `systemctl reboot`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rules)
        recovery = flat(self.text[self.text.index("## Recovery paths"):])
        for phrase in ("python3 -m opos.native.blank --restore", "sudo touch /etc/opos/native-off",
                       "nmcli radio wifi on", "reboot bootloader"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, recovery)

    def test_the_results_table_has_one_row_per_check_and_records_no_pass(self):
        table = self.text[self.text.index("## Results"):]
        rows = re.findall(r"^\| (C\d{2}) \|(.*)\|$", table, re.M)
        self.assertEqual([r[0] for r in rows], [cid for cid, _, _ in self.checks])
        for cid, rest in rows:
            cells = [c.strip() for c in rest.split("|")]
            with self.subTest(check=cid):
                self.assertEqual(cells[1:3], ["", "NOT_RUN"])
        self.assertNotRegex(self.text, r"\|\s*(PASS|FAIL)\s*\|")

    def test_linked_from_the_index_and_free_of_private_details(self):
        self.assertIn("(duo-device-checks.md)", read("docs/README.md"))
        for pattern in PRIVATE:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, self.text))

    def test_links_resolve(self):
        base = (ROOT / RUNBOOK).parent
        for target in re.findall(r"\]\(([^)\s#]+)(?:#[^)\s]*)?\)", self.text):
            if re.match(r"[a-z]+:", target):
                continue
            with self.subTest(link=target):
                self.assertTrue((base / target).exists())


if __name__ == "__main__":
    unittest.main()
