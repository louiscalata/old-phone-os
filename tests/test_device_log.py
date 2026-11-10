# SPDX-License-Identifier: GPL-3.0-or-later
"""The D1-D11 on-device table stays NOT_RUN until a dated evidence link exists."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADING = "## On-device checks"
LINK = re.compile(r"\]\(https?://[^)\s]+\)")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
BANNED = ("imei", "imsi", "iccid", "msisdn")


def section():
    text = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    start = text.index(HEADING)
    end = text.find("\n## ", start + len(HEADING))
    return text[start:end if end != -1 else len(text)]


def rows():
    found = []
    for line in section().splitlines():
        if not line.startswith("| D"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        found.append({
            "id": cells[0],
            "check": cells[1],
            "date": cells[2],
            "result": cells[3],
            "evidence": cells[4],
        })
    return found


def pass_is_complete(row):
    """A PASS needs a date and an evidence link. NOT_RUN does not."""
    if row["result"].upper() != "PASS":
        return True
    return bool(DATE.search(row["date"])) and bool(LINK.search(row["evidence"]))


class DeviceLog(unittest.TestCase):
    def test_d1_through_d11_are_present_and_not_passed(self):
        got = rows()
        self.assertEqual([row["id"] for row in got], ["D%d" % n for n in range(1, 12)])
        body = section().lower()
        for word in BANNED:
            self.assertNotIn(word, body)
        for row in got:
            self.assertTrue(pass_is_complete(row), row["id"])
            self.assertIn("](https://github.com/louiscalata/old-phone-os/pull/", row["evidence"], row["id"])
            self.assertEqual(row["result"], "NOT_RUN", row["id"])
            self.assertEqual(row["date"], "", row["id"])

    def test_a_pass_without_a_date_or_a_link_is_rejected(self):
        linked = {"date": "", "result": "PASS", "evidence": "[PR #1](https://github.com/louiscalata/old-phone-os/pull/1)"}
        dated = {"date": "2026-11-10", "result": "PASS", "evidence": "a note"}
        both = {"date": "2026-11-10", "result": "PASS", "evidence": "[log](https://example.com/log)"}
        self.assertFalse(pass_is_complete(linked))
        self.assertFalse(pass_is_complete(dated))
        self.assertTrue(pass_is_complete(both))
