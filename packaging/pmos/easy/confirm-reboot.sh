# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
printf '%s\n' 'WARNING: this script does not reboot the phone or verify a multi-reboot setup. Physical-device flashing is untested. Do not unplug a device you choose to flash.'
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
