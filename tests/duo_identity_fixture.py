# SPDX-License-Identifier: GPL-3.0-or-later
"""A labelled, simulated image identity for selftest and display tests. No image or phone."""
from pathlib import Path
import tomllib
from opos import image_identity as identity

ROOT = Path(__file__).resolve().parents[1]
REVISION = "0123456789abcdef0123456789abcdef01234567"


def add_identity(root):
    root = Path(root)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    base = 'NAME="Droidian"\nID=droidian\nID_LIKE="debian"\nVERSION_ID=next\n'
    home = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["urls"]["Source"]
    values = {"NAME": "Old Phone OS", "ID": "opos", "ID_LIKE": "droidian debian", "VERSION": version,
              "VERSION_ID": version, "BUILD_ID": REVISION[:12], "PRETTY_NAME": "Old Phone OS " + version, "HOME_URL": home}
    files = {"etc/os-release": "".join(k + '="' + v + '"\n' for k, v in values.items()),
             "usr/lib/os-release.base": base, "etc/issue": "Old Phone OS " + version + "\n",
             "etc/hostname": "opos\n", "usr/bin/opos-version": (ROOT / "packaging/duo-os/opos-version").read_text(),
             "usr/libexec/opos/image_identity.py": (ROOT / "src/opos/image_identity.py").read_text()}
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    m = {"format": 1, "version": version, "revision": REVISION, "build_id": REVISION[:12], "home_url": home,
         "source_date_epoch": 1794093790, "base": {"sha256": "a" * 64, "os_release": identity.os_release(base)},
         "switches": {"OPOS_OSK": "1"}, "files": [identity.record(root, name) for name in sorted(files)]}
    p = root / "etc/opos/image.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(identity.canonical(m))
    return m
