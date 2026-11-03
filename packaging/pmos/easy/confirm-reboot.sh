# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
printf '%s\n' 'WARNING: the phone will reboot several times during setup. Do not unplug it or press buttons until it says Done.'
if [ "$#" -eq 0 ]; then
  printf '%s\n' 'Type reboot and press Enter to continue:'
  ans=""
  IFS= read -r ans || true
  if [ "$ans" = "reboot" ]; then
    exit 0
  fi
  printf '%s\n' 'aborted' >&2
  exit 1
fi
if [ "$#" -eq 1 ] && [ "$1" = "--yes" ]; then
  exit 0
fi
printf '%s\n' 'usage: confirm-reboot.sh [--yes]' >&2
exit 2
