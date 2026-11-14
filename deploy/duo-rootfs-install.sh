#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

private=''

cleanup() {
 # Remove only the two private copies and their folder.
 if [ -n "$private" ]; then
 rm -f "$private/twrp.img" "$private/boot.img"
 rmdir "$private" 2>/dev/null || :
 private=''
 fi
}

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

private_copy() {
 # Copy a checked image once into the private folder; the copy is what gets booted.
 # $1 image, $2 name of the copy, $3 the hash that passed the check.
 cp "$1" "$private/$2" || refuse "cannot make a private copy of the image: $1"
 chmod 0400 "$private/$2" || refuse "cannot make a private copy of the image: $1"
 copy_hash=$(image_hash "$private/$2") || exit 1
 [ "$copy_hash" = "$3" ] || refuse "cannot make a private copy of the image: $1 changed while it was copied"
}

device_var() {
 # Print the one value the selected device reports for a fastboot variable.
 var_output=$(fastboot -s "$serial" getvar "$1" 2>&1) || return 1
 printf '%s\n' "$var_output" | awk -v name="$1:" '
   { sub(/\r$/, ""); sub(/^\(bootloader\)[[:space:]]*/, "") }
   $1 == name { count++; if (NF != 2) bad=1; value=$2 }
   END { if (bad || count != 1) exit 1; print value }
 '
}

check_device() {
 devices=$(fastboot devices) || refuse 'cannot list fastboot devices'
 serial=$(printf '%s\n' "$devices" | awk '
   NF { if (NF != 2 || $2 != "fastboot") bad=1; count++; device=$1 }
   END { if (bad || count != 1) exit 1; print device }
 ') || refuse 'expected exactly one fastboot device'
 product=$(device_var product) || refuse 'cannot check device product'
 [ "$product" = surfaceduo ] || refuse 'unexpected device product: expected Surface Duo 1'
 # A locked bootloader cannot RAM-boot TWRP: refuse before anything is erased.
 unlocked=$(device_var unlocked) || refuse 'cannot check that the bootloader is unlocked'
 [ "$unlocked" = yes ] || refuse 'the bootloader does not report unlocked: yes'
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

recheck() {
 # $1 file, $2 the hash that passed the check, $3 what to call it.
 hash_now=$(image_hash "$1") || exit 1
 [ "$hash_now" = "$2" ] || refuse "$3 changed after it was checked"
}

recheck_images_before_erase() {
 # Nothing is erased yet: refuse if a named file or a private copy changed.
 recheck "$twrp" "$twrp_checked" 'TWRP image'
 recheck "$twrp_run" "$twrp_checked" 'TWRP image'
 recheck "$boot" "$boot_checked" 'boot image'
 recheck "$boot_run" "$boot_checked" 'boot image'
 recheck "$rootfs" "$rootfs_checked" 'rootfs image'
}

recheck_twrp_image() { recheck "$twrp_run" "$twrp_checked" 'TWRP image'; }

recheck_boot_image() { recheck "$boot_run" "$boot_checked" 'boot image'; }

recheck_rootfs_image() { recheck "$rootfs" "$rootfs_checked" 'rootfs image'; }

recheck_device_product() {
 # Same serial, and it must still be the product that was checked before the erase.
 # Shows the real command once; the answer is read here, so this does not go through run.
 if [ "$yes" != 1 ]; then
 printf '+ fastboot getvar product\n'
 return 0
 fi
 printf '+ fastboot -s %s getvar product\n' "$serial"
 product_now=$(device_var product) || refuse 'cannot check device product after the reboot'
 [ "$product_now" = "$product" ] || refuse 'unexpected device product after the reboot: expected the product checked before the erase'
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
 echo 'manual step: check the battery is at least 20 percent; this script does not check the battery'
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
 # RAM-boot private copies, so a later change to the named files cannot reach the phone.
 trap cleanup EXIT
 trap 'exit 129' HUP
 trap 'exit 130' INT
 trap 'exit 143' TERM
 private=$(mktemp -d "${TMPDIR:-/tmp}/duo-install.XXXXXX") || refuse 'cannot create a private folder for the checked images'
 twrp_run=$private/twrp.img
 boot_run=$private/boot.img
 private_copy "$twrp" twrp.img "$twrp_checked"
 private_copy "$boot" boot.img "$boot_checked"
 command -v adb >/dev/null 2>&1 || refuse 'adb is required'
 command -v fastboot >/dev/null 2>&1 || refuse 'fastboot is required'
 check_device
 # Pin all commands to the one device that passed the product check.
 fastboot_cmd=fb
 adb_cmd=ad
 else
 echo 'Execution requires --yes --erase-userdata and successful image and device checks.'
 echo 'It then RAM-boots private copies of the TWRP and boot images, made when they are checked.'
 fastboot_cmd=fastboot
 adb_cmd=adb
 twrp_run=$twrp
 boot_run=$boot
 fi
 # Last step before the erase: every image must still be what was checked.
 run recheck_images_before_erase
 run "$fastboot_cmd" format:ext4 userdata
 # userdata is already erased here: refuse if the TWRP copy is no longer what was checked.
 run recheck_twrp_image
 run "$fastboot_cmd" boot "$twrp_run"
 run "$adb_cmd" wait-for-recovery
 # TWRP 3.5.2 for the Duo leaves the freshly formatted userdata unmounted (seen 2026-11-07).
 run "$adb_cmd" shell 'mountpoint -q /data || mount -t ext4 /dev/block/platform/soc/1d84000.ufshc/by-name/userdata /data'
 # The rootfs is not copied (it is large): do not push a file that changed since the check.
 run recheck_rootfs_image
 run "$adb_cmd" push "$rootfs" "$dest"
 run verify
 # Refuse here, still in TWRP, if the boot copy is no longer what was checked.
 run recheck_boot_image
 # fastboot only answers in the bootloader: leave TWRP for it before the one RAM-boot.
 run "$adb_cmd" reboot bootloader
 recheck_device_product
 # Check again directly before the RAM-boot: the reboot gives the copy time to change.
 run recheck_boot_image
 run "$fastboot_cmd" boot "$boot_run"
}

main "$@"
