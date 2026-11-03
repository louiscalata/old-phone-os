#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
step_fde() {
  if [ -e "$(p "/usr/lib/droidian/device/encryption-supported")" ]; then
    say "fde: device marker present"
  else
    say "fde: device marker absent"
  fi
  if have_cmd dpkg; then
    if dpkg -s droidian-encryption-service >/dev/null 2>&1; then
      say "fde: encryption service installed"
    else
      say "fde: encryption service absent"
    fi
  fi
  if have_cmd findmnt; then
    src="$(findmnt -no SOURCE / 2>/dev/null || true)"
    case "$src" in
      */dev/mapper*|*/dev/dm-*) say "fde: root on device-mapper volume" ;;
      *) say "fde: root not on device-mapper volume" ;;
    esac
  fi
  say "fde: NOT enabled by this script. Droidian encryption is opt-in, needs the fastboot-flashable layout (LVM on userdata with a reserved volume) and a tested unlock path; enabling it on an unverified port can leave the phone unbootable. Read docs/device-hardening.md and get the owner's approval first."
  return 0
}
