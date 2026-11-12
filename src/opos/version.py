# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the application version from its source, current OTA release or package."""
import json
import re
import tomllib
from importlib import metadata
from pathlib import Path


def project_version():
    release = Path(__file__).resolve().parents[2]
    try:
        with (release / "pyproject.toml").open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    except (OSError, ValueError, KeyError):
        # The updater puts the signed release manifest beside this OTA tree.
        # Its application version may differ from the immutable image version.
        try:
            current = json.loads((release / "manifest.json").read_bytes())
            revision = (release / "REVISION").read_text(encoding="utf-8").strip()
            version = current["version"]
            if (re.fullmatch(r"[0-9a-f]{40}", revision) and current["revision"] == revision
                    and isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}", version)):
                return version
        except (OSError, ValueError, KeyError, TypeError):
            pass
        # A wheel carries the same version in its generated package metadata.
        try:
            return metadata.version("opos")
        except metadata.PackageNotFoundError:
            return "unknown"
