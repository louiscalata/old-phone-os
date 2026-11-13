# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared legal and about text: one source for the kiosk About screen, the
install-time setup screens, the desktop installers and the case study.

Installers read it with `python -m opos.notices` (JSON) or `import opos.notices`.
docs/NOTICES.md is generated from markdown() and kept in sync by tests/test_notices.py.
Every PRIVACY and TESTED_DEVICES line is checked in docs/notices-verification.md.
Stdlib only; no network modules.
"""
import contextlib
import json
import os
import re
import sys
from opos.version import project_version

# Owner's words, exact. Never paraphrase (docs/jaryo.md section 11).
DISCLAIMER = (
    "Old Phone OS Interface is Louis Calata's hackathon demo. It is experimental software, "
    "provided as-is with no warranty (GPL-3.0). Installing it replaces your phone's "
    "operating system and may erase data; back up first and use at your own risk."
)

INSTALL_SCOPE = (
    "The replacement warning is for OS flashing; per-user install.sh installs OPOS "
    "within the existing OS."
)

# pyproject.toml owns the version; packages copy it into their generated metadata.
VERSION = project_version()

UNPUBLISHED_BUILD = "unpublished"
_BUILD_RE = re.compile(r"[0-9a-f]{7,40}")


def _build():
    """Short git hash from _build_info.py or OPOS_BUILD, else "unpublished"."""
    cands = []
    # Optional release metadata: any failure falls back to OPOS_BUILD.
    with contextlib.suppress(Exception):
        from opos import _build_info  # written by a release build, absent in the repo
        cands.append(getattr(_build_info, "BUILD", ""))
    cands.append(os.environ.get("OPOS_BUILD", ""))
    for c in cands:
        c = str(c or "").strip().lower()
        if _BUILD_RE.fullmatch(c):
            return c[:12]
    return UNPUBLISHED_BUILD


BUILD = _build()
LICENSE_LINE = "GNU GPL-3.0-or-later"
# Public repository named in README.md (Quickstart: git clone ...).
SOURCE_URL = "github.com/louiscalata/old-phone-os"

# From packaging/pmos/*/APKBUILD depends=; requirements.txt lists no packages.
THIRD_PARTY = (
    "Packages the postmarketOS build depends on:",
    "Linux kernel (GPL-2.0)",
    "Python 3 standard library (PSF License); no other Python packages are used",
    "Chromium (BSD-3-Clause and others), shows the phone screen",
    "cage (MIT), the kiosk session",
    "ModemManager (GPL-2.0-or-later), packaged for later modem use",
    "NetworkManager (GPL-2.0-or-later)",
    "polkit (LGPL-2.0-or-later) and D-Bus (AFL-2.1 or GPL-2.0-or-later)",
    "OpenRC (BSD-2-Clause) and psmisc (GPL-2.0-or-later)",
    "These postmarketOS repository packages retain their licenses and copyrights.",
    "The Duo image uses Droidian, phoc and systemd. This is not a complete component or license inventory for redistributing it.",
)

AFFILIATION = (
    "Old Phone OS Interface is not affiliated with or endorsed by postmarketOS, Microsoft, Google "
    "or any phone maker. All trademarks belong to their owners."
)

TESTED_DEVICES = (
    "Physical phones, including Surface Duo, are untested for this corrected source export.",
    "A November 3 build booted to Home in postmarketOS QEMU with software rendering; that does not validate physical devices.",
)

RISKS = (
    "Unlocking the bootloader or replacing the operating system may void your warranty.",
    "Unlocking the bootloader usually erases all data on the phone.",
    "A failed or interrupted install can leave the phone unable to start (bricked).",
    "Tasks, calls and texts are simulated. OPOS cannot place an emergency call; keep another way to call for help.",
)

RESTORE_STOCK = "[restore guide: unpublished]"

PRIVACY = (
    "Old Phone OS Interface sends no telemetry or analytics.",
    "The phone and setup pages are loopback-only; the public demo server listens on the network.",
    "By default the Helper uses its built-in rules and programs on this phone only.",
    "Local phone and setup code sends nothing off the phone unless you opt in to Wikipedia search, a Helper or Inference route to your computer on a private network, paid cloud use with your key, or coding tool installation.",
    "Hosted demo: entered numbers, messages and questions go to its server. Use fictional information.",
    "System software, including postmarketOS and Chromium, may use the network independently of OPOS.",
)

CONTACT = "See the project repository for support."
COPYRIGHT = "© 2026 Louis Calata"

FIELDS = ("disclaimer", "version", "build", "license", "source_url", "third_party", "affiliation", "tested_devices", "risks", "restore_stock", "privacy", "contact", "copyright")
_TITLES = {"version": "Version", "build": "Build", "license": "License", "source_url": "Source", "third_party": "Third-party notices", "affiliation": "Affiliation", "tested_devices": "Tested devices", "risks": "Risks", "restore_stock": "Restore the original system", "privacy": "Privacy", "contact": "Contact", "copyright": "Copyright"}


def notices():
    """All fields as a JSON-ready dict (lists for multi-line fields)."""
    return {
        "disclaimer": DISCLAIMER,
        "version": VERSION,
        "build": BUILD,
        "license": LICENSE_LINE,
        "source_url": SOURCE_URL,
        "third_party": list(THIRD_PARTY),
        "affiliation": AFFILIATION,
        "tested_devices": list(TESTED_DEVICES),
        "risks": list(RISKS),
        "restore_stock": RESTORE_STOCK,
        "privacy": list(PRIVACY),
        "contact": CONTACT,
        "copyright": COPYRIGHT,
    }


def as_text(data=None):
    """Plain text (also valid Markdown) of every field."""
    d = notices() if data is None else data
    out = ["About Old Phone OS Interface", "", d["disclaimer"], "", INSTALL_SCOPE, ""]
    for k in FIELDS[1:]:
        v = d[k]
        out.append(_TITLES[k])
        if isinstance(v, (list, tuple)):
            out.extend("- " + str(x) for x in v)
        else:
            out.append(str(v))
        out.append("")
    return "\n".join(out)


MD_HEADER = (
    "<!-- SPDX-License-Identifier: GPL-3.0-or-later -->\n"
    "<!-- Generated from src/opos/notices.py by `python -m opos.notices --markdown`; do not edit by hand. "
    "Shared with the installer workstream: the .exe/.dmg installers import opos.notices or run "
    "`python -m opos.notices` (JSON). Build shows \"unpublished\" here; a release build fills it in. "
    "Evidence for each privacy and tested-device line: docs/notices-verification.md. -->\n"
)


def markdown():
    """Content of docs/NOTICES.md: header plus as_text() with the build pinned."""
    d = notices()
    d["build"] = UNPUBLISHED_BUILD
    return MD_HEADER + as_text(d)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--text" in argv:
        sys.stdout.write(as_text())
    elif "--markdown" in argv:
        sys.stdout.write(markdown())
    else:
        sys.stdout.write(json.dumps(notices(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
