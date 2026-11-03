# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh # cage kiosk session sources this file to choose its command
CAGE_UI_COMMAND=${CAGE_UI_COMMAND:-/usr/bin/opos-kiosk}
export CAGE_UI_COMMAND
