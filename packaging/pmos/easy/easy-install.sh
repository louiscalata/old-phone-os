# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
set -e
HERE=$(cd "$(dirname "$0")" || exit 1; pwd)
FLASH=0
USAGE="usage: easy-install.sh [--flash] IMAGE"
WARNING="WARNING: flashing with the port's own commands ERASES the partition they write; the port documentation names it"
NOTICE="this helper does not flash: --flash checks the image and then refuses; flash with the port documentation"
if [ "$#" -eq 1 ] && [ "$1" = "--help" ]; then echo "$USAGE"; echo "$NOTICE"; exit 0; fi
while [ "$#" -gt 0 ]; do case "$1" in --flash) FLASH=1; shift;; -*) echo "$USAGE" >&2; exit 2;; *) break;; esac; done
if [ "$#" -ne 1 ] || [ -z "$1" ]; then echo "$USAGE" >&2; exit 2; fi
IMAGE="$1"
if [ "$FLASH" -eq 0 ]; then echo "step 1: sh $HERE/image-prep.sh --check $IMAGE"; echo "step 2: none ($NOTICE)"; exit 0; fi
# Say so before any check: no answer or flag makes this helper write to a device.
printf '%s\n' "$NOTICE"
printf '%s\n' "$WARNING"
sh "$HERE/image-prep.sh" "--check" "$IMAGE"
printf 'Selected image: %s\n' "$IMAGE"
printf '%s\n' 'refusing to flash: this helper cannot confirm that a flasher writes the checked image to the right partition; use the port documentation' >&2
exit 1
