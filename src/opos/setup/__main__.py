# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the Old Phone OS setup command line with python -m opos.setup."""
from __future__ import annotations
from opos.setup.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
