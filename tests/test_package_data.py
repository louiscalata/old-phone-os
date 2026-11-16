# SPDX-License-Identifier: GPL-3.0-or-later
"""Every runtime file under src/opos that is not Python is named by the package-data globs in pyproject.toml."""

import fnmatch
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "opos"


def matches(rel, pattern):
    """Match one path against one glob the way setuptools does: '*' never crosses a '/'."""
    parts, pats = rel.split("/"), pattern.split("/")
    return len(parts) == len(pats) and all(fnmatch.fnmatchcase(p, q) for p, q in zip(parts, pats))


class TestPackageData(unittest.TestCase):
    def test_every_asset_is_in_package_data(self):
        with open(ROOT / "pyproject.toml", "rb") as handle:
            globs = tomllib.load(handle)["tool"]["setuptools"]["package-data"]["opos"]
        assets = sorted(
            p.relative_to(PACKAGE).as_posix()
            for p in PACKAGE.rglob("*")
            if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts and not p.name.startswith(".")
        )
        self.assertIn("apps_static/calc.js", assets)  # calc_page reads it at import time
        missing = [rel for rel in assets if not any(matches(rel, g) for g in globs)]
        self.assertEqual(missing, [], "a built wheel would lack these files")

    def test_matching_does_not_cross_folders(self):
        self.assertTrue(matches("web_static/a.css", "web_static/*"))
        self.assertFalse(matches("web_static/fonts/a.woff2", "web_static/*"))


if __name__ == "__main__":
    unittest.main()
