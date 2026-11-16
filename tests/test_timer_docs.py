# SPDX-License-Identifier: GPL-3.0-or-later
"""The docs written before the sensorfwd boot timer (stage 1) landed said the image did not start the sensor service.
The build now installs opos-sensorfwd-delayed.timer, so those sentences must be gone and each place must say what is
true: the timer starts the service 120 s after boot unless /etc/opos/sensors-off exists, the file blocks future starts
only, and the cold boot with the timer, ten cold boots and the image on the phone are still NOT_RUN."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = {
    "packaging/duo-os/NATIVE.md": 2,
    "ROADMAP.md": 1,
    "docs/duo-os-final.md": 1,
    "docs/duo-device-checks.md": 1,
}
STALE = (
    "not in this repository's image",
    "nothing in this repository starts",
    "nothing in this repository's image build starts",
    "installed by hand on that phone",
    "installed by hand on the owner's phone",
    "owner's own delayed sensor start, installed by hand",
)


def flat(name):
    return " ".join((ROOT / name).read_text(encoding="utf-8").split())


def paragraphs(name):
    return [
        " ".join(p.split())
        for p in re.split(r"\n\s*\n|\n(?=\s*(?:- |\d+\. |\|))", (ROOT / name).read_text(encoding="utf-8"))
    ]


class TimerDocsTest(unittest.TestCase):
    def test_the_stale_sentences_are_gone(self):
        for name in list(PLACES) + ["CHANGELOG.md"]:
            text = flat(name)
            for phrase in STALE:
                with self.subTest(doc=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_each_place_names_the_timer_and_what_stays_not_run(self):
        for name, count in PLACES.items():
            blocks = [p for p in paragraphs(name) if "opos-sensorfwd-delayed.timer" in p and "NOT_RUN" in p]
            with self.subTest(doc=name):
                self.assertGreaterEqual(len(blocks), count if name != "packaging/duo-os/NATIVE.md" else 1)
                joined = " ".join(blocks)
                if name != "packaging/duo-os/NATIVE.md":
                    self.assertIn("120 s after boot", joined)
                self.assertIn("ten cold boots", joined)

    def test_native_says_the_build_installs_the_timer_and_the_file_blocks_future_starts_only(self):
        text = flat("packaging/duo-os/NATIVE.md")
        self.assertIn("the image build installs `opos-sensorfwd-delayed.timer`", text)
        self.assertIn("/etc/opos/sensors-off", text)
        self.assertIn("blocks future starts only", text)

    def test_the_changelog_has_one_timer_line_under_unreleased(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = text.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(sum("opos-sensorfwd-delayed.timer" in line for line in unreleased.splitlines()), 1)

    def test_the_roadmap_names_the_timer_lane(self):
        self.assertIn("opos-sensorfwd-delayed.timer", flat("ROADMAP.md"))


if __name__ == "__main__":
    unittest.main()
