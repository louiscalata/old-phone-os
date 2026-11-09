# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
set -e
HERE=$(cd "$(dirname "$0")" || exit 1; pwd)
FLASH=0
YES=""
if [ "$#" -eq 1 ] && [ "$1" = "--help" ]; then echo "usage: easy-install.sh [--flash] [--yes] IMAGE"; exit 0; fi
while [ "$#" -gt 0 ]; do case "$1" in --flash) FLASH=1; shift;; --yes) YES="--yes"; shift;; -*) echo "usage: easy-install.sh [--flash] [--yes] IMAGE" >&2; exit 2;; *) break;; esac; done
if [ "$#" -ne 1 ] || [ -z "$1" ]; then echo "usage: easy-install.sh [--flash] [--yes] IMAGE" >&2; exit 2; fi
IMAGE="$1"
if [ "$FLASH" -eq 0 ]; then echo "step 1: sh $HERE/image-prep.sh --check $IMAGE"; echo "step 2: sh $HERE/confirm-reboot.sh"; echo "step 3: skipped (no --flash)"; exit 0; fi
sh "$HERE/image-prep.sh" "--check" "$IMAGE"
if [ -n "$YES" ]; then sh "$HERE/confirm-reboot.sh" "$YES"; else sh "$HERE/confirm-reboot.sh"; fi
echo "step 3: flashing (physical-device flashing is untested; pmbootstrap uses its installed rootfs, not the checked image path)"
pmbootstrap flasher flash_rootfs
