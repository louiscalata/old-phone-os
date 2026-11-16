# SPDX-License-Identifier: GPL-3.0-or-later
"""The docs for the hinge angle and back mode (packaging/duo-os/NATIVE.md, "The hinge and back mode", and the lines
that point at it): every number matches the native shell's code once that code is in the tree, the CPU figures are
labelled as the PC session's, phone-side release names stay out, and what nobody has run stays NOT_RUN."""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "src" / "opos" / "native"
DOC = "packaging/duo-os/NATIVE.md"
HEADING = "## The hinge and back mode"
DOCS = (
    DOC,
    "ROADMAP.md",
    "CHANGELOG.md",
    "docs/architecture.md",
    "docs/duo-os-final.md",
    "docs/duo-on-device-checklist.md",
    "docs/duo-acceptance.md",
    "docs/duo-device-checks.md",
)
# Starts of a block: a heading, a list item, a numbered item or a table row (as in tests/test_duo_device_checks.py).
ITEM = re.compile(r"^\s*(#|- |\d+\. |\|)")


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def flat(text):
    return " ".join(text.split())


def section():
    text = read(DOC)
    start = text.index(HEADING)
    end = text.find("\n## ", start + len(HEADING))
    return flat(text[start : end if end != -1 else len(text)])


def blocks_with(name, words):
    """Each block (paragraph, list item or table row) of a doc that holds `words`, flattened."""
    lines = read(name).splitlines()
    found = []
    starts = set()
    for i, line in enumerate(lines):
        if words not in line:
            continue
        start = i
        while start > 0 and lines[start].strip() and not ITEM.match(lines[start]):
            start -= 1
        if not lines[start].strip():
            start += 1
        if start in starts:  # a second mention in the same block is the same block
            continue
        starts.add(start)
        end = i + 1
        while end < len(lines) and lines[end].strip() and not ITEM.match(lines[end]):
            end += 1
        found.append(flat("\n".join(lines[start:end])))
    return found


def constants(name):
    """The module-level NAME = literal assignments of a native shell module, read without importing it."""
    tree = ast.parse((NATIVE / name).read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def num(value):
    return "%g" % value


class TheHingeSectionSaysWhatWasSeenAndWhatWasNot(unittest.TestCase):
    def test_the_section_and_the_file_rows_are_there(self):
        doc = read(DOC)
        self.assertIn(HEADING, doc)
        for row in ("| `src/opos/native/hinge.py` |", "| `src/opos/native/facing.py` |"):
            self.assertIn(row, doc)
        for name, link in (
            ("ROADMAP.md", "(packaging/duo-os/NATIVE.md#the-hinge-and-back-mode)"),
            ("CHANGELOG.md", "(packaging/duo-os/NATIVE.md#the-hinge-and-back-mode)"),
            ("docs/duo-os-final.md", "(../packaging/duo-os/NATIVE.md#the-hinge-and-back-mode)"),
            ("docs/duo-acceptance.md", "(../packaging/duo-os/NATIVE.md#the-hinge-and-back-mode)"),
            ("docs/architecture.md", "(../packaging/duo-os/NATIVE.md)"),
        ):
            with self.subTest(doc=name):
                self.assertTrue(link in read(name), link)

    def test_the_lid_no_longer_says_no_angle_reaches_linux(self):
        lid = flat(read(DOC))
        self.assertNotIn("No hinge angle or accelerometer reaches Linux on this phone, so book", lid)
        self.assertIn("The kernel gives Linux no hinge angle or accelerometer on this phone", lid)

    def test_what_nobody_ran_stays_not_run(self):
        text = section()
        tail = text[text.index("**NOT_RUN**") :]
        for words in (
            "a dark screen ignoring touch",
            "the Settings Hinge line on the glass",
            "ten cold boots",
            "any other phone",
        ):
            self.assertIn(words, tail)
        checklist = read("docs/duo-on-device-checklist.md")
        for row in (
            "| Folded back, only the screen that faces you is lit, and it follows a flip within about a second | NOT_RUN |",
            "| Folded back, touch on the dark screen is ignored | NOT_RUN |",
            "| Both screens light again when the lid opens after back mode | NOT_RUN |",
        ):
            self.assertIn(row, checklist)

    def test_the_cpu_figures_are_labelled_as_the_pc_sessions(self):
        seen = 0
        for name in (DOC, "ROADMAP.md", "CHANGELOG.md"):
            for block in blocks_with(name, "93 and 45"):
                seen += 1
                with self.subTest(doc=name):
                    self.assertIn("peer-measured", block)
                    self.assertIn("ticks", block)
                    self.assertNotIn("percent", block)
        self.assertEqual(seen, 3)

    def test_no_phone_side_release_name_and_no_other_phone(self):
        for name in DOCS:
            text = flat(read(name))
            with self.subTest(doc=name):
                self.assertIsNone(re.search(r"(?i)\brelease (?:8|9|10|11|12)\b|\b(?:8-96675|9-8ff2|12-adead)", text))
                self.assertNotIn("Z Fold", text)
                self.assertNotIn("saves battery", text)
        for name in (DOC, "ROADMAP.md"):
            self.assertIn("an installed release", flat(read(name)))

    def test_the_sensors_off_file_is_never_said_to_stop_a_running_service(self):
        # The file only blocks later starts; a sensorfwd that is already running needs the timer disabled and a stop.
        stop = [
            b for b in blocks_with("docs/duo-device-checks.md", "sensors-off") if b.startswith("**Stop and undo.**")
        ]
        self.assertEqual(len(stop), 1)
        for words in (
            "only blocks later starts",
            "does not stop a `sensorfwd` that is already running",
            "`sudo systemctl disable --now opos-sensorfwd-delayed.timer`",
            "`sudo systemctl stop sensorfwd`",
        ):
            self.assertIn(words, stop[0])
        target = r"(?:(?:a|the) )?(?:(?:already )?running )?(?:`?sensorfwd`?|(?:the )?sensor service|(?:the )?sensors?)"
        wrong = re.compile(
            r"(?i)\b(?:stop(?:s|ping)?|halt(?:s|ing)?|disabl(?:e|es|ing)|turn(?:s|ing)? off|"
            r"switch(?:es|ing)? off|shut(?:s|ting)? down) " + target + r"(?![\w-])|"
            r"\b(?:turn(?:s|ing)?|switch(?:es|ing)?|keep(?:s|ing)?|shut(?:s|ting)?) "
            r"(?:it|them|" + target + r") (?:off|down)\b"
        )
        negated = re.compile(
            r"(?i)\b(?:does not|do not|doesn't|don't|never|cannot|can't|will not|won't|not enough to)\s*$"
        )
        for name in DOCS:
            for block in blocks_with(name, "sensors-off"):
                # Commands in code spans (the owner's live stop) and negated claims are what the docs should say.
                text = re.sub(r"`[^`]*systemctl[^`]*`", "`command`", block)
                claims = [m.group(0) for m in wrong.finditer(text) if not negated.search(text[: m.start()])]
                with self.subTest(doc=name, block=block[:60]):
                    self.assertEqual(claims, [])


@unittest.skipUnless(
    (NATIVE / "hinge.py").exists() and (NATIVE / "facing.py").exists(), "the hinge code is not in this tree yet"
)
class TheNumbersMatchTheCode(unittest.TestCase):
    def setUp(self):
        self.text = section()
        self.hinge = constants("hinge.py")
        self.facing = constants("facing.py")
        self.posture = constants("posture.py")

    def test_the_sensor_names_and_the_data_socket(self):
        h = self.hinge
        for words in (
            "`%s`" % h["NAME"],
            "`%s`" % h["SENSOR"],
            "`%s`" % h["ACCEL"],
            "`%s`" % h["SOCKET"],
            "the first reading waits %s s" % num(h["SETTLE_S"]),
            "looks again every %s s" % num(h["RETRY_S"]),
        ):
            self.assertIn(words, self.text)

    def test_the_polling(self):
        h = self.hinge
        for words in (
            "every %s s while the hinge is moving" % num(h["POLL_S"]),
            "by %d degrees or more in the last %s s" % (h["MIN_STEP"], num(h["CALM_S"])),
            "at %d degrees or more" % h["FAST_FROM"],
            "otherwise every %s s, with the accelerometer read every %s s"
            % (num(h["SLOW_POLL_S"]), num(h["SLOW_TILT_S"])),
        ):
            self.assertIn(words, self.text)

    def test_the_zones_and_the_hysteresis(self):
        z = dict((name, (low, high)) for name, low, high in self.posture["ZONES"])
        self.assertEqual(list(z), ["closed", "book", "flat", "tent", "back"])
        words = "closed below %d degrees, book %d to %d, flat %d to %d, tent %d to %d, back from %d" % (
            z["closed"][1],
            z["book"][0],
            z["book"][1],
            z["flat"][0],
            z["flat"][1],
            z["tent"][0],
            z["tent"][1],
            z["back"][0],
        )
        self.assertIn(words, self.text)
        self.assertIn("leaves it by %d degrees" % self.posture["HYST"], self.text)

    def test_which_screen_faces_you(self):
        f = self.facing
        g = f["G"]
        threshold = 0.35  # facing.THRESHOLD is written as 0.35 * G, which literal_eval cannot read
        src = (NATIVE / "facing.py").read_text(encoding="utf-8")
        self.assertIn("THRESHOLD = %s * G" % num(threshold), src)
        self.assertEqual(g, 1024.0)
        for words in (
            "above +%s g" % num(threshold),
            "below -%s g" % num(threshold),
            "hold for %s s" % num(f["HOLD_S"]),
        ):
            self.assertIn(words, self.text)

    def test_the_settings_line_is_quoted_as_the_code_writes_it(self):
        src = (NATIVE / "posture.py").read_text(encoding="utf-8")
        self.assertIn('" One screen is lit; the other is dark."', src)
        self.assertIn('"One screen is lit; the other is dark."', self.text)


if __name__ == "__main__":
    unittest.main()
