#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

refuse() {
 printf '%s\n' "$*" >&2
 exit 1
}

run() {
 # Show and run the real command, pinned to the one checked device.
 case "$1" in
 fb) shift; set -- fastboot -s "$serial" "$@";;
 ad) shift; set -- adb -s "$serial" "$@";;
 esac
 printf '+ %s\n' "$*"
 if [ "$yes" = 1 ]; then
 "$@"
 fi
}

image_hash() {
 if command -v sha256sum >/dev/null 2>&1; then
 hash_output=$(sha256sum "$1") || refuse 'cannot check image SHA-256'
 elif command -v shasum >/dev/null 2>&1; then
 hash_output=$(shasum -a 256 "$1") || refuse 'cannot check image SHA-256'
 else
 refuse 'cannot check image SHA-256: sha256sum or shasum is required'
 fi
 hash=${hash_output%% *}
 valid_hash "$hash" || refuse 'invalid image SHA-256 output'
 printf '%s\n' "$hash" | tr 'A-F' 'a-f'
}

valid_hash() {
 [ "${#1}" -eq 64 ] || return 1
 case "$1" in *[!0-9a-fA-F]*) return 1;; esac
}

check_image() {
 [ -f "$1" ] && [ -r "$1" ] && [ -s "$1" ] || refuse "image must be a readable, non-empty file: $1"
 checked_hash=$(image_hash "$1") || exit 1
 if [ -n "$2" ]; then
 expected_hash=$(printf '%s\n' "$2" | tr 'A-F' 'a-f')
 [ "$checked_hash" = "$expected_hash" ] || refuse "image SHA-256 mismatch: $1"
 fi
}

check_device() {
 devices=$(fastboot devices) || refuse 'cannot list fastboot devices'
 serial=$(printf '%s\n' "$devices" | awk '
   NF { if (NF != 2 || $2 != "fastboot") bad=1; count++; device=$1 }
   END { if (bad || count != 1) exit 1; print device }
 ') || refuse 'expected exactly one fastboot device'
 product_output=$(fastboot -s "$serial" getvar product 2>&1) || refuse 'cannot check device product'
 product=$(printf '%s\n' "$product_output" | awk '
   { sub(/\r$/, ""); sub(/^\(bootloader\)[[:space:]]*/, "") }
   /^[[:space:]]*product:/ { count++; if (NF != 2) bad=1; value=$2 }
   END { if (bad || count != 1) exit 1; print value }
 ') || refuse 'cannot identify device product'
 [ "$product" = surfaceduo ] || refuse 'unexpected device product: expected Surface Duo 1'
}

ad() { adb -s "$serial" "$@"; }

verify() {
 phone_hash_output=$(ad shell sha256sum "$dest") || refuse 'verify failed'
 phone_hash=$(printf '%s\n' "$phone_hash_output" | tr -d '\r' | cut -d' ' -f1)
 # Compare with the hash that passed the check, not a fresh local read.
 local_hash=$rootfs_checked
 phone_size_output=$(ad shell wc -c "$dest") || refuse 'verify failed'
 phone_size=$(printf '%s\n' "$phone_size_output" | tr -d '\r' | cut -d' ' -f1)
 local_size=$(wc -c < "$rootfs" | tr -d ' ')
 if [ "$phone_hash" != "$local_hash" ] || [ "$phone_size" != "$local_size" ]; then
 refuse 'verify failed'
 fi
}

recheck_twrp_image() {
 twrp_now=$(image_hash "$twrp") || exit 1
 [ "$twrp_now" = "$twrp_checked" ] || refuse 'TWRP image changed after it was checked'
}

recheck_boot_image() {
 boot_now=$(image_hash "$boot") || exit 1
 [ "$boot_now" = "$boot_checked" ] || refuse 'boot image changed after it was checked'
}

usage() {
 printf '%s\n' 'usage: duo-rootfs-install.sh [--yes --erase-userdata] [--twrp-sha256 HASH] [--boot-sha256 HASH] [--rootfs-sha256 HASH] TWRP_IMG DROIDIAN_BOOT_IMG ROOTFS_IMG' >&2
 exit 2
}

main() {
 yes=0
 erase_userdata=0
 twrp_hash='' boot_hash='' rootfs_hash=''
 while [ "$#" -gt 0 ]; do
 case "$1" in
 --yes) yes=1; shift;;
 --erase-userdata) erase_userdata=1; shift;;
 --twrp-sha256|--boot-sha256|--rootfs-sha256)
 [ "$#" -ge 2 ] || usage
 valid_hash "$2" || refuse 'SHA-256 must contain exactly 64 hexadecimal characters'
 case "$1" in
 --twrp-sha256) twrp_hash=$2;;
 --boot-sha256) boot_hash=$2;;
 --rootfs-sha256) rootfs_hash=$2;;
 esac
 shift 2;;
 -*) usage;;
 *) break;;
 esac
 done
 [ "$#" -eq 3 ] || usage
 # Prefix relative paths so filenames cannot become tool options.
 case "$1" in /*) twrp=$1;; *) twrp=./$1;; esac
 case "$2" in /*) boot=$2;; *) boot=./$2;; esac
 case "$3" in /*) rootfs=$3;; *) rootfs=./$3;; esac
 dest=/data/rootfs.img
 echo 'WARNING: fastboot format:ext4 userdata ERASES userdata'
 echo 'check the battery is at least 20 percent'
 echo 'stop on Device Error; never retry the RAM-boot'
 echo 'never run reboot or systemctl reboot on the RAM-booted kernel'
 if [ "$yes" = 1 ]; then
 [ "$erase_userdata" = 1 ] || refuse '--yes also requires --erase-userdata: this erases all userdata'
 check_image "$twrp" "$twrp_hash"
 twrp_checked=$checked_hash
 check_image "$boot" "$boot_hash"
 boot_checked=$checked_hash
 check_image "$rootfs" "$rootfs_hash"
 rootfs_checked=$checked_hash
 command -v adb >/dev/null 2>&1 || refuse 'adb is required'
 command -v fastboot >/dev/null 2>&1 || refuse 'fastboot is required'
 check_device
 # Pin all commands to the one device that passed the product check.
 fastboot_cmd=fb
 adb_cmd=ad
 else
 echo 'Execution requires --yes --erase-userdata and successful image and device checks.'
 fastboot_cmd=fastboot
 adb_cmd=adb
 fi
 run "$fastboot_cmd" format:ext4 userdata
 # userdata is already erased here: refuse if TWRP is no longer the checked file.
 run recheck_twrp_image
 run "$fastboot_cmd" boot "$twrp"
 run "$adb_cmd" wait-for-recovery
 # TWRP 3.5.2 for the Duo leaves the freshly formatted userdata unmounted (seen 2026-11-07).
 run "$adb_cmd" shell 'mountpoint -q /data || mount -t ext4 /dev/block/platform/soc/1d84000.ufshc/by-name/userdata /data'
 run "$adb_cmd" push "$rootfs" "$dest"
 run verify
 # Refuse here, still in TWRP, if the boot image is no longer the checked file.
 run recheck_boot_image
 # fastboot only answers in the bootloader: leave TWRP for it before the one RAM-boot.
 run "$adb_cmd" reboot bootloader
 run "$fastboot_cmd" getvar product
 # Check again directly before the RAM-boot: the reboot gives the file time to change.
 run recheck_boot_image
 run "$fastboot_cmd" boot "$boot"
}

main "$@"
