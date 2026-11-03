# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from opos import notices


ROOT = Path(__file__).resolve().parent.parent
NAMES = re.compile(r"\b(?:Jev|Nisi|AGIW|Astra|Sol|Fable|Opus)\b")


class TestPublicCreditNames(unittest.TestCase):
    def section(self, text, heading):
        parts = re.split(r"^## " + re.escape(heading) + r"$", text, flags=re.M)
        self.assertEqual(len(parts), 2, heading)
        following = re.split(r"(?=^## )", parts[1], maxsplit=1, flags=re.M)
        return following[0], parts[0] + "".join(following[1:])

    def test_credit_names_are_defined(self):
        text = (ROOT / "WORK-BY-MODEL.md").read_text(encoding="utf-8")
        definitions, outside = self.section(text, "Names used above")
        used = set(NAMES.findall(outside))
        defined = set(NAMES.findall(definitions))
        self.assertTrue(used)
        for name in sorted(used):
            with self.subTest(name=name):
                self.assertIn(name, defined)

    def test_notice_records_november_3_build(self):
        text = "\n".join(notices.TESTED_DEVICES)
        self.assertNotIn("QA-only", text)
        self.assertIn("November 3", text)

    def test_commit_template_provenance_parses_without_repository(self):
        text = (ROOT / "guard" / "RULES.md").read_text(encoding="utf-8")
        section, _ = self.section(text, "Commit messages")
        templates = re.findall(r"^```text\n(.*?)^```$", section, re.M | re.S)
        self.assertEqual(len(templates), 1)
        message = templates[0]
        self.assertEqual(len(message.splitlines()), 6)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        with tempfile.TemporaryDirectory() as directory:
            env["GIT_CEILING_DIRECTORIES"] = str(Path(directory).resolve().parent)
            parsed = subprocess.run(
                ["git", "interpret-trailers", "--parse"], input=message,
                text=True, capture_output=True, check=True, timeout=10,
                cwd=directory, env=env,
            )
        self.assertIn("Provenance: <id>", parsed.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
