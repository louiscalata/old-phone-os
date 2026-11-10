# SPDX-License-Identifier: GPL-3.0-or-later
"""Boot watch lines for Wi-Fi, the container radio, and the audio processor.

The lines are present, missing, or not ONLINE. They do not include names, addresses,
or equipment identifiers. Not measured on a phone.
"""
from __future__ import annotations

from pathlib import Path


def watch(root="/") -> list[str]:
    base = Path(root)
    lines = [
        "watch: wlan0 present" if (base / "sys/class/net/wlan0").exists() else "watch: wlan0 missing",
        "watch: ril_0 present" if (base / "ril_0").exists() else "watch: ril_0 missing",
    ]
    proc = base / "sys/class/remoteproc"
    states = sorted(proc.glob("*/state")) if proc.is_dir() else []
    if not states:
        lines.append("watch: adsp missing")
        return lines
    texts = []
    for path in states:
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace").strip().lower())
        except OSError:
            texts.append("")
    if texts and all(text in ("online", "running") for text in texts):
        lines.append("watch: adsp ONLINE")
    else:
        lines.append("watch: adsp not ONLINE")
    return lines


def main() -> int:
    for line in watch("/"):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
