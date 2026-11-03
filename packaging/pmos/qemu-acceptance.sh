# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
if [ "$#" -eq 0 ]; then
printf "%s\n" "check 1: ssh -p 2222 user@127.0.0.1 'opos-firstboot'" "check 2: ssh -p 2222 user@127.0.0.1 'opos-kiosk-off --dry-run'" "check 3: ssh -p 2222 user@127.0.0.1 'sh -s' < tools/opos-bench.sh"
exit 0
fi
if [ "$#" -eq 1 ] && [ "$1" = "--help" ]; then
printf "%s\n" "usage: qemu-acceptance.sh [--help]"
exit 0
fi
printf "%s\n" "usage: qemu-acceptance.sh [--help]" >&2
exit 2
