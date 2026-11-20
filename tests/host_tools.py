"""Host-tool helpers for portable test skips.

The image policy runs the host `sshd -T` against Linux image configs; Apple's OpenSSH on macOS parses them differently,
so builds are only checked on a Linux host.
"""

from __future__ import annotations

import shutil
import sys
import unittest

# Only a Linux host checks the image with its own sshd -T; elsewhere the build-block tests skip.
LINUX_HOST = sys.platform.startswith("linux")


def needs_tools(*names: str):
    return unittest.skipUnless(
        all(shutil.which(name) for name in names),
        "needs " + ", ".join(names) + " on PATH (Linux build host)",
    )
