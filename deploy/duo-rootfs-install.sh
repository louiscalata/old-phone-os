#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

run() {
 printf '+ %s\n' "$*"
 if [ "$yes" = 1 ]; then
 "$@"
 fi
}

verify() {
 phone_hash=$(adb shell sha256sum "$dest" | tr -d '\r' | cut -d' ' -f1)
 if command -v sha256sum >/dev/null 2>&1; then
 local_hash=$(sha256sum "$rootfs" | cut -d' ' -f1)
 else
 local_hash=$(shasum -a 256 "$rootfs" | cut -d' ' -f1)
 fi
 phone_size=$(adb shell wc -c "$dest" | tr -d '\r' | cut -d' ' -f1)
 local_size=$(wc -c < "$rootfs" | tr -d ' ')
 if [ "$phone_hash" != "$local_hash" ] || [ "$phone_size" != "$local_size" ]; then
 printf 'verify failed\n' >&2
 exit 1
 fi
}

main() {
 yes=0
 if [ "${1-}" = --yes ]; then
 yes=1
 shift
 fi
 if [ $# -ne 3 ]; then
 printf 'usage: duo-rootfs-install.sh [--yes] TWRP_IMG DROIDIAN_BOOT_IMG ROOTFS_IMG\n' >&2
 exit 2
 fi
 twrp=$1
 boot=$2
 rootfs=$3
 dest=/data/rootfs.img
 echo 'WARNING: fastboot format:ext4 userdata ERASES userdata'
 echo 'check the battery is at least 20 percent'
 echo 'stop on Device Error; never retry the RAM-boot'
 echo 'never run reboot or systemctl reboot on the RAM-booted kernel'
 run fastboot format:ext4 userdata
 run fastboot boot "$twrp"
 run adb wait-for-recovery
 run adb push "$rootfs" "$dest"
 run verify
 # fastboot only answers in the bootloader: leave TWRP for it before the one RAM-boot.
 run adb reboot bootloader
 run fastboot wait-for-device
 run fastboot boot "$boot"
}

main "$@"
