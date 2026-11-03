# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
# UXP14: one line per real-browser acceptance violation (selector + measurement), from tests/test_uxr_acceptance.py.
cd "$(dirname "$0")/.." || exit 1
SB=$(mktemp -d)
OPOS_UXR_VERBOSE=1 HOME=$SB TMPDIR=$SB PYTHONPATH=src "${PYTHON:-python3}" -m unittest tests/test_uxr_acceptance.py 2>/dev/null | grep '^UXR '
