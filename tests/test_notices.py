# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import io
import json
import os
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from opos import notices

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "opos" / "notices.py"
EXACT = ("Old Phone OS Interface is Louis Calata's hackathon demo. It is experimental software, "
         "provided as-is with no warranty (GPL-3.0). Installing it replaces your phone's "
         "operating system and may erase data; back up first and use at your own risk.")
NETWORK = {"urllib", "http", "socket", "requests", "ssl", "ftplib", "smtplib", "asyncio", "subprocess"}


def _all_text(d):
    out = []
    for v in d.values():
        out.extend(v if isinstance(v, list) else [v])
    return out


class TestNotices(unittest.TestCase):
    def test_disclaimer_exact(self):
        self.assertEqual(notices.DISCLAIMER, EXACT)
        self.assertEqual(notices.notices()["disclaimer"], EXACT)
        self.assertIn(EXACT + "\n\n" + notices.INSTALL_SCOPE + "\n\n", notices.as_text())
        self.assertEqual(notices.INSTALL_SCOPE,
                         "The replacement warning is for OS flashing; per-user install.sh "
                         "installs OPOS within the existing OS.")
        jaryo = (ROOT / "docs" / "jaryo.md").read_text(encoding="utf-8")
        self.assertIn("> " + EXACT, jaryo)

    def test_every_field_non_empty(self):
        d = notices.notices()
        self.assertEqual(tuple(d), notices.FIELDS)
        for k, v in d.items():
            self.assertTrue(v, k)
            if isinstance(v, list):
                for x in v:
                    self.assertTrue(isinstance(x, str) and x.strip(), k)
            else:
                self.assertTrue(isinstance(v, str) and v.strip(), k)
        self.assertEqual(d["license"], "GNU GPL-3.0-or-later")
        self.assertEqual(d["copyright"], notices.COPYRIGHT)
        self.assertTrue(any("untested" in x for x in d["tested_devices"]))
        tp = " ".join(d["third_party"])
        for name in ("postmarketOS", "Linux"):
            self.assertIn(name, tp)
        for name in ("postmarketOS", "Microsoft", "Google"):
            self.assertIn(name, d["affiliation"])
        risks = " ".join(d["risks"]).lower()
        for word in ("warranty", "bootloader", "brick", "emergency"):
            self.assertIn(word, risks)

    def test_version_matches_packaging(self):
        apk = (ROOT / "packaging" / "pmos" / "opos" / "APKBUILD").read_text(encoding="utf-8")
        self.assertEqual(re.search(r"^pkgver=(\S+)$", apk, re.M).group(1), notices.VERSION)

    def test_source_address_matches_the_public_project_contract(self):
        from urllib.parse import urlsplit
        address = urlsplit("https://" + notices.SOURCE_URL)
        self.assertEqual((address.hostname, address.path),
                         ("github.com", "/louiscalata/old-phone-os"))
        self.assertIsNone(address.username)
        self.assertIsNone(address.password)
        self.assertFalse(address.query or address.fragment)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://" + notices.SOURCE_URL, readme)

    def test_build_resolution(self):
        with mock.patch.dict(os.environ, {"OPOS_BUILD": "05F2C4F5"}):
            self.assertEqual(notices._build(), "05f2c4f5")
        for bad in ("", "not a hash", "../../x", "12"):
            with mock.patch.dict(os.environ, {"OPOS_BUILD": bad}):
                if not (ROOT / "src" / "opos" / "_build_info.py").exists():
                    self.assertEqual(notices._build(), "unpublished")

    def test_no_private_paths_ips_or_quiet_words(self):
        texts = _all_text(notices.notices()) + [(ROOT / "docs" / "NOTICES.md").read_text(encoding="utf-8")]
        blob = "\n".join(texts)
        for pat in (r"/Users/", r"/home/", r"C:\\Users", r"~/",
                    r"\b10\.\d+\.\d+\.\d+\b", r"\b192\.168\.\d+\.\d+\b", r"\b172\.(1[6-9]|2\d|3[01])\.\d+\.\d+\b",
                    r"\b127\.\d+\.\d+\.\d+\b", r"\bfe80:", r"@"):
            self.assertIsNone(re.search(pat, blob), pat)
        for t in _all_text(notices.notices()):
            self.assertIsNone(re.search(r"\b(AI|model|models|governor|assistant)\b", t, re.I), t)

    def test_notices_md_in_sync(self):
        md = (ROOT / "docs" / "NOTICES.md").read_text(encoding="utf-8")
        self.assertEqual(md, notices.markdown())
        d = notices.notices()
        d["build"] = "unpublished"
        self.assertTrue(md.endswith(notices.as_text(d)))
        self.assertIn("installer", md.split("-->\n")[1])
        self.assertIn("python -m opos.notices", md)

    def test_verification_doc_names_every_claim(self):
        doc = (ROOT / "docs" / "notices-verification.md").read_text(encoding="utf-8")
        for claim in notices.PRIVACY + notices.TESTED_DEVICES:
            self.assertIn("| " + claim + " |", doc)
        self.assertGreaterEqual(doc.count("src/opos/"), 10)

    def test_no_network_module_imported(self):
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertFalse(names & NETWORK, names)
        self.assertLessEqual(names, {"contextlib", "json", "os", "re", "sys", "opos"})

    def test_cli_prints_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(notices.main([]), 0)
        self.assertEqual(json.loads(buf.getvalue()), notices.notices())
        buf = io.StringIO()
        with redirect_stdout(buf):
            notices.main(["--markdown"])
        self.assertEqual(buf.getvalue(), notices.markdown())


if __name__ == "__main__":
    unittest.main()
